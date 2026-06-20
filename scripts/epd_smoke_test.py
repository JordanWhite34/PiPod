#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageFont


ROOT_DIR = Path(__file__).resolve().parents[1]
LIB_DIR = ROOT_DIR / "lib"
FONT_PATH = ROOT_DIR / "pic" / "Font.ttc"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw a visible smoke-test pattern on the PiPod Waveshare e-paper display."
    )
    parser.add_argument(
        "--pwr-pin",
        default=None,
        help=(
            "BCM pin used for the Waveshare PWR line. Use 'none' if the display is "
            "powered directly or the PWR line is not connected. Defaults to driver setting."
        ),
    )
    parser.add_argument(
        "--spi-bus",
        default=None,
        help="SPI bus number, default 0.",
    )
    parser.add_argument(
        "--spi-device",
        default=None,
        help="SPI device/chip-select number, default 0.",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=3.0,
        help="Seconds to hold each full-screen test pattern.",
    )
    parser.add_argument(
        "--sequence",
        choices=["smoke", "all", "clear", "black", "checker", "grid", "partial"],
        default="smoke",
        help="Which display manipulation sequence to run.",
    )
    parser.add_argument(
        "--partial-steps",
        type=int,
        default=8,
        help="Number of moving-box partial refresh steps for partial/all sequences.",
    )
    parser.add_argument(
        "--leave-on",
        action="store_true",
        help="Leave the final test pattern on screen instead of clearing before sleep.",
    )
    return parser.parse_args()


def configure_env(args: argparse.Namespace):
    if args.pwr_pin is not None:
        os.environ["PIPOD_EPD_PWR_PIN"] = str(args.pwr_pin)
    if args.spi_bus is not None:
        os.environ["PIPOD_EPD_SPI_BUS"] = str(args.spi_bus)
    if args.spi_device is not None:
        os.environ["PIPOD_EPD_SPI_DEVICE"] = str(args.spi_device)


