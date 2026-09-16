"""add canonical typed OHLCV volume/turnover semantics sidecar

Revision ID: 20260916_0048
Revises: 20260909_0047
Create Date: 2026-09-16

Module 3B.2. Extends the EXISTING historical market-data pipeline once more --
no new source registry, dataset registry or observation envelope is created.

One thing happens here: a single typed sidecar table,
``historical_ohlcv_volume_semantics``, bound 1:1 to a normalized OHLCV
observation, records the resolved canonical meaning of that bar's volume and
turnover (unit, asset, the provider-published turnover figure, and the explicit
semantic version and source identity it was authorized under).

Unlike the Module 3I.1/3I.2 typed payload kinds, this sidecar is **optional**.
OHLCV is not a typed-payload kind: its financial values live in
``normalized_value`` exactly as before, and a normalized OHLCV row may exist
with no sidecar at all -- that is legacy/unitless evidence, which stays valid.
So ``require_typed_payload_for_kind`` is deliberately NOT touched: nothing here
makes an OHLCV envelope require a sidecar to reach COMMIT. What is enforced is
the converse: a sidecar row may only attach to an OHLCV envelope
(``require_matching_observation_kind('OHLCV')``, the set-valued trigger function
introduced in ``20260907_0044``), and the row is immutable once written
(``prevent_immutable_mutation``).

The sealed dataset content hash binds this sidecar only for the NEW rows that
carry it; a legacy OHLCV row without a sidecar contributes nothing new to the
hash, so every existing sealed dataset reproduces bit-identically.
"""

from alembic import op

from trade_platform.postgres_schema import immutable_trigger_sql

revision = "20260916_0048"
down_revision = "20260909_0047"
branch_labels = None
depends_on = None


TABLE = "historical_ohlcv_volume_semantics"

_VOLUME_UNITS = "'CONTRACTS','BASE_ASSET','QUOTE_ASSET'"


def upgrade() -> None:
    statements = (
        f"""CREATE TABLE {TABLE} (
            normalized_observation_id UUID PRIMARY KEY
                REFERENCES historical_normalized_observations(normalized_observation_id),
            volume_unit TEXT NOT NULL CHECK(volume_unit IN ({_VOLUME_UNITS})),
            volume_asset VARCHAR(12) NOT NULL CHECK(LENGTH(volume_asset) BETWEEN 2 AND 12),
            turnover NUMERIC(38,18) NOT NULL CHECK(turnover >= 0),
            turnover_unit TEXT NOT NULL CHECK(turnover_unit IN ({_VOLUME_UNITS})),
            turnover_asset VARCHAR(12) NOT NULL CHECK(LENGTH(turnover_asset) BETWEEN 2 AND 12),
            semantic_version TEXT NOT NULL CHECK(LENGTH(TRIM(semantic_version)) > 0),
            source_reference TEXT NOT NULL CHECK(LENGTH(TRIM(source_reference)) > 0)
        )""",
        # A sidecar row may only describe an OHLCV envelope. The set-valued
        # kind-match function (20260907_0044) is reused with a single permitted
        # kind, so a volume-semantics row can never attach to a mark price,
        # settlement, funding or open-interest envelope.
        f"""CREATE CONSTRAINT TRIGGER {TABLE}_kind_match
            AFTER INSERT ON {TABLE}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION require_matching_observation_kind('OHLCV')""",
        immutable_trigger_sql(TABLE),
        (
            f"COMMENT ON TABLE {TABLE} IS "
            "'Optional canonical typed authority for OHLCV volume/turnover semantics "
            "(Module 3B.2), bound 1:1 to a normalized OHLCV observation. Absent for "
            "legacy/unitless OHLCV rows, which stay valid; present only for OHLCV "
            "authorized under a volume-semantics rule. Its canonical tuple is folded "
            "into the sealed dataset content hash of the NEW rows that carry it.'"
        ),
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_immutable ON {TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_kind_match ON {TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
