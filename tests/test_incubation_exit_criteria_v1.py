"""Phase R10 -- owner-gated forward incubation exit criteria: nothing defaulted, nothing promoted."""

from __future__ import annotations

import copy
import dataclasses
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.test_paper_incubation_registry_v1 import (
    CYCLE,
    GROSS,
    STATED,
    STUDY,
    TRIAL,
    Registry,
    fill,
    resolve,
)
from tests.test_runner_liveness_v1 import (
    BOUNDS,
    DAY0,
    FULL_SEALS,
    MemoryStore,
    acceptance,
    run_session,
)
from trade_platform import candidate_lifecycle_readiness_v1 as readiness
from trade_platform import incubation_exit_criteria_v1 as exits
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.activation_readiness_v1 import OR6, OR7, OR11
from trade_platform.candidate_lifecycle_readiness_v1 import (
    NEED_EXIT_CRITERIA,
    NEED_FORWARD_FUNDING,
    NEED_LIVENESS,
    NEED_REGISTRY_BINDING,
    CandidateEvidenceV1,
)
from trade_platform.incubation_exit_criteria_v1 import (
    CRITERIA_AFTER_INCUBATION,
    NEED_NET_FORWARD_METRICS,
    NEED_RUNNER_CONTRACT,
    STAGE_EVIDENCE_INCOMPLETE,
    STAGE_REFUSED,
    STAGE_UNRESOLVED,
    STATUS_AUTHORIZED,
    STATUS_DRAFT,
    UNRESOLVED_VALUE,
    ExitCriteriaError,
    ExitCriteriaPacketV1,
    ForwardCriterionV1,
    RegisteredExitCriteriaV1,
    assess_exit_readiness_v1,
    resolve_exit_criteria_v1,
)
from trade_platform.paper_incubation_report_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    STATE_PROFESSIONALLY_VALIDATED_RESERVED,
    bind_incubation_v1,
    incubation_liveness_v1,
)
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

ACCOUNT = "c" * 64
AUTHORIZATION = {"authorized_by": "owner", "authorized_on": "2026-09-25"}
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / \
    "20261017_0065_strategy_lab_incubation_exit_criteria.py"


def world() -> Registry:
    """The R6 registry of one opened, validated cycle whose rerun names its study (as the readiness module reads it)."""
    registry = Registry()
    registry.rerun["study_id"] = STUDY
    registry.packet["rerun_hash"] = identity_hash_v1(registry.rerun)
    registry.refresh()
    return registry


def values(**overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "performance_criteria": (ForwardCriterionV1("net_total_return", ">", "0"),
                                 ForwardCriterionV1("max_drawdown", "<=", "0.05")),
        "minimum_evidence_coverage": "0.5", "minimum_round_trips": 1, "maximum_chain_breaks": 0,
        "maximum_unfilled_signals": 0, "maximum_skipped_fills": 0,
        "required_cost_policy_hash": GROSS.policy().content_hash, "required_account_policy_hash": ACCOUNT,
        **AUTHORIZATION,
    }
    out.update(overrides)
    return out


def packet(registry: Registry | None = None, *, bare: bool = False, **overrides: Any) -> ExitCriteriaPacketV1:
    registry = registry or world()
    fields: dict[str, Any] = {"cycle_id": CYCLE, "study_id": STUDY, "trial_id": TRIAL,
                              "preregistration_hash": registry.packet_hash, "symbol": "ETHUSDT",
                              **({} if bare else values())}
    fields.update(overrides)
    return ExitCriteriaPacketV1(**fields)


class Rows:
    """``strategy_lab_incubation_exit_criteria`` rows for the resolver."""

    def __init__(self, *items: tuple[ExitCriteriaPacketV1, datetime]) -> None:
        self.rows = [self.row(item, at) for item, at in items]
        self.queries: list[str] = []

    @staticmethod
    def row(item: ExitCriteriaPacketV1, at: datetime) -> list[Any]:
        binding = item.frozen["binding"]
        return [item.content_hash, binding["cycle_id"], binding["study_id"], binding["trial_id"],
                binding["preregistration_hash"], item.status, copy.deepcopy(item.identity()), at]

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.queries.append(query)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self.rows]


