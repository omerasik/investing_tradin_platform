"""Phase R10 -- incubation length and elapsed time bound to the frozen OR-7 preregistration packet."""

from __future__ import annotations

import dataclasses
import random
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.paper_incubation_report_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    FILL_STATUS_FILLED,
    STATE_PROFESSIONALLY_VALIDATED_RESERVED,
    UNRESOLVED_NO_BINDING,
    UNRESOLVED_OR7_INCUBATION_DAYS,
    WINDOW_ELAPSED,
    WINDOW_INCOMPLETE,
    WINDOW_UNRESOLVED,
    IncubationBindingV1,
    PaperIncubationError,
    bind_incubation_v1,
    evidenced_segments_v1,
    incubation_report_v1,
)
from trade_platform.paper_incubation_report_v1 import PACKET_STATUS_AUTHORIZED as AUTHORIZED
from trade_platform.paper_incubation_report_v1 import PACKET_STATUS_DRAFT as DRAFT
from trade_platform.paper_incubation_report_v1 import STATE_HOLDOUT_FAILED_REJECTED as REJECTED
from trade_platform.paper_incubation_report_v1 import STATE_INCUBATING as INCUBATING
from trade_platform.paper_incubation_report_v1 import (
    UNRESOLVED_PACKET_AUTHORIZATION as MISSING_AUTHORIZATION,
)
from trade_platform.paper_incubation_report_v1 import (
    UNRESOLVED_PACKET_INCUBATION as MISSING_INCUBATION,
)
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

GROSS = gross_cost_policy_v1()
MINUTE = 60_000_000
DAY = 86_400_000_000
STUDY = "6f1c2a52-0000-4000-8000-000000000001"
TRIAL = "6f1c2a52-0000-4000-8000-0000000000a1"
OTHER_TRIAL = "6f1c2a52-0000-4000-8000-0000000000a2"
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
SINCE_MICROS = int(SINCE.timestamp()) * 1_000_000


def packet(**overrides: Any) -> dict[str, Any]:
    """A stored packet identity (``PreregistrationV1.identity()``), authorized unless overridden."""
    identity: dict[str, Any] = {
        "schema_version": "strategy-lab-preregistration-v1",
        "cycle": {"cycle_id": "cycle-2026-08-20", "holdout_start": "2026-08-20T00:00:00+00:00", "note": "n"},
        "study_id": STUDY, "study_content_hash": "a" * 64, "candidate_set_hash": "b" * 64, "rerun_hash": "c" * 64,
        "rerun_selection_status": "ESTABLISHED", "candidates": [TRIAL, OTHER_TRIAL], "symbol": "BTCUSDT",
        "holdout_start": "2026-08-20T00:00:00+00:00", "holdout_end_exclusive": "2026-08-27T00:00:00+00:00",
        "lags_micros": [0], "acceptance_criteria": [], "minimum_trades": 1, "cost_policy": None,
        "incubation_days": 28, "lag_sweep_rule": "r", "funding_rule": "f",
        "authorized_by": "owner", "authorized_on": "2026-08-27",
        "status": AUTHORIZED, "unresolved": [],
    }
    identity.update(overrides)
    return identity


def unapproved(**overrides: Any) -> dict[str, Any]:
    """The prepared OR-7 packet: 28 days written down, but no authorization."""
    fields: dict[str, Any] = {"authorized_by": None, "authorized_on": None, "status": DRAFT,
                              "unresolved": [MISSING_AUTHORIZATION]}
    fields.update(overrides)
    return packet(**fields)


def validation_of(packet_hash: str, **overrides: Any) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "schema_version": "strategy-lab-holdout-validation-v1", "preregistration_hash": packet_hash,
        "study_id": STUDY, "cycle_id": "cycle-2026-08-20",
        "candidates": [{"trial_id": TRIAL, "state": INCUBATING, "reasons": []},
                       {"trial_id": OTHER_TRIAL, "state": REJECTED, "reasons": ["x"]}],
        "claim_ceiling": "CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED",
    }
    identity.update(overrides)
    return identity


