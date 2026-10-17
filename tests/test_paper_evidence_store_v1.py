"""Paper evidence store -- verification before any write (offline; the PostgreSQL round trip is separate).

Real evidence only: parity reports come from :func:`assess_fill_parity_v1` over the parity
tests' bar fixture moved before the current holdout (2026-08-01, so the unopened gate of
cycle-2026-08-20 admits it); funding sections come from :func:`incubation_report_v1` with
bridges from the bridge tests' fixture (2026-06-01). Hashes are hand-forged only in tests
proving that forgery is refused.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess  # nosec B404 - runs this interpreter on a fixed snippet
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid5

from trade_platform import paper_evidence_store_v1 as store
from trade_platform.paper_evidence_store_v1 import PaperEvidenceStoreError

MINUTE = 60_000_000
DAY_SHIFT = timedelta(days=61)  # 2026-10-01 -> 2026-08-01, before the current holdout
PARITY_DAY0 = datetime(2026, 8, 1, tzinfo=UTC)
PARITY_AS_OF = PARITY_DAY0 + timedelta(days=1)
PARITY_RECORDED = PARITY_AS_OF + timedelta(hours=1)


class Fixtures:
    """Built once per process: the study, its incubating candidate, and real evidence."""

    _built: Fixtures | None = None

    def __init__(self) -> None:
        from tests.test_live_signals_v1 import _live_bars
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform.live_signals_v1 import WatchedCandidateV1

        self.temp = Path(tempfile.mkdtemp(prefix="evidence-store-"))
        study, _ = build_window_and_study(self.temp, family="mean_reversion_z")
        self.study = study
        self.trial = next(str(t.trial_id) for t in study.trials()
                          if study.parameter_space.typed_point(t.parameters) ==
                          {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"),
                           "direction": "long_short"})
        self.candidate = WatchedCandidateV1(study, self.trial, "BTCUSDT", "INCUBATING", "v" * 64)
        self.bars = [self._shift(bar) for bar in _live_bars(_walk(90, seed=21))]

    @classmethod
    def get(cls) -> Fixtures:
        if cls._built is None:
            cls._built = Fixtures()
        return cls._built

    @staticmethod
    def _shift(bar: Any) -> Any:
        delta = DAY_SHIFT // timedelta(microseconds=1)
        moved = dataclasses.replace(bar.bar, bar_open_micros=bar.bar.bar_open_micros - delta,
                                    open_market_knowledge_micros=bar.bar.open_market_knowledge_micros - delta,
                                    complete_market_knowledge_micros=bar.bar.complete_market_knowledge_micros - delta)
        return dataclasses.replace(bar, bar=moved, completed_by_arrival_nanos=bar.completed_by_arrival_nanos
                                   - delta * 1000)

    # -- parity ---------------------------------------------------------------------------

    @staticmethod
    def gate(opened_end: datetime | None = None) -> Any:
        from tests.test_live_signals_v1 import opened_gate
        from trade_platform.live_signals_v1 import LiveHoldoutGateV1
        from trade_platform.strategy_lab_validation_v1 import CURRENT_CYCLE_V1

        if opened_end is not None:
            return opened_gate(opened_end)
        return LiveHoldoutGateV1.for_cycle(CURRENT_CYCLE_V1, None)

    def live(self, bars: list[Any], gate: Any = None) -> tuple[list[Any], list[Any]]:
        from tests.test_paper_incubation_v1 import _run
        from trade_platform.live_signals_v1 import LiveStrategyRunnerV1
        from trade_platform.paper_incubation_v1 import PaperIncubationEngineV1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=bars[0].bar.bar_open_micros)
        runner = LiveStrategyRunnerV1([self.candidate], holdout_gate=gate or self.gate(), clock=lambda: start)
        signals = [signal for bar in bars for signal in runner.on_bars([bar])]
        return signals, _run(PaperIncubationEngineV1(gross_cost_policy_v1()), signals, bars)

    def report(self, *, replay: list[Any] | None = None, live_bars: list[Any] | None = None,
               gate: Any = None, live_gate: Any = None) -> Any:
        from tests.test_paper_fill_parity_v1 import _sealed
        from trade_platform.paper_fill_parity_v1 import assess_fill_parity_v1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        bars = self.bars if live_bars is None else live_bars
        signals, fills = self.live(bars, live_gate or gate)
        return assess_fill_parity_v1(candidates=[self.candidate], holdout_gate=gate or self.gate(),
                                     cost_policy=gross_cost_policy_v1(),
                                     replay_bars=_sealed(self.bars if replay is None else replay), live_bars=bars,
                                     live_signals=signals, live_fills=fills)

    def failing_replay(self) -> list[Any]:
        """The sealed side disagrees on one bar's close: the assessment FAILs."""
        target = self.bars[40].bar.bar_open_micros
        return [dataclasses.replace(bar, bar=dataclasses.replace(bar.bar, close_price=bar.bar.close_price + 1))
                if bar.bar.bar_open_micros == target else bar for bar in self.bars]

    # -- funding --------------------------------------------------------------------------

    def fills(self, *entries_exits: tuple[int, int | None]) -> list[dict[str, Any]]:
        from tests.test_paper_funding_evidence_v1 import fill_at

        out: list[dict[str, Any]] = []
        for entry, exit_ in entries_exits:
            first = fill_at(entry)
            items = [first] if exit_ is None else [first, fill_at(exit_, previous=first, target_from=1, target_to=0,
                                                                  price="101")]
            for item in items:
                item.update(study_id=str(self.study.study_id), trial_id=self.trial)
            out += items
        return out

    def evidence(self, *bridges: Any, trial: str | None = None) -> Any:
        from trade_platform.paper_funding_evidence_v1 import candidate_funding_evidence_v1

        return candidate_funding_evidence_v1(study_id=str(self.study.study_id), trial_id=trial or self.trial,
                                             symbol="BTCUSDT", bridges=list(bridges))

    def section(self, fills: list[dict[str, Any]], *evidence: Any, as_of: datetime | None = None) -> dict[str, Any]:
        from tests.test_forward_funding_price_bridge_v1 import AS_OF
        from trade_platform.paper_incubation_report_v1 import incubation_report_v1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        report = incubation_report_v1(fills, gross_cost_policy_v1(), as_of=as_of or AS_OF, funding=list(evidence))
        (candidate,) = report["candidates"]
        return dict(candidate["funding_evidence"])


