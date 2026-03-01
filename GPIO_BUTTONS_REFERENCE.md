# PiPod GPIO Button Reference

This document describes how the 5-way navigation button is wired and configured in PiPod.

## Scope

This GPIO input path handles only navigation:

- `UP`
- `DOWN`
- `LEFT`
- `RIGHT`
- `SELECT` (center click)

`VOLUME` and `POWER` are separate hardware buttons and are not part of this 5-way GPIO input module.

## Navigation Behavior

App-wide behavior is:

- `LEFT` => Back (`BACK`)
- `RIGHT` => Select/activate (`SELECT`)
- `SELECT` => Select/activate
- `UP` / `DOWN` => Move focus/selection

Examples:

- In menu/music lists: `RIGHT` enters/selects, `LEFT` goes back.
- In Now Playing: `RIGHT` activates the focused control, `LEFT` returns back.

## Default GPIO Pin Mapping (BCM)

PiPod uses BCM numbering by default:

- `UP` = GPIO `6`
- `DOWN` = GPIO `19`
- `LEFT` = GPIO `5`
- `RIGHT` = GPIO `26`
- `SELECT` = GPIO `13`

## Wiring Assumptions

The implementation uses internal pull-ups (`pull_up=True` by default), so buttons are expected to be active-low:

- One side of each button switch -> GPIO pin
- Other side of each button switch -> GND

When a button is pressed, the GPIO is pulled to ground and an event is generated.

## Environment Variables

All settings are optional. If unset, defaults are used.

- `PIPOD_GPIO_ENABLED` (default `1`)
- `PIPOD_GPIO_UP_PIN` (default `6`)
- `PIPOD_GPIO_DOWN_PIN` (default `19`)
- `PIPOD_GPIO_LEFT_PIN` (default `5`)
- `PIPOD_GPIO_RIGHT_PIN` (default `26`)
- `PIPOD_GPIO_SELECT_PIN` (default `13`)
- `PIPOD_GPIO_DEBOUNCE_MS` (default `70`)
- `PIPOD_GPIO_PULL_UP` (default `1`)

Boolean values accepted: `1/0`, `true/false`, `yes/no`, `on/off` (case-insensitive).

## Example Configuration

```bash
export PIPOD_GPIO_ENABLED=1
export PIPOD_GPIO_UP_PIN=6
export PIPOD_GPIO_DOWN_PIN=19
export PIPOD_GPIO_LEFT_PIN=5
export PIPOD_GPIO_RIGHT_PIN=26
export PIPOD_GPIO_SELECT_PIN=13
export PIPOD_GPIO_DEBOUNCE_MS=70
export PIPOD_GPIO_PULL_UP=1
```

## Runtime Behavior and Fallback

- On startup, the app attempts to initialize the GPIO 5-way input.
- If GPIO is unavailable (missing `gpiozero`, pin factory issues, permission issues, etc.), the app logs a warning and falls back to keyboard input.
- You can force keyboard-only mode by setting:

```bash
export PIPOD_GPIO_ENABLED=0
```

## Volume and Power Buttons

Volume and power are intentionally separate from this 5-way navigation path.

- They should use their own dedicated hardware handling.
- They are not configured by the `PIPOD_GPIO_*` variables above.
- They do not emit navigation events in this module.

## Implementation Files

- GPIO input provider: `ai-src/input_provider.py`
- Event parsing and nav aliasing: `ai-src/pipod_runtime.py`
- Startup wiring/fallback: `ai-src/app.py`
