# PiPod

PiPod is a Raspberry Pi Zero 2 W music player runtime with an e-paper UI, GPIO 5-way navigation input, Bluetooth/music sync settings, and a local simulator + regression harness.

This repository has been slimmed down to only the files needed for:
- On-device runtime (`ai-src/app.py`)
- Local simulator and automated regression tests (`ai-src/simulate_pipod.py`, `ai-src/test_simulator.py`)

## Project Layout
- `ai-src/app.py`: device entrypoint (e-paper + GPIO + runtime loop)
- `ai-src/pipod_runtime.py`: UI/rendering/application loop
- `ai-src/library.py`: music indexer (`sqlite`) and metadata extraction
- `ai-src/player.py`: queue-based audio playback wrapper
- `ai-src/input_provider.py`: keyboard + GPIO event providers
- `ai-src/settings_store.py`: persisted settings (`data/settings.json`)
- `ai-src/settings_actions.py`: Bluetooth and music sync actions
- `ai-src/simulate_pipod.py`: simulator/scenario runner
- `ai-src/test_simulator.py`: regression tests for runtime/simulator behavior
- `ai-src/simulator_adapters.py`: fake EPD/player/library/settings adapters
- `lib/waveshare_epd/`: minimal display driver set for current target panel
- `pic/Font.ttc`: runtime UI font
- `data/sim_tracks.json`: deterministic simulator fixture catalog

## Device Runtime
Run on device:

```bash
python3 ai-src/app.py
```

Runtime menu:
- Music
- Now Playing
- Shuffle All
- Settings

Controls:
- `u` / `d` move selection
- `s` or `right` select
- `b` or `left` go back
- `q` quit

Settings persistence:
- `data/settings.json`
- Keys: `audio_output_mode`, `music_import_dir`, `last_connected_bt_address`

## Simulator And Regression
Run tests:

```bash
python3 ai-src/test_simulator.py
```

Run scripted scenarios:

```bash
python3 ai-src/simulate_pipod.py --mode scenario --scenario all --no-frames
```

## Removed Legacy Files
Legacy vendor demo content and duplicate/unused code were intentionally removed, including:
- Extra Waveshare panel drivers and sample images not used by PiPod runtime
- Tracked bytecode/caches (`*.pyc`, `__pycache__/`)
- Duplicate standalone player demo (`src/player.py`)
- Legacy Waveshare demo script (`ai-src/test.py`)
- Generated library DB artifact (`data/library.db`)

Only the current runtime target driver files are kept in `lib/waveshare_epd`:
- `__init__.py`
- `epdconfig.py`
- `epd2in13_V4.py`
