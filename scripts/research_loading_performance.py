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

P2B.1 --evaluation-tasks 12|40|1000 uses the same fixed 512 MiB process ceiling,
120 s external deadline, host reserve and disk guard. Here reference means the
standalone P2A VERIFIED evaluator, not the legacy loader reference above. The
1000-task workload cycles 40 unique points in three unregistered synthetic study
declarations. Optimized mode admits one explicit scope per study (334/333/333
tasks, <=105 s each), not a database worker-pool throughput claim. Real workers
retain their existing claim-batch boundary (default 8). Use a complete reference
40-task --reference-receipt to check every repeated decision/result identity.
An interrupted run retains only its last flushed prefix, never a 1000-task pass.
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
import tempfile
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


def resource_policy(args) -> dict[str, Any]:
    """Separate ephemeral-runner admission; local production-host guards stay fixed."""
    if not getattr(args, "hosted_windows_acceptance", False):
        return {"name": "local-research-host-v1", "reserve": HOST_RESERVE,
                "disk_floor": DISK_FLOOR, "commit_floor": 1024 ** 3}
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Windows"
            or getattr(args, "real", False) or getattr(args, "working_set_mib", None) != 512):
        raise ValueError("hosted_acceptance_requires_isolated_standard_windows_runner")
    return {"name": "github-hosted-windows-p2b1-v1", "reserve": 2 * 1024 ** 3,
            "disk_floor": 4 * 1024 ** 3, "commit_floor": 2 * 1024 ** 3}


def sampled_host(policy: dict[str, Any]) -> dict[str, int]:
    state = host_memory()
    if policy["name"] == "github-hosted-windows-p2b1-v1":
        # The ephemeral host has no capture/DB. Still protect workspace, TEMP
        # and the Windows/pagefile volume independently; use their minimum.
        paths = (ROOT, Path(tempfile.gettempdir()), Path(os.environ["SystemDrive"] + "/"))
        state["disk_free_bytes"] = min(shutil.disk_usage(p).free for p in paths)
    return state


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
    if args.evaluation_tasks:
        return evaluation_workload(args)
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


