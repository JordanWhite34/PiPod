#!/usr/bin/env python3
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
from typing import Callable

try:
    from gpiozero import Button
except Exception:  # pragma: no cover - runtime dependency check
    Button = None

EventProvider = Callable[[float], str | None]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on", "y"}:
        return True
    if value in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


@dataclass(frozen=True)
class GpioFiveWayConfig:
    enabled: bool = True
    up_pin: int = 5
    down_pin: int = 6
    left_pin: int = 12
    right_pin: int = 13
    select_pin: int = 19
    vol_up_pin: int | None = 20
    vol_down_pin: int | None = 21
    debounce_ms: int = 70
    pull_up: bool = True

    @property
    def bounce_time_s(self) -> float:
        return max(0.0, float(self.debounce_ms) / 1000.0)

    @classmethod
    def from_env(cls) -> "GpioFiveWayConfig":
        return cls(
            enabled=_env_bool("PIPOD_GPIO_ENABLED", True),
            up_pin=_env_int("PIPOD_GPIO_UP_PIN", 5),
            down_pin=_env_int("PIPOD_GPIO_DOWN_PIN", 6),
            left_pin=_env_int("PIPOD_GPIO_LEFT_PIN", 12),
            right_pin=_env_int("PIPOD_GPIO_RIGHT_PIN", 13),
            select_pin=_env_int("PIPOD_GPIO_SELECT_PIN", 19),
            vol_up_pin=_env_int("PIPOD_GPIO_VOL_UP_PIN", 20),
            vol_down_pin=_env_int("PIPOD_GPIO_VOL_DOWN_PIN", 21),
            debounce_ms=_env_int("PIPOD_GPIO_DEBOUNCE_MS", 70),
            pull_up=_env_bool("PIPOD_GPIO_PULL_UP", True),
        )


class GpioFiveWayInput:
    """GPIO-backed 5-way event source using press callbacks."""

    def __init__(
        self,
        config: GpioFiveWayConfig,
        button_factory: Callable[..., object] | None = None,
    ):
        self.config = config
        self._queue: deque[str] = deque()
        self._buttons: list[object] = []

        if button_factory is None:
            if Button is None:
                raise RuntimeError("gpiozero.Button is unavailable")
            button_factory = Button

        pin_map = (
            ("UP", config.up_pin),
            ("DOWN", config.down_pin),
            ("LEFT", config.left_pin),
            ("RIGHT", config.right_pin),
            ("SELECT", config.select_pin),
        )
        if config.vol_up_pin is not None:
            pin_map = (*pin_map, ("VOL_UP", config.vol_up_pin))
        if config.vol_down_pin is not None:
            pin_map = (*pin_map, ("VOL_DOWN", config.vol_down_pin))

        for event_name, pin in pin_map:
            button = button_factory(
                pin=int(pin),
                pull_up=bool(config.pull_up),
                bounce_time=config.bounce_time_s,
            )
            setattr(button, "when_pressed", self._queue_event(event_name))
            self._buttons.append(button)

    def _queue_event(self, event_name: str) -> Callable[[], None]:
        def _handler():
            self._queue.append(event_name)

        return _handler

    def poll_nonblocking(self) -> str | None:
        if not self._queue:
            return None
        return self._queue.popleft()

    def close(self):
        for button in self._buttons:
            try:
                setattr(button, "when_pressed", None)
            except Exception:
                pass
            close = getattr(button, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._buttons.clear()
        self._queue.clear()


class CombinedEventProvider:
    """Prefer queued GPIO events, fall back to keyboard provider."""

    def __init__(
        self,
        keyboard_provider: EventProvider,
        gpio_input: GpioFiveWayInput | None = None,
    ):
        self._keyboard_provider = keyboard_provider
        self._gpio_input = gpio_input

    def __call__(self, timeout_s: float) -> str | None:
        timeout_s = max(0.0, float(timeout_s))
        if self._gpio_input is not None:
            event = self._gpio_input.poll_nonblocking()
            if event is not None:
                return event
        return self._keyboard_provider(timeout_s)

    def close(self):
        if self._gpio_input is not None:
            self._gpio_input.close()
            self._gpio_input = None
