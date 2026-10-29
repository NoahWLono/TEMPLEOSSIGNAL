# Run TempleOS Signal

The application has two parts: HolyC inside an unmodified TempleOS VM, and a
Python bridge on the host. Demo mode runs locally without a Signal account.
Signal mode uses a separately installed and linked `signal-cli` account.

## Requirements

Use a Linux host for the tested path. The host needs Python 3.11 or later and
`qemu-system-x86_64`. Install QEMU's graphical display package if your distribution
separates it from its base emulator. The bridge and image builder have no
third-party Python dependencies. Host commands below work in both Bash and Fish.

Obtain the official [TempleOS boot ISO](https://templeos.org/Downloads/TempleOS.ISO).
Keep it outside the repository. The image used for verification and its observed
SHA-256 are recorded in [UPSTREAM.md](UPSTREAM.md).

```sh
git clone https://github.com/NoahWLono/TEMPLEOSSIGNAL.git
cd TEMPLEOSSIGNAL
python3 scripts/build_media.py
```

This creates `build/TEMPLEOS_SIGNAL.ISO`, a RedSea data disc. It is not an OS
installer and does not boot by itself. Rebuild after changing HolyC source with
`python3 scripts/build_media.py --force`.

## 1. Start the local demo

In the first host terminal, from the repository root:

```sh
mkdir -m 700 /tmp/temple-signal-run
python3 -m temple_signal --demo --socket /tmp/temple-signal-run/serial.sock
```

Use a fresh private directory name if that one already exists from another run.
The bridge creates its UNIX socket there. The mode-700 directory is necessary
because the serial connection carries plaintext messages.

In a second terminal, from the same repository root, substitute your ISO path:

```sh
python3 scripts/run_qemu.py \
  --templeos-iso /path/to/TempleOS.ISO \
  --socket /tmp/temple-signal-run/serial.sock
```

The launcher attaches the TempleOS boot ISO as secondary IDE master and the
application CD as secondary IDE slave, exposes COM1 to the bridge, and disables
all virtual network adapters. TCG emulation is the default. `--accel kvm` is an
optional faster Linux configuration if your machine already supports it.

## 2. Load the HolyC application

Boot the live TempleOS CD and decline installation if you only want a demo.
After its startup questions, open a normal command prompt. The boot CD is
usually `T:`. To mount the second CD as `U:`, enter this HolyC:

```c
CBlkDev *ts_app_cd=BlkDevNextFreeSlot('U',BDT_ATAPI);
ts_app_cd->base0=0x170;
ts_app_cd->base1=0;
ts_app_cd->unit=1;
BlkDevAdd(ts_app_cd,,FALSE,TRUE);
Cd("U:/Signal");
#include "Load.HC"
```

If `U:` is already mounted, use `Cd("U:/Signal");` directly. `DrvRep;` lists
existing drives. On a different IDE layout use `Mount;` and select the
application CD reported by TempleOS rather than copying these port addresses.

The client compiles in TempleOS's own HolyC compiler. Demo mode is labeled in
the interface. A demo response proves the guest/host path is operating; it does
not contact Signal. Close the application through its onscreen exit key before
reloading its source. Restart the VM or use a fresh task after editing compiler
definitions, since repeated includes allocate additional HolyC code.

## 3. Link an actual Signal account on the host

Install a current [signal-cli release](https://github.com/AsamK/signal-cli/releases)
using the instructions for your host. Its JVM and native builds have different
runtime requirements. See the [reviewed upstream requirements](UPSTREAM.md).

In a real terminal on the host:

```sh
signal-cli link --name TempleOS-Signal
```

Scan the QR code with Signal's **Settings > Linked devices** flow on your phone.
This adds a linked device. Do not use `register` for an account already active
on your phone: upstream documents that registration can replace the existing
primary client. Link and manage the account outside the TempleOS guest.

The account selector is its full international phone number or, for numberless
accounts, the account ACI. You can inspect local accounts with:

```sh
signal-cli -o json listAccounts
```

Stop the demo bridge, then start the Signal bridge with your actual account:

```sh
python3 -m temple_signal \
  --account +15551234567 \
  --socket /tmp/temple-signal-run/serial.sock
```

The number above is a placeholder. Start a fresh guest client after changing
backends so it performs a new `HELLO`. Do not run another signal-cli process
against the same account data while the bridge owns it. The bridge launches its
own JSON-RPC subprocess and continuously receives incoming Signal events.

Use Note to Self for the first real send, then verify it appears on your phone.
Send a reply from your phone and confirm it appears inside TempleOS. These two
checks require your linked account. No actual account credentials or private
messages belong in this repository.

### Identity verification

The bridge launches signal-cli with `--trust-new-identities never`. Existing
verified identities continue to work. An unknown or changed identity requires
manual verification on the host. Stop the bridge first, inspect the identity
with `signal-cli -a ACCOUNT listIdentities`, and compare the safety number with
the person through a trusted channel. Only then use signal-cli's documented
`trust --verified-safety-number` operation. Restart the bridge afterwards.

The TempleOS application has no button that silently trusts every identity.
An accepted send is shown as sent, not delivered or read. A timeout may mean a
send succeeded; check another linked device before manually resending.

## Optional existing TempleOS disk

```sh
python3 scripts/run_qemu.py \
  --templeos-iso /path/to/TempleOS.ISO \
  --socket /tmp/temple-signal-run/serial.sock \
  --disk /path/to/templeos.qcow2 --boot-disk
```

By default, disk writes go to a temporary QEMU snapshot and disappear when the
VM exits. Add `--write-disk` only when you want persistent changes to that image.
Use `--disk-format raw` for an existing raw image. These options do not create,
format or repartition a disk.

The media builder can also produce a nonbootable ATA source image:

```sh
python3 scripts/build_media.py --format disk --output build/Signal.img
```

That image contains `/Signal` on one RedSea partition. It is a read-only source
distribution, not an installed OS or a writable message store.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| QEMU reports a missing GUI backend | Install the distribution's QEMU GUI/display package; headless tests instead use `--headless --qmp ...` |
| Guest cannot see `/Signal` | Mount the application CD as `U:`; use the RedSea image made by this repository, not a generic ISO9660 disc |
| Client times out before `READY` | Start the bridge first, confirm both commands use the same socket path, and check COM1 uses `0x3F8` |
| Bridge rejects socket permissions | The containing directory must be owned by you and have mode 700 |
| Real backend will not start | Run `signal-cli --version`, confirm the account is linked, and stop other processes holding the same data directory |
| New contact cannot be messaged | Review and verify its identity with signal-cli on the host; do not enable blanket identity trust |
| Emoji or non-Latin text becomes `?` | TempleOS's bitmap-font display is intentionally limited; the transport itself is UTF-8 |
| Expiring messages are replaced by a notice | Version 1 suppresses disappearing and view-once content instead of retaining it in an interface without those lifetime guarantees |
| Message appears as an ambiguous send | The reply was lost or the backend failed; the program will not automatically send it twice |

## Automated checks

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s scripts/tests -v
python3 -m compileall -q temple_signal scripts
```

See [TESTING.md](TESTING.md) for the exact distinction between host tests, real
HolyC execution, demo transport checks and live-account verification.