def registered(item: ExitCriteriaPacketV1, at: datetime = STATED - timedelta(hours=2)) -> RegisteredExitCriteriaV1:
    resolved = resolve_exit_criteria_v1(Rows((item, at)), cycle_id=CYCLE, study_id=STUDY, trial_id=TRIAL)
    assert resolved is not None
    return resolved


def evidence(registry: Registry, fills: list[dict[str, Any]] | None = None,
             account: dict[str, Any] | None = None) -> CandidateEvidenceV1:
    states = [{"study_id": r[0], "trial_id": r[1], "state": r[2], "evidence_hash": r[3], "reasons": [],
               "recorded_at": r[4]} for r in registry.state_rows]
    return CandidateEvidenceV1(
        study_id=STUDY, trial_id=TRIAL, rerun_identity=registry.rerun, rerun_hash=identity_hash_v1(registry.rerun),
        packet_identity=registry.packet, packet_hash=registry.packet_hash,
        opening={"cycle_id": CYCLE, "preregistration_hash": registry.packet_hash},
        validation_identity=registry.validation, validation_hash=registry.validation_hash, states=states,
        fills=fills or [], account_policy=account)


def round_trip(start: int = 1, end: int = 60) -> list[dict[str, Any]]:
    first = fill(start)
    return [first, fill(end, first)]


AS_OF = STATED + timedelta(hours=2)


def assess(registry: Registry, criteria: Any, *, fills: list[dict[str, Any]] | None = None,
           resolution: Any = "registry", account: dict[str, Any] | None = None,
           as_of: datetime = AS_OF) -> exits.ExitReadinessV1:
    return assess_exit_readiness_v1(
        evidence(registry, round_trip() if fills is None else fills,
                 {"status": "ACTIVE", "content_hash": ACCOUNT} if account is None else account),
        cost_policy=GROSS, as_of=as_of, resolution=resolve(registry) if resolution == "registry" else resolution,
        criteria=criteria)


LIVE_AS_OF = DAY0 + timedelta(minutes=120)


def live_world() -> Registry:
    """``world()`` on the liveness fixtures' symbol, its candidate INCUBATING since their DAY0."""
    registry = world()
    registry.packet["symbol"] = "BTCUSDT"
    registry.refresh()
    registry.validation["holdout"]["symbol"] = "BTCUSDT"
    registry.state_rows = [(*row[:3], registry.validation_hash, DAY0) for row in registry.state_rows]
    return registry


def watched_by_runner(registry: Registry) -> SimpleNamespace:
    return SimpleNamespace(study=SimpleNamespace(study_id=STUDY, content_hash=registry.packet["study_content_hash"]),
                           trial_id=TRIAL, symbol="BTCUSDT", authority="INCUBATING",
                           authority_evidence_hash=registry.validation_hash)


def live_exit(registry: Registry, store: MemoryStore, criteria: Any) -> exits.ExitReadinessV1:
    resolution = resolve(registry)
    (binding,) = resolution.bindings
    liveness = incubation_liveness_v1(store.all(), binding, study_content_hash=registry.packet["study_content_hash"],
                                      as_of=LIVE_AS_OF, acceptance=acceptance(), sealed=FULL_SEALS, **BOUNDS)
    ev = dataclasses.replace(evidence(registry, [], {"status": "ACTIVE", "content_hash": ACCOUNT}),
                             runner_liveness=liveness)
    return assess_exit_readiness_v1(ev, cost_policy=GROSS, as_of=LIVE_AS_OF, resolution=resolution,
                                    criteria=criteria)


class MirrorTests(unittest.TestCase):
    def test_the_migration_pins_the_identity_constants(self) -> None:
        text = MIGRATION.read_text(encoding="utf-8")
        for constant in (readiness.PROMOTION_V1, exits.ACCOUNT_ACTIVATION_V1, 'revision = "20261017_0065"',
                         "WHERE status='AUTHORIZED'"):
            self.assertIn(constant, text)

    def test_no_stage_is_a_candidate_state_and_validated_stays_reserved(self) -> None:
        canonical = {state.value for state in validation.CandidateStateV1}
        self.assertTrue(canonical.isdisjoint(exits.STAGES_V1))
        self.assertNotIn(STATE_PROFESSIONALLY_VALIDATED_RESERVED, exits.STAGES_V1)
        self.assertTrue(set(exits.STAGES_V1).isdisjoint(readiness.STAGES_V1))


