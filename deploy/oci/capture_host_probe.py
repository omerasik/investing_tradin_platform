"""Read-only host probe for the OCI capture VM (OCI-1). Standard library only.

Measures what a CPU weight cannot promise on a shared host:

* scheduling lateness -- how late ``time.sleep`` wakes up in this process's
  cgroup (run it from ``trade-capture-health.service``, which carries the
  recorders' CPUWeight, so the figure describes their scheduling class);
* clock evidence -- the newest ``CLOCK_OFFSET_SAMPLE`` the recorders themselves
  wrote into a session lifecycle log (RTT-bounded offset to Bybit server time;
  evidence only, nothing is adjusted);
* headroom -- load, available memory and the cgroup CPU weight in force.

The thresholds are PROPOSED operational acceptance criteria for the cloud
validation stage (deploy/oci/README.md), not evidence rules and not owner
decisions. Exit 0 when every check passes, 1 otherwise. Writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path

#: Proposed: p99 / worst sleep wakeup lateness inside the recorder cgroup.
LATENESS_P99_MS = 10.0
LATENESS_MAX_MS = 100.0
#: Same bound as ``capture_bybit_public.py clock --warn-ms`` (|offset| + bound).
CLOCK_WORST_MS = 250.0
#: Recorders sample every 900 s; allow one missed sample plus slack.
CLOCK_SAMPLE_MAX_AGE_S = 2000.0
#: Proposed: memory the kernel can still hand out without swapping.
MEM_AVAILABLE_MIN_MIB = 2048.0

LIFECYCLE_FILE_NAME = "lifecycle.ndjson"
CLOCK_OFFSET_KIND = "CLOCK_OFFSET_SAMPLE"


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile; ``values`` must be non-empty."""
    if not values:
        raise ValueError("percentile_of_nothing")
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * fraction // 1))  # ceil without float rounding drift
    return ordered[int(rank) - 1]


def measure_sleep_lateness_ms(samples: int, interval_s: float) -> list[float]:
    late: list[float] = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        time.sleep(interval_s)
        late.append((time.perf_counter_ns() - start) / 1e6 - interval_s * 1e3)
    return late


def latest_clock_sample(root: Path) -> dict[str, float] | None:
    """The newest recorder-written clock sample under ``root`` (by arrival time)."""
    best: dict[str, float] | None = None
    for path in root.rglob(LIFECYCLE_FILE_NAME):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue  # a torn last line of an open session
            if event.get("kind") != CLOCK_OFFSET_KIND:
                continue
            detail = json.loads(event["detail"])
            sample = {
                "arrival_utc_nanos": float(event["arrival_utc_nanos"]),
                "offset_ms": detail["offset_estimate_nanos"] / 1e6,
                "bound_ms": detail["offset_bound_nanos"] / 1e6,
            }
            if best is None or sample["arrival_utc_nanos"] > best["arrival_utc_nanos"]:
                best = sample
            break
    return best


def _mem_available_mib() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def _cgroup_cpu_weight() -> str:
    try:
        relative = Path("/proc/self/cgroup").read_text(encoding="utf-8").strip().split("::", 1)[1]
        return (Path("/sys/fs/cgroup") / relative.lstrip("/") / "cpu.weight").read_text().strip()
    except (OSError, IndexError):
        return "unknown (cgroup v1 or not Linux)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="+", type=Path, help="archive roots to read clock evidence from")
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--interval-ms", type=float, default=10.0)
    args = parser.parse_args(argv)
    failed = False

    def check(label: str, ok: bool, text: str) -> None:
        nonlocal failed
        failed |= not ok
        print(f"{label:<14} {'PASS' if ok else 'FAIL'} {text}")

    late = measure_sleep_lateness_ms(args.samples, args.interval_ms / 1e3)
    p50, p99, worst = percentile(late, 0.5), percentile(late, 0.99), max(late)
    check(
        "sched_late",
        p99 <= LATENESS_P99_MS and worst <= LATENESS_MAX_MS,
        f"p50 {p50:.2f} ms p99 {p99:.2f} ms max {worst:.2f} ms over {len(late)} sleeps "
        f"(proposed p99 <= {LATENESS_P99_MS:g}, max <= {LATENESS_MAX_MS:g}); cpu.weight {_cgroup_cpu_weight()}",
    )

    now = time.time_ns()
    for root in args.roots:
        sample = latest_clock_sample(root)
        if sample is None:
            check("clock_sample", False, f"none under {root}")
            continue
        age = (now - sample["arrival_utc_nanos"]) / 1e9
        worst_ms = abs(sample["offset_ms"]) + sample["bound_ms"]
        check(
            "clock_sample",
            worst_ms <= CLOCK_WORST_MS and age <= CLOCK_SAMPLE_MAX_AGE_S,
            f"offset {sample['offset_ms']:+.1f} ms +/- {sample['bound_ms']:.1f} ms, {age:.0f} s old "
            f"({root.name}; |offset|+bound <= {CLOCK_WORST_MS:g}, age <= {CLOCK_SAMPLE_MAX_AGE_S:g})",
        )

    load = os.getloadavg() if hasattr(os, "getloadavg") else (float("nan"),) * 3
    available = _mem_available_mib()
    check(
        "headroom",
        available is not None and available >= MEM_AVAILABLE_MIN_MIB,
        f"load {load[0]:.1f}/{load[1]:.1f}/{load[2]:.1f} on {os.cpu_count()} CPUs, "
        f"MemAvailable {'unknown' if available is None else f'{available:.0f} MiB'} "
        f"(proposed >= {MEM_AVAILABLE_MIN_MIB:g})",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
