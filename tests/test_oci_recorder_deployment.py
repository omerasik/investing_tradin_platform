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
import json
import re
import shlex
import shutil
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
#: install.sh points this at a Python >= 3.11; the system python3 is never used.
INTERPRETER_LINK = "/opt/trade-capture/python"


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
            self.assertEqual(argv[:4], [INTERPRETER_LINK, "-I", "-B", "-u"], name)
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
            # Interpreter start-up (distro sitecustomize, .pth hooks) is not the CLI's.
            "before = set(sys.modules)\n"
            f"spec = importlib.util.spec_from_file_location('cli', {str(SCRIPT)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "tops = {name.split('.')[0] for name in set(sys.modules) - before}\n"
            "print(' '.join(sorted(tops - set(sys.stdlib_module_names) - {'__main__', 'cli'})))\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.split(), ["trade_platform"])


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "capture_host_probe_oci", DEPLOY / "capture_host_probe.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("capture_host_probe_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = _load_probe()


class OracleLinuxHostTests(unittest.TestCase):
    """The real VM: Oracle Linux 9, system python3 3.9, SELinux enforcing, busy CPUs."""

    def test_installer_never_selects_an_interpreter_below_3_11(self) -> None:
        text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        self.assertIn("sys.version_info < (3, 11)", text)
        candidates = text.split("for candidate in", 1)[1].split("; do", 1)[0].split()
        self.assertEqual(candidates[1], "/usr/bin/python3.12")
        self.assertEqual(candidates[-1], "/usr/bin/python3")  # last resort, still version-checked
        self.assertIn(f"/opt/trade-capture/python.new {INTERPRETER_LINK}", text)
        self.assertNotIn("dnf install", text.replace("dnf install python3.12 (python3", ""))

    def test_installer_changes_nothing_host_wide_beyond_its_own_files(self) -> None:
        text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        for forbidden in ("restart systemd-journald", "firewall-cmd", "iptables", "mkfs", "docker",
                          "systemctl start", "systemctl restart", "enable --now trade"):
            self.assertNotIn(forbidden, text.replace(
                "systemctl enable --now trade-capture-r0.service", ""), forbidden)
        self.assertIn("restorecon -RF /opt/trade-capture /srv/trade-capture", text)
        self.assertIn("chrony-wait.service", text)

    def test_recorders_and_probe_share_one_cpu_weight(self) -> None:
        weights = {
            name: _unit(name)["Service"]["CPUWeight"]
            for name in (*RECORDER_UNITS, "trade-capture-health.service")
        }
        self.assertEqual(set(weights.values()), {"1000"}, weights)

    def test_health_runs_through_a_bin_t_interpreter(self) -> None:
        argv = shlex.split(_unit("trade-capture-health.service")["Service"]["ExecStart"])
        self.assertEqual(argv[0], "/usr/bin/bash")
        health = (DEPLOY / "trade-capture-health.sh").read_text(encoding="utf-8")
        self.assertIn(f"PYTHON={INTERPRETER_LINK}", health)
        self.assertNotIn("/usr/bin/python3 ", health)
        self.assertIn("capture_host_probe.py", health)


class CaptureHostProbeTests(unittest.TestCase):
    def test_percentile_is_nearest_rank(self) -> None:
        values = [float(value) for value in range(1, 101)]
        self.assertEqual(PROBE.percentile(values, 0.5), 50.0)
        self.assertEqual(PROBE.percentile(values, 0.99), 99.0)
        self.assertEqual(PROBE.percentile([7.0], 0.99), 7.0)
        with self.assertRaises(ValueError):
            PROBE.percentile([], 0.5)

    def test_newest_recorder_clock_sample_wins_and_torn_lines_are_skipped(self) -> None:
        def sample(arrival: int, offset: int, bound: int) -> str:
            detail = json.dumps({"offset_estimate_nanos": offset, "offset_bound_nanos": bound})
            return json.dumps(
                {"kind": "CLOCK_OFFSET_SAMPLE", "arrival_utc_nanos": arrival, "detail": detail}
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "v1" / "a" / "session=1"
            new = root / "v1" / "b" / "session=2"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / "lifecycle.ndjson").write_text(sample(10, 400_000_000, 5_000_000) + "\n")
            (new / "lifecycle.ndjson").write_text(
                sample(20, 2_000_000, 1_000_000) + "\n"
                + json.dumps({"kind": "SESSION_STARTED", "arrival_utc_nanos": 30}) + "\n"
                + '{"kind": "CLOCK_OFF'
            )
            latest = PROBE.latest_clock_sample(root)
            self.assertEqual(PROBE.latest_clock_sample(root / "missing"), None)
        assert latest is not None
        self.assertEqual(latest["arrival_utc_nanos"], 20.0)
        self.assertEqual(latest["offset_ms"], 2.0)
        self.assertEqual(latest["bound_ms"], 1.0)

    def test_clock_threshold_matches_the_cli_warning(self) -> None:
        match = re.search(
            r'clock\.add_argument\("--warn-ms", type=float, default=([0-9.]+)\)',
            SCRIPT.read_text(encoding="utf-8"),
        )
        assert match is not None
        self.assertEqual(PROBE.CLOCK_WORST_MS, float(match.group(1)))


@unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze not available")
class SystemdAnalyzeVerifyTests(unittest.TestCase):
    def test_units_parse_without_unknown_or_invalid_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for unit in (DEPLOY / "systemd").iterdir():
                # The absolute executables must exist where verify runs.
                text = unit.read_text(encoding="utf-8").replace(INTERPRETER_LINK, sys.executable)
                (Path(directory) / unit.name).write_text(text, encoding="utf-8")
            result = subprocess.run(
                ["systemd-analyze", "verify", *sorted(str(path) for path in Path(directory).iterdir())],
                capture_output=True, text=True, check=False,
            )
        output = result.stdout + result.stderr
        for marker in ("Unknown key", "Unknown section", "Failed to parse", "Invalid", "bad-setting"):
            self.assertNotIn(marker, output, output)


if __name__ == "__main__":
    unittest.main()
