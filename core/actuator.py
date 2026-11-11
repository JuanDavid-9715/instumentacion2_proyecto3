"""Relay output and the fail-safe contract.

Polarity, decided during design and not negotiable:

* relay energized  -> NC contact open -> PLC input X at 0 V -> line runs
* relay de-energized -> NC contact closed -> PLC input X at 24 V -> line pauses

Losing power, crashing the process, raising an exception or unplugging the
Raspberry all end in the same physical state: de-energized, line paused.

``safe()`` must therefore never raise. If it did, the ``finally`` block that
calls it would abort before de-energizing.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from core.config import ActuatorConfig

log = logging.getLogger(__name__)


@runtime_checkable
class Output(Protocol):
    """Anything that can hold the PLC input in the run or pause state."""

    def set_ok(self, ok: bool) -> None:
        """Energize (True) or de-energize (False) the relay."""

    def safe(self) -> None:
        """Force the pause state. Idempotent and must never raise."""

    def close(self) -> None:
        """Release the hardware. Must leave the relay de-energized."""

    @property
    def ok(self) -> bool:
        """Last commanded state."""


class MockOutput:
    """Development and test output. Records every transition."""

    def __init__(self) -> None:
        self._ok = False
        self.history: list[bool] = [False]
        self.closed = False

    def set_ok(self, ok: bool) -> None:
        value = bool(ok)
        if value != self._ok:
            self._ok = value
            self.history.append(value)

    def safe(self) -> None:
        self.set_ok(False)

    def close(self) -> None:
        self.safe()
        self.closed = True

    @property
    def ok(self) -> bool:
        return self._ok

    # -- test helpers ----------------------------------------------------- #

    def transitions(self) -> list[bool]:
        """Every commanded transition, oldest first."""
        return list(self.history)

    def reset_history(self) -> None:
        self.history = [self._ok]


class RelayOutput:
    """Drives any device exposing ``on()``, ``off()`` and ``close()``.

    ``gpiozero.OutputDevice`` is the production device. Keeping the polarity
    decision out of this class makes it testable with a plain recorder object:
    ``active_low`` is translated into ``active_high`` when the device is built,
    in :func:`make_output`.
    """

    def __init__(self, device) -> None:
        self._device = device
        self._ok = False
        self._device.off()
        self._closed = False

    def set_ok(self, ok: bool) -> None:
        if self._closed:
            return
        value = bool(ok)
        if value:
            self._device.on()
        else:
            self._device.off()
        self._ok = value

    def safe(self) -> None:
        try:
            self.set_ok(False)
        except Exception:
            log.exception("could not de-energize the relay; the line may keep running")

    def close(self) -> None:
        self.safe()
        if self._closed:
            return
        self._closed = True
        try:
            self._device.close()
        except Exception:
            log.exception("error while releasing the relay device")

    @property
    def ok(self) -> bool:
        return self._ok


def make_output(config: ActuatorConfig) -> Output:
    """Build the output selected by the configuration."""
    if config.backend == "mock":
        return MockOutput()
    if config.backend == "gpio":
        from gpiozero import OutputDevice  # imported lazily: absent on the PC

        # active_low=True means a logic 0 energizes the relay module.
        device = OutputDevice(
            config.gpio_pin,
            active_high=not config.active_low,
            initial_value=False,
        )
        return RelayOutput(device)
    raise ValueError(f"unknown actuator backend: {config.backend}")
