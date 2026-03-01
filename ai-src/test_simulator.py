#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest

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
    StatusPlumbing,
    VOLUME_SLIDER_KNOB_CENTER_Y_OFFSET,
    _volume_slider_knob_x,
    load_fonts,
    render_now_playing,
)
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


if __name__ == "__main__":
    unittest.main()