def states_of(validation_hash: str, *, at: datetime = SINCE) -> list[dict[str, Any]]:
    return [{"trial_id": TRIAL, "state": INCUBATING, "evidence_hash": validation_hash, "reasons": [],
             "recorded_at": at},
            {"trial_id": OTHER_TRIAL, "state": REJECTED, "evidence_hash": validation_hash, "reasons": ["x"],
             "recorded_at": at}]


def bind(packet_identity: dict[str, Any] | None = None, *, trial: str = TRIAL,
         validation_overrides: dict[str, Any] | None = None,
         states: list[dict[str, Any]] | None = None) -> IncubationBindingV1:
    packet_identity = packet() if packet_identity is None else packet_identity
    packet_hash = identity_hash_v1(packet_identity)
    validation_identity = validation_of(packet_hash, **(validation_overrides or {}))
    validation_hash = identity_hash_v1(validation_identity)
    return bind_incubation_v1(packet_identity=packet_identity, packet_hash=packet_hash,
                              validation_identity=validation_identity, validation_hash=validation_hash,
                              states=states_of(validation_hash) if states is None else states, trial_id=trial)


def fill(minute: int, *, status: str = FILL_STATUS_FILLED, previous: dict[str, Any] | None = None,
         target_from: int = 0, target_to: int = 1, price: str | None = "100",
         symbol: str = "BTCUSDT") -> dict[str, Any]:
    """A fill decided half a minute before ``minute`` (minutes after the incubating instant)."""
    opened = SINCE_MICROS + minute * MINUTE
    return {"signal_id": str(uuid4()), "study_id": STUDY, "trial_id": TRIAL, "symbol": symbol, "status": status,
            "target_from": target_from, "target_to": target_to,
            "previous_signal_id": None if previous is None else previous["signal_id"],
            "decided_at_micros": opened - MINUTE // 2, "fill_bar_open_micros": opened,
            "fill_price": price if status == FILL_STATUS_FILLED else None,
            "cost_policy_hash": GROSS.policy().content_hash}


def run(start_minute: int, minutes: list[int]) -> list[dict[str, Any]]:
    """One runner's linked fills (no restart in between); the first follows a (re)start."""
    out = [fill(start_minute)]
    for minute in minutes:
        out.append(fill(minute, previous=out[-1], target_from=out[-1]["target_to"],
                        target_to=0 if out[-1]["target_to"] else 1))
    return out


def report(fills: list[dict[str, Any]], *, as_of: datetime | None = None,
           bindings: tuple[IncubationBindingV1, ...] | None = None) -> dict[str, Any]:
    as_of = SINCE + timedelta(days=60) if as_of is None else as_of
    (candidate,) = incubation_report_v1(fills, GROSS, as_of=as_of,
                                        bindings=(bind(),) if bindings is None else bindings)["candidates"]
    return candidate


class MirrorTests(unittest.TestCase):
    def test_mirrored_names_equal_the_validation_module(self) -> None:
        self.assertEqual(INCUBATING, validation.CandidateStateV1.INCUBATING.value)
        self.assertEqual(REJECTED, validation.CandidateStateV1.HOLDOUT_FAILED_REJECTED.value)
        self.assertEqual((AUTHORIZED, DRAFT), (validation.STATUS_AUTHORIZED, validation.STATUS_DRAFT))
        self.assertEqual(MISSING_INCUBATION, validation.UNRESOLVED_INCUBATION)
        self.assertEqual(MISSING_AUTHORIZATION, validation.UNRESOLVED_AUTHORIZATION)

    def test_professionally_validated_is_reserved_not_a_validation_state(self) -> None:
        self.assertNotIn(STATE_PROFESSIONALLY_VALIDATED_RESERVED,
                         {state.value for state in validation.CandidateStateV1})


