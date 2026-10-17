"""Paper evidence store -- append-only PostgreSQL authority for parity and forward funding evidence.

``RESEARCH_ONLY`` / paper only. Stores two kinds of already-issued, content-addressed
evidence (migration ``20261017_0066``) and reads them back verified. Nothing here
computes evidence, promotes a candidate, opens a holdout or costs funding; there is
no generic "submit evidence" path and no HTTP route writes here.

Import-light: the protected API image has no numerical libraries, and the parity
module pulls them (its runner). Its report type is therefore imported only for
type checking; a report is verified *structurally* by re-deriving its own content
hash, its observations' hashes and its assessment from them. The mirrored parity
constants are pinned equal to the source by a test.

What is stored
--------------
``paper_fill_parity_assessments``
    A :class:`~trade_platform.paper_fill_parity_v1.FillParityReportV1` is
    multi-candidate; it is stored once per candidate it names (key: report hash,
    study, trial), every row with the verbatim report identity. The study id is
    derived from the candidate's study content hash exactly as
    :attr:`~trade_platform.strategy_lab_study_v1.StudySpecV1.study_id` derives it.
``paper_funding_evidence_records``
    A candidate's ``funding_evidence`` section
    (:func:`~trade_platform.paper_funding_evidence_v1.funding_evidence_section_v1`)
    with the :class:`~trade_platform.paper_funding_evidence_v1.CandidateFundingEvidenceV1`
    bridges it binds (``None`` for ``NO_FUNDING_EVIDENCE_SUPPLIED``). The section is
    re-derived *in full* from those bridges and the holding intervals it states --
    on write and on every read -- so a section the module would not produce is refused.
    The holding intervals and the skipped-fill fact it states must also be exactly
    those :func:`~trade_platform.paper_incubation_report_v1.incubation_report_v1`
    derives from the cycle's stored paper fills recorded by ``as_of`` (again on write
    and on every read), so a section over invented holdings is refused.

Two clocks
----------
``as_of`` is the evidence's knowledge instant, ``recorded_at`` the store's. A write
refuses ``as_of`` after ``recorded_at`` and any fact of the evidence after its own
``as_of`` (every ``*_micros`` fact of a parity observation, the completion of every
live window's last bar; every funding bridge's ``as_of`` and holding end; an open
holding's end must *be* the report's ``as_of``). A read at ``as_of`` sees only rows
with ``recorded_at <= as_of`` and evidence ``as_of <= as_of`` and returns the latest
by (evidence ``as_of``, ``recorded_at``, id). Every candidate row is re-derived; one
that does not re-derive raises :class:`PaperEvidenceStoreError` and is never
skipped. ``None`` is ABSENT, never a stored ``INCOMPLETE`` / ``FAIL`` / any funding
state, which are returned verbatim.

Cycle, candidate and holdout
----------------------------
The cycle must be in the research cycle registry. The candidate's study and trial
must be in the Strategy Lab ledger. Where the R6 registry binds the candidate
(recorded candidate states and their validations) it must be bound to this cycle,
never rejected, and the evidence's validation / authority hash must be that
binding's; a hash naming another cycle's validation is refused.

Holdout isolation. A parity report records the :class:`LiveHoldoutGateV1` it was
run under (``inputs.holdout``). It must be this cycle's gate (same cycle and
holdout start); a gate that claims an opened holdout must match the registry's
opening known at ``as_of``. Every bar / signal the report observed, and every live
window, must be admitted by that recorded gate -- so evidence over an unopened
holdout (or inside an opened holdout span) is refused. Funding evidence records no
gate: its bridges' funding windows and its holding intervals may not touch the
holdout span (unbounded while the holdout is unopened as of ``as_of``).

Idempotency and immutability
----------------------------
Re-recording content-identical evidence under an existing key returns the stored
row unchanged (its original ``as_of`` / ``recorded_at``: the earliest recorded
knowledge stands); a different payload under the key is refused
(``CONFLICTING_DUPLICATE_*``). Newer evidence is a new row; nothing is updated or
deleted (immutable triggers). Writes run in the caller's transaction.

Every refusal is a :class:`PaperEvidenceStoreError` whose message is a stable,
upper-case reason code.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .paper_funding_evidence_v1 import (
    FILLS_SKIPPED_UNKNOWN_POSITION,
    FUNDING_EVIDENCE_STATES_V1,
    HOLDING_OPEN_AT_AS_OF,
    MISSING_VERIFIED_FEE_SCHEDULE,
    PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1,
    STATE_ECONOMICALLY_COSTABLE_RESERVED,
    STATE_NO_FUNDING_EVIDENCE,
    CandidateFundingEvidenceV1,
    PaperFundingEvidenceError,
    candidate_funding_evidence_v1,
    funding_evidence_section_v1,
)
from .strategy_lab_study_v1 import _NAMESPACE as _STUDY_NAMESPACE
from .strategy_lab_study_v1 import (
    StrategyLabStudyError,
    canonical_identity_json_v1,
    identity_hash_v1,
)

if TYPE_CHECKING:
    from .paper_fill_parity_v1 import FillParityReportV1

# Mirrors of :mod:`trade_platform.paper_fill_parity_v1` (not imported at runtime: it pulls the
# numerical stack). A test pins every one equal to the source.
FILL_PARITY_SCHEMA_VERSION_V1: Final = "paper-fill-parity-v1"
FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1: Final = "paper-fill-parity-observation-v1"
FILL_PARITY_CLAIM_V1: Final = "READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION"
PARITY_STATUSES_V1: Final = ("PASS", "FAIL", "INCOMPLETE")
_PARITY_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.paper_fill_parity_v1")
_PASS, _FAIL, _INCOMPLETE = PARITY_STATUSES_V1
_MATCH: Final = "MATCH"
_DIVERGED: Final = "DIVERGED"
_UNAVAILABLE: Final = "UNAVAILABLE"
_KIND_SIGNAL: Final = "SIGNAL"
_NO_MATCHED_FILLED: Final = "NO_MATCHED_FILLED_OBSERVATION"
_FILLED: Final = "FILLED"
_AUTHORITY_INCUBATING: Final = "INCUBATING"
_REJECTED_STATE: Final = "HOLDOUT_FAILED_REJECTED"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.paper_evidence_store_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE_MICROS: Final = 60_000_000

# Reason codes (the message of every refusal).
TIMESTAMP_NOT_TIMEZONE_AWARE: Final = "TIMESTAMP_NOT_TIMEZONE_AWARE"
AS_OF_AFTER_RECORDED_AT: Final = "AS_OF_AFTER_RECORDED_AT"
EVIDENCE_FACTS_AFTER_AS_OF: Final = "EVIDENCE_FACTS_AFTER_AS_OF"
CYCLE_UNKNOWN: Final = "CYCLE_UNKNOWN"
CANDIDATE_NOT_IN_STRATEGY_LAB: Final = "CANDIDATE_NOT_IN_STRATEGY_LAB"
CANDIDATE_REJECTED_IN_REGISTRY: Final = "CANDIDATE_REJECTED_IN_REGISTRY"
CANDIDATE_BOUND_TO_ANOTHER_CYCLE: Final = "CANDIDATE_BOUND_TO_ANOTHER_CYCLE"
CANDIDATE_AUTHORITY_EVIDENCE_DISAGREES_WITH_REGISTRY: Final = "CANDIDATE_AUTHORITY_EVIDENCE_DISAGREES_WITH_REGISTRY"
HOLDOUT_GATE_NOT_OF_THIS_CYCLE: Final = "HOLDOUT_GATE_NOT_OF_THIS_CYCLE"
HOLDOUT_GATE_OPENING_NOT_REGISTERED: Final = "HOLDOUT_GATE_OPENING_NOT_REGISTERED"
EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT: Final = "EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT"
EVIDENCE_TOUCHES_THE_OPENED_HOLDOUT_SPAN: Final = "EVIDENCE_TOUCHES_THE_OPENED_HOLDOUT_SPAN"
FILL_PARITY_REPORT_MALFORMED: Final = "FILL_PARITY_REPORT_MALFORMED"
UNSUPPORTED_FILL_PARITY_SCHEMA_VERSION: Final = "UNSUPPORTED_FILL_PARITY_SCHEMA_VERSION"
UNSUPPORTED_FILL_PARITY_CONTRACT: Final = "UNSUPPORTED_FILL_PARITY_CONTRACT"
FILL_PARITY_REPORT_DOES_NOT_REDERIVE: Final = "FILL_PARITY_REPORT_DOES_NOT_REDERIVE"
FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE: Final = "FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE"
FILL_PARITY_ASSESSMENT_DOES_NOT_REDERIVE: Final = "FILL_PARITY_ASSESSMENT_DOES_NOT_REDERIVE"
FILL_PARITY_CANDIDATE_NOT_INCUBATING: Final = "FILL_PARITY_CANDIDATE_NOT_INCUBATING"
FILL_PARITY_CANDIDATE_REPEATED: Final = "FILL_PARITY_CANDIDATE_REPEATED"
CONFLICTING_DUPLICATE_FILL_PARITY: Final = "CONFLICTING_DUPLICATE_FILL_PARITY"
STORED_FILL_PARITY_DOES_NOT_REDERIVE: Final = "STORED_FILL_PARITY_DOES_NOT_REDERIVE"
FUNDING_EVIDENCE_NOT_ISSUED: Final = "FUNDING_EVIDENCE_NOT_ISSUED"
FUNDING_SECTION_MALFORMED: Final = "FUNDING_SECTION_MALFORMED"
UNSUPPORTED_FUNDING_EVIDENCE_SCHEMA_VERSION: Final = "UNSUPPORTED_FUNDING_EVIDENCE_SCHEMA_VERSION"
FUNDING_STATE_ECONOMICALLY_COSTABLE_REFUSED: Final = "FUNDING_STATE_ECONOMICALLY_COSTABLE_REFUSED"
FUNDING_STATE_UNKNOWN: Final = "FUNDING_STATE_UNKNOWN"
FUNDING_SECTION_CLAIMS_COSTABLE: Final = "FUNDING_SECTION_CLAIMS_COSTABLE"
FUNDING_ECONOMIC_BLOCKERS_MISSING: Final = "FUNDING_ECONOMIC_BLOCKERS_MISSING"
FUNDING_SECTION_DOES_NOT_REDERIVE: Final = "FUNDING_SECTION_DOES_NOT_REDERIVE"
FUNDING_EVIDENCE_NOT_OF_THIS_CANDIDATE: Final = "FUNDING_EVIDENCE_NOT_OF_THIS_CANDIDATE"
FUNDING_SECTION_WITHOUT_ITS_EVIDENCE: Final = "FUNDING_SECTION_WITHOUT_ITS_EVIDENCE"
FUNDING_AS_OF_IS_NOT_THE_REPORT_AS_OF: Final = "FUNDING_AS_OF_IS_NOT_THE_REPORT_AS_OF"
CONFLICTING_DUPLICATE_FUNDING_EVIDENCE: Final = "CONFLICTING_DUPLICATE_FUNDING_EVIDENCE"
STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE: Final = "STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE"
FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS: Final = "FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS"
STORED_FILLS_DO_NOT_REDERIVE: Final = "STORED_FILLS_DO_NOT_REDERIVE"


class PaperEvidenceStoreError(ValueError):
    """Every refusal of the paper evidence store; the message is a stable reason code."""


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


@dataclass(frozen=True, slots=True)
class StoredFillParityV1:
    """One stored (and re-derived) parity assessment of one candidate."""

    assessment_id: UUID
    report_content_hash: str
    cycle_id: str
    study_id: UUID
    trial_id: UUID
    symbol: str
    status: str
    reasons: tuple[str, ...]
    as_of: datetime
    window_start: datetime | None
    window_end: datetime | None
    source_hashes: Mapping[str, Any]
    identity: Mapping[str, Any]
    recorded_at: datetime
    report_id: UUID
    schema_version: str
    study_content_hash: str
    authority_evidence_hash: str
    holdout_start: datetime
    holdout_end_exclusive: datetime | None


@dataclass(frozen=True, slots=True)
class StoredFundingEvidenceV1:
    """One stored (and re-derived) funding evidence section of one candidate. Never a cost."""

    record_id: UUID
    evidence_hash: str
    cycle_id: str
    study_id: UUID
    trial_id: UUID
    symbol: str
    state: str
    completeness_blockers: tuple[str, ...]
    economic_blockers: tuple[str, ...]
    as_of: datetime
    window_start: datetime | None
    window_end: datetime | None
    bridge_hashes: Mapping[str, str]
    evidence: Mapping[str, Any]
    recorded_at: datetime
    schema_version: str
    evidence_validation_hash: str | None


@dataclass(frozen=True, slots=True)
class _StoredBridgeV1:
    """A stored bridge identity, re-offered to the funding module's own acceptance."""

    identity: Mapping[str, Any]
    content_hash: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _refuse(code: str, cause: BaseException | None = None) -> PaperEvidenceStoreError:
    error = PaperEvidenceStoreError(code)
    if cause is not None:
        error.__cause__ = cause
    return error


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _refuse(TIMESTAMP_NOT_TIMEZONE_AWARE)
    return value.astimezone(UTC)


