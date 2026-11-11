"""Shared runtime state and the detection loop body.

This is the seam between the hardware-free modules and the process that also
serves HTTP: the ``Runtime`` owns the detector, the state machine and the relay,
keeps a snapshot the web layer can read at any time, and applies threshold
changes coming from the browser.

Failing safely is the whole point of this class. If the detector throws, the
line is paused. If the actuator throws while energizing, the pause state is
forced. Neither case may leave the relay energized.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Mapping

import numpy as np

from core.actuator import Output, make_output
from core.capture import FrameBus
from core.config import AppConfig, apply_overrides
from core.detector import CornDetector, Detection
from core.state import LineStateMachine

log = logging.getLogger(__name__)

_FPS_WINDOW = 30


@dataclass(frozen=True)
class Snapshot:
    ok: bool = False
    contaminated: bool = False
    area_px: int = 0
    boxes: tuple[tuple[int, int, int, int], ...] = ()
    frames: int = 0
    fps: float = 0.0
    detection_ms: float = 0.0
    errors: int = 0
    last_error: str | None = None
    updated_at: float = 0.0
    dirty_count: int = 0
    clean_count: int = 0
    confirm_frames: int = 0
    clear_frames: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "contaminated": self.contaminated,
            "area_px": self.area_px,
            "boxes": [list(box) for box in self.boxes],
            "frames": self.frames,
            "fps": round(self.fps, 1),
            "detection_ms": round(self.detection_ms, 2),
            "errors": self.errors,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
            "hysteresis": {
                "dirty_count": self.dirty_count,
                "clean_count": self.clean_count,
                "confirm_frames": self.confirm_frames,
                "clear_frames": self.clear_frames,
            },
        }


class Runtime:
    """Wires detector -> hysteresis -> relay and publishes a snapshot."""

    def __init__(
        self,
        config: AppConfig,
        *,
        bus: FrameBus | None = None,
        detector: CornDetector | None = None,
        machine: LineStateMachine | None = None,
        output: Output | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._config = config
        self.bus = bus or FrameBus()
        self.detector = detector or CornDetector(config.detector, config.roi)
        self.machine = machine or LineStateMachine(config.state)
        self.output = output or make_output(config.actuator)

        # A freshly started system must not energize anything until it has
        # proved it can see. The first processed frame releases the line.
        self.output.safe()

        self._snapshot = Snapshot(
            confirm_frames=config.state.confirm_frames,
            clear_frames=config.state.clear_frames,
            updated_at=time.time(),
        )
        self._timestamps: deque[float] = deque(maxlen=_FPS_WINDOW)
        self._last_ok: bool | None = None
        self._stopping = threading.Event()

    # -- configuration ---------------------------------------------------- #

    @property
    def config(self) -> AppConfig:
        return self._config

    def apply_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Apply detector/state/roi overrides. Raises ``ConfigError``."""
        with self._lock:
            updated = apply_overrides(self._config, payload)
            self._config = updated
            self.detector.reconfigure(updated.detector, updated.roi)
            self.machine.reconfigure(updated.state)
            self._snapshot = replace(
                self._snapshot,
                confirm_frames=updated.state.confirm_frames,
                clear_frames=updated.state.clear_frames,
                updated_at=time.time(),
            )
            log.info(
                "configuration updated: detector=%s state=%s",
                updated.detector,
                updated.state,
            )
            return updated.public_dict()

    def public_config(self) -> dict[str, Any]:
        return self._config.public_dict()

    # -- state ------------------------------------------------------------ #

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    # -- detection loop body ---------------------------------------------- #

    def process(self, frame: np.ndarray | None) -> Snapshot:
        """Analyse one frame and command the relay."""
        if frame is None:
            return self.snapshot()

        try:
            detection = self.detector.detect(frame)
        except Exception as exc:  # noqa: BLE001 - a broken detector must pause the line
            log.exception("detector failure; pausing the line")
            self.force_pause(f"detector: {exc}")
            return self.snapshot()

        ok = self.machine.update(detection.contaminated)
        self._command(ok)
        self._publish(detection, ok)
        return self.snapshot()

    def begin_shutdown(self) -> None:
        """Pause the line and refuse any further command to energize.

        Called from the signal handler. Without the flag, the frame already in
        flight would finish its ``process()`` and command the relay back on
        after the handler de-energized it.
        """
        self._stopping.set()
        try:
            self.output.safe()
        except Exception:  # noqa: BLE001 - the handler must never raise
            log.exception("could not de-energize the relay during shutdown")

    def _command(self, ok: bool) -> None:
        if self._stopping.is_set():
            return
        try:
            self.output.set_ok(ok)
        except Exception:  # noqa: BLE001 - never let the actuator kill the loop
            log.exception("actuator failure; forcing the pause state")
            try:
                self.output.safe()
            except Exception:  # noqa: BLE001
                log.exception("actuator safe() failed too")
            return
        if self._stopping.is_set():
            # A signal interrupted us between the check and the command: undo
            # it. A lock cannot be used here because a signal handler runs on
            # this very thread and would risk deadlocking.
            try:
                self.output.safe()
            except Exception:  # noqa: BLE001
                log.exception("actuator safe() failed after shutdown")

    def force_pause(self, reason: str) -> None:
        """Pause the line and record why. Used for detector and camera faults."""
        self.machine.reset()
        self._command(False)
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                ok=False,
                errors=self._snapshot.errors + 1,
                last_error=reason,
                updated_at=time.time(),
                dirty_count=self.machine.snapshot(False).dirty_count,
                clean_count=self.machine.snapshot(False).clean_count,
            )

    def _publish(self, detection: Detection, ok: bool) -> None:
        now = time.time()
        self._timestamps.append(now)
        fps = 0.0
        if len(self._timestamps) > 1:
            span = self._timestamps[-1] - self._timestamps[0]
            if span > 0:
                fps = (len(self._timestamps) - 1) / span

        if self._last_ok is None or ok != self._last_ok:
            log.warning(
                "line %s (contaminated=%s area=%d px boxes=%d)",
                "RUNNING" if ok else "PAUSED",
                detection.contaminated,
                detection.area_px,
                len(detection.boxes),
            )
            self._last_ok = ok

        machine = self.machine.snapshot(detection.contaminated)
        with self._lock:
            self._snapshot = Snapshot(
                ok=ok,
                contaminated=detection.contaminated,
                area_px=detection.area_px,
                boxes=detection.boxes,
                frames=self._snapshot.frames + 1,
                fps=fps,
                detection_ms=detection.elapsed_ms,
                errors=self._snapshot.errors,
                last_error=None,
                updated_at=now,
                dirty_count=machine.dirty_count,
                clean_count=machine.clean_count,
                confirm_frames=machine.confirm_frames,
                clear_frames=machine.clear_frames,
            )

    # -- shutdown --------------------------------------------------------- #

    def shutdown(self) -> None:
        """Leave the line in the pause state and release the hardware."""
        try:
            self.output.close()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            log.exception("error while closing the actuator")
        finally:
            self.bus.close()


def build_runtime(config: AppConfig, **kwargs: Any) -> Runtime:
    """Convenience constructor used by ``app.py`` and by the tests."""
    return Runtime(config, **kwargs)


__all__ = ["Runtime", "Snapshot", "build_runtime"]
