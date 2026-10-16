"""Phase R10 -- OR-7 incubation bindings resolved only from the persisted R6 registry rows."""

from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.paper_incubation_registry_v1 import (
    PREREGISTRATION_SCHEMA_VERSION,
    REGISTRY_SOURCE_V1,
    SELECTION_ESTABLISHED,
    UNRESOLVED_HOLDOUT_NOT_OPENED,
    UNRESOLVED_HOLDOUT_NOT_VALIDATED,
    UNRESOLVED_NO_INCUBATING_CANDIDATE,
    VALIDATION_SCHEMA_VERSION,
    IncubationRegistryError,
    resolve_incubation_bindings_v1,
)
from trade_platform.paper_incubation_report_v1 import (
    FILL_STATUS_FILLED,
    UNRESOLVED_OR7_INCUBATION_DAYS,
    UNRESOLVED_PACKET_AUTHORIZATION,
    WINDOW_INCOMPLETE,
    WINDOW_UNRESOLVED,
    PaperIncubationError,
    incubation_report_v1,
)
from trade_platform.strategy_lab_authority_rerun_v1 import (
    SELECTION_ESTABLISHED as RERUN_ESTABLISHED,
)
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

GROSS = gross_cost_policy_v1()
CYCLE = "cycle-2026-08-20"
START = datetime(2026, 8, 20, tzinfo=UTC)
END = datetime(2026, 9, 24, tzinfo=UTC)
OPENED = datetime(2026, 9, 25, 9, tzinfo=UTC)
VALIDATED = OPENED + timedelta(hours=1)
STATED = VALIDATED + timedelta(seconds=1)
STUDY = "6f1c2a52-0000-4000-8000-000000000001"
TRIAL = "6f1c2a52-0000-4000-8000-0000000000a1"
OTHER = "6f1c2a52-0000-4000-8000-0000000000a2"
DATASET = "d" * 64
MINUTE = 60_000_000


