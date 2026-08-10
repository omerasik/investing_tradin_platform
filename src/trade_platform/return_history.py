"""Durable, provider-attributed portfolio return observations for historical risk limits."""

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .broker_adapter import BrokerConfiguration
from .data_providers import HttpTransport, ProviderConfiguration, UrlLibTransport


class ReturnHistoryError(ValueError):
    pass


class ReturnProviderTransientError(ReturnHistoryError):
    """A safely retryable provider-side outage, throttling response, or timeout."""


@dataclass(frozen=True, slots=True)
class ReturnIngestionCadence:
    account_id: str
    provider: str
    interval: timedelta
    grace: timedelta
    approved_by: str
    updated_at: datetime

    def validate(self) -> None:
        if (not self.account_id.strip() or not self.provider.strip() or not self.approved_by.strip()
                or self.interval <= timedelta(0) or self.grace < timedelta(0)
                or self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None):
            raise ReturnHistoryError("invalid_return_ingestion_cadence")


@dataclass(frozen=True, slots=True)
class ReturnIngestionRetryPolicy:
    maximum_attempts: int = 3
    base_delay: timedelta = timedelta(seconds=1)

    def validate(self) -> None:
        if self.maximum_attempts < 1 or self.base_delay < timedelta(0):
            raise ReturnHistoryError("invalid_return_ingestion_retry_policy")


class PortfolioReturnProvider(Protocol):
    """Provider boundary for account-performance return ingestion."""

    provider_name: str

    def fetch_returns(self, account_id: str, *, start: datetime, end: datetime) -> list["PortfolioReturnObservation"]: ...


@dataclass(frozen=True, slots=True)
class PortfolioReturnPage:
    observations: tuple["PortfolioReturnObservation", ...]
    next_cursor: str | None


class PaginatedPortfolioReturnProvider(Protocol):
    provider_name: str

    def fetch_return_page(self, account_id: str, *, start: datetime, end: datetime, cursor: str | None) -> PortfolioReturnPage: ...


@dataclass(frozen=True, slots=True)
class ReturnProviderCapabilities:
    supports_account_performance: bool
    supports_pagination: bool
    network_connected: bool
    maximum_observations_per_request: int | None = None
    rate_limit_per_minute: int | None = None


@dataclass(frozen=True, slots=True)
class AccountPerformanceSnapshot:
    """Provider-attributed account NAV mark used to derive a period return."""

    account_id: str
    observed_at: datetime
    equity: Decimal
    source_reference: str
    ingested_at: datetime

    def validate(self) -> None:
        if (not self.account_id.strip() or not self.source_reference.strip() or self.equity <= 0
                or not self.equity.is_finite() or any(value.tzinfo is None or value.utcoffset() is None for value in (self.observed_at, self.ingested_at))
                or self.ingested_at < self.observed_at):
            raise ReturnHistoryError("invalid_account_performance_snapshot")


