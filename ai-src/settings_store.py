#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = ROOT_DIR / "data"
DEFAULT_SETTINGS_PATH = DATA_DIR / "settings.json"
DEFAULT_IMPORT_DIR = "/home/jrwhite/PiPodSync/inbox"
VALID_AUDIO_OUTPUT_MODES = {"auto", "aux", "bluetooth"}
VALID_ALBUM_ART_MODES = {"enhanced", "classic"}


@dataclass(frozen=True)
class PersistedSettings:
    audio_output_mode: str = "auto"
    music_import_dir: str = DEFAULT_IMPORT_DIR
    last_connected_bt_address: str | None = None
    album_art_mode: str = "enhanced"

    @classmethod
    def from_raw(cls, raw: dict | None) -> "PersistedSettings":
        raw = raw if isinstance(raw, dict) else {}

        mode = str(raw.get("audio_output_mode", "auto") or "auto").strip().lower()
        if mode == "speaker":
            mode = "aux"
        if mode not in VALID_AUDIO_OUTPUT_MODES:
            mode = "auto"

        import_dir = str(raw.get("music_import_dir", DEFAULT_IMPORT_DIR) or "").strip()
        if not import_dir:
            import_dir = DEFAULT_IMPORT_DIR

        last_bt = raw.get("last_connected_bt_address")
        if last_bt is not None:
            last_bt = str(last_bt).strip()
            if not last_bt:
                last_bt = None

        album_art_mode = str(raw.get("album_art_mode", "enhanced") or "enhanced").strip().lower()
        if album_art_mode not in VALID_ALBUM_ART_MODES:
            album_art_mode = "enhanced"

        return cls(
            audio_output_mode=mode,
            music_import_dir=import_dir,
            last_connected_bt_address=last_bt,
            album_art_mode=album_art_mode,
        )


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH

    def load(self) -> PersistedSettings:
        if not self.path.exists():
            settings = PersistedSettings()
            self.save(settings)
            return settings

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            settings = PersistedSettings()
            self.save(settings)
            return settings

        settings = PersistedSettings.from_raw(raw)
        if raw != asdict(settings):
            self.save(settings)
        return settings

    def save(self, settings: PersistedSettings) -> None:
        payload = asdict(PersistedSettings.from_raw(asdict(settings)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)
