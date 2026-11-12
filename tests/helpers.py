"""Synthetic frames and configuration builders for the test suite.

The belt is black, corn is yellow-orange and saturated, rice is pale and almost
desaturated. Generating those images with NumPy lets the detector be tested
before a single second of real video exists.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from core.config import (
    ActuatorConfig,
    AppConfig,
    BeltConfig,
    CameraConfig,
    DetectorConfig,
    LoggingConfig,
    RoiConfig,
    SafetyConfig,
    StateConfig,
    WebConfig,
)

# BGR, the order OpenCV uses.
BLACK = (0, 0, 0)
YELLOW = (0, 220, 255)
ORANGE = (0, 140, 255)
RICE = (205, 205, 205)
GREY_SHADOW = (60, 60, 60)
DARK_YELLOW = (0, 30, 30)

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 360


def blank(width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> np.ndarray:
    """A clean black belt."""
    return np.zeros((height, width, 3), dtype=np.uint8)


def with_rect(
    x: int,
    y: int,
    w: int,
    h: int,
    color: tuple[int, int, int] = YELLOW,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> np.ndarray:
    """A black belt with one solid rectangle on it."""
    frame = blank(width, height)
    frame[y:y + h, x:x + w] = color
    return frame


def make_config(
    *,
    detector: DetectorConfig | None = None,
    state: StateConfig | None = None,
    roi: RoiConfig | None = None,
    belt: BeltConfig | None = None,
    actuator: ActuatorConfig | None = None,
    camera: CameraConfig | None = None,
    web: WebConfig | None = None,
    safety: SafetyConfig | None = None,
) -> AppConfig:
    """A valid configuration with test-friendly defaults."""
    return AppConfig(
        camera=camera or CameraConfig(),
        roi=roi or RoiConfig(),
        detector=detector or DetectorConfig(),
        state=state or StateConfig(confirm_frames=2, clear_frames=2),
        actuator=actuator or ActuatorConfig(backend="mock"),
        web=web or WebConfig(),
        logging=LoggingConfig(),
        belt=belt or BeltConfig(),
        safety=safety or SafetyConfig(),
        source_path="<test>",
    )


def with_state(config: AppConfig, **kwargs) -> AppConfig:
    return replace(config, state=replace(config.state, **kwargs))
