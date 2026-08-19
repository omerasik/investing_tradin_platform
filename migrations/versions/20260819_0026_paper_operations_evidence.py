"""add immutable simulated paper operations evidence

Revision ID: 20260819_0026
Revises: 20260819_0025
Create Date: 2026-08-19
"""

from alembic import op

revision = "20260819_0026"
down_revision = "20260819_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE paper_execution_quality_evidence ("
        "evidence_id UUID PRIMARY KEY, "
        "intent_id UUID NOT NULL REFERENCES paper_order_intents(intent_id), "
        "policy_version TEXT NOT NULL CHECK (btrim(policy_version) <> ''), "
        "policy_parameters JSONB NOT NULL CHECK "
        "(jsonb_typeof(policy_parameters) = 'object'), "
        "evidence_class TEXT NOT NULL CHECK "
        "(evidence_class = 'SIMULATED_PAPER_REFERENCE'), "
        "reference_source TEXT NOT NULL CHECK (btrim(reference_source) <> ''), "
        "arrival_price NUMERIC(30,12) NOT NULL CHECK (arrival_price > 0), "
        "decision_price NUMERIC(30,12) NOT NULL CHECK (decision_price > 0), "
        "requested_quantity NUMERIC(30,12) NOT NULL CHECK (requested_quantity > 0), "
        "filled_quantity NUMERIC(30,12) NOT NULL CHECK "
        "(filled_quantity >= 0 AND filled_quantity <= requested_quantity), "
        "fill_ratio NUMERIC(30,12) NOT NULL CHECK (fill_ratio >= 0 AND fill_ratio <= 1), "
        "vwap NUMERIC(30,12) CHECK (vwap > 0), "
        "adverse_arrival_slippage_fraction NUMERIC(30,12), "
        "adverse_decision_slippage_fraction NUMERIC(30,12), "
        "realized_shortfall_fraction NUMERIC(30,12), "
        "first_fill_latency_ms BIGINT CHECK (first_fill_latency_ms >= 0), "
        "completion_latency_ms BIGINT CHECK (completion_latency_ms >= 0), "
        "final_status TEXT NOT NULL CHECK "
        "(final_status IN ('FILLED','CANCELLED','REJECTED','EXPIRED')), "
        "passed BOOLEAN NOT NULL, "
        "breach_reasons JSONB NOT NULL CHECK (jsonb_typeof(breach_reasons) = 'array'), "
        "limitations JSONB NOT NULL CHECK (jsonb_typeof(limitations) = 'array'), "
        "fill_ids JSONB NOT NULL CHECK (jsonb_typeof(fill_ids) = 'array'), "
        "evaluated_at TIMESTAMPTZ NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'), "
        "UNIQUE(intent_id, policy_version))"
    )
    op.execute(
        "CREATE INDEX paper_execution_quality_evaluated_idx "
        "ON paper_execution_quality_evidence(evaluated_at, evidence_id)"
    )
    op.execute(
        "CREATE TABLE paper_shadow_rehearsal_evidence ("
        "evidence_id UUID PRIMARY KEY, "
        "campaign_reference TEXT NOT NULL CHECK (btrim(campaign_reference) <> ''), "
        "primary_intent_id UUID NOT NULL REFERENCES paper_order_intents(intent_id), "
        "shadow_intent_id UUID NOT NULL REFERENCES paper_order_intents(intent_id), "
        "evidence_class TEXT NOT NULL CHECK "
        "(evidence_class = 'SIMULATED_PAPER_REFERENCE'), "
        "matched BOOLEAN NOT NULL, "
        "differences JSONB NOT NULL CHECK (jsonb_typeof(differences) = 'array'), "
        "requires_incident BOOLEAN NOT NULL, "
        "limitations JSONB NOT NULL CHECK (jsonb_typeof(limitations) = 'array'), "
        "compared_at TIMESTAMPTZ NOT NULL, "
        "content_hash CHAR(64) NOT NULL UNIQUE CHECK "
        "(content_hash ~ '^[0-9a-f]{64}$'), "
        "CHECK (primary_intent_id <> shadow_intent_id), "
        "CHECK (matched = NOT requires_incident), "
        "UNIQUE(campaign_reference, primary_intent_id, shadow_intent_id))"
    )
    op.execute(
        "CREATE INDEX paper_shadow_rehearsal_campaign_idx "
        "ON paper_shadow_rehearsal_evidence(campaign_reference, compared_at, evidence_id)"
    )
    for table in (
        "paper_execution_quality_evidence",
        "paper_shadow_rehearsal_evidence",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_shadow_rehearsal_evidence")
    op.execute("DROP TABLE IF EXISTS paper_execution_quality_evidence")
