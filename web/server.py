"""FastAPI interface: live stream, state and runtime configuration.

The web layer is a consumer only. It never touches the camera directly and
never draws on the frame the detector is reading: overlays are painted on a
copy.
"""

from __future__ import annotations

import logging
import queue
import time
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from core.config import ConfigError
from core.runtime import Runtime

log = logging.getLogger(__name__)

BOUNDARY = "frameboundary"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_FILE = _STATIC_DIR / "index.html"


def _read_index() -> str:
    try:
        return _INDEX_FILE.read_text(encoding="utf-8")
    except OSError:
        log.exception("cannot read %s", _INDEX_FILE)
        return "<h1>Interfaz no disponible</h1>"


def _draw_overlay(frame, snapshot) -> "cv2.Mat":
    """Paint the detection result on a copy of the frame."""
    color = (0, 200, 0) if snapshot.ok else (0, 0, 255)
    for x, y, w, h in snapshot.boxes:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 255), 2)

    label = "LINEA OK" if snapshot.ok else "PAUSA"
    cv2.putText(frame, label, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
    cv2.putText(
        frame,
        f"{snapshot.fps:.0f} fps  area {snapshot.area_px}",
        (12, 68),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        1,
        cv2.LINE_AA,
    )
    return frame


def _mjpeg_generator(runtime: Runtime):
    subscriber = runtime.bus.subscribe()
    web_config = runtime.config.web
    quality = int(web_config.jpeg_quality)
    interval = 1.0 / web_config.stream_fps if web_config.stream_fps > 0 else 0.0
    last_sent = 0.0
    try:
        while True:
            try:
                frame = subscriber.get(timeout=2.0)
            except queue.Empty:
                continue
            if frame is None:
                break

            now = time.monotonic()
            if interval and (now - last_sent) < interval:
                continue
            last_sent = now

            image = _draw_overlay(frame.copy(), runtime.snapshot())
            ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(buffer.nbytes).encode() + b"\r\n\r\n"
                + buffer.tobytes() + b"\r\n"
            )
    except GeneratorExit:
        return
    finally:
        runtime.bus.unsubscribe(subscriber)


def create_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(
        title="Sistema de vision - linea de arroz",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_read_index())

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse(
            _mjpeg_generator(runtime),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/state")
    def get_state() -> JSONResponse:
        return JSONResponse(runtime.snapshot().to_dict())

    @app.get("/api/config")
    def get_config() -> JSONResponse:
        return JSONResponse(runtime.public_config())

    @app.post("/api/config")
    async def post_config(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001 - any malformed body is a 400
            raise HTTPException(status_code=400, detail="cuerpo JSON invalido") from exc
        try:
            return JSONResponse(runtime.apply_config(payload))
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/health")
    def health() -> JSONResponse:
        snapshot = runtime.snapshot()
        return JSONResponse(
            {
                "streaming": snapshot.frames > 0,
                "frames": snapshot.frames,
                "last_error": snapshot.last_error,
                "subscribers": runtime.bus.subscriber_count,
            }
        )

    return app
