"""Candidate lifecycle readiness: read-only, non-promoting, every missing item named."""

from __future__ import annotations

import dataclasses
import random
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from trade_platform import account_policy_v1, strategy_lab_authority_rerun_v1
from trade_platform import candidate_lifecycle_readiness_v1 as readiness
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.activation_readiness_v1 import OR6, OR7, OR11
from trade_platform.candidate_lifecycle_readiness_v1 import (
    NEED_EXIT_CRITERIA,
    NEED_FEE_SCHEDULE,
    NEED_FORWARD_FUNDING,
    NEED_HOLDOUT_OPENING,
    NEED_HOLDOUT_VALIDATION,
    NEED_LIVENESS,
    NEED_PARITY,
    NEED_PREREGISTRATION,
    NEED_SIZING,
    STAGE_DECIMAL_SELECTION_ESTABLISHED,
    STAGE_ELIGIBLE,
    STAGE_EVIDENCE_INCONSISTENT,
    STAGE_FORWARD_EVIDENCE_INCOMPLETE,
    STAGE_HOLDOUT_REJECTED,
    STAGE_INCUBATION_EVIDENCE_FAILED,
    STAGE_INCUBATION_INCOMPLETE,
    STAGE_SEARCH_ONLY,
    CandidateEvidenceV1,
    CandidateReadinessError,
    CandidateReadinessV1,
    assess_candidate_readiness_v1,
)
from trade_platform.paper_incubation_report_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    FILL_STATUS_FILLED,
    STATE_PROFESSIONALLY_VALIDATED_RESERVED,
)
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

GROSS = gross_cost_policy_v1()
MINUTE = 60_000_000
STUDY = "6f1c2a52-0000-4000-8000-000000000001"
OTHER_STUDY = "6f1c2a52-0000-4000-8000-000000000002"
TRIAL = "6f1c2a52-0000-4000-8000-0000000000a1"
OTHER_TRIAL = "6f1c2a52-0000-4000-8000-0000000000a2"
CYCLE = "cycle-2026-08-20"
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
SINCE_MICROS = int(SINCE.timestamp()) * 1_000_000
AS_OF = SINCE + timedelta(days=60)


def rerun(status: str = "ESTABLISHED", selected: tuple[str, ...] = (TRIAL, OTHER_TRIAL),
          study: str = STUDY) -> dict[str, Any]:
    return {"schema_version": "strategy-lab-authority-rerun-v1", "study_id": study, "candidate_set_hash": "b" * 64,
            "authoritative_selection": {"status": status, "selected": [{"trial_id": t} for t in selected]}}


def packet(rerun_hash: str, **overrides: Any) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "schema_version": "strategy-lab-preregistration-v1",
        "cycle": {"cycle_id": CYCLE, "holdout_start": "2026-08-20T00:00:00+00:00", "note": "n"},
        "study_id": STUDY, "study_content_hash": "a" * 64, "candidate_set_hash": "b" * 64, "rerun_hash": rerun_hash,
        "rerun_selection_status": "ESTABLISHED", "candidates": [TRIAL, OTHER_TRIAL], "symbol": "BTCUSDT",
        "holdout_start": "2026-08-20T00:00:00+00:00", "holdout_end_exclusive": "2026-08-27T00:00:00+00:00",
        "lags_micros": [0], "acceptance_criteria": [], "minimum_trades": 1, "cost_policy": None,
        "incubation_days": 28, "lag_sweep_rule": "r", "funding_rule": "f",
        "authorized_by": "owner", "authorized_on": "2026-08-27", "status": "AUTHORIZED", "unresolved": [],
    }
    identity.update(overrides)
    return identity


def draft(rerun_hash: str) -> dict[str, Any]:
    """The prepared packet as it stands: no fee schedule, no drawdown cap decision, no authorization."""
    return packet(rerun_hash, authorized_by=None, authorized_on=None, status="DRAFT",
                  unresolved=["MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6",
                              "MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7", "MISSING_OWNER_AUTHORIZATION"])