def bridge() -> Any:
    from tests.test_forward_funding_price_bridge_v1 import _run

    return _run()


def funding_hours(hours: int) -> int:
    from tests.test_forward_funding_price_bridge_v1 import _H, D0

    return (D0 + hours * _H) * 1000


def funding_as_of() -> datetime:
    from tests.test_forward_funding_price_bridge_v1 import AS_OF

    return AS_OF


def rehashed_section(section: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A tampered section whose evidence hash is recomputed (only re-derivation can catch it)."""
    from trade_platform.strategy_lab_study_v1 import identity_hash_v1

    body = {key: value for key, value in section.items() if key != "evidence_hash"} | changes
    return {**body, "evidence_hash": identity_hash_v1(body)}


def reissued(report: Any, mutate: Any) -> Any:
    """A tampered report whose content hash and id are recomputed (only re-derivation can catch it)."""
    import copy

    from trade_platform.paper_fill_parity_v1 import FillParityReportV1
    from trade_platform.strategy_lab_study_v1 import identity_hash_v1

    identity = copy.deepcopy(dict(report.identity))
    mutate(identity)
    content_hash = identity_hash_v1(identity)
    return FillParityReportV1(identity, uuid5(store._PARITY_NAMESPACE, f"report:{content_hash}"), content_hash,
                              report.observations)


class NoDatabase:
    """A cursor that fails the test if any query reaches it: verification precedes the database."""

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        raise AssertionError(f"database reached before the evidence was verified: {query}")

    def fetchone(self) -> Any:  # pragma: no cover
        raise AssertionError("database reached")

    def fetchall(self) -> Any:  # pragma: no cover
        raise AssertionError("database reached")


class Scripted:
    """A cursor answering each query in order from a script (for registry-binding rules)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.current: Any = None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.current = self.answers.pop(0)

    def fetchone(self) -> Any:
        return self.current

    def fetchall(self) -> Any:
        return self.current


class MirrorTests(unittest.TestCase):
    def test_the_parity_mirrors_are_the_source_constants(self) -> None:
        from trade_platform import live_signals_v1, paper_fill_parity_v1, paper_incubation_report_v1

        self.assertEqual(store.FILL_PARITY_SCHEMA_VERSION_V1, paper_fill_parity_v1.FILL_PARITY_SCHEMA_VERSION_V1)
        self.assertEqual(store.FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1,
                         paper_fill_parity_v1.FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1)
        self.assertEqual(store._PARITY_NAMESPACE, paper_fill_parity_v1._NAMESPACE)
        self.assertEqual(store.PARITY_STATUSES_V1, (paper_fill_parity_v1.ASSESSMENT_PASS,
                                                    paper_fill_parity_v1.ASSESSMENT_FAIL,
                                                    paper_fill_parity_v1.ASSESSMENT_INCOMPLETE))
        self.assertEqual((store._MATCH, store._DIVERGED, store._UNAVAILABLE),
                         (paper_fill_parity_v1.OUTCOME_MATCH, paper_fill_parity_v1.OUTCOME_DIVERGED,
                          paper_fill_parity_v1.OUTCOME_UNAVAILABLE))
        self.assertEqual(store._KIND_SIGNAL, paper_fill_parity_v1.KIND_SIGNAL)
        self.assertEqual(store._NO_MATCHED_FILLED, paper_fill_parity_v1.NO_MATCHED_FILLED_OBSERVATION)
        self.assertEqual(store._FILLED, paper_incubation_report_v1.FILL_STATUS_FILLED)
        self.assertEqual(store._AUTHORITY_INCUBATING, live_signals_v1.AUTHORITY_INCUBATING)
        report = Fixtures.get().report()
        self.assertEqual(store.FILL_PARITY_CLAIM_V1, report.identity["claim"])

    def test_the_study_id_is_derived_as_the_study_spec_derives_it(self) -> None:
        study = Fixtures.get().study
        self.assertEqual(study.study_id, store._study_id(study.content_hash))

    def test_the_migration_pins_the_vocabulary(self) -> None:
        from trade_platform.paper_funding_evidence_v1 import FUNDING_EVIDENCE_STATES_V1

        text = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
                "20261017_0066_paper_evidence_store.py").read_text(encoding="utf-8")
        for constant in ('revision = "20261017_0066"', 'down_revision = "20261017_0065"',
                         store.FILL_PARITY_SCHEMA_VERSION_V1, "paper-funding-evidence-v1", *FUNDING_EVIDENCE_STATES_V1,
                         *store.PARITY_STATUSES_V1, "CHECK(state <> 'ECONOMICALLY_COSTABLE')"):
            self.assertIn(constant, text)

    def test_the_module_is_import_light(self) -> None:
        # The module and the fill loaders it imports when it re-derives holdings.
        code = ("import sys, trade_platform.paper_evidence_store_v1, trade_platform.paper_account_v1, "
                "trade_platform.paper_incubation_registry_v1, trade_platform.paper_incubation_report_v1; "
                "print(sorted(m for m in ('numpy', 'polars', 'duckdb', 'pyarrow', 'pandas') if m in sys.modules))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,  # nosec
                                timeout=120)
        self.assertEqual("[]", result.stdout.strip())


