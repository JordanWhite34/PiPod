#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import os
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from pipod_runtime import (
    MUSIC_ITEM_SCROLL_DELAY_S,
    MusicItem,
    MusicViewState,
    NOW_PLAYING_ART_SIZE,
    NOW_PLAYING_ART_TOP,
    NOW_PLAYING_FOCUSABLE,
    NOW_PLAYING_LEFT_MARGIN,
    NOW_PLAYING_PROGRESS_TOP_GAP,
    SETTINGS_ITEM_SCROLL_DELAY_S,
    NOW_PLAYING_TIME_TOP_GAP,
    NOW_PLAYING_TITLE_TOP_GAP,
    NOW_PLAYING_VOLUME_TOP_GAP,
    RunConfig,
    RuntimeDependencies,
    SettingsItem,
    SettingsViewState,
    StatusPlumbing,
    VOLUME_SLIDER_KNOB_CENTER_Y_OFFSET,
    _volume_slider_knob_x,
    _next_now_playing_idle_art_name,
    _persist_now_playing_idle_art_image,
    _infer_now_playing_idle_art_name_from_persisted_image,
    _resolve_now_playing_idle_art_selection_name,
    _resolve_now_playing_idle_art_path,
    build_music_index,
    load_now_playing_idle_art,
    load_playlists_manifest,
    load_fonts,
    parse_input_token,
    render_music_browser,
    render_settings_browser,
    render_now_playing,
    run_pipod_loop,
)
from settings_actions import BluetoothDevice, SettingsActionResult, SettingsActions
from settings_store import PersistedSettings, SettingsStore
from input_provider import CombinedEventProvider, GpioFiveWayConfig, GpioFiveWayInput
from simulator_adapters import FakeEPD, FakeSettingsActions, FixtureLibrary, MockPlayer

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT_DIR / "data" / "sim_tracks.json"


