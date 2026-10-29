"""Wire format tests use corrupt byte streams, bounds and documented vectors."""

from __future__ import annotations

import unittest

from temple_signal.protocol import (
    Frame,
    LineFramer,
    MAX_FIELD,
    MAX_FRAME,
    MAX_ID,
    ProtocolError,
    clipped_text,
    crc16,
    decode,
    encode,
)


def checked_payload(payload: bytes) -> bytes:
    """Build a checksum-valid packet to test the parser beneath its CRC gate."""
    return payload + f"\t{crc16(payload):04X}\n".encode("ascii")


class ProtocolTests(unittest.TestCase):
    def test_crc_check_vector(self):
        self.assertEqual(crc16(b"123456789"), 0x29B1)
        self.assertEqual(crc16(b""), 0xFFFF)

    def test_roundtrip_preserves_utf8_and_literal_markup(self):
        original = Frame(123, "SEND", ("D", "+12025550100", '$MA,"plain text"$\tשלום\n"100%"'))
        encoded = encode(original)
        self.assertEqual(encoded.count(b"\n"), 1)
        self.assertNotIn(b"$", encoded)
        self.assertEqual(decode(encoded), original)
        self.assertEqual(decode(encode(Frame(MAX_ID, "EVENT", ("", "")))), Frame(MAX_ID, "EVENT", ("", "")))

    def test_single_byte_corruption_and_invalid_checksums_fail(self):
        valid = encode(Frame(1, "SEND", ("D", "+12025550100", "SENSITIVE TEXT")))
        damaged = bytearray(valid)
        damaged[8] ^= 1
        for bad in [bytes(damaged), valid[:-6] + b"\tzzzz\n", valid[:-1], valid[:-1] + b"\r\n", b"\xff\n", b"missing checksum\n"]:
            with self.subTest(packet=bad[:10]):
                with self.assertRaises(ProtocolError) as caught:
                    decode(bad)
                self.assertNotIn("SENSITIVE", str(caught.exception))

    def test_checksum_valid_malformed_fields_fail(self):
        payloads = [
            b"TS0\t1\tPING",
            b"TS1\t01\tPING",
            b"TS1\t+1\tPING",
            b"TS1\t-1\tPING",
            b"TS1\t2147483648\tPING",
            b"TS1\t1\tping",
            b"TS1\t1\tA123",
            b"TS1\t1\t" + b"A" * 17,
            b"TS1\t1\tPING\t",
            b"TS1\t1\tPING\t2a",
            b"TS1\t1\tPING\tA",
            b"TS1\t1\tPING\tGG",
            b"TS1\t1\tPING\t00",
            b"TS1\t1\tPING\tC0AF",
            b"TS1\t1\tPING\tEDA080",
            b"TS1\t1\tPING" + b"\t-" * 11,
            b"TS1\t1\tPING\t" + b"41" * (MAX_FIELD + 1),
            b"TS1\t1\tPI\rNG",
        ]
        for payload in payloads:
            with self.subTest(header=payload[:40]):
                with self.assertRaises(ProtocolError):
                    decode(checked_payload(payload))

    def test_encoding_rejects_invalid_or_oversize_messages(self):
        bad_frames = [
            Frame(-1, "PING"), Frame(MAX_ID + 1, "PING"), Frame(1.5, "PING"),
            Frame(1, "lower"), Frame(1, "X", ("\0",)),
            Frame(1, "X", ("é" * (MAX_FIELD // 2 + 1),)),
            Frame(1, "X", tuple("" for _ in range(11))),
            Frame(1, "X", ("x" * MAX_FIELD, "y" * MAX_FIELD)),
        ]
        for frame in bad_frames:
            with self.subTest(operation=frame.op):
                with self.assertRaises(ProtocolError):
                    encode(frame)

    def test_exact_4096_byte_boundary(self):
        frame = Frame(1, "PING", ("a" * 1024, "b" * 1015))
        packet = encode(frame)
        self.assertEqual(len(packet), MAX_FRAME)
        self.assertEqual(decode(packet), frame)
        framer = LineFramer()
        self.assertEqual(framer.feed(packet), [packet])
        with self.assertRaises(ProtocolError):
            decode(packet[:-1] + b"a\n")

    def test_arbitrary_chunks_and_multiple_packets(self):
        packets = [encode(Frame(n, "POLL", (str(n - 1),))) for n in range(1, 8)]
        stream = b"".join(packets)
        for step in [1, 2, 7, 4096]:
            framer = LineFramer()
            actual = []
            for offset in range(0, len(stream), step):
                actual.extend(framer.feed(stream[offset:offset + step]))
            self.assertEqual(actual, packets)

    def test_overlong_stream_is_bounded_and_resynchronizes(self):
        framer = LineFramer()
        valid = encode(Frame(10, "PING"))
        self.assertEqual(framer.feed(b"x" * MAX_FRAME), [])
        self.assertTrue(framer.discarding)
        self.assertEqual(framer.buffer, bytearray())
        for _ in range(20):
            self.assertEqual(framer.feed(b"x" * 1024), [])
            self.assertEqual(len(framer.buffer), 0)
        self.assertEqual(framer.feed(b"\n" + valid), [None, valid])
        self.assertFalse(framer.discarding)

    def test_display_clipping_removes_controls_and_preserves_utf8_bounds(self):
        self.assertEqual(clipped_text("a\x00b\x1bc\x7f\n\t", 100), "a?b?c?\n\t")
        clipped = clipped_text("é" * 100, 17, " [cut]")
        self.assertLessEqual(len(clipped.encode("utf-8")), 17)
        self.assertTrue(clipped.endswith(" [cut]"))
        self.assertEqual(clipped_text(None, 10), "")
        self.assertEqual(clipped_text("surrogate\ud800", 100), "surrogate?")


if __name__ == "__main__":
    unittest.main()
