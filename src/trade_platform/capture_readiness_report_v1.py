"""Bounded, read-only R1B recovery report; no host or acceptance authority.

Every input partition is explicitly selected and fully replayed by the existing
archive verifier. Nothing recursively searches an archive or contacts a host.
The caller supplies the diagnostic hand-off bound: formal OR-1 bound S remains
UNRESOLVED. COMPLETE labels and the historical 24-hour comparison are therefore
diagnostic, never owner acceptance. The approved 60-second paging threshold is
used only by the existing operational coverage contract.

Host observations must come from independent probes, name their evidence and
declare their own freshness limit. Missing, stale or future observations are
UNKNOWN; an alive recorder or passing ping never supplies a coverage window.
All history is explicitly limited to the requested UTC interval and selected
partitions, not advertised as an archive-wide maximum or an as-of reconstruction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from trade_platform.first_party_capture_archive_v1 import (
    PARTITION_STATUS_COMPLETE,
    FirstPartyCaptureArchiveError,
    ProvenWindowV1,
    read_partition_status_v1,
    verify_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_universe_contract_v1,
)
from trade_platform.first_party_capture_coverage_alert_v1 import (
    MeasuredHourCoverageV1,
    coverage_breaches_v1,
    measured_hour_coverage_v1,
)
from trade_platform.first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    AcceptanceStatusV1,
    HourlySegmentV1,
    acceptance_status_v1,
    chain_proven_runs_v1,
    classify_hour_v1,
    derive_hourly_acceptance_v1,
)

REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
HOST_COMPONENTS = ("recorder", "disk", "memory", "clock", "deadman", "oci")
PAGING_THRESHOLD_NANOS = 60_000_000_000
OR1_REQUIRED_HOURS = 24


class HostStateV1(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class HostObservationV1:
    component: str
    state: HostStateV1
    evidence_reference: str
    observed_at_utc_nanos: int
    max_age_nanos: int
    detail: str


@dataclass(frozen=True, slots=True)
class HostHealthV1:
    component: str
    state: HostStateV1
    reason: str
    observation: HostObservationV1 | None


@dataclass(frozen=True, slots=True)
class PartitionRefusalV1:
    symbol: str
    directory: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SymbolReadinessV1:
    symbol: str
    latest_diagnostic_complete_hour_start_utc_nanos: int | None
    hours: tuple[HourlySegmentV1, ...]
    missing_hours_utc_nanos: tuple[int, ...]
    incomplete_hours: tuple[HourlySegmentV1, ...]
    continuity_holes: tuple[MeasuredHourCoverageV1, ...]
    paging_breaches: tuple[MeasuredHourCoverageV1, ...]
    verified_partitions: int


@dataclass(frozen=True, slots=True)
class CaptureReadinessReportV1:
    start_utc_nanos: int
    end_utc_nanos: int
    observed_at_utc_nanos: int
    selected_partitions: tuple[tuple[str, str], ...]
    symbols: tuple[SymbolReadinessV1, ...]
    diagnostic_status: AcceptanceStatusV1
    longest_diagnostic_joint_run_hours: int
    current_run_hours: int
    host_health: tuple[HostHealthV1, ...]
    refusals: tuple[PartitionRefusalV1, ...]
    reasons: tuple[str, ...]
    owner_actions: tuple[str, ...]
    formal_bound_s: str = "UNRESOLVED_OWNER_APPROVAL_REQUIRED"
    formal_acceptance: str = "UNAVAILABLE"
    readiness: str = "NO_GO"
    paging_threshold_nanos: int = PAGING_THRESHOLD_NANOS
    history_scope: str = "REQUESTED_INTERVAL_AND_SELECTED_PARTITIONS_ONLY"


def _health(
    observations: Sequence[HostObservationV1], observed_at: int,
) -> tuple[HostHealthV1, ...]:
    if any(item.component not in HOST_COMPONENTS for item in observations):
        raise ValueError("unknown_host_component")
    if len({item.component for item in observations}) != len(observations):
        raise ValueError("duplicate_host_component")
    result = []
    for component in HOST_COMPONENTS:
        item = next((item for item in observations if item.component == component), None)
        reason = "INDEPENDENT_OBSERVATION_UNAVAILABLE"
        if item is not None:
            if not isinstance(item.state, HostStateV1):
                raise ValueError("host_state_requires_explicit_enum")
            if (type(item.observed_at_utc_nanos) is not int or type(item.max_age_nanos) is not int
                    or item.observed_at_utc_nanos < 0):
                raise ValueError("host_observation_times_require_nonnegative_integer_nanos")
            age = observed_at - item.observed_at_utc_nanos
            if not item.evidence_reference.strip() or not item.detail.strip():
                reason = "OBSERVATION_LACKS_EVIDENCE_REFERENCE_OR_DETAIL"
            elif item.max_age_nanos < 0 or age < 0:
                reason = "OBSERVATION_TIME_INVALID"
            elif age > item.max_age_nanos:
                reason = "OBSERVATION_STALE"
            else:
                result.append(HostHealthV1(component, item.state, item.detail, item))
                continue
        result.append(HostHealthV1(component, HostStateV1.UNKNOWN, reason, item))
    return tuple(result)


def capture_readiness_report_v1(
    partitions: Sequence[tuple[str, Path]],
    *,
    start_utc_nanos: int,
    end_utc_nanos: int,
    observed_at_utc_nanos: int,
    diagnostic_max_unproven_nanos: int,
    max_partitions: int,
    max_report_hours: int,
    host_observations: Sequence[HostObservationV1] = (),
) -> CaptureReadinessReportV1:
    """Verify explicit local partitions and project existing acceptance contracts.

    ``end`` is exclusive and may include the pending UTC hour, but never a
    future hour. Only fully elapsed hours receive verdicts. Full replay is
    bounded by the explicit partition selection; resource budgets are caller
    limits, not evidence/acceptance assumptions. This function writes nothing.
    """
    parameters = (start_utc_nanos, end_utc_nanos, observed_at_utc_nanos,
                  diagnostic_max_unproven_nanos, max_partitions, max_report_hours)
    if any(type(value) is not int or value < 0 for value in parameters):
        raise ValueError("report_parameters_require_nonnegative_integers")
    hours = (end_utc_nanos - start_utc_nanos) // HOUR_NANOS
    if (start_utc_nanos % HOUR_NANOS or end_utc_nanos % HOUR_NANOS
            or hours < 1 or hours > max_report_hours):
        raise ValueError("report_requires_bounded_nonempty_utc_hour_range")
    if end_utc_nanos > (observed_at_utc_nanos // HOUR_NANOS + 1) * HOUR_NANOS:
        raise ValueError("report_range_includes_future_hour")
    if max_partitions < 1 or len(partitions) > max_partitions:
        raise ValueError("explicit_partition_budget_exceeded")
    if not 0 <= diagnostic_max_unproven_nanos < HOUR_NANOS:
        raise ValueError("diagnostic_bound_must_be_within_one_hour")
    selected = tuple((symbol, str(path.resolve())) for symbol, path in partitions)
    if any(symbol not in REQUIRED_SYMBOLS for symbol, _ in selected):
        raise ValueError("partition_symbol_outside_r1b_universe")
    if len({path for _, path in selected}) != len(selected):
        raise ValueError("duplicate_partition_selection")
    health = _health(host_observations, observed_at_utc_nanos)
    windows: dict[str, list[ProvenWindowV1]] = {symbol: [] for symbol in REQUIRED_SYMBOLS}
    verified = dict.fromkeys(REQUIRED_SYMBOLS, 0)
    refusals = []
    for symbol, directory in selected:
        contract = first_party_bybit_universe_contract_v1(symbol)
        path = Path(directory)
        try:
            partition_status = read_partition_status_v1(path, contract=contract)
            if partition_status.status != PARTITION_STATUS_COMPLETE:
                refusals.append(PartitionRefusalV1(symbol, directory, partition_status.reasons))
                continue
            proof = verify_partition_v1(path, contract=contract)
            if any(item.last_proven_utc_nanos > observed_at_utc_nanos for item in proof.coverage):
                refusals.append(PartitionRefusalV1(symbol, directory, ("EVIDENCE_AFTER_OBSERVATION",)))
                continue
            verified[symbol] += 1
            for interval in proof.coverage:
                start = max(start_utc_nanos, interval.start_utc_nanos)
                end = min(end_utc_nanos, interval.end_utc_nanos)
                if start < end:
                    windows[symbol].append(ProvenWindowV1(
                        proof.session_id,
                        replace(interval, start_utc_nanos=start, last_proven_utc_nanos=end - 1),
                    ))
        except (OSError, FirstPartyCaptureArchiveError) as error:
            refusals.append(PartitionRefusalV1(symbol, directory, (str(error),)))
    acceptance = derive_hourly_acceptance_v1(
        tuple(windows.items()), max_unproven_nanos=diagnostic_max_unproven_nanos,
        required_consecutive_hours=OR1_REQUIRED_HOURS,
    )
    status = acceptance_status_v1(acceptance, observed_at_utc_nanos=observed_at_utc_nanos)
    closed_end = min(end_utc_nanos, observed_at_utc_nanos // HOUR_NANOS * HOUR_NANOS)
    summaries = []
    for symbol in REQUIRED_SYMBOLS:
        runs = chain_proven_runs_v1(windows[symbol])
        segments = tuple(classify_hour_v1(
            runs, hour, max_unproven_nanos=diagnostic_max_unproven_nanos,
        ) for hour in range(start_utc_nanos, closed_end, HOUR_NANOS))
        complete = [item.hour_start_utc_nanos for item in segments if item.complete]
        coverage = tuple(measured_hour_coverage_v1(symbol, windows[symbol], hour)
                         for hour in range(start_utc_nanos, closed_end, HOUR_NANOS))
        summaries.append(SymbolReadinessV1(
            symbol, max(complete, default=None), segments,
            tuple(item.hour_start_utc_nanos for item in coverage
                  if item.uncovered_nanos == HOUR_NANOS),
            tuple(item for item in segments if not item.complete),
            tuple(item for item in coverage if item.holes),
            coverage_breaches_v1(coverage, max_uncovered_nanos=PAGING_THRESHOLD_NANOS),
            verified[symbol],
        ))
    recorder = next(item for item in health if item.component == "recorder")
    current = status.current_run_hours
    reasons = ["FORMAL_ACCEPTANCE_BOUND_S_UNRESOLVED"]
    if recorder.state is not HostStateV1.OK:
        current = 0
        reasons.append("CURRENT_RECORDER_NOT_INDEPENDENTLY_PROVEN_RUNNING")
    if not acceptance.met:
        reasons.append("DIAGNOSTIC_JOINT_24H_NOT_PROVEN_IN_SELECTED_SCOPE")
    if status.state.value != "CURRENT":
        reasons.append(f"CAPTURE_{status.state.value}")
    if refusals:
        reasons.append("SELECTED_PARTITION_EVIDENCE_REFUSED")
    reasons.extend(f"HOST_{item.component.upper()}_{item.state.value}"
                   for item in health if item.state is not HostStateV1.OK)
    actions = [
        "OR-1 owner: approve formal hand-off bound S explicitly; 60-second paging is not S.",
        ("OR-1 owner: review verified BTC/ETH/SOL 24 consecutive COMPLETE hours under approved S; "
         "accept recovery explicitly before R0 retirement or cutover."),
    ]
    if any(item.incomplete_hours for item in summaries) or refusals:
        actions.append("Capture owner: inspect listed refused partitions, missing hours and session end "
                       "proofs; preserve evidence; authorize recovery separately and collect new sealed "
                       "hours without bridging historical gaps.")
    prerequisites = {
        "recorder": "identify the R1B process/service, its session and last sealed partition; "
                    "inspect exit/connection-loss evidence and request restart authorization",
        "disk": "measure free bytes and archive growth on the actual capture volume; "
                "compare the configured stop floor and resolve capacity before recovery",
        "memory": "measure available memory and recorder working set on the capture host; "
                  "review configured resource limits and OOM evidence",
        "clock": "read the recorder's offset estimate, uncertainty bound and sample timestamp; "
                 "review clock-step events against the capture clock contract",
        "deadman": "inspect the existing scheduler/service registration, last run result and "
                   "fresh independent dead-man observation; obtain authorization for changes",
        "oci": "obtain authorized remote host/service and backup status; approve deployment "
               "or cutover separately if still required",
    }
    for item in health:
        if item.state is not HostStateV1.OK:
            actions.append(f"Host owner: obtain fresh independent {item.component} evidence with source, "
                           "observation time and explicit freshness policy; "
                           f"{prerequisites[item.component]}.")
    longest = acceptance.longest_run
    return CaptureReadinessReportV1(
        start_utc_nanos, end_utc_nanos, observed_at_utc_nanos, selected, tuple(summaries),
        status, longest.hours if longest else 0, current, health,
        tuple(refusals), tuple(reasons), tuple(actions),
    )
