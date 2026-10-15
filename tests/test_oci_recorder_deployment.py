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
        # Shipped text artifacts only: importing capture_host_probe.py in these
        # tests can leave generated bytecode beside it, which is not deployed.
        shipped = [
            path for path in DEPLOY.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.relative_to(DEPLOY).parts
            and path.suffix not in {".pyc", ".pyo"}
        ]
        names = {path.relative_to(DEPLOY).as_posix() for path in shipped}
        self.assertLessEqual(
            {"install.sh", "trade-capture-health.sh", "capture_host_probe.py",
             *(f"systemd/{unit}" for unit in RECORDER_UNITS),
             "systemd/trade-capture-health.service", "systemd/trade-capture-health.timer",
             "trade-capture-coverage.sh", "systemd/trade-capture-coverage.service",
             "systemd/trade-capture-coverage.timer"},
            names,
        )
        for path in shipped:
            self.assertNotIn(b"\r", path.read_bytes(), str(path))
        for script in ("install.sh", "trade-capture-health.sh", "trade-capture-coverage.sh"):
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

    def test_health_never_execs_chronyc_under_no_new_privileges(self) -> None:
        # 2026-10-14: every snapshot logged an nnp_transition AVC (initrc_t ->
        # chronyc_t). The fix drops the informational call; it never widens
        # SELinux policy or drops the unit's sandboxing.
        service = _unit("trade-capture-health.service")["Service"]
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["ProtectSystem"], "strict")
        self.assertEqual(service["CapabilityBoundingSet"], "")
        commands = [
            line for line in (DEPLOY / "trade-capture-health.sh").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertFalse([line for line in commands if "chronyc" in line])
        self.assertTrue([line for line in commands if "timedatectl show -p NTPSynchronized" in line])

    def test_health_separates_service_state_from_coverage_evidence(self) -> None:
        health = (DEPLOY / "trade-capture-health.sh").read_text(encoding="utf-8")
        self.assertIn('echo "service        OK"', health)
        self.assertIn('echo "service        ATTENTION"', health)
        self.assertNotIn('echo "verdict', health)
        facts = [line for line in health.splitlines() if "universe-hours" in line and "$CLI" in line]
        self.assertEqual(len(facts), 1)
        self.assertIn('--root "$U2_ROOT"', facts[0])
        # Facts only: no hand-off bound, and they never set the exit code.
        self.assertNotIn("max-unproven-seconds", health)
        self.assertNotIn("fail=1", facts[0])
        self.assertIn("NOT JUDGED HERE", health)
        self.assertTrue(health.rstrip().endswith('exit "$fail"'))


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


def _load_offsite() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_offsite_oci", DEPLOY / "capture_offsite.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("capture_offsite_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


OFFSITE = _load_offsite()
PAR = "https://objectstorage.eu-amsterdam-1.oraclecloud.com/p/SECRETTOKEN/n/ns/b/trade-capture-backup/o/"


class _MemoryStore:
    """An object store with OCI's create-only semantics, in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.gets: list[str] = []

    def put_new(self, name: str, body: bytes) -> bool:
        self.puts.append(name)
        if name in self.objects:
            return False
        self.objects[name] = body
        return True

    def get(self, name: str) -> bytes:
        self.gets.append(name)
        return self.objects[name]

    def list_names(self, prefix: str) -> list[str]:
        return sorted(name for name in self.objects if name.startswith(prefix))


#: The values trade-capture-backup.service passes (README 7a).
BUDGET = {"horizon_days": 4, "budget_window_days": 7, "budget_bytes": 8_000_000_000}
DAY = 86_400


def _retar(body: bytes, *, rename: tuple[str, str] | None = None, mutate=None) -> bytes:
    """Rewrite a partition tar, optionally moving its members or changing one file."""
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(body)) as source, \
            tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for member in source.getmembers():
            data = source.extractfile(member).read()  # type: ignore[union-attr]
            if mutate is not None:
                data = mutate(member.name, data)
            if rename is not None:
                member.name = member.name.replace(*rename)
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class _ArchiveTest(unittest.TestCase):
    """``r0`` holds production-contract partitions, ``u2`` universe-contract ones."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "r0"
        self.universe_root = self.base / "u2"
        self.state = self.base / "state"
        self.state.mkdir()

    def _complete(self, *, compact: bool = True, count: int = 3, universe: bool = False) -> Path:
        from tests.test_first_party_capture_archive_v1 import CONTRACT, _complete_partition
        from trade_platform.first_party_capture_archive_v1 import compact_partition_v1
        from trade_platform.first_party_capture_authority_v1 import (
            first_party_bybit_universe_contracts_v1,
        )

        contract = first_party_bybit_universe_contracts_v1()[0] if universe else CONTRACT
        root = self.universe_root if universe else self.root
        directory = _complete_partition(root, contract=contract, count=count)
        if compact:
            compact_partition_v1(directory)
        return directory

    def _name(self, directory: Path) -> str:
        universe = self.universe_root in directory.parents
        root = self.universe_root if universe else self.root
        return f"{'u2' if universe else 'r0'}/{directory.relative_to(root).as_posix()}.tar"

    def _backup(self, store: _MemoryStore, roots=None, **options: object):
        settings = {**BUDGET, **options}
        roots = [("r0", self.root), ("u2", self.universe_root)] if roots is None else roots
        return OFFSITE.run_backup(roots, store, self.state, **settings)

    def _ledger(self) -> list[dict]:
        text = (self.state / OFFSITE.LEDGER_NAME).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]


class OffsiteBackupTests(_ArchiveTest):
    def test_only_complete_compacted_partitions_are_uploaded_once(self) -> None:
        from datetime import date
        from uuid import uuid4

        from trade_platform.first_party_capture_archive_v1 import CapturePartitionWriterV1
        from trade_platform.first_party_capture_authority_v1 import (
            first_party_bybit_capture_contract_v1,
        )

        done = self._complete()
        universe = self._complete(universe=True)
        raw = self._complete(compact=False)
        open_writer = CapturePartitionWriterV1(
            root=self.root, contract=first_party_bybit_capture_contract_v1(), session_id=uuid4(),
            day=date(2026, 9, 23),
        )
        self.addCleanup(open_writer.close_without_finalizing)
        store = _MemoryStore()
        report = self._backup(store)
        self.assertEqual(report.uploaded, (self._name(done), self._name(universe)))
        self.assertEqual(report.pending_compaction, (self._name(raw),))
        self.assertEqual(report.skipped_not_complete, 1)  # the OPEN partition
        self.assertEqual(report.conflicts, ())
        self.assertTrue(report.ok)
        self.assertEqual(report.ledger_states[OFFSITE.UPLOADED], 2)
        self.assertEqual(report.ledger_states[OFFSITE.RESTORE_VERIFIED], 0)
        # The ledger makes the next run send nothing.
        again = self._backup(store)
        self.assertEqual((again.uploaded, again.conflicts), ((), ()))
        self.assertEqual(store.puts, [self._name(done), self._name(universe)])

    def test_the_tar_is_deterministic_and_holds_exactly_the_partition(self) -> None:
        import tarfile

        directory = self._complete()
        relative = directory.relative_to(self.root).as_posix()
        first = OFFSITE.partition_tar_bytes(directory, relative)
        self.assertEqual(first, OFFSITE.partition_tar_bytes(directory, relative))
        with tarfile.open(fileobj=io.BytesIO(first)) as archive:
            members = archive.getmembers()
        self.assertEqual(
            sorted(member.name for member in members),
            sorted(f"{relative}/{path.name}" for path in directory.iterdir()),
        )
        self.assertTrue(all(m.mtime == 0 and m.uid == 0 and m.isreg() for m in members))

    def test_an_existing_object_is_never_overwritten_nor_counted_as_backed_up(self) -> None:
        directory = self._complete()
        store = _MemoryStore()
        name = self._name(directory)
        store.objects[name] = b"uploaded by an earlier, lost ledger"
        report = self._backup(store)
        self.assertEqual((report.uploaded, report.conflicts), ((), (name,)))
        self.assertEqual(store.objects[name], b"uploaded by an earlier, lost ledger")
        self.assertEqual(report.unverified_in_window, (name,))
        self.assertFalse(report.ok)  # fail closed: the remote content is unknown
        (line,) = self._ledger()
        self.assertEqual(line["outcome"], OFFSITE.REMOTE_EXISTS_UNVERIFIED)
        self.assertNotIn(OFFSITE.UPLOADED, json.dumps(line))
        # Idempotent: never re-sent, but it keeps the run in ATTENTION ...
        again = self._backup(store)
        self.assertEqual(store.puts, [name])
        self.assertEqual(again.unverified_in_window, (name,))
        self.assertFalse(again.ok)
        # ... until the bucket's lifecycle window has passed; it is never promoted.
        later = self._backup(store, now=line["at"] + 8 * DAY)
        self.assertTrue(later.ok)
        self.assertEqual(later.ledger_states[OFFSITE.REMOTE_EXISTS_UNVERIFIED], 1)
        self.assertEqual(later.ledger_states[OFFSITE.UPLOADED], 0)

    def test_a_pre_hardening_already_present_line_reads_unverified(self) -> None:
        directory = self._complete()
        name = self._name(directory)
        legacy = {"object": name, "manifest_content_hash": json.loads(
            (directory / OFFSITE.MANIFEST_FILE_NAME).read_text(encoding="utf-8"))["manifest_content_hash"],
            "tar_sha256": "0" * 64, "bytes": 10, "outcome": "ALREADY_PRESENT", "at": 2_000_000_000}
        (self.state / OFFSITE.LEDGER_NAME).write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        report = self._backup(_MemoryStore(), now=2_000_000_000 + 60)
        self.assertEqual(report.ledger_states[OFFSITE.REMOTE_EXISTS_UNVERIFIED], 1)
        self.assertEqual(report.unverified_in_window, (name,))
        self.assertFalse(report.ok)

    def test_a_torn_ledger_line_never_swallows_the_next_entry(self) -> None:
        first = self._complete()
        store = _MemoryStore()
        self._backup(store)
        with (self.state / OFFSITE.LEDGER_NAME).open("a", encoding="utf-8") as handle:
            handle.write('{"object": "r0/torn')  # a crash mid-append
        second = self._complete()
        report = self._backup(store)
        self.assertEqual(report.uploaded, (self._name(second),))
        ledger, inconsistent = OFFSITE.read_ledger(self.state / OFFSITE.LEDGER_NAME)
        self.assertEqual(sorted(ledger), sorted([self._name(first), self._name(second)]))
        self.assertEqual(inconsistent, ())

    def test_a_tampered_partition_is_never_uploaded(self) -> None:
        directory = self._complete()
        with (directory / "lifecycle.ndjson").open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
        store = _MemoryStore()
        report = self._backup(store)
        self.assertEqual((report.uploaded, report.skipped_not_complete), ((), 1))
        self.assertEqual(store.objects, {})

    def test_a_partition_off_its_canonical_path_or_label_is_never_uploaded(self) -> None:
        from uuid import uuid4

        directory = self._complete()
        moved = directory.with_name(f"session={uuid4()}")
        directory.rename(moved)  # internally COMPLETE, but its path misstates its session
        store = _MemoryStore()
        report = self._backup(store)
        self.assertEqual(report.failures, ((self._name(moved), "partition_is_not_at_its_canonical_path"),))
        self.assertEqual(store.puts, [])
        # A production partition under the universe label is refused as well.
        misfiled = self._complete()
        self.state = self.base / "state-2"
        self.state.mkdir()
        report = self._backup(store, roots=[("u2", self.root)])
        self.assertIn(
            (f"u2/{misfiled.relative_to(self.root).as_posix()}.tar",
             "partition_contract_is_not_approved_for_its_root_label"),
            report.failures,
        )
        self.assertEqual(store.puts, [])
        with self.assertRaises(OFFSITE.OffsiteError):
            self._backup(store, roots=[("x9", self.root)])

    def test_the_budget_withholds_uploads_and_alerts(self) -> None:
        directories = [self._complete() for _ in range(3)]
        store = _MemoryStore()
        one = len(OFFSITE.partition_tar_bytes(
            directories[0], directories[0].relative_to(self.root).as_posix()))
        report = self._backup(store, budget_bytes=2 * one + one // 2)
        self.assertEqual((len(report.uploaded), report.over_budget), (2, 1))
        self.assertFalse(report.ok)
        self.assertEqual(report.window_bytes, 2 * one)
        # Bytes leave the estimate once the window (lifecycle + slack) has passed.
        ledger_path = self.state / OFFSITE.LEDGER_NAME
        aged = [{**line, "at": line["at"] - 7 * DAY - 1} for line in self._ledger()]
        ledger_path.write_text("".join(json.dumps(line) + "\n" for line in aged), encoding="utf-8")
        later = self._backup(store, budget_bytes=2 * one + one // 2)
        self.assertEqual((len(later.uploaded), later.over_budget, later.window_bytes), (1, 0, one))
        self.assertTrue(later.ok)

    def test_partitions_beyond_the_horizon_are_never_sent(self) -> None:
        self._complete()
        store = _MemoryStore()
        report = self._backup(store, now=__import__("time").time() + 5 * DAY)
        self.assertEqual((report.uploaded, report.beyond_horizon), ((), 1))
        self.assertTrue(report.ok)
        self.assertEqual(store.puts, [])
        for bad in ({"horizon_days": 0}, {"horizon_days": 8}, {"budget_bytes": 0}):
            with self.assertRaises(OFFSITE.OffsiteError):
                self._backup(store, **bad)

    def test_a_long_uncompacted_partition_fails_the_run(self) -> None:
        import time as clock

        self._complete(compact=False)
        report = self._backup(_MemoryStore(), now=clock.time() + OFFSITE.STALE_UNCOMPACTED_SECONDS + 60)
        self.assertEqual(len(report.stale_uncompacted), 1)
        self.assertFalse(report.ok)

    def test_uploads_per_run_are_bounded(self) -> None:
        for _ in range(3):
            self._complete()
        store = _MemoryStore()
        report = self._backup(store, max_uploads=2)
        self.assertEqual((len(report.uploaded), report.deferred), (2, 1))
        self.assertEqual(len(self._backup(store).uploaded), 1)

    def test_a_transport_failure_is_reported_and_retried_next_run(self) -> None:
        directory = self._complete()

        class Broken(_MemoryStore):
            def put_new(self, name: str, body: bytes) -> bool:
                raise OFFSITE.OffsiteError("put_failed:http_401")

        report = self._backup(Broken())
        self.assertFalse(report.ok)
        self.assertEqual(report.failures[0][1], "put_failed:http_401")
        self.assertFalse((self.state / OFFSITE.LEDGER_NAME).exists())
        self.assertEqual(len(self._backup(_MemoryStore()).uploaded), 1)
        self.assertTrue(directory.exists())


class OffsiteRestoreTests(_ArchiveTest):
    def test_restore_replays_every_object_into_an_empty_root(self) -> None:
        directory = self._complete(count=5)
        universe = self._complete(count=2, universe=True)
        store = _MemoryStore()
        self._backup(store)
        restored = self.base / "restored"
        report = OFFSITE.run_restore_verify(store, restored)
        self.assertEqual(report.failures, ())
        self.assertEqual([item.record_count for item in report.verified], [5, 2])
        self.assertEqual([item.ledger_match for item in report.verified], [False, False])
        for label, root, source in (("r0", self.root, directory), ("u2", self.universe_root, universe)):
            for path in source.iterdir():
                self.assertEqual(
                    path.read_bytes(), (restored / label / source.relative_to(root) / path.name).read_bytes()
                )
        with self.assertRaises(OFFSITE.OffsiteError):
            OFFSITE.run_restore_verify(store, restored)  # never into a non-empty root

    def test_a_corrupted_object_fails_verification(self) -> None:
        directory = self._complete()
        store = _MemoryStore()
        self._backup(store)
        (name,) = store.objects

        def corrupt(member: str, data: bytes) -> bytes:
            return data[:-8] + bytes(8) if member.endswith("records.ndjson.gz") else data

        store.objects[name] = _retar(store.objects[name], mutate=corrupt)
        report = OFFSITE.run_restore_verify(store, self.base / "restored")
        self.assertEqual(report.verified, ())
        self.assertEqual([failed for failed, _ in report.failures], [name])
        self.assertIn(directory.relative_to(self.root).as_posix(), name)

    def test_a_substituted_object_that_still_replays_is_caught_by_the_ledger(self) -> None:
        self._complete()
        store = _MemoryStore()
        self._backup(store)
        (name,) = store.objects
        # Trailing bytes after the end-of-archive blocks: every member still
        # extracts and replays identically, but the object is not what was sent.
        store.objects[name] += b"\0" * 1024
        blind = OFFSITE.run_restore_verify(store, self.base / "blind")
        self.assertEqual([item.name for item in blind.verified], [name])
        ledger, _ = OFFSITE.read_ledger(self.state / OFFSITE.LEDGER_NAME)
        checked = OFFSITE.run_restore_verify(store, self.base / "checked", ledger=ledger)
        self.assertEqual(checked.verified, ())
        self.assertEqual(checked.failures, ((name, "OffsiteError:object_bytes_differ_from_the_ledger"),))
        # An object the VM never recorded is refused too.
        store.objects[name.replace("r0/", "u2/")] = store.objects[name]
        other = OFFSITE.run_restore_verify(store, self.base / "other", ledger=ledger, prefix="u2/")
        self.assertEqual(other.failures[0][1], "OffsiteError:object_is_not_in_the_backup_ledger")

    def test_an_incorrectly_named_object_is_refused(self) -> None:
        from uuid import uuid4

        directory = self._complete()
        store = _MemoryStore()
        self._backup(store)
        (name,) = store.objects
        body = store.objects.pop(name)
        session = directory.name
        # A self-consistent tar moved to another session's path still replays,
        # but its own manifest names a different path.
        renamed = name.replace(session, f"session={uuid4()}")
        store.objects[renamed] = _retar(body, rename=(session, renamed.split("/")[-1][: -len(".tar")]))
        # The same bytes filed under the other root label hold the wrong contract.
        relabelled = name.replace("r0/", "u2/", 1)
        store.objects[relabelled] = body
        report = OFFSITE.run_restore_verify(store, self.base / "restored")
        self.assertEqual(report.verified, ())
        self.assertEqual(
            dict(report.failures),
            {
                renamed: "OffsiteError:partition_is_not_at_its_canonical_path",
                relabelled: "OffsiteError:partition_contract_is_not_approved_for_its_root_label",
            },
        )

    def test_names_outside_approved_labels_and_layout_are_never_fetched(self) -> None:
        from uuid import uuid4

        session = uuid4()
        good = f"v1/exchange=BYBIT/instrument=X/date=2026-09-23/session={session}"
        bad = [
            f"x9/{good}.tar",                                     # unapproved label
            f"../{good}.tar",                                     # traversal as label
            f"r0/{good}",                                         # not a tar
            f"r0/{good.replace('instrument=X', 'instrument=..')}.tar",
            f"r0/{good.replace('date=2026-09-23', 'date=20260923')}.tar",
            f"r0/{good.replace(str(session), str(session).upper())}.tar",
            f"r0/v1/exchange=BYBIT/instrument=X/../../date=2026-09-23/session={session}.tar",
            f"r0/v1/exchange=BYBIT/instrument=X/date=2026-09-23/extra/session={session}.tar",
            f"r0/v2/exchange=BYBIT/instrument=X/date=2026-09-23/session={session}.tar",
            f"r0//{good}.tar",
            f"r0/{good.replace('exchange=', 'venue=')}.tar",
        ]
        store = _MemoryStore()
        for name in bad:
            store.objects[name] = b"never read"
        report = OFFSITE.run_restore_verify(store, self.base / "restored")
        self.assertEqual(report.verified, ())
        self.assertEqual(sorted(name for name, _ in report.failures), sorted(bad))
        self.assertEqual(store.gets, [])
        self.assertEqual(list((self.base / "restored").iterdir()), [])
        self.assertEqual(OFFSITE.parse_object_name(f"u2/{good}.tar"), ("u2", good))

    def test_members_outside_the_named_partition_are_refused(self) -> None:
        import tarfile
        from uuid import uuid4

        relative = f"v1/exchange=BYBIT/instrument=X/date=2026-09-23/session={uuid4()}"
        name = f"r0/{relative}.tar"
        for member_name in ("../escape/session.json", f"{relative}/../../escape/session.json",
                            "v1/other/session=x/session.json", f"{relative}/evil.sh",
                            f"/{relative}/session.json"):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                info = tarfile.TarInfo(member_name)
                info.size = 2
                archive.addfile(info, io.BytesIO(b"{}"))
            with self.assertRaises(OFFSITE.OffsiteError):
                OFFSITE.extract_partition(buffer.getvalue(), name, self.base / "r")
        # A link member, and a tar that is not exactly one compacted partition.
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(f"{relative}/session.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        with self.assertRaises(OFFSITE.OffsiteError):
            OFFSITE.extract_partition(buffer.getvalue(), name, self.base / "r")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(f"{relative}/session.json")
            info.size = 2
            archive.addfile(info, io.BytesIO(b"{}"))
        with self.assertRaises(OFFSITE.OffsiteError) as caught:
            OFFSITE.extract_partition(buffer.getvalue(), name, self.base / "r")
        self.assertEqual(str(caught.exception), "tar_does_not_hold_exactly_one_compacted_partition")
        with self.assertRaises(OFFSITE.OffsiteError):
            OFFSITE.extract_partition(b"", "r0/../session=b.tar", self.base / "r")
        self.assertFalse((self.base / "escape").exists())
        self.assertFalse((self.base / "r").exists() and any((self.base / "r").rglob("*.json")))


class OffsiteVerificationStateTests(_ArchiveTest):
    """Upload, unverified conflict and restore-verified stay three separate states."""

    def _drill(self, store: _MemoryStore, name: str) -> Path:
        """restore-verify --ledger --receipt on 'another host', through the CLI."""
        ledger_copy = self.base / f"ledger-copy-{name}.ndjson"
        ledger_copy.write_bytes((self.state / OFFSITE.LEDGER_NAME).read_bytes())
        receipt = self.base / f"receipt-{name}.ndjson"
        par = self.base / "par"
        par.write_text(PAR + "\n", encoding="utf-8")
        output = io.StringIO()
        with patch.object(OFFSITE, "ParObjectStoreV1", return_value=store), redirect_stdout(output):
            code = OFFSITE.main([
                "restore-verify", "--par-file", str(par), "--destination", str(self.base / f"drill-{name}"),
                "--ledger", str(ledger_copy), "--receipt", str(receipt),
            ])
        self.assertNotIn("SECRETTOKEN", output.getvalue())
        self.assertEqual(code, 0, output.getvalue())
        return receipt

    def _accept(self, receipt: Path) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = OFFSITE.main(["accept-restore", "--state-dir", str(self.state), "--receipt", str(receipt)])
        return code, output.getvalue()

    def test_only_a_matching_restore_receipt_marks_data_recoverable(self) -> None:
        self._complete()
        store = _MemoryStore()
        report = self._backup(store)
        self.assertEqual(report.ledger_states[OFFSITE.RESTORE_VERIFIED], 0)
        receipt = self._drill(store, "a")
        code, output = self._accept(receipt)
        self.assertEqual(code, 0, output)
        after = self._backup(store)
        self.assertEqual(after.ledger_states[OFFSITE.RESTORE_VERIFIED], 1)
        self.assertEqual(after.ledger_states[OFFSITE.UPLOADED], 0)
        self.assertTrue(after.ok)
        # Re-accepting is a no-op; a receipt with other bytes is refused.
        self.assertEqual(self._accept(receipt)[0], 0)
        (line,) = receipt.read_text(encoding="utf-8").splitlines()
        forged = self.base / "forged.ndjson"
        forged.write_text(json.dumps({**json.loads(line), "tar_sha256": "f" * 64}) + "\n", encoding="utf-8")
        code, output = self._accept(forged)
        self.assertEqual(code, 1)
        self.assertIn("receipt_bytes_differ_from_the_ledger", output)

    def test_a_conflict_resolves_only_when_the_remote_bytes_are_the_local_partition(self) -> None:
        directory = self._complete()
        name = self._name(directory)
        genuine = OFFSITE.partition_tar_bytes(directory, directory.relative_to(self.root).as_posix())
        # The object exists (a PUT whose response was lost), so this run cannot see its content.
        store = _MemoryStore()
        store.objects[name] = genuine
        self.assertFalse(self._backup(store).ok)
        code, output = self._accept(self._drill(store, "b"))
        self.assertEqual(code, 0, output)
        resolved = self._backup(store)
        self.assertTrue(resolved.ok)
        self.assertEqual(resolved.unverified_in_window, ())
        self.assertEqual(resolved.ledger_states[OFFSITE.RESTORE_VERIFIED], 1)

    def test_a_conflict_with_other_bytes_stays_unverified(self) -> None:
        directory = self._complete()
        name = self._name(directory)
        store = _MemoryStore()
        store.objects[name] = OFFSITE.partition_tar_bytes(
            directory, directory.relative_to(self.root).as_posix()) + b"\0" * 512
        self.assertFalse(self._backup(store).ok)
        ledger, _ = OFFSITE.read_ledger(self.state / OFFSITE.LEDGER_NAME)
        drill = OFFSITE.run_restore_verify(store, self.base / "drill", ledger=ledger)
        self.assertEqual(drill.failures, ((name, "OffsiteError:object_bytes_differ_from_the_ledger"),))
        still = self._backup(store)
        self.assertEqual(still.unverified_in_window, (name,))
        self.assertFalse(still.ok)

    def test_the_cli_report_names_each_state_separately(self) -> None:
        self._complete()
        par = self.base / "par"
        par.write_text(PAR + "\n", encoding="utf-8")
        store = _MemoryStore()
        output = io.StringIO()
        argv = ["backup", "--root", f"r0={self.root}", "--root", f"u2={self.universe_root}",
                "--par-file", str(par), "--state-dir", str(self.state),
                "--horizon-days", "4", "--budget-window-days", "7", "--budget-bytes", "8000000000"]
        with patch.object(OFFSITE, "ParObjectStoreV1", return_value=store), redirect_stdout(output):
            self.assertEqual(OFFSITE.main(argv), 0)
        text = output.getvalue()
        self.assertIn("uploaded       1 created this run", text)
        self.assertIn("conflict       0 ", text)
        self.assertIn("ledger         1 UPLOADED, 0 RESTORE_VERIFIED, 0 REMOTE_EXISTS_UNVERIFIED", text)
        self.assertNotIn("SECRETTOKEN", text)
        with redirect_stdout(io.StringIO()), patch.object(sys, "stderr", io.StringIO()), \
                self.assertRaises(SystemExit):
            OFFSITE.main(argv[:-6])  # the budget flags have no defaults


class OffsiteTransportTests(unittest.TestCase):
    def _url_file(self, text: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "url"
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def test_only_a_bucket_object_par_over_https_is_accepted(self) -> None:
        self.assertEqual(OFFSITE.read_url_file(self._url_file(PAR), par=True), PAR)
        for bad in (PAR.replace("https", "http"), PAR + "x", PAR + "?a=1", "https://example.org/o/"):
            with self.assertRaises(OFFSITE.OffsiteError) as caught:
                OFFSITE.read_url_file(self._url_file(bad), par=True)
            self.assertNotIn("SECRETTOKEN", str(caught.exception))

    def test_put_is_create_only_and_md5_checked_and_never_leaks_the_par(self) -> None:
        import base64
        import hashlib
        import urllib.error

        seen: list = []

        def opener(request, timeout):
            seen.append(request)
            return b""

        store = OFFSITE.ParObjectStoreV1(PAR)
        with patch.object(OFFSITE, "_open", side_effect=opener):
            self.assertTrue(store.put_new("u2/v1/x=1/session=a.tar", b"body"))
        (request,) = seen
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url, PAR + "u2/v1/x=1/session=a.tar")
        self.assertEqual(request.get_header("If-none-match"), "*")
        self.assertEqual(
            request.get_header("Content-md5"),
            base64.b64encode(hashlib.md5(b"body", usedforsecurity=False).digest()).decode(),
        )

        def conflict(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 412, "Precondition Failed", None, None)

        def denied(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

        with patch.object(OFFSITE, "_open", side_effect=conflict):
            self.assertFalse(store.put_new("u2/a/session=b.tar", b"body"))
        with patch.object(OFFSITE, "_open", side_effect=denied), \
                self.assertRaises(OFFSITE.OffsiteError) as caught:
            store.put_new("u2/a/session=b.tar", b"body")
        self.assertEqual(str(caught.exception), "put_failed:http_401")

    def test_ping_targets_success_or_fail_and_reports_no_url(self) -> None:
        import urllib.error

        url = "https://hc-ping.com/SECRET-UUID"
        seen: list = []
        with patch.object(OFFSITE, "_open", side_effect=lambda request, timeout: seen.append(request)):
            OFFSITE.send_ping(url, "success", "service OK")
            OFFSITE.send_ping(url, "fail", "x" * 50_000)
        self.assertEqual([r.full_url for r in seen], [url, url + "/fail"])
        self.assertEqual(len(seen[1].data), OFFSITE.PING_BODY_LIMIT)

        def down(request, timeout):
            raise urllib.error.URLError("unreachable")

        with patch.object(OFFSITE, "_open", side_effect=down), \
                patch.object(OFFSITE.time, "sleep"), \
                self.assertRaises(OFFSITE.OffsiteError) as caught:
            OFFSITE.send_ping(url, "fail", "")
        self.assertNotIn("SECRET", str(caught.exception))


class OffsiteUnitTests(unittest.TestCase):
    def test_backup_unit_reads_the_archive_and_yields_to_the_recorders(self) -> None:
        unit = _unit("trade-capture-backup.service")
        service = unit["Service"]
        self.assertEqual(unit["Unit"]["ConditionPathExists"], "/etc/trade-capture/backup-par-url")
        self.assertEqual(unit["Unit"]["OnFailure"], "trade-capture-alert@%n.service")
        self.assertEqual(service["User"], "trade-capture")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["ProtectSystem"], "strict")
        self.assertNotIn("ReadWritePaths", service)  # the archive stays read-only
        self.assertEqual(service["StateDirectory"], "trade-capture-backup")
        self.assertEqual(service["CapabilityBoundingSet"], "")
        self.assertLess(int(service["CPUWeight"]), int(_unit("trade-capture-u2.service")["Service"]["CPUWeight"]))
        self.assertEqual(service["IOSchedulingClass"], "idle")
        argv = shlex.split(service["ExecStart"])
        self.assertEqual(argv[:3], [INTERPRETER_LINK, "-I", "-B"])
        self.assertEqual(argv[3], "/opt/trade-capture/current/deploy/oci/capture_offsite.py")
        self.assertIn("--root", argv)
        self.assertEqual(argv[argv.index("--state-dir") + 1], "/var/lib/trade-capture-backup")
        # The storage budget is stated in the unit, matches the tests and README
        # 7a, and leaves headroom under a 10 GB Standard allowance.
        budget = {flag: int(argv[argv.index(f"--{flag.replace('_', '-')}") + 1]) for flag in BUDGET}
        self.assertEqual(budget, BUDGET)
        self.assertLessEqual(budget["budget_bytes"], 8_000_000_000)
        readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
        lifecycle = int(re.search(r"delete objects after \*\*(\d+) days\*\*", readme).group(1))  # type: ignore[union-attr]
        retention = int(re.search(r"time-bound \*\*(\d+) days\*\*", readme).group(1))  # type: ignore[union-attr]
        self.assertLessEqual(budget["horizon_days"], retention)  # a lost ledger meets objects, never re-sends
        self.assertLess(retention, lifecycle)
        self.assertGreaterEqual(budget["budget_window_days"], lifecycle + 2)  # slack for a late lifecycle run
        timer = _unit("trade-capture-backup.timer")["Timer"]
        self.assertEqual(timer["OnCalendar"], "*-*-* *:25:00 UTC")

    def test_recorders_alert_when_they_enter_the_failed_state(self) -> None:
        for name in RECORDER_UNITS:
            self.assertEqual(_unit(name)["Unit"]["OnFailure"], "trade-capture-alert@%n.service", name)
        alert = _unit("trade-capture-alert@.service")
        self.assertEqual(alert["Unit"]["ConditionPathExists"], "/etc/trade-capture/alert-ping-url")
        argv = shlex.split(alert["Service"]["ExecStart"])
        self.assertEqual(argv[4:6], ["ping", "fail"])
        self.assertEqual(alert["Service"]["NoNewPrivileges"], "yes")

    def test_no_url_or_secret_is_shipped_and_nothing_is_enabled(self) -> None:
        for path in (DEPLOY / "systemd").iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("https://", text, path.name)
            self.assertNotIn("http://", text, path.name)
        install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        self.assertIn("trade-capture-backup.timer", install)
        self.assertNotIn("enable trade-capture-backup", install)
        self.assertNotIn("enable --now trade-capture-backup", install)
        for target in ("> /etc/trade-capture/backup-par-url", "> /etc/trade-capture/alert-ping-url"):
            self.assertNotIn(target, install)

    def test_health_pings_its_own_text_without_changing_its_exit(self) -> None:
        health = (DEPLOY / "trade-capture-health.sh").read_text(encoding="utf-8")
        self.assertIn("PING_URL_FILE=/etc/trade-capture/alert-ping-url", health)
        self.assertIn('capture_offsite.py" \\\n        ping "$outcome"', health)
        self.assertIn('exit "$status"', health)

    def test_coverage_alert_is_its_own_check_never_service_health(self) -> None:
        unit = _unit("trade-capture-coverage.service")
        service = unit["Service"]
        # ATTENTION is the alert's outcome, not a failed unit: no alert@ page, no health verdict.
        self.assertEqual(service["SuccessExitStatus"], "1")
        self.assertNotIn("OnFailure", unit["Unit"])
        self.assertEqual(service["User"], "trade-capture")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["ProtectSystem"], "strict")
        self.assertNotIn("ReadWritePaths", service)  # reads the archive only
        self.assertLess(int(service["CPUWeight"]), int(_unit("trade-capture-u2.service")["Service"]["CPUWeight"]))
        self.assertEqual(shlex.split(service["ExecStart"]),
                         ["/usr/bin/bash", "/opt/trade-capture/current/deploy/oci/trade-capture-coverage.sh"])
        # The paging threshold is stated in the unit and in the README's trade-off table.
        threshold = service["Environment"].removeprefix("COVERAGE_MAX_UNCOVERED_SECONDS=")
        self.assertEqual(threshold, "60")
        self.assertIn(f"**{threshold} s (unit default, proposed)**", (DEPLOY / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(_unit("trade-capture-coverage.timer")["Timer"]["OnCalendar"], "*-*-* *:15:00 UTC")
        script = (DEPLOY / "trade-capture-coverage.sh").read_text(encoding="utf-8")
        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        ping_file = re.search(r"^PING_URL_FILE=(\S+)$", script, re.MULTILINE)
        self.assertIsNotNone(ping_file)
        self.assertEqual(ping_file.group(1), "/etc/trade-capture/coverage-ping-url")  # type: ignore[union-attr]
        self.assertNotIn("alert-ping-url", script)
        for scope in ("--scope production", "--scope universe"):
            self.assertIn(scope, script)
        install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        self.assertIn("trade-capture-coverage.timer", install)
        self.assertNotIn("enable --now trade-capture-coverage", install)
        self.assertNotIn("> /etc/trade-capture/coverage-ping-url", install)

    def test_offsite_script_needs_only_the_standard_library(self) -> None:
        probe = (
            "import importlib.util, sys\n"
            "before = set(sys.modules)\n"
            f"spec = importlib.util.spec_from_file_location('m', {str(DEPLOY / 'capture_offsite.py')!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules['m'] = module\n"
            "spec.loader.exec_module(module)\n"
            "tops = {name.split('.')[0] for name in set(sys.modules) - before}\n"
            "print(' '.join(sorted(tops - set(sys.stdlib_module_names) - {'__main__', 'm'})))\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.split(), ["trade_platform"])


class WindowsDeadmanTests(unittest.TestCase):
    """2026-10-14 22:27Z: both Windows recorders stopped at the disk floor, unnoticed for 5 h."""

    SCRIPT = ROOT / "scripts" / "windows_capture_deadman.ps1"

    def test_the_check_only_reads_and_pings_its_own_url_file(self) -> None:
        text = self.SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("Start-Process", "Stop-Process", "Remove-Item", "Set-Content", "Out-File",
                          "http://", "https://"):
            self.assertNotIn(forbidden, text, forbidden)
        # The Scheduled Task command is printed for the owner, never executed.
        self.assertNotIn("\n    Register-ScheduledTask", text)
        self.assertNotIn("\nRegister-ScheduledTask", text)
        self.assertIn("'.trade_platform\\deadman-ping-url'", text)
        self.assertIn("capture_offsite.py'", text)
        self.assertIn("ping $outcome --url-file $UrlFile", text)
        self.assertIn("[double]$MinFreeGB = 20", text)
        self.assertEqual(CLI.DEFAULT_MIN_FREE_GIB, 20)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not available")
    def test_empty_roots_report_attention_without_pinging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            url_file = Path(directory) / "deadman-ping-url"
            url_file.write_text("https://hc-ping.example/never-used", encoding="utf-8")
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(self.SCRIPT), "-NoPing", "-Python", sys.executable,
                 "-UrlFile", str(url_file), "-R0Root", str(Path(directory) / "r0"),
                 "-U2Root", str(Path(directory) / "u2")],
                capture_output=True, text=True, check=False, timeout=120,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("NOT RUNNING", result.stdout)
        self.assertIn("manifest_r0    none in the last two UTC days", result.stdout)
        self.assertIn("service        ATTENTION", result.stdout)
        self.assertNotIn("alert_ping", result.stdout)


@unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze not available")
class SystemdAnalyzeVerifyTests(unittest.TestCase):
    def test_units_parse_without_unknown_or_invalid_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for unit in (DEPLOY / "systemd").iterdir():
                # The absolute executables must exist where verify runs.
                text = unit.read_text(encoding="utf-8").replace(INTERPRETER_LINK, sys.executable)
                # A template is verified as one instance of itself.
                name = unit.name.replace("@.", "@verify.")
                (Path(directory) / name).write_text(text, encoding="utf-8")
            result = subprocess.run(
                ["systemd-analyze", "verify", *sorted(str(path) for path in Path(directory).iterdir())],
                capture_output=True, text=True, check=False,
            )
        output = result.stdout + result.stderr
        for marker in ("Unknown key", "Unknown section", "Failed to parse", "Invalid", "bad-setting"):
            self.assertNotIn(marker, output, output)


if __name__ == "__main__":
    unittest.main()
