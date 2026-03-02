#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import logging
import os
import select
import sys
import time
from pathlib import Path
from typing import Callable, Protocol, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
from settings_actions import BluetoothDevice, SettingsActionResult, SettingsActions
from settings_store import PersistedSettings, SettingsStore

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover - runtime dependency check
    MutagenFile = None

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
PIC_DIR = ROOT_DIR / "pic"
FONT_PATH = PIC_DIR / "Font.ttc"
ICONS_DIR = APP_DIR / "assets" / "icons"
NOW_PLAYING_ASSETS_DIR = APP_DIR / "assets" / "now_playing"
SHUFFLE_ICON_PATH = ICONS_DIR / "shuffle_thick.png"
LOOP_ICON_PATH = ICONS_DIR / "loop.png"
NOW_PLAYING_IDLE_ART_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MUSIC_DIR_ENV_VAR = "PIPOD_MUSIC_DIR"
DEFAULT_MUSIC_DIR = ROOT_DIR / "music"
PLAYLISTS_MANIFEST_NAME = "playlists.json"
MUSIC_DIR = Path(
    os.getenv(MUSIC_DIR_ENV_VAR, str(DEFAULT_MUSIC_DIR))
).expanduser().resolve()
DATA_DIR = ROOT_DIR / "data"
LIBRARY_DB_PATH = DATA_DIR / "library.db"
NOW_PLAYING_IDLE_ART_PERSIST_PATH = DATA_DIR / "persisted_idle_cover.png"

logging.basicConfig(level=logging.INFO)

MENU_ITEMS = [
    "Music",
    "Now Playing",
    "Shuffle All",
    "Settings",
]

DEFAULT_FOOTER_TEXT = "u/d/s q p n r"
NOW_PLAYING_FOOTER_TEXT = "b back  t art  q quit"
FOOTER_SCROLL_INTERVAL_S = 0.16
FOOTER_SCROLL_STEP_PX = 1
FOOTER_SCROLL_GAP_PX = 24
NOW_PLAYING_TITLE_SCROLL_DELAY_S = 2.2
NOW_PLAYING_TITLE_SCROLL_INTERVAL_S = 0.12
NOW_PLAYING_TITLE_SCROLL_STEP_PX = 1
NOW_PLAYING_TITLE_SCROLL_GAP_PX = 24
SETTINGS_ITEM_SCROLL_DELAY_S = 1.0
SETTINGS_ITEM_SCROLL_INTERVAL_S = 0.12
SETTINGS_ITEM_SCROLL_STEP_PX = 1
SETTINGS_ITEM_SCROLL_GAP_PX = 24
MUSIC_ITEM_SCROLL_DELAY_S = 1.0
MUSIC_ITEM_SCROLL_INTERVAL_S = 0.12
MUSIC_ITEM_SCROLL_STEP_PX = 1
MUSIC_ITEM_SCROLL_GAP_PX = 24
NOW_PLAYING_ART_SIZE = 96
NOW_PLAYING_LEFT_MARGIN = 8
NOW_PLAYING_CONTEXT_TOP = 4
NOW_PLAYING_CONTEXT_FONT_SIZE = 10
NOW_PLAYING_ART_TOP = 20
NOW_PLAYING_PROGRESS_TOP_GAP = 8
NOW_PLAYING_TIME_TOP_GAP = 11
NOW_PLAYING_TITLE_TOP_GAP = 13
NOW_PLAYING_VOLUME_TOP_GAP = 20
NOW_PLAYING_CONTROLS_TOP_GAP = 24
VOLUME_SLIDER_KNOB_CENTER_Y_OFFSET = 6
FOLDER_ART_NAMES = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "front.jpg",
    "front.jpeg",
    "front.png",
)
ALBUM_ART_AUTOCONTRAST_CUTOFF = 2
ALBUM_ART_UNSHARP_RADIUS = 1.0
ALBUM_ART_UNSHARP_PERCENT = 180
ALBUM_ART_UNSHARP_THRESHOLD = 2
ALBUM_ART_GAMMA = 0.92
ALBUM_ART_MODE_ENHANCED = "enhanced"
ALBUM_ART_MODE_ENHANCED_PLUS = "enhanced_plus"
ALBUM_ART_MODE_CLASSIC = "classic"
DEFAULT_ALBUM_ART_MODE = ALBUM_ART_MODE_ENHANCED
ALBUM_ART_MODE_CYCLE = (
    ALBUM_ART_MODE_ENHANCED,
    ALBUM_ART_MODE_ENHANCED_PLUS,
    ALBUM_ART_MODE_CLASSIC,
)
_MODE_ICON_MASK_CACHE: dict[tuple[str, int, int], Image.Image | None] = {}
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
MUSIC_ICON_PLAYLIST = "playlist"
MUSIC_ICON_ARTIST = "artist"
MUSIC_ICON_ALBUM = "album"
MUSIC_ICON_SONG = "song"
MUSIC_ICON_CATEGORY = "category"


class DisplayLike(Protocol):
    width: int
    height: int

    def init(self): ...

    def Clear(self, color: int = 0xFF): ...

    def getbuffer(self, image): ...

    def displayPartBaseImage(self, image): ...

    def displayPartial(self, image): ...

    def sleep(self): ...


class LibraryLike(Protocol):
    def scan(self): ...

    def random_tracks(self): ...

    def all_tracks(self): ...

    def track_by_path(self, path: Path): ...

    def library_counts(self) -> tuple[int, int, int]: ...

    def close(self): ...


class PlayerLike(Protocol):
    def set_queue(self, paths: list[Path], shuffle: bool = False, autoplay: bool = False) -> bool: ...

    def toggle_pause(self) -> bool: ...

    def next_track(self) -> bool: ...

    def previous_track(self) -> bool: ...

    def toggle_shuffle(self) -> bool: ...

    def toggle_loop(self) -> bool: ...

    def poll(self) -> bool: ...

    def playback_progress(self) -> tuple[float, int]: ...

    def state(self): ...

    def is_playing(self) -> bool: ...

    def set_volume_level(self, level: int): ...

    def set_muted(self, muted: bool): ...

    def current_track_path(self) -> Path | None: ...

    def shutdown(self): ...


class SettingsStoreLike(Protocol):
    def load(self) -> PersistedSettings: ...

    def save(self, settings: PersistedSettings) -> None: ...


class SettingsActionsLike(Protocol):
    def bluetooth_adapter_status(self) -> SettingsActionResult: ...

    def bluetooth_scan(self, duration_s: int | None = None) -> SettingsActionResult: ...

    def bluetooth_paired_devices(self) -> SettingsActionResult: ...

    def bluetooth_pair_connect(self, address: str) -> SettingsActionResult: ...

    def bluetooth_connect(self, address: str) -> SettingsActionResult: ...

    def bluetooth_disconnect(self, address: str) -> SettingsActionResult: ...

    def bluetooth_forget(self, address: str) -> SettingsActionResult: ...

    def sync_music_from_import(self, import_dir: Path) -> SettingsActionResult: ...

    def system_info(self, player, library, settings: PersistedSettings) -> SettingsActionResult: ...


EventProvider = Callable[[float], str | None]

NOW_PLAYING_FOCUSABLE = (
    "PREV",
    "PLAY_PAUSE",
    "NEXT",
    "SHUFFLE",
    "LOOP",
)

INPUT_TOKEN_MAPPING = {
    "u": "UP",
    "up": "UP",
    "d": "DOWN",
    "down": "DOWN",
    "s": "SELECT",
    "select": "SELECT",
    "left": "LEFT",
    "right": "RIGHT",
    "b": "BACK",
    "q": "QUIT",
    "quit": "QUIT",
    "v+": "VOL_UP",
    "+": "VOL_UP",
    "v-": "VOL_DOWN",
    "-": "VOL_DOWN",
    "b+": "BAT_UP",
    "b-": "BAT_DOWN",
    "m": "TOGGLE_MUTE",
    "c": "TOGGLE_CHARGE",
    "p": "PLAY_PAUSE",
    "n": "NEXT_TRACK",
    "k": "PREV_TRACK",
    "r": "RESCAN_LIBRARY",
    "t": "TOGGLE_ART_MODE",
}


@dataclass
class RunConfig:
    timeout_s: float = 0.1
    max_steps: int | None = None
    interactive: bool = True
    show_controls_log: bool = True
    initialize_display: bool = True
    clear_display_on_start: bool = True
    loop_step_s: float | None = None
    raise_exceptions: bool = True


@dataclass
class RuntimeDependencies:
    display: DisplayLike
    library: LibraryLike
    player: PlayerLike
    event_provider: EventProvider | None = None
    fonts: tuple | None = None
    status_plumbing: "StatusPlumbing" | None = None
    settings_store: SettingsStoreLike | None = None
    settings_actions: SettingsActionsLike | None = None


@dataclass
class RunStats:
    status: str = "ok"
    error: str | None = None
    events_processed: int = 0
    loop_steps: int = 0
    frames_base: int = 0
    frames_partial: int = 0
    final_view: str = "menu"
    selected_index: int = 0
    selected_menu_item: str = MENU_ITEMS[0]
    now_playing_label: str = ""
    now_playing_context_label: str = ""
    library_totals_label: str = ""

    @property
    def frames_total(self) -> int:
        return self.frames_base + self.frames_partial

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "error": self.error,
            "events_processed": self.events_processed,
            "loop_steps": self.loop_steps,
            "frames_base": self.frames_base,
            "frames_partial": self.frames_partial,
            "frames_total": self.frames_total,
            "final_view": self.final_view,
            "selected_index": self.selected_index,
            "selected_menu_item": self.selected_menu_item,
            "now_playing_label": self.now_playing_label,
            "now_playing_context_label": self.now_playing_context_label,
            "library_totals_label": self.library_totals_label,
        }


@dataclass(frozen=True)
class MusicTrackEntry:
    path: Path
    title: str
    artist: str
    album: str
    track_no: int


@dataclass(frozen=True)
class MusicItem:
    id: str
    label: str
    icon: str
    kind: str
    track_paths: tuple[Path, ...] = ()
    child_items: tuple["MusicItem", ...] = ()


@dataclass
class MusicViewState:
    title: str
    items: tuple[MusicItem, ...]
    selected_idx: int = 0


@dataclass(frozen=True)
class SettingsItem:
    id: str
    label: str
    kind: str = "action"
    help_text: str = ""
    action: str | None = None
    address: str | None = None
    connected: bool = False
    value: str = ""


@dataclass
class SettingsViewState:
    view_id: str
    title: str
    items: tuple[SettingsItem, ...]
    selected_idx: int = 0
    context: str | None = None


def measure_text_width(text, font):
    """Measure rendered text width for overflow/scroll decisions."""
    probe = Image.new("1", (1, 1), 255)
    probe_draw = ImageDraw.Draw(probe)
    return int(probe_draw.textlength(text, font=font) + 0.999)


def ellipsize_text(text, font, width):
    """Clip text with an ellipsis so it fits into a single line."""
    text = str(text or "").strip()
    if not text:
        return ""
    if width <= 0:
        return ""
    if measure_text_width(text, font) <= width:
        return text

    ellipsis = "..."
    if measure_text_width(ellipsis, font) > width:
        return ""

    low = 0
    high = len(text)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if measure_text_width(candidate, font) <= width:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + ellipsis


def _normalize_metadata_text(value, fallback):
    text = str(value or "").strip()
    return text if text else fallback


def _safe_track_no(value):
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _track_sort_key(entry: MusicTrackEntry):
    return (
        entry.artist.casefold(),
        entry.album.casefold(),
        9999 if entry.track_no <= 0 else entry.track_no,
        entry.title.casefold(),
        str(entry.path).casefold(),
    )


def _coerce_music_entries(tracks: Sequence) -> list[MusicTrackEntry]:
    entries: list[MusicTrackEntry] = []
    for track in tracks:
        raw_path = getattr(track, "path", None)
        if raw_path is None:
            continue
        path = Path(raw_path)
        title = _normalize_metadata_text(getattr(track, "title", ""), fallback=path.stem or "Unknown Track")
        artist = _normalize_metadata_text(getattr(track, "artist", ""), fallback=UNKNOWN_ARTIST)
        album = _normalize_metadata_text(getattr(track, "album", ""), fallback=UNKNOWN_ALBUM)
        track_no = _safe_track_no(getattr(track, "track_no", 0))
        entries.append(
            MusicTrackEntry(
                path=path,
                title=title,
                artist=artist,
                album=album,
                track_no=track_no,
            )
        )
    entries.sort(key=_track_sort_key)
    return entries


def _song_item(entry: MusicTrackEntry, *, item_id: str, label: str) -> MusicItem:
    return MusicItem(
        id=item_id,
        label=label,
        icon=MUSIC_ICON_SONG,
        kind="song",
        track_paths=(entry.path,),
        child_items=(),
    )


def _playlist_item_id(label: str, seen: dict[str, int]) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(label))
    slug = slug.strip("_") or "playlist"
    count = seen.get(slug, 0) + 1
    seen[slug] = count
    if count > 1:
        slug = f"{slug}_{count}"
    return f"playlist:user:{slug}"


def _normalize_playlist_track_paths(paths: Sequence) -> tuple[Path, ...]:
    normalized: list[Path] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return tuple(normalized)


def _playlist_song_item(
    *,
    playlist_item_id: str,
    index: int,
    path: Path,
    entry_by_path: dict[Path, MusicTrackEntry],
) -> MusicItem:
    entry = entry_by_path.get(path)
    if entry is None:
        label = path.stem
    else:
        label = f"{entry.title} - {entry.artist}"
    return MusicItem(
        id=f"{playlist_item_id}:song:{index}",
        label=label,
        icon=MUSIC_ICON_SONG,
        kind="song",
        track_paths=(path,),
        child_items=(),
    )


def _playlist_browser_item(
    *,
    item_id: str,
    label: str,
    track_paths: Sequence[Path],
    entry_by_path: dict[Path, MusicTrackEntry],
) -> MusicItem:
    normalized_paths = _normalize_playlist_track_paths(track_paths)
    play_all_item = MusicItem(
        id=f"{item_id}:play_all",
        label="Play All",
        icon=MUSIC_ICON_PLAYLIST,
        kind="playlist",
        track_paths=normalized_paths,
        child_items=(),
    )
    song_items = tuple(
        _playlist_song_item(
            playlist_item_id=item_id,
            index=index,
            path=path,
            entry_by_path=entry_by_path,
        )
        for index, path in enumerate(normalized_paths, start=1)
    )
    return MusicItem(
        id=item_id,
        label=label,
        icon=MUSIC_ICON_PLAYLIST,
        kind="playlist_group",
        track_paths=normalized_paths,
        child_items=(play_all_item, *song_items),
    )


def _resolve_playlist_entry_path(
    raw_entry,
    *,
    music_root: Path,
    abs_lookup: dict[str, Path],
    abs_lookup_folded: dict[str, Path],
    rel_lookup: dict[str, Path],
    rel_lookup_folded: dict[str, Path],
) -> Path | None:
    if not isinstance(raw_entry, str):
        return None
    raw_value = raw_entry.strip()
    if not raw_value:
        return None

    path_value = Path(raw_value).expanduser()
    if path_value.is_absolute():
        absolute_key = str(path_value.resolve())
        return abs_lookup.get(absolute_key) or abs_lookup_folded.get(absolute_key.casefold())

    rel_key = raw_value.replace("\\", "/")
    while rel_key.startswith("./"):
        rel_key = rel_key[2:]
    rel_key = rel_key.lstrip("/")
    if not rel_key:
        return None

    direct = rel_lookup.get(rel_key)
    if direct is not None:
        return direct
    folded = rel_lookup_folded.get(rel_key.casefold())
    if folded is not None:
        return folded

    absolute_key = str((music_root / rel_key).resolve())
    return abs_lookup.get(absolute_key) or abs_lookup_folded.get(absolute_key.casefold())


