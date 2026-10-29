"""Independent reader checks for images consumed by TempleOS's own driver."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "build_media.py"
spec = importlib.util.spec_from_file_location("build_media", SCRIPT)
assert spec and spec.loader
media = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = media
spec.loader.exec_module(media)


def read_tree(image: bytes, root: int, prefix: str = "") -> dict[str, bytes]:
    """Read using the disk struct fields, independent of the writer's Node tree."""
    record = struct.Struct("<H38sQQQ")
    _, _, self_cluster, directory_size, _ = record.unpack_from(image, root * 512)
    if self_cluster != root or directory_size % 512:
        raise AssertionError("malformed directory self entry")
    result = {}
    for pos in range(root * 512 + 128, root * 512 + directory_size, 64):
        attr, raw_name, cluster, size, _ = record.unpack_from(image, pos)
        name = raw_name.split(b"\0", 1)[0].decode("ascii")
        if not name:
            return result
        path = prefix + "/" + name
        if attr & 0x10:
            result.update(read_tree(image, cluster, path))
        else:
            result[path] = image[cluster * 512:cluster * 512 + size]
    raise AssertionError("missing zero directory terminator")


class RedSeaMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "HolyC"
        self.source.mkdir()
        (self.source / "Load.HC").write_bytes(b'#include "Main.HC";\n')
        (self.source / "Main.HC").write_bytes(b'U0 Hello(){"Hello!\\n";}\n')

    def header(self, image: bytes, offset: int) -> tuple[int, int]:
        signature = image[offset * 512 + 3]
        absolute, sectors, root, bitmaps = struct.unpack_from("<QQQQ", image, offset * 512 + 8)
        self.assertEqual(signature, 0x88)
        self.assertEqual(absolute, offset)
        self.assertEqual(image[offset * 512 + 510:offset * 512 + 512], b"\x55\xaa")
        self.assertEqual(sectors * 512, len(image) - offset * 512)
        self.assertGreaterEqual(bitmaps * 4096, sectors)
        self.assertGreater(root, offset + bitmaps)
        return root, sectors

    def test_iso_layout_recognized_by_templeos_iso_init(self) -> None:
        image = media.build_image(self.source)
        self.assertEqual(len(image) % 2048, 0)
        for sector, kind in ((16, 1), (17, 0), (18, 2), (19, 255)):
            self.assertEqual(image[sector * 2048:sector * 2048 + 7], bytes([kind]) + b"CD001\x01")
        # ISOInit starts at block 19*4 and adds 12 for the boot descriptor.
        offset = 19 * 4 + 12
        root, _ = self.header(image, offset)
        # Offsets from TempleOS's packed CISOPriDesc, also observed in the
        # official 5.03 boot ISO. Standard ISO9660 offsets are wrong here.
        self.assertEqual(image[18 * 2048 + 314:18 * 2048 + 330], b"TempleOS RedSea\0")
        self.assertEqual(struct.unpack_from("<I", image, 18 * 2048 + 152)[0], root)
        self.assertEqual(image[18 * 2048 + 877], 1)
        self.assertEqual(struct.unpack_from("<I", image, 18 * 2048 + 80)[0], len(image) // 2048)
        self.assertEqual(struct.unpack_from(">I", image, 18 * 2048 + 84)[0], len(image) // 2048)
        self.assertEqual(sum(struct.unpack_from("<16H", image, 20 * 2048)) & 0xFFFF, 0)
        self.assertEqual(image[20 * 2048 + 32], 0, "app image must not claim to boot")
        self.assertEqual(read_tree(image, root)["/Signal/Main.HC"], (self.source / "Main.HC").read_bytes())

    def test_nested_directory_multiple_sectors_and_empty_file(self) -> None:
        nested = self.source / "Tests"
        nested.mkdir()
        expected = {"/Signal/Load.HC": (self.source / "Load.HC").read_bytes(),
                    "/Signal/Main.HC": (self.source / "Main.HC").read_bytes()}
        for i in range(24):
            data = bytes([i]) * (i * 200)
            (nested / f"Test{i:02d}.HC").write_bytes(data)
            expected[f"/Signal/Tests/Test{i:02d}.HC"] = data
        image = media.build_image(self.source)
        root, _ = self.header(image, 88)
        self.assertEqual(read_tree(image, root), expected)

    def test_ata_partition_is_correct_and_readable(self) -> None:
        image = media.build_image(self.source, "disk")
        flag, _, kind, _, offset, sectors = struct.unpack_from("<B3sB3sII", image, 446)
        self.assertEqual((flag, kind, offset), (0, 0x88, 2048))
        self.assertEqual(image[510:512], b"\x55\xaa")
        root, actual_sectors = self.header(image, offset)
        self.assertEqual(sectors, actual_sectors)
        self.assertEqual(len(read_tree(image, root)), 2)

    def test_reproducible_independent_of_host_mtime(self) -> None:
        first = media.build_image(self.source, epoch=1767225600)
        (self.source / "Main.HC").touch()
        self.assertEqual(first, media.build_image(self.source, epoch=1767225600))

    def test_long_names_rejected_without_truncation(self) -> None:
        (self.source / ("x" * 38)).write_text("x")
        with self.assertRaisesRegex(ValueError, "file name"):
            media.build_image(self.source)

    def test_symlinks_rejected(self) -> None:
        (self.source / "Alias.HC").symlink_to(self.source / "Load.HC")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            media.build_image(self.source)

    def test_empty_source_rejected(self) -> None:
        source = Path(self.temp.name) / "Empty"
        source.mkdir()
        with self.assertRaisesRegex(ValueError, "empty"):
            media.build_image(source)

    def test_cdate_matches_original_year_start_calculation(self) -> None:
        self.assertEqual(media.temple_date(0), 719527 << 32)
        self.assertEqual(media.temple_date(43200), (719527 << 32) | 0x80000000)


if __name__ == "__main__":
    unittest.main()
