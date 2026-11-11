"""The seam between detector, hysteresis and relay."""

from __future__ import annotations

import numpy as np
import pytest

from core.actuator import MockOutput
from core.config import ConfigError, StateConfig
from core.runtime import Runtime
from helpers import YELLOW, blank, make_config, with_rect

CORN = with_rect(300, 180, 80, 80, YELLOW)


def build(**kwargs) -> tuple[Runtime, MockOutput]:
    output = MockOutput()
    return Runtime(make_config(**kwargs), output=output), output


def test_the_line_stays_paused_until_the_first_frame() -> None:
    runtime, output = build()
    assert output.ok is False
    assert runtime.snapshot().frames == 0


def test_the_first_clean_frame_releases_the_line() -> None:
    runtime, output = build()
    runtime.process(blank())
    assert output.ok is True
    assert runtime.snapshot().frames == 1


def test_contamination_pauses_the_line_after_confirmation() -> None:
    runtime, output = build(state=StateConfig(confirm_frames=2, clear_frames=2))
    runtime.process(blank())
    runtime.process(CORN)
    assert output.ok is True  # one frame is not enough
    runtime.process(CORN)
    assert output.ok is False
    assert runtime.snapshot().contaminated is True


def test_clean_frames_resume_the_line() -> None:
    runtime, output = build(state=StateConfig(confirm_frames=1, clear_frames=2))
    runtime.process(CORN)
    assert output.ok is False
    runtime.process(blank())
    assert output.ok is False
    runtime.process(blank())
    assert output.ok is True


def test_none_frame_is_ignored() -> None:
    runtime, output = build()
    runtime.process(blank())
    runtime.process(None)
    assert output.ok is True
    assert runtime.snapshot().frames == 1


def test_detector_failure_pauses_the_line() -> None:
    class BrokenDetector:
        def detect(self, frame):
            raise RuntimeError("boom")

        def reconfigure(self, *args, **kwargs):
            raise AssertionError("not expected")

    output = MockOutput()
    runtime = Runtime(make_config(), detector=BrokenDetector(), output=output)
    runtime.process(blank())

    assert output.ok is False
    snapshot = runtime.snapshot()
    assert snapshot.errors == 1
    assert "boom" in (snapshot.last_error or "")


def test_actuator_failure_never_escapes() -> None:
    class BrokenOutput(MockOutput):
        def set_ok(self, ok):
            if ok:
                raise RuntimeError("relay stuck")
            super().set_ok(ok)

    output = BrokenOutput()
    runtime = Runtime(make_config(), output=output)
    runtime.process(blank())  # must not raise
    assert output.ok is False


def test_shutdown_leaves_the_line_paused() -> None:
    runtime, output = build()
    runtime.process(blank())
    assert output.ok is True
    runtime.shutdown()
    assert output.ok is False
    assert output.closed is True


def test_snapshot_reports_area_and_boxes() -> None:
    runtime, _ = build(state=StateConfig(confirm_frames=1, clear_frames=1))
    runtime.process(CORN)
    snapshot = runtime.snapshot()
    assert snapshot.area_px > 3000
    assert len(snapshot.boxes) == 1
    payload = snapshot.to_dict()
    assert payload["contaminated"] is True
    assert payload["ok"] is False
    assert payload["hysteresis"]["confirm_frames"] == 1


def test_snapshot_counts_frames_and_measures_fps() -> None:
    runtime, _ = build()
    for _ in range(5):
        runtime.process(blank())
    snapshot = runtime.snapshot()
    assert snapshot.frames == 5
    assert snapshot.fps >= 0.0


def test_apply_config_changes_the_detector_threshold() -> None:
    runtime, _ = build()
    runtime.apply_config({"detector": {"min_area_px": 10_000}})
    runtime.process(CORN)
    assert runtime.snapshot().contaminated is False


def test_apply_config_changes_the_hysteresis() -> None:
    public = Runtime(make_config(), output=MockOutput()).apply_config({"state": {"confirm_frames": 7}})
    assert public["state"]["confirm_frames"] == 7


def test_apply_config_rejects_unknown_fields() -> None:
    runtime, _ = build()
    with pytest.raises(ConfigError):
        runtime.apply_config({"detector": {"nope": 1}})


def test_apply_config_keeps_the_previous_config_on_error() -> None:
    runtime, _ = build()
    with pytest.raises(ConfigError):
        runtime.apply_config({"state": {"confirm_frames": 0}})
    assert runtime.public_config()["state"]["confirm_frames"] == 2


def test_frames_are_not_mutated_by_the_runtime() -> None:
    runtime, _ = build()
    frame = blank()
    before = frame.copy()
    runtime.process(frame)
    assert np.array_equal(frame, before)