class SimulatorAdapterTests(unittest.TestCase):
    def test_fake_epd_captures_base_and_partial_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epd = FakeEPD(output_dir=Path(temp_dir), write_frames=True, frame_prefix="test")
            epd.init()
            image = Image.new("1", (epd.width, epd.height), 255)
            epd.displayPartBaseImage(epd.getbuffer(image))
            epd.displayPartial(epd.getbuffer(image))
            self.assertEqual(epd.base_frame_count, 1)
            self.assertEqual(epd.partial_frame_count, 1)
            self.assertEqual(epd.frame_count, 2)
            frame_files = list((Path(temp_dir) / "frames").glob("*.png"))
            self.assertEqual(len(frame_files), 2)

    def test_fixture_library_has_deterministic_order(self):
        library_a = FixtureLibrary(FIXTURE_PATH, seed=1337)
        library_b = FixtureLibrary(FIXTURE_PATH, seed=1337)
        order_a = [track.path for track in library_a.random_tracks()]
        order_b = [track.path for track in library_b.random_tracks()]
        self.assertEqual(order_a, order_b)
        artists, songs, albums = library_a.library_counts()
        self.assertGreaterEqual(artists, 1)
        self.assertGreaterEqual(songs, 1)
        self.assertGreaterEqual(albums, 1)

    def test_mock_player_queue_controls_and_poll(self):
        player = MockPlayer(seed=7)
        track_a = Path("/sim/A/Album/track_a.mp3")
        track_b = Path("/sim/A/Album/track_b.mp3")
        player.set_track_durations({track_a: 2, track_b: 5})

        self.assertTrue(player.set_queue([track_a, track_b], autoplay=True))
        self.assertTrue(player.is_playing())

        player.advance_time(2.1)
        self.assertTrue(player.poll())
        self.assertEqual(player.current_track_path(), track_b)

        self.assertTrue(player.toggle_pause())
        self.assertTrue(player.state().is_paused)
        self.assertTrue(player.toggle_pause())
        self.assertFalse(player.state().is_paused)

    def test_now_playing_top_banner_removed(self):
        library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        tracks = [track.path for track in library.random_tracks()]
        player.set_track_durations(library.duration_map())
        player.set_queue(tracks, autoplay=True)

        epd = FakeEPD(write_frames=False)
        status = StatusPlumbing().read()
        image = render_now_playing(
            epd=epd,
            fonts=load_fonts(),
            player=player,
            library=library,
            status=status,
            charge_anim_frame=0,
            album_art_cache={},
            focus_index=1,
            song_scroll_px=0,
        )
        pixels = image.load()

        # Top divider at y=26 should no longer span the display.
        row_y = 26
        black_on_row = sum(1 for x in range(epd.width) if pixels[x, row_y] == 0)
        self.assertLess(black_on_row, 20)

        # Old top-right battery/volume block should be empty now.
        black_in_status_corner = sum(
            1
            for x in range(111, epd.width)
            for y in range(0, 26)
            if pixels[x, y] == 0
        )
        self.assertEqual(black_in_status_corner, 0)

    def test_now_playing_volume_slider_knob_moves_with_volume(self):
        library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        tracks = [track.path for track in library.random_tracks()]
        player.set_track_durations(library.duration_map())
        player.set_queue(tracks, autoplay=True)

        epd = FakeEPD(write_frames=False)
        fonts = load_fonts()
        base_status = StatusPlumbing().read()

        low_status = type(base_status)(
            battery_percent=base_status.battery_percent,
            volume_level=1,
            is_charging=base_status.is_charging,
            is_muted=False,
        )
        high_status = type(base_status)(
            battery_percent=base_status.battery_percent,
            volume_level=9,
            is_charging=base_status.is_charging,
            is_muted=False,
        )

        low_image = render_now_playing(
            epd=epd,
            fonts=fonts,
            player=player,
            library=library,
            status=low_status,
            charge_anim_frame=0,
            album_art_cache={},
            focus_index=1,
            song_scroll_px=0,
        )
        high_image = render_now_playing(
            epd=epd,
            fonts=fonts,
            player=player,
            library=library,
            status=high_status,
            charge_anim_frame=0,
            album_art_cache={},
            focus_index=1,
            song_scroll_px=0,
        )

        art_bottom = NOW_PLAYING_ART_TOP + NOW_PLAYING_ART_SIZE - 1
        progress_y = art_bottom + NOW_PLAYING_PROGRESS_TOP_GAP
        time_y = progress_y + NOW_PLAYING_TIME_TOP_GAP
        song_y = time_y + NOW_PLAYING_TITLE_TOP_GAP
        volume_y = song_y + NOW_PLAYING_VOLUME_TOP_GAP
        knob_y = volume_y + VOLUME_SLIDER_KNOB_CENTER_Y_OFFSET
        slider_w = epd.width - (NOW_PLAYING_LEFT_MARGIN * 2)

        low_knob_x = _volume_slider_knob_x(
            NOW_PLAYING_LEFT_MARGIN,
            volume_y,
            slider_w,
            low_status.volume_level,
            is_muted=low_status.is_muted,
        )
        high_knob_x = _volume_slider_knob_x(
            NOW_PLAYING_LEFT_MARGIN,
            volume_y,
            slider_w,
            high_status.volume_level,
            is_muted=high_status.is_muted,
        )
        self.assertGreater(high_knob_x, low_knob_x)

        low_pixels = low_image.load()
        high_pixels = high_image.load()
        low_outline_pixels = sum(
            1
            for x in range(low_knob_x - 4, low_knob_x + 5)
            for y in range(knob_y - 4, knob_y + 5)
            if 0 <= x < epd.width and 0 <= y < epd.height and low_pixels[x, y] == 0
        )
        high_outline_pixels = sum(
            1
            for x in range(high_knob_x - 4, high_knob_x + 5)
            for y in range(knob_y - 4, knob_y + 5)
            if 0 <= x < epd.width and 0 <= y < epd.height and high_pixels[x, y] == 0
        )
        self.assertGreater(low_outline_pixels, 0)
        self.assertGreater(high_outline_pixels, 0)

    def test_now_playing_focusables_unchanged(self):
        self.assertTupleEqual(
            NOW_PLAYING_FOCUSABLE,
            ("PREV", "PLAY_PAUSE", "NEXT", "SHUFFLE", "LOOP"),
        )

    def test_now_playing_progress_ring_replaces_progress_bar(self):
        library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        tracks = [track.path for track in library.random_tracks()]
        player.set_track_durations(library.duration_map())
        player.set_queue(tracks, autoplay=True)
        player.advance_time(30.0)

        epd = FakeEPD(write_frames=False)
        status = StatusPlumbing().read()
        fonts = load_fonts()

        bar_image = render_now_playing(
            epd=epd,
            fonts=fonts,
            player=player,
            library=library,
            status=status,
            charge_anim_frame=0,
            album_art_cache={},
            focus_index=1,
            song_scroll_px=0,
            progress_ring_enabled=False,
        )
        ring_image = render_now_playing(
            epd=epd,
            fonts=fonts,
            player=player,
            library=library,
            status=status,
            charge_anim_frame=0,
            album_art_cache={},
            focus_index=1,
            song_scroll_px=0,
            progress_ring_enabled=True,
        )

        bar_pixels = bar_image.load()
        ring_pixels = ring_image.load()

        def border_black_pixels(pixels, border=3):
            count = 0
            for y in range(epd.height):
                for x in range(epd.width):
                    if x < border or x >= epd.width - border or y < border or y >= epd.height - border:
                        if pixels[x, y] == 0:
                            count += 1
            return count

        art_bottom = NOW_PLAYING_ART_TOP + NOW_PLAYING_ART_SIZE - 1
        progress_y = art_bottom + NOW_PLAYING_PROGRESS_TOP_GAP
        progress_h = 8
        progress_x = NOW_PLAYING_LEFT_MARGIN
        progress_w = epd.width - (NOW_PLAYING_LEFT_MARGIN * 2)

        def progress_bar_black_pixels(pixels):
            count = 0
            for y in range(progress_y, progress_y + progress_h):
                for x in range(progress_x, progress_x + progress_w):
                    if 0 <= x < epd.width and 0 <= y < epd.height and pixels[x, y] == 0:
                        count += 1
            return count

        corner_points = (
            (1, 1),
            (epd.width - 2, 1),
            (1, epd.height - 2),
            (epd.width - 2, epd.height - 2),
        )
        for point_x, point_y in corner_points:
            self.assertEqual(bar_pixels[point_x, point_y], 255)
            self.assertEqual(ring_pixels[point_x, point_y], 0)

        self.assertGreater(border_black_pixels(ring_pixels), border_black_pixels(bar_pixels) + 120)
        self.assertGreater(progress_bar_black_pixels(bar_pixels), progress_bar_black_pixels(ring_pixels) + 60)

    def test_resolve_now_playing_idle_art_path_uses_selected_when_available(self):
        first = Path("/tmp/first.png")
        second = Path("/tmp/second.png")
        with mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)):
            chosen = _resolve_now_playing_idle_art_path("second.png")
        self.assertEqual(chosen, second)

    def test_resolve_now_playing_idle_art_path_falls_back_to_first(self):
        first = Path("/tmp/first.png")
        second = Path("/tmp/second.png")
        with mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)):
            chosen = _resolve_now_playing_idle_art_path("missing.png")
        self.assertEqual(chosen, first)

    def test_next_now_playing_idle_art_name_cycles(self):
        first = Path("/tmp/first.png")
        second = Path("/tmp/second.png")
        third = Path("/tmp/third.png")
        with mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second, third)):
            self.assertEqual(_next_now_playing_idle_art_name("second.png"), "third.png")
            self.assertEqual(_next_now_playing_idle_art_name("third.png"), "first.png")
            self.assertEqual(_next_now_playing_idle_art_name(None), "first.png")

    def test_resolve_idle_art_selection_name_uses_persisted_metadata_fallback(self):
        first = Path("/tmp/first.png")
        second = Path("/tmp/second.png")
        with (
            mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)),
            mock.patch("pipod_runtime._read_persisted_now_playing_idle_art_name", return_value="second.png"),
        ):
            selected = _resolve_now_playing_idle_art_selection_name(None)
        self.assertEqual(selected, "second.png")

    def test_persist_now_playing_idle_art_image_writes_png(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            target = root / "persisted_idle_cover.png"
            selection_target = root / "persisted_idle_cover_selection.txt"
            Image.new("RGB", (8, 8), "red").save(source, format="JPEG")
            with (
                mock.patch("pipod_runtime._resolve_now_playing_idle_art_path", return_value=source),
                mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_PERSIST_PATH", target),
                mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_SELECTION_PATH", selection_target),
            ):
                self.assertTrue(_persist_now_playing_idle_art_image("source.jpg"))
            self.assertTrue(target.exists())
            self.assertEqual(selection_target.read_text(encoding="utf-8"), "source.jpg")
            with Image.open(target) as persisted:
                self.assertEqual(persisted.format, "PNG")

    def test_load_now_playing_idle_art_uses_persisted_fallback_when_assets_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            persisted = root / "persisted_idle_cover.png"
            Image.new("RGB", (20, 20), "black").save(persisted, format="PNG")
            with (
                mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=()),
                mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_PERSIST_PATH", persisted),
            ):
                art = load_now_playing_idle_art(16, {}, render_mode="classic", selected_name="missing.png")
            self.assertIsNotNone(art)
            self.assertEqual(art.size, (16, 16))

    def test_load_now_playing_idle_art_prefers_persisted_when_selected_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "asset.png"
            persisted = root / "persisted_idle_cover.png"
            Image.new("RGB", (20, 20), "white").save(asset, format="PNG")
            Image.new("RGB", (20, 20), "black").save(persisted, format="PNG")
            with (
                mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(asset,)),
                mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_PERSIST_PATH", persisted),
            ):
                art = load_now_playing_idle_art(16, {}, render_mode="classic", selected_name="missing.png")
            self.assertIsNotNone(art)
            self.assertEqual(art.size, (16, 16))
            self.assertEqual(art.getpixel((0, 0)), 0)

    def test_infer_idle_art_name_from_persisted_image_matches_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.png"
            second = root / "second.png"
            persisted = root / "persisted_idle_cover.png"
            Image.new("RGB", (10, 10), "white").save(first, format="PNG")
            Image.new("RGB", (10, 10), "black").save(second, format="PNG")
            Image.new("RGB", (10, 10), "black").save(persisted, format="PNG")
            with (
                mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_PERSIST_PATH", persisted),
                mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)),
            ):
                selected = _infer_now_playing_idle_art_name_from_persisted_image()
            self.assertEqual(selected, "second.png")


class SettingsRenderTests(unittest.TestCase):
    def test_settings_selected_item_scroll_delay_is_one_second(self):
        self.assertEqual(SETTINGS_ITEM_SCROLL_DELAY_S, 1.0)
        self.assertEqual(MUSIC_ITEM_SCROLL_DELAY_S, 1.0)

    def test_render_settings_selected_row_scroll_changes_pixels(self):
        epd = FakeEPD(write_frames=False)
        status = StatusPlumbing().read()
        fonts = load_fonts()
        long_label = (
            "Import Folder: /home/jrwhite/PiPodSync/inbox/"
            "this/is/a/very/long/path/that/must/scroll/to/read"
        )
        view = SettingsViewState(
            view_id="settings_test",
            title="Settings",
            items=(
                SettingsItem(
                    id="settings:test:item",
                    label=long_label,
                    kind="info",
                    help_text="Long path",
                ),
            ),
            selected_idx=0,
        )

        image_start = render_settings_browser(
            epd=epd,
            fonts=fonts,
            view_state=view,
            status=status,
            charge_anim_frame=0,
            selected_label="test",
            footer_scroll_px=0,
            selected_item_scroll_px=0,
        )
        image_scrolled = render_settings_browser(
            epd=epd,
            fonts=fonts,
            view_state=view,
            status=status,
            charge_anim_frame=0,
            selected_label="test",
            footer_scroll_px=0,
            selected_item_scroll_px=48,
        )

        row_box = (11, 34, epd.width - 9, 52)
        self.assertNotEqual(image_start.crop(row_box).tobytes(), image_scrolled.crop(row_box).tobytes())

    def test_render_music_selected_playlist_row_scroll_changes_pixels(self):
        epd = FakeEPD(write_frames=False)
        status = StatusPlumbing().read()
        fonts = load_fonts()
        long_label = (
            "Road Trip Summer 2026 Extended Set With Very Long Playlist Name For Scroll Testing"
        )
        view = MusicViewState(
            title="Playlists",
            items=(
                MusicItem(
                    id="playlist:user:road_trip",
                    label=long_label,
                    icon="playlist",
                    kind="playlist_group",
                    track_paths=(),
                    child_items=(MusicItem(id="playlist:user:road_trip:play_all", label="Play All", icon="song", kind="song"),),
                ),
            ),
            selected_idx=0,
        )

        image_start = render_music_browser(
            epd=epd,
            fonts=fonts,
            view_state=view,
            status=status,
            charge_anim_frame=0,
            selected_label="test",
            footer_scroll_px=0,
            selected_item_scroll_px=0,
        )
        image_scrolled = render_music_browser(
            epd=epd,
            fonts=fonts,
            view_state=view,
            status=status,
            charge_anim_frame=0,
            selected_label="test",
            footer_scroll_px=0,
            selected_item_scroll_px=48,
        )

        row_box = (29, 34, epd.width - 18, 52)
        self.assertNotEqual(image_start.crop(row_box).tobytes(), image_scrolled.crop(row_box).tobytes())


