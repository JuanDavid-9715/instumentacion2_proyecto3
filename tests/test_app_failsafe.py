"""Process-level fail-safe: every exit path leaves the relay de-energized.

The runtime tests cover the containment inside ``Runtime.process``. These tests
drive the real ``app.run`` loop with a stub capture source, so the signal
handlers, the ``finally`` block and the camera-loss watchdog are exercised too.
"""

from __future__ import annotations

import os
import signal
import threading
import time

import pytest

import app as app_module
import core.runtime
from core.actuator import MockOutput
from core.config import SafetyConfig
from core.state import StateSnapshot
from helpers import blank, make_config


class StubGrabber:
    """Hardware-free capture source: feeds frames from a daemon thread."""

    def __init__(self, frames, opened: bool = True) -> None:
        self.frames = list(frames)
        self.opened = opened
        self.bus = None
        self.last_error = None

    def open(self) -> bool:
        return self.opened

    def start(self) -> None:
        def feed() -> None:
            deadline = time.monotonic() + 5.0
            while self.bus.subscriber_count == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            for frame in self.frames:
                self.bus.publish(frame)
                time.sleep(0.02)

        threading.Thread(target=feed, daemon=True).start()

    def stop(self, timeout: float = 5.0) -> None:
        pass


class ExplodingDetector:
    def detect(self, frame):
        raise RuntimeError("boom")

    def reconfigure(self, *args, **kwargs) -> None:
        pass


class ExplodingMachine:
    """Reports line-OK once, then raises, to simulate a fatal loop error."""

    def __init__(self) -> None:
        self.calls = 0

    def update(self, contaminated: bool) -> bool:
        self.calls += 1
        if self.calls == 1:
            return True
        raise RuntimeError("boom")

    def snapshot(self, contaminated: bool) -> StateSnapshot:
        return StateSnapshot(
            blocked=False,
            contaminated=contaminated,
            dirty_count=0,
            clean_count=0,
            confirm_frames=1,
            clear_frames=1,
        )

    def reset(self) -> None:
        pass

    def reconfigure(self, config) -> None:
        pass


def _install(monkeypatch, frames, *, opened=True, detector=None, machine=None):
    output = MockOutput()
    grabber = StubGrabber(frames, opened=opened)
    holder: dict = {}
    real_build = core.runtime.build_runtime

    def make_grabber(config, bus, **kwargs):
        grabber.bus = bus
        return grabber

    def build(config, **kwargs):
        runtime = real_build(config, **kwargs)
        holder["runtime"] = runtime
        return runtime

    monkeypatch.setattr(app_module, "FrameGrabber", make_grabber)
    monkeypatch.setattr(app_module, "build_runtime", build)
    monkeypatch.setattr(core.runtime, "make_output", lambda config: output)
    if detector is not None:
        monkeypatch.setattr(core.runtime, "CornDetector", lambda *args, **kwargs: detector)
    if machine is not None:
        monkeypatch.setattr(core.runtime, "LineStateMachine", lambda *args, **kwargs: machine)
    return output, holder


def _send_signal_when(predicate, timeout: float = 5.0) -> threading.Thread:
    def run() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(0.005)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_sigterm_de_energizes_and_exits_cleanly(monkeypatch) -> None:
    output, holder = _install(monkeypatch, [blank()])
    killer = _send_signal_when(lambda: output.ok)
    code = app_module.run(make_config(), with_web=False)
    killer.join(timeout=1.0)

    assert code == 0
    assert holder["runtime"].snapshot().frames == 1
    assert output.transitions() == [False, True, False]
    assert output.ok is False
    assert output.closed is True


def test_fatal_loop_error_de_energizes_an_energized_relay(monkeypatch) -> None:
    output, _ = _install(monkeypatch, [blank(), blank()], machine=ExplodingMachine())
    code = app_module.run(make_config(), with_web=False)

    assert code == 1
    assert output.transitions() == [False, True, False]
    assert output.ok is False
    assert output.closed is True


def test_detector_exception_pauses_the_line_at_process_level(monkeypatch) -> None:
    output, holder = _install(monkeypatch, [blank()], detector=ExplodingDetector())
    killer = _send_signal_when(
        lambda: holder.get("runtime") is not None and holder["runtime"].snapshot().errors > 0
    )
    code = app_module.run(make_config(), with_web=False)
    killer.join(timeout=1.0)

    assert code == 0
    assert output.transitions() == [False]
    assert holder["runtime"].machine.blocked is True
    assert holder["runtime"].snapshot().errors >= 1


def test_capture_open_failure_exits_2_and_releases_the_relay(monkeypatch) -> None:
    output, _ = _install(monkeypatch, [], opened=False)
    code = app_module.run(make_config(), with_web=False)

    assert code == 2
    assert output.ok is False
    assert output.closed is True


def test_camera_loss_pauses_the_line(monkeypatch) -> None:
    """One frame, then silence: the watchdog must pause instead of running blind."""
    output, holder = _install(monkeypatch, [blank()])
    config = make_config(safety=SafetyConfig(frame_timeout_s=0.3))
    killer = _send_signal_when(
        lambda: holder.get("runtime") is not None and holder["runtime"].snapshot().errors > 0
    )
    code = app_module.run(config, with_web=False)
    killer.join(timeout=1.0)

    assert code == 0
    assert output.transitions() == [False, True, False]
    assert output.ok is False
    assert "no frames" in (holder["runtime"].snapshot().last_error or "")


def test_sigterm_while_a_frame_is_being_processed_does_not_re_energize(monkeypatch) -> None:
    """The handler de-energizes; the in-flight frame must not leave it energised."""
    output, _ = _install(monkeypatch, [blank() for _ in range(200)])
    killer = _send_signal_when(lambda: output.ok)
    code = app_module.run(make_config(), with_web=False)
    killer.join(timeout=1.0)

    assert code == 0
    assert output.ok is False
    assert output.closed is True
    # A momentary command may race the signal, but nothing may be commanded
    # after the final de-energize.
    assert output.transitions()[-1] is False


def test_missing_config_file_exits_with_a_message(tmp_path, capsys) -> None:
    code = app_module.main(["-c", str(tmp_path / "absent.yaml")])
    assert code == 2
    captured = capsys.readouterr()
    assert "error de configuracion" in captured.err


@pytest.mark.parametrize(
    "args",
    [["--config", "x.yaml"], ["-c", "x.yaml"]],
)
def test_config_flag_is_accepted_in_both_forms(args) -> None:
    parsed = app_module._parse_args(args)  # noqa: SLF001 - the CLI contract
    assert parsed.config == "x.yaml"
