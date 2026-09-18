"""PostgreSQL evidence for Phase 3C scheduled historical acquisition.

Every provider response is a synthetic FIXTURE served by an in-process scripted
transport that answers the real Bybit adapter's request URLs; nothing is
retrieved from Bybit and no socket is opened. The real canonical
``CRYPTO:BYBIT:BTCUSDT:PERP`` is onboarded into a DISPOSABLE database (the
``test_historical_acquisition_postgres`` pattern), then the real
``SchedulerWorker`` drives the scheduled runner through durable job policies,
real advisory locks, real sealing and canonical feature materialization.

Each test uses its own anchor hours apart from the others, so their windows (and
therefore raw/normalized rows) never overlap inside the shared disposable DB.
"""

from __future__ import annotations

import json
import os
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SYMBOL = "BTCUSDT"
ONBOARDED_AT = datetime(2026, 9, 15, tzinfo=UTC)
NOW = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE_MS = 60_000
_FIVE_MINUTES_MS = 300_000


class SyntheticBybitTransport:
    """Deterministic FIXTURE answers for the public-market URLs; never opens a socket."""

    def __init__(self, *, on_request: Callable[[str], None] | None = None) -> None:
        self.urls: list[str] = []
        self.fail_window_starts: set[datetime] = set()
        self._on_request = on_request

    def get(self, url: str, timeout_seconds: float) -> object:
        from trade_platform.data_providers import HttpResponse

        self.urls.append(url)
        if self._on_request is not None:
            self._on_request(url)
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        open_interest = parsed.path.endswith("/open-interest")
        start_ms = int(query["startTime" if open_interest else "start"])
        end_ms = int(query["endTime" if open_interest else "end"])
        if _EPOCH + timedelta(milliseconds=start_ms) in self.fail_window_starts:
            return HttpResponse(400, "fixture provider failure")
        if open_interest:
            rows: list[object] = [
                {"openInterest": f"{460000 + (instant // _FIVE_MINUTES_MS) % 97}.0", "timestamp": str(instant)}
                for instant in range(start_ms, end_ms + 1, _FIVE_MINUTES_MS)
            ]
            result: dict[str, object] = {
                "category": "linear", "symbol": SYMBOL, "list": list(reversed(rows)), "nextPageCursor": "",
            }
        else:
            trade = parsed.path.endswith("/market/kline")
            kline_rows: list[object] = []
            for open_ms in range(start_ms, end_ms + 1, _MINUTE_MS):
                row = [str(open_ms), "27000.0", "27100.0", "26900.0", f"{27000 + (open_ms // _MINUTE_MS) % 89}.0"]
                if trade:
                    row += ["12.5", "337500.0"]
                kline_rows.append(row)
            result = {"category": "linear", "symbol": SYMBOL, "list": list(reversed(kline_rows))}
        return HttpResponse(
            200, json.dumps({"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": end_ms})
        )


