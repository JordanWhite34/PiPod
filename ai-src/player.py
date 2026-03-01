#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import random

try:
    import pygame
except Exception:  # pragma: no cover - runtime dependency check
    pygame = None

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover - runtime dependency check
    MutagenFile = None


def _clamp_volume_level(value: int) -> int:
    return max(0, min(10, int(value)))


@dataclass(frozen=True)
class PlayerState:
    available: bool
    backend: str
    error: str | None
    queue_length: int
    current_index: int
    is_paused: bool
    is_shuffle: bool
    is_loop: bool
    current_track: Path | None


class MusicPlayer:
    """Simple queue-based music player backed by pygame mixer."""

    def __init__(self):
        self._queue: list[Path] = []
        self._index = -1
        self._is_paused = False
        self._has_started = False
        self._volume_level = 5
        self._is_muted = False
        self._available = False
        self._backend = "pygame"
        self._error: str | None = None
        self._shuffle_enabled = False
        self._loop_enabled = True
        self._track_duration_cache_s: dict[Path, int] = {}
        self._last_known_position_s = 0.0

        if pygame is None:
            self._backend = "unavailable"
            self._error = "pygame import failed"
            return

        try:
            pygame.mixer.init()
            self._available = True
            self.set_volume_level(self._volume_level)
        except Exception as exc:
            self._backend = "unavailable"
            self._error = f"pygame mixer init failed: {exc}"
            logging.warning("Audio output unavailable: %s", exc)

    def is_available(self) -> bool:
        return self._available

    def is_playing(self) -> bool:
        if not self._available or self._is_paused or not self._has_started:
            return False
        try:
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False

    def state(self) -> PlayerState:
        return PlayerState(
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
        normalized = [Path(path) for path in paths if Path(path).exists()]
        self._shuffle_enabled = bool(shuffle)
        if shuffle:
            random.shuffle(normalized)

        self._queue = normalized
        self._index = 0 if self._queue else -1
        self._is_paused = False
        self._has_started = False
        self._last_known_position_s = 0.0

        if autoplay and self._queue:
            return self._play_from_index(self._index, step=1)
        return bool(self._queue)

    def play(self) -> bool:
        if not self._available or not self._queue:
            return False
        if self._is_paused:
            pygame.mixer.music.unpause()
            self._is_paused = False
            return True
        if self._index < 0:
            self._index = 0
        return self._play_from_index(self._index, step=1)

    def toggle_pause(self) -> bool:
        if not self._available:
            return False
        if self._is_paused:
            pygame.mixer.music.unpause()
            self._is_paused = False
            return True

        # If not currently busy, toggle should behave like play.
        if not pygame.mixer.music.get_busy():
            return self.play()

        self._last_known_position_s = self.current_position_s()
        pygame.mixer.music.pause()
        self._is_paused = True
        return True

    def next_track(self) -> bool:
        if not self._available or not self._queue:
            return False
        if not self._loop_enabled and self._index >= len(self._queue) - 1:
            return False
        start_index = 0 if self._index < 0 else (self._index + 1) % len(self._queue)
        self._is_paused = False
        return self._play_from_index(start_index, step=1)

    def previous_track(self) -> bool:
        if not self._available or not self._queue:
            return False
        start_index = 0 if self._index < 0 else (self._index - 1) % len(self._queue)
        self._is_paused = False
        return self._play_from_index(start_index, step=-1)

    def stop(self):
        if not self._available:
            return
        pygame.mixer.music.stop()
        self._is_paused = False
        self._has_started = False
        self._last_known_position_s = 0.0

    def poll(self) -> bool:
        """Advance queue when a track finishes; return True when state changed."""
        if not self._available or not self._has_started or self._is_paused:
            return False
        if pygame.mixer.music.get_busy():
            return False
        if not self._loop_enabled and self._index >= len(self._queue) - 1:
            self.stop()
            return True
        return self.next_track()

    def toggle_shuffle(self) -> bool:
        self._shuffle_enabled = not self._shuffle_enabled
        if self._queue and self._shuffle_enabled:
            current = self.current_track_path()
            if current is not None:
                rest = [path for idx, path in enumerate(self._queue) if idx != self._index]
                random.shuffle(rest)
                self._queue = [current, *rest]
                self._index = 0
            else:
                random.shuffle(self._queue)
        return True

    def toggle_loop(self) -> bool:
        self._loop_enabled = not self._loop_enabled
        return True

    def set_volume_level(self, level: int):
        self._volume_level = _clamp_volume_level(level)
        if self._available and not self._is_muted:
            pygame.mixer.music.set_volume(self._volume_level / 10.0)

    def set_muted(self, muted: bool):
        self._is_muted = bool(muted)
        if not self._available:
            return
        if self._is_muted:
            pygame.mixer.music.set_volume(0.0)
        else:
            pygame.mixer.music.set_volume(self._volume_level / 10.0)

    def current_track_path(self) -> Path | None:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    def current_position_s(self) -> float:
        if not self._available or not self._has_started:
            return 0.0
        if self._is_paused:
            return max(0.0, self._last_known_position_s)

        try:
            pos_ms = int(pygame.mixer.music.get_pos())
        except Exception:
            return max(0.0, self._last_known_position_s)

        if pos_ms >= 0:
            self._last_known_position_s = pos_ms / 1000.0
        return max(0.0, self._last_known_position_s)

    def playback_progress(self) -> tuple[float, int]:
        current = self.current_track_path()
        if current is None:
            return 0.0, 0
        duration_s = self._duration_for_track(current)
        position_s = self.current_position_s()
        if duration_s > 0:
            position_s = min(position_s, float(duration_s))
        return position_s, duration_s

    def is_shuffle_enabled(self) -> bool:
        return self._shuffle_enabled

    def is_loop_enabled(self) -> bool:
        return self._loop_enabled

    def now_playing_label(self) -> str:
        if not self._available:
            return f"Audio unavailable: {self._error or 'unknown'}"
        current = self.current_track_path()
        if current is None:
            return "Now Playing: (nothing queued)"
        if self._is_paused:
            return f"Paused: {current.stem}"
        if self._has_started:
            return f"Playing: {current.stem}"
        return f"Queued: {current.stem}"

    def shutdown(self):
        if not self._available:
            return
        self.stop()
        try:
            pygame.mixer.quit()
        except Exception:
            pass

    def _load_and_play_current(self) -> bool:
        current = self.current_track_path()
        if current is None:
            return False
        try:
            pygame.mixer.music.load(str(current))
            pygame.mixer.music.play()
            self._is_paused = False
            self._has_started = True
            self._last_known_position_s = 0.0
            return True
        except Exception as exc:
            self._error = f"failed to play {current.name}: {exc}"
            logging.warning("Playback failed for %s: %s", current, exc)
            return False

    def _duration_for_track(self, path: Path) -> int:
        cached = self._track_duration_cache_s.get(path)
        if cached is not None:
            return cached

        duration_s = 0
        if MutagenFile is not None:
            try:
                audio = MutagenFile(path)
                length = getattr(audio.info, "length", 0) if getattr(audio, "info", None) else 0
                duration_s = max(0, int(round(length or 0)))
            except Exception:
                duration_s = 0

        self._track_duration_cache_s[path] = duration_s
        return duration_s

    def _play_from_index(self, start_index: int, step: int) -> bool:
        """Try each queued track until one plays; returns False if none are playable."""
        if not self._queue:
            return False
        queue_len = len(self._queue)
        index = start_index % queue_len

        for _ in range(queue_len):
            self._index = index
            if self._load_and_play_current():
                return True
            index = (index + step) % queue_len

        self._has_started = False
        self._is_paused = False
        return False
