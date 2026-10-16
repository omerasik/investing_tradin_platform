"""Phase R4.5 -- Strategy Lab read model over real PostgreSQL (synthetic studies only)."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from uuid import UUID, uuid4

from tests.test_strategy_lab_ledger_v1_postgres import _Clock, _study
from tests.test_strategy_lab_manifest_v1_postgres import tie_evaluator


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyLabReadModelPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_study_detail_reflects_ledger_manifest_and_candidates(self) -> None:
        from trade_platform.operator_dashboard import (
            DashboardObjectNotFound,
            PostgresOperatorDashboardQueries,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
        from trade_platform.strategy_lab_manifest_v1 import (
            CandidateSelectionRuleV1,
            PostgresStrategyLabManifestStoreV1,
            RankDirectionV1,
            build_study_manifest_v1,
            freeze_candidates_v1,
        )
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            clock = _Clock(datetime(2026, 9, 4, 12, tzinfo=UTC))
            ledger = PostgresStrategyLabLedgerV1(database, clock=clock)
            spec = _study(6)
            run_trial_worker_v1(ledger, spec, tie_evaluator, worker="read", clock=clock)
            manifest = build_study_manifest_v1(ledger, spec)
            store = PostgresStrategyLabManifestStoreV1(database)
            store.record_manifest(manifest)
            chosen = freeze_candidates_v1(manifest, CandidateSelectionRuleV1("score", RankDirectionV1.HIGHER_IS_BETTER, 2))
            store.record_candidate_set(chosen)

            queries = PostgresOperatorDashboardQueries(database)
            detail = queries.strategy_lab_study(spec.study_id)
            self.assertEqual(detail.summary.planned_trial_count, 6)
            self.assertEqual(detail.summary.queue_states, {"SUCCEEDED": 6})
            self.assertEqual(detail.summary.result_count, 6)
            self.assertEqual(detail.summary.authority.authority_status, "NON_AUTHORITATIVE")
            self.assertFalse(detail.summary.authority.promotable)
            self.assertEqual(detail.summary.authority.reasons, list(spec.authority().reasons))
            self.assertEqual([m.manifest_hash for m in detail.manifests], [manifest.manifest_hash])
            self.assertEqual(detail.manifests[0].outcomes, {"EVALUATED": 5, "INADMISSIBLE_PARAMETERS": 1})
            (candidate_set,) = detail.candidate_sets
            self.assertEqual(candidate_set.candidate_set_hash, chosen.candidate_set_hash)
            self.assertEqual(candidate_set.authoritative_rerun, "PENDING_OWNER_DECISION_OR_3")
            self.assertEqual(candidate_set.multiple_testing_trial_count, 6)
            page = queries.strategy_lab_studies(limit=100, offset=0)
            self.assertIn(spec.study_id, [item.study_id for item in page.items])
            with self.assertRaises(DashboardObjectNotFound):
                queries.strategy_lab_study(uuid4())

            # B.1.2a: legacy (pre-policy) study economics stay unavailable; holdout boundary restated.
            self.assertEqual(detail.economics.verified_costs, "UNAVAILABLE")
            self.assertEqual(detail.economics.net_profitability, "UNAVAILABLE")
            self.assertEqual(detail.holdout.boundary, datetime(2026, 8, 20, tzinfo=UTC))
            self.assertTrue(detail.holdout.evaluation_before_boundary)
            self.assertEqual(detail.reruns, [])
            self.assertEqual(detail.summary.instrument_source, "UNRESOLVED")  # no catalogue passed

            # Server-side sort over the exact stored text; inadmissible (no metric) always last.
            first = queries.strategy_lab_trials(spec.study_id, limit=2, offset=0, sort="trades", direction="desc")
            self.assertEqual((first.total, first.page.has_more), (6, True))
            self.assertEqual([t.metrics["trades"] for t in first.items], ["6", "4"])
            second = queries.strategy_lab_trials(spec.study_id, limit=2, offset=2, sort="trades", direction="desc")
            self.assertEqual([t.metrics["trades"] for t in second.items], ["3", "2"])
            last = queries.strategy_lab_trials(spec.study_id, limit=2, offset=4, sort="trades", direction="desc")
            self.assertEqual([t.outcome for t in last.items], ["EVALUATED", "INADMISSIBLE_PARAMETERS"])
            self.assertFalse(last.page.has_more)
            ascending = queries.strategy_lab_trials(spec.study_id, limit=6, offset=0, sort="score", direction="asc")
            # Ties (score 1.0 twice) break by ordinal; the stored text is returned verbatim ("1.0", not "1").
            self.assertEqual([t.metrics.get("score") for t in ascending.items], ["0.0", "1.0", "1.0", "2.0", "3.0", None])
            self.assertEqual(ascending.items[1].ordinal < ascending.items[2].ordinal, True)
            self.assertEqual(ascending.metric_keys, ["score", "trades"])
            self.assertEqual(ascending.numeric_tier, "SEARCH_NON_AUTHORITATIVE")
            self.assertEqual(queries.strategy_lab_trials(spec.study_id, outcome="INADMISSIBLE_PARAMETERS").total, 1)
            self.assertEqual(queries.strategy_lab_trials(spec.study_id, outcome="PENDING").total, 0)
            frozen = queries.strategy_lab_trials(spec.study_id, frozen_only=True, sort="score", direction="desc")
            self.assertEqual([(t.frozen_rank, t.metrics["score"]) for t in frozen.items], [(1, "3.0"), (2, "2.0")])
            self.assertTrue(all(t.rerun is None and t.candidate_state is None for t in frozen.items))
            with self.assertRaises(DashboardObjectNotFound):
                queries.strategy_lab_trials(uuid4())

            trial = queries.strategy_lab_trial(spec.study_id, frozen.items[0].trial_id)
            self.assertEqual(trial.trial.parameters, {"window": "6"})
            self.assertEqual((trial.rerun_lags, trial.candidate_states), ([], []))
            with self.assertRaises(DashboardObjectNotFound):
                queries.strategy_lab_trial(spec.study_id, uuid4())

            # Instruments resolve only through the window catalogue, by exact (id, content hash).
            window = {"dataset_version_id": str(UUID(int=7)), "content_hash": "c" * 64, "symbol": "FIXTUREUSDT",
                      "first_utc_day": "2026-05-01", "last_utc_day": "2026-05-31", "bars": 10, "continuous": True,
                      "not_published_days": [], "rejected_days": []}
            matched = queries.strategy_lab_studies(limit=100, offset=0, strategy_family="ledger_fixture",
                                                   instrument="FIXTUREUSDT", windows=[window])
            self.assertIn(spec.study_id, [item.study_id for item in matched.items])
            self.assertTrue(all(item.instrument == "FIXTUREUSDT" and item.strategy_family == "ledger_fixture"
                                for item in matched.items))
            self.assertIn("FIXTUREUSDT", matched.facets.instruments)
            self.assertIn("ledger_fixture", matched.facets.strategy_families)
            mismatched = {**window, "content_hash": "d" * 64}
            self.assertNotIn(spec.study_id, [item.study_id for item in queries.strategy_lab_studies(
                limit=100, offset=0, instrument="FIXTUREUSDT", windows=[mismatched]).items])
            self.assertEqual(queries.strategy_lab_studies(limit=100, offset=0, instrument="FIXTUREUSDT").total, 0)
        finally:
            database.close()

    def test_heatmap_and_break_even_distribution_match_the_stored_trials(self) -> None:
        import hashlib

        from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import (
            PostgresStrategyLabLedgerV1,
            TrialOutcomeV1,
        )
        from trade_platform.strategy_lab_study_v1 import (
            DatasetBindingV1,
            InputRoleV1,
            ParameterDomainV1,
            ParameterKindV1,
            ParameterSpaceV1,
            SearchModeV1,
            SearchPlanV1,
            StrategySpecV1,
            StudySpecV1,
        )
        from trade_platform.strategy_lab_worker_v1 import TrialEvaluationV1, run_trial_worker_v1

        strategy = StrategySpecV1(
            family="heatmap_fixture", version="1.0.0", implementation_sha256=hashlib.sha256(uuid4().bytes).hexdigest(),
            execution_convention="next_bar_open",
            input_roles=(InputRoleV1("bars", frozenset({EvidenceTierV1.T0_SYNTHETIC})),),
            parameter_schema={"fast": ParameterKindV1.INTEGER, "slow": ParameterKindV1.INTEGER,
                              "band": ParameterKindV1.DECIMAL},
        )
        spec = StudySpecV1(
            strategy=strategy,
            parameter_space=ParameterSpaceV1.of(
                ParameterDomainV1.integer_values("fast", [1, 2, 3]), ParameterDomainV1.integer_values("slow", [2, 3]),
                ParameterDomainV1.decimal_set("band", ["0", "0.5"])),
            datasets=(DatasetBindingV1("bars", UUID(int=7), "c" * 64, EvidenceTierV1.T0_SYNTHETIC,
                                       datetime(2026, 6, 1, tzinfo=UTC)),),
            search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=12),
            evaluation_upper_bound_exclusive=datetime(2026, 7, 1, tzinfo=UTC), label="heatmap fixture",
        )

        def evaluator(study, parameters):  # type: ignore[no-untyped-def]
            fast, slow = int(parameters["fast"]), int(parameters["slow"])
            if fast >= slow:
                return TrialEvaluationV1(TrialOutcomeV1.INADMISSIBLE_PARAMETERS, {"reason": "fast_not_shorter_than_slow"})
            return TrialEvaluationV1(TrialOutcomeV1.EVALUATED,
                                     {"score": f"{fast * slow}.{str(parameters['band']).replace('.', '')}", "trades": fast})

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            clock = _Clock(datetime(2026, 9, 4, 12, tzinfo=UTC))
            run_trial_worker_v1(PostgresStrategyLabLedgerV1(database, clock=clock), spec, evaluator,
                                worker="heatmap", clock=clock)
            queries = PostgresOperatorDashboardQueries(database)
            required = queries.strategy_lab_heatmap(spec.study_id, x="fast", y="slow", metric="score", fixed={})
            self.assertEqual((required.state, required.required_fixed), ("SELECTION_REQUIRED", ["band"]))
            view = queries.strategy_lab_heatmap(spec.study_id, x="fast", y="slow", metric="score",
                                                fixed={"band": "0.5"})
            self.assertEqual(view.state, "AVAILABLE")
            self.assertEqual(view.counts, {"VALUE": 3, "INADMISSIBLE_PARAMETERS": 3})
            # Independent check: every cell equals the one stored result with exactly these parameters.
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT t.trial_id, t.parameters->>'fast', t.parameters->>'slow', r.outcome, r.metrics->>'score' "
                    "FROM strategy_lab_trials t JOIN strategy_lab_trial_results r USING (trial_id) "
                    "WHERE t.study_id=%s AND t.parameters->>'band'='0.5'", (spec.study_id,))
                stored = {(row[1], row[2]): row for row in cursor.fetchall()}
            self.assertEqual(len(stored), len(view.cells))
            for cell in view.cells:
                row = stored[cell.x, cell.y]
                self.assertEqual(cell.trial_ids, [row[0]])
                self.assertEqual(cell.value, row[4] if row[3] == "EVALUATED" else None)
            self.assertEqual([c.value for c in view.cells if c.state == "VALUE"], ["2.05", "3.05", "6.05"])
            self.assertEqual([c.rank for c in view.cells if c.state == "VALUE"], [1, 2, 3])
            self.assertEqual(view.evidence.study_content_hash, spec.content_hash)
            self.assertEqual((view.evidence.result_count, view.evidence.trial_count), (12, 12))
            invalid = queries.strategy_lab_heatmap(spec.study_id, x="fast", y="slow", metric="score",
                                                   fixed={"band": "0.50"})
            self.assertEqual((invalid.state, invalid.cells), ("SELECTION_INVALID", []))

            # A pre-policy study declares no break-even reporting: unavailable, with outcomes still restated.
            distribution = queries.strategy_lab_break_even_distribution(spec.study_id)
            self.assertEqual(distribution.state, "UNAVAILABLE")
            self.assertEqual(distribution.trial_outcomes, {"EVALUATED": 6, "INADMISSIBLE_PARAMETERS": 6})
            self.assertEqual(distribution.evidence.results_digest, view.evidence.results_digest)
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
