"""Bounded, offline Strategy Lab walk-forward benchmark (synthetic; no alpha claim).

Run from the repository root with its analytics environment::

    python scripts/research_performance.py --implementation reference --output .tmp/performance-before.json
    python scripts/research_performance.py --implementation optimized --output .tmp/performance-after.json

Each repetition is a fresh, single-threaded child with a 1 GiB memory ceiling
and a 120 second external deadline. Windows uses a kill-on-close Job Object;
Linux uses RLIMIT_AS. No database, provider, holdout, or production data is read.
The workload includes SDK row materialization and the unchanged three families,
40 trials total and two rolling folds per family. Timings separate loading and
evaluation. OS I/O includes interpreter/import activity; dataset I/O is zero.
The reference mode uses the unchanged SDK position routine, bypassing the new
schedule preparation; it is an offline benchmark oracle, never a runtime switch.
Compare repeated medians on the same host, not a profiled run against a normal
run. A report hash is a correctness check, never a performance/alpha assertion.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MEMORY_BYTES = 1024 ** 3
DEADLINE_SECONDS = 120
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def _windows_job(process: subprocess.Popen[str]) -> tuple[Any, Any]:
    """Attach the stdin-blocked child before it imports analytics or builds data."""
    from ctypes import wintypes as wt

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wt.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("active", wt.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wt.DWORD), ("scheduling", wt.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.basic.flags = 0x100 | 0x8 | 0x2000  # PROCESS_MEMORY / ACTIVE_PROCESS / KILL_ON_JOB_CLOSE
    limits.basic.active = 1
    limits.process_memory = MEMORY_BYTES
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job, int(process._handle)):
        kernel.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, job


def _resources() -> dict[str, Any]:
    if os.name == "nt":
        from ctypes import wintypes as wt

        class Memory(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("faults", wt.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                    "commit", "peak_commit")]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wt.HANDLE
        kernel.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        kernel.GetProcessIoCounters.argtypes = [wt.HANDLE, ctypes.c_void_p]
        handle = kernel.GetCurrentProcess()
        memory, io = Memory(), IO()
        memory.cb = ctypes.sizeof(memory)
        if not kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.GetProcessIoCounters(handle, ctypes.byref(io)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"peak_rss_bytes": memory.peak_ws, "peak_commit_bytes": memory.peak_commit,
                "os_read_bytes": io.read_bytes, "os_write_bytes": io.write_bytes,
                "page_faults": memory.faults}
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"peak_rss_bytes": int(usage.ru_maxrss) * 1024, "peak_commit_bytes": None,
            "os_read_bytes": usage.ru_inblock * 512, "os_write_bytes": usage.ru_oublock * 512,
            "page_faults": usage.ru_majflt + usage.ru_minflt}


def _workload(rows: int) -> tuple[Any, list[Any], Any, Any]:
    import random
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from uuid import NAMESPACE_URL, uuid5

    from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
    from trade_platform.strategy_lab_manifest_v1 import RankDirectionV1
    from trade_platform.strategy_lab_policies_v1 import (
        gross_cost_policy_v1,
        or3_numeric_policy_v1,
        or5_t2_timing_policy_v1,
    )
    from trade_platform.strategy_lab_splits_v1 import SplitModeV1, WalkForwardPlanV1
    from trade_platform.strategy_lab_study_v1 import (
        UNTOUCHED_HOLDOUT_BOUNDARY_V1,
        DatasetBindingV1,
        SearchModeV1,
        SearchPlanV1,
        StudySpecV1,
    )
    from trade_platform.strategy_lab_study_v1 import (
        ParameterDomainV1 as D,
    )
    from trade_platform.strategy_lab_study_v1 import (
        ParameterSpaceV1 as S,
    )
    from trade_platform.strategy_lab_walk_forward_v1 import WalkForwardSelectionRuleV1
    from trade_platform.strategy_sdk_v1 import FAMILIES_V1, BarsV1

    rng = random.Random(741)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    price = Decimal("30000.0")
    raw = []
    for i in range(rows):
        opened = start + timedelta(minutes=i + (2880 if i >= rows // 2 else 0))
        close = price + Decimal(rng.randrange(-40, 41)) / 10
        raw.append((opened, opened + timedelta(minutes=1), None, price,
                    max(price, close) + Decimal("0.5"), min(price, close) - Decimal("0.5"), close))
        price = close
    bars = BarsV1.from_rows(raw)
    end = raw[-1][1] + timedelta(minutes=1)
    fingerprint = hashlib.sha256(bars.open_us.tobytes() + bars.close_ticks.tobytes()).hexdigest()
    binding = DatasetBindingV1("bars", uuid5(NAMESPACE_URL, f"synthetic-performance:{fingerprint}"),
                               fingerprint, EvidenceTierV1.T2_EVENT_TIME, end + timedelta(minutes=1))
    direction = D.categorical_set("direction", ["long_only", "long_short"])
    spaces = {
        "trend_ma_cross": S.of(D.integer_values("fast_bars", [15, 60]),
                               D.integer_values("slow_bars", [120, 480]),
                               D.decimal_set("band", ["0", "0.0005"]), direction),
        "mean_reversion_z": S.of(D.integer_values("lookback_bars", [30, 240]),
                                 D.decimal_set("entry_z", ["1.5", "2"]),
                                 D.decimal_set("exit_z", ["0", "0.5"]), direction),
        "breakout_channel": S.of(D.integer_values("entry_bars", [60, 240]),
                                 D.integer_values("exit_bars", [15, 30]), direction),
    }
    studies = [StudySpecV1(
        strategy=FAMILIES_V1[name].spec(), parameter_space=space, datasets=(binding,),
        search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality),
        evaluation_upper_bound_exclusive=UNTOUCHED_HOLDOUT_BOUNDARY_V1,
        policies={"numeric": or3_numeric_policy_v1().payload, "timing": or5_t2_timing_policy_v1().payload,
                  "cost": gross_cost_policy_v1().policy().payload},
        label="SYNTHETIC PERFORMANCE ONLY",
    ) for name, space in spaces.items()]
    plan = WalkForwardPlanV1(mode=SplitModeV1.ROLLING, train_duration=timedelta(days=30),
                             test_duration=timedelta(days=7), step=timedelta(days=7),
                             embargo=timedelta(days=1), first_test_start=end - timedelta(days=14),
                             evaluation_end_exclusive=end)
    return bars, studies, plan, WalkForwardSelectionRuleV1("total_return", RankDirectionV1.HIGHER_IS_BETTER)


def _worker(rows: int, profile: bool, implementation: str) -> dict[str, Any]:
    # Parent sets the OS limit before sending this permission to begin.
    if sys.stdin.readline().strip() != "RUN_BOUNDED_SYNTHETIC":
        raise RuntimeError("missing_parent_resource_admission")
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    sys.path.insert(0, str(ROOT / "src"))
    from contextlib import ExitStack
    from datetime import timedelta
    from unittest.mock import patch

    from trade_platform import strategy_lab_walk_forward_v1 as walk_forward
    from trade_platform.strategy_sdk_v1 import held_positions_v1, restrict_to_bound_v1

    started = time.perf_counter()
    bars, studies, plan, rule = _workload(rows)
    materialized = time.perf_counter()
    import cProfile
    import pstats

    profiler = cProfile.Profile()
    if profile:
        profiler.enable()
    with ExitStack() as stack:
        if implementation == "reference":
            lag = studies[0].timing_lag
            bounded = restrict_to_bound_v1(bars, plan.evaluation_end_exclusive, lag)
            lag_us = lag // timedelta(microseconds=1)
            stack.enter_context(patch.object(walk_forward, "_held_decision_indices_v1",
                                             new=lambda bars, lag_us: None))
            stack.enter_context(patch.object(walk_forward, "_positions_from_schedule_v1",
                new=lambda targets, schedule: held_positions_v1(bounded, targets, lag_us)))
        reports = [walk_forward.run_walk_forward_v1(study, plan, rule, bars=bars) for study in studies]
    if profile:
        profiler.disable()
    finished = time.perf_counter()
    identities = [{"study_id": str(study.study_id), "study_hash": study.content_hash,
                   "trial_hashes": [trial.content_hash for trial in study.trials()],
                   "report_hash": report.report_hash} for study, report in zip(studies, reports, strict=True)]
    stats = pstats.Stats(profiler) if profile else None
    return {"rows": rows, "implementation": implementation,
            "trials": sum(study.planned_trial_count for study in studies),
            "folds_per_family": 2, "profiled": profile,
            "materialization_seconds": materialized - started,
            "evaluation_seconds": finished - materialized, "elapsed_seconds": finished - started,
            "trials_per_second": 40 / (finished - materialized), "identities": identities,
            "dataset_read_bytes": 0, "dataset_write_bytes": 0,
            "resources": _resources(),
            "profile": [] if stats is None else [
                {"function": f"{key[0]}:{key[1]}:{key[2]}", "calls": value[1],
                 "self_seconds": value[2], "cumulative_seconds": value[3]}
                for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:18]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=86_400)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--implementation", choices=("reference", "optimized"), default="optimized")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 64_800 <= args.rows <= 100_800 or not 1 <= args.repeats <= 5:
        parser.error("bounded workload requires 64800..100800 rows and 1..5 repetitions")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        parser.error("hard memory limits are implemented only for Windows and Linux")
    if args.worker:
        print(json.dumps(_worker(args.rows, args.profile, args.implementation), sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required")
    environment = os.environ.copy()
    environment.update(dict.fromkeys(THREAD_VARIABLES, "1"))
    interpreter = sys.executable
    if os.name == "nt":
        environment["__PYVENV_LAUNCHER__"] = sys.executable
        interpreter = sys._base_executable
    runs = []
    for _ in range(args.repeats):
        command = [interpreter, str(Path(__file__).resolve()), "--worker", "--rows", str(args.rows),
                   "--implementation", args.implementation]
        if args.profile:
            command.append("--profile")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=environment, cwd=ROOT)
        kernel = job = None
        try:
            if os.name == "nt":
                kernel, job = _windows_job(process)
            stdout, stderr = process.communicate("RUN_BOUNDED_SYNTHETIC\n", timeout=DEADLINE_SECONDS)
            if process.returncode:
                raise RuntimeError(f"bounded benchmark failed ({process.returncode}): {stderr}")
            runs.append(json.loads(stdout))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if job is not None:
                kernel.CloseHandle(job)
    if any(run["identities"] != runs[0]["identities"] for run in runs):
        raise RuntimeError("non_deterministic_benchmark_results")
    report = {"schema": "research-performance-v1", "synthetic_only": True,
              "limits": {"memory_bytes": MEMORY_BYTES, "wall_seconds_per_run": DEADLINE_SECONDS,
                         "workers": 1, "numeric_threads": 1},
              "environment": {"python": sys.version, "platform": sys.platform},
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "sdk_sha256": hashlib.sha256((ROOT / "src/trade_platform/strategy_sdk_v1.py").read_bytes()).hexdigest(),
              "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "walk_forward_sha256": hashlib.sha256(
                  (ROOT / "src/trade_platform/strategy_lab_walk_forward_v1.py").read_bytes()).hexdigest(),
              "runs": runs,
              "median": {name: statistics.median(run[name] for run in runs) for name in (
                  "materialization_seconds", "evaluation_seconds", "elapsed_seconds", "trials_per_second")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "median": report["median"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
