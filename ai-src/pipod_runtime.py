#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import io
import logging
import select
import sys
import time
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover - runtime dependency check
    MutagenFile = None

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
PIC_DIR = ROOT_DIR / "pic"
FONT_PATH = PIC_DIR / "Font.ttc"
ICONS_DIR = APP_DIR / "assets" / "icons"
SHUFFLE_ICON_PATH = ICONS_DIR / "shuffle_thick.png"
LOOP_ICON_PATH = ICONS_DIR / "loop.png"
MUSIC_DIR = Path("/home/jrwhite/Music")
DATA_DIR = ROOT_DIR / "data"
LIBRARY_DB_PATH = DATA_DIR / "library.db"

logging.basicConfig(level=logging.INFO)

MENU_ITEMS = [
    "Music",
    "Now Playing",
    "Shuffle All",
    "Settings",
]

DEFAULT_FOOTER_TEXT = "u/d/s q p n r"
NOW_PLAYING_FOOTER_TEXT = "b back  q quit"
FOOTER_SCROLL_INTERVAL_S = 0.16
FOOTER_SCROLL_STEP_PX = 1
FOOTER_SCROLL_GAP_PX = 24
NOW_PLAYING_TITLE_SCROLL_DELAY_S = 2.2
NOW_PLAYING_TITLE_SCROLL_INTERVAL_S = 0.12
NOW_PLAYING_TITLE_SCROLL_STEP_PX = 1
NOW_PLAYING_TITLE_SCROLL_GAP_PX = 24
NOW_PLAYING_ART_SIZE = 96
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
_MODE_ICON_MASK_CACHE: dict[tuple[str, int, int], Image.Image | None] = {}


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


EventProvider = Callable[[float], str | None]

NOW_PLAYING_FOCUSABLE = (
    "PREV",
    "PLAY_PAUSE",
    "NEXT",
    "SHUFFLE",
    "LOOP",
)


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
            "library_totals_label": self.library_totals_label,
        }


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


def load_album_art(track_path, size, cache):
    if track_path in cache:
        return cache[track_path]

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
                art_image = ImageOps.fit(
                    source.convert("L"),
                    (size, size),
                    method=resample,
                ).convert("1")
        except Exception:
            art_image = None

    cache[track_path] = art_image
    return art_image


def _find_folder_art_path(track_path):
    parent = Path(track_path).parent
    for name in FOLDER_ART_NAMES:
        candidate = parent / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


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
):
    """Draw footer text and loop-scroll it when it exceeds available width."""
    if width <= 0 or height <= 0:
        return

    footer = Image.new("1", (width, height), 255)
    footer_draw = ImageDraw.Draw(footer)
    text = str(text or "").strip()
    if not text:
        image.paste(footer, (x, y))
        return

    text_width = int(footer_draw.textlength(text, font=font) + 0.999)
    if text_width <= width:
        footer_draw.text((0, 0), text, font=font, fill=0)
    else:
        cycle_width = text_width + FOOTER_SCROLL_GAP_PX
        offset = int(scroll_px) % cycle_width
        footer_draw.text((-offset, 0), text, font=font, fill=0)
        footer_draw.text((cycle_width - offset, 0), text, font=font, fill=0)

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
        tracks = library.random_tracks()
        if not tracks:
            library.scan()
            tracks = library.random_tracks()
        if not tracks:
            return False

        started = player.set_queue(
            [track.path for track in tracks],
            shuffle=True,
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
            shuffle=True,
            autoplay=True,
        )

    return False


def render_menu(
    epd,
    fonts,
    selected_idx,
    status,
    charge_anim_frame,
    selected_label=None,
    footer_scroll_px=0,
):
    """Render a mono frame buffer for the main menu."""
    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    title_font, item_font, hint_font = fonts

    draw.text((8, 4), "PiPod", font=title_font, fill=0)
    status_x = epd.width - 31
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
    focus_index=1,
    song_scroll_px=0,
):
    """Render a mono frame buffer for album art + progress playback view."""
    image = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)
    _, item_font, hint_font = fonts

    status_x = epd.width - 31
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

    left = 8
    max_width = epd.width - (left * 2)
    state = player.state()

    progress_s, progress_duration_s = player.playback_progress()
    art_left = (epd.width - NOW_PLAYING_ART_SIZE) // 2
    art_top = 32
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
        draw.rectangle((art_left, art_top, art_right, art_bottom), outline=0, fill=255)
        draw.text((art_left + 12, art_top + 40), "NO COVER", font=item_font, fill=0)
        song_name = "Nothing queued"
        artist_name = "Use Shuffle All"
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

        album_art = load_album_art(state.current_track, NOW_PLAYING_ART_SIZE, album_art_cache)
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
    progress_y = art_bottom + 10
    progress_w = epd.width - (left * 2)
    draw_progress_bar(draw, progress_x, progress_y, progress_w, 8, progress_bar_ratio)

    time_y = progress_y + 11
    draw.text((progress_x, time_y), elapsed_label, font=hint_font, fill=0)
    duration_w = measure_text_width(duration_label, hint_font)
    draw.text((progress_x + progress_w - duration_w, time_y), duration_label, font=hint_font, fill=0)

    song_artist = f"{song_name} - {artist_name}"
    song_line_y = time_y + 14
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
    controls_y = time_y + 40
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
    raw = raw.strip().lower()
    mapping = {
        "u": "UP",
        "up": "UP",
        "d": "DOWN",
        "down": "DOWN",
        "s": "SELECT",
        "select": "SELECT",
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
    }
    return mapping.get(raw)


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


