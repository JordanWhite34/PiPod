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


if __name__ == "__main__":
    unittest.main()
