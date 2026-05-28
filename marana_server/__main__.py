"""Entry point: python -m marana_server [options]"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from marana_server.camera import MaranaCamera
from marana_server.service import MaranaService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="marana_server")
    p.add_argument("--sim", action="store_true",
                   help="open the SDK simulator camera instead of a real device")
    p.add_argument("--bind", default=os.environ.get("MARANA_BIND", "0.0.0.0"),
                   help="interface to bind (default 0.0.0.0)")
    p.add_argument("--ctrl-port", type=int,
                   default=int(os.environ.get("MARANA_CTRL_PORT", "5555")))
    p.add_argument("--frame-port", type=int,
                   default=int(os.environ.get("MARANA_FRAME_PORT", "5556")))
    p.add_argument("--captures-dir",
                   default=os.environ.get("MARANA_CAPTURES_DIR", "/var/lib/marana/captures"))
    p.add_argument("--allow-shutdown", action="store_true",
                   help="enable the shutdown command (otherwise it's denied)")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    log = logging.getLogger("marana_server")

    cam = MaranaCamera()
    cam.open(sim=args.sim)
    log.info("Opened %s (sim=%s) model=%r serial=%r sensor=%dx%d",
             "simulator" if args.sim else "real camera", args.sim,
             cam.model, cam.serial, cam.sensor_width, cam.sensor_height)

    service = MaranaService(
        camera=cam,
        ctrl_endpoint=f"tcp://{args.bind}:{args.ctrl_port}",
        pub_endpoint=f"tcp://{args.bind}:{args.frame_port}",
        captures_dir=args.captures_dir,
        sim=args.sim,
        allow_shutdown=args.allow_shutdown,
    )
    service.start()

    stop = False
    def _sig(signo, frame):
        nonlocal stop
        log.info("Received signal %s, shutting down", signo)
        stop = True
        service.shutdown()
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        while not stop and service.is_alive():
            time.sleep(0.5)
    finally:
        service.join(timeout=5.0)
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
