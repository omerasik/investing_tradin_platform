"""Paper evidence production: stored records -> unchanged resolvers -> verified sink, or an explicit refusal.

Bounded fixtures only: the 90-bar mean-reversion fixture of the parity tests (five INCUBATING
signals), its live signals and R10 fills turned into the rows the stores would hold, runner
progress written by the real recorder into memory, and a sealed dataset of the same bars with
finalized knowledge times (+2 s). The in-memory sources and sink live here, in tests only; the
production source has no live-bar path.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import shutil
import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

from tests.test_forward_funding_price_bridge_v1 import D0 as FUNDING_D0
from tests.test_forward_funding_price_bridge_v1 import END as FUNDING_END
from tests.test_forward_funding_price_bridge_v1 import (
    INTERVAL_8H,
    RATES,
    _close_bars,
    _dataset,
    _observe,
    _store,
)
from tests.test_forward_funding_price_bridge_v1 import _acceptance as funding_acceptance
from tests.test_live_signals_v1 import DAY0, _live_bars, opened_gate
from tests.test_paper_incubation_v1 import _run
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _walk
from trade_platform import paper_evidence_production_v1 as production
from trade_platform import strategy_lab_validation_v1 as validation
from trade_platform.bybit_forward_settled_funding_v1 import ForwardSettledFundingStoreV1
from trade_platform.first_party_t4_forward_bars_v1 import T4PriceConventionV1
from trade_platform.live_signals_v1 import (
    LiveBarV1,
    LiveHoldoutGateV1,
    LiveStrategyRunnerV1,
    WatchedCandidateV1,
)
from trade_platform.paper_evidence_production_v1 import (
    FundingDeclarationV1,
    FundingPriceEvidenceV1,
    PaperEvidenceProductionError,
    PostgresPaperEvidenceSourcesV1,
    SealedDatasetBarsV1,
    run_paper_evidence_production_v1,
)
from trade_platform.paper_fill_parity_v1 import (
    FillParityReportV1,
    SealedReplayBarV1,
    assess_fill_parity_v1,
)
from trade_platform.paper_funding_evidence_v1 import CandidateFundingEvidenceV1
from trade_platform.paper_incubation_v1 import PaperFillV1, PaperIncubationEngineV1
from trade_platform.runner_liveness_v1 import RunnerProgressRecorderV1, RunnerProgressV1
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1, gross_cost_policy_v2
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

GROSS = gross_cost_policy_v1()
MINUTE = 60_000_000
SEAL_HASH = "5" * 64
SEALED_KNOWLEDGE_SHIFT = 2_000_000
CYCLE = validation.CURRENT_CYCLE_V1.cycle_id
AS_OF = DAY0 + timedelta(days=1)
RECORDED_AT = AS_OF + timedelta(hours=1)
SEALED_AT = DAY0 + timedelta(hours=3)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _at(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def signal_row(signal: Any, *, recorded_at: datetime | None = None) -> dict[str, Any]:
    identity = dict(signal.identity)
    return {"signal_id": signal.signal_id, "content_hash": identity_hash_v1(identity),
            "study_id": UUID(identity["candidate"]["study_id"]), "trial_id": UUID(identity["candidate"]["trial_id"]),
            "symbol": identity["symbol"], "authority": identity["authority"],
            "target_from": identity["target_from"], "target_to": identity["target_to"],
            "bar_open_at": _at(int(identity["evidence"]["bar_open_micros"])), "decided_at": signal.decided_at,
            "identity": identity, "recorded_at": recorded_at or signal.decided_at + timedelta(seconds=1)}


def fill_row(fill: PaperFillV1) -> dict[str, Any]:
    identity = dict(fill.identity)
    return {"fill_id": fill.fill_id, "content_hash": fill.content_hash, "signal_id": UUID(identity["signal_id"]),
            "signal_content_hash": identity["signal_content_hash"], "study_id": UUID(identity["study_id"]),
            "trial_id": UUID(identity["trial_id"]), "symbol": identity["symbol"], "status": identity["status"],
            "target_from": identity["target_from"], "target_to": identity["target_to"],
            "decided_at": _at(int(identity["decided_at_micros"])),
            "fill_bar_open_at": _at(int(identity["fill_bar_open_micros"])),
            "fill_price": None if identity["fill_price"] is None else Decimal(identity["fill_price"]),
            "cost_policy_hash": identity["cost_policy_hash"], "identity": identity,
            "recorded_at": _at(int(identity["fill_bar_open_micros"])) + timedelta(minutes=1, seconds=1)}


class _ProgressRows:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, progress: RunnerProgressV1) -> bool:
        wall = _at(int(progress.identity["host_wall_micros"]))
        self.rows.append({"content_hash": progress.content_hash, "identity": dict(progress.identity),
                          "recorded_at": wall})
        return True


def progress_rows(candidate: WatchedCandidateV1, gate: LiveHoldoutGateV1, bars: Sequence[LiveBarV1],
                  *, session: UUID = UUID(int=7), start: datetime = DAY0) -> list[dict[str, Any]]:
    """The real recorder's rows for one runner session (started at ``start``) that processed ``bars``."""
    store = _ProgressRows()
    clock = {"wall": start, "mono": 0}
    recorder = RunnerProgressRecorderV1(store, runner="paper_incubation", symbol="BTCUSDT",
                                        capture_source_id=UUID(int=99), candidates=[candidate], holdout_gate=gate,
                                        runner_session_id=session, wall_clock=lambda: clock["wall"],
                                        monotonic_clock=lambda: clock["mono"])
    recorder.start()
    for bar in bars:
        clock["wall"] = _at(bar.completed_by_arrival_nanos // 1000 + 500_000)
        clock["mono"] = (bar.completed_by_arrival_nanos // 1000 + 500_000 - int(start.timestamp() * 1e6)) * 1000
        recorder.on_poll([bar])
    return store.rows


def sealed_dataset(bars: Sequence[LiveBarV1], *, number: int = 1, content_hash: str = SEAL_HASH,
                   sealed_at: datetime = SEALED_AT, symbol: str = "BTCUSDT") -> SealedDatasetBarsV1:
    return SealedDatasetBarsV1(UUID(int=number), content_hash, bars[0].session_id, symbol, sealed_at, tuple(
        dataclasses.replace(bar.bar,
                            open_market_knowledge_micros=bar.bar.open_market_knowledge_micros + SEALED_KNOWLEDGE_SHIFT,
                            complete_market_knowledge_micros=(bar.bar.complete_market_knowledge_micros
                                                              + SEALED_KNOWLEDGE_SHIFT))
        for bar in bars))


class FixtureSources:
    """Tests-only in-memory stand-in for the persisted stores (the production source answers no live bars)."""

    def __init__(self, *, signals: list[dict[str, Any]], fills: list[dict[str, Any]],
                 liveness: list[dict[str, Any]], live_bars: list[LiveBarV1] | None,
                 sealed: list[SealedDatasetBarsV1], funding_store: ForwardSettledFundingStoreV1 | None = None,
                 funding_price: FundingPriceEvidenceV1 | None = None,
                 cycle_fills: list[dict[str, Any]] | None = None) -> None:
        self.signals, self.fills, self.liveness = signals, fills, liveness
        self.cycle_fills = cycle_fills or []
        self.bars, self.sealed = live_bars, sealed
        self.funding_store, self.funding_price = funding_store, funding_price
        self.sealed_requests: list[tuple[int, int]] = []

    def signal_rows(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        return list(self.signals)

    def fill_rows(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        return list(self.fills)

    def liveness_rows(self, *, study_id: str, trial_id: str, symbol: str) -> list[dict[str, Any]]:
        return list(self.liveness)

    def live_bars(self, *, study_id: str, trial_id: str, symbol: str) -> list[LiveBarV1] | None:
        return None if self.bars is None else list(self.bars)

    def sealed_datasets(self, *, symbol: str, start_micros: int, end_micros: int,
                        as_of: datetime) -> list[SealedDatasetBarsV1]:
        self.sealed_requests.append((start_micros, end_micros))
        return list(self.sealed)

    def forward_funding_store(self, symbol: str) -> ForwardSettledFundingStoreV1 | None:
        return self.funding_store

    def funding_price_evidence(self, *, symbol: str, declaration: FundingDeclarationV1,
                               as_of: datetime) -> FundingPriceEvidenceV1:
        if self.funding_price is None:
            raise production.EvidenceInputRefusedV1(production.FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE)
        return self.funding_price

    def cycle_incubation_inputs(self, *, cycle_id: str, as_of: datetime) -> tuple[list[Any], Any, None]:
        return list(self.cycle_fills), GROSS, None


class MemorySink:
    """The verified store's idempotency contract in memory: same content returns the first row unchanged."""

    def __init__(self, refuse: str | None = None) -> None:
        self.parity: dict[tuple[str, str], SimpleNamespace] = {}
        self.funding: dict[tuple[str, str], SimpleNamespace] = {}
        self.reports: list[FillParityReportV1] = []
        self.evidence: list[CandidateFundingEvidenceV1] = []
        self.sections: list[dict[str, Any]] = []
        self.refuse = refuse

    def record_fill_parity_v1(self, report: FillParityReportV1, *, cycle_id: str, as_of: datetime,
                              recorded_at: datetime) -> tuple[SimpleNamespace, ...]:
        if self.refuse:
            raise ValueError(self.refuse)
        assert isinstance(report, FillParityReportV1) and identity_hash_v1(dict(report.identity)) == report.content_hash
        self.reports.append(report)
        rows = []
        for item in report.identity["inputs"]["candidates"]:
            key = (report.content_hash, item["trial_id"])
            row = self.parity.setdefault(key, SimpleNamespace(
                assessment_id=uuid5(UUID(int=1), f"{report.content_hash}:{item['trial_id']}"),
                report_content_hash=report.content_hash, cycle_id=cycle_id, status=report.status,
                recorded_at=recorded_at))
            rows.append(row)
        return tuple(rows)

    def record_funding_evidence_v1(self, evidence: CandidateFundingEvidenceV1 | None, *, section: Any,
                                   cycle_id: str, as_of: datetime, recorded_at: datetime) -> SimpleNamespace:
        if self.refuse:
            raise ValueError(self.refuse)
        assert isinstance(evidence, CandidateFundingEvidenceV1)
        self.evidence.append(evidence)
        self.sections.append(dict(section))
        digest = str(section["evidence_hash"])
        return self.funding.setdefault((cycle_id, digest), SimpleNamespace(
            record_id=uuid5(UUID(int=2), digest), evidence_hash=digest, recorded_at=recorded_at))


class ProductionTests(unittest.TestCase):
    temp: Path
    bars: list[LiveBarV1]
    candidate: WatchedCandidateV1
    gate: LiveHoldoutGateV1
    signals: list[Any]
    fills: list[PaperFillV1]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="evidence-production-"))
        study, _ = build_window_and_study(cls.temp, family="mean_reversion_z")
        trial = next(str(t.trial_id) for t in study.trials()
                     if study.parameter_space.typed_point(t.parameters) ==
                     {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"),
                      "direction": "long_short"})
        cls.candidate = WatchedCandidateV1(study, trial, "BTCUSDT", "INCUBATING", "v" * 64)
        cls.gate = opened_gate()
        cls.bars = _live_bars(_walk(90, seed=21))
        runner = LiveStrategyRunnerV1([cls.candidate], holdout_gate=cls.gate, clock=lambda: DAY0)
        cls.signals = [s for bar in cls.bars for s in runner.on_bars([bar])]
        cls.fills = _run(PaperIncubationEngineV1(GROSS), cls.signals, cls.bars)
        assert len(cls.signals) == 5 and len(cls.fills) == 5

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def sources(self, **overrides: Any) -> FixtureSources:
        kwargs: dict[str, Any] = {
            "signals": [signal_row(s) for s in self.signals], "fills": [fill_row(f) for f in self.fills],
            "liveness": progress_rows(self.candidate, self.gate, self.bars), "live_bars": None,
            "sealed": [sealed_dataset(self.bars)]}
        kwargs.update(overrides)
        return FixtureSources(**kwargs)

    def run_production(self, sources: FixtureSources, *, sink: MemorySink | None = None, **overrides: Any) -> Any:
        kwargs: dict[str, Any] = {"cycle_id": CYCLE, "candidates": [self.candidate], "holdout_gate": self.gate,
                                  "cost_policy": GROSS, "as_of": AS_OF, "recorded_at": RECORDED_AT, "funding": None,
                                  "sink": sink}
        kwargs.update(overrides)
        return run_paper_evidence_production_v1(sources, **kwargs)

    @staticmethod
    def outcome(run: Any, kind: str = production.KIND_FILL_PARITY) -> Any:
        return next(item for item in run.outcomes if item.kind == kind)

    # -- today's production reality: refusals with the missing-input report ---------------------

    def test_live_bars_not_persisted_refuses_and_reports_every_missing_input(self) -> None:
        sink = MemorySink()
        run = self.run_production(self.sources(), sink=sink)
        parity = self.outcome(run)
        self.assertEqual(production.STATUS_REFUSED, parity.status)
        self.assertEqual((production.LIVE_BARS_NOT_PERSISTED,), parity.reasons)
        self.assertEqual((production.LIVE_BARS_NOT_PERSISTED,), parity.missing_inputs)
        funding = self.outcome(run, production.KIND_FUNDING_EVIDENCE)
        self.assertEqual(production.STATUS_REFUSED, funding.status)
        self.assertIn(production.FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED, funding.reasons)
        self.assertIn("FORWARD_FUNDING_ACQUISITION:UNRESOLVED_OWNER_AUTHORIZATION", funding.reasons)
        self.assertEqual({production.LIVE_BARS_NOT_PERSISTED, production.FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED},
                         set(run.missing_inputs))
        self.assertEqual([], sink.reports)
        self.assertEqual([], sink.evidence)

    def test_independent_missing_inputs_are_all_named(self) -> None:
        run = self.run_production(self.sources(liveness=[], sealed=[]))
        self.assertEqual((production.LIVE_BARS_NOT_PERSISTED, production.RUNNER_LIVENESS_NOT_RECORDED,
                          production.SEAL_NOT_FOUND), self.outcome(run).reasons)
        self.assertEqual(self.outcome(run).reasons, self.outcome(run).missing_inputs)

    def test_no_stored_paper_signals_in_the_cycle_refuses_both_kinds(self) -> None:
        run = self.run_production(self.sources(signals=[], fills=[]))
        for item in run.outcomes:
            self.assertEqual((production.NO_LIVE_PAPER_SIGNALS_IN_CYCLE,), item.reasons)

    def test_the_production_source_has_no_live_bar_path(self) -> None:
        source = PostgresPaperEvidenceSourcesV1(None, frame_store=None, capture_root=None, funding_root=None)
        self.assertIsNone(source.live_bars(study_id="s", trial_id="t", symbol="BTCUSDT"))
        self.assertIsNone(source.forward_funding_store("BTCUSDT"))
        with self.assertRaises(production.EvidenceInputRefusedV1) as caught:
            source.sealed_datasets(symbol="BTCUSDT", start_micros=0, end_micros=1, as_of=AS_OF)
        self.assertEqual(production.SEAL_NOT_FOUND, caught.exception.code)

    def test_the_module_has_no_network_or_process_imports(self) -> None:
        tree = ast.parse(inspect.getsource(production))
        names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)
                 for alias in node.names} | {node.module.split(".")[0] for node in ast.walk(tree)
                                             if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(names & {"socket", "urllib", "http", "requests", "httpx", "subprocess", "aiohttp"})

    # -- valid evidence: verified persistence, idempotent ------------------------------------------

    def test_valid_evidence_is_rebuilt_with_the_unchanged_resolver_and_persisted_once(self) -> None:
        sink = MemorySink()
        sources = self.sources(live_bars=self.bars)
        run = self.run_production(sources, sink=sink)
        parity = self.outcome(run)
        self.assertEqual((production.STATUS_PERSISTED, "PASS", ()),
                         (parity.status, parity.evidence_status, parity.reasons))
        self.assertEqual(1, len(sink.reports))
        direct = assess_fill_parity_v1(
            candidates=[self.candidate], holdout_gate=self.gate, cost_policy=GROSS,
            replay_bars=[SealedReplayBarV1("BTCUSDT", 0, self.bars[0].session_id, bar, SEAL_HASH)
                         for bar in sealed_dataset(self.bars).bars],
            live_bars=self.bars, live_signals=self.signals, live_fills=self.fills)
        self.assertEqual(direct.content_hash, parity.hashes["report_content_hash"])
        self.assertEqual(direct.content_hash, sink.reports[0].content_hash)
        knowledge = next(o for o in sink.reports[0].observations if o.identity["kind"] == "SIGNAL"
                         ).identity["facts"]["knowledge"]
        self.assertEqual("LIVE_PROVISIONAL_NO_LATER_SAMPLE_YET", knowledge["live_provisional"]["clock_bound"])
        self.assertEqual(SEALED_KNOWLEDGE_SHIFT, knowledge["sealed_finalized"]["decided_at_micros"]
                         - knowledge["live_provisional"]["decided_at_micros"])
        # The sealed probe covered the stored decisions' windows and fills.
        self.assertTrue(sources.sealed_requests)
        again = self.run_production(sources, sink=sink, recorded_at=RECORDED_AT + timedelta(hours=1))
        self.assertEqual(production.STATUS_ALREADY_STORED, self.outcome(again).status)
        self.assertEqual(parity.hashes["report_content_hash"], self.outcome(again).hashes["report_content_hash"])

    def test_a_dry_run_reports_without_writing(self) -> None:
        run = self.run_production(self.sources(live_bars=self.bars), sink=None)
        self.assertTrue(run.dry_run)
        self.assertEqual(production.STATUS_DRY_RUN, self.outcome(run).status)
        self.assertEqual(run.payload()["outcomes"][0]["hashes"]["report_content_hash"],
                         self.outcome(run).hashes["report_content_hash"])

    def test_a_sink_refusal_is_reported_verbatim(self) -> None:
        run = self.run_production(self.sources(live_bars=self.bars),
                                  sink=MemorySink(refuse="CONFLICTING_DUPLICATE_FILL_PARITY"))
        self.assertEqual(("SINK_REFUSED:CONFLICTING_DUPLICATE_FILL_PARITY",), self.outcome(run).reasons)

    def test_a_store_holdout_refusal_names_the_unopened_holdout(self) -> None:
        run = self.run_production(self.sources(live_bars=self.bars),
                                  sink=MemorySink(refuse="EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT"))
        self.assertEqual((production.HOLDOUT_UNOPENED_OR_7, "SINK_REFUSED:EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT"),
                         self.outcome(run).reasons)
        self.assertIn(production.HOLDOUT_UNOPENED_OR_7, run.missing_inputs)

    def test_live_bars_of_two_runner_sessions_are_renumbered_never_one_window(self) -> None:
        first = progress_rows(self.candidate, self.gate, self.bars[:45], session=UUID(int=7))
        second = progress_rows(self.candidate, self.gate, self.bars[45:], session=UUID(int=8))
        processed = production._processed_bars(self.sources(liveness=first + second), self.candidate, AS_OF)
        bound = production._bound_live_bars(self.bars, processed, symbol="BTCUSDT", as_of=AS_OF)
        self.assertEqual({0}, {bar.segment for bar in self.bars})  # both feeds numbered their segment 0
        self.assertEqual([0] * 45 + [1] * 45, [bar.segment for bar in bound])
        # The same bar recorded by both sessions cannot say which feed it belonged to.
        overlap = progress_rows(self.candidate, self.gate, self.bars[40:], session=UUID(int=9))
        processed = production._processed_bars(self.sources(liveness=first + overlap), self.candidate, AS_OF)
        with self.assertRaises(production._Refusal) as caught:
            production._bound_live_bars(self.bars, processed, symbol="BTCUSDT", as_of=AS_OF)
        self.assertEqual((production.AMBIGUOUS_RUNNER_PROGRESS,), caught.exception.reasons)

    # -- forged, mismatched, stale, ambiguous --------------------------------------------------------

    def test_a_forged_signal_row_is_refused_before_the_sink(self) -> None:
        rows = [signal_row(s) for s in self.signals]
        rows[1] = {**rows[1], "identity": {**rows[1]["identity"], "explanation": {"forged": "yes"}}}
        sink = MemorySink()
        run = self.run_production(self.sources(signals=rows, live_bars=self.bars), sink=sink)
        # Its fill then names a signal that is not a verified stored one.
        self.assertEqual((production.MISMATCHED_FILL_SIGNAL, production.STORED_SIGNAL_DOES_NOT_REDERIVE),
                         self.outcome(run).reasons)
        self.assertEqual([], sink.reports)

    def test_a_fill_column_that_disagrees_with_its_identity_is_refused(self) -> None:
        rows = [fill_row(f) for f in self.fills]
        rows[0] = {**rows[0], "fill_price": rows[0]["fill_price"] + Decimal("1")}
        run = self.run_production(self.sources(fills=rows, live_bars=self.bars), sink=MemorySink())
        self.assertEqual((production.STORED_FILL_DOES_NOT_REDERIVE,), self.outcome(run).reasons)

    def test_tampered_runner_progress_is_refused(self) -> None:
        rows = progress_rows(self.candidate, self.gate, self.bars)
        rows[3] = {**rows[3], "identity": {**rows[3]["identity"], "host_wall_micros": 1}}
        run = self.run_production(self.sources(liveness=rows, live_bars=self.bars), sink=MemorySink())
        self.assertIn(production.RUNNER_LIVENESS_DOES_NOT_REDERIVE, self.outcome(run).reasons)

    def test_a_live_bar_the_runner_never_recorded_is_refused(self) -> None:
        rows = progress_rows(self.candidate, self.gate, self.bars[:60])
        run = self.run_production(self.sources(liveness=rows, live_bars=self.bars), sink=MemorySink())
        self.assertEqual((production.LIVE_BAR_NOT_RECORDED_BY_RUNNER,), self.outcome(run).reasons)
        moved = [dataclasses.replace(bar, bar=dataclasses.replace(bar.bar, trade_manifest_hash="other"))
                 if index == 10 else bar for index, bar in enumerate(self.bars)]
        run = self.run_production(self.sources(live_bars=moved), sink=MemorySink())
        self.assertEqual((production.LIVE_BAR_NOT_RECORDED_BY_RUNNER,), self.outcome(run).reasons)

    def test_candidate_evidence_of_another_validation_is_mismatched(self) -> None:
        other = WatchedCandidateV1(self.candidate.study, self.candidate.trial_id, "BTCUSDT", "INCUBATING", "w" * 64)
        run = self.run_production(self.sources(live_bars=self.bars), candidates=[other], sink=MemorySink())
        self.assertEqual((production.MISMATCHED_CANDIDATE_EVIDENCE,), self.outcome(run).reasons)

    def test_signals_under_an_earlier_gate_are_stale(self) -> None:
        unopened = LiveHoldoutGateV1.for_cycle(validation.CURRENT_CYCLE_V1, None)
        run = self.run_production(self.sources(live_bars=self.bars), holdout_gate=unopened, sink=MemorySink())
        self.assertEqual((production.STALE_HOLDOUT_GATE,), self.outcome(run).reasons)

    def test_a_gate_of_another_cycle_and_another_cost_policy_are_refused(self) -> None:
        run = self.run_production(self.sources(live_bars=self.bars), cycle_id="cycle-other", sink=MemorySink())
        self.assertEqual({(production.HOLDOUT_GATE_OF_ANOTHER_CYCLE,)}, {item.reasons for item in run.outcomes})
        run = self.run_production(self.sources(live_bars=self.bars), cost_policy=gross_cost_policy_v2(),
                                  sink=MemorySink())
        self.assertEqual((production.MISMATCHED_COST_POLICY,), self.outcome(run).reasons)

    def test_research_watch_candidates_are_not_paper_evidence(self) -> None:
        watch = WatchedCandidateV1(self.candidate.study, self.candidate.trial_id, "BTCUSDT", "RESEARCH_WATCH",
                                   "v" * 64)
        run = self.run_production(self.sources(live_bars=self.bars), candidates=[watch], sink=MemorySink())
        self.assertEqual({(production.CANDIDATE_NOT_INCUBATING,)}, {item.reasons for item in run.outcomes})

    def test_ambiguous_or_foreign_sealed_evidence_is_refused(self) -> None:
        twice = [sealed_dataset(self.bars), sealed_dataset(self.bars[40:], number=2, content_hash="6" * 64)]
        run = self.run_production(self.sources(live_bars=self.bars, sealed=twice), sink=MemorySink())
        self.assertEqual((production.AMBIGUOUS_SEALED_BARS,), self.outcome(run).reasons)
        foreign = [sealed_dataset(self.bars, symbol="ETHUSDT")]
        run = self.run_production(self.sources(live_bars=self.bars, sealed=foreign), sink=MemorySink())
        self.assertEqual((production.MISMATCHED_SEAL_SYMBOL,), self.outcome(run).reasons)

    def test_point_in_time_reads_ignore_later_records_and_seals(self) -> None:
        early = DAY0 + timedelta(minutes=20)  # before the first signal (minute 34)
        run = self.run_production(self.sources(live_bars=self.bars), as_of=early, recorded_at=early,
                                  sink=MemorySink())
        self.assertEqual((production.NO_LIVE_PAPER_SIGNALS_IN_CYCLE,), self.outcome(run).reasons)
        late_seal = [sealed_dataset(self.bars, sealed_at=AS_OF + timedelta(minutes=1))]
        run = self.run_production(self.sources(live_bars=self.bars, sealed=late_seal), sink=MemorySink())
        self.assertEqual((production.SEAL_NOT_FOUND,), self.outcome(run).reasons)

    def test_caller_errors_raise(self) -> None:
        with self.assertRaises(PaperEvidenceProductionError):
            self.run_production(self.sources(), recorded_at=AS_OF - timedelta(seconds=1))
        with self.assertRaises(PaperEvidenceProductionError):
            self.run_production(self.sources(), candidates=[])
        with self.assertRaises(PaperEvidenceProductionError):
            self.run_production(self.sources(), candidates=[self.candidate, self.candidate])

    # -- funding ----------------------------------------------------------------------------------

    def funding_sources(self, **overrides: Any) -> FixtureSources:
        kwargs: dict[str, Any] = {
            "funding_store": _store([_observe(RATES)]),
            "funding_price": FundingPriceEvidenceV1((_dataset(_close_bars()),), funding_acceptance(), ())}
        kwargs.update(overrides)
        return self.sources(**kwargs)

    @staticmethod
    def declaration(**overrides: Any) -> FundingDeclarationV1:
        kwargs: dict[str, Any] = {
            "convention": T4PriceConventionV1.LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT, "start_ms": FUNDING_D0,
            "end_ms": FUNDING_END, "mark_nearby_tolerance_ms": None, "capture_max_unproven_nanos": 10_000_000_000,
            "published_interval": INTERVAL_8H}
        kwargs.update(overrides)
        return FundingDeclarationV1(**kwargs)

    def test_stored_forward_funding_needs_a_declared_convention(self) -> None:
        run = self.run_production(self.funding_sources(), sink=MemorySink())
        self.assertEqual((production.FUNDING_PRICE_CONVENTION_NOT_DECLARED,),
                         self.outcome(run, production.KIND_FUNDING_EVIDENCE).reasons)
        with self.assertRaises(PaperEvidenceProductionError):
            self.declaration(convention="LAST_TRADE")

    def test_stored_forward_funding_is_bridged_unchanged_and_persisted_once(self) -> None:
        sink = MemorySink()
        sources = self.funding_sources()
        run = self.run_production(sources, sink=sink, funding=self.declaration())
        funding = self.outcome(run, production.KIND_FUNDING_EVIDENCE)
        self.assertEqual(production.STATUS_PERSISTED, funding.status)
        self.assertEqual(1, len(sink.evidence))
        study_id, trial_id = str(self.candidate.study.study_id), self.candidate.trial_id
        self.assertEqual((study_id, trial_id, "BTCUSDT"),
                         (sink.evidence[0].study_id, sink.evidence[0].trial_id, sink.evidence[0].symbol))
        [(bridge_hash, identity)] = sink.evidence[0].bridges
        self.assertEqual([bridge_hash], funding.hashes["bridge_hashes"])
        self.assertEqual(identity_hash_v1(dict(identity)), bridge_hash)
        self.assertEqual("EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED", identity["status"])
        again = self.run_production(sources, sink=sink, funding=self.declaration(),
                                    recorded_at=RECORDED_AT + timedelta(hours=1))
        self.assertEqual(production.STATUS_ALREADY_STORED, self.outcome(again, production.KIND_FUNDING_EVIDENCE).status)

    def test_missing_funding_price_evidence_is_named(self) -> None:
        run = self.run_production(self.funding_sources(funding_price=None), sink=MemorySink(),
                                  funding=self.declaration())
        funding = self.outcome(run, production.KIND_FUNDING_EVIDENCE)
        self.assertEqual((production.FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE,), funding.reasons)
        self.assertEqual((production.FUNDING_PRICE_EVIDENCE_NOT_AVAILABLE,), funding.missing_inputs)


if __name__ == "__main__":
    unittest.main()
