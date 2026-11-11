"""Relay polarity and the fail-safe contract."""

from __future__ import annotations

import sys
import types

import pytest

from core.actuator import MockOutput, RelayOutput, make_output
from core.config import ActuatorConfig


class RecorderDevice:
    """Stands in for gpiozero.OutputDevice."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    def on(self) -> None:
        self.calls.append("on")

    def off(self) -> None:
        self.calls.append("off")

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


class FailingDevice(RecorderDevice):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def off(self) -> None:
        if self.fail:
            raise RuntimeError("relay driver failure")
        super().off()


# -- MockOutput -------------------------------------------------------------- #


def test_mock_starts_de_energized() -> None:
    output = MockOutput()
    assert output.ok is False
    assert output.transitions() == [False]


def test_mock_records_only_real_transitions() -> None:
    output = MockOutput()
    output.set_ok(True)
    output.set_ok(True)
    output.set_ok(False)
    output.set_ok(False)
    assert output.transitions() == [False, True, False]


def test_mock_close_forces_the_safe_state() -> None:
    output = MockOutput()
    output.set_ok(True)
    output.close()
    assert output.ok is False
    assert output.closed is True


# -- RelayOutput ------------------------------------------------------------- #


def test_relay_starts_de_energized() -> None:
    device = RecorderDevice()
    RelayOutput(device)
    assert device.calls == ["off"]


def test_relay_energizes_and_de_energizes() -> None:
    device = RecorderDevice()
    output = RelayOutput(device)
    output.set_ok(True)
    assert device.calls[-1] == "on"
    assert output.ok is True
    output.set_ok(False)
    assert device.calls[-1] == "off"
    assert output.ok is False


def test_relay_close_de_energizes_before_releasing() -> None:
    device = RecorderDevice()
    output = RelayOutput(device)
    output.set_ok(True)
    output.close()
    assert device.calls[-2:] == ["off", "close"]
    assert device.closed is True


def test_relay_close_is_idempotent() -> None:
    device = RecorderDevice()
    output = RelayOutput(device)
    output.close()
    output.close()
    assert device.calls.count("close") == 1


def test_safe_never_raises_even_when_the_device_fails() -> None:
    device = FailingDevice()
    output = RelayOutput(device)
    device.fail = True
    output.safe()  # must not propagate
    assert True


def test_close_never_raises_even_when_the_device_fails() -> None:
    device = FailingDevice()
    output = RelayOutput(device)
    device.fail = True
    output.close()
    assert True


def test_set_ok_after_close_is_ignored() -> None:
    device = RecorderDevice()
    output = RelayOutput(device)
    output.close()
    before = list(device.calls)
    output.set_ok(True)
    assert device.calls == before


# -- make_output ------------------------------------------------------------- #


def test_mock_backend_returns_mock_output() -> None:
    assert isinstance(make_output(ActuatorConfig(backend="mock")), MockOutput)


def test_unknown_backend_raises() -> None:
    with pytest.raises(ValueError):
        make_output(ActuatorConfig(backend="pwm"))


def _install_fake_gpiozero(monkeypatch: pytest.MonkeyPatch) -> dict:
    created: dict = {}

    class FakeOutputDevice(RecorderDevice):
        def __init__(self, pin, active_high=True, initial_value=False):
            super().__init__()
            created.update(pin=pin, active_high=active_high, initial_value=initial_value)
            self._active = False

        def on(self) -> None:
            self._active = True
            super().on()

        def off(self) -> None:
            self._active = False
            super().off()

    module = types.ModuleType("gpiozero")
    module.OutputDevice = FakeOutputDevice  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gpiozero", module)
    return created


def test_active_low_relay_is_built_with_active_high_false(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_fake_gpiozero(monkeypatch)
    make_output(ActuatorConfig(backend="gpio", gpio_pin=27, active_low=True))
    assert created == {"pin": 27, "active_high": False, "initial_value": False}


def test_active_high_relay_is_built_with_active_high_true(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_fake_gpiozero(monkeypatch)
    make_output(ActuatorConfig(backend="gpio", gpio_pin=17, active_low=False))
    assert created == {"pin": 17, "active_high": True, "initial_value": False}


def test_gpio_backend_starts_de_energized(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_gpiozero(monkeypatch)
    output = make_output(ActuatorConfig(backend="gpio", gpio_pin=27, active_low=True))
    assert output.ok is False
