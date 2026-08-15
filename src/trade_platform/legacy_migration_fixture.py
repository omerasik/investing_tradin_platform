"""Deterministic representative legacy SQLite workflow for migration proof."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

FIXTURE_IDS = {
    "legacy_instrument": "legacy-us-equity",
    "legacy_account": "legacy-paper-account",
    "legacy_strategy_version": "legacy-strategy-v1",
    "legacy_dataset_version": "legacy-dataset-v1",
    "signal_id": "10000000-0000-0000-0000-000000000001",
    "signal_assessment_id": "10000000-0000-0000-0000-000000000002",
    "model_id": "10000000-0000-0000-0000-000000000003",
    "model_validation_id": "10000000-0000-0000-0000-000000000004",
    "model_approval_id": "10000000-0000-0000-0000-000000000005",
    "intent_id": "10000000-0000-0000-0000-000000000006",
    "order_created_event_id": "10000000-0000-0000-0000-000000000007",
    "partial_event_id": "10000000-0000-0000-0000-000000000008",
    "final_event_id": "10000000-0000-0000-0000-000000000009",
    "partial_fill_id": "10000000-0000-0000-0000-000000000010",
    "final_fill_id": "10000000-0000-0000-0000-000000000011",
    "risk_decision_id": "10000000-0000-0000-0000-000000000012",
    "reservation_id": "10000000-0000-0000-0000-000000000013",
    "kill_switch_event_id": "10000000-0000-0000-0000-000000000014",
    "broker_event_id": "10000000-0000-0000-0000-000000000015",
    "reconciliation_id": "10000000-0000-0000-0000-000000000016",
    "account_evidence_id": "10000000-0000-0000-0000-000000000017",
    "quant_artifact_id": "10000000-0000-0000-0000-000000000018",
    "validation_package_id": "10000000-0000-0000-0000-000000000019",
    "promotion_decision_id": "10000000-0000-0000-0000-000000000020",
    "activation_id": "10000000-0000-0000-0000-000000000021",
    "audit_event_id": "10000000-0000-0000-0000-000000000022",
    "risk_pending_event_id": "10000000-0000-0000-0000-000000000023",
    "approved_event_id": "10000000-0000-0000-0000-000000000024",
    "submission_pending_event_id": "10000000-0000-0000-0000-000000000025",
    "submitted_event_id": "10000000-0000-0000-0000-000000000026",
    "acknowledged_event_id": "10000000-0000-0000-0000-000000000027",
}

FIXTURE_TIME = "2026-01-15T15:00:00+00:00"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def create_representative_legacy_fixture(path: str | Path) -> dict[str, str]:
    """Create one complete safe-paper legacy workflow with stable identities."""
    target = Path(path)
    connection = sqlite3.connect(target)
    ids = dict(FIXTURE_IDS)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE instruments (instrument_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                venue TEXT NOT NULL, asset_class TEXT NOT NULL, quote_currency TEXT NOT NULL,
                tick_size TEXT NOT NULL, lot_size TEXT NOT NULL, delisted_at TEXT);
            CREATE TABLE signals (signal_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL, current_status TEXT NOT NULL);
            CREATE TABLE signal_validations (assessment_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL,
                status TEXT NOT NULL, passed_json TEXT NOT NULL, failures_json TEXT NOT NULL,
                assessed_at TEXT NOT NULL);
            CREATE TABLE policy_documents (policy_type TEXT NOT NULL, version TEXT NOT NULL,
                payload_json TEXT NOT NULL, digest TEXT NOT NULL, approved_by TEXT NOT NULL,
                approved_at TEXT NOT NULL, PRIMARY KEY(policy_type, version));
            CREATE TABLE model_versions (model_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                version TEXT NOT NULL, task TEXT NOT NULL, feature_version TEXT NOT NULL,
                dataset_version TEXT NOT NULL, artifact_reference TEXT NOT NULL,
                created_at TEXT NOT NULL);
            CREATE TABLE model_validations (validation_id TEXT PRIMARY KEY, model_id TEXT NOT NULL,
                metrics_json TEXT NOT NULL, economic_value_after_cost TEXT NOT NULL,
                evidence_reference TEXT NOT NULL, validated_at TEXT NOT NULL);
            CREATE TABLE model_approval_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE, model_id TEXT NOT NULL, status TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, validation_id TEXT, occurred_at TEXT NOT NULL);
            CREATE TABLE paper_orders (intent_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL,
                instrument_id TEXT NOT NULL, account_id TEXT NOT NULL, side TEXT NOT NULL,
                quantity TEXT NOT NULL, limit_price TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL, filled_quantity TEXT NOT NULL, average_fill_price TEXT);
            CREATE TABLE paper_order_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE, intent_id TEXT NOT NULL, event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL);
            CREATE TABLE paper_fills (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                fill_id TEXT NOT NULL UNIQUE, external_fill_id TEXT NOT NULL UNIQUE,
                intent_id TEXT NOT NULL, occurred_at TEXT NOT NULL, quantity TEXT NOT NULL,
                price TEXT NOT NULL);
            CREATE TABLE risk_daily_notional (intent_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                trading_day TEXT NOT NULL, notional TEXT NOT NULL);
            CREATE TABLE risk_decisions (decision_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
                decision TEXT NOT NULL, reasons_json TEXT NOT NULL, decided_at TEXT NOT NULL);
            CREATE TABLE kill_switch_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, active INTEGER NOT NULL,
                actor TEXT NOT NULL, occurred_at TEXT NOT NULL);
            CREATE TABLE broker_events (event_id TEXT PRIMARY KEY, broker_name TEXT NOT NULL,
                sequence INTEGER NOT NULL, external_event_id TEXT NOT NULL UNIQUE,
                intent_id TEXT NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL,
                occurred_at TEXT NOT NULL);
            CREATE TABLE broker_cursors (broker_name TEXT PRIMARY KEY, sequence INTEGER NOT NULL);
            CREATE TABLE paper_reconciliation_runs (run_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL, source TEXT NOT NULL, occurred_at TEXT NOT NULL,
                complete INTEGER NOT NULL, discrepancies_json TEXT NOT NULL);
            CREATE TABLE paper_reconciled_accounts (evidence_id TEXT PRIMARY KEY,
                reconciliation_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
                source TEXT NOT NULL, snapshot_json TEXT NOT NULL, healthy INTEGER NOT NULL,
                ingested_at TEXT NOT NULL);
            CREATE TABLE validation_artifacts (artifact_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL UNIQUE, artifact_type TEXT NOT NULL,
                strategy_version TEXT NOT NULL, dataset_version TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE promotion_decisions (decision_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, status TEXT NOT NULL,
                reasons_json TEXT NOT NULL, held_out_periods INTEGER NOT NULL,
                held_out_total_return TEXT, decided_at TEXT NOT NULL);
            CREATE TABLE strategy_activations (activation_id TEXT PRIMARY KEY,
                strategy_version TEXT NOT NULL, active INTEGER NOT NULL, actor TEXT NOT NULL,
                effective_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
                promotion_decision_id TEXT);
            CREATE TABLE audit_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL);
            """
        )
        signal_payload = {
            "signal_id": ids["signal_id"],
            "instrument_id": ids["legacy_instrument"],
            "strategy_version": ids["legacy_strategy_version"],
            "expires_at": "2026-01-16T15:00:00+00:00",
            "direction": "BUY",
        }
        policy_payload = {"maximum_order_notional": "1000.000000000001"}
        quant_payload = {
            "identity": {
                "artifact_id": ids["quant_artifact_id"],
                "artifact_version": "legacy-v1",
                "content_hash": "a" * 64,
                "evaluated_at": FIXTURE_TIME,
            },
            "evidence_type": "capacity",
            "strategy_version": ids["legacy_strategy_version"],
            "dataset_version": ids["legacy_dataset_version"],
            "passed": True,
        }
        package_payload = {
            "identity": {
                "artifact_id": ids["validation_package_id"],
                "artifact_version": "legacy-package-v1",
                "content_hash": "b" * 64,
                "evaluated_at": FIXTURE_TIME,
            },
            "strategy_version": ids["legacy_strategy_version"],
            "dataset_version": ids["legacy_dataset_version"],
            "feature_versions": ["legacy-features-v1"],
            "cost_model_version": "legacy-cost-v1",
            "evidence_ids": {"capacity": ids["quant_artifact_id"]},
            "limitations": ["legacy_fixture"],
        }
        snapshot = {
            "as_of": FIXTURE_TIME,
            "cash_currency": "USD",
            "cash_amount": "980.000000000000",
            "buying_power": "780.000000000000",
            "positions": {ids["legacy_instrument"]: ["2.000000000000", "10.000000000001"]},
            "open_intent_ids": [],
            "fill_ids": ["legacy-fill-partial", "legacy-fill-final"],
            "realized_pnl": "0.000000000000",
            "fees": "0.000000000000",
        }
        rows = (
            ("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)", (ids["legacy_instrument"], "MIGR", "XNYS", "EQUITY", "USD", "0.01", "1", None)),
            ("INSERT INTO signals VALUES (?,?,?,?)", (ids["signal_id"], json.dumps(signal_payload, sort_keys=True), FIXTURE_TIME, "VALIDATED")),
            ("INSERT INTO signal_validations VALUES (?,?,?,?,?,?)", (ids["signal_assessment_id"], ids["signal_id"], "VALIDATED", json.dumps(["DATA", "RISK"]), "[]", FIXTURE_TIME)),
            ("INSERT INTO policy_documents VALUES (?,?,?,?,?,?)", ("risk", "legacy-risk-v1", json.dumps(policy_payload, sort_keys=True), _digest(policy_payload), "legacy-reviewer", FIXTURE_TIME)),
            ("INSERT INTO model_versions VALUES (?,?,?,?,?,?,?,?)", (ids["model_id"], "legacy-model", "v1", "direction", "legacy-features-v1", ids["legacy_dataset_version"], "legacy://model", FIXTURE_TIME)),
            ("INSERT INTO model_validations VALUES (?,?,?,?,?,?)", (ids["model_validation_id"], ids["model_id"], '{"brier":"0.1"}', "0.01", "legacy://validation", FIXTURE_TIME)),
            ("INSERT INTO model_approval_events (event_id,model_id,status,actor,reason,validation_id,occurred_at) VALUES (?,?,?,?,?,?,?)", (ids["model_approval_id"], ids["model_id"], "APPROVED", "legacy-reviewer", "reviewed", ids["model_validation_id"], FIXTURE_TIME)),
            ("INSERT INTO paper_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", (ids["intent_id"], ids["signal_id"], ids["legacy_instrument"], ids["legacy_account"], "BUY", "2.000000000000", "10.000000000001", FIXTURE_TIME, "FILLED", "2.000000000000", "10.000000000001")),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["order_created_event_id"], ids["intent_id"], "ORDER_CREATED", FIXTURE_TIME, '{"status":"PROPOSED"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["risk_pending_event_id"], ids["intent_id"], "ORDER_STATUS_CHANGED", FIXTURE_TIME, '{"from":"PROPOSED","to":"RISK_PENDING"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["approved_event_id"], ids["intent_id"], "ORDER_STATUS_CHANGED", FIXTURE_TIME, '{"from":"RISK_PENDING","to":"APPROVED"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["submission_pending_event_id"], ids["intent_id"], "ORDER_STATUS_CHANGED", FIXTURE_TIME, '{"from":"APPROVED","to":"SUBMISSION_PENDING"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["submitted_event_id"], ids["intent_id"], "ORDER_STATUS_CHANGED", FIXTURE_TIME, '{"from":"SUBMISSION_PENDING","to":"SUBMITTED"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["acknowledged_event_id"], ids["intent_id"], "ORDER_STATUS_CHANGED", FIXTURE_TIME, '{"from":"SUBMITTED","to":"ACKNOWLEDGED"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["partial_event_id"], ids["intent_id"], "FILL_INGESTED", FIXTURE_TIME, '{"external_fill_id":"legacy-fill-partial","price":"10.000000000001","quantity":"1","status":"PARTIALLY_FILLED"}')),
            ("INSERT INTO paper_order_events (event_id,intent_id,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)", (ids["final_event_id"], ids["intent_id"], "FILL_INGESTED", FIXTURE_TIME, '{"external_fill_id":"legacy-fill-final","price":"10.000000000001","quantity":"1","status":"FILLED"}')),
            ("INSERT INTO paper_fills (fill_id,external_fill_id,intent_id,occurred_at,quantity,price) VALUES (?,?,?,?,?,?)", (ids["partial_fill_id"], "legacy-fill-partial", ids["intent_id"], FIXTURE_TIME, "1.000000000000", "10.000000000001")),
            ("INSERT INTO paper_fills (fill_id,external_fill_id,intent_id,occurred_at,quantity,price) VALUES (?,?,?,?,?,?)", (ids["final_fill_id"], "legacy-fill-final", ids["intent_id"], FIXTURE_TIME, "1.000000000000", "10.000000000001")),
            ("INSERT INTO risk_daily_notional VALUES (?,?,?,?)", (ids["intent_id"], ids["legacy_account"], "2026-01-15", "20.000000000002")),
            ("INSERT INTO risk_decisions VALUES (?,?,?,?,?)", (ids["risk_decision_id"], ids["intent_id"], "APPROVE", "[]", FIXTURE_TIME)),
            ("INSERT INTO kill_switch_events (event_id,scope,active,actor,occurred_at) VALUES (?,?,?,?,?)", (ids["kill_switch_event_id"], f"ACCOUNT:{ids['legacy_account']}", 1, "legacy-operator", FIXTURE_TIME)),
            ("INSERT INTO broker_events VALUES (?,?,?,?,?,?,?,?)", (ids["broker_event_id"], "legacy-broker", 1, "legacy-broker-event-1", ids["intent_id"], "FILL", "FILLED", FIXTURE_TIME)),
            ("INSERT INTO broker_cursors VALUES (?,?)", ("legacy-broker", 1)),
            ("INSERT INTO paper_reconciliation_runs VALUES (?,?,?,?,?,?)", (ids["reconciliation_id"], ids["legacy_account"], "legacy-broker", FIXTURE_TIME, 1, "[]")),
            ("INSERT INTO paper_reconciled_accounts VALUES (?,?,?,?,?,?,?)", (ids["account_evidence_id"], ids["reconciliation_id"], ids["legacy_account"], "legacy-broker", json.dumps(snapshot, sort_keys=True), 1, FIXTURE_TIME)),
            ("INSERT INTO validation_artifacts VALUES (?,?,?,?,?,?,?)", (ids["quant_artifact_id"], "a" * 64, "ExternalValidationEvidence", ids["legacy_strategy_version"], ids["legacy_dataset_version"], json.dumps(quant_payload, sort_keys=True), FIXTURE_TIME)),
            ("INSERT INTO validation_artifacts VALUES (?,?,?,?,?,?,?)", (ids["validation_package_id"], "b" * 64, "StrategyValidationPackage", ids["legacy_strategy_version"], ids["legacy_dataset_version"], json.dumps(package_payload, sort_keys=True), FIXTURE_TIME)),
            ("INSERT INTO promotion_decisions VALUES (?,?,?,?,?,?,?,?)", (ids["promotion_decision_id"], "10000000-0000-0000-0000-000000000099", ids["legacy_strategy_version"], "REVIEW_REQUIRED", "[]", 20, "0.10", FIXTURE_TIME)),
            ("INSERT INTO strategy_activations VALUES (?,?,?,?,?,?,?)", (ids["activation_id"], ids["legacy_strategy_version"], 1, "legacy-reviewer", FIXTURE_TIME, FIXTURE_TIME, ids["promotion_decision_id"])),
            ("INSERT INTO audit_events VALUES (?,?,?,?,?)", (ids["audit_event_id"], "LEGACY_WORKFLOW_COMPLETED", FIXTURE_TIME, "legacy-system", '{"complete":true}')),
        )
        for statement, parameters in rows:
            connection.execute(statement, parameters)
        connection.commit()
        return ids
    finally:
        connection.close()
