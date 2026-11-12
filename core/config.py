"""Loading and validation of ``config.yaml``.

Every numeric threshold used by the detector, the state machine and the
actuator lives here. Nothing may be hardcoded in the other modules.

Validation happens once, at startup, so a malformed file fails loudly instead
of silently producing a detector that never triggers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when the configuration is missing, malformed or out of range."""


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CameraConfig:
    source: str = "device"  # "device" | "file"
    device: int = 0
    path: str = ""
    width: int = 1280
    height: int = 720
    fps: int = 30
    fourcc: str = "MJPG"
    buffersize: int = 1


@dataclass(frozen=True)
class RoiConfig:
    """Region of interest as fractions of the frame.

    Fractions keep the same configuration valid on any resolution: the PC
    captures 1280x720 and the Pi may capture something else.
    """

    enabled: bool = False
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0

    def as_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Return ``(x, y, w, h)`` in pixels, clamped to the frame."""
        x0 = max(0, min(width - 1, int(round(self.x * width))))
        y0 = max(0, min(height - 1, int(round(self.y * height))))
        x1 = max(x0 + 1, min(width, int(round((self.x + self.w) * width))))
        y1 = max(y0 + 1, min(height, int(round((self.y + self.h) * height))))
        return x0, y0, x1 - x0, y1 - y0


@dataclass(frozen=True)
class BeltConfig:
    """Polygon that follows the conveyor belt, in fractions of the frame.

    A rectangle cannot follow a belt seen in perspective: it either includes
    the table and the frame beside the belt, or cuts the belt itself. Tracing
    the belt surface keeps the detector on the belt, so a yellow object lying
    outside it can never pause the line.
    """

    enabled: bool = False
    points: tuple[tuple[float, float], ...] = ()

    def as_points(self, width: int, height: int) -> tuple[tuple[int, int], ...]:
        """Return the polygon vertices in pixels, clamped to the frame."""
        return tuple(
            (
                max(0, min(width - 1, int(round(px * width)))),
                max(0, min(height - 1, int(round(py * height)))),
            )
            for px, py in self.points
        )


@dataclass(frozen=True)
class DetectorConfig:
    hue_min: int = 12
    hue_max: int = 38
    sat_min: int = 120
    val_min: int = 80
    min_area_px: int = 300
    morph_kernel: int = 5
    open_iterations: int = 1
    close_iterations: int = 2


@dataclass(frozen=True)
class StateConfig:
    confirm_frames: int = 3
    clear_frames: int = 8


@dataclass(frozen=True)
class ActuatorConfig:
    backend: str = "mock"  # "mock" | "gpio"
    gpio_pin: int = 17
    active_low: bool = True


@dataclass(frozen=True)
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    jpeg_quality: int = 80
    stream_fps: int = 15


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"


@dataclass(frozen=True)
class SafetyConfig:
    """Timing that keeps the system honest when the camera goes quiet."""

    # If no frame is analysed for this long, the line is paused. Without it a
    # disconnected camera would freeze the snapshot at "OK" and the line would
    # keep running blind: the worst possible failure for a fail-safe design.
    frame_timeout_s: float = 2.0


@dataclass(frozen=True)
class AppConfig:
    camera: CameraConfig
    roi: RoiConfig
    detector: DetectorConfig
    state: StateConfig
    actuator: ActuatorConfig
    web: WebConfig
    logging: LoggingConfig
    belt: BeltConfig = BeltConfig()
    safety: SafetyConfig = SafetyConfig()
    source_path: str = "config.yaml"

    def public_dict(self) -> dict[str, Any]:
        """Subset exposed through the web API. No secrets live here."""
        return {
            "detector": dict(self.detector.__dict__),
            "state": dict(self.state.__dict__),
            "roi": dict(self.roi.__dict__),
            "belt": {
                "enabled": self.belt.enabled,
                "points": [list(point) for point in self.belt.points],
            },
            "actuator": {"backend": self.actuator.backend},
            "camera": {
                "source": self.camera.source,
                "device": self.camera.device,
                "width": self.camera.width,
                "height": self.camera.height,
                "fps": self.camera.fps,
            },
        }


# --------------------------------------------------------------------------- #
# Reading helpers
# --------------------------------------------------------------------------- #


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"section '{name}' must be a mapping")
    return value


def _get(section: Mapping[str, Any], key: str, default: Any, where: str) -> Any:
    value = section.get(key, default)
    if value is None:
        raise ConfigError(f"{where}.{key} cannot be null")
    return value


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be an integer, got {value!r}")
    return value


def _as_float(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} must be a number, got {value!r}")
    return float(value)


def _as_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false, got {value!r}")
    return value


def _as_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a non-empty string, got {value!r}")
    return value


def _in_range(value: int, low: int, high: int, where: str) -> int:
    if not low <= value <= high:
        raise ConfigError(f"{where} must be between {low} and {high}, got {value}")
    return value


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #


def _build_camera(raw: Mapping[str, Any]) -> CameraConfig:
    source = _as_str(_get(raw, "source", "device", "camera"), "camera.source")
    if source not in ("device", "file"):
        raise ConfigError("camera.source must be 'device' or 'file'")
    raw_path = raw.get("path") or ""
    path = _as_str(raw_path, "camera.path") if raw_path else ""
    if source == "file" and not path:
        raise ConfigError("camera.path is required when camera.source is 'file'")
    return CameraConfig(
        source=source,
        device=_in_range(_as_int(_get(raw, "device", 0, "camera"), "camera.device"), 0, 64, "camera.device"),
        path=path,
        width=_in_range(_as_int(_get(raw, "width", 1280, "camera"), "camera.width"), 64, 4096, "camera.width"),
        height=_in_range(_as_int(_get(raw, "height", 720, "camera"), "camera.height"), 64, 4096, "camera.height"),
        fps=_in_range(_as_int(_get(raw, "fps", 30, "camera"), "camera.fps"), 1, 240, "camera.fps"),
        fourcc=_as_str(_get(raw, "fourcc", "MJPG", "camera"), "camera.fourcc"),
        buffersize=_in_range(_as_int(_get(raw, "buffersize", 1, "camera"), "camera.buffersize"), 1, 16, "camera.buffersize"),
    )


def _build_roi(raw: Mapping[str, Any]) -> RoiConfig:
    roi = RoiConfig(
        enabled=_as_bool(_get(raw, "enabled", False, "roi"), "roi.enabled"),
        x=_as_float(_get(raw, "x", 0.0, "roi"), "roi.x"),
        y=_as_float(_get(raw, "y", 0.0, "roi"), "roi.y"),
        w=_as_float(_get(raw, "w", 1.0, "roi"), "roi.w"),
        h=_as_float(_get(raw, "h", 1.0, "roi"), "roi.h"),
    )
    for name in ("x", "y", "w", "h"):
        value = getattr(roi, name)
        if not 0.0 <= value <= 1.0:
            raise ConfigError(f"roi.{name} must be between 0.0 and 1.0, got {value}")
    if roi.w <= 0.0 or roi.h <= 0.0:
        raise ConfigError("roi.w and roi.h must be greater than 0")
    if roi.x + roi.w > 1.0 + 1e-9 or roi.y + roi.h > 1.0 + 1e-9:
        raise ConfigError("roi.x + roi.w and roi.y + roi.h must not exceed 1.0")
    return roi


def _build_belt(raw: Mapping[str, Any]) -> BeltConfig:
    enabled = _as_bool(_get(raw, "enabled", False, "belt"), "belt.enabled")
    raw_points = raw.get("points") or []
    if not isinstance(raw_points, (list, tuple)):
        raise ConfigError("belt.points must be a list of [x, y] pairs")
    points: list[tuple[float, float]] = []
    for index, item in enumerate(raw_points):
        where = f"belt.points[{index}]"
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ConfigError(f"{where} must be an [x, y] pair")
        px = _as_float(item[0], f"{where}[0]")
        py = _as_float(item[1], f"{where}[1]")
        if not 0.0 <= px <= 1.0:
            raise ConfigError(f"{where}.x must be between 0.0 and 1.0, got {px}")
        if not 0.0 <= py <= 1.0:
            raise ConfigError(f"{where}.y must be between 0.0 and 1.0, got {py}")
        points.append((px, py))
    if enabled and len(points) < 3:
        raise ConfigError("belt.points needs at least 3 points when belt.enabled is true")
    return BeltConfig(enabled=enabled, points=tuple(points))


def _build_detector(raw: Mapping[str, Any]) -> DetectorConfig:
    cfg = DetectorConfig(
        hue_min=_in_range(_as_int(_get(raw, "hue_min", 12, "detector"), "detector.hue_min"), 0, 179, "detector.hue_min"),
        hue_max=_in_range(_as_int(_get(raw, "hue_max", 38, "detector"), "detector.hue_max"), 0, 179, "detector.hue_max"),
        sat_min=_in_range(_as_int(_get(raw, "sat_min", 120, "detector"), "detector.sat_min"), 0, 255, "detector.sat_min"),
        val_min=_in_range(_as_int(_get(raw, "val_min", 80, "detector"), "detector.val_min"), 0, 255, "detector.val_min"),
        min_area_px=_in_range(_as_int(_get(raw, "min_area_px", 300, "detector"), "detector.min_area_px"), 1, 10_000_000, "detector.min_area_px"),
        morph_kernel=_as_int(_get(raw, "morph_kernel", 5, "detector"), "detector.morph_kernel"),
        open_iterations=_in_range(_as_int(_get(raw, "open_iterations", 1, "detector"), "detector.open_iterations"), 0, 10, "detector.open_iterations"),
        close_iterations=_in_range(_as_int(_get(raw, "close_iterations", 2, "detector"), "detector.close_iterations"), 0, 10, "detector.close_iterations"),
    )
    if cfg.morph_kernel < 1 or cfg.morph_kernel % 2 == 0:
        raise ConfigError(f"detector.morph_kernel must be an odd integer >= 1, got {cfg.morph_kernel}")
    if cfg.hue_min > cfg.hue_max:
        raise ConfigError("detector.hue_min cannot be greater than detector.hue_max")
    return cfg


def _build_state(raw: Mapping[str, Any]) -> StateConfig:
    return StateConfig(
        confirm_frames=_in_range(_as_int(_get(raw, "confirm_frames", 3, "state"), "state.confirm_frames"), 1, 600, "state.confirm_frames"),
        clear_frames=_in_range(_as_int(_get(raw, "clear_frames", 8, "state"), "state.clear_frames"), 1, 600, "state.clear_frames"),
    )


def _build_actuator(raw: Mapping[str, Any]) -> ActuatorConfig:
    backend = _as_str(_get(raw, "backend", "mock", "actuator"), "actuator.backend")
    if backend not in ("mock", "gpio"):
        raise ConfigError("actuator.backend must be 'mock' or 'gpio'")
    return ActuatorConfig(
        backend=backend,
        gpio_pin=_in_range(_as_int(_get(raw, "gpio_pin", 17, "actuator"), "actuator.gpio_pin"), 0, 53, "actuator.gpio_pin"),
        active_low=_as_bool(_get(raw, "active_low", True, "actuator"), "actuator.active_low"),
    )


def _build_web(raw: Mapping[str, Any]) -> WebConfig:
    return WebConfig(
        host=_as_str(_get(raw, "host", "0.0.0.0", "web"), "web.host"),
        port=_in_range(_as_int(_get(raw, "port", 8000, "web"), "web.port"), 1, 65535, "web.port"),
        jpeg_quality=_in_range(_as_int(_get(raw, "jpeg_quality", 80, "web"), "web.jpeg_quality"), 1, 100, "web.jpeg_quality"),
        stream_fps=_in_range(_as_int(_get(raw, "stream_fps", 15, "web"), "web.stream_fps"), 0, 240, "web.stream_fps"),
    )


def _build_logging(raw: Mapping[str, Any]) -> LoggingConfig:
    level = _as_str(_get(raw, "level", "INFO", "logging"), "logging.level").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(f"logging.level is not a valid level: {level}")
    return LoggingConfig(level=level)


def _build_safety(raw: Mapping[str, Any]) -> SafetyConfig:
    timeout = _as_float(_get(raw, "frame_timeout_s", 2.0, "safety"), "safety.frame_timeout_s")
    if not 0.2 <= timeout <= 60.0:
        raise ConfigError(f"safety.frame_timeout_s must be between 0.2 and 60.0, got {timeout}")
    return SafetyConfig(frame_timeout_s=timeout)


def _build(raw: Mapping[str, Any], source_path: str) -> AppConfig:
    return AppConfig(
        camera=_build_camera(_section(raw, "camera")),
        roi=_build_roi(_section(raw, "roi")),
        detector=_build_detector(_section(raw, "detector")),
        state=_build_state(_section(raw, "state")),
        actuator=_build_actuator(_section(raw, "actuator")),
        web=_build_web(_section(raw, "web")),
        logging=_build_logging(_section(raw, "logging")),
        belt=_build_belt(_section(raw, "belt")),
        safety=_build_safety(_section(raw, "safety")),
        source_path=source_path,
    )


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Read and validate a configuration file."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ConfigError(f"configuration file not found: {file_path}")
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {file_path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{file_path} must contain a mapping at the top level")
    return _build(raw, str(file_path))


# --------------------------------------------------------------------------- #
# Runtime overrides (web API)
# --------------------------------------------------------------------------- #

_DETECTOR_FIELDS = set(DetectorConfig.__dataclass_fields__)
_STATE_FIELDS = set(StateConfig.__dataclass_fields__)
_ROI_FIELDS = set(RoiConfig.__dataclass_fields__)


def apply_overrides(config: AppConfig, payload: Mapping[str, Any]) -> AppConfig:
    """Return a new configuration with the detector/state/roi fields overridden.

    Only the tunable sections are writable. Camera, actuator and web settings
    require an application restart and are rejected here.
    """
    if not isinstance(payload, Mapping):
        raise ConfigError("request body must be a JSON object")

    detector = config.detector
    state = config.state
    roi = config.roi

    unknown_sections = set(payload) - {"detector", "state", "roi"}
    if unknown_sections:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown_sections))}")

    if "detector" in payload:
        patch = payload["detector"]
        if not isinstance(patch, Mapping):
            raise ConfigError("detector must be an object")
        bad = set(patch) - _DETECTOR_FIELDS
        if bad:
            raise ConfigError(f"unknown detector field(s): {', '.join(sorted(bad))}")
        detector = _build_detector({**detector.__dict__, **patch})

    if "state" in payload:
        patch = payload["state"]
        if not isinstance(patch, Mapping):
            raise ConfigError("state must be an object")
        bad = set(patch) - _STATE_FIELDS
        if bad:
            raise ConfigError(f"unknown state field(s): {', '.join(sorted(bad))}")
        state = _build_state({**state.__dict__, **patch})

    if "roi" in payload:
        patch = payload["roi"]
        if not isinstance(patch, Mapping):
            raise ConfigError("roi must be an object")
        bad = set(patch) - _ROI_FIELDS
        if bad:
            raise ConfigError(f"unknown roi field(s): {', '.join(sorted(bad))}")
        roi = _build_roi({**roi.__dict__, **patch})

    return replace(config, detector=detector, state=state, roi=roi)