class SandboxAccountPerformanceProvider:
    """Paper-only account-performance adapter with explicit, attributable NAV marks.

    A sandbox cannot infer an account's mark-to-market performance from fills alone,
    so callers record sourced NAV snapshots. The adapter deterministically derives
    return periods from consecutive marks and never opens a network connection.
    """

    capabilities = ReturnProviderCapabilities(True, False, False)

    def __init__(self, configuration: BrokerConfiguration) -> None:
        configuration.validate()
        self.configuration = configuration
        self.provider_name = f"sandbox-performance:{configuration.broker_name}"
        self._snapshots: dict[str, list[AccountPerformanceSnapshot]] = {}

    def health(self) -> bool:
        return True

    def record_snapshot(self, snapshot: AccountPerformanceSnapshot) -> None:
        snapshot.validate()
        if snapshot.account_id != self.configuration.account_id:
            raise ReturnHistoryError("sandbox_performance_account_mismatch")
        current = self._snapshots.setdefault(snapshot.account_id, [])
        if any(item.source_reference == snapshot.source_reference for item in current):
            raise ReturnHistoryError("duplicate_account_performance_snapshot")
        current.append(snapshot)

    def fetch_returns(self, account_id: str, *, start: datetime, end: datetime) -> list["PortfolioReturnObservation"]:
        if (account_id != self.configuration.account_id or start.tzinfo is None or start.utcoffset() is None
                or end.tzinfo is None or end.utcoffset() is None or start >= end):
            raise ReturnHistoryError("invalid_sandbox_performance_request")
        snapshots = sorted((item for item in self._snapshots.get(account_id, ()) if start <= item.observed_at <= end), key=lambda item: (item.observed_at, item.ingested_at, item.source_reference))
        if not snapshots or snapshots[0].observed_at != start:
            raise ReturnHistoryError("sandbox_performance_start_snapshot_required")
        observations: list[PortfolioReturnObservation] = []
        previous = snapshots[0]
        for current in snapshots[1:]:
            if current.observed_at <= previous.observed_at:
                raise ReturnHistoryError("non_increasing_account_performance_time")
            reference = f"{previous.source_reference}->{current.source_reference}"
            observation_id = uuid5(NAMESPACE_URL, f"{self.provider_name}:{account_id}:{previous.observed_at.isoformat()}:{current.observed_at.isoformat()}:{reference}")
            observations.append(PortfolioReturnObservation(
                observation_id, account_id, previous.observed_at, current.observed_at,
                current.equity / previous.equity - Decimal("1"), self.provider_name, reference,
                max(current.ingested_at, current.observed_at),
            ))
            previous = current
        return observations