def _instants(as_of: object, recorded_at: object) -> tuple[datetime, datetime]:
    known, recorded = _aware(as_of), _aware(recorded_at)
    if known > recorded:
        raise _refuse(AS_OF_AFTER_RECORDED_AT)
    return known, recorded


def _micros(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(microseconds=1)


def _instant(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def _plain(value: object, code: str) -> Any:
    """A canonical deep copy (plain JSON types only), or refuse."""
    try:
        return json.loads(canonical_identity_json_v1({"value": value}))["value"]
    except (StrategyLabStudyError, TypeError, ValueError) as error:
        raise _refuse(code, error) from error


def _hash(payload: Mapping[str, Any], code: str) -> str:
    try:
        return identity_hash_v1(payload)
    except (StrategyLabStudyError, TypeError, ValueError) as error:
        raise _refuse(code, error) from error


def _canonical(value: object) -> str:
    return canonical_identity_json_v1({"value": value})


def _json(raw: object) -> Any:
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def _uuid(value: object, code: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as error:
        raise _refuse(code, error) from error


def _study_id(study_content_hash: str) -> UUID:
    """:attr:`StudySpecV1.study_id` from the study's content hash (pinned equal by a test)."""
    return uuid5(_STUDY_NAMESPACE, f"study:{study_content_hash}")


def _int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _refuse(code)
    return value


def _walk_micros(value: object, key: str = "") -> Iterator[int]:
    """Every integer fact whose key ends in ``_micros``, at any depth."""
    if isinstance(value, Mapping):
        for name, item in value.items():
            yield from _walk_micros(item, str(name))
    elif isinstance(value, list):
        for item in value:
            yield from _walk_micros(item, key)
    elif key.endswith("_micros") and isinstance(value, int) and not isinstance(value, bool):
        yield value


# ---------------------------------------------------------------------------
# Registry checks (shared)
# ---------------------------------------------------------------------------


def _cycle_holdout_start(cursor: _Cursor, cycle_id: str) -> datetime:
    if not isinstance(cycle_id, str) or not cycle_id.strip():
        raise _refuse(CYCLE_UNKNOWN)
    cursor.execute("SELECT holdout_start FROM strategy_lab_research_cycles WHERE cycle_id=%s", (cycle_id,))
    row = cursor.fetchone()
    if row is None:
        raise _refuse(CYCLE_UNKNOWN)
    return _aware(row[0])


def _registered_opening_end(cursor: _Cursor, cycle_id: str, as_of: datetime) -> datetime | None:
    """The cycle's holdout end if its opening was registered at or before ``as_of``."""
    cursor.execute("SELECT holdout_end_exclusive, opened_at FROM strategy_lab_holdout_openings WHERE cycle_id=%s",
                   (cycle_id,))
    row = cursor.fetchone()
    if row is None or _aware(row[1]) > as_of:
        return None
    return _aware(row[0])


def _check_candidate(cursor: _Cursor, *, cycle_id: str, study_id: UUID, trial_id: UUID,
                     study_content_hash: str | None, claimed: Sequence[str]) -> None:
    """The candidate is in the ledger and, where the R6 registry binds it, bound to this cycle."""
    cursor.execute("SELECT s.content_hash FROM strategy_lab_trials t JOIN strategy_lab_studies s "
                   "ON s.study_id = t.study_id WHERE t.trial_id=%s AND t.study_id=%s", (trial_id, study_id))
    row = cursor.fetchone()
    if row is None or (study_content_hash is not None and str(row[0]).strip() != study_content_hash):
        raise _refuse(CANDIDATE_NOT_IN_STRATEGY_LAB)
    _check_registry_binding(cursor, cycle_id=cycle_id, study_id=study_id, trial_id=trial_id, claimed=claimed)


def _check_registry_binding(cursor: _Cursor, *, cycle_id: str, study_id: UUID, trial_id: UUID,
                            claimed: Sequence[str]) -> None:
    cursor.execute("SELECT s.state, s.evidence_hash, v.cycle_id FROM strategy_lab_candidate_states s "
                   "JOIN strategy_lab_holdout_validations v ON v.validation_hash = s.evidence_hash "
                   "WHERE s.study_id=%s AND s.trial_id=%s ORDER BY s.recorded_at, s.evidence_hash",
                   (study_id, trial_id))
    rows = [(str(state), str(evidence).strip(), str(cycle)) for state, evidence, cycle in cursor.fetchall()]
    if any(state == _REJECTED_STATE for state, _, _ in rows):
        raise _refuse(CANDIDATE_REJECTED_IN_REGISTRY)
    if rows:
        ours = {evidence for _, evidence, cycle in rows if cycle == cycle_id}
        if not ours:
            raise _refuse(CANDIDATE_BOUND_TO_ANOTHER_CYCLE)
        if any(item not in ours for item in claimed):
            raise _refuse(CANDIDATE_AUTHORITY_EVIDENCE_DISAGREES_WITH_REGISTRY)
        return
    for item in claimed:
        cursor.execute("SELECT cycle_id FROM strategy_lab_holdout_validations WHERE validation_hash=%s", (item,))
        found = cursor.fetchone()
        if found is not None and str(found[0]) != cycle_id:
            raise _refuse(CANDIDATE_BOUND_TO_ANOTHER_CYCLE)


def _touches(low: int, high: int, *, holdout_start: int, holdout_end: int | None, closed: bool) -> bool:
    """Whether ``[low, high]`` (``closed``) or ``[low, high)`` meets ``[holdout_start, holdout_end)``."""
    reaches = high >= holdout_start if closed else high > holdout_start
    return reaches and (holdout_end is None or low < holdout_end)


def _holdout_refusal(holdout_end: int | None) -> PaperEvidenceStoreError:
    return _refuse(EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT if holdout_end is None
                   else EVIDENCE_TOUCHES_THE_OPENED_HOLDOUT_SPAN)


# ---------------------------------------------------------------------------
# Fill parity: verification
# ---------------------------------------------------------------------------


def _rederived_assessment(observations: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The report's ``summary`` and ``assessment`` from its observations (mirrors the parity module)."""
    outcomes = Counter(str(item["outcome"]) for item in observations)
    by_reason = Counter(str(reason) for item in observations for reason in item["reasons"])
    diverged = sorted({str(reason) for item in observations if item["outcome"] == _DIVERGED
                       for reason in item["reasons"]})
    unavailable = sorted({str(reason) for item in observations if item["outcome"] == _UNAVAILABLE
                          for reason in item["reasons"]})
    matched = [item for item in observations if item["kind"] == _KIND_SIGNAL and item["outcome"] == _MATCH]
    matched_filled = sum(1 for item in matched if item["facts"]["fill_both_filled"])
    fill_statuses = Counter(item["facts"]["dimensions"]["fill_eligibility"]["live"] for item in matched)
    if diverged:
        status, reasons = _FAIL, diverged
    elif unavailable or matched_filled == 0:
        status = _INCOMPLETE
        reasons = sorted(set(unavailable) | ({_NO_MATCHED_FILLED} if matched_filled == 0 else set()))
    else:
        status, reasons = _PASS, []
    summary = {"outcomes": dict(sorted(outcomes.items())), "reasons": dict(sorted(by_reason.items())),
               "matched_signals_with_both_fills_filled": matched_filled,
               "matched_signal_fill_statuses": dict(sorted(fill_statuses.items()))}
    return summary, {"status": status, "reasons": reasons}


def _verified_report_identity(report: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Re-derive a parity report: identity hash, report id, observations and assessment."""
    identity = getattr(report, "identity", None)
    content_hash = getattr(report, "content_hash", None)
    report_id = getattr(report, "report_id", None)
    observations = getattr(report, "observations", None)
    if not isinstance(identity, Mapping) or not isinstance(content_hash, str) or not isinstance(report_id, UUID) \
            or not isinstance(observations, tuple):
        raise _refuse(FILL_PARITY_REPORT_MALFORMED)
    plain = _plain(dict(identity), FILL_PARITY_REPORT_MALFORMED)
    if not isinstance(plain, dict):
        raise _refuse(FILL_PARITY_REPORT_MALFORMED)
    if plain.get("schema_version") != FILL_PARITY_SCHEMA_VERSION_V1:
        raise _refuse(UNSUPPORTED_FILL_PARITY_SCHEMA_VERSION)
    if plain.get("claim") != FILL_PARITY_CLAIM_V1 or plain.get("tolerance") != "NONE":
        raise _refuse(UNSUPPORTED_FILL_PARITY_CONTRACT)
    if _hash(plain, FILL_PARITY_REPORT_MALFORMED) != content_hash \
            or uuid5(_PARITY_NAMESPACE, f"report:{content_hash}") != report_id:
        raise _refuse(FILL_PARITY_REPORT_DOES_NOT_REDERIVE)
    observed: list[dict[str, Any]] = []
    hashes: list[str] = []
    for item in observations:
        obs_identity = getattr(item, "identity", None)
        obs_hash = getattr(item, "content_hash", None)
        if not isinstance(obs_identity, Mapping) or not isinstance(obs_hash, str):
            raise _refuse(FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE)
        obs_plain = _plain(dict(obs_identity), FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE)
        if not isinstance(obs_plain, dict) \
                or obs_plain.get("schema_version") != FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1 \
                or _hash(obs_plain, FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE) != obs_hash \
                or getattr(item, "observation_id", None) != uuid5(_PARITY_NAMESPACE, f"observation:{obs_hash}"):
            raise _refuse(FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE)
        observed.append(obs_plain)
        hashes.append(obs_hash)
    if hashes != plain.get("observations") or hashes != sorted(hashes):
        raise _refuse(FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE)
    try:
        summary, assessment = _rederived_assessment(observed)
    except (KeyError, TypeError) as error:
        raise _refuse(FILL_PARITY_ASSESSMENT_DOES_NOT_REDERIVE, error) from error
    if plain.get("summary") != summary or plain.get("assessment") != assessment:
        raise _refuse(FILL_PARITY_ASSESSMENT_DOES_NOT_REDERIVE)
    return plain, observed


def _gate(identity: Mapping[str, Any], code: str) -> tuple[str, datetime, datetime | None]:
    try:
        gate = identity["inputs"]["holdout"]
        cycle_id = gate["cycle_id"]
        start = _aware(datetime.fromisoformat(gate["holdout_start"]))
        raw_end = gate["holdout_end_exclusive"]
        end = None if raw_end is None else _aware(datetime.fromisoformat(raw_end))
    except (KeyError, TypeError, ValueError, PaperEvidenceStoreError) as error:
        raise _refuse(code, error) from error
    if not isinstance(cycle_id, str):
        raise _refuse(code)
    return cycle_id, start, end


def _parity_candidates(identity: Mapping[str, Any], code: str) -> list[dict[str, Any]]:
    candidates = identity.get("inputs", {}).get("candidates") if isinstance(identity.get("inputs"), Mapping) else None
    if not isinstance(candidates, list) or not candidates:
        raise _refuse(code)
    return candidates


def _parity_columns(identity: Mapping[str, Any], candidate: Mapping[str, Any], content_hash: str,
                    code: str) -> dict[str, Any]:
    """Every column of one candidate's row, derived from the report identity alone."""
    try:
        study_hash = str(candidate["study_content_hash"])
        trial_id = _uuid(candidate["trial_id"], code)
        symbol = candidate["symbol"]
        authority_hash = candidate["authority_evidence_hash"]
        inputs = identity["inputs"]
        window = inputs["live_windows"].get(symbol)
        cycle_id, holdout_start, holdout_end = _gate(identity, code)
        assessment = identity["assessment"]
        source_hashes = {"study_content_hash": study_hash, "authority_evidence_hash": authority_hash,
                         "cost_policy_hash": inputs["cost_policy_hash"],
                         "seal_content_hashes": list(inputs["seal_content_hashes"]),
                         "live_signal_hashes": list(inputs["live_signal_hashes"]),
                         "live_fill_hashes": list(inputs["live_fill_hashes"])}
        window_start = None if window is None else _instant(_int(window["first_bar_open_micros"], code))
        window_end = None if window is None else _instant(_int(window["last_bar_open_micros"], code))
    except (KeyError, TypeError, AttributeError) as error:
        raise _refuse(code, error) from error
    if not isinstance(symbol, str) or not isinstance(authority_hash, str) or len(study_hash) != 64:
        raise _refuse(code)
    study_id = _study_id(study_hash)
    return {
        "assessment_id": uuid5(_NAMESPACE, f"fill-parity:{content_hash}:{study_id}:{trial_id}"),
        "report_content_hash": content_hash,
        "report_id": uuid5(_PARITY_NAMESPACE, f"report:{content_hash}"),
        "schema_version": identity["schema_version"],
        "cycle_id": cycle_id,
        "study_id": study_id,
        "study_content_hash": study_hash,
        "trial_id": trial_id,
        "symbol": symbol,
        "authority_evidence_hash": authority_hash,
        "status": assessment["status"],
        "reasons": [str(reason) for reason in assessment["reasons"]],
        "window_start": window_start,
        "window_end": window_end,
        "holdout_start": holdout_start,
        "holdout_end_exclusive": holdout_end,
        "source_hashes": source_hashes,
        "identity": dict(identity),
    }


_PARITY_COLUMNS: Final = ("assessment_id", "report_content_hash", "report_id", "schema_version", "cycle_id",
                          "study_id", "study_content_hash", "trial_id", "symbol", "authority_evidence_hash",
                          "status", "reasons", "as_of", "window_start", "window_end", "holdout_start",
                          "holdout_end_exclusive", "source_hashes", "identity", "recorded_at")
_PARITY_CONTENT: Final = tuple(name for name in _PARITY_COLUMNS if name not in ("as_of", "recorded_at"))
_JSON_COLUMNS: Final = frozenset({"source_hashes", "identity", "bridge_hashes", "bridges", "evidence"})


def _normal(name: str, value: object) -> object:
    """A column value in a comparable form (JSON canonical, instants in UTC, hashes stripped)."""
    if name in _JSON_COLUMNS:
        return _canonical(_json(value))
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _same(expected: Mapping[str, Any], row: Mapping[str, Any], names: Sequence[str]) -> bool:
    try:
        return all(_normal(name, expected[name]) == _normal(name, row[name]) for name in names)
    except (StrategyLabStudyError, TypeError, ValueError):
        return False


def _stored_parity(row: Sequence[Any]) -> StoredFillParityV1:
    """Re-derive a stored row from its own identity; any disagreement raises."""
    if len(row) != len(_PARITY_COLUMNS):
        raise _refuse(STORED_FILL_PARITY_DOES_NOT_REDERIVE)
    stored = dict(zip(_PARITY_COLUMNS, row, strict=True))
    code = STORED_FILL_PARITY_DOES_NOT_REDERIVE
    identity = _json(stored["identity"])
    if not isinstance(identity, dict) or identity.get("schema_version") != FILL_PARITY_SCHEMA_VERSION_V1:
        raise _refuse(code)
    content_hash = str(stored["report_content_hash"]).strip()
    if _hash(identity, code) != content_hash:
        raise _refuse(code)
    match = [item for item in _parity_candidates(identity, code)
             if isinstance(item, Mapping) and str(_study_id(str(item.get("study_content_hash")))) ==
             str(stored["study_id"]) and str(item.get("trial_id")) == str(stored["trial_id"])]
    if len(match) != 1:
        raise _refuse(code)
    expected = _parity_columns(identity, match[0], content_hash, code)
    if not _same(expected, stored, _PARITY_CONTENT):
        raise _refuse(code)
    as_of, recorded_at = _aware(stored["as_of"]), _aware(stored["recorded_at"])
    if as_of > recorded_at or (expected["window_end"] is not None
                               and expected["window_end"] + timedelta(microseconds=_MINUTE_MICROS) > as_of):
        raise _refuse(code)
    return StoredFillParityV1(
        assessment_id=expected["assessment_id"], report_content_hash=content_hash, cycle_id=expected["cycle_id"],
        study_id=expected["study_id"], trial_id=expected["trial_id"], symbol=expected["symbol"],
        status=expected["status"], reasons=tuple(expected["reasons"]), as_of=as_of,
        window_start=expected["window_start"], window_end=expected["window_end"],
        source_hashes=expected["source_hashes"], identity=identity, recorded_at=recorded_at,
        report_id=expected["report_id"], schema_version=expected["schema_version"],
        study_content_hash=expected["study_content_hash"], authority_evidence_hash=expected["authority_evidence_hash"],
        holdout_start=expected["holdout_start"], holdout_end_exclusive=expected["holdout_end_exclusive"])


def _parity_admitted(identity: Mapping[str, Any], observed: Sequence[Mapping[str, Any]], *, start: datetime,
                     end: datetime | None) -> None:
    """Every observed bar / signal and every live window is admitted by the recorded gate."""
    start_micros = _micros(start)
    end_micros = None if end is None else _micros(end)
    for window in identity["inputs"]["live_windows"].values():
        if _touches(int(window["first_bar_open_micros"]), int(window["last_bar_open_micros"]),
                    holdout_start=start_micros, holdout_end=end_micros, closed=True):
            raise _holdout_refusal(end_micros)
    for item in observed:
        for name in ("bar_open_micros", "signal_bar_open_micros"):
            opened = item["key"].get(name)
            if opened is not None and _touches(int(opened), int(opened), holdout_start=start_micros,
                                               holdout_end=end_micros, closed=True):
                raise _holdout_refusal(end_micros)


# ---------------------------------------------------------------------------
# Fill parity: write and read
# ---------------------------------------------------------------------------


def record_fill_parity_v1(cursor: _Cursor, report: FillParityReportV1, *, cycle_id: str, as_of: datetime,
                          recorded_at: datetime) -> tuple[StoredFillParityV1, ...]:
    """Store one verified parity report once per candidate it names (sorted by study, trial).

    ``as_of`` is the instant the report's evidence was known (at or after every
    fact it holds); ``recorded_at`` the store's instant. Runs in the caller's
    transaction; returns the stored rows (an identical earlier row unchanged).
    """
    known, recorded = _instants(as_of, recorded_at)
    identity, observed = _verified_report_identity(report)
    content_hash = str(report.content_hash)
    candidates = _parity_candidates(identity, FILL_PARITY_REPORT_MALFORMED)
    columns = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise _refuse(FILL_PARITY_REPORT_MALFORMED)
        if candidate.get("authority") != _AUTHORITY_INCUBATING:
            raise _refuse(FILL_PARITY_CANDIDATE_NOT_INCUBATING)
        columns.append(_parity_columns(identity, candidate, content_hash, FILL_PARITY_REPORT_MALFORMED))
    if len({(item["study_id"], item["trial_id"]) for item in columns}) != len(columns):
        raise _refuse(FILL_PARITY_CANDIDATE_REPEATED)
    # Look-ahead: nothing the report holds may lie after its own as_of.
    known_micros = _micros(known)
    try:
        windows = list(identity["inputs"]["live_windows"].values())
        last_complete = [int(window["last_bar_open_micros"]) + _MINUTE_MICROS for window in windows]
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise _refuse(FILL_PARITY_REPORT_MALFORMED, error) from error
    if any(value > known_micros for value in last_complete) or any(
            value > known_micros for item in observed for value in _walk_micros(item)):
        raise _refuse(EVIDENCE_FACTS_AFTER_AS_OF)

    # Cycle and the recorded holdout gate, against the registry as known at as_of.
    gate_cycle, gate_start, gate_end = _gate(identity, FILL_PARITY_REPORT_MALFORMED)
    holdout_start = _cycle_holdout_start(cursor, cycle_id)
    if gate_cycle != cycle_id or gate_start != holdout_start:
        raise _refuse(HOLDOUT_GATE_NOT_OF_THIS_CYCLE)
    if gate_end is not None and _registered_opening_end(cursor, cycle_id, known) != gate_end:
        raise _refuse(HOLDOUT_GATE_OPENING_NOT_REGISTERED)
    _parity_admitted(identity, observed, start=gate_start, end=gate_end)
    for item in columns:
        _check_candidate(cursor, cycle_id=cycle_id, study_id=item["study_id"], trial_id=item["trial_id"],
                         study_content_hash=item["study_content_hash"], claimed=[item["authority_evidence_hash"]])

    stored = []
    for item in sorted(columns, key=lambda value: (str(value["study_id"]), str(value["trial_id"]))):
        stored.append(_insert_parity(cursor, {**item, "as_of": known, "recorded_at": recorded}))
    return tuple(stored)


def _insert_parity(cursor: _Cursor, values: Mapping[str, Any]) -> StoredFillParityV1:
    select = (f"SELECT {', '.join(_PARITY_COLUMNS)} FROM paper_fill_parity_assessments "  # nosec B608
              "WHERE report_content_hash=%s AND study_id=%s AND trial_id=%s")
    key = (values["report_content_hash"], values["study_id"], values["trial_id"])
    cursor.execute(select, key)
    existing = cursor.fetchone()
    if existing is None:
        params = tuple(json.dumps(values[name], sort_keys=True) if name in _JSON_COLUMNS else values[name]
                       for name in _PARITY_COLUMNS)
        placeholders = ", ".join("%s::jsonb" if name in _JSON_COLUMNS else "%s" for name in _PARITY_COLUMNS)
        cursor.execute(f"INSERT INTO paper_fill_parity_assessments ({', '.join(_PARITY_COLUMNS)}) "  # nosec B608
                       f"VALUES ({placeholders}) ON CONFLICT DO NOTHING RETURNING {', '.join(_PARITY_COLUMNS)}",
                       params)
        inserted = cursor.fetchone()
        if inserted is not None:
            return _stored_parity(inserted)
        cursor.execute(select, key)
        existing = cursor.fetchone()
        if existing is None:  # pragma: no cover - a conflict always leaves the row it conflicted with
            raise _refuse(CONFLICTING_DUPLICATE_FILL_PARITY)
    row = _stored_parity(existing)
    if not _same(values, dict(zip(_PARITY_COLUMNS, existing, strict=True)), _PARITY_CONTENT):
        raise _refuse(CONFLICTING_DUPLICATE_FILL_PARITY)
    return row


def read_fill_parity_v1(cursor: _Cursor, *, cycle_id: str, study_id: UUID, trial_id: UUID,
                        as_of: datetime) -> StoredFillParityV1 | None:
    """The latest parity assessment of the candidate known at ``as_of``; ``None`` when ABSENT."""
    known = _aware(as_of)
    cursor.execute(f"SELECT {', '.join(_PARITY_COLUMNS)} FROM paper_fill_parity_assessments "  # nosec B608
                   "WHERE cycle_id=%s AND study_id=%s AND trial_id=%s AND recorded_at <= %s AND as_of <= %s "
                   "ORDER BY as_of, recorded_at, assessment_id",
                   (cycle_id, _uuid(study_id, CANDIDATE_NOT_IN_STRATEGY_LAB),
                    _uuid(trial_id, CANDIDATE_NOT_IN_STRATEGY_LAB), known, known))
    rows = [_stored_parity(row) for row in cursor.fetchall()]
    for row in rows:
        if (row.cycle_id, str(row.study_id), str(row.trial_id)) != (cycle_id, str(study_id), str(trial_id)) \
                or row.recorded_at > known or row.as_of > known:
            raise _refuse(STORED_FILL_PARITY_DOES_NOT_REDERIVE)
    return rows[-1] if rows else None


# ---------------------------------------------------------------------------
# Funding evidence: verification
# ---------------------------------------------------------------------------


_FUNDING_COLUMNS: Final = ("record_id", "evidence_hash", "schema_version", "cycle_id", "study_id", "trial_id",
                           "symbol", "state", "completeness_blockers", "economic_blockers", "as_of", "window_start",
                           "window_end", "bridge_hashes", "bridges", "evidence_validation_hash", "evidence",
                           "recorded_at")
_FUNDING_CONTENT: Final = tuple(name for name in _FUNDING_COLUMNS if name not in ("as_of", "recorded_at"))


def _funding_columns(section: object, bridges: Sequence[Mapping[str, Any]], evidence_validation_hash: object, *,
                     cycle_id: str,
                     as_of: datetime, code: str) -> dict[str, Any]:
    """Every column of a funding record, after re-deriving the section from its bridges and holdings."""
    if not isinstance(section, Mapping):
        raise _refuse(FUNDING_SECTION_MALFORMED if code != STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE else code)
    body = _plain(dict(section), FUNDING_SECTION_MALFORMED)
    if body.get("schema_version") != PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1:
        raise _refuse(UNSUPPORTED_FUNDING_EVIDENCE_SCHEMA_VERSION)
    state = body.get("state")
    if state == STATE_ECONOMICALLY_COSTABLE_RESERVED:
        raise _refuse(FUNDING_STATE_ECONOMICALLY_COSTABLE_REFUSED)
    if state not in FUNDING_EVIDENCE_STATES_V1:
        raise _refuse(FUNDING_STATE_UNKNOWN)
    if body.get("costable") is not False:
        raise _refuse(FUNDING_SECTION_CLAIMS_COSTABLE)
    if not isinstance(body.get("economic_blockers"), list) or not body["economic_blockers"]:
        raise _refuse(FUNDING_ECONOMIC_BLOCKERS_MISSING)
    claimed = body.pop("evidence_hash", None)
    if _hash(body, code) != claimed:
        raise _refuse(code)
    full = {**body, "evidence_hash": claimed}
    try:
        candidate = body["candidate"]
        study_text, trial_text, symbol = str(candidate["study_id"]), str(candidate["trial_id"]), candidate["symbol"]
        binding_hash = candidate["validation_hash"]
        holdings: list[dict[str, Any]] = [{"direction": int(item["direction"]), "start_micros": int(item["start_micros"]),
                     "end_micros": int(item["end_micros"]), "end_kind": str(item["end_kind"])}
                    for item in body["holding_intervals"]]
        completeness = [str(item) for item in body["completeness_blockers"]]
        economic = [str(item) for item in body["economic_blockers"]]
        pairs = [(str(item["content_hash"]), item["identity"]) for item in bridges]
    except (KeyError, TypeError, ValueError) as error:
        raise _refuse(code, error) from error
    if not isinstance(symbol, str) or (binding_hash is not None and not isinstance(binding_hash, str)) \
            or (evidence_validation_hash is not None and not isinstance(evidence_validation_hash, str)):
        raise _refuse(code)
    if state == STATE_NO_FUNDING_EVIDENCE and not pairs and evidence_validation_hash is not None:
        raise _refuse(code)
    if pairs and state == STATE_NO_FUNDING_EVIDENCE:
        raise _refuse(code)
    if not pairs and state != STATE_NO_FUNDING_EVIDENCE:
        raise _refuse(FUNDING_SECTION_WITHOUT_ITS_EVIDENCE if code != STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE
                      else code)
    # Look-ahead: every bridge and every holding is known by as_of; an open holding ends at it.
    known_micros = _micros(as_of)
    try:
        bridge_as_of = [int(identity["as_of_micros"]) for _, identity in pairs]
    except (KeyError, TypeError, ValueError) as error:
        raise _refuse(code, error) from error
    if any(value > known_micros for value in bridge_as_of) or any(
            item["end_micros"] > known_micros for item in holdings):
        raise _refuse(EVIDENCE_FACTS_AFTER_AS_OF)
    if any(item["end_kind"] == HOLDING_OPEN_AT_AS_OF and item["end_micros"] != known_micros for item in holdings):
        raise _refuse(FUNDING_AS_OF_IS_NOT_THE_REPORT_AS_OF)
    try:
        rebuilt = None if not pairs else candidate_funding_evidence_v1(
            study_id=study_text, trial_id=trial_text, symbol=symbol,
            bridges=[_StoredBridgeV1(identity, content_hash) for content_hash, identity in pairs],
            validation_hash=evidence_validation_hash)
        rederived = funding_evidence_section_v1(
            rebuilt, study_id=study_text, trial_id=trial_text, symbol=symbol, holdings=holdings,
            fills_skipped=int(FILLS_SKIPPED_UNKNOWN_POSITION in completeness), as_of_micros=known_micros,
            binding_validation_hash=binding_hash, fee_schedule_verified=MISSING_VERIFIED_FEE_SCHEDULE not in economic)
        same = _canonical(rederived) == _canonical(full)
    except (PaperFundingEvidenceError, StrategyLabStudyError, KeyError, TypeError, ValueError) as error:
        raise _refuse(code, error) from error
    if not same:
        raise _refuse(code)
    study_id, trial_id = _uuid(study_text, code), _uuid(trial_text, code)
    spans: list[tuple[int, int, bool]] = [(int(identity["start_ms"]) * 1000, int(identity["end_ms_exclusive"]) * 1000,
                                           False) for _, identity in pairs]
    spans += [(item["start_micros"], item["end_micros"], True) for item in holdings]
    return {
        "record_id": uuid5(_NAMESPACE, f"funding-evidence:{cycle_id}:{claimed}"),
        "evidence_hash": claimed,
        "schema_version": body["schema_version"],
        "cycle_id": cycle_id,
        "study_id": study_id,
        "trial_id": trial_id,
        "symbol": symbol,
        "state": state,
        "completeness_blockers": completeness,
        "economic_blockers": economic,
        "window_start": None if not spans else _instant(min(low for low, _, _ in spans)),
        "window_end": None if not spans else _instant(max(high for _, high, _ in spans)),
        "bridge_hashes": {content_hash: str(identity["funding_window"]["content_hash"])
                          for content_hash, identity in pairs},
        "bridges": [{"content_hash": content_hash, "identity": identity} for content_hash, identity in pairs],
        "evidence_validation_hash": evidence_validation_hash,
        "evidence": full,
        "_spans": spans,
        "_claimed": [item for item in (binding_hash, evidence_validation_hash) if item is not None],
    }


def _stored_funding(row: Sequence[Any]) -> StoredFundingEvidenceV1:
    code = STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE
    if len(row) != len(_FUNDING_COLUMNS):
        raise _refuse(code)
    stored = dict(zip(_FUNDING_COLUMNS, row, strict=True))
    as_of, recorded_at = _aware(stored["as_of"]), _aware(stored["recorded_at"])
    if as_of > recorded_at:
        raise _refuse(code)
    bridges = _json(stored["bridges"])
    if not isinstance(bridges, list):
        raise _refuse(code)
    validation = stored["evidence_validation_hash"]
    try:
        expected = _funding_columns(_json(stored["evidence"]), bridges,
                                    None if validation is None else str(validation), cycle_id=str(stored["cycle_id"]),
                                    as_of=as_of, code=code)
    except PaperEvidenceStoreError as error:
        raise _refuse(code, error) from error
    if not _same(expected, stored, _FUNDING_CONTENT):
        raise _refuse(code)
    return StoredFundingEvidenceV1(
        record_id=expected["record_id"], evidence_hash=expected["evidence_hash"], cycle_id=expected["cycle_id"],
        study_id=expected["study_id"], trial_id=expected["trial_id"], symbol=expected["symbol"],
        state=expected["state"], completeness_blockers=tuple(expected["completeness_blockers"]),
        economic_blockers=tuple(expected["economic_blockers"]), as_of=as_of, window_start=expected["window_start"],
        window_end=expected["window_end"], bridge_hashes=expected["bridge_hashes"], evidence=expected["evidence"],
        recorded_at=recorded_at, schema_version=expected["schema_version"],
        evidence_validation_hash=expected["evidence_validation_hash"])


def _holding_key(item: Mapping[str, Any]) -> tuple[int, int, int, str]:
    return (int(item["direction"]), int(item["start_micros"]), int(item["end_micros"]), str(item["end_kind"]))


def _check_holdings_against_stored_fills(cursor: _Cursor, *, cycle_id: str, as_of: datetime,
                                         section: Mapping[str, Any], bridges: Sequence[Mapping[str, Any]]) -> None:
    """The section's holdings and skipped-fill fact are the stored fills' own, as known at ``as_of``.

    The cycle's paper fills are loaded and re-derived by the loaders the terminal read
    model uses (:func:`~trade_platform.paper_account_v1.cycle_fill_rows_v1`, the cycle's
    cost policy and registry resolution); only fills recorded at or before ``as_of`` count.
    The holdings come from :func:`~trade_platform.paper_incubation_report_v1.incubation_report_v1`
    itself (no accounting here): the section's bridges are attached without a validation
    binding, which changes no holding. A section binding no bridge states no holding; its
    fills are still loaded and must re-derive.
    """
    from .paper_account_v1 import cycle_cost_policy_v1, cycle_fill_rows_v1, fills_signals_from_rows
    from .paper_incubation_registry_v1 import (
        IncubationRegistryError,
        resolve_incubation_bindings_v1,
    )
    from .paper_incubation_report_v1 import incubation_report_v1

    known_micros = _micros(as_of)
    candidate = section["candidate"]
    study_id, trial_id, symbol = str(candidate["study_id"]), str(candidate["trial_id"]), str(candidate["symbol"])
    try:
        cost = cycle_cost_policy_v1(cursor, cycle_id)
        try:
            resolution: Any = resolve_incubation_bindings_v1(cursor, cycle_id=cycle_id)
        except IncubationRegistryError:
            resolution = None  # as the read model: an unresolved registry binds nothing, holdings are unchanged
        fill_rows, signal_rows = cycle_fill_rows_v1(cursor, cycle_id)
        identities, _ = fills_signals_from_rows(fill_rows, signal_rows)
        hashes = [str(row[0]).strip() for row in fill_rows]
        recorded: dict[str, datetime] = {}
        if hashes:
            cursor.execute("SELECT content_hash, recorded_at FROM paper_incubation_fills WHERE content_hash = ANY(%s)",
                           (hashes,))
            recorded = {str(row[0]).strip(): _aware(row[1]) for row in cursor.fetchall()}
        fills = [identity for content_hash, identity in zip(hashes, identities, strict=True)
                 if recorded[content_hash] <= as_of and int(identity["decided_at_micros"]) <= known_micros]
        report = incubation_report_v1(fills, cost, as_of=as_of, resolution=resolution)
    except (PaperEvidenceStoreError, ValueError, KeyError, TypeError) as error:
        raise _refuse(STORED_FILLS_DO_NOT_REDERIVE, error) from error
    if not bridges:
        return
    reported = any((str(item["study_id"]), str(item["trial_id"]), str(item["symbol"])) == (study_id, trial_id, symbol)
                   for item in report["candidates"])
    derived_holdings: list[tuple[int, int, int, str]] = []
    derived_skipped = False
    if reported:
        try:
            proxy = candidate_funding_evidence_v1(
                study_id=study_id, trial_id=trial_id, symbol=symbol,
                bridges=[_StoredBridgeV1(item["identity"], str(item["content_hash"])) for item in bridges])
            with_funding = incubation_report_v1(fills, cost, as_of=as_of, resolution=resolution, funding=[proxy])
            (entry,) = [item for item in with_funding["candidates"]
                        if (str(item["study_id"]), str(item["trial_id"])) == (study_id, trial_id)]
            derived = entry["funding_evidence"]
            derived_holdings = [_holding_key(item) for item in derived["holding_intervals"]]
            derived_skipped = FILLS_SKIPPED_UNKNOWN_POSITION in derived["completeness_blockers"]
        except (PaperFundingEvidenceError, ValueError, KeyError, TypeError) as error:
            raise _refuse(STORED_FILLS_DO_NOT_REDERIVE, error) from error
    try:
        stated = [_holding_key(item) for item in section["holding_intervals"]]
        skipped = FILLS_SKIPPED_UNKNOWN_POSITION in section["completeness_blockers"]
    except (KeyError, TypeError, ValueError) as error:
        raise _refuse(FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS, error) from error
    if (stated, skipped) != (derived_holdings, derived_skipped):
        raise _refuse(FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS)


# ---------------------------------------------------------------------------
# Funding evidence: write and read
# ---------------------------------------------------------------------------


def record_funding_evidence_v1(cursor: _Cursor, evidence: CandidateFundingEvidenceV1 | None, *,
                               section: Mapping[str, Any], cycle_id: str, as_of: datetime,
                               recorded_at: datetime) -> StoredFundingEvidenceV1:
    """Store one candidate's verified ``funding_evidence`` section with the bridges it binds.

    ``section`` is the candidate's section of
    :func:`~trade_platform.paper_incubation_report_v1.incubation_report_v1` built at
    ``as_of`` (the report's reading instant); ``evidence`` is the issued
    :class:`CandidateFundingEvidenceV1` that section was built from, or ``None`` for
    ``NO_FUNDING_EVIDENCE_SUPPLIED``. Runs in the caller's transaction.
    """
    known, recorded = _instants(as_of, recorded_at)
    if evidence is not None and not isinstance(evidence, CandidateFundingEvidenceV1):
        raise _refuse(FUNDING_EVIDENCE_NOT_ISSUED)
    if not isinstance(section, Mapping):
        raise _refuse(FUNDING_SECTION_MALFORMED)
    candidate = section.get("candidate")
    if evidence is not None and (not isinstance(candidate, Mapping) or (
            evidence.study_id, evidence.trial_id, evidence.symbol) != (
            str(candidate.get("study_id")), str(candidate.get("trial_id")), candidate.get("symbol"))):
        raise _refuse(FUNDING_EVIDENCE_NOT_OF_THIS_CANDIDATE)
    bridges = [] if evidence is None else [
        {"content_hash": content_hash, "identity": _plain(dict(identity), FUNDING_SECTION_DOES_NOT_REDERIVE)}
        for content_hash, identity in evidence.bridges]
    values = _funding_columns(section, bridges, None if evidence is None else evidence.validation_hash,
                              cycle_id=cycle_id, as_of=known, code=FUNDING_SECTION_DOES_NOT_REDERIVE)

    holdout_start = _cycle_holdout_start(cursor, cycle_id)
    end = _registered_opening_end(cursor, cycle_id, known)
    end_micros = None if end is None else _micros(end)
    for low, high, closed in values.pop("_spans"):
        if _touches(low, high, holdout_start=_micros(holdout_start), holdout_end=end_micros, closed=closed):
            raise _holdout_refusal(end_micros)
    _check_candidate(cursor, cycle_id=cycle_id, study_id=values["study_id"], trial_id=values["trial_id"],
                     study_content_hash=None, claimed=values.pop("_claimed"))
    _check_holdings_against_stored_fills(cursor, cycle_id=cycle_id, as_of=known, section=values["evidence"],
                                         bridges=values["bridges"])
    return _insert_funding(cursor, {**values, "as_of": known, "recorded_at": recorded})


def _insert_funding(cursor: _Cursor, values: Mapping[str, Any]) -> StoredFundingEvidenceV1:
    select = (f"SELECT {', '.join(_FUNDING_COLUMNS)} FROM paper_funding_evidence_records "  # nosec B608
              "WHERE cycle_id=%s AND evidence_hash=%s")
    key = (values["cycle_id"], values["evidence_hash"])
    cursor.execute(select, key)
    existing = cursor.fetchone()
    if existing is None:
        params = tuple(json.dumps(values[name], sort_keys=True) if name in _JSON_COLUMNS else values[name]
                       for name in _FUNDING_COLUMNS)
        placeholders = ", ".join("%s::jsonb" if name in _JSON_COLUMNS else "%s" for name in _FUNDING_COLUMNS)
        cursor.execute(f"INSERT INTO paper_funding_evidence_records ({', '.join(_FUNDING_COLUMNS)}) "  # nosec B608
                       f"VALUES ({placeholders}) ON CONFLICT DO NOTHING RETURNING {', '.join(_FUNDING_COLUMNS)}",
                       params)
        inserted = cursor.fetchone()
        if inserted is not None:
            return _stored_funding(inserted)
        cursor.execute(select, key)
        existing = cursor.fetchone()
        if existing is None:  # pragma: no cover - a conflict always leaves the row it conflicted with
            raise _refuse(CONFLICTING_DUPLICATE_FUNDING_EVIDENCE)
    row = _stored_funding(existing)
    if not _same(values, dict(zip(_FUNDING_COLUMNS, existing, strict=True)), _FUNDING_CONTENT):
        raise _refuse(CONFLICTING_DUPLICATE_FUNDING_EVIDENCE)
    return row


def read_funding_evidence_v1(cursor: _Cursor, *, cycle_id: str, study_id: UUID, trial_id: UUID,
                             as_of: datetime) -> StoredFundingEvidenceV1 | None:
    """The latest funding evidence of the candidate known at ``as_of``; ``None`` when ABSENT."""
    known = _aware(as_of)
    cursor.execute(f"SELECT {', '.join(_FUNDING_COLUMNS)} FROM paper_funding_evidence_records "  # nosec B608
                   "WHERE cycle_id=%s AND study_id=%s AND trial_id=%s AND recorded_at <= %s AND as_of <= %s "
                   "ORDER BY as_of, recorded_at, record_id",
                   (cycle_id, _uuid(study_id, CANDIDATE_NOT_IN_STRATEGY_LAB),
                    _uuid(trial_id, CANDIDATE_NOT_IN_STRATEGY_LAB), known, known))
    raw = cursor.fetchall()
    rows = [_stored_funding(row) for row in raw]
    for row in rows:
        if (row.cycle_id, str(row.study_id), str(row.trial_id)) != (cycle_id, str(study_id), str(trial_id)) \
                or row.recorded_at > known or row.as_of > known:
            raise _refuse(STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE)
    bridges_at = _FUNDING_COLUMNS.index("bridges")
    for row, stored in zip(rows, raw, strict=True):
        # Every row's holdings are re-derived from the stored fills it was recorded against.
        _check_holdings_against_stored_fills(cursor, cycle_id=row.cycle_id, as_of=row.as_of, section=row.evidence,
                                             bridges=_json(stored[bridges_at]))
    return rows[-1] if rows else None


__all__ = [
    "AS_OF_AFTER_RECORDED_AT",
    "CANDIDATE_AUTHORITY_EVIDENCE_DISAGREES_WITH_REGISTRY",
    "CANDIDATE_BOUND_TO_ANOTHER_CYCLE",
    "CANDIDATE_NOT_IN_STRATEGY_LAB",
    "CANDIDATE_REJECTED_IN_REGISTRY",
    "CONFLICTING_DUPLICATE_FILL_PARITY",
    "CONFLICTING_DUPLICATE_FUNDING_EVIDENCE",
    "CYCLE_UNKNOWN",
    "EVIDENCE_FACTS_AFTER_AS_OF",
    "EVIDENCE_TOUCHES_THE_OPENED_HOLDOUT_SPAN",
    "EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT",
    "FILL_PARITY_ASSESSMENT_DOES_NOT_REDERIVE",
    "FILL_PARITY_CANDIDATE_NOT_INCUBATING",
    "FILL_PARITY_CANDIDATE_REPEATED",
    "FILL_PARITY_CLAIM_V1",
    "FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE",
    "FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1",
    "FILL_PARITY_REPORT_DOES_NOT_REDERIVE",
    "FILL_PARITY_REPORT_MALFORMED",
    "FILL_PARITY_SCHEMA_VERSION_V1",
    "FUNDING_AS_OF_IS_NOT_THE_REPORT_AS_OF",
    "FUNDING_ECONOMIC_BLOCKERS_MISSING",
    "FUNDING_EVIDENCE_NOT_ISSUED",
    "FUNDING_EVIDENCE_NOT_OF_THIS_CANDIDATE",
    "FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS",
    "FUNDING_SECTION_CLAIMS_COSTABLE",
    "FUNDING_SECTION_DOES_NOT_REDERIVE",
    "FUNDING_SECTION_MALFORMED",
    "FUNDING_SECTION_WITHOUT_ITS_EVIDENCE",
    "FUNDING_STATE_ECONOMICALLY_COSTABLE_REFUSED",
    "FUNDING_STATE_UNKNOWN",
    "HOLDOUT_GATE_NOT_OF_THIS_CYCLE",
    "HOLDOUT_GATE_OPENING_NOT_REGISTERED",
    "PARITY_STATUSES_V1",
    "STORED_FILLS_DO_NOT_REDERIVE",
    "STORED_FILL_PARITY_DOES_NOT_REDERIVE",
    "STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE",
    "TIMESTAMP_NOT_TIMEZONE_AWARE",
    "UNSUPPORTED_FILL_PARITY_CONTRACT",
    "UNSUPPORTED_FILL_PARITY_SCHEMA_VERSION",
    "UNSUPPORTED_FUNDING_EVIDENCE_SCHEMA_VERSION",
    "PaperEvidenceStoreError",
    "StoredFillParityV1",
    "StoredFundingEvidenceV1",
    "read_fill_parity_v1",
    "read_funding_evidence_v1",
    "record_fill_parity_v1",
    "record_funding_evidence_v1",
]