class RequirementTests(unittest.TestCase):
    def test_an_authorized_packet_binds_its_incubation_length_and_hash(self) -> None:
        binding = bind()
        self.assertEqual((28, ()), (binding.required_days, binding.unresolved))
        candidate = report(run(1, [60]))
        incubation = candidate["incubation"]
        self.assertEqual(28, candidate["required_days"])
        self.assertEqual(identity_hash_v1(packet()), incubation["binding"]["preregistration_hash"])
        self.assertEqual(SINCE_MICROS, incubation["binding"]["incubating_since_micros"])
        self.assertEqual(WINDOW_INCOMPLETE, incubation["window"])

    def test_missing_authorization_is_unresolved_never_the_prepared_28_days(self) -> None:
        binding = bind(unapproved())
        self.assertEqual((None, (MISSING_AUTHORIZATION,)), (binding.required_days, binding.unresolved))
        # Even a window far longer than 28 days of evidence stays unresolved.
        candidate = report(run(1, [40 * 24 * 60]), bindings=(binding,))
        self.assertEqual(UNRESOLVED_OR7_INCUBATION_DAYS, candidate["required_days"])
        self.assertEqual([MISSING_AUTHORIZATION], candidate["incubation"]["unresolved"])
        self.assertEqual(WINDOW_UNRESOLVED, candidate["incubation"]["window"])
        # Half an authorization is no authorization.
        half = bind(packet(authorized_on=None, status=DRAFT, unresolved=[MISSING_AUTHORIZATION]))
        self.assertIsNone(half.required_days)

    def test_missing_incubation_length_is_unresolved_with_no_default(self) -> None:
        binding = bind(packet(incubation_days=None, status=DRAFT, unresolved=[MISSING_INCUBATION]))
        self.assertEqual((None, (MISSING_INCUBATION,)), (binding.required_days, binding.unresolved))
        both = bind(unapproved(incubation_days=None, unresolved=[MISSING_INCUBATION, MISSING_AUTHORIZATION]))
        self.assertEqual((MISSING_AUTHORIZATION, MISSING_INCUBATION), both.unresolved)

    def test_a_packet_whose_status_contradicts_its_fields_is_refused(self) -> None:
        for forged in (packet(authorized_by=None),                         # AUTHORIZED, unauthorized
                       packet(authorized_by="  "),                         # blank actor
                       packet(incubation_days=None),                       # AUTHORIZED, no length
                       unapproved(unresolved=[]),                          # DRAFT, no reason
                       unapproved(unresolved=["MISSING_OWNER_ACCEPTANCE_CRITERIA_OR_7"]),  # gap omitted
                       packet(status="APPROVED"),
                       packet(incubation_days=0), packet(incubation_days=True), packet(incubation_days="28"),
                       packet(authorized_on="yesterday")):
            with self.subTest(forged=forged), self.assertRaises(PaperIncubationError):
                bind(forged)

    def test_without_a_binding_the_requirement_is_unresolved(self) -> None:
        candidate = report(run(1, [60]), bindings=())
        self.assertEqual(UNRESOLVED_OR7_INCUBATION_DAYS, candidate["required_days"])
        self.assertEqual([UNRESOLVED_NO_BINDING], candidate["incubation"]["unresolved"])
        self.assertIsNone(candidate["incubation"]["binding"])
        with self.assertRaises(TypeError):
            incubation_report_v1(run(1, [60]), GROSS, as_of=SINCE + timedelta(days=1),
                                 incubation_days=28)  # type: ignore[call-arg]


