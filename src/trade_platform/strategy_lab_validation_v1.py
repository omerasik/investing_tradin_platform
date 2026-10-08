"""Phase R6 -- Strategy Lab preregistration, one-shot holdout, holdout validation and incubation state.

``RESEARCH_ONLY``. Generalizes the 3D.9A open-to-open preregistration pattern
(:mod:`trade_platform.open_to_open_preregistration_v1`: every owner-decided
field absent by default, a DRAFT packet with one unresolved reason per gap, a
single holdout gate) to Strategy Lab SDK candidates. It does not reuse the
legacy SQLite promotion gate's defaulted thresholds: every threshold here is an
owner decision (OR-7) or absent.

Research cycles (issued, persisted, prospective)
------------------------------------------------
:data:`CURRENT_CYCLE_V1` is the current cycle: its untouched holdout starts at
the immutable 2026-08-20 boundary. A future cycle is issued only by
:meth:`PostgresHoldoutRegistryV1.register_cycle`, which stores it with a
database-assigned registration instant and refuses a holdout start that is not
strictly later (a boundary can never be declared retroactively). Cycle ids are
derived from the holdout start; holdout spans of different cycles may never
overlap (database exclusion constraint), so the current holdout cannot be
re-opened under another name.

Preregistration (:class:`PreregistrationV1`)
--------------------------------------------
Binds the study, its Decimal authority rerun (selection ``ESTABLISHED``), the
selected candidates, the cycle, the holdout symbol and span (whole UTC days),
the OR-5 lag schedule, and the owner's OR-6/OR-7 inputs: acceptance criteria,
minimum trades, incubation length, and an OR-6 cost policy that must rebuild
exactly through :class:`~trade_platform.strategy_lab_policies_v1.CostPolicyV1`
with a verified fee schedule and a non-empty stress envelope. The identity is
deep-frozen at construction (canonical JSON); validation reads only that
frozen identity. Every gap is an ``UNRESOLVED_*`` reason and the packet stays
``DRAFT``.

Holdout (:class:`PostgresHoldoutRegistryV1`)
-------------------------------------------
Opening is one-shot per cycle, atomic, requires an ``AUTHORIZED`` packet (also
enforced by the database), and the holdout end may not lie after the opening
instant. The registry issues the only :class:`HoldoutOpeningV1` token; holdout
days can be acquired and windowed only with it, and every day of a window is
checked.

Holdout validation (:func:`validate_on_holdout_v1`)
---------------------------------------------------
Exactly one validation per cycle (database-unique). It must use a research
bar window of the preregistered symbol covering the whole opened span; its
content hash is bound into the result. Every candidate is recomputed in
Decimal at every preregistered OR-5 lag and net of every approved cost
scenario. A candidate is rejected if a criterion fails anywhere in the cost
envelope at the baseline lag, if the verdict or the net-return sign changes
under the sweep, or if it holds positions across funding windows (funding is
not modelled for T2; OR-6 fails closed). A pass makes it ``INCUBATING``; T2
holdout evidence is CONDITIONAL and nothing here can reach a validated state.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase
from .public_archive_research_bars_v1 import ResearchBarDatasetV1
from .research_data_plane_v1 import ResearchFrameStoreV1
from .strategy_lab_authority_rerun_v1 import (
    SELECTION_ESTABLISHED,
    AuthorityRerunV1,
    decimal_metrics_v1,
)
from .strategy_lab_policies_v1 import (
    CostPolicyV1,
    FeeScheduleV1,
    SlippageScenarioV1,
    StrategyLabPolicyError,
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

REASON_COST_ENVELOPE: Final = "HOLDOUT_CRITERIA_NOT_MET_UNDER_THE_APPROVED_COST_ENVELOPE"
REASON_FUNDING: Final = "FUNDING_EXPOSURE_NOT_MODELLED_FAIL_CLOSED"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.strategy_lab_validation_v1")
_CYCLE_ISSUER: Final = object()
_REGISTRY_ISSUER: Final = object()
_DAY: Final = timedelta(days=1)


class StrategyLabValidationError(ValueError):
    """Raised when a cycle, packet, opening or validation cannot be established."""


class CandidateStateV1(StrEnum):
    HOLDOUT_FAILED_REJECTED = "HOLDOUT_FAILED_REJECTED"
    INCUBATING = "INCUBATING"


def _midnight_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise StrategyLabValidationError(f"{name}_must_be_timezone_aware")
    value = value.astimezone(UTC)
    if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
        raise StrategyLabValidationError(f"{name}_must_be_a_whole_utc_day")
    return value


# ---------------------------------------------------------------------------
# Research cycles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchCycleV1:
    """A research cycle; issued only for the current boundary or by the registry."""

    holdout_start: datetime
    registered_at: datetime | None
    note: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _CYCLE_ISSUER:
            raise StrategyLabValidationError("research_cycle_is_issued_only_by_its_registry")
        object.__setattr__(self, "holdout_start", _midnight_utc(self.holdout_start, "holdout_start"))

    @property
    def cycle_id(self) -> str:
        return f"cycle-{self.holdout_start.date().isoformat()}"

    def payload(self) -> dict[str, Any]:
        return {"cycle_id": self.cycle_id, "holdout_start": self.holdout_start.isoformat(), "note": self.note}


#: The current cycle. Its boundary was preregistered by the 3D.9A pilot and is immutable.
CURRENT_CYCLE_V1: Final = ResearchCycleV1(
    UNTOUCHED_HOLDOUT_BOUNDARY_V1, None,
    "untouched holdout preregistered by the 3D.9A pilot; immutable for this cycle", _CYCLE_ISSUER,
)


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
        if not isinstance(self.metric, str) or not self.metric.strip() or not isinstance(self.threshold, str):
            raise StrategyLabValidationError("criterion_needs_a_metric_and_a_text_threshold")
        if not Decimal(self.threshold).is_finite():
            raise StrategyLabValidationError("criterion_threshold_must_be_finite")

    def holds(self, metrics: Mapping[str, Any]) -> bool | None:
        raw = metrics.get(self.metric)
        if raw is None:
            return None
        value, threshold = Decimal(str(raw)), Decimal(self.threshold)
        return {">": value > threshold, ">=": value >= threshold,
                "<": value < threshold, "<=": value <= threshold}[self.comparator]

    def payload(self) -> dict[str, str]:
        return {"metric": self.metric, "comparator": self.comparator, "threshold": self.threshold}


def _verified_cost_policy(raw: Mapping[str, Any] | None) -> tuple[CostPolicyV1 | None, str | None]:
    """Rebuild an OR-6 payload through the policy classes; ``(policy, None)`` or ``(None, reason)``."""
    if raw is None:
        return None, UNRESOLVED_COST_BASIS
    try:
        fees = raw.get("venue_fees")
        policy = CostPolicyV1(
            fee_schedule=None if fees is None else FeeScheduleV1(**fees),
            slippage_scenarios=tuple(SlippageScenarioV1(**item) for item in raw.get("slippage_scenarios", [])),
            fill_liquidity=str(raw.get("fill_liquidity", "")),
        )
    except (StrategyLabPolicyError, TypeError, ValueError):
        return None, UNRESOLVED_COST_BASIS
    if policy.policy().payload != dict(raw) or not policy.promotable_cost_basis:
        return None, UNRESOLVED_COST_BASIS
    return policy, None


def _positive_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StrategyLabValidationError(f"{name}_must_be_a_positive_int")
    return value


@dataclass(frozen=True)
class PreregistrationV1:
    study: StudySpecV1
    rerun: AuthorityRerunV1
    symbol: str
    cycle: ResearchCycleV1 = CURRENT_CYCLE_V1
    holdout_end_exclusive: datetime | None = None
    acceptance_criteria: tuple[CriterionV1, ...] = ()
    minimum_trades: int | None = None
    cost_policy: Mapping[str, Any] | None = None
    incubation_days: int | None = None
    authorized_by: str | None = None
    authorized_on: str | None = None
    #: Canonical JSON of the identity, fixed at construction; the only thing validation reads.
    _frozen: str = field(init=False, repr=False, compare=False, default="")

    def __post_init__(self) -> None:
        if not isinstance(self.cycle, ResearchCycleV1):
            raise StrategyLabValidationError("cycle_must_be_an_issued_research_cycle")
        if self.rerun.identity["study_content_hash"] != self.study.content_hash:
            raise StrategyLabValidationError("rerun_is_not_from_this_study")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise StrategyLabValidationError("holdout_symbol_required")
        if self.holdout_end_exclusive is not None:
            end = _midnight_utc(self.holdout_end_exclusive, "holdout_end")
            if end <= self.cycle.holdout_start:
                raise StrategyLabValidationError("holdout_end_must_follow_the_holdout_start")
            object.__setattr__(self, "holdout_end_exclusive", end)
        if not all(isinstance(item, CriterionV1) for item in self.acceptance_criteria):
            raise StrategyLabValidationError("acceptance_criteria_must_be_criteria")
        _positive_int(self.minimum_trades, "minimum_trades")
        _positive_int(self.incubation_days, "incubation_days")
        if self.authorized_by is not None and not self.authorized_by.strip():
            raise StrategyLabValidationError("authorized_by_must_be_nonblank")
        if self.authorized_on is not None:
            date.fromisoformat(self.authorized_on)
        # Deep-freeze: the identity is canonical JSON from here on; validation reads only it.
        frozen = json.dumps(self._build(), sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "_frozen", frozen)

    def _build(self) -> dict[str, Any]:
        selection = self.rerun.identity["authoritative_selection"]
        timing = self.study.policies.get("timing") if self.study.policies else None
        lags = [] if timing is None else [
            int(timing["baseline_dissemination_lag_micros"]), *[int(v) for v in timing["mandatory_sweep_lags_micros"]]
        ]
        return {
            "schema_version": PREREGISTRATION_SCHEMA_VERSION_V1,
            "cycle": self.cycle.payload(),
            "study_id": str(self.study.study_id),
            "study_content_hash": self.study.content_hash,
            "candidate_set_hash": self.rerun.identity["candidate_set_hash"],
            "rerun_hash": self.rerun.rerun_hash,
            "rerun_selection_status": self.rerun.selection_status,
            "candidates": [str(item["trial_id"]) for item in selection.get("selected", [])],
            "symbol": self.symbol,
            "holdout_start": self.cycle.holdout_start.isoformat(),
            "holdout_end_exclusive": (None if self.holdout_end_exclusive is None
                                      else self.holdout_end_exclusive.isoformat()),
            "lags_micros": lags,
            "acceptance_criteria": [criterion.payload() for criterion in self.acceptance_criteria],
            "minimum_trades": self.minimum_trades,
            "cost_policy": None if self.cost_policy is None else json.loads(json.dumps(dict(self.cost_policy))),
            "incubation_days": self.incubation_days,
            "lag_sweep_rule": "MATERIAL_CHANGE_IN_VERDICT_OR_NET_RETURN_SIGN_FAILS",
            "funding_rule": REASON_FUNDING,
            "authorized_by": None if self.authorized_by is None else self.authorized_by.strip(),
            "authorized_on": self.authorized_on,
        }

    @property
    def frozen(self) -> dict[str, Any]:
        return json.loads(self._frozen)

    @property
    def unresolved(self) -> tuple[str, ...]:
        frozen = self.frozen
        reasons = []
        if frozen["rerun_selection_status"] != SELECTION_ESTABLISHED:
            reasons.append(UNRESOLVED_AUTHORITY)
        if frozen["holdout_end_exclusive"] is None:
            reasons.append(UNRESOLVED_HOLDOUT_END)
        if not frozen["acceptance_criteria"]:
            reasons.append(UNRESOLVED_CRITERIA)
        if frozen["minimum_trades"] is None:
            reasons.append(UNRESOLVED_MINIMUM_TRADES)
        if _verified_cost_policy(frozen["cost_policy"])[1] is not None:
            reasons.append(UNRESOLVED_COST_BASIS)
        if frozen["incubation_days"] is None:
            reasons.append(UNRESOLVED_INCUBATION)
        if not frozen["authorized_by"] or not frozen["authorized_on"]:
            reasons.append(UNRESOLVED_AUTHORIZATION)
        if not frozen["lags_micros"]:
            reasons.append("MISSING_OR_5_TIMING_POLICY")
        return tuple(reasons)

    @property
    def status(self) -> str:
        return STATUS_AUTHORIZED if not self.unresolved else STATUS_DRAFT

    def identity(self) -> dict[str, Any]:
        return {**self.frozen, "status": self.status, "unresolved": list(self.unresolved)}

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    @property
    def preregistration_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"preregistration:{self.content_hash}")


# ---------------------------------------------------------------------------
# Holdout registry (one-shot)
# ---------------------------------------------------------------------------


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

    def admits_day(self, day: date) -> bool:
        start = self.holdout_start.astimezone(UTC).date()
        end = self.holdout_end_exclusive.astimezone(UTC).date()
        return start <= day < end


class PostgresHoldoutRegistryV1:
    """Cycles, preregistrations, one-shot openings, validations, states (migration 20261008_0057)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register_cycle(self, *, holdout_start: datetime, note: str) -> ResearchCycleV1:
        """A future cycle; the database stamps the registration and refuses a non-prospective start."""
        start = _midnight_utc(holdout_start, "holdout_start")
        if start <= UNTOUCHED_HOLDOUT_BOUNDARY_V1:
            raise StrategyLabValidationError("a_new_cycle_must_start_after_the_current_holdout")
        if not note.strip():
            raise StrategyLabValidationError("cycle_note_required")
        cycle_id = f"cycle-{start.date().isoformat()}"
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_research_cycles (cycle_id, holdout_start, note) VALUES (%s,%s,%s) "
                "RETURNING registered_at", (cycle_id, start, note.strip()))
            row = cursor.fetchone()
        if row is None:
            raise StrategyLabValidationError("cycle_not_registered")
        return ResearchCycleV1(start, row[0], note.strip(), _CYCLE_ISSUER)

    def cycle(self, cycle_id: str) -> ResearchCycleV1:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT holdout_start, registered_at, note FROM strategy_lab_research_cycles "
                           "WHERE cycle_id=%s", (cycle_id,))
            row = cursor.fetchone()
        if row is None:
            raise StrategyLabValidationError("cycle_not_registered")
        return ResearchCycleV1(row[0], row[1], str(row[2]), _CYCLE_ISSUER)

    def record_preregistration(self, packet: PreregistrationV1) -> bool:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            return self._insert_preregistration(cursor, packet)

    @staticmethod
    def _insert_preregistration(cursor: Any, packet: PreregistrationV1) -> bool:
        cursor.execute(
            "INSERT INTO strategy_lab_preregistrations (preregistration_hash, preregistration_id, study_id, "
            "cycle_id, status, identity, recorded_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) "
            "ON CONFLICT (preregistration_hash) DO NOTHING RETURNING preregistration_hash",
            (packet.content_hash, packet.preregistration_id, packet.study.study_id, packet.cycle.cycle_id,
             packet.status, json.dumps(packet.identity(), sort_keys=True), datetime.now(UTC)),
        )
        return cursor.fetchone() is not None

    def open_holdout(self, packet: PreregistrationV1, *, opened_by: str) -> HoldoutOpeningV1:
        if packet.status != STATUS_AUTHORIZED:
            raise StrategyLabValidationError("only_an_authorized_preregistration_opens_the_holdout")
        if not opened_by.strip():
            raise StrategyLabValidationError("opening_requires_an_actor")
        end = packet.holdout_end_exclusive
        if end is None:  # unreachable for an AUTHORIZED packet; refused, never assumed
            raise StrategyLabValidationError("authorized_packet_without_a_holdout_end")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            self._insert_preregistration(cursor, packet)
            cursor.execute(
                "INSERT INTO strategy_lab_holdout_openings (cycle_id, holdout_start, holdout_end_exclusive, "
                "preregistration_hash, preregistration_status, opened_by) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (cycle_id) DO NOTHING RETURNING cycle_id",
                (packet.cycle.cycle_id, packet.cycle.holdout_start, end, packet.content_hash, packet.status,
                 opened_by.strip()),
            )
            if cursor.fetchone() is None:
                raise StrategyLabValidationError("holdout_already_opened_for_this_cycle")
        return HoldoutOpeningV1(packet.cycle.cycle_id, packet.cycle.holdout_start, end, packet.content_hash,
                                opened_by.strip(), _REGISTRY_ISSUER)

    def opening(self, cycle_id: str) -> HoldoutOpeningV1 | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT preregistration_hash, holdout_start, holdout_end_exclusive, opened_by "
                           "FROM strategy_lab_holdout_openings WHERE cycle_id=%s", (cycle_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return HoldoutOpeningV1(cycle_id, row[1], row[2], str(row[0]).strip(), str(row[3]), _REGISTRY_ISSUER)

    def record_validation(self, run: ValidationRunV1) -> None:
        """One validation per cycle, and the states it implies, atomically. A second is refused."""
        identity = run.identity
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_holdout_validations (validation_hash, cycle_id, preregistration_hash, "
                "dataset_content_hash, identity, recorded_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (cycle_id) DO NOTHING RETURNING validation_hash",
                (run.content_hash, identity["cycle_id"], identity["preregistration_hash"],
                 identity["holdout"]["dataset_content_hash"], json.dumps(dict(identity), sort_keys=True),
                 datetime.now(UTC)),
            )
            if cursor.fetchone() is None:
                raise StrategyLabValidationError("holdout_already_validated_for_this_cycle")
            for event in run.state_events:
                cursor.execute(
                    "INSERT INTO strategy_lab_candidate_states (study_id, trial_id, state, evidence_hash, reasons, "
                    "recorded_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                    (UUID(str(event["study_id"])), UUID(str(event["trial_id"])), event["state"], run.content_hash,
                     json.dumps(list(event["reasons"])), datetime.now(UTC)),
                )

    def states(self, study_id: UUID) -> list[dict[str, Any]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT trial_id, state, evidence_hash, reasons, recorded_at FROM "
                           "strategy_lab_candidate_states WHERE study_id=%s ORDER BY recorded_at, trial_id",
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
        return [{"study_id": self.identity["study_id"], "trial_id": item["trial_id"], "state": item["state"],
                 "reasons": item["reasons"]} for item in self.identity["candidates"]]


def validate_on_holdout_v1(
    packet: PreregistrationV1, opening: HoldoutOpeningV1, holdout: ResearchBarDatasetV1, *,
    store: ResearchFrameStoreV1,
) -> ValidationRunV1:
    """Recompute every preregistered candidate on the whole opened span, in Decimal, under every lag and scenario."""
    frozen = packet.frozen
    if packet.status != STATUS_AUTHORIZED:
        raise StrategyLabValidationError("validation_requires_an_authorized_packet")
    if not isinstance(opening, HoldoutOpeningV1) or (
        opening.preregistration_hash, opening.cycle_id, opening.holdout_start.astimezone(UTC).isoformat(),
        opening.holdout_end_exclusive.astimezone(UTC).isoformat(),
    ) != (packet.content_hash, frozen["cycle"]["cycle_id"], frozen["holdout_start"], frozen["holdout_end_exclusive"]):
        raise StrategyLabValidationError("validation_requires_the_opening_of_this_packet")
    start = datetime.fromisoformat(frozen["holdout_start"])
    end = datetime.fromisoformat(frozen["holdout_end_exclusive"])
    if (holdout.symbol, holdout.identity["first_utc_day"], holdout.identity["last_utc_day"]) != (
        frozen["symbol"], start.date().isoformat(), (end - _DAY).date().isoformat()
    ):
        raise StrategyLabValidationError("holdout_dataset_must_be_the_preregistered_symbol_over_the_whole_span")
    bars = BarsV1.from_rows(list(store.iter_rows(store.load_manifest(holdout.bar_frame_manifest_hash))))
    study = packet.study
    family = FAMILIES_V1[study.strategy.family]
    if family.spec() != study.strategy:
        raise StrategyLabValidationError("study_strategy_is_not_this_sdk_family_version")
    policy, reason = _verified_cost_policy(frozen["cost_policy"])
    if policy is None:
        raise StrategyLabValidationError(f"validation_requires_a_verified_cost_basis:{reason}")
    scenarios = [(item.name, policy.total_cost_bps_per_side(item.name)) for item in policy.slippage_scenarios]
    lags = [int(value) for value in frozen["lags_micros"]]
    if lags != [lag // timedelta(microseconds=1) for lag in or5_lags_v1()]:
        raise StrategyLabValidationError("packet_lags_are_not_the_owner_approved_or_5_schedule")
    trials = {str(trial.trial_id): trial for trial in study.trials()}
    criteria = [CriterionV1(**item) for item in frozen["acceptance_criteria"]]
    minimum_trades = int(frozen["minimum_trades"])
    results = []
    for trial_id in frozen["candidates"]:
        params = study.parameter_space.typed_point(trials[trial_id].parameters)
        targets = family.targets_decimal(bars, params)
        by_lag: list[dict[str, Any]] = []
        for lag_us in lags:
            held = held_positions_v1(bars, targets, lag_us)
            per_scenario: list[dict[str, Any]] = []
            for name, cost in scenarios:
                metrics = decimal_metrics_v1(bars, held, cost_bps_per_side=cost, cost_label=name)
                checks = [criterion.holds(metrics) for criterion in criteria]
                enough = int(metrics["trades"]) >= minimum_trades
                per_scenario.append({"scenario": name, "cost_bps_per_side": format(cost, "f"), "metrics": metrics,
                                     "criteria": checks, "minimum_trades_met": enough,
                                     "passed": enough and all(check is True for check in checks)})
            by_lag.append({"lag_micros": lag_us, "scenarios": per_scenario,
                           "passed": all(item["passed"] for item in per_scenario)})
        baseline = by_lag[0]
        reasons = []
        if not baseline["passed"]:
            reasons.append(REASON_COST_ENVELOPE)
        for entry in by_lag:
            if any(int(s["metrics"]["funding_window_crossings"]) > 0 for s in entry["scenarios"]):
                reasons.append(REASON_FUNDING)
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
        "holdout": {"start": frozen["holdout_start"], "end_exclusive": frozen["holdout_end_exclusive"],
                    "symbol": holdout.symbol, "dataset_version_id": str(holdout.dataset_version_id),
                    "dataset_content_hash": holdout.content_hash, "bars": bars.size,
                    "not_published_days": list(holdout.identity["not_published_days"]),
                    "warmup": "INSIDE_THE_HOLDOUT_NO_CARRY_IN"},
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
    """Per candidate, the recorded states in order; a rejection is terminal and is never overwritten."""
    latest: dict[str, str] = {}
    for event in events:
        trial = str(event["trial_id"])
        if latest.get(trial) == CandidateStateV1.HOLDOUT_FAILED_REJECTED.value:
            continue
        latest[trial] = str(event["state"])
    return latest


__all__ = [
    "CURRENT_CYCLE_V1",
    "REASON_COST_ENVELOPE",
    "REASON_FUNDING",
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
    "validate_on_holdout_v1",
]
