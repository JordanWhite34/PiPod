#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random
from typing import Any, Callable

from PIL import Image

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


@dataclass(frozen=True)
class SimTrack:
    path: Path
    title: str
    artist: str
    album: str
    track_no: int
    duration_s: int


@dataclass(frozen=True)
class SimScanReport:
    total_files: int
    added: int
    updated: int
    removed: int
    skipped: int


@dataclass(frozen=True)
class MockPlayerState:
    available: bool
    backend: str
    error: str | None
    queue_length: int
    current_index: int
    is_paused: bool
    is_shuffle: bool
    is_loop: bool
    current_track: Path | None


class FakeEPD:
    """Hardware-free EPD adapter that captures frames and optionally writes PNGs."""

    def __init__(
        self,
        width: int = 122,
        height: int = 250,
        output_dir: Path | None = None,
        write_frames: bool = True,
        frame_prefix: str = "run",
        frame_consumer: Callable[[Image.Image], None] | None = None,
    ):
        self.width = int(width)
        self.height = int(height)
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.write_frames = bool(write_frames)
        self.frame_prefix = str(frame_prefix)
        self.frame_consumer = frame_consumer

        self.frames: list[Image.Image] = []
        self.base_frame_count = 0
        self.partial_frame_count = 0
        self._frame_index = 0
        self._initialized = False
        self._sleeping = False

        if self.output_dir is not None:
            self.frames_dir = self.output_dir / "frames"
            self.frames_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.frames_dir = None

    def init(self):
        self._initialized = True
        self._sleeping = False
        return 0

    def Clear(self, color: int = 0xFF):
        _ = color
        return 0

    def getbuffer(self, image):
        if isinstance(image, Image.Image):
            return image.convert("1").copy()
        return image

    def displayPartBaseImage(self, image):
        self.base_frame_count += 1
        self._capture_frame(image, frame_type="base")

    def displayPartial(self, image):
        self.partial_frame_count += 1
        self._capture_frame(image, frame_type="partial")

    def sleep(self):
        self._sleeping = True
        return 0

    @property
    def frame_count(self) -> int:
        return self.base_frame_count + self.partial_frame_count

    def _capture_frame(self, buffer: Any, frame_type: str):
        image = self._buffer_to_image(buffer)
        self.frames.append(image)
        if self.frame_consumer is not None:
            try:
                self.frame_consumer(image)
            except Exception:
                # Disable consumer after first failure so simulation keeps running.
                self.frame_consumer = None
        if self.write_frames and self.frames_dir is not None:
            filename = f"{self.frame_prefix}_{self._frame_index:04d}_{frame_type}.png"
            image.save(self.frames_dir / filename)
        self._frame_index += 1

    def _buffer_to_image(self, buffer: Any) -> Image.Image:
        if isinstance(buffer, Image.Image):
            return buffer.convert("1").copy()

        if isinstance(buffer, (bytes, bytearray, list, tuple)):
            try:
                raw = bytes(buffer)
                return Image.frombytes("1", (self.width, self.height), raw).copy()
            except Exception:
                pass

        return Image.new("1", (self.width, self.height), 255)


class FixtureLibrary:
    """Deterministic in-memory track catalog loaded from JSON fixture data."""

    def __init__(self, fixture_path: Path, seed: int = 1337):
        self.fixture_path = Path(fixture_path)
        self._seed = int(seed)
        self._rng = random.Random(self._seed)
        self._tracks = self._load_fixture_tracks(self.fixture_path)
        self._track_map = {track.path: track for track in self._tracks}

    def close(self):
        return None

    def scan(self) -> SimScanReport:
        total = len(self._tracks)
        return SimScanReport(total_files=total, added=0, updated=0, removed=0, skipped=total)

    def library_counts(self) -> tuple[int, int, int]:
        artists = {track.artist for track in self._tracks}
        albums = {(track.artist, track.album) for track in self._tracks}
        return len(artists), len(self._tracks), len(albums)

    def all_tracks(self) -> list[SimTrack]:
        return list(self._tracks)

    def random_tracks(self) -> list[SimTrack]:
        tracks = list(self._tracks)
        self._rng.shuffle(tracks)
        return tracks

    def track_by_path(self, path: Path) -> SimTrack | None:
        return self._track_map.get(Path(path))

    def duration_map(self) -> dict[Path, int]:
        return {track.path: int(track.duration_s) for track in self._tracks}

    @staticmethod
    def _load_fixture_tracks(path: Path) -> list[SimTrack]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("tracks", [])
        tracks: list[SimTrack] = []
        for index, entry in enumerate(entries, start=1):
            title = str(entry.get("title") or f"Track {index}").strip()
            artist = str(entry.get("artist") or UNKNOWN_ARTIST).strip() or UNKNOWN_ARTIST
            album = str(entry.get("album") or UNKNOWN_ALBUM).strip() or UNKNOWN_ALBUM
            duration_s = max(0, int(entry.get("duration_s", 0) or 0))
            track_no = max(0, int(entry.get("track_no", index) or 0))
            path_str = str(entry.get("path") or f"/sim/{artist}/{album}/{title}.mp3")
            tracks.append(
                SimTrack(
                    path=Path(path_str),
                    title=title,
                    artist=artist,
                    album=album,
                    track_no=track_no,
                    duration_s=duration_s,
                )
            )
        return tracks