class FillParityVerificationTests(unittest.TestCase):
    fx: Fixtures

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = Fixtures.get()

    def _refused(self, code: str, report: Any, *, as_of: Any = PARITY_AS_OF, recorded_at: Any = PARITY_RECORDED,
                 ) -> None:
        with self.assertRaises(PaperEvidenceStoreError) as refused:
            store.record_fill_parity_v1(NoDatabase(), report, cycle_id="cycle-2026-08-20", as_of=as_of,
                                        recorded_at=recorded_at)
        self.assertEqual(code, str(refused.exception))

    def test_the_fixture_reports_are_real_pass_fail_and_incomplete(self) -> None:
        self.assertEqual("PASS", self.fx.report().status)
        self.assertEqual("FAIL", self.fx.report(replay=self.fx.failing_replay()).status)
        self.assertEqual("INCOMPLETE", self.fx.report(replay=self.fx.bars[:60]).status)

    def test_a_forged_hash_is_refused(self) -> None:
        report = self.fx.report()
        forged = dataclasses.replace(report, identity={**report.identity,
                                                       "assessment": {"status": "FAIL", "reasons": ["X"]}})
        self._refused(store.FILL_PARITY_REPORT_DOES_NOT_REDERIVE, forged)
        self._refused(store.FILL_PARITY_REPORT_DOES_NOT_REDERIVE,
                      dataclasses.replace(report, report_id=uuid5(store._NAMESPACE, "other")))

    def test_a_rehashed_assessment_that_its_observations_contradict_is_refused(self) -> None:
        report = self.fx.report(replay=self.fx.failing_replay())

        def to_pass(identity: dict[str, Any]) -> None:
            identity["assessment"] = {"status": "PASS", "reasons": []}

        self._refused(store.FILL_PARITY_ASSESSMENT_DOES_NOT_REDERIVE, reissued(report, to_pass))

    def test_observations_that_do_not_rederive_are_refused(self) -> None:
        report = self.fx.report()
        self._refused(store.FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE,
                      dataclasses.replace(report, observations=report.observations[1:]))
        first = report.observations[0]
        tampered = dataclasses.replace(first, identity={**first.identity, "outcome": "DIVERGED"})
        self._refused(store.FILL_PARITY_OBSERVATIONS_DO_NOT_REDERIVE,
                      dataclasses.replace(report, observations=(tampered, *report.observations[1:])))

    def test_unsupported_schema_and_contract_are_refused(self) -> None:
        report = self.fx.report()
        self._refused(store.UNSUPPORTED_FILL_PARITY_SCHEMA_VERSION,
                      reissued(report, lambda identity: identity.update(schema_version="paper-fill-parity-v2")))
        self._refused(store.UNSUPPORTED_FILL_PARITY_CONTRACT,
                      reissued(report, lambda identity: identity.update(tolerance="1bp")))
        self._refused(store.FILL_PARITY_REPORT_MALFORMED, {"identity": {}, "content_hash": "0" * 64})

    def test_a_candidate_not_incubating_is_refused(self) -> None:
        def research_watch(identity: dict[str, Any]) -> None:
            identity["inputs"]["candidates"][0]["authority"] = "RESEARCH_WATCH"

        self._refused(store.FILL_PARITY_CANDIDATE_NOT_INCUBATING, reissued(self.fx.report(), research_watch))

    def test_look_ahead_is_refused(self) -> None:
        report = self.fx.report()
        self._refused(store.AS_OF_AFTER_RECORDED_AT, report, as_of=PARITY_RECORDED + timedelta(seconds=1))
        last = max(bar.bar.bar_open_micros for bar in self.fx.bars)
        before_last = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=last)
        self._refused(store.EVIDENCE_FACTS_AFTER_AS_OF, report, as_of=before_last)
        self._refused(store.TIMESTAMP_NOT_TIMEZONE_AWARE, report, as_of=PARITY_AS_OF.replace(tzinfo=None))

    def test_rederive_assessment_mirrors_the_module_on_every_status(self) -> None:
        for report in (self.fx.report(), self.fx.report(replay=self.fx.failing_replay()),
                       self.fx.report(replay=self.fx.bars[:60])):
            summary, assessment = store._rederived_assessment([dict(o.identity) for o in report.observations])
            self.assertEqual((report.identity["summary"], report.identity["assessment"]), (summary, assessment))