class HttpsAccountPerformanceProvider:
    """Credential-reference-gated HTTPS read adapter for a broker performance API.

    The endpoint contract is paged JSON with an ``observations`` array containing
    ``period_start``, ``period_end``, ``return_fraction``, ``source_reference``
    and ``ingested_at`` ISO-8601 fields, plus an optional ``next_cursor``.
    Credential material is deliberately not accepted or retained by this adapter.
    """

    capabilities = ReturnProviderCapabilities(True, True, True)

    def __init__(self, configuration: ProviderConfiguration, *, transport: HttpTransport | None = None) -> None:
        configuration.validate()
        if configuration.secret_reference is None or not configuration.secret_reference.startswith("env:"):
            raise ReturnHistoryError("account_performance_credential_reference_required")
        self._configuration = configuration
        self.provider_name = configuration.provider
        self._transport = transport or UrlLibTransport()

    def fetch_returns(self, account_id: str, *, start: datetime, end: datetime) -> list["PortfolioReturnObservation"]:
        return list(self.fetch_return_page(account_id, start=start, end=end, cursor=None).observations)

    def fetch_return_page(self, account_id: str, *, start: datetime, end: datetime, cursor: str | None) -> PortfolioReturnPage:
        if not account_id.strip() or start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ReturnHistoryError("invalid_https_account_performance_request")
        query = urlencode({"start": start.isoformat(), "end": end.isoformat(), **({"cursor": cursor} if cursor else {})})
        response = self._transport.get(f"{self._configuration.base_url.rstrip('/')}/v1/accounts/{account_id}/returns?{query}", self._configuration.request_timeout_seconds)
        if response.status_code in {429, 500, 502, 503, 504}:
            raise ReturnProviderTransientError(f"account_performance_http_status:{response.status_code}")
        if response.status_code != 200:
            raise ReturnHistoryError(f"account_performance_http_status:{response.status_code}")
        try:
            payload = json.loads(response.body)
            rows = payload["observations"]
            observations = tuple(PortfolioReturnObservation(
                UUID(str(item["observation_id"])), account_id,
                datetime.fromisoformat(str(item["period_start"])), datetime.fromisoformat(str(item["period_end"])),
                Decimal(str(item["return_fraction"])), self.provider_name, str(item["source_reference"]),
                datetime.fromisoformat(str(item["ingested_at"])),
            ) for item in rows)
            for item in observations:
                item.validate()
            next_cursor = payload.get("next_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise ValueError("invalid_cursor")
            return PortfolioReturnPage(observations, next_cursor)
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise ReturnHistoryError("invalid_account_performance_response") from error


@dataclass(frozen=True, slots=True)
class PortfolioReturnObservation:
    observation_id: UUID
    account_id: str
    period_start: datetime
    period_end: datetime
    return_fraction: Decimal
    provider: str
    source_reference: str
    ingested_at: datetime

    def validate(self) -> None:
        timestamps = (self.period_start, self.period_end, self.ingested_at)
        if not self.account_id.strip() or not self.provider.strip() or not self.source_reference.strip() or any(value.tzinfo is None or value.utcoffset() is None for value in timestamps) or self.period_start >= self.period_end or self.ingested_at < self.period_end or not self.return_fraction.is_finite() or self.return_fraction <= Decimal("-1"):
            raise ReturnHistoryError("invalid_portfolio_return_observation")


@dataclass(frozen=True, slots=True)
class ReturnHistoryEvidence:
    """The exact immutable observation window used for a historical-risk decision."""

    observations: tuple[PortfolioReturnObservation, ...]

    def __post_init__(self) -> None:
        if not self.observations:
            raise ReturnHistoryError("empty_return_history_evidence")

    @property
    def returns(self) -> list[Decimal]:
        return [item.return_fraction for item in self.observations]

    @property
    def observed_at(self) -> datetime:
        return self.observations[-1].period_end

    @property
    def reference(self) -> str:
        """Stable fingerprint including every field that changes a selected return."""
        rows = [
            {
                "observation_id": str(item.observation_id), "account_id": item.account_id,
                "period_start": item.period_start.isoformat(), "period_end": item.period_end.isoformat(),
                "return_fraction": str(item.return_fraction), "provider": item.provider,
                "source_reference": item.source_reference, "ingested_at": item.ingested_at.isoformat(),
            }
            for item in self.observations
        ]
        return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SQLitePortfolioReturnStore:
    """Append-only returns; as-of reads exclude future periods and late ingestion."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS portfolio_return_observations (
                observation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, period_start TEXT NOT NULL,
                period_end TEXT NOT NULL, return_fraction TEXT NOT NULL, provider TEXT NOT NULL,
                source_reference TEXT NOT NULL, ingested_at TEXT NOT NULL,
                UNIQUE(account_id, provider, source_reference))"""
        )
        self._connection.execute("""CREATE TABLE IF NOT EXISTS portfolio_return_ingestion_runs (
            run_id TEXT PRIMARY KEY, provider TEXT NOT NULL, account_id TEXT NOT NULL, start_at TEXT NOT NULL,
            end_at TEXT NOT NULL, requested_at TEXT NOT NULL, completed_at TEXT NOT NULL, status TEXT NOT NULL,
            observation_count INTEGER NOT NULL, error TEXT)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS portfolio_return_provider_health (
            provider TEXT PRIMARY KEY, checked_at TEXT NOT NULL, healthy INTEGER NOT NULL, reason TEXT,
            consecutive_failures INTEGER NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS portfolio_return_ingestion_commands (
            idempotency_key TEXT PRIMARY KEY, actor TEXT NOT NULL, provider TEXT NOT NULL, account_id TEXT NOT NULL,
            start_at TEXT NOT NULL, end_at TEXT NOT NULL, status TEXT NOT NULL, observation_count INTEGER,
            error TEXT, requested_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS portfolio_return_ingestion_cadence (
            account_id TEXT NOT NULL, provider TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
            grace_seconds INTEGER NOT NULL, approved_by TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(account_id, provider))""")
        self._connection.commit()

    def append(self, observation: PortfolioReturnObservation) -> None:
        observation.validate()
        try:
            self._connection.execute(
                "INSERT INTO portfolio_return_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(observation.observation_id), observation.account_id, observation.period_start.isoformat(),
                 observation.period_end.isoformat(), str(observation.return_fraction), observation.provider,
                 observation.source_reference, observation.ingested_at.isoformat()),
            )
        except sqlite3.IntegrityError as error:
            raise ReturnHistoryError("duplicate_portfolio_return_observation") from error
        self._connection.commit()

    def returns_as_of(self, account_id: str, decision_at: datetime) -> list[Decimal]:
        return [item.return_fraction for item in self.observations_as_of(account_id, decision_at)]

    def observations_as_of(self, account_id: str, decision_at: datetime) -> list[PortfolioReturnObservation]:
        if not account_id.strip() or decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ReturnHistoryError("invalid_return_history_query")
        rows = self._connection.execute(
            "SELECT * FROM portfolio_return_observations WHERE account_id = ? AND period_end <= ? AND ingested_at <= ? ORDER BY period_end, ingested_at, rowid",
            (account_id, decision_at.isoformat(), decision_at.isoformat()),
        ).fetchall()
        return [PortfolioReturnObservation(UUID(row[0]), row[1], datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]), Decimal(row[4]), row[5], row[6], datetime.fromisoformat(row[7])) for row in rows]

    def returns_for_policy(self, account_id: str, decision_at: datetime, *, minimum_observations: int, window_observations: int, maximum_age_seconds: int) -> list[Decimal]:
        return self.evidence_for_policy(
            account_id, decision_at, minimum_observations=minimum_observations,
            window_observations=window_observations, maximum_age_seconds=maximum_age_seconds,
        ).returns

    def evidence_for_policy(self, account_id: str, decision_at: datetime, *, minimum_observations: int, window_observations: int, maximum_age_seconds: int) -> ReturnHistoryEvidence:
        if minimum_observations < 1 or window_observations < minimum_observations or maximum_age_seconds < 0:
            raise ReturnHistoryError("invalid_return_history_policy_requirements")
        observations = self.observations_as_of(account_id, decision_at)
        if len(observations) < window_observations:
            raise ReturnHistoryError("insufficient_return_history_window")
        selected = observations[-window_observations:]
        if (decision_at - selected[-1].period_end).total_seconds() > maximum_age_seconds:
            raise ReturnHistoryError("stale_return_history")
        return ReturnHistoryEvidence(tuple(selected))

    def provider_health(self, provider: str) -> tuple[datetime, bool, str | None, int] | None:
        row = self._connection.execute("SELECT checked_at, healthy, reason, consecutive_failures FROM portfolio_return_provider_health WHERE provider = ?", (provider,)).fetchone()
        return None if row is None else (datetime.fromisoformat(row[0]), bool(row[1]), row[2], row[3])

    def ingestion_runs(self, account_id: str) -> list[tuple[UUID, str, datetime, datetime, str, int, str | None]]:
        rows = self._connection.execute("SELECT run_id, provider, start_at, end_at, status, observation_count, error FROM portfolio_return_ingestion_runs WHERE account_id = ? ORDER BY requested_at, rowid", (account_id,)).fetchall()
        return [(UUID(row[0]), row[1], datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3]), row[4], row[5], row[6]) for row in rows]

    def ingestion_commands(self, account_id: str) -> list[tuple[str, str, str, datetime, datetime, str, int | None, str | None, datetime]]:
        rows = self._connection.execute("SELECT idempotency_key, actor, provider, start_at, end_at, status, observation_count, error, requested_at FROM portfolio_return_ingestion_commands WHERE account_id = ? ORDER BY requested_at DESC, rowid DESC", (account_id,)).fetchall()
        return [(row[0], row[1], row[2], datetime.fromisoformat(row[3]), datetime.fromisoformat(row[4]), row[5], row[6], row[7], datetime.fromisoformat(row[8])) for row in rows]

    def set_ingestion_cadence(self, cadence: ReturnIngestionCadence) -> None:
        cadence.validate()
        self._connection.execute("INSERT INTO portfolio_return_ingestion_cadence VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, provider) DO UPDATE SET interval_seconds=excluded.interval_seconds, grace_seconds=excluded.grace_seconds, approved_by=excluded.approved_by, updated_at=excluded.updated_at", (cadence.account_id, cadence.provider, int(cadence.interval.total_seconds()), int(cadence.grace.total_seconds()), cadence.approved_by, cadence.updated_at.isoformat())); self._connection.commit()

    def ingestion_cadences(self) -> list[ReturnIngestionCadence]:
        rows = self._connection.execute("SELECT account_id, provider, interval_seconds, grace_seconds, approved_by, updated_at FROM portfolio_return_ingestion_cadence ORDER BY account_id, provider").fetchall()
        return [ReturnIngestionCadence(row[0], row[1], timedelta(seconds=row[2]), timedelta(seconds=row[3]), row[4], datetime.fromisoformat(row[5])) for row in rows]

    def due_ingestion_cadences(self, now: datetime) -> list[tuple[ReturnIngestionCadence, datetime | None, bool]]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ReturnHistoryError("invalid_return_ingestion_due_time")
        rows = self._connection.execute("SELECT account_id, provider, interval_seconds, grace_seconds, approved_by, updated_at FROM portfolio_return_ingestion_cadence ORDER BY account_id, provider").fetchall()
        due: list[tuple[ReturnIngestionCadence, datetime | None, bool]] = []
        for row in rows:
            cadence = ReturnIngestionCadence(row[0], row[1], timedelta(seconds=row[2]), timedelta(seconds=row[3]), row[4], datetime.fromisoformat(row[5]))
            latest = self._connection.execute("SELECT completed_at FROM portfolio_return_ingestion_runs WHERE account_id=? AND provider=? AND status='SUCCEEDED' ORDER BY completed_at DESC, rowid DESC LIMIT 1", (cadence.account_id, cadence.provider)).fetchone()
            completed = None if latest is None else datetime.fromisoformat(latest[0])
            deadline = cadence.updated_at if completed is None else completed + cadence.interval
            if now >= deadline:
                due.append((cadence, completed, now > deadline + cadence.grace))
        return due

    def ingest(self, provider: PortfolioReturnProvider, account_id: str, *, start: datetime, end: datetime, retry_policy: ReturnIngestionRetryPolicy = ReturnIngestionRetryPolicy(), now: Callable[[], datetime] | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
        if not account_id.strip() or start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None or start >= end or not provider.provider_name.strip():
            raise ReturnHistoryError("invalid_return_history_ingestion_request")
        retry_policy.validate()
        clock = now or (lambda: datetime.now(timezone.utc))
        run_id, requested_at = uuid4(), clock()
        if requested_at.tzinfo is None or requested_at.utcoffset() is None:
            raise ReturnHistoryError("invalid_return_ingestion_clock")
        try:
            observations = self._fetch_with_retry(provider, account_id, start, end, retry_policy, sleep)
            if any(item.account_id != account_id or item.provider != provider.provider_name or item.period_start < start or item.period_end > end for item in observations):
                raise ReturnHistoryError("provider_return_history_contract_violation")
            for observation in observations:
                self.append(observation)
        except Exception as error:
            self._record_ingestion_run(run_id, provider.provider_name, account_id, start, end, requested_at, clock(), "FAILED", 0, str(error))
            self._record_provider_health(provider.provider_name, clock(), False, str(error))
            if isinstance(error, ReturnHistoryError):
                raise
            raise ReturnHistoryError("return_provider_fetch_failed") from error
        self._record_ingestion_run(run_id, provider.provider_name, account_id, start, end, requested_at, clock(), "SUCCEEDED", len(observations), None)
        self._record_provider_health(provider.provider_name, clock(), True, None)
        return len(observations)

    def ingest_authorized(self, provider: PortfolioReturnProvider, account_id: str, *, start: datetime, end: datetime, actor: str, idempotency_key: str, retry_policy: ReturnIngestionRetryPolicy = ReturnIngestionRetryPolicy()) -> int:
        """Durable operator command boundary; identical completed commands replay safely."""
        if not actor.strip() or not idempotency_key.strip():
            raise ReturnHistoryError("return_ingestion_actor_and_idempotency_key_required")
        row = self._connection.execute("SELECT actor, provider, account_id, start_at, end_at, status, observation_count, error FROM portfolio_return_ingestion_commands WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        signature = (actor, provider.provider_name, account_id, start.isoformat(), end.isoformat())
        if row is not None:
            if tuple(row[:5]) != signature:
                raise ReturnHistoryError("return_ingestion_idempotency_conflict")
            if row[5] == "SUCCEEDED":
                return row[6]
            raise ReturnHistoryError(f"prior_return_ingestion_failed:{row[7]}")
        self._connection.execute("INSERT INTO portfolio_return_ingestion_commands VALUES (?, ?, ?, ?, ?, ?, 'PENDING', NULL, NULL, ?)", (idempotency_key, *signature, datetime.now(timezone.utc).isoformat())); self._connection.commit()
        try:
            count = self.ingest(provider, account_id, start=start, end=end, retry_policy=retry_policy)
        except Exception as error:
            self._connection.execute("UPDATE portfolio_return_ingestion_commands SET status='FAILED', error=? WHERE idempotency_key=?", (str(error), idempotency_key)); self._connection.commit(); raise
        self._connection.execute("UPDATE portfolio_return_ingestion_commands SET status='SUCCEEDED', observation_count=? WHERE idempotency_key=?", (count, idempotency_key)); self._connection.commit()
        return count

    def _fetch_with_retry(self, provider: PortfolioReturnProvider, account_id: str, start: datetime, end: datetime, retry_policy: ReturnIngestionRetryPolicy, sleep: Callable[[float], None]) -> list[PortfolioReturnObservation]:
        for attempt in range(retry_policy.maximum_attempts):
            try:
                if hasattr(provider, "fetch_return_page"):
                    return self._collect_pages(provider, account_id, start, end)
                return provider.fetch_returns(account_id, start=start, end=end)
            except ReturnProviderTransientError:
                if attempt + 1 == retry_policy.maximum_attempts:
                    raise
                sleep(retry_policy.base_delay.total_seconds() * (2 ** attempt))
        raise AssertionError("retry loop must return or raise")

    @staticmethod
    def _collect_pages(provider: PaginatedPortfolioReturnProvider, account_id: str, start: datetime, end: datetime, *, maximum_pages: int = 1000) -> list[PortfolioReturnObservation]:
        cursor: str | None = None; seen: set[str] = set(); observations: list[PortfolioReturnObservation] = []
        for _ in range(maximum_pages):
            page = provider.fetch_return_page(account_id, start=start, end=end, cursor=cursor)
            observations.extend(page.observations)
            if page.next_cursor is None:
                return observations
            if not page.next_cursor or page.next_cursor in seen:
                raise ReturnHistoryError("return_provider_pagination_cursor_loop")
            seen.add(page.next_cursor); cursor = page.next_cursor
        raise ReturnHistoryError("return_provider_pagination_limit_exceeded")

    def _record_ingestion_run(self, run_id: UUID, provider: str, account_id: str, start: datetime, end: datetime, requested_at: datetime, completed_at: datetime, status: str, count: int, error: str | None) -> None:
        self._connection.execute("INSERT INTO portfolio_return_ingestion_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(run_id), provider, account_id, start.isoformat(), end.isoformat(), requested_at.isoformat(), completed_at.isoformat(), status, count, error)); self._connection.commit()

    def _record_provider_health(self, provider: str, checked_at: datetime, healthy: bool, reason: str | None) -> None:
        prior = self.provider_health(provider); failures = 0 if healthy else 1 + (0 if prior is None else prior[3])
        self._connection.execute("INSERT INTO portfolio_return_provider_health VALUES (?, ?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET checked_at=excluded.checked_at, healthy=excluded.healthy, reason=excluded.reason, consecutive_failures=excluded.consecutive_failures", (provider, checked_at.isoformat(), int(healthy), reason, failures)); self._connection.commit()

    def close(self) -> None:
        self._connection.close()
