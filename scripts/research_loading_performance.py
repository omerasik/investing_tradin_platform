"""P2A: bounded loader benchmark; no provider, database or strategy campaign.

Prepare the explicit synthetic 86,400-bar fixture (only under workspace .tmp)::

    python scripts/research_loading_performance.py --prepare-fixture --root .tmp/p2a-fixture --output .tmp/fixture.json

Then supply its dataset UUID and content hash to both implementations::

    python scripts/research_loading_performance.py --implementation reference --root <root> --dataset <uuid> --expected <hash> --output .tmp/reference.json
    python scripts/research_loading_performance.py --implementation optimized --root <root> --dataset <uuid> --expected <hash> --output .tmp/optimized.json

Three fresh sequential children, one numeric thread, 120 s/run. Synthetic memory
ceiling 512 MiB; --real permits only the pinned development ETH dataset and uses
1 GiB. Reuse the historical 128 MiB Windows hard working-set/512 MiB host reserve
guard. --working-set-mib 512 (synthetic) or 1024 (real) permits a natural-peak
comparison only when that larger admission plus reserve is available. Linux
uses RLIMIT_AS and requires the full process ceiling plus reserve.
The disk floor is the existing 24 GiB research admission floor. No service, clock,
OS cache, production file or economic/operational gate is changed.

Reference = original row reader + unchanged SDK, including its unsafe warm-cache
fast path. Optimized = bounded Decimal sharing + revalidated Strategy Lab loader.
Warm-cache verification counts are explicit; warm times are not equal-assurance
comparisons. Windows transfer counters are NOT physical disk bytes. Linux reports
/proc/self/io physical bytes separately. Never infer a physical-I/O improvement
or a cold OS cache from process transfer counts. --profile attributes time and
memory by stage; compare unprofiled repetitions for final elapsed-time claims.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import research_performance as bounds

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 ** 2
HOST_RESERVE = 512 * MIB
WORKING_SET_LIMIT = 128 * MIB
DISK_FLOOR = 24 * 1024 ** 3
REAL_ID = "da5bca46-8e14-506e-96db-44c039cfd7f2"
# Immutable dataset content SHA-256 verified from its catalog, never a credential.
REAL_HASH = "9d465533eb2e7e39ce15fac9d774b663f8f98f360bda8bb9e7ebf68cd2a373d2"  # pragma: allowlist secret


def host_memory() -> dict[str, int]:
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total", "free", "total_commit", "free_commit", "virtual", "free_virtual", "extended")]

        status = Status()
        status.size = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise RuntimeError("host_memory_evidence_unavailable")
        free, commit = status.free, status.free_commit
    else:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        values = {key: int(value.split()[0]) * 1024 for key, value in fields.items()}
        free = values["MemAvailable"]
        commit = max(0, values["CommitLimit"] - values["Committed_AS"])
    return {"available_physical_bytes": free, "commit_headroom_bytes": commit,
            "disk_free_bytes": shutil.disk_usage(ROOT).free}


def resources() -> dict[str, Any]:
    result = bounds._resources()
    result["physical_read_bytes"] = result["physical_write_bytes"] = None
    if os.name == "nt":
        from ctypes import wintypes as wt

        class Memory(ctypes.Structure):
            _fields_ = [("size", wt.DWORD), ("faults", wt.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                    "commit", "peak_commit", "private")]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wt.HANDLE
        kernel.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        memory = Memory()
        memory.size = ctypes.sizeof(memory)
        if not kernel.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(memory), memory.size):
            raise ctypes.WinError(ctypes.get_last_error())
        result.update(working_set_bytes=memory.ws, private_bytes=memory.private)
    else:
        fields = dict(line.split(":", 1) for line in Path("/proc/self/io").read_text().splitlines())
        result.update(physical_read_bytes=int(fields["read_bytes"]),
                      physical_write_bytes=int(fields["write_bytes"]))
    return result


def prepare_fixture(root: Path) -> dict[str, Any]:
    import random
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from uuid import uuid5

    from trade_platform import public_archive_research_bars_v1 as public
    from trade_platform.research_data_plane_v1 import (
        T2_ARCHIVE_OHLCV_1M_FRAME,
        ResearchFrameStoreV1,
    )
    from trade_platform.strategy_lab_study_v1 import identity_hash_v1

    store = ResearchFrameStoreV1(root)
    first = datetime(2025, 1, 1, tzinfo=UTC)

    def rows():
        price, rng = Decimal("30000.0"), random.Random(741)
        for i in range(86_400):
            opened = first + timedelta(minutes=i)
            close = price + Decimal(rng.randrange(-40, 41)) / 10
            yield (opened, opened + timedelta(minutes=1), None, price,
                   max(price, close) + Decimal("0.5"), min(price, close) - Decimal("0.5"), close,
                   Decimal("0.02"), (price + close) * Decimal("0.01"), 2, 0,
                   "false", "false", 0, f"SYNTHETIC-FIRST-{i}", f"SYNTHETIC-LAST-{i}")
            price = close

    manifest = store.write_frame(T2_ARCHIVE_OHLCV_1M_FRAME, rows(),
                                lineage={"synthetic_benchmark_only": True, "seed": 741})
    identity = {"schema_version": public.BAR_DATASET_SCHEMA_VERSION_V1, "synthetic_benchmark_only": True,
                "symbol": "SYNTHETIC_BENCHMARK", "evidence_tier": "T2_EVENT_TIME",
                "first_utc_day": "2025-01-01", "last_utc_day": "2025-03-01",
                "bar_frame": {"logical_content_hash": manifest.logical_content_hash, "row_count": 86_400},
                "last_bar_close_at": (first + timedelta(minutes=86_400)).isoformat()}
    digest = identity_hash_v1(identity)
    dataset_id = uuid5(public._NAMESPACE, f"public-archive-bar-dataset:{digest}")
    payload = {"identity": identity, "content_hash": digest, "dataset_version_id": str(dataset_id),
               "bar_frame_manifest_hash": manifest.manifest_hash}
    public._write_json(public._dataset_path(store, dataset_id), payload)
    return payload


def reference_rows(store, manifest):
    """Exact pre-P2A reader: unchanged full-column conversion, writer row-group batches."""
    import pyarrow.parquet as pq

    from trade_platform.research_data_plane_v1 import (
        FRAME_SCHEMAS,
        ROW_GROUP_ROWS,
        ResearchDataPlaneError,
    )

    frame = FRAME_SCHEMAS.get(manifest.frame_kind)
    if frame is None or frame.fingerprint() != manifest.frame_schema_fingerprint:
        raise ResearchDataPlaneError("frame_schema_not_recognised")
    for item in manifest.objects:
        parquet = pq.ParquetFile(str(store.object_path(item)))
        if not parquet.schema_arrow.equals(frame.schema):
            raise ResearchDataPlaneError("parquet_schema_differs_from_frame_schema")
        for batch in parquet.iter_batches(batch_size=ROW_GROUP_ROWS):
            columns = [column.to_pylist() for column in batch.columns]
            yield from zip(*columns, strict=True)


def bars_fingerprint(bars) -> str:
    import numpy as np

    digest = hashlib.sha256()
    for name, value in vars(bars).items():
        digest.update(name.encode() + b"\n")
        if isinstance(value, np.ndarray):
            digest.update(str(value.dtype).encode() + str(value.shape).encode() + value.tobytes())
        elif isinstance(value, tuple):
            for decimal in value:
                digest.update(str(decimal).encode() + b"\n")
        else:
            digest.update(str(value).encode() + b"\n")
    return digest.hexdigest()


def load_workload(args) -> dict[str, Any]:
    import cProfile
    import pstats
    from contextlib import ExitStack
    from datetime import datetime
    from unittest.mock import patch
    from uuid import UUID

    from trade_platform import research_data_plane_v1 as plane
    from trade_platform import strategy_lab_data_loading_v1 as loading
    from trade_platform import strategy_sdk_v1 as sdk
    from trade_platform.strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1

    catalog_path = args.root / "v1/datasets/public-archive-bars" / f"{args.dataset}.json"
    catalog_before = catalog_path.read_bytes()
    payload = json.loads(catalog_before)
    if datetime.fromisoformat(payload["identity"]["last_bar_close_at"]) >= UNTOUCHED_HOLDOUT_BOUNDARY_V1:
        raise ValueError("benchmark_refuses_holdout_dataset")
    if not args.real and not payload["identity"].get("synthetic_benchmark_only"):
        raise ValueError("non_synthetic_dataset_requires_explicit_pinned_real_mode")
    manifest_path = args.root / "v1/manifests" / f"{payload['bar_frame_manifest_hash']}.json"
    manifest_before = manifest_path.read_bytes()
    events: list[dict[str, Any]] = []
    verify = plane.ResearchFrameStoreV1.verify
    original_bars = sdk.BarsV1.from_rows
    original_ticks = sdk._exact_ticks

    def verified(store, manifest):
        started = time.perf_counter()
        verify(store, manifest)
        events.append({"stage": "verification", "seconds": time.perf_counter() - started,
                       "object_bytes": manifest.total_bytes, **resources()})

    def materialize(cls, *a, **kw):
        events.append({"stage": "python_rows_materialized", **resources()})
        started = time.perf_counter()
        result = original_bars(*a, **kw)
        events.append({"stage": "sdk_numpy_conversion_including_ticks",
                       "seconds": time.perf_counter() - started, **resources()})
        return result

    def ticks(*a, **kw):
        started = time.perf_counter()
        result = original_ticks(*a, **kw)
        events.append({"stage": "exact_ticks", "seconds": time.perf_counter() - started, **resources()})
        return result

    file_reader = plane.pq.ParquetFile
    file_hash = plane._file_sha256
    decode_columns = plane._python_columns_v1

    def hashed(path):
        started = time.perf_counter()
        result = file_hash(path)
        events.append({"stage": "byte_hash", "seconds": time.perf_counter() - started,
                       "file_bytes": path.stat().st_size, **resources()})
        return result

    def decoded(batch):
        started = time.perf_counter()
        result = decode_columns(batch)
        events.append({"stage": "python_decimal_materialization", "rows": batch.num_rows,
                       "seconds": time.perf_counter() - started, **resources()})
        return result

    class ProfileColumn:
        def __init__(self, column, name):
            self.column, self.name = column, name

        def to_pylist(self):
            started = time.perf_counter()
            result = self.column.to_pylist()
            events.append({"stage": "python_column_materialization", "column": self.name,
                           "seconds": time.perf_counter() - started, **resources()})
            return result

    class ProfileBatch:
        def __init__(self, batch):
            self.columns = [ProfileColumn(column, name) for column, name in
                            zip(batch.columns, batch.schema.names, strict=True)]

    class ProfileFile:
        def __init__(self, *a, **kw):
            started = time.perf_counter()
            self.reader = file_reader(*a, **kw)
            events.append({"stage": "parquet_open_footer", "seconds": time.perf_counter() - started,
                           **resources()})

        def __getattr__(self, name):
            return getattr(self.reader, name)

        def iter_batches(self, *a, **kw):
            iterator = self.reader.iter_batches(*a, **kw)
            while True:
                started = time.perf_counter()
                try:
                    batch = next(iterator)
                except StopIteration:
                    return
                events.append({"stage": "parquet_arrow_batch", "rows": batch.num_rows,
                               "seconds": time.perf_counter() - started, **resources()})
                yield ProfileBatch(batch) if args.implementation == "reference" else batch

    loading.clear_bars_cache_v1()
    profiler = cProfile.Profile()
    with ExitStack() as stack:
        if args.profile:
            stack.enter_context(patch.object(plane.pq, "ParquetFile", new=ProfileFile))
            stack.enter_context(patch.object(plane, "_file_sha256", new=hashed))
            stack.enter_context(patch.object(plane, "_python_columns_v1", new=decoded))
        if args.implementation == "reference":
            stack.enter_context(patch.object(plane.ResearchFrameStoreV1, "iter_rows", new=reference_rows))
        stack.enter_context(patch.object(plane.ResearchFrameStoreV1, "verify", new=verified))
        stack.enter_context(patch.object(sdk.BarsV1, "from_rows", new=classmethod(materialize)))
        stack.enter_context(patch.object(sdk, "_exact_ticks", new=ticks))
        loader = sdk.load_bars_v1 if args.implementation == "reference" else loading.load_bars_v1
        before = resources()
        if args.profile:
            profiler.enable()
        started = time.perf_counter()
        bars = loader(args.root, UUID(args.dataset), expected_content_hash=args.expected)
        elapsed = time.perf_counter() - started
        after_cold, cold_events = resources(), len(events)
        started = time.perf_counter()
        cached = loader(args.root, UUID(args.dataset), expected_content_hash=args.expected)
        warm = time.perf_counter() - started
        after_warm = resources()
        if args.profile:
            profiler.disable()
    if catalog_before != catalog_path.read_bytes() or manifest_before != manifest_path.read_bytes():
        raise ValueError("benchmark_source_metadata_changed")
    profile = []
    if args.profile:
        profile = [{"function": str(key), "calls": value[1], "self_seconds": value[2],
                    "cumulative_seconds": value[3]}
                   for key, value in sorted(pstats.Stats(profiler).stats.items(),
                                            key=lambda item: item[1][3], reverse=True)[:30]]
    return {"implementation": args.implementation, "rows": bars.size, "profiled": args.profile,
            "loading_seconds": elapsed, "warm_loading_seconds": warm, "cache_same_object": cached is bars,
            "cold_verifications": sum(e["stage"] == "verification" for e in events[:cold_events]),
            "warm_verifications": sum(e["stage"] == "verification" for e in events[cold_events:]),
            "cache_entries": len(sdk._BARS_CACHE), "bars_fingerprint": bars_fingerprint(bars),
            "dataset_content_hash": payload["content_hash"],
            "manifest_hash": payload["bar_frame_manifest_hash"],
            "catalog_bytes_sha256": hashlib.sha256(catalog_before).hexdigest(),
            "manifest_bytes_sha256": hashlib.sha256(manifest_before).hexdigest(),
            "strategy_implementation_hashes": {name: family.spec().implementation_sha256
                                                for name, family in sdk.FAMILIES_V1.items()},
            "before": before, "after_cold": after_cold, "after_warm": after_warm,
            "events": events, "profile": profile,
            "physical_io_note": "Windows transfer counters are not physical disk bytes; OS cache not flushed"}


def worker(args, limit: int) -> None:
    if sys.stdin.readline().strip() != "RUN_BOUNDED_SYNTHETIC":
        raise RuntimeError("missing_parent_resource_admission")
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    sys.path.insert(0, str(ROOT / "src"))
    result = prepare_fixture(args.root) if args.prepare_fixture else load_workload(args)
    print(json.dumps(result, sort_keys=True))


def run_child(args, limit: int) -> dict[str, Any]:
    host = host_memory()
    working_set_limit = args.working_set_mib * MIB
    admission = (working_set_limit if os.name == "nt" else limit) + HOST_RESERVE
    if (host["available_physical_bytes"] < admission or host["commit_headroom_bytes"] < limit + HOST_RESERVE
            or host["disk_free_bytes"] < DISK_FLOOR):
        return {"status": "RESOURCE_BLOCKED", "host": host}
    env = os.environ.copy()
    env.update(dict.fromkeys(bounds.THREAD_VARIABLES, "1"))
    interpreter = sys.executable
    if os.name == "nt":
        env["__PYVENV_LAUNCHER__"] = sys.executable
        interpreter = sys._base_executable
    command = [interpreter, str(Path(__file__).resolve()), "--worker", *sys.argv[1:]]
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT)
    kernel = job = None
    minimum = host.copy()
    try:
        if os.name == "nt":
            from ctypes import wintypes as wt
            kernel, job = bounds._windows_job(child, memory_bytes=limit)
            kernel.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wt.DWORD]
            if not kernel.SetProcessWorkingSetSizeEx(int(child._handle), 16 * MIB, working_set_limit, 0x4 | 0x2):
                raise ctypes.WinError(ctypes.get_last_error())
        started, token = time.monotonic(), "RUN_BOUNDED_SYNTHETIC\n"
        while True:
            state = host_memory()
            minimum = {key: min(value, state[key]) for key, value in minimum.items()}
            if (state["available_physical_bytes"] < HOST_RESERVE or state["commit_headroom_bytes"] < 1024 ** 3
                    or state["disk_free_bytes"] < DISK_FLOOR or time.monotonic() - started > 120):
                return {"status": "RESOURCE_STOPPED", "minimum_host": minimum}
            try:
                stdout, stderr = child.communicate(token, timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                token = None
        if child.returncode:
            return {"status": "FAILED", "exit_code": child.returncode, "stderr": stderr}
        return {"status": "COMPLETE", "host_before": host, "minimum_host": minimum,
                "host_after": host_memory(), "result": json.loads(stdout)}
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
        if job is not None:
            kernel.CloseHandle(job)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prepare-fixture", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--implementation", choices=("reference", "optimized"), default="optimized")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset")
    parser.add_argument("--expected")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--working-set-mib", type=int, choices=(128, 512, 1024), default=128)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt" and not sys.platform.startswith("linux"):
        parser.error("resource enforcement is supported only on Windows and Linux")
    if not 1 <= args.repeats <= 3:
        parser.error("repeats must be in 1..3")
    if args.prepare_fixture:
        if args.real or not args.root.resolve().is_relative_to((ROOT / ".tmp").resolve()):
            parser.error("fixture preparation is allowed only inside workspace .tmp")
    elif not args.dataset or not args.expected:
        parser.error("loading requires explicit --dataset and --expected")
    if args.real and (args.dataset != REAL_ID or args.expected != REAL_HASH):
        parser.error("real mode permits only the pinned 524160-bar ETH development dataset")
    limit = (1024 if args.real else 512) * MIB
    if args.working_set_mib * MIB > limit:
        parser.error("working-set ceiling cannot exceed the process-memory ceiling")
    if args.worker:
        worker(args, limit)
        return 0
    count = 1 if args.prepare_fixture else args.repeats
    runs = []
    for _ in range(count):
        run = run_child(args, limit)
        runs.append(run)
        if run["status"] != "COMPLETE":
            break
    report = {"schema": "research-loading-performance-v1", "runs": runs,
              "implementation": args.implementation, "real_development_loader_only": args.real,
              "limits": {"process_memory_bytes": limit,
                         "working_set_bytes": args.working_set_mib * MIB if os.name == "nt" else None,
                         "host_reserve_bytes": HOST_RESERVE, "disk_floor_bytes": DISK_FLOOR,
                         "wall_seconds_per_run": 120, "workers": 1, "numeric_threads": 1},
              "environment": {"python": sys.version, "platform": sys.platform},
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  "src/trade_platform/strategy_sdk_v1.py", "src/trade_platform/research_data_plane_v1.py",
                  "src/trade_platform/strategy_lab_data_loading_v1.py", "scripts/research_loading_performance.py")}}
    complete = [run["result"] for run in runs if run["status"] == "COMPLETE"]
    if complete and not args.prepare_fixture:
        keys = ("bars_fingerprint", "dataset_content_hash", "manifest_hash", "strategy_implementation_hashes")
        if any(any(run[key] != complete[0][key] for key in keys) for run in complete):
            raise RuntimeError("non_deterministic_loading_identity")
        report["median"] = {key: statistics.median(run[key] for run in complete)
                            for key in ("loading_seconds", "warm_loading_seconds")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "statuses": [run["status"] for run in runs],
                      "median": report.get("median")}))
    return 0 if len(complete) == count else 2


if __name__ == "__main__":
    raise SystemExit(main())
