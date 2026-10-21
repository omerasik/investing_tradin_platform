"""Paper evidence production end to end on PostgreSQL: stored records -> assessment -> verified store -> read back.

Fixtures stay here. The parity fixture is the evidence store tests' one, moved to
2026-08-01 (before the current holdout); it is stored under another trial of the same
study than the store tests use, so neither module's point-in-time reads see the
other's rows. The live bars and sealed datasets the production source cannot supply
are injected by a tests-only source subclass; the production source itself is also
run against the same stored rows and must refuse.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from tests.test_paper_evidence_store_v1 import PARITY_AS_OF, Fixtures

CYCLE = "cycle-2026-08-20"
AS_OF = PARITY_AS_OF + timedelta(hours=3)
RECORDED_AT = AS_OF + timedelta(minutes=10)
_NS = UUID("0b6f0c5e-3c11-4a7e-9a55-6e0d2c4f7a01")


class _Progress:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def record(self, progress: Any) -> bool:
        self.items.append(progress)
        return True


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PaperEvidenceProductionPostgresTests(unittest.TestCase):
    fx: Fixtures
    bars: list[Any]
    database: Any
    candidate: Any
    signals: list[Any]
    fills: list[Any]
    funding_root: Path

    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        from tests.test_paper_incubation_v1 import _run
        from trade_platform.live_signals_v1 import LiveStrategyRunnerV1, WatchedCandidateV1
        from trade_platform.paper_incubation_v1 import PaperIncubationEngineV1
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        cls.fx = Fixtures.get()
        # One fixed capture session: the stored rows re-derive identically in every process.
        cls.bars = [dataclasses.replace(bar, session_id=uuid5(_NS, "capture-session")) for bar in cls.fx.bars]
        cls.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        PostgresStrategyLabLedgerV1(cls.database).register_study(cls.fx.study)
        cls.funding_root = Path(tempfile.mkdtemp(prefix="evidence-production-funding-"))

        # Another trial than the store tests' one that fills on the fixture bars (deterministic pick).
        start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=cls.bars[0].bar.bar_open_micros)
        gate = Fixtures.gate()
        for trial in sorted(str(t.trial_id) for t in cls.fx.study.trials()):
            if trial == cls.fx.trial:
                continue
            candidate = WatchedCandidateV1(cls.fx.study, trial, "BTCUSDT", "INCUBATING", "v" * 64)
            runner = LiveStrategyRunnerV1([candidate], holdout_gate=gate, clock=lambda: start)
            signals = [signal for bar in cls.bars for signal in runner.on_bars([bar])]
            fills = _run(PaperIncubationEngineV1(gross_cost_policy_v1()), signals, cls.bars)
            if any(fill.status == "FILLED" for fill in fills):
                cls.candidate, cls.signals, cls.fills = candidate, signals, fills
                break
        else:  # pragma: no cover - the fixture study always has one
            raise AssertionError("no filling trial in the fixture study")
        cls._store_forward_records(start)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        shutil.rmtree(cls.funding_root, ignore_errors=True)
        if Fixtures._built is not None:
            shutil.rmtree(Fixtures._built.temp, ignore_errors=True)

    @classmethod
    def _store_forward_records(cls, start: datetime) -> None:
        """The rows the R8/R10 runner would have written, with past audit instants (explicit columns)."""
        from tests.test_paper_evidence_production_v1 import fill_row, signal_row
        from trade_platform.runner_liveness_v1 import RunnerProgressRecorderV1

        progress = _Progress()
        clock = {"wall": start, "mono": 0}
        recorder = RunnerProgressRecorderV1(progress, runner="paper_incubation", symbol="BTCUSDT",
                                            capture_source_id=UUID(int=99), candidates=[cls.candidate],
                                            holdout_gate=Fixtures.gate(),
                                            runner_session_id=uuid5(_NS, cls.candidate.trial_id),
                                            wall_clock=lambda: clock["wall"], monotonic_clock=lambda: clock["mono"])
        recorder.start()
        for bar in cls.bars:
            micros = bar.completed_by_arrival_nanos // 1000 + 500_000
            clock["wall"] = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=micros)
            clock["mono"] = (micros - int(start.timestamp() * 1_000_000)) * 1000
            recorder.on_poll([bar])
        with cls.database.transaction() as connection, connection.cursor() as cursor:
            for signal in cls.signals:
                row = signal_row(signal)
                cursor.execute(
                    "INSERT INTO live_strategy_signals (signal_id, content_hash, study_id, trial_id, symbol, "
                    "authority, target_from, target_to, bar_open_at, decided_at, identity, recorded_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                    (row["signal_id"], row["content_hash"], row["study_id"], row["trial_id"], row["symbol"],
                     row["authority"], row["target_from"], row["target_to"], row["bar_open_at"], row["decided_at"],
                     json.dumps(row["identity"], sort_keys=True), row["recorded_at"]))
            for fill in cls.fills:
                row = fill_row(fill)
                cursor.execute(
                    "INSERT INTO paper_incubation_fills (fill_id, content_hash, signal_id, signal_content_hash, "
                    "study_id, trial_id, symbol, status, target_from, target_to, decided_at, fill_bar_open_at, "
                    "fill_price, cost_policy_hash, identity, recorded_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                    (row["fill_id"], row["content_hash"], row["signal_id"], row["signal_content_hash"],
                     row["study_id"], row["trial_id"], row["symbol"], row["status"], row["target_from"],
                     row["target_to"], row["decided_at"], row["fill_bar_open_at"], row["fill_price"],
                     row["cost_policy_hash"], json.dumps(row["identity"], sort_keys=True), row["recorded_at"]))
            for item in progress.items:
                identity = item.identity
                binding = identity["binding"]
                wall = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=int(identity["host_wall_micros"]))
                cursor.execute(
                    "INSERT INTO runner_liveness_progress (progress_id, idempotency_key, content_hash, "
                    "runner_session_id, sequence, kind, symbol, capture_source_id, binding_hash, "
                    "previous_progress_hash, host_wall_at, bar_count, identity, recorded_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                    (item.progress_id, item.idempotency_key, item.content_hash, UUID(identity["runner_session_id"]),
                     identity["sequence"], identity["kind"], None if binding is None else binding["symbol"],
                     None if binding is None else UUID(binding["capture_source_id"]), identity["binding_hash"],
                     identity["previous_progress_hash"], wall, len(identity["bars"]),
                     json.dumps(dict(identity), sort_keys=True), wall))

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            yield cursor

    def _run(self, sources_for: Any, *, recorded_at: datetime = RECORDED_AT, funding: Any = None,
             persist: bool = True, candidate: Any = None, gate: Any = None, as_of: datetime = AS_OF) -> Any:
        from trade_platform.paper_evidence_production_v1 import (
            StorePaperEvidenceSinkV1,
            run_paper_evidence_production_v1,
        )
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        with self.cursor() as cursor:
            return run_paper_evidence_production_v1(
                sources_for(cursor), cycle_id=CYCLE, candidates=[candidate or self.candidate],
                holdout_gate=gate or Fixtures.gate(), cost_policy=gross_cost_policy_v1(), as_of=as_of,
                recorded_at=recorded_at, funding=funding, sink=StorePaperEvidenceSinkV1(cursor) if persist else None)

    def _injected(self, cursor: Any) -> Any:
        """The production source over the stored rows, with the inputs it cannot supply injected (tests only)."""
        from tests.test_forward_funding_price_bridge_v1 import (
            RATES,
            _close_bars,
            _dataset,
            _observe,
            _store,
        )
        from tests.test_forward_funding_price_bridge_v1 import _acceptance as funding_acceptance
        from tests.test_paper_evidence_production_v1 import sealed_dataset
        from trade_platform.paper_evidence_production_v1 import (
            FundingPriceEvidenceV1,
            PostgresPaperEvidenceSourcesV1,
        )

        bars = self.bars

        class Injected(PostgresPaperEvidenceSourcesV1):
            # Legacy injected contract regression only. The independent raw-pipeline
            # tests exercise the unmodified authoritative production hooks.
            def verified_live_bars(self, **_: Any) -> Any:
                return list(bars)

            def verified_replay_bars(self, **_: Any) -> Any:
                from trade_platform.paper_fill_parity_v1 import SealedReplayBarV1

                dataset = sealed_dataset(bars, sealed_at=PARITY_AS_OF)
                return [SealedReplayBarV1(dataset.exchange_symbol, 0, dataset.session_id, bar, dataset.content_hash)
                        for bar in dataset.bars]

            def live_bars(self, **_: Any) -> Any:  # type: ignore[override]
                return list(bars)

            def sealed_datasets(self, **_: Any) -> Any:  # type: ignore[override]
                return [sealed_dataset(bars, sealed_at=PARITY_AS_OF)]

            def forward_funding_store(self, symbol: str) -> Any:
                return _store([_observe(RATES)])

            def funding_price_evidence(self, **_: Any) -> Any:
                return FundingPriceEvidenceV1((_dataset(_close_bars()),), funding_acceptance(), ())

        return Injected(cursor, frame_store=None, capture_root=None, funding_root=None)

    def _production(self, cursor: Any) -> Any:
        from trade_platform.paper_evidence_production_v1 import PostgresPaperEvidenceSourcesV1

        return PostgresPaperEvidenceSourcesV1(cursor, frame_store=None, capture_root=None,
                                              funding_root=self.funding_root)

    def _declaration(self) -> Any:
        from tests.test_forward_funding_price_bridge_v1 import D0, END, INTERVAL_8H
        from trade_platform.first_party_t4_forward_bars_v1 import T4PriceConventionV1
        from trade_platform.paper_evidence_production_v1 import FundingDeclarationV1

        return FundingDeclarationV1(T4PriceConventionV1.LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT, D0, END, None,
                                    10_000_000_000, INTERVAL_8H)

    def _stored_counts(self) -> tuple[int, int]:
        with self.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM paper_fill_parity_assessments WHERE trial_id=%s",
                           (UUID(self.candidate.trial_id),))
            parity = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM paper_funding_evidence_records WHERE trial_id=%s",
                           (UUID(self.candidate.trial_id),))
            return parity, cursor.fetchone()[0]

    @staticmethod
    def _pick(run: Any, kind: str) -> Any:
        return next(item for item in run.outcomes if item.kind == kind)

    # -- (b) the production source today: refusals, nothing persisted ---------------------------

    def test_a_the_production_source_refuses_and_persists_nothing(self) -> None:
        from trade_platform import paper_evidence_production_v1 as production

        before = self._stored_counts()
        run = self._run(self._production, funding=self._declaration())
        parity = self._pick(run, production.KIND_FILL_PARITY)
        funding = self._pick(run, production.KIND_FUNDING_EVIDENCE)
        self.assertEqual((production.STATUS_REFUSED, (production.RUNNER_LIVENESS_DOES_NOT_REDERIVE, production.SEAL_NOT_FOUND)),
                         (parity.status, parity.reasons))
        self.assertEqual(production.STATUS_REFUSED, funding.status)
        self.assertIn(production.FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED, funding.reasons)
        self.assertIn(production.RUNNER_LIVENESS_DOES_NOT_REDERIVE, parity.reasons)
        self.assertIn(production.FORWARD_FUNDING_ACQUISITION_NOT_AUTHORIZED, run.missing_inputs)
        self.assertEqual(before, self._stored_counts())

    # -- (a) + (c) the deterministic fixture path, then an idempotent rerun ----------------------

    def test_b_stored_records_reach_the_verified_store_and_read_back(self) -> None:
        from trade_platform import paper_evidence_production_v1 as production
        from trade_platform.paper_evidence_store_v1 import (
            read_fill_parity_v1,
            read_funding_evidence_v1,
        )

        dry = self._run(self._injected, funding=self._declaration(), persist=False)
        self.assertEqual({production.STATUS_DRY_RUN}, {item.status for item in dry.outcomes}, dry.payload())
        run = self._run(self._injected, funding=self._declaration())
        parity = self._pick(run, production.KIND_FILL_PARITY)
        funding = self._pick(run, production.KIND_FUNDING_EVIDENCE)
        self.assertEqual((production.STATUS_PERSISTED, production.STATUS_PERSISTED), (parity.status, funding.status))
        self.assertEqual(dry.outcomes[0].hashes["report_content_hash"], parity.hashes["report_content_hash"])
        study_id, trial_id = self.fx.study.study_id, UUID(self.candidate.trial_id)
        with self.cursor() as cursor:
            self.assertIsNone(read_fill_parity_v1(cursor, cycle_id=CYCLE, study_id=study_id, trial_id=trial_id,
                                                  as_of=RECORDED_AT - timedelta(seconds=1)))
            stored = read_fill_parity_v1(cursor, cycle_id=CYCLE, study_id=study_id, trial_id=trial_id,
                                         as_of=RECORDED_AT)
            funded = read_funding_evidence_v1(cursor, cycle_id=CYCLE, study_id=study_id, trial_id=trial_id,
                                              as_of=RECORDED_AT)
        assert stored is not None and funded is not None
        self.assertEqual((parity.hashes["report_content_hash"], parity.evidence_status, AS_OF),
                         (stored.report_content_hash, stored.status, stored.as_of))
        self.assertEqual([str(stored.assessment_id)], parity.hashes["assessment_ids"])
        self.assertEqual((funding.hashes["evidence_hash"], "EVIDENCE_LINKED", funding.hashes["bridge_hashes"]),
                         (funded.evidence_hash, funded.state, list(funded.bridge_hashes)))
        self.assertIn("NO_HOLDING_INTERVAL_TO_BIND", funded.completeness_blockers)  # not a cycle-bound candidate

        again = self._run(self._injected, funding=self._declaration(), recorded_at=RECORDED_AT + timedelta(days=1))
        self.assertEqual({production.STATUS_ALREADY_STORED}, {item.status for item in again.outcomes})
        self.assertEqual((1, 1), self._stored_counts())

    # -- (d) forged and holdout evidence ----------------------------------------------------------

    def test_c_forged_records_are_refused_before_the_store(self) -> None:
        from trade_platform import paper_evidence_production_v1 as production

        before = self._stored_counts()
        injected = self._injected

        def forged(cursor: Any) -> Any:
            source = injected(cursor)
            rows = source.signal_rows

            def tampered(**kwargs: Any) -> Any:
                out = [dict(row) for row in rows(**kwargs)]
                out[0]["identity"] = {**out[0]["identity"], "explanation": {"forged": "yes"}}
                return out

            source.signal_rows = tampered
            return source

        run = self._run(forged, recorded_at=RECORDED_AT + timedelta(days=2))
        self.assertIn(production.STORED_SIGNAL_DOES_NOT_REDERIVE, self._pick(run, production.KIND_FILL_PARITY).reasons)
        self.assertEqual(before, self._stored_counts())

    def test_d_evidence_over_the_unopened_holdout_is_refused_with_the_store_code(self) -> None:
        from tests.test_live_signals_v1 import DAY0, _live_bars, opened_gate
        from tests.test_paper_evidence_production_v1 import (
            FixtureSources,
            fill_row,
            progress_rows,
            sealed_dataset,
            signal_row,
        )
        from tests.test_paper_incubation_v1 import _run
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform import paper_evidence_production_v1 as production
        from trade_platform.live_signals_v1 import LiveStrategyRunnerV1
        from trade_platform.paper_incubation_v1 import PaperIncubationEngineV1
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1

        # Forward bars inside the cycle's holdout, evaluated under a gate claiming an opening the
        # registry does not hold: the producer finds them consistent, the store refuses.
        bars, gate = _live_bars(_walk(90, seed=21)), opened_gate()
        runner = LiveStrategyRunnerV1([self.candidate], holdout_gate=gate, clock=lambda: DAY0)
        signals = [signal for bar in bars for signal in runner.on_bars([bar])]
        fills = _run(PaperIncubationEngineV1(gross_cost_policy_v1()), signals, bars)
        sources = FixtureSources(signals=[signal_row(s) for s in signals], fills=[fill_row(f) for f in fills],
                                 liveness=progress_rows(self.candidate, gate, bars), live_bars=bars,
                                 sealed=[sealed_dataset(bars)])
        before = self._stored_counts()
        at = DAY0 + timedelta(days=1)
        run = self._run(lambda _cursor: sources, gate=gate, as_of=at, recorded_at=at)
        parity = self._pick(run, production.KIND_FILL_PARITY)
        self.assertEqual((production.STATUS_REFUSED, ("HOLDOUT_UNOPENED_OR_7",
                                                      "SINK_REFUSED:HOLDOUT_GATE_OPENING_NOT_REGISTERED")),
                         (parity.status, parity.reasons))
        self.assertEqual((production.HOLDOUT_UNOPENED_OR_7,), parity.missing_inputs)
        self.assertEqual(before, self._stored_counts())


if __name__ == "__main__":
    unittest.main()