class PacketTests(unittest.TestCase):
    def test_a_bare_packet_is_a_draft_with_every_slot_unresolved(self) -> None:
        bare = packet(bare=True)
        self.assertEqual(STATUS_DRAFT, bare.status)
        self.assertEqual((exits.UNRESOLVED_PERFORMANCE, exits.UNRESOLVED_COVERAGE, exits.UNRESOLVED_ROUND_TRIPS,
                          exits.UNRESOLVED_CHAIN_BREAKS, exits.UNRESOLVED_UNFILLED, exits.UNRESOLVED_SKIPPED,
                          exits.UNRESOLVED_COST_POLICY, exits.UNRESOLVED_ACCOUNT_POLICY,
                          exits.UNRESOLVED_AUTHORIZATION), bare.unresolved)
        self.assertEqual({UNRESOLVED_VALUE}, set(bare.thresholds().values()))
        self.assertEqual(list(exits.REQUIRED_FORWARD_EVIDENCE_V1), bare.identity()["required_forward_evidence"])
        self.assertEqual(readiness.PROMOTION_V1, bare.identity()["promotion"])

    def test_an_authorized_complete_packet_is_content_addressed(self) -> None:
        full = packet()
        self.assertEqual((STATUS_AUTHORIZED, ()), (full.status, full.unresolved))
        self.assertEqual(identity_hash_v1(full.identity()), full.content_hash)
        self.assertEqual(full.content_hash, packet().content_hash)
        same = packet(minimum_evidence_coverage="0.50", performance_criteria=(
            ForwardCriterionV1("net_total_return", ">", "0.000"), ForwardCriterionV1("max_drawdown", "<=", ".050")))
        self.assertEqual(full.content_hash, same.content_hash)
        self.assertNotEqual(full.content_hash, packet(maximum_chain_breaks=1).content_hash)
        self.assertEqual("0.05", full.thresholds()["performance"][1]["threshold"])

    def test_values_without_an_authorization_are_refused(self) -> None:
        for change in ({"authorized_by": None, "authorized_on": None}, ):
            with self.assertRaisesRegex(ExitCriteriaError, "unauthorized_threshold_values"):
                packet(**change)
        with self.assertRaisesRegex(ExitCriteriaError, "unauthorized_threshold_values"):
            packet(bare=True, minimum_round_trips=50)
        with self.assertRaisesRegex(ExitCriteriaError, "authorization_needs_authorized_by_and_authorized_on"):
            packet(authorized_on=None)
        with self.assertRaisesRegex(ExitCriteriaError, "authorization_without_values"):
            packet(bare=True, **AUTHORIZATION)
        with self.assertRaisesRegex(ExitCriteriaError, "authorized_on_must_be_an_iso_date"):
            packet(authorized_on="25/09/2026")
        with self.assertRaisesRegex(ExitCriteriaError, "authorized_by_required"):
            packet(authorized_by=" ")

    def test_a_partial_authorization_stays_draft_and_every_threshold_unresolved(self) -> None:
        partial = packet(bare=True, minimum_round_trips=50, **AUTHORIZATION)
        self.assertEqual(STATUS_DRAFT, partial.status)
        self.assertNotIn(exits.UNRESOLVED_ROUND_TRIPS, partial.unresolved)
        self.assertNotIn(exits.UNRESOLVED_AUTHORIZATION, partial.unresolved)
        self.assertIn(exits.UNRESOLVED_COST_POLICY, partial.unresolved)
        self.assertEqual({UNRESOLVED_VALUE}, set(partial.thresholds().values()))

    def test_contradictory_or_malformed_packets_are_refused(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"performance_criteria": (ForwardCriterionV1("net_total_return", ">", "0.1"),
                                       ForwardCriterionV1("net_total_return", "<", "0.05"))}, "empty_range"),
            ({"performance_criteria": (ForwardCriterionV1("net_total_return", ">=", "0.1"),
                                       ForwardCriterionV1("net_total_return", "<", "0.1"))}, "empty_range"),
            ({"performance_criteria": (ForwardCriterionV1("max_drawdown", "<=", "0.1"),
                                       ForwardCriterionV1("max_drawdown", "<=", "0.1"))}, "duplicate"),
            ({"performance_criteria": (ForwardCriterionV1("max_drawdown", "<=", "0.1"),
                                       ForwardCriterionV1("max_drawdown", "<", "0.2"))}, "redundant_bounds"),
            ({"performance_criteria": ("net_total_return > 0",)}, "must_be_forward_criteria"),
            ({"minimum_evidence_coverage": "0"}, "coverage_must_be_in"),
            ({"minimum_evidence_coverage": "1.5"}, "coverage_must_be_in"),
            ({"minimum_evidence_coverage": "NaN"}, "must_be_finite"),
            ({"minimum_evidence_coverage": 0.5}, "decimal_text"),
            ({"minimum_round_trips": 0}, "minimum_round_trips_must_be"),
            ({"maximum_chain_breaks": -1}, "maximum_chain_breaks_must_be"),
            ({"maximum_unfilled_signals": True}, "maximum_unfilled_signals_must_be"),
            ({"required_cost_policy_hash": "X" * 64}, "sha256_hex"),
            ({"cycle_id": "cycle-x"}, "cycle_id_malformed"),
            ({"trial_id": "not-a-uuid"}, "trial_id_must_be_a_uuid"),
            ({"preregistration_hash": None}, "preregistration_hash_required"),
        ]
        for change, reason in cases:
            with self.subTest(change=change), self.assertRaisesRegex(ExitCriteriaError, reason):
                fields = {"cycle_id": CYCLE, "study_id": STUDY, "trial_id": TRIAL,
                          "preregistration_hash": world().packet_hash, "symbol": "ETHUSDT", **values(), **change}
                ExitCriteriaPacketV1(**fields)
        for bad in (("sharpe", ">", "1"), ("max_drawdown", "==", "0"), ("max_drawdown", "<", "inf")):
            with self.subTest(bad=bad), self.assertRaises(ExitCriteriaError):
                ForwardCriterionV1(*bad)

    def test_a_stored_identity_rebuilds_exactly_or_is_refused(self) -> None:
        full = packet()
        self.assertEqual(full.content_hash, ExitCriteriaPacketV1.from_identity(full.identity()).content_hash)
        forged = full.identity()
        forged["status"] = STATUS_DRAFT
        with self.assertRaisesRegex(ExitCriteriaError, "do_not_rebuild"):
            ExitCriteriaPacketV1.from_identity(forged)
        smuggled = packet(bare=True).identity()
        smuggled["criteria"]["minimum_round_trips"] = 50  # a value without an authorization
        with self.assertRaisesRegex(ExitCriteriaError, "unauthorized_threshold_values"):
            ExitCriteriaPacketV1.from_identity(smuggled)
        with self.assertRaisesRegex(ExitCriteriaError, "malformed"):
            ExitCriteriaPacketV1.from_identity({"binding": {}})


