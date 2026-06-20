# PiPod GPIO Button Reference

This document describes how the 5-way navigation button is wired and configured in PiPod.

## Scope

This GPIO input path handles only navigation:

- `UP`
- `DOWN`
- `LEFT`
- `RIGHT`
- `SELECT` (center click)
- `SELECT_HOLD` (center hold)
- Optional `VOL_UP`
- Optional `VOL_DOWN`

The center/select hold opens PiPod's soft-power dialog. It is app-level power
handling, not a dedicated physical power switch.

## Navigation Behavior

App-wide behavior is:

- `LEFT` => Back (`BACK`)
- `RIGHT` => Select/activate (`SELECT`)
- `SELECT` => Select/activate
- `SELECT_HOLD` => Open the Sleep / Power Off dialog
- `UP` / `DOWN` => Move focus/selection

Examples:

- In menu/music lists: `RIGHT` enters/selects, `LEFT` goes back.
- In Now Playing: `RIGHT` activates the focused control, `LEFT` returns back.

## Default GPIO Pin Mapping (BCM)

PiPod uses BCM numbering by default:

- `MAIN_UP` / `UP` = GPIO `5` (physical pin `29`)
- `MAIN_DOWN` / `DOWN` = GPIO `6` (physical pin `31`)
- `MAIN_LEFT` / `LEFT` = GPIO `12` (physical pin `32`)
- `MAIN_RIGHT` / `RIGHT` = GPIO `13` (physical pin `33`)
- `MAIN_CENTER` / `SELECT` = GPIO `16` (physical pin `36`)
- `VOL_UP` = GPIO `20` (physical pin `38`)
- `VOL_DOWN` = GPIO `21` (physical pin `40`)

Equivalent Python mapping:

```python
BUTTON_PINS = {
    "MAIN_UP": 5,
    "MAIN_DOWN": 6,
    "MAIN_LEFT": 12,
    "MAIN_RIGHT": 13,
    "MAIN_CENTER": 16,
    "VOL_UP": 20,
    "VOL_DOWN": 21,
}
```

If I2S audio is enabled with the DAC wiring in the README, note that I2S uses
GPIO `21` for `DIN`. That conflicts with `VOL_DOWN` in this button map.

## Wiring Assumptions

The implementation uses internal pull-ups (`pull_up=True` by default), so buttons are expected to be active-low:

- One side of each button switch -> GPIO pin
- Other side of each button switch -> GND

When a button is pressed, the GPIO is pulled to ground and an event is generated.

## Environment Variables

All settings are optional. If unset, defaults are used.

- `PIPOD_GPIO_ENABLED` (default `1`)
- `PIPOD_GPIO_UP_PIN` (default `5`)
- `PIPOD_GPIO_DOWN_PIN` (default `6`)
- `PIPOD_GPIO_LEFT_PIN` (default `12`)
- `PIPOD_GPIO_RIGHT_PIN` (default `13`)
- `PIPOD_GPIO_SELECT_PIN` (default `16`)
- `PIPOD_GPIO_VOL_UP_PIN` (default `20`)
- `PIPOD_GPIO_VOL_DOWN_PIN` (default `21`)
- `PIPOD_GPIO_DEBOUNCE_MS` (default `70`)
- `PIPOD_GPIO_SELECT_HOLD_MS` (default `1200`)
- `PIPOD_GPIO_PULL_UP` (default `1`)

Boolean values accepted: `1/0`, `true/false`, `yes/no`, `on/off` (case-insensitive).

## Example Configuration

```bash
export PIPOD_GPIO_ENABLED=1
export PIPOD_GPIO_UP_PIN=5
export PIPOD_GPIO_DOWN_PIN=6
export PIPOD_GPIO_LEFT_PIN=12
export PIPOD_GPIO_RIGHT_PIN=13
export PIPOD_GPIO_SELECT_PIN=16
export PIPOD_GPIO_VOL_UP_PIN=20
export PIPOD_GPIO_VOL_DOWN_PIN=21
export PIPOD_GPIO_DEBOUNCE_MS=70
export PIPOD_GPIO_SELECT_HOLD_MS=1200
export PIPOD_GPIO_PULL_UP=1
```

## Volume Buttons

Volume buttons use the same active-low GPIO handling as the main controls.
They emit `VOL_UP` and `VOL_DOWN` runtime events.

## Runtime Behavior and Fallback

- On startup, the app attempts to initialize the GPIO 5-way input.
- If GPIO is unavailable (missing `gpiozero`, pin factory issues, permission issues, etc.), the app logs a warning and falls back to keyboard input.
- You can force keyboard-only mode by setting:

```bash
export PIPOD_GPIO_ENABLED=0
```

## Soft Power

Holding the center/select button emits `SELECT_HOLD` and opens a power dialog:

- `Sleep` blanks and sleeps the e-paper display while music keeps playing.
- `Power Off` is a soft-off state: playback stops, the display blanks/sleeps,
  and the app ignores normal controls while it keeps running.
- A center click wakes from sleep.
- A center hold wakes from soft-off.

This does not cut battery power to the Raspberry Pi. It is intentionally a
software state so the same center/select button can wake the app again.

## Implementation Files

- GPIO input provider: `ai-src/input_provider.py`
- Event parsing and nav aliasing: `ai-src/pipod_runtime.py`
- Startup wiring/fallback: `ai-src/app.py`