def load_playlists_manifest(music_root: Path, tracks: Sequence) -> tuple[tuple[str, tuple[Path, ...]], ...]:
    manifest_path = Path(music_root).expanduser().resolve() / PLAYLISTS_MANIFEST_NAME
    if not manifest_path.exists():
        return ()

    try:
        raw_text = manifest_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logging.warning("Unable to read playlists manifest %s: %s", manifest_path, exc)
        return ()

    if not raw_text:
        return ()

    try:
        raw_manifest = json.loads(raw_text)
    except Exception as exc:
        logging.warning("Invalid JSON in playlists manifest %s: %s", manifest_path, exc)
        return ()

    if not isinstance(raw_manifest, dict):
        logging.warning("Ignoring playlists manifest %s: top-level object must be a map", manifest_path)
        return ()

    raw_playlists = raw_manifest.get("playlists", raw_manifest)
    if not isinstance(raw_playlists, dict):
        logging.warning("Ignoring playlists manifest %s: 'playlists' must be a map", manifest_path)
        return ()

    manifest_root = manifest_path.parent
    abs_lookup: dict[str, Path] = {}
    abs_lookup_folded: dict[str, Path] = {}
    rel_lookup: dict[str, Path] = {}
    rel_lookup_folded: dict[str, Path] = {}
    for track in tracks:
        raw_path = getattr(track, "path", None)
        if raw_path is None:
            continue
        path = Path(raw_path)
        absolute_key = str(path.resolve())
        abs_lookup[absolute_key] = path
        abs_lookup_folded[absolute_key.casefold()] = path
        try:
            rel_key = path.resolve().relative_to(manifest_root).as_posix()
        except Exception:
            continue
        rel_lookup[rel_key] = path
        rel_lookup_folded[rel_key.casefold()] = path

    playlists: list[tuple[str, tuple[Path, ...]]] = []
    for raw_name, raw_entries in raw_playlists.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        if not isinstance(raw_entries, list):
            logging.warning("Ignoring playlist '%s': expected a list of track paths", name)
            continue

        resolved_paths: list[Path] = []
        seen_paths: set[Path] = set()
        for raw_entry in raw_entries:
            resolved = _resolve_playlist_entry_path(
                raw_entry,
                music_root=manifest_root,
                abs_lookup=abs_lookup,
                abs_lookup_folded=abs_lookup_folded,
                rel_lookup=rel_lookup,
                rel_lookup_folded=rel_lookup_folded,
            )
            if resolved is None or resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            resolved_paths.append(resolved)
        playlists.append((name, tuple(resolved_paths)))

    return tuple(playlists)


def build_music_index(
    tracks: Sequence,
    playlists: Sequence[tuple[str, Sequence[Path]]] | None = None,
) -> tuple[MusicItem, ...]:
    entries = _coerce_music_entries(tracks)
    all_paths = tuple(entry.path for entry in entries)
    entry_by_path = {entry.path: entry for entry in entries}

    playlist_items: list[MusicItem] = []
    playlist_slug_counts: dict[str, int] = {}
    for label, track_paths in playlists or ():
        item_label = str(label).strip() or "Playlist"
        item_id = _playlist_item_id(item_label, playlist_slug_counts)
        playlist_items.append(
            _playlist_browser_item(
                item_id=item_id,
                label=item_label,
                track_paths=track_paths,
                entry_by_path=entry_by_path,
            )
        )

    all_songs_playlist = _playlist_browser_item(
        item_id="playlist:all_songs",
        label="All Songs",
        track_paths=all_paths,
        entry_by_path=entry_by_path,
    )
    shuffle_all_playlist = MusicItem(
        id="playlist:shuffle_all",
        label="Shuffle All",
        icon=MUSIC_ICON_PLAYLIST,
        kind="playlist_shuffle",
        track_paths=(),
        child_items=(),
    )
    playlists_items = tuple(
        sorted(
            [*playlist_items, all_songs_playlist, shuffle_all_playlist],
            key=lambda item: item.label.casefold(),
        )
    )

    artist_map: dict[str, dict[str, list[MusicTrackEntry]]] = {}
    for entry in entries:
        album_map = artist_map.setdefault(entry.artist, {})
        album_map.setdefault(entry.album, []).append(entry)

    artist_items: list[MusicItem] = []
    for artist in sorted(artist_map.keys(), key=lambda value: value.casefold()):
        album_map = artist_map[artist]
        album_items: list[MusicItem] = []
        for album in sorted(album_map.keys(), key=lambda value: value.casefold()):
            album_entries = sorted(album_map[album], key=_track_sort_key)
            song_items = tuple(
                _song_item(
                    song,
                    item_id=f"song:{song.path}",
                    label=song.title,
                )
                for song in album_entries
            )
            album_items.append(
                MusicItem(
                    id=f"artist_album:{artist}|{album}",
                    label=album,
                    icon=MUSIC_ICON_ALBUM,
                    kind="album",
                    track_paths=tuple(song.path for song in album_entries),
                    child_items=song_items,
                )
            )
        artist_tracks = [song.path for album_entries in album_map.values() for song in album_entries]
        artist_items.append(
            MusicItem(
                id=f"artist:{artist}",
                label=artist,
                icon=MUSIC_ICON_ARTIST,
                kind="artist",
                track_paths=tuple(artist_tracks),
                child_items=tuple(album_items),
            )
        )

    album_groups: dict[tuple[str, str], list[MusicTrackEntry]] = {}
    for entry in entries:
        album_groups.setdefault((entry.album, entry.artist), []).append(entry)
    album_items: list[MusicItem] = []
    for album_key in sorted(album_groups.keys(), key=lambda value: (value[0].casefold(), value[1].casefold())):
        album, artist = album_key
        album_entries = sorted(album_groups[album_key], key=_track_sort_key)
        song_items = tuple(
            _song_item(
                song,
                item_id=f"song:{song.path}",
                label=song.title,
            )
            for song in album_entries
        )
        album_items.append(
            MusicItem(
                id=f"album:{artist}|{album}",
                label=f"{album} - {artist}",
                icon=MUSIC_ICON_ALBUM,
                kind="album",
                track_paths=tuple(song.path for song in album_entries),
                child_items=song_items,
            )
        )

    songs_items = tuple(
        _song_item(
            song,
            item_id=f"song:{song.path}",
            label=f"{song.title} - {song.artist}",
        )
        for song in sorted(
            entries,
            key=lambda entry: (
                entry.title.casefold(),
                entry.artist.casefold(),
                entry.album.casefold(),
                9999 if entry.track_no <= 0 else entry.track_no,
                str(entry.path).casefold(),
            ),
        )
    )

    root_items = (
        MusicItem(
            id="music:playlists",
            label="Playlists",
            icon=MUSIC_ICON_CATEGORY,
            kind="category",
            child_items=playlists_items,
        ),
        MusicItem(
            id="music:artists",
            label="Artists",
            icon=MUSIC_ICON_CATEGORY,
            kind="category",
            child_items=tuple(artist_items),
        ),
        MusicItem(
            id="music:albums",
            label="Albums",
            icon=MUSIC_ICON_CATEGORY,
            kind="category",
            child_items=tuple(album_items),
        ),
        MusicItem(
            id="music:songs",
            label="Songs",
            icon=MUSIC_ICON_CATEGORY,
            kind="category",
            child_items=songs_items,
        ),
    )
    return root_items


def _clamp_index(index: int, size: int) -> int:
    if size <= 0:
        return 0
    return max(0, min(int(index), size - 1))


def _find_item_index(items: tuple[MusicItem, ...], item_id: str | None) -> int | None:
    if not item_id:
        return None
    for idx, item in enumerate(items):
        if item.id == item_id:
            return idx
    return None


def _selected_music_ids(nav_stack: list[MusicViewState]) -> list[str]:
    ids: list[str] = []
    for view in nav_stack:
        if not view.items:
            break
        idx = _clamp_index(view.selected_idx, len(view.items))
        ids.append(view.items[idx].id)
    return ids


def _restore_music_nav_stack(
    root_items: tuple[MusicItem, ...],
    previous_stack: list[MusicViewState] | None = None,
) -> list[MusicViewState]:
    selected_ids = _selected_music_ids(previous_stack or [])
    root_idx = _find_item_index(root_items, selected_ids[0] if selected_ids else None)
    stack = [MusicViewState(title="Music", items=root_items, selected_idx=root_idx or 0)]
    for selected_id in selected_ids[1:]:
        parent_view = stack[-1]
        if not parent_view.items:
            break
        parent_idx = _clamp_index(parent_view.selected_idx, len(parent_view.items))
        parent_item = parent_view.items[parent_idx]
        if not parent_item.child_items:
            break
        child_idx = _find_item_index(parent_item.child_items, selected_id)
        stack.append(
            MusicViewState(
                title=parent_item.label,
                items=parent_item.child_items,
                selected_idx=child_idx or 0,
            )
        )
    return stack


def _current_music_view(nav_stack: list[MusicViewState]) -> MusicViewState:
    if nav_stack:
        return nav_stack[-1]
    return MusicViewState(title="Music", items=(), selected_idx=0)


def _current_music_view_name(nav_stack: list[MusicViewState]) -> str:
    return "music_list" if len(nav_stack) > 1 else "music_root"


def _music_song_queue(items: tuple[MusicItem, ...], selected_item: MusicItem) -> list[Path]:
    song_paths: list[Path] = []
    seen: set[Path] = set()
    for item in items:
        if item.kind != "song" or not item.track_paths:
            continue
        path = Path(item.track_paths[0])
        if path in seen:
            continue
        seen.add(path)
        song_paths.append(path)

    if not selected_item.track_paths:
        return song_paths

    selected_path = Path(selected_item.track_paths[0])
    if selected_path not in song_paths:
        return song_paths
    selected_idx = song_paths.index(selected_path)
    return song_paths[selected_idx:] + song_paths[:selected_idx]


def _current_settings_view(nav_stack: list[SettingsViewState]) -> SettingsViewState:
    if nav_stack:
        return nav_stack[-1]
    return SettingsViewState(view_id="settings_root", title="Settings", items=(), selected_idx=0)


def _current_settings_view_name(nav_stack: list[SettingsViewState]) -> str:
    return "settings_list" if len(nav_stack) > 1 else "settings_root"


def _normalize_bt_devices(raw_devices) -> list[BluetoothDevice]:
    devices: list[BluetoothDevice] = []
    for entry in raw_devices or []:
        if isinstance(entry, BluetoothDevice):
            devices.append(entry)
            continue
        if isinstance(entry, dict):
            address = str(entry.get("address", "")).strip().upper()
            if not address:
                continue
            devices.append(
                BluetoothDevice(
                    address=address,
                    name=str(entry.get("name", address)),
                    paired=bool(entry.get("paired", False)),
                    connected=bool(entry.get("connected", False)),
                    trusted=bool(entry.get("trusted", False)),
                )
            )
    devices.sort(key=lambda device: (device.name.casefold(), device.address))
    return devices


def _settings_root_items(
    settings: PersistedSettings,
    bt_status: SettingsActionResult,
) -> tuple[SettingsItem, ...]:
    bt_suffix = "on" if bool(bt_status.details.get("powered", False)) else "off"
    if not bt_status.ok:
        bt_suffix = "unavailable"
    return (
        SettingsItem(
            id="settings:bluetooth",
            label=f"Bluetooth ({bt_suffix})",
            kind="submenu",
            help_text="Scan and pair headphones",
            action="open_bluetooth",
        ),
        SettingsItem(
            id="settings:audio_output",
            label=f"Audio Output ({settings.audio_output_mode})",
            kind="submenu",
            help_text="Set preferred output mode",
            action="open_audio_output",
        ),
        SettingsItem(
            id="settings:album_art",
            label=f"Album Art ({settings.album_art_mode})",
            kind="submenu",
            help_text="Set album art render style",
            action="open_album_art",
        ),
        SettingsItem(
            id="settings:library",
            label="Library",
            kind="submenu",
            help_text="Rebuild indexed music library",
            action="open_library",
        ),
        SettingsItem(
            id="settings:about",
            label="About",
            kind="submenu",
            help_text="System status and runtime info",
            action="open_about",
        ),
    )


def _settings_bluetooth_items(bt_status: SettingsActionResult) -> tuple[SettingsItem, ...]:
    status_text = bt_status.message if bt_status.message else "Adapter status unavailable"
    return (
        SettingsItem(
            id="settings:bt_scan",
            label="Scan & Pair Headphones",
            kind="action",
            help_text="Find nearby devices and pair",
            action="bt_scan",
        ),
        SettingsItem(
            id="settings:bt_paired",
            label="Paired Devices",
            kind="action",
            help_text="Connect, disconnect, or forget",
            action="bt_paired",
        ),
        SettingsItem(
            id="settings:bt_status",
            label=f"Adapter Status: {status_text}",
            kind="info",
            help_text="Bluetooth adapter details",
        ),
    )


def _settings_bluetooth_scan_items(result: SettingsActionResult) -> tuple[SettingsItem, ...]:
    devices = _normalize_bt_devices(result.details.get("devices", []))
    items: list[SettingsItem] = [
        SettingsItem(
            id="settings:bt_scan_again",
            label="Scan Again",
            kind="action",
            help_text="Rescan nearby Bluetooth devices",
            action="bt_scan",
        )
    ]
    for device in devices:
        state = "connected" if device.connected else "paired" if device.paired else "new"
        items.append(
            SettingsItem(
                id=f"settings:bt_scan:{device.address}",
                label=f"{device.name} ({state})",
                kind="action",
                help_text="Pair and connect this device",
                action="bt_pair_connect",
                address=device.address,
            )
        )
    if len(items) == 1:
        items.append(
            SettingsItem(
                id="settings:bt_scan_none",
                label="No devices found",
                kind="info",
                help_text="Try Scan Again with device in pairing mode",
            )
        )
    return tuple(items)


def _settings_bluetooth_paired_items(result: SettingsActionResult) -> tuple[SettingsItem, ...]:
    devices = _normalize_bt_devices(result.details.get("devices", []))
    items: list[SettingsItem] = []
    for device in devices:
        state = "connected" if device.connected else "disconnected"
        items.append(
            SettingsItem(
                id=f"settings:bt_paired:{device.address}",
                label=f"{device.name} ({state})",
                kind="action",
                help_text="Open device actions",
                action="bt_open_device",
                address=device.address,
                connected=device.connected,
            )
        )
    if not items:
        items.append(
            SettingsItem(
                id="settings:bt_paired_none",
                label="No paired devices",
                kind="info",
                help_text="Pair headphones first from scan list",
            )
        )
    return tuple(items)


def _settings_bluetooth_device_detail_items(device: BluetoothDevice) -> tuple[SettingsItem, ...]:
    primary_action = "bt_disconnect" if device.connected else "bt_connect"
    primary_label = "Disconnect" if device.connected else "Connect"
    return (
        SettingsItem(
            id=f"settings:bt_detail:{device.address}:toggle",
            label=primary_label,
            kind="action",
            help_text="Toggle device connection state",
            action=primary_action,
            address=device.address,
        ),
        SettingsItem(
            id=f"settings:bt_detail:{device.address}:forget",
            label="Forget Device",
            kind="action",
            help_text="Remove pairing from device list",
            action="bt_forget",
            address=device.address,
        ),
        SettingsItem(
            id=f"settings:bt_detail:{device.address}:status",
            label=f"Status: {'connected' if device.connected else 'disconnected'}",
            kind="info",
            help_text=device.address,
        ),
    )


def _settings_audio_output_items(settings: PersistedSettings) -> tuple[SettingsItem, ...]:
    def mode_item(mode: str, label: str) -> SettingsItem:
        marker = "[x]" if settings.audio_output_mode == mode else "[ ]"
        return SettingsItem(
            id=f"settings:audio:{mode}",
            label=f"{marker} {label}",
            kind="action",
            help_text=f"Set output mode to {label.lower()}",
            action="set_audio_mode",
            value=mode,
        )

    return (
        mode_item("auto", "Auto"),
        mode_item("aux", "AUX"),
        mode_item("bluetooth", "Bluetooth"),
    )


def _settings_album_art_items(settings: PersistedSettings) -> tuple[SettingsItem, ...]:
    def mode_item(mode: str, label: str) -> SettingsItem:
        marker = "[x]" if settings.album_art_mode == mode else "[ ]"
        return SettingsItem(
            id=f"settings:album_art:{mode}",
            label=f"{marker} {label}",
            kind="action",
            help_text=f"Set album art mode to {label.lower()}",
            action="set_album_art_mode",
            value=mode,
        )

    selected_idle_art, selected_idle_idx, total_idle_art = _idle_art_selection_details(
        settings.now_playing_idle_art
    )
    if total_idle_art <= 0 or selected_idle_art is None:
        idle_art_item = SettingsItem(
            id="settings:album_art:idle:none",
            label="Idle Cover (0/0)",
            kind="info",
            help_text=f"Add images to {NOW_PLAYING_ASSETS_DIR}",
        )
    else:
        idle_art_item = SettingsItem(
            id=f"settings:album_art:idle:{selected_idle_art.name}",
            label=f"Idle Cover ({selected_idle_idx}/{total_idle_art})",
            kind="action",
            help_text=f"{selected_idle_art.name} (select for next)",
            action="cycle_now_playing_idle_art",
            value=selected_idle_art.name,
        )

    return (
        mode_item(ALBUM_ART_MODE_ENHANCED_PLUS, "Enhanced+"),
        mode_item(ALBUM_ART_MODE_ENHANCED, "Enhanced"),
        mode_item(ALBUM_ART_MODE_CLASSIC, "Classic"),
        idle_art_item,
    )


