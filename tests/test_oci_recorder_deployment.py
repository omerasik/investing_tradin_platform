"""OCI-1: the Linux deployment artifacts stay bound to the capture CLI they run.

The systemd units in ``deploy/oci/systemd`` must never restart a recorder that
stopped at the disk floor or found its root already locked (both would loop or
create a second writer), must stop it with SIGINT so lanes finalize, and must run
the unchanged CLI with its own contracts on separate roots. The recorder VM gets
no third-party package, so the CLI's import closure must stay standard library.
"""

from __future__ import annotations

import configparser
import importlib.util
import io
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "oci"
SCRIPT = ROOT / "scripts" / "capture_bybit_public.py"
RECORDER_UNITS = {"trade-capture-r0.service": "supervise", "trade-capture-u2.service": "universe"}
ARCHIVE_MOUNT = "/srv/trade-capture"


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_bybit_public_oci", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("capture_cli_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # systemd keys are case-sensitive
    parser.read_string((DEPLOY / "systemd" / name).read_text(encoding="utf-8"))
    return parser


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (DEPLOY / "capture.env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


class RecorderUnitTests(unittest.TestCase):
    def test_restart_never_loops_on_disk_floor_or_held_lock(self) -> None:
        for name in RECORDER_UNITS:
            service = _unit(name)["Service"]
            self.assertEqual(service["Restart"], "on-failure", name)
            self.assertEqual(
                {int(code) for code in service["RestartPreventExitStatus"].split()},
                {CLI.EXIT_DISK_FLOOR, CLI.EXIT_ALREADY_RUNNING},
                name,
            )
            self.assertNotIn(0, {CLI.EXIT_DISK_FLOOR, CLI.EXIT_ALREADY_RUNNING})
            unit = _unit(name)["Unit"]
            self.assertGreater(int(unit["StartLimitBurst"]), 0, name)
            self.assertGreater(int(unit["StartLimitIntervalSec"]), 0, name)

    def test_stop_is_an_operator_interrupt_and_runs_unprivileged(self) -> None:
        for name in RECORDER_UNITS:
            service = _unit(name)["Service"]
            self.assertEqual(service["KillSignal"], "SIGINT", name)
            self.assertEqual(service["User"], "trade-capture", name)
            self.assertEqual(service["NoNewPrivileges"], "yes", name)
            self.assertEqual(service["ProtectSystem"], "strict", name)
            self.assertEqual(service["ReadWritePaths"], ARCHIVE_MOUNT, name)
            self.assertEqual(service["CapabilityBoundingSet"], "", name)

    def test_units_wait_for_the_archive_volume_and_clock_sync(self) -> None:
        for name in RECORDER_UNITS:
            unit = _unit(name)["Unit"]
            self.assertEqual(unit["AssertPathIsMountPoint"], ARCHIVE_MOUNT, name)
            self.assertEqual(unit["RequiresMountsFor"], ARCHIVE_MOUNT, name)
            self.assertIn("time-sync.target", unit["After"].split(), name)

    def test_exec_start_runs_the_unchanged_cli_on_its_own_root(self) -> None:
        roots = set()
        for name, command in RECORDER_UNITS.items():
            argv = shlex.split(_unit(name)["Service"]["ExecStart"])
            self.assertEqual(argv[:4], ["/usr/bin/python3", "-I", "-B", "-u"], name)
            self.assertEqual(argv[4], "/opt/trade-capture/current/scripts/capture_bybit_public.py")
            self.assertEqual(argv[5], "--root", name)
            roots.add(argv[6])
            self.assertEqual(argv[7], command, name)
            options = argv[8:]
            self.assertEqual(options[options.index("--segment-seconds") + 1], "3600", name)
            self.assertEqual(options[options.index("--min-free-gb") + 1], "${MIN_FREE_GB}", name)
        self.assertEqual(roots, {"${R0_ROOT}", "${U2_ROOT}"})

    def test_env_example_separates_roots_and_keeps_the_floor(self) -> None:
        env = _env_example()
        self.assertNotEqual(env["R0_ROOT"], env["U2_ROOT"])
        for key in ("R0_ROOT", "U2_ROOT"):
            self.assertTrue(env[key].startswith(f"{ARCHIVE_MOUNT}/validation/"), key)
        self.assertEqual(float(env["MIN_FREE_GB"]), CLI.DEFAULT_MIN_FREE_GIB)
        for key in env:
            for word in ("KEY", "SECRET", "TOKEN", "PASSWORD", "LIVE"):
                self.assertNotIn(word, key.upper())

    def test_artifacts_keep_lf_endings_and_shebangs(self) -> None:
        for path in DEPLOY.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"\r", path.read_bytes(), str(path))
        for script in ("install.sh", "trade-capture-health.sh"):
            self.assertTrue(
                (DEPLOY / script).read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")
            )


class RecorderExitCodeTests(unittest.TestCase):
    def test_floor_above_free_space_exits_disk_floor_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as root, \
                patch.object(CLI, "request_keep_awake_v1", return_value=False), \
                redirect_stdout(io.StringIO()):
            code = CLI.main(["--root", root, "universe", "--min-free-gb", "1e12"])
        self.assertEqual(code, CLI.EXIT_DISK_FLOOR)

    def test_second_recorder_on_a_locked_root_exits_already_running(self) -> None:
        with tempfile.TemporaryDirectory() as root, \
                CLI.exclusive_instance_lock_v1(Path(root), CLI.RECORDER_LOCK_NAME), \
                redirect_stdout(io.StringIO()):
            code = CLI.main(["--root", root, "supervise", "--segment-seconds", "3600"])
        self.assertEqual(code, CLI.EXIT_ALREADY_RUNNING)


class RecorderImportClosureTests(unittest.TestCase):
    def test_capture_cli_needs_only_the_standard_library(self) -> None:
        probe = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('cli', {str(SCRIPT)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "tops = {name.split('.')[0] for name in sys.modules}\n"
            "print(' '.join(sorted(tops - set(sys.stdlib_module_names) - {'__main__', 'cli'})))\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.split(), ["trade_platform"])


if __name__ == "__main__":
    unittest.main()
