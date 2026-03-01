#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import shutil
import sys
from typing import Any

from PIL import Image

from pipod_runtime import (
    MUSIC_DIR,
    RunConfig,
    RuntimeDependencies,
    StatusPlumbing,
    load_fonts,
    read_key_event,
    run_pipod_loop,
    sync_audio_output,
)
from sim_scenarios import WAIT, expand_scenarios, scenario_events
from simulator_adapters import FakeEPD, FixtureLibrary, MockPlayer

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DEFAULT_FIXTURE_PATH = ROOT_DIR / "data" / "sim_tracks.json"
DEFAULT_OUTPUT_DIR = APP_DIR / "sim-output" / "latest"


class ScriptedEventProvider:
    def __init__(self, events: list[str]):
        self._events = list(events)
        self._index = 0

    def __call__(self, timeout_s: float) -> str | None:
        _ = timeout_s
        if self._index >= len(self._events):
            return "QUIT"
        event = self._events[self._index]
        self._index += 1
        if event == WAIT:
            return None
        return event


class LivePreviewWindow:
    """Best-effort live frame preview window for interactive simulation."""

    def __init__(self, width: int, height: int, scale: int = 3, title: str = "PiPod Live Preview"):
        self.width = int(width)
        self.height = int(height)
        self.scale = max(1, int(scale))
        self.enabled = False
        self._closed = False
        self._photo = None
        self._tk_module = None
        self._image_tk = None
        self._root = None
        self._label = None

        try:
            import tkinter as tk
            from PIL import ImageTk

            self._tk_module = tk
            self._image_tk = ImageTk
            self._root = tk.Tk()
            self._root.title(title)
            self._root.resizable(False, False)
            self._root.protocol("WM_DELETE_WINDOW", self.close)
            self._label = tk.Label(self._root, bg="white")
            self._label.pack()
            self.enabled = True
        except Exception:
            self.enabled = False

    def show(self, image: Image.Image):
        if not self.enabled or self._closed:
            return
        try:
            frame = image.convert("L")
            if self.scale != 1:
                frame = frame.resize(
                    (self.width * self.scale, self.height * self.scale),
                    Image.Resampling.NEAREST if hasattr(Image, "Resampling") else Image.NEAREST,
                )
            self._photo = self._image_tk.PhotoImage(frame)
            self._label.configure(image=self._photo)
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._closed = True
            self.enabled = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PiPod simulator + regression harness")
    parser.add_argument(
        "--mode",
        choices=["interactive", "scenario", "both"],
        default="both",
        help="Run interactive mode, scripted scenarios, or both",
    )
    parser.add_argument(
        "--audio",
        choices=["mock", "real"],
        default="mock",
        help="Use deterministic mock audio or real pygame-backed audio",
    )
    parser.add_argument(
        "--scenario",
        choices=["smoke", "navigation", "playback", "status_controls", "music_browse", "all"],
        default="all",
        help="Scripted scenario to execute",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for frames and summary JSON",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=500,
        help="Hard cap on loop iterations for scripted runs",
    )
    parser.add_argument(
        "--no-frames",
        action="store_true",
        help="Disable frame image artifacts",
    )
    parser.add_argument(
        "--live-preview",
        action="store_true",
        help="Show a live window preview (interactive mode); disables frame file output",
    )
    parser.add_argument(
        "--preview-scale",
        type=int,
        default=3,
        help="Window upscale factor for --live-preview",
    )
    parser.add_argument(
        "--music-dir",
        default=str(MUSIC_DIR),
        help="Music root when --audio real is selected",
    )
    parser.add_argument(
        "--fixture-path",
        default=str(DEFAULT_FIXTURE_PATH),
        help="Path to deterministic fixture track catalog JSON",
    )
    return parser.parse_args()


def prepare_output_dir(path: Path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "frames").mkdir(parents=True, exist_ok=True)


def build_library_and_player(
    audio_mode: str,
    fixture_path: Path,
    output_dir: Path,
    run_label: str,
    music_dir: Path,
) -> tuple[Any, Any, list[str]]:
    notes: list[str] = []

    if audio_mode == "real":
        try:
            from library import MusicLibrary
            from player import MusicPlayer
        except Exception as exc:
            notes.append(f"audio=real requested but runtime imports failed ({exc}); using mock audio")
        else:
            if music_dir.exists() and music_dir.is_dir():
                db_path = output_dir / f"{run_label}_library.db"
                library = MusicLibrary(music_root=music_dir, db_path=db_path)
                player = MusicPlayer()
                status = "available" if getattr(player, "is_available", lambda: True)() else "unavailable"
                notes.append(f"using real library+audio (music_dir={music_dir}, player={status})")
                return library, player, notes

            notes.append(
                f"audio=real requested but music_dir missing ({music_dir}); using fixtures + mock audio"
            )

    library = FixtureLibrary(fixture_path=fixture_path)
    player = MockPlayer()
    player.set_track_durations(library.duration_map())
    notes.append("using deterministic fixture library + mock audio")
    return library, player, notes