def _disposable_dsn(source_dsn: str, database_name: str) -> str:
    return urlunparse(urlparse(source_dsn)._replace(path=f"/{database_name}"))


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ScheduledHistoricalAcquisitionPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str
    source_id: UUID

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest("Phase 3C scheduled acquisition requires a local or CI disposable PostgreSQL DSN")
        cls.database_name = f"scheduled_acquisition_phase3c_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')

        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", cls.dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        old_dsn = os.environ.get("POSTGRES_TEST_DSN")
        try:
            os.environ["POSTGRES_TEST_DSN"] = cls.dsn
            command.upgrade(config, "head")
        finally:
            if old_dsn is not None:
                os.environ["POSTGRES_TEST_DSN"] = old_dsn

        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.persistence import PostgresDatabase

        database = PostgresDatabase(cls.dsn)
        try:
            cls.source_id = onboard_bybit_btcusdt_perpetual_v1(
                database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
            ).source_id
        finally:
            database.close()

    @classmethod
    def tearDownClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    # ---- helpers -----------------------------------------------------------

    def setUp(self) -> None:
        self._databases: list[object] = []

    def tearDown(self) -> None:
        for database in self._databases:
            database.close()  # type: ignore[attr-defined]

    def _database(self):
        from trade_platform.persistence import PostgresDatabase

        database = PostgresDatabase(self.dsn)
        self._databases.append(database)
        return database

    def _authorization(self, name: str, anchor: datetime, *, catchup: int = 3):
        from trade_platform.historical_market_data import ObservationKind
        from trade_platform.scheduled_historical_acquisition_v1 import (
            ScheduledHistoricalAcquisitionAuthorizationV1,
        )

        return ScheduledHistoricalAcquisitionAuthorizationV1(
            authorization_version=f"fixture-{name}-v1",
            job_name=f"scheduled_bybit_acquisition_fixture_{name}",
            job_policy_version="fixture-policy-v1",
            provider="bybit",
            source_id=self.source_id,
            instrument_id="CRYPTO:BYBIT:BTCUSDT:PERP",
            provider_symbol=SYMBOL,
            observation_kinds=frozenset(
                {
                    ObservationKind.OHLCV,
                    ObservationKind.MARK_PRICE,
                    ObservationKind.INDEX_PRICE,
                    ObservationKind.OPEN_INTEREST,
                }
            ),
            normalization_version="bybit-v5-md-phase3c-fixture",
            materialize_features=True,
            window_size=timedelta(minutes=10),
            schedule_anchor=anchor,
            finality_lag=timedelta(minutes=2),
            maximum_pages_per_kind=2,
            maximum_catchup_windows_per_invocation=catchup,
            minimum_request_interval=timedelta(milliseconds=250),
            authorized_by="fixture-operator",
            authorization_reference=f"FIXTURE-{name.upper()}",
            authorized_at=ONBOARDED_AT,
        )

    @staticmethod
    def _configuration():
        from trade_platform.data_providers import ProviderConfiguration

        return ProviderConfiguration(
            provider="bybit",
            base_url="https://api.bybit.com",
            terms_accepted=True,
            secret_reference=None,
            minimum_request_interval=timedelta(milliseconds=250),
        )

    def _context(self, database):
        from trade_platform.data_health import PostgresDataHealthStore
        from trade_platform.operational_alerts import PostgresOperationalAlertStore
        from trade_platform.operational_jobs import PostgresOperationalJobStore
        from trade_platform.postgres_market_data import PostgresHistoricalBarStore
        from trade_platform.retention_evidence import PostgresRetentionEvidenceStore
        from trade_platform.scheduler import JobContext

        alerts = PostgresOperationalAlertStore(database)
        return JobContext(
            database=database,
            job_store=PostgresOperationalJobStore(database, alerts=alerts),
            alerts=alerts,
            retention_store=PostgresRetentionEvidenceStore(database),
            bar_store=PostgresHistoricalBarStore(database),
            data_health_store=PostgresDataHealthStore(database),
        )

    def _approve_policy(
        self,
        context,
        authorization,
        *,
        version: str | None = None,
        enabled: bool = True,
        offset: timedelta = timedelta(0),
    ):
        from trade_platform.operational_jobs import build_job_policy

        return context.job_store.append_policy(
            build_job_policy(
                job_name=authorization.job_name,
                version=version or authorization.job_policy_version,
                interval=timedelta(minutes=5),
                grace=timedelta(minutes=5),
                owner="fixture-operations",
                runbook_uri="runbook:fixture-scheduled-acquisition",
                approved_by="fixture-operator",
                approved_at=ONBOARDED_AT + offset,
                enabled=enabled,
            )
        )

    def _runner(self, database, context, authorization, transport):
        from trade_platform.scheduled_historical_acquisition_v1 import (
            build_postgres_scheduled_acquisition_runner,
        )

        return build_postgres_scheduled_acquisition_runner(
            database,
            context.job_store,
            authorization,
            self._configuration(),
            transport=transport,
            sleep=lambda _seconds: None,
            now=lambda: NOW,
        )

    def _sealed_versions(self, database, authorization) -> set[str]:
        from trade_platform.scheduled_historical_acquisition_v1 import (
            scheduled_dataset_version_prefix,
        )

        prefix = scheduled_dataset_version_prefix(authorization)
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM historical_dataset_versions WHERE source_id=%s AND left(version,%s)=%s",
                (self.source_id, len(prefix), prefix),
            )
            return {str(row[0]) for row in cursor.fetchall()}

    # ---- tests -------------------------------------------------------------

    def test_scheduler_ticks_bounded_catch_up_failure_boundary_and_identical_retry(self) -> None:
        from trade_platform.scheduled_historical_acquisition_v1 import (
            ScheduledAcquisitionOutcome,
            plan_scheduled_windows,
            scheduled_dataset_version,
        )
        from trade_platform.scheduler import SchedulerWorker

        anchor = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
        database = self._database()
        context = self._context(database)
        authorization = self._authorization("ticks", anchor)
        transport = SyntheticBybitTransport()
        runner = self._runner(database, context, authorization, transport)
        clock = {"now": anchor + timedelta(minutes=52)}
        worker = SchedulerWorker(
            context=context,
            registry={authorization.job_name: runner.as_job_runner()},
            clock=lambda: clock["now"],
        )

        # Gate 1 alone (runner composed) without gate 2 (durable policy): never runs.
        self.assertEqual(worker.run_tick(clock["now"]), ())
        # A disabled durable policy is not execution authority either.
        self._approve_policy(context, authorization, version="fixture-policy-draft", enabled=False)
        self.assertEqual(worker.run_tick(clock["now"]), ())
        self.assertEqual(transport.urls, [])
        # Enabled, but not the policy version this authorization binds: FAILED, zero calls.
        self._approve_policy(
            context, authorization, version="fixture-policy-unauthorized", offset=timedelta(seconds=1)
        )
        runs = worker.run_tick(clock["now"])
        self.assertEqual([run.status.value for run in runs], ["FAILED"])
        self.assertEqual(runs[0].summary["outcome"], "NOT_AUTHORIZED")
        self.assertEqual(runs[0].summary["error"], "operational_job_policy_version_not_authorized")
        self.assertEqual(transport.urls, [])
        # Operator approves exactly the authorized version: both gates now hold.
        self._approve_policy(context, authorization, offset=timedelta(seconds=2))
        clock["now"] += timedelta(minutes=5)
        # ---- tick 1: bounded catch-up (5 eligible, bound 3) -> SUCCEEDED with backlog.
        runs = worker.run_tick(clock["now"])
        self.assertEqual([run.status.value for run in runs], ["SUCCEEDED"])
        summary = runs[0].summary
        self.assertEqual(summary["outcome"], ScheduledAcquisitionOutcome.BACKLOG_REMAINS.value)
        self.assertEqual(summary["windows_eligible"], "5")
        self.assertEqual(summary["windows_acquired"], "3")
        self.assertEqual(summary["backlog_remaining"], "2")
        self.assertEqual(summary["backlog_remains"], "true")
        self.assertEqual(summary["feature_counts"].split(","), ["basis=10;open_interest_change=1"] * 3)
        self.assertEqual(len(transport.urls), 12)
        windows = plan_scheduled_windows(authorization, clock["now"], ()).pending
        self.assertEqual(
            self._sealed_versions(database, authorization),
            {scheduled_dataset_version(authorization, window) for window in windows},
        )

        # ---- tick 2 (next due): W3 succeeds, W4 fails -> FAILED run, failure boundary visible.
        transport.fail_window_starts.add(anchor + timedelta(minutes=40))
        clock["now"] += timedelta(minutes=5)
        runs = worker.run_tick(clock["now"])
        self.assertEqual([run.status.value for run in runs], ["FAILED"])
        failed_summary = runs[0].summary
        self.assertEqual(failed_summary["outcome"], "WINDOW_FAILED")
        self.assertEqual(failed_summary["windows_acquired"], "1")
        self.assertEqual(
            failed_summary["first_failed_window"],
            f"[{(anchor + timedelta(minutes=40)).isoformat()},{(anchor + timedelta(minutes=50)).isoformat()})",
        )
        self.assertEqual(failed_summary["first_failed_status"], "PROVIDER_FAILED")
        failed_fingerprint = failed_summary["acquisition_fingerprints"].split(",")[-1]
        self.assertTrue(
            any(
                alert.code == "OPERATIONAL_JOB_EXECUTION_FAILED"
                and alert.resource == f"job:{authorization.job_name}"
                for alert in context.alerts.active()
            )
        )
        self.assertEqual(len(self._sealed_versions(database, authorization)), 4)

        # ---- tick 3: still due (FAILED never advances due-state); retries exactly W4.
        transport.fail_window_starts.clear()
        clock["now"] += timedelta(minutes=1)
        requests_before = len(transport.urls)
        runs = worker.run_tick(clock["now"])
        self.assertEqual([run.status.value for run in runs], ["SUCCEEDED"])
        self.assertEqual(runs[0].summary["outcome"], "UP_TO_DATE")
        # The cutoff moved to minute 61, so W5 [50,60) is now eligible too -- but it
        # is acquired only AFTER the failed W4 is retried with its identical identity.
        retried_fingerprint, newer_fingerprint = runs[0].summary["acquisition_fingerprints"].split(",")
        self.assertEqual(retried_fingerprint, failed_fingerprint)
        self.assertNotEqual(newer_fingerprint, failed_fingerprint)
        self.assertEqual(
            runs[0].summary["windows_considered"],
            f"[{(anchor + timedelta(minutes=40)).isoformat()},{(anchor + timedelta(minutes=50)).isoformat()}),"
            f"[{(anchor + timedelta(minutes=50)).isoformat()},{(anchor + timedelta(minutes=60)).isoformat()})",
        )
        self.assertEqual(len(transport.urls) - requests_before, 8)
        self.assertEqual(len(self._sealed_versions(database, authorization)), 6)

        # ---- completed windows replay with zero provider fetches.
        requests_before = len(transport.urls)
        replay = runner.run(clock["now"])
        self.assertEqual(replay.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(replay.windows_considered, ())
        self.assertEqual(replay.windows_completed_before, 6)
        self.assertEqual(len(transport.urls), requests_before)

    def test_two_workers_never_both_acquire_the_same_window(self) -> None:
        from trade_platform.scheduled_historical_acquisition_v1 import ScheduledAcquisitionOutcome

        anchor = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)
        as_of = anchor + timedelta(minutes=12)  # exactly one eligible window
        authorization = self._authorization("concurrency", anchor)

        database_a = self._database()
        context_a = self._context(database_a)
        self._approve_policy(context_a, authorization)
        database_b = self._database()
        context_b = self._context(database_b)

        transport_b = SyntheticBybitTransport()
        runner_b = self._runner(database_b, context_b, authorization, transport_b)
        observed: list[object] = []

        def worker_b_attempts_same_window(_url: str) -> None:
            if not observed:
                # Worker A is mid-acquisition of W0 and holds its window lock on its
                # own PostgreSQL session; worker B (a separate session) tries now.
                observed.append(runner_b.run(as_of))

        transport_a = SyntheticBybitTransport(on_request=worker_b_attempts_same_window)
        runner_a = self._runner(database_a, context_a, authorization, transport_a)

        result_a = runner_a.run(as_of)
        self.assertEqual(result_a.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(len(result_a.window_outcomes), 1)
        self.assertFalse(result_a.window_outcomes[0].already_completed)
        self.assertEqual(len(transport_a.urls), 4)

        result_b = observed[0]
        self.assertEqual(result_b.outcome, ScheduledAcquisitionOutcome.WINDOW_CONTENDED)  # type: ignore[attr-defined]
        self.assertEqual(result_b.contended_window, result_a.window_outcomes[0].window)  # type: ignore[attr-defined]
        self.assertEqual(transport_b.urls, [])

        # Once A released the lock, B observes the sealed window: zero provider calls.
        later_b = runner_b.run(as_of)
        self.assertEqual(later_b.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(later_b.windows_completed_before, 1)
        self.assertEqual(transport_b.urls, [])
        self.assertEqual(len(self._sealed_versions(database_a, authorization)), 1)

    def test_restart_retries_the_failed_window_with_identical_identity(self) -> None:
        from trade_platform.historical_acquisition import AcquisitionStatus
        from trade_platform.scheduled_historical_acquisition_v1 import (
            ScheduledAcquisitionOutcome,
            build_scheduled_request,
        )

        anchor = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        as_of = anchor + timedelta(minutes=32)  # W0, W1, W2
        authorization = self._authorization("restart", anchor)

        first_database = self._database()
        first_context = self._context(first_database)
        self._approve_policy(first_context, authorization)
        first_transport = SyntheticBybitTransport()
        first_transport.fail_window_starts.add(anchor + timedelta(minutes=10))
        first = self._runner(first_database, first_context, authorization, first_transport).run(as_of)
        self.assertEqual(first.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
        failed = first.first_failed_window
        assert failed is not None
        self.assertEqual(failed.status, AcquisitionStatus.PROVIDER_FAILED)
        # Gap-preserving: W2 was never requested and nothing was sealed past W1.
        self.assertFalse(any(str(_ms(anchor + timedelta(minutes=20))) in url for url in first_transport.urls))
        self.assertEqual(len(self._sealed_versions(first_database, authorization)), 1)
        first_database.close()
        self._databases.remove(first_database)

        # "Restart": a brand-new process/session with a fresh runner instance.
        second_database = self._database()
        second_context = self._context(second_database)
        second_transport = SyntheticBybitTransport()
        second = self._runner(second_database, second_context, authorization, second_transport).run(as_of)
        self.assertEqual(second.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(second.windows_considered, first.windows_considered[1:])
        retried = second.window_outcomes[0]
        self.assertEqual(retried.window, failed.window)
        self.assertEqual(retried.dataset_version, failed.dataset_version)
        self.assertEqual(retried.acquisition_fingerprint, failed.acquisition_fingerprint)
        self.assertEqual(
            retried.acquisition_fingerprint, build_scheduled_request(authorization, failed.window).idempotency_key
        )
        self.assertEqual(len(self._sealed_versions(second_database, authorization)), 3)
        self.assertEqual(len(second_transport.urls), 8)


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


if __name__ == "__main__":
    unittest.main()
