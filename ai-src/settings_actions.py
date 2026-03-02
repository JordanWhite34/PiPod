#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
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
_SCAN_NEW_LINE = re.compile(r"^\[NEW\]\s+Device\s+([0-9A-F:]{17})(?:\s+(.+))?$", flags=re.IGNORECASE)
_SCAN_CHANGED_NAME_LINE = re.compile(
    r"^\[CHG\]\s+Device\s+([0-9A-F:]{17})\s+(?:Name|Alias):\s+(.+)$",
    flags=re.IGNORECASE,
)
_ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


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
            prep_commands = (
                ["power", "on"],
                ["agent", "on"],
                ["default-agent"],
                ["pairable", "on"],
            )
            for command in prep_commands:
                prep_output = self._run_bt(command)
                if prep_output is None:
                    return SettingsActionResult(ok=False, message="Bluetooth unavailable")
                if self._output_indicates_failure(prep_output):
                    fallback_devices = self._devices_with_state(
                        self._list_devices(command="paired-devices"),
                        paired=True,
                    )
                    return SettingsActionResult(
                        ok=False,
                        message=f"Bluetooth adapter setup failed ({' '.join(command)})",
                        details={"devices": fallback_devices, "output": prep_output},
                    )

            scan_output = self._run_bt_interactive_scan(scan_timeout)
            if scan_output is None:
                return SettingsActionResult(ok=False, message="Bluetooth unavailable")
        except TimeoutError:
            fallback_devices = self._devices_with_state(self._list_devices(command="paired-devices"), paired=True)
            return SettingsActionResult(
                ok=False,
                message="Bluetooth scan timed out",
                details={"devices": fallback_devices},
            )
        except Exception as exc:
            logging.warning("Bluetooth scan failed: %s", exc)
            fallback_devices = self._devices_with_state(self._list_devices(command="paired-devices"), paired=True)
            return SettingsActionResult(
                ok=False,
                message="Bluetooth scan failed",
                details={"devices": fallback_devices},
            )

        discovered_devices = self._parse_scan_discoveries(scan_output)
        paired_devices = self._list_devices(command="paired-devices")
        merged_devices = self._merge_devices(discovered_devices, paired_devices)
        devices = self._devices_with_state(merged_devices)
        nearby_count = len({address for address, _ in discovered_devices})
        paired_count = len({address for address, _ in paired_devices})
        return SettingsActionResult(
            ok=True,
            message=f"Found {nearby_count} nearby device(s), {paired_count} paired total",
            details={
                "devices": devices,
                "nearby_count": nearby_count,
                "paired_count": paired_count,
            },
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
            (
                "Progress Border",
                "on" if bool(getattr(settings, "now_playing_progress_ring", False)) else "off",
            ),
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

    def _run_bt_interactive_scan(self, scan_timeout: int) -> str | None:
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            return None

        try:
            if proc.stdin is None:
                raise RuntimeError("bluetoothctl stdin unavailable")
            proc.stdin.write("scan on\n")
            proc.stdin.flush()
            time.sleep(scan_timeout)
            for command in ("scan off", "devices", "paired-devices", "quit"):
                proc.stdin.write(f"{command}\n")
            proc.stdin.flush()
            output, _ = proc.communicate(timeout=scan_timeout + 10)
            return str(output or "").strip()
        except subprocess.TimeoutExpired as exc:
            if proc.poll() is None:
                proc.kill()
            try:
                remaining, _ = proc.communicate(timeout=2)
            except Exception:
                remaining = ""
            partial = f"{str(exc.stdout or '').strip()}\n{str(remaining or '').strip()}".strip()
            raise TimeoutError(partial) from exc
        except Exception:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.communicate(timeout=2)
            except Exception:
                pass
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()

    @staticmethod
    def _parse_scan_discoveries(output: str) -> list[tuple[str, str]]:
        devices: dict[str, str] = {}
        order: list[str] = []
        for raw_line in str(output or "").splitlines():
            line = _ANSI_ESCAPE.sub("", raw_line).strip()
            if not line:
                continue

            changed_name_match = _SCAN_CHANGED_NAME_LINE.match(line)
            if changed_name_match is not None:
                address = changed_name_match.group(1).upper()
                name = changed_name_match.group(2).strip() or address
                if address not in devices:
                    order.append(address)
                devices[address] = name
                continue

            new_match = _SCAN_NEW_LINE.match(line)
            if new_match is None:
                continue
            address = new_match.group(1).upper()
            name = (new_match.group(2) or "").strip() or address
            if address not in devices:
                order.append(address)
                devices[address] = name
                continue
            if devices[address] == address and name != address:
                devices[address] = name

        return [(address, devices[address]) for address in order]

    @staticmethod
    def _merge_devices(
        primary: Iterable[tuple[str, str]],
        secondary: Iterable[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        merged: dict[str, str] = {}
        order: list[str] = []

        def add(entries: Iterable[tuple[str, str]], *, prefer_existing: bool):
            for address, name in entries:
                normalized_address = str(address).strip().upper()
                if not normalized_address:
                    continue
                normalized_name = str(name or "").strip() or normalized_address
                existing = merged.get(normalized_address)
                if existing is None:
                    merged[normalized_address] = normalized_name
                    order.append(normalized_address)
                    continue
                if prefer_existing:
                    if existing == normalized_address and normalized_name != normalized_address:
                        merged[normalized_address] = normalized_name
                    continue
                if existing == normalized_address and normalized_name != normalized_address:
                    merged[normalized_address] = normalized_name

        add(primary, prefer_existing=True)
        add(secondary, prefer_existing=False)
        return [(address, merged[address]) for address in order]

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

    @staticmethod
    def _output_indicates_failure(output: str) -> bool:
        lowered = str(output or "").strip().lower()
        if not lowered:
            return True
        failure_markers = (
            "not available",
            "failed",
            "error",
            "no default controller available",
            "not ready",
            "not powered",
        )
        return any(marker in lowered for marker in failure_markers)
