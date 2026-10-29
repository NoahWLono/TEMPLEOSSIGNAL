"""Tests for the VM's local transport and repeatable device layout."""

import argparse
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "run_qemu.py"
spec = importlib.util.spec_from_file_location("run_qemu", SCRIPT)
assert spec and spec.loader
launcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = launcher
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def args(self, **overrides):
        values = dict(qemu="qemu-system-x86_64", accel="tcg", memory=512,
                      boot_disk=False, templeos_iso=Path("/tmp/TempleOS.ISO"),
                      app_iso=Path("/tmp/app.ISO"), socket=Path("/tmp/private/serial.sock"),
                      disk=None, disk_format="qcow2", write_disk=False,
                      headless=False, qmp=None, debug_log=None)
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_no_guest_network_and_private_serial_transport(self):
        command = launcher.command(self.args())
        self.assertEqual(command[command.index("-nic") + 1], "none")
        self.assertEqual(command[command.index("-chardev") + 1],
                         "socket,id=signal,path=/tmp/private/serial.sock,reconnect=1")
        self.assertNotIn("-netdev", command)
        self.assertIn("chardev:signal", command)

    def test_app_disc_is_secondary_slave_and_readonly(self):
        command = launcher.command(self.args())
        drives = [command[i + 1] for i, value in enumerate(command) if value == "-drive"]
        self.assertIn("index=2,readonly=on", drives[0])
        self.assertIn("index=3,readonly=on", drives[1])

    def test_hard_disk_changes_are_ephemeral_by_default(self):
        command = launcher.command(self.args(disk=Path("/tmp/os.qcow2")))
        self.assertTrue(any("index=0,snapshot=on" in part for part in command))
        command = launcher.command(self.args(disk=Path("/tmp/os.qcow2"), write_disk=True))
        self.assertFalse(any("snapshot=on" in part for part in command))

    def test_qemu_suboption_escaping_preserves_literal_filename(self):
        self.assertEqual(launcher.qemu_value("/tmp/odd,readonly=off.ISO"),
                         "/tmp/odd,,readonly=off.ISO")

    def test_headless_qmp_and_debug_port_are_explicit(self):
        command = launcher.command(self.args(headless=True,
                                             qmp=Path("/tmp/private/qmp.sock"),
                                             debug_log=Path("/tmp/private/smoke.log")))
        self.assertEqual(command[command.index("-display") + 1], "none")
        self.assertIn("unix:/tmp/private/qmp.sock,server=on,wait=off", command)
        self.assertIn("isa-debugcon.iobase=0xe9", command)


if __name__ == "__main__":
    unittest.main()
