#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable

try:
    from library import SUPPORTED_AUDIO_EXTENSIONS as LIBRARY_AUDIO_EXTENSIONS
except Exception:
    LIBRARY_AUDIO_EXTENSIONS = {
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

SUPPORTED_SYNC_EXTENSIONS = {str(ext).lower() for ext in LIBRARY_AUDIO_EXTENSIONS}

_DEVICE_LINE = re.compile(r"^Device\s+([0-9A-F:]{17})\s+(.+)$", flags=re.IGNORECASE)


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool = False
    connected: bool = False
    trusted: bool = False


@dataclass(frozen=True)
class SettingsActionResult:
    ok: bool
    message: str
    details: dict[str, object] = field(default_factory=dict)


class SettingsActions:
    def __init__(self, music_dir: Path, scan_seconds: int = 6):
        self.music_dir = Path(music_dir).expanduser()
        self.scan_seconds = max(1, int(scan_seconds))

    def bluetooth_adapter_status(self) -> SettingsActionResult:
        if shutil.which("bluetoothctl") is None:
            return SettingsActionResult(ok=False, message="Bluetooth unavailable: bluetoothctl missing")

        output = self._run_bt(["show"])
        if output is None:
            return SettingsActionResult(ok=False, message="Bluetooth unavailable")

        powered = self._parse_bt_flag(output, "Powered")
        discoverable = self._parse_bt_flag(output, "Discoverable")
        pairable = self._parse_bt_flag(output, "Pairable")
        return SettingsActionResult(
            ok=True,
            message=f"Adapter {'on' if powered else 'off'}",
            details={
                "powered": powered,
                "discoverable": discoverable,
                "pairable": pairable,
            },
        )

    def bluetooth_scan(self, duration_s: int | None = None) -> SettingsActionResult:
        if shutil.which("bluetoothctl") is None:
            return SettingsActionResult(ok=False, message="Bluetooth unavailable: bluetoothctl missing")

        scan_timeout = max(1, int(duration_s or self.scan_seconds))
        try:
            subprocess.run(
                ["bluetoothctl", "--timeout", str(scan_timeout), "scan", "on"],
                check=False,
                capture_output=True,
                text=True,
                timeout=scan_timeout + 3,
            )
        except Exception:
            # Best-effort; we'll still read known devices below.
            pass

        devices = self._devices_with_state(self._list_devices(command="devices"))
        return SettingsActionResult(
            ok=True,
            message=f"Found {len(devices)} device(s)",
            details={"devices": devices},
        )

    def bluetooth_paired_devices(self) -> SettingsActionResult:
        if shutil.which("bluetoothctl") is None:
            return SettingsActionResult(ok=False, message="Bluetooth unavailable: bluetoothctl missing")

        devices = self._devices_with_state(self._list_devices(command="paired-devices"), paired=True)
        return SettingsActionResult(
            ok=True,
            message=f"{len(devices)} paired device(s)",
            details={"devices": devices},
        )

    def bluetooth_pair_connect(self, address: str) -> SettingsActionResult:
        address = str(address).strip().upper()
        if not address:
            return SettingsActionResult(ok=False, message="Bluetooth address required")

        pair_output = self._run_bt(["pair", address]) or ""
        trust_output = self._run_bt(["trust", address]) or ""
        connect_output = self._run_bt(["connect", address]) or ""
        ok = self._command_succeeded(pair_output) and self._command_succeeded(connect_output)
        message = "Paired and connected" if ok else "Pair/connect failed"
        return SettingsActionResult(
            ok=ok,
            message=message,
            details={
                "address": address,
                "pair_output": pair_output,
                "trust_output": trust_output,
                "connect_output": connect_output,
            },
        )

    def bluetooth_connect(self, address: str) -> SettingsActionResult:
        return self._single_bt_action(["connect", address], success_message="Connected")

    def bluetooth_disconnect(self, address: str) -> SettingsActionResult:
        return self._single_bt_action(["disconnect", address], success_message="Disconnected")

    def bluetooth_forget(self, address: str) -> SettingsActionResult:
        return self._single_bt_action(["remove", address], success_message="Device forgotten")

    def sync_music_from_import(self, import_dir: Path) -> SettingsActionResult:
        source_root = Path(import_dir).expanduser()
        if not source_root.exists() or not source_root.is_dir():
            return SettingsActionResult(
                ok=False,
                message=f"Import folder missing: {source_root}",
                details={"imported": 0, "skipped": 0, "errors": 1},
            )

        self.music_dir.mkdir(parents=True, exist_ok=True)

        imported = 0
        skipped = 0
        errors = 0
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            if source.suffix.lower() not in SUPPORTED_SYNC_EXTENSIONS:
                skipped += 1
                continue

            rel_path = source.relative_to(source_root)
            destination = self.music_dir / rel_path
            destination.parent.mkdir(parents=True, exist_ok=True)

            try:
                if destination.exists():
                    src_stat = source.stat()
                    dst_stat = destination.stat()
                    if src_stat.st_size == dst_stat.st_size:
                        skipped += 1
                        continue
                shutil.copy2(source, destination)
                imported += 1
            except Exception as exc:
                errors += 1
                logging.warning("Sync copy failed for %s -> %s: %s", source, destination, exc)

        ok = errors == 0
        return SettingsActionResult(
            ok=ok,
            message=f"Sync: {imported} imported, {skipped} skipped, {errors} errors",
            details={
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "source": str(source_root),
                "destination": str(self.music_dir),
            },
        )

    def system_info(self, player, library, settings) -> SettingsActionResult:
        try:
            state = player.state()
            backend = f"{getattr(state, 'backend', 'unknown')} ({'ok' if getattr(state, 'available', False) else 'unavailable'})"
        except Exception:
            backend = "unknown"

        try:
            artists, songs, albums = library.library_counts()
            library_label = f"{artists} artists / {songs} songs / {albums} albums"
        except Exception:
            library_label = "unknown"

        rows = (
            ("Audio Backend", backend),
            ("Audio Mode", str(getattr(settings, "audio_output_mode", "auto"))),
            ("Album Art", str(getattr(settings, "album_art_mode", "enhanced"))),
            ("Music Root", str(self.music_dir)),
            ("Import Folder", str(getattr(settings, "music_import_dir", ""))),
            ("Last BT Device", str(getattr(settings, "last_connected_bt_address", None) or "None")),
            ("Library", library_label),
        )
        return SettingsActionResult(ok=True, message="System information", details={"rows": rows})

    def _single_bt_action(self, command: list[str], success_message: str) -> SettingsActionResult:
        address = str(command[-1]).strip().upper()
        if shutil.which("bluetoothctl") is None:
            return SettingsActionResult(ok=False, message="Bluetooth unavailable: bluetoothctl missing")
        output = self._run_bt(command) or ""
        ok = self._command_succeeded(output)
        message = success_message if ok else f"{success_message} failed"
        return SettingsActionResult(
            ok=ok,
            message=message,
            details={"address": address, "output": output},
        )

    def _devices_with_state(
        self,
        devices: Iterable[tuple[str, str]],
        *,
        paired: bool = False,
    ) -> list[BluetoothDevice]:
        result: list[BluetoothDevice] = []
        for address, name in devices:
            info = self._run_bt(["info", address]) or ""
            result.append(
                BluetoothDevice(
                    address=address,
                    name=name,
                    paired=paired or self._parse_bt_flag(info, "Paired"),
                    connected=self._parse_bt_flag(info, "Connected"),
                    trusted=self._parse_bt_flag(info, "Trusted"),
                )
            )
        result.sort(key=lambda device: (device.name.casefold(), device.address))
        return result

    @staticmethod
    def _parse_bt_flag(output: str, key: str) -> bool:
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith(f"{key}:"):
                continue
            return line.split(":", 1)[1].strip().lower() in {"yes", "on", "true", "1"}
        return False

    @staticmethod
    def _parse_devices(output: str) -> list[tuple[str, str]]:
        devices: list[tuple[str, str]] = []
        for line in output.splitlines():
            match = _DEVICE_LINE.match(line.strip())
            if match is None:
                continue
            address = match.group(1).upper()
            name = match.group(2).strip()
            if not name:
                name = address
            devices.append((address, name))
        return devices

    def _list_devices(self, command: str) -> list[tuple[str, str]]:
        output = self._run_bt([command]) or ""
        return self._parse_devices(output)

    def _run_bt(self, commands: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                ["bluetoothctl", *commands],
                check=False,
                capture_output=True,
                text=True,
                timeout=12,
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            logging.warning("bluetoothctl %s failed: %s", commands, exc)
            return ""
        return f"{proc.stdout}\n{proc.stderr}".strip()

    @staticmethod
    def _command_succeeded(output: str) -> bool:
        lowered = str(output or "").strip().lower()
        if not lowered:
            return False
        if "not available" in lowered or "failed" in lowered or "error" in lowered:
            return False
        success_markers = (
            "successful",
            "succeeded",
            "done",
            "connection successful",
            "already connected",
            "disconnected",
            "removed",
            "changing",
        )
        return any(marker in lowered for marker in success_markers)
