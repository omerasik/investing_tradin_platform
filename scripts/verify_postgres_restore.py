"""Verify a PostgreSQL backup restored into a separate fresh database."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import psycopg

from trade_platform.persistence import PostgresDatabase
from trade_platform.postgres_paper_oms import PostgresPaperOms
from trade_platform.postgres_recovery import PostgresRecoveryGate

CRITICAL_TABLES = (
    "runtime_signal_proposals",
    "runtime_signal_validations",
    "runtime_signal_lifecycle_events",
    "paper_order_intents",
    "oms_events",
    "fills",
    "broker_sync_events",
    "broker_event_cursors",
    "risk_reservations",
    "risk_decisions",
    "kill_switch_events",
    "reconciliations",
    "reconciled_account_evidence",
    "reconciled_position_evidence",
    "quant_validation_artifacts",
    "validation_packages",
    "validation_package_artifacts",
    "strategy_definitions",
    "strategy_versions",
    "research_experiments",
    "walk_forward_evidence",
    "golden_execution_artifacts",
    "strategy_promotion_decisions",
    "strategy_activation_events",
    "professional_instruments",
    "professional_symbol_mappings",
    "professional_identifier_mappings",
    "professional_instrument_lifecycle_events",
    "professional_calendar_definitions",
    "professional_calendar_weekly_sessions",
    "professional_calendar_exceptions",
    "historical_data_sources",
    "historical_raw_observations",
    "historical_normalized_observations",
    "historical_dataset_versions",
    "historical_dataset_members",
    "historical_ingestion_checkpoints",
    "data_health_assessments",
    "data_health_findings",
    "pit_fundamental_sources",
    "pit_fundamental_filings",
    "pit_fundamental_facts",
    "pit_macro_sources",
    "pit_macro_observations",
    "feature_definition_versions",
    "feature_materializations",
    "strategy_scorecards",
    "scorecard_metric_observations",
    "scorecard_components",
    "scorecard_data_health_assessments",
    "scorecard_validation_packages",
    "investment_theses",
    "investment_evidence",
    "investment_evidence_data_health_assessments",
    "investment_reviews",
    "investment_policy_versions",
    "investment_rebalance_candidates",
    "regime_model_versions",
    "regime_runs",
    "regime_run_data_health_assessments",
    "regime_observations",
    "regime_run_observations",
    "regime_method_evaluations",
    "regime_run_method_evaluations",
    "regime_risk_adjustment_candidates",
    "portfolio_construction_policy_versions",
    "portfolio_construction_runs",
    "portfolio_construction_run_data_health_assessments",
    "portfolio_sleeve_inputs",
    "portfolio_covariance_estimates",
    "portfolio_target_candidates",
    "portfolio_target_sleeve_weights",
    "portfolio_constraint_evaluations",
    "portfolio_risk_gate_evidence",
    "news_source_policy_versions",
    "news_document_revisions",
    "news_document_entity_links",
    "news_event_extractions",
    "news_event_clusters",
    "news_event_cluster_members",
    "news_event_lineage",
    "news_event_assessments",
    "news_event_assessment_data_health",
    "news_event_research_candidates",
    "sre_service_versions",
    "sre_slo_policy_versions",
    "sre_telemetry_events",
    "sre_dependency_probes",
    "sre_sli_windows",
    "sre_alert_policy_versions",
    "sre_alerts",
    "sre_alert_events",
    "sre_incidents",
    "sre_failure_drill_runs",
    "social_source_policy_versions",
    "social_content_observations",
    "social_narrative_clusters",
    "social_narrative_cluster_members",
    "social_narrative_metric_windows",
    "social_narrative_window_data_health",
)


def _snapshot(dsn: str, table: str) -> tuple[int, str]:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT to_jsonb(value)::text FROM {table} AS value "  # nosec B608: fixed allow-list
            "ORDER BY to_jsonb(value)::text"
        )
        rows = [str(row[0]) for row in cursor.fetchall()]
    payload = json.dumps(rows, separators=(",", ":")).encode()
    return len(rows), hashlib.sha256(payload).hexdigest()


def verify_restore(source_dsn: str, restored_dsn: str) -> dict[str, Any]:
    results: dict[str, Any] = {"tables": {}}
    with (
        psycopg.connect(source_dsn) as source,
        psycopg.connect(restored_dsn) as restored,
        source.cursor() as source_cursor,
        restored.cursor() as restored_cursor,
    ):
        source_cursor.execute("SELECT version_num FROM alembic_version")
        restored_cursor.execute("SELECT version_num FROM alembic_version")
        source_revision = str(source_cursor.fetchone()[0])
        restored_revision = str(restored_cursor.fetchone()[0])
        if source_revision != restored_revision:
            raise RuntimeError("restore_alembic_revision_mismatch")
        results["alembic_revision"] = restored_revision

    for table in CRITICAL_TABLES:
        source_snapshot = _snapshot(source_dsn, table)
        restored_snapshot = _snapshot(restored_dsn, table)
        if source_snapshot != restored_snapshot:
            raise RuntimeError(f"restore_table_mismatch:{table}")
        results["tables"][table] = {
            "rows": restored_snapshot[0],
            "sha256": restored_snapshot[1],
        }

    database = PostgresDatabase(restored_dsn)
    gate = PostgresRecoveryGate(database)
    gate.require_reconciliation("fresh_restore_drill")
    if not gate.blocks():
        raise RuntimeError("restore_gate_not_active")
    results["risk_increase_blocked_before_reconciliation"] = True
    results["paper_submission_blocked_before_reconciliation"] = True

    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT package_id, content_hash, canonical_manifest, integrity_status "
            "FROM validation_packages"
        )
        for package_id, content_hash, manifest, status in cursor.fetchall():
            if status == "VERIFIED":
                if manifest is None or hashlib.sha256(str(manifest).encode()).hexdigest() != content_hash:
                    raise RuntimeError(f"restore_validation_manifest_mismatch:{package_id}")
            elif status != "LEGACY_UNVERIFIABLE" or manifest is not None:
                raise RuntimeError(f"restore_validation_integrity_state_invalid:{package_id}")
        cursor.execute("SELECT intent_id FROM paper_order_intents ORDER BY created_at DESC LIMIT 1")
        intent_row = cursor.fetchone()
        if intent_row is None:
            raise RuntimeError("restore_has_no_oms_state")
        intent_id = intent_row[0]
        cursor.execute("SELECT COUNT(*) FROM broker_event_cursors")
        if int(cursor.fetchone()[0]) < 1:
            raise RuntimeError("restore_has_no_broker_cursor")
        cursor.execute("SELECT COUNT(*) FROM risk_decisions")
        if int(cursor.fetchone()[0]) < 1:
            raise RuntimeError("restore_has_no_risk_state")
        cursor.execute("SELECT COUNT(*) FROM kill_switch_events")
        if int(cursor.fetchone()[0]) < 1:
            raise RuntimeError("restore_has_no_kill_switch_state")
        cursor.execute(
            "SELECT COUNT(*) FROM reconciliations WHERE complete = true"
        )
        if int(cursor.fetchone()[0]) < 1:
            raise RuntimeError("restore_reconciliation_incomplete")
        cursor.execute("SELECT COUNT(*) FROM strategy_promotion_decisions")
        if int(cursor.fetchone()[0]) < 1:
            raise RuntimeError("restore_has_no_promotion_history")

    order = PostgresPaperOms(database).get(intent_id)
    results["reconstructed_order_status"] = order.status.value
    gate.mark_reconciled("restore_hashes_and_authorities_reconciled")
    if gate.blocks():
        raise RuntimeError("restore_gate_not_released")
    results["reconciliation"] = "MATCHED"
    database.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dsn", required=True)
    parser.add_argument("--restored-dsn", required=True)
    args = parser.parse_args()
    print(json.dumps(verify_restore(args.source_dsn, args.restored_dsn), sort_keys=True))


if __name__ == "__main__":
    main()