class Registry:
    """The rows migrations 0053/0056/0057 hold for one opened, validated cycle; mutate one to forge it."""

    def __init__(self, *, authorized: bool = True) -> None:
        self.study = {"schema_version": "strategy-lab-study-v1", "label": "s"}
        study_hash = identity_hash_v1(self.study)
        self.rerun = {"study_content_hash": study_hash, "candidate_set_hash": "b" * 64,
                      "authoritative_selection": {"status": "ESTABLISHED",
                                                  "selected": [{"trial_id": TRIAL}, {"trial_id": OTHER}]}}
        unresolved = [] if authorized else [UNRESOLVED_PACKET_AUTHORIZATION]
        self.packet: dict[str, Any] = {
            "schema_version": PREREGISTRATION_SCHEMA_VERSION,
            "cycle": {"cycle_id": CYCLE, "holdout_start": START.isoformat(), "note": "n"},
            "study_id": STUDY, "study_content_hash": study_hash, "candidate_set_hash": "b" * 64,
            "rerun_hash": identity_hash_v1(self.rerun), "rerun_selection_status": "ESTABLISHED",
            "candidates": [TRIAL, OTHER], "symbol": "ETHUSDT",
            "holdout_start": START.isoformat(), "holdout_end_exclusive": END.isoformat(),
            "lags_micros": [2_000_000], "acceptance_criteria": [], "minimum_trades": 50, "cost_policy": None,
            "incubation_days": 28, "lag_sweep_rule": "r", "funding_rule": "f",
            "authorized_by": "owner" if authorized else None, "authorized_on": "2026-09-25" if authorized else None,
            "status": "AUTHORIZED" if authorized else "DRAFT", "unresolved": unresolved,
        }
        self.cycle_rows: list[tuple[Any, ...]] = [(CYCLE, START)]
        self.opened = True
        self.validated = True
        self.opening_overrides: dict[int, Any] = {}
        self.packet_row_overrides: dict[int, Any] = {}
        self.extra_states: list[tuple[Any, ...]] = []
        self.validation: dict[str, Any] = {}
        self.state_rows: list[tuple[Any, ...]] = []
        self.refresh()

    @property
    def packet_hash(self) -> str:
        return identity_hash_v1(self.packet)

    @property
    def validation_hash(self) -> str:
        return identity_hash_v1(self.validation)

    def refresh(self) -> None:
        """Re-derive the validation and its states from the (possibly edited) packet."""
        self.validation = {
            "schema_version": VALIDATION_SCHEMA_VERSION, "preregistration_hash": self.packet_hash,
            "study_id": STUDY, "cycle_id": CYCLE,
            "holdout": {"start": START.isoformat(), "end_exclusive": END.isoformat(), "symbol": "ETHUSDT",
                        "dataset_content_hash": DATASET},
            "candidates": [{"trial_id": TRIAL, "state": "INCUBATING", "reasons": []},
                           {"trial_id": OTHER, "state": "HOLDOUT_FAILED_REJECTED", "reasons": ["x"]}],
            "claim_ceiling": "CONDITIONAL_T2_HOLDOUT_INCUBATION_REQUIRED",
        }
        self.state_rows = [(STUDY, TRIAL, "INCUBATING", self.validation_hash, STATED),
                           (STUDY, OTHER, "HOLDOUT_FAILED_REJECTED", self.validation_hash, STATED)]

    def validation_row(self) -> tuple[Any, ...]:
        return (self.validation_hash, self.packet_hash, DATASET, copy.deepcopy(self.validation), VALIDATED)

    def rows(self, query: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        if "FROM strategy_lab_research_cycles" in query:
            return [row for row in self.cycle_rows if row[0] == params[0]]
        if "FROM strategy_lab_holdout_openings" in query:
            if not self.opened:
                return []
            row = [self.packet_hash, "AUTHORIZED", START, END, OPENED]
            for index, value in self.opening_overrides.items():
                row[index] = value
            return [tuple(row)]
        if "count(*) FROM strategy_lab_holdout_validations" in query:
            return [(1 if self.validated else 0,)]
        if "FROM strategy_lab_holdout_validations" in query:
            return [self.validation_row()] if self.validated else []
        if "FROM strategy_lab_preregistrations" in query:
            row = [self.packet_hash, STUDY, CYCLE, self.packet["status"], copy.deepcopy(self.packet)]
            for index, value in self.packet_row_overrides.items():
                row[index] = value
            return [tuple(row)] if str(row[0]) == params[0] else []
        if "FROM strategy_lab_studies" in query:
            return [(identity_hash_v1(self.study), copy.deepcopy(self.study))]
        if "FROM strategy_lab_authority_reruns" in query:
            rerun_hash = identity_hash_v1(self.rerun)
            status = self.rerun["authoritative_selection"]["status"]
            return [(rerun_hash, STUDY, "b" * 64, status, copy.deepcopy(self.rerun))] if rerun_hash == params[0] else []
        if "FROM strategy_lab_candidate_states" in query:
            return [*self.state_rows, *self.extra_states]
        raise AssertionError(query)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, registry: Registry) -> None:
        self.registry, self.result, self.queries = registry, [], []  # type: list[tuple[Any, ...]]

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.queries.append(query)
        self.result = self.registry.rows(query, params)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result


def resolve(registry: Registry) -> Any:
    return resolve_incubation_bindings_v1(registry.cursor(), cycle_id=CYCLE)


def refused(case: unittest.TestCase, registry: Registry, reason: str) -> None:
    with case.assertRaises(IncubationRegistryError) as caught:
        resolve(registry)
    case.assertIn(reason, str(caught.exception))


