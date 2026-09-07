"""Real PostgreSQL evidence for Module 3I.2 crypto market-data authority.

Every integration test file in this suite shares one PostgreSQL database for the
whole CI run with no reset between files, so this file registers its own fixture
instruments under ``operator_dashboard.RESERVED_TEST_FIXTURE_PREFIX``
(``TESTFIXTURE:``), which keeps them off the operator's unfiltered instrument
discovery page.

Every rate, price, quantity, venue, funding schedule and provider identifier is
a FIXTURE. Nothing here was retrieved from or verified against any crypto venue
or data provider; no real funding rate, mark price, index price, open-interest
figure, venue rule or instrument list is claimed; and no network call is made.
"""

import hashlib
import json
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

VENUE = "TESTFIXCEX"
NAMESPACE = "TESTFIX_CRYPTO_MD_PROVIDER"
REGISTERED_AT = datetime(2025, 7, 1, tzinfo=UTC)
#: 08:00 UTC lies on the fixture convention's eight-hourly, zero-offset schedule.
SETTLED_AT = datetime(2025, 7, 10, 8, tzinfo=UTC)
LATER_TARGET = datetime(2025, 7, 10, 16, tzinfo=UTC)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class CryptoMarketObservationPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_crypto_market_data_authority_end_to_end(self) -> None:
        from trade_platform.crypto_instruments import (
            CryptoFundingConvention,
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoSettlementType,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.domain import AssetClass
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
        crypto = PostgresCryptoInstrumentAuthority(database)
        pipeline = PostgresHistoricalMarketDataPipeline(database)

        # ---- instrument master + 3H.2 crypto semantics -----------------------
        def register(
            suffix: str, symbol: str, identifier: str, *,
            instrument_type: InstrumentType, representation: RepresentationKind,
            asset_class: AssetClass = AssetClass.CRYPTO, venue: str = VENUE,
            quote: str = "USDT", extra: dict[str, object] | None = None,
        ) -> str:
            instrument_id = f"TESTFIXTURE:CRYMD:{suffix}"
            master.register(
                ProfessionalInstrument(
                    instrument_id=instrument_id, asset_class=asset_class,
                    instrument_type=instrument_type, exchange_name=venue, venue=venue,
                    mic=None, canonical_symbol=symbol, listing_date=date(2024, 1, 2),
                    base_currency="BTC" if asset_class is AssetClass.CRYPTO else "USD",
                    quote_currency=quote,
                    settlement_currency=quote,
                    contract_multiplier=Decimal(1), contract_size=Decimal(1),
                    tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                    price_precision=2, quantity_precision=5,
                    trading_timezone=(
                        "UTC" if asset_class is AssetClass.CRYPTO else "America/New_York"
                    ),
                    market_session_type=(
                        SessionType.CRYPTO_24X7 if asset_class is AssetClass.CRYPTO
                        else SessionType.FUTURES_23X5
                    ),
                    representation_kind=representation, registered_at=REGISTERED_AT,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    **(extra or {}),  # type: ignore[arg-type]
                )
            )
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=NAMESPACE, value=identifier, valid_from=REGISTERED_AT,
                    valid_until=None, ingested_at=REGISTERED_AT,
                    source_reference="fixture:provider-identifier",
                )
            )
            return instrument_id

        perpetual_id = register(
            "BTCUSDT:PERP", "TESTFIXMDBTCUSDTP", "BTCUSDT-PERP",
            instrument_type=InstrumentType.CRYPTO_PERPETUAL,
            representation=RepresentationKind.PERPETUAL,
        )
        spot_id = register(
            "BTCUSDT:SPOT", "TESTFIXMDBTCUSDTS", "BTCUSDT-SPOT",
            instrument_type=InstrumentType.SPOT_CRYPTO,
            representation=RepresentationKind.SPOT,
        )
        dated_id = register(
            "BTCUSDT:20250926", "TESTFIXMDBTCUSDT0926", "BTCUSDT-0926",
            instrument_type=InstrumentType.CRYPTO_DATED_FUTURE,
            representation=RepresentationKind.FUTURE,
        )
        futures_id = register(
            "GC:062025", "TESTFIXMDGC", "GC062025",
            instrument_type=InstrumentType.FUTURE, representation=RepresentationKind.FUTURE,
            asset_class=AssetClass.COMMODITY, quote="USD",
            extra={
                "contract_code": "TESTFIXMDGC", "expiration_date": date(2025, 6, 26),
                "first_notice_date": date(2025, 6, 25), "last_trade_date": date(2025, 6, 26),
                "roll_rule": "TESTFIX_ROLL_V1", "continuous_parent_id": None,
            },
        )
        unspecified_id = register(
            "ETHUSDT:PERP", "TESTFIXMDETHUSDTP", "ETHUSDT-PERP",
            instrument_type=InstrumentType.CRYPTO_PERPETUAL,
            representation=RepresentationKind.PERPETUAL,
        )

        def specify(instrument_id: str, kind: CryptoInstrumentKind, **overrides: object) -> None:
            fields: dict[str, object] = {
                "instrument_id": instrument_id, "venue": VENUE, "kind": kind,
                "base_asset": "BTC", "quote_asset": "USDT", "settlement_asset": "USDT",
                "settlement_style": SettlementStyle.LINEAR,
                "settlement_type": CryptoSettlementType.CASH_SETTLED,
                "contract_multiplier": Decimal(1), "contract_size": Decimal(1),
                "reference_price_requirement": ReferencePriceRequirement.MARK_AND_INDEX,
                "index_reference": "TESTFIX_BTCUSDT_INDEX",
                "registered_at": REGISTERED_AT,
                "source_reference": "fixture:crypto-specification",
            }
            fields.update(overrides)
            crypto.specify_instrument(
                CryptoInstrumentSpecification(**fields)  # type: ignore[arg-type]
            )

        specify(perpetual_id, CryptoInstrumentKind.PERPETUAL)
        specify(
            spot_id, CryptoInstrumentKind.SPOT, settlement_asset=None, settlement_style=None,
            settlement_type=CryptoSettlementType.PHYSICAL_DELIVERY,
            reference_price_requirement=ReferencePriceRequirement.NONE, index_reference=None,
        )
        specify(
            dated_id, CryptoInstrumentKind.DATED_FUTURE,
            expiry_at=datetime(2025, 9, 26, 8, tzinfo=UTC),
            reference_price_requirement=ReferencePriceRequirement.INDEX_ONLY,
        )

        # Two funding-schedule versions with independent effective and knowledge
        # clocks. v1 is announced late (known 2025-07-05) so an earlier
        # ingestion genuinely cannot see it; v2 changes the cadence from eight
        # to four hours and only takes effect on 2025-07-20.
        convention_known_at = datetime(2025, 7, 5, tzinfo=UTC)
        crypto.register_funding_convention(
            CryptoFundingConvention(
                instrument_id=perpetual_id, convention_version=1,
                interval_hours=Decimal(8), first_funding_offset_hours=Decimal(0),
                funding_settlement_asset="USDT", funding_rate_floor=Decimal("-0.0075"),
                funding_rate_cap=Decimal("0.0075"), effective_from=REGISTERED_AT,
                known_at=convention_known_at, source_reference="fixture:funding-convention-v1",
                source_hash="a" * 64,
            )
        )
        crypto.register_funding_convention(
            CryptoFundingConvention(
                instrument_id=perpetual_id, convention_version=2,
                interval_hours=Decimal(4), first_funding_offset_hours=Decimal(0),
                funding_settlement_asset="USDT", funding_rate_floor=Decimal("-0.0075"),
                funding_rate_cap=Decimal("0.0075"),
                effective_from=datetime(2025, 7, 20, tzinfo=UTC),
                known_at=datetime(2025, 7, 15, tzinfo=UTC),
                source_reference="fixture:funding-convention-v2", source_hash="b" * 64,
            )
        )

        # ---- source authorization -------------------------------------------
        def build_source(
            name: str, scope: AssetScope, kinds: frozenset[ObservationKind]
        ) -> AuthorizedHistoricalSource:
            return AuthorizedHistoricalSource(
                provider="TESTFIX_CRYPTO_MD", dataset_name=name,
                provider_identifier_namespace=NAMESPACE, provider_terms_version=f"{name}-v1",
                authorization_reference=f"fixture://authorization/{name}",
                authorized_at=REGISTERED_AT, created_at=REGISTERED_AT,
                asset_scope=scope.value, authorized_observation_kinds=kinds,
            )

        # A CRYPTO source cannot claim a futures settlement price, and a FUTURES
        # source cannot claim a crypto reference price.
        with self.assertRaises(HistoricalDataAuthorizationError) as raised:
            build_source(
                "crypto-settlement", AssetScope.CRYPTO,
                frozenset({ObservationKind.SETTLEMENT_PRICE}),
            ).validate()
        self.assertIn("observation_kind_outside_asset_scope", str(raised.exception))
        with self.assertRaises(HistoricalDataAuthorizationError) as raised:
            build_source(
                "futures-mark", AssetScope.FUTURES, frozenset({ObservationKind.MARK_PRICE})
            ).validate()
        self.assertIn("observation_kind_outside_asset_scope", str(raised.exception))

        crypto_source = build_source(
            "crypto-full", AssetScope.CRYPTO,
            frozenset({
                ObservationKind.FUNDING_RATE_REALIZED,
                ObservationKind.FUNDING_RATE_INDICATIVE,
                ObservationKind.MARK_PRICE,
                ObservationKind.INDEX_PRICE,
                ObservationKind.OPEN_INTEREST,
            }),
        )
        indicative_only = build_source(
            "crypto-indicative-only", AssetScope.CRYPTO,
            frozenset({ObservationKind.FUNDING_RATE_INDICATIVE}),
        )
        futures_source = build_source(
            "futures-open-interest", AssetScope.FUTURES,
            frozenset({ObservationKind.OPEN_INTEREST}),
        )
        for source in (crypto_source, indicative_only, futures_source):
            pipeline.register_source(source)

        # ---- payload builders ------------------------------------------------
        def raw(
            source: AuthorizedHistoricalSource, kind: ObservationKind,
            payload: dict[str, object], *, identifier: str = "BTCUSDT-PERP",
            event_at: datetime = SETTLED_AT, ingested_at: datetime | None = None,
            revision: int = 0, exchange: str = VENUE,
            adjustment: AdjustmentStatus = AdjustmentStatus.AS_REPORTED,
        ) -> RawHistoricalObservation:
            return RawHistoricalObservation(
                source_id=source.source_id, observation_kind=kind,
                provider_identifier=identifier, provider_symbol="TESTFIXMDBTCUSDTP",
                exchange=exchange, event_at=event_at, effective_at=event_at,
                ingested_at=ingested_at or (event_at + timedelta(minutes=5)),
                adjustment_status=adjustment, revision=revision,
                provenance_uri=f"fixture://{kind.value}/{identifier}/{event_at.isoformat()}/{revision}",
                raw_payload=payload,
            )

        def funding(**overrides: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "funding_rate": "0.0001",
                "target_funding_at": SETTLED_AT.isoformat(),
                "published_at": SETTLED_AT.isoformat(),
                "settlement_asset": "USDT",
            }
            payload.update(overrides)
            return payload

        def estimate(published_at: datetime, rate: str) -> dict[str, object]:
            return {
                "funding_rate": rate,
                "target_funding_at": LATER_TARGET.isoformat(),
                "published_at": published_at.isoformat(),
                "settlement_asset": "USDT",
            }

        def reference(**overrides: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "price": "58234.50", "price_asset": "USDT",
                "observed_at": SETTLED_AT.isoformat(),
                "methodology_reference": "fixture://methodology/mark-v1",
            }
            payload.update(overrides)
            return payload

        def open_interest(**overrides: object) -> dict[str, object]:
            payload: dict[str, object] = {
                "open_interest": "1250.5", "unit": "BASE_ASSET", "unit_asset": "BTC",
                "observed_at": SETTLED_AT.isoformat(),
            }
            payload.update(overrides)
            return payload

        def normalize_failure(
            observation: RawHistoricalObservation, error: type[Exception]
        ) -> str:
            (captured,) = pipeline.capture_raw([observation])
            with self.assertRaises(error) as failure:
                pipeline.normalize(captured, "crypto-md-v1", observation.ingested_at)
            return str(failure.exception)

        # ---- authorization is per exact kind, not per asset class -------------
        # Invariants 8/9/20: a source authorized only for indicative funding
        # cannot write a realized rate, enforced by the database's composite
        # capability foreign key rather than only by Python.
        with self.assertRaises(HistoricalMarketDataError) as raised:
            pipeline.capture_raw(
                [raw(indicative_only, ObservationKind.FUNDING_RATE_REALIZED, funding())]
            )
        self.assertIn("raw_historical_capture_failed", str(raised.exception))
        with self.assertRaises(HistoricalMarketDataError):
            pipeline.capture_raw(
                [raw(indicative_only, ObservationKind.MARK_PRICE, reference())]
            )

        # Invariant: equity adjustment semantics stay undefined for every new kind.
        for kind in (
            ObservationKind.FUNDING_RATE_REALIZED, ObservationKind.FUNDING_RATE_INDICATIVE,
            ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE,
        ):
            for status in (
                AdjustmentStatus.LATEST_ADJUSTED, AdjustmentStatus.POINT_IN_TIME_ADJUSTED,
            ):
                with self.subTest(kind=kind, status=status), self.assertRaises(
                    HistoricalMarketDataError
                ) as raised:
                    raw(crypto_source, kind, funding(), adjustment=status).validate()
                self.assertIn("adjustment_status_undefined_for_kind", str(raised.exception))

        # ---- happy path -------------------------------------------------------
        settled_ingest = SETTLED_AT + timedelta(minutes=5)
        (realized_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED, funding())]
        )
        (restated_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                 funding(funding_rate="0.00012"), revision=1,
                 ingested_at=datetime(2025, 7, 11, tzinfo=UTC))]
        )
        early_publication = datetime(2025, 7, 10, 4, tzinfo=UTC)
        late_publication = datetime(2025, 7, 10, 12, tzinfo=UTC)
        (early_estimate_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.FUNDING_RATE_INDICATIVE,
                 estimate(early_publication, "0.00020"), event_at=early_publication)]
        )
        (late_estimate_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.FUNDING_RATE_INDICATIVE,
                 estimate(late_publication, "0.00025"), event_at=late_publication)]
        )
        (mark_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.MARK_PRICE, reference())]
        )
        (index_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.INDEX_PRICE,
                 reference(price="58230.10",
                           methodology_reference="fixture://methodology/index-v1"))]
        )
        (oi_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.OPEN_INTEREST, open_interest())]
        )

        # Invariant 22: the same revision with a conflicting payload fails closed.
        with self.assertRaises(HistoricalMarketDataError) as raised:
            pipeline.capture_raw(
                [raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                     funding(funding_rate="0.09"))]
            )
        self.assertIn("raw_historical_observation_conflict", str(raised.exception))

        realized = pipeline.normalize(realized_raw, "crypto-md-v1", settled_ingest)
        restated = pipeline.normalize(
            restated_raw, "crypto-md-v1", datetime(2025, 7, 11, tzinfo=UTC)
        )
        early_estimate = pipeline.normalize(
            early_estimate_raw, "crypto-md-v1", early_publication + timedelta(minutes=5)
        )
        late_estimate = pipeline.normalize(
            late_estimate_raw, "crypto-md-v1", late_publication + timedelta(minutes=5)
        )
        mark = pipeline.normalize(mark_raw, "crypto-md-v1", settled_ingest)
        index = pipeline.normalize(index_raw, "crypto-md-v1", settled_ingest)
        crypto_oi = pipeline.normalize(oi_raw, "crypto-md-v1", settled_ingest)

        # The envelope holds only a pointer marker. Both funding kinds and both
        # reference-price kinds share a typed table; the envelope kind, not any
        # stored column, is what distinguishes them.
        self.assertEqual(
            realized.normalized_value,
            {"canonical_payload_table": "crypto_funding_observations"},
        )
        self.assertEqual(realized.normalized_value, early_estimate.normalized_value)
        self.assertEqual(
            mark.normalized_value,
            {"canonical_payload_table": "crypto_reference_price_observations"},
        )
        self.assertEqual(mark.normalized_value, index.normalized_value)
        # Invariant 26 at the pipeline boundary: crypto open interest serializes
        # under the SAME frozen canonical identity as 3I.1 futures open interest,
        # even though the physical table is now open_interest_observations.
        self.assertEqual(
            crypto_oi.normalized_value,
            {"canonical_payload_table": "futures_open_interest_observations"},
        )

        # The convention actually in force was bound to the record.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT funding_rate,target_funding_at,settlement_asset,convention_version "
                "FROM crypto_funding_observations WHERE normalized_observation_id=%s",
                (realized.normalized_observation_id,),
            )
            stored_funding = cursor.fetchone()
            cursor.execute(
                "SELECT open_interest,unit,unit_asset FROM open_interest_observations "
                "WHERE normalized_observation_id=%s",
                (crypto_oi.normalized_observation_id,),
            )
            stored_oi = cursor.fetchone()
        self.assertEqual(Decimal(str(stored_funding[0])), Decimal("0.0001"))
        self.assertEqual(stored_funding[1], SETTLED_AT)
        self.assertEqual(str(stored_funding[2]), "USDT")
        self.assertEqual(int(str(stored_funding[3])), 1)
        self.assertEqual(Decimal(str(stored_oi[0])), Decimal("1250.5"))
        self.assertEqual((str(stored_oi[1]), str(stored_oi[2])), ("BASE_ASSET", "BTC"))

        # ---- fail-closed instrument and venue binding ------------------------
        # Invariants 1, 2, 3, 10, 11, 17.
        for label, kind, payload, identifier, expected in (
            ("realized_funding_on_spot", ObservationKind.FUNDING_RATE_REALIZED,
             funding(), "BTCUSDT-SPOT", "funding_requires_perpetual"),
            ("indicative_funding_on_spot", ObservationKind.FUNDING_RATE_INDICATIVE,
             estimate(early_publication, "0.0002"), "BTCUSDT-SPOT",
             "funding_requires_perpetual"),
            ("funding_on_dated_future", ObservationKind.FUNDING_RATE_REALIZED,
             funding(), "BTCUSDT-0926", "funding_requires_perpetual"),
            ("mark_on_index_only_contract", ObservationKind.MARK_PRICE,
             reference(), "BTCUSDT-0926", "reference_price_not_permitted_by_instrument"),
            ("index_on_spot", ObservationKind.INDEX_PRICE,
             reference(), "BTCUSDT-SPOT", "reference_price_not_permitted_by_instrument"),
            ("mark_on_spot", ObservationKind.MARK_PRICE,
             reference(), "BTCUSDT-SPOT", "reference_price_not_permitted_by_instrument"),
            ("open_interest_on_spot", ObservationKind.OPEN_INTEREST,
             open_interest(), "BTCUSDT-SPOT", "open_interest_requires_derivative"),
        ):
            with self.subTest(label):
                event_at = (
                    early_publication if kind is ObservationKind.FUNDING_RATE_INDICATIVE
                    else SETTLED_AT
                )
                self.assertIn(
                    expected,
                    normalize_failure(
                        raw(crypto_source, kind, payload, identifier=identifier,
                            event_at=event_at),
                        HistoricalDataQualityError,
                    ),
                )

        # Invariant 17: a crypto kind may not describe a futures or equity
        # instrument at all -- this fails before any payload is even parsed.
        self.assertIn(
            "kind_requires_crypto_instrument",
            normalize_failure(
                raw(crypto_source, ObservationKind.MARK_PRICE, reference(),
                    identifier="GC062025"),
                HistoricalDataResolutionError,
            ),
        )
        # ...and a crypto instrument with no 3H.2 specification fails closed.
        self.assertIn(
            "instrument_has_no_crypto_specification",
            normalize_failure(
                raw(crypto_source, ObservationKind.MARK_PRICE, reference(),
                    identifier="ETHUSDT-PERP"),
                HistoricalDataResolutionError,
            ),
        )

        # Invariant 18: the observation's venue must be the instrument's venue.
        self.assertIn(
            "exchange_instrument_mismatch",
            normalize_failure(
                raw(crypto_source, ObservationKind.MARK_PRICE,
                    reference(observed_at=(SETTLED_AT - timedelta(hours=8)).isoformat()),
                    exchange="TESTFIXOTHERCEX",
                    event_at=SETTLED_AT - timedelta(hours=8)),
                HistoricalDataQualityError,
            ),
        )

        # Invariant 19: a FUTURES-scoped source may hold OPEN_INTEREST authority
        # -- there is one open-interest authority -- but it does not thereby get
        # to describe a crypto perpetual.
        self.assertIn(
            "historical_source_asset_out_of_scope",
            normalize_failure(
                raw(futures_source, ObservationKind.OPEN_INTEREST, open_interest(),
                    event_at=SETTLED_AT - timedelta(hours=16)),
                HistoricalDataResolutionError,
            ),
        )

        # ---- funding convention binding, two clocks --------------------------
        # Invariant 5: a convention this platform did not yet know cannot
        # validate an observation ingested before it was learned.
        unseen_target = datetime(2025, 7, 3, 8, tzinfo=UTC)
        self.assertIn(
            "funding_convention_not_known_at_observation_knowledge_time",
            normalize_failure(
                raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                    funding(target_funding_at=unseen_target.isoformat(),
                            published_at=unseen_target.isoformat()),
                    event_at=unseen_target),
                HistoricalDataQualityError,
            ),
        )

        # Invariant 6: an instant the visible schedule never produces is refused.
        off_schedule = datetime(2025, 7, 12, 9, tzinfo=UTC)
        self.assertIn(
            "funding_target_incompatible_with_convention_schedule",
            normalize_failure(
                raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                    funding(target_funding_at=off_schedule.isoformat(),
                            published_at=off_schedule.isoformat()),
                    event_at=off_schedule),
                HistoricalDataQualityError,
            ),
        )

        # The venue's LATER schedule change does not reach back. 04:00 lies on
        # v2's four-hourly grid but not on v1's eight-hourly one, and v1 is the
        # schedule effective at a 2025-07-12 funding instant -- so ingesting this
        # record long after v2 was announced still refuses it.
        pre_change_target = datetime(2025, 7, 12, 4, tzinfo=UTC)
        self.assertIn(
            "funding_target_incompatible_with_convention_schedule",
            normalize_failure(
                raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                    funding(target_funding_at=pre_change_target.isoformat(),
                            published_at=pre_change_target.isoformat()),
                    event_at=pre_change_target,
                    ingested_at=datetime(2025, 7, 25, tzinfo=UTC)),
                HistoricalDataQualityError,
            ),
        )
        # ...while the same 04:00 offset AFTER the change is accepted, and binds
        # to convention version 2.
        post_change_target = datetime(2025, 7, 22, 4, tzinfo=UTC)
        (post_change_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                 funding(target_funding_at=post_change_target.isoformat(),
                         published_at=post_change_target.isoformat()),
                 event_at=post_change_target)]
        )
        post_change = pipeline.normalize(
            post_change_raw, "crypto-md-v1", post_change_target + timedelta(minutes=5)
        )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT convention_version FROM crypto_funding_observations "
                "WHERE normalized_observation_id=%s",
                (post_change.normalized_observation_id,),
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 2)

        # Invariants 4, 7, 8, 9, 12, 13, 14, 15 at the pipeline boundary.
        for position, (label, kind, payload, expected) in enumerate((
            ("settlement_asset_mismatch", ObservationKind.FUNDING_RATE_REALIZED,
             funding(settlement_asset="USDC"),
             "funding_settlement_asset_differs_from_convention"),
            ("rate_above_cap", ObservationKind.FUNDING_RATE_REALIZED,
             funding(funding_rate="0.02"), "funding_rate_above_convention_cap"),
            ("rate_below_floor", ObservationKind.FUNDING_RATE_REALIZED,
             funding(funding_rate="-0.02"), "funding_rate_below_convention_floor"),
            ("indicative_as_realized", ObservationKind.FUNDING_RATE_REALIZED,
             funding(target_funding_at=LATER_TARGET.isoformat()),
             "realized_funding_target_must_equal_event_instant"),
            ("non_positive_mark", ObservationKind.MARK_PRICE, reference(price="0"),
             "non_positive_reference_price"),
            ("negative_open_interest", ObservationKind.OPEN_INTEREST,
             open_interest(open_interest="-1"), "negative_open_interest"),
            ("base_asset_unit_mismatch", ObservationKind.OPEN_INTEREST,
             open_interest(unit_asset="ETH"),
             "base_asset_open_interest_unit_asset_mismatch"),
            ("quote_notional_unit_mismatch", ObservationKind.OPEN_INTEREST,
             open_interest(unit="QUOTE_NOTIONAL", unit_asset="BTC"),
             "quote_notional_open_interest_unit_asset_mismatch"),
        )):
            with self.subTest(label):
                # Distinct funding instants, each still on the eight-hourly
                # schedule and each after the convention became known, so every
                # case fails for the reason under test and nothing else.
                event_at = SETTLED_AT - timedelta(hours=8) * (position + 1)
                case = dict(payload)
                if "target_funding_at" in case:
                    case["published_at"] = event_at.isoformat()
                    case["target_funding_at"] = (
                        (event_at + timedelta(hours=8)).isoformat()
                        if label == "indicative_as_realized" else event_at.isoformat()
                    )
                self.assertIn(
                    expected,
                    normalize_failure(
                        raw(crypto_source, kind, case, event_at=event_at),
                        HistoricalDataQualityError,
                    ),
                )
        # Invariant 9: a realized funding instant is never in the future of its
        # own publication, so a realized record cannot be filed as indicative.
        settled_instant = datetime(2025, 7, 13, 8, tzinfo=UTC)
        self.assertIn(
            "indicative_funding_target_must_be_in_the_future",
            normalize_failure(
                raw(crypto_source, ObservationKind.FUNDING_RATE_INDICATIVE,
                    funding(target_funding_at=settled_instant.isoformat(),
                            published_at=settled_instant.isoformat()),
                    event_at=settled_instant),
                HistoricalDataQualityError,
            ),
        )

        # Nothing rejected above left a normalized envelope behind.
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations "
                "WHERE quality_status<>'VALIDATED'"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)

        # ---- sealed datasets and point-in-time replay ------------------------
        early_dataset = pipeline.seal_dataset(
            crypto_source.source_id, "crypto-md-early", "crypto-md-v1",
            (realized.normalized_observation_id, early_estimate.normalized_observation_id,
             mark.normalized_observation_id, index.normalized_observation_id,
             crypto_oi.normalized_observation_id),
            datetime(2025, 7, 10, 9, tzinfo=UTC),
        )
        full_dataset = pipeline.seal_dataset(
            crypto_source.source_id, "crypto-md-full", "crypto-md-v1",
            (realized.normalized_observation_id, restated.normalized_observation_id,
             early_estimate.normalized_observation_id,
             late_estimate.normalized_observation_id, mark.normalized_observation_id,
             index.normalized_observation_id, crypto_oi.normalized_observation_id),
            datetime(2025, 7, 11, 12, tzinfo=UTC),
        )
        self.assertEqual(len(full_dataset.content_hash), 64)
        self.assertNotEqual(full_dataset.content_hash, early_dataset.content_hash)

        window_start = datetime(2025, 7, 10, tzinfo=UTC)
        window_end = datetime(2025, 7, 10, 23, 59, tzinfo=UTC)

        def replay(dataset_version_id: object, knowledge_at: datetime) -> dict:
            observations = pipeline.research_query(
                dataset_version_id,  # type: ignore[arg-type]
                perpetual_id, window_start, window_end, knowledge_at,
            )
            grouped: dict = {}
            for item in observations:
                grouped.setdefault(item.observation_kind, []).append(item)
            return grouped

        early_view = replay(early_dataset.dataset_version_id, datetime(2025, 7, 10, 10, tzinfo=UTC))
        # The realized rate visible then is the original, not the later restatement.
        self.assertEqual(len(early_view[ObservationKind.FUNDING_RATE_REALIZED]), 1)
        self.assertEqual(
            Decimal(
                str(early_view[ObservationKind.FUNDING_RATE_REALIZED][0]
                    .normalized_value["funding_rate"])
            ),
            Decimal("0.0001"),
        )
        # ...and only the estimate published by then.
        self.assertEqual(len(early_view[ObservationKind.FUNDING_RATE_INDICATIVE]), 1)
        self.assertEqual(
            early_view[ObservationKind.FUNDING_RATE_INDICATIVE][0]
            .normalized_value["published_at"],
            early_publication.isoformat(),
        )
        self.assertEqual(
            Decimal(str(early_view[ObservationKind.MARK_PRICE][0].normalized_value["price"])),
            Decimal("58234.50"),
        )
        self.assertNotEqual(
            early_view[ObservationKind.MARK_PRICE][0].normalized_value["price"],
            early_view[ObservationKind.INDEX_PRICE][0].normalized_value["price"],
        )
        self.assertEqual(
            early_view[ObservationKind.OPEN_INTEREST][0].normalized_value["unit"], "BASE_ASSET"
        )

        late_view = replay(full_dataset.dataset_version_id, datetime(2025, 7, 12, tzinfo=UTC))
        self.assertEqual(
            Decimal(
                str(late_view[ObservationKind.FUNDING_RATE_REALIZED][0]
                    .normalized_value["funding_rate"])
            ),
            Decimal("0.00012"),
        )
        self.assertEqual(late_view[ObservationKind.FUNDING_RATE_REALIZED][0].revision, 1)
        # Both successive estimates for the SAME future funding instant survive;
        # the later one did not overwrite the earlier one.
        estimates = late_view[ObservationKind.FUNDING_RATE_INDICATIVE]
        self.assertEqual(len(estimates), 2)
        self.assertEqual(
            [item.normalized_value["funding_rate"] for item in estimates],
            [
                str(Decimal("0.00020").quantize(Decimal("1.000000000000"))),
                str(Decimal("0.00025").quantize(Decimal("1.000000000000"))),
            ],
        )
        self.assertEqual(
            {item.normalized_value["target_funding_at"] for item in estimates},
            {LATER_TARGET.isoformat()},
        )

        # Invariant 21: a dataset sealed after a knowledge time is invisible at
        # it, so a later-known revision cannot leak into an earlier replay.
        self.assertEqual(
            replay(full_dataset.dataset_version_id, datetime(2025, 7, 10, 10, tzinfo=UTC)), {}
        )

        # ---- dataset identity -------------------------------------------------
        # Invariant 26: crypto open interest hashes through the frozen canonical
        # identity token, recomputed here from an independent re-implementation
        # of the sealing formula with that token written out as a literal. If the
        # token were ever "corrected" to the physical table name, this diverges.
        oi_dataset = pipeline.seal_dataset(
            crypto_source.source_id, "crypto-md-open-interest-only", "crypto-md-v1",
            (crypto_oi.normalized_observation_id,), datetime(2025, 7, 11, 12, tzinfo=UTC),
        )
        self.assertEqual(
            oi_dataset.content_hash,
            _recomputed_open_interest_dataset_hash(
                database, (crypto_oi.normalized_observation_id,)
            ),
        )

        # Invariant 27: a dataset whose only difference is a canonical financial
        # value must hash differently.
        variants = []
        for index_value, rate in enumerate(("0.00031", "0.00032")):
            target = datetime(2025, 7, 14, 8, tzinfo=UTC) + timedelta(days=index_value)
            (variant_raw,) = pipeline.capture_raw(
                [raw(crypto_source, ObservationKind.FUNDING_RATE_REALIZED,
                     funding(funding_rate=rate, target_funding_at=target.isoformat(),
                             published_at=target.isoformat()),
                     event_at=target)]
            )
            variant = pipeline.normalize(
                variant_raw, "crypto-md-v1", target + timedelta(minutes=5)
            )
            variants.append(
                pipeline.seal_dataset(
                    crypto_source.source_id, f"crypto-md-variant-{index_value}", "crypto-md-v1",
                    (variant.normalized_observation_id,), datetime(2025, 7, 20, tzinfo=UTC),
                )
            )
        self.assertNotEqual(variants[0].content_hash, variants[1].content_hash)

        # ---- database-level integrity, bypassing Python ----------------------
        # Invariant 23: a typed payload may not attach to an envelope of another
        # kind, even one that shares its table's kind set boundary.
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO crypto_funding_observations VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (mark.normalized_observation_id, Decimal("0.0001"), SETTLED_AT, SETTLED_AT,
                 "USDT", stored_funding_convention_id(database, realized), 1),
            )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO crypto_reference_price_observations VALUES (%s,%s,%s,%s,%s)",
                (realized.normalized_observation_id, Decimal("1"), "USDT", SETTLED_AT, None),
            )
        # A reference-price row may attach to either reference-price kind, and a
        # funding row to either funding kind -- but never across the pair.
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO crypto_reference_price_observations VALUES (%s,%s,%s,%s,%s)",
                (crypto_oi.normalized_observation_id, Decimal("1"), "USDT", SETTLED_AT, None),
            )

        # Invariant 12 as a database CHECK.
        for label, price in (("zero", Decimal(0)), ("negative", Decimal("-1"))):
            with (
                self.subTest(label),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "INSERT INTO crypto_reference_price_observations VALUES (%s,%s,%s,%s,%s)",
                    (uuid4(), price, "USDT", SETTLED_AT, None),
                )

        # Invariant 24: an envelope of a typed kind cannot commit without its
        # canonical payload -- enforced by a deferred constraint trigger.
        orphan_at = datetime(2025, 7, 9, 8, tzinfo=UTC)
        (orphan_raw,) = pipeline.capture_raw(
            [raw(crypto_source, ObservationKind.MARK_PRICE, reference(), event_at=orphan_at)]
        )
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO historical_normalized_observations "
                "VALUES (%s,%s,%s,'crypto-md-v1','{}'::jsonb,'VALIDATED','[]'::jsonb,%s)",
                (uuid4(), orphan_raw, perpetual_id, orphan_at + timedelta(minutes=5)),
            )

        # Invariant 25: canonical evidence rejects UPDATE and DELETE.
        for table in (
            "crypto_funding_observations",
            "crypto_reference_price_observations",
            "open_interest_observations",
        ):
            with (
                self.subTest(table),
                self.assertRaises(Exception),  # noqa: B017
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(f"DELETE FROM {table}")  # nosec B608 - fixed literal list
        with (
            self.assertRaises(Exception),  # noqa: B017
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("UPDATE crypto_funding_observations SET funding_rate = 1")

        # ---- restart durability ----------------------------------------------
        restarted = PostgresHistoricalMarketDataPipeline(PostgresDatabase(dsn))
        durable = [
            item for item in restarted.research_query(
                full_dataset.dataset_version_id, perpetual_id, window_start, window_end,
                datetime(2025, 7, 12, tzinfo=UTC),
            )
            if item.observation_kind is ObservationKind.FUNDING_RATE_REALIZED
        ]
        self.assertEqual(
            Decimal(str(durable[0].normalized_value["funding_rate"])), Decimal("0.00012")
        )

        # ---- Data Health carries every new observation kind -------------------
        from trade_platform.data_health import (
            DataHealthAction,
            DataHealthAssessment,
            DataHealthScope,
            PostgresDataHealthStore,
        )

        store = PostgresDataHealthStore(database)
        evaluated_at = datetime(2025, 7, 12, tzinfo=UTC)
        kinds = (
            "FUNDING_RATE_REALIZED", "FUNDING_RATE_INDICATIVE",
            "MARK_PRICE", "INDEX_PRICE", "OPEN_INTEREST",
        )
        for kind_value in kinds:
            store.persist(
                DataHealthAssessment(
                    assessment_id=uuid4(), dataset_version_id=full_dataset.dataset_version_id,
                    scope_type=DataHealthScope.INSTRUMENT, scope_value=perpetual_id,
                    policy_version="crypto-md-health-v1", evaluated_at=evaluated_at,
                    expected_start=window_start, expected_end=window_end,
                    max_action=DataHealthAction.INFO, blocking=False, findings=(),
                    content_hash=hashlib.sha256(
                        f"fixture:crypto-md-health:{kind_value}".encode()
                    ).hexdigest(),
                    interval="",
                    observation_kind=kind_value, source_id=crypto_source.source_id,
                )
            )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT observation_kind FROM data_health_assessments "
                "WHERE scope_value=%s AND evaluated_at=%s ORDER BY observation_kind",
                (perpetual_id, evaluated_at),
            )
            recorded = [str(row[0]) for row in cursor.fetchall()]
        # Five coexisting series on one instrument, one interval, one evaluation
        # timestamp -- possible only because observation_kind is part of the
        # assessment's identity. Realized and indicative funding are tracked
        # separately, so a healthy estimate stream cannot mask a broken settled one.
        self.assertEqual(recorded, sorted(kinds))
        self.assertTrue(spot_id and dated_id and futures_id and unspecified_id)


def stored_funding_convention_id(database: object, normalized: object) -> object:
    with database.transaction() as connection, connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(
            "SELECT convention_id FROM crypto_funding_observations "
            "WHERE normalized_observation_id=%s",
            (normalized.normalized_observation_id,),  # type: ignore[attr-defined]
        )
        return cursor.fetchone()[0]


def _recomputed_open_interest_dataset_hash(database: object, normalized_ids: tuple) -> str:
    """Re-implement the sealing formula independently, token written as a literal.

    This deliberately duplicates ``seal_dataset``'s digest rather than importing
    it: the point is to detect a change to the canonical open-interest
    serialization -- above all a "fix" that replaced the frozen identity token
    with the current physical table name -- which would silently invalidate every
    dataset sealed before Module 3I.2 renamed the table.
    """
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
