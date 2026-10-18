"""Paper evidence production -- stored forward records to verified parity and funding evidence, or a refusal.

``RESEARCH_ONLY`` / paper only. This module starts nothing: no runner, no
capture, no acquisition, no network call, no order, no account. It reads what
the forward path already persisted, re-derives every record, rebuilds the two
in-memory evidence objects with the *unchanged* resolvers, and hands only those
issued objects to a sink (the paper evidence store's ``record_*`` API). Where an
input the resolvers need is not persisted, it refuses with an explicit reason
code and never substitutes synthetic, re-captured or sealed-for-live data.

Fill parity (:func:`~trade_platform.paper_fill_parity_v1.assess_fill_parity_v1`)
------------------------------------------------------------------------------
Per INCUBATING candidate, from

* stored live signals (``live_strategy_signals``): each row's identity must
  re-derive its ``content_hash`` and ``signal_id`` and agree with every column;
  ``decided_at`` is the stored decision instant;
* stored paper fills (``paper_incubation_fills``): the same, plus the fill's
  signal, decision instant and cost policy must be the stored signal's and the
  run's;
* stored runner progress (``runner_liveness_progress``): the sessions that
  watched the candidate under exactly its evidence; a session that does not
  re-derive refuses the candidate;
* the live bars the runner was fed -- **not persisted today** (see
  :data:`MISSING_PERSISTED_INPUTS_V1`). Progress records list only each bar's
  stable identity (capture session, feed segment, closing record and trade
  manifest hashes, completing arrival), never its prices, trade references,
  ambiguity flags or provisional knowledge times, and warm-up bars only as a
  count. So the production source refuses ``LIVE_BARS_NOT_PERSISTED``. A live
  bar is never inferred from a sealed one. Should a source supply live bars,
  each must match exactly one progress listing of a bound session, and feed
  segments are renumbered per (runner session, feed segment) so a restart
  never continues a rolling window;
* sealed T4 datasets, rebuilt from raw capture by the source
  (:func:`sealed_dataset_from_seal_v1`): one replay segment per dataset, since
  nothing crosses a sealed segment boundary. Their knowledge times are the
  finalized bracketed ones; the live side keeps its ``LIVE_PROVISIONAL`` ones.

Point in time: only rows recorded at or before ``as_of`` (and signals decided,
fills' bars opened, live bars completed and seals sealed by then) are read.

Funding evidence (:func:`~trade_platform.paper_funding_evidence_v1.candidate_funding_evidence_v1`)
---------------------------------------------------------------------------------------------
Only already-stored forward settled funding (the append-only
:class:`~trade_platform.bybit_forward_settled_funding_v1.ForwardSettledFundingStoreV1`)
is bridged, through the unchanged
:func:`~trade_platform.forward_funding_price_bridge_v1.bridge_forward_funding_prices_v1`.
Forward acquisition is not authorized, so an empty store is
``FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED`` -- nothing is fetched. The
price convention, the bridge window, the mark tolerance and the capture
acceptance bound are caller declarations with no default
(:class:`FundingDeclarationV1`); undeclared is ``FUNDING_PRICE_CONVENTION_NOT_DECLARED``.

The stored record is the candidate's ``funding_evidence`` section, built by the
unchanged :func:`~trade_platform.paper_incubation_report_v1.incubation_report_v1`
over the cycle's stored paper fills recorded and decided by ``as_of`` -- loaded
exactly as the store and the terminal read model load them -- with the bridges
attached. A candidate the report does not list (no cycle fill as of ``as_of``)
binds no holding: its section is the funding module's own over no holdings,
which is what the store re-derives for it.

Persistence
-----------
A sink (:class:`StorePaperEvidenceSinkV1`: the verified
:mod:`~trade_platform.paper_evidence_store_v1`, bound to the caller's cursor and
transaction) receives only the issued report / evidence object and the section;
its refusals are reported verbatim (``SINK_REFUSED:<code>``), and a holdout
refusal also names the unopened holdout (``HOLDOUT_UNOPENED_OR_7``) as the missing
input. A stored row whose ``recorded_at`` is not this run's is the idempotent
``ALREADY_STORED``. With no sink the run is a dry run (``DRY_RUN_WOULD_RECORD``).
The run result lists, per candidate and evidence kind, the outcome, its hashes,
its reasons and the missing inputs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol
from uuid import UUID, uuid5

from .bybit_forward_settled_funding_v1 import (
    ForwardFundingError,
    ForwardSettledFundingStoreV1,
    PublishedFundingIntervalV1,
    resolve_forward_funding_acquisition_authorization_v1,
)
from .first_party_t4_forward_bars_v1 import T4ForwardDatasetBarsV1, T4PriceConventionV1
from .first_party_t4_normalization_v1 import T4MinuteBarV1
from .live_signals_v1 import _NAMESPACE as _SIGNAL_NAMESPACE
from .live_signals_v1 import (
    AUTHORITY_INCUBATING,
    LiveBarV1,
    LiveHoldoutGateV1,
    LiveSignalV1,
    WatchedCandidateV1,
)
from .paper_fill_parity_v1 import _NAMESPACE as _PARITY_NAMESPACE
from .paper_fill_parity_v1 import (
    FillParityReportV1,
    PaperFillParityError,
    SealedReplayBarV1,
    assess_fill_parity_v1,
)
from .paper_funding_evidence_v1 import (
    CandidateFundingEvidenceV1,
    PaperFundingEvidenceError,
    candidate_funding_evidence_v1,
)
from .paper_incubation_v1 import _NAMESPACE as _FILL_NAMESPACE
from .paper_incubation_v1 import PaperFillV1
from .runner_liveness_v1 import (
    KIND_BARS,
    WATCHED_AS_KEYS,
    _Refused,
    _relevant,
    _session_records,
    candidate_payload_v1,
)
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

PRODUCTION_RUN_SCHEMA_VERSION_V1: Final = "paper-evidence-production-run-v1"

KIND_FILL_PARITY: Final = "FILL_PARITY"
KIND_FUNDING_EVIDENCE: Final = "FUNDING_EVIDENCE"

STATUS_PERSISTED: Final = "PERSISTED"
STATUS_ALREADY_STORED: Final = "ALREADY_STORED"
STATUS_REFUSED: Final = "REFUSED"
STATUS_DRY_RUN: Final = "DRY_RUN_WOULD_RECORD"

# -- Refusal reason codes ---------------------------------------------------------------------
# Missing inputs (nothing persisted, or nothing persisted for this candidate as of the run).
LIVE_BARS_NOT_PERSISTED: Final = "LIVE_BARS_NOT_PERSISTED"
NO_LIVE_PAPER_SIGNALS_IN_CYCLE: Final = "NO_LIVE_PAPER_SIGNALS_IN_CYCLE"
RUNNER_LIVENESS_NOT_RECORDED: Final = "RUNNER_LIVENESS_NOT_RECORDED"
SEAL_NOT_FOUND: Final = "SEAL_NOT_FOUND"
FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED: Final = "FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED"
FUNDING_PRICE_CONVENTION_NOT_DECLARED: Final = "FUNDING_PRICE_CONVENTION_NOT_DECLARED"
FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE: Final = "FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE"
# Forged or tampered (a stored record that does not re-derive).
STORED_SIGNAL_DOES_NOT_REDERIVE: Final = "STORED_SIGNAL_DOES_NOT_REDERIVE"
STORED_FILL_DOES_NOT_REDERIVE: Final = "STORED_FILL_DOES_NOT_REDERIVE"
RUNNER_LIVENESS_DOES_NOT_REDERIVE: Final = "RUNNER_LIVENESS_DOES_NOT_REDERIVE"
SEAL_DOES_NOT_REDERIVE: Final = "SEAL_DOES_NOT_REDERIVE"
FORWARD_FUNDING_STORE_DOES_NOT_REDERIVE: Final = "FORWARD_FUNDING_STORE_DOES_NOT_REDERIVE"
# Mismatched (well-formed evidence of something else).
CANDIDATE_NOT_INCUBATING: Final = "CANDIDATE_NOT_INCUBATING"
HOLDOUT_GATE_OF_ANOTHER_CYCLE: Final = "HOLDOUT_GATE_OF_ANOTHER_CYCLE"
MISMATCHED_CANDIDATE_EVIDENCE: Final = "MISMATCHED_CANDIDATE_EVIDENCE"
MISMATCHED_COST_POLICY: Final = "MISMATCHED_COST_POLICY"
MISMATCHED_FILL_SIGNAL: Final = "MISMATCHED_FILL_SIGNAL"
MISMATCHED_SEAL_SYMBOL: Final = "MISMATCHED_SEAL_SYMBOL"
LIVE_BAR_NOT_RECORDED_BY_RUNNER: Final = "LIVE_BAR_NOT_RECORDED_BY_RUNNER"
# Stale (recorded under a holdout gate that is no longer the cycle's gate).
STALE_HOLDOUT_GATE: Final = "STALE_HOLDOUT_GATE"
# Ambiguous (two different records where one is required).
AMBIGUOUS_LIVE_BARS: Final = "AMBIGUOUS_LIVE_BARS"
AMBIGUOUS_SEALED_BARS: Final = "AMBIGUOUS_SEALED_BARS"
AMBIGUOUS_RUNNER_PROGRESS: Final = "AMBIGUOUS_RUNNER_PROGRESS"
# Downstream refusals, kept verbatim after the prefix.
PARITY_INPUT_REFUSED: Final = "PARITY_INPUT_REFUSED"
FUNDING_EVIDENCE_REFUSED: Final = "FUNDING_EVIDENCE_REFUSED"
ISSUED_EVIDENCE_DOES_NOT_REDERIVE: Final = "ISSUED_EVIDENCE_DOES_NOT_REDERIVE"
CYCLE_PAPER_FILLS_DO_NOT_REDERIVE: Final = "CYCLE_PAPER_FILLS_DO_NOT_REDERIVE"
SINK_REFUSED: Final = "SINK_REFUSED"
#: Named beside a store holdout refusal: the owner has not opened the cycle's holdout (OR-7).
HOLDOUT_UNOPENED_OR_7: Final = "HOLDOUT_UNOPENED_OR_7"
_STORE_HOLDOUT_REFUSALS: Final = frozenset({"EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT",
                                            "HOLDOUT_GATE_OPENING_NOT_REGISTERED"})

#: What the resolvers need and the platform does not persist today (as of migration 0065).
MISSING_PERSISTED_INPUTS_V1: Final = {
    LIVE_BARS_NOT_PERSISTED: (
        "The completed live bars the R8/R10 runner was fed (LiveBarV1: OHLCV, volumes, trade counts, "
        "first/last trade references, sequence-ambiguity flags, LIVE_PROVISIONAL open/complete knowledge "
        "times, clock bound). runner_liveness_progress keeps only each processed bar's capture session, "
        "feed segment, closing record hash, trade manifest hash, completing arrival and gate admission, "
        "and warm-up bars only as a count; live_strategy_signals and paper_incubation_fills keep only "
        "the signal bar's and fill bar's hashes and the fill price. Persisting them needs a schema change."),
    FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED: (
        "Forward settled funding observations: acquisition is unauthorized "
        "(UNRESOLVED_OWNER_AUTHORIZATION), so the append-only forward funding store holds none."),
    "PUBLISHED_FUNDING_INTERVAL_NOT_PERSISTED": (
        "The venue-published funding interval (PublishedFundingIntervalV1) has no store; without it a "
        "forward funding window is IRREGULAR (PUBLISHED_INTERVAL_EVIDENCE_MISSING), never COMPLETE."),
    "WATCHED_CANDIDATE_STUDY_SPEC_NOT_PERSISTED": (
        "The frozen StudySpecV1 behind a WatchedCandidateV1 is rebuilt by the caller (as the runner "
        "script does); it is verified against the stored signals' study id and content hash, never trusted."),
    HOLDOUT_UNOPENED_OR_7: (
        "The cycle's holdout is not opened by the owner (OR-7): the store refuses any evidence over a bar at or "
        "after the holdout start, so forward evidence of this cycle cannot be stored yet."),
}

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE: Final = 60_000_000


class PaperEvidenceProductionError(ValueError):
    """A caller error (malformed request). Evidence problems are refusals in the run result, not raises."""


class EvidenceInputRefusedV1(ValueError):
    """Raised by a source for an input it cannot honestly supply. ``code`` is a stable reason code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}:{detail}")
        self.code = code
        self.detail = detail


