"""Phase R1A -- capture capacity measurement, stated without overclaiming.

``RESEARCH_ONLY``. Measures what first-party capture costs on the operator's
host -- records per second, bytes on disk before and after lossless
compaction, CPU and memory -- so the owner can choose a production capture
universe (decision OR-2) from evidence rather than from a guess. Nothing here
chooses a universe, writes evidence, or feeds any economic decision.

The timestamp delta is not latency
----------------------------------
For every captured message the recorder has its own arrival reading and Bybit's
message timestamp ``ts``. Their difference, :class:`TimestampDeltaHistogramV1`,
is reported as an **observed timestamp delta** and never as dissemination
latency. It mixes network transit, venue-side batching, the host clock's coarse
resolution (about 15.6 ms on Windows) and an unknown offset between the host's
wall clock and Bybit's clock -- and nothing in the delta alone separates them.

What *can* be measured is a bound on that offset. :func:`sample_bybit_server_clock_offset_v1`
reads Bybit's public server-time endpoint between two host clock readings; the
server instant must lie between them, so the offset estimate is bounded by half
the round trip (plus the host clock's resolution). Those samples are recorded
beside the deltas as evidence. Whether they bound the offset tightly enough to
characterize latency is a later judgement, not something this module asserts;
they must not be used to justify a T2 publication-lag value.

Projections are labelled as projections
---------------------------------------
Per-day figures are linear extrapolations of the measured window, reported as
such. A short window at one time of day is not a day of market activity.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.error import URLError
from urllib.request import Request, urlopen

from .first_party_capture_archive_v1 import (
    CompactionResultV1,
    FirstPartyCaptureRecordV1,
    measure_clock_resolution_nanos,
)
from .first_party_capture_authority_v1 import (
    MEASUREMENT_SAMPLE_V1,
    FirstPartyCaptureContractV1,
)

CAPACITY_REPORT_SCHEMA_VERSION: Final = "first-party-capture-capacity-report-v1"

#: Bybit's official public server-time endpoint. No credential, no account.
BYBIT_SERVER_TIME_URL_V1: Final = "https://api.bybit.com/v5/market/time"

DELTA_LABEL_V1: Final = "observed_arrival_minus_message_ts_delta_millis"
DELTA_CAVEAT_V1: Final = (
    "Observed timestamp delta, not latency: it includes network transit, venue "
    "batching, host clock resolution and an unknown host-versus-venue clock "
    "offset. See clock_offset_samples for RTT-bounded offset evidence."
)
PROJECTION_LABEL_V1: Final = "linear_extrapolation_of_the_measured_window"

_NANOS_PER_MILLI: Final = 1_000_000
_SECONDS_PER_DAY: Final = 86_400


class TimestampDeltaHistogramV1:
    """Exact integer-millisecond histogram of observed timestamp deltas.

    Sparse and bounded by the number of distinct millisecond values seen, so a
    day of messages costs kilobytes, not a list of every delta. Quantiles are
    exact at millisecond granularity (nearest-rank).
    """

    __slots__ = ("_buckets", "count")

    def __init__(self) -> None:
        self._buckets: dict[int, int] = {}
        self.count = 0

    def add(self, delta_millis: int) -> None:
        self._buckets[delta_millis] = self._buckets.get(delta_millis, 0) + 1
        self.count += 1

    def quantile(self, q: float) -> int | None:
        if not 0.0 <= q <= 1.0:
            raise ValueError("quantile_must_be_between_zero_and_one")
        if self.count == 0:
            return None
        rank = max(1, math.ceil(q * self.count))
        seen = 0
        for value in sorted(self._buckets):
            seen += self._buckets[value]
            if seen >= rank:
                return value
        return max(self._buckets)  # pragma: no cover - rank <= count always matches

    def summary(self) -> dict[str, int | None]:
        return {
            "count": self.count,
            "min": None if self.count == 0 else min(self._buckets),
            "p50": self.quantile(0.50),
            "p90": self.quantile(0.90),
            "p99": self.quantile(0.99),
            "p999": self.quantile(0.999),
            "max": None if self.count == 0 else max(self._buckets),
        }


class SymbolCaptureStatsV1:
    """Per-contract counters fed by the recorder's record observer.

    Thread-confined: a fleet gives each contract its own instance, and a
    restarted lane reuses it only after the previous recorder's thread ended.
    """

    __slots__ = ("_lock", "bytes_by_channel", "contract", "deltas", "records_by_channel")

    def __init__(self, contract: FirstPartyCaptureContractV1) -> None:
        self.contract = contract
        self.records_by_channel: dict[str, int] = {}
        self.bytes_by_channel: dict[str, int] = {}
        self.deltas: dict[str, TimestampDeltaHistogramV1] = {}
        self._lock = threading.Lock()

    def observe(self, record: FirstPartyCaptureRecordV1, written_bytes: int) -> None:
        with self._lock:
            channel = record.channel
            self.records_by_channel[channel] = self.records_by_channel.get(channel, 0) + 1
            self.bytes_by_channel[channel] = self.bytes_by_channel.get(channel, 0) + written_bytes
            if record.exchange_timestamp_millis is not None:
                delta = (
                    record.arrival_utc_nanos
                    - record.exchange_timestamp_millis * _NANOS_PER_MILLI
                ) // _NANOS_PER_MILLI
                self.deltas.setdefault(channel, TimestampDeltaHistogramV1()).add(delta)

    @property
    def records(self) -> int:
        return sum(self.records_by_channel.values())

    @property
    def bytes(self) -> int:
        return sum(self.bytes_by_channel.values())


@dataclass(frozen=True, slots=True)
class ProcessResourceSampleV1:
    monotonic_seconds: float
    cpu_seconds: float
    working_set_bytes: int | None
    peak_working_set_bytes: int | None


def _windows_memory() -> tuple[int | None, int | None]:
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - Windows-only attribute
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Counters),
        wintypes.DWORD,
    ]
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return None, None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def sample_process_resources_v1() -> ProcessResourceSampleV1:
    """CPU seconds across all threads, plus current and peak resident memory."""
    working: int | None
    peak: int | None
    if sys.platform == "win32":
        try:
            working, peak = _windows_memory()
        except (OSError, AttributeError, ctypes.ArgumentError):
            working, peak = None, None
    else:
        import resource

        # ru_maxrss is kilobytes on Linux; no portable current-RSS reading.
        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
        working = None
    return ProcessResourceSampleV1(
        monotonic_seconds=time.monotonic(),
        cpu_seconds=time.process_time(),
        working_set_bytes=working,
        peak_working_set_bytes=peak,
    )


@dataclass(frozen=True, slots=True)
class ClockOffsetSampleV1:
    """One RTT-bounded reading of Bybit server time against the host clock.

    ``offset_estimate_nanos`` is ``server - midpoint(send, receive)``; positive
    means Bybit's clock read later than the host's. The true offset lies within
    ``half_round_trip_nanos`` of the estimate, widened by the host clock
    resolution, provided the server read its clock between the two host
    readings. Evidence about the offset, not a synchronization.
    """

    host_send_utc_nanos: int
    host_receive_utc_nanos: int
    server_utc_nanos: int
    host_clock_resolution_nanos: int

    @property
    def half_round_trip_nanos(self) -> int:
        return (self.host_receive_utc_nanos - self.host_send_utc_nanos) // 2

    @property
    def offset_estimate_nanos(self) -> int:
        midpoint = (self.host_send_utc_nanos + self.host_receive_utc_nanos) // 2
        return self.server_utc_nanos - midpoint

    @property
    def offset_bound_nanos(self) -> int:
        return self.half_round_trip_nanos + self.host_clock_resolution_nanos

    def to_payload(self) -> dict[str, int]:
        return {
            "host_send_utc_nanos": self.host_send_utc_nanos,
            "host_receive_utc_nanos": self.host_receive_utc_nanos,
            "server_utc_nanos": self.server_utc_nanos,
            "host_clock_resolution_nanos": self.host_clock_resolution_nanos,
            "offset_estimate_nanos": self.offset_estimate_nanos,
            "offset_bound_nanos": self.offset_bound_nanos,
        }


def parse_bybit_server_time_nanos_v1(body: str) -> int:
    """Bybit's ``/v5/market/time`` body to integer nanoseconds, or raise."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise ValueError("bybit_server_time_response_not_ok")
    result = payload.get("result")
    nanos = result.get("timeNano") if isinstance(result, dict) else None
    if not isinstance(nanos, str) or not nanos.isdigit():
        raise ValueError("bybit_server_time_nanos_malformed")
    return int(nanos)


