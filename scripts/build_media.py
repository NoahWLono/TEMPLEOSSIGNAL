#!/usr/bin/env python3
"""Package HolyC sources as a deterministic, read-only TempleOS RedSea image.

This is a data image, not a bootable TempleOS distribution. Unlike a regular
ISO9660 disc, its filesystem is readable by unmodified TempleOS 5.03.
The layout follows the public-domain TempleOS source documented in
docs/UPSTREAM.md. No third-party Python dependencies or root mounts are needed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import struct
import tempfile
from dataclasses import dataclass, field

SECTOR = 512
CD_SECTOR = 2048
REDSEA_CD_OFFSET = 88
REDSEA_HD_OFFSET = 2048
CONTIGUOUS = 0x800
DIRECTORY = 0x10
MAX_CONTENT = 32 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9_.+-]{1,37}\Z")


@dataclass
class Node:
    name: str
    data: bytes | None = None
    children: list["Node"] = field(default_factory=list)
    cluster: int = 0
    size: int = 0

    @property
    def is_dir(self) -> bool:
        return self.data is None

    @property
    def sectors(self) -> int:
        if self.is_dir:
            # Self, parent, children, and a zero entry terminating the directory.
            return max(1, ((len(self.children) + 3) * 64 + SECTOR - 1) // SECTOR)
        return max(1, (len(self.data or b"") + SECTOR - 1) // SECTOR)


def read_source(source: Path) -> Node:
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source must be a directory, not a symbolic link")
    content_size = 0

    def walk(path: Path, name: str) -> Node:
        nonlocal content_size
        if not NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError(f"unsupported TempleOS file name: {name!r}")
        if path.is_symlink():
            raise ValueError(f"symbolic links are not packaged: {path.name}")
        if path.is_dir():
            return Node(name, children=[walk(p, p.name) for p in sorted(path.iterdir())])
        if not path.is_file():
            raise ValueError(f"not a regular source file: {path.name}")
        if path.stat().st_size > MAX_CONTENT - content_size:
            raise ValueError("source tree exceeds 32 MiB")
        data = path.read_bytes()
        content_size += len(data)
        if content_size > MAX_CONTENT:
            raise ValueError("source tree exceeds 32 MiB")
        return Node(name, data=data)

    app = walk(source, "Signal")
    if not app.children:
        raise ValueError("source directory is empty")
    return Node(".", children=[app])


def flatten(node: Node) -> list[Node]:
    result = [node]
    for child in node.children:
        result.extend(flatten(child))
    return result


def temple_date(epoch: int) -> int:
    """CDate: days since year 0 in high word, fraction of day in low word."""
    value = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    if not 1970 <= value.year < 4000:
        raise ValueError("SOURCE_DATE_EPOCH must lie between 1970 and 3999")
    year = value.year
    previous = year - 1
    year_start = year * 365 + previous // 4 - previous // 100 + previous // 400
    days = year_start + value.timetuple().tm_yday - 1
    seconds = value.hour * 3600 + value.minute * 60 + value.second
    return (days << 32) | ((seconds << 32) // 86400)


def directory_record(name: str, node: Node, timestamp: int, size: int | None = None) -> bytes:
    attr = CONTIGUOUS | (DIRECTORY if node.is_dir else 0)
    return struct.pack(
        "<H38sQQQ", attr, name.encode("ascii"), node.cluster,
        node.size if size is None else size, timestamp,
    )


def _dual16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _dual32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _iso_headers(image: bytearray, root_cluster: int) -> None:
    # TempleOS ISOInit recognizes this marker in the supplementary descriptor.
    # A boot descriptor adds 12 512-byte blocks to the base offset (19 * 4).
    # The boot catalog is explicitly NONBOOTABLE: use the official OS ISO too.
    size = len(image) // CD_SECTOR
    primary = bytearray(CD_SECTOR)
    primary[0:7] = b"\x01CD001\x01"
    primary[80:88] = _dual32(size)
    primary[120:124] = _dual16(1)
    primary[124:128] = _dual16(1)
    primary[128:132] = _dual16(CD_SECTOR)
    # TempleOS's packed CISOPriDesc differs from the standard ISO9660 layout:
    # root_dir_record=152, publisher_id=314, file_structure_version=877.
    # These offsets also match bytes in the original TempleOS 5.03 boot ISO.
    primary[152:160] = _dual32(root_cluster)
    marker = b"TempleOS RedSea\0"
    primary[314:314 + len(marker)] = marker
    primary[877] = 1
    image[16 * CD_SECTOR:17 * CD_SECTOR] = primary
    boot = bytearray(CD_SECTOR)
    boot[0:7] = b"\x00CD001\x01"
    identifier = b"EL TORITO SPECIFICATION\0"
    boot[7:7 + len(identifier)] = identifier
    struct.pack_into("<I", boot, 0x47, 20)
    image[17 * CD_SECTOR:18 * CD_SECTOR] = boot
    supplementary = bytearray(primary)
    supplementary[0] = 2
    image[18 * CD_SECTOR:19 * CD_SECTOR] = supplementary
    image[19 * CD_SECTOR:19 * CD_SECTOR + 7] = b"\xffCD001\x01"
    catalog = bytearray(CD_SECTOR)
    struct.pack_into("<H", catalog, 0, 1)
    catalog[4:12] = b"TempleOS"
    struct.pack_into("<H", catalog, 30, 0xAA55)
    words = struct.unpack_from("<16H", catalog)
    struct.pack_into("<H", catalog, 28, (-sum(words)) & 0xFFFF)
    # Initial/default entry byte 32 remains zero: not bootable.
    image[20 * CD_SECTOR:21 * CD_SECTOR] = catalog


def build_image(source: Path, kind: str = "iso", epoch: int = 0) -> bytes:
    if kind not in {"iso", "disk"}:
        raise ValueError("image format must be iso or disk")
    root = read_source(source)
    nodes = flatten(root)
    timestamp = temple_date(epoch)
    offset = REDSEA_CD_OFFSET if kind == "iso" else REDSEA_HD_OFFSET
    payload = sum(n.sectors for n in nodes)
    bitmap_sectors = 1
    while True:
        total = offset + 1 + bitmap_sectors + payload
        total = ((total + 3) // 4) * 4
        partition_sectors = total - offset
        needed = (partition_sectors + 4095) // 4096
        if needed == bitmap_sectors:
            break
        bitmap_sectors = needed

    next_cluster = offset + 1 + bitmap_sectors
    for node in nodes:
        node.cluster = next_cluster
        node.size = node.sectors * SECTOR if node.is_dir else len(node.data or b"")
        next_cluster += node.sectors

    image = bytearray(total * SECTOR)
    fingerprint = hashlib.sha256()
    for node in nodes:
        fingerprint.update(node.name.encode("ascii") + b"\0")
        fingerprint.update(node.data or b"")
    unique_id = struct.unpack("<Q", fingerprint.digest()[:8])[0]
    header = struct.pack(
        "<3sB4sQQQQQ462sH", b"\0" * 3, 0x88, b"\0" * 4,
        offset, partition_sectors, root.cluster, bitmap_sectors, unique_id,
        b"\0" * 462, 0xAA55,
    )
    image[offset * SECTOR:(offset + 1) * SECTOR] = header
    # CD media is read-only. Reserve every block, including final padding and
    # non-existent tail bits, so accidentally mounting the disk writable cannot
    # allocate over files. This image is an immutable source distribution.
    image[(offset + 1) * SECTOR:(offset + 1 + bitmap_sectors) * SECTOR] = (
        b"\xff" * (bitmap_sectors * SECTOR)
    )

    def write_node(node: Node, parent: Node) -> None:
        start = node.cluster * SECTOR
        if node.is_dir:
            data = directory_record(".", node, timestamp)
            data += directory_record("..", parent, timestamp, size=0)
            data += b"".join(directory_record(n.name, n, timestamp) for n in node.children)
            image[start:start + len(data)] = data
            for child in node.children:
                write_node(child, node)
        else:
            image[start:start + node.size] = node.data or b""

    write_node(root, root)
    if kind == "iso":
        _iso_headers(image, root.cluster)
    else:
        # Nonbootable MBR, one RedSea partition. Geometry is deliberately LBA.
        struct.pack_into(
            "<B3sB3sII", image, 446, 0, b"\xfe\xff\xff", 0x88,
            b"\xfe\xff\xff", offset, partition_sectors,
        )
        image[510:512] = b"\x55\xaa"
    return bytes(image)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "TempleOS")
    parser.add_argument("--output", type=Path, default=root / "build" / "TEMPLEOS_SIGNAL.ISO")
    parser.add_argument("--format", choices=("iso", "disk"), default="iso")
    parser.add_argument("--force", action="store_true", help="replace an existing output image")
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    if output.exists() and not args.force:
        parser.error(f"output exists; use --force to replace: {output}")
    if output.is_symlink():
        parser.error("output must not be a symbolic link")
    try:
        image = build_image(args.source, args.format, int(os.environ.get("SOURCE_DATE_EPOCH", "0")))
    except (OSError, ValueError, OverflowError) as exc:
        parser.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    # A temporary file avoids leaving a half-written artifact on interruption.
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".media-", delete=False) as stream:
            temp_name = stream.name
            stream.write(image)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, output)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"{hashlib.sha256(image).hexdigest()}  {output.name}")
    print(f"Created {output} ({len(image)} bytes); application path: /Signal/Load.HC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