def validation_of(packet_hash: str, *, verdict: str = "INCUBATING", **overrides: Any) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "schema_version": "strategy-lab-holdout-validation-v1", "preregistration_hash": packet_hash,
        "study_id": STUDY, "cycle_id": CYCLE,
        "candidates": [{"trial_id": TRIAL, "state": verdict,
                        "reasons": [] if verdict == "INCUBATING" else ["HOLDOUT_CRITERIA_NOT_MET"]},
                       {"trial_id": OTHER_TRIAL, "state": "HOLDOUT_FAILED_REJECTED", "reasons": ["x"]}],
        "claim_ceiling": "CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED",
    }
    identity.update(overrides)
    return identity


def states_of(validation_hash: str, verdict: str = "INCUBATING") -> list[dict[str, Any]]:
    return [{"study_id": STUDY, "trial_id": TRIAL, "state": verdict, "evidence_hash": validation_hash,
             "reasons": [], "recorded_at": SINCE},
            {"study_id": STUDY, "trial_id": OTHER_TRIAL, "state": "HOLDOUT_FAILED_REJECTED",
             "evidence_hash": validation_hash, "reasons": ["x"], "recorded_at": SINCE}]


def fill(minute: int, *, previous: dict[str, Any] | None = None, target_from: int = 0, target_to: int = 1,
         status: str = FILL_STATUS_FILLED, trial: str = TRIAL) -> dict[str, Any]:
    opened = SINCE_MICROS + minute * MINUTE
    return {"signal_id": str(uuid4()), "study_id": STUDY, "trial_id": trial, "symbol": "BTCUSDT", "status": status,
            "target_from": target_from, "target_to": target_to,
            "previous_signal_id": None if previous is None else previous["signal_id"],
            "decided_at_micros": opened - MINUTE // 2, "fill_bar_open_micros": opened,
            "fill_price": "100" if status == FILL_STATUS_FILLED else None,
            "cost_policy_hash": GROSS.policy().content_hash}


def run(start: int, minutes: list[int]) -> list[dict[str, Any]]:
    out = [fill(start)]
    for minute in minutes:
        out.append(fill(minute, previous=out[-1], target_from=out[-1]["target_to"],
                        target_to=0 if out[-1]["target_to"] else 1))
    return out


def chain(*, upto: str = "fills", packet_identity: dict[str, Any] | None = None, verdict: str = "INCUBATING",
          fills: list[dict[str, Any]] | None = None, **overrides: Any) -> CandidateEvidenceV1:
    """The stored chain of TRIAL up to ``upto`` (rerun, packet, opening, validation, fills)."""
    steps = ["rerun", "packet", "opening", "validation", "fills"]
    reach = steps.index(upto)
    fields: dict[str, Any] = {"study_id": STUDY, "trial_id": TRIAL}
    rerun_identity = rerun()
    rerun_hash = identity_hash_v1(rerun_identity)
    fields.update(rerun_identity=rerun_identity, rerun_hash=rerun_hash)
    if reach >= 1:
        identity = packet(rerun_hash) if packet_identity is None else packet_identity
        fields.update(packet_identity=identity, packet_hash=identity_hash_v1(identity))
    if reach >= 2:
        fields["opening"] = {"cycle_id": CYCLE, "preregistration_hash": fields["packet_hash"]}
    if reach >= 3:
        judged = validation_of(fields["packet_hash"], verdict=verdict)
        fields.update(validation_identity=judged, validation_hash=identity_hash_v1(judged),
                      states=states_of(identity_hash_v1(judged), verdict))
    if reach >= 4:
        fields["fills"] = run(1, [60, 120]) if fills is None else fills
    fields.update(overrides)
    return CandidateEvidenceV1(**fields)


def assess(evidence: CandidateEvidenceV1, *, as_of: datetime = AS_OF) -> CandidateReadinessV1:
    return assess_candidate_readiness_v1(evidence, cost_policy=GROSS, as_of=as_of)


