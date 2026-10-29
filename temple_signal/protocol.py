"""Bounded ASCII serial framing. See PROTOCOL.md for the wire contract."""
from __future__ import annotations

import binascii
import re
from dataclasses import dataclass

MAX_FRAME = 4096
MAX_FIELDS = 10
MAX_FIELD = 1536
MAX_TEXT = 1024
MAX_TARGET = 128
MAX_ID = 2_147_483_647
_OP = re.compile(r"[A-Z][A-Z_]{0,15}\Z")
_ID = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")
_HEX = re.compile(r"(?:[0-9A-F]{2})+\Z")


class ProtocolError(ValueError):
    """A malformed frame; never echoes message content."""


@dataclass(frozen=True)
class Frame:
    request_id: int
    op: str
    fields: tuple[str, ...] = ()


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: check value for b'123456789' is 0x29B1."""
    return binascii.crc_hqx(data, 0xFFFF)


def encode(frame: Frame) -> bytes:
    if not isinstance(frame.request_id, int) or not 0 <= frame.request_id <= MAX_ID:
        raise ProtocolError("Invalid request ID")
    if not _OP.fullmatch(frame.op) or len(frame.fields) > MAX_FIELDS:
        raise ProtocolError("Invalid operation or field count")
    parts = ["TS1", str(frame.request_id), frame.op]
    for field in frame.fields:
        if not isinstance(field, str) or "\0" in field:
            raise ProtocolError("Invalid field")
        raw = field.encode("utf-8")
        if len(raw) > MAX_FIELD:
            raise ProtocolError("Field too long")
        parts.append(raw.hex().upper() if raw else "-")
    payload = "\t".join(parts).encode("ascii")
    result = payload + f"\t{crc16(payload):04X}\n".encode("ascii")
    if len(result) > MAX_FRAME:
        raise ProtocolError("Frame too long")
    return result


def decode(line: bytes) -> Frame:
    if not line or len(line) > MAX_FRAME or not line.endswith(b"\n"):
        raise ProtocolError("Invalid frame length or terminator")
    try:
        data = line[:-1].decode("ascii")
    except UnicodeDecodeError:
        raise ProtocolError("Non-ASCII frame") from None
    if "\r" in data or "\n" in data:
        raise ProtocolError("Unexpected line break")
    try:
        payload, checksum = data.rsplit("\t", 1)
    except ValueError:
        raise ProtocolError("Missing checksum") from None
    if not re.fullmatch(r"[0-9A-F]{4}", checksum):
        raise ProtocolError("Invalid checksum field")
    if crc16(payload.encode("ascii")) != int(checksum, 16):
        raise ProtocolError("Checksum mismatch")
    parts = payload.split("\t")
    if not 3 <= len(parts) <= 3 + MAX_FIELDS or parts[0] != "TS1":
        raise ProtocolError("Invalid frame header")
    if not _ID.fullmatch(parts[1]) or int(parts[1]) > MAX_ID:
        raise ProtocolError("Invalid request ID")
    if not _OP.fullmatch(parts[2]):
        raise ProtocolError("Invalid operation")
    fields = []
    for value in parts[3:]:
        if value == "-":
            fields.append("")
            continue
        if len(value) > 2 * MAX_FIELD or not _HEX.fullmatch(value):
            raise ProtocolError("Invalid hex field")
        try:
            field = bytes.fromhex(value).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ProtocolError("Invalid UTF-8 field") from None
        if "\0" in field:
            raise ProtocolError("NUL is not supported")
        fields.append(field)
    return Frame(int(parts[1]), parts[2], tuple(fields))


class LineFramer:
    """Bounded incremental reader. None denotes an overlong discarded frame."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data: bytes) -> list[bytes | None]:
        result: list[bytes | None] = []
        for value in data:
            if self.discarding:
                if value == 10:
                    self.discarding = False
                    result.append(None)
                continue
            self.buffer.append(value)
            if value == 10:
                result.append(bytes(self.buffer))
                self.buffer.clear()
            elif len(self.buffer) >= MAX_FRAME:
                self.buffer.clear()
                self.discarding = True
        return result


def clipped_text(value: object, limit: int, marker: str = "...") -> str:
    """Sanitize control bytes and bound UTF-8 without splitting a codepoint."""
    text = str(value) if value is not None else ""
    text = "".join(ch if ch in "\n\r\t" or (ord(ch) >= 32 and ord(ch) != 127)
                   else "?" for ch in text)
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return raw.decode("utf-8")
    suffix = marker.encode("utf-8")[:limit]
    return raw[:limit - len(suffix)].decode("utf-8", "ignore") + suffix.decode("utf-8")