class ResolverTests(unittest.TestCase):
    def test_the_authorized_packet_wins_over_drafts_and_the_resolver_only_reads(self) -> None:
        draft, full = packet(bare=True), packet()
        rows = Rows((draft, STATED - timedelta(days=2)), (full, STATED - timedelta(days=1)))
        resolved = resolve_exit_criteria_v1(rows, cycle_id=CYCLE, study_id=STUDY, trial_id=TRIAL)
        assert resolved is not None
        self.assertEqual((full.content_hash, True), (resolved.criteria_hash, resolved.issued))
        self.assertTrue(all(query.lstrip().upper().startswith("SELECT") for query in rows.queries))
        latest = packet(bare=True, minimum_round_trips=5, **AUTHORIZATION)
        only_drafts = Rows((draft, STATED - timedelta(days=2)), (latest, STATED - timedelta(days=1)))
        resolved_draft = resolve_exit_criteria_v1(only_drafts, cycle_id=CYCLE, study_id=STUDY, trial_id=TRIAL)
        assert resolved_draft is not None
        self.assertEqual(latest.content_hash, resolved_draft.criteria_hash)
        self.assertIsNone(resolve_exit_criteria_v1(Rows(), cycle_id=CYCLE, study_id=STUDY, trial_id=TRIAL))

    def test_two_authorized_packets_for_one_candidate_are_ambiguous(self) -> None:
        rows = Rows((packet(), STATED), (packet(maximum_chain_breaks=3), STATED))
        with self.assertRaisesRegex(ExitCriteriaError, "conflicting_authorized_exit_criteria"):
            resolve_exit_criteria_v1(rows, cycle_id=CYCLE, study_id=STUDY, trial_id=TRIAL)

    def test_forged_or_mismatched_rows_are_refused(self) -> None:
        def refused(change: Any, reason: str) -> None:
            rows = Rows((packet(), STATED))
            change(rows.rows[0])
            with self.assertRaisesRegex(ExitCriteriaError, reason):
                resolve_exit_criteria_v1(rows, cycle_id=CYCLE, study_id=STUDY, trial_id=TRIAL)

        refused(lambda row: row.__setitem__(0, "0" * 64), "does_not_rederive")
        refused(lambda row: row[6]["criteria"].__setitem__("maximum_chain_breaks", 9), "does_not_rederive")
        refused(lambda row: row.__setitem__(5, STATUS_DRAFT), "row_contradicts_its_identity")
        refused(lambda row: row.__setitem__(4, "e" * 64), "row_contradicts_its_identity")
        refused(lambda row: row.__setitem__(7, datetime(2026, 9, 1)), "timezone_aware")

        def other_schema(row: list[Any]) -> None:
            row[6]["schema_version"] = "incubation-exit-criteria-v0"
            row[0] = identity_hash_v1(row[6])

        refused(other_schema, "schema_version_is_not_current")
        rows = Rows((packet(), STATED))
        with self.assertRaisesRegex(ExitCriteriaError, "of_another_candidate"):
            resolve_exit_criteria_v1(rows, cycle_id=CYCLE, study_id=STUDY, trial_id=STUDY)

    def test_a_registered_packet_cannot_be_constructed_or_swapped(self) -> None:
        with self.assertRaisesRegex(ExitCriteriaError, "issued_only_by_the_resolver"):
            RegisteredExitCriteriaV1(packet(), packet().content_hash, STATED)
        genuine = registered(packet())
        swapped = dataclasses.replace(genuine, packet=packet(maximum_chain_breaks=7))
        self.assertFalse(swapped.issued)
        restamped = dataclasses.replace(genuine, recorded_at=STATED - timedelta(days=30))
        self.assertFalse(restamped.issued)


