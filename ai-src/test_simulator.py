#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

from PIL import Image

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from pipod_runtime import (
    NOW_PLAYING_ART_SIZE,
    NOW_PLAYING_ART_TOP,
    NOW_PLAYING_FOCUSABLE,
    NOW_PLAYING_LEFT_MARGIN,
    NOW_PLAYING_PROGRESS_TOP_GAP,
    NOW_PLAYING_TIME_TOP_GAP,
    NOW_PLAYING_TITLE_TOP_GAP,
    NOW_PLAYING_VOLUME_TOP_GAP,
    RunConfig,
    RuntimeDependencies,
    StatusPlumbing,
    VOLUME_SLIDER_KNOB_CENTER_Y_OFFSET,
    _volume_slider_knob_x,
    build_music_index,
    load_fonts,
    parse_input_token,
    render_now_playing,
    run_pipod_loop,
)
from input_provider import CombinedEventProvider, GpioFiveWayConfig, GpioFiveWayInput
from simulator_adapters import FakeEPD, FixtureLibrary, MockPlayer

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


class InputParsingTests(unittest.TestCase):
    def test_parse_input_token_supports_left_and_right(self):
        self.assertEqual(parse_input_token("left"), "LEFT")
        self.assertEqual(parse_input_token("right"), "RIGHT")

    def test_parse_input_token_keeps_existing_shortcuts(self):
        self.assertEqual(parse_input_token("r"), "RESCAN_LIBRARY")
        self.assertEqual(parse_input_token("u"), "UP")
        self.assertEqual(parse_input_token("d"), "DOWN")
        self.assertEqual(parse_input_token("s"), "SELECT")
        self.assertEqual(parse_input_token("b"), "BACK")


class FakeButton:
    def __init__(self, pin: int, pull_up: bool, bounce_time: float):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed = None
        self.closed = False

    def press(self):
        if callable(self.when_pressed):
            self.when_pressed()

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
                "PIPOD_GPIO_DEBOUNCE_MS": "",
                "PIPOD_GPIO_PULL_UP": "",
            },
        ):
            config = GpioFiveWayConfig.from_env()
        self.assertEqual(config.up_pin, 6)
        self.assertEqual(config.down_pin, 19)
        self.assertEqual(config.left_pin, 5)
        self.assertEqual(config.right_pin, 26)
        self.assertEqual(config.select_pin, 13)
        self.assertEqual(config.debounce_ms, 70)
        self.assertTrue(config.pull_up)

    def test_gpio_input_enqueues_events_and_closes_buttons(self):
        created: list[FakeButton] = []

        def fake_factory(pin: int, pull_up: bool, bounce_time: float):
            button = FakeButton(pin=pin, pull_up=pull_up, bounce_time=bounce_time)
            created.append(button)
            return button

        config = GpioFiveWayConfig(
            enabled=True,
            up_pin=6,
            down_pin=19,
            left_pin=5,
            right_pin=26,
            select_pin=13,
            debounce_ms=70,
            pull_up=True,
        )
        gpio_input = GpioFiveWayInput(config=config, button_factory=fake_factory)

        for button in created:
            self.assertAlmostEqual(button.bounce_time, 0.07, places=6)
            self.assertTrue(button.pull_up)

        for button in created:
            button.press()
        self.assertEqual(
            [gpio_input.poll_nonblocking() for _ in range(5)],
            ["UP", "DOWN", "LEFT", "RIGHT", "SELECT"],
        )
        self.assertIsNone(gpio_input.poll_nonblocking())

        gpio_input.close()
        self.assertTrue(all(button.closed for button in created))

    def test_combined_event_provider_prioritizes_gpio_queue(self):
        created: list[FakeButton] = []

        def fake_factory(pin: int, pull_up: bool, bounce_time: float):
            button = FakeButton(pin=pin, pull_up=pull_up, bounce_time=bounce_time)
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
    def _run_scripted(self, events: list[str]) -> tuple[dict, MockPlayer]:
        library = FixtureLibrary(FIXTURE_PATH, seed=1337)
        player = MockPlayer(seed=1337)
        player.set_track_durations(library.duration_map())
        epd = FakeEPD(write_frames=False)
        dependencies = RuntimeDependencies(
            display=epd,
            library=library,
            player=player,
            event_provider=ScriptedEventProvider(events),
            fonts=load_fonts(),
            status_plumbing=StatusPlumbing(),
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
            player.shutdown()
            epd.sleep()

        return stats, player

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
            "/sim/Daft Punk/Discovery/02 Aerodynamic.mp3",
        )
        self.assertTrue(player.next_track())
        self.assertEqual(
            str(player.current_track_path()),
            "/sim/Fleetwood Mac/Rumours/01 Dreams.mp3",
        )

    def test_playlists_shuffle_all_stays_in_music_view(self):
        stats, player = self._run_scripted(
            [
                "SELECT",  # Music root
                "SELECT",  # Playlists
                "DOWN",  # Shuffle All
                "SELECT",
                "QUIT",
            ]
        )
        self.assertEqual(stats["final_view"], "music_list")
        self.assertIsNotNone(player.current_track_path())
        self.assertTrue(player.state().is_shuffle)

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


if __name__ == "__main__":
    unittest.main()
