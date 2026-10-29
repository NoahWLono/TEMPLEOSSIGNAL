# Upstream sources and design decisions

Source review snapshot: October 28, 2026. These repositories were read as
references. They are not silently vendored into this repository.

| Project | Reviewed commit | Purpose |
| --- | --- | --- |
| [TempleOS-Official](https://github.com/TempleOS-Community/TempleOS-Official/tree/1dd8859b7803355f41d75222d01ed42d5dda057f) | `1dd8859b7803355f41d75222d01ed42d5dda057f` | Original HolyC APIs, UART operations, graphics and RedSea disk layout |
| [signal-cli](https://github.com/AsamK/signal-cli/tree/1e878f38457d22adb55d9b08b7f58543fdb92318) | `1e878f38457d22adb55d9b08b7f58543fdb92318` | JSON-RPC command and notification contract |
| [Signal libsignal](https://github.com/signalapp/libsignal/tree/904c8dc8216073b078a5f2f5090e3da8382e6a92) | `904c8dc8216073b078a5f2f5090e3da8382e6a92` | Official cryptographic implementation and supported language bindings |

## TempleOS constraints

Terry Davis's own [FAQ](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Doc/FAQ.DD)
states that TempleOS has no networking and uses HolyC. Its
[features](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Doc/Features.DD)
include 640 by 480 graphics in 16 colors and ring-0 execution.

The guest application follows those constraints. A new HolyC polled COM1 driver
uses ordinary port I/O, with the original
[serial example](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Doc/Comm.HC)
as an API/register reference. The UI uses TempleOS's own graphics and keyboard
facilities. No POSIX layer, guest TCP/IP stack, webview, TLS implementation or
kernel patch is added.

The host bridge is an explicit extension of the system's communication boundary.
The complete system is therefore not an air-gapped TempleOS installation and
not a standalone all-HolyC implementation of Signal. Messages and keys are
handled by a separate host process, with plaintext text crossing the local UART.

## Why a RedSea image builder exists

Although the source tree contains an old `FileSysISO.HC`, the ordinary
[FileRead implementation](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Kernel/BlkDev/DskFile.HC)
dispatches only RedSea and FAT32. A generic `genisoimage` or `mkisofs` disc is not
a working application distribution for stock TempleOS.

The builder implements the documented
[RedSea structures](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Doc/RedSea.DD)
and follows the reader in
[DskCDDVD.HC](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Kernel/BlkDev/DskCDDVD.HC)
and the original writer in
[DskISORedSea.HC](https://github.com/TempleOS-Community/TempleOS-Official/blob/1dd8859b7803355f41d75222d01ed42d5dda057f/Adam/Opt/Boot/DskISORedSea.HC).
It uses 512-byte sectors, 64-byte directory records and absolute block addresses.
The CD wrapper places the RedSea volume at sector 88 and includes the publisher
marker `TempleOS RedSea`. Its El Torito catalog is marked nonbootable. Use the
official TempleOS boot CD alongside it.

The packed `CISOPriDesc` structure is private to TempleOS: the root record starts
at byte 152, publisher ID at byte 314 and filesystem version at byte 877. These
are four bytes earlier than the corresponding standard ISO9660 fields. The
builder follows both the source structure and the actual original boot-image
bytes, since the standard offsets leave the disc unrecognized by TempleOS.

An optional ATA image uses one MBR partition of type `0x88`. Both formats are
immutable source media: all allocation bitmap bits are reserved. Copy files to a
normal TempleOS disk if editing them inside the guest.

## Signal integration

The [official libsignal README](https://github.com/signalapp/libsignal/blob/904c8dc8216073b078a5f2f5090e3da8382e6a92/README.md)
describes Rust implementations exposed through Java, Swift and TypeScript
bindings. It also states that external use is unsupported and APIs may change.
This project does not translate or reimplement those cryptographic primitives.

Instead, the host uses signal-cli's documented
[JSON-RPC interface](https://github.com/AsamK/signal-cli/blob/1e878f38457d22adb55d9b08b7f58543fdb92318/man/signal-cli-jsonrpc.5.adoc)
through private process pipes. Direct and group sends use the documented `send`
methods. Incoming messages arrive as `receive` notifications following an
explicit `subscribeReceive` request.

The reviewed signal-cli development snapshot calls itself `0.14.10-SNAPSHOT`.
That snapshot is a contract reference, not a promise that an arbitrarily old
installed binary will work. The upstream
[installation guide](https://github.com/AsamK/signal-cli/blob/1e878f38457d22adb55d9b08b7f58543fdb92318/README.md)
requires JRE 25 for its JVM build and advises keeping signal-cli current as
Signal's service evolves. A native signal-cli build has different runtime needs;
follow its release instructions.

## Boot image provenance

The guest verification uses the official image from
[templeos.org/Downloads/TempleOS.ISO](https://templeos.org/Downloads/TempleOS.ISO).
The downloaded image's SHA-256 is:

```text
5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26
```

This is the hash observed for the tested download, not a signature from the
author. The repository distributes its own source and packaging tools, not a
modified TempleOS system image.

## Licenses and naming

The existing repository license is GPL-3.0. Original application and bridge
code retain that license. TempleOS's reference source is public domain.
signal-cli and libsignal remain separately installed projects under their own
licenses. Signal is a trademark of its owners. This is an independent hobby
client, with no endorsement or official Signal support implied.