class MirrorTests(unittest.TestCase):
    def test_mirrored_names_equal_their_authorities(self) -> None:
        self.assertEqual(readiness.SELECTION_ESTABLISHED, strategy_lab_authority_rerun_v1.SELECTION_ESTABLISHED)
        self.assertEqual(readiness.ACCOUNT_POLICY_ACTIVE, account_policy_v1.STATUS_ACTIVE)
        self.assertEqual(set(readiness._PACKET_GATES) - {"MISSING_OWNER_AUTHORIZATION"}, {
            validation.UNRESOLVED_HOLDOUT_END, validation.UNRESOLVED_CRITERIA, validation.UNRESOLVED_MINIMUM_TRADES,
            validation.UNRESOLVED_INCUBATION, validation.UNRESOLVED_COST_BASIS})
        self.assertIn(validation.UNRESOLVED_AUTHORIZATION, readiness._PACKET_GATES)

    def test_no_new_candidate_state_and_validated_stays_reserved(self) -> None:
        canonical = {state.value for state in validation.CandidateStateV1}
        self.assertEqual({"INCUBATING", "HOLDOUT_FAILED_REJECTED"}, canonical)
        self.assertNotIn(STATE_PROFESSIONALLY_VALIDATED_RESERVED, readiness.STAGES_V1)
        self.assertTrue(canonical.isdisjoint(readiness.STAGES_V1))