def fill(minute: int, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    opened = int(STATED.timestamp()) * 1_000_000 + minute * MINUTE
    return {"signal_id": str(uuid4()), "study_id": STUDY, "trial_id": TRIAL, "symbol": "ETHUSDT",
            "status": FILL_STATUS_FILLED, "target_from": 0 if previous is None else 1,
            "target_to": 1 if previous is None else 0,
            "previous_signal_id": None if previous is None else previous["signal_id"],
            "decided_at_micros": opened - MINUTE // 2, "fill_bar_open_micros": opened, "fill_price": "100",
            "cost_policy_hash": GROSS.policy().content_hash}


class MirrorTests(unittest.TestCase):
    def test_mirrored_names_equal_the_r6_modules(self) -> None:
        self.assertEqual(PREREGISTRATION_SCHEMA_VERSION, validation.PREREGISTRATION_SCHEMA_VERSION_V1)
        self.assertEqual(VALIDATION_SCHEMA_VERSION, validation.VALIDATION_SCHEMA_VERSION_V1)
        self.assertEqual(SELECTION_ESTABLISHED, RERUN_ESTABLISHED)


class ResolutionTests(unittest.TestCase):
    def test_an_authorized_opened_validated_cycle_binds_its_incubating_candidate_only(self) -> None:
        registry = Registry()
        resolution = resolve(registry)
        self.assertEqual((CYCLE, REGISTRY_SOURCE_V1, ()), (resolution.cycle_id, resolution.source,
                                                            resolution.unresolved))
        (binding,) = resolution.bindings  # the rejected candidate is never bound
        self.assertEqual((STUDY, TRIAL, "ETHUSDT", 28, ()), (binding.study_id, binding.trial_id, binding.symbol,
                                                              binding.required_days, binding.unresolved))
        self.assertEqual((registry.packet_hash, registry.validation_hash),
                         (binding.preregistration_hash, binding.validation_hash))
        self.assertEqual(int(STATED.timestamp()) * 1_000_000, binding.incubating_since_micros)
        first = fill(1)
        report = incubation_report_v1([first, fill(60, first)], GROSS, as_of=STATED + timedelta(days=1),
                                      resolution=resolution)
        (candidate,) = report["candidates"]
        self.assertEqual(28, candidate["required_days"])
        self.assertEqual(WINDOW_INCOMPLETE, candidate["incubation"]["window"])
        self.assertEqual(REGISTRY_SOURCE_V1, report["preregistration_registry"]["source"])
        self.assertEqual("INCUBATING", report["state"])

    def test_the_resolution_is_deterministic_and_read_only(self) -> None:
        registry = Registry()
        cursor = registry.cursor()
        first = resolve_incubation_bindings_v1(cursor, cycle_id=CYCLE)
        self.assertEqual(first, resolve(registry))
        self.assertTrue(all(query.lstrip().upper().startswith("SELECT") for query in cursor.queries))

    def test_an_unopened_holdout_is_unresolved_never_a_default(self) -> None:
        registry = Registry()
        registry.opened = registry.validated = False
        resolution = resolve(registry)
        self.assertEqual(((), (UNRESOLVED_HOLDOUT_NOT_OPENED,)), (resolution.bindings, resolution.unresolved))
        (candidate,) = incubation_report_v1([fill(1)], GROSS, as_of=STATED + timedelta(days=1),
                                            resolution=resolution)["candidates"]
        self.assertEqual(UNRESOLVED_OR7_INCUBATION_DAYS, candidate["required_days"])
        self.assertEqual([UNRESOLVED_HOLDOUT_NOT_OPENED], candidate["incubation"]["unresolved"])
        self.assertEqual(WINDOW_UNRESOLVED, candidate["incubation"]["window"])

    def test_an_opened_but_unvalidated_holdout_is_unresolved(self) -> None:
        registry = Registry()
        registry.validated = False
        self.assertEqual((UNRESOLVED_HOLDOUT_NOT_VALIDATED,), resolve(registry).unresolved)

    def test_no_incubating_candidate_is_unresolved(self) -> None:
        registry = Registry()
        registry.validation["candidates"][0]["state"] = "HOLDOUT_FAILED_REJECTED"
        registry.state_rows = [(STUDY, trial, "HOLDOUT_FAILED_REJECTED", registry.validation_hash, STATED)
                               for trial in (TRIAL, OTHER)]
        self.assertEqual((UNRESOLVED_NO_INCUBATING_CANDIDATE,), resolve(registry).unresolved)

    def test_missing_authorization_stays_unresolved_even_if_rows_say_opened(self) -> None:
        # The database refuses this (composite FK on status); a corrupt row is still refused here.
        registry = Registry(authorized=False)
        registry.packet_row_overrides = {3: "AUTHORIZED"}
        refused(self, registry, "preregistration_row_contradicts_its_identity")
        registry.packet_row_overrides = {}
        refused(self, registry, "opened_preregistration_is_not_authorized")

    def test_an_unregistered_cycle_or_blank_id_is_refused(self) -> None:
        registry = Registry()
        with self.assertRaises(IncubationRegistryError):
            resolve_incubation_bindings_v1(registry.cursor(), cycle_id="cycle-2026-12-01")
        with self.assertRaises(IncubationRegistryError):
            resolve_incubation_bindings_v1(registry.cursor(), cycle_id=" ")


class ForgeryTests(unittest.TestCase):
    def test_an_identity_that_does_not_rederive_is_refused(self) -> None:
        cases = [
            ("preregistration_identity_does_not_rederive", lambda r: r.packet_row_overrides.update(
                {4: {**r.packet, "incubation_days": 1}})),
            ("validation_identity_does_not_rederive", lambda r: setattr(
                r, "validation_row", lambda: (r.validation_hash, r.packet_hash, DATASET,
                                              {**r.validation, "claim_ceiling": "VALIDATED"}, VALIDATED))),
            ("study_identity_does_not_rederive", lambda r: setattr(r, "rows", _patched(
                r, "FROM strategy_lab_studies", lambda row: (row[0], {"forged": True})))),
            ("authority_rerun_identity_does_not_rederive", lambda r: setattr(r, "rows", _patched(
                r, "FROM strategy_lab_authority_reruns", lambda row: (*row[:4], {"forged": True})))),
        ]
        for reason, forge in cases:
            registry = Registry()
            forge(registry)
            with self.subTest(reason=reason):
                refused(self, registry, reason)

    def test_mismatched_references_are_refused(self) -> None:
        cases: list[tuple[str, Any]] = [
            ("opening_does_not_name_an_authorized_preregistration", lambda r: r.opening_overrides.update({1: "DRAFT"})),
            ("opening_holdout_start_is_not_the_cycles",
             lambda r: r.opening_overrides.update({2: START + timedelta(days=1)})),
            ("preregistration_holdout_end_is_not_the_opened_end",
             lambda r: r.opening_overrides.update({3: END + timedelta(days=1)})),
            ("opened_preregistration_not_recorded", lambda r: r.opening_overrides.update({0: "e" * 64})),
            ("preregistration_row_contradicts_its_identity",
             lambda r: r.packet_row_overrides.update({1: "6f1c2a52-0000-4000-8000-000000000002"})),
            ("preregistration_row_contradicts_its_identity", lambda r: r.packet_row_overrides.update({2: "cycle-x"})),
            ("validation_is_not_of_the_opened_preregistration", lambda r: setattr(
                r, "validation_row", lambda: (r.validation_hash, "e" * 64, DATASET, r.validation, VALIDATED))),
            ("validation_dataset_hash_does_not_match_its_row", lambda r: setattr(
                r, "validation_row", lambda: (r.validation_hash, r.packet_hash, "f" * 64, r.validation, VALIDATED))),
            ("validation_recorded_before_the_holdout_was_opened", lambda r: setattr(
                r, "validation_row", lambda: (r.validation_hash, r.packet_hash, DATASET, r.validation,
                                              OPENED - timedelta(seconds=1)))),
        ]
        for reason, forge in cases:
            registry = Registry()
            forge(registry)
            with self.subTest(reason=reason):
                refused(self, registry, reason)

    def test_a_validation_that_contradicts_the_packet_is_refused(self) -> None:
        edits: list[tuple[str, Any]] = [
            ("validation_cycle_or_study_is_not_the_packets", {"cycle_id": "cycle-2026-12-01"}),
            ("validation_cycle_or_study_is_not_the_packets", {"study_id": "6f1c2a52-0000-4000-8000-000000000002"}),
            ("validation_holdout_is_not_the_preregistered_span_and_symbol",
             {"holdout": {"start": START.isoformat(), "end_exclusive": END.isoformat(), "symbol": "BTCUSDT",
                          "dataset_content_hash": DATASET}}),
            ("validation_candidates_are_not_the_preregistered_ones",
             {"candidates": [{"trial_id": TRIAL, "state": "INCUBATING", "reasons": []}]}),
            ("validation_schema_version_is_not_current", {"schema_version": "strategy-lab-holdout-validation-v0"}),
        ]
        for reason, edit in edits:
            registry = Registry()
            registry.validation.update(edit)
            registry.state_rows = [(*row[:3], registry.validation_hash, row[4]) for row in registry.state_rows]
            with self.subTest(reason=reason, edit=edit):
                refused(self, registry, reason)

    def test_a_stale_or_unestablished_packet_chain_is_refused(self) -> None:
        cases: list[tuple[str, Any]] = [
            ("preregistration_schema_version_is_not_current", {"schema_version": "strategy-lab-preregistration-v0"}),
            ("preregistered_study_content_hash_is_not_the_recorded_study", {"study_content_hash": "9" * 64}),
            ("preregistered_rerun_not_recorded", {"rerun_hash": "9" * 64}),
            ("rerun_is_not_of_the_preregistered_study_and_candidate_set", {"candidate_set_hash": "9" * 64}),
            ("preregistration_cycle_is_not_the_registered_cycle",
             {"cycle": {"cycle_id": CYCLE, "holdout_start": END.isoformat(), "note": "n"}}),
            ("preregistration_candidates_malformed", {"candidates": [TRIAL, TRIAL]}),
        ]
        for reason, edit in cases:
            registry = Registry()
            registry.packet.update(edit)
            registry.refresh()
            with self.subTest(reason=reason):
                refused(self, registry, reason)
        registry = Registry()
        registry.rerun["authoritative_selection"]["status"] = "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED"
        registry.packet["rerun_hash"] = identity_hash_v1(registry.rerun)
        registry.refresh()
        refused(self, registry, "rerun_selection_is_not_established")
        registry = Registry()
        registry.rerun["authoritative_selection"]["selected"] = [{"trial_id": OTHER}, {"trial_id": TRIAL}]
        registry.packet["rerun_hash"] = identity_hash_v1(registry.rerun)
        registry.refresh()
        refused(self, registry, "preregistered_candidates_are_not_the_rerun_selection")

    def test_states_that_do_not_reproduce_the_validation_are_refused(self) -> None:
        def missing(r: Registry) -> None:
            r.state_rows = r.state_rows[:1]

        def flipped(r: Registry) -> None:
            r.state_rows[1] = (STUDY, OTHER, "INCUBATING", r.validation_hash, STATED)

        def early(r: Registry) -> None:
            r.state_rows[0] = (*r.state_rows[0][:4], VALIDATED - timedelta(seconds=1))

        def foreign(r: Registry) -> None:
            r.extra_states.append(("6f1c2a52-0000-4000-8000-000000000002", TRIAL, "INCUBATING",
                                   r.validation_hash, STATED))

        def naive(r: Registry) -> None:
            r.state_rows[0] = (*r.state_rows[0][:4], STATED.replace(tzinfo=None))

        cases = [("candidate_states_do_not_reproduce_the_validation", missing),
                 ("candidate_states_do_not_reproduce_the_validation", flipped),
                 ("candidate_state_recorded_before_its_validation", early),
                 ("a_state_under_this_validation_is_of_another_study", foreign),
                 ("state_recorded_at_must_be_a_persisted_timezone_aware_instant", naive)]
        for reason, forge in cases:
            registry = Registry()
            forge(registry)
            with self.subTest(reason=reason):
                refused(self, registry, reason)

    def test_a_rejection_under_another_validation_is_terminal(self) -> None:
        registry = Registry()
        registry.extra_states.append((STUDY, TRIAL, "HOLDOUT_FAILED_REJECTED", "9" * 64, STATED))
        with self.assertRaises(PaperIncubationError) as caught:
            resolve(registry)
        self.assertIn("candidate_is_rejected", str(caught.exception))

    def test_ambiguous_rows_are_refused(self) -> None:
        registry = Registry()
        registry.cycle_rows.append(registry.cycle_rows[0])
        refused(self, registry, "research_cycle_is_ambiguous")
        registry = Registry()
        original = registry.rows

        def doubled(query: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
            rows = original(query, params)
            return rows * 2 if "FROM strategy_lab_holdout_validations WHERE" in query and "count" not in query \
                else rows

        registry.rows = doubled  # type: ignore[method-assign]
        refused(self, registry, "holdout_validation_is_ambiguous")

    def test_a_validation_without_an_opening_is_refused(self) -> None:
        registry = Registry()
        registry.opened = False
        refused(self, registry, "validation_without_a_holdout_opening")


class _Rows:
    """A cursor over fixed rows per table (the genuine R6 objects, stored the way the registry stores them)."""

    def __init__(self, tables: dict[str, list[tuple[Any, ...]]]) -> None:
        self.tables, self.result = tables, []  # type: dict[str, list[tuple[Any, ...]]], list[tuple[Any, ...]]

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.result = next(rows for table, rows in self.tables.items() if f"FROM {table}" in query)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result


class GenuineIdentityTests(unittest.TestCase):
    """The resolver accepts exactly what R6 itself produces: a real packet, opening, validation and states."""

    def setUp(self) -> None:
        import shutil
        import tempfile
        from pathlib import Path

        from tests.test_strategy_lab_e2e_fixture import build_window_and_study

        self.temp = Path(tempfile.mkdtemp(prefix="r10-registry-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.study, self.data_root = build_window_and_study(self.temp, family="breakout_channel")

    def test_a_genuine_validated_chain_binds_exactly_its_incubating_candidates(self) -> None:
        from tests.test_bybit_funding_history_v1 import fixture_funding_history
        from tests.test_strategy_lab_e2e_fixture import FIRST_DAY, SyntheticDays
        from tests.test_strategy_lab_validation_v1 import authorized_packet, opening_for, test_cycle
        from trade_platform.public_archive_research_bars_v1 import (
            acquire_and_derive_day_v1,
            build_research_bar_dataset_v1,
        )
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
        from trade_platform.strategy_lab_authority_rerun_v1 import AuthorityRerunV1

        trials = [str(t.trial_id) for t in self.study.trials()][:4]
        rerun_identity = {"study_content_hash": self.study.content_hash, "candidate_set_hash": "c" * 64,
                          "authoritative_selection": {"status": "ESTABLISHED", "selected": [
                              {"rank": i, "trial_id": t} for i, t in enumerate(trials, 1)]}}
        rerun = AuthorityRerunV1(rerun_identity, identity_hash_v1(rerun_identity))
        cycle = test_cycle()
        packet = authorized_packet(self.study, trials, cycle, rerun=rerun)
        opening = opening_for(packet)
        store = ResearchFrameStoreV1(self.data_root)
        archive = self.data_root.parent / "archive"
        for offset in range(2):
            acquire_and_derive_day_v1(archive, "BTCUSDT", FIRST_DAY + timedelta(days=offset), store=store,
                                      evict=True, fetch=SyntheticDays())
        holdout = build_research_bar_dataset_v1(archive, "BTCUSDT", FIRST_DAY, FIRST_DAY + timedelta(days=1),
                                                store=store)
        funding = fixture_funding_history(store.root, "BTCUSDT", FIRST_DAY, 2)
        run = validation.validate_on_holdout_v1(packet, opening, holdout, store=store, funding=funding)
        assert packet.holdout_end_exclusive is not None
        opened_at = packet.holdout_end_exclusive + timedelta(hours=1)
        validated_at, stated_at = opened_at + timedelta(minutes=5), opened_at + timedelta(minutes=6)
        study_id = str(self.study.study_id)
        rows = _Rows({
            "strategy_lab_research_cycles": [(cycle.cycle_id, cycle.holdout_start)],
            "strategy_lab_holdout_openings": [(packet.content_hash + "  ", "AUTHORIZED", cycle.holdout_start,
                                               packet.holdout_end_exclusive, opened_at)],
            "strategy_lab_preregistrations": [(packet.content_hash, self.study.study_id, cycle.cycle_id,
                                               packet.status, json_roundtrip(packet.identity()))],
            "strategy_lab_studies": [(self.study.content_hash, json_roundtrip(self.study.identity()))],
            "strategy_lab_authority_reruns": [(rerun.rerun_hash, self.study.study_id, "c" * 64, "ESTABLISHED",
                                               json_roundtrip(rerun.identity))],
            "strategy_lab_holdout_validations": [(run.content_hash, packet.content_hash,
                                                  run.identity["holdout"]["dataset_content_hash"],
                                                  json_roundtrip(dict(run.identity)), validated_at)],
            "strategy_lab_candidate_states": [(self.study.study_id, event["trial_id"], event["state"],
                                               run.content_hash, stated_at) for event in run.state_events],
        })
        resolution = resolve_incubation_bindings_v1(rows, cycle_id=cycle.cycle_id)
        incubating = [item["trial_id"] for item in run.identity["candidates"] if item["state"] == "INCUBATING"]
        self.assertTrue(incubating)  # deterministic fixture: the bound path is exercised
        self.assertEqual(sorted(incubating), [binding.trial_id for binding in resolution.bindings])
        for binding in resolution.bindings:
            self.assertEqual((study_id, "BTCUSDT", 14, ()), (binding.study_id, binding.symbol,
                                                              binding.required_days, binding.unresolved))
            self.assertEqual((packet.content_hash, run.content_hash),
                             (binding.preregistration_hash, binding.validation_hash))
        self.assertEqual(() if incubating else (UNRESOLVED_NO_INCUBATING_CANDIDATE,), resolution.unresolved)


def json_roundtrip(value: Any) -> Any:
    """What a JSONB column returns."""
    import json

    return json.loads(json.dumps(value, sort_keys=True))


def _patched(registry: Registry, table: str, change: Any) -> Any:
    original = registry.rows

    def rows(query: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        out = original(query, params)
        return [change(row) for row in out] if table in query else out

    return rows


if __name__ == "__main__":
    unittest.main()
