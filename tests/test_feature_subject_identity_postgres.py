"""Real PostgreSQL evidence for Module 3J.0 generalized Feature Authority subject identity.

Fixture instruments/series use the ``TESTFIXTURE:3J0:`` prefix so they cannot
displace real records. Every price, definition and identifier here is a
FIXTURE; nothing here was retrieved from or verified against any exchange.
"""

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FeatureSubjectIdentityPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_generalized_subject_identity_end_to_end(self) -> None:
        from trade_platform.domain import AssetClass
        from trade_platform.feature_authority import (
            FeatureAuthorityError,
            FeatureDefinitionVersion,
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

        dsn = os.environ["POSTGRES_TEST_DSN"]
        database = PostgresDatabase(dsn)
        master = PostgresProfessionalInstrumentMaster(database)
        contracts = PostgresFuturesContractAuthority(database)
        authority = PostgresFeatureAuthority(database)

        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        t0 = datetime(2026, 1, 5, 12, tzinfo=UTC)

        def register_instrument(instrument_id: str, symbol: str) -> None:
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=AssetClass.EQUITY,
                    instrument_type=InstrumentType.COMMON_STOCK, exchange_name="NASDAQ",
                    venue="XNAS", mic="XNAS", canonical_symbol=symbol,
                    listing_date=date(2023, 1, 3), base_currency="USD", quote_currency="USD",
                    settlement_currency="USD", contract_multiplier=Decimal(1),
                    contract_size=Decimal(1), tick_size=Decimal("0.01"), lot_size=Decimal(1),
                    price_precision=2, quantity_precision=0, trading_timezone="America/New_York",
                    market_session_type=SessionType.US_EQUITY,
                    representation_kind=RepresentationKind.DIRECT, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                )
            )

        def register_series(series_id: str, root: str) -> None:
            contracts.register_series(
                FuturesContractSeries(
                    series_id=series_id, root_symbol=root, exchange_name="COMEX", venue="XCEC",
                    mic="XCEC", asset_class=AssetClass.COMMODITY, underlying_reference="Gold",
                    currency="USD", contract_multiplier=Decimal(100), unit_of_measure="TROY_OUNCE",
                    tick_size=Decimal("0.10"), tick_value=Decimal("10.00"), price_precision=2,
                    quantity_precision=0, settlement_type=SettlementType.CASH_SETTLED,
                    trading_timezone="America/New_York", session_type=SessionType.FUTURES_23X5,
                    registered_at=registered_at, source_reference="fixture:series",
                )
            )

        instrument_id = "TESTFIXTURE:3J0:INSTR"
        series_id = "TESTFIXTURE:3J0:SERIES:GC"
        other_series_id = "TESTFIXTURE:3J0:SERIES:CL"
        shared_id = "TESTFIXTURE:3J0:SHARED"

        register_instrument(instrument_id, "TESTFIX3J0")
        register_series(series_id, "TESTFIX3J0GC")
        register_series(other_series_id, "TESTFIX3J0CL")
        register_instrument(shared_id, "TESTFIX3J0SHARED")
        register_series(shared_id, "TESTFIX3J0SHAREDROOT")

        definition = FeatureDefinitionVersion(
            "testfixture_3j0_simple_return", FeatureFamily.PRICE_RETURNS, "1.0.0", "quant",
            "Fixture feature for 3J.0 subject-identity evidence.", ("OHLCV",),
            ("close", "event_at", "knowledge_at"), "1d",
            "event/effective/knowledge bounded", 1, {}, "reject", "reject",
            "reject_future_knowledge", None, None, "decimal", "transparent-market-v1",
            registered_at,
        )
        authority.register(definition)

        # ---- 1-3: existing V1 instrument path is untouched ---------------------
        v1_original = FeatureMaterialization.create(
            feature_id=definition.feature_id, instrument_id="fixture:UNREGISTERED_LEGACY",
            dataset_version="fixture-v1", event_at=t0, effective_at=t0, knowledge_at=t0,
            computed_at=t0, source_observation_manifest=("raw:legacy",), value=Decimal("0.01"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        # A V1 write for an instrument never registered anywhere must still
        # succeed exactly as it always has -- the legacy path was never
        # specified to check subject existence, and this is the same fixture
        # pattern the pre-existing FeatureAuthorityPostgresTests suite depends
        # on ("fixture:SPY").
        authority.materialize(v1_original)
        self.assertEqual(
            authority.latest_as_of(
                definition.feature_id, "fixture:UNREGISTERED_LEGACY", "fixture-v1", t0
            ),
            (v1_original,),
        )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT subject_type,subject_id,hash_version,content_hash FROM "
                "feature_materializations WHERE materialization_id=%s",
                (v1_original.materialization_id,),
            )
            stored_type, stored_subject, stored_hash_version, stored_hash = cursor.fetchone()
        self.assertEqual(str(stored_type), "INSTRUMENT")
        self.assertEqual(str(stored_subject), "fixture:UNREGISTERED_LEGACY")
        self.assertEqual(str(stored_hash_version), "V1")
        # The stored hash is bit-identical to the pre-3J.0 formula: it was
        # never recomputed by the migration or by materialize().
        self.assertEqual(str(stored_hash).strip(), v1_original.content_hash)

        # ---- 4: a valid FUTURES_SERIES subject materializes -------------------
        series_value = FeatureMaterializationV2.create(
            feature_id=definition.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=series_id, dataset_version="fixture-v1", event_at=t0, effective_at=t0,
            knowledge_at=t0, computed_at=t0, source_observation_manifest=("curve:fixture",),
            value=Decimal("0.02"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        authority.materialize_subject(series_value)
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                "fixture-v1", t0,
            ),
            (series_value,),
        )

        # ---- 5: unknown futures series is rejected -----------------------------
        with self.assertRaises(FeatureAuthorityError):
            authority.materialize_subject(
                FeatureMaterializationV2.create(
                    feature_id=definition.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
                    subject_id="TESTFIXTURE:3J0:SERIES:UNKNOWN", dataset_version="fixture-v1",
                    event_at=t0, effective_at=t0, knowledge_at=t0, computed_at=t0,
                    source_observation_manifest=("curve:fixture",), value=Decimal("0.03"),
                    quality_status=FeatureQualityStatus.VALIDATED,
                )
            )

        # ---- 6: unknown instrument is rejected ---------------------------------
        with self.assertRaises(FeatureAuthorityError):
            authority.materialize_subject(
                FeatureMaterializationV2.create(
                    feature_id=definition.feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
                    subject_id="TESTFIXTURE:3J0:UNKNOWN_INSTRUMENT", dataset_version="fixture-v1",
                    event_at=t0, effective_at=t0, knowledge_at=t0, computed_at=t0,
                    source_observation_manifest=("bar:fixture",), value=Decimal("0.04"),
                    quality_status=FeatureQualityStatus.VALIDATED,
                )
            )

        # ---- 7: unsupported subject type is rejected ---------------------------
        with self.assertRaises(ValueError):
            FeatureSubjectType("ACCOUNT")
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO feature_materializations VALUES "
                "(%s,%s,NULL,'fixture-v1',%s,%s,%s,%s,'[\"x\"]'::jsonb,1,'VALIDATED',%s,"
                "'ACCOUNT','v2raw','V2')",
                (uuid4(), definition.feature_id, t0, t0, t0, t0, "0" * 64),
            )

        # ---- 8: a series ID placed into legacy instrument identity is rejected -
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO feature_materializations VALUES "
                "(%s,%s,%s,'fixture-v1',%s,%s,%s,%s,'[\"x\"]'::jsonb,1,'VALIDATED',%s,"
                "'FUTURES_SERIES',%s,'V2')",
                (uuid4(), definition.feature_id, series_id, t0, t0, t0, t0, "1" * 64, series_id),
            )

        # ---- 9: INSTRUMENT subject / legacy instrument_id disagreement rejected
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO feature_materializations VALUES "
                "(%s,%s,'TESTFIXTURE:3J0:WRONG','fixture-v1',%s,%s,%s,%s,'[\"x\"]'::jsonb,1,"
                "'VALIDATED',%s,'INSTRUMENT',%s,'V2')",
                (uuid4(), definition.feature_id, t0, t0, t0, t0, "2" * 64, instrument_id),
            )

        # ---- 10: two subject types sharing textual subject_id do not collide --
        instrument_shared = FeatureMaterializationV2.create(
            feature_id=definition.feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=shared_id, dataset_version="fixture-shared", event_at=t0, effective_at=t0,
            knowledge_at=t0, computed_at=t0, source_observation_manifest=("shared:instrument",),
            value=Decimal("1.00"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        series_shared = FeatureMaterializationV2.create(
            feature_id=definition.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=shared_id, dataset_version="fixture-shared", event_at=t0, effective_at=t0,
            knowledge_at=t0, computed_at=t0, source_observation_manifest=("shared:series",),
            value=Decimal("2.00"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        authority.materialize_subject(instrument_shared)
        authority.materialize_subject(series_shared)
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.INSTRUMENT, shared_id,
                "fixture-shared", t0,
            ),
            (instrument_shared,),
        )
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, shared_id,
                "fixture-shared", t0,
            ),
            (series_shared,),
        )
        # ---- 14/15: subject_type/subject_id both drive V2 identity ------------
        self.assertNotEqual(instrument_shared.content_hash, series_shared.content_hash)

        # ---- 11: two futures series never collide with instrument_id NULL -----
        other_series_value = FeatureMaterializationV2.create(
            feature_id=definition.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=other_series_id, dataset_version="fixture-v1", event_at=t0, effective_at=t0,
            knowledge_at=t0, computed_at=t0, source_observation_manifest=("curve:fixture",),
            value=Decimal("0.05"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        authority.materialize_subject(other_series_value)
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                "fixture-v1", t0,
            ),
            (series_value,),
        )
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, other_series_id,
                "fixture-v1", t0,
            ),
            (other_series_value,),
        )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM feature_materializations WHERE instrument_id IS NULL "
                "AND feature_id=%s AND dataset_version='fixture-v1'",
                (definition.feature_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 2)

        # ---- 12: duplicate identical V2 materialization is idempotent ---------
        authority.materialize_subject(series_value)  # replay, not a new row
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM feature_materializations WHERE feature_id=%s "
                "AND subject_type='FUTURES_SERIES' AND subject_id=%s AND dataset_version='fixture-v1' "
                "AND event_at=%s",
                (definition.feature_id, series_id, t0),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 1)

        # ---- 13: same natural identity, different hash -> fails closed --------
        from dataclasses import replace

        conflicting = replace(series_value, value=Decimal("99.99"), content_hash="f" * 64)
        with self.assertRaisesRegex(FeatureAuthorityError, "conflict"):
            authority.materialize_subject(conflicting)

        # ---- 16: a future-known materialization cannot leak into an earlier PIT
        early_series_run = FeatureMaterializationV2.create(
            feature_id=definition.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=series_id, dataset_version="fixture-pit", event_at=t0, effective_at=t0,
            knowledge_at=t0, computed_at=t0, source_observation_manifest=("curve:v1",),
            value=Decimal("10.00"), quality_status=FeatureQualityStatus.VALIDATED,
        )
        later_knowledge = t0 + timedelta(days=1)
        later_series_run = FeatureMaterializationV2.create(
            feature_id=definition.feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=series_id, dataset_version="fixture-pit", event_at=t0, effective_at=t0,
            knowledge_at=later_knowledge, computed_at=later_knowledge,
            source_observation_manifest=("curve:v2-revised",), value=Decimal("11.00"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        authority.materialize_subject(early_series_run)
        authority.materialize_subject(later_series_run)
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                "fixture-pit", t0 + timedelta(hours=1),
            ),
            (early_series_run,),
        )
        self.assertEqual(
            authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                "fixture-pit", later_knowledge,
            ),
            (later_series_run,),
        )

        # ---- 17: a direct SQL invalid subject identity fails at COMMIT --------
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO feature_materializations VALUES "
                "(%s,%s,NULL,'fixture-v1',%s,%s,%s,%s,'[\"x\"]'::jsonb,1,'VALIDATED',%s,"
                "'INSTRUMENT',%s,'V2')",
                (
                    uuid4(), definition.feature_id, t0, t0, t0, t0, "3" * 64,
                    "TESTFIXTURE:3J0:GHOST_INSTRUMENT",
                ),
            )

        # ---- 18: immutable evidence still rejects UPDATE/DELETE ---------------
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE feature_materializations SET value=0 WHERE materialization_id=%s",
                (series_value.materialization_id,),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM feature_materializations WHERE materialization_id=%s",
                (series_value.materialization_id,),
            )

        # ---- 19: restore/restart preserves generalized subject identity -------
        database.close()
        reopened = PostgresDatabase(dsn)
        reopened_authority = PostgresFeatureAuthority(reopened)
        self.assertEqual(
            reopened_authority.latest_as_of_subject(
                definition.feature_id, FeatureSubjectType.FUTURES_SERIES, series_id,
                "fixture-v1", t0,
            ),
            (series_value,),
        )
        self.assertEqual(
            reopened_authority.latest_as_of(
                definition.feature_id, "fixture:UNREGISTERED_LEGACY", "fixture-v1", t0
            ),
            (v1_original,),
        )

        # ---- 20: no second feature-materialization authority exists -----------
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND table_name LIKE %s",
                ("%feature_material%",),
            )
            tables = [str(row[0]) for row in cursor.fetchall()]
        self.assertEqual(tables, ["feature_materializations"])
        reopened.close()
