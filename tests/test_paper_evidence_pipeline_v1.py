"""Bounded raw-archive fixtures; no capture, network or production evidence writes."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid5

from tests import test_first_party_t4_v1 as raw
from tests.test_first_party_t4_v1 import SECOND, _TempRoots
from tests.test_paper_evidence_production_v1 import fill_row, signal_row
from tests.test_paper_incubation_v1 import _run
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_sdk_v1 import _walk
from trade_platform.first_party_t4_seal_v1 import (
    discover_t4_segments_v1,
    plan_t4_replay_slice_v1,
    seal_t4_segment_v1,
)
from trade_platform.live_signals_v1 import (
    LiveBarFeedV1,
    LiveHoldoutGateV1,
    LiveStrategyRunnerV1,
    WatchedCandidateV1,
)
from trade_platform.paper_evidence_production_v1 import (
    PaperPriceDeclarationV1,
    PostgresPaperEvidenceSourcesV1,
    StorePaperEvidenceSinkV1,
    produce_fill_parity_v1,
    run_paper_evidence_production_v1,
)
from trade_platform.paper_incubation_v1 import PaperIncubationEngineV1
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
from trade_platform.strategy_lab_validation_v1 import CURRENT_CYCLE_V1

START = datetime(2026, 8, 3, 0, 35, tzinfo=UTC)
BASE = int(START.timestamp()) * SECOND
AS_OF = START + timedelta(hours=3)
NS = UUID("bc3d72df-b71e-45ad-9897-d1d98ebf5232")


class RawPipelineTests(_TempRoots):
    def build_pipeline(self):
        prices = _walk(86, seed=21)
        def change_price(sequence, payload):
            data = json.loads(payload)
            if data["topic"].startswith("publicTrade"):
                minute = min(85, (data["data"][0]["T"] * 1_000_000 - BASE) // (60 * SECOND))
                for trade in data["data"]:
                    trade["p"] = str(prices[minute])
            return json.dumps(data)
        with patch.object(raw, "BASE", BASE):
            self.archive.session(windows=[(BASE, BASE + 86 * 60 * SECOND)],
                samples=[BASE - 5 * SECOND + n * 60 * SECOND for n in range(88)],
                day=START.date(), session_id=uuid5(NS, "capture"), tamper=change_price)
        self.bars = LiveBarFeedV1(self.capture, raw.CONTRACT).poll()[:51]
        plan = discover_t4_segments_v1(self.capture).segments[0]
        parent = seal_t4_segment_v1(plan, store=self.store(), sealed_at=START + timedelta(hours=2))
        left = plan_t4_replay_slice_v1(parent.identity, start_arrival_nanos=plan.start_arrival_nanos,
            end_arrival_nanos_inclusive=BASE + 30 * 60 * SECOND, capture_root=self.capture)
        right = plan_t4_replay_slice_v1(parent.identity, start_arrival_nanos=BASE + 28 * 60 * SECOND,
            end_arrival_nanos_inclusive=plan.end_arrival_nanos, capture_root=self.capture)
        self.seals = [seal_t4_segment_v1(selected, store=self.store(), sealed_at=START + timedelta(hours=2))
                      for selected in (left, right)]
        self.catalogue_seals = self.seals
        self.study, _ = build_window_and_study(self.temp / "study", family="mean_reversion_z")
        self.study = replace(self.study, search=replace(self.study.search, max_trials=self.study.search.max_trials + 17))
        trial = next(str(t.trial_id) for t in self.study.trials()
            if self.study.parameter_space.typed_point(t.parameters) ==
            {"lookback_bars": 30, "entry_z": Decimal("1.5"), "exit_z": Decimal("0.5"), "direction": "long_short"})
        self.candidate = WatchedCandidateV1(self.study, trial, "BTCUSDT", "INCUBATING", "c" * 64)
        self.gate = LiveHoldoutGateV1.for_cycle(CURRENT_CYCLE_V1, None)
        runner = LiveStrategyRunnerV1([self.candidate], holdout_gate=self.gate, clock=lambda: START)
        self.signals = [s for bar in self.bars for s in runner.on_bars([bar])]
        self.fills = _run(PaperIncubationEngineV1(gross_cost_policy_v1()), self.signals, self.bars)

    def test_raw_feed_and_independent_seal_continuity_have_comparable_history(self):
        from trade_platform.paper_fill_parity_v1 import assess_fill_parity_v1
        from trade_platform.sealed_replay_continuity_v1 import sealed_replay_bars_from_seals_v1
        self.build_pipeline()
        replay = tuple(bar for bar in sealed_replay_bars_from_seals_v1(self.seals, store=self.store(), as_of=AS_OF)
                       if self.bars[0].bar.bar_open_micros <= bar.bar.bar_open_micros <= self.bars[-1].bar.bar_open_micros)
        class CatalogCursor:
            def execute(inner, *args):
                pass

            def fetchall(inner):
                return [(seal.dataset_version_id, seal.content_hash, dict(seal.identity),
                         dict(seal.frame_manifests), seal.sealed_at) for seal in self.catalogue_seals]

        source = PostgresPaperEvidenceSourcesV1(CatalogCursor(), frame_store=self.store(),
                                                capture_root=self.capture, funding_root=None)
        actual = source.verified_replay_bars(symbol="BTCUSDT", start_micros=self.bars[0].bar.bar_open_micros,
            end_micros=self.bars[-1].bar.bar_open_micros, as_of=AS_OF, live_bars=self.bars)
        expected = sealed_replay_bars_from_seals_v1(self.catalogue_seals, store=self.store(), as_of=AS_OF)
        self.assertEqual(tuple(bar for bar in expected if self.bars[0].bar.bar_open_micros <=
                               bar.bar.bar_open_micros <= self.bars[-1].bar.bar_open_micros), actual)
        declaration = PaperPriceDeclarationV1(raw.CONTRACT.source_id, self.bars[-1].session_id,
            raw.CONTRACT.instrument_scope, datetime.fromtimestamp(self.bars[-2].bar.bar_open_micros / 1_000_000, tz=UTC),
            3 * 60 * 60 * 1_000_000, 2101 * SECOND)
        price = source.paper_trade_price(symbol="BTCUSDT", declaration=declaration,
            entry_bar_open_micros=self.bars[35].bar.bar_open_micros, as_of=AS_OF)
        self.assertEqual("AVAILABLE", price.status, price.reasons)
        self.assertTrue(self.signals)
        self.assertEqual({0}, {bar.segment for bar in replay})
        report = assess_fill_parity_v1(candidates=[self.candidate], holdout_gate=self.gate,
            cost_policy=gross_cost_policy_v1(), replay_bars=replay, live_bars=self.bars,
            live_signals=self.signals, live_fills=self.fills)
        self.assertEqual("PASS", report.status, report.reasons)

    def test_authoritative_postgres_production_and_verified_retrieval(self):
        if not os.environ.get("POSTGRES_TEST_DSN"):
            self.skipTest("POSTGRES_TEST_DSN not configured")
        from alembic import command
        from alembic.config import Config

        from trade_platform.first_party_t4_dataset_v1 import PostgresFirstPartyT4CatalogV1
        from trade_platform.paper_evidence_store_v1 import read_fill_parity_v1
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.runner_liveness_v1 import (
            PostgresRunnerLivenessStoreV1,
            RunnerProgressRecorderV1,
        )
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")
        self.build_pipeline()
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.addCleanup(database.close)
        PostgresStrategyLabLedgerV1(database).register_study(self.study)
        for seal in self.catalogue_seals:
            PostgresFirstPartyT4CatalogV1(database).register(seal)
        clock = {"wall": START, "mono": 0}
        recorder = RunnerProgressRecorderV1(PostgresRunnerLivenessStoreV1(database), runner="paper_incubation",
            symbol="BTCUSDT", capture_source_id=raw.CONTRACT.source_id, candidates=[self.candidate],
            holdout_gate=self.gate, runner_session_id=uuid5(NS, "runner"),
            wall_clock=lambda: clock["wall"], monotonic_clock=lambda: clock["mono"])
        recorder.start()
        for bar in self.bars:
            clock["wall"] = datetime.fromtimestamp(bar.completed_by_arrival_nanos / SECOND + 1, tz=UTC)
            clock["mono"] = int((clock["wall"] - START).total_seconds()) * SECOND
            recorder.on_poll([bar])
        known = datetime.now(UTC)
        with database.transaction() as connection, connection.cursor() as cursor:
            for row in [signal_row(s) for s in self.signals]:
                names = tuple(row)
                cursor.execute("INSERT INTO live_strategy_signals (" + ",".join(names) + ") VALUES (" +
                    ",".join("%s::jsonb" if n == "identity" else "%s" for n in names) + ") ON CONFLICT DO NOTHING",
                    tuple(json.dumps(row[n]) if n == "identity" else row[n] for n in names))
            for row in [fill_row(f) for f in self.fills]:
                names = tuple(row)
                cursor.execute("INSERT INTO paper_incubation_fills (" + ",".join(names) + ") VALUES (" +
                    ",".join("%s::jsonb" if n == "identity" else "%s" for n in names) + ") ON CONFLICT DO NOTHING",
                    tuple(json.dumps(row[n]) if n == "identity" else row[n] for n in names))
            source = PostgresPaperEvidenceSourcesV1(cursor, frame_store=self.store(), capture_root=self.capture, funding_root=None)
            # Sealing is historical, but this catalogue was registered now.
            # An earlier reading must not discover that later persisted evidence.
            self.assertEqual([], source._seals(symbol="BTCUSDT",
                start_micros=self.bars[0].bar.bar_open_micros, end_micros=self.bars[-1].bar.bar_open_micros, as_of=AS_OF))
            report = produce_fill_parity_v1(source, self.candidate, cycle_id=CURRENT_CYCLE_V1.cycle_id,
                gate=self.gate, cost_policy=gross_cost_policy_v1(), as_of=known)
            self.assertEqual("PASS", report.status, report.reasons)
            declaration = PaperPriceDeclarationV1(raw.CONTRACT.source_id, self.bars[-1].session_id,
                raw.CONTRACT.instrument_scope, datetime.fromtimestamp(self.bars[-2].bar.bar_open_micros / 1_000_000, tz=UTC),
                int((known - START).total_seconds()) * 1_000_000, 2101 * SECOND)
            run = run_paper_evidence_production_v1(source, candidates=[self.candidate],
                cycle_id=CURRENT_CYCLE_V1.cycle_id, holdout_gate=self.gate, cost_policy=gross_cost_policy_v1(),
                as_of=known, recorded_at=known, funding=None, sink=StorePaperEvidenceSinkV1(cursor),
                prices={"BTCUSDT": declaration})
            self.assertEqual("PERSISTED", run.outcomes[0].status, run.outcomes[0].reasons)
            self.assertEqual("AVAILABLE", run.economics[0]["price_resolution"]["status"], run.economics)
            self.assertEqual("AVAILABLE", run.economics[0]["economics"]["candidates"][0]["gross_unrealized"]["status"])
            stored = read_fill_parity_v1(cursor, cycle_id=CURRENT_CYCLE_V1.cycle_id,
                study_id=self.study.study_id, trial_id=UUID(self.candidate.trial_id), as_of=known)
            self.assertIsNotNone(stored)
            self.assertEqual(report.content_hash, stored.report_content_hash)