class PathTests(unittest.TestCase):
    def test_search_only_without_an_established_selection(self) -> None:
        bare = assess(CandidateEvidenceV1(study_id=STUDY, trial_id=TRIAL))
        self.assertEqual(STAGE_SEARCH_ONLY, bare.stage)
        self.assertIsNone(bare.canonical_state)
        failed = rerun(status="FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED")
        not_established = assess(CandidateEvidenceV1(study_id=STUDY, trial_id=TRIAL, rerun_identity=failed,
                                                     rerun_hash=identity_hash_v1(failed)))
        self.assertEqual(STAGE_SEARCH_ONLY, not_established.stage)
        self.assertEqual("DECIMAL_SELECTION_NOT_ESTABLISHED:FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED",
                         not_established.blockers[0])
        outside = rerun(selected=(OTHER_TRIAL,))
        self.assertEqual("CANDIDATE_NOT_IN_THE_DECIMAL_SELECTION", assess(CandidateEvidenceV1(
            study_id=STUDY, trial_id=TRIAL, rerun_identity=outside,
            rerun_hash=identity_hash_v1(outside))).blockers[0])
        # The whole remaining path is named, owner gates included.
        self.assertEqual(("OR-11", "OR-6", "OR-7"), bare.owner_gates)

    def test_selected_candidate_without_a_packet(self) -> None:
        result = assess(chain(upto="rerun"))
        self.assertEqual(STAGE_DECIMAL_SELECTION_ESTABLISHED, result.stage)
        self.assertEqual((NEED_PREREGISTRATION, NEED_HOLDOUT_OPENING, NEED_HOLDOUT_VALIDATION), result.blockers[:3])

    def test_the_prepared_draft_packet_names_each_owner_gap(self) -> None:
        identity = draft(identity_hash_v1(rerun()))
        result = assess(chain(upto="packet", packet_identity=identity))
        self.assertEqual(STAGE_DECIMAL_SELECTION_ESTABLISHED, result.stage)
        self.assertEqual([f"{OR7}:MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7", f"{OR7}:MISSING_OWNER_AUTHORIZATION",
                          f"{OR6}:MISSING_VERIFIED_FEE_SCHEDULE_AND_STRESS_ENVELOPE_OR_6", NEED_HOLDOUT_OPENING],
                         list(result.blockers[:4]))

    def test_an_authorized_packet_waits_for_the_opening_then_the_validation(self) -> None:
        self.assertEqual(NEED_HOLDOUT_OPENING, assess(chain(upto="packet")).blockers[0])
        opened = assess(chain(upto="opening"))
        self.assertEqual(STAGE_DECIMAL_SELECTION_ESTABLISHED, opened.stage)
        self.assertEqual(NEED_HOLDOUT_VALIDATION, opened.blockers[0])

    def test_a_cycle_spent_by_another_packet_is_named(self) -> None:
        result = assess(chain(upto="packet", opening={"cycle_id": CYCLE, "preregistration_hash": "9" * 64}))
        self.assertEqual(STAGE_DECIMAL_SELECTION_ESTABLISHED, result.stage)
        self.assertEqual(f"CYCLE_HOLDOUT_SPENT_BY_ANOTHER_PREREGISTRATION:{'9' * 64}", result.blockers[0])

    def test_holdout_rejection_is_terminal(self) -> None:
        result = assess(chain(upto="validation", verdict="HOLDOUT_FAILED_REJECTED"))
        self.assertEqual(STAGE_HOLDOUT_REJECTED, result.stage)
        self.assertEqual("HOLDOUT_FAILED_REJECTED", result.canonical_state)
        self.assertEqual(("HOLDOUT_FAILED_REJECTED_TERMINAL:HOLDOUT_CRITERIA_NOT_MET",), result.blockers)
        self.assertEqual(STAGE_EVIDENCE_INCONSISTENT,
                         assess(chain(verdict="HOLDOUT_FAILED_REJECTED")).stage)  # fills for a rejected candidate

    def test_incubating_without_forward_fills(self) -> None:
        result = assess(chain(upto="validation"))
        self.assertEqual(STAGE_INCUBATION_INCOMPLETE, result.stage)
        self.assertEqual("INCUBATING", result.canonical_state)
        self.assertEqual(28, result.evidence["required_days"])

    def test_incomplete_window_lists_the_whole_forward_path(self) -> None:
        result = assess(chain())
        self.assertEqual(STAGE_INCUBATION_INCOMPLETE, result.stage)
        self.assertTrue(result.blockers[0].startswith("INCUBATION_WINDOW_INCOMPLETE:"))
        self.assertTrue(result.blockers[0].endswith("/28_DAYS"))
        for item in (NEED_PARITY, NEED_FEE_SCHEDULE, NEED_FORWARD_FUNDING, NEED_SIZING,
                     f"{OR11}:NO_ACCOUNT_POLICY_RECORDED", NEED_EXIT_CRITERIA, NEED_LIVENESS):
            self.assertIn(item, result.blockers)

    def test_elapsed_window_is_forward_evidence_incomplete_never_eligible(self) -> None:
        fills = run(1, [28 * 24 * 60 + 1])
        replay = {item["signal_id"]: identity_hash_v1(item) for item in fills}
        active = {"status": "ACTIVE", "unresolved": []}
        result = assess(chain(fills=fills, replay_fill_hashes=replay, account_policy=active))
        self.assertEqual(STAGE_FORWARD_EVIDENCE_INCOMPLETE, result.stage)
        self.assertNotIn(NEED_PARITY, result.blockers)
        self.assertEqual((NEED_FEE_SCHEDULE, NEED_FORWARD_FUNDING, "FORWARD_EVIDENCE_NOT_COST_COMPLETE", NEED_SIZING,
                          NEED_EXIT_CRITERIA, NEED_LIVENESS), result.blockers)
        self.assertEqual("INCUBATING", result.canonical_state)
        self.assertNotEqual(STAGE_ELIGIBLE, result.stage)
        self.assertNotIn(STATE_PROFESSIONALLY_VALIDATED_RESERVED, repr(result.payload()).replace(
            readiness.PROMOTION_V1, ""))

    def test_an_unconfigured_account_policy_lists_its_or11_values(self) -> None:
        unconfigured = {"status": "UNCONFIGURED", "unresolved": ["MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11"]}
        result = assess(chain(account_policy=unconfigured))
        self.assertIn(f"{OR11}:MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11", result.blockers)


class DuplicateTests(unittest.TestCase):
    def test_a_duplicated_state_event_is_inconsistent(self) -> None:
        base = chain()
        result = assess(chain(states=[*base.states, dict(base.states[0])]))
        self.assertEqual(STAGE_EVIDENCE_INCONSISTENT, result.stage)

    def test_a_duplicated_fill_fails_the_incubation_evidence(self) -> None:
        fills = run(1, [60])
        result = assess(chain(fills=[*fills, dict(fills[-1])]))
        self.assertEqual(STAGE_INCUBATION_EVIDENCE_FAILED, result.stage)
        self.assertEqual(("INCUBATION_REPORT_REFUSED:one_fill_per_signal",), result.blockers)

    def test_a_second_incubating_state_from_another_validation_is_inconsistent(self) -> None:
        base = chain()
        extra = {**base.states[0], "evidence_hash": "f" * 64}
        self.assertEqual(STAGE_EVIDENCE_INCONSISTENT, assess(chain(states=[*base.states, extra])).stage)


