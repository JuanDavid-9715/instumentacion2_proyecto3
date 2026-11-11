"""Line state machine with hysteresis.

A single contaminated frame must not stop the conveyor: dust, a specular
highlight or one noisy frame would pause the line constantly. A single clean
frame must not resume it either, or the belt would stutter while the corn is
still under the camera.

Two counters do the work:

* ``confirm_frames`` consecutive contaminated frames are required to pause.
* ``clear_frames`` consecutive clean frames are required to resume.

Any frame that contradicts the current counter resets it.

No hardware is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import StateConfig


@dataclass
class StateSnapshot:
    blocked: bool
    contaminated: bool
    dirty_count: int
    clean_count: int
    confirm_frames: int
    clear_frames: int


class LineStateMachine:
    """Turns per-frame contamination into a stable line-OK signal."""

    def __init__(self, config: StateConfig) -> None:
        self._config = config
        self._blocked = False
        self._dirty = 0
        self._clean = 0

    # -- configuration ---------------------------------------------------- #

    @property
    def config(self) -> StateConfig:
        return self._config

    def reconfigure(self, config: StateConfig) -> None:
        self._config = config
        # Counters belong to the previous thresholds: restart the tally.
        self._dirty = 0
        self._clean = 0

    # -- state ------------------------------------------------------------ #

    @property
    def blocked(self) -> bool:
        return self._blocked

    def reset(self) -> None:
        self._blocked = True
        self._dirty = 0
        self._clean = 0

    def update(self, contaminated: bool) -> bool:
        """Feed one frame. Returns ``True`` when the line may run."""
        if not self._blocked:
            if contaminated:
                self._dirty += 1
                self._clean = 0
                if self._dirty >= self._config.confirm_frames:
                    self._blocked = True
                    self._dirty = 0
            else:
                self._dirty = 0
                self._clean = 0
        else:
            if not contaminated:
                self._clean += 1
                self._dirty = 0
                if self._clean >= self._config.clear_frames:
                    self._blocked = False
                    self._clean = 0
            else:
                self._clean = 0
                self._dirty += 1
        return not self._blocked

    def snapshot(self, contaminated: bool) -> StateSnapshot:
        return StateSnapshot(
            blocked=self._blocked,
            contaminated=contaminated,
            dirty_count=self._dirty,
            clean_count=self._clean,
            confirm_frames=self._config.confirm_frames,
            clear_frames=self._config.clear_frames,
        )
