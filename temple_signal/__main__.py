"""Run the local serial bridge, in explicit demo or Signal mode."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import signal
import sys

from . import __version__
from .bridge import BridgeServer
from .demo import DemoBackend
from .signal_backend import BackendError, SignalBackend


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="offline echo backend; never contacts Signal")
    mode.add_argument("--account", help="linked signal-cli account: E.164 number or ACI UUID")
    result.add_argument("--socket", type=Path, default=Path(f"/tmp/temple-signal-{os.getuid()}") / "serial.sock",
                        help="COM1 UNIX socket; parent must be owned by you with mode 0700")
    result.add_argument("--signal-cli", default="signal-cli", help="path to the separately installed signal-cli executable")
    result.add_argument("--data-dir", type=Path, help="existing signal-cli data directory; default is upstream's default")
    result.add_argument("--version", action="version", version=f"temple-signal {__version__}")
    return result


async def run(args: argparse.Namespace) -> None:
    backend = (DemoBackend() if args.demo else
               SignalBackend(args.account, executable=args.signal_cli, data_dir=args.data_dir))
    bridge = BridgeServer(backend, args.socket.expanduser())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await bridge.start()
        print(f"Temple Signal {__version__}: {'DEMO (offline)' if args.demo else 'SIGNAL'}", flush=True)
        print(f"Serial socket: {bridge.path}", flush=True)
        print("Waiting for TempleOS COM1. Press Ctrl+C to stop.", flush=True)
        await stop.wait()
    finally:
        await bridge.close()


def main() -> int:
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except (BackendError, OSError, ValueError) as exc:
        # Only intentionally safe backend errors or local setup errors are printed.
        print(f"temple-signal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
