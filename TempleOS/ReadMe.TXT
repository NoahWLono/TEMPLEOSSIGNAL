TEMPLEOSSIGNAL 0.1.0
https://github.com/NoahWLono/TEMPLEOSSIGNAL

Native HolyC text client for original TempleOS 5.03.
640x480 pixels, 16 colors, COM1 at 115200 baud, 8N1.

HOST REQUIREMENT
TempleOS has no networking. Run the supplied Python bridge on your
Linux/macOS host. It talks to signal-cli, which holds the Signal account
and cryptographic keys. The local serial link carries plaintext.
The VM and host together are the messaging endpoint.

The DEMO badge means an offline echo simulator. No Signal messages are
sent in demo mode. See the project's main README for the host setup,
device linking, QEMU commands, and mounting this data disc.

START
From a fresh TempleOS terminal, after mounting the app disc as U:

  Cd("U:/Signal");
  #include "Load.HC"

Load.HC compiles the source and opens the application. After exiting,
TSSignal; runs the compiled application again. Use a new terminal
before including the library a second time.

KEYBOARD
F2          Contacts
F3          Groups
F4          Note to Self
F5          Reconnect to the host bridge
F6          Clear the visible transcript
Tab         Switch target and message fields
Enter       Select a list entry, finish a target, or send a message
Up/Down     Select a contact or group
PgUp/PgDn   Page lists or scroll the transcript
Ctrl-U      Clear the focused input field
Backspace   Remove the final input character
Escape      Close a picker, then exit the application

Messages from all conversations appear in one activity view.
Group IDs and Note-to-Self markers identify their conversation context.
Outgoing messages support printable ASCII, up to 1024 bytes.
Incoming Unicode characters outside the font are shown as '?'.
Transcript storage is limited to 256 history lines in RAM.
No attachment, call, reaction, edit, or disappearing-message support.
The SENT response is not a delivery/read receipt.

A send timeout has UNKNOWN status. The client does not retry sends.
Check another linked device before manually sending the draft again.

NATIVE TESTS
Use the host bridge's --demo mode and a fresh TempleOS terminal:

  Cd("U:/Signal");
  #include "Test.HC"

This runs native codec tests and a serial round trip through the offline
bridge. It refuses to send if the backend reports SIGNAL.
See scripts/smoke_qemu.py in the repository for headless automation.
Tests write PASS/FAIL markers to QEMU debug port 0xE9.

LICENSE
All guest application source is on this disc in .HC files.
The project is licensed under GPL version 3; see COPYING.TXT.
This is an unofficial independent client, not a Signal product.
TempleOS itself is Terry A. Davis's public-domain operating system.
