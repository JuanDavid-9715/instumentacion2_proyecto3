"""HTTP surface: index, state and runtime configuration."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from core.actuator import MockOutput
from core.runtime import Runtime
from helpers import YELLOW, blank, make_config, with_rect
from web.server import _mjpeg_generator, create_app

CORN = with_rect(300, 180, 80, 80, YELLOW)


def build() -> tuple[TestClient, Runtime, MockOutput]:
    output = MockOutput()
    runtime = Runtime(make_config(), output=output)
    return TestClient(create_app(runtime)), runtime, output


def test_index_is_served_in_spanish() -> None:
    client, _, _ = build()
    response = client.get("/")
    assert response.status_code == 200
    assert "Línea de arroz" in response.text
    assert 'src="/stream"' in response.text


def test_state_endpoint_reports_the_line() -> None:
    client, runtime, _ = build()
    runtime.process(blank())
    payload = client.get("/api/state").json()
    assert payload["ok"] is True
    assert payload["frames"] == 1
    assert payload["hysteresis"]["confirm_frames"] == 2


def test_state_endpoint_reports_the_pause() -> None:
    client, runtime, _ = build()
    client.post("/api/config", json={"state": {"confirm_frames": 1, "clear_frames": 1}})
    runtime.process(CORN)
    payload = client.get("/api/state").json()
    assert payload["ok"] is False
    assert payload["contaminated"] is True


def test_config_reads_current_values() -> None:
    client, _, _ = build()
    payload = client.get("/api/config").json()
    assert payload["detector"]["min_area_px"] == 300
    assert payload["state"]["confirm_frames"] == 2
    assert payload["actuator"] == {"backend": "mock"}


def test_config_write_applies_immediately() -> None:
    client, _, _ = build()
    response = client.post("/api/config", json={"detector": {"min_area_px": 900}})
    assert response.status_code == 200
    assert response.json()["detector"]["min_area_px"] == 900
    assert client.get("/api/config").json()["detector"]["min_area_px"] == 900


def test_config_write_rejects_out_of_range_values() -> None:
    client, _, _ = build()
    response = client.post("/api/config", json={"detector": {"hue_min": 500}})
    assert response.status_code == 400
    assert "hue_min" in response.json()["detail"]


def test_config_write_rejects_sections_that_need_a_restart() -> None:
    client, _, _ = build()
    response = client.post("/api/config", json={"camera": {"device": 0}})
    assert response.status_code == 400


def test_config_write_rejects_a_malformed_body() -> None:
    client, _, _ = build()
    response = client.post(
        "/api/config",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_config_write_never_touches_config_yaml() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "config.yaml"
    before = path.read_bytes()
    client, _, _ = build()
    assert client.post("/api/config", json={"detector": {"min_area_px": 900}}).status_code == 200
    assert path.read_bytes() == before


def test_health_reports_no_frames_before_capturing() -> None:
    client, _, _ = build()
    payload = client.get("/api/health").json()
    assert payload["streaming"] is False
    assert payload["frames"] == 0


def test_health_reports_streaming_after_a_frame() -> None:
    client, runtime, _ = build()
    runtime.process(blank())
    payload = client.get("/api/health").json()
    assert payload["streaming"] is True
    assert payload["frames"] == 1


def test_stream_yields_mjpeg_parts() -> None:
    runtime = Runtime(make_config(), output=MockOutput())
    chunks: list[bytes] = []

    def consume() -> None:
        for chunk in _mjpeg_generator(runtime):
            chunks.append(chunk)
            runtime.bus.close()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()

    deadline = time.monotonic() + 2.0
    while runtime.bus.subscriber_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    runtime.bus.publish(blank())
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert chunks, "the generator produced no frame"
    assert chunks[0].startswith(b"--frameboundary")
    assert b"Content-Type: image/jpeg" in chunks[0]
    assert b"\xff\xd8" in chunks[0]  # JPEG start-of-image marker
