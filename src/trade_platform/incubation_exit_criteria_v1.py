"""Phase R10 -- owner-gated forward incubation exit criteria and a read-only exit-readiness view.

``RESEARCH_ONLY``, NON-promoting. The R6 preregistration
(:mod:`trade_platform.strategy_lab_validation_v1`) carries the owner's *holdout*
acceptance criteria and the incubation length; nothing records what the
*forward* paper incubation must show before the owner may decide on a candidate.
This module is that contract: a versioned, content-addressed exit-criteria packet
(:class:`ExitCriteriaPacketV1`), an append-only store (migration
``20261017_0065``), a registry resolver, and a read-only evaluation layered on
:func:`trade_platform.candidate_lifecycle_readiness_v1.assess_candidate_readiness_v1`.

The packet
----------
It binds one candidate -- research cycle, study, trial, the preregistration hash
it was preregistered under and the holdout symbol -- and the forward evidence
identities its forward fills must carry: the OR-6 cost policy hash (verified fee
schedule) and the OR-11 account policy hash. It records the owner's slots:

* forward performance criteria (``metric comparator threshold`` on the closed
  :data:`FORWARD_METRICS_V1`, evaluated net only);
* minimum evidence coverage (evidenced fraction of the calendar span since the
  candidate became ``INCUBATING``) and minimum forward round trips;
* tolerated chain breaks, unfilled signals and fills skipped at an unknown
  position (each an integer ``>= 0``);
* the required cost and account policy hashes (OR-6 / OR-11).

Nothing is defaulted. A missing slot is one ``MISSING_OWNER_*`` reason and the
packet stays ``DRAFT``; it is ``AUTHORIZED`` only when every slot is set *and*
``authorized_by`` / ``authorized_on`` are present. A value without an
authorization is refused at construction, never stored as a proposal, and the
evaluation treats every threshold of a ``DRAFT`` packet as ``UNRESOLVED``.
Contradictory packets (a metric bounded into an empty range, a duplicate
criterion, a coverage outside ``(0, 1]``, half an authorization, an
authorization of nothing) are refused. The evidence inputs in
:data:`REQUIRED_FORWARD_EVIDENCE_V1` are part of every identity and cannot be
waived by the owner in v1: forward settled funding, runner continuity,
live/replay fill parity, a verified fee schedule and an active OR-11 policy.

The store and the resolver
--------------------------
:class:`PostgresExitCriteriaRegistryV1` records a packet only for a recorded
preregistration of the same cycle and study that names the trial, idempotently
by content hash; at most one ``AUTHORIZED`` packet per candidate and cycle
(partial unique index), so a conflicting second authorization is refused; rows
are immutable and ``recorded_at`` is the database's clock.
:func:`resolve_exit_criteria_v1` re-derives every stored row (hash, schema,
columns vs identity, construction rules) and issues the only
:class:`RegisteredExitCriteriaV1`; a packet built in memory never satisfies the
evaluation.

Exit readiness (:func:`assess_exit_readiness_v1`)
-------------------------------------------------
Requires the cycle's registry-resolved OR-7 binding
(:mod:`trade_platform.paper_incubation_registry_v1`); a caller-supplied binding
cannot be passed and a stored chain that is not the registry's is refused as
stale. With an authorized packet the readiness module's
``MISSING_OWNER_FORWARD_INCUBATION_EXIT_CRITERIA`` and gap-tolerance blockers
are replaced by checks against the authorized values; every other blocker
stays. Criteria recorded after the candidate became ``INCUBATING`` are an
OR-7 finding (they could have seen forward evidence). Runner continuity has no
merged contract yet, so it is always ``MISSING`` here, and the
``EXIT_EVIDENCE_COMPLETE_FOR_OWNER_DECISION`` stage is unreachable in v1. Even
that stage would only say the owner's decision has its evidence: nothing here
judges pass/fail, records a state, reaches ``PROFESSIONALLY_VALIDATED`` or
activates an account.

Import-light (no numpy/pyarrow): the protected API may serve it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .activation_readiness_v1 import OR6, OR7, OR11, gate_of
from .candidate_lifecycle_readiness_v1 import (
    NEED_COST_COMPLETE,
    NEED_EXIT_CRITERIA,
    NEED_FEE_SCHEDULE,
    NEED_FORWARD_FUNDING,
    NEED_LIVENESS,
    NEED_PARITY,
    PROMOTION_V1,
    STAGE_ELIGIBLE,
    STAGE_EVIDENCE_INCONSISTENT,
    STAGE_FORWARD_EVIDENCE_INCOMPLETE,
    STAGE_INCUBATION_EVIDENCE_FAILED,
    STAGE_INCUBATION_INCOMPLETE,
    CandidateEvidenceV1,
    CandidateReadinessV1,
    assess_candidate_readiness_v1,
)
from .paper_incubation_report_v1 import (
    BINDING_SOURCE_REGISTRY_V1,
    IncubationBindingV1,
    IncubationResolutionV1,
)
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

if TYPE_CHECKING:
    from .persistence import PostgresDatabase

SCHEMA_VERSION_V1: Final = "incubation-exit-criteria-v1"
ASSESSMENT_SCHEMA_VERSION_V1: Final = "incubation-exit-readiness-v1"
STATUS_DRAFT: Final = "DRAFT"
STATUS_AUTHORIZED: Final = "AUTHORIZED"
UNRESOLVED_VALUE: Final = "UNRESOLVED"

#: Net forward metrics a performance criterion may name. Extending it is a new version.
FORWARD_METRICS_V1: Final = ("net_total_return", "max_drawdown")
COMPARATORS_V1: Final = (">", ">=", "<", "<=")

EVIDENCE_FORWARD_FUNDING: Final = "FORWARD_SETTLED_FUNDING"
EVIDENCE_RUNNER_CONTINUITY: Final = "RUNNER_CONTINUITY"
EVIDENCE_PARITY: Final = "LIVE_REPLAY_FILL_PARITY"
EVIDENCE_FEES: Final = "VERIFIED_FEE_SCHEDULE_OR_6"
EVIDENCE_RISK: Final = "ACTIVE_ACCOUNT_RISK_POLICY_OR_11"
#: Required in every packet; not owner-waivable in v1.
REQUIRED_FORWARD_EVIDENCE_V1: Final = (EVIDENCE_FORWARD_FUNDING, EVIDENCE_RUNNER_CONTINUITY, EVIDENCE_PARITY,
                                       EVIDENCE_FEES, EVIDENCE_RISK)
INPUT_PRESENT: Final = "PRESENT"
INPUT_MISSING: Final = "MISSING"
INPUT_FAILED: Final = "FAILED"

UNRESOLVED_PERFORMANCE: Final = "MISSING_OWNER_FORWARD_PERFORMANCE_CRITERIA_OR_7"
UNRESOLVED_COVERAGE: Final = "MISSING_OWNER_MINIMUM_EVIDENCE_COVERAGE_OR_7"
UNRESOLVED_ROUND_TRIPS: Final = "MISSING_OWNER_MINIMUM_FORWARD_ROUND_TRIPS_OR_7"
UNRESOLVED_CHAIN_BREAKS: Final = "MISSING_OWNER_CHAIN_BREAK_TOLERANCE_OR_7"
UNRESOLVED_UNFILLED: Final = "MISSING_OWNER_UNFILLED_SIGNAL_TOLERANCE_OR_7"
UNRESOLVED_SKIPPED: Final = "MISSING_OWNER_SKIPPED_FILL_TOLERANCE_OR_7"
UNRESOLVED_COST_POLICY: Final = "MISSING_VERIFIED_COST_POLICY_BINDING_OR_6"
UNRESOLVED_ACCOUNT_POLICY: Final = "MISSING_ACCOUNT_RISK_POLICY_BINDING_OR_11"
UNRESOLVED_AUTHORIZATION: Final = "MISSING_OWNER_AUTHORIZATION"
_UNRESOLVED_GATES: Final[dict[str, str]] = {
    UNRESOLVED_PERFORMANCE: OR7, UNRESOLVED_COVERAGE: OR7, UNRESOLVED_ROUND_TRIPS: OR7,
    UNRESOLVED_CHAIN_BREAKS: OR7, UNRESOLVED_UNFILLED: OR7, UNRESOLVED_SKIPPED: OR7,
    UNRESOLVED_COST_POLICY: OR6, UNRESOLVED_ACCOUNT_POLICY: OR11, UNRESOLVED_AUTHORIZATION: OR7,
}

STAGE_REFUSED: Final = "EXIT_CRITERIA_REFUSED"
STAGE_UNRESOLVED: Final = "EXIT_CRITERIA_UNRESOLVED"
STAGE_EVIDENCE_INCOMPLETE: Final = "EXIT_EVIDENCE_INCOMPLETE"
STAGE_COMPLETE: Final = "EXIT_EVIDENCE_COMPLETE_FOR_OWNER_DECISION"
STAGES_V1: Final = (STAGE_REFUSED, STAGE_UNRESOLVED, STAGE_EVIDENCE_INCOMPLETE, STAGE_COMPLETE)

ACCOUNT_ACTIVATION_V1: Final = "NEVER_ACTIVATES_AN_ACCOUNT"
NEED_REGISTRY_BINDING: Final = "OR7_INCUBATION_BINDING_NOT_REGISTRY_RESOLVED"
NEED_NET_FORWARD_METRICS: Final = "NET_FORWARD_METRICS_NOT_PRODUCED"
NEED_RUNNER_CONTRACT: Final = "RUNNER_CONTINUITY_CONTRACT_NOT_AVAILABLE"
CRITERIA_AFTER_INCUBATION: Final = f"{OR7}:EXIT_CRITERIA_RECORDED_AFTER_INCUBATION_BEGAN"
_GAP_TOLERANCE_PREFIX: Final = f"{OR7}:FORWARD_EVIDENCE_GAP_TOLERANCE_UNDECIDED"
_INCUBATION_STAGES: Final = (STAGE_INCUBATION_INCOMPLETE, STAGE_INCUBATION_EVIDENCE_FAILED,
                             STAGE_FORWARD_EVIDENCE_INCOMPLETE, STAGE_ELIGIBLE)

_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_CYCLE: Final = re.compile(r"^cycle-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.incubation_exit_criteria_v1")
_ISSUER: Final = object()
_ISSUED: set[tuple[str, str]] = set()


class ExitCriteriaError(ValueError):
    """Raised when a packet is contradictory or unauthorized, or a stored one is forged, stale or ambiguous."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExitCriteriaError(f"{name}_required")
    return value.strip()


