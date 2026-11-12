"""Detector behaviour on synthetic frames: black belt, yellow corn, pale rice."""

from __future__ import annotations

import numpy as np
import pytest

from core.config import BeltConfig, DetectorConfig, RoiConfig
from core.detector import CornDetector
from helpers import BLACK, DARK_YELLOW, GREY_SHADOW, ORANGE, RICE, YELLOW, blank, with_rect


@pytest.fixture
def detector() -> CornDetector:
    return CornDetector(DetectorConfig())


def test_empty_belt_is_clean(detector: CornDetector) -> None:
    result = detector.detect(blank())
    assert result.contaminated is False
    assert result.area_px == 0
    assert result.boxes == ()


def test_yellow_grain_is_contaminated(detector: CornDetector) -> None:
    result = detector.detect(with_rect(300, 180, 60, 60, YELLOW))
    assert result.contaminated is True
    assert 2500 <= result.area_px <= 3600
    assert len(result.boxes) == 1


def test_orange_grain_is_contaminated(detector: CornDetector) -> None:
    result = detector.detect(with_rect(300, 180, 60, 60, ORANGE))
    assert result.contaminated is True


def test_box_is_reported_in_full_frame_coordinates(detector: CornDetector) -> None:
    x, y, w, h = detector.detect(with_rect(300, 180, 60, 60, YELLOW)).boxes[0]
    assert abs(x - 300) <= 6
    assert abs(y - 180) <= 6
    assert abs(w - 60) <= 6
    assert abs(h - 60) <= 6


def test_pale_rice_is_clean(detector: CornDetector) -> None:
    """Rice is bright but desaturated: the saturation gate rejects it."""
    assert detector.detect(with_rect(300, 180, 60, 60, RICE)).contaminated is False


def test_grey_shadow_is_clean(detector: CornDetector) -> None:
    assert detector.detect(with_rect(300, 180, 60, 60, GREY_SHADOW)).contaminated is False


def test_dark_yellow_is_clean(detector: CornDetector) -> None:
    """Below val_min the pixel is unlit, not corn."""
    assert detector.detect(with_rect(300, 180, 60, 60, DARK_YELLOW)).contaminated is False


def test_speck_smaller_than_the_minimum_area_is_ignored(detector: CornDetector) -> None:
    result = detector.detect(with_rect(300, 180, 15, 15, YELLOW))
    assert result.contaminated is False
    assert result.area_px == 0


def test_two_separate_grains_are_counted(detector: CornDetector) -> None:
    frame = with_rect(100, 100, 40, 40, YELLOW)
    frame = frame.copy()
    frame[220:260, 420:460] = YELLOW
    result = detector.detect(frame)
    assert result.contaminated is True
    assert len(result.boxes) == 2
    assert result.area_px >= 2800


def test_roi_ignores_contamination_outside_it() -> None:
    roi = RoiConfig(enabled=True, x=0.25, y=0.25, w=0.5, h=0.5)
    detector = CornDetector(DetectorConfig(), roi)
    assert detector.detect(with_rect(10, 10, 60, 60, YELLOW)).contaminated is False


def test_roi_detects_inside_and_reports_absolute_coordinates() -> None:
    roi = RoiConfig(enabled=True, x=0.25, y=0.25, w=0.5, h=0.5)
    detector = CornDetector(DetectorConfig(), roi)
    result = detector.detect(with_rect(290, 150, 60, 60, YELLOW))
    assert result.contaminated is True
    x, y, _, _ = result.boxes[0]
    assert 280 <= x <= 300
    assert 140 <= y <= 160


def test_detector_does_not_modify_the_frame(detector: CornDetector) -> None:
    frame = with_rect(300, 180, 60, 60, YELLOW)
    before = frame.copy()
    detector.detect(frame)
    assert np.array_equal(frame, before)


def test_empty_array_is_clean(detector: CornDetector) -> None:
    assert detector.detect(np.zeros((0, 0, 3), dtype=np.uint8)).contaminated is False


