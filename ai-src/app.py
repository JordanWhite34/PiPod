#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import sys

from pathlib import Path

from library import MusicLibrary
from input_provider import CombinedEventProvider, GpioFiveWayConfig, GpioFiveWayInput
from pipod_runtime import (
    LIBRARY_DB_PATH,
    MUSIC_DIR,
    RunConfig,
    RuntimeDependencies,
    StatusPlumbing,
    load_fonts,
    read_key_event,
    run_pipod_loop,
    sync_audio_output,
)
from player import MusicPlayer
from settings_actions import SettingsActions
from settings_store import SettingsStore

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
LIB_DIR = ROOT_DIR / "lib"

if LIB_DIR.exists():
    sys.path.insert(0, str(LIB_DIR))

from waveshare_epd import epd2in13_V4, epdconfig

logging.basicConfig(level=logging.INFO)


def main():
    epd = None
    library = None
    player = None
    event_provider = read_key_event
    combined_input = None
    try:
        fonts = load_fonts()
        status_plumbing = StatusPlumbing()
        status = status_plumbing.read()

        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        logging.info("Music folder ready: %s", MUSIC_DIR)

        library = MusicLibrary(music_root=MUSIC_DIR, db_path=LIBRARY_DB_PATH)
        player = MusicPlayer()
        sync_audio_output(status, player)

        gpio_config = GpioFiveWayConfig.from_env()
        if gpio_config.enabled:
            try:
                gpio_input = GpioFiveWayInput(gpio_config)
            except Exception as exc:
                logging.warning("GPIO input unavailable; using keyboard only: %s", exc)
            else:
                combined_input = CombinedEventProvider(read_key_event, gpio_input=gpio_input)
                event_provider = combined_input
                logging.info(
                    "GPIO input enabled (BCM: up=%d down=%d left=%d right=%d select=%d vol_up=%s vol_down=%s, debounce=%dms, select_hold=%dms, pull_up=%s)",
                    gpio_config.up_pin,
                    gpio_config.down_pin,
                    gpio_config.left_pin,
                    gpio_config.right_pin,
                    gpio_config.select_pin,
                    gpio_config.vol_up_pin,
                    gpio_config.vol_down_pin,
                    gpio_config.debounce_ms,
                    gpio_config.select_hold_ms,
                    gpio_config.pull_up,
                )
        else:
            logging.info("GPIO input disabled by PIPOD_GPIO_ENABLED=0; keyboard only")

        logging.info(
            "EPD pins (BCM): rst=%s dc=%s cs=%s busy=%s pwr=%s spi=%s.%s",
            epdconfig.RST_PIN,
            epdconfig.DC_PIN,
            epdconfig.CS_PIN,
            epdconfig.BUSY_PIN,
            epdconfig.PWR_PIN,
            os.getenv("PIPOD_EPD_SPI_BUS", "0"),
            os.getenv("PIPOD_EPD_SPI_DEVICE", "0"),
        )
        epd = epd2in13_V4.EPD()

        config = RunConfig(
            timeout_s=0.1,
            interactive=True,
            show_controls_log=True,
            initialize_display=True,
            clear_display_on_start=True,
            loop_step_s=None,
            raise_exceptions=True,
        )
        dependencies = RuntimeDependencies(
            display=epd,
            library=library,
            player=player,
            event_provider=event_provider,
            fonts=fonts,
            status_plumbing=status_plumbing,
            settings_store=SettingsStore(),
            settings_actions=SettingsActions(music_dir=MUSIC_DIR),
        )

        stats = run_pipod_loop(config, dependencies)
        if stats.get("status") == "interrupted":
            logging.info("Interrupted by user")

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        if combined_input is not None:
            combined_input.close()
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