def _settings_library_items() -> tuple[SettingsItem, ...]:
    return (
        SettingsItem(
            id="settings:library:rebuild",
            label="Rebuild Library Index",
            kind="action",
            help_text="Rescan all music files",
            action="rebuild_library",
        ),
    )


def _settings_about_items(info_result: SettingsActionResult) -> tuple[SettingsItem, ...]:
    rows = info_result.details.get("rows", ())
    items: list[SettingsItem] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        items.append(
            SettingsItem(
                id=f"settings:about:{row[0]}",
                label=f"{row[0]}: {row[1]}",
                kind="info",
                help_text="System/runtime status",
            )
        )
    if not items:
        items.append(
            SettingsItem(
                id="settings:about:none",
                label="No system information available",
                kind="info",
                help_text="Runtime status unavailable",
            )
        )
    return tuple(items)


def _settings_footer_label(nav_stack: list[SettingsViewState], last_result: str | None) -> str:
    view = _current_settings_view(nav_stack)
    if not view.items:
        return str(last_result or "b back")
    selected = view.items[_clamp_index(view.selected_idx, len(view.items))]
    help_text = selected.help_text or "s select  b back"
    if last_result:
        return f"{last_result} | {help_text}"
    return help_text


def _safe_library_tracks(library) -> list:
    all_tracks_fn = getattr(library, "all_tracks", None)
    if callable(all_tracks_fn):
        try:
            return list(all_tracks_fn())
        except Exception as exc:
            logging.warning("Failed to read all tracks for music browser: %s", exc)
    return []