class FundingVerificationTests(unittest.TestCase):
    fx: Fixtures

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = Fixtures.get()

    def _refused(self, code: str, evidence: Any, section: Any, *, as_of: datetime | None = None) -> None:
        with self.assertRaises(PaperEvidenceStoreError) as refused:
            store.record_funding_evidence_v1(NoDatabase(), evidence, section=section, cycle_id="cycle-2026-08-20",
                                             as_of=as_of or funding_as_of(),
                                             recorded_at=funding_as_of() + timedelta(hours=1))
        self.assertEqual(code, str(refused.exception))

    def _complete(self) -> tuple[Any, dict[str, Any]]:
        evidence = self.fx.evidence(bridge())
        return evidence, self.fx.section(self.fx.fills((funding_hours(1), funding_hours(10))), evidence)

    def test_the_fixture_sections_are_real_states(self) -> None:
        evidence, section = self._complete()
        self.assertEqual("EVIDENCE_COMPLETE", section["state"])
        linked = self.fx.section(self.fx.fills((funding_hours(1), None)), evidence)
        self.assertEqual("EVIDENCE_LINKED", linked["state"])
        self.assertEqual("NO_FUNDING_EVIDENCE_SUPPLIED",
                         self.fx.section(self.fx.fills((funding_hours(1), funding_hours(10))))["state"])

    def test_a_forged_section_is_refused(self) -> None:
        evidence, section = self._complete()
        self._refused(store.FUNDING_SECTION_DOES_NOT_REDERIVE, evidence, {**section, "settlements_outside_holdings": 0})
        # Re-hashed, but the module would not produce it from these bridges and holdings.
        self._refused(store.FUNDING_SECTION_DOES_NOT_REDERIVE, evidence,
                      rehashed_section(section, state="EVIDENCE_LINKED", completeness_blockers=["X"]))
        self._refused(store.FUNDING_SECTION_DOES_NOT_REDERIVE, evidence, rehashed_section(
            section, holding_intervals=[{**section["holding_intervals"][0], "coverage": "NOT_COVERED"}]))

    def test_economically_costable_is_refused(self) -> None:
        evidence, section = self._complete()
        self._refused(store.FUNDING_STATE_ECONOMICALLY_COSTABLE_REFUSED, evidence,
                      rehashed_section(section, state="ECONOMICALLY_COSTABLE", costable=True))
        self._refused(store.FUNDING_SECTION_CLAIMS_COSTABLE, evidence, rehashed_section(section, costable=True))
        self._refused(store.FUNDING_ECONOMIC_BLOCKERS_MISSING, evidence, rehashed_section(section,
                                                                                          economic_blockers=[]))
        self._refused(store.FUNDING_STATE_UNKNOWN, evidence, rehashed_section(section, state="COSTED"))

    def test_unsupported_schema_is_refused(self) -> None:
        evidence, section = self._complete()
        self._refused(store.UNSUPPORTED_FUNDING_EVIDENCE_SCHEMA_VERSION, evidence,
                      rehashed_section(section, schema_version="paper-funding-evidence-v2"))

    def test_evidence_of_another_candidate_is_refused(self) -> None:
        _, section = self._complete()
        other = next(str(t.trial_id) for t in self.fx.study.trials() if str(t.trial_id) != self.fx.trial)
        self._refused(store.FUNDING_EVIDENCE_NOT_OF_THIS_CANDIDATE, self.fx.evidence(bridge(), trial=other), section)
        self._refused(store.FUNDING_EVIDENCE_NOT_ISSUED, {"bridges": []}, section)
        self._refused(store.FUNDING_SECTION_WITHOUT_ITS_EVIDENCE, None, section)
        # A copy of issued evidence carrying the issuer token but changed is re-proven, never trusted.
        evidence = self.fx.evidence(bridge())
        self._refused(store.FUNDING_SECTION_DOES_NOT_REDERIVE, dataclasses.replace(evidence, validation_hash="a" * 64),
                      section)

    def test_look_ahead_is_refused(self) -> None:
        evidence, section = self._complete()
        bridge_as_of = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            microseconds=int(dict(evidence.bridges[0][1])["as_of_micros"]))
        self._refused(store.EVIDENCE_FACTS_AFTER_AS_OF, evidence, section, as_of=bridge_as_of - timedelta(seconds=1))
        open_section = self.fx.section(self.fx.fills((funding_hours(1), None)), evidence)
        self._refused(store.FUNDING_AS_OF_IS_NOT_THE_REPORT_AS_OF, evidence, open_section,
                      as_of=funding_as_of() + timedelta(minutes=1))
        with self.assertRaises(PaperEvidenceStoreError) as refused:
            store.record_funding_evidence_v1(NoDatabase(), evidence, section=section, cycle_id="cycle-2026-08-20",
                                             as_of=funding_as_of(), recorded_at=funding_as_of() - timedelta(seconds=1))
        self.assertEqual(store.AS_OF_AFTER_RECORDED_AT, str(refused.exception))