def _micros(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PaperEvidenceProductionError("instant_must_be_timezone_aware")
    return (value - _EPOCH) // timedelta(microseconds=1)


def _at(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def _json(value: Any) -> Any:
    return value if isinstance(value, dict | list) else json.loads(value)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SealedDatasetBarsV1:
    """Every minute bar of one sealed T4 dataset that re-derived from raw capture (see the source)."""

    dataset_version_id: UUID
    content_hash: str
    session_id: UUID
    exchange_symbol: str
    sealed_at: datetime
    bars: tuple[T4MinuteBarV1, ...]


@dataclass(frozen=True, slots=True)
class FundingDeclarationV1:
    """The caller's explicit funding-bridge declarations. None has a default.

    ``convention`` (which proven trade price a settlement instant refers to) is an
    economic choice the bridge records as unapproved; ``mark_nearby_tolerance_ms``
    is ``None`` (no tolerance declared) or a restriction; ``capture_max_unproven_nanos``
    is the engineering bound of the hourly capture acceptance the price evidence is
    judged under; ``published_interval`` is venue evidence or ``None`` (missing).
    """

    convention: T4PriceConventionV1
    start_ms: int
    end_ms: int
    mark_nearby_tolerance_ms: int | None
    capture_max_unproven_nanos: int
    published_interval: PublishedFundingIntervalV1 | None

    def __post_init__(self) -> None:
        if not isinstance(self.convention, T4PriceConventionV1):
            raise PaperEvidenceProductionError("funding_price_convention_must_be_declared")
        for value in (self.start_ms, self.end_ms, self.capture_max_unproven_nanos):
            if isinstance(value, bool) or not isinstance(value, int):
                raise PaperEvidenceProductionError("funding_declaration_values_must_be_integers")
        if self.end_ms <= self.start_ms:
            raise PaperEvidenceProductionError("funding_window_end_not_after_start")
        if self.published_interval is not None and not isinstance(self.published_interval,
                                                                   PublishedFundingIntervalV1):
            raise PaperEvidenceProductionError("published_interval_must_be_venue_interval_evidence")


@dataclass(frozen=True, slots=True)
class FundingPriceEvidenceV1:
    """The sealed price evidence a funding bridge binds to: datasets, capture acceptance, mark evidence."""

    datasets: tuple[T4ForwardDatasetBarsV1, ...]
    acceptance: Any  # HourlyAcceptanceV1
    mark_evidence: tuple[Any, ...]  # T4MarkEvidenceDatasetV1


class PaperEvidenceSourcesV1(Protocol):
    """Read-only access to persisted forward evidence. Implementations never fetch, capture or synthesize.

    Row methods return every stored row for the candidate (the producer applies the
    point-in-time filter and re-derives each row). ``live_bars`` returns ``None``
    when the runner's live bars are not persisted. Sealed and funding methods raise
    :class:`EvidenceInputRefusedV1` for an input they cannot supply honestly.
    """

    def signal_rows(self, *, study_id: str, trial_id: str, symbol: str) -> Sequence[Mapping[str, Any]]: ...

    def fill_rows(self, *, study_id: str, trial_id: str, symbol: str) -> Sequence[Mapping[str, Any]]: ...

    def liveness_rows(self, *, study_id: str, trial_id: str, symbol: str) -> Sequence[Mapping[str, Any]]: ...

    def live_bars(self, *, study_id: str, trial_id: str, symbol: str) -> Sequence[LiveBarV1] | None: ...

    def sealed_datasets(self, *, symbol: str, start_micros: int, end_micros: int,
                        as_of: datetime) -> Sequence[SealedDatasetBarsV1]: ...

    def forward_funding_store(self, symbol: str) -> ForwardSettledFundingStoreV1 | None: ...

    def funding_price_evidence(self, *, symbol: str, declaration: FundingDeclarationV1,
                               as_of: datetime) -> FundingPriceEvidenceV1: ...

    def cycle_incubation_inputs(self, *, cycle_id: str,
                                as_of: datetime) -> tuple[Sequence[Mapping[str, Any]], CostPolicyV1, Any]:
        """(the cycle's stored fill identities recorded and decided by ``as_of``, its OR-6 policy, its
        registry resolution or ``None``) -- what the store re-derives funding holdings from."""
        ...


class PaperEvidenceSinkV1(Protocol):
    """The verified paper evidence store's write API (``paper_evidence_store_v1``), bound to one cursor."""

    def record_fill_parity_v1(self, report: FillParityReportV1, *, cycle_id: str, as_of: datetime,
                              recorded_at: datetime) -> Sequence[Any]: ...

    def record_funding_evidence_v1(self, evidence: CandidateFundingEvidenceV1 | None, *,
                                   section: Mapping[str, Any], cycle_id: str, as_of: datetime,
                                   recorded_at: datetime) -> Any: ...


class StorePaperEvidenceSinkV1:
    """:class:`PaperEvidenceSinkV1` over :mod:`~trade_platform.paper_evidence_store_v1`.

    Holds the caller's cursor: every write runs in the caller's transaction, which
    the caller commits (or not). Refusals are the store's
    :class:`~trade_platform.paper_evidence_store_v1.PaperEvidenceStoreError`.
    """

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def record_fill_parity_v1(self, report: FillParityReportV1, *, cycle_id: str, as_of: datetime,
                              recorded_at: datetime) -> Sequence[Any]:
        from .paper_evidence_store_v1 import record_fill_parity_v1

        return record_fill_parity_v1(self._cursor, report, cycle_id=cycle_id, as_of=as_of, recorded_at=recorded_at)

    def record_funding_evidence_v1(self, evidence: CandidateFundingEvidenceV1 | None, *,
                                   section: Mapping[str, Any], cycle_id: str, as_of: datetime,
                                   recorded_at: datetime) -> Any:
        from .paper_evidence_store_v1 import record_funding_evidence_v1

        return record_funding_evidence_v1(self._cursor, evidence, section=section, cycle_id=cycle_id, as_of=as_of,
                                          recorded_at=recorded_at)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductionOutcomeV1:
    kind: str
    study_id: str
    trial_id: str
    symbol: str
    status: str
    reasons: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    hashes: Mapping[str, Any] = field(default_factory=dict)
    evidence_status: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "study_id": self.study_id, "trial_id": self.trial_id, "symbol": self.symbol,
                "status": self.status, "reasons": list(self.reasons), "missing_inputs": list(self.missing_inputs),
                "hashes": dict(self.hashes), "evidence_status": self.evidence_status}


@dataclass(frozen=True, slots=True)
class ProductionRunV1:
    cycle_id: str
    as_of: datetime
    recorded_at: datetime
    dry_run: bool
    outcomes: tuple[ProductionOutcomeV1, ...]

    @property
    def missing_inputs(self) -> dict[str, str]:
        """Every missing input any outcome named, with what it is."""
        named = sorted({code for outcome in self.outcomes for code in outcome.missing_inputs})
        return {code: MISSING_PERSISTED_INPUTS_V1.get(code, "not stored for this candidate as of the run")
                for code in named}

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": PRODUCTION_RUN_SCHEMA_VERSION_V1,
            "claim": "EVIDENCE_PRODUCTION_REPORT_NOT_A_DECISION",
            "cycle_id": self.cycle_id,
            "as_of": self.as_of.astimezone(UTC).isoformat(),
            "recorded_at": self.recorded_at.astimezone(UTC).isoformat(),
            "dry_run": self.dry_run,
            "outcomes": [item.payload() for item in self.outcomes],
            "missing_inputs": self.missing_inputs,
        }


