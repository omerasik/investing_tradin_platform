"""Phase R6 -- Strategy Lab preregistration, one-shot holdout, holdout validation and incubation state.

``RESEARCH_ONLY``. Generalizes the 3D.9A open-to-open preregistration pattern
(:mod:`trade_platform.open_to_open_preregistration_v1`: every owner-decided
field absent by default, a DRAFT packet with one unresolved reason per gap, a
single holdout gate) to any Strategy Lab SDK candidate. It does not reuse the
legacy SQLite promotion gate's defaulted thresholds: every threshold here is
an owner decision (OR-7) or absent.

Research cycles
---------------
:data:`CURRENT_CYCLE_V1` is the current research cycle; its untouched holdout
starts at the immutable 2026-08-20 boundary. :func:`new_research_cycle_v1`
declares a *future* cycle prospectively: its holdout start must lie strictly
after the instant it is registered, so a boundary can never be moved or
declared retroactively over data already seen.

Preregistration (:class:`PreregistrationV1`)
--------------------------------------------
Binds a study, its frozen candidate set and a Decimal authority rerun whose
selection is ``ESTABLISHED`` (OR-3), the candidates that rerun selected, the
cycle and its holdout start, and the owner's OR-7 inputs: holdout end,
acceptance criteria, minimum trades, the OR-6 cost policy with a verified fee
schedule and the approved stress envelope, and incubation length. Every gap is
an ``UNRESOLVED_*`` reason and the packet stays ``DRAFT``; only a complete
packet with an explicit owner authorization is ``AUTHORIZED``.

Holdout (:class:`PostgresHoldoutRegistryV1`)
-------------------------------------------
Opening is one-shot per cycle (a unique row): a second opening of the same
cycle is refused, because a holdout seen once is no longer untouched. Opening
requires an ``AUTHORIZED`` packet and returns a :class:`HoldoutOpeningV1`
token that only the registry issues; holdout data access requires it.

Holdout validation (:func:`validate_on_holdout_v1`)
---------------------------------------------------
Every preregistered candidate is recomputed in Decimal over the holdout bars
at the OR-5 baseline lag and every mandatory sweep lag, net of each approved
cost scenario (fee + named slippage stress). A candidate passes only if every
criterion holds at the baseline under *every* scenario, and the verdict and
the sign of the net return are unchanged under every sweep lag (OR-5: a
material change fails). Every result is kept. A pass makes the candidate
``INCUBATING`` (forward evidence on T4 comes later and is calendar-bound),
never ``VALIDATED``; T2 holdout evidence is CONDITIONAL.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase
from .strategy_lab_authority_rerun_v1 import (
    SELECTION_ESTABLISHED,
    AuthorityRerunV1,
    decimal_metrics_v1,
)
from .strategy_lab_policies_v1 import (
    OR6_SCHEMA_VERSION_V1,
    or5_lags_v1,
)
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, StudySpecV1, identity_hash_v1
from .strategy_sdk_v1 import FAMILIES_V1, BarsV1, held_positions_v1

PREREGISTRATION_SCHEMA_VERSION_V1: Final = "strategy-lab-preregistration-v1"
VALIDATION_SCHEMA_VERSION_V1: Final = "strategy-lab-holdout-validation-v1"

STATUS_DRAFT: Final = "DRAFT"
STATUS_AUTHORIZED: Final = "AUTHORIZED"

UNRESOLVED_AUTHORITY: Final = "AUTHORITY_RERUN_SELECTION_NOT_ESTABLISHED"
UNRESOLVED_HOLDOUT_END: Final = "MISSING_OWNER_HOLDOUT_END_OR_7"
UNRESOLVED_CRITERIA: Final = "MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7"
UNRESOLVED_MINIMUM_TRADES: Final = "MISSING_OWNER_MINIMUM_TRADES_OR_7"
UNRESOLVED_COST_BASIS: Final = "MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6"
UNRESOLVED_INCUBATION: Final = "MISSING_OWNER_INCUBATION_LENGTH_OR_7"
UNRESOLVED_AUTHORIZATION: Final = "MISSING_OWNER_AUTHORIZATION"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.strategy_lab_validation_v1")


class StrategyLabValidationError(ValueError):
    """Raised when a cycle, packet, opening or validation cannot be established."""


class CandidateStateV1(StrEnum):
    SEARCH_NON_AUTHORITATIVE = "SEARCH_NON_AUTHORITATIVE"
    DECIMAL_AUTHORITATIVE = "DECIMAL_AUTHORITATIVE"
    PREREGISTERED = "PREREGISTERED"
    HOLDOUT_FAILED_REJECTED = "HOLDOUT_FAILED_REJECTED"
    INCUBATING = "INCUBATING"
    INCUBATION_FAILED_REJECTED = "INCUBATION_FAILED_REJECTED"
    #: Reserved for a later, calendar-bound phase; nothing here can reach it.
    PROFESSIONALLY_VALIDATED = "PROFESSIONALLY_VALIDATED"


# ---------------------------------------------------------------------------
# Research cycles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchCycleV1:
    cycle_id: str
    holdout_start: datetime
    registered_at: datetime | None
    note: str

    def payload(self) -> dict[str, Any]:
        return {"cycle_id": self.cycle_id, "holdout_start": self.holdout_start.isoformat(),
                "registered_at": None if self.registered_at is None else self.registered_at.isoformat(),
                "note": self.note}


#: The current cycle. Its boundary was preregistered by the 3D.9A engineering
#: pilot and is immutable; it is not re-registered here.
CURRENT_CYCLE_V1: Final = ResearchCycleV1(
    cycle_id="cycle-2026-08-20", holdout_start=UNTOUCHED_HOLDOUT_BOUNDARY_V1, registered_at=None,
    note="untouched holdout preregistered by the 3D.9A pilot; immutable for this cycle",
)


def new_research_cycle_v1(*, holdout_start: datetime, registered_at: datetime, note: str) -> ResearchCycleV1:
    """A future cycle, declared prospectively: its holdout starts strictly after registration."""
    if holdout_start.tzinfo is None or registered_at.tzinfo is None:
        raise StrategyLabValidationError("cycle_instants_must_be_timezone_aware")
    if holdout_start <= registered_at:
        raise StrategyLabValidationError("a_holdout_boundary_must_be_declared_prospectively")
    if holdout_start <= CURRENT_CYCLE_V1.holdout_start:
        raise StrategyLabValidationError("a_new_cycle_must_start_after_the_current_holdout")
    start = holdout_start.astimezone(UTC)
    return ResearchCycleV1(f"cycle-{start.date().isoformat()}", start, registered_at.astimezone(UTC), note)


# ---------------------------------------------------------------------------
# Preregistration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CriterionV1:
    """One owner acceptance criterion on a Decimal metric: ``metric comparator threshold``."""

    metric: str
    comparator: str
    threshold: str

    def __post_init__(self) -> None:
        if self.comparator not in {">", ">=", "<", "<="}:
            raise StrategyLabValidationError("criterion_comparator_unknown")
        if not self.metric or not isinstance(self.threshold, str):
            raise StrategyLabValidationError("criterion_needs_a_metric_and_a_text_threshold")
        Decimal(self.threshold)

    def holds(self, metrics: Mapping[str, Any]) -> bool | None:
        raw = metrics.get(self.metric)
        if raw is None:
            return None
        value, threshold = Decimal(str(raw)), Decimal(self.threshold)
        return {">": value > threshold, ">=": value >= threshold,
                "<": value < threshold, "<=": value <= threshold}[self.comparator]

    def payload(self) -> dict[str, str]:
        return {"metric": self.metric, "comparator": self.comparator, "threshold": self.threshold}


@dataclass(frozen=True, slots=True)
class PreregistrationV1:
    study: StudySpecV1
    rerun: AuthorityRerunV1
    cycle: ResearchCycleV1 = CURRENT_CYCLE_V1
    holdout_end_exclusive: datetime | None = None
    acceptance_criteria: tuple[CriterionV1, ...] = ()
    minimum_trades: int | None = None
    cost_policy: Mapping[str, Any] | None = None
    incubation_days: int | None = None
    authorized_by: str | None = None
    authorized_on: str | None = None
    _identity: dict[str, Any] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        if self.rerun.identity["study_content_hash"] != self.study.content_hash:
            raise StrategyLabValidationError("rerun_is_not_from_this_study")
        if self.holdout_end_exclusive is not None and self.holdout_end_exclusive <= self.cycle.holdout_start:
            raise StrategyLabValidationError("holdout_end_must_follow_the_holdout_start")
        object.__setattr__(self, "_identity", self._build())

    @property
    def unresolved(self) -> tuple[str, ...]:
        reasons = []
        if self.rerun.selection_status != SELECTION_ESTABLISHED:
            reasons.append(UNRESOLVED_AUTHORITY)
        if self.holdout_end_exclusive is None:
            reasons.append(UNRESOLVED_HOLDOUT_END)
        if not self.acceptance_criteria:
            reasons.append(UNRESOLVED_CRITERIA)
        if self.minimum_trades is None:
            reasons.append(UNRESOLVED_MINIMUM_TRADES)
        cost = self.cost_policy or {}
        if cost.get("schema_version") != OR6_SCHEMA_VERSION_V1 or not cost.get("venue_fees") or not cost.get(
                "slippage_scenarios"):
            reasons.append(UNRESOLVED_COST_BASIS)
        if self.incubation_days is None:
            reasons.append(UNRESOLVED_INCUBATION)
        if not self.authorized_by or not self.authorized_on:
            reasons.append(UNRESOLVED_AUTHORIZATION)
        return tuple(reasons)

    @property
    def status(self) -> str:
        return STATUS_AUTHORIZED if not self.unresolved else STATUS_DRAFT

    def _build(self) -> dict[str, Any]:
        selection = self.rerun.identity["authoritative_selection"]
        return {
            "schema_version": PREREGISTRATION_SCHEMA_VERSION_V1,
            "cycle": self.cycle.payload(),
            "study_id": str(self.study.study_id),
            "study_content_hash": self.study.content_hash,
            "candidate_set_hash": self.rerun.identity["candidate_set_hash"],
            "rerun_hash": self.rerun.rerun_hash,
            "candidates": [item["trial_id"] for item in selection.get("selected", [])],
            "holdout_end_exclusive": (None if self.holdout_end_exclusive is None
                                      else self.holdout_end_exclusive.astimezone(UTC).isoformat()),
            "acceptance_criteria": [criterion.payload() for criterion in self.acceptance_criteria],
            "minimum_trades": self.minimum_trades,
            "cost_policy": None if self.cost_policy is None else dict(self.cost_policy),
            "incubation_days": self.incubation_days,
            "timing_policy": self.study.policies.get("timing") if self.study.policies else None,
            "lag_sweep_rule": "MATERIAL_CHANGE_IN_VERDICT_OR_NET_RETURN_SIGN_FAILS",
            "authorized_by": self.authorized_by,
            "authorized_on": self.authorized_on,
        }

    def identity(self) -> dict[str, Any]:
        return {**self._identity, "status": self.status, "unresolved": list(self.unresolved)}

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    @property
    def preregistration_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"preregistration:{self.content_hash}")

    @property
    def candidates(self) -> list[str]:
        return list(self._identity["candidates"])


# ---------------------------------------------------------------------------
# Holdout registry (one-shot)
# ---------------------------------------------------------------------------


_REGISTRY_ISSUER: Final = object()


@dataclass(frozen=True, slots=True)
class HoldoutOpeningV1:
    """Proof that a cycle's holdout was opened, once, for one authorized packet. Registry-issued."""

    cycle_id: str
    holdout_start: datetime
    holdout_end_exclusive: datetime
    preregistration_hash: str
    opened_by: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _REGISTRY_ISSUER:
            raise StrategyLabValidationError("holdout_opening_is_issued_only_by_the_registry")

    def admits_day(self, day: Any) -> bool:
        return self.holdout_start.date() <= day < self.holdout_end_exclusive.date()


