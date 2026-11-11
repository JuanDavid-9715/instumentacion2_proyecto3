"""S2: one shared frame, maxsize-1 queues, read-only consumers."""

from __future__ import annotations

import numpy as np

from core.capture import FrameBus
from core.config import DetectorConfig
from core.detector import CornDetector
from helpers import YELLOW, blank, with_rect
from web.server import _draw_overlay


class _Snapshot:
    ok = True
    boxes: tuple = ()
    fps = 0.0
    area_px = 0


def test_subscribers_receive_a_maxsize_one_queue() -> None:
    bus = FrameBus()
    assert bus.subscribe().maxsize == 1


def test_all_consumers_share_one_read_only_frame() -> None:
    bus = FrameBus()
    first, second = bus.subscribe(), bus.subscribe()
    frame = with_rect(300, 180, 60, 60, YELLOW)
    before = frame.copy()
    bus.publish(frame)

    a, b = first.get_nowait(), second.get_nowait()
    assert a is frame and b is frame

    CornDetector(DetectorConfig()).detect(a)
    _draw_overlay(b.copy(), _Snapshot())
    assert np.array_equal(frame, before)


def test_close_signals_consumers_and_late_subscribers_get_nothing() -> None:
    bus = FrameBus()
    consumer = bus.subscribe()
    bus.close()
    assert consumer.get_nowait() is None

    late = bus.subscribe()
    bus.publish(blank())
    assert late.empty()
