#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
from pathlib import Path
from typing import Iterable
import unicodedata

from mutagen import File as MutagenFile

SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"


@dataclass(frozen=True)
class Track:
    path: Path
    title: str
    artist: str
    album: str
    track_no: int
    duration_s: int


@dataclass(frozen=True)
class ScanReport:
    total_files: int
    added: int
    updated: int
    removed: int
    skipped: int


class MusicLibrary:
    """Maintain a local SQLite index of music files under one root."""

    def __init__(self, music_root: Path, db_path: Path):
        self.music_root = music_root.expanduser().resolve()
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        self._conn.close()

    def _init_schema(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                mtime_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT NOT NULL,
                track_no INTEGER NOT NULL DEFAULT 0,
                duration_s INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_artist_album ON tracks(artist, album)"
        )
        self._conn.commit()

    def scan(self) -> ScanReport:
        seen_paths = set()
        added = 0
        updated = 0
        skipped = 0
        total_files = 0

        with self._conn:
            self._repair_stored_paths()
            existing = {
                row["path"]: (row["mtime_ns"], row["size_bytes"])
                for row in self._conn.execute(
                    "SELECT path, mtime_ns, size_bytes FROM tracks"
                ).fetchall()
            }

            for path in self._iter_audio_files():
                total_files += 1
                path_str = str(path)
                seen_paths.add(path_str)

                stat = path.stat()
                mtime_ns = int(stat.st_mtime_ns)
                size_bytes = int(stat.st_size)
                cached = existing.get(path_str)

                if cached == (mtime_ns, size_bytes):
                    skipped += 1
                    continue

                title, artist, album, track_no, duration_s = self._extract_track_metadata(path)
                self._conn.execute(
                    """
                    INSERT INTO tracks(path, mtime_ns, size_bytes, title, artist, album, track_no, duration_s)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime_ns=excluded.mtime_ns,
                        size_bytes=excluded.size_bytes,
                        title=excluded.title,
                        artist=excluded.artist,
                        album=excluded.album,
                        track_no=excluded.track_no,
                        duration_s=excluded.duration_s
                    """,
                    (
                        path_str,
                        mtime_ns,
                        size_bytes,
                        title,
                        artist,
                        album,
                        track_no,
                        duration_s,
                    ),
                )
                if cached is None:
                    added += 1
                else:
                    updated += 1

            missing_paths = set(existing).difference(seen_paths)
            removed = len(missing_paths)
            if missing_paths:
                self._conn.executemany(
                    "DELETE FROM tracks WHERE path = ?",
                    [(path_str,) for path_str in missing_paths],
                )

        return ScanReport(
            total_files=total_files,
            added=added,
            updated=updated,
            removed=removed,
            skipped=skipped,
        )

    def _repair_stored_paths(self):
        """Repair stale index rows where path text encoding drifted from filesystem bytes."""
        rows = self._conn.execute("SELECT path FROM tracks").fetchall()
        for row in rows:
            original_path = str(row["path"])
            candidate = _find_existing_repair_candidate(original_path)
            if candidate is None:
                continue
            try:
                self._conn.execute(
                    "UPDATE tracks SET path = ? WHERE path = ?",
                    (candidate, original_path),
                )
                logging.info("Repaired indexed path: %s -> %s", original_path, candidate)
            except sqlite3.IntegrityError:
                # A canonical row already exists; drop the stale duplicate.
                self._conn.execute("DELETE FROM tracks WHERE path = ?", (original_path,))
                logging.info("Removed stale duplicate indexed path: %s", original_path)

    def track_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()
        return int(row["count"]) if row else 0

    def library_counts(self) -> tuple[int, int, int]:
        """
        Return (artists, songs, albums) from the indexed library.
        """
        row = self._conn.execute(
            """
            SELECT
                COUNT(DISTINCT artist) AS artists,
                COUNT(*) AS songs,
                COUNT(DISTINCT album) AS albums
            FROM tracks
            """
        ).fetchone()
        if row is None:
            return 0, 0, 0
        return int(row["artists"]), int(row["songs"]), int(row["albums"])

    def all_tracks(self) -> list[Track]:
        rows = self._conn.execute(
            """
            SELECT path, title, artist, album, track_no, duration_s
            FROM tracks
            ORDER BY
                lower(artist),
                lower(album),
                CASE WHEN track_no <= 0 THEN 9999 ELSE track_no END,
                lower(title),
                lower(path)
            """
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def random_tracks(self) -> list[Track]:
        rows = self._conn.execute(
            """
            SELECT path, title, artist, album, track_no, duration_s
            FROM tracks
            ORDER BY RANDOM()
            """
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def track_by_path(self, path: Path) -> Track | None:
        row = self._conn.execute(
            """
            SELECT path, title, artist, album, track_no, duration_s
            FROM tracks
            WHERE path = ?
            """,
            (str(path),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_track(row)

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> Track:
        return Track(
            path=Path(row["path"]),
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            track_no=int(row["track_no"]),
            duration_s=int(row["duration_s"]),
        )

    def _iter_audio_files(self) -> Iterable[Path]:
        if not self.music_root.exists():
            logging.warning("Music folder does not exist: %s", self.music_root)
            return []

        audio_files = []
        for path in self.music_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                continue
            audio_files.append(path.resolve())
        audio_files.sort()
        return audio_files

    @staticmethod
    def _extract_track_metadata(path: Path) -> tuple[str, str, str, int, int]:
        title = path.stem
        artist = UNKNOWN_ARTIST
        album = UNKNOWN_ALBUM
        track_no = 0
        duration_s = 0

        try:
            audio = MutagenFile(path, easy=True)
            if audio is None:
                return title, artist, album, track_no, duration_s

            title = _clean_text(_first_tag_value(audio, "title"), fallback=title)
            artist = _clean_text(_first_tag_value(audio, "artist"), fallback=artist)
            album = _clean_text(_first_tag_value(audio, "album"), fallback=album)
            track_no = _parse_track_number(_first_tag_value(audio, "tracknumber"))

            length = getattr(audio.info, "length", 0) if getattr(audio, "info", None) else 0
            duration_s = max(0, int(round(length or 0)))
        except Exception as exc:
            logging.warning("Metadata read failed for %s: %s", path, exc)

        return title, artist, album, track_no, duration_s


def _first_tag_value(audio, key: str) -> str | None:
    value = audio.get(key)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    return str(value)


def _clean_text(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = value.strip()
    return cleaned if cleaned else fallback


def _parse_track_number(value: str | None) -> int:
    if not value:
        return 0
    number = value.split("/", 1)[0].strip()
    digits = "".join(ch for ch in number if ch.isdigit())
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _find_existing_repair_candidate(path_str: str) -> str | None:
    path = Path(path_str)
    if path.exists():
        return None

    for candidate in _repair_candidates(path_str):
        candidate_path = Path(candidate)
        if candidate_path.exists():
            return str(candidate_path.resolve())
    return None


def _repair_candidates(path_str: str) -> list[str]:
    candidates: list[str] = []

    def add_candidate(value: str):
        if not value or value == path_str or value in candidates:
            return
        candidates.append(value)

    add_candidate(unicodedata.normalize("NFC", path_str))
    add_candidate(unicodedata.normalize("NFD", path_str))

    for source_codec in ("latin-1", "cp1252"):
        try:
            decoded = path_str.encode(source_codec).decode("utf-8")
        except UnicodeError:
            continue
        add_candidate(decoded)
        add_candidate(unicodedata.normalize("NFC", decoded))
        add_candidate(unicodedata.normalize("NFD", decoded))

    return candidates
