#!/usr/bin/env python3
from dataclasses import dataclass
import io
import logging
import select
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from library import MusicLibrary
from player import MusicPlayer

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover - runtime dependency check
    MutagenFile = None

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
LIB_DIR = ROOT_DIR / "lib"
PIC_DIR = ROOT_DIR / "pic"
FONT_PATH = PIC_DIR / "Font.ttc"
MUSIC_DIR = Path("/home/jrwhite/Music")
DATA_DIR = ROOT_DIR / "data"
LIBRARY_DB_PATH = DATA_DIR / "library.db"

if LIB_DIR.exists():
    sys.path.insert(0, str(LIB_DIR))

from waveshare_epd import epd2in13_V4

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
    inner_right = x + 18
    inner_bottom = y + 8
    width = inner_right - inner_left + 1
    height = inner_bottom - inner_top + 1

    # Slow bobbing of the whole liquid surface.
    bob = [0, 0, -1, -1, -1, 0, 0, 1, 1, 0]
    base_surface = inner_top + 2 + bob[frame % len(bob)]

    wave_primary = [0, 0, 1, 1, 2, 1, 1, 0, 0, -1, -1, -2, -1, -1]
    wave_secondary = [0, 1, 0, -1, 0, 1, 0, -1]

    surface_y = [inner_top] * width

    # Fill the battery with moving liquid.
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

        # Internal shimmer to make the liquid feel alive.
        for py in range(y_surface + 1, inner_bottom + 1):
            if ((col + py + frame) % 4) == 0:
                draw.point((px, py), fill=255)

    # Crest highlights sliding across the wave.
    for col in range(width):
        if (col + frame) % 5 == 0:
            draw.point((inner_left + col, surface_y[col]), fill=255)

    # Rising bubbles (white cutouts) drifting to the right as they rise.
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
    body_w = 20
    body_h = 10
    tip_w = 2

    draw.rectangle((x, y, x + body_w, y + body_h), outline=0, fill=255)
    draw.rectangle((x + body_w + 1, y + 3, x + body_w + tip_w, y + body_h - 3), fill=0)

    if is_charging and percent < 100:
        draw_charging_animation(draw, x, y, charge_anim_frame)
        return

    segments = 4
    gap = 1
    inner_w = body_w - 4
    seg_w = (inner_w - (segments - 1) * gap) // segments
    filled_segments = (percent + 24) // 25
    if percent == 0:
        filled_segments = 0

    for idx in range(segments):
        sx = x + 2 + idx * (seg_w + gap)
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

        # Recover from stale index paths or moved files by rescanning once.
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
    if state.is_paused:
        playback_state = "Paused"
    elif player.is_playing():
        playback_state = "Playing"
    else:
        playback_state = "Queued"

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
    draw.text(
        (left, time_y + 14),
        ellipsize_text(song_artist, item_font, max_width),
        font=item_font,
        fill=0,
    )

    controls_line = f"REV  SKIP  {playback_state}"
    draw.text(
        (left, time_y + 34),
        ellipsize_text(controls_line, hint_font, max_width),
        font=hint_font,
        fill=0,
    )

    modes_line = (
        f"SHUF:{'ON' if state.is_shuffle else 'OFF'}  "
        f"LOOP:{'ON' if state.is_loop else 'OFF'}"
    )
    draw.text(
        (left, time_y + 48),
        ellipsize_text(modes_line, hint_font, max_width),
        font=hint_font,
        fill=0,
    )

    footer_left = 6
    footer_top = epd.height - 20
    footer_width = epd.width - (footer_left * 2)
    footer_height = epd.height - footer_top
    draw.line((0, footer_top - 4, epd.width - 1, footer_top - 4), fill=0)
    draw_footer_text(
        image,
        hint_font,
        NOW_PLAYING_FOOTER_TEXT,
        footer_left,
        footer_top,
        footer_width,
        footer_height,
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


def main():
    epd = None
    library = None
    player = None
    try:
        fonts = load_fonts()
        status_plumbing = StatusPlumbing()
        status = status_plumbing.read()
        library = MusicLibrary(music_root=MUSIC_DIR, db_path=LIBRARY_DB_PATH)
        library.scan()
        library_totals_label = format_library_totals(library)
        player = MusicPlayer()
        sync_audio_output(status, player)

        epd = epd2in13_V4.EPD()
        logging.info("Initializing display")
        epd.init()
        epd.Clear(0xFF)

        selected_idx = 0
        current_view = "menu"
        selected_label = None
        album_art_cache = {}
        now_playing_last_progress_bucket = -1
        footer_width = epd.width - 12
        footer_scroll_px = 0
        footer_last_scroll_tick = time.monotonic()
        footer_should_scroll = False

        def set_selected_label(value):
            nonlocal selected_label
            nonlocal footer_scroll_px
            nonlocal footer_last_scroll_tick
            nonlocal footer_should_scroll
            selected_label = value
            footer_scroll_px = 0
            footer_last_scroll_tick = time.monotonic()
            if not selected_label:
                footer_should_scroll = False
                return
            footer_should_scroll = (
                measure_text_width(str(selected_label).strip(), fonts[2]) > footer_width
            )

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
        logging.info("Music library root: %s", MUSIC_DIR)
        logging.info(
            "Controls: u/d/s, b (back), q, p/n/k/r, v+/v-, b+/b-, m, c then Enter"
        )

        while True:
            event = read_key_event(timeout_s=0.1)
            should_redraw = False
            if event is not None:
                if event == "QUIT":
                    break
                elif current_view == "menu" and event == "UP":
                    selected_idx = (selected_idx - 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif current_view == "menu" and event == "DOWN":
                    selected_idx = (selected_idx + 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif current_view == "menu" and event == "SELECT":
                    menu_item = MENU_ITEMS[selected_idx]
                    if menu_item == "Now Playing":
                        current_view = "now_playing"
                        now_playing_last_progress_bucket = -1
                        should_redraw = True
                    elif handle_menu_action(menu_item, library, player):
                        should_redraw = True
                elif current_view == "now_playing" and event == "BACK":
                    current_view = "menu"
                    now_playing_last_progress_bucket = -1
                    set_selected_label(footer_status_label(library_totals_label, player))
                    should_redraw = True
                elif event == "PLAY_PAUSE":
                    should_redraw = player.toggle_pause() or should_redraw
                elif event == "NEXT_TRACK":
                    should_redraw = player.next_track() or should_redraw
                elif event == "PREV_TRACK":
                    should_redraw = player.previous_track() or should_redraw
                elif event == "RESCAN_LIBRARY":
                    library.scan()
                    library_totals_label = format_library_totals(library)
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

            footer_label = footer_status_label(library_totals_label, player)
            if footer_label != selected_label:
                set_selected_label(footer_label)
                if current_view == "menu":
                    should_redraw = True

            if current_view == "menu" and footer_should_scroll:
                now = time.monotonic()
                elapsed = now - footer_last_scroll_tick
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

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        if library is not None:
            library.close()
        if player is not None:
            player.shutdown()
        if epd is not None:
            logging.info("Clearing display before sleep")
            epd.init()
            epd.Clear(0xFF)
            logging.info("Putting display to sleep")
            epd.sleep()


if __name__ == "__main__":
    main()