class StoredFillsHoldingsTests(unittest.TestCase):
    """Holdings re-derived from the cycle's stored fills, the loaders patched to serve real fill identities."""

    fx: Fixtures

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = Fixtures.get()

    def _check(self, section: dict[str, Any], evidence: Any, stored: list[dict[str, Any]], *,
               recorded: datetime | None = None, tamper: bool = False) -> None:
        from unittest import mock

        from trade_platform import paper_account_v1, paper_incubation_registry_v1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
        from trade_platform.strategy_lab_study_v1 import identity_hash_v1

        rows = [(("0" * 64) if tamper else identity_hash_v1(fill), fill) for fill in stored]
        at = recorded or funding_as_of() - timedelta(minutes=1)
        cursor = Scripted([[(content_hash, at) for content_hash, _ in rows]])
        unresolved = paper_incubation_registry_v1.IncubationRegistryError("unresolved")
        with mock.patch.object(paper_account_v1, "cycle_cost_policy_v1", return_value=gross_cost_policy_v1()), \
                mock.patch.object(paper_account_v1, "cycle_fill_rows_v1", return_value=(rows, [])), \
                mock.patch.object(paper_incubation_registry_v1, "resolve_incubation_bindings_v1",
                                  side_effect=unresolved):
            store._check_holdings_against_stored_fills(
                cursor, cycle_id="cycle-2026-08-20", as_of=funding_as_of(), section=section,
                bridges=[{"content_hash": h, "identity": dict(i)} for h, i in evidence.bridges])

    def _refused(self, code: str, *args: Any, **kwargs: Any) -> None:
        with self.assertRaises(PaperEvidenceStoreError) as refused:
            self._check(*args, **kwargs)
        self.assertEqual(code, str(refused.exception))

    def test_holdings_must_be_the_stored_fills_own(self) -> None:
        evidence = self.fx.evidence(bridge())
        round_trip = self.fx.fills((funding_hours(1), funding_hours(10)))
        section = self.fx.section(round_trip, evidence)
        self._check(section, evidence, round_trip)  # built from exactly the stored fills
        extra = self.fx.fills((funding_hours(1), funding_hours(3)), (funding_hours(5), funding_hours(10)))
        self._refused(store.FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS, self.fx.section(extra, evidence), evidence,
                      round_trip)
        self._refused(store.FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS, section, evidence, round_trip[:1])
        self._refused(store.FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS, section, evidence, [])
        # A fill recorded after the evidence's as_of was not known then.
        self._refused(store.FUNDING_HOLDINGS_DO_NOT_MATCH_STORED_FILLS, section, evidence, round_trip,
                      recorded=funding_as_of() + timedelta(seconds=1))

    def test_stored_fills_that_do_not_rederive_are_refused(self) -> None:
        evidence = self.fx.evidence(bridge())
        round_trip = self.fx.fills((funding_hours(1), funding_hours(10)))
        self._refused(store.STORED_FILLS_DO_NOT_REDERIVE, self.fx.section(round_trip, evidence), evidence,
                      round_trip, tamper=True)


