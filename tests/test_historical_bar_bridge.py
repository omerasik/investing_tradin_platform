import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.historical_bar_bridge import HistoricalBarBridgeError, normalized_ohlcv_to_bar
from trade_platform.historical_market_data import (
    AdjustmentStatus,
    NormalizedHistoricalObservation,
    ObservationKind,
    QualityStatus,
    RawHistoricalObservation,
)
from trade_platform.market_data import (
    DataQualityError,
    PrecomputedBarProvider,
    SQLiteBarStore,
    ingest_from_provider,
)

EVENT = datetime(2025, 1, 2, tzinfo=UTC)
NORMALIZED_AT = EVENT + timedelta(hours=1)


def _raw(
    source_id, *, kind: ObservationKind = ObservationKind.OHLCV, event_at: datetime = EVENT,
) -> RawHistoricalObservation:
    payload: dict[str, object] = (
        {"interval": "1d", "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": "1000000"}
        if kind is ObservationKind.OHLCV
        else {"amount": "0.24", "currency": "USD"}
    )
    return RawHistoricalObservation(
        source_id, kind, "101", "AAPL", "CONSOLIDATED_TAPE", event_at, event_at,
        event_at + timedelta(minutes=1), AdjustmentStatus.RAW, 2, "databento://batch/ohlcv-1d", payload,
    )


def _normalized(
    raw: RawHistoricalObservation, *, instrument_id: str = "US:XNAS:AAPL",
    quality_status: QualityStatus = QualityStatus.VALIDATED,
    normalized_value: dict[str, object] | None = None,
) -> NormalizedHistoricalObservation:
    default_value = {"interval": "1d", "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": "1000000"}
    return NormalizedHistoricalObservation(
        uuid4(), raw.raw_observation_id, instrument_id, "databento-normalizer-v1",
        normalized_value if normalized_value is not None else default_value,
        quality_status, (), NORMALIZED_AT,
    )


class NormalizedOhlcvToBarTests(unittest.TestCase):
    def test_daily_translation_preserves_identity_provenance_and_precision(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(raw)

        bar = normalized_ohlcv_to_bar(normalized, raw, "databento")

        self.assertEqual(bar.instrument_id, "US:XNAS:AAPL")
        self.assertEqual(bar.interval, "1d")
        self.assertEqual(bar.provider, "databento")
        self.assertEqual(bar.source_identifier, str(raw.raw_observation_id))
        self.assertEqual((bar.event_at, bar.effective_at), (raw.event_at, raw.effective_at))
        self.assertEqual(bar.ingested_at, normalized.normalized_at)  # normalization time, not capture time
        self.assertEqual((bar.open, bar.high, bar.low, bar.close, bar.volume),
                         (Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("1000000")))
        self.assertEqual(bar.original_timezone, "UTC")
        self.assertEqual(bar.revision, raw.revision)
        self.assertEqual(bar.data_version, normalized.normalization_version)

    def test_minute_translation(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(
            raw, normalized_value={"interval": "1m", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10"},
        )
        bar = normalized_ohlcv_to_bar(normalized, raw, "databento")
        self.assertEqual(bar.interval, "1m")

    def test_rejected_observation_is_refused(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(raw, quality_status=QualityStatus.REJECTED)
        with self.assertRaisesRegex(HistoricalBarBridgeError, "rejected_normalized_observation_cannot_become_a_bar"):
            normalized_ohlcv_to_bar(normalized, raw, "databento")

    def test_mismatched_raw_observation_is_refused(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        other_raw = _raw(source_id)  # different raw_observation_id (default_factory=uuid4)
        normalized = _normalized(raw)
        with self.assertRaisesRegex(HistoricalBarBridgeError, "normalized_and_raw_observation_mismatch"):
            normalized_ohlcv_to_bar(normalized, other_raw, "databento")

    def test_non_ohlcv_observation_kind_is_refused(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id, kind=ObservationKind.DIVIDEND)
        normalized = _normalized(raw, normalized_value={"amount": "0.24", "currency": "USD"})
        with self.assertRaisesRegex(HistoricalBarBridgeError, "bar_bridge_requires_ohlcv_observation_kind"):
            normalized_ohlcv_to_bar(normalized, raw, "databento")

    def test_unsupported_interval_is_refused(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(
            raw, normalized_value={"interval": "5m", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10"},
        )
        with self.assertRaisesRegex(HistoricalBarBridgeError, "unsupported_bar_bridge_interval"):
            normalized_ohlcv_to_bar(normalized, raw, "databento")

    def test_malformed_normalized_price_is_refused(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(
            raw, normalized_value={"interval": "1d", "open": None, "high": "101", "low": "99", "close": "100.5", "volume": "1"},
        )
        with self.assertRaisesRegex(HistoricalBarBridgeError, "normalized_ohlcv_missing_open"):
            normalized_ohlcv_to_bar(normalized, raw, "databento")

    def test_empty_provider_is_refused(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(raw)
        with self.assertRaisesRegex(HistoricalBarBridgeError, "bar_bridge_requires_a_provider_name"):
            normalized_ohlcv_to_bar(normalized, raw, "")


class PrecomputedBarProviderIngestionTests(unittest.TestCase):
    def test_validated_bar_reaches_the_bar_store_through_ingest_from_provider(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(raw)
        bar = normalized_ohlcv_to_bar(normalized, raw, "databento")
        provider = PrecomputedBarProvider("databento", [bar])
        store = SQLiteBarStore()

        digest = ingest_from_provider(provider, store, bar.instrument_id, bar.interval, EVENT - timedelta(days=1), EVENT + timedelta(days=1))

        self.assertTrue(digest)
        stored = store.read_range(bar.instrument_id, bar.interval, EVENT - timedelta(days=1), EVENT + timedelta(days=1))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].close, Decimal("100.5"))

    def test_provider_mismatch_is_rejected_before_reaching_the_store(self) -> None:
        source_id = uuid4()
        raw = _raw(source_id)
        normalized = _normalized(raw)
        bar = normalized_ohlcv_to_bar(normalized, raw, "some-other-provider")  # bridge output tagged wrong
        provider = PrecomputedBarProvider("databento", [bar])  # provider object claims "databento"
        store = SQLiteBarStore()

        with self.assertRaisesRegex(DataQualityError, "provider_provenance_mismatch"):
            ingest_from_provider(provider, store, bar.instrument_id, bar.interval, EVENT - timedelta(days=1), EVENT + timedelta(days=1))
        self.assertEqual(store.known_series(), [])

    def test_precomputed_bar_provider_requires_a_name(self) -> None:
        with self.assertRaisesRegex(DataQualityError, "precomputed_bar_provider_requires_a_name"):
            PrecomputedBarProvider("", [])

    # Deterministic idempotency-on-replay and conflicting-duplicate rejection are
    # PostgresHistoricalBarStore-specific guarantees (its idempotent-or-reject
    # content-hash comparison, Module 3F) -- SQLiteBarStore does a plain INSERT with
    # no such handling. Those two acceptance scenarios are covered against the real
    # PostgresHistoricalBarStore in test_historical_bar_bridge_postgres.py instead of
    # being asserted here against a store that does not make that guarantee.


if __name__ == "__main__":
    unittest.main()
