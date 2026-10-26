"""Synthetic SDK worker scope acceptance against existing PostgreSQL; rolled back."""

import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tests.test_strategy_lab_e2e_fixture import build_window_and_study


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class EvaluationScopeWorkerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")

    def setUp(self):
        import psycopg

        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_study_v1 import ParameterDomainV1 as D
        from trade_platform.strategy_lab_study_v1 import ParameterSpaceV1 as S

        connection = psycopg.connect(os.environ["POSTGRES_TEST_DSN"])
        self.addCleanup(connection.close)
        outer = connection.transaction(force_rollback=True)
        outer.__enter__()
        self.addCleanup(outer.__exit__, None, None, None)

        class RollbackDatabase:
            @contextmanager
            def transaction(self):
                with connection.transaction():
                    yield connection

        self.clock = lambda: datetime(2026, 9, 2, 12, tzinfo=UTC)
        self.ledger = PostgresStrategyLabLedgerV1(RollbackDatabase(), clock=self.clock)
        self.temp = tempfile.TemporaryDirectory(prefix="scope-worker-fixture-")
        self.addCleanup(self.temp.cleanup)
        space = S.of(D.integer_values("fast_bars", [15, 30]), D.integer_values("slow_bars", [120]),
                     D.decimal_set("band", ["0.002"]), D.categorical_set("direction", ["long_short"]))
        self.study, self.root = build_window_and_study(Path(self.temp.name), family="trend_ma_cross", space=space)

    def test_real_claim_complete_and_result_hashes_match_standalone_with_one_batch_verification(self):
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
        from trade_platform.strategy_lab_data_loading_v1 import BarStrategyEvaluatorV1
        from trade_platform.strategy_lab_ledger_v1 import result_content_hash_v1
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        evaluator = BarStrategyEvaluatorV1(self.root)
        expected = {}
        for trial in self.study.trials():
            result = evaluator(self.study, self.study.parameter_space.typed_point(trial.parameters))
            expected[trial.trial_id] = result_content_hash_v1(trial_content_hash=trial.content_hash,
                outcome=result.outcome, metrics=result.metrics, cost_policy_slot=self.study.cost_policy_slot)
        original = ResearchFrameStoreV1.verify
        calls = []

        def counted(store, manifest):
            calls.append(manifest.manifest_hash)
            return original(store, manifest)

        with patch.object(ResearchFrameStoreV1, "verify", counted):
            report = run_trial_worker_v1(self.ledger, self.study, evaluator, worker="fixture-scope", batch_size=2, clock=self.clock)
        self.assertEqual((report.succeeded, report.failed, report.lost_leases), (2, 0, 0))
        self.assertEqual(len(calls), 1)
        self.assertEqual({r.trial_id: r.result_content_hash for r in self.ledger.results(self.study.study_id)}, expected)

    def test_corrupt_admission_records_failures_and_supplies_no_result(self):
        from trade_platform.public_archive_research_bars_v1 import load_research_bar_dataset_v1
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
        from trade_platform.strategy_lab_data_loading_v1 import BarStrategyEvaluatorV1
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        binding, = self.study.datasets
        store = ResearchFrameStoreV1(self.root)
        dataset = load_research_bar_dataset_v1(store, binding.dataset_version_id)
        path = store.object_path(store.load_manifest(dataset.bar_frame_manifest_hash).objects[0])
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(data)
        report = run_trial_worker_v1(self.ledger, self.study, BarStrategyEvaluatorV1(self.root),
                                     worker="fixture-corrupt-scope", batch_size=2, clock=self.clock)
        self.assertEqual((report.succeeded, report.failed), (0, 2))
        self.assertEqual(self.ledger.results(self.study.study_id), ())

    def test_mid_calculation_stop_leaves_claims_for_lease_expiry_without_false_result(self):
        from trade_platform import strategy_sdk_v1 as sdk
        from trade_platform.strategy_lab_data_loading_v1 import BarStrategyEvaluatorV1
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        stopped = threading.Event()
        kind = type(sdk.FAMILIES_V1[self.study.strategy.family])
        original = kind.targets_f64

        def stop_during_calculation(*args, **kw):
            stopped.set()
            return original(*args, **kw)

        with patch.object(kind, "targets_f64", stop_during_calculation):
            report = run_trial_worker_v1(self.ledger, self.study, BarStrategyEvaluatorV1(self.root),
                worker="fixture-cancelled-scope", batch_size=2, clock=self.clock, should_stop=stopped.is_set)
        self.assertTrue(report.stopped)
        self.assertEqual((report.succeeded, report.failed), (0, 0))
        self.assertEqual(self.ledger.progress(self.study.study_id).states, {"CLAIMED": 2})
        self.assertEqual(self.ledger.results(self.study.study_id), ())
