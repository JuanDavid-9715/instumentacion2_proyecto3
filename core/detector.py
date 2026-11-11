"""Corn detection on the black input conveyor belt.

The belt is black, rice is pale and desaturated, corn is yellow-orange. The
separator is therefore colour saturation inside a hue window, not brightness:
pale rice and grey shadows fail the saturation test, a dark belt fails the
value test.

No camera, no GPIO and no web framework are imported here, so the detector can
be exercised with recorded video or synthetic frames on the development PC.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from core.config import DetectorConfig, RoiConfig


@dataclass(frozen=True)
class Detection:
    """Result of one frame analysis, in full-frame coordinates."""

    contaminated: bool
    area_px: int
    boxes: tuple[tuple[int, int, int, int], ...]
    elapsed_ms: float = 0.0

    @staticmethod
    def empty() -> "Detection":
        return Detection(contaminated=False, area_px=0, boxes=(), elapsed_ms=0.0)


class CornDetector:
    """HSV threshold + morphology + contour area."""

    def __init__(self, config: DetectorConfig, roi: RoiConfig | None = None) -> None:
        self._roi = roi or RoiConfig()
        # Thresholds and kernel are swapped as one pair so a frame analysed on
        # another thread never sees a mismatched combination.
        self._settings: tuple[DetectorConfig, np.ndarray] = (
            config,
            self._make_kernel(config.morph_kernel),
        )

    # -- configuration ---------------------------------------------------- #

    def reconfigure(self, config: DetectorConfig, roi: RoiConfig | None = None) -> None:
        """Apply new thresholds without restarting the process."""
        self._settings = (config, self._make_kernel(config.morph_kernel))
        if roi is not None:
            self._roi = roi

    @property
    def config(self) -> DetectorConfig:
        return self._settings[0]

    @property
    def config(self) -> DetectorConfig:
        return self._settings[0]

    @staticmethod
    def _make_kernel(size: int) -> np.ndarray:
        size = max(1, int(size))
        if size % 2 == 0:
            size += 1
        return cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))

    # -- detection -------------------------------------------------------- #

    def detect(self, frame_bgr: np.ndarray) -> Detection:
        """Analyse one BGR frame. The input is never modified."""
        started = time.perf_counter()
        if frame_bgr is None or frame_bgr.size == 0:
            return Detection.empty()

        height, width = frame_bgr.shape[:2]
        offset_x, offset_y, view = 0, 0, frame_bgr
        if self._roi.enabled:
            offset_x, offset_y, roi_w, roi_h = self._roi.as_pixels(width, height)
            view = frame_bgr[offset_y:offset_y + roi_h, offset_x:offset_x + roi_w]

        cfg, kernel = self._settings
        hsv = cv2.cvtColor(view, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array([cfg.hue_min, cfg.sat_min, cfg.val_min], dtype=np.uint8),
            np.array([cfg.hue_max, 255, 255], dtype=np.uint8),
        )

        if cfg.open_iterations > 0:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=cfg.open_iterations)
        if cfg.close_iterations > 0:
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=cfg.close_iterations)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes: list[tuple[int, int, int, int]] = []
        total_area = 0.0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < cfg.min_area_px:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            boxes.append((x + offset_x, y + offset_y, w, h))
            total_area += area

        elapsed = (time.perf_counter() - started) * 1000.0
        return Detection(
            contaminated=bool(boxes),
            area_px=int(round(total_area)),
            boxes=tuple(boxes),
            elapsed_ms=round(elapsed, 3),
        )
