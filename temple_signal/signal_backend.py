"""Private, bounded signal-cli JSON-RPC adapter.

This module contains no Signal cryptography. A separately installed signal-cli
process owns the account, protocol state and authenticated Signal connections.
Only typed JSON-RPC commands cross its stdin/stdout pipes. No shell, plaintext
message log, network listener, automatic send retry, or identity trust operation
is implemented here.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024
MAX_NAME_BYTES = 160
MAX_TARGET_BYTES = 192
MAX_DIRECTORY_ROWS = 4096
_NUMBER = re.compile(r"\+[1-9][0-9]{6,14}\Z")


class BackendError(Exception):
    """Safe guest-facing error; ambiguous sends must not be auto-retried."""

    def __init__(self, code: str, message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.ambiguous = ambiguous


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    target: str
    sender: str
    text: str
    timestamp: int
    direction: str = "incoming"
    name: str = ""


def _short(value: object, limit: int, *, suffix: str = "") -> str:
    """Bound UTF-8 storage without cutting a code point or allowing NUL."""
    if not isinstance(value, str):
        return ""
    value = value.replace("\x00", "\\0")
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return encoded.decode("utf-8")
    reserve = len(suffix.encode("utf-8"))
    return encoded[: max(0, limit - reserve)].decode("utf-8", errors="ignore") + suffix


def _timestamp(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**63:
        return value
    return 0


def _expiry(value: object) -> bool:
    # Unexpected timer types are handled conservatively, rather than assuming 0.
    return value not in (None, 0, False)


def _valid_person(value: str) -> bool:
    if _NUMBER.fullmatch(value):
        return True
    try:
        parsed = uuid.UUID(value)
        return str(parsed) == value.lower() or parsed.hex == value.lower()
    except (ValueError, AttributeError):
        return False


def _first_string(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _reject_json_constant(_: str) -> Any:
    raise ValueError("JSON constants must be finite")


def _rpc_error(error: object, *, mutating: bool) -> BackendError:
    """Map errors without leaking upstream phone numbers, tokens or messages."""
    code = error.get("code") if isinstance(error, dict) else None
    known = {
        -4: ("IDENTITY", "Verify the contact's safety number on the host before sending."),
        -5: ("RATE_LIMIT", "Signal rate limit. Wait and resolve it with signal-cli on the host."),
        -6: ("CAPTCHA", "Resolve the Signal CAPTCHA with signal-cli on the host."),
        -32601: ("VERSION", "Unsupported signal-cli method. Update signal-cli on the host."),
        -32602: ("VERSION", "signal-cli rejected command parameters. Check the installed version."),
        -32600: ("VERSION", "signal-cli rejected the JSON-RPC request."),
        -1: ("SIGNAL", "Signal rejected this operation. Check the account or target on the host."),
    }
    if isinstance(code, int) and not isinstance(code, bool) and code in known:
        name, message = known[code]
        data = error.get("data") if isinstance(error, dict) else None
        results = data.get("results") if isinstance(data, dict) else None
        partial = isinstance(results, list) and any(
            isinstance(row, dict) and row.get("type") == "SUCCESS" for row in results
        )
        return BackendError(name, message, ambiguous=mutating and partial)
    return BackendError(
        "SEND_UNKNOWN" if mutating else "SIGNAL",
        "Signal operation failed. A send may have succeeded; check before retrying."
        if mutating
        else "signal-cli could not complete this operation. Check the host account.",
        ambiguous=mutating,
    )


class SignalBackend:
    """One selected account on one private signal-cli subprocess.

    ``start`` subscribes only after the stdout reader is running. ``send`` checks
    the selected conversation's timer and blocked state immediately beforehand,
    then performs exactly one send request. A successful return is the Signal
    send timestamp, never a claim that the other person read the message.

    The process's stderr is discarded because upstream diagnostics can contain
    private identifiers or challenge tokens. Run signal-cli separately for
    account troubleshooting. Do not run another signal-cli process against the
    same account directory while this backend is active.
    """

    mode = "SIGNAL"
    description = "Signal through a private host signal-cli process."

    def __init__(
        self,
        account: str,
        executable: str = "signal-cli",
        data_dir: str | Path | None = None,
        timeout: float = 45.0,
        queue_limit: int = 128,
    ):
        if not isinstance(account, str) or not _valid_person(account):
            raise BackendError("ACCOUNT", "Use a full international phone number or account ACI.")
        if not math.isfinite(timeout) or timeout <= 0 or not 1 <= queue_limit <= 4096:
            raise ValueError("timeout must be positive and queue_limit must be 1..4096")
        self.account = account
        self.executable = str(executable)
        self.data_dir = str(data_dir) if data_dir is not None else None
        self.timeout = float(timeout)
        self.queue_limit = queue_limit
        self._events: collections.deque[Event] = collections.deque()
        self._dropped = 0
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, tuple[asyncio.Future[Any], bool]] = {}
        self._request_number = 0
        self._active = False
        self._closing = False
        self._failure: BackendError | None = None
        self._write_lock = asyncio.Lock()
        self._subscription: int | None = None

    @property
    def connected(self) -> bool:
        return self._active and self._failure is None

    def command(self) -> list[str]:
        command = [self.executable]
        if self.data_dir is not None:
            command.extend(["--data-dir", self.data_dir])
        command.extend(
            [
                "-a", self.account,
                "--trust-new-identities", "never",
                "jsonRpc", "--receive-mode", "manual",
                "--ignore-attachments", "--ignore-stories",
                "--ignore-avatars", "--ignore-stickers",
            ]
        )
        return command

    async def start(self) -> None:
        if self.connected:
            return
        if self._process is not None:
            raise BackendError("UNAVAILABLE", "Close the previous backend before reconnecting.")
        self._closing = False
        self._failure = None
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_JSON_BYTES + 1,
            )
        except (OSError, ValueError) as exc:
            raise BackendError("UNAVAILABLE", "Cannot start signal-cli. Install it and link the host account.") from exc
        self._active = True
        self._reader_task = asyncio.create_task(self._read_loop(), name="signal-cli-reader")
        try:
            result = await self._request("subscribeReceive")
            if not isinstance(result, int) or isinstance(result, bool) or result < 0:
                raise BackendError("PROTOCOL", "signal-cli returned an invalid subscription.")
            self._subscription = result
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        self._closing = True
        self._active = False
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._fail_pending("UNAVAILABLE", "Signal connection closed.")
        self._reader_task = None
        self._process = None
        self._subscription = None
        self._events.clear()
        self._dropped = 0

    def _fail_pending(self, code: str, message: str) -> None:
        for future, mutating in self._pending.values():
            if not future.done():
                future.set_exception(
                    BackendError(
                        "SEND_UNKNOWN" if mutating else code,
                        "Signal connection lost. Send outcome unknown; check before retrying."
                        if mutating else message,
                        ambiguous=mutating,
                    )
                )

    async def _request(self, method: str, params: dict[str, Any] | None = None, *, mutating: bool = False) -> Any:
        process = self._process
        if not self.connected or process is None or process.stdin is None:
            raise BackendError("UNAVAILABLE", "Signal is disconnected. Restart the bridge on the host.")
        self._request_number += 1
        request_id = f"temple-{self._request_number}"
        packet = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, mutating)
        attempted = False
        try:
            async with self._write_lock:
                attempted = True
                process.stdin.write(encoded)
                await asyncio.wait_for(process.stdin.drain(), timeout=self.timeout)
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise BackendError(
                "SEND_UNKNOWN" if mutating and attempted else "TIMEOUT",
                "Send timed out. It may have succeeded; check before retrying."
                if mutating and attempted else "signal-cli request timed out.",
                ambiguous=mutating and attempted,
            ) from exc
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            raise BackendError(
                "SEND_UNKNOWN" if mutating and attempted else "UNAVAILABLE",
                "Signal connection lost. Send outcome unknown; check before retrying."
                if mutating and attempted else "Signal connection lost.",
                ambiguous=mutating and attempted,
            ) from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        failure = BackendError("UNAVAILABLE", "signal-cli exited. Check the linked account on the host.")
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                if len(line) > MAX_JSON_BYTES:
                    raise ValueError("oversize JSON-RPC response")
                packet = json.loads(line, parse_constant=_reject_json_constant)
                if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0":
                    raise ValueError("invalid JSON-RPC response")
                if packet.get("method") == "receive":
                    self._receive(packet.get("params"))
                    continue
                request_id = packet.get("id")
                if not isinstance(request_id, str) or request_id not in self._pending:
                    continue
                future, mutating = self._pending[request_id]
                if future.done():
                    continue
                if "error" in packet:
                    future.set_exception(_rpc_error(packet["error"], mutating=mutating))
                elif "result" in packet:
                    future.set_result(packet["result"])
                else:
                    future.set_exception(BackendError("PROTOCOL", "Invalid signal-cli response.", ambiguous=mutating))
        except asyncio.CancelledError:
            raise
        except (ValueError, UnicodeError, ConnectionError, OSError, RecursionError):
            failure = BackendError("PROTOCOL", "Invalid or oversized signal-cli output. Restart the host bridge.")
        finally:
            self._active = False
            if not self._closing:
                self._failure = failure
                self._enqueue(Event("notice", "", "bridge", failure.message, 0, "notice"))
            self._fail_pending(failure.code, failure.message)

    def _enqueue(self, event: Event) -> None:
        if len(self._events) >= self.queue_limit:
            self._events.popleft()
            self._dropped += 1
        self._events.append(event)

    def poll(self) -> Event | None:
        if self._dropped:
            count, self._dropped = self._dropped, 0
            return Event(
                "notice", "", "bridge",
                f"Receive queue overflow: {count} earlier event(s) were dropped. Check Signal on your phone.",
                0, "notice",
            )
        return self._events.popleft() if self._events else None

    def _receive(self, params: object) -> None:
        if not isinstance(params, dict):
            return
        wrapped = params.get("result", params)
        if not isinstance(wrapped, dict):
            return
        envelope = wrapped.get("envelope")
        if not isinstance(envelope, dict):
            return
        # This process is started in single-account mode; source account aliases
        # can legitimately differ between a phone-number selection and ACI event.
        sender_id = _first_string(envelope, "sourceNumber", "sourceUuid", "source")
        sender = _short(sender_id, MAX_TARGET_BYTES) or "unknown"
        name = _short(envelope.get("sourceName"), MAX_NAME_BYTES)
        data = envelope.get("dataMessage")
        direction = "incoming"
        if not isinstance(data, dict):
            sync = envelope.get("syncMessage")
            if not isinstance(sync, dict):
                if isinstance(envelope.get("editMessage"), dict):
                    self._enqueue(Event("notice", sender, sender, "Message edited in Signal. View the updated message on your phone.", _timestamp(envelope.get("timestamp")), "notice", name))
                return
            data = sync.get("sentMessage")
            if not isinstance(data, dict):
                return
            direction = "outgoing"
        group = data.get("groupInfo")
        if isinstance(group, dict) and isinstance(group.get("groupId"), str):
            kind, target = "group", group["groupId"]
        elif direction == "outgoing":
            target = _first_string(data, "destinationNumber", "destinationUuid", "destination")
            if not target:
                return
            kind = "self" if target == self.account or target == sender_id else "direct"
        else:
            target = sender_id
            kind = "self" if target == self.account else "direct"
        target = _short(target, MAX_TARGET_BYTES)
        timestamp = _timestamp(data.get("timestamp")) or _timestamp(envelope.get("timestamp"))
        if data.get("viewOnce") or _expiry(data.get("expiresInSeconds")):
            self._enqueue(Event(kind, target, sender, "[Private timed or view-once message. Open it in the official Signal app.]", timestamp, "notice", name))
            return
        if isinstance(data.get("remoteDelete"), dict):
            deleted = _timestamp(data["remoteDelete"].get("targetSentTimestamp")) or _timestamp(data["remoteDelete"].get("timestamp"))
            self._events = collections.deque(e for e in self._events if not (e.target == target and e.timestamp == deleted))
            self._enqueue(Event(kind, target, sender, "[A message was deleted in Signal. Clear any local copy with the client clear command.]", timestamp, "notice", name))
            return
        if isinstance(data.get("editMessage"), dict):
            self._enqueue(Event(kind, target, sender, "[Message edited in Signal. View it on your phone.]", timestamp, "notice", name))
            return
        text = data.get("message")
        if isinstance(text, str) and text:
            self._enqueue(Event(kind, target, sender, _short(text, MAX_MESSAGE_BYTES, suffix=" [truncated]"), timestamp, direction, name))
        elif data.get("attachments") or data.get("sticker") or data.get("contacts"):
            self._enqueue(Event(kind, target, sender, "[Attachment, sticker or shared contact. Open it in Signal.]", timestamp, "notice", name))

    async def _contact_rows(self, target: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"allRecipients": True}
        if target:
            params["recipient"] = [target]
        result = await self._request("listContacts", params)
        return self._checked_rows(result)

    @staticmethod
    def _checked_rows(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list) or len(value) > MAX_DIRECTORY_ROWS or any(not isinstance(row, dict) for row in value):
            raise BackendError("PROTOCOL", "Invalid or oversized Signal contact directory.")
        return value

    async def list_contacts(self) -> list[dict[str, str]]:
        normalized = []
        for row in await self._contact_rows():
            if row.get("isBlocked") or row.get("isHidden") or row.get("unregistered"):
                continue
            target = _first_string(row, "number", "uuid")
            if not _valid_person(target):
                continue
            profile = row.get("profile")
            profile_name = " ".join(
                value for key in ("givenName", "familyName")
                if isinstance(profile, dict) and isinstance(value := profile.get(key), str) and value
            )
            name = _first_string(row, "name", "nickName") or profile_name or target
            if _expiry(row.get("messageExpirationTime")):
                name += " [timed chat]"
            normalized.append({"id": target, "name": _short(name, MAX_NAME_BYTES), "kind": "direct"})
        return normalized

    async def _group_rows(self) -> list[dict[str, Any]]:
        return self._checked_rows(await self._request("listGroups"))

    async def list_groups(self) -> list[dict[str, str]]:
        normalized = []
        for row in await self._group_rows():
            if not row.get("isMember") or row.get("isBlocked") or row.get("isTerminated"):
                continue
            target = row.get("id")
            if not isinstance(target, str) or not target or len(target.encode("utf-8")) > MAX_TARGET_BYTES:
                continue
            name = _first_string(row, "name") or target
            if _expiry(row.get("messageExpirationTime")):
                name += " [timed chat]"
            normalized.append({"id": target, "name": _short(name, MAX_NAME_BYTES), "kind": "group"})
        return normalized

    async def send(self, kind: str, target: str, text: str) -> int:
        if not isinstance(text, str) or not text or "\x00" in text or len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise BackendError("MESSAGE", "Messages must contain 1 to 1024 UTF-8 bytes and no NUL.")
        params: dict[str, Any] = {"message": text}
        if kind == "group":
            if not isinstance(target, str) or not target or len(target.encode("utf-8")) > MAX_TARGET_BYTES:
                raise BackendError("TARGET", "Invalid group identifier.")
            rows = [row for row in await self._group_rows() if row.get("id") == target]
            if not rows or not rows[0].get("isMember") or rows[0].get("isBlocked") or rows[0].get("isTerminated"):
                raise BackendError("TARGET", "Select an active, unblocked group you belong to.")
            params["groupId"] = target
        elif kind in ("direct", "self"):
            recipient = self.account if kind == "self" else target
            if not isinstance(recipient, str) or not _valid_person(recipient):
                raise BackendError("TARGET", "Use a full phone number or Signal UUID for the recipient.")
            rows = await self._contact_rows(recipient)
            if any(row.get("isBlocked") or row.get("unregistered") for row in rows):
                raise BackendError("TARGET", "This contact is blocked or unregistered.")
            if kind == "self":
                params["noteToSelf"] = True
            else:
                params["recipient"] = [recipient]
        else:
            raise BackendError("TARGET", "Unknown conversation type.")
        if any(_expiry(row.get("messageExpirationTime")) for row in rows):
            raise BackendError("TIMED_CHAT", "Timed chats are read-only notices here. Use the official Signal app.")
        result = await self._request("send", params, mutating=True)
        if not isinstance(result, dict) or not _timestamp(result.get("timestamp")):
            raise BackendError("SEND_UNKNOWN", "Signal returned no send timestamp. Check before retrying.", ambiguous=True)
        results = result.get("results")
        if results is not None:
            if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
                raise BackendError("SEND_UNKNOWN", "Signal returned invalid send results. Check before retrying.", ambiguous=True)
            failures = [row for row in results if row.get("type") != "SUCCESS"]
            if failures:
                partial = any(row.get("type") == "SUCCESS" for row in results)
                raise BackendError(
                    "PARTIAL" if partial else "SEND_FAILED",
                    "Some group recipients did not receive this send. Do not resend to the whole group."
                    if partial else "Signal could not send to the recipient. Check signal-cli on the host.",
                    ambiguous=partial or any(row.get("type") == "NETWORK_FAILURE" for row in failures),
                )
        return result["timestamp"]
