#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import os
import json
from pathlib import Path
import sys
from types import SimpleNamespace
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
    build_music_index,
    load_playlists_manifest,
    load_fonts,
    parse_input_token,
    render_settings_browser,
    render_now_playing,
    run_pipod_loop,
)
from settings_actions import SettingsActionResult, SettingsActions
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


class SettingsRenderTests(unittest.TestCase):
    def test_settings_selected_item_scroll_delay_is_one_second(self):
        self.assertEqual(SETTINGS_ITEM_SCROLL_DELAY_S, 1.0)

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
        self.assertEqual(playlist_labels[:3], ["Road Trip", "All Songs", "Shuffle All"])
        self.assertEqual(playlists_root.child_items[0].track_paths, custom_playlist_paths)

    def _run_scripted(self, events: list[str]) -> tuple[dict, MockPlayer]:
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

    def test_settings_music_sync_triggers_library_rescan(self):
        stats, _, state = self._run_scripted(
            [
                "DOWN",
                "DOWN",
                "DOWN",  # Settings
                "SELECT",
                "DOWN",  # Music Sync
                "SELECT",
                "SELECT",  # Sync From Import Folder
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
