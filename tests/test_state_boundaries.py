"""S6 boundary cases: the counters reset exactly at each transition."""

from __future__ import annotations

from core.config import StateConfig
from core.state import LineStateMachine


def test_dirty_counter_is_zeroed_when_the_line_pauses() -> None:
    machine = LineStateMachine(StateConfig(confirm_frames=3, clear_frames=3))
    for _ in range(3):
        machine.update(True)
    assert machine.blocked is True
    assert machine.snapshot(True).dirty_count == 0


def test_clean_counter_is_zeroed_when_the_line_resumes() -> None:
    machine = LineStateMachine(StateConfig(confirm_frames=1, clear_frames=3))
    machine.update(True)
    assert [machine.update(False) for _ in range(3)] == [False, False, True]
    assert machine.blocked is False
    assert machine.snapshot(False).clean_count == 0


def test_one_dirty_frame_restarts_the_clear_count() -> None:
    machine = LineStateMachine(StateConfig(confirm_frames=1, clear_frames=3))
    machine.update(True)
    machine.update(False)
    machine.update(False)
    machine.update(True)
    assert [machine.update(False) for _ in range(2)] == [False, False]
    assert machine.blocked is True
    assert machine.update(False) is True


def test_max_confirm_frames_pauses_exactly_on_the_last_frame() -> None:
    machine = LineStateMachine(StateConfig(confirm_frames=600, clear_frames=600))
    for _ in range(599):
        machine.update(True)
    assert machine.blocked is False
    assert machine.update(True) is False
