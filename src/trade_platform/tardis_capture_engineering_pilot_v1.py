"""Phase 3D.9S.2A -- the zero-cost Tardis capture engineering verdict.

``RESEARCH_ENGINEERING_EVIDENCE_ONLY``. Composes the three narrow pilot modules
-- :mod:`trade_platform.tardis_capture_evidence_v1`,
:mod:`trade_platform.bybit_ticker_state_reconstruction_v1` and
:mod:`trade_platform.bybit_trade_bar_reconstruction_v1` -- into exactly one
deterministic, content-hashed engineering verdict:
``TARDIS_CAPTURE_ENGINEERING_PROVEN`` or
``TARDIS_CAPTURE_ENGINEERING_FAILED``.

**The verdict is about source mechanics, never about a strategy.** It answers
one question: can a historically captured Bybit WebSocket feed carry distinct,
measured, causally sound decision times for the existing
``crypto_basis_mean_reversion`` path? It computes no P&L, no return, no Sharpe,
no hit rate, no threshold and no parameter ranking, and it has no code path that
could. The timing proof is structural only: that many distinct availability
instants exist, that no availability precedes its own evidence, and that a
strictly-later entry bar remains selectable.

**A passing verdict authorizes nothing.** It does not make Tardis a canonical
research source, does not widen
:mod:`trade_platform.real_market_data_provenance_v1`, does not mark anything
``REAL_DATA_RESEARCH_EVIDENCE``, and does not touch the sealed 150-day Bybit
REST dataset or any existing ``FeatureMaterializationV2``. Introducing a second
authorized source contract is Phase 3D.9S.2B's decision and the owner's, not
this module's.

**Holdout discipline is enforced, not assumed.** Every capture interval must end
strictly before :data:`UNTOUCHED_HOLDOUT_BOUNDARY_V1`. A pilot that reaches into
the untouched holdout fails closed here rather than being reviewed afterwards.

**Determinism is proven by replay, not asserted.** The caller supplies two
independently produced reconstructions of the same capture; the verdict requires
them to be bit-for-bit equal by content hash.

**Every failure is a named reason.** There is no partial pass and no
"acceptable" default: any unmet check yields ``FAILED`` with the reasons
attached.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .bybit_ticker_state_reconstruction_v1 import (
    BASIS_FORMULA_SEMANTIC_VERSION_V1,
    BybitTickerReconstructionV1,
)
from .bybit_trade_bar_reconstruction_v1 import (
    BAR_BUILDER_SEMANTIC_VERSION_V1,
    ReconstructedTradeBarV1,
    bar_open_nanos,
    first_strictly_later_bar,
)
from .tardis_capture_evidence_v1 import (
    CAPTURE_LIFECYCLE_V1,
    TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
    TardisCaptureCoverageV1,
    TardisCaptureEvidenceError,
    canonical_hash,
    nanos_to_datetime,
)

PILOT_SEMANTIC_VERSION_V1: Final = "tardis-capture-engineering-pilot-1.0.0"

#: Pre-registered untouched holdout boundary. Pilot evidence must end strictly
#: before this instant; the holdout is never opened, evaluated or summarized.
UNTOUCHED_HOLDOUT_BOUNDARY_V1: Final = datetime(2026, 8, 20, tzinfo=UTC)

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.tardis_capture_engineering_pilot_v1")


class TardisCaptureEngineeringPilotError(ValueError):
    """Raised when the pilot cannot even be evaluated (missing or incoherent inputs)."""


class TardisCaptureEngineeringVerdictV1(StrEnum):
    PROVEN = "TARDIS_CAPTURE_ENGINEERING_PROVEN"
    FAILED = "TARDIS_CAPTURE_ENGINEERING_FAILED"


@dataclass(frozen=True, slots=True)
class TardisCaptureEngineeringPilotReportV1:
    """One deterministic engineering verdict with its descriptive evidence.

    Every count here is a capture statistic or a structural timing fact. None is
    a performance claim, and the two explicit ``False`` authority flags exist so
    no reader can mistake a passing verdict for a source authorization.
    """

    verdict: TardisCaptureEngineeringVerdictV1
    reasons: tuple[str, ...]
    lifecycle: str
    is_real_data_research_evidence: bool
    canonical_source_authorized: bool
    symbol: str
    ticker_record_count: int
    ticker_snapshot_count: int
    ticker_delta_count: int
    mark_update_count: int
    index_update_count: int
    both_component_message_count: int
    state_reset_count: int
    basis_observation_count: int
    distinct_availability_instant_count: int
    minimum_recorder_lag_millis: int | None
    maximum_recorder_lag_millis: int | None
    trade_count: int
    bar_count: int
    ambiguous_open_bar_count: int
    ambiguous_close_bar_count: int
    ticker_gap_count: int
    trade_gap_count: int
    strict_entry_feasible_count: int
    earliest_evidence_at: datetime
    latest_evidence_at: datetime
    pilot_semantic_version: str
    content_hash: str

    @property
    def is_proven(self) -> bool:
        return self.verdict is TardisCaptureEngineeringVerdictV1.PROVEN

    @property
    def report_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"tardis-capture-engineering-pilot-v1:{self.content_hash}")


def _lag_millis(exchange_at: datetime | None, arrival_nanos: int) -> int | None:
    """Descriptive exchange->recorder lag. Never used as an economic input."""
    if exchange_at is None:
        return None
    exchange_nanos = int(exchange_at.timestamp() * 1000) * 1_000_000
    return (arrival_nanos - exchange_nanos) // 1_000_000


def evaluate_tardis_capture_engineering_pilot(
    *,
    symbol: str,
    ticker_reconstruction: BybitTickerReconstructionV1,
    ticker_reconstruction_replay: BybitTickerReconstructionV1,
    bars: Sequence[ReconstructedTradeBarV1],
    bars_replay: Sequence[ReconstructedTradeBarV1],
    ticker_coverage: TardisCaptureCoverageV1,
    trade_coverage: TardisCaptureCoverageV1,
    trade_count: int,
    minimum_distinct_availability_instants: int,
) -> TardisCaptureEngineeringPilotReportV1:
    """Evaluate one pilot run into exactly one verdict. No performance is computed."""
    if not symbol.strip():
        raise TardisCaptureEngineeringPilotError("tardis_pilot_symbol_required")
    if minimum_distinct_availability_instants < 1:
        raise TardisCaptureEngineeringPilotError("tardis_pilot_minimum_availability_instants_invalid")
    if not ticker_coverage.intervals or not trade_coverage.intervals:
        raise TardisCaptureEngineeringPilotError("tardis_pilot_requires_capture_coverage")

    reasons: list[str] = []
    observations = ticker_reconstruction.observations

    # --- Holdout discipline -------------------------------------------------
    interval_bounds = [
        (interval.start_nanos, interval.end_nanos)
        for coverage in (ticker_coverage, trade_coverage)
        for interval in coverage.intervals
    ]
    earliest_evidence_at = nanos_to_datetime(min(start for start, _ in interval_bounds))
    latest_evidence_at = nanos_to_datetime(max(end for _, end in interval_bounds))
    if latest_evidence_at >= UNTOUCHED_HOLDOUT_BOUNDARY_V1:
        reasons.append("pilot_evidence_reaches_untouched_holdout")

    # --- Determinism --------------------------------------------------------
    if ticker_reconstruction.content_hash != ticker_reconstruction_replay.content_hash:
        reasons.append("ticker_reconstruction_replay_not_deterministic")
    if [b.content_hash for b in bars] != [b.content_hash for b in bars_replay]:
        reasons.append("trade_bar_replay_not_deterministic")

    # --- Ticker state mechanics --------------------------------------------
    if ticker_reconstruction.snapshot_count < 1:
        reasons.append("no_snapshot_established_authoritative_ticker_state")
    if ticker_reconstruction.mark_update_count < 1:
        reasons.append("no_mark_price_updates_captured")
    if ticker_reconstruction.index_update_count < 1:
        reasons.append("no_index_price_updates_captured")
    if ticker_reconstruction.both_component_message_count >= min(
        ticker_reconstruction.mark_update_count, ticker_reconstruction.index_update_count
    ):
        # If every mark (or every index) update also carried the other side, the
        # asynchronous pairing rule would be untested by this evidence.
        reasons.append("mark_and_index_never_observed_independently")
    if not observations:
        reasons.append("no_basis_observation_reconstructed")

    # --- Causality ----------------------------------------------------------
    for observation in observations:
        expected = max(observation.mark_local_timestamp_nanos, observation.index_local_timestamp_nanos)
        if observation.research_available_at_nanos != expected:
            reasons.append("basis_availability_is_not_max_of_component_arrivals")
            break
    for observation in observations:
        if observation.research_available_at_nanos < observation.mark_local_timestamp_nanos or (
            observation.research_available_at_nanos < observation.index_local_timestamp_nanos
        ):
            reasons.append("basis_availability_precedes_a_component_arrival")
            break

    distinct_instants = ticker_reconstruction.distinct_availability_instants
    if len(distinct_instants) < minimum_distinct_availability_instants:
        reasons.append("too_few_distinct_basis_availability_instants")

    # --- Bars ---------------------------------------------------------------
    if not bars:
        reasons.append("no_trade_bar_reconstructed")
    for bar in bars:
        if bar.trade_count < 1:
            reasons.append("trade_bar_without_contributing_trades")
            break
    for bar in bars:
        if bar.research_available_at_nanos < bar_open_nanos(bar):
            reasons.append("trade_bar_available_before_its_own_open_boundary")
            break
    for bar in bars:
        if bar.quote_turnover is not None and bar.quote_turnover_unit is None:
            reasons.append("quote_turnover_carries_no_unit")
            break

    # --- Collection gaps are represented and never bridged ------------------
    for coverage in (ticker_coverage, trade_coverage):
        for gap in coverage.gaps:
            midpoint = (gap.start_nanos + gap.end_nanos) // 2
            if coverage.covers_nanos(midpoint):
                reasons.append("collection_gap_silently_bridged")
                break
            try:
                coverage.require_covered(midpoint)
            except TardisCaptureEvidenceError:
                continue
            reasons.append("collection_gap_did_not_fail_closed")
            break

    # --- Strict research execution timing (structural only) -----------------
    strict_entry_feasible_count = 0
    for observation in observations:
        entry_bar = first_strictly_later_bar(
            bars, research_available_at_nanos=observation.research_available_at_nanos
        )
        if entry_bar is None:
            continue
        if bar_open_nanos(entry_bar) <= observation.research_available_at_nanos:
            reasons.append("entry_bar_not_strictly_later_than_research_availability")
            break
        strict_entry_feasible_count += 1
    if strict_entry_feasible_count < 1 and not reasons:
        reasons.append("no_strictly_later_entry_bar_available_for_any_observation")

    lags = [
        lag
        for observation in observations
        for lag in (
            _lag_millis(observation.mark_exchange_timestamp, observation.mark_local_timestamp_nanos),
            _lag_millis(observation.index_exchange_timestamp, observation.index_local_timestamp_nanos),
        )
        if lag is not None
    ]

    ordered_reasons = tuple(dict.fromkeys(reasons))
    verdict = (
        TardisCaptureEngineeringVerdictV1.PROVEN
        if not ordered_reasons
        else TardisCaptureEngineeringVerdictV1.FAILED
    )
    content_hash = canonical_hash(
        {
            "lifecycle": CAPTURE_LIFECYCLE_V1,
            "pilot_semantic_version": PILOT_SEMANTIC_VERSION_V1,
            "parser_semantic_version": TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
            "basis_formula_semantic_version": BASIS_FORMULA_SEMANTIC_VERSION_V1,
            "bar_builder_semantic_version": BAR_BUILDER_SEMANTIC_VERSION_V1,
            "verdict": verdict.value,
            "reasons": list(ordered_reasons),
            "symbol": symbol,
            "ticker_reconstruction_content_hash": ticker_reconstruction.content_hash,
            "ticker_coverage_content_hash": ticker_coverage.content_hash,
            "trade_coverage_content_hash": trade_coverage.content_hash,
            "bar_content_hashes": [b.content_hash for b in bars],
            "distinct_availability_instant_count": len(distinct_instants),
            "strict_entry_feasible_count": strict_entry_feasible_count,
            "is_real_data_research_evidence": False,
            "canonical_source_authorized": False,
        }
    )
    return TardisCaptureEngineeringPilotReportV1(
        verdict=verdict,
        reasons=ordered_reasons,
        lifecycle=CAPTURE_LIFECYCLE_V1,
        is_real_data_research_evidence=False,
        canonical_source_authorized=False,
        symbol=symbol,
        ticker_record_count=ticker_reconstruction.record_count,
        ticker_snapshot_count=ticker_reconstruction.snapshot_count,
        ticker_delta_count=ticker_reconstruction.delta_count,
        mark_update_count=ticker_reconstruction.mark_update_count,
        index_update_count=ticker_reconstruction.index_update_count,
        both_component_message_count=ticker_reconstruction.both_component_message_count,
        state_reset_count=ticker_reconstruction.state_reset_count,
        basis_observation_count=len(observations),
        distinct_availability_instant_count=len(distinct_instants),
        minimum_recorder_lag_millis=min(lags) if lags else None,
        maximum_recorder_lag_millis=max(lags) if lags else None,
        trade_count=trade_count,
        bar_count=len(bars),
        ambiguous_open_bar_count=sum(1 for b in bars if b.open_is_sequence_ambiguous),
        ambiguous_close_bar_count=sum(1 for b in bars if b.close_is_sequence_ambiguous),
        ticker_gap_count=len(ticker_coverage.gaps),
        trade_gap_count=len(trade_coverage.gaps),
        strict_entry_feasible_count=strict_entry_feasible_count,
        earliest_evidence_at=earliest_evidence_at,
        latest_evidence_at=latest_evidence_at,
        pilot_semantic_version=PILOT_SEMANTIC_VERSION_V1,
        content_hash=content_hash,
    )