class _Refusal(Exception):
    def __init__(self, reasons: Sequence[str], missing: Sequence[str] = ()) -> None:
        super().__init__(",".join(reasons))
        self.reasons = tuple(sorted(set(reasons)))
        self.missing = tuple(sorted(set(missing)))


# ---------------------------------------------------------------------------
# Stored-record intake (each row re-derived; columns must agree with the identity)
# ---------------------------------------------------------------------------


def _same_instant(column: object, micros: int) -> bool:
    return isinstance(column, datetime) and column.tzinfo is not None and _micros(column) == micros


def _signal_from_row(row: Mapping[str, Any]) -> LiveSignalV1:
    try:
        identity = _json(row["identity"])
        content_hash = identity_hash_v1(dict(identity))
        signal_id = UUID(str(row["signal_id"]))
        evidence = identity["evidence"]
        candidate = identity["candidate"]
        decided_at = row["decided_at"]
        agrees = (
            str(row["content_hash"]).strip() == content_hash
            and signal_id == uuid5(_SIGNAL_NAMESPACE, f"live-signal:{content_hash}")
            and str(row["study_id"]) == str(candidate["study_id"])
            and str(row["trial_id"]) == str(candidate["trial_id"])
            and row["symbol"] == identity["symbol"] and row["authority"] == identity["authority"]
            and int(row["target_from"]) == int(identity["target_from"])
            and int(row["target_to"]) == int(identity["target_to"])
            and _same_instant(row["bar_open_at"], int(evidence["bar_open_micros"]))
            and isinstance(decided_at, datetime) and decided_at.tzinfo is not None
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _Refusal([STORED_SIGNAL_DOES_NOT_REDERIVE]) from error
    if not agrees:
        raise _Refusal([STORED_SIGNAL_DOES_NOT_REDERIVE])
    return LiveSignalV1(identity, signal_id, decided_at.astimezone(UTC))


def _fill_from_row(row: Mapping[str, Any]) -> PaperFillV1:
    try:
        identity = _json(row["identity"])
        content_hash = identity_hash_v1(dict(identity))
        fill_id = UUID(str(row["fill_id"]))
        price = identity["fill_price"]
        stored_price = row["fill_price"]
        agrees = (
            str(row["content_hash"]).strip() == content_hash
            and fill_id == uuid5(_FILL_NAMESPACE, f"paper-fill:{content_hash}")
            and str(row["signal_id"]) == str(identity["signal_id"])
            and str(row["signal_content_hash"]).strip() == identity["signal_content_hash"]
            and str(row["study_id"]) == str(identity["study_id"])
            and str(row["trial_id"]) == str(identity["trial_id"])
            and row["symbol"] == identity["symbol"] and row["status"] == identity["status"]
            and int(row["target_from"]) == int(identity["target_from"])
            and int(row["target_to"]) == int(identity["target_to"])
            and _same_instant(row["decided_at"], int(identity["decided_at_micros"]))
            and _same_instant(row["fill_bar_open_at"], int(identity["fill_bar_open_micros"]))
            and str(row["cost_policy_hash"]).strip() == identity["cost_policy_hash"]
            and ((price is None and stored_price is None)
                 or (price is not None and stored_price is not None and Decimal(str(stored_price)) == Decimal(price)))
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise _Refusal([STORED_FILL_DOES_NOT_REDERIVE]) from error
    if not agrees:
        raise _Refusal([STORED_FILL_DOES_NOT_REDERIVE])
    return PaperFillV1(identity, fill_id, content_hash)


def _recorded_by(row: Mapping[str, Any], as_of: datetime, malformed: str) -> bool:
    """Whether the platform held the row at ``as_of`` (its audit ``recorded_at``)."""
    recorded = row.get("recorded_at")
    if not isinstance(recorded, datetime) or recorded.tzinfo is None:
        raise _Refusal([malformed])
    return recorded <= as_of


@dataclass(frozen=True, slots=True)
class _CandidateRecords:
    signals: tuple[LiveSignalV1, ...]
    fills: tuple[PaperFillV1, ...]
    ignored_signals: Mapping[str, int]


def _candidate_records(sources: PaperEvidenceSourcesV1, candidate: WatchedCandidateV1, *, cycle_id: str,
                       gate: LiveHoldoutGateV1, cost_policy: CostPolicyV1, as_of: datetime) -> _CandidateRecords:
    """The candidate's stored INCUBATING signals of this cycle and their fills, verified, as of ``as_of``."""
    study_id, trial_id, symbol = str(candidate.study.study_id), candidate.trial_id, candidate.symbol
    reasons: list[str] = []
    signals: list[LiveSignalV1] = []
    ignored = {"RESEARCH_WATCH_AUTHORITY": 0, "OTHER_CYCLE": 0, "AFTER_AS_OF": 0}
    expected = candidate_payload_v1(candidate)
    every_signal: dict[str, LiveSignalV1] = {}
    for row in sources.signal_rows(study_id=study_id, trial_id=trial_id, symbol=symbol):
        try:
            signal = _signal_from_row(row)
            in_time = _recorded_by(row, as_of, STORED_SIGNAL_DOES_NOT_REDERIVE)
        except _Refusal as refusal:
            reasons.extend(refusal.reasons)
            continue
        identity = signal.identity
        if (str(identity["candidate"]["study_id"]), str(identity["candidate"]["trial_id"]),
                identity["symbol"]) != (study_id, trial_id, symbol):
            reasons.append(MISMATCHED_CANDIDATE_EVIDENCE)  # a source answered for another candidate
            continue
        every_signal[str(signal.signal_id)] = signal
        if not in_time or signal.decided_at > as_of:
            ignored["AFTER_AS_OF"] += 1
            continue
        if identity["authority"] != AUTHORITY_INCUBATING:
            ignored["RESEARCH_WATCH_AUTHORITY"] += 1  # never paper-incubated: not paper evidence
            continue
        if identity["holdout"].get("cycle_id") != cycle_id:
            ignored["OTHER_CYCLE"] += 1
            continue
        stated = {"study_content_hash": str(identity["candidate"]["study_content_hash"]),
                  "authority": str(identity["authority"]),
                  "authority_evidence_hash": str(identity["authority_evidence_hash"])}
        if stated != {key: expected[key] for key in WATCHED_AS_KEYS}:
            reasons.append(MISMATCHED_CANDIDATE_EVIDENCE)  # another study version or another validation
            continue
        if dict(identity["holdout"]) != gate.payload():
            reasons.append(STALE_HOLDOUT_GATE)
            continue
        signals.append(signal)

    fills: list[PaperFillV1] = []
    kept = {str(signal.signal_id): signal for signal in signals}
    policy_hash = cost_policy.policy().content_hash
    for row in sources.fill_rows(study_id=study_id, trial_id=trial_id, symbol=symbol):
        try:
            fill = _fill_from_row(row)
            in_time = _recorded_by(row, as_of, STORED_FILL_DOES_NOT_REDERIVE)
        except _Refusal as refusal:
            reasons.extend(refusal.reasons)
            continue
        identity = fill.identity
        if (str(identity["study_id"]), str(identity["trial_id"]), identity["symbol"]) != (study_id, trial_id, symbol):
            reasons.append(MISMATCHED_CANDIDATE_EVIDENCE)
            continue
        if not in_time or int(identity["fill_bar_open_micros"]) > _micros(as_of):
            continue
        owner = every_signal.get(str(identity["signal_id"]))
        if owner is None or identity["signal_content_hash"] != identity_hash_v1(dict(owner.identity)) \
                or int(identity["decided_at_micros"]) != _micros(owner.decided_at):
            reasons.append(MISMATCHED_FILL_SIGNAL)
            continue
        if str(owner.signal_id) not in kept:
            continue  # the fill of a signal this run does not assess (other cycle, after as_of)
        if identity["cost_policy_hash"] != policy_hash:
            reasons.append(MISMATCHED_COST_POLICY)
            continue
        fills.append(fill)
    if reasons:
        raise _Refusal(reasons)
    if not signals:
        raise _Refusal([NO_LIVE_PAPER_SIGNALS_IN_CYCLE], [NO_LIVE_PAPER_SIGNALS_IN_CYCLE])
    return _CandidateRecords(tuple(signals), tuple(fills), ignored)


# ---------------------------------------------------------------------------
# Runner progress and live bars
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ProcessedBar:
    runner_session_id: str
    capture_session_id: str
    feed_segment: int
    closing_record_hash: str
    trade_manifest_hash: str
    completed_by_arrival_nanos: int


def _processed_bars(sources: PaperEvidenceSourcesV1, candidate: WatchedCandidateV1,
                    as_of: datetime) -> dict[int, list[_ProcessedBar]]:
    """Bars the runner sessions bound to this candidate (under exactly its evidence) recorded processing."""
    study_id, trial_id, symbol = str(candidate.study.study_id), candidate.trial_id, candidate.symbol
    by_session: dict[str, list[Mapping[str, Any]]] = {}
    for row in sources.liveness_rows(study_id=study_id, trial_id=trial_id, symbol=symbol):
        if not _recorded_by(row, as_of, RUNNER_LIVENESS_DOES_NOT_REDERIVE):
            continue
        identity = row.get("identity")
        key = str(identity.get("runner_session_id")) if isinstance(identity, Mapping) else "<malformed>"
        by_session.setdefault(key, []).append({"content_hash": row.get("content_hash"), "identity": identity})
    expected = {key: str(candidate_payload_v1(candidate)[key]) for key in WATCHED_AS_KEYS}
    out: dict[int, list[_ProcessedBar]] = {}
    bound = 0
    for session_id, items in sorted(by_session.items()):
        try:
            ordered = _session_records(items)
            entry = _relevant(ordered[0][1]["binding"], study_id, trial_id, symbol)
        except (_Refused, KeyError, TypeError, AttributeError) as error:
            raise _Refusal([RUNNER_LIVENESS_DOES_NOT_REDERIVE]) from error
        if entry is None or {key: str(entry.get(key)) for key in WATCHED_AS_KEYS} != expected:
            continue  # another candidate's or another evidence's session proves nothing here
        bound += 1
        for _, record in ordered:
            if record["kind"] != KIND_BARS:
                continue
            for bar in record["bars"]:
                try:
                    item = _ProcessedBar(session_id, str(bar["capture_session_id"]), int(bar["feed_segment"]),
                                         str(bar["closing_record_hash"]), str(bar["trade_manifest_hash"]),
                                         int(bar["completed_by_arrival_nanos"]))
                    out.setdefault(int(bar["bar_open_micros"]), []).append(item)
                except (KeyError, TypeError, ValueError) as error:
                    raise _Refusal([RUNNER_LIVENESS_DOES_NOT_REDERIVE]) from error
    if not bound:
        raise _Refusal([RUNNER_LIVENESS_NOT_RECORDED], [RUNNER_LIVENESS_NOT_RECORDED])
    return out


def _bound_live_bars(bars: Sequence[LiveBarV1], processed: Mapping[int, Sequence[_ProcessedBar]], *,
                     symbol: str, as_of: datetime) -> list[LiveBarV1]:
    """Live bars matched one-to-one to recorded runner progress, renumbered per (runner session, feed segment)."""
    as_of_nanos = _micros(as_of) * 1000
    reasons: list[str] = []
    matched: list[tuple[LiveBarV1, tuple[str, int]]] = []
    seen: dict[int, LiveBarV1] = {}
    for bar in bars:
        if not isinstance(bar, LiveBarV1) or bar.symbol != symbol:
            reasons.append(LIVE_BAR_NOT_RECORDED_BY_RUNNER)
            continue
        if bar.completed_by_arrival_nanos > as_of_nanos:
            continue
        opened = bar.bar.bar_open_micros
        if opened in seen:
            reasons.append(AMBIGUOUS_LIVE_BARS)
            continue
        seen[opened] = bar
        hits = [item for item in processed.get(opened, ())
                if (item.capture_session_id, item.feed_segment, item.closing_record_hash, item.trade_manifest_hash,
                    item.completed_by_arrival_nanos) == (str(bar.session_id), bar.segment,
                                                         bar.bar.closing_record_hash, bar.bar.trade_manifest_hash,
                                                         bar.completed_by_arrival_nanos)]
        if not hits:
            reasons.append(LIVE_BAR_NOT_RECORDED_BY_RUNNER)
        elif len(hits) > 1:
            reasons.append(AMBIGUOUS_RUNNER_PROGRESS)
        else:
            matched.append((bar, (hits[0].runner_session_id, hits[0].feed_segment)))
    if reasons:
        raise _Refusal(reasons)
    first: dict[tuple[str, int], int] = {}
    for bar, key in matched:
        first[key] = min(first.get(key, bar.bar.bar_open_micros), bar.bar.bar_open_micros)
    ordinal = {key: index for index, key in enumerate(sorted(first, key=lambda k: (first[k], k)))}
    return [LiveBarV1(bar.symbol, ordinal[key], bar.session_id, bar.bar, bar.completed_by_arrival_nanos,
                      bar.clock_bound_nanos) for bar, key in matched]


# ---------------------------------------------------------------------------
# Sealed replay
# ---------------------------------------------------------------------------


def _evidence_window(records: _CandidateRecords, live_bars: Sequence[LiveBarV1]) -> tuple[int, int]:
    """The minutes the candidate's stored decisions and fills depend on (bar opens, inclusive)."""
    starts = [int(s.identity["evidence"]["window_first_bar_open_micros"]) for s in records.signals]
    ends = [int(s.identity["evidence"]["bar_open_micros"]) + 2 * _MINUTE for s in records.signals]
    ends += [int(f.identity["fill_bar_open_micros"]) for f in records.fills]
    starts += [bar.bar.bar_open_micros for bar in live_bars]
    ends += [bar.bar.bar_open_micros for bar in live_bars]
    return min(starts), max(ends)


def _replay_bars(sources: PaperEvidenceSourcesV1, *, symbol: str, window: tuple[int, int],
                 as_of: datetime) -> list[SealedReplayBarV1]:
    try:
        datasets = list(sources.sealed_datasets(symbol=symbol, start_micros=window[0], end_micros=window[1],
                                                as_of=as_of))
    except EvidenceInputRefusedV1 as refusal:
        raise _Refusal([refusal.code], [refusal.code] if refusal.code == SEAL_NOT_FOUND else []) from refusal
    datasets = [item for item in datasets if item.sealed_at <= as_of]
    if not datasets:
        raise _Refusal([SEAL_NOT_FOUND], [SEAL_NOT_FOUND])
    if any(item.exchange_symbol != symbol for item in datasets):
        raise _Refusal([MISMATCHED_SEAL_SYMBOL])
    ordered = sorted(datasets, key=lambda item: (min((b.bar_open_micros for b in item.bars), default=0),
                                                 str(item.dataset_version_id)))
    out: list[SealedReplayBarV1] = []
    minutes: set[int] = set()
    for segment, dataset in enumerate(ordered):  # nothing crosses a sealed segment: one replay segment each
        for bar in dataset.bars:
            if bar.bar_open_micros in minutes:
                raise _Refusal([AMBIGUOUS_SEALED_BARS])
            minutes.add(bar.bar_open_micros)
            out.append(SealedReplayBarV1(symbol, segment, dataset.session_id, bar, dataset.content_hash))
    return out


# ---------------------------------------------------------------------------
# Producers
# ---------------------------------------------------------------------------


def _verified_report(report: FillParityReportV1, candidate: WatchedCandidateV1) -> FillParityReportV1:
    content_hash = identity_hash_v1(dict(report.identity))
    named = [(item["study_content_hash"], item["trial_id"], item["symbol"])
             for item in report.identity["inputs"]["candidates"]]
    if content_hash != report.content_hash or report.report_id != uuid5(_PARITY_NAMESPACE, f"report:{content_hash}") \
            or named != [(candidate.study.content_hash, candidate.trial_id, candidate.symbol)]:
        raise _Refusal([ISSUED_EVIDENCE_DOES_NOT_REDERIVE])
    return report


def produce_fill_parity_v1(sources: PaperEvidenceSourcesV1, candidate: WatchedCandidateV1, *, cycle_id: str,
                           gate: LiveHoldoutGateV1, cost_policy: CostPolicyV1,
                           as_of: datetime) -> FillParityReportV1:
    """The candidate's parity report rebuilt from persisted records, or :class:`_Refusal`.

    Independent missing inputs are all reported: a run without live bars still
    probes the sealed evidence, so its refusal names every missing input.
    """
    if candidate.authority != AUTHORITY_INCUBATING:
        raise _Refusal([CANDIDATE_NOT_INCUBATING])
    records = _candidate_records(sources, candidate, cycle_id=cycle_id, gate=gate, cost_policy=cost_policy,
                                 as_of=as_of)
    reasons: list[str] = []
    missing: list[str] = []
    live: list[LiveBarV1] = []
    try:
        processed = _processed_bars(sources, candidate, as_of)
    except _Refusal as refusal:
        reasons += refusal.reasons
        missing += refusal.missing
        processed = None
    supplied = sources.live_bars(study_id=str(candidate.study.study_id), trial_id=candidate.trial_id,
                                 symbol=candidate.symbol)
    if supplied is None:
        reasons.append(LIVE_BARS_NOT_PERSISTED)
        missing.append(LIVE_BARS_NOT_PERSISTED)
    elif processed is not None:
        try:
            live = _bound_live_bars(supplied, processed, symbol=candidate.symbol, as_of=as_of)
        except _Refusal as refusal:
            reasons += refusal.reasons
    replay: list[SealedReplayBarV1] = []
    try:
        replay = _replay_bars(sources, symbol=candidate.symbol, window=_evidence_window(records, live), as_of=as_of)
    except _Refusal as refusal:
        reasons += refusal.reasons
        missing += refusal.missing
    if reasons:
        raise _Refusal(reasons, missing)
    try:
        report = assess_fill_parity_v1(candidates=[candidate], holdout_gate=gate, cost_policy=cost_policy,
                                       replay_bars=replay, live_bars=live, live_signals=list(records.signals),
                                       live_fills=list(records.fills))
    except (PaperFillParityError, ValueError) as error:  # the resolvers' own refusals, verbatim
        raise _Refusal([f"{PARITY_INPUT_REFUSED}:{error}"]) from error
    return _verified_report(report, candidate)


def produce_funding_evidence_v1(sources: PaperEvidenceSourcesV1, candidate: WatchedCandidateV1, *, cycle_id: str,
                                gate: LiveHoldoutGateV1, cost_policy: CostPolicyV1, as_of: datetime,
                                declaration: FundingDeclarationV1 | None
                                ) -> tuple[CandidateFundingEvidenceV1, dict[str, Any]]:
    """The candidate's forward funding evidence (from already-stored settled funding only) and its report
    section, or :class:`_Refusal`."""
    from .forward_funding_price_bridge_v1 import (
        ForwardFundingPriceBridgeError,
        bridge_forward_funding_prices_v1,
    )

    if candidate.authority != AUTHORITY_INCUBATING:
        raise _Refusal([CANDIDATE_NOT_INCUBATING])
    _candidate_records(sources, candidate, cycle_id=cycle_id, gate=gate, cost_policy=cost_policy, as_of=as_of)
    store = sources.forward_funding_store(candidate.symbol)
    try:
        observations = [] if store is None else store.load(candidate.symbol)
    except (ForwardFundingError, OSError, KeyError, ValueError) as error:
        raise _Refusal([FORWARD_FUNDING_STORE_DOES_NOT_REDERIVE]) from error
    if store is None or not observations:
        state = resolve_forward_funding_acquisition_authorization_v1().state
        raise _Refusal([FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED, f"FORWARD_FUNDING_ACQUISITION:{state}"],
                       [FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED])
    if declaration is None:
        raise _Refusal([FUNDING_PRICE_CONVENTION_NOT_DECLARED], [FUNDING_PRICE_CONVENTION_NOT_DECLARED])
    try:
        price = sources.funding_price_evidence(symbol=candidate.symbol, declaration=declaration, as_of=as_of)
    except EvidenceInputRefusedV1 as refusal:
        raise _Refusal([refusal.code], [FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE]
                       if refusal.code == FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE else []) from refusal
    try:
        bridge = bridge_forward_funding_prices_v1(
            store, symbol=candidate.symbol, start_ms=declaration.start_ms, end_ms=declaration.end_ms, as_of=as_of,
            published_interval=declaration.published_interval, datasets=list(price.datasets),
            acceptance=price.acceptance, convention=declaration.convention, mark_evidence=list(price.mark_evidence),
            mark_nearby_tolerance_ms=declaration.mark_nearby_tolerance_ms)
        evidence = candidate_funding_evidence_v1(study_id=str(candidate.study.study_id), trial_id=candidate.trial_id,
                                                 symbol=candidate.symbol, bridges=[bridge])
    except (ForwardFundingPriceBridgeError, ForwardFundingError, PaperFundingEvidenceError) as error:
        raise _Refusal([f"{FUNDING_EVIDENCE_REFUSED}:{error}"]) from error
    if any(identity_hash_v1(dict(identity)) != content_hash for content_hash, identity in evidence.bridges):
        raise _Refusal([ISSUED_EVIDENCE_DOES_NOT_REDERIVE])
    return evidence, funding_section_v1(sources, candidate, evidence, cycle_id=cycle_id, as_of=as_of)


def funding_section_v1(sources: PaperEvidenceSourcesV1, candidate: WatchedCandidateV1,
                       evidence: CandidateFundingEvidenceV1 | None, *, cycle_id: str,
                       as_of: datetime) -> dict[str, Any]:
    """The candidate's ``funding_evidence`` section of the cycle's incubation report at ``as_of``.

    Built by the unchanged report over the cycle's stored fills (recorded and decided
    by ``as_of``) with ``evidence`` attached. A candidate the report does not list has
    no cycle fill as of ``as_of``: its section is the funding module's own over no
    holdings, under the cycle's policy (the store re-derives exactly that).
    """
    from .paper_funding_evidence_v1 import funding_evidence_section_v1
    from .paper_incubation_report_v1 import incubation_report_v1

    study_id, trial_id, symbol = str(candidate.study.study_id), candidate.trial_id, candidate.symbol
    try:
        fills, cost, resolution = sources.cycle_incubation_inputs(cycle_id=cycle_id, as_of=as_of)
        def listed(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
            return [item for item in report["candidates"]
                    if (str(item["study_id"]), str(item["trial_id"]), str(item["symbol"])) == (study_id, trial_id,
                                                                                              symbol)]

        # The report refuses funding of a candidate it does not list: ask first, as the store does.
        found = listed(incubation_report_v1(list(fills), cost, as_of=as_of, resolution=resolution))
        if len(found) > 1:
            raise _Refusal([CYCLE_PAPER_FILLS_DO_NOT_REDERIVE])
        if found:
            if evidence is None:
                return dict(found[0]["funding_evidence"])
            (entry,) = listed(incubation_report_v1(list(fills), cost, as_of=as_of, resolution=resolution,
                                                   funding=[evidence]))
            return dict(entry["funding_evidence"])
        return funding_evidence_section_v1(evidence, study_id=study_id, trial_id=trial_id, symbol=symbol,
                                           holdings=[], fills_skipped=0, as_of_micros=_micros(as_of),
                                           binding_validation_hash=None,
                                           fee_schedule_verified=cost.fee_schedule is not None)
    except EvidenceInputRefusedV1 as refusal:
        raise _Refusal([refusal.code]) from refusal
    except (ValueError, KeyError, TypeError) as error:
        raise _Refusal([f"{CYCLE_PAPER_FILLS_DO_NOT_REDERIVE}:{error}"]) from error


def _refused(kind: str, candidate: WatchedCandidateV1, refusal: _Refusal) -> ProductionOutcomeV1:
    return ProductionOutcomeV1(kind, str(candidate.study.study_id), candidate.trial_id, candidate.symbol,
                               STATUS_REFUSED, refusal.reasons, refusal.missing)


def _sink_error(error: Exception) -> _Refusal:
    code = str(error)
    holdout = [HOLDOUT_UNOPENED_OR_7] if code in _STORE_HOLDOUT_REFUSALS else []
    return _Refusal([f"{SINK_REFUSED}:{code}", *holdout], holdout)


def run_paper_evidence_production_v1(
    sources: PaperEvidenceSourcesV1,
    *,
    cycle_id: str,
    candidates: Sequence[WatchedCandidateV1],
    holdout_gate: LiveHoldoutGateV1,
    cost_policy: CostPolicyV1,
    as_of: datetime,
    recorded_at: datetime,
    funding: FundingDeclarationV1 | None,
    sink: PaperEvidenceSinkV1 | None,
) -> ProductionRunV1:
    """Produce and (unless ``sink`` is ``None``: a dry run) record each candidate's parity and funding evidence.

    ``as_of`` is the point-in-time reading instant; ``recorded_at`` the write
    instant (never before ``as_of``). ``funding`` holds the caller's declarations
    (``None``: none declared).
    """
    if not isinstance(holdout_gate, LiveHoldoutGateV1):
        raise PaperEvidenceProductionError("holdout_gate_must_be_an_issued_live_holdout_gate")
    if _micros(recorded_at) < _micros(as_of):
        raise PaperEvidenceProductionError("recorded_at_before_as_of")
    if not candidates:
        raise PaperEvidenceProductionError("production_requires_at_least_one_candidate")
    keys = [(str(c.study.study_id), c.trial_id, c.symbol) for c in candidates]
    if len(set(keys)) != len(keys):
        raise PaperEvidenceProductionError("candidate_supplied_twice")
    outcomes: list[ProductionOutcomeV1] = []
    for candidate in sorted(candidates, key=lambda c: (str(c.study.study_id), c.trial_id, c.symbol)):
        study_id = str(candidate.study.study_id)
        if holdout_gate.cycle_id != cycle_id:
            refusal = _Refusal([HOLDOUT_GATE_OF_ANOTHER_CYCLE])
            outcomes += [_refused(KIND_FILL_PARITY, candidate, refusal),
                         _refused(KIND_FUNDING_EVIDENCE, candidate, refusal)]
            continue

        try:
            report = produce_fill_parity_v1(sources, candidate, cycle_id=cycle_id, gate=holdout_gate,
                                            cost_policy=cost_policy, as_of=as_of)
            hashes: dict[str, Any] = {"report_content_hash": report.content_hash, "report_id": str(report.report_id)}
            status = STATUS_DRY_RUN
            if sink is not None:
                try:
                    stored = list(sink.record_fill_parity_v1(report, cycle_id=cycle_id, as_of=as_of,
                                                             recorded_at=recorded_at))
                except ValueError as error:
                    raise _sink_error(error) from error
                hashes["assessment_ids"] = sorted(str(getattr(row, "assessment_id", "")) for row in stored)
                status = STATUS_PERSISTED if stored and all(row.recorded_at == recorded_at for row in stored) \
                    else STATUS_ALREADY_STORED
            outcomes.append(ProductionOutcomeV1(KIND_FILL_PARITY, study_id, candidate.trial_id, candidate.symbol,
                                                status, report.reasons, (), hashes, report.status))
        except _Refusal as refusal:
            outcomes.append(_refused(KIND_FILL_PARITY, candidate, refusal))

        try:
            evidence, section = produce_funding_evidence_v1(sources, candidate, cycle_id=cycle_id,
                                                            gate=holdout_gate, cost_policy=cost_policy, as_of=as_of,
                                                            declaration=funding)
            hashes = {"bridge_hashes": [content_hash for content_hash, _ in evidence.bridges],
                      "evidence_hash": str(section["evidence_hash"])}
            status = STATUS_DRY_RUN
            if sink is not None:
                try:
                    row = sink.record_funding_evidence_v1(evidence, section=section, cycle_id=cycle_id, as_of=as_of,
                                                          recorded_at=recorded_at)
                except ValueError as error:
                    raise _sink_error(error) from error
                hashes["record_id"] = str(getattr(row, "record_id", ""))
                status = STATUS_PERSISTED if row.recorded_at == recorded_at else STATUS_ALREADY_STORED
            outcomes.append(ProductionOutcomeV1(KIND_FUNDING_EVIDENCE, study_id, candidate.trial_id,
                                                candidate.symbol, status, tuple(section["completeness_blockers"]),
                                                (), hashes, str(section["state"])))
        except _Refusal as refusal:
            outcomes.append(_refused(KIND_FUNDING_EVIDENCE, candidate, refusal))
    return ProductionRunV1(cycle_id, as_of, recorded_at, sink is None, tuple(outcomes))


# ---------------------------------------------------------------------------
# Production sources (PostgreSQL rows + sealed frames + raw capture; read-only)
# ---------------------------------------------------------------------------


def sealed_dataset_from_seal_v1(seal: Any, *, store: Any) -> SealedDatasetBarsV1:
    """The minute bars of an intact, raw-replayed seal, cross-checked against its re-derived forward bars."""
    from .first_party_t4_forward_bars_v1 import (
        T4ForwardBarError,
        _sealed_minute_bars,
        forward_bars_from_seal_v1,
    )

    try:
        forward = forward_bars_from_seal_v1(seal, store=store)  # integrity, raw replay, knowledge re-derivation
        minute = _sealed_minute_bars(seal, store)
    except (T4ForwardBarError, ValueError, OSError, KeyError) as error:
        raise EvidenceInputRefusedV1(SEAL_DOES_NOT_REDERIVE, str(error)) from error
    proven = {bar.bar_open_micros: bar for bar in forward.bars}
    if sorted(proven) != sorted(bar.bar_open_micros for bar in minute):
        raise EvidenceInputRefusedV1(SEAL_DOES_NOT_REDERIVE, "minute_bars_differ_from_forward_bars")
    for bar in minute:
        twin = proven[bar.bar_open_micros]
        if (bar.open_price, bar.high_price, bar.low_price, bar.close_price, bar.trade_count,
                bar.first_trade_reference, bar.last_trade_reference, bar.open_market_knowledge_micros,
                bar.complete_market_knowledge_micros, bar.open_is_sequence_ambiguous, bar.close_is_sequence_ambiguous,
                bar.trade_manifest_hash, bar.closing_record_hash) != (
                twin.open_price, twin.high_price, twin.low_price, twin.close_price, twin.trade_count,
                twin.first_trade_reference, twin.last_trade_reference, twin.open_market_knowledge_micros,
                twin.complete_market_knowledge_micros, twin.open_is_sequence_ambiguous,
                twin.close_is_sequence_ambiguous, twin.trade_manifest_hash, twin.clock.closing_record_hash):
            raise EvidenceInputRefusedV1(SEAL_DOES_NOT_REDERIVE, "minute_bar_differs_from_forward_bar")
    return SealedDatasetBarsV1(seal.dataset_version_id, seal.content_hash, forward.session_id,
                               forward.exchange_symbol, seal.sealed_at, tuple(minute))


#: Catalog query pruning only (host-arrival vs venue minutes, the known host clock offset): bars are
#: then selected by their venue minute, so this never decides evidence.
_CATALOG_ARRIVAL_MARGIN_NANOS: Final = 3_600_000_000_000


class PostgresPaperEvidenceSourcesV1:
    """Persisted forward evidence through one cursor, plus explicitly given frame, capture and funding roots.

    Read-only: SELECTs with explicit column lists, frame reads and raw-capture
    replays. ``live_bars`` always answers ``None`` (not persisted). A sealed
    dataset is used only after it rebuilds from raw capture
    (:func:`~trade_platform.first_party_t4_dataset_v1.t4_seal_from_catalog_v1`);
    without a frame store and capture root nothing is sealed evidence.
    """

    def __init__(self, cursor: Any, *, frame_store: Any | None, capture_root: Path | None,
                 funding_root: Path | None) -> None:
        self._cursor = cursor
        self._frame_store = frame_store
        self._capture_root = capture_root
        self._funding_root = funding_root

    def signal_rows(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        self._cursor.execute(
            "SELECT signal_id, content_hash, study_id, trial_id, symbol, authority, target_from, target_to, "
            "bar_open_at, decided_at, identity, recorded_at FROM live_strategy_signals "
            "WHERE study_id=%s AND trial_id=%s AND symbol=%s ORDER BY decided_at, signal_id",
            (UUID(study_id), UUID(trial_id), symbol))
        names = ("signal_id", "content_hash", "study_id", "trial_id", "symbol", "authority", "target_from",
                 "target_to", "bar_open_at", "decided_at", "identity", "recorded_at")
        return [dict(zip(names, row, strict=True)) for row in self._cursor.fetchall()]

    def fill_rows(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        self._cursor.execute(
            "SELECT fill_id, content_hash, signal_id, signal_content_hash, study_id, trial_id, symbol, status, "
            "target_from, target_to, decided_at, fill_bar_open_at, fill_price, cost_policy_hash, identity, "
            "recorded_at FROM paper_incubation_fills WHERE study_id=%s AND trial_id=%s AND symbol=%s "
            "ORDER BY decided_at, signal_id", (UUID(study_id), UUID(trial_id), symbol))
        names = ("fill_id", "content_hash", "signal_id", "signal_content_hash", "study_id", "trial_id", "symbol",
                 "status", "target_from", "target_to", "decided_at", "fill_bar_open_at", "fill_price",
                 "cost_policy_hash", "identity", "recorded_at")
        return [dict(zip(names, row, strict=True)) for row in self._cursor.fetchall()]

    def liveness_rows(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        wanted = json.dumps([{"study_id": study_id, "trial_id": trial_id}])
        self._cursor.execute(
            "SELECT p.content_hash, p.identity, p.recorded_at FROM runner_liveness_progress p "
            "WHERE p.runner_session_id IN (SELECT s.runner_session_id FROM runner_liveness_progress s "
            "WHERE s.kind='STARTED' AND s.symbol=%s AND s.identity->'binding'->'candidates' @> %s::jsonb) "
            "ORDER BY p.runner_session_id, p.sequence", (symbol, wanted))
        return [{"content_hash": str(row[0]).strip(), "identity": _json(row[1]), "recorded_at": row[2]}
                for row in self._cursor.fetchall()]

    def cycle_incubation_inputs(self, *, cycle_id: str,
                                as_of: datetime) -> tuple[list[Mapping[str, Any]], CostPolicyV1, Any]:
        """The read model's (and the store's) loaders: cycle fills recorded and decided by ``as_of``."""
        from .paper_account_v1 import (
            cycle_cost_policy_v1,
            cycle_fill_rows_v1,
            fills_signals_from_rows,
        )
        from .paper_incubation_registry_v1 import (
            IncubationRegistryError,
            resolve_incubation_bindings_v1,
        )

        cost = cycle_cost_policy_v1(self._cursor, cycle_id)
        try:
            resolution: Any = resolve_incubation_bindings_v1(self._cursor, cycle_id=cycle_id)
        except IncubationRegistryError:
            resolution = None  # as the read model and the store: an unresolved registry binds nothing
        fill_rows, signal_rows = cycle_fill_rows_v1(self._cursor, cycle_id)
        identities, _ = fills_signals_from_rows(fill_rows, signal_rows)
        hashes = [str(row[0]).strip() for row in fill_rows]
        recorded: dict[str, datetime] = {}
        if hashes:
            self._cursor.execute("SELECT content_hash, recorded_at FROM paper_incubation_fills "
                                 "WHERE content_hash = ANY(%s)", (hashes,))
            recorded = {str(row[0]).strip(): row[1] for row in self._cursor.fetchall()}
        known = _micros(as_of)
        fills = [identity for content_hash, identity in zip(hashes, identities, strict=True)
                 if recorded[content_hash] <= as_of and int(identity["decided_at_micros"]) <= known]
        return fills, cost, resolution

    def live_bars(self, *, study_id: str, trial_id: str, symbol: str) -> None:
        return None  # not persisted anywhere (MISSING_PERSISTED_INPUTS_V1); never rebuilt from a seal

    def _seals(self, *, symbol: str, start_micros: int, end_micros: int, as_of: datetime) -> list[Any]:
        from .first_party_t4_dataset_v1 import CataloguedT4DatasetV1, t4_seal_from_catalog_v1

        if self._frame_store is None or self._capture_root is None:
            raise EvidenceInputRefusedV1(SEAL_NOT_FOUND, "frame_store_and_capture_root_not_given")
        self._cursor.execute(
            "SELECT dataset_version_id, content_hash, identity, frame_manifests, sealed_at "
            "FROM first_party_t4_datasets WHERE identity->>'exchange_symbol' = %s "
            "AND end_arrival_nanos_inclusive >= %s AND start_arrival_nanos <= %s AND sealed_at <= %s "
            "ORDER BY start_arrival_nanos, dataset_version_id",
            (symbol, start_micros * 1000 - _CATALOG_ARRIVAL_MARGIN_NANOS,
             (end_micros + _MINUTE) * 1000 + _CATALOG_ARRIVAL_MARGIN_NANOS, as_of))
        seals = []
        for row in self._cursor.fetchall():
            catalogued = CataloguedT4DatasetV1(
                dataset_version_id=UUID(str(row[0])), content_hash=str(row[1]).strip(), identity=_json(row[2]),
                frame_manifests={str(k): str(v) for k, v in _json(row[3]).items()}, sealed_at=row[4])
            try:
                seals.append(t4_seal_from_catalog_v1(catalogued, store=self._frame_store,
                                                     capture_root=self._capture_root))
            except (ValueError, OSError, KeyError) as error:
                raise EvidenceInputRefusedV1(SEAL_DOES_NOT_REDERIVE, str(error)) from error
        return seals

    def sealed_datasets(self, *, symbol: str, start_micros: int, end_micros: int,
                        as_of: datetime) -> list[SealedDatasetBarsV1]:
        datasets = [sealed_dataset_from_seal_v1(seal, store=self._frame_store) for seal in
                    self._seals(symbol=symbol, start_micros=start_micros, end_micros=end_micros, as_of=as_of)]
        return [item for item in datasets if any(start_micros <= bar.bar_open_micros <= end_micros
                                                 for bar in item.bars)]

    def forward_funding_store(self, symbol: str) -> ForwardSettledFundingStoreV1 | None:
        return None if self._funding_root is None else ForwardSettledFundingStoreV1(self._funding_root)

    def funding_price_evidence(self, *, symbol: str, declaration: FundingDeclarationV1,
                               as_of: datetime) -> FundingPriceEvidenceV1:
        from .first_party_capture_authority_v1 import (
            resolve_t4_registered_first_party_capture_contract_v1,
        )
        from .first_party_capture_hourly_acceptance_v1 import (
            derive_hourly_acceptance_v1,
            verified_windows_v1,
        )
        from .first_party_t4_forward_bars_v1 import T4ForwardBarError, forward_bars_from_seal_v1
        from .first_party_t4_settlement_mark_price_v1 import (
            T4MarkEvidenceError,
            mark_evidence_from_seal_v1,
        )

        seals = self._seals(symbol=symbol, start_micros=declaration.start_ms * 1000,
                            end_micros=declaration.end_ms * 1000, as_of=as_of)
        if not seals:
            raise EvidenceInputRefusedV1(FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE, "no_sealed_dataset_in_the_window")
        contracts = {str(seal.source_id) for seal in seals}
        if len(contracts) != 1:
            raise EvidenceInputRefusedV1(AMBIGUOUS_SEALED_BARS, "datasets_of_several_capture_sources")
        contract = resolve_t4_registered_first_party_capture_contract_v1(seals[0].source_id)
        store = self._frame_store
        if contract is None or self._capture_root is None or store is None:
            raise EvidenceInputRefusedV1(FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE, "capture_source_not_t4_registered")
        try:
            datasets = tuple(forward_bars_from_seal_v1(seal, store=store) for seal in seals)
            marks = tuple(mark_evidence_from_seal_v1(seal, store=store) for seal in seals)
            windows = verified_windows_v1(self._capture_root, contract)
            acceptance = derive_hourly_acceptance_v1(
                [(symbol, windows.windows)], max_unproven_nanos=declaration.capture_max_unproven_nanos,
                required_consecutive_hours=1)
        except (T4ForwardBarError, T4MarkEvidenceError, ValueError, OSError) as error:
            raise EvidenceInputRefusedV1(SEAL_DOES_NOT_REDERIVE, str(error)) from error
        return FundingPriceEvidenceV1(datasets, acceptance, marks)


__all__ = [
    "AMBIGUOUS_LIVE_BARS",
    "AMBIGUOUS_RUNNER_PROGRESS",
    "AMBIGUOUS_SEALED_BARS",
    "CANDIDATE_NOT_INCUBATING",
    "CYCLE_PAPER_FILLS_DO_NOT_REDERIVE",
    "FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED",
    "FORWARD_FUNDING_STORE_DOES_NOT_REDERIVE",
    "FUNDING_EVIDENCE_REFUSED",
    "FUNDING_PRICE_CONVENTION_NOT_DECLARED",
    "FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE",
    "HOLDOUT_GATE_OF_ANOTHER_CYCLE",
    "HOLDOUT_UNOPENED_OR_7",
    "ISSUED_EVIDENCE_DOES_NOT_REDERIVE",
    "KIND_FILL_PARITY",
    "KIND_FUNDING_EVIDENCE",
    "LIVE_BARS_NOT_PERSISTED",
    "LIVE_BAR_NOT_RECORDED_BY_RUNNER",
    "MISMATCHED_CANDIDATE_EVIDENCE",
    "MISMATCHED_COST_POLICY",
    "MISMATCHED_FILL_SIGNAL",
    "MISMATCHED_SEAL_SYMBOL",
    "MISSING_PERSISTED_INPUTS_V1",
    "NO_LIVE_PAPER_SIGNALS_IN_CYCLE",
    "PARITY_INPUT_REFUSED",
    "PRODUCTION_RUN_SCHEMA_VERSION_V1",
    "RUNNER_LIVENESS_DOES_NOT_REDERIVE",
    "RUNNER_LIVENESS_NOT_RECORDED",
    "SEAL_DOES_NOT_REDERIVE",
    "SEAL_NOT_FOUND",
    "SINK_REFUSED",
    "STALE_HOLDOUT_GATE",
    "STATUS_ALREADY_STORED",
    "STATUS_DRY_RUN",
    "STATUS_PERSISTED",
    "STATUS_REFUSED",
    "STORED_FILL_DOES_NOT_REDERIVE",
    "STORED_SIGNAL_DOES_NOT_REDERIVE",
    "EvidenceInputRefusedV1",
    "FundingDeclarationV1",
    "FundingPriceEvidenceV1",
    "PaperEvidenceProductionError",
    "PaperEvidenceSinkV1",
    "PaperEvidenceSourcesV1",
    "PostgresPaperEvidenceSourcesV1",
    "ProductionOutcomeV1",
    "ProductionRunV1",
    "SealedDatasetBarsV1",
    "StorePaperEvidenceSinkV1",
    "funding_section_v1",
    "produce_fill_parity_v1",
    "produce_funding_evidence_v1",
    "run_paper_evidence_production_v1",
    "sealed_dataset_from_seal_v1",
]