def run_with_dependencies(
    run_label: str,
    event_provider,
    interactive: bool,
    args: argparse.Namespace,
    output_dir: Path,
    frame_prefix: str,
) -> dict:
    library, player, notes = build_library_and_player(
        audio_mode=args.audio,
        fixture_path=Path(args.fixture_path),
        output_dir=output_dir,
        run_label=run_label,
        music_dir=Path(args.music_dir).expanduser(),
    )

    preview = None
    if args.live_preview and interactive:
        preview = LivePreviewWindow(width=122, height=250, scale=args.preview_scale)
        if preview.enabled:
            notes.append("live preview enabled")
        else:
            notes.append("live preview unavailable in this environment")

    write_frames = (not args.no_frames) and (not args.live_preview)
    if args.live_preview:
        notes.append("frame file output disabled for live preview mode")

    epd = FakeEPD(
        width=122,
        height=250,
        output_dir=output_dir,
        write_frames=write_frames,
        frame_prefix=frame_prefix,
        frame_consumer=preview.show if preview is not None and preview.enabled else None,
    )

    status_plumbing = StatusPlumbing()
    sync_audio_output(status_plumbing.read(), player)

    config = RunConfig(
        timeout_s=0.1 if interactive else 0.0,
        max_steps=None if interactive else int(args.max_steps),
        interactive=interactive,
        show_controls_log=interactive,
        initialize_display=True,
        clear_display_on_start=True,
        loop_step_s=0.1 if interactive else 0.5,
        raise_exceptions=False,
    )
    dependencies = RuntimeDependencies(
        display=epd,
        library=library,
        player=player,
        event_provider=event_provider,
        fonts=load_fonts(),
        status_plumbing=status_plumbing,
    )

    try:
        stats = run_pipod_loop(config, dependencies)
    finally:
        try:
            library.close()
        except Exception:
            pass
        try:
            player.shutdown()
        except Exception:
            pass
        try:
            epd.sleep()
        except Exception:
            pass
        if preview is not None:
            preview.close()

    stats["run_label"] = run_label
    stats["mode"] = "interactive" if interactive else "scenario"
    stats["frames_written"] = epd.frame_count
    stats["notes"] = notes
    return stats


def print_run_summary(stats: dict):
    notes = "; ".join(stats.get("notes", []))
    line = (
        f"[{stats.get('run_label')}] status={stats.get('status')} "
        f"events={stats.get('events_processed')} frames={stats.get('frames_total')} "
        f"view={stats.get('final_view')} selected={stats.get('selected_menu_item')} "
        f"now='{stats.get('now_playing_label')}'"
    )
    print(line)
    if notes:
        print(f"  notes: {notes}")
    if stats.get("error"):
        print(f"  error: {stats['error']}")


def main() -> int:
    args = parse_args()

    logging.basicConfig(level=logging.INFO)

    output_dir = Path(args.output_dir).expanduser().resolve()
    prepare_output_dir(output_dir)

    runs: list[dict] = []

    if args.mode in ("scenario", "both"):
        for name in expand_scenarios(args.scenario):
            events = scenario_events(name)
            provider = ScriptedEventProvider(events)
            stats = run_with_dependencies(
                run_label=f"scenario:{name}",
                event_provider=provider,
                interactive=False,
                args=args,
                output_dir=output_dir,
                frame_prefix=f"scenario_{name}",
            )
            runs.append(stats)
            print_run_summary(stats)

    if args.mode in ("interactive", "both"):
        if not sys.stdin.isatty():
            note = {
                "run_label": "interactive",
                "mode": "interactive",
                "status": "skipped",
                "error": None,
                "events_processed": 0,
                "frames_total": 0,
                "final_view": "menu",
                "selected_menu_item": "Music",
                "now_playing_label": "",
                "notes": ["interactive mode skipped (stdin is not a TTY)"],
            }
            runs.append(note)
            print_run_summary(note)
        else:
            stats = run_with_dependencies(
                run_label="interactive",
                event_provider=read_key_event,
                interactive=True,
                args=args,
                output_dir=output_dir,
                frame_prefix="interactive",
            )
            runs.append(stats)
            print_run_summary(stats)

    summary = {
        "status": "ok" if all(run.get("status") in ("ok", "skipped", "interrupted") for run in runs) else "error",
        "args": {
            "mode": args.mode,
            "audio": args.audio,
            "scenario": args.scenario,
            "max_steps": args.max_steps,
            "frames_enabled": (not args.no_frames) and (not args.live_preview),
            "live_preview": args.live_preview,
            "output_dir": str(output_dir),
            "fixture_path": str(Path(args.fixture_path).expanduser().resolve()),
        },
        "runs": runs,
    }

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")

    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