def _safe_library_totals_label(library):
    try:
        return format_library_totals(library)
    except Exception as exc:
        logging.warning("Failed to read library totals: %s", exc)
        return "0 artists | 0 songs | 0 albums"


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
        return "Nothing queued - Use Shuffle All"

    metadata = library.track_by_path(current)
    if metadata is not None:
        return f"{metadata.title} - {metadata.artist}"

    current_path = Path(current)
    song_name = current_path.stem
    artist_name = "Unknown Artist"
    if current_path.parent and current_path.parent.parent:
        artist_name = current_path.parent.parent.name
    return f"{song_name} - {artist_name}"


def _play_pause_or_shuffle_all(player, library) -> bool:
    try:
        current = player.current_track_path()
    except Exception:
        current = None

    if current is None:
        return handle_menu_action("Shuffle All", library, player)
    try:
        return bool(player.toggle_pause())
    except Exception:
        return False


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

    stats = RunStats()

    selected_idx = 0
    current_view = "menu"
    selected_label = None
    album_art_cache = {}
    now_playing_last_progress_bucket = -1
    now_playing_focus_index = 1
    now_playing_song_text = ""
    now_playing_song_scroll_px = 0
    now_playing_song_should_scroll = False
    now_playing_song_last_scroll_tick = 0.0
    now_playing_song_scroll_start_tick = 0.0
    footer_width = epd.width - 12
    footer_scroll_px = 0

    virtual_clock = 0.0

    def now_clock():
        if config.loop_step_s is None:
            return time.monotonic()
        return virtual_clock

    footer_last_scroll_tick = now_clock()
    now_playing_song_last_scroll_tick = now_clock()
    now_playing_song_scroll_start_tick = now_clock()
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

    try:
        status = status_plumbing.read()
        sync_audio_output(status, player)

        _safe_scan_library(library)
        library_totals_label = _safe_library_totals_label(library)

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
        )
        epd.displayPartBaseImage(epd.getbuffer(image))
        stats.frames_base += 1

        if config.show_controls_log and config.interactive:
            logging.info("Music library root: %s", MUSIC_DIR)
            logging.info(
                "Controls: u/d/s, b (back), q, p/n/k/r, v+/v-, b+/b-, m, c then Enter"
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
                stats.events_processed += 1
                if event == "QUIT":
                    break
                if current_view == "menu" and event == "UP":
                    selected_idx = (selected_idx - 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif current_view == "menu" and event == "DOWN":
                    selected_idx = (selected_idx + 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif current_view == "menu" and event == "SELECT":
                    menu_item = MENU_ITEMS[selected_idx]
                    if menu_item == "Now Playing":
                        current_view = "now_playing"
                        now_playing_focus_index = 1
                        now_playing_last_progress_bucket = -1
                        set_now_playing_song_text(_now_playing_song_artist_text(player, library))
                        should_redraw = True
                    elif handle_menu_action(menu_item, library, player):
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
                        should_redraw = _play_pause_or_shuffle_all(player, library) or should_redraw
                    elif focused == "NEXT":
                        should_redraw = player.next_track() or should_redraw
                    elif focused == "SHUFFLE":
                        should_redraw = _toggle_player_mode(player, "shuffle") or should_redraw
                    elif focused == "LOOP":
                        should_redraw = _toggle_player_mode(player, "loop") or should_redraw
                elif current_view == "now_playing" and event == "BACK":
                    current_view = "menu"
                    now_playing_last_progress_bucket = -1
                    set_selected_label(footer_status_label(library_totals_label, player))
                    should_redraw = True
                elif event == "PLAY_PAUSE":
                    should_redraw = _play_pause_or_shuffle_all(player, library) or should_redraw
                elif event == "NEXT_TRACK":
                    should_redraw = player.next_track() or should_redraw
                elif event == "PREV_TRACK":
                    should_redraw = player.previous_track() or should_redraw
                elif event == "RESCAN_LIBRARY":
                    _safe_scan_library(library)
                    library_totals_label = _safe_library_totals_label(library)
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

            footer_label = footer_status_label(library_totals_label, player)
            if footer_label != selected_label:
                set_selected_label(footer_label)
                if current_view == "menu":
                    should_redraw = True

            if current_view == "menu" and footer_should_scroll:
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
                        focus_index=now_playing_focus_index,
                        song_scroll_px=now_playing_song_scroll_px,
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
                    )
                epd.displayPartial(epd.getbuffer(image))
                stats.frames_partial += 1

        stats.final_view = current_view
        stats.selected_index = selected_idx
        stats.selected_menu_item = MENU_ITEMS[selected_idx]
        stats.now_playing_label = _safe_now_playing_label(player)
        stats.library_totals_label = library_totals_label
        return stats.to_dict()

    except KeyboardInterrupt:
        stats.status = "interrupted"
        stats.final_view = current_view
        stats.selected_index = selected_idx
        stats.selected_menu_item = MENU_ITEMS[selected_idx]
        stats.now_playing_label = _safe_now_playing_label(player)
        return stats.to_dict()
    except Exception as exc:
        stats.status = "error"
        stats.error = str(exc)
        stats.final_view = current_view
        stats.selected_index = selected_idx
        stats.selected_menu_item = MENU_ITEMS[selected_idx]
        stats.now_playing_label = _safe_now_playing_label(player)
        if config.raise_exceptions:
            raise
        return stats.to_dict()
