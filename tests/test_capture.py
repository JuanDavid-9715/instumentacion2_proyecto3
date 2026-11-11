"""Frame fan-out and the camera recipe."""

from __future__ import annotations

import queue

import cv2
import numpy as np

from core.capture import FrameBus, FrameGrabber
from core.config import CameraConfig


# -- FrameBus ---------------------------------------------------------------- #


def test_every_subscriber_receives_the_frame() -> None:
    bus = FrameBus()
    first = bus.subscribe()
    second = bus.subscribe()
    bus.publish("frame")
    assert first.get_nowait() == "frame"
    assert second.get_nowait() == "frame"


def test_a_slow_consumer_keeps_only_the_latest_frame() -> None:
    bus = FrameBus()
    subscriber = bus.subscribe()
    for index in range(5):
        bus.publish(index)
    assert subscriber.get_nowait() == 4
    assert subscriber.empty()


def test_publishing_never_blocks() -> None:
    bus = FrameBus()
    subscriber = bus.subscribe()
    for index in range(1000):
        bus.publish(index)
    assert subscriber.get_nowait() == 999


def test_close_signals_every_consumer() -> None:
    bus = FrameBus()
    subscriber = bus.subscribe()
    bus.close()
    assert subscriber.get_nowait() is None
    assert bus.subscriber_count == 0


def test_unsubscribe_stops_delivery() -> None:
    bus = FrameBus()
    subscriber = bus.subscribe()
    bus.unsubscribe(subscriber)
    bus.publish("frame")
    assert subscriber.empty()


def test_subscribing_after_close_yields_nothing() -> None:
    bus = FrameBus()
    bus.close()
    subscriber = bus.subscribe()
    bus.publish("frame")
    assert subscriber.empty()


# -- Camera recipe ----------------------------------------------------------- #


class FakeCapture:
    def __init__(self, width: int = 1280, height: int = 720, fps: float = 30.0) -> None:
        self.applied: dict[int, float] = {}
        self._width = width
        self._height = height
        self._fps = fps

    def set(self, prop: int, value: float) -> bool:
        self.applied[prop] = value
        return True

    def get(self, prop: int) -> float:
        return {
            cv2.CAP_PROP_FRAME_WIDTH: float(self._width),
            cv2.CAP_PROP_FRAME_HEIGHT: float(self._height),
            cv2.CAP_PROP_FPS: self._fps,
        }.get(prop, 0.0)


def test_camera_recipe_is_applied_in_full() -> None:
    grabber = FrameGrabber(CameraConfig(width=1280, height=720, fps=30, buffersize=1), FrameBus())
    capture = FakeCapture()
    grabber._apply_device_settings(capture)  # noqa: SLF001 - the recipe is the unit under test

    assert capture.applied[cv2.CAP_PROP_FOURCC] == cv2.VideoWriter_fourcc(*"MJPG")
    assert capture.applied[cv2.CAP_PROP_FRAME_WIDTH] == 1280
    assert capture.applied[cv2.CAP_PROP_FRAME_HEIGHT] == 720
    assert capture.applied[cv2.CAP_PROP_FPS] == 30
    assert capture.applied[cv2.CAP_PROP_BUFFERSIZE] == 1


def test_recipe_uses_the_configured_resolution() -> None:
    grabber = FrameGrabber(CameraConfig(width=640, height=480, fps=15, fourcc="YUYV"), FrameBus())
    capture = FakeCapture(width=640, height=480, fps=15.0)
    grabber._apply_device_settings(capture)  # noqa: SLF001

    assert capture.applied[cv2.CAP_PROP_FRAME_WIDTH] == 640
    assert capture.applied[cv2.CAP_PROP_FRAME_HEIGHT] == 480
    assert capture.applied[cv2.CAP_PROP_FOURCC] == cv2.VideoWriter_fourcc(*"YUYV")


def test_open_reports_a_missing_device() -> None:
    grabber = FrameGrabber(CameraConfig(source="device", device=63), FrameBus())
    assert grabber.open() is False
    assert grabber.last_error is not None


def test_stop_on_a_thread_that_never_started_is_safe() -> None:
    grabber = FrameGrabber(CameraConfig(), FrameBus())
    grabber.stop(timeout=0.1)  # must not raise
