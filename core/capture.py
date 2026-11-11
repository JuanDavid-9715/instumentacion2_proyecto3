"""Frame acquisition. One ``VideoCapture``, one owner thread.

OpenCV buffers frames internally. Reading at the detector's pace while the
camera keeps filling that buffer produces growing latency: the detector would
analyse a frame from half a second ago and the pause would arrive too late.

Two rules prevent it:

1. ``CAP_PROP_BUFFERSIZE = 1`` asks the driver to keep almost nothing.
2. Every consumer gets a ``queue.Queue(maxsize=1)``. Publishing never blocks;
   when a consumer is behind, the stale frame is discarded and replaced. A slow
   consumer therefore loses frames instead of lagging.

The frame array is shared between consumers and must be treated as read-only.
Anything that draws on the frame (the web overlay) works on a copy.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

import cv2
import numpy as np

from core.config import CameraConfig

log = logging.getLogger(__name__)


class FrameBus:
    """Fan-out of the latest frame to independent consumers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._closed = False

    def subscribe(self, maxsize: int = 1) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            if self._closed:
                return subscriber
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, frame: np.ndarray | None) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            self._put_latest(subscriber, frame)

    def close(self) -> None:
        """Signal every consumer that no more frames will arrive."""
        with self._lock:
            subscribers = list(self._subscribers)
            self._subscribers.clear()
            self._closed = True
        for subscriber in subscribers:
            self._put_latest(subscriber, None)

    @staticmethod
    def _put_latest(subscriber: queue.Queue, item: Any) -> None:
        while True:
            try:
                subscriber.put_nowait(item)
                return
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    return


class FrameGrabber(threading.Thread):
    """Owns the only ``VideoCapture`` in the process."""

    def __init__(self, config: CameraConfig, bus: FrameBus, *, name: str = "capture") -> None:
        super().__init__(name=name, daemon=True)
        self._config = config
        self._bus = bus
        self._stop = threading.Event()
        self._capture: cv2.VideoCapture | None = None
        self._last_error: str | None = None
        self._failed_reads = 0
        self.frames_published = 0

    # -- public API ------------------------------------------------------- #

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.is_alive():
            self.join(timeout=timeout)

    # -- lifecycle -------------------------------------------------------- #

    def open(self) -> bool:
        """Open the configured source and apply the validated recipe."""
        cfg = self._config
        if cfg.source == "file":
            capture = cv2.VideoCapture(cfg.path)
        else:
            capture = cv2.VideoCapture(cfg.device, cv2.CAP_V4L2)
            self._apply_device_settings(capture)

        if not capture.isOpened():
            self._last_error = f"cannot open camera source: {cfg.source}={cfg.device or cfg.path!r}"
            capture.release()
            return False

        self._capture = capture
        self._last_error = None
        self._failed_reads = 0
        log.info("capture opened: source=%s device=%s path=%s", cfg.source, cfg.device, cfg.path or "-")
        return True

    def _apply_device_settings(self, capture: cv2.VideoCapture) -> None:
        """The recipe proven on the real camera. Do not change it without measuring."""
        cfg = self._config
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg.fourcc))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        capture.set(cv2.CAP_PROP_FPS, cfg.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, cfg.buffersize)

        actual_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        if (actual_w, actual_h) != (cfg.width, cfg.height):
            log.warning("camera negotiated %dx%d instead of %dx%d", actual_w, actual_h, cfg.width, cfg.height)
        if actual_fps and abs(actual_fps - cfg.fps) > 1.0:
            log.warning("camera negotiated %.1f fps instead of %d", actual_fps, cfg.fps)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()

    def probe(self) -> dict[str, Any]:
        """Open the source once and report what it actually negotiated."""
        result: dict[str, Any] = {"opened": False}
        started = time.perf_counter()
        if not self.open():
            result["error"] = self._last_error
            return result

        try:
            assert self._capture is not None
            ok, frame = self._capture.read()
            result.update(
                opened=True,
                read_ok=bool(ok),
                width=int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=round(float(self._capture.get(cv2.CAP_PROP_FPS)), 2),
                shape=list(frame.shape) if frame is not None else None,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        finally:
            self.close()
        return result

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._capture is None and not self.open():
                    if self._stop.wait(2.0):
                        break
                    continue

                assert self._capture is not None
                ok, frame = self._capture.read()
                if not ok or frame is None:
                    if self._config.source == "file":
                        # End of the recorded clip: rewind and replay.
                        self.close()
                        self._failed_reads = 0
                        self._stop.wait(0.01)
                        continue
                    self._failed_reads += 1
                    self._last_error = "camera read failed"
                    if self._failed_reads >= 30:
                        log.error("camera failed %d times in a row, reopening", self._failed_reads)
                        self.close()
                        self._failed_reads = 0
                        self._stop.wait(1.0)
                    else:
                        self._stop.wait(0.02)
                    continue

                self._failed_reads = 0
                self._last_error = None
                self.frames_published += 1
                self._bus.publish(frame)
        except Exception as exc:  # noqa: BLE001 - the thread must never die silently
            self._last_error = f"capture thread crashed: {exc}"
            log.exception("capture thread crashed")
        finally:
            self.close()
            log.info("capture thread stopped after %d frames", self.frames_published)


def probe_source(config: CameraConfig) -> dict[str, Any]:
    """Open the source once and report what the camera actually negotiated."""
    return FrameGrabber(config, FrameBus()).probe()
