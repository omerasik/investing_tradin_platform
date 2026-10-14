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
             "systemd/trade-capture-health.service", "systemd/trade-capture-health.timer"},
            names,
        )
        for path in shipped:
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

    def put_new(self, name: str, body: bytes) -> bool:
        self.puts.append(name)
        if name in self.objects:
            return False
        self.objects[name] = body
        return True

    def get(self, name: str) -> bytes:
        return self.objects[name]

    def list_names(self, prefix: str) -> list[str]:
        return sorted(name for name in self.objects if name.startswith(prefix))


class _ArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "u2"
        self.state = self.base / "state"
        self.state.mkdir()

    def _complete(self, *, compact: bool = True, count: int = 3) -> Path:
        from tests.test_first_party_capture_archive_v1 import _complete_partition
        from trade_platform.first_party_capture_archive_v1 import compact_partition_v1

        directory = _complete_partition(self.root, count=count)
        if compact:
            compact_partition_v1(directory)
        return directory

    def _backup(self, store: _MemoryStore, **options: object):
        return OFFSITE.run_backup([("u2", self.root)], store, self.state, **options)


class OffsiteBackupTests(_ArchiveTest):
    def test_only_complete_compacted_partitions_are_uploaded_once(self) -> None:
        from datetime import date
        from uuid import uuid4

        from trade_platform.first_party_capture_archive_v1 import CapturePartitionWriterV1
        from trade_platform.first_party_capture_authority_v1 import (
            first_party_bybit_capture_contract_v1,
        )

        done = self._complete()
        raw = self._complete(compact=False)
        open_writer = CapturePartitionWriterV1(
            root=self.root, contract=first_party_bybit_capture_contract_v1(), session_id=uuid4(),
            day=date(2026, 9, 23),
        )
        self.addCleanup(open_writer.close_without_finalizing)
        store = _MemoryStore()
        report = self._backup(store)
        name = f"u2/{done.relative_to(self.root).as_posix()}.tar"
        self.assertEqual(report.uploaded, (name,))
        self.assertEqual(report.pending_compaction, (f"u2/{raw.relative_to(self.root).as_posix()}.tar",))
        self.assertEqual(report.skipped_not_complete, 1)  # the OPEN partition
        self.assertTrue(report.ok)
        # The ledger makes the next run send nothing.
        again = self._backup(store)
        self.assertEqual((again.uploaded, again.already_present), ((), ()))
        self.assertEqual(store.puts, [name])

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

    def test_an_existing_object_is_never_overwritten_and_is_recorded(self) -> None:
        directory = self._complete()
        store = _MemoryStore()
        name = f"u2/{directory.relative_to(self.root).as_posix()}.tar"
        store.objects[name] = b"uploaded by an earlier, lost ledger"
        report = self._backup(store)
        self.assertEqual(report.already_present, (name,))
        self.assertEqual(store.objects[name], b"uploaded by an earlier, lost ledger")

    def test_a_tampered_partition_is_never_uploaded(self) -> None:
        directory = self._complete()
        with (directory / "lifecycle.ndjson").open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
        store = _MemoryStore()
        report = self._backup(store)
        self.assertEqual((report.uploaded, report.skipped_not_complete), ((), 1))
        self.assertEqual(store.objects, {})

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
        self.assertEqual(len(self._backup(_MemoryStore()).uploaded), 1)
        self.assertTrue(directory.exists())


class OffsiteRestoreTests(_ArchiveTest):
    def test_restore_replays_every_object_into_an_empty_root(self) -> None:
        directory = self._complete(count=5)
        store = _MemoryStore()
        self._backup(store)
        restored = self.base / "restored"
        report = OFFSITE.run_restore_verify(store, restored)
        self.assertEqual(report.failures, ())
        self.assertEqual([count for _, count in report.verified], [5])
        relative = directory.relative_to(self.root)
        for path in directory.iterdir():
            self.assertEqual(path.read_bytes(), (restored / "u2" / relative / path.name).read_bytes())
        with self.assertRaises(OFFSITE.OffsiteError):
            OFFSITE.run_restore_verify(store, restored)  # never into a non-empty root

    def test_a_corrupted_object_fails_verification(self) -> None:
        import tarfile

        directory = self._complete()
        store = _MemoryStore()
        self._backup(store)
        (name,) = store.objects
        relative = directory.relative_to(self.root).as_posix()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(store.objects[name])) as source, \
                tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as target:
            for member in source.getmembers():
                data = source.extractfile(member).read()  # type: ignore[union-attr]
                if member.name.endswith("records.ndjson.gz"):
                    data = data[:-8] + bytes(8)
                member.size = len(data)
                target.addfile(member, io.BytesIO(data))
        store.objects[name] = buffer.getvalue()
        report = OFFSITE.run_restore_verify(store, self.base / "restored")
        self.assertEqual(report.verified, ())
        self.assertEqual([failed for failed, _ in report.failures], [name])
        self.assertIn(relative, name)

    def test_members_outside_the_named_partition_are_refused(self) -> None:
        import tarfile

        for member_name in ("../escape/session.json", "v1/other/session=x/session.json",
                            "v1/a/session=b/evil.sh"):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                info = tarfile.TarInfo(member_name)
                info.size = 2
                archive.addfile(info, io.BytesIO(b"{}"))
            with self.assertRaises(OFFSITE.OffsiteError):
                OFFSITE.extract_partition(buffer.getvalue(), "u2/v1/a/session=b.tar", self.base / "r")
        with self.assertRaises(OFFSITE.OffsiteError):
            OFFSITE.extract_partition(b"", "u2/../session=b.tar", self.base / "r")
        self.assertFalse((self.base / "escape").exists())


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