class InputParsingTests(unittest.TestCase):
    def test_parse_input_token_supports_left_and_right(self):
        self.assertEqual(parse_input_token("left"), "LEFT")
        self.assertEqual(parse_input_token("right"), "RIGHT")

    def test_parse_input_token_keeps_existing_shortcuts(self):
        self.assertEqual(parse_input_token("r"), "RESCAN_LIBRARY")
        self.assertEqual(parse_input_token("t"), "TOGGLE_ART_MODE")
        self.assertEqual(parse_input_token("u"), "UP")
        self.assertEqual(parse_input_token("d"), "DOWN")
        self.assertEqual(parse_input_token("s"), "SELECT")
        self.assertEqual(parse_input_token("b"), "BACK")

    def test_parse_input_token_supports_select_hold(self):
        self.assertEqual(parse_input_token("hold"), "SELECT_HOLD")
        self.assertEqual(parse_input_token("select_hold"), "SELECT_HOLD")


class FakeButton:
    def __init__(
        self,
        pin: int,
        pull_up: bool,
        bounce_time: float,
        hold_time: float | None = None,
        hold_repeat: bool | None = None,
    ):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.hold_time = hold_time
        self.hold_repeat = hold_repeat
        self.when_pressed = None
        self.when_released = None
        self.when_held = None
        self.closed = False

    def press(self):
        if callable(self.when_pressed):
            self.when_pressed()

    def release(self):
        if callable(self.when_released):
            self.when_released()

    def hold(self):
        if callable(self.when_held):
            self.when_held()

    def close(self):
        self.closed = True


class GpioInputProviderTests(unittest.TestCase):
    def test_gpio_five_way_config_from_env_defaults(self):
        with mock.patch.dict(
            os.environ,
            {
                "PIPOD_GPIO_ENABLED": "",
                "PIPOD_GPIO_UP_PIN": "",
                "PIPOD_GPIO_DOWN_PIN": "",
                "PIPOD_GPIO_LEFT_PIN": "",
                "PIPOD_GPIO_RIGHT_PIN": "",
                "PIPOD_GPIO_SELECT_PIN": "",
                "PIPOD_GPIO_VOL_UP_PIN": "",
                "PIPOD_GPIO_VOL_DOWN_PIN": "",
                "PIPOD_GPIO_DEBOUNCE_MS": "",
                "PIPOD_GPIO_SELECT_HOLD_MS": "",
                "PIPOD_GPIO_PULL_UP": "",
            },
        ):
            config = GpioFiveWayConfig.from_env()
        self.assertEqual(config.up_pin, 5)
        self.assertEqual(config.down_pin, 6)
        self.assertEqual(config.left_pin, 12)
        self.assertEqual(config.right_pin, 13)
        self.assertEqual(config.select_pin, 19)
        self.assertEqual(config.vol_up_pin, 20)
        self.assertEqual(config.vol_down_pin, 21)
        self.assertEqual(config.debounce_ms, 70)
        self.assertEqual(config.select_hold_ms, 1200)
        self.assertTrue(config.pull_up)

    def test_gpio_five_way_config_from_env_supports_volume_and_hold_settings(self):
        with mock.patch.dict(
            os.environ,
            {
                "PIPOD_GPIO_VOL_UP_PIN": "22",
                "PIPOD_GPIO_VOL_DOWN_PIN": "23",
                "PIPOD_GPIO_SELECT_HOLD_MS": "900",
            },
        ):
            config = GpioFiveWayConfig.from_env()

        self.assertEqual(config.vol_up_pin, 22)
        self.assertEqual(config.vol_down_pin, 23)
        self.assertEqual(config.select_hold_ms, 900)
        self.assertAlmostEqual(config.select_hold_time_s, 0.9, places=6)

    def test_gpio_input_enqueues_events_and_closes_buttons(self):
        created: list[FakeButton] = []

        def fake_factory(
            pin: int,
            pull_up: bool,
            bounce_time: float,
            hold_time: float | None = None,
            hold_repeat: bool | None = None,
        ):
            button = FakeButton(
                pin=pin,
                pull_up=pull_up,
                bounce_time=bounce_time,
                hold_time=hold_time,
                hold_repeat=hold_repeat,
            )
            created.append(button)
            return button

        config = GpioFiveWayConfig(
            enabled=True,
            up_pin=5,
            down_pin=6,
            left_pin=12,
            right_pin=13,
            select_pin=19,
            vol_up_pin=20,
            vol_down_pin=21,
            debounce_ms=70,
            select_hold_ms=1200,
            pull_up=True,
        )
        gpio_input = GpioFiveWayInput(config=config, button_factory=fake_factory)

        for button in created:
            self.assertAlmostEqual(button.bounce_time, 0.07, places=6)
            self.assertTrue(button.pull_up)

        for button in created:
            if button.pin == config.select_pin:
                button.release()
            else:
                button.press()
        self.assertEqual(
            [gpio_input.poll_nonblocking() for _ in range(7)],
            ["UP", "DOWN", "LEFT", "RIGHT", "SELECT", "VOL_UP", "VOL_DOWN"],
        )
        self.assertIsNone(gpio_input.poll_nonblocking())

        gpio_input.close()
        self.assertTrue(all(button.closed for button in created))

    def test_gpio_select_hold_suppresses_release_select(self):
        created: list[FakeButton] = []

        def fake_factory(
            pin: int,
            pull_up: bool,
            bounce_time: float,
            hold_time: float | None = None,
            hold_repeat: bool | None = None,
        ):
            button = FakeButton(
                pin=pin,
                pull_up=pull_up,
                bounce_time=bounce_time,
                hold_time=hold_time,
                hold_repeat=hold_repeat,
            )
            created.append(button)
            return button

        config = GpioFiveWayConfig(select_hold_ms=1500)
        gpio_input = GpioFiveWayInput(config=config, button_factory=fake_factory)
        select_button = next(button for button in created if button.pin == config.select_pin)

        self.assertAlmostEqual(select_button.hold_time, 1.5, places=6)
        self.assertFalse(select_button.hold_repeat)

        select_button.hold()
        select_button.release()

        self.assertEqual(gpio_input.poll_nonblocking(), "SELECT_HOLD")
        self.assertIsNone(gpio_input.poll_nonblocking())
        gpio_input.close()

    def test_combined_event_provider_prioritizes_gpio_queue(self):
        created: list[FakeButton] = []

        def fake_factory(
            pin: int,
            pull_up: bool,
            bounce_time: float,
            hold_time: float | None = None,
            hold_repeat: bool | None = None,
        ):
            button = FakeButton(
                pin=pin,
                pull_up=pull_up,
                bounce_time=bounce_time,
                hold_time=hold_time,
                hold_repeat=hold_repeat,
            )
            created.append(button)
            return button

        gpio_input = GpioFiveWayInput(GpioFiveWayConfig(), button_factory=fake_factory)
        created[2].press()  # LEFT

        calls: list[float] = []

        def keyboard_provider(timeout_s: float):
            calls.append(timeout_s)
            return "UP"

        combined = CombinedEventProvider(keyboard_provider, gpio_input=gpio_input)
        self.assertEqual(combined(0.1), "LEFT")
        self.assertEqual(calls, [])

        self.assertEqual(combined(0.1), "UP")
        self.assertEqual(calls, [0.1])

        combined.close()
        self.assertTrue(all(button.closed for button in created))