def test_reconfigure_applies_the_new_area_threshold(detector: CornDetector) -> None:
    frame = with_rect(300, 180, 60, 60, YELLOW)
    assert detector.detect(frame).contaminated is True
    detector.reconfigure(DetectorConfig(min_area_px=10_000))
    assert detector.detect(frame).contaminated is False


def test_reconfigure_swaps_the_roi(detector: CornDetector) -> None:
    frame = with_rect(10, 10, 60, 60, YELLOW)
    assert detector.detect(frame).contaminated is True
    detector.reconfigure(DetectorConfig(), RoiConfig(enabled=True, x=0.5, y=0.5, w=0.5, h=0.5))
    assert detector.detect(frame).contaminated is False


def test_black_color_is_not_detected(detector: CornDetector) -> None:
    assert detector.detect(with_rect(300, 180, 60, 60, BLACK)).contaminated is False


def test_belt_polygon_ignores_corn_outside_it() -> None:
    """A yellow object beside the belt must not pause the line."""
    belt = BeltConfig(
        enabled=True,
        points=((0.25, 0.0), (0.75, 0.0), (0.75, 1.0), (0.25, 1.0)),
    )
    detector = CornDetector(DetectorConfig(), belt=belt)
    assert detector.detect(with_rect(10, 100, 40, 40, YELLOW)).contaminated is False
    assert detector.detect(with_rect(300, 100, 40, 40, YELLOW)).contaminated is True


def test_belt_polygon_clips_a_blob_that_crosses_its_edge() -> None:
    belt = BeltConfig(
        enabled=True,
        points=((0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)),
    )
    detector = CornDetector(DetectorConfig(), belt=belt)
    result = detector.detect(with_rect(260, 100, 120, 60, YELLOW))
    assert result.contaminated is True
    x, _, w, _ = result.boxes[0]
    assert 318 <= x <= 322
    assert w <= 66


def test_belt_edge_does_not_bridge_two_blobs_across_it() -> None:
    """Closing must not pull an outside blob into the belt through a gap."""
    belt = BeltConfig(
        enabled=True,
        points=((0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)),
    )
    detector = CornDetector(DetectorConfig(min_area_px=10), belt=belt)
    frame = blank()
    frame[100:160, 295:318] = YELLOW  # outside the belt, which starts at x = 320
    frame[100:160, 321:344] = YELLOW  # inside
    result = detector.detect(frame)
    assert result.contaminated is True
    assert len(result.boxes) == 1
    # Only the inside blob counts. contourArea returns (w-1)*(h-1), so 22*59.
    # Bridging the gap across the boundary would add columns and push this
    # over 1350.
    assert 1280 <= result.area_px <= 1330


def test_belt_polygon_and_roi_work_together() -> None:
    belt = BeltConfig(
        enabled=True,
        points=((0.25, 0.0), (0.75, 0.0), (0.75, 1.0), (0.25, 1.0)),
    )
    roi = RoiConfig(enabled=True, x=0.0, y=0.0, w=0.5, h=1.0)
    detector = CornDetector(DetectorConfig(), roi, belt)
    result = detector.detect(with_rect(150, 100, 60, 60, YELLOW))
    assert result.contaminated is True
    x, _, w, _ = result.boxes[0]
    assert 158 <= x <= 162  # clipped by the polygon edge at x = 160
    assert w <= 56


def test_reconfigure_swaps_the_belt_polygon() -> None:
    detector = CornDetector(DetectorConfig())
    frame = with_rect(10, 100, 40, 40, YELLOW)
    assert detector.detect(frame).contaminated is True
    detector.reconfigure(
        DetectorConfig(),
        belt=BeltConfig(
            enabled=True,
            points=((0.25, 0.0), (0.75, 0.0), (0.75, 1.0), (0.25, 1.0)),
        ),
    )
    assert detector.detect(frame).contaminated is False
