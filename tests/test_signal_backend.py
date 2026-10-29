"""Behavioral tests, including a real local JSON-RPC subprocess.

No test calls the installed signal-cli, logs into an account, or uses a network.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest

from temple_signal.signal_backend import (
    BackendError,
    Event,
    MAX_MESSAGE_BYTES,
    SignalBackend,
    _rpc_error,
)


ACCOUNT = "+12025550100"
CONTACT = "+12025550101"
UUID = "11111111-1111-4111-8111-111111111111"
GROUP = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="


def notification(data=None, *, source=CONTACT, wrapped=True, sync=False):
    envelope = {
        "sourceNumber": source,
        "sourceName": "Test person",
        "timestamp": 1720000000000,
    }
    data = data or {"message": "Hello", "timestamp": 1720000000001, "expiresInSeconds": 0}
    if sync:
        envelope["syncMessage"] = {"sentMessage": data}
    else:
        envelope["dataMessage"] = data
    params = {"envelope": envelope, "account": ACCOUNT}
    return {"subscription": 0, "result": params} if wrapped else params


class StubBackend(SignalBackend):
    """Controlled command responses for validating parameter mapping."""

    def __init__(self, contacts=None, groups=None, result=None):
        super().__init__(ACCOUNT)
        self.calls = []
        self.contacts = contacts if contacts is not None else [{"number": CONTACT, "messageExpirationTime": 0}]
        self.groups = groups if groups is not None else [{"id": GROUP, "name": "Test group", "isMember": True, "messageExpirationTime": 0}]
        self.result = result if result is not None else {"timestamp": 1720000000010, "results": [{"type": "SUCCESS"}]}

    async def _request(self, method, params=None, *, mutating=False):
        self.calls.append((method, params or {}, mutating))
        if method == "listContacts":
            return self.contacts
        if method == "listGroups":
            return self.groups
        if method == "send":
            return self.result
        raise AssertionError(method)


class MappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_group_and_note_to_self_use_real_rpc_keys(self):
        backend = StubBackend()
        self.assertEqual(await backend.send("direct", CONTACT, 'A "quote"\nשלום'), 1720000000010)
        self.assertEqual(backend.calls[-1], ("send", {"recipient": [CONTACT], "message": 'A "quote"\nשלום'}, True))
        await backend.send("group", GROUP, "group text")
        self.assertEqual(backend.calls[-1], ("send", {"groupId": GROUP, "message": "group text"}, True))
        await backend.send("self", "", "private note")
        self.assertEqual(backend.calls[-1], ("send", {"noteToSelf": True, "message": "private note"}, True))
        self.assertEqual(backend.calls[-2][1]["recipient"], [ACCOUNT])

    async def test_malformed_targets_and_messages_do_not_reach_send(self):
        backend = StubBackend()
        cases = [
            ("direct", "--help", "text"),
            ("direct", CONTACT, ""),
            ("direct", CONTACT, "hello\x00world"),
            ("direct", CONTACT, "é" * 513),
            ("bad-kind", CONTACT, "hello"),
            ("group", "", "hello"),
        ]
        for kind, target, text in cases:
            with self.subTest(kind=kind, target=target):
                with self.assertRaises(BackendError):
                    await backend.send(kind, target, text)
        self.assertFalse(any(method == "send" for method, _, _ in backend.calls))

    async def test_unicode_at_exact_byte_limit_is_sent_intact(self):
        backend = StubBackend()
        text = "é" * 512
        await backend.send("direct", UUID, text)
        self.assertEqual(backend.calls[-1][1]["message"], text)

    async def test_expiring_and_blocked_chats_cannot_send(self):
        for contact in [{"messageExpirationTime": 30}, {"messageExpirationTime": "unexpected"}, {"isBlocked": True}, {"unregistered": True}]:
            backend = StubBackend(contacts=[{"number": CONTACT, **contact}])
            with self.assertRaises(BackendError):
                await backend.send("direct", CONTACT, "must not be sent")
            self.assertFalse(any(method == "send" for method, _, _ in backend.calls))
        for group in [{"messageExpirationTime": 30}, {"isMember": False}, {"isBlocked": True}, {"isTerminated": True}]:
            backend = StubBackend(groups=[{"id": GROUP, "isMember": True, **group}])
            with self.assertRaises(BackendError):
                await backend.send("group", GROUP, "must not be sent")
            self.assertFalse(any(method == "send" for method, _, _ in backend.calls))

    async def test_partial_send_is_never_reported_as_full_success(self):
        backend = StubBackend(result={"timestamp": 1720000000010, "results": [{"type": "SUCCESS"}, {"type": "IDENTITY_FAILURE"}]})
        with self.assertRaises(BackendError) as caught:
            await backend.send("group", GROUP, "hello")
        self.assertEqual(caught.exception.code, "PARTIAL")
        self.assertTrue(caught.exception.ambiguous)
        self.assertEqual(sum(method == "send" for method, _, _ in backend.calls), 1)

    async def test_timestamp_without_results_matches_documented_older_shape(self):
        backend = StubBackend(result={"timestamp": 123})
        self.assertEqual(await backend.send("self", "", "note"), 123)

    async def test_invalid_send_result_is_ambiguous(self):
        for result in [{}, {"timestamp": True}, {"timestamp": -1}, {"timestamp": 123, "results": {}}, {"timestamp": 123, "results": [None]}]:
            backend = StubBackend(result=result)
            with self.assertRaises(BackendError) as caught:
                await backend.send("self", "", "note")
            self.assertTrue(caught.exception.ambiguous)

    async def test_contact_and_group_filtering_and_profile_names(self):
        backend = StubBackend(contacts=[
            {"number": CONTACT, "name": "Alice"},
            {"uuid": UUID, "profile": {"givenName": "Bob", "familyName": "Example"}},
            {"number": "+12025550102", "isBlocked": True},
            {"number": "+12025550103", "isHidden": True},
            {"number": "+12025550104", "unregistered": True},
            {"number": "+12025550105", "messageExpirationTime": 60},
            {"number": "not-an-id"},
        ], groups=[
            {"id": GROUP, "name": "Garden", "isMember": True},
            {"id": "inactive", "name": "Inactive", "isMember": False},
            {"id": "blocked", "isMember": True, "isBlocked": True},
        ])
        contacts = await backend.list_contacts()
        self.assertEqual(len(contacts), 3)
        self.assertEqual(contacts[0], {"id": CONTACT, "name": "Alice", "kind": "direct"})
        self.assertEqual(contacts[1]["name"], "Bob Example")
        self.assertIn("timed chat", contacts[2]["name"])
        self.assertEqual(await backend.list_groups(), [{"id": GROUP, "name": "Garden", "kind": "group"}])


class ReceiveTests(unittest.TestCase):
    def test_documented_wrapped_and_unwrapped_notifications(self):
        for wrapped in (True, False):
            backend = SignalBackend(ACCOUNT)
            backend._receive(notification(wrapped=wrapped))
            event = backend.poll()
            self.assertEqual(event.kind, "direct")
            self.assertEqual(event.target, CONTACT)
            self.assertEqual(event.sender, CONTACT)
            self.assertEqual(event.name, "Test person")
            self.assertEqual(event.text, "Hello")
            self.assertEqual(event.timestamp, 1720000000001)
            self.assertIsNone(backend.poll())

    def test_group_and_sent_sync_target_selection(self):
        backend = SignalBackend(ACCOUNT)
        backend._receive(notification({"message": "group", "groupInfo": {"groupId": GROUP}}))
        event = backend.poll()
        self.assertEqual((event.kind, event.target), ("group", GROUP))
        backend._receive(notification({"message": "outgoing", "destinationUuid": UUID}, source=ACCOUNT, sync=True))
        event = backend.poll()
        self.assertEqual((event.kind, event.target, event.direction), ("direct", UUID, "outgoing"))
        backend._receive(notification({"message": "note", "destinationNumber": ACCOUNT}, source=ACCOUNT, sync=True))
        event = backend.poll()
        self.assertEqual((event.kind, event.target), ("self", ACCOUNT))

    def test_timed_and_view_once_payloads_are_never_exposed(self):
        for special in [{"expiresInSeconds": 10}, {"viewOnce": True}, {"expiresInSeconds": "not-an-integer"}]:
            backend = SignalBackend(ACCOUNT)
            backend._receive(notification({"message": "SECRET PAYLOAD", **special}))
            event = backend.poll()
            self.assertEqual(event.direction, "notice")
            self.assertNotIn("SECRET", event.text)
            self.assertIn("official Signal app", event.text)
        backend = SignalBackend(ACCOUNT)
        backend._receive(notification({"message": "SECRET", "expiresInSeconds": 30, "destinationNumber": CONTACT}, source=ACCOUNT, sync=True))
        self.assertNotIn("SECRET", backend.poll().text)

    def test_utf8_truncation_and_control_bytes_are_bounded(self):
        backend = SignalBackend(ACCOUNT)
        backend._receive(notification({"message": "é" * 1500 + "\x00"}))
        event = backend.poll()
        self.assertLessEqual(len(event.text.encode("utf-8")), MAX_MESSAGE_BYTES)
        self.assertTrue(event.text.endswith("[truncated]"))
        backend._receive(notification({"message": "zero\x00 and surrogate\ud800"}))
        event = backend.poll()
        self.assertNotIn("\x00", event.text)
        event.text.encode("utf-8")

    def test_queue_overflow_is_explicit_and_keeps_recent_events(self):
        backend = SignalBackend(ACCOUNT, queue_limit=2)
        for n in range(4):
            backend._receive(notification({"message": str(n)}))
        event = backend.poll()
        self.assertEqual(event.direction, "notice")
        self.assertIn("2 earlier", event.text)
        self.assertEqual(backend.poll().text, "2")
        self.assertEqual(backend.poll().text, "3")
        self.assertIsNone(backend.poll())

    def test_remote_delete_discards_queued_copy(self):
        backend = SignalBackend(ACCOUNT)
        backend._receive(notification({"message": "not-yet-displayed", "timestamp": 123}))
        backend._receive(notification({"remoteDelete": {"timestamp": 123}, "timestamp": 456}))
        event = backend.poll()
        self.assertNotIn("not-yet-displayed", event.text)
        self.assertIn("deleted", event.text)
        self.assertIsNone(backend.poll())

    def test_malformed_notifications_are_ignored_without_crash(self):
        backend = SignalBackend(ACCOUNT)
        for malformed in [None, [], {}, {"result": None}, {"envelope": None}, {"envelope": {"syncMessage": []}}]:
            backend._receive(malformed)
        self.assertIsNone(backend.poll())

    def test_error_mapping_does_not_disclose_private_upstream_text(self):
        error = _rpc_error({"code": -4, "message": "TOKEN SECRET +12025550199", "data": "SECRET"}, mutating=True)
        self.assertEqual(error.code, "IDENTITY")
        self.assertNotIn("SECRET", error.message)
        self.assertFalse(error.ambiguous)
        error = _rpc_error({"code": []}, mutating=True)
        self.assertTrue(error.ambiguous)

    def test_command_preserves_arguments_without_shell_and_never_trusts(self):
        backend = SignalBackend(ACCOUNT, executable="/tmp/path with spaces/signal-cli", data_dir="/tmp/account data")
        command = backend.command()
        self.assertEqual(command[0], "/tmp/path with spaces/signal-cli")
        self.assertEqual(command[1:3], ["--data-dir", "/tmp/account data"])
        self.assertEqual(command[command.index("--trust-new-identities") + 1], "never")
        self.assertEqual(command[command.index("--receive-mode") + 1], "manual")
        self.assertNotIn("--send-read-receipts", command)
        self.assertIn("--ignore-attachments", command)


FAKE_CLI = r'''
import json, sys, time
def output(data):
    print(json.dumps(data), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    response = {'jsonrpc':'2.0','id':request['id']}
    params = request['params']
    if method == 'subscribeReceive':
        # A notification may arrive before the subscription response.
        output({'jsonrpc':'2.0','method':'receive','params':{'subscription':0,'result':{'envelope':{'sourceNumber':'+12025550101','dataMessage':{'message':'initial notification','timestamp':123}}}}})
        response['result'] = 0
    elif method == 'listContacts':
        response['result'] = [{'number':'+12025550101','name':'Alice','messageExpirationTime':0}]
    elif method == 'listGroups':
        response['result'] = [{'id':'group-id','name':'Garden','isMember':True,'messageExpirationTime':0}]
    elif method == 'send':
        if params['message'] == 'timeout':
            time.sleep(0.3)
        if params['message'] == 'exit':
            sys.exit(0)
        if params['message'] == 'bad-json':
            print('not JSON', flush=True)
            continue
        if params['message'] == 'identity':
            response['error'] = {'code':-4,'message':'untrusted SECRET identifier'}
        else:
            response['result'] = {'timestamp':1720000000010,'results':[{'type':'SUCCESS'}]}
    else:
        response['error'] = {'code':-32601,'message':'unsupported'}
    output(response)
'''


class ProcessBackend(SignalBackend):
    def command(self):
        return [sys.executable, "-u", "-c", FAKE_CLI]


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = ProcessBackend(ACCOUNT, timeout=1.0)
        await self.backend.start()

    async def asyncTearDown(self):
        await self.backend.close()

    async def test_notification_before_subscription_and_concurrent_queries(self):
        self.assertEqual(self.backend.poll().text, "initial notification")
        contacts, groups = await asyncio.gather(self.backend.list_contacts(), self.backend.list_groups())
        self.assertEqual(contacts[0]["name"], "Alice")
        self.assertEqual(groups[0]["name"], "Garden")
        self.assertEqual(await self.backend.send("self", "", "שלום"), 1720000000010)
        self.assertTrue(self.backend.connected)

    async def test_upstream_identity_error_is_actionable_and_sanitized(self):
        with self.assertRaises(BackendError) as caught:
            await self.backend.send("direct", CONTACT, "identity")
        self.assertEqual(caught.exception.code, "IDENTITY")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse(caught.exception.ambiguous)

    async def test_timeout_is_ambiguous_and_late_reply_is_not_reused(self):
        self.backend.timeout = 0.05
        with self.assertRaises(BackendError) as caught:
            await self.backend.send("self", "", "timeout")
        self.assertEqual(caught.exception.code, "SEND_UNKNOWN")
        self.assertTrue(caught.exception.ambiguous)
        self.backend.timeout = 1.0
        self.assertEqual((await self.backend.list_contacts())[0]["name"], "Alice")
        self.assertFalse(self.backend._pending)

    async def test_exit_during_send_does_not_claim_failure_or_success(self):
        with self.assertRaises(BackendError) as caught:
            await self.backend.send("self", "", "exit")
        self.assertTrue(caught.exception.ambiguous)
        self.assertFalse(self.backend.connected)

    async def test_bad_json_closes_transport_and_makes_send_ambiguous(self):
        with self.assertRaises(BackendError) as caught:
            await self.backend.send("self", "", "bad-json")
        self.assertTrue(caught.exception.ambiguous)
        self.assertFalse(self.backend.connected)

    async def test_close_ends_process_and_clears_memory_queue(self):
        process = self.backend._process
        await self.backend.close()
        self.assertIsNotNone(process.returncode)
        self.assertIsNone(self.backend.poll())
        self.assertFalse(self.backend.connected)
        await self.backend.close()


if __name__ == "__main__":
    unittest.main()