class PostgresHoldoutRegistryV1:
    """Preregistrations and the one-shot holdout openings (migration 20261008_0057)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record_preregistration(self, packet: PreregistrationV1) -> bool:
        identity = packet.identity()
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_preregistrations (preregistration_hash, preregistration_id, study_id, "
                "cycle_id, status, identity, recorded_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (preregistration_hash) DO NOTHING RETURNING preregistration_hash",
                (packet.content_hash, packet.preregistration_id, packet.study.study_id, packet.cycle.cycle_id,
                 packet.status, json.dumps(identity, sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def open_holdout(self, packet: PreregistrationV1, *, opened_by: str) -> HoldoutOpeningV1:
        if packet.status != STATUS_AUTHORIZED:
            raise StrategyLabValidationError("only_an_authorized_preregistration_opens_the_holdout")
        if not opened_by.strip():
            raise StrategyLabValidationError("opening_requires_an_actor")
        self.record_preregistration(packet)
        end = packet.holdout_end_exclusive
        assert end is not None  # guaranteed by AUTHORIZED
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_holdout_openings (cycle_id, preregistration_hash, holdout_start, "
                "holdout_end_exclusive, opened_by, opened_at) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (cycle_id) DO NOTHING RETURNING cycle_id",
                (packet.cycle.cycle_id, packet.content_hash, packet.cycle.holdout_start, end, opened_by,
                 datetime.now(UTC)),
            )
            if cursor.fetchone() is None:
                raise StrategyLabValidationError("holdout_already_opened_for_this_cycle")
        return HoldoutOpeningV1(packet.cycle.cycle_id, packet.cycle.holdout_start, end, packet.content_hash,
                                opened_by, _REGISTRY_ISSUER)

    def opening(self, cycle_id: str) -> HoldoutOpeningV1 | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT preregistration_hash, holdout_start, holdout_end_exclusive, opened_by "
                           "FROM strategy_lab_holdout_openings WHERE cycle_id=%s", (cycle_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return HoldoutOpeningV1(cycle_id, row[1], row[2], str(row[0]).strip(), str(row[3]), _REGISTRY_ISSUER)

    def record_validation(self, run: ValidationRunV1) -> bool:
        """Persist a holdout validation and the candidate states it implies (append-only)."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_holdout_validations (validation_hash, preregistration_hash, cycle_id, "
                "identity, recorded_at) VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT (validation_hash) DO NOTHING "
                "RETURNING validation_hash",
                (run.content_hash, run.identity["preregistration_hash"], run.identity["cycle_id"],
                 json.dumps(dict(run.identity), sort_keys=True), datetime.now(UTC)),
            )
            created = cursor.fetchone() is not None
        self.record_states(run.state_events)
        return created

    def record_states(self, events: Sequence[Mapping[str, Any]]) -> None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            for event in events:
                cursor.execute(
                    "INSERT INTO strategy_lab_candidate_states (event_hash, study_id, trial_id, state, "
                    "evidence_hash, reasons, recorded_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) "
                    "ON CONFLICT (event_hash) DO NOTHING",
                    (identity_hash_v1(dict(event)), UUID(str(event["study_id"])), UUID(str(event["trial_id"])),
                     event["state"], event["evidence_hash"], json.dumps(list(event["reasons"])),
                     datetime.now(UTC)),
                )

    def states(self, study_id: UUID) -> list[dict[str, Any]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT trial_id, state, evidence_hash, reasons, recorded_at FROM "
                           "strategy_lab_candidate_states WHERE study_id=%s ORDER BY recorded_at, event_hash",
                           (study_id,))
            rows = cursor.fetchall()
        return [{"trial_id": str(r[0]), "state": r[1], "evidence_hash": str(r[2]).strip(),
                 "reasons": r[3] if isinstance(r[3], list) else json.loads(r[3]), "recorded_at": r[4]}
                for r in rows]


