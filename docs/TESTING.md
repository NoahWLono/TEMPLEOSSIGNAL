# Verification

The project uses four distinct kinds of evidence. A successful Python test run
alone does not mean the HolyC source compiled, and a demo echo does not mean an
actual Signal account was linked.

## Recorded result

Validated October 28, 2026 (America/Toronto), using Python 3.12.14 and QEMU
8.2.2 on Linux. The guest was the original TempleOS 5.03 image, with its native
compiler, 512 MiB RAM, one emulated CPU and no network adapter.

| Check | Result |
| --- | --- |
| Host protocol, bridge and Signal adapter | 53 tests passed |
| RedSea image builder and QEMU launcher | 13 tests passed |
| Native HolyC compilation | Passed in original TempleOS |
| Native CRC, bounds and UTF-8 self-tests | `TS SELFTEST PASS` |
| Actual COM1 request/response | `TS SERIAL PASS` |
| Native send, receive and acknowledgement | `TS SMOKE PASS` |
| Keyboard-driven send and receive in the rendered UI | `TS UI PASS` |
| Real Signal account / phone delivery | Not performed; requires the user's linked account |

The [unaltered guest debug output](verification/native-run.txt),
[native test framebuffer](assets/native-tests.png), and
[actual application framebuffer](assets/temple-signal-demo.png) are committed.
The UI capture is 640 by 480 pixels. It displays the message entered by the test
runner through virtual keyboard events, followed by its offline echo. The
[source checksum manifest](verification/source-sha256.txt) identifies the code
and distribution files used in this run.

The final tested application image is the same byte-for-byte image shipped in
[`releases/v0.1.0`](../releases/v0.1.0/):

```text
8e2f74fc96d947ae8c2b316da63bcdf901dea61940271568f565cc3a8367d6f5  TEMPLEOS_SIGNAL.ISO
```

These results establish native compilation, functioning UI input, media mounting
and the complete local guest/host path. The Signal adapter was exercised against
documented JSON-RPC fixtures, including a real local subprocess. That does not
establish live Signal service delivery or account compatibility.

## Host tests

From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s scripts/tests -v
python3 -m compileall -q temple_signal scripts
```

Bridge and adapter tests use local streams and controlled JSON-RPC fixtures.
They must not send messages through a real Signal account. They check framing,
boundaries, command mapping, receive handling, error propagation and state
transitions independently of external service availability.

The 13 packaging and launcher tests passed during development. The RedSea tests
use an independent directory reader to inspect produced bytes, including the
filesystem header, absolute sector addresses, multi-sector directories, empty
files, exact source preservation, ISO detection markers and nonbootable catalog.
They also verify deterministic rebuilds and reject names that cannot be stored
without truncation. Launcher checks cover the disabled NIC, private serial
transport, exact IDE layout, disk snapshots and QEMU option escaping.

The GitHub workflow runs the host tests, syntax checks and image build, then a
separate QEMU job runs the original TempleOS compiler and the same native test
harness. It verifies the boot ISO hash and saves guest logs and framebuffers.
Neither job logs in to Signal or sends a real message.

## Guest tests

Run the complete verified procedure with:

```sh
python3 scripts/smoke_qemu.py --templeos-iso /path/to/TempleOS.ISO
```

The harness starts its own offline demo bridge, builds fresh RedSea media,
boots the guest, mounts the app disc, compiles `Test.HC` with `ExeFile`, and
checks its debug-port markers. It then opens `TSSignal`, presses F4, types
`Hello from HolyC!`, presses Enter, captures the actual screen, exits the app,
and checks that the received-event count increased. All child processes are
closed after the run. `--skip-ui-capture` skips the last keyboard/UI check.


The guest must be tested with an unmodified TempleOS boot ISO, its native HolyC
compiler, the RedSea application CD and a QEMU COM1 connection. The reference
ISO and exact source provenance are in [UPSTREAM.md](UPSTREAM.md).

Guest test output should come from actual code running inside TempleOS. A host
translation of HolyC, a mock screenshot or a text-only parser is not equivalent.
The launcher supports headless runs with explicit QMP and debug-port outputs:

```sh
python3 scripts/run_qemu.py \
  --templeos-iso /path/to/TempleOS.ISO \
  --socket /tmp/temple-signal-run/serial.sock \
  --headless \
  --qmp /tmp/temple-signal-run/qmp.sock \
  --debug-log /tmp/temple-signal-run/guest-test.log
```

QMP is for the local test runner to drive the VM keyboard and capture its
framebuffer. Guest tests emit only test status to the debug port at `0xE9`.
Do not reuse debug log or QMP paths while another VM is running.

## Demo integration

Run the client against `python3 -m temple_signal --demo ...` and verify a native
HolyC send, response handling, incoming event polling, and the onscreen DEMO
label. This exercises the UART and application logic without an account. It
cannot certify current Signal service compatibility or remote delivery.

## Live-account acceptance

After linking a current signal-cli installation, a user with that account can
complete these checks:

1. Start the real bridge and confirm the guest identifies Signal mode.
2. Send a unique message to Note to Self and observe it on the phone.
3. Send another Note to Self message from the phone and observe it in TempleOS.
4. With a willing recipient and verified identity, test one direct message in
   each direction.
5. Test an existing group if group messaging will be used.

Account-linking, actual network delivery and phone receipt require a real
account. No account credentials or actual private message logs are included in
the repository. Keep those checks separate from the reproducible automated
tests and record the installed signal-cli version when reporting a failure.