class ExitReadinessTests(unittest.TestCase):
    def test_authorized_criteria_still_block_on_every_missing_evidence_input(self) -> None:
        registry = world()
        result = assess(registry, registered(packet(registry)))
        self.assertEqual(STAGE_EVIDENCE_INCOMPLETE, result.stage)
        self.assertEqual(readiness.STAGE_INCUBATION_INCOMPLETE, result.readiness_stage)
        self.assertEqual((), result.refusals)
        self.assertNotIn(NEED_EXIT_CRITERIA, result.blockers)
        for blocker in (NEED_FORWARD_FUNDING, NEED_LIVENESS, NEED_NET_FORWARD_METRICS,
                        readiness.NEED_PARITY, readiness.NEED_FEE_SCHEDULE, readiness.NEED_COST_COMPLETE,
                        readiness.NEED_SIZING):
            self.assertIn(blocker, result.blockers)
        # Runner liveness is the continuity contract now: missing evidence, not a missing contract.
        self.assertNotIn(NEED_RUNNER_CONTRACT, result.blockers)
        self.assertEqual({exits.EVIDENCE_FORWARD_FUNDING: exits.INPUT_MISSING,
                          exits.EVIDENCE_RUNNER_CONTINUITY: exits.INPUT_MISSING,
                          exits.EVIDENCE_PARITY: exits.INPUT_MISSING, exits.EVIDENCE_FEES: exits.INPUT_MISSING,
                          exits.EVIDENCE_RISK: exits.INPUT_MISSING}, result.evidence_inputs)
        self.assertNotIn(CRITERIA_AFTER_INCUBATION, result.blockers)
        self.assertTrue(result.facts["registry_bound"])
        # The readiness module's own binding is caller-supplied; its provenance blocker is kept, never cleared.
        self.assertEqual("INCUBATION_BINDING_NOT_REGISTRY_RESOLVED", NEED_REGISTRY_BINDING)
        self.assertIn(NEED_REGISTRY_BINDING, result.blockers)
        payload = result.payload()
        self.assertEqual((readiness.PROMOTION_V1, exits.ACCOUNT_ACTIVATION_V1),
                         (payload["promotion"], payload["account_activation"]))
        self.assertEqual(result.assessment_hash, assess(registry, registered(packet(registry))).assessment_hash)

    def test_without_criteria_or_with_a_draft_the_criteria_stay_unresolved(self) -> None:
        registry = world()
        none = assess(registry, None)
        self.assertEqual(STAGE_UNRESOLVED, none.stage)
        self.assertIn(NEED_EXIT_CRITERIA, none.blockers)
        self.assertEqual({}, none.thresholds)
        draft = assess(registry, registered(packet(registry, bare=True)))
        self.assertEqual(STAGE_UNRESOLVED, draft.stage)
        self.assertIn(f"{OR7}:{exits.UNRESOLVED_PERFORMANCE}", draft.blockers)
        self.assertIn(f"{OR6}:{exits.UNRESOLVED_COST_POLICY}", draft.blockers)
        self.assertIn(f"{OR11}:{exits.UNRESOLVED_ACCOUNT_POLICY}", draft.blockers)
        self.assertIn(NEED_EXIT_CRITERIA, draft.blockers)
        self.assertEqual({UNRESOLVED_VALUE}, set(draft.thresholds.values()))
        self.assertEqual(("OR-11", "OR-6", "OR-7"), draft.owner_gates)

    def test_an_in_memory_packet_never_satisfies_the_criteria(self) -> None:
        registry = world()
        genuine = registered(packet(registry))
        forged = dataclasses.replace(genuine, packet=packet(registry, maximum_unfilled_signals=99))
        for criteria in (packet(registry), forged):
            with self.subTest(criteria=type(criteria).__name__):
                result = assess(registry, criteria)
                self.assertEqual(STAGE_REFUSED, result.stage)
                self.assertIn("EXIT_CRITERIA_NOT_REGISTRY_RESOLVED", result.refusals)

    def test_a_caller_supplied_binding_never_satisfies_the_registry_requirement(self) -> None:
        registry = world()
        ev = evidence(registry, round_trip())
        caller = bind_incubation_v1(packet_identity=registry.packet, packet_hash=registry.packet_hash,
                                    validation_identity=registry.validation, validation_hash=registry.validation_hash,
                                    states=ev.states, trial_id=TRIAL)
        result = assess(registry, registered(packet(registry)), resolution=caller)
        self.assertEqual(STAGE_REFUSED, result.stage)
        self.assertIn("INCUBATION_RESOLUTION_NOT_ISSUED_BY_THE_REGISTRY", result.refusals)
        missing = assess(registry, registered(packet(registry)), resolution=None)
        self.assertIn(NEED_REGISTRY_BINDING, missing.blockers)
        self.assertFalse(missing.facts["registry_bound"])

    def test_an_unopened_cycle_lists_the_registry_reason(self) -> None:
        registry = world()
        closed = world()
        closed.opened = closed.validated = False
        result = assess(registry, registered(packet(registry)), resolution=resolve(closed))
        self.assertIn(f"{NEED_REGISTRY_BINDING}:HOLDOUT_NOT_OPENED_FOR_CYCLE_OR_7", result.blockers)
        self.assertEqual(STAGE_EVIDENCE_INCOMPLETE, result.stage)

    def test_stale_references_are_refused(self) -> None:
        registry = world()
        other = world()
        other.packet["minimum_trades"] = 30  # another preregistration of the same cycle and study
        other.refresh()
        stale_criteria = assess(registry, registered(packet(other)))
        self.assertEqual(STAGE_REFUSED, stale_criteria.stage)
        self.assertIn("STALE_EXIT_CRITERIA_PREREGISTRATION_REFERENCE", stale_criteria.refusals)
        self.assertIn("EXIT_CRITERIA_ARE_NOT_OF_THE_REGISTRY_RESOLVED_BINDING", stale_criteria.refusals)
        # The caller's chain is self-consistent but is not the one the registry resolved.
        stale_chain = assess(registry, registered(packet(registry)), resolution=resolve(other))
        self.assertIn("STALE_EVIDENCE_CHAIN_IS_NOT_THE_REGISTRY_RESOLVED_ONE", stale_chain.refusals)

    def test_criteria_of_another_candidate_are_refused(self) -> None:
        registry = world()
        theirs = packet(registry, trial_id="6f1c2a52-0000-4000-8000-0000000000a2")
        resolved = resolve_exit_criteria_v1(Rows((theirs, STATED)), cycle_id=CYCLE, study_id=STUDY,
                                            trial_id="6f1c2a52-0000-4000-8000-0000000000a2")
        result = assess(registry, resolved)
        self.assertIn("EXIT_CRITERIA_OF_ANOTHER_CANDIDATE", result.refusals)

    def test_criteria_recorded_after_incubation_began_are_an_owner_finding(self) -> None:
        registry = world()
        late = assess(registry, registered(packet(registry), STATED + timedelta(minutes=1)))
        self.assertIn(CRITERIA_AFTER_INCUBATION, late.blockers)

    def test_gap_tolerance_is_checked_against_the_authorized_values(self) -> None:
        registry = world()
        first = fill(1)
        missed = {**fill(30, first), "status": FILL_STATUS_BAR_NOT_OBSERVED, "fill_price": None}
        fills = [first, missed]
        strict = assess(registry, registered(packet(registry)), fills=fills)
        self.assertTrue(any(item.startswith("FORWARD_EVIDENCE_GAPS_EXCEED_AUTHORIZED_TOLERANCE:")
                            and "unfilled=1>0" in item for item in strict.blockers))
        self.assertFalse(any("GAP_TOLERANCE_UNDECIDED" in item for item in strict.blockers))
        tolerant = assess(registry, registered(packet(registry, maximum_unfilled_signals=1,
                                                       maximum_chain_breaks=5, maximum_skipped_fills=5)),
                          fills=fills)
        self.assertFalse(any("GAP" in item for item in tolerant.blockers))
        undecided = assess(registry, None, fills=fills)
        self.assertTrue(any("GAP_TOLERANCE_UNDECIDED" in item for item in undecided.blockers))

    def test_coverage_and_round_trips_are_checked_against_the_authorized_minimums(self) -> None:
        registry = world()
        demanding = registered(packet(registry, minimum_evidence_coverage="1", minimum_round_trips=3))
        result = assess(registry, demanding, as_of=STATED + timedelta(days=3))
        self.assertIn("FORWARD_ROUND_TRIPS_BELOW_AUTHORIZED_MINIMUM:1/3", result.blockers)
        self.assertTrue(any(item.startswith("FORWARD_EVIDENCE_COVERAGE_BELOW_AUTHORIZED_MINIMUM:")
                            for item in result.blockers))
        lenient = registered(packet(registry, minimum_evidence_coverage="0.000001"))
        ok = assess(registry, lenient, as_of=STATED + timedelta(hours=1, minutes=1))
        self.assertFalse(any("BELOW_AUTHORIZED_MINIMUM" in item for item in ok.blockers))

    def test_the_forward_evidence_must_carry_the_authorized_policy_identities(self) -> None:
        registry = world()
        other_cost = registered(packet(registry, required_cost_policy_hash="f" * 64))
        result = assess(registry, other_cost, account={"status": "ACTIVE", "content_hash": "e" * 64})
        self.assertIn(f"{OR6}:FORWARD_FILLS_NOT_UNDER_THE_AUTHORIZED_COST_POLICY", result.blockers)
        self.assertIn(f"{OR11}:ACCOUNT_POLICY_IS_NOT_THE_AUTHORIZED_ONE", result.blockers)

    def test_inconsistent_upstream_evidence_is_a_refusal(self) -> None:
        registry = world()
        ev = evidence(registry, round_trip())
        forged = dataclasses.replace(ev, validation_hash="0" * 64)
        result = assess_exit_readiness_v1(forged, cost_policy=GROSS, as_of=AS_OF, resolution=resolve(registry),
                                          criteria=registered(packet(registry)))
        self.assertEqual(STAGE_REFUSED, result.stage)
        self.assertTrue(any(item.startswith("READINESS:") for item in result.refusals))

    def test_runner_continuity_follows_the_persisted_liveness(self) -> None:
        registry = live_world()
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(120), watched=watched_by_runner(registry))
        authorized = registered(packet(registry, symbol="BTCUSDT", minimum_evidence_coverage="0.9"))
        present = live_exit(registry, store, authorized)
        self.assertEqual(exits.INPUT_PRESENT, present.evidence_inputs[exits.EVIDENCE_RUNNER_CONTINUITY])
        self.assertNotIn(NEED_LIVENESS, present.blockers)
        self.assertNotIn(NEED_RUNNER_CONTRACT, present.blockers)
        self.assertFalse(any("COVERAGE_BELOW" in item for item in present.blockers))
        # Every other gate stays: nothing is complete, promoted or activated.
        self.assertEqual(STAGE_EVIDENCE_INCOMPLETE, present.stage)
        for blocker in (NEED_FORWARD_FUNDING, NEED_NET_FORWARD_METRICS, readiness.NEED_FEE_SCHEDULE,
                        readiness.NEED_SIZING, readiness.NEED_PARITY):
            self.assertIn(blocker, present.blockers)
        self.assertEqual(exits.ACCOUNT_ACTIVATION_V1, present.payload()["account_activation"])

    def test_unproven_runner_time_is_judged_by_the_authorized_coverage_only(self) -> None:
        registry = live_world()
        store = MemoryStore()
        run_session(store, start_minute=0, minutes=range(60), watched=watched_by_runner(registry))  # stops at 60
        gap = readiness.LIVENESS_GAP_TOLERANCE_PREFIX
        undecided = live_exit(registry, store, None)
        self.assertTrue(any(item.startswith(gap) for item in undecided.blockers))
        strict = live_exit(registry, store, registered(packet(registry, symbol="BTCUSDT",
                                                               minimum_evidence_coverage="0.9")))
        self.assertFalse(any(item.startswith(gap) for item in strict.blockers))
        self.assertIn("FORWARD_EVIDENCE_COVERAGE_BELOW_AUTHORIZED_MINIMUM:0.5<0.9", strict.blockers)
        lenient = live_exit(registry, store, registered(packet(registry, symbol="BTCUSDT",
                                                                minimum_evidence_coverage="0.5")))
        self.assertFalse(any("COVERAGE_BELOW" in item or item.startswith(gap) for item in lenient.blockers))

    def test_a_tampered_runner_chain_fails_continuity(self) -> None:
        registry = live_world()
        store = MemoryStore()
        recorder = run_session(store, start_minute=0, minutes=range(120), watched=watched_by_runner(registry))
        key = f"runner-liveness:{recorder.runner_session_id}:3"
        store.rows[key] = {"content_hash": store.rows[key]["content_hash"],
                           "identity": {**store.rows[key]["identity"], "sequence": 99}}
        result = live_exit(registry, store, registered(packet(registry, symbol="BTCUSDT")))
        self.assertEqual(exits.INPUT_FAILED, result.evidence_inputs[exits.EVIDENCE_RUNNER_CONTINUITY])
        self.assertEqual(readiness.STAGE_INCUBATION_EVIDENCE_FAILED, result.readiness_stage)
        self.assertNotEqual(exits.STAGE_COMPLETE, result.stage)

    def test_the_assessment_never_reaches_a_validated_or_complete_stage(self) -> None:
        registry = world()
        for criteria in (None, registered(packet(registry, bare=True)), registered(packet(registry))):
            result = assess(registry, criteria, as_of=STATED + timedelta(days=40))
            self.assertNotEqual(exits.STAGE_COMPLETE, result.stage)
            self.assertNotIn(STATE_PROFESSIONALLY_VALIDATED_RESERVED, result.payload().values())


if __name__ == "__main__":
    unittest.main()
