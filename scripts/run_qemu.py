#!/usr/bin/env python3
"""Launch unmodified TempleOS with a local serial bridge and RedSea app CD."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess


def qemu_value(value: str | Path) -> str:
    # QEMU separates suboptions with commas, independently of shell quoting.
    return str(value).replace(",", ",,")


def private_parent(path: Path) -> None:
    parent = path.parent
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"not a directory: {parent}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError(f"socket directory must be owned by this user: {parent}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError(f"socket directory must be private (chmod 700): {parent}")
    if len(os.fsencode(path)) > 100:
        raise ValueError("UNIX socket path is too long; use a short private directory in /tmp")


def command(args: argparse.Namespace) -> list[str]:
    result = [
        args.qemu, "-name", "TempleOS Signal", "-machine", "pc",
        "-accel", args.accel, "-cpu", "qemu64", "-m", str(args.memory),
        "-smp", "1", "-nic", "none", "-rtc", "base=localtime",
        "-boot", "order=c" if args.boot_disk else "order=d",
        "-drive", f"file={qemu_value(args.templeos_iso)},format=raw,media=cdrom,if=ide,index=2,readonly=on",
        "-drive", f"file={qemu_value(args.app_iso)},format=raw,media=cdrom,if=ide,index=3,readonly=on",
        "-chardev", f"socket,id=signal,path={qemu_value(args.socket)},reconnect=1",
        "-serial", "chardev:signal", "-serial", "null", "-monitor", "none",
    ]
    if args.disk:
        result.extend([
            "-drive",
            f"file={qemu_value(args.disk)},format={args.disk_format},if=ide,index=0"
            + ("" if args.write_disk else ",snapshot=on"),
        ])
    if args.headless:
        result.extend(["-display", "none"])
    if args.qmp:
        result.extend(["-qmp", f"unix:{qemu_value(args.qmp)},server=on,wait=off"])
    if args.debug_log:
        result.extend(["-debugcon", "file:" + str(args.debug_log), "-global", "isa-debugcon.iobase=0xe9"])
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templeos-iso", required=True, type=Path, help="official bootable TempleOS ISO")
    parser.add_argument("--app-iso", type=Path, default=root / "build" / "TEMPLEOS_SIGNAL.ISO")
    parser.add_argument("--socket", required=True, type=Path, help="bridge's local UNIX serial socket")
    parser.add_argument("--disk", type=Path, help="optional existing TempleOS hard disk image")
    parser.add_argument("--disk-format", choices=("raw", "qcow2"), default="qcow2")
    parser.add_argument("--boot-disk", action="store_true", help="boot the supplied disk instead of live CD")
    parser.add_argument("--write-disk", action="store_true", help="persist disk changes; default uses a temporary snapshot")
    parser.add_argument("--headless", action="store_true", help="no display; use --qmp for automated guest tests")
    parser.add_argument("--qmp", type=Path, help="optional private QEMU monitor socket")
    parser.add_argument("--debug-log", type=Path, help="optional guest port 0xE9 test output (contains no chat text)")
    parser.add_argument("--memory", type=int, default=512, help="guest RAM in MiB (default 512)")
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--dry-run", action="store_true", help="print command without starting QEMU")
    args = parser.parse_args()
    for name in ("templeos_iso", "app_iso", "socket", "disk", "qmp", "debug_log"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser().absolute())
    for path in (args.templeos_iso, args.app_iso, args.disk):
        if path is not None and not path.is_file():
            parser.error(f"image file does not exist: {path}")
    if args.boot_disk and not args.disk:
        parser.error("--boot-disk requires --disk")
    if args.write_disk and not args.disk:
        parser.error("--write-disk requires --disk")
    if not 256 <= args.memory <= 32768:
        parser.error("--memory must be between 256 and 32768 MiB")
    if not args.dry_run and not shutil.which(args.qemu):
        parser.error("QEMU is missing; install qemu-system-x86 (Linux) or qemu (macOS)")
    try:
        private_parent(args.socket)
        if args.qmp:
            private_parent(args.qmp)
            if args.qmp.exists():
                raise ValueError("QMP socket already exists; use a fresh path")
        if args.debug_log:
            if args.debug_log.exists() or args.debug_log.is_symlink():
                raise ValueError("debug log already exists; use a fresh output path")
            private_parent(args.debug_log)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    argv = command(args)
    if args.dry_run:
        print(shlex.join(argv))
        return 0
    os.umask(0o077)
    print("Guest networking: disabled. COM1: local Signal bridge.", flush=True)
    print('Load app with Cd("U:/Signal"); #include "Load.HC"', flush=True)
    try:
        return subprocess.call(argv)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        parser.error(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
