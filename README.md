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

Run live preview simulation for development:
```bash
python3 ai-src/simulate_pipod.py --mode interactive --live-preview
```

### Raspberry Pi Zero 2 W + I2S DAC Quick Setup

If your DAC is wired as:
- `BCK` -> `GPIO18` (pin 12)
- `LCK/LRCK` -> `GPIO19` (pin 35)
- `DIN` -> `GPIO21` (pin 40)
- `VIN` -> `3.3V` (pin 1 or 17)
- `GND` -> `GND`
- `SCK` unused

Important GPIO conflicts with the default PiPod wiring:
- I2S `BCK` uses `GPIO18`. The Waveshare display driver also defaults its optional `PWR` control line to `GPIO18`.
- I2S `LCK/LRCK` uses `GPIO19`. The default `MAIN_CENTER` button also uses `GPIO19`.
- I2S `DIN` uses `GPIO21`. The default `VOL_DOWN` button also uses `GPIO21`.

For I2S audio and e-paper at the same time, do one of these before launching PiPod:

```bash
# If the display PWR pin is not connected and the panel is powered directly:
export PIPOD_EPD_PWR_PIN=none

# Or, if you rewired the display PWR line to another BCM pin:
export PIPOD_EPD_PWR_PIN=22
```

If you use the default GPIO buttons with I2S audio, also move the conflicting
buttons to free BCM pins:

```bash
export PIPOD_GPIO_SELECT_PIN=16
export PIPOD_GPIO_VOL_DOWN_PIN=26
```

Run the setup script on the Pi:

```bash
sudo bash scripts/setup_i2s_dac.sh
```

This script:
- Disables onboard analog audio (`dtparam=audio=off`)
- Enables I2S DAC overlay (`dtoverlay=hifiberry-dac`)
- Writes a default ALSA output route to `/etc/asound.conf`

Then reboot and verify sound:

```bash
sudo reboot
# after reboot:
aplay -l
speaker-test -c2 -twav -D default
python3 ai-src/app.py
```

If `aplay -l` does not show a HifiBerry/PCM DAC card, try a different overlay:

```bash
sudo bash scripts/setup_i2s_dac.sh iqaudio-dac
sudo reboot
```

If pygame still fails to open audio, force ALSA explicitly:

```bash
export PIPOD_SDL_AUDIODRIVER=alsa
python3 ai-src/app.py
```

### E-Paper Display Smoke Test

If the e-paper display is blank, run a direct panel test on the Pi:

```bash
python3 scripts/epd_smoke_test.py --leave-on
```

To actively manipulate the panel through multiple full and partial refresh
patterns:

```bash
python3 scripts/epd_smoke_test.py --sequence all --hold-seconds 1 --leave-on
```

If `GPIO18` is being used by the I2S DAC and the display is powered directly, run:

```bash
PIPOD_EPD_PWR_PIN=none python3 scripts/epd_smoke_test.py --leave-on
```

If you rewired the display `PWR` line to another BCM pin, pass that pin:

```bash
PIPOD_EPD_PWR_PIN=22 python3 scripts/epd_smoke_test.py --leave-on
```

Music library folder:
- Default: `music/` at repo root
- On startup, PiPod creates this folder automatically if missing
- Place your `.mp3`, `.flac`, `.m4a`, etc. files there (subfolders are supported)
- Optional override: set `PIPOD_MUSIC_DIR` to use a different path

Playlist manifest:
- Optional file: `music/playlists.json`
- Format:

```json
{
  "playlists": {
    "Road Trip": [
      "Artist/Album/01 Song.mp3",
      "Artist/Album/02 Song.mp3"
    ],
    "Chill": [
      "Artist/Album/03 Song.mp3"
    ]
  }
}
```

- Track paths are relative to `music/`
- Missing entries are ignored
- Playlists appear under `Music > Playlists` along with `All Songs` and `Shuffle All`

Runtime menu:
- Music
- Now Playing
- Shuffle All
- Settings

Controls:
- `u` / `d` move selection
- `s` or `right` select
- `b` or `left` go back
- `t` toggle album art render mode (Now Playing)
- `q` quit

Bluetooth scan and pairing (Pi Zero 2 W):
- Menu path: `Settings > Bluetooth > Scan & Pair Headphones`
- PiPod runs a real BlueZ scan session via `bluetoothctl` (not simulator fixtures)
- Before each scan, PiPod prepares adapter state with: `power on`, `agent on`, `default-agent`, `pairable on`
- Scan results show nearby discoveries from the active scan window plus paired devices not currently nearby
- If scanning times out or fails, PiPod returns a safe fallback list of paired devices
- Requirement: `bluetoothctl` must be installed and the Bluetooth service running on the Pi

Settings persistence:
- `data/settings.json`
- Keys: `audio_output_mode`, `album_art_mode`, `now_playing_progress_ring`, `music_import_dir`, `last_connected_bt_address`

## Simulator And Regression
Run tests:

```bash
python3 ai-src/test_simulator.py
```

Run scripted scenarios:

```bash
python3 ai-src/simulate_pipod.py --mode scenario --scenario all --no-frames
```

Simulator audio source:
- Default uses your real `music/` library (`--audio real`)
- Use deterministic fixtures only when needed: `--audio mock`

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
