"""add append-only PostgreSQL runtime safety evidence

Revision ID: 20260814_0002
Revises: 20260813_0001
Create Date: 2026-08-14
"""

from alembic import op

revision = "20260814_0002"
down_revision = "20260813_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The order row is an immutable intent; lifecycle is rebuilt from events.
    op.execute(
        "ALTER TABLE oms_events ADD COLUMN event_sequence BIGINT GENERATED ALWAYS AS IDENTITY"
    )
    op.execute(
        "CREATE UNIQUE INDEX oms_events_sequence_idx ON oms_events(intent_id, event_sequence)"
    )
    op.execute(
        "CREATE TABLE kill_switch_events (event_id UUID PRIMARY KEY, scope TEXT NOT NULL, active BOOLEAN NOT NULL, actor TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL)"
    )
    op.execute(
        "CREATE INDEX kill_switch_events_scope_idx ON kill_switch_events(scope, occurred_at DESC)"
    )
    op.execute(
        "CREATE TABLE broker_sync_events (event_id UUID PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(account_id), source TEXT NOT NULL, sequence BIGINT NOT NULL CHECK(sequence > 0), external_event_id TEXT NOT NULL UNIQUE, intent_id UUID NOT NULL REFERENCES paper_order_intents(intent_id), event_type TEXT NOT NULL, status TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, details JSONB NOT NULL, UNIQUE(account_id, source, sequence))"
    )
    op.execute(
        "CREATE TABLE reconciled_account_evidence (evidence_id UUID PRIMARY KEY, reconciliation_id UUID NOT NULL UNIQUE REFERENCES reconciliations(reconciliation_id), account_id TEXT NOT NULL REFERENCES accounts(account_id), source TEXT NOT NULL, as_of TIMESTAMPTZ NOT NULL, cash_currency CHAR(3) NOT NULL, cash_amount NUMERIC(30,12) NOT NULL, buying_power NUMERIC(30,12) NOT NULL, open_intent_ids JSONB NOT NULL, fill_ids JSONB NOT NULL, realized_pnl NUMERIC(30,12) NOT NULL, fees NUMERIC(30,12) NOT NULL, healthy BOOLEAN NOT NULL, ingested_at TIMESTAMPTZ NOT NULL)"
    )
    op.execute(
        "CREATE TABLE reconciled_position_evidence (evidence_id UUID NOT NULL REFERENCES reconciled_account_evidence(evidence_id), instrument_id UUID NOT NULL REFERENCES instruments(instrument_id), quantity NUMERIC(30,12) NOT NULL, average_cost NUMERIC(30,12) NOT NULL, PRIMARY KEY(evidence_id, instrument_id))"
    )
    op.execute(
        "CREATE TABLE strategy_activation_events (activation_id UUID PRIMARY KEY, strategy_version_id UUID NOT NULL REFERENCES strategy_versions(strategy_version_id), active BOOLEAN NOT NULL, actor TEXT NOT NULL, effective_at TIMESTAMPTZ NOT NULL, ingested_at TIMESTAMPTZ NOT NULL, promotion_decision_id UUID REFERENCES strategy_promotion_decisions(decision_id))"
    )
    op.execute(
        "ALTER TABLE validation_packages ADD COLUMN feature_versions JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "CREATE TABLE postgres_backfill_runs (run_id UUID PRIMARY KEY, source_fingerprint CHAR(64) NOT NULL, mapping_fingerprint CHAR(64) NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('DRY_RUN','APPLY')), started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ, status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','FAILED')), report JSONB NOT NULL, UNIQUE(source_fingerprint, mapping_fingerprint, mode))"
    )
    op.execute(
        "CREATE TABLE postgres_backfill_rows (run_id UUID NOT NULL REFERENCES postgres_backfill_runs(run_id), source_table TEXT NOT NULL, source_identity TEXT NOT NULL, destination_table TEXT NOT NULL, destination_identity TEXT NOT NULL, content_hash CHAR(64) NOT NULL, PRIMARY KEY(run_id, source_table, source_identity), UNIQUE(destination_table, destination_identity))"
    )
    op.execute(
        "ALTER TABLE quant_validation_artifacts DROP CONSTRAINT IF EXISTS quant_validation_artifacts_artifact_type_check"
    )
    op.execute(
        "ALTER TABLE quant_validation_artifacts ADD CONSTRAINT quant_validation_artifacts_artifact_type_check CHECK(artifact_type IN ('capacity','slippage','latency','bootstrap','monte_carlo','stress','parameter_stability','multiple_testing','data_quality','scorecard','execution_realism','oos_walk_forward','golden_reconciliation'))"
    )
    for table in (
        "kill_switch_events",
        "broker_sync_events",
        "reconciled_account_evidence",
        "reconciled_position_evidence",
        "strategy_activation_events",
        "paper_order_intents",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    # Production is forward-only. Kept solely for disposable developer DBs.
    for table in (
        "postgres_backfill_rows",
        "postgres_backfill_runs",
        "strategy_activation_events",
        "reconciled_position_evidence",
        "reconciled_account_evidence",
        "broker_sync_events",
        "kill_switch_events",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP INDEX IF EXISTS oms_events_sequence_idx")
    op.execute("ALTER TABLE oms_events DROP COLUMN IF EXISTS event_sequence")
    op.execute("ALTER TABLE validation_packages DROP COLUMN IF EXISTS feature_versions")