def format_clock(total_seconds):
    total_seconds = max(0, int(total_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes}:{seconds:02}"


def _extract_cover_art_bytes(track_path):
    if MutagenFile is None:
        return None

    try:
        audio = MutagenFile(track_path)
    except Exception:
        return None
    if audio is None:
        return None

    pictures = getattr(audio, "pictures", None)
    if pictures:
        data = getattr(pictures[0], "data", None)
        if data:
            return bytes(data)

    tags = getattr(audio, "tags", None)
    if not tags:
        return None

    if hasattr(tags, "getall"):
        for key in ("APIC", "PIC"):
            frames = tags.getall(key)
            if frames:
                data = getattr(frames[0], "data", None)
                if data:
                    return bytes(data)

    try:
        covr = tags.get("covr")
    except Exception:
        covr = None
    if covr:
        first = covr[0] if isinstance(covr, (list, tuple)) else covr
        try:
            return bytes(first)
        except Exception:
            pass

    return None


def load_album_art(track_path, size, cache, render_mode: str = DEFAULT_ALBUM_ART_MODE):
    mode = _normalize_album_art_mode(render_mode)
    cache_key = (track_path, mode)
    if cache_key in cache:
        return cache[cache_key]

    art_image = None
    art_bytes = _extract_cover_art_bytes(track_path)
    source_path = None
    if not art_bytes:
        source_path = _find_folder_art_path(track_path)

    if art_bytes or source_path is not None:
        try:
            if hasattr(Image, "Resampling"):
                resample = Image.Resampling.LANCZOS
            else:
                resample = Image.LANCZOS

            if art_bytes:
                source = Image.open(io.BytesIO(art_bytes))
            else:
                source = Image.open(source_path)
            with source:
                if mode == ALBUM_ART_MODE_CLASSIC:
                    art_image = _render_album_art_classic_for_epd(source, size, resample)
                elif mode == ALBUM_ART_MODE_ENHANCED_PLUS:
                    art_image = _render_album_art_enhanced_plus_for_epd(source, size, resample)
                else:
                    art_image = _render_album_art_enhanced_for_epd(source, size, resample)
        except Exception:
            art_image = None

    cache[cache_key] = art_image
    return art_image


def _list_now_playing_idle_art_paths() -> tuple[Path, ...]:
    if not NOW_PLAYING_ASSETS_DIR.exists() or not NOW_PLAYING_ASSETS_DIR.is_dir():
        return ()
    entries: list[Path] = []
    for candidate in NOW_PLAYING_ASSETS_DIR.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in NOW_PLAYING_IDLE_ART_EXTENSIONS:
            continue
        entries.append(candidate)
    entries.sort(key=lambda path: path.name.casefold())
    return tuple(entries)


def _resolve_now_playing_idle_art_path(
    selected_name: str | None = None,
    *,
    fallback_to_first: bool = True,
) -> Path | None:
    art_paths = _list_now_playing_idle_art_paths()
    if not art_paths:
        return None

    chosen_name = Path(str(selected_name or "").strip()).name
    if chosen_name:
        for candidate in art_paths:
            if candidate.name == chosen_name:
                return candidate
        chosen_name_folded = chosen_name.casefold()
        for candidate in art_paths:
            if candidate.name.casefold() == chosen_name_folded:
                return candidate

    if fallback_to_first:
        return art_paths[0]
    return None


def _idle_art_selection_details(selected_name: str | None = None) -> tuple[Path | None, int, int]:
    art_paths = _list_now_playing_idle_art_paths()
    total = len(art_paths)
    if total == 0:
        return None, 0, 0

    selected_path = _resolve_now_playing_idle_art_path(selected_name)
    if selected_path is None:
        selected_path = art_paths[0]

    selected_idx = 1
    for idx, candidate in enumerate(art_paths, start=1):
        if candidate == selected_path:
            selected_idx = idx
            break
    return selected_path, selected_idx, total


def _next_now_playing_idle_art_name(selected_name: str | None = None) -> str | None:
    art_paths = _list_now_playing_idle_art_paths()
    total = len(art_paths)
    if total == 0:
        return None

    selected_path = _resolve_now_playing_idle_art_path(selected_name, fallback_to_first=False)
    if selected_path is None:
        return art_paths[0].name

    try:
        current_idx = art_paths.index(selected_path)
    except ValueError:
        return art_paths[0].name
    return art_paths[(current_idx + 1) % total].name


def _persist_now_playing_idle_art_image(selected_name: str | None = None) -> bool:
    source_path = _resolve_now_playing_idle_art_path(selected_name, fallback_to_first=False)
    if source_path is None:
        return False

    try:
        NOW_PLAYING_IDLE_ART_PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = NOW_PLAYING_IDLE_ART_PERSIST_PATH.with_name(
            f"{NOW_PLAYING_IDLE_ART_PERSIST_PATH.name}.tmp"
        )
        with Image.open(source_path) as source:
            source.convert("RGB").save(temp_path, format="PNG")
        temp_path.replace(NOW_PLAYING_IDLE_ART_PERSIST_PATH)
        return True
    except Exception:
        return False


def _resolve_persisted_now_playing_idle_art_path() -> Path | None:
    try:
        if NOW_PLAYING_IDLE_ART_PERSIST_PATH.exists() and NOW_PLAYING_IDLE_ART_PERSIST_PATH.is_file():
            return NOW_PLAYING_IDLE_ART_PERSIST_PATH
    except Exception:
        return None
    return None


def load_now_playing_idle_art(
    size,
    cache,
    render_mode: str = DEFAULT_ALBUM_ART_MODE,
    selected_name: str | None = None,
):
    mode = _normalize_album_art_mode(render_mode)
    source_path = _resolve_now_playing_idle_art_path(selected_name, fallback_to_first=False)
    if source_path is None:
        source_path = _resolve_persisted_now_playing_idle_art_path()
    if source_path is None:
        source_path = _resolve_now_playing_idle_art_path(selected_name, fallback_to_first=True)
    source_key = ""
    if source_path is not None:
        try:
            source_key = f"{source_path.resolve()}:{source_path.stat().st_mtime_ns}"
        except Exception:
            source_key = str(source_path)
    cache_key = (f"idle:{source_key}", mode, int(size))
    if cache_key in cache:
        return cache[cache_key]

    art_image = None
    if source_path is not None:
        try:
            if hasattr(Image, "Resampling"):
                resample = Image.Resampling.LANCZOS
            else:
                resample = Image.LANCZOS
            source = Image.open(source_path)
            with source:
                if mode == ALBUM_ART_MODE_CLASSIC:
                    art_image = _render_album_art_classic_for_epd(source, size, resample)
                elif mode == ALBUM_ART_MODE_ENHANCED_PLUS:
                    art_image = _render_album_art_enhanced_plus_for_epd(source, size, resample)
                else:
                    art_image = _render_album_art_enhanced_for_epd(source, size, resample)
        except Exception:
            art_image = None

    cache[cache_key] = art_image
    return art_image


def _find_folder_art_path(track_path):
    parent = Path(track_path).parent
    for name in FOLDER_ART_NAMES:
        candidate = parent / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _normalize_album_art_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode == ALBUM_ART_MODE_CLASSIC:
        return ALBUM_ART_MODE_CLASSIC
    if mode == ALBUM_ART_MODE_ENHANCED_PLUS:
        return ALBUM_ART_MODE_ENHANCED_PLUS
    return ALBUM_ART_MODE_ENHANCED


def _render_album_art_classic_for_epd(source: Image.Image, size: int, resample) -> Image.Image:
    return ImageOps.fit(source.convert("L"), (size, size), method=resample).convert("1")


def _render_album_art_enhanced_for_epd(source: Image.Image, size: int, resample) -> Image.Image:
    # Improve perceived detail before final 1-bit conversion for e-paper.
    art = ImageOps.fit(source.convert("L"), (size, size), method=resample)
    art = ImageOps.autocontrast(art, cutoff=ALBUM_ART_AUTOCONTRAST_CUTOFF)
    art = art.filter(
        ImageFilter.UnsharpMask(
            radius=ALBUM_ART_UNSHARP_RADIUS,
            percent=ALBUM_ART_UNSHARP_PERCENT,
            threshold=ALBUM_ART_UNSHARP_THRESHOLD,
        )
    )
    art = art.point(lambda value: int(255 * ((value / 255.0) ** ALBUM_ART_GAMMA)))

    if hasattr(Image, "Dither"):
        dither = Image.Dither.FLOYDSTEINBERG
    else:
        dither = Image.FLOYDSTEINBERG
    return art.convert("1", dither=dither)


def _render_album_art_enhanced_plus_for_epd(source: Image.Image, size: int, resample) -> Image.Image:
    art = ImageOps.fit(source.convert("L"), (size, size), method=resample)
    art = _preprocess_album_art_adaptive(art)
    return _adaptive_dither_to_binary(art)


def _preprocess_album_art_adaptive(art: Image.Image) -> Image.Image:
    stat = ImageStat.Stat(art)
    mean = float(stat.mean[0]) if stat.mean else 128.0
    stddev = float(stat.stddev[0]) if stat.stddev else 0.0

    if stddev < 35.0:
        cutoff = 1
        sharp_percent = 220
        sharp_radius = 1.15
        sharp_threshold = 1
    elif stddev < 60.0:
        cutoff = 2
        sharp_percent = 180
        sharp_radius = 1.0
        sharp_threshold = 2
    else:
        cutoff = 3
        sharp_percent = 140
        sharp_radius = 0.9
        sharp_threshold = 2

    if mean > 165.0:
        gamma = 0.86
    elif mean < 90.0:
        gamma = 1.02
    else:
        gamma = 0.93

    art = ImageOps.autocontrast(art, cutoff=cutoff)
    art = art.filter(
        ImageFilter.UnsharpMask(
            radius=sharp_radius,
            percent=sharp_percent,
            threshold=sharp_threshold,
        )
    )
    return art.point(lambda value: int(255 * ((value / 255.0) ** gamma)))


def _adaptive_dither_to_binary(art: Image.Image) -> Image.Image:
    if hasattr(Image, "Dither"):
        fs_dither = Image.Dither.FLOYDSTEINBERG
    else:
        fs_dither = Image.FLOYDSTEINBERG
    fs_image = art.convert("1", dither=fs_dither)
    ordered_image = _ordered_bayer_8x8_dither(art)

    target_black = _target_black_ratio_from_grayscale(art)
    fs_error = abs(_binary_black_ratio(fs_image) - target_black)
    ordered_error = abs(_binary_black_ratio(ordered_image) - target_black)

    if fs_error + 0.01 < ordered_error:
        return fs_image
    if ordered_error + 0.01 < fs_error:
        return ordered_image

    return fs_image if art.entropy() >= 5.2 else ordered_image


def _target_black_ratio_from_grayscale(art: Image.Image) -> float:
    stat = ImageStat.Stat(art)
    mean = float(stat.mean[0]) if stat.mean else 128.0
    return max(0.0, min(1.0, 1.0 - (mean / 255.0)))


def _binary_black_ratio(image: Image.Image) -> float:
    hist = image.histogram()
    if not hist:
        return 0.5
    total = float(sum(hist))
    if total <= 0:
        return 0.5
    black = float(hist[0])
    return black / total


def _ordered_bayer_8x8_dither(art: Image.Image) -> Image.Image:
    bayer = (
        (0, 48, 12, 60, 3, 51, 15, 63),
        (32, 16, 44, 28, 35, 19, 47, 31),
        (8, 56, 4, 52, 11, 59, 7, 55),
        (40, 24, 36, 20, 43, 27, 39, 23),
        (2, 50, 14, 62, 1, 49, 13, 61),
        (34, 18, 46, 30, 33, 17, 45, 29),
        (10, 58, 6, 54, 9, 57, 5, 53),
        (42, 26, 38, 22, 41, 25, 37, 21),
    )

    src = art.convert("L")
    width, height = src.size
    src_px = src.load()
    out = Image.new("1", (width, height), 255)
    out_px = out.load()
    for y in range(height):
        row = bayer[y & 7]
        for x in range(width):
            threshold = int(((row[x & 7] + 0.5) * 255) / 64.0)
            out_px[x, y] = 0 if src_px[x, y] < threshold else 255
    return out


def _next_album_art_mode(current_mode: str) -> str:
    mode = _normalize_album_art_mode(current_mode)
    try:
        idx = ALBUM_ART_MODE_CYCLE.index(mode)
    except ValueError:
        return ALBUM_ART_MODE_CYCLE[0]
    return ALBUM_ART_MODE_CYCLE[(idx + 1) % len(ALBUM_ART_MODE_CYCLE)]


def draw_progress_bar(draw, x, y, width, height, progress_ratio):
    progress_ratio = max(0.0, min(1.0, float(progress_ratio)))
    right = x + width - 1
    bottom = y + height - 1
    draw.rectangle((x, y, right, bottom), outline=0, fill=255)

    inner_width = max(0, width - 2)
    filled_width = int(round(inner_width * progress_ratio))
    if filled_width <= 0:
        return
    draw.rectangle(
        (x + 1, y + 1, x + filled_width, bottom - 1),
        fill=0,
    )


def draw_footer_text(
    image,
    font,
    text,
    x,
    y,
    width,
    height,
    scroll_px=0,
    text_fill=0,
    bg_fill=255,
):
    """Draw footer text and loop-scroll it when it exceeds available width."""
    if width <= 0 or height <= 0:
        return

    footer = Image.new("1", (width, height), bg_fill)
    footer_draw = ImageDraw.Draw(footer)
    text = str(text or "").strip()
    if not text:
        image.paste(footer, (x, y))
        return

    text_width = int(footer_draw.textlength(text, font=font) + 0.999)
    if text_width <= width:
        footer_draw.text((0, 0), text, font=font, fill=text_fill)
    else:
        cycle_width = text_width + FOOTER_SCROLL_GAP_PX
        offset = int(scroll_px) % cycle_width
        footer_draw.text((-offset, 0), text, font=font, fill=text_fill)
        footer_draw.text((cycle_width - offset, 0), text, font=font, fill=text_fill)

    image.paste(footer, (x, y))


def draw_scrolling_text(
    image,
    font,
    text,
    x,
    y,
    width,
    height,
    scroll_px=0,
    gap_px=24,
):
    """Draw one-line text; loop-scroll when it exceeds width."""
    if width <= 0 or height <= 0:
        return

    strip = Image.new("1", (width, height), 255)
    strip_draw = ImageDraw.Draw(strip)
    text = str(text or "").strip()
    if not text:
        image.paste(strip, (x, y))
        return

    text_width = int(strip_draw.textlength(text, font=font) + 0.999)
    if text_width <= width:
        strip_draw.text((0, 0), text, font=font, fill=0)
    else:
        cycle_width = text_width + max(8, int(gap_px))
        offset = int(scroll_px) % cycle_width
        strip_draw.text((-offset, 0), text, font=font, fill=0)
        strip_draw.text((cycle_width - offset, 0), text, font=font, fill=0)

    image.paste(strip, (x, y))


def draw_scrolling_text_line(
    image,
    font,
    text,
    x,
    y,
    width,
    height,
    scroll_px=0,
    gap_px=24,
    text_fill=0,
    bg_fill=255,
):
    """Draw one-line text with optional loop-scroll and configurable colors."""
    if width <= 0 or height <= 0:
        return

    strip = Image.new("1", (width, height), bg_fill)
    strip_draw = ImageDraw.Draw(strip)
    text = str(text or "").strip()
    if not text:
        image.paste(strip, (x, y))
        return

    text_width = int(strip_draw.textlength(text, font=font) + 0.999)
    if text_width <= width:
        strip_draw.text((0, 0), text, font=font, fill=text_fill)
    else:
        cycle_width = text_width + max(8, int(gap_px))
        offset = int(scroll_px) % cycle_width
        strip_draw.text((-offset, 0), text, font=font, fill=text_fill)
        strip_draw.text((cycle_width - offset, 0), text, font=font, fill=text_fill)

    image.paste(strip, (x, y))


def format_library_totals(library):
    artists, songs, albums = library.library_counts()
    artist_label = "artist" if artists == 1 else "artists"
    song_label = "song" if songs == 1 else "songs"
    album_label = "album" if albums == 1 else "albums"
    return f"{artists} {artist_label} | {songs} {song_label} | {albums} {album_label}"


def footer_status_label(library_totals_label, player):
    if player.is_playing():
        current = player.current_track_path()
        if current is not None:
            return f"Now Playing: {current.stem}"
        return "Now Playing"
    return library_totals_label


def clamp_percent(value):
    """Constrain percent-style values to 0..100."""
    return max(0, min(100, int(value)))


def clamp_volume_level(value):
    """Constrain volume level to 0..10."""
    return max(0, min(10, int(value)))


@dataclass
class DeviceStatus:
    """Status values to render in the top-right header block."""

    battery_percent: int = 82
    volume_level: int = 5
    is_charging: bool = False
    is_muted: bool = False


class StatusPlumbing:
    """Placeholder battery/volume plumbing. Replace with real hardware reads later."""

    CHARGE_ANIM_INTERVAL_S = 0.12
    CHARGE_ANIM_FRAMES = 24

    def __init__(self):
        self._status = DeviceStatus()
        self._charge_anim_frame = 0
        self._last_charge_anim_tick = time.monotonic()

    def read(self):
        return self._status

    def charge_anim_frame(self):
        return self._charge_anim_frame

    def tick_animation(self):
        """Advance charging animation while charging and below 100%."""
        if not self._status.is_charging or self._status.battery_percent >= 100:
            if self._charge_anim_frame != 0:
                self._charge_anim_frame = 0
                return True
            return False

        now = time.monotonic()
        if now - self._last_charge_anim_tick >= self.CHARGE_ANIM_INTERVAL_S:
            self._last_charge_anim_tick = now
            self._charge_anim_frame = (self._charge_anim_frame + 1) % self.CHARGE_ANIM_FRAMES
            return True
        return False

    def apply_debug_event(self, event):
        """Allow keyboard-only tuning while real input plumbing is pending."""
        changed = False
        if event == "VOL_UP":
            self._status.volume_level = clamp_volume_level(self._status.volume_level + 1)
            self._status.is_muted = False
            changed = True
        elif event == "VOL_DOWN":
            self._status.volume_level = clamp_volume_level(self._status.volume_level - 1)
            changed = True
        elif event == "BAT_UP":
            self._status.battery_percent = clamp_percent(self._status.battery_percent + 5)
            changed = True
        elif event == "BAT_DOWN":
            self._status.battery_percent = clamp_percent(self._status.battery_percent - 5)
            changed = True
        elif event == "TOGGLE_CHARGE":
            self._status.is_charging = not self._status.is_charging
            self._charge_anim_frame = 0
            self._last_charge_anim_tick = time.monotonic()
            changed = True
        elif event == "TOGGLE_MUTE":
            self._status.is_muted = not self._status.is_muted
            changed = True
        return changed


def load_fonts():
    """Load menu fonts with a safe fallback for quick bring-up."""
    try:
        title = ImageFont.truetype(str(FONT_PATH), 18)
        item = ImageFont.truetype(str(FONT_PATH), 14)
        hint = ImageFont.truetype(str(FONT_PATH), 12)
        return title, item, hint
    except Exception:
        fallback = ImageFont.load_default()
        return fallback, fallback, fallback


def draw_charging_animation(draw, x, y, frame):
    """Draw a fluid, liquid-style charging animation inside the battery body."""
    inner_left = x + 2
    inner_top = y + 2
    body_w = 22
    body_h = 10
    inner_right = x + body_w - 2
    inner_bottom = y + body_h - 2
    width = inner_right - inner_left + 1
    height = inner_bottom - inner_top + 1

    bob = [0, 0, -1, -1, -1, 0, 0, 1, 1, 0]
    base_surface = inner_top + 2 + bob[frame % len(bob)]

    wave_primary = [0, 0, 1, 1, 2, 1, 1, 0, 0, -1, -1, -2, -1, -1]
    wave_secondary = [0, 1, 0, -1, 0, 1, 0, -1]

    surface_y = [inner_top] * width

    for col in range(width):
        px = inner_left + col
        y_surface = (
            base_surface
            + (wave_primary[(col + frame) % len(wave_primary)] // 2)
            + (wave_secondary[(col + frame * 2) % len(wave_secondary)] // 2)
        )
        y_surface = max(inner_top, min(inner_bottom - 1, y_surface))
        surface_y[col] = y_surface

        draw.line((px, y_surface, px, inner_bottom), fill=0)

        for py in range(y_surface + 1, inner_bottom + 1):
            if ((col + py + frame) % 4) == 0:
                draw.point((px, py), fill=255)

    for col in range(width):
        if (col + frame) % 5 == 0:
            draw.point((inner_left + col, surface_y[col]), fill=255)

    bubbles = (
        (0, 2, 0),
        (5, 3, 4),
        (11, 2, 9),
    )
    for start_x, speed, phase in bubbles:
        rise = (frame * speed + phase) % (height + 5)
        by = inner_bottom - rise
        if by < inner_top:
            continue
        bx = inner_left + ((start_x + frame) % width)
        col_idx = bx - inner_left
        if by >= surface_y[col_idx]:
            draw.point((bx, by), fill=255)
            if by + 1 <= inner_bottom and (frame + phase) % 2 == 0:
                draw.point((bx, by + 1), fill=255)


def draw_battery_icon(draw, x, y, percent, is_charging, charge_anim_frame=0):
    """Draw a compact battery icon at (x, y)."""
    percent = clamp_percent(percent)
    body_w = 22
    body_h = 10
    tip_w = 2

    draw.rectangle((x, y, x + body_w, y + body_h), outline=0, fill=255)
    draw.rectangle((x + body_w + 1, y + 3, x + body_w + tip_w, y + body_h - 3), fill=0)

    if is_charging and percent < 100:
        draw_charging_animation(draw, x, y, charge_anim_frame)
        return

    segments = 4
    gap = 1
    inner_left = x + 2
    inner_w = body_w - 1
    seg_w = (inner_w - (segments - 1) * gap) // segments
    filled_segments = (percent + 24) // 25
    if percent == 0:
        filled_segments = 0

    for idx in range(segments):
        sx = inner_left + idx * (seg_w + gap)
        ex = sx + seg_w - 1
        if idx < filled_segments:
            draw.rectangle((sx, y + 2, ex, y + body_h - 2), fill=0)
        else:
            draw.rectangle((sx, y + 2, ex, y + body_h - 2), outline=0, fill=255)


def draw_volume_icon(draw, x, y, volume_level, is_muted):
    """Draw a compact speaker + 10-step wave icon at (x, y)."""
    volume_level = clamp_volume_level(volume_level)
    draw.polygon(
        [
            (x, y + 3),
            (x + 3, y + 3),
            (x + 6, y + 1),
            (x + 6, y + 9),
            (x + 3, y + 6),
            (x, y + 6),
        ],
        outline=0,
        fill=255,
    )

    if is_muted:
        draw.line((x + 8, y + 1, x + 16, y + 8), fill=0)
        draw.line((x + 8, y + 8, x + 16, y + 1), fill=0)
        return

    def draw_dashed_arc(bbox, start, end):
        dash = 8
        gap = 6
        angle = start
        show = True
        while angle < end:
            step = dash if show else gap
            next_angle = min(end, angle + step)
            if show:
                draw.arc(bbox, angle, next_angle, fill=0)
            angle = next_angle
            show = not show

    full_waves = volume_level // 2
    has_half_wave = (volume_level % 2) == 1
    wave_start = -50
    wave_end = 50

    for idx in range(5):
        bbox = (
            x + 7 + (idx * 2),
            y + 2 - idx,
            x + 11 + (idx * 4),
            y + 8 + idx,
        )
        if idx < full_waves:
            draw.arc(bbox, wave_start, wave_end, fill=0)
        elif idx == full_waves and has_half_wave:
            draw_dashed_arc(bbox, wave_start, wave_end)


def _volume_slider_track_bounds(x, y, width):
    left_icon_w = 10
    right_icon_w = 12
    edge_pad = 4
    track_x0 = int(x) + left_icon_w + edge_pad
    track_x1 = int(x) + int(width) - right_icon_w - edge_pad - 1
    if track_x1 <= track_x0:
        track_x1 = track_x0 + 1
    center_y = int(y) + VOLUME_SLIDER_KNOB_CENTER_Y_OFFSET
    return track_x0, track_x1, center_y


def _volume_slider_knob_x(x, y, width, volume_level, is_muted=False):
    level = 0 if is_muted else clamp_volume_level(volume_level)
    track_x0, track_x1, _ = _volume_slider_track_bounds(x, y, width)
    span = max(1, track_x1 - track_x0)
    return track_x0 + int(round((level / 10.0) * span))


def draw_volume_slider(draw, x, y, width, volume_level, is_muted):
    """Draw a compact horizontal volume slider with left/right speaker glyphs."""
    x = int(x)
    y = int(y)
    width = max(32, int(width))

    # Left speaker icon.
    draw.polygon(
        [
            (x, y + 4),
            (x + 2, y + 4),
            (x + 5, y + 2),
            (x + 5, y + 10),
            (x + 2, y + 8),
            (x, y + 8),
        ],
        outline=0,
        fill=255,
    )

    # Right speaker icon + waves.
    right_x = x + width - 10
    draw.polygon(
        [
            (right_x, y + 4),
            (right_x + 2, y + 4),
            (right_x + 5, y + 2),
            (right_x + 5, y + 10),
            (right_x + 2, y + 8),
            (right_x, y + 8),
        ],
        outline=0,
        fill=255,
    )
    draw.arc((right_x + 5, y + 3, right_x + 9, y + 9), -45, 45, fill=0)
    draw.arc((right_x + 6, y + 2, right_x + 12, y + 10), -45, 45, fill=0)

    track_x0, track_x1, center_y = _volume_slider_track_bounds(x, y, width)
    draw.rectangle((track_x0, center_y - 1, track_x1, center_y + 1), outline=0, fill=255)

    knob_x = _volume_slider_knob_x(x, y, width, volume_level, is_muted=is_muted)
    if knob_x > track_x0:
        draw.line((track_x0 + 1, center_y, knob_x, center_y), fill=0, width=1)
    draw.ellipse((knob_x - 4, center_y - 4, knob_x + 4, center_y + 4), outline=0, fill=255)

    if is_muted:
        draw.line((x + 1, y + 1, x + 8, y + 11), fill=0)
        draw.line((x + 1, y + 11, x + 8, y + 1), fill=0)


def draw_rounded_box(draw, x0, y0, x1, y1, fill=255, outline=None, radius=4, width=1):
    kwargs = {}
    if fill is not None:
        kwargs["fill"] = fill
    if outline is not None:
        kwargs["outline"] = outline
        kwargs["width"] = width
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, **kwargs)
    else:
        draw.rectangle((x0, y0, x1, y1), **kwargs)


def draw_icon_prev(draw, x, y, color):
    draw.rectangle((x, y + 1, x + 1, y + 11), fill=color)
    draw.polygon([(x + 3, y + 6), (x + 12, y + 1), (x + 12, y + 11)], fill=color)


def draw_icon_next(draw, x, y, color):
    draw.polygon([(x + 1, y + 1), (x + 1, y + 11), (x + 10, y + 6)], fill=color)
    draw.rectangle((x + 12, y + 1, x + 13, y + 11), fill=color)


def draw_icon_play_pause(draw, x, y, color, is_playing, is_paused):
    if is_playing and not is_paused:
        # Center pause bars inside the 14x14 icon box.
        draw.rectangle((x + 2, y + 1, x + 4, y + 12), fill=color)
        draw.rectangle((x + 9, y + 1, x + 11, y + 12), fill=color)
    else:
        draw.polygon([(x + 2, y + 1), (x + 2, y + 11), (x + 11, y + 6)], fill=color)


def _draw_polyline(draw, points, color, width=2):
    """Draw a thick polyline with rounded joints/end caps."""
    if len(points) < 2:
        return
    for idx in range(len(points) - 1):
        draw.line((points[idx][0], points[idx][1], points[idx + 1][0], points[idx + 1][1]), fill=color, width=width)
    radius = max(1, width // 2)
    for px, py in points:
        draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color)


def _load_mode_icon_mask(path: Path, width: int, height: int) -> Image.Image | None:
    key = (str(path), int(width), int(height))
    cached = _MODE_ICON_MASK_CACHE.get(key, "__missing__")
    if cached != "__missing__":
        return cached

    if not path.exists() or not path.is_file():
        _MODE_ICON_MASK_CACHE[key] = None
        return None

    try:
        with Image.open(path) as source:
            rgba = source.convert("RGBA")
            alpha = rgba.getchannel("A")
            alpha_min, alpha_max = alpha.getextrema()

            if alpha_max <= 0:
                _MODE_ICON_MASK_CACHE[key] = None
                return None

            if alpha_min == 255 and alpha_max == 255:
                # Fully opaque source, infer icon shape from dark pixels.
                gray = rgba.convert("L")
                mask = gray.point(lambda p: 255 if p < 220 else 0, mode="L")
            else:
                # Use transparency as authoritative shape mask.
                mask = alpha.point(lambda p: 255 if p > 8 else 0, mode="L")

            if hasattr(Image, "Resampling"):
                resample = Image.Resampling.LANCZOS
            else:
                resample = Image.LANCZOS
            mask = mask.resize((int(width), int(height)), resample)
            mask = mask.point(lambda p: 255 if p >= 96 else 0, mode="1")

            _MODE_ICON_MASK_CACHE[key] = mask
            return mask
    except Exception:
        _MODE_ICON_MASK_CACHE[key] = None
        return None


def _draw_mode_icon_from_asset(
    draw,
    path: Path,
    x: int,
    y: int,
    color: int,
    width: int,
    height: int,
) -> bool:
    mask = _load_mode_icon_mask(path, width=width, height=height)
    if mask is None:
        return False
    draw.bitmap((x, y), mask, fill=color)
    return True


def _erode_mask(mask: Image.Image, passes: int = 1) -> Image.Image:
    """Binary erosion to keep a clean black outer layer when selected."""
    src = mask.convert("1")
    w, h = src.size
    for _ in range(max(1, int(passes))):
        spix = src.load()
        out = Image.new("1", (w, h), 0)
        opix = out.load()
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                if not spix[x, y]:
                    continue
                keep = True
                for ny in (y - 1, y, y + 1):
                    for nx in (x - 1, x, x + 1):
                        if not spix[nx, ny]:
                            keep = False
                            break
                    if not keep:
                        break
                if keep:
                    opix[x, y] = 255
        src = out
    return src


def _strip_edge_components(mask: Image.Image) -> Image.Image:
    """
    Remove connected components that touch the icon bounds.
    This drops outer frame fragments from full-button assets while keeping
    the inner glyph for selected rendering.
    """
    src = mask.convert("1")
    w, h = src.size
    spix = src.load()
    out = Image.new("1", (w, h), 0)
    opix = out.load()

    visited: set[tuple[int, int]] = set()
    kept_any = False
    largest_component: list[tuple[int, int]] = []
    largest_size = 0

    for y in range(h):
        for x in range(w):
            if not spix[x, y] or (x, y) in visited:
                continue

            stack = [(x, y)]
            visited.add((x, y))
            component: list[tuple[int, int]] = []
            touches_edge = False

            while stack:
                cx, cy = stack.pop()
                component.append((cx, cy))
                if cx == 0 or cy == 0 or cx == (w - 1) or cy == (h - 1):
                    touches_edge = True

                for ny in (cy - 1, cy, cy + 1):
                    for nx in (cx - 1, cx, cx + 1):
                        if nx < 0 or ny < 0 or nx >= w or ny >= h:
                            continue
                        if (nx, ny) in visited or not spix[nx, ny]:
                            continue
                        visited.add((nx, ny))
                        stack.append((nx, ny))

            if len(component) > largest_size:
                largest_size = len(component)
                largest_component = component

            if touches_edge:
                continue

            kept_any = True
            for px, py in component:
                opix[px, py] = 255

    if kept_any:
        return out

    # If everything touches the bounds, fall back to the largest component.
    if largest_component:
        fallback = Image.new("1", (w, h), 0)
        fpix = fallback.load()
        for px, py in largest_component:
            fpix[px, py] = 255
        return fallback

    return src


def draw_mode_asset_button(draw, x0, y0, x1, y1, path: Path, selected=False, hovered=False):
    """
    Draw shuffle/loop using uploaded full-button assets (no synthetic button frame).
    Selected: black fill behind icon + white icon. Unselected: black icon on white.
    """
    width = (x1 - x0 + 1)
    height = (y1 - y0 + 1)
    icon_x = x0
    icon_y = y0

    if selected:
        # Rounded selected background keeps button silhouette while preserving
        # the black outer layer around the white icon glyph.
        draw_rounded_box(draw, x0, y0, x1, y1, fill=0, outline=None, radius=4, width=1)
        color = 255
    else:
        color = 0

    if hovered:
        draw_rounded_box(draw, x0 - 2, y0 - 2, x1 + 2, y1 + 2, fill=None, outline=0, radius=5, width=1)

    if selected:
        mask = _load_mode_icon_mask(path, width=width, height=height)
        if mask is not None:
            core_mask = _strip_edge_components(mask)
            inner_white = _erode_mask(core_mask, passes=1)
            if inner_white.getbbox() is None:
                inner_white = core_mask
            draw.bitmap((icon_x, icon_y), inner_white, fill=255)
            return

    if not _draw_mode_icon_from_asset(draw, path, icon_x, icon_y, color, width=width, height=height):
        # Fallback to in-code glyph if asset is missing.
        if path == SHUFFLE_ICON_PATH:
            draw_icon_shuffle(draw, icon_x, icon_y, color)
        else:
            draw_icon_loop(draw, icon_x, icon_y, color)


def draw_icon_shuffle(draw, x, y, color):
    if _draw_mode_icon_from_asset(draw, SHUFFLE_ICON_PATH, x, y, color, width=17, height=17):
        return

    # Traced from reference: two crossing flowing paths ending in right arrowheads.
    _draw_polyline(
        draw,
        [
            (x + 1, y + 4),
            (x + 4, y + 4),
            (x + 7, y + 7),
            (x + 10, y + 4),
            (x + 12, y + 4),
        ],
        color,
        width=2,
    )
    draw.polygon([(x + 12, y + 1), (x + 15, y + 4), (x + 12, y + 7)], fill=color)

    _draw_polyline(
        draw,
        [
            (x + 1, y + 12),
            (x + 4, y + 12),
            (x + 7, y + 9),
            (x + 10, y + 12),
            (x + 12, y + 12),
        ],
        color,
        width=2,
    )
    draw.polygon([(x + 12, y + 9), (x + 15, y + 12), (x + 12, y + 15)], fill=color)


def draw_icon_loop(draw, x, y, color):
    if _draw_mode_icon_from_asset(draw, LOOP_ICON_PATH, x, y, color, width=17, height=17):
        return

    # Traced from reference: rounded rectangular repeat loop with opposite arrows.
    _draw_polyline(
        draw,
        [
            (x + 3, y + 4),
            (x + 10, y + 4),
            (x + 12, y + 6),
            (x + 12, y + 9),
        ],
        color,
        width=2,
    )
    draw.polygon([(x + 10, y + 2), (x + 14, y + 4), (x + 10, y + 6)], fill=color)

    _draw_polyline(
        draw,
        [
            (x + 12, y + 11),
            (x + 5, y + 11),
            (x + 3, y + 9),
            (x + 3, y + 6),
        ],
        color,
        width=2,
    )
    draw.polygon([(x + 5, y + 9), (x + 1, y + 11), (x + 5, y + 13)], fill=color)


def draw_music_item_icon(draw, x, y, icon_name, color):
    x = int(x)
    y = int(y)
    if icon_name == MUSIC_ICON_PLAYLIST:
        for idx in range(3):
            yy = y + 2 + (idx * 4)
            draw.rectangle((x + 1, yy, x + 2, yy + 1), fill=color)
            draw.rectangle((x + 4, yy, x + 12, yy + 1), fill=color)
        return

    if icon_name == MUSIC_ICON_ARTIST:
        draw.ellipse((x + 4, y + 1, x + 9, y + 6), outline=color, fill=255)
        draw.arc((x + 1, y + 5, x + 12, y + 13), 200, -20, fill=color)
        return

    if icon_name == MUSIC_ICON_ALBUM:
        draw.ellipse((x + 1, y + 1, x + 12, y + 12), outline=color, fill=255)
        draw.ellipse((x + 5, y + 5, x + 8, y + 8), outline=color, fill=255)
        return

    if icon_name == MUSIC_ICON_SONG:
        draw.line((x + 8, y + 1, x + 8, y + 9), fill=color, width=1)
        draw.line((x + 8, y + 1, x + 12, y + 2), fill=color, width=1)
        draw.ellipse((x + 3, y + 7, x + 7, y + 11), outline=color, fill=255)
        return

    # Category icon fallback.
    draw.rectangle((x + 1, y + 4, x + 12, y + 11), outline=color, fill=255)
    draw.rectangle((x + 1, y + 2, x + 6, y + 4), outline=color, fill=255)


def draw_music_chevron(draw, x, y, color):
    draw.line((x, y, x + 3, y + 3), fill=color)
    draw.line((x + 3, y + 3, x, y + 6), fill=color)


def draw_icon_button(
    draw,
    x0,
    y0,
    x1,
    y1,
    icon_drawer,
    icon_color,
    selected=False,
    hovered=False,
    icon_size=14,
):
    bg = 0 if selected else 255
    draw_rounded_box(draw, x0, y0, x1, y1, fill=bg, outline=0, radius=4, width=1)
    if hovered:
        draw_rounded_box(draw, x0 - 2, y0 - 2, x1 + 2, y1 + 2, fill=None, outline=0, radius=5, width=1)
    icon_x = x0 + ((x1 - x0 + 1 - int(icon_size)) // 2)
    icon_y = y0 + ((y1 - y0 + 1 - int(icon_size)) // 2)
    icon_drawer(draw, icon_x, icon_y, icon_color)


def draw_now_playing_icon_controls(
    draw,
    x,
    y,
    width,
    focus_index,
    is_playing,
    is_paused,
    shuffle_on,
    loop_on,
    bottom_edge_y,
):
    """Draw transport and mode controls as icon buttons with hover/focus outlines."""
    base_w = 26
    base_h = 20
    play_w = 34
    play_h = 24

    # Row 1: same-size side buttons, larger play/pause center button.
    row1_total = base_w + play_w + base_w
    row1_remaining = max(0, width - row1_total)
    row1_gap = max(8, row1_remaining // 4)
    row1_used = row1_total + (row1_gap * 2)
    row1_left_pad = max(0, (width - row1_used) // 2)
    row1_x = x + row1_left_pad
    side_y = y + ((play_h - base_h) // 2)

    prev_box = (row1_x, side_y, row1_x + base_w - 1, side_y + base_h - 1)
    play_x = prev_box[2] + 1 + row1_gap
    play_box = (play_x, y, play_x + play_w - 1, y + play_h - 1)
    next_x = play_box[2] + 1 + row1_gap
    next_box = (next_x, side_y, next_x + base_w - 1, side_y + base_h - 1)

    draw_icon_button(
        draw,
        *prev_box,
        icon_drawer=draw_icon_prev,
        icon_color=0,
        selected=False,
        hovered=focus_index == 0,
    )
    draw_icon_button(
        draw,
        *play_box,
        icon_drawer=lambda d, ix, iy, c: draw_icon_play_pause(d, ix, iy, c, is_playing, is_paused),
        icon_color=0,
        selected=False,
        hovered=focus_index == 1,
    )
    draw_icon_button(
        draw,
        *next_box,
        icon_drawer=draw_icon_next,
        icon_color=0,
        selected=False,
        hovered=focus_index == 2,
    )

    # Row 2: two mode buttons centered between transport row and bottom edge.
    row1_bottom = y + play_h
    available_below = max(0, int(bottom_edge_y) - row1_bottom - base_h)
    mode_gap_top = available_below // 2
    if mode_gap_top < 8:
        mode_gap_top = 8
    mode_y = row1_bottom + mode_gap_top
    row2_total = base_w * 2
    row2_remaining = max(0, width - row2_total)
    mode_gap = max(12, row2_remaining // 3)
    row2_used = row2_total + mode_gap
    row2_left_pad = max(0, (width - row2_used) // 2)
    mode_left_x = x + row2_left_pad
    mode_right_x = mode_left_x + base_w + mode_gap
    mode_box_left = (
        mode_left_x,
        mode_y,
        mode_left_x + base_w - 1,
        mode_y + base_h - 1,
    )
    mode_box_right = (
        mode_right_x,
        mode_y,
        mode_right_x + base_w - 1,
        mode_y + base_h - 1,
    )

    draw_mode_asset_button(
        draw,
        *mode_box_left,
        path=SHUFFLE_ICON_PATH,
        selected=shuffle_on,
        hovered=focus_index == 3,
    )
    draw_mode_asset_button(
        draw,
        *mode_box_right,
        path=LOOP_ICON_PATH,
        selected=loop_on,
        hovered=focus_index == 4,
    )


def sync_audio_output(status, player):
    player.set_volume_level(status.volume_level)
    player.set_muted(status.is_muted)


def handle_menu_action(menu_item, library, player):
    """Execute selected menu action. Return True when state changed."""
    if menu_item == "Shuffle All":
        return _start_random_queue(library, player, shuffle=True)

    return False


def _start_random_queue(library, player, shuffle: bool) -> bool:
    tracks = library.random_tracks()
    if not tracks:
        library.scan()
        tracks = library.random_tracks()
    if not tracks:
        return False

    started = player.set_queue(
        [track.path for track in tracks],
        shuffle=shuffle,
        autoplay=True,
    )
    if started:
        return True

    library.scan()
    tracks = library.random_tracks()
    if not tracks:
        return False
    return player.set_queue(
        [track.path for track in tracks],
        shuffle=shuffle,
        autoplay=True,
    )


def render_menu(
    epd,
    fonts,
    selected_idx,
    status,
    charge_anim_frame,
    selected_label=None,
    footer_scroll_px=0,
    footer_selected=False,
):
    """Render a mono frame buffer for the main menu."""
    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    title_font, item_font, hint_font = fonts

    status_x = epd.width - 31
    title_left = 4
    title_width = max(0, status_x - title_left - 4)
    title_text = ellipsize_text("PiPod", title_font, title_width)
    draw.text((title_left, 4), title_text, font=title_font, fill=0)
    draw_battery_icon(
        draw,
        status_x,
        1,
        status.battery_percent,
        status.is_charging,
        charge_anim_frame=charge_anim_frame,
    )
    draw_volume_icon(draw, status_x, 13, status.volume_level, status.is_muted)
    draw.line((0, 26, epd.width - 1, 26), fill=0)

    start_y = 34
    row_h = 24

    for idx, label in enumerate(MENU_ITEMS):
        row_top = start_y + idx * row_h
        if idx == selected_idx:
            draw.rectangle((6, row_top - 2, epd.width - 7, row_top + 16), fill=0)
            draw.text((11, row_top), label, font=item_font, fill=255)
        else:
            draw.text((11, row_top), label, font=item_font, fill=0)

    footer_left = 6
    footer_top = epd.height - 20
    footer_width = epd.width - (footer_left * 2)
    footer_height = epd.height - footer_top
    draw.line((0, footer_top - 4, epd.width - 1, footer_top - 4), fill=0)
    if footer_selected:
        draw.rectangle(
            (footer_left - 1, footer_top - 1, footer_left + footer_width - 1, epd.height - 1),
            fill=0,
        )
    footer_text = selected_label if selected_label else DEFAULT_FOOTER_TEXT
    draw_footer_text(
        image,
        hint_font,
        footer_text,
        footer_left,
        footer_top,
        footer_width,
        footer_height,
        scroll_px=footer_scroll_px,
        text_fill=255 if footer_selected else 0,
        bg_fill=0 if footer_selected else 255,
    )

    return image


def render_music_browser(
    epd,
    fonts,
    view_state: MusicViewState,
    status,
    charge_anim_frame,
    selected_label=None,
    footer_scroll_px=0,
    selected_item_scroll_px=0,
    footer_selected=False,
):
    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    title_font, item_font, hint_font = fonts

    status_x = epd.width - 31
    title_left = 4
    title_width = max(0, status_x - title_left - 4)
    title_text = ellipsize_text(view_state.title, title_font, title_width)
    draw.text((title_left, 4), title_text, font=title_font, fill=0)
    draw_battery_icon(
        draw,
        status_x,
        1,
        status.battery_percent,
        status.is_charging,
        charge_anim_frame=charge_anim_frame,
    )
    draw_volume_icon(draw, status_x, 13, status.volume_level, status.is_muted)
    draw.line((0, 26, epd.width - 1, 26), fill=0)

    start_y = 34
    row_h = 24
    footer_left = 6
    footer_top = epd.height - 20
    footer_width = epd.width - (footer_left * 2)
    footer_height = epd.height - footer_top

    visible_rows = max(1, (footer_top - start_y) // row_h)
    total_items = len(view_state.items)
    selected_idx = _clamp_index(view_state.selected_idx, total_items)
    max_start = max(0, total_items - visible_rows)
    window_start = max(0, min(max_start, selected_idx - (visible_rows // 2)))

    text_left = 29
    for row in range(visible_rows):
        idx = window_start + row
        if idx >= total_items:
            break
        item = view_state.items[idx]
        row_top = start_y + row * row_h
        selected = idx == selected_idx
        fg = 255 if selected else 0
        if selected:
            draw.rectangle((6, row_top - 2, epd.width - 7, row_top + 16), fill=0)

        draw_music_item_icon(draw, 11, row_top + 1, item.icon, fg)
        has_children = bool(item.child_items)
        right_padding = 18 if has_children else 9
        label_width = max(0, epd.width - text_left - right_padding)
        if selected:
            draw_scrolling_text_line(
                image,
                item_font,
                item.label,
                text_left,
                row_top,
                label_width,
                18,
                scroll_px=selected_item_scroll_px,
                gap_px=MUSIC_ITEM_SCROLL_GAP_PX,
                text_fill=255,
                bg_fill=0,
            )
        else:
            label = ellipsize_text(item.label, item_font, label_width)
            draw.text((text_left, row_top), label, font=item_font, fill=fg)
        if has_children:
            draw_music_chevron(draw, epd.width - 14, row_top + 5, fg)

    if total_items == 0:
        draw.text((11, start_y), "No music found", font=item_font, fill=0)

    draw.line((0, footer_top - 4, epd.width - 1, footer_top - 4), fill=0)
    if footer_selected:
        draw.rectangle(
            (footer_left - 1, footer_top - 1, footer_left + footer_width - 1, epd.height - 1),
            fill=0,
        )
    footer_text = selected_label if selected_label else DEFAULT_FOOTER_TEXT
    draw_footer_text(
        image,
        hint_font,
        footer_text,
        footer_left,
        footer_top,
        footer_width,
        footer_height,
        scroll_px=footer_scroll_px,
        text_fill=255 if footer_selected else 0,
        bg_fill=0 if footer_selected else 255,
    )
    return image


def render_settings_browser(
    epd,
    fonts,
    view_state: SettingsViewState,
    status,
    charge_anim_frame,
    selected_label=None,
    footer_scroll_px=0,
    selected_item_scroll_px=0,
    footer_selected=False,
):
    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    title_font, item_font, hint_font = fonts

    status_x = epd.width - 31
    title_left = 4
    title_width = max(0, status_x - title_left - 4)
    title_text = ellipsize_text(view_state.title, title_font, title_width)
    draw.text((title_left, 4), title_text, font=title_font, fill=0)
    draw_battery_icon(
        draw,
        status_x,
        1,
        status.battery_percent,
        status.is_charging,
        charge_anim_frame=charge_anim_frame,
    )
    draw_volume_icon(draw, status_x, 13, status.volume_level, status.is_muted)
    draw.line((0, 26, epd.width - 1, 26), fill=0)

    start_y = 34
    row_h = 24
    footer_left = 6
    footer_top = epd.height - 20
    footer_width = epd.width - (footer_left * 2)
    footer_height = epd.height - footer_top

    visible_rows = max(1, (footer_top - start_y) // row_h)
    total_items = len(view_state.items)
    selected_idx = _clamp_index(view_state.selected_idx, total_items)
    max_start = max(0, total_items - visible_rows)
    window_start = max(0, min(max_start, selected_idx - (visible_rows // 2)))

    for row in range(visible_rows):
        idx = window_start + row
        if idx >= total_items:
            break
        item = view_state.items[idx]
        row_top = start_y + row * row_h
        selected = idx == selected_idx
        fg = 255 if selected else 0
        if selected:
            draw.rectangle((6, row_top - 2, epd.width - 7, row_top + 16), fill=0)

        right_padding = 18 if item.kind == "submenu" else 9
        label_width = max(0, epd.width - 11 - right_padding)
        if selected:
            draw_scrolling_text_line(
                image,
                item_font,
                item.label,
                11,
                row_top,
                label_width,
                18,
                scroll_px=selected_item_scroll_px,
                gap_px=SETTINGS_ITEM_SCROLL_GAP_PX,
                text_fill=255,
                bg_fill=0,
            )
        else:
            label = ellipsize_text(item.label, item_font, label_width)
            draw.text((11, row_top), label, font=item_font, fill=fg)
        if item.kind == "submenu":
            draw_music_chevron(draw, epd.width - 14, row_top + 5, fg)

    if total_items == 0:
        draw.text((11, start_y), "No settings available", font=item_font, fill=0)

    draw.line((0, footer_top - 4, epd.width - 1, footer_top - 4), fill=0)
    if footer_selected:
        draw.rectangle(
            (footer_left - 1, footer_top - 1, footer_left + footer_width - 1, epd.height - 1),
            fill=0,
        )
    footer_text = selected_label if selected_label else "s select  b back"
    draw_footer_text(
        image,
        hint_font,
        footer_text,
        footer_left,
        footer_top,
        footer_width,
        footer_height,
        scroll_px=footer_scroll_px,
        text_fill=255 if footer_selected else 0,
        bg_fill=0 if footer_selected else 255,
    )
    return image


def render_now_playing(
    epd,
    fonts,
    player,
    library,
    status,
    charge_anim_frame,
    album_art_cache,
    album_art_render_mode=DEFAULT_ALBUM_ART_MODE,
    focus_index=1,
    song_scroll_px=0,
    context_label="",
    idle_art_selection=None,
):
    """Render a mono frame buffer for album art + progress playback view."""
    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    _, item_font, hint_font = fonts
    try:
        context_font = ImageFont.truetype(str(FONT_PATH), NOW_PLAYING_CONTEXT_FONT_SIZE)
    except Exception:
        context_font = hint_font

    left = NOW_PLAYING_LEFT_MARGIN
    max_width = epd.width - (left * 2)
    state = player.state()
    context_width = max(0, epd.width - (left * 2))
    context_text = ellipsize_text(str(context_label or "").strip(), context_font, context_width)
    if context_text:
        context_x = max(left, (epd.width - measure_text_width(context_text, context_font)) // 2)
        draw.text((context_x, NOW_PLAYING_CONTEXT_TOP), context_text, font=context_font, fill=0)

    progress_s, progress_duration_s = player.playback_progress()
    art_left = (epd.width - NOW_PLAYING_ART_SIZE) // 2
    art_top = NOW_PLAYING_ART_TOP
    art_right = art_left + NOW_PLAYING_ART_SIZE - 1
    art_bottom = art_top + NOW_PLAYING_ART_SIZE - 1

    song_name = "Nothing queued"
    artist_name = "Unknown Artist"
    progress_bar_ratio = 0.0
    elapsed_label = "0:00"
    duration_label = "0:00"

    if not state.available:
        draw.rectangle((art_left, art_top, art_right, art_bottom), outline=0, fill=255)
        draw.text((art_left + 12, art_top + 40), "NO AUDIO", font=item_font, fill=0)
        song_name = "Audio unavailable"
        artist_name = state.error or "unknown"
    elif state.current_track is None:
        idle_art = load_now_playing_idle_art(
            NOW_PLAYING_ART_SIZE,
            album_art_cache,
            render_mode=album_art_render_mode,
            selected_name=idle_art_selection,
        )
        if idle_art is not None:
            image.paste(idle_art, (art_left, art_top))
        else:
            draw.rectangle((art_left, art_top, art_right, art_bottom), outline=0, fill=255)
            draw.text((art_left + 12, art_top + 40), "NO COVER", font=item_font, fill=0)
        song_name = "Nothing queued"
        artist_name = "Press Play"
    else:
        metadata = library.track_by_path(state.current_track)
        if metadata is not None:
            song_name = metadata.title
            artist_name = metadata.artist
            if progress_duration_s <= 0 and metadata.duration_s > 0:
                progress_duration_s = metadata.duration_s
        else:
            song_name = state.current_track.stem
            if state.current_track.parent and state.current_track.parent.parent:
                artist_name = state.current_track.parent.parent.name

        album_art = load_album_art(
            state.current_track,
            NOW_PLAYING_ART_SIZE,
            album_art_cache,
            render_mode=album_art_render_mode,
        )
        if album_art is not None:
            image.paste(album_art, (art_left, art_top))
        else:
            draw.rectangle((art_left, art_top, art_right, art_bottom), outline=0, fill=255)
            draw.text((art_left + 12, art_top + 40), "NO COVER", font=item_font, fill=0)

        if progress_duration_s > 0:
            progress_bar_ratio = min(1.0, progress_s / progress_duration_s)
            elapsed_label = format_clock(progress_s)
            duration_label = format_clock(progress_duration_s)
        else:
            elapsed_label = format_clock(progress_s)
            duration_label = "?:??"

    draw.rectangle((art_left, art_top, art_right, art_bottom), outline=0)

    progress_x = left
    progress_y = art_bottom + NOW_PLAYING_PROGRESS_TOP_GAP
    progress_w = epd.width - (left * 2)
    draw_progress_bar(draw, progress_x, progress_y, progress_w, 8, progress_bar_ratio)

    time_y = progress_y + NOW_PLAYING_TIME_TOP_GAP
    draw.text((progress_x, time_y), elapsed_label, font=hint_font, fill=0)
    duration_w = measure_text_width(duration_label, hint_font)
    draw.text((progress_x + progress_w - duration_w, time_y), duration_label, font=hint_font, fill=0)

    song_artist = f"{song_name} - {artist_name}"
    song_line_y = time_y + NOW_PLAYING_TITLE_TOP_GAP
    draw_scrolling_text(
        image,
        item_font,
        song_artist,
        left,
        song_line_y,
        max_width,
        18,
        scroll_px=song_scroll_px,
        gap_px=NOW_PLAYING_TITLE_SCROLL_GAP_PX,
    )
    volume_y = song_line_y + NOW_PLAYING_VOLUME_TOP_GAP
    draw_volume_slider(draw, left, volume_y, max_width, status.volume_level, status.is_muted)
    controls_y = volume_y + NOW_PLAYING_CONTROLS_TOP_GAP
    draw_now_playing_icon_controls(
        draw,
        left,
        controls_y,
        max_width,
        focus_index=focus_index,
        is_playing=player.is_playing(),
        is_paused=state.is_paused,
        shuffle_on=state.is_shuffle,
        loop_on=state.is_loop,
        bottom_edge_y=epd.height - 8,
    )

    return image


def read_key_event(timeout_s=0.1):
    """Read one keyboard event with timeout so animations can update."""
    if not sys.stdin.isatty():
        time.sleep(timeout_s)
        return None

    ready, _, _ = select.select([sys.stdin], [], [], timeout_s)
    if not ready:
        return None

    raw = sys.stdin.readline()
    if raw == "":
        return None
    return parse_input_token(raw)


def parse_input_token(raw: str | None) -> str | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    return INPUT_TOKEN_MAPPING.get(text)


def normalize_navigation_event_alias(event: str) -> str:
    if event == "LEFT":
        return "BACK"
    if event == "RIGHT":
        return "SELECT"
    return event


def _advance_player_time(player, delta_s: float):
    if delta_s <= 0:
        return
    advance = getattr(player, "advance_time", None)
    if callable(advance):
        advance(float(delta_s))


def _safe_scan_library(library):
    try:
        return library.scan()
    except Exception as exc:
        logging.warning("Library scan failed: %s", exc)
        return None


def _safe_load_settings(settings_store) -> PersistedSettings:
    try:
        return settings_store.load()
    except Exception as exc:
        logging.warning("Settings load failed: %s", exc)
        return PersistedSettings()


def _safe_save_settings(settings_store, settings: PersistedSettings) -> bool:
    try:
        settings_store.save(settings)
        return True
    except Exception as exc:
        logging.warning("Settings save failed: %s", exc)
        return False


def _safe_settings_action(action_name: str, fn, *args, **kwargs) -> SettingsActionResult:
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        logging.warning("Settings action '%s' failed: %s", action_name, exc)
        return SettingsActionResult(ok=False, message=f"{action_name} failed: {exc}")
    if isinstance(result, SettingsActionResult):
        return result
    return SettingsActionResult(ok=False, message=f"{action_name} returned invalid result")


def _safe_library_totals_label(library):
    try:
        return format_library_totals(library)
    except Exception as exc:
        logging.warning("Failed to read library totals: %s", exc)
        return "0 artists | 0 songs | 0 albums"


def _library_music_root(library) -> Path | None:
    try:
        raw_music_root = getattr(library, "music_root", None)
        if raw_music_root is None:
            return None
        return Path(raw_music_root).expanduser().resolve()
    except Exception:
        return None


def _safe_now_playing_label(player):
    label_fn = getattr(player, "now_playing_label", None)
    if callable(label_fn):
        try:
            return str(label_fn())
        except Exception:
            return ""
    state_fn = getattr(player, "state", None)
    if callable(state_fn):
        try:
            state = state_fn()
            current_track = getattr(state, "current_track", None)
            is_paused = bool(getattr(state, "is_paused", False))
            if current_track is None:
                return "Now Playing: (nothing queued)"
            prefix = "Paused" if is_paused else "Playing"
            return f"{prefix}: {Path(current_track).stem}"
        except Exception:
            return ""
    return ""


def _toggle_player_mode(player, mode_name: str) -> bool:
    toggle_fn = getattr(player, f"toggle_{mode_name}", None)
    if not callable(toggle_fn):
        return False
    try:
        return bool(toggle_fn())
    except Exception:
        return False


def _now_playing_song_artist_text(player, library) -> str:
    state = player.state()
    if not getattr(state, "available", True):
        return f"Audio unavailable - {getattr(state, 'error', 'unknown') or 'unknown'}"
    current = getattr(state, "current_track", None)
    if current is None:
        return "Nothing queued - Press Play"

    metadata = library.track_by_path(current)
    if metadata is not None:
        return f"{metadata.title} - {metadata.artist}"

    current_path = Path(current)
    song_name = current_path.stem
    artist_name = "Unknown Artist"
    if current_path.parent and current_path.parent.parent:
        artist_name = current_path.parent.parent.name
    return f"{song_name} - {artist_name}"


def _infer_now_playing_context_label(player, library) -> str:
    state = player.state()
    current = getattr(state, "current_track", None)
    if current is None:
        return "Nothing queued"

    metadata = library.track_by_path(current)
    if metadata is not None:
        album = str(getattr(metadata, "album", "") or "").strip()
        if album:
            return album

    current_path = Path(current)
    parent = str(getattr(current_path.parent, "name", "") or "").strip()
    if parent:
        return parent
    return "Library"


def _music_playback_context_label(view: MusicViewState, item: MusicItem) -> str | None:
    if item.kind == "playlist_shuffle":
        return "Shuffle All"
    if item.kind in {"playlist", "song"}:
        title = str(view.title or "").strip()
        if title:
            return title
    return None


def _play_pause_or_shuffle_all(player, library) -> bool:
    try:
        current = player.current_track_path()
    except Exception:
        current = None

    if current is None:
        # Start with a random track but keep shuffle mode disabled.
        return _start_random_queue(library, player, shuffle=False)
    try:
        return bool(player.toggle_pause())
    except Exception:
        return False


def _select_music_item(
    view: MusicViewState,
    item: MusicItem,
    player,
    library,
) -> tuple[bool, MusicViewState | None]:
    if item.child_items:
        return True, MusicViewState(title=item.label, items=item.child_items, selected_idx=0)

    if item.kind == "playlist_shuffle":
        return handle_menu_action("Shuffle All", library, player), None

    if item.kind == "playlist":
        if not item.track_paths:
            return False, None
        started = player.set_queue(list(item.track_paths), shuffle=False, autoplay=True)
        return bool(started), None

    if item.kind == "song":
        queue_paths = _music_song_queue(view.items, item)
        if not queue_paths and item.track_paths:
            queue_paths = [Path(item.track_paths[0])]
        if not queue_paths:
            return False, None
        started = player.set_queue(queue_paths, shuffle=False, autoplay=True)
        return bool(started), None

    return False, None


def run_pipod_loop(config: RunConfig, dependencies: RuntimeDependencies) -> dict:
    """Run the shared PiPod application loop and return structured stats."""
    epd = dependencies.display
    library = dependencies.library
    player = dependencies.player
    fonts = dependencies.fonts if dependencies.fonts is not None else load_fonts()
    status_plumbing = (
        dependencies.status_plumbing
        if dependencies.status_plumbing is not None
        else StatusPlumbing()
    )
    event_provider = dependencies.event_provider or read_key_event
    settings_store = dependencies.settings_store if dependencies.settings_store is not None else SettingsStore()
    settings_actions = (
        dependencies.settings_actions
        if dependencies.settings_actions is not None
        else SettingsActions(music_dir=MUSIC_DIR)
    )

    stats = RunStats()

    selected_idx = 0
    current_view = "menu"
    selected_label = None
    library_totals_label = "0 artists | 0 songs | 0 albums"
    music_root_items: tuple[MusicItem, ...] = ()
    music_nav_stack: list[MusicViewState] = []
    settings_nav_stack: list[SettingsViewState] = []
    settings = _safe_load_settings(settings_store)
    settings_last_result: str | None = None
    settings_bt_status = _safe_settings_action(
        "bluetooth_adapter_status",
        settings_actions.bluetooth_adapter_status,
    )
    album_art_cache = {}
    album_art_render_mode = _normalize_album_art_mode(settings.album_art_mode)
    now_playing_last_progress_bucket = -1
    now_playing_focus_index = 1
    now_playing_song_text = ""
    now_playing_context_label = ""
    now_playing_song_scroll_px = 0
    now_playing_song_should_scroll = False
    now_playing_song_last_scroll_tick = 0.0
    now_playing_song_scroll_start_tick = 0.0
    music_item_scroll_key = ""
    music_item_scroll_px = 0
    music_item_should_scroll = False
    music_item_last_scroll_tick = 0.0
    music_item_scroll_start_tick = 0.0
    settings_item_scroll_key = ""
    settings_item_scroll_px = 0
    settings_item_should_scroll = False
    settings_item_last_scroll_tick = 0.0
    settings_item_scroll_start_tick = 0.0
    footer_width = epd.width - 12
    footer_scroll_px = 0
    footer_selected = False

    virtual_clock = 0.0

    def now_clock():
        if config.loop_step_s is None:
            return time.monotonic()
        return virtual_clock

    footer_last_scroll_tick = now_clock()
    now_playing_song_last_scroll_tick = now_clock()
    now_playing_song_scroll_start_tick = now_clock()
    music_item_last_scroll_tick = now_clock()
    music_item_scroll_start_tick = now_clock()
    settings_item_last_scroll_tick = now_clock()
    settings_item_scroll_start_tick = now_clock()
    footer_should_scroll = False

    def set_selected_label(value):
        nonlocal selected_label
        nonlocal footer_scroll_px
        nonlocal footer_last_scroll_tick
        nonlocal footer_should_scroll
        selected_label = value
        footer_scroll_px = 0
        footer_last_scroll_tick = now_clock()
        if not selected_label:
            footer_should_scroll = False
            return
        footer_should_scroll = (
            measure_text_width(str(selected_label).strip(), fonts[2]) > footer_width
        )

    def set_now_playing_song_text(value):
        nonlocal now_playing_song_text
        nonlocal now_playing_song_scroll_px
        nonlocal now_playing_song_should_scroll
        nonlocal now_playing_song_last_scroll_tick
        nonlocal now_playing_song_scroll_start_tick
        now_playing_song_text = str(value or "").strip()
        now_playing_song_scroll_px = 0
        now_playing_song_last_scroll_tick = now_clock()
        now_playing_song_scroll_start_tick = now_clock()
        now_playing_song_should_scroll = (
            bool(now_playing_song_text)
            and measure_text_width(now_playing_song_text, fonts[1]) > (epd.width - 16)
        )

    def set_now_playing_context_label(value: str | None = None):
        nonlocal now_playing_context_label
        text = str(value or "").strip()
        if text:
            now_playing_context_label = text
            return
        now_playing_context_label = _infer_now_playing_context_label(player, library)

    def player_has_current_track() -> bool:
        try:
            return player.current_track_path() is not None
        except Exception:
            return False

    def set_music_item_scroll_key(key_value, label_text, label_width):
        nonlocal music_item_scroll_key
        nonlocal music_item_scroll_px
        nonlocal music_item_should_scroll
        nonlocal music_item_last_scroll_tick
        nonlocal music_item_scroll_start_tick
        key_text = str(key_value or "").strip()
        label_text = str(label_text or "").strip()
        key = f"{key_text}|{int(label_width)}"
        if key == music_item_scroll_key:
            return False
        music_item_scroll_key = key
        music_item_scroll_px = 0
        music_item_last_scroll_tick = now_clock()
        music_item_scroll_start_tick = now_clock()
        music_item_should_scroll = (
            bool(label_text)
            and int(label_width) > 0
            and measure_text_width(label_text, fonts[1]) > int(label_width)
        )
        return True

    def refresh_selected_music_scroll_state():
        if not music_nav_stack:
            return set_music_item_scroll_key("", "", 0)
        view = _current_music_view(music_nav_stack)
        if not view.items:
            return set_music_item_scroll_key("", "", 0)
        selected_item = view.items[_clamp_index(view.selected_idx, len(view.items))]
        right_padding = 18 if bool(selected_item.child_items) else 9
        label_width = max(0, epd.width - 29 - right_padding)
        return set_music_item_scroll_key(selected_item.id, selected_item.label, label_width)

    def set_settings_item_scroll_key(key_value, label_text, label_width):
        nonlocal settings_item_scroll_key
        nonlocal settings_item_scroll_px
        nonlocal settings_item_should_scroll
        nonlocal settings_item_last_scroll_tick
        nonlocal settings_item_scroll_start_tick
        key_text = str(key_value or "").strip()
        label_text = str(label_text or "").strip()
        key = f"{key_text}|{int(label_width)}"
        if key == settings_item_scroll_key:
            return False
        settings_item_scroll_key = key
        settings_item_scroll_px = 0
        settings_item_last_scroll_tick = now_clock()
        settings_item_scroll_start_tick = now_clock()
        settings_item_should_scroll = (
            bool(label_text)
            and int(label_width) > 0
            and measure_text_width(label_text, fonts[1]) > int(label_width)
        )
        return True

    def refresh_selected_settings_scroll_state():
        if not settings_nav_stack:
            return set_settings_item_scroll_key("", "", 0)
        view = _current_settings_view(settings_nav_stack)
        if not view.items:
            return set_settings_item_scroll_key("", "", 0)
        selected_item = view.items[_clamp_index(view.selected_idx, len(view.items))]
        right_padding = 18 if selected_item.kind == "submenu" else 9
        label_width = max(0, epd.width - 11 - right_padding)
        return set_settings_item_scroll_key(selected_item.id, selected_item.label, label_width)

    def run_settings_action(action_name: str, fn, *args, **kwargs) -> SettingsActionResult:
        logging.info("Settings action: %s", action_name)
        result = _safe_settings_action(action_name, fn, *args, **kwargs)
        logging.info("Settings action result: ok=%s msg=%s", result.ok, result.message)
        return result

    def build_settings_view(
        view_id: str,
        title: str,
        items: tuple[SettingsItem, ...],
        selected_hint: int = 0,
        context: str | None = None,
    ) -> SettingsViewState:
        return SettingsViewState(
            view_id=view_id,
            title=title,
            items=items,
            selected_idx=_clamp_index(selected_hint, len(items)),
            context=context,
        )

    def build_settings_root_view(selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_root",
            title="Settings",
            items=_settings_root_items(settings, settings_bt_status),
            selected_hint=selected_hint,
        )

    def build_settings_bluetooth_view(selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_bluetooth",
            title="Bluetooth",
            items=_settings_bluetooth_items(settings_bt_status),
            selected_hint=selected_hint,
        )

    def build_settings_audio_view(selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_audio",
            title="Audio Output",
            items=_settings_audio_output_items(settings),
            selected_hint=selected_hint,
        )

    def build_settings_album_art_view(selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_album_art",
            title="Album Art",
            items=_settings_album_art_items(settings),
            selected_hint=selected_hint,
        )

    def build_settings_library_view(selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_library",
            title="Library",
            items=_settings_library_items(),
            selected_hint=selected_hint,
        )

    def build_settings_about_view(selected_hint: int = 0) -> SettingsViewState:
        info_result = run_settings_action("system_info", settings_actions.system_info, player, library, settings)
        return build_settings_view(
            view_id="settings_about",
            title="About",
            items=_settings_about_items(info_result),
            selected_hint=selected_hint,
        )

    def build_settings_bt_scan_view(result: SettingsActionResult, selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_bt_scan",
            title="Scan Results",
            items=_settings_bluetooth_scan_items(result),
            selected_hint=selected_hint,
        )

    def build_settings_bt_paired_view(result: SettingsActionResult, selected_hint: int = 0) -> SettingsViewState:
        return build_settings_view(
            view_id="settings_bt_paired",
            title="Paired Devices",
            items=_settings_bluetooth_paired_items(result),
            selected_hint=selected_hint,
        )

    def build_settings_bt_device_view(address: str, selected_hint: int = 0) -> SettingsViewState:
        paired_result = run_settings_action("bluetooth_paired_devices", settings_actions.bluetooth_paired_devices)
        devices = _normalize_bt_devices(paired_result.details.get("devices", []))
        device = next((entry for entry in devices if entry.address == address), None)
        if device is None:
            items = (
                SettingsItem(
                    id="settings:bt_device_missing",
                    label="Device not found",
                    kind="info",
                    help_text=address,
                ),
            )
            return build_settings_view(
                view_id="settings_bt_device",
                title="Bluetooth Device",
                items=items,
                selected_hint=0,
                context=address,
            )
        return build_settings_view(
            view_id="settings_bt_device",
            title=device.name,
            items=_settings_bluetooth_device_detail_items(device),
            selected_hint=selected_hint,
            context=device.address,
        )

    def enter_settings_root():
        nonlocal current_view
        settings_nav_stack.clear()
        settings_nav_stack.append(build_settings_root_view())
        current_view = _current_settings_view_name(settings_nav_stack)

    def refresh_settings_root_if_needed():
        if not settings_nav_stack:
            return
        current_root = settings_nav_stack[0]
        settings_nav_stack[0] = build_settings_root_view(selected_hint=current_root.selected_idx)

    def replace_current_settings_view(view: SettingsViewState):
        if settings_nav_stack:
            settings_nav_stack[-1] = view
        else:
            settings_nav_stack.append(view)

    def push_settings_view(view: SettingsViewState):
        settings_nav_stack.append(view)

    def set_settings_and_save(new_settings: PersistedSettings):
        nonlocal settings
        nonlocal album_art_render_mode
        settings = new_settings
        album_art_render_mode = _normalize_album_art_mode(settings.album_art_mode)
        _safe_save_settings(settings_store, settings)
        refresh_settings_root_if_needed()

    def rebuild_music_index_and_nav(*, scan_library: bool = False):
        nonlocal library_totals_label
        nonlocal music_root_items
        nonlocal music_nav_stack
        if scan_library:
            _safe_scan_library(library)
        tracks = _safe_library_tracks(library)
        music_root = _library_music_root(library)
        manifest_playlists = load_playlists_manifest(music_root, tracks) if music_root is not None else ()
        library_totals_label = _safe_library_totals_label(library)
        music_root_items = build_music_index(tracks, playlists=manifest_playlists)
        music_nav_stack = _restore_music_nav_stack(music_root_items, music_nav_stack)

    def enter_now_playing():
        nonlocal current_view
        nonlocal now_playing_focus_index
        nonlocal now_playing_last_progress_bucket
        nonlocal footer_selected
        current_view = "now_playing"
        now_playing_focus_index = 1
        now_playing_last_progress_bucket = -1
        set_now_playing_song_text(_now_playing_song_artist_text(player, library))
        set_now_playing_context_label()
        footer_selected = False

    try:
        status = status_plumbing.read()
        sync_audio_output(status, player)

        rebuild_music_index_and_nav(scan_library=True)

        if config.initialize_display:
            logging.info("Initializing display")
            epd.init()
        if config.clear_display_on_start:
            epd.Clear(0xFF)

        set_selected_label(footer_status_label(library_totals_label, player))

        image = render_menu(
            epd,
            fonts,
            selected_idx,
            status,
            status_plumbing.charge_anim_frame(),
            selected_label,
            footer_scroll_px=footer_scroll_px,
            footer_selected=footer_selected,
        )
        epd.displayPartBaseImage(epd.getbuffer(image))
        stats.frames_base += 1

        if config.show_controls_log and config.interactive:
            logging.info("Music library root: %s", MUSIC_DIR)
            logging.info(
                "Controls: u/d/s + left/right, b (back), q, p/n/k/r/t, v+/v-, b+/b-, m, c then Enter"
            )

        while True:
            if config.max_steps is not None and stats.loop_steps >= config.max_steps:
                break

            timeout_s = max(0.0, float(config.timeout_s))
            event = event_provider(timeout_s)
            stats.loop_steps += 1

            step_s = config.loop_step_s if config.loop_step_s is not None else timeout_s
            if config.loop_step_s is not None:
                virtual_clock += float(step_s)
            _advance_player_time(player, float(step_s))

            should_redraw = False
            if event is not None:
                event = normalize_navigation_event_alias(event)
                stats.events_processed += 1
                if event == "QUIT":
                    break
                if current_view == "menu" and event == "UP":
                    if footer_selected:
                        footer_selected = False
                        selected_idx = len(MENU_ITEMS) - 1
                    else:
                        selected_idx = (selected_idx - 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif current_view == "menu" and event == "DOWN":
                    if footer_selected:
                        footer_selected = False
                        selected_idx = 0
                    elif selected_idx == len(MENU_ITEMS) - 1:
                        footer_selected = True
                    else:
                        selected_idx += 1
                    should_redraw = True
                elif current_view == "menu" and event == "SELECT":
                    if footer_selected:
                        enter_now_playing()
                        should_redraw = True
                    else:
                        menu_item = MENU_ITEMS[selected_idx]
                        if menu_item == "Music":
                            music_nav_stack = [MusicViewState(title="Music", items=music_root_items, selected_idx=0)]
                            current_view = _current_music_view_name(music_nav_stack)
                            footer_selected = False
                            should_redraw = True
                        elif menu_item == "Now Playing":
                            enter_now_playing()
                            should_redraw = True
                        elif menu_item == "Settings":
                            settings_bt_status = run_settings_action(
                                "bluetooth_adapter_status",
                                settings_actions.bluetooth_adapter_status,
                            )
                            enter_settings_root()
                            footer_selected = False
                            should_redraw = True
                        else:
                            handled_menu_action = handle_menu_action(menu_item, library, player)
                            if handled_menu_action and menu_item == "Shuffle All":
                                set_now_playing_context_label("Shuffle All")
                            should_redraw = handled_menu_action or should_redraw
                elif current_view in ("music_root", "music_list") and event == "UP":
                    view = _current_music_view(music_nav_stack)
                    if footer_selected:
                        footer_selected = False
                        if view.items:
                            view.selected_idx = len(view.items) - 1
                    elif view.items:
                        view.selected_idx = (view.selected_idx - 1) % len(view.items)
                    else:
                        footer_selected = True
                    should_redraw = True
                elif current_view in ("music_root", "music_list") and event == "DOWN":
                    view = _current_music_view(music_nav_stack)
                    if footer_selected:
                        footer_selected = False
                        if view.items:
                            view.selected_idx = 0
                    elif view.items:
                        if view.selected_idx == len(view.items) - 1:
                            footer_selected = True
                        else:
                            view.selected_idx += 1
                    else:
                        footer_selected = True
                    should_redraw = True
                elif current_view in ("music_root", "music_list") and event == "SELECT":
                    if footer_selected:
                        enter_now_playing()
                        should_redraw = True
                    else:
                        view = _current_music_view(music_nav_stack)
                        if view.items:
                            selected_item = view.items[_clamp_index(view.selected_idx, len(view.items))]
                            queue_context_label = _music_playback_context_label(view, selected_item)
                            handled, next_view = _select_music_item(view, selected_item, player, library)
                            if handled and next_view is None and queue_context_label:
                                set_now_playing_context_label(queue_context_label)
                            should_redraw = handled or should_redraw
                            if next_view is not None:
                                music_nav_stack.append(next_view)
                                current_view = _current_music_view_name(music_nav_stack)
                                footer_selected = False
                                should_redraw = True
                elif current_view == "now_playing" and event == "UP":
                    now_playing_focus_index = (now_playing_focus_index - 1) % len(NOW_PLAYING_FOCUSABLE)
                    should_redraw = True
                elif current_view == "now_playing" and event == "DOWN":
                    now_playing_focus_index = (now_playing_focus_index + 1) % len(NOW_PLAYING_FOCUSABLE)
                    should_redraw = True
                elif current_view == "now_playing" and event == "SELECT":
                    focused = NOW_PLAYING_FOCUSABLE[now_playing_focus_index]
                    if focused == "PREV":
                        should_redraw = player.previous_track() or should_redraw
                    elif focused == "PLAY_PAUSE":
                        had_current_track = player_has_current_track()
                        play_pause_result = _play_pause_or_shuffle_all(player, library)
                        if play_pause_result and not had_current_track:
                            set_now_playing_context_label("Shuffle All")
                        should_redraw = play_pause_result or should_redraw
                    elif focused == "NEXT":
                        should_redraw = player.next_track() or should_redraw
                    elif focused == "SHUFFLE":
                        should_redraw = _toggle_player_mode(player, "shuffle") or should_redraw
                    elif focused == "LOOP":
                        should_redraw = _toggle_player_mode(player, "loop") or should_redraw
                elif current_view == "now_playing" and event == "BACK":
                    current_view = "menu"
                    now_playing_last_progress_bucket = -1
                    footer_selected = False
                    set_selected_label(footer_status_label(library_totals_label, player))
                    should_redraw = True
                elif current_view == "now_playing" and event == "TOGGLE_ART_MODE":
                    new_mode = _next_album_art_mode(album_art_render_mode)
                    set_settings_and_save(
                        PersistedSettings(
                            audio_output_mode=settings.audio_output_mode,
                            music_import_dir=settings.music_import_dir,
                            last_connected_bt_address=settings.last_connected_bt_address,
                            album_art_mode=new_mode,
                            now_playing_idle_art=settings.now_playing_idle_art,
                        )
                    )
                    should_redraw = True
                elif current_view in ("settings_root", "settings_list") and event == "UP":
                    view = _current_settings_view(settings_nav_stack)
                    if footer_selected:
                        footer_selected = False
                        if view.items:
                            view.selected_idx = len(view.items) - 1
                    elif view.items:
                        view.selected_idx = (view.selected_idx - 1) % len(view.items)
                    else:
                        footer_selected = True
                    should_redraw = True
                elif current_view in ("settings_root", "settings_list") and event == "DOWN":
                    view = _current_settings_view(settings_nav_stack)
                    if footer_selected:
                        footer_selected = False
                        if view.items:
                            view.selected_idx = 0
                    elif view.items:
                        if view.selected_idx == len(view.items) - 1:
                            footer_selected = True
                        else:
                            view.selected_idx += 1
                    else:
                        footer_selected = True
                    should_redraw = True
                elif current_view in ("settings_root", "settings_list") and event == "SELECT" and footer_selected:
                    enter_now_playing()
                    should_redraw = True
                elif current_view in ("settings_root", "settings_list") and event == "SELECT":
                    view = _current_settings_view(settings_nav_stack)
                    if view.items:
                        selected_item = view.items[_clamp_index(view.selected_idx, len(view.items))]
                        action = selected_item.action
                        if view.view_id == "settings_root":
                            if action == "open_bluetooth":
                                push_settings_view(build_settings_bluetooth_view())
                                current_view = _current_settings_view_name(settings_nav_stack)
                            elif action == "open_audio_output":
                                push_settings_view(build_settings_audio_view())
                                current_view = _current_settings_view_name(settings_nav_stack)
                            elif action == "open_album_art":
                                push_settings_view(build_settings_album_art_view())
                                current_view = _current_settings_view_name(settings_nav_stack)
                            elif action == "open_library":
                                push_settings_view(build_settings_library_view())
                                current_view = _current_settings_view_name(settings_nav_stack)
                            elif action == "open_about":
                                push_settings_view(build_settings_about_view())
                                current_view = _current_settings_view_name(settings_nav_stack)
                            should_redraw = True
                        elif view.view_id == "settings_bluetooth":
                            if action == "bt_scan":
                                scan_result = run_settings_action("bluetooth_scan", settings_actions.bluetooth_scan)
                                settings_last_result = scan_result.message
                                push_settings_view(build_settings_bt_scan_view(scan_result))
                                current_view = _current_settings_view_name(settings_nav_stack)
                                should_redraw = True
                            elif action == "bt_paired":
                                paired_result = run_settings_action(
                                    "bluetooth_paired_devices",
                                    settings_actions.bluetooth_paired_devices,
                                )
                                settings_last_result = paired_result.message
                                push_settings_view(build_settings_bt_paired_view(paired_result))
                                current_view = _current_settings_view_name(settings_nav_stack)
                                should_redraw = True
                        elif view.view_id == "settings_bt_scan":
                            if action == "bt_scan":
                                scan_result = run_settings_action("bluetooth_scan", settings_actions.bluetooth_scan)
                                settings_last_result = scan_result.message
                                replace_current_settings_view(
                                    build_settings_bt_scan_view(scan_result, selected_hint=view.selected_idx)
                                )
                                should_redraw = True
                            elif action == "bt_pair_connect" and selected_item.address:
                                pair_result = run_settings_action(
                                    "bluetooth_pair_connect",
                                    settings_actions.bluetooth_pair_connect,
                                    selected_item.address,
                                )
                                settings_last_result = pair_result.message
                                if pair_result.ok:
                                    set_settings_and_save(
                                        PersistedSettings(
                                            audio_output_mode=settings.audio_output_mode,
                                            music_import_dir=settings.music_import_dir,
                                            last_connected_bt_address=selected_item.address,
                                            album_art_mode=settings.album_art_mode,
                                            now_playing_idle_art=settings.now_playing_idle_art,
                                        )
                                    )
                                    settings_bt_status = run_settings_action(
                                        "bluetooth_adapter_status",
                                        settings_actions.bluetooth_adapter_status,
                                    )
                                    refresh_settings_root_if_needed()
                                should_redraw = True
                        elif view.view_id == "settings_bt_paired":
                            if action == "bt_open_device" and selected_item.address:
                                push_settings_view(build_settings_bt_device_view(selected_item.address))
                                current_view = _current_settings_view_name(settings_nav_stack)
                                should_redraw = True
                        elif view.view_id == "settings_bt_device":
                            address = selected_item.address or view.context
                            if address and action in {"bt_connect", "bt_disconnect", "bt_forget"}:
                                if action == "bt_connect":
                                    action_result = run_settings_action(
                                        "bluetooth_connect",
                                        settings_actions.bluetooth_connect,
                                        address,
                                    )
                                    if action_result.ok:
                                        set_settings_and_save(
                                            PersistedSettings(
                                                audio_output_mode=settings.audio_output_mode,
                                                music_import_dir=settings.music_import_dir,
                                                last_connected_bt_address=address,
                                                album_art_mode=settings.album_art_mode,
                                                now_playing_idle_art=settings.now_playing_idle_art,
                                            )
                                        )
                                elif action == "bt_disconnect":
                                    action_result = run_settings_action(
                                        "bluetooth_disconnect",
                                        settings_actions.bluetooth_disconnect,
                                        address,
                                    )
                                else:
                                    action_result = run_settings_action(
                                        "bluetooth_forget",
                                        settings_actions.bluetooth_forget,
                                        address,
                                    )
                                    if action_result.ok and settings.last_connected_bt_address == address:
                                        set_settings_and_save(
                                            PersistedSettings(
                                                audio_output_mode=settings.audio_output_mode,
                                                music_import_dir=settings.music_import_dir,
                                                last_connected_bt_address=None,
                                                album_art_mode=settings.album_art_mode,
                                                now_playing_idle_art=settings.now_playing_idle_art,
                                            )
                                        )
                                settings_last_result = action_result.message
                                settings_bt_status = run_settings_action(
                                    "bluetooth_adapter_status",
                                    settings_actions.bluetooth_adapter_status,
                                )
                                refresh_settings_root_if_needed()
                                if action == "bt_forget" and action_result.ok:
                                    if len(settings_nav_stack) > 1:
                                        settings_nav_stack.pop()
                                    paired_result = run_settings_action(
                                        "bluetooth_paired_devices",
                                        settings_actions.bluetooth_paired_devices,
                                    )
                                    replace_current_settings_view(build_settings_bt_paired_view(paired_result))
                                    current_view = _current_settings_view_name(settings_nav_stack)
                                else:
                                    replace_current_settings_view(
                                        build_settings_bt_device_view(address, selected_hint=view.selected_idx)
                                    )
                                should_redraw = True
                        elif view.view_id == "settings_audio":
                            if action == "set_audio_mode":
                                mode = str(selected_item.value or "").strip().lower()
                                if mode in {"auto", "aux", "bluetooth"}:
                                    set_settings_and_save(
                                        PersistedSettings(
                                            audio_output_mode=mode,
                                            music_import_dir=settings.music_import_dir,
                                            last_connected_bt_address=settings.last_connected_bt_address,
                                            album_art_mode=settings.album_art_mode,
                                            now_playing_idle_art=settings.now_playing_idle_art,
                                        )
                                    )
                                    settings_last_result = f"Audio mode set to {mode}"
                                    replace_current_settings_view(
                                        build_settings_audio_view(selected_hint=view.selected_idx)
                                    )
                                    refresh_settings_root_if_needed()
                                    should_redraw = True
                        elif view.view_id == "settings_album_art":
                            if action == "set_album_art_mode":
                                mode = _normalize_album_art_mode(selected_item.value)
                                set_settings_and_save(
                                    PersistedSettings(
                                        audio_output_mode=settings.audio_output_mode,
                                        music_import_dir=settings.music_import_dir,
                                        last_connected_bt_address=settings.last_connected_bt_address,
                                        album_art_mode=mode,
                                        now_playing_idle_art=settings.now_playing_idle_art,
                                    )
                                )
                                settings_last_result = f"Album art mode set to {mode}"
                                replace_current_settings_view(
                                    build_settings_album_art_view(selected_hint=view.selected_idx)
                                )
                                refresh_settings_root_if_needed()
                                should_redraw = True
                            elif action == "cycle_now_playing_idle_art":
                                next_idle_art_name = _next_now_playing_idle_art_name(
                                    settings.now_playing_idle_art
                                )
                                set_settings_and_save(
                                    PersistedSettings(
                                        audio_output_mode=settings.audio_output_mode,
                                        music_import_dir=settings.music_import_dir,
                                        last_connected_bt_address=settings.last_connected_bt_address,
                                        album_art_mode=settings.album_art_mode,
                                        now_playing_idle_art=next_idle_art_name,
                                    )
                                )
                                _persist_now_playing_idle_art_image(next_idle_art_name)
                                selected_idle_art, selected_idle_idx, total_idle_art = _idle_art_selection_details(
                                    next_idle_art_name
                                )
                                if total_idle_art <= 0 or selected_idle_art is None:
                                    settings_last_result = "No idle covers available"
                                else:
                                    settings_last_result = (
                                        f"Idle cover {selected_idle_idx}/{total_idle_art}: "
                                        f"{selected_idle_art.name}"
                                    )
                                replace_current_settings_view(
                                    build_settings_album_art_view(selected_hint=view.selected_idx)
                                )
                                should_redraw = True
                        elif view.view_id == "settings_library":
                            if action == "rebuild_library":
                                report = _safe_scan_library(library)
                                rebuild_music_index_and_nav(scan_library=False)
                                if report is None:
                                    settings_last_result = "Library rebuild failed"
                                else:
                                    settings_last_result = (
                                        f"Library rebuilt (+{report.added} ~{report.updated} -{report.removed})"
                                    )
                                should_redraw = True
                        elif view.view_id == "settings_about":
                            replace_current_settings_view(build_settings_about_view(selected_hint=view.selected_idx))
                            should_redraw = True
                elif current_view in ("music_root", "music_list") and event == "BACK":
                    if len(music_nav_stack) > 1:
                        music_nav_stack.pop()
                        current_view = _current_music_view_name(music_nav_stack)
                        footer_selected = False
                    else:
                        current_view = "menu"
                        footer_selected = False
                        set_selected_label(footer_status_label(library_totals_label, player))
                    should_redraw = True
                elif current_view in ("settings_root", "settings_list") and event == "BACK":
                    if len(settings_nav_stack) > 1:
                        settings_nav_stack.pop()
                        current_view = _current_settings_view_name(settings_nav_stack)
                        footer_selected = False
                    else:
                        current_view = "menu"
                        footer_selected = False
                        set_selected_label(footer_status_label(library_totals_label, player))
                    should_redraw = True
                elif event == "PLAY_PAUSE":
                    had_current_track = player_has_current_track()
                    play_pause_result = _play_pause_or_shuffle_all(player, library)
                    if play_pause_result and not had_current_track:
                        set_now_playing_context_label("Shuffle All")
                    should_redraw = play_pause_result or should_redraw
                elif event == "NEXT_TRACK":
                    should_redraw = player.next_track() or should_redraw
                elif event == "PREV_TRACK":
                    should_redraw = player.previous_track() or should_redraw
                elif event == "RESCAN_LIBRARY":
                    rebuild_music_index_and_nav(scan_library=True)
                    if current_view in ("music_root", "music_list"):
                        current_view = _current_music_view_name(music_nav_stack)
                    should_redraw = True
                else:
                    should_redraw = status_plumbing.apply_debug_event(event)
                    if should_redraw:
                        status = status_plumbing.read()
                        sync_audio_output(status, player)

            if player.poll():
                should_redraw = True

            if current_view == "now_playing":
                progress_s, _ = player.playback_progress()
                progress_bucket = int(progress_s)
                if progress_bucket != now_playing_last_progress_bucket:
                    now_playing_last_progress_bucket = progress_bucket
                    should_redraw = True

                current_song_text = _now_playing_song_artist_text(player, library)
                if current_song_text != now_playing_song_text:
                    set_now_playing_song_text(current_song_text)
                    should_redraw = True
                elif now_playing_song_should_scroll:
                    now_tick = now_clock()
                    if now_tick - now_playing_song_scroll_start_tick >= NOW_PLAYING_TITLE_SCROLL_DELAY_S:
                        elapsed = now_tick - now_playing_song_last_scroll_tick
                        steps = int(elapsed / NOW_PLAYING_TITLE_SCROLL_INTERVAL_S)
                        if steps > 0:
                            now_playing_song_last_scroll_tick += (
                                steps * NOW_PLAYING_TITLE_SCROLL_INTERVAL_S
                            )
                            now_playing_song_scroll_px += steps * NOW_PLAYING_TITLE_SCROLL_STEP_PX
                            should_redraw = True

            if current_view in ("music_root", "music_list"):
                if refresh_selected_music_scroll_state():
                    should_redraw = True
                elif music_item_should_scroll:
                    now_tick = now_clock()
                    if now_tick - music_item_scroll_start_tick >= MUSIC_ITEM_SCROLL_DELAY_S:
                        elapsed = now_tick - music_item_last_scroll_tick
                        steps = int(elapsed / MUSIC_ITEM_SCROLL_INTERVAL_S)
                        if steps > 0:
                            music_item_last_scroll_tick += steps * MUSIC_ITEM_SCROLL_INTERVAL_S
                            music_item_scroll_px += steps * MUSIC_ITEM_SCROLL_STEP_PX
                            should_redraw = True
            else:
                if music_item_scroll_key:
                    set_music_item_scroll_key("", "", 0)

            if current_view in ("settings_root", "settings_list"):
                refresh_settings_root_if_needed()
                if refresh_selected_settings_scroll_state():
                    should_redraw = True
                elif settings_item_should_scroll:
                    now_tick = now_clock()
                    if now_tick - settings_item_scroll_start_tick >= SETTINGS_ITEM_SCROLL_DELAY_S:
                        elapsed = now_tick - settings_item_last_scroll_tick
                        steps = int(elapsed / SETTINGS_ITEM_SCROLL_INTERVAL_S)
                        if steps > 0:
                            settings_item_last_scroll_tick += steps * SETTINGS_ITEM_SCROLL_INTERVAL_S
                            settings_item_scroll_px += steps * SETTINGS_ITEM_SCROLL_STEP_PX
                            should_redraw = True
                footer_label = _settings_footer_label(settings_nav_stack, settings_last_result)
            else:
                if settings_item_scroll_key:
                    set_settings_item_scroll_key("", "", 0)
                footer_label = footer_status_label(library_totals_label, player)
            if footer_label != selected_label:
                set_selected_label(footer_label)
                if current_view in ("menu", "music_root", "music_list", "settings_root", "settings_list"):
                    should_redraw = True

            if current_view in ("menu", "music_root", "music_list", "settings_root", "settings_list") and footer_should_scroll:
                elapsed = now_clock() - footer_last_scroll_tick
                steps = int(elapsed / FOOTER_SCROLL_INTERVAL_S)
                if steps > 0:
                    footer_last_scroll_tick += steps * FOOTER_SCROLL_INTERVAL_S
                    footer_scroll_px += steps * FOOTER_SCROLL_STEP_PX
                    should_redraw = True

            should_redraw = status_plumbing.tick_animation() or should_redraw

            if should_redraw:
                status = status_plumbing.read()
                if current_view == "now_playing":
                    image = render_now_playing(
                        epd,
                        fonts,
                        player,
                        library,
                        status,
                        status_plumbing.charge_anim_frame(),
                        album_art_cache,
                        album_art_render_mode=album_art_render_mode,
                        focus_index=now_playing_focus_index,
                        song_scroll_px=now_playing_song_scroll_px,
                        context_label=now_playing_context_label,
                        idle_art_selection=settings.now_playing_idle_art,
                    )
                elif current_view in ("music_root", "music_list"):
                    image = render_music_browser(
                        epd,
                        fonts,
                        _current_music_view(music_nav_stack),
                        status,
                        status_plumbing.charge_anim_frame(),
                        selected_label=selected_label,
                        footer_scroll_px=footer_scroll_px,
                        selected_item_scroll_px=music_item_scroll_px,
                        footer_selected=footer_selected,
                    )
                elif current_view in ("settings_root", "settings_list"):
                    image = render_settings_browser(
                        epd,
                        fonts,
                        _current_settings_view(settings_nav_stack),
                        status,
                        status_plumbing.charge_anim_frame(),
                        selected_label=selected_label,
                        footer_scroll_px=footer_scroll_px,
                        selected_item_scroll_px=settings_item_scroll_px,
                        footer_selected=footer_selected,
                    )
                else:
                    image = render_menu(
                        epd,
                        fonts,
                        selected_idx,
                        status,
                        status_plumbing.charge_anim_frame(),
                        selected_label,
                        footer_scroll_px=footer_scroll_px,
                        footer_selected=footer_selected,
                    )
                epd.displayPartial(epd.getbuffer(image))
                stats.frames_partial += 1

        stats.final_view = current_view
        stats.selected_index = selected_idx
        stats.selected_menu_item = MENU_ITEMS[selected_idx]
        stats.now_playing_label = _safe_now_playing_label(player)
        stats.now_playing_context_label = now_playing_context_label
        stats.library_totals_label = library_totals_label
        return stats.to_dict()

    except KeyboardInterrupt:
        stats.status = "interrupted"
        stats.final_view = current_view
        stats.selected_index = selected_idx
        stats.selected_menu_item = MENU_ITEMS[selected_idx]
        stats.now_playing_label = _safe_now_playing_label(player)
        stats.now_playing_context_label = now_playing_context_label
        return stats.to_dict()
    except Exception as exc:
        stats.status = "error"
        stats.error = str(exc)
        stats.final_view = current_view
        stats.selected_index = selected_idx
        stats.selected_menu_item = MENU_ITEMS[selected_idx]
        stats.now_playing_label = _safe_now_playing_label(player)
        stats.now_playing_context_label = now_playing_context_label
        if config.raise_exceptions:
            raise
        return stats.to_dict()