def load_font(size: int):
    if FONT_PATH.exists():
        try:
            return ImageFont.truetype(str(FONT_PATH), size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_primary(width: int, height: int) -> Image.Image:
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    title_font = load_font(20)
    text_font = load_font(14)

    draw.rectangle((0, 0, width - 1, height - 1), outline=0)
    draw.rectangle((4, 4, width - 5, 36), fill=0)
    draw.text((10, 9), "PiPod", font=title_font, fill=255)
    draw.text((10, 48), "EPD TEST", font=title_font, fill=0)
    draw.text((10, 78), time.strftime("%H:%M:%S"), font=text_font, fill=0)
    draw.line((8, 108, width - 9, 108), fill=0, width=2)
    for idx, y in enumerate(range(124, 220, 16)):
        if idx % 2 == 0:
            draw.rectangle((10, y, width - 11, y + 8), fill=0)
        else:
            draw.rectangle((10, y, width - 11, y + 8), outline=0)
    return image


def draw_inverse(width: int, height: int) -> Image.Image:
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    title_font = load_font(18)
    text_font = load_font(13)

    draw.rectangle((0, 0, width - 1, height - 1), fill=0)
    draw.rectangle((8, 8, width - 9, height - 9), outline=255, width=2)
    draw.text((16, 28), "SCREEN OK", font=title_font, fill=255)
    draw.text((16, 62), "If you see this,", font=text_font, fill=255)
    draw.text((16, 82), "SPI + panel work.", font=text_font, fill=255)
    for x in range(16, width - 16, 16):
        draw.line((x, 122, x, height - 26), fill=255)
    for y in range(122, height - 26, 16):
        draw.line((16, y, width - 17, y), fill=255)
    return image


def draw_solid(width: int, height: int, color: int) -> Image.Image:
    return Image.new("1", (width, height), color)


def draw_checker(width: int, height: int, tile: int = 12) -> Image.Image:
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            if ((x // tile) + (y // tile)) % 2 == 0:
                draw.rectangle((x, y, min(width - 1, x + tile - 1), min(height - 1, y + tile - 1)), fill=0)
    draw.rectangle((0, 0, width - 1, height - 1), outline=0)
    return image


def draw_grid(width: int, height: int) -> Image.Image:
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    title_font = load_font(16)
    text_font = load_font(11)

    for x in range(0, width, 10):
        draw.line((x, 0, x, height - 1), fill=0)
    for y in range(0, height, 10):
        draw.line((0, y, width - 1, y), fill=0)
    draw.rectangle((0, 0, width - 1, height - 1), outline=0, width=2)
    draw.rectangle((5, 5, width - 6, 42), fill=255)
    draw.text((10, 10), "GRID TEST", font=title_font, fill=0)
    draw.text((10, 29), f"{width}x{height}", font=text_font, fill=0)
    return image


def draw_partial_frame(width: int, height: int, step: int, steps: int) -> Image.Image:
    image = draw_grid(width, height)
    draw = ImageDraw.Draw(image)
    text_font = load_font(12)
    steps = max(1, int(steps))
    usable_w = width - 42
    x = 8 + int((usable_w * step) / max(1, steps - 1))
    y = 176
    draw.rectangle((0, 150, width - 1, height - 1), fill=255)
    draw.text((8, 154), f"PARTIAL {step + 1}/{steps}", font=text_font, fill=0)
    draw.rectangle((x, y, x + 26, y + 26), fill=0)
    draw.rectangle((8, 214, width - 9, 228), outline=0)
    draw.rectangle((8, 214, 8 + int((width - 17) * (step + 1) / steps), 228), fill=0)
    return image


def show_full(epd, label: str, image: Image.Image, hold_seconds: float):
    print(f"Drawing {label}...")
    epd.displayPartBaseImage(epd.getbuffer(image))
    time.sleep(max(0.0, hold_seconds))


def run_partial_sequence(epd, steps: int, hold_seconds: float):
    steps = max(1, int(steps))
    print(f"Drawing partial refresh sequence ({steps} step(s))...")
    base = draw_grid(epd.width, epd.height)
    epd.displayPartBaseImage(epd.getbuffer(base))
    time.sleep(max(0.0, min(hold_seconds, 1.0)))
    for step in range(steps):
        frame = draw_partial_frame(epd.width, epd.height, step, steps)
        epd.displayPartial(epd.getbuffer(frame))
        time.sleep(max(0.0, min(hold_seconds, 0.75)))


def run_sequence(epd, args: argparse.Namespace):
    hold_seconds = max(0.0, args.hold_seconds)

    if args.sequence == "clear":
        print("Clearing to white...")
        epd.Clear(0xFF)
        return

    if args.sequence == "black":
        show_full(epd, "black fill", draw_solid(epd.width, epd.height, 0), hold_seconds)
        return

    if args.sequence == "checker":
        show_full(epd, "checkerboard", draw_checker(epd.width, epd.height), hold_seconds)
        return

    if args.sequence == "grid":
        show_full(epd, "grid", draw_grid(epd.width, epd.height), hold_seconds)
        return

    if args.sequence == "partial":
        run_partial_sequence(epd, args.partial_steps, hold_seconds)
        return

    print("Clearing to white...")
    epd.Clear(0xFF)
    show_full(epd, "primary test frame", draw_primary(epd.width, epd.height), hold_seconds)
    print("Drawing inverse test frame...")
    epd.displayPartial(epd.getbuffer(draw_inverse(epd.width, epd.height)))
    time.sleep(hold_seconds)

    if args.sequence == "all":
        show_full(epd, "white fill", draw_solid(epd.width, epd.height, 255), hold_seconds)
        show_full(epd, "black fill", draw_solid(epd.width, epd.height, 0), hold_seconds)
        show_full(epd, "checkerboard", draw_checker(epd.width, epd.height), hold_seconds)
        show_full(epd, "grid", draw_grid(epd.width, epd.height), hold_seconds)
        run_partial_sequence(epd, args.partial_steps, hold_seconds)


def main() -> int:
    args = parse_args()
    configure_env(args)
    sys.path.insert(0, str(LIB_DIR))

    from waveshare_epd import epd2in13_V4
    from waveshare_epd import epdconfig

    print("PiPod e-paper smoke test")
    print(f"  display: {epd2in13_V4.EPD_WIDTH}x{epd2in13_V4.EPD_HEIGHT}")
    print(f"  RST={epdconfig.RST_PIN} DC={epdconfig.DC_PIN} CS={epdconfig.CS_PIN}")
    print(f"  BUSY={epdconfig.BUSY_PIN} PWR={epdconfig.PWR_PIN}")
    print(
        "  SPI="
        f"{os.getenv('PIPOD_EPD_SPI_BUS', '0')}.{os.getenv('PIPOD_EPD_SPI_DEVICE', '0')}"
    )

    epd = epd2in13_V4.EPD()
    slept = False
    try:
        print("Initializing panel...")
        if epd.init() != 0:
            print("Display init returned non-zero status.")
            return 2

        run_sequence(epd, args)

        if not args.leave_on:
            print("Clearing before sleep...")
            epd.Clear(0xFF)

        print("Sleeping panel...")
        epd.sleep()
        slept = True
        print("EPD smoke test complete.")
        return 0
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    finally:
        if not slept:
            try:
                epd.sleep()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
