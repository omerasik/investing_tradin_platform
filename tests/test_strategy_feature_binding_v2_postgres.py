"""Real PostgreSQL evidence for Module 3J.2a (Subject-Aware Strategy Lab
Feature Binding V2). Proves the module against the actual
``PostgresFeatureAuthority`` boundary -- not merely the fake in-memory reader
used by ``tests/test_strategy_feature_binding_v2.py``.

Fixture instruments/series use the ``TESTFIXTURE:3J2A:`` prefix so they can
never collide with real records on the shared CI database. Every value here
is a FIXTURE.
"""

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class StrategyFeatureBindingV2PostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_v2_binding_resolves_real_instrument_and_futures_series_evidence(self) -> None:
        from trade_platform.domain import AssetClass
        from trade_platform.feature_authority import (
            FeatureFamily,
            FeatureMaterialization,
            FeatureMaterializationV2,
            FeatureQualityStatus,
            FeatureSubjectType,
            PostgresFeatureAuthority,
        )
        from trade_platform.futures_contracts import (
            FuturesContractSeries,
            PostgresFuturesContractAuthority,
            SettlementType,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
        )
        from trade_platform.strategy_feature_binding_v2 import (
            ResearchFeatureBundleRequestV2,
            ResearchFeatureBundleStatus,
            ResearchFeatureRequirementV2,
            ResearchQualityPolicyV2,
            align_exact_event_feature_matrix,
            build_research_feature_bundle,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)
        master = PostgresProfessionalInstrumentMaster(database)
        contracts = PostgresFuturesContractAuthority(database)
        feature_authority = PostgresFeatureAuthority(database)

        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        instrument_id = "TESTFIXTURE:3J2A:EQ"
        series_id = "TESTFIXTURE:3J2A:SERIES:GC"

        master.register(
            ProfessionalInstrument(
                instrument_id=instrument_id, asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.COMMON_STOCK, exchange_name="NASDAQ", venue="XNAS",
                mic="XNAS", canonical_symbol="TESTFIX3J2AEQ", listing_date=date(2023, 1, 3),
                base_currency="USD", quote_currency="USD", settlement_currency="USD",
                contract_multiplier=Decimal(1), contract_size=Decimal(1), tick_size=Decimal("0.01"),
                lot_size=Decimal(1), price_precision=2, quantity_precision=0,
                trading_timezone="America/New_York", market_session_type=SessionType.US_EQUITY,
                representation_kind=RepresentationKind.DIRECT, registered_at=registered_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        contracts.register_series(
            FuturesContractSeries(
                series_id=series_id, root_symbol="TESTFIX3J2AGC", exchange_name="COMEX", venue="XCEC",
                mic="XCEC", asset_class=AssetClass.COMMODITY, underlying_reference="Gold", currency="USD",
                contract_multiplier=Decimal(100), unit_of_measure="TROY_OUNCE", tick_size=Decimal("0.10"),
                tick_value=Decimal("10.00"), price_precision=2, quantity_precision=0,
                settlement_type=SettlementType.CASH_SETTLED, trading_timezone="America/New_York",
                session_type=SessionType.FUTURES_23X5, registered_at=registered_at,
                source_reference="fixture:series",
            )
        )

        momentum_def, spread_def = (
            _definition("testfixture_3j2a_momentum", registered_at),
            _definition("testfixture_3j2a_spread", registered_at),
        )
        feature_authority.register(momentum_def)
        feature_authority.register(spread_def)

        dataset_version_id = uuid4()
        t0 = datetime(2025, 3, 20, 19, tzinfo=UTC)

        # ---- invariant: valid V2 INSTRUMENT feature series resolves -------
        momentum_value = FeatureMaterializationV2.create(
            feature_id=momentum_def.feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset_version_id), event_at=t0,
            effective_at=t0, knowledge_at=t0, computed_at=t0,
            source_observation_manifest=("fixture:momentum",), value=Decimal("0.05"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        feature_authority.materialize_subject(momentum_value)

        instrument_requirement = ResearchFeatureRequirementV2(
            momentum_def.feature_id, momentum_def.name, momentum_def.semantic_version,
            FeatureSubjectType.INSTRUMENT,
        )
        instrument_request = ResearchFeatureBundleRequestV2(
            FeatureSubjectType.INSTRUMENT, instrument_id, dataset_version_id, t0 + timedelta(hours=1),
            (instrument_requirement,), ResearchQualityPolicyV2.VALIDATED_ONLY,
        )
        instrument_outcome = build_research_feature_bundle(feature_authority, instrument_request)
        self.assertEqual(instrument_outcome.status, ResearchFeatureBundleStatus.AVAILABLE)
        assert instrument_outcome.bundle is not None
        self.assertEqual(instrument_outcome.bundle.feature_series[0].materializations, (momentum_value,))

        matrix = align_exact_event_feature_matrix(instrument_outcome.bundle, (t0,))
        self.assertEqual(matrix.values[momentum_def.name], (Decimal("0.05"),))

        # ---- invariant: valid V2 FUTURES_SERIES feature series resolves,
        # and is never expressed through instrument_id --------------------
        spread_value = FeatureMaterializationV2.create(
            feature_id=spread_def.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=series_id, dataset_version=str(dataset_version_id), event_at=t0,
            effective_at=t0, knowledge_at=t0, computed_at=t0,
            source_observation_manifest=("fixture:spread",), value=Decimal("0.03"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        feature_authority.materialize_subject(spread_value)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT instrument_id FROM feature_materializations WHERE materialization_id=%s",
                (spread_value.materialization_id,),
            )
            self.assertIsNone(cursor.fetchone()[0])

        series_requirement = ResearchFeatureRequirementV2(
            spread_def.feature_id, spread_def.name, spread_def.semantic_version,
            FeatureSubjectType.FUTURES_SERIES,
        )
        series_request = ResearchFeatureBundleRequestV2(
            FeatureSubjectType.FUTURES_SERIES, series_id, dataset_version_id, t0 + timedelta(hours=1),
            (series_requirement,), ResearchQualityPolicyV2.VALIDATED_ONLY,
        )
        series_outcome = build_research_feature_bundle(feature_authority, series_request)
        self.assertEqual(series_outcome.status, ResearchFeatureBundleStatus.AVAILABLE)
        assert series_outcome.bundle is not None
        self.assertEqual(series_outcome.bundle.subject_type, FeatureSubjectType.FUTURES_SERIES)

        # ---- invariant: unknown subject rejected through the existing
        # authority -- nothing was ever materialized against it, because the
        # write-side subject-existence trigger never let such a row exist. --
        unknown_request = ResearchFeatureBundleRequestV2(
            FeatureSubjectType.INSTRUMENT, "TESTFIXTURE:3J2A:NEVER_REGISTERED", dataset_version_id,
            t0 + timedelta(hours=1), (instrument_requirement,), ResearchQualityPolicyV2.VALIDATED_ONLY,
        )
        unknown_outcome = build_research_feature_bundle(feature_authority, unknown_request)
        self.assertEqual(unknown_outcome.status, ResearchFeatureBundleStatus.UNAVAILABLE)

        # ---- invariant: a pre-existing V1 hash is completely untouched ----
        v1_value = FeatureMaterialization.create(
            feature_id=momentum_def.feature_id, instrument_id="fixture:UNREGISTERED_3J2A_LEGACY",
            dataset_version="fixture-v1", event_at=t0, effective_at=t0, knowledge_at=t0, computed_at=t0,
            source_observation_manifest=("raw:legacy",), value=Decimal("0.01"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        feature_authority.materialize(v1_value)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash, hash_version FROM feature_materializations WHERE materialization_id=%s",
                (v1_value.materialization_id,),
            )
            stored_hash, stored_hash_version = cursor.fetchone()
        self.assertEqual(str(stored_hash).strip(), v1_value.content_hash)
        self.assertEqual(str(stored_hash_version), "V1")

        # ---- invariant: no new Feature Authority / store / strategy /
        # signal / order / risk authority exists -- this module reads only. -
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND (table_name LIKE %s OR table_name LIKE %s OR table_name LIKE %s)",
                ("%3j2a%", "%feature_binding%", "%research_feature_bundle%"),
            )
            self.assertEqual(cursor.fetchall(), [])

        self.assertEqual(FeatureFamily.DERIVATIVES.value, "DERIVATIVES")
        database.close()


def _definition(name: str, created_at: datetime):
    from trade_platform.feature_authority import FeatureDefinitionVersion, FeatureFamily

    return FeatureDefinitionVersion(
        name, FeatureFamily.DERIVATIVES, "1.0.0", "quant", "3J.2a Postgres fixture feature.",
        ("FIXTURE_DATASET",), ("value",), "1d", "event/effective/knowledge bounded", 0, {},
        "reject", "reject", "reject_future_knowledge", None, None, "dimensionless", "3j2a-fixture-v1",
        created_at,
    )


if __name__ == "__main__":
    unittest.main()