def evaluation_workload(args) -> dict[str, Any]:
    """P2B.1 direct evaluator tasks, not registered studies or an economic campaign.

    Reference is the unchanged P2A verified evaluator (NOT the unsafe SDK cache).
    Exactly 40 declared parameter points across three existing families are cycled
    for the 1000-task workload. Queue/database latency is explicitly unmeasured.
    """
    import dataclasses
    from contextlib import ExitStack, nullcontext
    from datetime import datetime, timedelta
    from unittest.mock import patch
    from uuid import UUID

    import numpy as np

    from trade_platform import research_data_plane_v1 as plane
    from trade_platform import strategy_lab_data_loading_v1 as loading
    from trade_platform import strategy_lab_walk_forward_v1 as execution
    from trade_platform import strategy_sdk_v1 as sdk
    from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
    from trade_platform.strategy_lab_ledger_v1 import TrialClaimV1, result_content_hash_v1
    from trade_platform.strategy_lab_policies_v1 import (
        gross_cost_policy_v1,
        or3_numeric_policy_v1,
        or5_t2_timing_policy_v1,
    )
    from trade_platform.strategy_lab_study_v1 import (
        UNTOUCHED_HOLDOUT_BOUNDARY_V1,
        DatasetBindingV1,
        ParameterSpaceV1,
        SearchModeV1,
        SearchPlanV1,
        StudySpecV1,
    )
    from trade_platform.strategy_lab_worker_v1 import _evaluate

    payload = json.loads((args.root / "v1/datasets/public-archive-bars" / f"{args.dataset}.json").read_bytes())
    if not payload["identity"].get("synthetic_benchmark_only") or payload["content_hash"] != args.expected:
        raise ValueError("evaluation_benchmark_requires_the_exact_synthetic_dataset")
    last = datetime.fromisoformat(payload["identity"]["last_bar_close_at"])
    if last >= UNTOUCHED_HOLDOUT_BOUNDARY_V1:
        raise ValueError("benchmark_refuses_holdout_dataset")
    binding = DatasetBindingV1("bars", UUID(args.dataset), args.expected, EvidenceTierV1.T2_EVENT_TIME,
                               last + timedelta(seconds=60, microseconds=1))
    studies = []
    for family in sdk.FAMILIES_V1.values():
        space = ParameterSpaceV1.of(*(dataclasses.replace(d, values=d.values[:2]) for d in family.parameter_space().domains))
        studies.append(StudySpecV1(family.spec(), space, (binding,), SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality),
            UNTOUCHED_HOLDOUT_BOUNDARY_V1, policies={"numeric": or3_numeric_policy_v1().payload,
                "timing": or5_t2_timing_policy_v1().payload, "cost": gross_cost_policy_v1().policy().payload},
            label="synthetic workload declaration only; not registered"))
    counters: dict[str, dict[str, Any]] = {}
    decisions: dict[str, str] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    input_fingerprints: dict[str, str] = {}
    scope_receipts: list[dict[str, Any]] = []
    chain = hashlib.sha256()
    completed = failed = excluded = 0
    oracle = None
    if args.reference_receipt:
        receipt_bytes = args.reference_receipt.read_bytes()
        receipt = json.loads(receipt_bytes)
        complete = [r["result"] for r in receipt["runs"] if r["status"] == "COMPLETE"]
        if (receipt.get("implementation") != "reference" or receipt.get("evaluation_tasks") != 40
                or not complete or any(r["dataset_content_hash"] != args.expected or r["completed"] != 40
                    or r["failed"] or r["excluded"] or len(r["unique_results"]) != 40 for r in complete)
                or any(r["unique_results"] != complete[0]["unique_results"] for r in complete)):
            raise ValueError("reference_receipt_dataset_mismatch")
        oracle = complete[0]["unique_results"]

    def timed(name, function, *, decision=False):
        def call(*a, **kw):
            started = time.perf_counter()
            try:
                result = function(*a, **kw)
            finally:
                item = counters.setdefault(name, {"count": 0, "seconds": 0.0})
                item["count"] += 1
                item["seconds"] += time.perf_counter() - started
            if decision:
                value = result[0] if name.startswith("targets:") else result
                decisions[name.split(":")[0]] = hashlib.sha256(np.asarray(value, dtype=np.int64).tobytes()).hexdigest()
            return result
        return call

    loading.clear_bars_cache_v1()
    with ExitStack() as stack:
        stack.enter_context(patch.object(plane.ResearchFrameStoreV1, "verify", timed("verification", plane.ResearchFrameStoreV1.verify)))
        convert = sdk.BarsV1.from_rows
        stack.enter_context(patch.object(sdk.BarsV1, "from_rows", classmethod(lambda cls, *a, **kw: timed("materialization", convert)(*a, **kw))))
        stack.enter_context(patch.object(sdk, "held_positions_v1", timed("held", sdk.held_positions_v1, decision=True)))
        stack.enter_context(patch.object(execution, "_positions_from_schedule_v1",
            timed("held", execution._positions_from_schedule_v1, decision=True)))
        stack.enter_context(patch.object(execution, "_held_decision_indices_v1",
            timed("execution_schedule", execution._held_decision_indices_v1)))
        stack.enter_context(patch.object(sdk, "search_metrics_f64", timed("metrics", sdk.search_metrics_f64)))
        for family in sdk.FAMILIES_V1.values():
            stack.enter_context(patch.object(type(family), "targets_f64", timed("targets:" + family.family, type(family).targets_f64, decision=True)))
        evaluator = loading.BarStrategyEvaluatorV1(args.root)
        dispatch_seconds = 0.0
        before = resources()
        started = time.perf_counter()
        for index, study in enumerate(studies):
            trials = list(study.trials())
            count = (len(trials) if args.evaluation_tasks == 40 else
                     args.evaluation_tasks // 3 + (index < args.evaluation_tasks % 3))
            context = nullcontext(evaluator)
            if args.implementation == "optimized":
                context = evaluator.batch_scope_v1(study, maximum_evaluations=count, maximum_seconds=105.0)
            with context as active:
                if args.implementation == "optimized" and hasattr(active, "identity"):
                    scope_receipts.append(dict(active.identity))
                for ordinal in range(count):
                    trial = trials[ordinal % len(trials)]
                    decisions.clear()
                    # Fixture envelope ONLY: no registration, DB claim or lease
                    # is represented as real. Exercise the real worker's typed
                    # parameter/result-validation wrapper, measuring its Python
                    # overhead separately from the inner evaluator. DB queue and
                    # process-pool overhead remain unmeasured.
                    claim = TrialClaimV1(trial.trial_id, study.study_id, trial.ordinal, trial.content_hash,
                                         trial.parameters, 1, "offline-no-claim", last)
                    inner_seconds = 0.0

                    def evaluated(spec, parameters):
                        nonlocal inner_seconds
                        tick = time.perf_counter()
                        try:
                            return active(spec, parameters)
                        finally:
                            inner_seconds += time.perf_counter() - tick

                    try:
                        dispatched = time.perf_counter()
                        evaluation = _evaluate(study, evaluated, claim)
                        dispatch_seconds += time.perf_counter() - dispatched - inner_seconds
                        match evaluation:
                            case str(reason):
                                raise RuntimeError("worker_evaluation_failed:" + reason)
                    except Exception as error:
                        failed += 1
                        print(json.dumps({"progress": True, "completed": completed, "failed": failed, "excluded": excluded,
                            "error_type": type(error).__name__, "error": str(error), "stages": counters,
                            "seconds": time.perf_counter() - started}, sort_keys=True), flush=True)
                        raise
                    result_hash = result_content_hash_v1(trial_content_hash=trial.content_hash, outcome=evaluation.outcome,
                                                        metrics=evaluation.metrics, cost_policy_slot=study.cost_policy_slot)
                    item = {"study_id": str(study.study_id), "study_hash": study.content_hash, "trial_id": str(trial.trial_id),
                            "trial_hash": trial.content_hash, "result_hash": result_hash, "decisions": dict(decisions),
                            "outcome": evaluation.outcome.value, "metrics": dict(evaluation.metrics)}
                    if trial.content_hash in outcomes and outcomes[trial.content_hash] != item:
                        raise ValueError("non_deterministic_task_result")
                    if oracle is not None and oracle.get(trial.content_hash) != item:
                        raise ValueError("reference_calculation_or_identity_mismatch")
                    outcomes[trial.content_hash] = item
                    chain.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode())
                    completed += 1
                    excluded += evaluation.outcome.value != "EVALUATED"
                    if study.content_hash not in input_fingerprints:
                        bars = (active._bars if args.implementation == "optimized" else
                                sdk._BARS_CACHE[(str(binding.dataset_version_id), binding.content_hash)])
                        fingerprint = bars_fingerprint(bars)
                        input_fingerprints[study.content_hash] = fingerprint
                        del bars
                        if oracle is not None and complete[0]["input_fingerprints"].get(study.content_hash) != fingerprint:
                            raise ValueError("reference_input_snapshot_mismatch")
                    print(json.dumps({"progress": True, "completed": completed, "failed": failed, "excluded": excluded,
                                      "seconds": time.perf_counter() - started, "stages": counters,
                                      "result_chain": chain.hexdigest(), **resources()}, sort_keys=True), flush=True)
        elapsed = time.perf_counter() - started
        after = resources()
    loading.clear_bars_cache_v1()
    return {"implementation": args.implementation, "evaluation_tasks": args.evaluation_tasks, "completed": completed,
            "failed": failed, "excluded": excluded, "unique_configurations_evaluated": len(outcomes),
            "declared_unique_configurations": sum(s.planned_trial_count for s in studies),
            "registered_studies": 0, "queue_database_overhead": "not measured; offline worker-envelope calls only",
            "worker_python_validation_seconds": dispatch_seconds,
            "elapsed_seconds": elapsed, "tasks_per_second": completed / elapsed, "stages": counters,
            "dataset_content_hash": args.expected, "manifest_hash": payload["bar_frame_manifest_hash"],
            "result_chain": chain.hexdigest(), "unique_results": outcomes, "before": before, "after": after,
            "scope_receipts": scope_receipts, "input_fingerprints": input_fingerprints,
            "reference_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest() if oracle is not None else None,
            "physical_io_note": "Windows transfers are not physical disk bytes; progress pipe I/O included; OS cache not flushed"}