class MissingEvidenceTests(unittest.TestCase):
    def test_later_evidence_without_its_predecessor_is_inconsistent(self) -> None:
        full = chain()
        cases = [
            chain(upto="rerun", validation_identity=full.validation_identity, validation_hash=full.validation_hash),
            chain(upto="packet", states=full.states),
            dataclasses.replace(chain(upto="opening"), fills=full.fills),
            CandidateEvidenceV1(study_id=STUDY, trial_id=TRIAL, packet_identity=full.packet_identity,
                                packet_hash=full.packet_hash),
            chain(upto="validation", states=[]),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(STAGE_EVIDENCE_INCONSISTENT, assess(case).stage)

    def test_an_identity_and_its_hash_go_together(self) -> None:
        with self.assertRaises(CandidateReadinessError):
            CandidateEvidenceV1(study_id=STUDY, trial_id=TRIAL, rerun_identity=rerun())
        with self.assertRaises(CandidateReadinessError):
            CandidateEvidenceV1(study_id=" ", trial_id=TRIAL)
        with self.assertRaises(CandidateReadinessError):
            assess(chain(), as_of=AS_OF.replace(tzinfo=None))

    def test_unobserved_fill_bars_are_a_forward_gap_for_the_owner(self) -> None:
        first = fill(1)
        missed = fill(60, previous=first, target_from=1, target_to=0, status=FILL_STATUS_BAR_NOT_OBSERVED)
        result = assess(chain(fills=[first, missed]))
        self.assertIn(f"{OR7}:FORWARD_EVIDENCE_GAP_TOLERANCE_UNDECIDED:chain_breaks=1,unfilled=1,skipped=0",
                      result.blockers)

    def test_live_replay_parity_violation_fails_the_evidence(self) -> None:
        fills = run(1, [60])
        replay = {item["signal_id"]: identity_hash_v1(item) for item in fills}
        replay[fills[0]["signal_id"]] = "0" * 64
        result = assess(chain(fills=fills, replay_fill_hashes=replay))
        self.assertEqual(STAGE_INCUBATION_EVIDENCE_FAILED, result.stage)
        self.assertEqual(("LIVE_REPLAY_FILL_PARITY_VIOLATION:1",), result.blockers)


class RestartTests(unittest.TestCase):
    def test_a_restart_gap_is_never_bridged_into_maturity(self) -> None:
        # Two runners 30 days apart: wall clock exceeds 28 days, evidence does not.
        fills = run(1, [60]) + run(30 * 24 * 60, [30 * 24 * 60 + 60])
        result = assess(chain(fills=fills))
        self.assertEqual(STAGE_INCUBATION_INCOMPLETE, result.stage)
        self.assertEqual(2, result.evidence["evidenced_segments"])
        self.assertTrue(result.blockers[0].startswith("INCUBATION_WINDOW_INCOMPLETE:0.0"))


class PrematureMaturityTests(unittest.TestCase):
    def test_reading_later_never_lengthens_the_clock(self) -> None:
        fills = run(1, [60, 120])
        near = assess(chain(fills=fills), as_of=SINCE + timedelta(hours=3))
        far = assess(chain(fills=fills), as_of=SINCE + timedelta(days=3650))
        self.assertEqual(STAGE_INCUBATION_INCOMPLETE, far.stage)
        self.assertEqual(near.evidence, far.evidence)

    def test_evidence_after_the_reading_instant_or_before_incubation_fails(self) -> None:
        early = assess(chain(), as_of=SINCE + timedelta(minutes=30))
        self.assertEqual(STAGE_INCUBATION_EVIDENCE_FAILED, early.stage)
        self.assertEqual(("INCUBATION_REPORT_REFUSED:fill_evidence_after_the_report_as_of",), early.blockers)
        before = assess(chain(fills=[fill(0)]))
        self.assertEqual(("INCUBATION_REPORT_REFUSED:fill_decided_before_the_candidate_was_incubating",),
                         before.blockers)

    def test_an_unauthorized_packet_never_matures_on_its_prepared_28_days(self) -> None:
        # A validation exists only after an authorized opening; a draft packet with one is refused.
        identity = draft(identity_hash_v1(rerun()))
        result = assess(chain(packet_identity=identity, fills=run(1, [40 * 24 * 60])))
        self.assertEqual(STAGE_EVIDENCE_INCONSISTENT, result.stage)
        self.assertEqual(("HOLDOUT_OPENED_FOR_A_DRAFT_PREREGISTRATION",), result.blockers)


class IdentityTests(unittest.TestCase):
    def test_inconsistent_candidate_identities_fail_closed(self) -> None:
        full = chain()
        rerun_hash = full.rerun_hash or ""
        other_rerun = rerun(study=OTHER_STUDY)
        other_packet = packet("d" * 64)
        cases = {
            "rerun_hash": chain(rerun_hash="0" * 64),
            "rerun_study": chain(upto="rerun", rerun_identity=other_rerun, rerun_hash=identity_hash_v1(other_rerun)),
            "packet_hash": chain(packet_hash="0" * 64),
            "packet_rerun": chain(upto="packet", packet_identity=other_packet,
                                  packet_hash=identity_hash_v1(other_packet)),
            "packet_trial": chain(upto="packet", packet_identity=packet(rerun_hash, candidates=[OTHER_TRIAL])),
            "packet_status": chain(upto="packet", packet_identity=packet(rerun_hash, status="DRAFT")),
            "opening_cycle": chain(upto="opening", opening={"cycle_id": "cycle-2027-01-01",
                                                           "preregistration_hash": full.packet_hash}),
            "validation_hash": chain(validation_hash="0" * 64),
            "fill_trial": chain(fills=[fill(1, trial=OTHER_TRIAL)]),
            "state_study": chain(states=[{**full.states[0], "study_id": OTHER_STUDY}]),
            "state_verdict": chain(states=[{**full.states[0], "state": "HOLDOUT_FAILED_REJECTED"}]),
        }
        for name, case in cases.items():
            with self.subTest(case=name):
                result = assess(case)
                self.assertEqual(STAGE_EVIDENCE_INCONSISTENT, result.stage)
                self.assertEqual(1, len(result.blockers))

    def test_a_validation_of_another_packet_is_inconsistent(self) -> None:
        full = chain()
        foreign = validation_of("e" * 64)
        result = assess(chain(validation_identity=foreign, validation_hash=identity_hash_v1(foreign),
                              states=states_of(identity_hash_v1(foreign))))
        self.assertEqual(("VALIDATION_IS_OF_ANOTHER_PREREGISTRATION",), result.blockers)
        self.assertNotEqual(full.validation_hash, identity_hash_v1(foreign))


class DeterminismTests(unittest.TestCase):
    def test_the_assessment_is_deterministic_and_order_free(self) -> None:
        fills = run(1, [60, 120]) + run(500, [560])
        base = chain(fills=fills)
        shuffled = list(fills)
        random.Random(3).shuffle(shuffled)
        first, again = assess(base), assess(chain(fills=shuffled, states=list(reversed(base.states)),
                                                  rerun_identity=base.rerun_identity, rerun_hash=base.rerun_hash,
                                                  packet_identity=base.packet_identity, packet_hash=base.packet_hash,
                                                  opening=base.opening, validation_identity=base.validation_identity,
                                                  validation_hash=base.validation_hash))
        self.assertEqual(first.payload(), again.payload())
        self.assertEqual(first.assessment_hash, again.assessment_hash)
        self.assertEqual(readiness.PROMOTION_V1, first.payload()["promotion"])

    def test_the_inputs_are_never_mutated(self) -> None:
        base = chain()
        snapshot = repr(base)
        assess(base)
        self.assertEqual(snapshot, repr(base))


if __name__ == "__main__":
    unittest.main()
