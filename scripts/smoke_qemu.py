#!/usr/bin/env python3
"""Compile and exercise the real HolyC client in a headless TempleOS VM.

The harness uses only the offline demo backend. It downloads nothing, never
links a Signal account, and requires a locally supplied official TempleOS ISO.
QEMU and Python 3.11+ are the only host dependencies.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

TESTED_ISO_SHA256 = "5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26"
PASS_MARKERS = (b"TS SELFTEST PASS\n", b"TS SERIAL PASS\n", b"TS SMOKE PASS\n")
MAX_QMP_LINE = 1024 * 1024


class QMP:
    def __init__(self, path: Path, key_delay: float = 0.1):
        self.path = path
        self.key_delay = key_delay
        self.sock: socket.socket | None = None
        self.stream = None
        self.sequence = 0

    def __enter__(self) -> "QMP":
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        try:
            self.sock.connect(str(self.path))
            self.stream = self.sock.makefile("rwb", buffering=0)
            greeting = self.read()
            if "QMP" not in greeting:
                raise RuntimeError("QEMU did not provide a QMP greeting")
            self.command("qmp_capabilities")
            return self
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(self) -> dict:
        if self.stream is None:
            raise RuntimeError("QMP stream is not connected")
        line = self.stream.readline(MAX_QMP_LINE + 1)
        if not line:
            raise RuntimeError("QEMU closed the monitor connection")
        if len(line) > MAX_QMP_LINE or not line.endswith(b"\n"):
            raise RuntimeError("QMP response exceeded the frame limit")
        data = json.loads(line)
        if not isinstance(data, dict):
            raise RuntimeError("QMP response was not an object")
        return data

    def command(self, name: str, arguments: dict | None = None):
        self.sequence += 1
        request: dict = {"execute": name, "id": self.sequence}
        if arguments is not None:
            request["arguments"] = arguments
        assert self.stream is not None
        self.stream.write(json.dumps(request).encode("ascii") + b"\n")
        while True:
            response = self.read()
            if "event" in response:
                continue
            if response.get("id") != self.sequence:
                raise RuntimeError("QMP response ID mismatch")
            if "error" in response:
                raise RuntimeError(f"QMP error: {response['error']}")
            return response.get("return")

    def key(self, name: str) -> None:
        # Explicit key releases avoid HMP sendkey's timer-driven modifier races
        # when TCG guest time runs more slowly than the host clock.
        keys = name.split("-")
        self.command("input-send-event", {"events": [
            {"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": key}}}
            for key in keys
        ]})
        time.sleep(self.key_delay)
        self.command("input-send-event", {"events": [
            {"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": key}}}
            for key in reversed(keys)
        ]})
        time.sleep(self.key_delay)

    def type(self, text: str) -> None:
        symbols = {
            " ": "spc", "\n": "ret", "\t": "tab", ";": "semicolon",
            ":": "shift-semicolon", '"': "shift-apostrophe", "'": "apostrophe",
            "/": "slash", "?": "shift-slash", ".": "dot", ",": "comma",
            "(": "shift-9", ")": "shift-0", "[": "bracket_left",
            "]": "bracket_right", "{": "shift-bracket_left", "}": "shift-bracket_right",
            "#": "shift-3", "_": "shift-minus", "-": "minus", "=": "equal",
            "+": "shift-equal", "*": "shift-8", "&": "shift-7", "!": "shift-1",
            "$": "shift-4", "\\": "backslash", "<": "shift-comma", ">": "shift-dot",
            "|": "shift-backslash",
        }
        for char in text:
            if char in symbols:
                key = symbols[char]
            elif char.isascii() and char.isalnum():
                key = ("shift-" + char.lower()) if char.isupper() else char
            else:
                raise ValueError(f"unsupported guest keyboard character: {char!r}")
            self.key(key)

    def focus_left_shell(self) -> None:
        self.command("input-send-event", {"events": [
            {"type": "rel", "data": {"axis": "x", "value": -200}},
            {"type": "rel", "data": {"axis": "y", "value": -80}},
        ]})
        for down in (True, False):
            self.command("input-send-event", {"events": [
                {"type": "btn", "data": {"button": "left", "down": down}},
            ]})
        time.sleep(0.2)

    def screenshot(self, path: Path) -> None:
        self.command("screendump", {"filename": str(path)})


def wait_path(path: Path, process: subprocess.Popen, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{description} exited with status {process.returncode}; see logs")
        if path.exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {description}")


def stop_process_group(process: subprocess.Popen | None) -> None:
    """Terminate only this harness's process session, including QEMU's wrapper."""
    if process is None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def copy_results(runtime: Path, output: Path) -> None:
    for name in ("bridge.log", "qemu.log", "guest.log", "compile.ppm", "failure.ppm", "success.ppm", "ui.ppm"):
        source = runtime / name
        if source.is_file():
            shutil.copyfile(source, output / name)


def run(args: argparse.Namespace, root: Path, output: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="tssmoke-", dir="/tmp") as directory:
        runtime = Path(directory)
        os.chmod(runtime, 0o700)
        app_iso = runtime / "app.ISO"
        serial = runtime / "serial.sock"
        monitor = runtime / "qmp.sock"
        guest_log = runtime / "guest.log"
        bridge: subprocess.Popen | None = None
        emulator: subprocess.Popen | None = None
        qmp: QMP | None = None
        passed = False
        with (runtime / "bridge.log").open("wb") as bridge_log, (runtime / "qemu.log").open("wb") as qemu_log:
            try:
                subprocess.run(
                    [sys.executable, str(root / "scripts/build_media.py"), "--output", str(app_iso)],
                    cwd=root, check=True, timeout=30,
                )
                bridge = subprocess.Popen(
                    [sys.executable, "-m", "temple_signal", "--demo", "--socket", str(serial)],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=bridge_log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                wait_path(serial, bridge, 10, "demo bridge")
                emulator = subprocess.Popen(
                    [sys.executable, str(root / "scripts/run_qemu.py"),
                     "--templeos-iso", str(args.templeos_iso), "--app-iso", str(app_iso),
                     "--socket", str(serial), "--headless", "--qmp", str(monitor),
                     "--debug-log", str(guest_log), "--qemu", args.qemu, "--accel", args.accel],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=qemu_log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                wait_path(monitor, emulator, 10, "QEMU monitor")
                qmp = QMP(monitor, args.key_delay).__enter__()
                print(f"Booting original TempleOS ({args.boot_wait:g}s wait)...", flush=True)
                # Short intervals let Ctrl+C interrupt promptly.
                deadline = time.monotonic() + args.boot_wait
                while time.monotonic() < deadline:
                    if emulator.poll() is not None:
                        raise RuntimeError("QEMU exited while booting")
                    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                qmp.focus_left_shell()
                qmp.key("n")
                time.sleep(0.3)
                qmp.key("n")
                time.sleep(0.5)
                print("Mounting the RedSea application disc and invoking native HolyC tests...", flush=True)
                qmp.type(
                    "CBlkDev *ts_app_cd=BlkDevNextFreeSlot('U',BDT_ATAPI);"
                    "ts_app_cd->base0=0x170;ts_app_cd->base1=0;ts_app_cd->unit=1;"
                    'BlkDevAdd(ts_app_cd,,FALSE,TRUE);Cd("U:/Signal");\n'
                )
                time.sleep(0.5)
                # ExeFile still runs TempleOS's original compiler. ExePutS
                # catches compiler errors internally, so an outer catch cannot
                # detect them. A fixed end marker makes missing PASS markers
                # fail promptly after compilation or test execution returns.
                qmp.type(
                    'ExeFile("Test.HC");U8 *ts_done="TS HARNESS END\\n";'
                    'while(*ts_done)OutU8(0xE9,*ts_done++);\n'
                )
                time.sleep(2)
                qmp.screenshot(runtime / "compile.ppm")
                shutil.copyfile(runtime / "compile.ppm", output / "compile.ppm")
                print(f"Native compiler framebuffer: {output / 'compile.ppm'}", flush=True)
                deadline = time.monotonic() + args.timeout
                shown = set()
                while time.monotonic() < deadline:
                    if emulator.poll() is not None:
                        raise RuntimeError("QEMU exited before guest tests completed")
                    if bridge.poll() is not None:
                        raise RuntimeError("demo bridge exited before guest tests completed")
                    data = guest_log.read_bytes() if guest_log.exists() else b""
                    if len(data) > MAX_QMP_LINE:
                        raise RuntimeError("guest debug output exceeded 1 MiB")
                    for marker in PASS_MARKERS:
                        if marker in data and marker not in shown:
                            print(marker.decode("ascii").strip(), flush=True)
                            shown.add(marker)
                    if b"FAIL" in data or b"REQUIRES DEMO" in data:
                        raise RuntimeError("native guest tests reported failure")
                    if all(marker in data for marker in PASS_MARKERS):
                        qmp.screenshot(runtime / "success.ppm")
                        if not args.skip_ui_capture:
                            print("Opening the compiled HolyC application for a real UI capture...", flush=True)
                            qmp.type("I64 ts_ui_before=ts_received;TSSignal;\n")
                            time.sleep(3)
                            qmp.key("f4")
                            qmp.type("Hello from HolyC!\n")
                            time.sleep(3)
                            qmp.screenshot(runtime / "ui.ppm")
                            qmp.key("esc")
                            time.sleep(1)
                            qmp.type(
                                'if(ts_received>ts_ui_before)TSDebug("TS UI PASS\\n");'
                                'else TSDebug("TS UI FAIL\\n");\n'
                            )
                            ui_deadline = time.monotonic() + 10
                            while time.monotonic() < ui_deadline:
                                ui_data = guest_log.read_bytes()
                                if b"TS UI FAIL\n" in ui_data:
                                    raise RuntimeError("native keyboard send/receive failed")
                                if b"TS UI PASS\n" in ui_data:
                                    print("TS UI PASS", flush=True)
                                    break
                                time.sleep(0.2)
                            else:
                                raise RuntimeError("native keyboard verification did not finish")
                        passed = True
                        break
                    if b"TS HARNESS END\n" in data:
                        raise RuntimeError("native compiler or tests returned without every PASS marker")
                    time.sleep(0.2)
                if not passed:
                    raise TimeoutError("native guest test timed out; inspect failure.ppm for compiler output")
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                print(f"SMOKE FAILED: {exc}", file=sys.stderr, flush=True)
                if qmp is not None:
                    with contextlib.suppress(Exception):
                        qmp.screenshot(runtime / "failure.ppm")
            finally:
                if qmp is not None:
                    with contextlib.suppress(Exception):
                        qmp.command("quit")
                    qmp.close()
                stop_process_group(emulator)
                stop_process_group(bridge)
                bridge_log.flush()
                qemu_log.flush()
                copy_results(runtime, output)
        return passed


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templeos-iso", required=True, type=Path)
    parser.add_argument("--iso-sha256", default=TESTED_ISO_SHA256,
                        help="expected boot ISO digest; defaults to the documented official image")
    parser.add_argument("--boot-wait", type=float, default=20.0,
                        help="seconds to allow original TempleOS to reach startup prompts")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="maximum seconds waiting for native test results after input")
    parser.add_argument("--key-delay", type=float, default=0.1,
                        help="seconds to hold and release each explicit QMP key (default 0.1 each)")
    parser.add_argument("--output-dir", type=Path, help="new directory for logs and guest framebuffer")
    parser.add_argument("--skip-ui-capture", action="store_true",
                        help="skip the native keyboard round-trip check and UI capture")
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    args = parser.parse_args()
    args.templeos_iso = args.templeos_iso.expanduser().resolve()
    if not args.templeos_iso.is_file():
        parser.error("the supplied TempleOS ISO does not exist")
    if not shutil.which(args.qemu):
        parser.error("qemu-system-x86_64 is not installed")
    if not 1 <= args.boot_wait <= 60 or not 1 <= args.timeout <= 300 or not 0.05 <= args.key_delay <= 1:
        parser.error("boot-wait must be 1..60, timeout 1..300, and key-delay 0.05..1")
    with args.templeos_iso.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != args.iso_sha256.lower():
        parser.error(f"boot ISO SHA-256 mismatch: got {actual}")
    if args.output_dir is None:
        base = root / "build" / "smoke"
        base.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    else:
        output = args.output_dir.expanduser().absolute()
        try:
            output.mkdir(parents=True, mode=0o700)
        except FileExistsError:
            parser.error("--output-dir already exists; choose a fresh directory")
    os.umask(0o077)
    print(f"Artifacts: {output}", flush=True)
    print(f"TempleOS SHA-256: {actual}", flush=True)
    try:
        passed = run(args, root, output)
    except KeyboardInterrupt:
        print("Smoke test interrupted; owned child processes were stopped.", file=sys.stderr)
        return 130
    if passed:
        print("PASS: native HolyC compile, self-tests and demo serial integration.", flush=True)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
