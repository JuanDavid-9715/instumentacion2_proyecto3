"""Hysteresis: one noisy frame must not pause the line, one clean frame must not resume it."""

from __future__ import annotations

from core.config import StateConfig
from core.state import LineStateMachine


def make(confirm: int = 3, clear: int = 4) -> LineStateMachine:
    return LineStateMachine(StateConfig(confirm_frames=confirm, clear_frames=clear))


def feed(machine: LineStateMachine, contaminated: bool, times: int) -> list[bool]:
    return [machine.update(contaminated) for _ in range(times)]


def test_starts_running() -> None:
    assert make().blocked is False


def test_requires_confirm_frames_to_pause() -> None:
    machine = make(confirm=3)
    assert feed(machine, True, 2) == [True, True]
    assert machine.blocked is False
    assert machine.update(True) is False
    assert machine.blocked is True


def test_a_clean_frame_resets_the_confirmation_counter() -> None:
    machine = make(confirm=3)
    feed(machine, True, 2)
    machine.update(False)
    assert feed(machine, True, 2) == [True, True]
    assert machine.blocked is False
    assert machine.update(True) is False


def test_requires_clear_frames_to_resume() -> None:
    machine = make(confirm=2, clear=4)
    feed(machine, True, 2)
    assert machine.blocked is True
    assert feed(machine, False, 3) == [False, False, False]
    assert machine.update(False) is True
    assert machine.blocked is False


def test_contamination_resets_the_clear_counter() -> None:
    machine = make(confirm=2, clear=4)
    feed(machine, True, 2)
    feed(machine, False, 3)
    machine.update(True)
    assert feed(machine, False, 3) == [False, False, False]
    assert machine.blocked is True
    assert machine.update(False) is True


def test_confirm_of_one_pauses_immediately() -> None:
    machine = make(confirm=1, clear=1)
    assert machine.update(True) is False


def test_reset_goes_to_the_pause_state() -> None:
    machine = make(confirm=3, clear=2)
    machine.update(False)
    machine.reset()
    assert machine.blocked is True
    assert machine.update(False) is False
    assert machine.update(False) is True


def test_snapshot_reports_the_counters() -> None:
    machine = make(confirm=5, clear=7)
    machine.update(True)
    snapshot = machine.snapshot(contaminated=True)
    assert snapshot.dirty_count == 1
    assert snapshot.clean_count == 0
    assert snapshot.confirm_frames == 5
    assert snapshot.clear_frames == 7
    assert snapshot.blocked is False


def test_reconfigure_restarts_the_tally() -> None:
    machine = make(confirm=5, clear=5)
    feed(machine, True, 4)
    machine.reconfigure(StateConfig(confirm_frames=2, clear_frames=2))
    assert machine.snapshot(False).dirty_count == 0
    assert machine.update(True) is True
    assert machine.update(True) is False
