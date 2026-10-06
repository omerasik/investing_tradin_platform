from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from trade_platform.single_instance_lock_v1 import (
    InstanceLockHeldError,
    exclusive_instance_lock_v1,
    instance_lock_status_v1,
)

_HOLDER = """
import sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from trade_platform.single_instance_lock_v1 import exclusive_instance_lock_v1
with exclusive_instance_lock_v1(Path({directory!r}), "recorder", description="child"):
    print("held", flush=True)
    time.sleep(60)
"""


class SingleInstanceLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_second_holder_is_refused_and_release_frees_the_lock(self) -> None:
        self.assertFalse(instance_lock_status_v1(self.directory, "recorder").held)
        with exclusive_instance_lock_v1(self.directory, "recorder", description="supervise") as owner:
            status = instance_lock_status_v1(self.directory, "recorder")
            self.assertTrue(status.held)
            self.assertEqual(status.owner, owner)
            with self.assertRaisesRegex(InstanceLockHeldError, "already running"), \
                    exclusive_instance_lock_v1(self.directory, "recorder"):
                pass
            with exclusive_instance_lock_v1(self.directory, "measurement"):  # other names are independent
                pass
        self.assertFalse(instance_lock_status_v1(self.directory, "recorder").held)
        self.assertFalse((self.directory / ".recorder.owner.json").exists())
        with exclusive_instance_lock_v1(self.directory, "recorder"):
            pass

    def test_lock_dies_with_its_process(self) -> None:
        src = str(Path(__file__).resolve().parents[1] / "src")
        child = subprocess.Popen(
            [sys.executable, "-c", _HOLDER.format(src=src, directory=str(self.directory))],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")  # type: ignore[union-attr]
            status = instance_lock_status_v1(self.directory, "recorder")
            self.assertTrue(status.held)
            # Not child.pid: a Windows venv python.exe is a launcher that spawns the interpreter.
            self.assertEqual((status.owner or {}).get("description"), "child")
            with self.assertRaises(InstanceLockHeldError), exclusive_instance_lock_v1(self.directory, "recorder"):
                pass
        finally:
            child.kill()
            child.wait(timeout=30)
            child.stdout.close()  # type: ignore[union-attr]
        deadline = time.monotonic() + 10
        while instance_lock_status_v1(self.directory, "recorder").held and time.monotonic() < deadline:
            time.sleep(0.1)
        with exclusive_instance_lock_v1(self.directory, "recorder") as owner:  # no stale lock to clear
            self.assertEqual(instance_lock_status_v1(self.directory, "recorder").owner, owner)

    def test_invalid_names_are_refused(self) -> None:
        for name in ("", "../x", "a b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                instance_lock_status_v1(self.directory, name)


if __name__ == "__main__":
    unittest.main()
