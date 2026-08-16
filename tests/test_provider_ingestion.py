import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from trade_platform.data_providers import ProviderHealthRegistry, ProviderOperationalStatus
from trade_platform.historical_market_data import (
    AdjustmentStatus,
    ObservationKind,
    RawHistoricalObservation,
)
from trade_platform.provider_ingestion import (
    HistoricalIngestionRequest,
    RawHistoricalPage,
    ingest_raw_historical_pages,
)

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def raw(source_id: UUID, sequence: int) -> RawHistoricalObservation:
    event_at = datetime(2025, 1, sequence, 21, tzinfo=UTC)
    return RawHistoricalObservation(
        source_id, ObservationKind.OHLCV, f"fixture-{sequence}", "FIXTURE", "XNAS",
        event_at, event_at, event_at + timedelta(minutes=1), AdjustmentStatus.RAW, 0,
        f"fixture://provider-ingestion/{sequence}",
        {"interval": "1d", "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1"},
    )


class FixtureRawAdapter:
    name = "fixture_raw"

    def __init__(self, pages: list[RawHistoricalPage]) -> None:
        self._pages = list(pages)
        self.calls: list[str | None] = []

    def fetch_raw_page(
        self, source_id: UUID, scope: dict[str, object], cursor: str | None
    ) -> RawHistoricalPage:
        self.calls.append(cursor)
        return self._pages.pop(0)


class RecordingCaptureSink:
    def __init__(self) -> None:
        self.batches: list[list[RawHistoricalObservation]] = []

    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]:
        self.batches.append(observations)
        return tuple(item.raw_observation_id for item in observations)


class FailingRawAdapter:
    name = "failing_fixture"

    def fetch_raw_page(
        self, source_id: UUID, scope: dict[str, object], cursor: str | None
    ) -> RawHistoricalPage:
        from trade_platform.data_providers import ProviderError

        raise ProviderError("fixture_provider_outage")


class ProviderIngestionTests(unittest.TestCase):
    def test_fixture_adapter_captures_pages_and_returns_a_resumable_completed_checkpoint(self) -> None:
        source_id = uuid4()
        adapter = FixtureRawAdapter([
            RawHistoricalPage((raw(source_id, 2),), "page-2", "fixture-v1", NOW),
            RawHistoricalPage((raw(source_id, 3),), None, "fixture-v1", NOW),
        ])
        capture = RecordingCaptureSink()
        health = ProviderHealthRegistry(lambda: NOW)

        result = ingest_raw_historical_pages(
            adapter, HistoricalIngestionRequest(source_id, {"instrument": "US:XNAS:FIXTURE"}),
            capture, health, now=lambda: NOW,
        )

        self.assertEqual(result.state, ProviderOperationalStatus.HEALTHY)
        self.assertEqual(adapter.calls, [None, "page-2"])
        self.assertEqual(result.checkpoint.resume_cursor, None)
        self.assertEqual(result.checkpoint.provider_version, "fixture-v1")
        self.assertEqual(result.checkpoint.captured_count, 2)
        self.assertEqual(len(result.captured_raw_ids), 2)
        self.assertTrue(health.get("fixture_raw").healthy)
        self.assertEqual(sum(len(batch) for batch in capture.batches), 2)

    def test_stale_and_error_are_explicit_and_never_invoke_another_adapter(self) -> None:
        source_id = uuid4()
        stale_adapter = FixtureRawAdapter([
            RawHistoricalPage((raw(source_id, 2),), None, "fixture-v1", NOW - timedelta(hours=25)),
        ])
        health = ProviderHealthRegistry(lambda: NOW)
        stale = ingest_raw_historical_pages(
            stale_adapter,
            HistoricalIngestionRequest(source_id, {"instrument": "US:XNAS:FIXTURE"}, stale_after=timedelta(hours=24)),
            RecordingCaptureSink(), health, now=lambda: NOW,
        )
        self.assertEqual((stale.state, health.get("fixture_raw").operational_status),
                         (ProviderOperationalStatus.STALE, ProviderOperationalStatus.STALE))

        failed = ingest_raw_historical_pages(
            FailingRawAdapter(), HistoricalIngestionRequest(source_id, {"instrument": "US:XNAS:FIXTURE"}),
            RecordingCaptureSink(), health, now=lambda: NOW,
        )
        self.assertEqual(failed.state, ProviderOperationalStatus.ERROR)
        self.assertEqual(failed.checkpoint.error_code, "fixture_provider_outage")
        self.assertEqual(
            health.get("failing_fixture").operational_status, ProviderOperationalStatus.ERROR
        )

    def test_cursor_loop_is_a_visible_error_with_the_last_safe_resume_cursor(self) -> None:
        source_id = uuid4()
        adapter = FixtureRawAdapter([
            RawHistoricalPage((), "loop", "fixture-v1", NOW),
            RawHistoricalPage((), "loop", "fixture-v1", NOW),
        ])
        result = ingest_raw_historical_pages(
            adapter, HistoricalIngestionRequest(source_id, {"instrument": "US:XNAS:FIXTURE"}),
            RecordingCaptureSink(), ProviderHealthRegistry(lambda: NOW), now=lambda: NOW,
        )
        self.assertEqual(result.state, ProviderOperationalStatus.ERROR)
        self.assertEqual(result.checkpoint.error_code, "provider_pagination_cursor_loop")
        self.assertEqual(result.checkpoint.resume_cursor, "loop")


if __name__ == "__main__":
    unittest.main()
