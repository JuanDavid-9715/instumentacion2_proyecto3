"""Configuration must fail loudly, never silently."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import ConfigError, RoiConfig, apply_overrides, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_project_config_is_valid() -> None:
    config = load_config(PROJECT_ROOT / "config.yaml")
    assert config.camera.width == 1280
    assert config.camera.height == 720
    assert config.actuator.backend == "mock"
    assert config.detector.hue_min < config.detector.hue_max


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml")


def test_invalid_yaml_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "camera: [1, 2\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_hue_out_of_range_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "detector:\n  hue_min: 500\n")
    with pytest.raises(ConfigError, match="detector.hue_min"):
        load_config(path)


def test_inverted_hue_window_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "detector:\n  hue_min: 100\n  hue_max: 20\n")
    with pytest.raises(ConfigError, match="hue_min"):
        load_config(path)


def test_even_morph_kernel_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "detector:\n  morph_kernel: 4\n")
    with pytest.raises(ConfigError, match="morph_kernel"):
        load_config(path)


def test_roi_outside_the_frame_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "roi:\n  x: 0.6\n  w: 0.6\n")
    with pytest.raises(ConfigError, match="roi.x"):
        load_config(path)


def test_file_source_requires_a_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "camera:\n  source: file\n")
    with pytest.raises(ConfigError, match="camera.path"):
        load_config(path)


def test_unknown_actuator_backend_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "actuator:\n  backend: pwm\n")
    with pytest.raises(ConfigError, match="actuator.backend"):
        load_config(path)


def test_string_instead_of_integer_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "web:\n  port: \"ocho mil\"\n")
    with pytest.raises(ConfigError, match="web.port"):
        load_config(path)


def test_roi_fractions_become_pixels() -> None:
    roi = RoiConfig(enabled=True, x=0.5, y=0.5, w=0.5, h=0.5)
    assert roi.as_pixels(640, 360) == (320, 180, 320, 180)


def test_roi_pixels_are_clamped_to_the_frame() -> None:
    roi = RoiConfig(enabled=True, x=0.0, y=0.0, w=1.0, h=1.0)
    assert roi.as_pixels(1280, 720) == (0, 0, 1280, 720)


def test_overrides_change_only_the_requested_fields() -> None:
    config = load_config(PROJECT_ROOT / "config.yaml")
    updated = apply_overrides(config, {"detector": {"min_area_px": 900}})
    assert updated.detector.min_area_px == 900
    assert updated.detector.hue_min == config.detector.hue_min
    assert updated.state == config.state
    assert config.detector.min_area_px != 900  # the original is untouched


def test_overrides_reject_unknown_fields() -> None:
    config = load_config(PROJECT_ROOT / "config.yaml")
    with pytest.raises(ConfigError, match="unknown detector field"):
        apply_overrides(config, {"detector": {"sensitivity": 5}})


def test_overrides_reject_sections_that_need_a_restart() -> None:
    config = load_config(PROJECT_ROOT / "config.yaml")
    with pytest.raises(ConfigError, match="unknown section"):
        apply_overrides(config, {"camera": {"device": 0}})


def test_detector_thresholds_come_from_the_yaml(tmp_path: Path) -> None:
    from core.actuator import MockOutput
    from core.runtime import Runtime
    from helpers import YELLOW, with_rect

    strict = _write(tmp_path, "detector:\n  min_area_px: 10000\n")
    runtime = Runtime(load_config(strict), output=MockOutput())
    runtime.process(with_rect(300, 180, 60, 60, YELLOW))
    assert runtime.snapshot().contaminated is False

    relaxed = _write(tmp_path, "detector:\n  min_area_px: 300\n")
    runtime = Runtime(load_config(relaxed), output=MockOutput())
    runtime.process(with_rect(300, 180, 60, 60, YELLOW))
    assert runtime.snapshot().contaminated is True


def test_safety_timeout_is_validated(tmp_path: Path) -> None:
    path = _write(tmp_path, "safety:\n  frame_timeout_s: 0.01\n")
    with pytest.raises(ConfigError, match="frame_timeout_s"):
        load_config(path)


def test_belt_polygon_is_read_from_the_yaml(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "belt:\n  enabled: true\n  points:\n    - [0.1, 0.0]\n    - [0.9, 0.0]\n    - [0.5, 1.0]\n",
    )
    config = load_config(path)
    assert config.belt.enabled is True
    assert len(config.belt.points) == 3
    assert config.belt.as_points(1000, 500) == ((100, 0), (900, 0), (500, 499))


def test_enabled_belt_with_too_few_points_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "belt:\n  enabled: true\n  points:\n    - [0.1, 0.0]\n    - [0.9, 0.0]\n")
    with pytest.raises(ConfigError, match="at least 3 points"):
        load_config(path)


def test_belt_point_outside_the_frame_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "belt:\n  enabled: true\n  points:\n    - [0.1, 0.0]\n    - [1.5, 0.0]\n    - [0.5, 1.0]\n",
    )
    with pytest.raises(ConfigError, match="belt.points"):
        load_config(path)


def test_belt_point_with_the_wrong_shape_is_an_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "belt:\n  enabled: true\n  points:\n    - [0.1, 0.0]\n    - [0.9]\n    - [0.5, 1.0]\n",
    )
    with pytest.raises(ConfigError, match="pair"):
        load_config(path)


def test_disabled_belt_needs_no_points(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, "belt:\n  enabled: false\n"))
    assert config.belt.enabled is False
    assert config.belt.points == ()


def test_project_config_traces_the_belt() -> None:
    config = load_config(PROJECT_ROOT / "config.yaml")
    assert config.belt.enabled is True
    assert len(config.belt.points) >= 3


def test_project_config_declares_a_stale_frame_timeout() -> None:
    config = load_config(PROJECT_ROOT / "config.yaml")
    assert config.safety.frame_timeout_s > 0