class RegistryBindingTests(unittest.TestCase):
    STUDY = uuid5(store._NAMESPACE, "study")
    TRIAL = uuid5(store._NAMESPACE, "trial")

    def _check(self, answers: list[Any], claimed: list[str]) -> None:
        store._check_registry_binding(Scripted(answers), cycle_id="cycle-2026-08-20", study_id=self.STUDY,
                                      trial_id=self.TRIAL, claimed=claimed)

    def test_the_registry_binding_rules(self) -> None:
        ours, theirs = "a" * 64, "b" * 64
        self._check([[]], [])  # unbound and nothing claimed
        self._check([[], None], [ours])  # the claimed hash names no validation
        self._check([[("INCUBATING", ours, "cycle-2026-08-20")]], [ours])
        for answers, claimed, code in (
            ([[("HOLDOUT_FAILED_REJECTED", ours, "cycle-2026-08-20")]], [ours], store.CANDIDATE_REJECTED_IN_REGISTRY),
            ([[("INCUBATING", theirs, "cycle-2027-01-01")]], [theirs], store.CANDIDATE_BOUND_TO_ANOTHER_CYCLE),
            ([[("INCUBATING", ours, "cycle-2026-08-20")]], [theirs],
             store.CANDIDATE_AUTHORITY_EVIDENCE_DISAGREES_WITH_REGISTRY),
            ([[], ("cycle-2027-01-01",)], [theirs], store.CANDIDATE_BOUND_TO_ANOTHER_CYCLE),
        ):
            with self.assertRaises(PaperEvidenceStoreError) as refused:
                self._check(answers, claimed)
            self.assertEqual(code, str(refused.exception))


def tearDownModule() -> None:
    if Fixtures._built is not None:
        shutil.rmtree(Fixtures._built.temp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