# ---------------------------------------------------------------------------
# Holdout validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationRunV1:
    identity: Mapping[str, Any]
    content_hash: str

    @property
    def state_events(self) -> list[dict[str, Any]]:
        return [
            {"study_id": self.identity["study_id"], "trial_id": item["trial_id"],
             "state": item["state"], "evidence_hash": self.content_hash, "reasons": item["reasons"]}
            for item in self.identity["candidates"]
        ]


def _scenarios(cost_policy: Mapping[str, Any]) -> list[tuple[str, Decimal]]:
    fee = Decimal(str(cost_policy["venue_fees"]["taker_fee_bps"]))
    return [(str(item["name"]), fee + Decimal(str(item["cost_bps_per_side"])))
            for item in cost_policy["slippage_scenarios"]]


def validate_on_holdout_v1(
    packet: PreregistrationV1, opening: HoldoutOpeningV1, holdout_bars: BarsV1,
) -> ValidationRunV1:
    """Recompute every preregistered candidate on the holdout, in Decimal, under every lag and scenario."""
    if not isinstance(opening, HoldoutOpeningV1) or opening.preregistration_hash != packet.content_hash:
        raise StrategyLabValidationError("validation_requires_the_opening_of_this_packet")
    if packet.status != STATUS_AUTHORIZED:
        raise StrategyLabValidationError("validation_requires_an_authorized_packet")
    start_us = (opening.holdout_start - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    end_us = (opening.holdout_end_exclusive - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    if holdout_bars.size == 0 or int(holdout_bars.open_us[0]) < start_us or int(holdout_bars.close_us[-1]) > end_us:
        raise StrategyLabValidationError("holdout_bars_outside_the_opened_span")
    study = packet.study
    family = FAMILIES_V1[study.strategy.family]
    if family.spec() != study.strategy:
        raise StrategyLabValidationError("study_strategy_is_not_this_sdk_family_version")
    trials = {str(trial.trial_id): trial for trial in study.trials()}
    scenarios = _scenarios(packet.cost_policy or {})
    lags = or5_lags_v1()
    results = []
    for trial_id in packet.candidates:
        params = study.parameter_space.typed_point(trials[trial_id].parameters)
        targets = family.targets_decimal(holdout_bars, params)
        by_lag = []
        for lag in lags:
            lag_us = lag // timedelta(microseconds=1)
            held = held_positions_v1(holdout_bars, targets, lag_us)
            per_scenario = []
            for name, cost in scenarios:
                metrics = decimal_metrics_v1(holdout_bars, held, cost_bps_per_side=cost, cost_label=name)
                checks = [criterion.holds(metrics) for criterion in packet.acceptance_criteria]
                enough = int(metrics["trades"]) >= int(packet.minimum_trades or 0)
                passed = enough and all(check is True for check in checks)
                per_scenario.append({"scenario": name, "cost_bps_per_side": format(cost, "f"),
                                     "metrics": metrics, "criteria": checks, "minimum_trades_met": enough,
                                     "passed": passed})
            by_lag.append({"lag_micros": lag_us, "scenarios": per_scenario,
                           "passed": all(item["passed"] for item in per_scenario)})
        baseline = by_lag[0]
        reasons = []
        if not baseline["passed"]:
            reasons.append("HOLDOUT_CRITERIA_NOT_MET_UNDER_THE_APPROVED_COST_ENVELOPE")
        for entry in by_lag[1:]:
            if entry["passed"] != baseline["passed"]:
                reasons.append(f"VERDICT_CHANGES_UNDER_OR5_LAG_{entry['lag_micros']}")
            for base_s, other_s in zip(baseline["scenarios"], entry["scenarios"], strict=True):
                if _sign(base_s["metrics"]["total_return"]) != _sign(other_s["metrics"]["total_return"]):
                    reasons.append(f"NET_RETURN_SIGN_CHANGES_UNDER_OR5_LAG_{entry['lag_micros']}:{base_s['scenario']}")
        state = CandidateStateV1.INCUBATING if not reasons else CandidateStateV1.HOLDOUT_FAILED_REJECTED
        results.append({"trial_id": trial_id, "lags": by_lag, "reasons": sorted(set(reasons)), "state": state.value})
    identity = {
        "schema_version": VALIDATION_SCHEMA_VERSION_V1,
        "preregistration_hash": packet.content_hash,
        "study_id": str(study.study_id),
        "cycle_id": opening.cycle_id,
        "holdout": {"start": opening.holdout_start.isoformat(), "end_exclusive": opening.holdout_end_exclusive.isoformat(),
                    "bars": holdout_bars.size, "warmup": "INSIDE_THE_HOLDOUT_NO_CARRY_IN"},
        "candidates": results,
        "claim_ceiling": "CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED",
    }
    return ValidationRunV1(identity, identity_hash_v1(identity))


def _sign(raw: object) -> int:
    if raw is None:
        return 0
    value = Decimal(str(raw))
    return (value > 0) - (value < 0)


def candidate_lifecycle_v1(events: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """The latest recorded state per candidate (append-only events, recorded order)."""
    latest: dict[str, str] = {}
    for event in events:
        latest[str(event["trial_id"])] = str(event["state"])
    return latest


__all__ = [
    "CURRENT_CYCLE_V1",
    "STATUS_AUTHORIZED",
    "STATUS_DRAFT",
    "CandidateStateV1",
    "CriterionV1",
    "HoldoutOpeningV1",
    "PostgresHoldoutRegistryV1",
    "PreregistrationV1",
    "ResearchCycleV1",
    "StrategyLabValidationError",
    "ValidationRunV1",
    "candidate_lifecycle_v1",
    "new_research_cycle_v1",
    "validate_on_holdout_v1",
]
