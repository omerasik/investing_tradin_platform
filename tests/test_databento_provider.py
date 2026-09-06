import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from trade_platform.data_providers import (
    HttpResponse,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderError,
    ProviderHealthRegistry,
    ProviderOperationalStatus,
    RetryPolicy,
)
from trade_platform.databento_provider import DatabentoHistoricalAdapter
from trade_platform.historical_market_data import ObservationKind, normalize_payload
from trade_platform.provider_ingestion import (
    HistoricalIngestionRequest,
    ingest_raw_historical_pages,
)

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
SECRET_ENV_VAR = "DATABENTO_TEST_API_KEY"  # pragma: allowlist secret
API_KEY = "s3cr3t"  # pragma: allowlist secret


def _config(**overrides: object) -> ProviderConfiguration:
    defaults: dict[str, object] = {
        "provider": "databento",
        "base_url": "https://hist.databento.com",
        "terms_accepted": True,
        "secret_reference": f"env:{SECRET_ENV_VAR}",
    }
    defaults.update(overrides)
    return ProviderConfiguration(**defaults)  # type: ignore[arg-type]


def _csv(rows: list[tuple[str, str, str, str, str, str, str, str]]) -> str:
    header = "ts_event,instrument_id,symbol,open,high,low,close,volume"
    return "\n".join([header, *(",".join(row) for row in rows)]) + "\n"


def _submitted(job_id: str) -> HttpResponse:
    return HttpResponse(200, json.dumps({"id": job_id, "state": "queued"}))


def _job_state(job_id: str, state: str) -> HttpResponse:
    return HttpResponse(200, json.dumps([{"id": job_id, "state": state}]))


def _files(job_id: str, url: str) -> HttpResponse:
    return HttpResponse(200, json.dumps([{"filename": f"{job_id}_0.csv", "https_url": url}]))


class ScriptedTransport:
    """Fake DatabentoHttpTransport; never touches a real socket."""

    def __init__(
        self, *, submits: list[HttpResponse], job_states: list[HttpResponse],
        files_by_job: dict[str, HttpResponse], downloads: dict[str, HttpResponse],
        expected_api_key: str = API_KEY,
    ) -> None:
        self._submits = list(submits)
        self._job_states = list(job_states)
        self._files_by_job = files_by_job
        self._downloads = downloads
        self._expected_api_key = expected_api_key
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.gets: list[str] = []

    def post(self, url: str, form: dict[str, str], api_key: str, timeout_seconds: float) -> HttpResponse:
        assert api_key == self._expected_api_key
        self.posts.append((url, form))
        return self._submits.pop(0)

    def get(self, url: str, api_key: str, timeout_seconds: float) -> HttpResponse:
        assert api_key == self._expected_api_key
        self.gets.append(url)
        if "batch.list_jobs" in url:
            return self._job_states.pop(0)
        if "batch.list_files" in url:
            return self._files_by_job[url.split("job_id=")[1]]
        return self._downloads[url]


class DatabentoHistoricalAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ[SECRET_ENV_VAR] = API_KEY
        self.addCleanup(os.environ.pop, SECRET_ENV_VAR, None)

    def _scope(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "dataset": "EQUS.SUMMARY", "schema": "ohlcv-1d",
            "symbols": ["AAPL", "SPY"], "start": "2025-01-01", "end": "2025-02-01",
        }
        base.update(overrides)
        return base

    def test_symbol_limit_is_rejected_before_any_request(self) -> None:
        adapter = DatabentoHistoricalAdapter(_config())
        scope = self._scope(symbols=[f"SYM{i}" for i in range(2001)])
        with self.assertRaisesRegex(ProviderConfigurationError, "databento_symbol_limit_exceeded"):
            adapter.fetch_raw_page(uuid4(), scope, None)

    def test_unsupported_schema_is_rejected(self) -> None:
        adapter = DatabentoHistoricalAdapter(_config())
        with self.assertRaisesRegex(ProviderConfigurationError, "unsupported_databento_schema"):
            adapter.fetch_raw_page(uuid4(), self._scope(schema="trades"), None)

    def test_terms_not_accepted_is_rejected_before_any_request(self) -> None:
        adapter = DatabentoHistoricalAdapter(_config(terms_accepted=False))
        with self.assertRaisesRegex(ProviderConfigurationError, "provider_terms_not_accepted"):
            adapter.fetch_raw_page(uuid4(), self._scope(), None)

    def test_missing_secret_reference_is_rejected_before_any_request(self) -> None:
        adapter = DatabentoHistoricalAdapter(_config(secret_reference=None))
        with self.assertRaisesRegex(ProviderConfigurationError, "databento_secret_reference_required"):
            adapter.fetch_raw_page(uuid4(), self._scope(), None)

    def test_invalid_resume_cursor_is_rejected(self) -> None:
        adapter = DatabentoHistoricalAdapter(_config())
        with self.assertRaisesRegex(ProviderConfigurationError, "invalid_databento_resume_cursor"):
            adapter.fetch_raw_page(uuid4(), self._scope(), "2025-03-01")

    def test_happy_path_single_chunk_submits_polls_downloads_and_normalizes_cleanly(self) -> None:
        source_id = uuid4()
        csv_url = "https://hist.databento.com/files/job-1_0.csv"
        transport = ScriptedTransport(
            submits=[_submitted("job-1")],
            job_states=[_job_state("job-1", "processing"), _job_state("job-1", "done")],
            files_by_job={"job-1": _files("job-1", csv_url)},
            downloads={csv_url: HttpResponse(200, _csv([
                ("2025-01-02T00:00:00Z", "101", "AAPL", "100", "101", "99", "100.5", "1000000"),
            ]))},
        )
        sleeps: list[float] = []
        adapter = DatabentoHistoricalAdapter(
            _config(), transport=transport, now=lambda: NOW, sleep=sleeps.append,
            poll_interval=timedelta(seconds=1),
        )

        page = adapter.fetch_raw_page(source_id, self._scope(end="2025-01-03"), None)

        self.assertIsNone(page.next_cursor)
        self.assertEqual(page.provider_version, "databento-batch-csv-ohlcv-1d-v1")
        self.assertEqual(len(page.records), 1)
        record = page.records[0]
        self.assertEqual((record.provider_identifier, record.provider_symbol, record.source_id), ("101", "AAPL", source_id))
        self.assertEqual(record.event_at, datetime(2025, 1, 2, tzinfo=UTC))
        self.assertEqual(sleeps, [1.0])  # one poll (processing) before DONE
        normalized, issues = normalize_payload(ObservationKind.OHLCV, record.raw_payload)
        self.assertEqual(issues, ())
        self.assertEqual(normalized["interval"], "1d")

    def test_multi_chunk_resumption_never_repeats_a_cursor_and_integrates_with_ingestion(self) -> None:
        source_id = uuid4()
        url_1, url_2 = "https://hist.databento.com/files/job-1_0.csv", "https://hist.databento.com/files/job-2_0.csv"
        transport = ScriptedTransport(
            submits=[_submitted("job-1"), _submitted("job-2")],
            job_states=[_job_state("job-1", "done"), _job_state("job-2", "done")],
            files_by_job={"job-1": _files("job-1", url_1), "job-2": _files("job-2", url_2)},
            downloads={
                url_1: HttpResponse(200, _csv([("2025-01-02T00:00:00Z", "101", "AAPL", "100", "101", "99", "100.5", "10")])),
                url_2: HttpResponse(200, _csv([("2025-02-02T00:00:00Z", "101", "AAPL", "110", "111", "109", "110.5", "20")])),
            },
        )
        adapter = DatabentoHistoricalAdapter(
            _config(), transport=transport, now=lambda: NOW, sleep=lambda _seconds: None,
            chunk_size=timedelta(days=31),
        )
        health = ProviderHealthRegistry(lambda: NOW)
        capture_calls: list[int] = []

        class RecordingSink:
            def capture_raw(self, observations: list[object]) -> tuple[UUID, ...]:
                capture_calls.append(len(observations))
                return tuple(uuid4() for _ in observations)

        result = ingest_raw_historical_pages(
            adapter,
            HistoricalIngestionRequest(source_id, self._scope(start="2025-01-01", end="2025-03-01")),
            RecordingSink(), health, now=lambda: NOW,
        )

        self.assertEqual(result.state, ProviderOperationalStatus.HEALTHY)
        self.assertEqual(capture_calls, [1, 1])
        self.assertEqual(result.checkpoint.captured_count, 2)
        self.assertEqual(len(transport.posts), 2)  # one job submitted per chunk, never re-submitted

    def test_expired_job_raises_provider_error(self) -> None:
        transport = ScriptedTransport(
            submits=[_submitted("job-1")], job_states=[_job_state("job-1", "expired")],
            files_by_job={}, downloads={},
        )
        adapter = DatabentoHistoricalAdapter(_config(), transport=transport, now=lambda: NOW, sleep=lambda _s: None)
        with self.assertRaisesRegex(ProviderError, "databento_batch_job_expired"):
            adapter.fetch_raw_page(uuid4(), self._scope(), None)

    def test_poll_timeout_raises_provider_error(self) -> None:
        transport = ScriptedTransport(
            submits=[_submitted("job-1")],
            job_states=[_job_state("job-1", "processing"), _job_state("job-1", "processing")],
            files_by_job={}, downloads={},
        )
        adapter = DatabentoHistoricalAdapter(
            _config(), transport=transport, now=lambda: NOW, sleep=lambda _s: None, maximum_poll_attempts=2,
        )
        with self.assertRaisesRegex(ProviderError, "databento_batch_job_poll_timeout"):
            adapter.fetch_raw_page(uuid4(), self._scope(), None)

    def test_missing_csv_column_fails_closed(self) -> None:
        csv_url = "https://hist.databento.com/files/job-1_0.csv"
        transport = ScriptedTransport(
            submits=[_submitted("job-1")], job_states=[_job_state("job-1", "done")],
            files_by_job={"job-1": _files("job-1", csv_url)},
            downloads={csv_url: HttpResponse(200, "ts_event,instrument_id,symbol\n2025-01-02T00:00:00Z,101,AAPL\n")},
        )
        adapter = DatabentoHistoricalAdapter(_config(), transport=transport, now=lambda: NOW, sleep=lambda _s: None)
        with self.assertRaisesRegex(ProviderError, "databento_csv_missing_expected_column"):
            adapter.fetch_raw_page(uuid4(), self._scope(), None)

    def test_429_with_retry_after_is_honored_before_succeeding(self) -> None:
        csv_url = "https://hist.databento.com/files/job-1_0.csv"
        transport = ScriptedTransport(
            submits=[_submitted("job-1")], job_states=[_job_state("job-1", "done")],
            files_by_job={"job-1": _files("job-1", csv_url)},
            downloads={csv_url: HttpResponse(200, _csv([
                ("2025-01-02T00:00:00Z", "101", "AAPL", "100", "101", "99", "100.5", "10"),
            ]))},
        )
        real_get = transport.get
        seen_429 = []

        def flaky_get(url: str, api_key: str, timeout_seconds: float) -> HttpResponse:
            if "batch.list_jobs" in url and not seen_429:
                seen_429.append(url)
                return HttpResponse(429, "", {"Retry-After": "3"})
            return real_get(url, api_key, timeout_seconds)

        transport.get = flaky_get  # type: ignore[method-assign]
        sleeps: list[float] = []
        adapter = DatabentoHistoricalAdapter(
            _config(), transport=transport, now=lambda: NOW, sleep=sleeps.append,
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        page = adapter.fetch_raw_page(uuid4(), self._scope(), None)
        self.assertEqual(len(page.records), 1)
        self.assertIn(3.0, sleeps)

    def test_exhausted_retries_raise_a_labeled_provider_error(self) -> None:
        transport = ScriptedTransport(submits=[], job_states=[], files_by_job={}, downloads={})

        def always_fails(url: str, form: dict[str, str], api_key: str, timeout_seconds: float) -> HttpResponse:
            return HttpResponse(503, "")

        transport.post = always_fails  # type: ignore[method-assign]
        adapter = DatabentoHistoricalAdapter(
            _config(), transport=transport, now=lambda: NOW, sleep=lambda _s: None,
            retry_policy=RetryPolicy(maximum_attempts=2, base_delay=timedelta(seconds=0.01)),
        )
        with self.assertRaisesRegex(ProviderError, "databento_http_status:503"):
            adapter.fetch_raw_page(uuid4(), self._scope(), None)


if __name__ == "__main__":
    unittest.main()
