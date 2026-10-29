# Temple Signal serial protocol, version 1

COM1 is a private local serial link, not a network protocol. Signal encryption
terminates in the host's signal-cli process. This link carries plaintext.

## Frames

One ASCII line per frame, terminated by LF (no CR). Payload:

`TS1<TAB>request_id<TAB>OP[<TAB>field...]`

Append `<TAB>CCCC<LF>`: uppercase four-digit CRC-16/CCITT-FALSE of the payload
(polynomial 0x1021, initial value 0xFFFF, no reflection/final XOR;
`123456789` gives `29B1`). Fields are UTF-8 encoded then uppercase hex encoded.
An empty field is `-`. IDs are decimal 1..2147483647; response ID 0 is reserved
for an uncorrelated framing error. Operations contain uppercase ASCII letters
and underscores, at most 16 bytes. Maximum complete line including LF: 4096
bytes. At most 10 fields, each at most 1536 decoded bytes. Messages: 1024 UTF-8
bytes. Targets and display names: 128 bytes each.

Only one request outstanding at a time. Request timeout: 60 seconds. Host replies
use the request ID. Neither side automatically retries SEND. A timeout means
UNKNOWN: check another linked device before manually resending.

## Operations

| Request | Fields | Response | Fields |
| --- | --- | --- | --- |
| HELLO | none | READY | version `1`, mode `DEMO` or `SIGNAL`, description |
| PING | none | PONG | none |
| CONTACTS | zero-based offset | CONTACT | next offset, target, name |
| GROUPS | zero-based offset | GROUP | next offset, group ID, name |
| CONTACTS / GROUPS | past last item | END | none |
| SEND | kind `D`, `G`, or `S`; target; text | SENT | Signal timestamp in milliseconds |
| POLL | last displayed event ID (`0` initially) | EVENT | event ID, kind, target, sender, name, timestamp milliseconds, text |
| POLL | last displayed event ID | EMPTY | none |
| any | invalid request / backend failure | ERR | stable code, explanation |

`D`: direct recipient (E.164 number or ACI UUID). `G`: existing group's base64
identifier. `S`: Note to Self, with empty target. Incoming `N` denotes a notice.
Notice target/sender may be empty. Username lookup, creating groups, attachments,
calls, reactions, edits, and disappearing message content are outside version 1.

HELLO establishes a fresh session and resets request replay state. Duplicate IDs
with identical payloads replay a cached response without another send. Reusing
an ID with different content is an error. Older expired IDs are rejected. A
restarted guest starts a new session and never resubmits SEND on its own.

CONTACTS and GROUPS at offset 0 refresh an in-memory snapshot; subsequent offsets
iterate that snapshot. Only unblocked contacts and joined unblocked groups appear.
Missing names fall back to IDs.

POLL acknowledges the last displayed event and returns the next event. Until
acknowledged it is returned again. The guest deduplicates by ID. IDs increase for
one bridge process, restarting after a bridge restart. HELLO resets the guest
cursor to zero. Inbox is bounded and volatile. Overflow produces a visible
notice; there is no durable history or promise of offline guest delivery.

## Display and bounds

The host receives Unicode. The TempleOS fixed bitmap font replaces unsupported
characters with `?`; CR/LF become line breaks and tab a space. Remote text is
rendered as individual glyphs, never as HolyC, DolDoc, format strings, or URLs.
NUL and disallowed control characters in outgoing messages are rejected. No
file path or executable name is accepted from the guest. CRC detects corruption,
but provides no authentication or encryption.

Bad checksums, overlong frames, malformed fields, or invalid UTF-8 are rejected.
Parsing resynchronizes at LF. The host only sends responses; the guest polls.
