"""Real PostgreSQL evidence for Module 3I.1 settlement / open-interest authority.

Fixture instruments live under ``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX``
so they cannot displace real instruments on the shared CI database's operator
discovery page.

Every price, quantity, contract date and provider identifier is a FIXTURE.
Nothing here was retrieved from or verified against any exchange; no real
settlement price, open-interest figure or contract specification is claimed.
"""

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

SETTLEMENT = "SETTLEMENT_PRICE"
OPEN_INTEREST = "OPEN_INTEREST"


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FuturesMarketObservationPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_settlement_and_open_interest_authority_end_to_end(self) -> None:
        from trade_platform.domain import AssetClass
        from trade_platform.futures_contracts import (
            FuturesContractSeries,
            FuturesContractSpecification,
            PostgresFuturesContractAuthority,
            SettlementType,
        )
        from trade_platform.futures_market_observations import SettlementFinality
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            HistoricalDataAuthorizationError,
            HistoricalDataQualityError,
            HistoricalDataResolutionError,
            HistoricalMarketDataError,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
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
        pipeline = PostgresHistoricalMarketDataPipeline(database)

        registered_at = datetime(2024, 1, 2, tzinfo=UTC)
        namespace = "TESTFIX_SETTLE_PROVIDER"
        venue = "XCEC"

        def register_instrument(
            suffix: str, symbol: str, *, instrument_type: InstrumentType,
            asset_class: AssetClass = AssetClass.COMMODITY,
            representation: RepresentationKind = RepresentationKind.FUTURE,
            continuous_parent: str | None = None, instrument_venue: str = venue,
        ) -> str:
            instrument_id = f"TESTFIXTURE:SETTLE:{suffix}"
            extra: dict[str, object] = {}
            if instrument_type is InstrumentType.FUTURE:
                extra = {
                    "contract_code": symbol,
                    "expiration_date": date(2025, 6, 26),
                    "first_notice_date": date(2025, 6, 25),
                    "last_trade_date": date(2025, 6, 26),
                    "roll_rule": "TESTFIX_ROLL_V1",
                    "continuous_parent_id": continuous_parent,
                }
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=asset_class,
                    instrument_type=instrument_type, exchange_name="COMEX",
                    venue=instrument_venue, mic=instrument_venue, canonical_symbol=symbol,
                    listing_date=date(2023, 1, 3), base_currency="USD", quote_currency="USD",
                    settlement_currency="USD", contract_multiplier=Decimal(100),
                    contract_size=Decimal(100), tick_size=Decimal("0.10"),
                    lot_size=Decimal(1), price_precision=2, quantity_precision=0,
                    trading_timezone="America/New_York",
                    market_session_type=SessionType.FUTURES_23X5,
                    representation_kind=representation, registered_at=registered_at,
                    lifecycle_status=LifecycleStatus.ACTIVE, **extra,  # type: ignore[arg-type]
                )
            )
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=namespace, value=suffix, valid_from=registered_at,
                    valid_until=None, ingested_at=registered_at,
                    source_reference="fixture:provider-identifier",
                )
            )
            return instrument_id

        contract_id = register_instrument(
            "GC062025", "TESTFIXSETGC", instrument_type=InstrumentType.FUTURE
        )
        equity_id = register_instrument(
            "EQUITY", "TESTFIXSETEQ", instrument_type=InstrumentType.COMMON_STOCK,
            asset_class=AssetClass.EQUITY, representation=RepresentationKind.DIRECT,
            instrument_venue="XNAS",
        )
        continuous_id = register_instrument(
            "GCCONTINUOUS", "TESTFIXSETGCC", instrument_type=InstrumentType.FUTURE,
            continuous_parent="TESTFIXTURE:SETTLE:GC062025",
        )
        unspecified_id = register_instrument(
            "GC092025", "TESTFIXSETGC9", instrument_type=InstrumentType.FUTURE
        )

        # Module 3H.1 contract specification: settlement/OI must resolve to a
        # real contract, so the instrument needs one.
        series = FuturesContractSeries(
            series_id="TESTFIXTURE:SETTLE:SERIES:GC", root_symbol="TESTFIXSETGC",
            exchange_name="COMEX", venue=venue, mic=venue,
            asset_class=AssetClass.COMMODITY, underlying_reference="Gold", currency="USD",
            contract_multiplier=Decimal(100), unit_of_measure="TROY_OUNCE",
            tick_size=Decimal("0.10"), tick_value=Decimal("10.00"), price_precision=2,
            quantity_precision=0, settlement_type=SettlementType.PHYSICAL_DELIVERY,
            trading_timezone="America/New_York", session_type=SessionType.FUTURES_23X5,
            registered_at=registered_at, source_reference="fixture:series",
        )
        contracts.register_series(series)
        contracts.specify_contract(
            FuturesContractSpecification(
                instrument_id=contract_id, series_id=series.series_id,
                contract_code="TESTFIXSETGC", contract_year=2025, contract_month=6,
                first_trade_date=date(2023, 6, 1), first_notice_date=date(2025, 6, 25),
                last_trade_date=date(2025, 6, 26), expiration_date=date(2025, 6, 26),
                settlement_date=date(2025, 6, 28),
                settlement_type=SettlementType.PHYSICAL_DELIVERY,
                contract_multiplier=Decimal(100), tick_size=Decimal("0.10"),
                tick_value=Decimal("10.00"), registered_at=registered_at,
                source_reference="fixture:contract",
            )
        )

        # ---- source authorization -------------------------------------------
        def build_source(
            name: str, scope: AssetScope, kinds: frozenset[ObservationKind] | None
        ) -> AuthorizedHistoricalSource:
            return AuthorizedHistoricalSource(
                provider="TESTFIX_SETTLE", dataset_name=name,
                provider_identifier_namespace=namespace,
                provider_terms_version=f"{name}-v1",
                authorization_reference=f"fixture://authorization/{name}",
                authorized_at=registered_at, created_at=registered_at,
                asset_scope=scope.value, authorized_observation_kinds=kinds,
            )

        # Invariant 9: an unknown asset scope is refused outright.
        with self.assertRaises(HistoricalDataAuthorizationError) as raised:
            build_source("bad-scope", AssetScope.FUTURES, None).validate()
        self.assertIn("asset_scope_requires_explicit_observation_kinds", str(raised.exception))
        with self.assertRaises(HistoricalDataAuthorizationError) as raised:
            AuthorizedHistoricalSource(
                provider="TESTFIX_SETTLE", dataset_name="unknown-scope",
                provider_identifier_namespace=namespace, provider_terms_version="v1",
                authorization_reference="fixture://authorization/unknown",
                authorized_at=registered_at, created_at=registered_at,
                asset_scope="CRYPTO_PERPETUALS",
            ).validate()
        self.assertIn("unsupported_asset_scope", str(raised.exception))

        # A FUTURES source cannot claim an equity corporate-action kind.
        with self.assertRaises(HistoricalDataAuthorizationError) as raised:
            build_source(
                "wrong-kind", AssetScope.FUTURES, frozenset({ObservationKind.DIVIDEND})
            ).validate()
        self.assertIn("observation_kind_outside_asset_scope", str(raised.exception))

        ohlcv_only = build_source(
            "ohlcv-only", AssetScope.FUTURES, frozenset({ObservationKind.OHLCV})
        )
        full = build_source(
            "settlement-and-oi", AssetScope.FUTURES,
            frozenset({ObservationKind.SETTLEMENT_PRICE, ObservationKind.OPEN_INTEREST}),
        )
        pipeline.register_source(ohlcv_only)
        pipeline.register_source(full)

        def raw(
            source_id: object, kind: ObservationKind, payload: dict[str, object], *,
            revision: int = 0, identifier: str = "GC062025",
            event_at: datetime = datetime(2025, 6, 20, 18, tzinfo=UTC),
            ingested_at: datetime | None = None,
            adjustment: AdjustmentStatus = AdjustmentStatus.AS_REPORTED,
        ) -> RawHistoricalObservation:
            return RawHistoricalObservation(
                source_id=source_id,  # type: ignore[arg-type]
                observation_kind=kind, provider_identifier=identifier,
                provider_symbol="TESTFIXSETGC", exchange=venue, event_at=event_at,
                effective_at=event_at, ingested_at=ingested_at or event_at,
                adjustment_status=adjustment, revision=revision,
                provenance_uri=f"fixture://{kind.value}/{identifier}/{revision}",
                raw_payload=payload,
            )

        def settlement(**overrides: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "settlement_price": "2350.40", "price_currency": "USD",
                "settlement_date": "2025-06-20",
                "settlement_effective_at": "2025-06-20T18:00:00+00:00",
                "finality": "PRELIMINARY", "quote_unit": "USD_PER_TROY_OUNCE",
            }
            payload.update(overrides)
            return payload

        def open_interest(**overrides: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "open_interest": "412500", "unit": "CONTRACTS",
                "observed_at": "2025-06-20T18:00:00+00:00",
            }
            payload.update(overrides)
            return payload

        # Invariant 10: OHLCV authority does not grant settlement authority.
        # Enforced by the database's composite capability foreign key.
        with self.assertRaises(HistoricalMarketDataError) as raised:
            pipeline.capture_raw(
                [raw(ohlcv_only.source_id, ObservationKind.SETTLEMENT_PRICE, settlement())]
            )
        self.assertIn("raw_historical_capture_failed", str(raised.exception))

        # Invariant 18: equity adjustment semantics are undefined for these kinds.
        for status in (AdjustmentStatus.LATEST_ADJUSTED, AdjustmentStatus.POINT_IN_TIME_ADJUSTED):
            with self.assertRaises(HistoricalMarketDataError) as raised:
                raw(
                    full.source_id, ObservationKind.SETTLEMENT_PRICE, settlement(),
                    adjustment=status,
                ).validate()
            self.assertIn("adjustment_status_undefined_for_kind", str(raised.exception))

        # ---- happy path: preliminary settlement, then a later final ----------
        preliminary_at = datetime(2025, 6, 20, 19, tzinfo=UTC)
        final_at = datetime(2025, 6, 21, 12, tzinfo=UTC)
        (preliminary_raw,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE, settlement(),
                 ingested_at=preliminary_at)]
        )
        (final_raw,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE,
                 settlement(settlement_price="2351.10", finality="FINAL"),
                 revision=1, ingested_at=final_at)]
        )
        (oi_raw,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.OPEN_INTEREST, open_interest(),
                 ingested_at=preliminary_at)]
        )

        # Invariant: same provider record replayed is idempotent.
        (replayed,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE, settlement(),
                 ingested_at=preliminary_at)]
        )
        self.assertEqual(replayed, preliminary_raw)

        # Invariant 13: a conflicting payload at the same revision fails closed.
        with self.assertRaises(HistoricalMarketDataError) as raised:
            pipeline.capture_raw(
                [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE,
                     settlement(settlement_price="9999.99"), ingested_at=preliminary_at)]
            )
        self.assertIn("raw_historical_observation_conflict", str(raised.exception))

        preliminary = pipeline.normalize(preliminary_raw, "settle-v1", preliminary_at)
        final = pipeline.normalize(final_raw, "settle-v1", final_at)
        oi = pipeline.normalize(oi_raw, "settle-v1", preliminary_at)

        # The envelope holds only a pointer marker -- never a second copy.
        self.assertEqual(
            preliminary.normalized_value,
            {"canonical_payload_table": "futures_settlement_observations"},
        )
        # Module 3I.2 renamed the physical table to open_interest_observations,
        # but the canonical payload identity written into the envelope is frozen
        # at its 3I.1 value. That is what keeps already-sealed dataset hashes
        # stable across the rename, so this literal must never be "corrected".
        self.assertEqual(
            oi.normalized_value,
            {"canonical_payload_table": "futures_open_interest_observations"},
        )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT settlement_price,finality FROM futures_settlement_observations "
                "WHERE normalized_observation_id=%s",
                (preliminary.normalized_observation_id,),
            )
            stored = cursor.fetchone()
        self.assertEqual(Decimal(str(stored[0])), Decimal("2350.400000000000000000"))
        self.assertEqual(str(stored[1]), SettlementFinality.PRELIMINARY.value)

        # ---- fail-closed instrument eligibility ------------------------------
        for identifier, expected in (
            ("EQUITY", "kind_requires_futures_instrument"),
            ("GCCONTINUOUS", "continuous_series_cannot_carry_contract_observations"),
            ("GC092025", "instrument_has_no_futures_contract_specification"),
            ("NOT_A_REGISTERED_IDENTIFIER", "historical_instrument_resolution_failed"),
        ):
            with self.subTest(identifier):
                (candidate,) = pipeline.capture_raw(
                    [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE, settlement(),
                         identifier=identifier, ingested_at=preliminary_at)]
                )
                with self.assertRaises(HistoricalDataResolutionError) as failure:
                    pipeline.normalize(candidate, "settle-v1", preliminary_at)
                self.assertIn(expected, str(failure.exception))
        self.assertTrue(equity_id and continuous_id and unspecified_id)

        # Invariant 2/3 at the pipeline boundary: a rejected typed payload never
        # produces a half-written envelope.
        (bad_oi,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.OPEN_INTEREST,
                 open_interest(open_interest="-1"), identifier="GC062025",
                 event_at=datetime(2025, 6, 19, 18, tzinfo=UTC), ingested_at=preliminary_at)]
        )
        with self.assertRaises(HistoricalDataQualityError) as raised:
            pipeline.normalize(bad_oi, "settle-v1", preliminary_at)
        self.assertIn("negative_open_interest", str(raised.exception))
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations "
                "WHERE raw_observation_id=%s",
                (bad_oi,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)

        # ---- sealing covers the canonical typed value ------------------------
        # Two dataset versions, because a sealed dataset is a snapshot of what
        # was known when it was sealed: seal_dataset refuses a member ingested
        # after created_at, and research_query hides a dataset created after
        # the requested knowledge time. So the only faithful way to replay the
        # preliminary-only state is the dataset that existed then -- exactly
        # how a real point-in-time research workflow versions its data.
        preliminary_dataset = pipeline.seal_dataset(
            full.source_id, "settle-dataset-preliminary", "settle-v1",
            (preliminary.normalized_observation_id, oi.normalized_observation_id),
            datetime(2025, 6, 20, 20, tzinfo=UTC),
        )
        sealed = pipeline.seal_dataset(
            full.source_id, "settle-dataset-v1", "settle-v1",
            (preliminary.normalized_observation_id, final.normalized_observation_id,
             oi.normalized_observation_id),
            final_at,
        )
        self.assertEqual(len(sealed.content_hash), 64)
        self.assertNotEqual(sealed.content_hash, preliminary_dataset.content_hash)

        # Invariant 19: an identical dataset whose only difference is a typed
        # financial value must hash differently.
        (variant_raw,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE,
                 settlement(settlement_price="2350.41"), identifier="GC062025",
                 event_at=datetime(2025, 6, 18, 18, tzinfo=UTC), ingested_at=preliminary_at)]
        )
        variant = pipeline.normalize(variant_raw, "settle-v1", preliminary_at)
        (twin_raw,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE,
                 settlement(settlement_price="2350.42"), identifier="GC062025",
                 event_at=datetime(2025, 6, 17, 18, tzinfo=UTC), ingested_at=preliminary_at)]
        )
        twin = pipeline.normalize(twin_raw, "settle-v1", preliminary_at)
        first = pipeline.seal_dataset(
            full.source_id, "settle-variant-a", "settle-v1",
            (variant.normalized_observation_id,), final_at,
        )
        second = pipeline.seal_dataset(
            full.source_id, "settle-variant-b", "settle-v1",
            (twin.normalized_observation_id,), final_at,
        )
        self.assertNotEqual(first.content_hash, second.content_hash)

        # Invariant 28: a 3I.1 futures open-interest dataset stays reproducible
        # after Module 3I.2 renamed the physical table to
        # open_interest_observations. The expected digest is recomputed here by
        # an independent re-implementation of the sealing formula with the
        # canonical identity token written out as the 3I.1 literal -- so if that
        # token were ever "corrected" to the new table name, this diverges and
        # every pre-3I.2 sealed dataset would have silently changed identity.
        open_interest_dataset = pipeline.seal_dataset(
            full.source_id, "settle-open-interest-only", "settle-v1",
            (oi.normalized_observation_id,), final_at,
        )
        self.assertEqual(
            open_interest_dataset.content_hash,
            _recomputed_3i1_open_interest_hash(database, (oi.normalized_observation_id,)),
        )

        # ---- point-in-time revision visibility -------------------------------
        window_start = datetime(2025, 6, 20, tzinfo=UTC)
        window_end = datetime(2025, 6, 20, 23, tzinfo=UTC)

        def settlement_at(dataset_version_id: object, knowledge_at: datetime) -> tuple:
            return tuple(
                item for item in pipeline.research_query(
                    dataset_version_id,  # type: ignore[arg-type]
                    contract_id, window_start, window_end, knowledge_at,
                )
                if item.observation_kind is ObservationKind.SETTLEMENT_PRICE
            )

        # Invariant 12: replaying the state that existed before the final
        # arrived must still yield the preliminary value -- the final did not
        # overwrite it.
        before = settlement_at(
            preliminary_dataset.dataset_version_id, datetime(2025, 6, 20, 21, tzinfo=UTC)
        )
        self.assertEqual(len(before), 1)
        # Compared as Decimal: the projection reproduces the canonical row
        # exactly, including the NUMERIC(38,18) scale it is stored at.
        self.assertEqual(
            Decimal(str(before[0].normalized_value["settlement_price"])), Decimal("2350.40")
        )
        self.assertEqual(before[0].normalized_value["finality"], "PRELIMINARY")
        self.assertEqual(before[0].revision, 0)

        after = settlement_at(sealed.dataset_version_id, datetime(2025, 6, 22, tzinfo=UTC))
        self.assertEqual(len(after), 1)
        self.assertEqual(
            Decimal(str(after[0].normalized_value["settlement_price"])), Decimal("2351.10")
        )
        self.assertEqual(after[0].normalized_value["finality"], "FINAL")
        self.assertEqual(after[0].revision, 1)

        # Invariant 11: a dataset sealed after a knowledge time is invisible at
        # it, so a later-known revision cannot leak into an earlier replay.
        self.assertEqual(
            settlement_at(sealed.dataset_version_id, datetime(2025, 6, 20, 21, tzinfo=UTC)),
            (),
        )

        # Both revisions remain independently reconstructable; nothing was
        # overwritten.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT r.revision,s.settlement_price,s.finality "
                "FROM futures_settlement_observations s "
                "JOIN historical_normalized_observations n "
                "  ON n.normalized_observation_id=s.normalized_observation_id "
                "JOIN historical_raw_observations r "
                "  ON r.raw_observation_id=n.raw_observation_id "
                "WHERE r.provider_identifier='GC062025' AND r.event_at=%s "
                "ORDER BY r.revision",
                (datetime(2025, 6, 20, 18, tzinfo=UTC),),
            )
            revisions = cursor.fetchall()
        self.assertEqual(
            [(int(str(row[0])), str(row[2])) for row in revisions],
            [(0, "PRELIMINARY"), (1, "FINAL")],
        )

        # Invariant 20: the projection agrees with the canonical typed row.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT open_interest,unit FROM open_interest_observations "
                "WHERE normalized_observation_id=%s",
                (oi.normalized_observation_id,),
            )
            canonical_oi = cursor.fetchone()
        projected = [
            item for item in pipeline.research_query(
                sealed.dataset_version_id, contract_id, window_start, window_end,
                datetime(2025, 6, 22, tzinfo=UTC),
            )
            if item.observation_kind is ObservationKind.OPEN_INTEREST
        ]
        self.assertEqual(len(projected), 1)
        self.assertEqual(
            Decimal(str(projected[0].normalized_value["open_interest"])),
            Decimal(str(canonical_oi[0])),
        )
        self.assertEqual(projected[0].normalized_value["unit"], str(canonical_oi[1]))

        # ---- database-level integrity, bypassing Python ----------------------
        # Invariant 14: a typed payload cannot exist without its envelope.
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO futures_settlement_observations VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (uuid4(), Decimal("1"), "USD", date(2025, 6, 20),
                 datetime(2025, 6, 20, 18, tzinfo=UTC), "FINAL", "USD_PER_TROY_OUNCE"),
            )
        # ...and cannot attach to an envelope of a different kind.
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO futures_settlement_observations VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (oi.normalized_observation_id, Decimal("1"), "USD", date(2025, 6, 20),
                 datetime(2025, 6, 20, 18, tzinfo=UTC), "FINAL", "USD_PER_TROY_OUNCE"),
            )

        # Invariants 1 and 2 as database CHECKs.
        for label, price in (("zero", Decimal(0)), ("negative", Decimal("-1"))):
            with (
                self.subTest(label),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "INSERT INTO futures_settlement_observations VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (variant.normalized_observation_id, price, "USD", date(2025, 6, 20),
                     datetime(2025, 6, 20, 18, tzinfo=UTC), "FINAL", "USD_PER_TROY_OUNCE"),
                )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO open_interest_observations VALUES (%s,%s,%s,%s,%s)",
                (variant.normalized_observation_id, Decimal("-1"), "CONTRACTS", None,
                 datetime(2025, 6, 20, 18, tzinfo=UTC)),
            )
        # A contract count may not name a unit asset, and an asset-denominated
        # unit must.
        for label, unit, asset in (
            ("contracts_with_asset", "CONTRACTS", "BTC"),
            ("base_asset_without_asset", "BASE_ASSET", None),
        ):
            with (
                self.subTest(label),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "INSERT INTO open_interest_observations VALUES (%s,%s,%s,%s,%s)",
                    (variant.normalized_observation_id, Decimal("1"), unit, asset,
                     datetime(2025, 6, 20, 18, tzinfo=UTC)),
                )

        # Invariant 15: an envelope of a typed kind cannot commit without its
        # canonical payload -- enforced by a deferred constraint trigger.
        (orphan_raw,) = pipeline.capture_raw(
            [raw(full.source_id, ObservationKind.SETTLEMENT_PRICE, settlement(),
                 identifier="GC062025", event_at=datetime(2025, 6, 16, 18, tzinfo=UTC),
                 ingested_at=preliminary_at)]
        )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO historical_normalized_observations "
                "VALUES (%s,%s,%s,'settle-v1','{}'::jsonb,'VALIDATED','[]'::jsonb,%s)",
                (uuid4(), orphan_raw, contract_id, preliminary_at),
            )

        # Invariant 16: immutable evidence rejects UPDATE and DELETE.
        for table in (
            "futures_settlement_observations",
            "open_interest_observations",
            "historical_source_capabilities",
        ):
            with (
                self.subTest(table),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(f"DELETE FROM {table}")
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE futures_settlement_observations SET settlement_price = 1"
            )

        # ---- restart durability ---------------------------------------------
        restarted = PostgresHistoricalMarketDataPipeline(PostgresDatabase(dsn))
        durable = [
            item for item in restarted.research_query(
                sealed.dataset_version_id, contract_id, window_start, window_end,
                datetime(2025, 6, 22, tzinfo=UTC),
            )
            if item.observation_kind is ObservationKind.SETTLEMENT_PRICE
        ]
        self.assertEqual(
            Decimal(str(durable[0].normalized_value["settlement_price"])), Decimal("2351.10")
        )

        # ---- Data Health carries the new series dimensions -------------------
        from trade_platform.data_health import (
            DataHealthAction,
            DataHealthAssessment,
            DataHealthScope,
            PostgresDataHealthStore,
        )

        store = PostgresDataHealthStore(database)
        evaluated_at = datetime(2025, 6, 22, tzinfo=UTC)
        assessment_ids = {}
        for index, kind in enumerate((SETTLEMENT, OPEN_INTEREST)):
            assessment = DataHealthAssessment(
                assessment_id=uuid4(), dataset_version_id=sealed.dataset_version_id,
                scope_type=DataHealthScope.INSTRUMENT, scope_value=contract_id,
                policy_version="settle-health-v1", evaluated_at=evaluated_at,
                expected_start=window_start, expected_end=window_end,
                max_action=DataHealthAction.INFO, blocking=False, findings=(),
                content_hash=str(index) * 64, interval="1d",
                observation_kind=kind, source_id=full.source_id,
            )
            store.persist(assessment)
            assessment_ids[kind] = assessment.assessment_id

        # Two kinds, one instrument, one interval, one evaluation timestamp --
        # these coexist only because observation_kind is part of the identity.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT observation_kind,source_id FROM data_health_assessments "
                "WHERE scope_value=%s AND evaluated_at=%s ORDER BY observation_kind",
                (contract_id, evaluated_at),
            )
            health_rows = cursor.fetchall()
        self.assertEqual([str(row[0]) for row in health_rows], [OPEN_INTEREST, SETTLEMENT])
        self.assertTrue(all(row[1] is not None for row in health_rows))

        reloaded = store.get(assessment_ids[SETTLEMENT])
        self.assertEqual(reloaded.observation_kind, SETTLEMENT)
        self.assertEqual(reloaded.interval, "1d")
        self.assertEqual(reloaded.source_id, full.source_id)

        # An equity instrument is untouched by any of this.
        self.assertEqual(
            master.get_as_of(equity_id, evaluated_at).instrument_type,
            InstrumentType.COMMON_STOCK,
        )
        self.assertGreater(final_at, preliminary_at + timedelta(0))