def _hash_or_none(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HASH.match(value):
        raise ExitCriteriaError(f"{name}_must_be_a_sha256_hex")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, str):
        raise ExitCriteriaError(f"{name}_must_be_decimal_text")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ExitCriteriaError(f"{name}_must_be_decimal_text") from error
    if not number.is_finite():
        raise ExitCriteriaError(f"{name}_must_be_finite")
    return number


def _canonical(number: Decimal) -> str:
    """One text per value, so equal thresholds hash equally (``0.50`` and ``0.5``)."""
    text = format(number.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _count(value: object, name: str, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ExitCriteriaError(f"{name}_must_be_an_int_of_at_least_{minimum}")
    return value


@dataclass(frozen=True, slots=True)
class ForwardCriterionV1:
    """One owner forward criterion on a net forward metric: ``metric comparator threshold``."""

    metric: str
    comparator: str
    threshold: str

    def __post_init__(self) -> None:
        if self.metric not in FORWARD_METRICS_V1:
            raise ExitCriteriaError("forward_criterion_metric_unknown")
        if self.comparator not in COMPARATORS_V1:
            raise ExitCriteriaError("forward_criterion_comparator_unknown")
        object.__setattr__(self, "threshold", _canonical(_decimal(self.threshold, "forward_criterion_threshold")))

    def payload(self) -> dict[str, str]:
        return {"metric": self.metric, "comparator": self.comparator, "threshold": self.threshold}


def _check_criteria(criteria: Sequence[ForwardCriterionV1]) -> None:
    if not all(isinstance(item, ForwardCriterionV1) for item in criteria):
        raise ExitCriteriaError("performance_criteria_must_be_forward_criteria")
    keys = [(item.metric, item.comparator) for item in criteria]
    if len(set(keys)) != len(keys):
        raise ExitCriteriaError("duplicate_forward_criterion")
    for metric in {item.metric for item in criteria}:
        lower = [item for item in criteria if item.metric == metric and item.comparator in (">", ">=")]
        upper = [item for item in criteria if item.metric == metric and item.comparator in ("<", "<=")]
        if len(lower) > 1 or len(upper) > 1:
            raise ExitCriteriaError("redundant_bounds_on_one_metric")
        if lower and upper:
            low, high = Decimal(lower[0].threshold), Decimal(upper[0].threshold)
            strict = lower[0].comparator == ">" or upper[0].comparator == "<"
            if low > high or (low == high and strict):
                raise ExitCriteriaError("contradictory_forward_criteria_empty_range")


@dataclass(frozen=True)
class ExitCriteriaPacketV1:
    """One candidate's forward incubation exit criteria. Every owner slot is absent by default."""

    cycle_id: str
    study_id: str
    trial_id: str
    preregistration_hash: str
    symbol: str
    performance_criteria: tuple[ForwardCriterionV1, ...] = ()
    minimum_evidence_coverage: str | None = None
    minimum_round_trips: int | None = None
    maximum_chain_breaks: int | None = None
    maximum_unfilled_signals: int | None = None
    maximum_skipped_fills: int | None = None
    required_cost_policy_hash: str | None = None
    required_account_policy_hash: str | None = None
    authorized_by: str | None = None
    authorized_on: str | None = None
    _frozen: str = field(init=False, repr=False, compare=False, default="")

    def __post_init__(self) -> None:
        cycle = _text(self.cycle_id, "cycle_id")
        if not _CYCLE.match(cycle):
            raise ExitCriteriaError("cycle_id_malformed")
        for name in ("study_id", "trial_id"):
            try:
                UUID(_text(getattr(self, name), name))
            except ValueError as error:
                raise ExitCriteriaError(f"{name}_must_be_a_uuid") from error
        if _hash_or_none(self.preregistration_hash, "preregistration_hash") is None:
            raise ExitCriteriaError("preregistration_hash_required")
        _text(self.symbol, "symbol")
        criteria = tuple(self.performance_criteria)
        _check_criteria(criteria)
        coverage = None
        if self.minimum_evidence_coverage is not None:
            value = _decimal(self.minimum_evidence_coverage, "minimum_evidence_coverage")
            if not Decimal(0) < value <= Decimal(1):
                raise ExitCriteriaError("minimum_evidence_coverage_must_be_in_(0,1]")
            coverage = _canonical(value)
        _count(self.minimum_round_trips, "minimum_round_trips", 1)
        for name in ("maximum_chain_breaks", "maximum_unfilled_signals", "maximum_skipped_fills"):
            _count(getattr(self, name), name, 0)
        _hash_or_none(self.required_cost_policy_hash, "required_cost_policy_hash")
        _hash_or_none(self.required_account_policy_hash, "required_account_policy_hash")
        by, on = self.authorized_by, self.authorized_on
        if (by is None) != (on is None):
            raise ExitCriteriaError("authorization_needs_authorized_by_and_authorized_on")
        if by is not None:
            _text(by, "authorized_by")
            try:
                date.fromisoformat(_text(on, "authorized_on"))
            except ValueError as error:
                raise ExitCriteriaError("authorized_on_must_be_an_iso_date") from error
        object.__setattr__(self, "performance_criteria", criteria)
        object.__setattr__(self, "minimum_evidence_coverage", coverage)
        frozen = json.dumps(self._build(), sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "_frozen", frozen)
        if not self.has_values and by is not None:
            raise ExitCriteriaError("authorization_without_values")
        if self.has_values and by is None:
            raise ExitCriteriaError("unauthorized_threshold_values")

    def _build(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION_V1,
            "binding": {"cycle_id": self.cycle_id.strip(), "study_id": str(UUID(self.study_id.strip())),
                        "trial_id": str(UUID(self.trial_id.strip())),
                        "preregistration_hash": self.preregistration_hash, "symbol": self.symbol.strip()},
            "criteria": {
                "performance": [item.payload() for item in self.performance_criteria],
                "minimum_evidence_coverage": self.minimum_evidence_coverage,
                "minimum_round_trips": self.minimum_round_trips,
                "maximum_chain_breaks": self.maximum_chain_breaks,
                "maximum_unfilled_signals": self.maximum_unfilled_signals,
                "maximum_skipped_fills": self.maximum_skipped_fills,
                "required_cost_policy_hash": self.required_cost_policy_hash,
                "required_account_policy_hash": self.required_account_policy_hash,
            },
            "required_forward_evidence": list(REQUIRED_FORWARD_EVIDENCE_V1),
            "authorized_by": None if self.authorized_by is None else self.authorized_by.strip(),
            "authorized_on": None if self.authorized_on is None else self.authorized_on.strip(),
            "promotion": PROMOTION_V1,
            "account_activation": ACCOUNT_ACTIVATION_V1,
        }

    @property
    def frozen(self) -> dict[str, Any]:
        return json.loads(self._frozen)

    @property
    def has_values(self) -> bool:
        criteria = self.frozen["criteria"]
        return bool(criteria["performance"]) or any(value is not None for key, value in criteria.items()
                                                     if key != "performance")

    @property
    def unresolved(self) -> tuple[str, ...]:
        criteria = self.frozen["criteria"]
        reasons = [reason for reason, missing in (
            (UNRESOLVED_PERFORMANCE, not criteria["performance"]),
            (UNRESOLVED_COVERAGE, criteria["minimum_evidence_coverage"] is None),
            (UNRESOLVED_ROUND_TRIPS, criteria["minimum_round_trips"] is None),
            (UNRESOLVED_CHAIN_BREAKS, criteria["maximum_chain_breaks"] is None),
            (UNRESOLVED_UNFILLED, criteria["maximum_unfilled_signals"] is None),
            (UNRESOLVED_SKIPPED, criteria["maximum_skipped_fills"] is None),
            (UNRESOLVED_COST_POLICY, criteria["required_cost_policy_hash"] is None),
            (UNRESOLVED_ACCOUNT_POLICY, criteria["required_account_policy_hash"] is None),
        ) if missing]
        if not self.frozen["authorized_by"]:
            reasons.append(UNRESOLVED_AUTHORIZATION)
        return tuple(reasons)

    @property
    def status(self) -> str:
        return STATUS_DRAFT if self.unresolved else STATUS_AUTHORIZED

    def thresholds(self) -> dict[str, Any]:
        """The effective owner values: every one ``UNRESOLVED`` unless the whole packet is authorized."""
        criteria = self.frozen["criteria"]
        if self.status != STATUS_AUTHORIZED:
            return {key: UNRESOLVED_VALUE for key in criteria}
        return criteria

    def identity(self) -> dict[str, Any]:
        return {**self.frozen, "status": self.status, "unresolved": list(self.unresolved)}

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    @property
    def criteria_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"exit-criteria:{self.content_hash}")

    @classmethod
    def from_identity(cls, identity: Mapping[str, Any]) -> ExitCriteriaPacketV1:
        """Rebuild a stored identity through the construction rules; it must re-derive exactly."""
        try:
            binding, criteria = identity["binding"], identity["criteria"]
            packet = cls(
                cycle_id=binding["cycle_id"], study_id=binding["study_id"], trial_id=binding["trial_id"],
                preregistration_hash=binding["preregistration_hash"], symbol=binding["symbol"],
                performance_criteria=tuple(ForwardCriterionV1(**item) for item in criteria["performance"]),
                minimum_evidence_coverage=criteria["minimum_evidence_coverage"],
                minimum_round_trips=criteria["minimum_round_trips"],
                maximum_chain_breaks=criteria["maximum_chain_breaks"],
                maximum_unfilled_signals=criteria["maximum_unfilled_signals"],
                maximum_skipped_fills=criteria["maximum_skipped_fills"],
                required_cost_policy_hash=criteria["required_cost_policy_hash"],
                required_account_policy_hash=criteria["required_account_policy_hash"],
                authorized_by=identity["authorized_by"], authorized_on=identity["authorized_on"])
        except (KeyError, TypeError) as error:
            raise ExitCriteriaError("stored_exit_criteria_malformed") from error
        if packet.identity() != dict(identity):
            raise ExitCriteriaError("stored_exit_criteria_do_not_rebuild")
        return packet


# ---------------------------------------------------------------------------
# Store and resolver
# ---------------------------------------------------------------------------


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


@dataclass(frozen=True, slots=True)
class RegisteredExitCriteriaV1:
    """A stored exit-criteria packet as the resolver re-derived it. Issued only by :func:`resolve_exit_criteria_v1`."""

    packet: ExitCriteriaPacketV1
    criteria_hash: str
    recorded_at: datetime
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise ExitCriteriaError("registered_exit_criteria_are_issued_only_by_the_resolver")

    @property
    def issued(self) -> bool:
        return ((self.criteria_hash, self.recorded_at.astimezone(UTC).isoformat()) in _ISSUED
                and self.packet.content_hash == self.criteria_hash)


def _json(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else json.loads(raw)
    if not isinstance(value, dict):
        raise ExitCriteriaError("stored_identity_is_not_an_object")
    return value


def resolve_exit_criteria_v1(cursor: _Cursor, *, cycle_id: str, study_id: str,
                             trial_id: str) -> RegisteredExitCriteriaV1 | None:
    """The candidate's authorized packet, else its latest draft, else ``None`` (read-only).

    Every stored row must re-derive and agree with its columns; two authorized
    packets for one candidate are refused as ambiguous (the database forbids them).
    """
    cursor.execute("SELECT criteria_hash, cycle_id, study_id, trial_id, preregistration_hash, status, identity, "
                   "recorded_at FROM strategy_lab_incubation_exit_criteria WHERE cycle_id=%s AND study_id=%s "
                   "AND trial_id=%s ORDER BY recorded_at, criteria_hash", (cycle_id, study_id, trial_id))
    found: list[RegisteredExitCriteriaV1] = []
    for row in cursor.fetchall():
        criteria_hash, identity = str(row[0]).strip(), _json(row[6])
        if identity_hash_v1(identity) != criteria_hash:
            raise ExitCriteriaError("stored_exit_criteria_identity_does_not_rederive")
        if identity.get("schema_version") != SCHEMA_VERSION_V1:
            raise ExitCriteriaError("stored_exit_criteria_schema_version_is_not_current")
        packet = ExitCriteriaPacketV1.from_identity(identity)
        binding = identity["binding"]
        if (str(row[1]), str(row[2]), str(row[3]), str(row[4]).strip(), str(row[5])) != (
                binding["cycle_id"], binding["study_id"], binding["trial_id"], binding["preregistration_hash"],
                packet.status):
            raise ExitCriteriaError("stored_exit_criteria_row_contradicts_its_identity")
        if (binding["cycle_id"], binding["study_id"], binding["trial_id"]) != (cycle_id, study_id, trial_id):
            raise ExitCriteriaError("stored_exit_criteria_are_of_another_candidate")
        recorded_at = row[7]
        if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
            raise ExitCriteriaError("recorded_at_must_be_a_persisted_timezone_aware_instant")
        registered = RegisteredExitCriteriaV1(packet, criteria_hash, recorded_at, _ISSUER)
        _ISSUED.add((criteria_hash, recorded_at.astimezone(UTC).isoformat()))
        found.append(registered)
    authorized = [item for item in found if item.packet.status == STATUS_AUTHORIZED]
    if len(authorized) > 1:
        raise ExitCriteriaError("conflicting_authorized_exit_criteria_for_one_candidate")
    if authorized:
        return authorized[0]
    return found[-1] if found else None


class PostgresExitCriteriaRegistryV1:
    """Append-only store of exit-criteria packets (migration ``20261017_0065``)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, packet: ExitCriteriaPacketV1) -> bool:
        """Record ``packet``; ``False`` if this exact packet is already stored. Never overwrites."""
        if not isinstance(packet, ExitCriteriaPacketV1):
            raise ExitCriteriaError("only_an_exit_criteria_packet_is_recorded")
        binding = packet.frozen["binding"]
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT study_id, cycle_id, identity FROM strategy_lab_preregistrations "
                           "WHERE preregistration_hash=%s", (binding["preregistration_hash"],))
            row = cursor.fetchone()
            if row is None:
                raise ExitCriteriaError("preregistration_not_recorded")
            prereg = _json(row[2])
            if identity_hash_v1(prereg) != binding["preregistration_hash"]:
                raise ExitCriteriaError("preregistration_identity_does_not_rederive")
            if (str(row[0]), str(row[1])) != (binding["study_id"], binding["cycle_id"]):
                raise ExitCriteriaError("preregistration_is_of_another_study_or_cycle")
            if binding["trial_id"] not in [str(item) for item in prereg.get("candidates", [])]:
                raise ExitCriteriaError("trial_is_not_a_preregistered_candidate")
            if prereg.get("symbol") != binding["symbol"]:
                raise ExitCriteriaError("symbol_is_not_the_preregistered_symbol")
            if packet.status == STATUS_AUTHORIZED:
                cursor.execute("SELECT criteria_hash FROM strategy_lab_incubation_exit_criteria WHERE cycle_id=%s "
                               "AND study_id=%s AND trial_id=%s AND status='AUTHORIZED'",
                               (binding["cycle_id"], binding["study_id"], binding["trial_id"]))
                existing = cursor.fetchone()
                if existing is not None and str(existing[0]).strip() != packet.content_hash:
                    raise ExitCriteriaError("conflicting_authorized_exit_criteria_for_one_candidate")
            cursor.execute(
                "INSERT INTO strategy_lab_incubation_exit_criteria (criteria_hash, criteria_id, cycle_id, study_id, "
                "trial_id, preregistration_hash, status, identity) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (criteria_hash) DO NOTHING RETURNING criteria_hash",
                (packet.content_hash, packet.criteria_id, binding["cycle_id"], UUID(binding["study_id"]),
                 UUID(binding["trial_id"]), binding["preregistration_hash"], packet.status,
                 json.dumps(packet.identity(), sort_keys=True)),
            )
            return cursor.fetchone() is not None

    def resolve(self, *, cycle_id: str, study_id: str, trial_id: str) -> RegisteredExitCriteriaV1 | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            return resolve_exit_criteria_v1(cursor, cycle_id=cycle_id, study_id=study_id, trial_id=trial_id)


# ---------------------------------------------------------------------------
# Exit readiness (read-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExitReadinessV1:
    study_id: str
    trial_id: str
    stage: str
    readiness_stage: str
    criteria_hash: str | None
    criteria_status: str | None
    thresholds: Mapping[str, Any]
    evidence_inputs: Mapping[str, str]
    blockers: tuple[str, ...]
    refusals: tuple[str, ...]
    facts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def owner_gates(self) -> tuple[str, ...]:
        return tuple(sorted({gate for gate in (gate_of(item) for item in self.blockers) if gate}))

    def payload(self) -> dict[str, Any]:
        return {"schema_version": ASSESSMENT_SCHEMA_VERSION_V1, "study_id": self.study_id,
                "trial_id": self.trial_id, "stage": self.stage, "readiness_stage": self.readiness_stage,
                "criteria_hash": self.criteria_hash, "criteria_status": self.criteria_status,
                "thresholds": dict(self.thresholds), "evidence_inputs": dict(self.evidence_inputs),
                "blockers": list(self.blockers), "refusals": list(self.refusals),
                "owner_gates": list(self.owner_gates), "facts": dict(self.facts), "promotion": PROMOTION_V1,
                "account_activation": ACCOUNT_ACTIVATION_V1, "claim": "EXIT_READINESS_ASSESSMENT_NOT_A_DECISION"}

    @property
    def assessment_hash(self) -> str:
        return identity_hash_v1(self.payload())


def _registry_binding(evidence: CandidateEvidenceV1, resolution: object,
                      refusals: list[str], blockers: list[str]) -> IncubationBindingV1 | None:
    if resolution is None:
        blockers.append(NEED_REGISTRY_BINDING)
        return None
    if (not isinstance(resolution, IncubationResolutionV1) or not resolution.issued
            or resolution.source != BINDING_SOURCE_REGISTRY_V1):
        refusals.append("INCUBATION_RESOLUTION_NOT_ISSUED_BY_THE_REGISTRY")
        return None
    mine = [b for b in resolution.bindings if (b.study_id, b.trial_id) == (evidence.study_id, evidence.trial_id)]
    if not mine:
        blockers += [f"{NEED_REGISTRY_BINDING}:{reason}" for reason in resolution.unresolved] or [
            f"{NEED_REGISTRY_BINDING}:NO_BINDING_FOR_THIS_CANDIDATE"]
        return None
    (binding,) = mine
    if binding.source != BINDING_SOURCE_REGISTRY_V1:  # pragma: no cover - the resolution refuses it
        refusals.append("INCUBATION_BINDING_NOT_REGISTRY_RESOLVED")
        return None
    if (binding.preregistration_hash, binding.validation_hash) != (evidence.packet_hash, evidence.validation_hash):
        refusals.append("STALE_EVIDENCE_CHAIN_IS_NOT_THE_REGISTRY_RESOLVED_ONE")
    return binding


def _criteria_refusals(packet: ExitCriteriaPacketV1, evidence: CandidateEvidenceV1,
                       binding: IncubationBindingV1 | None) -> list[str]:
    bound = packet.frozen["binding"]
    out: list[str] = []
    if (bound["study_id"], bound["trial_id"]) != (evidence.study_id, evidence.trial_id):
        out.append("EXIT_CRITERIA_OF_ANOTHER_CANDIDATE")
    cycle = str(((evidence.packet_identity or {}).get("cycle") or {}).get("cycle_id"))
    if evidence.packet_hash is not None and (bound["preregistration_hash"], bound["cycle_id"]) != (
            evidence.packet_hash, cycle):
        out.append("STALE_EXIT_CRITERIA_PREREGISTRATION_REFERENCE")
    if binding is not None and (bound["preregistration_hash"], bound["cycle_id"], bound["symbol"]) != (
            binding.preregistration_hash, binding.cycle_id, binding.symbol):
        out.append("EXIT_CRITERIA_ARE_NOT_OF_THE_REGISTRY_RESOLVED_BINDING")
    return out


def _evidence_inputs(blockers: Sequence[str]) -> dict[str, str]:
    def missing(*needs: str) -> bool:
        return any(item in blockers for item in needs)

    parity = (INPUT_FAILED if any(item.startswith("LIVE_REPLAY_FILL_PARITY_VIOLATION") for item in blockers)
              else INPUT_MISSING if missing(NEED_PARITY) else INPUT_PRESENT)
    return {
        EVIDENCE_FORWARD_FUNDING: INPUT_MISSING if missing(NEED_FORWARD_FUNDING) else INPUT_PRESENT,
        # No runner liveness contract is merged: required, never inferred, always missing in v1.
        EVIDENCE_RUNNER_CONTINUITY: INPUT_MISSING,
        EVIDENCE_PARITY: parity,
        EVIDENCE_FEES: INPUT_MISSING if missing(NEED_FEE_SCHEDULE, NEED_COST_COMPLETE) else INPUT_PRESENT,
        EVIDENCE_RISK: INPUT_MISSING if any(gate_of(item) == "OR-11" for item in blockers) else INPUT_PRESENT,
    }


def _authorized_checks(packet: ExitCriteriaPacketV1, registered: RegisteredExitCriteriaV1,
                       readiness: CandidateReadinessV1, evidence: CandidateEvidenceV1, cost_policy: CostPolicyV1,
                       binding: IncubationBindingV1 | None, as_of: datetime) -> list[str]:
    criteria, facts = packet.frozen["criteria"], readiness.evidence
    out: list[str] = []
    if binding is not None and registered.recorded_at.astimezone(UTC) > datetime.fromtimestamp(
            binding.incubating_since_micros / 1_000_000, UTC):
        out.append(CRITERIA_AFTER_INCUBATION)
    if cost_policy.policy().content_hash != criteria["required_cost_policy_hash"]:
        out.append(f"{OR6}:FORWARD_FILLS_NOT_UNDER_THE_AUTHORIZED_COST_POLICY")
    account = evidence.account_policy or {}
    if account.get("content_hash") != criteria["required_account_policy_hash"]:
        out.append(f"{OR11}:ACCOUNT_POLICY_IS_NOT_THE_AUTHORIZED_ONE")
    if "fills" in facts:
        gaps = {"chain_breaks": (facts["chain_breaks"], criteria["maximum_chain_breaks"]),
                "unfilled": (facts["unfilled"], criteria["maximum_unfilled_signals"]),
                "skipped": (facts["fills_skipped_unknown_position"], criteria["maximum_skipped_fills"])}
        over = [f"{name}={seen}>{allowed}" for name, (seen, allowed) in gaps.items() if seen > allowed]
        if over:
            out.append(f"FORWARD_EVIDENCE_GAPS_EXCEED_AUTHORIZED_TOLERANCE:{','.join(over)}")
        if facts["round_trips"] < criteria["minimum_round_trips"]:
            out.append(f"FORWARD_ROUND_TRIPS_BELOW_AUTHORIZED_MINIMUM:{facts['round_trips']}"
                       f"/{criteria['minimum_round_trips']}")
        if binding is not None:
            span = max(0, int(as_of.timestamp() * 1_000_000) - binding.incubating_since_micros)
            coverage = Decimal(int(facts["evidenced_elapsed_micros"])) / Decimal(span) if span else Decimal(0)
            if coverage < Decimal(criteria["minimum_evidence_coverage"]):
                out.append(f"FORWARD_EVIDENCE_COVERAGE_BELOW_AUTHORIZED_MINIMUM:"
                           f"{_canonical(coverage.quantize(Decimal('0.000001')))}"
                           f"<{criteria['minimum_evidence_coverage']}")
    # The performance criteria are on net forward metrics; the R10 report produces gross only.
    out.append(NEED_NET_FORWARD_METRICS)
    return out


def assess_exit_readiness_v1(evidence: CandidateEvidenceV1, *, cost_policy: CostPolicyV1, as_of: datetime,
                             resolution: IncubationResolutionV1 | None,
                             criteria: RegisteredExitCriteriaV1 | None) -> ExitReadinessV1:
    """Exit readiness of one candidate against its registry-resolved criteria. Never writes, never promotes.

    ``resolution`` is the cycle's registry-resolved OR-7 binding set and
    ``criteria`` the packet :func:`resolve_exit_criteria_v1` issued (or ``None``).
    Every input the R10 path does not evidence stays a blocker.
    """
    readiness = assess_candidate_readiness_v1(evidence, cost_policy=cost_policy, as_of=as_of)
    refusals: list[str] = []
    blockers: list[str] = []
    if readiness.stage == STAGE_EVIDENCE_INCONSISTENT:
        refusals += [f"READINESS:{item}" for item in readiness.blockers]
    binding = _registry_binding(evidence, resolution, refusals, blockers)
    inherited = list(readiness.blockers)
    packet: ExitCriteriaPacketV1 | None = None
    if criteria is None:
        blockers.append(NEED_EXIT_CRITERIA)
    elif not isinstance(criteria, RegisteredExitCriteriaV1) or not criteria.issued:
        refusals.append("EXIT_CRITERIA_NOT_REGISTRY_RESOLVED")
    else:
        packet = criteria.packet
        refusals += _criteria_refusals(packet, evidence, binding)
        if packet.status != STATUS_AUTHORIZED:
            blockers += [f"{_UNRESOLVED_GATES[reason]}:{reason}" for reason in packet.unresolved]
        else:
            inherited = [item for item in inherited
                         if item != NEED_EXIT_CRITERIA and not item.startswith(_GAP_TOLERANCE_PREFIX)]
            blockers += _authorized_checks(packet, criteria, readiness, evidence, cost_policy, binding, as_of)
    inputs = _evidence_inputs(readiness.blockers)
    blockers += inherited
    if NEED_LIVENESS not in blockers:  # pragma: no cover - the readiness module always lists it in v1
        blockers.append(NEED_LIVENESS)
    blockers.append(NEED_RUNNER_CONTRACT)
    if refusals:
        stage = STAGE_REFUSED
    elif packet is None or packet.status != STATUS_AUTHORIZED:
        stage = STAGE_UNRESOLVED
    elif blockers or readiness.stage not in _INCUBATION_STAGES or INPUT_MISSING in inputs.values():
        stage = STAGE_EVIDENCE_INCOMPLETE
    else:  # pragma: no cover - unreachable in v1: runner continuity is always missing
        stage = STAGE_COMPLETE
    return ExitReadinessV1(
        study_id=evidence.study_id, trial_id=evidence.trial_id, stage=stage, readiness_stage=readiness.stage,
        criteria_hash=None if packet is None else packet.content_hash,
        criteria_status=None if packet is None else packet.status,
        thresholds={} if packet is None else packet.thresholds(), evidence_inputs=inputs,
        blockers=tuple(dict.fromkeys(blockers)), refusals=tuple(dict.fromkeys(refusals)),
        facts={**dict(readiness.evidence), "registry_bound": binding is not None,
               "criteria_recorded_at": None if criteria is None or packet is None
               else criteria.recorded_at.astimezone(UTC).isoformat()})


__all__ = [
    "ACCOUNT_ACTIVATION_V1",
    "COMPARATORS_V1",
    "FORWARD_METRICS_V1",
    "REQUIRED_FORWARD_EVIDENCE_V1",
    "SCHEMA_VERSION_V1",
    "STAGES_V1",
    "STAGE_COMPLETE",
    "STAGE_EVIDENCE_INCOMPLETE",
    "STAGE_REFUSED",
    "STAGE_UNRESOLVED",
    "STATUS_AUTHORIZED",
    "STATUS_DRAFT",
    "ExitCriteriaError",
    "ExitCriteriaPacketV1",
    "ExitReadinessV1",
    "ForwardCriterionV1",
    "PostgresExitCriteriaRegistryV1",
    "RegisteredExitCriteriaV1",
    "assess_exit_readiness_v1",
    "resolve_exit_criteria_v1",
]