class ScriptedEventProvider:
    def __init__(self, events: list[str]):
        self._events = list(events)
        self._index = 0

    def __call__(self, timeout_s: float) -> str | None:
        _ = timeout_s
        if self._index >= len(self._events):
            return "QUIT"
        event = self._events[self._index]
        self._index += 1
        return event


class MusicBrowserTests(unittest.TestCase):
    def test_load_playlists_manifest_resolves_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            music_root = Path(temp_dir) / "music"
            track_a = music_root / "Artist One" / "Album One" / "01 Song A.mp3"
            track_b = music_root / "Artist One" / "Album One" / "02 Song B.mp3"
            (music_root / "Artist One" / "Album One").mkdir(parents=True, exist_ok=True)
            manifest = {
                "playlists": {
                    "Road Trip": [
                        "Artist One/Album One/01 Song A.mp3",
                        "./Artist One/Album One/02 Song B.mp3",
                        "missing/song.mp3",
                        "Artist One/Album One/01 Song A.mp3",
                    ]
                }
            }
            (music_root / "playlists.json").write_text(json.dumps(manifest), encoding="utf-8")
            tracks = [
                SimpleNamespace(path=track_a),
                SimpleNamespace(path=track_b),
            ]

            playlists = load_playlists_manifest(music_root, tracks)
            self.assertEqual(len(playlists), 1)
            self.assertEqual(playlists[0][0], "Road Trip")
            self.assertEqual(playlists[0][1], (track_a, track_b))

    def test_build_music_index_includes_custom_playlists(self):
        fixture_library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        tracks = fixture_library.all_tracks()
        fixture_library.close()

        custom_playlist_paths = (tracks[0].path, tracks[1].path)
        root_items = build_music_index(
            tracks,
            playlists=(("Road Trip", custom_playlist_paths),),
        )
        playlists_root = root_items[0]
        playlist_labels = [item.label for item in playlists_root.child_items]
        self.assertEqual(playlist_labels, ["All Songs", "Road Trip"])
        self.assertEqual(playlists_root.child_items[1].track_paths, custom_playlist_paths)

    def _run_scripted(self, events: list[str]) -> tuple[dict, MockPlayer]:
        stats, player, _ = self._run_scripted_with_epd(events)
        return stats, player

    def _run_scripted_with_epd(
        self,
        events: list[str],
        cleanup: bool = True,
    ) -> tuple[dict, MockPlayer, FakeEPD]:
        library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        player.set_track_durations(library.duration_map())
        epd = FakeEPD(write_frames=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_store = SettingsStore(Path(temp_dir) / "settings.json")
            dependencies = RuntimeDependencies(
                display=epd,
                library=library,
                player=player,
                event_provider=ScriptedEventProvider(events),
                fonts=load_fonts(),
                status_plumbing=StatusPlumbing(),
                settings_store=settings_store,
                settings_actions=FakeSettingsActions(music_dir=Path("/sim/music")),
            )
            config = RunConfig(
                timeout_s=0.0,
                max_steps=200,
                interactive=False,
                show_controls_log=False,
                initialize_display=False,
                clear_display_on_start=False,
                loop_step_s=0.5,
                raise_exceptions=True,
            )

            try:
                stats = run_pipod_loop(config, dependencies)
            finally:
                library.close()
                if cleanup:
                    player.shutdown()
                    epd.sleep()

        return stats, player, epd

    def test_music_root_entries_and_view(self):
        fixture_library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        tracks = fixture_library.all_tracks()
        fixture_library.close()
        root_items = build_music_index(tracks)
        self.assertEqual([item.label for item in root_items], ["Playlists", "Artists", "Albums", "Songs"])

        stats, _ = self._run_scripted(["SELECT", "QUIT"])
        self.assertEqual(stats["final_view"], "music_root")

    def test_artists_path_starts_playback(self):
        stats, player = self._run_scripted(
            [
                "SELECT",  # Music root
                "DOWN",  # Artists
                "SELECT",
                "SELECT",  # first artist
                "SELECT",  # first album
                "SELECT",  # first song
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_list")
        self.assertEqual(str(player.current_track_path()), "/sim/Aphex Twin/Selected Ambient Works 85-92/03 Xtal.mp3")

    def test_albums_path_starts_playback(self):
        stats, player = self._run_scripted(
            [
                "SELECT",  # Music root
                "DOWN",
                "DOWN",  # Albums
                "SELECT",
                "SELECT",  # first album
                "SELECT",  # first song
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_list")
        self.assertEqual(
            str(player.current_track_path()),
            "/sim/Nina Simone/Anthology/07 Sinnerman (Live at the Village Gate - Extra Long Deliberately Verbose Test Title Version).mp3",
        )
        self.assertEqual(stats["now_playing_context_label"], "Anthology - Nina Simone")

    def test_songs_path_starts_contextual_queue(self):
        stats, player = self._run_scripted(
            [
                "SELECT",  # Music root
                "DOWN",
                "DOWN",
                "DOWN",  # Songs
                "SELECT",
                "DOWN",
                "DOWN",  # select third song in sorted list
                "SELECT",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_list")
        self.assertEqual(
            str(player.current_track_path()),
            "/sim/Kendrick Lamar/good kid, m.A.A.d city/09 m.A.A.d city.mp3",
        )
        self.assertTrue(player.next_track())
        self.assertEqual(
            str(player.current_track_path()),
            "/sim/Fleetwood Mac/Rumours/02 Never Going Back Again.mp3",
        )

    def test_end_of_non_looping_list_falls_back_to_all_songs(self):
        library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        short_durations = {track.path: 1 for track in library.all_tracks()}
        player.set_track_durations(short_durations)
        player.toggle_loop()  # default is on; switch off for this behavior
        expected_all_songs_first = library.all_tracks()[0].path

        epd = FakeEPD(write_frames=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_store = SettingsStore(Path(temp_dir) / "settings.json")
            dependencies = RuntimeDependencies(
                display=epd,
                library=library,
                player=player,
                event_provider=ScriptedEventProvider(
                    [
                        "SELECT",  # Music root
                        "DOWN",
                        "DOWN",  # Albums
                        "SELECT",
                        "SELECT",  # first album (single-track anthology)
                        "SELECT",  # first song
                        "WAIT",  # allow poll() to reach queue end and trigger fallback
                        "QUIT",
                    ]
                ),
                fonts=load_fonts(),
                status_plumbing=StatusPlumbing(),
                settings_store=settings_store,
                settings_actions=FakeSettingsActions(music_dir=Path("/sim/music")),
            )
            config = RunConfig(
                timeout_s=0.0,
                max_steps=50,
                interactive=False,
                show_controls_log=False,
                initialize_display=False,
                clear_display_on_start=False,
                loop_step_s=1.0,
                raise_exceptions=True,
            )

            try:
                stats = run_pipod_loop(config, dependencies)
            finally:
                library.close()
                player.shutdown()
                epd.sleep()

        self.assertEqual(stats["final_view"], "music_list")
        self.assertEqual(stats["now_playing_context_label"], "All Songs")
        self.assertEqual(player.current_track_path(), expected_all_songs_first)

    def test_playlists_view_excludes_shuffle_all_entry(self):
        fixture_library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        tracks = fixture_library.all_tracks()
        fixture_library.close()
        playlist_labels = [item.label for item in build_music_index(tracks)[0].child_items]
        self.assertNotIn("Shuffle All", playlist_labels)

    def test_all_songs_playlist_play_all_starts_playback(self):
        stats, player = self._run_scripted(
            [
                "SELECT",  # Music root
                "SELECT",  # Playlists
                "SELECT",  # All Songs playlist entry
                "SELECT",  # Play All
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_list")
        self.assertIsNotNone(player.current_track_path())
        self.assertEqual(stats["now_playing_context_label"], "All Songs")

    def test_all_songs_playlist_song_selection_starts_selected_song(self):
        fixture_library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        tracks = fixture_library.all_tracks()
        fixture_library.close()
        all_songs_view = build_music_index(tracks)[0].child_items[0].child_items
        expected_path = Path(all_songs_view[2].track_paths[0])

        stats, player = self._run_scripted(
            [
                "SELECT",  # Music root
                "SELECT",  # Playlists
                "SELECT",  # All Songs playlist entry
                "DOWN",  # first song row
                "DOWN",  # second song row
                "SELECT",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_list")
        self.assertEqual(player.current_track_path(), expected_path)

    def test_now_playing_preserves_playlist_context_label(self):
        stats, _ = self._run_scripted(
            [
                "SELECT",  # Music root
                "SELECT",  # Playlists
                "SELECT",  # All Songs playlist entry
                "SELECT",  # Play All
                "BACK",  # Playlists
                "BACK",  # Music root
                "BACK",  # Menu
                "DOWN",  # Now Playing
                "SELECT",  # enter now playing
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "now_playing")
        self.assertEqual(stats["now_playing_context_label"], "All Songs")

    def test_back_navigation_returns_to_menu(self):
        stats, _ = self._run_scripted(
            [
                "SELECT",  # Music root
                "DOWN",  # Artists
                "SELECT",  # enter artists
                "BACK",  # back to music root
                "BACK",  # back to menu
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")
        self.assertEqual(stats["selected_menu_item"], "Music")

    def test_right_alias_select_enters_music_root(self):
        stats, _ = self._run_scripted(["RIGHT", "QUIT"])
        self.assertEqual(stats["final_view"], "music_root")

    def test_left_alias_back_navigation_returns_to_menu(self):
        stats, _ = self._run_scripted(
            [
                "RIGHT",  # Music root
                "DOWN",  # Artists
                "RIGHT",  # enter artists
                "LEFT",  # back to music root
                "LEFT",  # back to menu
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")
        self.assertEqual(stats["selected_menu_item"], "Music")

    def test_left_alias_back_from_now_playing_returns_menu(self):
        stats, _ = self._run_scripted(
            [
                "DOWN",  # Now Playing
                "RIGHT",  # enter now playing via alias
                "LEFT",  # back to menu via alias
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")

    def test_right_alias_select_activates_now_playing_focus(self):
        stats, player = self._run_scripted(
            [
                "DOWN",  # Now Playing
                "RIGHT",  # enter now playing
                "RIGHT",  # activate focused PLAY_PAUSE
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "now_playing")
        self.assertIsNotNone(player.current_track_path())
        self.assertFalse(player.state().is_shuffle)
        self.assertEqual(stats["now_playing_context_label"], "All Songs")

    def test_menu_shuffle_all_sets_all_songs_context(self):
        stats, player = self._run_scripted(
            [
                "DOWN",  # Now Playing
                "DOWN",  # Shuffle All
                "SELECT",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")
        self.assertIsNotNone(player.current_track_path())
        self.assertTrue(player.state().is_shuffle)
        self.assertEqual(stats["now_playing_context_label"], "All Songs")

    def test_menu_footer_select_enters_now_playing(self):
        stats, _ = self._run_scripted(
            [
                "DOWN",  # Now Playing
                "DOWN",  # Shuffle All
                "DOWN",  # Settings
                "DOWN",  # Footer
                "SELECT",  # open now playing from footer
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "now_playing")

    def test_music_footer_select_enters_now_playing(self):
        stats, _ = self._run_scripted(
            [
                "SELECT",  # Music root
                "DOWN",  # Artists
                "DOWN",  # Albums
                "DOWN",  # Songs
                "DOWN",  # Footer
                "SELECT",  # open now playing from footer
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "now_playing")

    def test_select_hold_opens_power_dialog_without_changing_view(self):
        stats, _, _ = self._run_scripted_with_epd(
            [
                "SELECT",  # Music root
                "SELECT_HOLD",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_root")
        self.assertEqual(stats["power_state"], "active")
        self.assertGreaterEqual(stats["frames_partial"], 2)

    def test_power_dialog_back_cancels_without_changing_view(self):
        stats, _, _ = self._run_scripted_with_epd(
            [
                "SELECT",  # Music root
                "SELECT_HOLD",
                "BACK",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_root")
        self.assertEqual(stats["power_state"], "active")

    def test_sleep_blanks_display_continues_playback_and_wakes_on_select(self):
        stats, player, epd = self._run_scripted_with_epd(
            [
                "PLAY_PAUSE",
                "SELECT_HOLD",
                "SELECT",  # Sleep
                "WAIT",
                "WAIT",
                "DOWN",  # ignored while sleeping
                "SELECT",  # wake
                "QUIT",
            ],
            cleanup=False,
        )
        try:
            progress_s, _ = player.playback_progress()
            self.assertEqual(stats["final_view"], "menu")
            self.assertEqual(stats["power_state"], "active")
            self.assertGreater(progress_s, 0.0)
            self.assertFalse(epd._sleeping)
        finally:
            player.shutdown()
            epd.sleep()

    def test_soft_off_stops_playback_ignores_normal_input_and_wakes_on_select_hold(self):
        stats, player, epd = self._run_scripted_with_epd(
            [
                "PLAY_PAUSE",
                "SELECT_HOLD",
                "DOWN",  # Power Off
                "SELECT",
                "DOWN",  # ignored while soft-off
                "SELECT",  # ignored while soft-off
                "SELECT_HOLD",  # wake
                "QUIT",
            ],
            cleanup=False,
        )
        try:
            self.assertEqual(stats["final_view"], "menu")
            self.assertEqual(stats["selected_menu_item"], "Music")
            self.assertEqual(stats["power_state"], "active")
            self.assertFalse(player.is_playing())
            self.assertFalse(epd._sleeping)
        finally:
            player.shutdown()
            epd.sleep()

    def test_now_playing_toggle_art_mode_triggers_redraw(self):
        stats, _ = self._run_scripted(
            [
                "DOWN",  # Now Playing
                "RIGHT",  # enter now playing
                "TOGGLE_ART_MODE",
                "TOGGLE_ART_MODE",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "now_playing")
        self.assertGreaterEqual(stats["frames_partial"], 4)

    def test_play_pause_from_empty_queue_starts_random_without_shuffle(self):
        stats, player = self._run_scripted(
            [
                "PLAY_PAUSE",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")
        self.assertIsNotNone(player.current_track_path())
        self.assertFalse(player.state().is_shuffle)
        self.assertEqual(stats["now_playing_context_label"], "All Songs")

    def test_menu_settings_enters_settings_root(self):
        stats, _ = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "settings_root")
        self.assertEqual(stats["selected_menu_item"], "Settings")

    def test_settings_back_returns_to_menu(self):
        stats, _ = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",
                "BACK",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")
        self.assertEqual(stats["selected_menu_item"], "Settings")

    def test_right_left_alias_supports_settings_navigation(self):
        stats, _ = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "RIGHT",
                "LEFT",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "menu")
        self.assertEqual(stats["selected_menu_item"], "Settings")


class SettingsStoreTests(unittest.TestCase):
    def test_settings_store_creates_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            settings = store.load()
            self.assertEqual(settings, PersistedSettings())
            self.assertTrue(settings_path.exists())

    def test_settings_store_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            expected = PersistedSettings(
                audio_output_mode="bluetooth",
                music_import_dir="/tmp/import",
                last_connected_bt_address="AA:BB:CC:DD:EE:FF",
                album_art_mode="enhanced_plus",
            )
            store.save(expected)
            actual = store.load()
            self.assertEqual(actual, expected)

    def test_settings_store_round_trip_with_now_playing_idle_art(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            expected = PersistedSettings(now_playing_idle_art="raccoon.png")
            store.save(expected)
            actual = store.load()
            self.assertEqual(actual.now_playing_idle_art, "raccoon.png")

    def test_settings_store_round_trip_with_now_playing_progress_ring(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            expected = PersistedSettings(now_playing_progress_ring=True)
            store.save(expected)
            actual = store.load()
            self.assertTrue(actual.now_playing_progress_ring)

    def test_settings_store_migrates_legacy_speaker_mode_to_aux(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "audio_output_mode": "speaker",
                        "music_import_dir": "/tmp/import",
                        "last_connected_bt_address": None,
                    }
                ),
                encoding="utf-8",
            )
            store = SettingsStore(settings_path)
            settings = store.load()
            self.assertEqual(settings.audio_output_mode, "aux")

    def test_settings_store_invalid_album_art_mode_defaults_to_enhanced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "audio_output_mode": "auto",
                        "music_import_dir": "/tmp/import",
                        "last_connected_bt_address": None,
                        "album_art_mode": "invalid",
                    }
                ),
                encoding="utf-8",
            )
            store = SettingsStore(settings_path)
            settings = store.load()
            self.assertEqual(settings.album_art_mode, "enhanced")


class SettingsActionsTests(unittest.TestCase):
    def test_sync_music_from_import_copies_supported_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            import_dir = root / "inbox"
            music_dir = root / "music"
            (import_dir / "Artist/Album").mkdir(parents=True, exist_ok=True)
            (import_dir / "Artist/Album/track01.mp3").write_bytes(b"mp3-a")
            (import_dir / "Artist/Album/track02.flac").write_bytes(b"flac-b")
            (import_dir / "Artist/Album/notes.txt").write_text("ignore me", encoding="utf-8")

            actions = SettingsActions(music_dir=music_dir)
            result = actions.sync_music_from_import(import_dir)
            self.assertTrue(result.ok)
            self.assertEqual(result.details["imported"], 2)
            self.assertEqual(result.details["skipped"], 1)
            self.assertEqual(result.details["errors"], 0)
            self.assertTrue((music_dir / "Artist/Album/track01.mp3").exists())
            self.assertTrue((music_dir / "Artist/Album/track02.flac").exists())

    def test_sync_music_from_import_missing_folder_returns_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            actions = SettingsActions(music_dir=root / "music")
            result = actions.sync_music_from_import(root / "missing")
            self.assertFalse(result.ok)
            self.assertIn("Import folder missing", result.message)

    def test_bluetooth_scan_returns_unavailable_when_bluetoothctl_missing(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"))
        with mock.patch("settings_actions.shutil.which", return_value=None):
            result = actions.bluetooth_scan()
        self.assertFalse(result.ok)
        self.assertIn("bluetoothctl missing", result.message.lower())

    def test_parse_scan_discoveries_tracks_name_updates(self):
        output = "\n".join(
            [
                "[NEW] Device AA:BB:CC:DD:EE:01 Unknown Device",
                "[CHG] Device AA:BB:CC:DD:EE:01 Name: Pixel Buds Pro",
                "[CHG] Device AA:BB:CC:DD:EE:02 Alias: Desk Speaker",
                "[CHG] Device AA:BB:CC:DD:EE:03 RSSI: -67",
                "Device AA:BB:CC:DD:EE:04 Not Nearby List Entry",
            ]
        )
        parsed = SettingsActions._parse_scan_discoveries(output)
        self.assertEqual(
            parsed,
            [
                ("AA:BB:CC:DD:EE:01", "Pixel Buds Pro"),
                ("AA:BB:CC:DD:EE:02", "Desk Speaker"),
            ],
        )

    def test_merge_devices_prefers_discovered_names_and_fills_placeholders(self):
        merged = SettingsActions._merge_devices(
            primary=[
                ("AA:BB:CC:DD:EE:01", "Scan Name"),
                ("AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:02"),
            ],
            secondary=[
                ("AA:BB:CC:DD:EE:01", "Paired Name"),
                ("AA:BB:CC:DD:EE:02", "Paired Better Name"),
                ("AA:BB:CC:DD:EE:03", "Paired Only"),
            ],
        )
        self.assertEqual(
            merged,
            [
                ("AA:BB:CC:DD:EE:01", "Scan Name"),
                ("AA:BB:CC:DD:EE:02", "Paired Better Name"),
                ("AA:BB:CC:DD:EE:03", "Paired Only"),
            ],
        )

    def test_bluetooth_scan_prepares_adapter_and_merges_discovered_and_paired(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        scan_output = "\n".join(
            [
                "[NEW] Device AA:BB:CC:DD:EE:11 Nearby Device",
                "[CHG] Device AA:BB:CC:DD:EE:11 Name: Nearby Renamed",
            ]
        )

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt", return_value="ok") as run_bt,
            mock.patch.object(actions, "_run_bt_interactive_scan", return_value=scan_output) as run_scan,
            mock.patch.object(
                actions,
                "_list_devices",
                side_effect=lambda command: [
                    ("AA:BB:CC:DD:EE:11", "Known Name"),
                    ("AA:BB:CC:DD:EE:33", "Known Only"),
                ]
                if command == "devices"
                else [("AA:BB:CC:DD:EE:22", "Paired Only")],
            ) as list_devices,
            mock.patch.object(actions, "_devices_with_state", return_value=[]) as with_state,
        ):
            result = actions.bluetooth_scan(duration_s=4)

        run_bt.assert_has_calls(
            [
                mock.call(["power", "on"]),
                mock.call(["agent", "on"]),
                mock.call(["default-agent"]),
                mock.call(["pairable", "on"]),
            ],
            any_order=False,
        )
        run_scan.assert_called_once_with(4)
        list_devices.assert_has_calls(
            [mock.call(command="devices"), mock.call(command="paired-devices")],
            any_order=False,
        )
        with_state.assert_called_once_with(
            [
                ("AA:BB:CC:DD:EE:11", "Nearby Renamed"),
                ("AA:BB:CC:DD:EE:33", "Known Only"),
                ("AA:BB:CC:DD:EE:22", "Paired Only"),
            ]
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Found 1 named nearby, 2 known, 1 paired")

    def test_bluetooth_scan_treats_agent_setup_as_best_effort(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        prep_outputs = {
            ("power", "on"): "Changing power on succeeded",
            ("agent", "on"): "Failed to register agent: mystery BlueZ agent error",
            ("default-agent",): "Failed to request default agent: no agent",
            ("pairable", "on"): "Failed to set pairable on: transient error",
        }

        def fake_run_bt(command):
            return prep_outputs.get(tuple(command), "ok")

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt", side_effect=fake_run_bt),
            mock.patch.object(
                actions,
                "_run_bt_interactive_scan",
                return_value="[NEW] Device AA:BB:CC:DD:EE:11 Named Headphones",
            ) as run_scan,
            mock.patch.object(actions, "_list_devices", return_value=[]),
            mock.patch.object(actions, "_devices_with_state", return_value=[]),
        ):
            result = actions.bluetooth_scan(duration_s=4)

        run_scan.assert_called_once_with(4)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Found 1 named nearby, 0 known, 0 paired")

    def test_bluetooth_scan_filters_unnamed_discoveries_but_keeps_paired_devices(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        scan_output = "\n".join(
            [
                "[NEW] Device AA:BB:CC:DD:EE:11",
                "[NEW] Device AA:BB:CC:DD:EE:22 Unknown Device",
                "[NEW] Device AA:BB:CC:DD:EE:33 Named Headphones",
                "[NEW] Device AA:BB:CC:DD:EE:44 123456",
            ]
        )

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt", return_value="ok"),
            mock.patch.object(actions, "_run_bt_interactive_scan", return_value=scan_output),
            mock.patch.object(
                actions,
                "_list_devices",
                side_effect=lambda command: [("AA:BB:CC:DD:EE:55", "Known Buds")]
                if command == "devices"
                else [("AA:BB:CC:DD:EE:11", "Already Paired")],
            ),
            mock.patch.object(actions, "_devices_with_state", return_value=[]) as with_state,
        ):
            result = actions.bluetooth_scan(duration_s=4)

        with_state.assert_called_once_with(
            [
                ("AA:BB:CC:DD:EE:33", "Named Headphones"),
                ("AA:BB:CC:DD:EE:55", "Known Buds"),
                ("AA:BB:CC:DD:EE:11", "Already Paired"),
            ]
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.details["nearby_count"], 1)
        self.assertEqual(result.details["raw_nearby_count"], 4)
        self.assertEqual(result.details["known_count"], 1)
        self.assertEqual(result.message, "Found 1 named nearby, 1 known, 1 paired")

    def test_bluetooth_saved_devices_include_named_known_devices_when_paired_list_is_empty(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        known_device = BluetoothDevice(
            address="90:9C:4A:E6:E7:F2",
            name="Trusted Headphones",
            paired=False,
            connected=True,
            trusted=True,
        )

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(
                actions,
                "_list_devices",
                side_effect=lambda command: [("90:9C:4A:E6:E7:F2", "Trusted Headphones")]
                if command == "devices"
                else [],
            ) as list_devices,
            mock.patch.object(actions, "_devices_with_state", return_value=[known_device]) as with_state,
        ):
            result = actions.bluetooth_paired_devices()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "1 saved device(s)")
        self.assertEqual(result.details["devices"], [known_device])
        list_devices.assert_has_calls(
            [mock.call(command="paired-devices"), mock.call(command="devices")],
            any_order=False,
        )
        with_state.assert_called_once_with([("90:9C:4A:E6:E7:F2", "Trusted Headphones")])

    def test_bluetooth_scan_timeout_returns_error_with_paired_fallback(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        fallback_devices = [
            BluetoothDevice(
                address="AA:BB:CC:DD:EE:33",
                name="Fallback Paired",
                paired=True,
                connected=False,
                trusted=True,
            )
        ]

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt", return_value="ok"),
            mock.patch.object(actions, "_run_bt_interactive_scan", side_effect=TimeoutError("scan timeout")),
            mock.patch.object(actions, "_list_devices", return_value=[("AA:BB:CC:DD:EE:33", "Fallback Paired")]),
            mock.patch.object(actions, "_devices_with_state", return_value=fallback_devices),
        ):
            result = actions.bluetooth_scan(duration_s=3)

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Bluetooth scan timed out")
        self.assertEqual(result.details["devices"], fallback_devices)

    def test_bluetooth_pair_connect_uses_single_agent_session_and_accepts_already_paired(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        session_output = "\n".join(
            [
                "Agent registered",
                "Default agent request successful",
                "Failed to pair: org.bluez.Error.AlreadyExists",
                "Changing AA:BB:CC:DD:EE:44 trust succeeded",
                "Connection successful",
            ]
        )
        info_output = "\n".join(
            [
                "Device AA:BB:CC:DD:EE:44",
                "Paired: yes",
                "Trusted: yes",
                "Connected: yes",
            ]
        )

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt_session", return_value=session_output) as run_session,
            mock.patch.object(actions, "_run_bt", return_value=info_output) as run_bt,
            mock.patch.object(
                actions,
                "_set_default_bluetooth_sink",
                return_value={"ok": True, "sink": "bluez_output.AA_BB_CC_DD_EE_44.a2dp-sink"},
            ) as set_sink,
        ):
            result = actions.bluetooth_pair_connect("aa:bb:cc:dd:ee:44")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Connected headphones")
        run_session.assert_called_once_with(
            [
                "power on",
                "agent on",
                "default-agent",
                "pairable on",
                "pair AA:BB:CC:DD:EE:44",
                "trust AA:BB:CC:DD:EE:44",
                "connect AA:BB:CC:DD:EE:44",
            ],
            timeout=45,
        )
        run_bt.assert_called_once_with(["info", "AA:BB:CC:DD:EE:44"])
        set_sink.assert_called_once_with("AA:BB:CC:DD:EE:44")
        self.assertTrue(result.details["audio_sink"]["ok"])

    def test_bluetooth_pair_connect_accepts_trusted_connected_device_even_when_not_paired(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        session_output = "\n".join(
            [
                "Failed to pair: org.bluez.Error.AuthenticationFailed",
                "Changing 90:9C:4A:E6:E7:F2 trust succeeded",
                "Connection successful",
            ]
        )
        info_output = "\n".join(
            [
                "Device 90:9C:4A:E6:E7:F2",
                "Paired: no",
                "Trusted: yes",
                "Connected: yes",
            ]
        )

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt_session", return_value=session_output),
            mock.patch.object(actions, "_run_bt", return_value=info_output),
            mock.patch.object(
                actions,
                "_set_default_bluetooth_sink",
                return_value={"ok": True, "sink": "bluez_output.90_9C_4A_E6_E7_F2.a2dp-sink"},
            ) as set_sink,
        ):
            result = actions.bluetooth_pair_connect("90:9c:4a:e6:e7:f2")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Connected headphones")
        self.assertFalse(result.details["paired"])
        self.assertTrue(result.details["trusted"])
        self.assertTrue(result.details["connected"])
        set_sink.assert_called_once_with("90:9C:4A:E6:E7:F2")

    def test_bluetooth_connect_powers_adapter_and_accepts_already_connected(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)
        session_output = "Failed to connect: org.bluez.Error.AlreadyConnected"
        info_output = "\n".join(
            [
                "Device AA:BB:CC:DD:EE:55",
                "Paired: yes",
                "Trusted: yes",
                "Connected: yes",
            ]
        )

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(actions, "_run_bt_session", return_value=session_output) as run_session,
            mock.patch.object(actions, "_run_bt", return_value=info_output),
            mock.patch.object(
                actions,
                "_set_default_bluetooth_sink",
                return_value={"ok": True, "sink": "bluez_output.AA_BB_CC_DD_EE_55.a2dp-sink"},
            ) as set_sink,
        ):
            result = actions.bluetooth_connect("aa:bb:cc:dd:ee:55")

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Connected")
        run_session.assert_called_once_with(
            ["power on", "connect AA:BB:CC:DD:EE:55"],
            timeout=25,
        )
        set_sink.assert_called_once_with("AA:BB:CC:DD:EE:55")
        self.assertTrue(result.details["audio_sink"]["ok"])

    def test_select_bluetooth_sink_prefers_matching_address(self):
        output = "\n".join(
            [
                "0\talsa_output.platform.bcm2835\tmodule-alsa-card.c\t...",
                "1\tbluez_output.11_22_33_44_55_66.a2dp-sink\tmodule-bluez5-device.c\t...",
                "2\tbluez_output.AA_BB_CC_DD_EE_55.a2dp-sink\tmodule-bluez5-device.c\t...",
            ]
        )
        sink = SettingsActions._select_bluetooth_sink(output, "AA_BB_CC_DD_EE_55")
        self.assertEqual(sink, "bluez_output.AA_BB_CC_DD_EE_55.a2dp-sink")

    def test_set_default_bluetooth_sink_sets_default_and_moves_inputs(self):
        actions = SettingsActions(music_dir=Path("/tmp/music"), scan_seconds=6)

        def fake_run(command, **kwargs):
            _ = kwargs
            if command == ["pactl", "list", "short", "sinks"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="1\tbluez_output.AA_BB_CC_DD_EE_55.a2dp-sink\tmodule-bluez5-device.c\t...\n",
                    stderr="",
                )
            if command == ["pactl", "set-default-sink", "bluez_output.AA_BB_CC_DD_EE_55.a2dp-sink"]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            if command == ["pactl", "list", "short", "sink-inputs"]:
                return subprocess.CompletedProcess(command, 0, stdout="42\t1\t23\t...\n", stderr="")
            if command == [
                "pactl",
                "move-sink-input",
                "42",
                "bluez_output.AA_BB_CC_DD_EE_55.a2dp-sink",
            ]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            raise AssertionError(f"Unexpected pactl command: {command}")

        with (
            mock.patch("settings_actions.shutil.which", return_value="/usr/bin/pactl"),
            mock.patch("settings_actions.subprocess.run", side_effect=fake_run),
        ):
            result = actions._set_default_bluetooth_sink("AA:BB:CC:DD:EE:55")

        self.assertTrue(result["ok"])
        self.assertEqual(result["sink"], "bluez_output.AA_BB_CC_DD_EE_55.a2dp-sink")
        self.assertEqual(result["moved_inputs"], 1)


class RuntimeSettingsFlowTests(unittest.TestCase):
    def _run_scripted(
        self,
        events: list[str],
        *,
        settings_actions=None,
    ) -> tuple[dict, Path, object]:
        class CountingFixtureLibrary(FixtureLibrary):
            def __init__(self, fixture_path: Path, seed: int = 1337):
                super().__init__(fixture_path, seed=seed)
                self.scan_calls = 0

            def scan(self):
                self.scan_calls += 1
                return super().scan()

        library = CountingFixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        player.set_track_durations(library.duration_map())
        epd = FakeEPD(write_frames=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            dependencies = RuntimeDependencies(
                display=epd,
                library=library,
                player=player,
                event_provider=ScriptedEventProvider(events),
                fonts=load_fonts(),
                status_plumbing=StatusPlumbing(),
                settings_store=SettingsStore(settings_path),
                settings_actions=settings_actions or FakeSettingsActions(music_dir=Path("/sim/music")),
            )
            config = RunConfig(
                timeout_s=0.0,
                max_steps=220,
                interactive=False,
                show_controls_log=False,
                initialize_display=False,
                clear_display_on_start=False,
                loop_step_s=0.5,
                raise_exceptions=True,
            )
            try:
                stats = run_pipod_loop(config, dependencies)
            finally:
                library.close()
                player.shutdown()
                epd.sleep()
            persisted = SettingsStore(settings_path).load()
            return stats, settings_path, (library, persisted)

    def test_settings_bluetooth_scan_pair_persists_last_device(self):
        stats, _, state = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",  # enter settings
                "SELECT",  # Bluetooth
                "SELECT",  # Scan & Pair Headphones
                "DOWN",  # first discovered device (after Scan Again)
                "SELECT",  # pair/connect
                "QUIT",
            ]
        )
        _, persisted = state
        self.assertEqual(stats["final_view"], "settings_list")
        self.assertIsNotNone(persisted.last_connected_bt_address)

    def test_settings_library_rebuild_triggers_library_rescan(self):
        stats, _, state = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",
                "DOWN",
                "DOWN",
                "DOWN",  # Library
                "SELECT",
                "SELECT",  # Rebuild Library Index
                "QUIT",
            ]
        )
        library, _ = state
        self.assertEqual(stats["final_view"], "settings_list")
        self.assertGreaterEqual(library.scan_calls, 2)

    def test_settings_album_art_mode_persists(self):
        stats, _, state = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",
                "DOWN",
                "DOWN",  # Album Art
                "SELECT",
                "DOWN",
                "DOWN",  # Classic
                "SELECT",
                "QUIT",
            ]
        )
        _, persisted = state
        self.assertEqual(stats["final_view"], "settings_list")
        self.assertEqual(persisted.album_art_mode, "classic")

    def test_settings_album_art_idle_cover_cycles_and_persists(self):
        first = Path("/tmp/first.png")
        second = Path("/tmp/second.png")
        with mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)):
            stats, _, state = self._run_scripted(
                [
                    "DOWN",
                    "DOWN",
                    "DOWN",  # Settings
                    "SELECT",
                    "DOWN",
                    "DOWN",  # Album Art
                    "SELECT",
                    "DOWN",
                    "DOWN",
                    "DOWN",  # Idle Cover
                    "DOWN",  # skip Full-Screen Progress Border
                    "SELECT",
                    "QUIT",
                ]
            )
        _, persisted = state
        self.assertEqual(stats["final_view"], "settings_list")
        self.assertEqual(persisted.now_playing_idle_art, "first.png")

    def test_startup_recovers_idle_cover_selection_from_persisted_metadata(self):
        first = Path("/tmp/first.png")
        second = Path("/tmp/second.png")
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            metadata_path = Path(temp_dir) / "persisted_idle_cover_selection.txt"
            settings_store = SettingsStore(settings_path)
            settings_store.save(PersistedSettings(now_playing_idle_art=None))
            metadata_path.write_text("second.png", encoding="utf-8")

            dependencies = RuntimeDependencies(
                display=FakeEPD(write_frames=False),
                library=FixtureLibrary(FIXTURE_PATH, seed=1337),
                player=MockPlayer(seed=1337),
                event_provider=ScriptedEventProvider(["QUIT"]),
                fonts=load_fonts(),
                status_plumbing=StatusPlumbing(),
                settings_store=settings_store,
                settings_actions=FakeSettingsActions(music_dir=Path("/sim/music")),
            )
            config = RunConfig(
                timeout_s=0.0,
                max_steps=10,
                interactive=False,
                show_controls_log=False,
                initialize_display=False,
                clear_display_on_start=False,
                loop_step_s=0.5,
                raise_exceptions=True,
            )
            try:
                with (
                    mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_SELECTION_PATH", metadata_path),
                    mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)),
                ):
                    run_pipod_loop(config, dependencies)
            finally:
                dependencies.library.close()
                dependencies.player.shutdown()
                dependencies.display.sleep()

            persisted = SettingsStore(settings_path).load()
            self.assertEqual(persisted.now_playing_idle_art, "second.png")

    def test_startup_recovers_idle_cover_selection_from_persisted_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = Path(temp_dir) / "settings.json"
            persisted_cover_path = Path(temp_dir) / "persisted_idle_cover.png"
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (10, 10), "black").save(first, format="PNG")
            Image.new("RGB", (10, 10), "white").save(second, format="PNG")
            Image.new("RGB", (10, 10), "white").save(persisted_cover_path, format="PNG")
            settings_store = SettingsStore(settings_path)
            settings_store.save(PersistedSettings(now_playing_idle_art=None))

            dependencies = RuntimeDependencies(
                display=FakeEPD(write_frames=False),
                library=FixtureLibrary(FIXTURE_PATH, seed=1337),
                player=MockPlayer(seed=1337),
                event_provider=ScriptedEventProvider(["QUIT"]),
                fonts=load_fonts(),
                status_plumbing=StatusPlumbing(),
                settings_store=settings_store,
                settings_actions=FakeSettingsActions(music_dir=Path("/sim/music")),
            )
            config = RunConfig(
                timeout_s=0.0,
                max_steps=10,
                interactive=False,
                show_controls_log=False,
                initialize_display=False,
                clear_display_on_start=False,
                loop_step_s=0.5,
                raise_exceptions=True,
            )
            try:
                with (
                    mock.patch("pipod_runtime.NOW_PLAYING_IDLE_ART_PERSIST_PATH", persisted_cover_path),
                    mock.patch("pipod_runtime._list_now_playing_idle_art_paths", return_value=(first, second)),
                ):
                    run_pipod_loop(config, dependencies)
            finally:
                dependencies.library.close()
                dependencies.player.shutdown()
                dependencies.display.sleep()

            persisted = SettingsStore(settings_path).load()
            self.assertEqual(persisted.now_playing_idle_art, "second.png")

    def test_unavailable_bluetooth_actions_do_not_crash(self):
        class UnavailableSettingsActions(FakeSettingsActions):
            def bluetooth_adapter_status(self):
                return SettingsActionResult(ok=False, message="Bluetooth unavailable")

            def bluetooth_scan(self, duration_s: int | None = None):
                _ = duration_s
                return SettingsActionResult(ok=False, message="Bluetooth unavailable", details={"devices": []})

            def bluetooth_paired_devices(self):
                return SettingsActionResult(ok=False, message="Bluetooth unavailable", details={"devices": []})

        stats, _, _ = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",
                "SELECT",  # Bluetooth
                "SELECT",  # Scan
                "QUIT",
            ],
            settings_actions=UnavailableSettingsActions(music_dir=Path("/sim/music")),
        )
        self.assertIn(stats["final_view"], {"settings_root", "settings_list"})


if __name__ == "__main__":
    unittest.main()