class MockPlayer:
    """Deterministic queue player used by simulation and scenario tests."""

    def __init__(self, seed: int = 1337):
        self._rng = random.Random(int(seed))
        self._queue: list[Path] = []
        self._index = -1
        self._is_paused = False
        self._has_started = False
        self._volume_level = 5
        self._is_muted = False
        self._available = True
        self._backend = "mock"
        self._error: str | None = None
        self._shuffle_enabled = False
        self._loop_enabled = True
        self._position_s = 0.0
        self._duration_map: dict[Path, int] = {}

    def set_track_durations(self, durations: dict[Path, int]):
        self._duration_map = {Path(path): max(0, int(value)) for path, value in durations.items()}

    def advance_time(self, delta_s: float):
        if delta_s <= 0:
            return
        if self._has_started and not self._is_paused and self.current_track_path() is not None:
            self._position_s += float(delta_s)

    def is_available(self) -> bool:
        return self._available

    def is_playing(self) -> bool:
        return self._available and self._has_started and not self._is_paused and bool(self._queue)

    def state(self) -> MockPlayerState:
        return MockPlayerState(
            available=self._available,
            backend=self._backend,
            error=self._error,
            queue_length=len(self._queue),
            current_index=self._index,
            is_paused=self._is_paused,
            is_shuffle=self._shuffle_enabled,
            is_loop=self._loop_enabled,
            current_track=self.current_track_path(),
        )

    def set_queue(self, paths: list[Path], shuffle: bool = False, autoplay: bool = False) -> bool:
        normalized = [Path(path) for path in paths]
        self._shuffle_enabled = bool(shuffle)
        if shuffle:
            self._rng.shuffle(normalized)

        self._queue = normalized
        self._index = 0 if self._queue else -1
        self._is_paused = False
        self._has_started = False
        self._position_s = 0.0

        if autoplay and self._queue:
            self._has_started = True
            return True
        return bool(self._queue)

    def play(self) -> bool:
        if not self._available or not self._queue:
            return False
        if self._is_paused:
            self._is_paused = False
            return True
        if self._index < 0:
            self._index = 0
        self._has_started = True
        if self._position_s < 0:
            self._position_s = 0.0
        return True

    def toggle_pause(self) -> bool:
        if not self._available:
            return False
        if self._is_paused:
            self._is_paused = False
            return True
        if not self._has_started:
            return self.play()
        self._is_paused = True
        return True

    def next_track(self) -> bool:
        if not self._available or not self._queue:
            return False
        if not self._loop_enabled and self._index >= len(self._queue) - 1:
            return False
        start_index = 0 if self._index < 0 else (self._index + 1) % len(self._queue)
        self._index = start_index
        self._has_started = True
        self._is_paused = False
        self._position_s = 0.0
        return True

    def previous_track(self) -> bool:
        if not self._available or not self._queue:
            return False
        start_index = 0 if self._index < 0 else (self._index - 1) % len(self._queue)
        self._index = start_index
        self._has_started = True
        self._is_paused = False
        self._position_s = 0.0
        return True

    def stop(self):
        self._is_paused = False
        self._has_started = False
        self._position_s = 0.0

    def poll(self) -> bool:
        if not self._available or not self._has_started or self._is_paused:
            return False
        duration = self._duration_for_current_track()
        if duration <= 0:
            return False
        if self._position_s >= float(duration):
            if not self._loop_enabled and self._index >= len(self._queue) - 1:
                self.stop()
                return True
            return self.next_track()
        return False

    def toggle_shuffle(self) -> bool:
        self._shuffle_enabled = not self._shuffle_enabled
        if self._queue and self._shuffle_enabled:
            current = self.current_track_path()
            if current is not None:
                rest = [path for idx, path in enumerate(self._queue) if idx != self._index]
                self._rng.shuffle(rest)
                self._queue = [current, *rest]
                self._index = 0
            else:
                self._rng.shuffle(self._queue)
        return True

    def toggle_loop(self) -> bool:
        self._loop_enabled = not self._loop_enabled
        return True

    def set_volume_level(self, level: int):
        self._volume_level = max(0, min(10, int(level)))

    def set_muted(self, muted: bool):
        self._is_muted = bool(muted)

    def current_track_path(self) -> Path | None:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    def current_position_s(self) -> float:
        return max(0.0, float(self._position_s))

    def playback_progress(self) -> tuple[float, int]:
        current = self.current_track_path()
        if current is None:
            return 0.0, 0
        duration = self._duration_for_current_track()
        position = self.current_position_s()
        if duration > 0:
            position = min(position, float(duration))
        return position, duration

    def is_shuffle_enabled(self) -> bool:
        return self._shuffle_enabled

    def is_loop_enabled(self) -> bool:
        return self._loop_enabled

    def now_playing_label(self) -> str:
        current = self.current_track_path()
        if current is None:
            return "Now Playing: (nothing queued)"
        if self._is_paused:
            return f"Paused: {current.stem}"
        if self._has_started:
            return f"Playing: {current.stem}"
        return f"Queued: {current.stem}"

    def shutdown(self):
        self.stop()

    def _duration_for_current_track(self) -> int:
        current = self.current_track_path()
        if current is None:
            return 0
        return max(0, int(self._duration_map.get(current, 0)))
