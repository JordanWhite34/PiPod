#!/usr/bin/env python3
from __future__ import annotations

WAIT = "WAIT"

SCENARIOS: dict[str, list[str]] = {
    "smoke": [
        WAIT,
        "QUIT",
    ],
    "navigation": [
        "DOWN",  # Music -> Now Playing
        "SELECT",
        WAIT,
        "BACK",
        "UP",
        "QUIT",
    ],
    "playback": [
        "DOWN",
        "DOWN",  # Music -> Shuffle All
        "SELECT",  # starts playback when queue exists
        WAIT,
        WAIT,
        "PLAY_PAUSE",
        WAIT,
        "PLAY_PAUSE",
        WAIT,
        "NEXT_TRACK",
        WAIT,
        WAIT,
        WAIT,
        "PREV_TRACK",
        WAIT,
        "QUIT",
    ],
    "status_controls": [
        "VOL_UP",
        "VOL_DOWN",
        "TOGGLE_MUTE",
        "BAT_DOWN",
        "BAT_UP",
        "TOGGLE_CHARGE",
        WAIT,
        WAIT,
        WAIT,
        "TOGGLE_CHARGE",
        "QUIT",
    ],
    "music_browse": [
        "SELECT",  # Music -> root
        "SELECT",  # Playlists
        "DOWN",  # Shuffle All
        "SELECT",  # start shuffle playback
        "BACK",  # back to root
        "DOWN",  # Artists
        "SELECT",
        "SELECT",
        "SELECT",
        "BACK",
        "BACK",
        "BACK",
        "DOWN",  # Albums
        "DOWN",
        "SELECT",
        "SELECT",
        "SELECT",
        "BACK",
        "BACK",
        "DOWN",  # Songs
        "DOWN",
        "DOWN",
        "SELECT",
        "BACK",
        "QUIT",
    ],
    "settings_smoke": [
        "DOWN",
        "DOWN",
        "DOWN",  # Settings
        "SELECT",  # enter settings root
        "DOWN",  # Music Sync
        "SELECT",
        "SELECT",  # Sync From Import Folder
        "BACK",
        "BACK",
        "QUIT",
    ],
}


def scenario_names() -> list[str]:
    return list(SCENARIOS.keys())


def scenario_events(name: str) -> list[str]:
    if name not in SCENARIOS:
        valid = ", ".join([*scenario_names(), "all"])
        raise ValueError(f"Unknown scenario '{name}'. Expected one of: {valid}")
    return list(SCENARIOS[name])


def expand_scenarios(selection: str) -> list[str]:
    selection = str(selection).strip().lower()
    if selection == "all":
        return scenario_names()
    return [selection]
