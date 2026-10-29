"""Application and private Unix-socket tests. All backends are local fixtures."""

from __future__ import annotations

import asyncio
import collections
import os
from pathlib import Path
import stat
import tempfile
import unittest

from temple_signal.bridge import BridgeServer, Inbox, Session, valid_target
from temple_signal.demo import DemoBackend
from temple_signal.protocol import Frame, MAX_FRAME, decode, encode
from temple_signal.signal_backend import BackendError, Event


ACCOUNT = "+12025550100"
CONTACT = "+12025550101"
GROUP = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
UUID = "11111111-1111-4111-8111-111111111111"


class FixtureBackend:
    mode = "SIGNAL"
    description = "Local fixture, no network."

    def __init__(self):
        self.contacts = [
            {"id": CONTACT, "name": "First", "kind": "direct"},
            {"id": UUID, "name": "Second", "kind": "direct"},
        ]
        self.groups = [{"id": GROUP, "name": "Group", "kind": "group"}]
        self.events = collections.deque()
        self.calls = collections.Counter()
        self.sends = []
        self.send_error = None

    async def start(self):
        self.calls["start"] += 1

    async def close(self):
        self.calls["close"] += 1

    async def list_contacts(self):
        self.calls["contacts"] += 1
        return self.contacts

    async def list_groups(self):
        self.calls["groups"] += 1
        return self.groups

    async def send(self, kind, target, text):
        self.calls["send"] += 1
        self.sends.append((kind, target, text))
        if self.send_error:
            raise self.send_error
        return 1720000000010

    def poll(self):
        self.calls["poll"] += 1
        return self.events.popleft() if self.events else None


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = FixtureBackend()
        self.inbox = Inbox(self.backend)
        self.session = Session(self.backend, self.inbox)
        self.ready = await self.session.handle(Frame(1, "HELLO"))

    async def test_hello_is_required_and_zero_reserved(self):
        self.assertEqual(self.ready, Frame(1, "READY", ("1", "SIGNAL", "Local fixture, no network.")))
        cold = Session(self.backend, self.inbox)
        reply = await cold.handle(Frame(1, "SEND", ("D", CONTACT, "hello")))
        self.assertEqual(reply.fields[0], "HELLO_REQUIRED")
        reply = await cold.handle(Frame(0, "HELLO"))
        self.assertEqual(reply.fields[0], "BAD_ID")
        self.assertEqual(self.backend.calls["send"], 0)

    async def test_duplicate_send_replays_without_second_execution(self):
        request = Frame(2, "SEND", ("D", CONTACT, "one message"))
        first = await self.session.handle(request)
        self.assertEqual(first, Frame(2, "SENT", ("1720000000010",)))
        self.assertEqual(await self.session.handle(request), first)
        self.assertEqual(self.backend.sends, [("direct", CONTACT, "one message")])
        changed = await self.session.handle(Frame(2, "SEND", ("D", CONTACT, "different message")))
        self.assertEqual(changed.fields[0], "ID_REUSE")
        self.assertEqual(self.backend.calls["send"], 1)

    async def test_expired_request_cache_never_reexecutes_send(self):
        request = Frame(2, "SEND", ("S", "", "one note"))
        await self.session.handle(request)
        for rid in range(3, 40):
            await self.session.handle(Frame(rid, "PING"))
        self.assertLessEqual(len(self.session.cache), 32)
        reply = await self.session.handle(request)
        self.assertEqual(reply.fields[0], "STALE_ID")
        self.assertEqual(self.backend.calls["send"], 1)

    async def test_backend_errors_are_cached_and_uncertainty_is_visible(self):
        self.backend.send_error = BackendError("IDENTITY", "Verify the identity.", ambiguous=True)
        request = Frame(2, "SEND", ("D", CONTACT, "hello"))
        reply = await self.session.handle(request)
        self.assertEqual(reply.op, "ERR")
        self.assertTrue(any(word in reply.fields[1].lower() for word in ("may", "unknown", "partial", "uncertain")))
        self.backend.send_error = None
        self.assertEqual(await self.session.handle(request), reply)
        self.assertEqual(self.backend.calls["send"], 1)

    async def test_target_validation_blocks_unstructured_targets(self):
        invalid = [
            ("D", "--help"), ("D", "example.invalid"),
            ("D", "; touch /tmp/not-a-command"), ("D", "x" * 129),
            ("G", "not base64!"), ("G", ""), ("S", CONTACT),
            ("X", CONTACT), ("D", "uuid-without-an-actual-uuid"),
        ]
        for rid, (kind, target) in enumerate(invalid, 2):
            reply = await self.session.handle(Frame(rid, "SEND", (kind, target, "test")))
            self.assertEqual(reply.fields[0], "BAD_TARGET")
        self.assertEqual(self.backend.calls["send"], 0)
        self.assertTrue(valid_target("D", CONTACT))
        self.assertTrue(valid_target("D", UUID))
        self.assertTrue(valid_target("G", GROUP))
        self.assertTrue(valid_target("S", ""))

    async def test_bad_text_does_not_reach_backend(self):
        for rid, text in enumerate(["", " \t\n ", "nul\0", "escape\x1b", "delete\x7f", "é" * 513], 2):
            reply = await self.session.handle(Frame(rid, "SEND", ("D", CONTACT, text)))
            self.assertEqual(reply.fields[0], "BAD_TEXT")
        self.assertEqual(self.backend.calls["send"], 0)
        text = '$MA,"literal"$\nשלום'
        reply = await self.session.handle(Frame(99, "SEND", ("D", CONTACT, text)))
        self.assertEqual(reply.op, "SENT")
        self.assertEqual(self.backend.sends[-1][2], text)

    async def test_malformed_operations_do_not_invoke_backend(self):
        requests = [
            Frame(2, "PING", ("extra",)), Frame(3, "SEND", ("D",)),
            Frame(4, "EXEC", ("Host();",)), Frame(5, "POLL", ()),
            Frame(6, "CONTACTS", ("-1",)), Frame(7, "GROUPS", ("0001",)),
            Frame(8, "POLL", ("1.0",)), Frame(9, "POLL", ("9" * 20,)),
        ]
        for request in requests:
            self.assertEqual((await self.session.handle(request)).op, "ERR")
        self.assertEqual(sum(self.backend.calls.values()), 0)

    async def test_contact_snapshot_pagination_and_refresh(self):
        missing = await self.session.handle(Frame(2, "CONTACTS", ("1",)))
        self.assertEqual(missing.fields[0], "NO_SNAPSHOT")
        first = await self.session.handle(Frame(3, "CONTACTS", ("0",)))
        self.assertEqual(first.fields, ("1", CONTACT, "First"))
        self.backend.contacts = [{"id": "+12025550109", "name": "Replacement"}]
        second = await self.session.handle(Frame(4, "CONTACTS", ("1",)))
        self.assertEqual(second.fields, ("2", UUID, "Second"))
        self.assertEqual(self.backend.calls["contacts"], 1)
        end = await self.session.handle(Frame(5, "CONTACTS", ("2",)))
        self.assertEqual(end.op, "END")
        refresh = await self.session.handle(Frame(6, "CONTACTS", ("0",)))
        self.assertEqual(refresh.fields[1], "+12025550109")
        self.assertEqual(self.backend.calls["contacts"], 2)

    async def test_snapshot_is_independent_of_backend_object_mutation(self):
        await self.session.handle(Frame(2, "CONTACTS", ("0",)))
        self.backend.contacts[1]["name"] = "Changed after snapshot"
        second = await self.session.handle(Frame(3, "CONTACTS", ("1",)))
        self.assertEqual(second.fields[2], "Second")

    async def test_group_snapshot_and_invalid_directory_ids(self):
        self.backend.contacts.append({"id": "not a target", "name": "Ignored"})
        await self.session.handle(Frame(2, "CONTACTS", ("0",)))
        self.assertEqual((await self.session.handle(Frame(3, "CONTACTS", ("2",)))).op, "END")
        group = await self.session.handle(Frame(4, "GROUPS", ("0",)))
        self.assertEqual(group, Frame(4, "GROUP", ("1", GROUP, "Group")))
        self.assertEqual((await self.session.handle(Frame(5, "GROUPS", ("1",)))).op, "END")

    async def test_ack_retains_event_until_displayed_then_advances(self):
        self.backend.events.extend([
            Event("direct", CONTACT, CONTACT, "first", 100),
            Event("direct", CONTACT, CONTACT, "second", 200),
        ])
        first = await self.session.handle(Frame(2, "POLL", ("0",)))
        replay = await self.session.handle(Frame(3, "POLL", ("0",)))
        self.assertEqual(first.fields, replay.fields)
        self.assertEqual(self.backend.calls["poll"], 1)
        future = await self.session.handle(Frame(4, "POLL", ("99",)))
        self.assertEqual(future.op, "ERR")
        second = await self.session.handle(Frame(5, "POLL", (first.fields[0],)))
        self.assertEqual(second.fields[-1], "second")
        self.assertEqual(int(second.fields[0]), int(first.fields[0]) + 1)
        empty = await self.session.handle(Frame(6, "POLL", (second.fields[0],)))
        self.assertEqual(empty.op, "EMPTY")

    async def test_reconnected_guest_keeps_unacknowledged_event(self):
        self.backend.events.append(Event("direct", CONTACT, CONTACT, "retained", 123))
        first = await self.session.handle(Frame(2, "POLL", ("0",)))
        other = Session(self.backend, self.inbox)
        await other.handle(Frame(1, "HELLO"))
        replay = await other.handle(Frame(2, "POLL", ("0",)))
        self.assertEqual(replay.fields, first.fields)
        self.assertEqual(self.backend.calls["poll"], 1)

    async def test_note_to_self_normalizes_wire_target(self):
        self.backend.events.append(Event("self", ACCOUNT, ACCOUNT, "private note", 123, "outgoing"))
        response = await self.session.handle(Frame(2, "POLL", ("0",)))
        self.assertEqual(response.op, "EVENT")
        self.assertEqual(response.fields[1:3], ("S", ""))
        self.assertIn("private note", response.fields[-1])

    async def test_event_size_and_controls_are_safe_for_wire_encoder(self):
        self.backend.events.append(Event("direct", CONTACT, "sender\0", "é" * 2000, 123, name="name\x1b"))
        response = await self.session.handle(Frame(2, "POLL", ("0",)))
        encoded = encode(response)
        self.assertLessEqual(len(encoded), MAX_FRAME)
        self.assertEqual(decode(encoded), response)
        self.assertIn("truncated", response.fields[-1])
        self.assertNotIn("\x00", response.fields[3])
        self.assertNotIn("\x1b", response.fields[4])


class SocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ts-socket-")
        self.path = Path(self.directory.name) / "uart.sock"
        self.backend = DemoBackend()
        self.server = BridgeServer(self.backend, self.path)
        self.writers = []
        await self.server.start()

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        await self.server.close()
        self.directory.cleanup()

    async def client(self):
        reader, writer = await asyncio.open_unix_connection(self.path)
        self.writers.append(writer)
        return reader, writer

    async def exchange(self, reader, writer, request):
        writer.write(encode(request))
        await writer.drain()
        return decode(await asyncio.wait_for(reader.readline(), 2.0))

    async def test_private_permissions_and_clean_shutdown(self):
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(stat.S_ISSOCK(self.path.stat().st_mode))
        await self.server.close()
        self.assertFalse(self.path.exists())

    async def test_only_one_guest_can_connect(self):
        reader1, writer1 = await self.client()
        self.assertEqual((await self.exchange(reader1, writer1, Frame(1, "HELLO"))).op, "READY")
        reader2, _ = await self.client()
        self.assertEqual(await asyncio.wait_for(reader2.read(1), 2.0), b"")
        self.assertEqual((await self.exchange(reader1, writer1, Frame(2, "PING"))).op, "PONG")

    async def test_end_to_end_send_duplicate_and_receive(self):
        reader, writer = await self.client()
        hello = await self.exchange(reader, writer, Frame(1, "HELLO"))
        self.assertEqual(hello.fields[1], "DEMO")
        initial = await self.exchange(reader, writer, Frame(2, "POLL", ("0",)))
        self.assertEqual(initial.op, "EVENT")
        request = Frame(3, "SEND", ("D", "+12025550123", "Hello through the private UART socket."))
        first = await self.exchange(reader, writer, request)
        self.assertEqual(first.op, "SENT")
        self.assertEqual(await self.exchange(reader, writer, request), first)
        self.assertEqual(self.backend.sent_count, 1)
        received = await self.exchange(reader, writer, Frame(4, "POLL", (initial.fields[0],)))
        self.assertEqual(received.fields[-1], request.fields[-1])

    async def test_malformed_then_overlong_then_valid_resynchronizes(self):
        reader, writer = await self.client()
        writer.write(b"bad\n" + b"x" * (MAX_FRAME + 100) + b"\n" + encode(Frame(1, "HELLO")))
        await writer.drain()
        first = decode(await asyncio.wait_for(reader.readline(), 2.0))
        second = decode(await asyncio.wait_for(reader.readline(), 2.0))
        third = decode(await asyncio.wait_for(reader.readline(), 2.0))
        self.assertEqual(first.fields[0], "BAD_FRAME")
        self.assertEqual(second.fields[0], "BAD_FRAME")
        self.assertEqual(third.op, "READY")

    async def test_close_preserves_replacement_path(self):
        # A process must remove only the socket it actually created.
        self.path.unlink()
        self.path.write_text("replacement file", encoding="utf-8")
        await self.server.close()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "replacement file")

    async def test_existing_file_is_never_overwritten(self):
        other = self.path.parent / "occupied"
        other.write_text("keep me", encoding="utf-8")
        backend = FixtureBackend()
        server = BridgeServer(backend, other)
        with self.assertRaises(FileExistsError):
            await server.start()
        self.assertEqual(other.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(backend.calls["start"], 0)

    async def test_shared_parent_and_symlink_parent_rejected(self):
        shared = self.path.parent / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        server = BridgeServer(FixtureBackend(), shared / "socket")
        with self.assertRaises(ValueError):
            await server.start()
        link = self.path.parent / "link"
        link.symlink_to(self.path.parent, target_is_directory=True)
        server = BridgeServer(FixtureBackend(), link / "socket")
        with self.assertRaises(ValueError):
            await server.start()


if __name__ == "__main__":
    unittest.main()