def last_evaluation_progress(output: str | bytes) -> dict[str, Any] | None:
    """Last complete flushed receipt; an interrupted trailing line is not success."""
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    for line in reversed(text.splitlines()):
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict) and item.get("progress") is True:
            return item
    return None


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
    policy = resource_policy(args)
    host = sampled_host(policy)
    working_set_limit = args.working_set_mib * MIB
    admission = (working_set_limit if os.name == "nt" else limit) + policy["reserve"]
    if (host["available_physical_bytes"] < admission or host["commit_headroom_bytes"] < limit + policy["reserve"]
            or host["disk_free_bytes"] < policy["disk_floor"]):
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
    partial_stdout: str | bytes = ""
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
            state = sampled_host(policy)
            minimum = {key: min(value, state[key]) for key, value in minimum.items()}
            if (state["available_physical_bytes"] < policy["reserve"]
                    or state["commit_headroom_bytes"] < policy["commit_floor"]
                    or state["disk_free_bytes"] < policy["disk_floor"] or time.monotonic() - started > 120):
                # Windows' communicate reader threads may expose no partial
                # output until EOF. Kill at the same guard, then drain the pipe
                # so a completed prefix is not incorrectly reported as absent.
                if child.poll() is None:
                    child.kill()
                stdout, _ = child.communicate(timeout=5)
                return {"status": "RESOURCE_STOPPED", "minimum_host": minimum,
                        "wall_seconds": time.monotonic() - started,
                        "stop_reason": ("wall_deadline" if time.monotonic() - started > 120 else "host_resource_guard"),
                        "last_progress": last_evaluation_progress(stdout or partial_stdout)}
            try:
                stdout, stderr = child.communicate(token, timeout=0.25)
                break
            except subprocess.TimeoutExpired as error:
                partial_stdout = error.output or b""
                token = None
        if child.returncode:
            return {"status": "FAILED", "exit_code": child.returncode, "stderr": stderr,
                    "wall_seconds": time.monotonic() - started, "failed_processes": 1,
                    "last_progress": last_evaluation_progress(stdout)}
        return {"status": "COMPLETE", "host_before": host, "minimum_host": minimum,
                "host_after": sampled_host(policy), "wall_seconds": time.monotonic() - started,
                "result": json.loads(stdout.splitlines()[-1])}
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
    parser.add_argument("--evaluation-tasks", type=int, choices=(12, 40, 1000), default=0)
    parser.add_argument("--reference-receipt", type=Path)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--hosted-windows-acceptance", action="store_true",
                        help="explicit separate admission for isolated GitHub-hosted Windows only")
    parser.add_argument("--implementation", choices=("reference", "optimized"), default="optimized")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset")
    parser.add_argument("--expected")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--working-set-mib", type=int, choices=(128, 512, 1024), default=128)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.evaluation_tasks and (args.real or args.prepare_fixture or args.profile):
        parser.error("evaluation gate requires synthetic data and unprofiled stage counters")
    try:
        policy = resource_policy(args)
    except ValueError as error:
        parser.error(str(error))
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
    report = {"schema": "research-batch-evaluation-performance-v1" if args.evaluation_tasks else "research-loading-performance-v1", "runs": runs,
              "implementation": args.implementation, "real_development_loader_only": args.real,
              "evaluation_tasks": args.evaluation_tasks,
              "limits": {"process_memory_bytes": limit,
                         "working_set_bytes": args.working_set_mib * MIB if os.name == "nt" else None,
                         "admission_policy": policy["name"],
                         "host_reserve_bytes": policy["reserve"], "disk_floor_bytes": policy["disk_floor"],
                         "runtime_commit_floor_bytes": policy["commit_floor"],
                         "wall_seconds_per_run": 120, "workers": 1, "numeric_threads": 1},
              "environment": {"python": sys.version, "platform": sys.platform},
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  "src/trade_platform/strategy_sdk_v1.py", "src/trade_platform/research_data_plane_v1.py",
                  "src/trade_platform/strategy_lab_data_loading_v1.py", "scripts/research_loading_performance.py",
                  "src/trade_platform/strategy_lab_worker_v1.py", "src/trade_platform/strategy_lab_evaluation_scope_v1.py",
                  "src/trade_platform/strategy_lab_walk_forward_v1.py")
                  if (ROOT / name).exists()}}
    complete = [run["result"] for run in runs if run["status"] == "COMPLETE"]
    if complete and args.evaluation_tasks:
        keys = ("result_chain", "unique_results", "completed", "dataset_content_hash")
        if any(any(r[k] != complete[0][k] for k in keys) for r in complete):
            raise RuntimeError("non_deterministic_evaluation_identity")
        report["median"] = {key: statistics.median(r[key] for r in complete)
                            for key in ("elapsed_seconds", "tasks_per_second")}
    elif complete and not args.prepare_fixture:
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
