"""Composition root.

One process, one port. Starts the capture thread, the detection loop and the
web server, and guarantees the relay ends de-energized no matter how the
process stops.

Order on shutdown matters: the actuator is forced to the pause state *before*
anything else is torn down, and it happens in a ``finally`` block, so a crash,
a ``SIGINT`` or a ``SIGTERM`` all end with the line paused.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import signal
import sys
import threading
import time
from typing import Sequence

import uvicorn

from core.capture import FrameGrabber, probe_source
from core.config import AppConfig, ConfigError, load_config
from core.runtime import Runtime, build_runtime
from web.server import create_app

log = logging.getLogger(__name__)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sistema de vision para deteccion de maiz en la banda de ingreso.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="ruta del archivo de configuracion (por defecto: config.yaml)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="abre la camara, informa lo negociado y termina sin arrancar el sistema",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="no arranca la interfaz web (solo captura y deteccion)",
    )
    return parser.parse_args(argv)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _install_signal_handlers(runtime: Runtime, stop: threading.Event) -> dict[int, object]:
    """Pause the line on SIGINT/SIGTERM before unwinding."""
    previous: dict[int, object] = {}

    def handler(signum: int, _frame) -> None:
        log.warning("signal %s received: pausing the line", signum)
        runtime.begin_shutdown()
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            log.debug("cannot install a handler for %s in this thread", sig)
    return previous


def _start_web(runtime: Runtime, config: AppConfig) -> uvicorn.Server:
    application = create_app(runtime)
    server_config = uvicorn.Config(
        application,
        host=config.web.host,
        port=config.web.port,
        log_level="warning",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, name="web", daemon=True)
    thread.start()
    return server


def run(config: AppConfig, *, with_web: bool = True) -> int:
    runtime = build_runtime(config)
    grabber = FrameGrabber(config.camera, runtime.bus)
    stop = threading.Event()
    previous_handlers = _install_signal_handlers(runtime, stop)

    server: uvicorn.Server | None = None
    subscriber: queue.Queue | None = None
    exit_code = 0

    try:
        if not grabber.open():
            log.error("cannot open the capture source: %s", grabber.last_error)
            return 2

        grabber.start()

        if with_web:
            server = _start_web(runtime, config)
            log.info("web interface: http://%s:%d", config.web.host, config.web.port)

        subscriber = runtime.bus.subscribe()
        timeout = config.safety.frame_timeout_s
        last_frame_at = time.monotonic()
        stale = False
        log.info("system running; the line stays paused until the first frame is analysed")

        while not stop.is_set():
            try:
                frame = subscriber.get(timeout=0.5)
            except queue.Empty:
                if not stale and time.monotonic() - last_frame_at > timeout:
                    log.error("no frame analysed for %.1f s: pausing the line", timeout)
                    runtime.force_pause(f"no frames for {timeout:.1f} s")
                    stale = True
                continue
            if frame is None:
                break
            if stale:
                log.warning("frames are back after a gap; the hysteresis must clear first")
                stale = False
            last_frame_at = time.monotonic()
            runtime.process(frame)
    except Exception:  # noqa: BLE001 - log, pause, and exit non-zero
        log.exception("fatal error in the main loop")
        exit_code = 1
    finally:
        stop.set()
        try:
            runtime.shutdown()  # de-energizes the relay and releases the device
        except Exception:  # noqa: BLE001
            log.exception("error during shutdown")
        grabber.stop()
        if server is not None:
            server.should_exit = True
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    log.info("stopped")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error de configuracion: {exc}", file=sys.stderr)
        return 2

    _configure_logging(config.logging.level)

    if args.probe:
        report = probe_source(config.camera)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("opened") else 2

    return run(config, with_web=not args.no_web)


if __name__ == "__main__":
    sys.exit(main())