class IdentityTests(unittest.TestCase):
    def test_a_packet_hash_that_does_not_rederive_is_refused(self) -> None:
        identity = packet()
        validation_identity = validation_of("d" * 64)
        with self.assertRaises(PaperIncubationError):
            bind_incubation_v1(packet_identity=identity, packet_hash="d" * 64,
                               validation_identity=validation_identity,
                               validation_hash=identity_hash_v1(validation_identity),
                               states=states_of(identity_hash_v1(validation_identity)), trial_id=TRIAL)

    def test_mismatched_packet_cycle_study_or_candidate_is_refused(self) -> None:
        cases: list[dict[str, Any]] = [
            {"validation_overrides": {"preregistration_hash": "e" * 64}},
            {"validation_overrides": {"cycle_id": "cycle-2026-12-01"}},
            {"validation_overrides": {"study_id": "6f1c2a52-0000-4000-8000-000000000002"}},
            {"trial": "6f1c2a52-0000-4000-8000-0000000000ff"},   # not preregistered
            {"trial": OTHER_TRIAL},                               # judged rejected
            {"validation_overrides": {"candidates": [{"trial_id": TRIAL, "state": REJECTED, "reasons": []}]}},
            {"states": []},
            {"states": [{"trial_id": TRIAL, "state": INCUBATING, "evidence_hash": "f" * 64, "recorded_at": SINCE}]},
            {"states": [{"trial_id": TRIAL, "state": INCUBATING, "evidence_hash": "f" * 64, "recorded_at": SINCE,
                         "study_id": "6f1c2a52-0000-4000-8000-000000000002"}]},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(PaperIncubationError):
                bind(**case)

    def test_state_events_must_be_this_validations_single_unrejected_incubation(self) -> None:
        identity = packet()
        packet_hash = identity_hash_v1(identity)
        validation_identity = validation_of(packet_hash)
        validation_hash = identity_hash_v1(validation_identity)
        good = states_of(validation_hash)
        variants = [
            [*good, {**good[0], "recorded_at": SINCE + timedelta(days=1)}],          # two starts
            [*good, {**good[0], "state": REJECTED}],                                 # later rejected
            [{**good[0], "study_id": "6f1c2a52-0000-4000-8000-000000000002"}],       # another study
            [{**good[0], "recorded_at": "2026-09-01T00:00:00+00:00"}],               # not persisted instant
            [{**good[0], "recorded_at": SINCE.replace(tzinfo=None)}],                # naive
        ]
        for states in variants:
            with self.subTest(states=states), self.assertRaises(PaperIncubationError):
                bind_incubation_v1(packet_identity=identity, packet_hash=packet_hash,
                                   validation_identity=validation_identity, validation_hash=validation_hash,
                                   states=states, trial_id=TRIAL)

    def test_fills_of_another_symbol_are_refused_for_a_bound_candidate(self) -> None:
        with self.assertRaises(PaperIncubationError):
            report([fill(1, symbol="ETHUSDT")])

    def test_bindings_are_issued_once_per_candidate_and_never_forged(self) -> None:
        binding = bind()
        with self.assertRaises(PaperIncubationError):
            IncubationBindingV1(STUDY, TRIAL, "BTCUSDT", "c", "p", "v", 0, 1, ())
        with self.assertRaises(PaperIncubationError):
            report(run(1, [60]), bindings=(dataclasses.replace(binding, required_days=1),))
        with self.assertRaises(PaperIncubationError):
            report(run(1, [60]), bindings=(binding, bind()))


class ElapsedTests(unittest.TestCase):
    def test_the_caller_cannot_inflate_elapsed_time(self) -> None:
        fills = run(1, [60, 120])
        evidenced = fills[-1]["fill_bar_open_micros"] - fills[0]["decided_at_micros"]
        just_after = SINCE + timedelta(minutes=121)
        far_future = SINCE + timedelta(days=3650)
        near, far = report(fills, as_of=just_after), report(fills, as_of=far_future)
        self.assertEqual(evidenced, near["incubation"]["evidenced_elapsed_micros"])
        self.assertEqual(near["incubation"], far["incubation"])
        self.assertEqual(near["elapsed_days"], far["elapsed_days"])
        self.assertEqual(WINDOW_INCOMPLETE, far["incubation"]["window"])

    def test_evidence_after_the_reading_instant_is_look_ahead(self) -> None:
        fills = run(1, [60])
        with self.assertRaises(PaperIncubationError):
            report(fills, as_of=SINCE + timedelta(minutes=30))
        with self.assertRaises(PaperIncubationError):
            report(fills, as_of=(SINCE + timedelta(days=1)).replace(tzinfo=None))

    def test_a_fill_decided_before_the_persisted_incubating_instant_is_refused(self) -> None:
        with self.assertRaises(PaperIncubationError):
            report([fill(0)])  # decided half a minute before the INCUBATING state was recorded

    def test_an_incomplete_window_then_an_elapsed_one_stays_incubating(self) -> None:
        short = report(run(1, [27 * 24 * 60]))
        self.assertEqual(WINDOW_INCOMPLETE, short["incubation"]["window"])
        self.assertEqual("26.999", short["elapsed_days"])  # rounded down, never up to the requirement
        whole = report(run(1, [28 * 24 * 60 + 1]))
        self.assertEqual(WINDOW_ELAPSED, whole["incubation"]["window"])
        self.assertEqual(INCUBATING, whole["state"])
        full = incubation_report_v1(run(1, [28 * 24 * 60 + 1]), GROSS, as_of=SINCE + timedelta(days=60),
                                    bindings=(bind(),))
        self.assertEqual(INCUBATING, full["state"])
        self.assertNotIn(STATE_PROFESSIONALLY_VALIDATED_RESERVED, repr(full))

    def test_an_unobserved_fill_bar_extends_the_segment_only_to_its_decision(self) -> None:
        first = fill(1)
        missed = fill(60, status=FILL_STATUS_BAR_NOT_OBSERVED, previous=first, target_from=1, target_to=0)
        (segment,) = evidenced_segments_v1([first, missed])
        self.assertEqual((first["decided_at_micros"], missed["decided_at_micros"]), segment)


class RestartTests(unittest.TestCase):
    def test_a_restart_gap_is_never_bridged(self) -> None:
        before = run(1, [60])                    # runner A: minute 0:30 .. 60
        after = run(10 * 24 * 60, [10 * 24 * 60 + 90])  # runner B after a 10-day outage
        candidate = report(before + after)
        a = before[-1]["fill_bar_open_micros"] - before[0]["decided_at_micros"]
        b = after[-1]["fill_bar_open_micros"] - after[0]["decided_at_micros"]
        self.assertEqual(a + b, candidate["incubation"]["evidenced_elapsed_micros"])
        self.assertEqual(2, len(candidate["incubation"]["evidenced_segments"]))
        self.assertLess(candidate["incubation"]["evidenced_elapsed_micros"], DAY)

    def test_elapsed_time_survives_a_restart_without_double_counting(self) -> None:
        before = run(1, [60, 120])
        prior = report(before, as_of=SINCE + timedelta(hours=3))["incubation"]["evidenced_elapsed_micros"]
        after = run(200, [260])
        resumed = report(before + after, as_of=SINCE + timedelta(hours=5))["incubation"]
        self.assertEqual(prior + after[-1]["fill_bar_open_micros"] - after[0]["decided_at_micros"],
                         resumed["evidenced_elapsed_micros"])
        # Re-reading the same stored fills after the restart reproduces the same clock.
        again = report(before + after, as_of=SINCE + timedelta(hours=5))["incubation"]
        self.assertEqual(resumed, again)
        # A fill delivered twice (replayed after the restart) is refused, never counted twice.
        with self.assertRaises(PaperIncubationError):
            report(before + after + [dict(after[-1])])

    def test_overlapping_runs_are_counted_once(self) -> None:
        one = run(1, [120])
        other = run(30, [90])  # a second, unlinked run inside the first one's span
        (segment,) = evidenced_segments_v1(sorted(one + other, key=lambda item: item["decided_at_micros"]))
        self.assertEqual((one[0]["decided_at_micros"], one[-1]["fill_bar_open_micros"]), segment)


class DeterminismTests(unittest.TestCase):
    def test_the_report_does_not_depend_on_input_order(self) -> None:
        fills = run(1, [60, 120]) + run(500, [560])
        binding = bind()
        as_of = SINCE + timedelta(days=2)
        expected = incubation_report_v1(fills, GROSS, as_of=as_of, bindings=(binding,))
        shuffled = list(fills)
        random.Random(7).shuffle(shuffled)
        self.assertEqual(expected, incubation_report_v1(shuffled, GROSS, as_of=as_of, bindings=(binding,)))
        self.assertEqual(expected, incubation_report_v1(fills, GROSS, as_of=as_of, bindings=(bind(),)))


class BindingSourceTests(unittest.TestCase):
    """bind_incubation_v1 stays for compatibility, labelled; the registry resolution is the authoritative path."""

    def test_a_caller_supplied_binding_is_labelled_as_such(self) -> None:
        from trade_platform.paper_incubation_report_v1 import BINDING_SOURCE_CALLER_SUPPLIED_V1

        binding = bind()
        self.assertEqual(BINDING_SOURCE_CALLER_SUPPLIED_V1, binding.source)
        self.assertEqual(BINDING_SOURCE_CALLER_SUPPLIED_V1, report(run(1, [60]))["incubation"]["binding"]["source"])
        full = incubation_report_v1(run(1, [60]), GROSS, as_of=SINCE + timedelta(days=1), bindings=(binding,))
        self.assertIsNone(full["preregistration_registry"])

    def test_a_resolution_admits_only_registry_sourced_bindings(self) -> None:
        from trade_platform.paper_incubation_report_v1 import (
            BINDING_SOURCE_REGISTRY_V1,
            IncubationResolutionV1,
            _bind_stored_chain_v1,
            _issue_resolution_v1,
        )

        identity = packet()
        packet_hash = identity_hash_v1(identity)
        validation_identity = validation_of(packet_hash)
        validation_hash = identity_hash_v1(validation_identity)
        registry_bound = _bind_stored_chain_v1(
            packet_identity=identity, packet_hash=packet_hash, validation_identity=validation_identity,
            validation_hash=validation_hash, states=states_of(validation_hash), trial_id=TRIAL,
            source=BINDING_SOURCE_REGISTRY_V1)
        resolution = _issue_resolution_v1("cycle-2026-08-20", BINDING_SOURCE_REGISTRY_V1, (registry_bound,), ())
        as_of = SINCE + timedelta(days=1)
        (candidate,) = incubation_report_v1(run(1, [60]), GROSS, as_of=as_of, resolution=resolution)["candidates"]
        self.assertEqual(BINDING_SOURCE_REGISTRY_V1, candidate["incubation"]["binding"]["source"])
        self.assertEqual(28, candidate["required_days"])
        with self.assertRaises(PaperIncubationError):  # a caller-supplied binding never enters a resolution
            _issue_resolution_v1("cycle-2026-08-20", BINDING_SOURCE_REGISTRY_V1, (bind(),), ())
        with self.assertRaises(PaperIncubationError):
            _issue_resolution_v1("cycle-2026-08-20", "SELF_DECLARED", (registry_bound,), ())
        with self.assertRaises(PaperIncubationError):  # a binding of another cycle
            _issue_resolution_v1("cycle-2026-12-01", BINDING_SOURCE_REGISTRY_V1, (registry_bound,), ())
        with self.assertRaises(PaperIncubationError):
            incubation_report_v1(run(1, [60]), GROSS, as_of=as_of, resolution=resolution, bindings=(bind(),))
        for forged in (dataclasses.replace(resolution, unresolved=("X",)),
                       dataclasses.replace(resolution, bindings=(dataclasses.replace(registry_bound,
                                                                                     required_days=1),))):
            with self.subTest(forged=forged), self.assertRaises(PaperIncubationError):
                incubation_report_v1(run(1, [60]), GROSS, as_of=as_of, resolution=forged)
        with self.assertRaises(PaperIncubationError):
            IncubationResolutionV1("cycle-2026-08-20", BINDING_SOURCE_REGISTRY_V1, (registry_bound,), ())
        with self.assertRaises(PaperIncubationError):
            dataclasses.replace(registry_bound, source="SELF_DECLARED")
        empty = _issue_resolution_v1("cycle-2026-08-20", BINDING_SOURCE_REGISTRY_V1, (), ("HOLDOUT_NOT_OPENED",))
        (unbound,) = incubation_report_v1(run(1, [60]), GROSS, as_of=as_of, resolution=empty)["candidates"]
        self.assertEqual((UNRESOLVED_OR7_INCUBATION_DAYS, ["HOLDOUT_NOT_OPENED"]),
                         (unbound["required_days"], unbound["incubation"]["unresolved"]))


if __name__ == "__main__":
    unittest.main()
