#!/usr/bin/env python3
from dataclasses import dataclass
import logging
import select
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
LIB_DIR = ROOT_DIR / "lib"
PIC_DIR = ROOT_DIR / "pic"
FONT_PATH = PIC_DIR / "Font.ttc"

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
    CHARGE_ANIM_INTERVAL_S = 0.35

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
            self._charge_anim_frame = (self._charge_anim_frame + 1) % 3
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


def draw_battery_icon(draw, x, y, percent, is_charging, charge_anim_frame=0):
    """Draw a compact battery icon at (x, y)."""
    percent = clamp_percent(percent)
    body_w = 20
    body_h = 10
    tip_w = 2

    draw.rectangle((x, y, x + body_w, y + body_h), outline=0, fill=255)
    draw.rectangle((x + body_w + 1, y + 3, x + body_w + tip_w, y + body_h - 3), fill=0)

    if is_charging and percent < 100:
        offsets = (-2, 0, 2)
        ox = offsets[charge_anim_frame % len(offsets)]
        bolt = [
            (x + 9 + ox, y + 1),
            (x + 7 + ox, y + 5),
            (x + 10 + ox, y + 5),
            (x + 8 + ox, y + 9),
            (x + 13 + ox, y + 4),
            (x + 10 + ox, y + 4),
        ]
        draw.polygon(bolt, fill=0)
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


def render_menu(epd, fonts, selected_idx, status, charge_anim_frame, selected_label=None):
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
    }
    return mapping.get(raw)


def main():
    epd = None
    try:
        fonts = load_fonts()
        status_plumbing = StatusPlumbing()
        status = status_plumbing.read()

        epd = epd2in13_V4.EPD()
        logging.info("Initializing display")
        epd.init()
        epd.Clear(0xFF)

        selected_idx = 0
        selected_label = None

        image = render_menu(
            epd,
            fonts,
            selected_idx,
            status,
            status_plumbing.charge_anim_frame(),
            selected_label,
        )
        epd.displayPartBaseImage(epd.getbuffer(image))
        logging.info("Controls: u/d/s/q, v+/v-, b+/b-, m, c then Enter")

        while True:
            event = read_key_event(timeout_s=0.1)
            should_redraw = False
            if event is not None:
                if event == "UP":
                    selected_idx = (selected_idx - 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif event == "DOWN":
                    selected_idx = (selected_idx + 1) % len(MENU_ITEMS)
                    should_redraw = True
                elif event == "SELECT":
                    selected_label = MENU_ITEMS[selected_idx]
                    should_redraw = True
                elif event == "QUIT":
                    break
                else:
                    should_redraw = status_plumbing.apply_debug_event(event)

            should_redraw = status_plumbing.tick_animation() or should_redraw

            if should_redraw:
                status = status_plumbing.read()
                image = render_menu(
                    epd,
                    fonts,
                    selected_idx,
                    status,
                    status_plumbing.charge_anim_frame(),
                    selected_label,
                )
                epd.displayPartial(epd.getbuffer(image))

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        if epd is not None:
            logging.info("Clearing display before sleep")
            epd.init()
            epd.Clear(0xFF)
            logging.info("Putting display to sleep")
            epd.sleep()


if __name__ == "__main__":
    main()