def _recomputed_3i1_open_interest_hash(database: object, normalized_ids: tuple) -> str:
    """The Module 3I.1 sealing digest, re-implemented independently of the pipeline.

    Deliberately does not import ``seal_dataset``: the point is to detect any
    change to the canonical open-interest serialization, above all a change to
    the frozen identity token that Module 3I.2 preserved when it renamed
    ``futures_open_interest_observations`` to ``open_interest_observations``.
    """
    import hashlib
    import json

    placeholders = ",".join(["%s"] * len(normalized_ids))
    with database.transaction() as connection, connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(
            "SELECT n.normalized_observation_id,r.raw_payload_sha256,r.observation_kind,"
            "n.normalized_value,o.open_interest,o.unit,o.observed_at,o.unit_asset "
            "FROM historical_normalized_observations n "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "JOIN open_interest_observations o "
            "ON o.normalized_observation_id=n.normalized_observation_id "
            f"WHERE n.normalized_observation_id IN ({placeholders})",  # nosec B608 - placeholders only
            normalized_ids,
        )
        rows = cursor.fetchall()
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: str(item[0])):
        canonical = "|".join(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                json.dumps(row[3], sort_keys=True, separators=(",", ":"), allow_nan=False),
                "futures_open_interest_observations",
                str(Decimal(str(row[4]))),
                str(row[5]),
                row[6].isoformat(),
                row[7] or "",
            )
        )
        digest.update(canonical.encode())
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