def _fetch_server_time_body(timeout_seconds: float) -> str:
    request = Request(
        BYBIT_SERVER_TIME_URL_V1,
        headers={"Accept": "application/json", "User-Agent": "trade-platform-first-party-capture/1.0"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - fixed HTTPS URL
        return str(response.read().decode("utf-8"))


def sample_bybit_server_clock_offset_v1(
    *,
    fetch: Callable[[float], str] = _fetch_server_time_body,
    clock_ns: Callable[[], int] = time.time_ns,
    timeout_seconds: float = 10.0,
) -> ClockOffsetSampleV1 | None:
    """One offset sample, or ``None`` if the public endpoint could not be read."""
    resolution = measure_clock_resolution_nanos()
    send = clock_ns()
    try:
        body = fetch(timeout_seconds)
    except (OSError, URLError, ValueError):
        return None
    receive = clock_ns()
    try:
        server = parse_bybit_server_time_nanos_v1(body)
    except ValueError:
        return None
    return ClockOffsetSampleV1(
        host_send_utc_nanos=send,
        host_receive_utc_nanos=receive,
        server_utc_nanos=server,
        host_clock_resolution_nanos=resolution,
    )


def bybit_clock_offset_payload_v1() -> dict[str, int] | None:
    """One offset sample as the JSON payload a fleet hands to its recorders."""
    sample = sample_bybit_server_clock_offset_v1()
    return None if sample is None else sample.to_payload()


@dataclass(slots=True)
class CapacityMeasurementV1:
    """Everything a measurement run accumulates, gathered for the report."""

    stats: dict[str, SymbolCaptureStatsV1] = field(default_factory=dict)
    resource_start: ProcessResourceSampleV1 | None = None
    resource_end: ProcessResourceSampleV1 | None = None
    peak_working_set_bytes: int | None = None

    def stats_for(self, contract: FirstPartyCaptureContractV1) -> SymbolCaptureStatsV1:
        key = str(contract.source_id)
        if key not in self.stats:
            self.stats[key] = SymbolCaptureStatsV1(contract)
        return self.stats[key]

    def observer_for(
        self, contract: FirstPartyCaptureContractV1
    ) -> Callable[[FirstPartyCaptureRecordV1, int], None]:
        return self.stats_for(contract).observe

    def sample_resources(self) -> None:
        sample = sample_process_resources_v1()
        if self.resource_start is None:
            self.resource_start = sample
        self.resource_end = sample
        if sample.peak_working_set_bytes is not None:
            self.peak_working_set_bytes = max(
                self.peak_working_set_bytes or 0, sample.peak_working_set_bytes
            )


def _per_day(amount: float, seconds: float) -> float | None:
    return None if seconds <= 0 else amount * _SECONDS_PER_DAY / seconds


def build_capacity_report_v1(
    measurement: CapacityMeasurementV1,
    *,
    contracts: Sequence[FirstPartyCaptureContractV1],
    measured_seconds: float,
    compactions: Sequence[CompactionResultV1],
    session_summaries: Mapping[str, Mapping[str, int]],
    disk_free_start_bytes: int,
    disk_free_end_bytes: int,
    failures: Mapping[str, int],
    restarts: int,
    end_proof: str,
    clock_samples: Sequence[Mapping[str, Any]] = (),
    clock_sample_failures: int = 0,
) -> dict[str, Any]:
    """The capacity report as plain JSON-ready data. Measured facts only.

    ``session_summaries`` maps a source id to per-symbol recorder counters
    (``reconnects``, ``gaps``, ``contract_violations``, ``clock_discontinuities``).
    """
    ranks = dict(MEASUREMENT_SAMPLE_V1)
    compaction_by_source: dict[str, tuple[int, int]] = {}
    for result in compactions:
        key = str(result.source_id)
        raw, packed = compaction_by_source.get(key, (0, 0))
        compaction_by_source[key] = (raw + result.uncompressed_bytes, packed + result.compressed_bytes)

    symbols: list[dict[str, Any]] = []
    total_records = 0
    total_bytes = 0
    total_compressed_per_day = 0.0
    compressed_complete = True
    for contract in contracts:
        key = str(contract.source_id)
        stats = measurement.stats.get(key) or SymbolCaptureStatsV1(contract)
        raw, packed = compaction_by_source.get(key, (0, 0))
        ratio = None if packed == 0 else raw / packed
        raw_per_day = _per_day(stats.bytes, measured_seconds)
        compressed_per_day = (
            None if raw_per_day is None or ratio is None else raw_per_day / ratio
        )
        if compressed_per_day is None:
            compressed_complete = False
        else:
            total_compressed_per_day += compressed_per_day
        total_records += stats.records
        total_bytes += stats.bytes
        symbols.append(
            {
                "exchange_symbol": contract.exchange_symbol,
                "turnover_rank_when_sampled": ranks.get(contract.exchange_symbol),
                "source_id": key,
                "records": stats.records,
                "records_by_channel": dict(sorted(stats.records_by_channel.items())),
                "records_per_second": (
                    None if measured_seconds <= 0 else stats.records / measured_seconds
                ),
                "raw_bytes": stats.bytes,
                "raw_bytes_by_channel": dict(sorted(stats.bytes_by_channel.items())),
                "raw_bytes_per_day_projected": raw_per_day,
                "compacted_raw_bytes": raw,
                "compacted_bytes": packed,
                "compression_ratio": ratio,
                "compressed_bytes_per_day_projected": compressed_per_day,
                DELTA_LABEL_V1: {
                    channel: histogram.summary()
                    for channel, histogram in sorted(stats.deltas.items())
                },
                "recorder": dict(session_summaries.get(key, {})),
            }
        )

    start = measurement.resource_start
    end = measurement.resource_end
    cpu_seconds = None if start is None or end is None else end.cpu_seconds - start.cpu_seconds
    wall = None if start is None or end is None else end.monotonic_seconds - start.monotonic_seconds
    offsets = list(clock_samples)
    return {
        "schema_version": CAPACITY_REPORT_SCHEMA_VERSION,
        "measured_seconds": measured_seconds,
        "end_proof": end_proof,
        "projection_label": PROJECTION_LABEL_V1,
        "delta_caveat": DELTA_CAVEAT_V1,
        "symbols": symbols,
        "totals": {
            "records": total_records,
            "records_per_second": (
                None if measured_seconds <= 0 else total_records / measured_seconds
            ),
            "raw_bytes": total_bytes,
            "raw_bytes_per_day_projected": _per_day(total_bytes, measured_seconds),
            "compressed_bytes_per_day_projected": (
                total_compressed_per_day if compressed_complete else None
            ),
        },
        "host": {
            "cpu_count": os.cpu_count(),
            "process_cpu_seconds": cpu_seconds,
            "process_cpu_percent_of_one_core": (
                None if not cpu_seconds or not wall else 100.0 * cpu_seconds / wall
            ),
            "peak_working_set_bytes": measurement.peak_working_set_bytes,
            "disk_free_start_bytes": disk_free_start_bytes,
            "disk_free_end_bytes": disk_free_end_bytes,
            "clock_resolution_nanos": measure_clock_resolution_nanos(),
        },
        "clock_offset_samples": [dict(sample) for sample in offsets],
        "clock_offset_sample_failures": clock_sample_failures,
        "clock_offset_summary": (
            None
            if not offsets
            else {
                "min_offset_estimate_nanos": min(s["offset_estimate_nanos"] for s in offsets),
                "max_offset_estimate_nanos": max(s["offset_estimate_nanos"] for s in offsets),
                "max_offset_bound_nanos": max(s["offset_bound_nanos"] for s in offsets),
                "min_offset_bound_nanos": min(s["offset_bound_nanos"] for s in offsets),
            }
        ),
        "restarts": restarts,
        "failures": dict(failures),
    }


def _mib(value: float | None) -> str:
    return "n/a" if value is None else f"{value / (1024 * 1024):,.1f} MiB"


def render_capacity_report_text_v1(report: Mapping[str, Any]) -> str:
    """A compact operator-readable rendering of :func:`build_capacity_report_v1`."""
    lines = [
        (
            f"capacity report ({report['schema_version']}), measured "
            f"{report['measured_seconds']:.0f}s, end_proof={report['end_proof']}"
        ),
        f"per-day figures: {report['projection_label']}",
        "",
        (
            f"{'symbol':<10}{'rank':>5}{'rec/s':>9}{'raw/day':>16}{'ratio':>7}{'gz/day':>14}"
            "  delta p50/p99 ms (tickers | trades)"
        ),
    ]
    for item in report["symbols"]:
        deltas = item[DELTA_LABEL_V1]

        def pair(channel: str, deltas: Mapping[str, Any] = deltas) -> str:
            summary = deltas.get(channel)
            return "-" if not summary else f"{summary['p50']}/{summary['p99']}"

        ratio = item["compression_ratio"]
        rate = item["records_per_second"]
        lines.append(
            f"{item['exchange_symbol']:<10}{item['turnover_rank_when_sampled'] or '-':>5}"
            f"{(rate or 0):>9.2f}{_mib(item['raw_bytes_per_day_projected']):>16}"
            f"{('n/a' if ratio is None else f'{ratio:.1f}x'):>7}"
            f"{_mib(item['compressed_bytes_per_day_projected']):>14}"
            f"  {pair('tickers')} | {pair('publicTrade')}"
        )
    totals = report["totals"]
    host = report["host"]
    lines += [
        "",
        (
            f"total: {totals['records_per_second'] or 0:.2f} rec/s, raw/day "
            f"{_mib(totals['raw_bytes_per_day_projected'])}, compressed/day "
            f"{_mib(totals['compressed_bytes_per_day_projected'])}"
        ),
        (
            f"host: cpu {host['process_cpu_percent_of_one_core'] or 0:.1f}% of one core "
            f"({host['cpu_count']} logical), peak working set "
            f"{_mib(host['peak_working_set_bytes'])}, disk free "
            f"{_mib(host['disk_free_start_bytes'])} -> {_mib(host['disk_free_end_bytes'])}"
        ),
        f"clock resolution {host['clock_resolution_nanos'] / 1e6:.3f} ms",
    ]
    offset = report["clock_offset_summary"]
    if offset is None:
        lines.append("clock offset: no samples")
    else:
        lines.append(
            "clock offset estimate (server - host) "
            f"{offset['min_offset_estimate_nanos'] / 1e6:+.1f}..{offset['max_offset_estimate_nanos'] / 1e6:+.1f} ms,"
            f" bound <= {offset['max_offset_bound_nanos'] / 1e6:.1f} ms"
        )
    lines += [f"note: {report['delta_caveat']}"]
    for failure, count in report["failures"].items():
        lines.append(f"lane failure x{count}: {failure}")
    lines.append(f"restarts: {report['restarts']}")
    return "\n".join(lines)


def write_capacity_report_v1(report: Mapping[str, Any], directory: Path, *, stamp: str) -> Path:
    """Write the report JSON (and its text rendering) under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"capacity-report-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.with_suffix(".txt").write_text(render_capacity_report_text_v1(report) + "\n", encoding="utf-8")
    return path


__all__ = [
    "BYBIT_SERVER_TIME_URL_V1",
    "CAPACITY_REPORT_SCHEMA_VERSION",
    "DELTA_CAVEAT_V1",
    "DELTA_LABEL_V1",
    "PROJECTION_LABEL_V1",
    "CapacityMeasurementV1",
    "ClockOffsetSampleV1",
    "ProcessResourceSampleV1",
    "SymbolCaptureStatsV1",
    "TimestampDeltaHistogramV1",
    "build_capacity_report_v1",
    "bybit_clock_offset_payload_v1",
    "parse_bybit_server_time_nanos_v1",
    "render_capacity_report_text_v1",
    "sample_bybit_server_clock_offset_v1",
    "sample_process_resources_v1",
    "write_capacity_report_v1",
]
