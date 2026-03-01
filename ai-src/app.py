#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys

from pathlib import Path

from library import MusicLibrary
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

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
LIB_DIR = ROOT_DIR / "lib"

if LIB_DIR.exists():
    sys.path.insert(0, str(LIB_DIR))

from waveshare_epd import epd2in13_V4

logging.basicConfig(level=logging.INFO)


def main():
    epd = None
    library = None
    player = None
    try:
        fonts = load_fonts()
        status_plumbing = StatusPlumbing()
        status = status_plumbing.read()

        library = MusicLibrary(music_root=MUSIC_DIR, db_path=LIBRARY_DB_PATH)
        player = MusicPlayer()
        sync_audio_output(status, player)

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
            event_provider=read_key_event,
            fonts=fonts,
            status_plumbing=status_plumbing,
        )

        stats = run_pipod_loop(config, dependencies)
        if stats.get("status") == "interrupted":
            logging.info("Interrupted by user")

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
