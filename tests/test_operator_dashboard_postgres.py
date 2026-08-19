import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class OperatorDashboardPostgresTests(unittest.TestCase):
    def test_authority_projections_are_bounded_pit_correct_and_read_only(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        now = datetime(2026, 8, 18, 12, tzinfo=UTC)
        suffix = uuid4().hex[:8]
        digest = lambda name: hashlib.sha256(f"cycle208:{suffix}:{name}".encode()).hexdigest()
        instrument = replace(
            mvp_instrument_universe(datetime(2024, 1, 1, tzinfo=UTC))[0],
            instrument_id=f"US:XNYS:C208-{suffix}", canonical_symbol=f"C208{suffix[:4]}",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)
        ids = {name: uuid4() for name in (
            "feature", "feature_old", "feature_new", "dataset", "dataset_version", "source", "historical_dataset",
            "health", "strategy", "strategy_version", "package", "scorecard", "metric_measured", "metric_assumed",
            "metric_unavailable", "component", "regime_model", "regime_model_version", "regime_run", "observation",
            "regime_candidate", "policy", "policy_version", "portfolio_run", "sleeve", "covariance", "target",
            "constraint", "news_source", "news_policy", "news_initial", "news_retraction", "news_link", "news_extraction",
            "news_assessment", "service", "service_version", "slo", "sli", "probe", "alert_policy", "alert", "alert_open",
            "alert_ack", "incident", "drill", "signal", "signal_validation", "signal_event",
        )}
        manifest = json.dumps({"cycle": 208, "synthetic": True}, sort_keys=True, separators=(",", ":"))
        package_hash = hashlib.sha256(manifest.encode()).hexdigest()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO datasets VALUES (%s,%s,'fixture','terms-v1',%s)",
                (ids["dataset"], f"cycle208-validation-{suffix}", now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO dataset_versions VALUES (%s,%s,'cycle208-v1',%s,NULL,NULL,%s)",
                (ids["dataset_version"], ids["dataset"], digest("dataset-version"), now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO historical_data_sources VALUES (%s,%s,'dashboard-inputs','FIXTURE','test-only',%s,%s,'US_EQUITIES_ETFS',%s)",
                (ids["source"], f"cycle208-source-{suffix}", f"fixture://cycle208/{suffix}", now - timedelta(days=2), now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO historical_dataset_versions VALUES (%s,%s,'cycle208-v1','fixture-normalization-v1',%s,%s,NULL,%s,'SEALED')",
                (ids["historical_dataset"], ids["source"], digest("historical-dataset"), now - timedelta(days=2), now),
            )
            cursor.execute(
                "INSERT INTO data_health_assessments VALUES (%s,%s,'GLOBAL','*','cycle208-health-v1',%s,%s,%s,'INFO',FALSE,%s,'{}'::jsonb)",
                (ids["health"], ids["historical_dataset"], now, now - timedelta(days=2), now, digest("health")),
            )
            signal_payload = json.dumps({
                "direction": "BUY", "strength": "0.8", "confidence": "0.7",
                "data_quality_score": "1", "explanation": "Synthetic review signal",
                "contradicting_evidence": ["fixture risk"],
            })
            cursor.execute(
                "INSERT INTO runtime_signal_proposals VALUES (%s,%s,'trend-cycle208-v1',%s,%s,%s::jsonb)",
                (ids["signal"], instrument.instrument_id, now - timedelta(hours=2), now + timedelta(hours=1), signal_payload),
            )
            cursor.execute(
                "INSERT INTO runtime_signal_validations VALUES (%s,%s,'VALIDATED','[\"data\",\"risk\"]'::jsonb,'[]'::jsonb,%s)",
                (ids["signal_validation"], ids["signal"], now - timedelta(hours=1)),
            )
            cursor.execute(
                "INSERT INTO runtime_signal_lifecycle_events (event_id,signal_id,from_status,to_status,actor,reason,evidence_references,occurred_at) VALUES (%s,%s,'CANDIDATE','VALIDATED','signal_validation','all_validation_stages_passed',%s::jsonb,%s)",
                (ids["signal_event"], ids["signal"], json.dumps([f"signal-validation:{ids['signal_validation']}"]), now - timedelta(hours=1)),
            )
            cursor.execute(
                "INSERT INTO feature_definition_versions VALUES (%s,'cycle208_simple_return','PRICE_RETURNS','1.0.0','quant','Cycle 208 fixture','[\"OHLCV\"]'::jsonb,'[\"close\",\"knowledge_at\"]'::jsonb,'1d','PIT event/effective/knowledge',1,'{\"horizon\":1}'::jsonb,'reject','reject','reject_future_knowledge',NULL,NULL,'decimal','transparent-v1',%s,NULL)",
                (ids["feature"], now - timedelta(days=2)),
            )
            for materialization_id, knowledge_at, value, source in (
                (ids["feature_old"], now - timedelta(hours=2), "0.01", "raw:old"),
                (ids["feature_new"], now - timedelta(hours=1), "0.02", "raw:revision"),
            ):
                cursor.execute(
                    "INSERT INTO feature_materializations VALUES (%s,%s,%s,'cycle208-v1',%s,%s,%s,%s,%s::jsonb,%s,'VALIDATED',%s)",
                    (materialization_id, ids["feature"], instrument.instrument_id, now - timedelta(days=1),
                     now - timedelta(days=1), knowledge_at, knowledge_at, json.dumps([source]), value,
                     digest(str(materialization_id))),
                )
            cursor.execute("INSERT INTO strategy_definitions VALUES (%s,'TREND','cycle208 fixture',%s)", (ids["strategy"], now - timedelta(days=2)))
            cursor.execute(
                "INSERT INTO strategy_versions VALUES (%s,%s,'trend-cycle208-v1','[]'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                (ids["strategy_version"], ids["strategy"], now - timedelta(days=2)),
            )
            cursor.execute(
                "INSERT INTO validation_packages (package_id,strategy_version_id,dataset_version_id,cost_model_version,content_hash,status,created_at,limitations,feature_versions,manifest_version,canonical_manifest,integrity_status) VALUES (%s,%s,%s,'cost-v1',%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,'[\"Synthetic engineering fixture\"]'::jsonb,'[\"simple_return:1.0.0\"]'::jsonb,'validation-package-manifest-v1',%s,'VERIFIED')",
                (ids["package"], ids["strategy_version"], ids["dataset_version"], package_hash, now, manifest),
            )
            cursor.execute(
                "INSERT INTO strategy_scorecards VALUES (%s,'scorecard-v2',%s,'trend-cycle208-v1',%s,'[\"simple_return:1.0.0\"]'::jsonb,'cycle208-v1','cost-v1',%s,%s,'REVIEW_REQUIRED','[\"Synthetic engineering fixture only\"]'::jsonb,'HEALTHY',%s::jsonb,'{\"research_run\":\"fixture://cycle208/run\"}'::jsonb,%s)",
                (ids["scorecard"], ids["strategy"], uuid4(), now, now, json.dumps([str(ids["health"])]), digest("scorecard")),
            )
            for metric_id, family, name, state, value in (
                (ids["metric_measured"], "PERFORMANCE", "total_return", "MEASURED", "0.10"),
                (ids["metric_assumed"], "EXECUTION", "slippage", "ASSUMED", "0.001"),
                (ids["metric_unavailable"], "ROBUSTNESS", "live_forward_return", "UNAVAILABLE", None),
            ):
                cursor.execute(
                    "INSERT INTO scorecard_metric_observations VALUES (%s,%s,%s,%s,%s,%s,'decimal','[]'::jsonb)",
                    (metric_id, ids["scorecard"], family, name, state, value),
                )
            cursor.execute("INSERT INTO scorecard_components VALUES (%s,%s,'complexity','component-v1',10,'Transparent count')", (ids["component"], ids["scorecard"]))
            cursor.execute("INSERT INTO scorecard_validation_packages VALUES (%s,%s,%s)", (ids["scorecard"], ids["package"], package_hash))
            cursor.execute(
                "INSERT INTO regime_model_versions VALUES (%s,%s,'regime-cycle208-v1','rule-v1','RESEARCH_ONLY',%s,'[]'::jsonb,'{}'::jsonb,%s,%s)",
                (ids["regime_model_version"], ids["regime_model"], now - timedelta(days=2), now - timedelta(days=1), digest("regime-model")),
            )
            cursor.execute(
                "INSERT INTO regime_runs VALUES (%s,%s,%s,'cycle208-v1',%s,%s,%s,'REVIEW_REQUIRED','[\"Synthetic fixture\"]'::jsonb,'{}'::jsonb,%s)",
                (ids["regime_run"], ids["regime_model_version"], ids["historical_dataset"], digest("historical-dataset"), instrument.instrument_id, now, digest("regime-run")),
            )
            cursor.execute(
                "INSERT INTO regime_observations VALUES (%s,%s,'RULE_BASED','TREND','MEASURED','BULL_TREND','{\"BULL_TREND\":0.8,\"BEAR_TREND\":0.2}'::jsonb,0.2,%s::jsonb,%s)",
                (ids["observation"], now - timedelta(days=1), json.dumps([str(ids["feature_new"])]), digest("observation")),
            )
            cursor.execute("INSERT INTO regime_run_observations VALUES (%s,%s,0)", (ids["regime_run"], ids["observation"]))
            cursor.execute(
                "INSERT INTO regime_risk_adjustment_candidates VALUES (%s,%s,%s,1,0.8,1,'REDUCE','REVIEW_REQUIRED','[\"regime_reduction\"]'::jsonb,FALSE,%s,%s)",
                (ids["regime_candidate"], ids["regime_run"], ids["strategy_version"], now, digest("regime-candidate")),
            )
            policy = {"target_volatility": "0.10", "factor_exposure_limits": [["market", "1"]]}
            cursor.execute(
                "INSERT INTO portfolio_construction_policy_versions VALUES (%s,%s,'policy-cycle208-v1','construction-v1','RESEARCH_ONLY',%s::jsonb,FALSE,%s,%s)",
                (ids["policy_version"], ids["policy"], json.dumps(policy), now - timedelta(days=1), digest("policy")),
            )
            cursor.execute(
                "INSERT INTO portfolio_construction_runs VALUES (%s,%s,%s,%s,%s,'REVIEW_REQUIRED',100000,'[\"Synthetic fixture\"]'::jsonb,'{}'::jsonb,%s)",
                (ids["portfolio_run"], ids["policy_version"], ids["regime_run"], digest("regime-run"), now, digest("portfolio-run")),
            )
            sleeve_payload = {"normalized_signal": "0.7", "risk_budget": "0.5", "capacity_weight": "0.8", "liquidity_score": "0.7", "drawdown": "0.1", "regime_current_multiplier": "1", "regime_proposed_multiplier": "0.8"}
            cursor.execute(
                "INSERT INTO portfolio_sleeve_inputs VALUES (%s,%s,0,%s,%s,%s,%s,'trend',%s::jsonb,%s)",
                (ids["sleeve"], ids["portfolio_run"], ids["strategy_version"], ids["scorecard"], ids["package"], ids["regime_candidate"], json.dumps(sleeve_payload), digest("sleeve")),
            )
            matrix = json.dumps([["0.04"]])
            cursor.execute(
                "INSERT INTO portfolio_covariance_estimates VALUES (%s,%s,%s,'cycle208-v1',%s,'sample-v1',252,%s,%s::jsonb,%s::jsonb,0.2,0.3,%s)",
                (ids["covariance"], ids["portfolio_run"], ids["historical_dataset"], digest("historical-dataset"), now, matrix, matrix, digest("covariance")),
            )
            cursor.execute(
                "INSERT INTO portfolio_target_candidates VALUES (%s,%s,'REVIEW_REQUIRED',FALSE,0.4,0.6,0.6,0.09,0.11,%s,%s)",
                (ids["target"], ids["portfolio_run"], now, digest("target")),
            )
            cursor.execute("INSERT INTO portfolio_target_sleeve_weights VALUES (%s,%s,0.6,60000,0.3,0.18,'[\"capacity_reduction\",\"regime_reduction\"]'::jsonb)", (ids["target"], ids["sleeve"]))
            cursor.execute("INSERT INTO portfolio_constraint_evaluations VALUES (%s,%s,'target_volatility','REDUCED',0.11,0.10,'[\"volatility_reduction\"]'::jsonb,%s)", (ids["constraint"], ids["portfolio_run"], digest("constraint")))
            cursor.execute("INSERT INTO portfolio_risk_gate_evidence VALUES (%s,TRUE,60000,60000,5000,'[]'::jsonb,'{}'::jsonb,FALSE,%s)", (ids["portfolio_run"], digest("risk-gate")))
            cursor.execute(
                "INSERT INTO news_source_policy_versions VALUES (%s,%s,'fixture-news','v1','terms-v1','secret-reference','NOT_APPROVED',FALSE,TRUE,'[\"en\"]'::jsonb,0.5,'CONFIGURATION_ONLY',FALSE,%s,%s)",
                (ids["news_policy"], ids["news_source"], now - timedelta(days=1), digest("news-policy")),
            )
            cursor.execute(
                "INSERT INTO news_document_revisions VALUES (%s,%s,'item-1','item-1',0,'INITIAL',NULL,'fixture://news/initial','en','Fixture issuer guidance',%s,%s,%s,%s,%s,'REPORTED','[]'::jsonb,'{}'::jsonb,%s)",
                (ids["news_initial"], ids["news_policy"], now - timedelta(hours=3), now - timedelta(hours=3), now - timedelta(hours=2), digest("news-fingerprint-initial"), digest("news-raw-initial"), digest("news-initial")),
            )
            cursor.execute(
                "INSERT INTO news_document_revisions VALUES (%s,%s,'item-1-retraction','item-1',1,'RETRACTION',%s,'fixture://news/retraction','en','Fixture issuer retracts guidance',%s,%s,%s,%s,%s,'OFFICIAL','[]'::jsonb,'{}'::jsonb,%s)",
                (ids["news_retraction"], ids["news_policy"], ids["news_initial"], now - timedelta(hours=3), now - timedelta(hours=1), now, digest("news-fingerprint-retraction"), digest("news-raw-retraction"), digest("news-retraction")),
            )
            cursor.execute("INSERT INTO news_event_lineage VALUES (%s,%s,'RETRACTS')", (ids["news_initial"], ids["news_retraction"]))
            cursor.execute("INSERT INTO news_document_entity_links VALUES (%s,%s,%s,'REVIEWED',1,FALSE,'{}'::jsonb,%s)", (ids["news_link"], ids["news_retraction"], instrument.instrument_id, digest("news-link")))
            cursor.execute(
                "INSERT INTO news_event_extractions VALUES (%s,%s,'EARNINGS','taxonomy-v1','model-v1',0.5,0.8,0.7,'DAYS',%s,'[]'::jsonb,'[\"Synthetic fixture\"]'::jsonb,%s)",
                (ids["news_extraction"], ids["news_retraction"], now, digest("news-extraction")),
            )
            cursor.execute("INSERT INTO news_event_assessments VALUES (%s,%s,%s,%s,%s,'WITHDRAWN',0.4,'[\"retracted\"]'::jsonb,'{}'::jsonb,%s)", (ids["news_assessment"], ids["news_retraction"], ids["news_extraction"], ids["news_link"], now, digest("news-assessment")))
            cursor.execute(
                "INSERT INTO sre_service_versions VALUES (%s,%s,'trade-api','cycle208-v1',%s,'CI','EVIDENCE_ONLY','[\"postgresql\"]'::jsonb,%s,%s)",
                (ids["service_version"], ids["service"], "a" * 40, now - timedelta(days=1), digest("service")),
            )
            cursor.execute("INSERT INTO sre_slo_policy_versions VALUES (%s,%s,'availability','good/total',0.99,3600,1,'CANDIDATE_ONLY',FALSE,%s,%s)", (ids["slo"], ids["service_version"], now - timedelta(days=1), digest("slo")))
            cursor.execute("INSERT INTO sre_sli_windows VALUES (%s,%s,%s,%s,98,100,0.98,1,'MEASURED','ENGINEERING_EVIDENCE_ONLY',%s)", (ids["sli"], ids["slo"], now - timedelta(hours=1), now, digest("sli")))
            cursor.execute("INSERT INTO sre_dependency_probes VALUES (%s,%s,'postgresql','DEPENDENCY','HEALTHY',%s,1,NULL,%s,'{}'::jsonb,%s)", (ids["probe"], ids["service_version"], now, "1" * 32, digest("probe")))
            cursor.execute("INSERT INTO sre_alert_policy_versions VALUES (%s,%s,'POSTGRES_DOWN','CRITICAL','platform','runbook://database','on-call',60,'{\"status\":\"UNAVAILABLE\"}'::jsonb,'EVIDENCE_ONLY',%s,%s)", (ids["alert_policy"], ids["service_version"], now - timedelta(days=1), digest("alert-policy")))
            cursor.execute("INSERT INTO sre_alerts VALUES (%s,%s,%s,'postgres:primary',%s,'fixture://sre/postgres',%s,%s)", (ids["alert"], ids["alert_policy"], digest("fingerprint"), now - timedelta(minutes=10), "2" * 32, digest("alert")))
            cursor.execute("INSERT INTO sre_alert_events VALUES (%s,%s,0,'OPEN','probe',%s,'{}'::jsonb,%s)", (ids["alert_open"], ids["alert"], now - timedelta(minutes=10), digest("alert-open")))
            cursor.execute("INSERT INTO sre_alert_events VALUES (%s,%s,1,'ACKNOWLEDGED','operator',%s,'{}'::jsonb,%s)", (ids["alert_ack"], ids["alert"], now - timedelta(minutes=9), digest("alert-ack")))
            cursor.execute("INSERT INTO sre_incidents VALUES (%s,%s,'CRITICAL','platform','runbook://database',%s,NULL,NULL,'DECLARED','{\"reason\":\"fixture outage\"}'::jsonb,%s)", (ids["incident"], ids["alert"], now - timedelta(minutes=10), digest("incident")))
            cursor.execute("INSERT INTO sre_failure_drill_runs VALUES (%s,%s,'restore','database unavailable','writes blocked','writes blocked',%s,%s,3,TRUE,'fixture://sre/drill','CI',%s)", (ids["drill"], ids["service_version"], now - timedelta(hours=2), now - timedelta(hours=2) + timedelta(seconds=3), digest("drill")))

        queries = PostgresOperatorDashboardQueries(database)
        definitions = queries.feature_definitions(family="PRICE_RETURNS", limit=10, offset=0)
        pit_old = queries.feature_materializations(
            feature_id=ids["feature"], instrument=instrument.instrument_id, dataset_version="cycle208-v1",
            decision_time=now - timedelta(hours=90), limit=10, offset=0,
        )
        pit_current = queries.feature_materializations(
            feature_id=ids["feature"], instrument=instrument.instrument_id, dataset_version="cycle208-v1",
            decision_time=now, limit=10, offset=0,
        )
        pit_before_revision = queries.feature_materializations(
            feature_id=ids["feature"], instrument=instrument.instrument_id, dataset_version="cycle208-v1",
            decision_time=now - timedelta(minutes=90), limit=10, offset=0,
        )
        scorecard = queries.strategy_scorecard(ids["scorecard"])
        signals = queries.signals(as_of=now, status="VALIDATED", instrument=instrument.instrument_id, strategy_version=None, limit=10, offset=0)
        regime = queries.regime_run(ids["regime_run"])
        portfolio = queries.portfolio_construction(ids["portfolio_run"])
        news = queries.news_events(
            instrument=instrument.instrument_id, entity=None, category="EARNINGS", start=now - timedelta(days=1),
            end=now + timedelta(minutes=1), correction_state="RETRACTION", limit=10, offset=0,
        )
        sre = queries.sre_overview(ids["service_version"])
        self.assertEqual(definitions.items[0].feature_definition_id, ids["feature"])
        self.assertEqual(pit_old.items, [])
        self.assertEqual((pit_before_revision.items[0].value, pit_before_revision.items[0].source_manifest), ("0.010000000000", ["raw:old"]))
        self.assertEqual((pit_current.items[0].value, pit_current.items[0].source_manifest), ("0.020000000000", ["raw:revision"]))
        evidence_states = {metric.evidence_state for group in scorecard.groups for metric in group.metrics}
        self.assertEqual(evidence_states, {"MEASURED", "ASSUMED", "UNAVAILABLE"})
        self.assertEqual(scorecard.evidence_classification, "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY")
        self.assertEqual((signals.state, signals.items[0].latest_reason, signals.items[0].automatic_authority), ("AVAILABLE", "all_validation_stages_passed", False))
        self.assertEqual((regime.dimensions[0].dimension, regime.risk_effects[0].action), ("TREND", "REDUCE"))
        self.assertFalse(regime.risk_effects[0].automatic_authority)
        self.assertEqual((portfolio.review_only, portfolio.sleeves[0].adjustment_reasons, portfolio.covariance.provider_backed), (True, ["capacity_reduction", "regime_reduction"], False))
        self.assertEqual((news.provider_state, news.items[0].revision_kind, news.items[0].correction_chain[0].relation), ("EXTERNAL_BLOCKED", "RETRACTION", "RETRACTS"))
        self.assertEqual((sre.slos[0].target_state, sre.slos[0].measured_state, sre.incidents[0].status, sre.reconciliation_status), ("TARGET", "MEASURED", "DECLARED", "UNAVAILABLE"))
        serialized = f"{scorecard.model_dump_json()} {regime.model_dump_json()} {portfolio.model_dump_json()} {news.model_dump_json()} {sre.model_dump_json()}".casefold()
        self.assertNotIn("secret-reference", serialized)
        self.assertNotIn(dsn.casefold(), serialized)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan=off")
            cursor.execute("EXPLAIN (COSTS OFF) SELECT * FROM feature_materializations WHERE feature_id=%s AND instrument_id=%s AND dataset_version=%s AND event_at<=%s ORDER BY event_at,knowledge_at DESC LIMIT 10", (ids["feature"], instrument.instrument_id, "cycle208-v1", now))
            feature_plan = " ".join(str(row[0]) for row in cursor.fetchall())
            cursor.execute("EXPLAIN (COSTS OFF) SELECT * FROM news_document_revisions WHERE root_source_item_id='item-1' AND published_at<=%s ORDER BY published_at DESC LIMIT 10", (now,))
            news_plan = " ".join(str(row[0]) for row in cursor.fetchall())
        self.assertIn("feature_materializations_asof_idx", feature_plan)
        self.assertIn("news_document_pit_idx", news_plan)
        database.close()


if __name__ == "__main__":
    unittest.main()
