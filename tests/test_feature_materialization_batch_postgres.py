"""Phase 3D.7R.2 evidence: the dataset-streamed, chunk-written feature path is the
per-event canonical path, only scalable.

Every price, open-interest figure, provider identifier and timestamp is a
FIXTURE; nothing here was retrieved from or verified against any venue. The
test owns a DISPOSABLE database beneath the configured local/CI PostgreSQL
instance (the ``test_historical_acquisition_postgres`` pattern) so it can
register the canonical ``crypto_mark_index_basis`` / ``open_interest_change``
definitions verbatim without disturbing the database-wide singleton
invariants other integration tests assert.

The central proof is differential: for the same sealed evidence, the batch
generators yield exactly the materializations -- values, manifests,
timestamps and V2 content hashes -- the unchanged per-event calculators
produce, fail at the same event with the same error, and leave the same
durable prefix behind.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
REGISTERED_AT = datetime(2025, 1, 2, tzinfo=UTC)
NAMESPACE = "TESTFIX_3D7R2_PROVIDER"
NORMALIZATION_VERSION = "3d7r2-v1"
_MINUTE = timedelta(minutes=1)
_FIVE = timedelta(minutes=5)


def _disposable_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def _identity(value: Any) -> tuple[object, ...]:
    """Everything a materialization asserts, except its random surrogate id."""
    return (
        value.feature_id, value.subject_type, value.subject_id, value.dataset_version,
        value.event_at, value.effective_at, value.knowledge_at, value.computed_at,
        value.source_observation_manifest, value.value, value.quality_status, value.content_hash,
    )


class _CountingDatabase:
    """Counts transactions opened against a real :class:`PostgresDatabase`."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.connection = inner.connection
        self.transactions = 0

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        self.transactions += 1
        with self.inner.transaction() as connection:
            yield connection


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FeatureMaterializationBatchPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("Phase 3D.7R.2 requires a local or CI disposable PostgreSQL DSN")
        cls.database_name = f"feature_batch_phase3d7r2_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        old_dsn = os.environ.get("POSTGRES_TEST_DSN")
        try:
            os.environ["POSTGRES_TEST_DSN"] = cls.dsn
            command.upgrade(config, "head")
        finally:
            if old_dsn is not None:
                os.environ["POSTGRES_TEST_DSN"] = old_dsn

        from trade_platform.crypto_derivatives_features import crypto_mark_index_basis_definition
        from trade_platform.feature_authority import PostgresFeatureAuthority
        from trade_platform.historical_market_data import (
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
        )
        from trade_platform.open_interest_features import open_interest_change_definition
        from trade_platform.persistence import PostgresDatabase

        cls.database = PostgresDatabase(cls.dsn)
        authority = PostgresFeatureAuthority(cls.database)
        cls.basis_definition = crypto_mark_index_basis_definition(REGISTERED_AT)
        cls.oi_definition = open_interest_change_definition(REGISTERED_AT)
        # Second, independently identified copies: lets the per-event and the
        # batch path each persist their own rows for a durable-state comparison.
        cls.basis_alt = replace(
            cls.basis_definition, name="crypto_mark_index_basis_3d7r2_alt", feature_id=uuid4()
        )
        cls.oi_alt = replace(
            cls.oi_definition, name="open_interest_change_3d7r2_alt", feature_id=uuid4()
        )
        for definition in (cls.basis_definition, cls.oi_definition, cls.basis_alt, cls.oi_alt):
            authority.register(definition)

        cls.source = AuthorizedHistoricalSource(
            provider="TESTFIX_3D7R2", dataset_name="3d7r2-fixture",
            provider_identifier_namespace=NAMESPACE, provider_terms_version="v1",
            authorization_reference="fixture://authorization/3d7r2",
            authorized_at=REGISTERED_AT, created_at=REGISTERED_AT,
            asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset({
                ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE,
                ObservationKind.OPEN_INTEREST,
            }),
        )
        cls.pipeline = PostgresHistoricalMarketDataPipeline(cls.database)
        cls.pipeline.register_source(cls.source)
        cls.venues: dict[str, str] = {}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    # ---- fixture helpers ------------------------------------------------------

    def _register_perpetual(self, suffix: str, *extra_identifiers: str) -> str:
        from trade_platform.crypto_instruments import (
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoSettlementType,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.domain import AssetClass
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

        master = PostgresProfessionalInstrumentMaster(self.database)
        venue = f"TESTFIXCEX3D7R2{suffix}"
        instrument_id = f"TESTFIXTURE:3D7R2:{suffix}"
        symbol = f"TESTFIX3D7R2{suffix}"
        self.venues[symbol] = venue
        master.register(
            ProfessionalInstrument(
                instrument_id=instrument_id, asset_class=AssetClass.CRYPTO,
                instrument_type=InstrumentType.CRYPTO_PERPETUAL, exchange_name=venue,
                venue=venue, mic=None, canonical_symbol=symbol, listing_date=date(2024, 1, 2),
                base_currency="BTC", quote_currency="USDT", settlement_currency="USDT",
                contract_multiplier=Decimal(1), contract_size=Decimal(1),
                tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                price_precision=2, quantity_precision=5, trading_timezone="UTC",
                market_session_type=SessionType.CRYPTO_24X7,
                representation_kind=RepresentationKind.PERPETUAL, registered_at=REGISTERED_AT,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        PostgresCryptoInstrumentAuthority(self.database).specify_instrument(
            CryptoInstrumentSpecification(
                instrument_id=instrument_id, venue=venue, kind=CryptoInstrumentKind.PERPETUAL,
                base_asset="BTC", quote_asset="USDT", settlement_asset="USDT",
                settlement_style=SettlementStyle.LINEAR,
                settlement_type=CryptoSettlementType.CASH_SETTLED,
                contract_multiplier=Decimal(1), contract_size=Decimal(1),
                reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
                index_reference=f"TESTFIX_3D7R2_{suffix}_INDEX", registered_at=REGISTERED_AT,
                source_reference="fixture:crypto-specification",
            )
        )
        for identifier in (suffix, *extra_identifiers):
            master.add_identifier_mapping(
                IdentifierMapping(
                    instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                    namespace=NAMESPACE, value=identifier, valid_from=REGISTERED_AT,
                    valid_until=None, ingested_at=REGISTERED_AT,
                    source_reference="fixture:provider-identifier",
                )
            )
        return instrument_id

    def _capture(
        self, kind_name: str, suffix: str, payload: dict[str, object], event_at: datetime, *,
        identifier: str | None = None, revision: int = 0, ingested_at: datetime | None = None,
    ) -> UUID:
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            RawHistoricalObservation,
        )

        kind = ObservationKind(kind_name)
        symbol = f"TESTFIX3D7R2{suffix}"
        provider_identifier = identifier or suffix
        resolved_ingested_at = ingested_at or event_at
        (raw_id,) = self.pipeline.capture_raw(
            [
                RawHistoricalObservation(
                    source_id=self.source.source_id, observation_kind=kind,
                    provider_identifier=provider_identifier, provider_symbol=symbol,
                    exchange=self.venues[symbol], event_at=event_at, effective_at=event_at,
                    ingested_at=resolved_ingested_at,
                    adjustment_status=AdjustmentStatus.AS_REPORTED, revision=revision,
                    provenance_uri=(
                        f"fixture://{kind.value}/{provider_identifier}/{revision}/"
                        f"{event_at.isoformat()}"
                    ),
                    raw_payload=payload,
                )
            ]
        )
        return self.pipeline.normalize(
            raw_id, NORMALIZATION_VERSION, resolved_ingested_at
        ).normalized_observation_id

    def _price(self, kind: str, suffix: str, price: str, event_at: datetime, **kwargs: Any) -> UUID:
        payload = {"price": price, "price_asset": "USDT", "observed_at": event_at.isoformat()}
        return self._capture(kind, suffix, payload, event_at, **kwargs)

    def _oi(
        self, suffix: str, value: str, unit: str, unit_asset: str | None, event_at: datetime,
        **kwargs: Any,
    ) -> UUID:
        payload: dict[str, object] = {
            "open_interest": value, "unit": unit, "observed_at": event_at.isoformat(),
        }
        if unit_asset is not None:
            payload["unit_asset"] = unit_asset
        return self._capture("OPEN_INTEREST", suffix, payload, event_at, **kwargs)

    def _seal(self, name: str, members: list[UUID], created_at: datetime) -> Any:
        return self.pipeline.seal_dataset(
            self.source.source_id, name, NORMALIZATION_VERSION, tuple(members), created_at
        )

    @staticmethod
    def _collect(produce: Callable[[], Iterator[Any]]) -> tuple[list[Any], str | None]:
        results: list[Any] = []
        try:
            for value in produce():
                results.append(value)  # noqa: PERF402 - keeps the prefix yielded before a raise
        except ValueError as error:
            return results, str(error)
        return results, None

    @staticmethod
    def _per_event(
        materialize: Callable[[datetime], Any], event_ats: list[datetime]
    ) -> tuple[list[Any], str | None]:
        results: list[Any] = []
        for event_at in event_ats:
            try:
                value = materialize(event_at)
            except ValueError as error:
                return results, str(error)
            if value is not None:
                results.append(value)
        return results, None

    def _stored(self, feature_id: UUID, dataset_version_id: UUID) -> list[tuple[object, ...]]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT event_at, effective_at, knowledge_at, computed_at, "
                "source_observation_manifest, value, hash_version, instrument_id, subject_id "
                "FROM feature_materializations WHERE feature_id=%s AND dataset_version=%s "
                "ORDER BY event_at",
                (feature_id, str(dataset_version_id)),
            )
            return [tuple(row) for row in cursor.fetchall()]

    # ---- crypto_mark_index_basis ------------------------------------------------

    def _basis_dataset(self, suffix: str, *extra: str) -> tuple[str, Any, list[datetime]]:
        """Mark/index evidence across a UTC midnight with revisions and gaps."""
        instrument_id = self._register_perpetual(suffix, *extra)
        start = datetime(2025, 6, 30, 23, 54, tzinfo=UTC)
        events = [start + index * _MINUTE for index in range(14)]
        seal_at = events[-1] + timedelta(hours=1)
        members: list[UUID] = []
        for index, event_at in enumerate(events):
            if index != 4:  # no MARK at events[4]
                members.append(self._price("MARK_PRICE", suffix, f"{30000 + index}.5", event_at))
            if index != 6:  # no INDEX at events[6]
                members.append(self._price("INDEX_PRICE", suffix, f"{29990 + index}.25", event_at))
        # A later, visible revision wins at events[2] ...
        members.append(
            self._price(
                "MARK_PRICE", suffix, "31234.75", events[2], revision=1,
                ingested_at=events[2] + timedelta(minutes=30),
            )
        )
        # ... as does an INDEX revision at events[8]. (A sealed dataset can never
        # hold a member ingested after its own created_at, so no member is ever
        # invisible at a decision_at at which the dataset itself is knowable.)
        members.append(
            self._price(
                "INDEX_PRICE", suffix, "29000.50", events[8], revision=1,
                ingested_at=seal_at - timedelta(minutes=1),
            )
        )
        dataset = self._seal(f"3d7r2-basis-{suffix}", members, seal_at)
        return instrument_id, dataset, events

    def test_basis_batch_reproduces_per_event_path_exactly(self) -> None:
        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
        )

        instrument_id, dataset, events = self._basis_dataset("BASIS")
        decision_at = events[-1] + timedelta(hours=2)
        requested = [event for index, event in enumerate(events) if index != 10]
        calculator = PostgresCryptoDerivativesFeatureCalculator(self.database)
        feature_id = self.basis_definition.feature_id

        batch, batch_error = self._collect(
            lambda: calculator.iter_crypto_mark_index_basis(
                feature_id=feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_ats=requested,
                decision_at=decision_at,
            )
        )
        self.assertIsNone(batch_error)
        # The generator is read-only.
        self.assertEqual(self._stored(feature_id, dataset.dataset_version_id), [])
        per_event, per_event_error = self._per_event(
            lambda event_at: calculator.materialize_crypto_mark_index_basis(
                feature_id=feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_at=event_at,
                decision_at=decision_at,
            ),
            requested,
        )
        self.assertIsNone(per_event_error)
        # 13 requested events, minus the missing MARK and the missing INDEX.
        self.assertEqual(len(per_event), 11)
        self.assertEqual([_identity(v) for v in batch], [_identity(v) for v in per_event])
        by_event = {value.event_at: value for value in batch}
        self.assertIn("mark_revision:1", by_event[events[2]].source_observation_manifest)
        self.assertIn("index_revision:1", by_event[events[8]].source_observation_manifest)
        self.assertIn("index_revision:0", by_event[events[9]].source_observation_manifest)
        self.assertNotIn(events[10], by_event)

        # Writing the batch over the per-event rows reconciles every row as
        # identical evidence: same count, no new row, no conflict.
        stored_before = self._stored(feature_id, dataset.dataset_version_id)
        written = calculator.materialize_crypto_mark_index_basis_batch(
            feature_id=feature_id, instrument_id=instrument_id,
            dataset_version_id=dataset.dataset_version_id, event_ats=requested,
            decision_at=decision_at,
        )
        self.assertEqual(written, 11)
        self.assertEqual(self._stored(feature_id, dataset.dataset_version_id), stored_before)

    def test_basis_ambiguity_fails_at_the_same_event_with_the_same_durable_prefix(self) -> None:
        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
        )

        instrument_id = self._register_perpetual("AMBIG", "AMBIGB")
        events = [datetime(2025, 7, 3, 10, tzinfo=UTC) + index * _MINUTE for index in range(6)]
        members: list[UUID] = []
        for index, event_at in enumerate(events):
            members.append(self._price("MARK_PRICE", "AMBIG", f"{100 + index}", event_at))
            members.append(self._price("INDEX_PRICE", "AMBIG", f"{99 + index}", event_at))
        # A second provider identity for the same instrument's INDEX at events[3].
        members.append(
            self._price("INDEX_PRICE", "AMBIG", "98", events[3], identifier="AMBIGB")
        )
        dataset = self._seal("3d7r2-basis-ambiguous", members, events[-1] + timedelta(hours=1))
        decision_at = events[-1] + timedelta(hours=2)
        calculator = PostgresCryptoDerivativesFeatureCalculator(self.database)

        per_event, per_event_error = self._per_event(
            lambda event_at: calculator.materialize_crypto_mark_index_basis(
                feature_id=self.basis_definition.feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_at=event_at,
                decision_at=decision_at,
            ),
            events,
        )
        with self.assertRaisesRegex(ValueError, "ambiguous_index_observation_identity"):
            calculator.materialize_crypto_mark_index_basis_batch(
                feature_id=self.basis_alt.feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_ats=events,
                decision_at=decision_at,
            )
        self.assertEqual(per_event_error, "ambiguous_index_observation_identity")
        self.assertEqual(len(per_event), 3)
        per_event_rows = self._stored(self.basis_definition.feature_id, dataset.dataset_version_id)
        batch_rows = self._stored(self.basis_alt.feature_id, dataset.dataset_version_id)
        self.assertEqual(len(batch_rows), 3)
        self.assertEqual(batch_rows, per_event_rows)

    def test_basis_batch_rejects_unordered_or_naive_event_requests(self) -> None:
        from trade_platform.crypto_derivatives_features import (
            CryptoDerivativesFeatureError,
            PostgresCryptoDerivativesFeatureCalculator,
        )

        calculator = PostgresCryptoDerivativesFeatureCalculator(self.database)
        first = datetime(2025, 7, 5, tzinfo=UTC)
        for event_ats, message in (
            ([first, first], "event_ats_must_be_strictly_increasing"),
            ([first + _MINUTE, first], "event_ats_must_be_strictly_increasing"),
            ([first.replace(tzinfo=None)], "event_at_must_be_timezone_aware"),
        ):
            with self.assertRaisesRegex(CryptoDerivativesFeatureError, message):
                list(
                    calculator.iter_crypto_mark_index_basis(
                        feature_id=uuid4(), instrument_id="TESTFIXTURE:3D7R2:NONE",
                        dataset_version_id=uuid4(), event_ats=event_ats,
                        decision_at=first + timedelta(days=1),
                    )
                )
        # No requested event -> no dataset read, nothing yielded (per-event parity).
        self.assertEqual(
            list(
                calculator.iter_crypto_mark_index_basis(
                    feature_id=uuid4(), instrument_id="TESTFIXTURE:3D7R2:NONE",
                    dataset_version_id=uuid4(), event_ats=[], decision_at=first,
                )
            ),
            [],
        )

    # ---- open_interest_change -----------------------------------------------------

    def test_open_interest_batch_reproduces_per_event_path_exactly(self) -> None:
        from trade_platform.open_interest_features import PostgresOpenInterestFeatureCalculator

        instrument_id = self._register_perpetual("OI")
        start = datetime(2025, 6, 30, 23, 30, tzinfo=UTC)
        grid = [start + index * _FIVE for index in range(16)]  # crosses UTC midnight
        members: list[UUID] = []
        units: dict[int, tuple[str, str | None]] = {5: ("QUOTE_NOTIONAL", "USDT"),
                                                    9: ("CONTRACTS", None),
                                                    12: ("CONTRACTS", None)}
        for index, event_at in enumerate(grid):
            unit, unit_asset = units.get(index, ("BASE_ASSET", "BTC"))
            members.append(self._oi("OI", f"{1000 + index * 7}.125", unit, unit_asset, event_at))
        # Filter-before-rank: at grid[7] the latest revision is QUOTE_NOTIONAL, so
        # the *current* at grid[7] is that revision, while the BASE_ASSET prior
        # for grid[8] is still grid[7]'s revision 0 BASE_ASSET observation.
        members.append(
            self._oi(
                "OI", "55555", "QUOTE_NOTIONAL", "USDT", grid[7], revision=1,
                ingested_at=grid[7] + timedelta(minutes=1),
            )
        )
        dataset = self._seal("3d7r2-oi", members, grid[-1] + timedelta(hours=1))
        decision_at = grid[-1] + timedelta(hours=2)
        # grid[3] and grid[11] are not requested but stay eligible priors.
        requested = [event for index, event in enumerate(grid) if index not in (3, 11)]
        calculator = PostgresOpenInterestFeatureCalculator(self.database)
        feature_id = self.oi_definition.feature_id

        batch, batch_error = self._collect(
            lambda: calculator.iter_open_interest_change(
                feature_id=feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_ats=requested,
                decision_at=decision_at,
            )
        )
        self.assertIsNone(batch_error)
        per_event, per_event_error = self._per_event(
            lambda event_at: calculator.materialize_open_interest_change(
                feature_id=feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_at=event_at,
                decision_at=decision_at,
            ),
            requested,
        )
        self.assertIsNone(per_event_error)
        self.assertEqual([_identity(v) for v in batch], [_identity(v) for v in per_event])

        def prior_of(event_at: datetime) -> str:
            (value,) = [v for v in batch if v.event_at == event_at]
            (token,) = [
                t for t in value.source_observation_manifest if t.startswith("prior_event_at:")
            ]
            return token.removeprefix("prior_event_at:")

        # Unrequested grid[3] is grid[4]'s prior; the first event has none.
        self.assertEqual(prior_of(grid[4]), grid[3].isoformat())
        self.assertNotIn(grid[0], {v.event_at for v in batch})
        # Unit continuity skips the QUOTE_NOTIONAL grid[5] ...
        self.assertEqual(prior_of(grid[6]), grid[4].isoformat())
        # ... and grid[7]'s revision-0 BASE_ASSET row is grid[8]'s prior.
        self.assertEqual(prior_of(grid[8]), grid[7].isoformat())
        (grid8,) = [v for v in batch if v.event_at == grid[8]]
        self.assertIn("prior_revision:0", grid8.source_observation_manifest)
        # grid[7]'s current is its QUOTE_NOTIONAL revision 1, whose prior is grid[5].
        self.assertEqual(prior_of(grid[7]), grid[5].isoformat())
        # NULL unit_asset CONTRACTS pairs across the unrequested grid[11].
        self.assertEqual(prior_of(grid[12]), grid[9].isoformat())
        # Cross-midnight predecessors are the immediately preceding *eligible*
        # observation: 00:00's prior is 23:50 (23:55 is QUOTE_NOTIONAL), and
        # 00:10's prior is 00:05's revision-0 BASE_ASSET row -- no daily reset.
        midnight = datetime(2025, 7, 1, tzinfo=UTC)
        self.assertEqual(grid[6], midnight)
        self.assertEqual(prior_of(midnight), (midnight - 2 * _FIVE).isoformat())
        self.assertEqual(prior_of(grid[9 - 1]), grid[7].isoformat())

        stored_before = self._stored(feature_id, dataset.dataset_version_id)
        written = calculator.materialize_open_interest_change_batch(
            feature_id=feature_id, instrument_id=instrument_id,
            dataset_version_id=dataset.dataset_version_id, event_ats=requested,
            decision_at=decision_at,
        )
        self.assertEqual(written, len(per_event))
        self.assertEqual(self._stored(feature_id, dataset.dataset_version_id), stored_before)

    def test_open_interest_failures_match_per_event_event_and_durable_prefix(self) -> None:
        from trade_platform.open_interest_features import PostgresOpenInterestFeatureCalculator

        instrument_id = self._register_perpetual("OIFAIL", "OIFAILB")
        grid = [datetime(2025, 7, 4, 8, tzinfo=UTC) + index * _FIVE for index in range(8)]
        members = [
            self._oi("OIFAIL", f"{500 + index}", "BASE_ASSET", "BTC", event_at)
            for index, event_at in enumerate(grid)
            if index != 6
        ]
        # A second provider identity at grid[3] makes grid[4]'s prior ambiguous
        # (and grid[3]'s own current ambiguous).
        members.append(
            self._oi("OIFAIL", "499", "BASE_ASSET", "BTC", grid[3], identifier="OIFAILB")
        )
        dataset = self._seal("3d7r2-oi-fail", members, grid[-1] + timedelta(hours=1))
        decision_at = grid[-1] + timedelta(hours=2)
        calculator = PostgresOpenInterestFeatureCalculator(self.database)

        for requested, expected_error, expected_count in (
            (grid[:3] + grid[4:], "ambiguous_prior_observation_identity", 2),
            (grid[:3], None, 2),
            (grid[4:5] + grid[5:7], "ambiguous_prior_observation_identity", 0),
            (grid[:3] + [grid[6]], "current_observation_not_found", 2),
            (grid[:4], "ambiguous_current_observation_identity", 2),
        ):
            batch, batch_error = self._collect(
                lambda requested=requested: calculator.iter_open_interest_change(
                    feature_id=self.oi_definition.feature_id, instrument_id=instrument_id,
                    dataset_version_id=dataset.dataset_version_id, event_ats=requested,
                    decision_at=decision_at,
                )
            )
            per_event, per_event_error = self._per_event(
                lambda event_at: calculator.materialize_open_interest_change(
                    feature_id=self.oi_definition.feature_id, instrument_id=instrument_id,
                    dataset_version_id=dataset.dataset_version_id, event_at=event_at,
                    decision_at=decision_at,
                ),
                requested,
            )
            self.assertEqual(batch_error, expected_error)
            self.assertEqual(per_event_error, expected_error)
            self.assertEqual(len(batch), expected_count)
            self.assertEqual([_identity(v) for v in batch], [_identity(v) for v in per_event])

        # Durable prefix on failure: the batch writer persists exactly what the
        # per-event loop persisted before the failing event, then re-raises.
        with self.assertRaisesRegex(ValueError, "current_observation_not_found"):
            calculator.materialize_open_interest_change_batch(
                feature_id=self.oi_alt.feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_ats=grid[:3] + [grid[6]],
                decision_at=decision_at,
            )
        self.assertEqual(
            self._stored(self.oi_alt.feature_id, dataset.dataset_version_id),
            self._stored(self.oi_definition.feature_id, dataset.dataset_version_id),
        )

    # ---- scaling ---------------------------------------------------------------

    def test_query_and_transaction_count_do_not_scale_per_event(self) -> None:
        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
        )
        from trade_platform.open_interest_features import PostgresOpenInterestFeatureCalculator

        instrument_id = self._register_perpetual("SCALE")
        start = datetime(2025, 7, 6, tzinfo=UTC)
        minutes = [start + index * _MINUTE for index in range(120)]
        members: list[UUID] = []
        for index, event_at in enumerate(minutes):
            members.append(self._price("MARK_PRICE", "SCALE", f"{200 + index}", event_at))
            members.append(self._price("INDEX_PRICE", "SCALE", f"{199 + index}", event_at))
            if index % 5 == 0:
                members.append(self._oi("SCALE", f"{7000 + index}", "BASE_ASSET", "BTC", event_at))
        dataset = self._seal("3d7r2-scale", members, minutes[-1] + timedelta(hours=1))
        decision_at = minutes[-1] + timedelta(hours=2)

        def basis_transactions(event_ats: list[datetime]) -> tuple[int, int]:
            counting = _CountingDatabase(self.database)
            calculator = PostgresCryptoDerivativesFeatureCalculator(cast(Any, counting))
            count = sum(
                1 for _ in calculator.iter_crypto_mark_index_basis(
                    feature_id=self.basis_definition.feature_id, instrument_id=instrument_id,
                    dataset_version_id=dataset.dataset_version_id, event_ats=event_ats,
                    decision_at=decision_at,
                )
            )
            return count, counting.transactions

        def oi_transactions(event_ats: list[datetime]) -> tuple[int, int]:
            counting = _CountingDatabase(self.database)
            calculator = PostgresOpenInterestFeatureCalculator(cast(Any, counting))
            count = sum(
                1 for _ in calculator.iter_open_interest_change(
                    feature_id=self.oi_definition.feature_id, instrument_id=instrument_id,
                    dataset_version_id=dataset.dataset_version_id, event_ats=event_ats,
                    decision_at=decision_at,
                )
            )
            return count, counting.transactions

        small_count, small_transactions = basis_transactions(minutes[:30])
        large_count, large_transactions = basis_transactions(minutes)
        self.assertEqual((small_count, large_count), (30, 120))
        # dataset read + specification read + DECLARE + FETCHes + CLOSE: constant.
        self.assertEqual(small_transactions, large_transactions)
        self.assertLessEqual(large_transactions, 7)
        oi_small = oi_transactions(minutes[:60:5])
        oi_large = oi_transactions(minutes[::5])
        self.assertEqual((oi_small[0], oi_large[0]), (11, 23))
        self.assertEqual(oi_small[1], oi_large[1])

        # Writes: one transaction per bounded chunk, never one per feature.
        from trade_platform.feature_authority import PostgresFeatureAuthority

        values = list(
            PostgresCryptoDerivativesFeatureCalculator(self.database).iter_crypto_mark_index_basis(
                feature_id=self.basis_alt.feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, event_ats=minutes,
                decision_at=decision_at,
            )
        )
        counting = _CountingDatabase(self.database)
        written = PostgresFeatureAuthority(cast(Any, counting)).materialize_subject_stream(
            values, batch_size=50
        )
        self.assertEqual(written, 120)
        self.assertEqual(counting.transactions, 3)  # 50 + 50 + 20
        self.assertEqual(len(self._stored(self.basis_alt.feature_id, dataset.dataset_version_id)), 120)

    # ---- PostgresFeatureAuthority batch contract ------------------------------------

    def test_materialize_subjects_preserves_the_single_row_contract(self) -> None:
        from trade_platform.feature_authority import (
            FEATURE_MATERIALIZATION_BATCH_MAX,
            FeatureAuthorityError,
            FeatureMaterializationV2,
            FeatureQualityStatus,
            FeatureSubjectType,
            PostgresFeatureAuthority,
        )

        instrument_id = self._register_perpetual("AUTH")
        authority = PostgresFeatureAuthority(self.database)
        dataset_version = f"3d7r2-authority-{uuid4()}"
        base = datetime(2025, 7, 8, tzinfo=UTC)

        def make(index: int, value: str = "1.5", subject_id: str = instrument_id) -> Any:
            event_at = base + index * _MINUTE
            return FeatureMaterializationV2.create(
                feature_id=self.basis_alt.feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
                subject_id=subject_id, dataset_version=dataset_version, event_at=event_at,
                effective_at=event_at, knowledge_at=event_at + _MINUTE,
                computed_at=event_at + _MINUTE,
                source_observation_manifest=(f"fixture:{index}",),
                value=Decimal(value).quantize(Decimal("1E-12")),
                quality_status=FeatureQualityStatus.VALIDATED,
            )

        def count() -> int:
            with self.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM feature_materializations WHERE dataset_version=%s",
                    (dataset_version,),
                )
                return int(str(cursor.fetchone()[0]))

        first = [make(index) for index in range(4)]
        authority.materialize_subjects([])
        authority.materialize_subjects(first)
        self.assertEqual(count(), 4)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT hash_version, instrument_id, subject_type, subject_id, "
                "source_observation_manifest, value FROM feature_materializations "
                "WHERE dataset_version=%s AND event_at=%s",
                (dataset_version, base),
            )
            rows = cursor.fetchall()
        self.assertEqual(
            rows,
            [("V2", instrument_id, "INSTRUMENT", instrument_id, ["fixture:0"],
              Decimal("1.500000000000"))],
        )
        # Identical replay (fresh surrogate ids, duplicates inside one chunk) is idempotent.
        authority.materialize_subjects([replace(v, materialization_id=uuid4()) for v in first * 2])
        self.assertEqual(count(), 4)
        # A same-identity/different-hash row fails closed and rolls back its whole chunk.
        with self.assertRaisesRegex(FeatureAuthorityError, "feature_materialization_conflict"):
            authority.materialize_subjects([make(10), make(1, value="2.5")])
        self.assertEqual(count(), 4)
        # Validation runs for every row before anything is written.
        broken = replace(make(11), computed_at=base)
        with self.assertRaisesRegex(FeatureAuthorityError, "feature_computed_before_knowledge"):
            authority.materialize_subjects([make(12), broken])
        self.assertEqual(count(), 4)
        # The deferred subject-existence constraint still applies at COMMIT.
        with self.assertRaisesRegex(FeatureAuthorityError, "feature_materialization_failed"):
            authority.materialize_subjects([make(13), make(14, subject_id="TESTFIXTURE:3D7R2:NOSUCH")])
        self.assertEqual(count(), 4)
        with self.assertRaisesRegex(FeatureAuthorityError, "batch_too_large"):
            authority.materialize_subjects([make(0)] * (FEATURE_MATERIALIZATION_BATCH_MAX + 1))
        for size in (0, FEATURE_MATERIALIZATION_BATCH_MAX + 1):
            with self.assertRaisesRegex(FeatureAuthorityError, "invalid_feature_materialization_batch"):
                authority.materialize_subject_stream([], batch_size=size)

        # A failing source stream: everything produced before the failure is
        # persisted (the per-row loop's durable state), then the error re-raises.
        def failing() -> Iterator[Any]:
            yield make(20)
            yield make(21)
            raise ValueError("source_failed_at_event")

        with self.assertRaisesRegex(ValueError, "source_failed_at_event"):
            authority.materialize_subject_stream(failing(), batch_size=10)
        self.assertEqual(count(), 6)


if __name__ == "__main__":
    unittest.main()
