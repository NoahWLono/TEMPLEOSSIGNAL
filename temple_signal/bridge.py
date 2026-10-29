"""One private serial terminal, bounded RPCs, and no automatic send retries."""
from __future__ import annotations

import asyncio
import base64
import os
import re
import stat
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .protocol import (Frame, LineFramer, MAX_TARGET, MAX_TEXT, ProtocolError,
                       clipped_text, decode, encode)
from .signal_backend import BackendError

KINDS = {"D": "direct", "G": "group", "S": "self"}
WIRE_KINDS = {value: key for key, value in KINDS.items()} | {"notice": "N"}


def valid_target(kind: str, target: str) -> bool:
    if len(target.encode("utf-8")) > MAX_TARGET:
        return False
    if kind == "S":
        return target == ""
    if kind == "D":
        if re.fullmatch(r"\+[1-9][0-9]{2,14}", target):
            return True
        try:
            return str(uuid.UUID(target)) == target.lower()
        except ValueError:
            return False
    if kind == "G":
        try:
            return bool(base64.b64decode(target, validate=True))
        except (ValueError, UnicodeEncodeError):
            return False
    return False


class Inbox:
    """Keep the front event until the guest explicitly acknowledges it."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.pending: tuple[str, ...] | None = None
        self.next_id = 1
        self.acknowledged = 0

    def poll(self, ack: int) -> tuple[str, ...] | None:
        if self.pending:
            current = int(self.pending[0])
            if ack > current:
                raise ValueError("Acknowledgement is ahead of the inbox")
            if ack == current:
                self.acknowledged = ack
                self.pending = None
        elif ack > self.acknowledged:
            raise ValueError("Unknown acknowledgement")
        if self.pending is not None:
            return self.pending
        event = self.backend.poll()
        if event is None:
            return None
        kind = WIRE_KINDS.get(event.kind, "N")
        target = "" if kind == "S" else str(event.target)
        text = str(event.text)
        if kind != "N" and not valid_target(kind, target):
            kind, target, text = "N", "", "Unsupported recipient ID. Open Signal on another device."
        if getattr(event, "direction", "incoming") == "outgoing":
            text = "[Sent on another device] " + text
        self.pending = (
            str(self.next_id), kind, target if kind != "N" else "",
            clipped_text(event.sender, MAX_TARGET),
            clipped_text(getattr(event, "name", "") or event.sender, 128),
            str(max(0, int(event.timestamp))),
            clipped_text(text, MAX_TEXT, " [truncated; open Signal]"),
        )
        self.next_id += 1
        return self.pending


class Session:
    """Replay protection lasts one serial connection / HELLO session."""

    def __init__(self, backend: Any, inbox: Inbox) -> None:
        self.backend, self.inbox = backend, inbox
        self.ready = False
        self.high_water = 0
        self.cache: OrderedDict[int, tuple[Frame, Frame]] = OrderedDict()
        self.snapshots: dict[str, list[dict[str, str]]] = {}

    @staticmethod
    def error(request_id: int, code: str, message: str) -> Frame:
        return Frame(request_id, "ERR", (clipped_text(code, 48), clipped_text(message, 512)))

    async def handle(self, request: Frame) -> Frame:
        rid, op = request.request_id, request.op
        if rid == 0:
            return self.error(0, "BAD_ID", "Request ID zero is reserved.")
        if op == "HELLO":
            if request.fields:
                return self.error(rid, "BAD_FIELDS", "HELLO takes no fields.")
            self.ready = True
            self.high_water = rid
            self.cache.clear()
            self.snapshots.clear()
            return Frame(rid, "READY", ("1", getattr(self.backend, "mode", "SIGNAL"),
                         getattr(self.backend, "description", "Host-side Signal endpoint.")))
        if not self.ready:
            return self.error(rid, "HELLO_REQUIRED", "Start with HELLO.")
        if rid in self.cache:
            old, response = self.cache[rid]
            return response if old == request else self.error(rid, "ID_REUSE", "Request ID reused with different content.")
        if rid <= self.high_water:
            return self.error(rid, "STALE_ID", "Expired request ID; operation was not repeated.")
        self.high_water = rid
        try:
            async with asyncio.timeout(50):
                response = await self.dispatch(request)
        except BackendError as exc:
            message = exc.message
            if exc.ambiguous:
                message += " Send outcome uncertain; check Signal before resending."
            response = self.error(rid, exc.code, message)
        except TimeoutError:
            response = self.error(rid, "UNKNOWN" if op == "SEND" else "TIMEOUT",
                                  "Timed out. Check Signal on another device before resending." if op == "SEND" else "Backend timed out.")
        except (ValueError, TypeError):
            response = self.error(rid, "BAD_FIELDS", "Invalid operation fields.")
        self.cache[rid] = (request, response)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return response

    async def dispatch(self, request: Frame) -> Frame:
        rid, op, fields = request.request_id, request.op, request.fields
        if op == "PING" and not fields:
            return Frame(rid, "PONG")
        if op in ("CONTACTS", "GROUPS") and len(fields) == 1:
            if not re.fullmatch(r"0|[1-9][0-9]{0,5}", fields[0]):
                raise ValueError("Invalid offset")
            offset = int(fields[0])
            if offset == 0:
                entries = await (self.backend.list_contacts() if op == "CONTACTS"
                                 else self.backend.list_groups())
                kind = "D" if op == "CONTACTS" else "G"
                self.snapshots[op] = [
                    {"id": str(item["id"]), "name": clipped_text(item.get("name") or item["id"], 128)}
                    for item in entries if valid_target(kind, str(item.get("id", "")))
                ][:10000]
            if op not in self.snapshots:
                return self.error(rid, "NO_SNAPSHOT", "Start directory listing at offset zero.")
            entries = self.snapshots[op]
            if offset >= len(entries):
                return Frame(rid, "END")
            item = entries[offset]
            return Frame(rid, "CONTACT" if op == "CONTACTS" else "GROUP",
                         (str(offset + 1), str(item["id"]), clipped_text(item.get("name") or item["id"], 128)))
        if op == "SEND" and len(fields) == 3:
            kind, target, text = fields
            if not valid_target(kind, target):
                return self.error(rid, "BAD_TARGET", "Use an E.164 number, ACI UUID, existing group, or Note to Self.")
            if not text.strip() or len(text.encode("utf-8")) > MAX_TEXT:
                return self.error(rid, "BAD_TEXT", "Message must contain 1 to 1024 UTF-8 bytes.")
            if any(ord(ch) < 32 and ch not in "\n\r\t" or ord(ch) == 127 for ch in text):
                return self.error(rid, "BAD_TEXT", "Unsupported control character.")
            timestamp = await self.backend.send(KINDS[kind], target, text)
            return Frame(rid, "SENT", (str(timestamp),))
        if op == "POLL" and len(fields) == 1:
            if not re.fullmatch(r"0|[1-9][0-9]{0,18}", fields[0]):
                raise ValueError("Invalid event acknowledgement")
            event = self.inbox.poll(int(fields[0]))
            return Frame(rid, "EVENT", event) if event else Frame(rid, "EMPTY")
        return self.error(rid, "BAD_REQUEST", "Unknown command or wrong number of fields.")


class BridgeServer:
    def __init__(self, backend: Any, path: Path) -> None:
        self.backend = backend
        self.path = Path(path).absolute()
        self.inbox = Inbox(backend)
        self.server: asyncio.AbstractServer | None = None
        self.active: asyncio.StreamWriter | None = None
        self.tasks: set[asyncio.Task] = set()
        self.socket_inode: int | None = None

    async def start(self) -> None:
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Socket directory must be owned by you with mode 0700.")
        if len(os.fsencode(self.path)) > 100:
            raise ValueError("Socket path is too long; choose a shorter private directory.")
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError("Socket path already exists; stop its owner or choose another path.")
        await self.backend.start()
        try:
            previous_umask = os.umask(0o077)
            try:
                self.server = await asyncio.start_unix_server(self.connect, path=str(self.path), limit=8192)
            finally:
                os.umask(previous_umask)
            self.socket_inode = self.path.lstat().st_ino
            self.path.chmod(0o600)
        except BaseException:
            await self.close()
            raise

    async def connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.active is not None:
            writer.close()
            await writer.wait_closed()
            return
        self.active = writer
        task = asyncio.current_task()
        self.tasks.add(task)
        session, framer = Session(self.backend, self.inbox), LineFramer()
        try:
            while data := await reader.read(4096):
                for line in framer.feed(data):
                    try:
                        if line is None:
                            raise ProtocolError("Frame too long")
                        request = decode(line)
                    except ProtocolError:
                        response = Session.error(0, "BAD_FRAME", "Malformed serial frame; synchronize and try again.")
                    else:
                        response = await session.handle(request)
                    writer.write(encode(response))
                    await asyncio.wait_for(writer.drain(), 10)
        except (ConnectionError, TimeoutError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if self.active is writer:
                self.active = None
            self.tasks.discard(task)

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.backend.close()
        if self.socket_inode is not None:
            try:
                if self.path.lstat().st_ino == self.socket_inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass
