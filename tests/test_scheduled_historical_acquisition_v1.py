"""Offline unit tests for Phase 3C scheduled historical acquisition.

Every provider response here is a synthetic FIXTURE produced by an in-process
scripted transport that parses the real Bybit adapter's request URL and answers
it; nothing is retrieved from Bybit and no socket is ever opened. The real
:class:`HistoricalAcquisitionService` and :class:`BybitCryptoHistoricalAdapter`
run unchanged over in-memory pipeline/evidence/feature fakes.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from trade_platform.bybit_crypto_provider import (
    BYBIT_EXCHANGE,
    BYBIT_PROVIDER_NAME,
    BYBIT_V5_SYMBOL_NAMESPACE,
    BybitCryptoHistoricalAdapter,
)
from trade_platform.bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    BYBIT_BTCUSDT_SYMBOL,
)
from trade_platform.crypto_instruments import (
    CryptoInstrumentKind,
    ReferencePriceRequirement,
    SettlementStyle,
)
from trade_platform.data_providers import (
    HttpResponse,
    MinimumIntervalRequestPacer,
    ProviderConfiguration,
    ProviderConfigurationError,
)
from trade_platform.historical_acquisition import (
    AcquisitionStatus,
    CryptoSpecView,
    HistoricalAcquisitionService,
    MaterializedFeatureCounts,
    NormalizedObservationView,
    SealedDatasetView,
    SourceProfile,
    acquisition_fingerprint,
)
from trade_platform.historical_market_data import (
    HistoricalDatasetVersion,
    HistoricalMarketDataError,
    NormalizedHistoricalObservation,
    ObservationKind,
    QualityStatus,
    RawHistoricalObservation,
)
from trade_platform.operational_jobs import OperationalJobPolicy, build_job_policy
from trade_platform.scheduled_historical_acquisition_v1 import (
    SCHEDULED_ACQUISITION_AUTHORIZATION_ENV,
    SCHEDULED_ACQUISITION_MODE_ENV,
    SCHEDULED_ACQUISITION_MODE_V1,
    SCHEDULED_ACQUISITION_TERMS_ENV,
    ScheduledAcquisitionConfigurationError,
    ScheduledAcquisitionOutcome,
    ScheduledAcquisitionWindow,
    ScheduledHistoricalAcquisitionAuthorizationV1,
    ScheduledHistoricalAcquisitionRunnerV1,
    authorization_from_json,
    build_scheduled_request,
    plan_scheduled_windows,
    scheduled_acquisition_from_environment,
    scheduled_dataset_version,
    window_lock_key,
)
from trade_platform.scheduler import JobExecutionFailed, default_job_registry

SOURCE_ID = UUID("33333333-3333-3333-3333-333333333333")
ANCHOR = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)
LAG = timedelta(minutes=2)
INTERVAL = timedelta(milliseconds=500)
JOB_NAME = "scheduled_bybit_btcusdt_acquisition_fixture"
POLICY_VERSION = "fixture-policy-v1"
ALL_KINDS = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)
BASIS_FEATURE_ID = UUID(int=101)
OPEN_INTEREST_FEATURE_ID = UUID(int=102)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MINUTE_MS = 60_000
_FIVE_MINUTES_MS = 300_000


def _authorization(**overrides: object) -> ScheduledHistoricalAcquisitionAuthorizationV1:
    values: dict[str, object] = {
        "authorization_version": "fixture-authorization-v1",
        "job_name": JOB_NAME,
        "job_policy_version": POLICY_VERSION,
        "provider": BYBIT_PROVIDER_NAME,
        "source_id": SOURCE_ID,
        "instrument_id": BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        "provider_symbol": BYBIT_BTCUSDT_SYMBOL,
        "observation_kinds": ALL_KINDS,
        "normalization_version": "bybit-v5-md-fixture",
        "materialize_features": True,
        "window_size": WINDOW,
        "schedule_anchor": ANCHOR,
        "finality_lag": LAG,
        "maximum_pages_per_kind": 2,
        "maximum_catchup_windows_per_invocation": 3,
        "minimum_request_interval": INTERVAL,
        "authorized_by": "fixture-operator",
        "authorization_reference": "FIXTURE-AUTHORIZATION-1",
        "authorized_at": ANCHOR - timedelta(days=1),
    }
    values.update(overrides)
    return ScheduledHistoricalAcquisitionAuthorizationV1(**values)  # type: ignore[arg-type]


def _configuration(**overrides: object) -> ProviderConfiguration:
    values: dict[str, object] = {
        "provider": BYBIT_PROVIDER_NAME,
        "base_url": "https://api.bybit.com",
        "terms_accepted": True,
        "secret_reference": None,
        "minimum_request_interval": INTERVAL,
    }
    values.update(overrides)
    return ProviderConfiguration(**values)  # type: ignore[arg-type]


def _policy(*, enabled: bool = True, version: str = POLICY_VERSION, job_name: str = JOB_NAME) -> OperationalJobPolicy:
    return build_job_policy(
        job_name=job_name,
        version=version,
        interval=timedelta(minutes=5),
        grace=timedelta(minutes=5),
        owner="fixture-operations",
        runbook_uri="runbook:fixture",
        approved_by="fixture-operator",
        approved_at=ANCHOR - timedelta(days=1),
        enabled=enabled,
    )


def _at(minutes: int) -> datetime:
    return ANCHOR + timedelta(minutes=minutes)


def _ms(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds()) * 1000


# ---------------------------------------------------------------------------
# Offline scripted Bybit transport
# ---------------------------------------------------------------------------


class SyntheticBybitTransport:
    """Answers the real adapter's public-market URLs with deterministic FIXTURE rows.

    Row values depend only on the timestamp, so a re-request of the same window
    returns byte-identical evidence. ``fail_window_starts`` answers HTTP 400 (a
    non-retryable provider failure) for any request whose range starts there;
    ``endless_open_interest_cursor`` always advertises another open-interest page.
    """

    def __init__(self, *, on_request: Callable[[str], None] | None = None) -> None:
        self.urls: list[str] = []
        self.fail_window_starts: set[datetime] = set()
        self.endless_open_interest_cursor = False
        self._on_request = on_request
        self._cursor_counter = 0

    def get(self, url: str, timeout_seconds: float) -> HttpResponse:
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
            rows = [
                {"openInterest": f"{460000 + (instant // _FIVE_MINUTES_MS) % 97}.0", "timestamp": str(instant)}
                for instant in range(start_ms, end_ms + 1, _FIVE_MINUTES_MS)
            ]
            cursor = ""
            if self.endless_open_interest_cursor:
                self._cursor_counter += 1
                cursor = f"fixture-page-{self._cursor_counter}"
            result: dict[str, object] = {
                "category": "linear",
                "symbol": BYBIT_BTCUSDT_SYMBOL,
                "list": list(reversed(rows)),
                "nextPageCursor": cursor,
            }
        else:
            trade = parsed.path.endswith("/market/kline")
            kline_rows: list[list[str]] = []
            for open_ms in range(start_ms, end_ms + 1, _MINUTE_MS):
                close = f"{27000 + (open_ms // _MINUTE_MS) % 89}.0"
                row = [str(open_ms), "27000.0", "27100.0", "26900.0", close]
                if trade:
                    row += ["12.5", "337500.0"]
                kline_rows.append(row)
            result = {"category": "linear", "symbol": BYBIT_BTCUSDT_SYMBOL, "list": list(reversed(kline_rows))}
        return HttpResponse(
            200, json.dumps({"retCode": 0, "retMsg": "OK", "result": result, "retExtInfo": {}, "time": end_ms})
        )

    def requested_window_starts(self) -> list[datetime]:
        starts: list[datetime] = []
        for url in self.urls:
            query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
            raw = query.get("startTime") or query["start"]
            starts.append(_EPOCH + timedelta(milliseconds=int(raw)))
        return starts


# ---------------------------------------------------------------------------
# In-memory durable authorities
# ---------------------------------------------------------------------------


class InMemoryHistoricalStore:
    """Pipeline + canonical evidence + checkpoint sink backed by dictionaries.

    Mirrors the real natural keys: raw capture deduplicates on
    ``(source, kind, event_at)``; one normalized row per raw id; one sealed
    dataset per ``(source, version)``.
    """

    def __init__(self) -> None:
        self.raws: dict[UUID, RawHistoricalObservation] = {}
        self._raw_ids: dict[tuple[UUID, ObservationKind, datetime], UUID] = {}
        self.normalized: dict[UUID, NormalizedObservationView] = {}
        self._raw_for_normalized: dict[UUID, UUID] = {}
        self.datasets: dict[tuple[UUID, str], tuple[HistoricalDatasetVersion, tuple[UUID, ...]]] = {}
        self.feature_values: dict[tuple[UUID, UUID], int] = {}
        self.checkpoints: list[object] = []
        # Pre-existing sealed datasets seeded directly (e.g. ones whose persisted
        # members do not match the deterministic scheduled version they squat on).
        self.forced_views: dict[tuple[UUID, str], SealedDatasetView] = {}

    # pipeline
    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]:
        ids: list[UUID] = []
        for observation in observations:
            key = (observation.source_id, observation.observation_kind, observation.event_at)
            raw_id = self._raw_ids.get(key)
            if raw_id is None:
                raw_id = uuid4()
                self._raw_ids[key] = raw_id
                self.raws[raw_id] = observation
            ids.append(raw_id)
        return tuple(ids)

    def normalize(
        self, raw_observation_id: UUID, normalization_version: str, normalized_at: datetime
    ) -> NormalizedHistoricalObservation:
        if raw_observation_id in self.normalized:
            raise AssertionError("normalize() twice for one raw id")
        normalized_id = uuid4()
        self.normalized[raw_observation_id] = NormalizedObservationView(
            normalized_id, raw_observation_id, BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
            normalization_version, QualityStatus.VALIDATED, (),
        )
        self._raw_for_normalized[normalized_id] = raw_observation_id
        return NormalizedHistoricalObservation(
            normalized_observation_id=normalized_id,
            raw_observation_id=raw_observation_id,
            instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
            normalization_version=normalization_version,
            normalized_value={},
            quality_status=QualityStatus.VALIDATED,
            quality_issues=(),
            normalized_at=normalized_at,
        )

    def seal_dataset(
        self,
        source_id: UUID,
        version: str,
        normalization_version: str,
        normalized_ids: tuple[UUID, ...],
        created_at: datetime,
    ) -> HistoricalDatasetVersion:
        if (source_id, version) in self.datasets:
            raise HistoricalMarketDataError("dataset_version_exists")
        events = sorted(self.raws[self._raw_for_normalized[item]].event_at for item in normalized_ids)
        digest = hashlib.sha256("|".join(sorted(str(item) for item in normalized_ids)).encode()).hexdigest()
        dataset = HistoricalDatasetVersion(
            uuid4(), source_id, version, normalization_version, digest, events[0], events[-1], created_at
        )
        self.datasets[(source_id, version)] = (dataset, normalized_ids)
        return dataset

    # checkpoint sink
    def record(self, checkpoint: object) -> None:
        self.checkpoints.append(checkpoint)

    # canonical evidence
    def source_profile(self, source_id: UUID) -> SourceProfile | None:
        return SourceProfile(BYBIT_PROVIDER_NAME, BYBIT_V5_SYMBOL_NAMESPACE, "CRYPTO", ALL_KINDS)

    def resolve_instrument_ids(self, namespace: str, provider_symbol: str, known_at: datetime) -> tuple[str, ...]:
        return (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,)

    def crypto_specification(self, instrument_id: str, known_at: datetime) -> CryptoSpecView | None:
        return CryptoSpecView(
            CryptoInstrumentKind.PERPETUAL, BYBIT_EXCHANGE, "BTC", "USDT", "USDT",
            SettlementStyle.LINEAR, ReferencePriceRequirement.MARK_AND_INDEX,
        )

    def existing_sealed_dataset(self, source_id: UUID, version: str) -> SealedDatasetView | None:
        forced = self.forced_views.get((source_id, version))
        if forced is not None:
            return forced
        entry = self.datasets.get((source_id, version))
        if entry is None:
            return None
        dataset, members = entry
        events: dict[ObservationKind, set[datetime]] = {}
        counts: dict[ObservationKind, int] = {}
        for normalized_id in members:
            raw = self.raws[self._raw_for_normalized[normalized_id]]
            events.setdefault(raw.observation_kind, set()).add(raw.event_at)
            counts[raw.observation_kind] = counts.get(raw.observation_kind, 0) + 1
        return SealedDatasetView(
            dataset.dataset_version_id, dataset.content_hash, dataset.normalization_version,
            dataset.valid_from, dataset.valid_until, dataset.created_at,
            frozenset({BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID}), frozenset({BYBIT_BTCUSDT_SYMBOL}),
            frozenset({BYBIT_BTCUSDT_SYMBOL}),
            {kind: frozenset(values) for kind, values in events.items()}, counts,
        )

    def feature_counts(self, dataset_version_id: UUID, feature_ids: tuple[UUID, ...]) -> Mapping[UUID, int]:
        return {feature_id: self.feature_values.get((dataset_version_id, feature_id), 0) for feature_id in feature_ids}

    def normalized_observation_for_raw(self, raw_observation_id: UUID) -> NormalizedObservationView | None:
        return self.normalized.get(raw_observation_id)


class InMemoryWindowEvidence:
    def __init__(self, store: InMemoryHistoricalStore) -> None:
        self._store = store
        self.reads = 0

    def sealed_datasets(self, source_id: UUID, version_prefix: str) -> Mapping[str, SealedDatasetView]:
        self.reads += 1
        versions = {version for (source, version) in self._store.datasets if source == source_id}
        versions |= {version for (source, version) in self._store.forced_views if source == source_id}
        views: dict[str, SealedDatasetView] = {}
        for version in versions:
            if version.startswith(version_prefix):
                view = self._store.existing_sealed_dataset(source_id, version)
                if view is not None:
                    views[version] = view
        return views

    def feature_counts(
        self, dataset_version_ids: tuple[UUID, ...], feature_ids: tuple[UUID, ...]
    ) -> Mapping[tuple[UUID, UUID], int]:
        return {
            (dataset_id, feature_id): self._store.feature_values.get((dataset_id, feature_id), 0)
            for dataset_id in dataset_version_ids
            for feature_id in feature_ids
        }


class InMemoryFeatureMaterializer:
    def __init__(self, store: InMemoryHistoricalStore) -> None:
        self._store = store
        self.fail_dataset_count = 0

    def resolve_basis_feature_id(self, definition_created_at: datetime) -> UUID:
        return BASIS_FEATURE_ID

    def resolve_open_interest_feature_id(self, definition_created_at: datetime) -> UUID:
        return OPEN_INTEREST_FEATURE_ID

    def materialize_features(
        self,
        *,
        instrument_id: str,
        dataset_version_id: UUID,
        basis_event_ats: tuple[datetime, ...],
        open_interest_event_ats: tuple[datetime, ...],
        decision_at: datetime,
        definition_created_at: datetime,
    ) -> MaterializedFeatureCounts:
        if self.fail_dataset_count:
            self.fail_dataset_count -= 1
            raise RuntimeError("fixture_feature_failure")
        basis = len(basis_event_ats)
        open_interest = max(len(open_interest_event_ats) - 1, 0)
        self._store.feature_values[(dataset_version_id, BASIS_FEATURE_ID)] = basis
        self._store.feature_values[(dataset_version_id, OPEN_INTEREST_FEATURE_ID)] = open_interest
        return MaterializedFeatureCounts(basis, open_interest)


class InMemoryWindowLock:
    """Models the advisory lock: a key held by another session cannot be claimed."""

    def __init__(self) -> None:
        self.held_elsewhere: set[str] = set()
        self.claimed: list[str] = []
        self.released: list[str] = []

    def try_claim(self, key: str) -> bool:
        if key in self.held_elsewhere:
            return False
        self.claimed.append(key)
        return True

    def release(self, key: str) -> None:
        self.released.append(key)


@dataclass
class StaticPolicyGate:
    policy: OperationalJobPolicy | None

    def effective_policy(self, job_name: str, as_of: datetime) -> OperationalJobPolicy | None:
        return self.policy if self.policy is not None and self.policy.job_name == job_name else None


class FakeClock:
    """Wall clock (service ``now``) plus monotonic clock the pacer sleeps against."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.monotonic_seconds = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_seconds += seconds

    def monotonic(self) -> float:
        return self.monotonic_seconds


class Harness:
    """One durable store; each :meth:`runner` call is a fresh process (restart)."""

    def __init__(self, *, authorization: ScheduledHistoricalAcquisitionAuthorizationV1 | None = None) -> None:
        self.authorization = authorization or _authorization()
        self.store = InMemoryHistoricalStore()
        self.evidence = InMemoryWindowEvidence(self.store)
        self.materializer = InMemoryFeatureMaterializer(self.store)
        self.lock = InMemoryWindowLock()
        self.gate = StaticPolicyGate(_policy())
        self.clock = FakeClock(_at(24 * 60))
        self.transport = SyntheticBybitTransport()

    def runner(
        self,
        *,
        configuration: ProviderConfiguration | None = None,
        pacer: MinimumIntervalRequestPacer | None = None,
    ) -> ScheduledHistoricalAcquisitionRunnerV1:
        configuration = configuration or _configuration()
        shared = pacer or MinimumIntervalRequestPacer(
            configuration.minimum_request_interval, monotonic=self.clock.monotonic, sleep=self.clock.sleep
        )

        def adapter_factory(config: ProviderConfiguration, now: Callable[[], datetime]) -> BybitCryptoHistoricalAdapter:
            return BybitCryptoHistoricalAdapter(
                config, transport=self.transport, now=now, sleep=self.clock.sleep, pacer=shared
            )

        service = HistoricalAcquisitionService(
            evidence=self.store,
            pipeline=self.store,
            checkpoint_store=self.store,
            feature_materializer=self.materializer,
            adapter_factory=adapter_factory,
            now=lambda: self.clock.now,
        )
        return ScheduledHistoricalAcquisitionRunnerV1(
            authorization=self.authorization,
            configuration=configuration,
            service=service,
            evidence=self.evidence,
            feature_identity=self.materializer,
            window_lock=self.lock,
            policy_gate=self.gate,
        )


def _full_env(authorization_document: str) -> Callable[[str], str | None]:
    values = {
        SCHEDULED_ACQUISITION_MODE_ENV: SCHEDULED_ACQUISITION_MODE_V1,
        SCHEDULED_ACQUISITION_AUTHORIZATION_ENV: authorization_document,
        SCHEDULED_ACQUISITION_TERMS_ENV: "accepted",
    }
    return values.get


def _authorization_document(**overrides: object) -> str:
    payload: dict[str, object] = {
        "authorization_version": "fixture-authorization-v1",
        "job_name": JOB_NAME,
        "job_policy_version": POLICY_VERSION,
        "provider": "bybit",
        "source_id": str(SOURCE_ID),
        "instrument_id": BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        "provider_symbol": BYBIT_BTCUSDT_SYMBOL,
        "observation_kinds": ["OHLCV", "MARK_PRICE", "INDEX_PRICE", "OPEN_INTEREST"],
        "normalization_version": "bybit-v5-md-fixture",
        "materialize_features": True,
        "window_size_seconds": 600,
        "schedule_anchor": ANCHOR.isoformat(),
        "finality_lag_seconds": 120,
        "maximum_pages_per_kind": 2,
        "maximum_catchup_windows_per_invocation": 3,
        "minimum_request_interval_seconds": 0.5,
        "authorized_by": "fixture-operator",
        "authorization_reference": "FIXTURE-AUTHORIZATION-1",
        "authorized_at": (ANCHOR - timedelta(days=1)).isoformat(),
    }
    payload.update(overrides)
    return json.dumps({key: value for key, value in payload.items() if value is not _DROP})


_DROP = object()


# ---------------------------------------------------------------------------
# Default registry / deployment opt-in
# ---------------------------------------------------------------------------


class DefaultRegistryIsProviderInertTests(unittest.TestCase):
    def test_default_registry_is_internal_only(self) -> None:
        registry = default_job_registry()
        self.assertEqual(
            set(registry),
            {
                "operational_job_monitor",
                "postgres_dependency_probe",
                "retention_evaluation_sweep",
                "data_health_evaluation",
            },
        )
        for runner in registry.values():
            self.assertEqual(runner.__module__, "trade_platform.scheduler")

    def test_no_environment_means_no_runner(self) -> None:
        self.assertIsNone(scheduled_acquisition_from_environment(lambda _name: None))
        self.assertIsNone(scheduled_acquisition_from_environment({SCHEDULED_ACQUISITION_MODE_ENV: ""}.get))

    def test_complete_environment_yields_bound_configuration(self) -> None:
        config = scheduled_acquisition_from_environment(_full_env(_authorization_document()))
        assert config is not None
        self.assertEqual(config.authorization, _authorization())
        self.assertTrue(config.configuration.terms_accepted)
        self.assertIsNone(config.configuration.secret_reference)
        self.assertEqual(config.configuration.minimum_request_interval, INTERVAL)
        self.assertTrue(config.configuration.base_url.startswith("https://"))

    def test_partial_environment_fails_closed(self) -> None:
        full = {
            SCHEDULED_ACQUISITION_MODE_ENV: SCHEDULED_ACQUISITION_MODE_V1,
            SCHEDULED_ACQUISITION_AUTHORIZATION_ENV: _authorization_document(),
            SCHEDULED_ACQUISITION_TERMS_ENV: "accepted",
        }
        for missing in full:
            partial = {key: value for key, value in full.items() if key != missing}
            with self.subTest(missing=missing), self.assertRaises(ScheduledAcquisitionConfigurationError):
                scheduled_acquisition_from_environment(partial.get)
        for only, value in full.items():
            with self.subTest(only=only), self.assertRaises(ScheduledAcquisitionConfigurationError):
                scheduled_acquisition_from_environment({only: value}.get)

    def test_mode_and_terms_must_be_exact_tokens(self) -> None:
        document = _authorization_document()
        for mode in ("1", "true", "yes", "on", "bybit"):
            env = {
                SCHEDULED_ACQUISITION_MODE_ENV: mode,
                SCHEDULED_ACQUISITION_AUTHORIZATION_ENV: document,
                SCHEDULED_ACQUISITION_TERMS_ENV: "accepted",
            }
            with self.subTest(mode=mode), self.assertRaises(ScheduledAcquisitionConfigurationError):
                scheduled_acquisition_from_environment(env.get)
        for terms in ("true", "1", "yes", "false", "not-accepted"):
            env = {
                SCHEDULED_ACQUISITION_MODE_ENV: SCHEDULED_ACQUISITION_MODE_V1,
                SCHEDULED_ACQUISITION_AUTHORIZATION_ENV: document,
                SCHEDULED_ACQUISITION_TERMS_ENV: terms,
            }
            with self.subTest(terms=terms), self.assertRaises(ScheduledAcquisitionConfigurationError) as caught:
                scheduled_acquisition_from_environment(env.get)
            self.assertEqual(str(caught.exception), "provider_terms_not_accepted")

    def test_authorization_document_has_no_defaults(self) -> None:
        for key in json.loads(_authorization_document()):
            with self.subTest(key=key), self.assertRaises(ScheduledAcquisitionConfigurationError) as caught:
                authorization_from_json(_authorization_document(**{key: _DROP}))
            self.assertIn("authorization_missing_fields", str(caught.exception))
        with self.assertRaises(ScheduledAcquisitionConfigurationError):
            authorization_from_json(_authorization_document(unexpected_field=1))
        with self.assertRaises(ScheduledAcquisitionConfigurationError):
            authorization_from_json("not json")

    def test_secret_reference_rejected(self) -> None:
        with self.assertRaises(ScheduledAcquisitionConfigurationError) as caught:
            authorization_from_json(_authorization_document(secret_reference="vault://bybit/api-key"))  # pragma: allowlist secret
        self.assertEqual(
            str(caught.exception), "public_bybit_scheduled_acquisition_rejects_secret_reference"
        )
        with self.assertRaises(ScheduledAcquisitionConfigurationError):
            Harness().runner(configuration=_configuration(secret_reference="vault://bybit/api-key"))

    def test_funding_rejected_in_document_and_object(self) -> None:
        with self.assertRaises(ScheduledAcquisitionConfigurationError) as caught:
            authorization_from_json(
                _authorization_document(
                    observation_kinds=["OHLCV", "MARK_PRICE", "INDEX_PRICE", "OPEN_INTEREST", "FUNDING_RATE_REALIZED"]
                )
            )
        self.assertEqual(str(caught.exception), "funding_not_supported")
        with self.assertRaises(ScheduledAcquisitionConfigurationError):
            _authorization(
                observation_kinds=ALL_KINDS | {ObservationKind.FUNDING_RATE_INDICATIVE}
            ).validate()


# ---------------------------------------------------------------------------
# Authorization validation
# ---------------------------------------------------------------------------


class AuthorizationValidationTests(unittest.TestCase):
    def test_valid_fixture_authorization(self) -> None:
        _authorization().validate()

    def test_invalid_authorizations_fail_closed(self) -> None:
        cases: dict[str, dict[str, object]] = {
            "provider": {"provider": "binance"},
            "instrument": {"instrument_id": "CRYPTO:BYBIT:ETHUSDT:PERP"},
            "symbol": {"provider_symbol": "ETHUSDT"},
            "kind_subset": {"observation_kinds": frozenset({ObservationKind.OHLCV})},
            "anchor_naive": {"schedule_anchor": ANCHOR.replace(tzinfo=None)},
            "anchor_seconds": {"schedule_anchor": ANCHOR + timedelta(seconds=30)},
            "anchor_oi_misaligned": {"schedule_anchor": ANCHOR + timedelta(minutes=3)},
            "window_not_oi_multiple": {"window_size": timedelta(minutes=7)},
            "window_zero": {"window_size": timedelta(0)},
            "window_fractional_minute": {"window_size": timedelta(minutes=10, seconds=30)},
            "lag_zero": {"finality_lag": timedelta(0)},
            "interval_zero": {"minimum_request_interval": timedelta(0)},
            "catchup_zero": {"maximum_catchup_windows_per_invocation": 0},
            "pages_zero": {"maximum_pages_per_kind": 0},
            "pages_cannot_cover_window": {
                "window_size": timedelta(minutes=1005),
                "maximum_pages_per_kind": 1,
            },
            "blank_actor": {"authorized_by": " "},
            "blank_reference": {"authorization_reference": ""},
            "blank_policy_version": {"job_policy_version": ""},
        }
        for name, overrides in cases.items():
            with self.subTest(name), self.assertRaises(ScheduledAcquisitionConfigurationError):
                _authorization(**overrides).validate()

    def test_runner_requires_explicit_terms_and_exact_interval(self) -> None:
        harness = Harness()
        with self.assertRaises(ScheduledAcquisitionConfigurationError) as caught:
            harness.runner(configuration=_configuration(terms_accepted=False))
        self.assertEqual(str(caught.exception), "provider_terms_not_accepted")
        with self.assertRaises(ScheduledAcquisitionConfigurationError):
            harness.runner(configuration=_configuration(minimum_request_interval=timedelta(0)))
        with self.assertRaises(ScheduledAcquisitionConfigurationError):
            harness.runner(configuration=_configuration(base_url="http://api.bybit.com"))
        self.assertEqual(harness.transport.urls, [])


# ---------------------------------------------------------------------------
# Durable job policy gate
# ---------------------------------------------------------------------------


class PolicyGateTests(unittest.TestCase):
    def _assert_not_authorized(self, harness: Harness, code: str) -> None:
        result = harness.runner().run(_at(60))
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.NOT_AUTHORIZED)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure_code, code)
        self.assertEqual(harness.transport.urls, [])
        self.assertEqual(harness.lock.claimed, [])

    def test_missing_policy_blocks_provider_execution(self) -> None:
        harness = Harness()
        harness.gate.policy = None
        self._assert_not_authorized(harness, "operational_job_policy_missing")

    def test_disabled_policy_blocks_provider_execution(self) -> None:
        harness = Harness()
        harness.gate.policy = _policy(enabled=False)
        self._assert_not_authorized(harness, "operational_job_policy_disabled")

    def test_unauthorized_policy_version_blocks_provider_execution(self) -> None:
        harness = Harness()
        harness.gate.policy = _policy(version="fixture-policy-v2")
        self._assert_not_authorized(harness, "operational_job_policy_version_not_authorized")

    def test_authorization_is_not_effective_before_authorized_at(self) -> None:
        harness = Harness(authorization=_authorization(authorized_at=_at(90)))
        self._assert_not_authorized(harness, "authorization_not_yet_effective")

    def test_not_authorized_job_runner_raises_structured_failure(self) -> None:
        harness = Harness()
        harness.gate.policy = None
        job = harness.runner().as_job_runner()
        with self.assertRaises(JobExecutionFailed) as caught:
            job(None, _at(60))  # type: ignore[arg-type]
        self.assertEqual(caught.exception.summary["outcome"], "NOT_AUTHORIZED")
        self.assertEqual(harness.transport.urls, [])


# ---------------------------------------------------------------------------
# Windows, cadence and identity
# ---------------------------------------------------------------------------


class WindowSemanticsTests(unittest.TestCase):
    def test_windows_are_deterministic_aligned_half_open_slots(self) -> None:
        authorization = _authorization()
        plan = plan_scheduled_windows(authorization, _at(47), ())
        # cutoff = 45 -> windows ending <= 45: [0,10) [10,20) [20,30) [30,40)
        self.assertEqual(plan.eligible_cutoff, _at(45))
        self.assertEqual(plan.windows_eligible, 4)
        self.assertEqual(
            [(window.start, window.end) for window in plan.pending],
            [(_at(0), _at(10)), (_at(10), _at(20)), (_at(20), _at(30))],
        )
        for window in plan.pending:
            self.assertEqual(window.start.utcoffset(), timedelta(0))
            self.assertEqual((window.start.second, window.start.microsecond), (0, 0))
            self.assertEqual(window.start.minute % 5, 0)
            self.assertEqual(window.end - window.start, WINDOW)
            self.assertLessEqual(window.end, plan.eligible_cutoff)

    def test_finality_lag_excludes_the_immature_tail(self) -> None:
        authorization = _authorization(maximum_catchup_windows_per_invocation=10)
        # [20,30) ends at 30; with a 2-minute lag it only becomes eligible at 32.
        self.assertEqual(plan_scheduled_windows(authorization, _at(31), ()).windows_eligible, 2)
        self.assertEqual(
            plan_scheduled_windows(authorization, _at(31) + timedelta(seconds=59), ()).windows_eligible, 2
        )
        self.assertEqual(plan_scheduled_windows(authorization, _at(32), ()).windows_eligible, 3)
        self.assertEqual(plan_scheduled_windows(authorization, _at(1), ()).windows_eligible, 0)
        self.assertEqual(plan_scheduled_windows(authorization, ANCHOR - timedelta(days=1), ()).pending, ())

    def test_same_as_of_produces_the_same_window_set(self) -> None:
        authorization = _authorization()
        first = plan_scheduled_windows(authorization, _at(97), ())
        second = plan_scheduled_windows(authorization, _at(97), ())
        self.assertEqual(first, second)
        # A trigger anywhere inside the same slot never widens or shifts a window.
        for as_of in (_at(92), _at(95) + timedelta(seconds=13), _at(101) + timedelta(seconds=59)):
            other = plan_scheduled_windows(authorization, as_of, ())
            self.assertEqual((other.windows_eligible, other.pending), (first.windows_eligible, first.pending))

    def test_same_window_yields_same_dataset_version_and_fingerprint(self) -> None:
        plan = plan_scheduled_windows(_authorization(), _at(47), ())
        window = plan.pending[1]
        first = build_scheduled_request(_authorization(), window)
        second = build_scheduled_request(_authorization(), window)
        self.assertEqual(first, second)
        self.assertEqual(first.idempotency_key, acquisition_fingerprint(first))
        self.assertEqual(first.dataset_version, scheduled_dataset_version(_authorization(), window))
        self.assertEqual((first.start, first.end), (_at(10), _at(20)))
        self.assertEqual(first.maximum_pages_per_kind, 2)
        self.assertNotEqual(first.dataset_version, build_scheduled_request(_authorization(), plan.pending[0]).dataset_version)

    def test_changed_authorization_or_window_changes_identity(self) -> None:
        base = _authorization()
        window = plan_scheduled_windows(base, _at(47), ()).pending[0]
        base_request = build_scheduled_request(base, window)
        changes: dict[str, dict[str, object]] = {
            "authorization_version": {"authorization_version": "fixture-authorization-v2"},
            "normalization_version": {"normalization_version": "bybit-v5-md-fixture-2"},
            "source_id": {"source_id": UUID(int=7)},
        }
        for name, overrides in changes.items():
            changed = _authorization(**overrides)
            changed_window = plan_scheduled_windows(changed, _at(47), ()).pending[0]
            request = build_scheduled_request(changed, changed_window)
            with self.subTest(name):
                self.assertNotEqual(request.dataset_version, base_request.dataset_version)
                self.assertNotEqual(request.idempotency_key, base_request.idempotency_key)
                self.assertNotEqual(window_lock_key(changed, changed_window), window_lock_key(base, window))
        # A different window size / anchor yields different windows and identity.
        for overrides in ({"window_size": timedelta(minutes=15)}, {"schedule_anchor": ANCHOR + timedelta(minutes=5)}):
            changed = _authorization(**overrides)
            request = build_scheduled_request(changed, plan_scheduled_windows(changed, _at(47), ()).pending[0])
            self.assertNotEqual(request.dataset_version, base_request.dataset_version)
            self.assertNotEqual(request.idempotency_key, base_request.idempotency_key)
        # A different window of the same authorization is a different identity.
        other = plan_scheduled_windows(base, _at(47), ()).pending[1]
        self.assertNotEqual(build_scheduled_request(base, other).idempotency_key, base_request.idempotency_key)
        self.assertNotEqual(window_lock_key(base, other), window_lock_key(base, window))
        # Operational bounds do not rename already-sealed windows...
        bounded = _authorization(finality_lag=timedelta(minutes=4), maximum_catchup_windows_per_invocation=9)
        self.assertEqual(build_scheduled_request(bounded, window), base_request)
        # ...but they are bound by the full authorization content hash.
        self.assertNotEqual(bounded.content_hash, base.content_hash)
        self.assertEqual(bounded.identity_hash, base.identity_hash)

    def test_foreign_or_malformed_versions_never_count_as_completed(self) -> None:
        authorization = _authorization()
        window = plan_scheduled_windows(authorization, _at(47), ()).pending[0]
        good = scheduled_dataset_version(authorization, window)
        other = scheduled_dataset_version(_authorization(authorization_version="other"), window)
        plan = plan_scheduled_windows(
            authorization, _at(47), (other, good + "x", good.replace("0000Z", "0003Z"), "bybit-sched-v1:junk")
        )
        self.assertEqual(plan.windows_completed, 0)
        self.assertEqual(plan.pending[0], window)


# ---------------------------------------------------------------------------
# Runner behaviour
# ---------------------------------------------------------------------------


class RunnerBehaviourTests(unittest.TestCase):
    def test_bounded_catch_up_is_oldest_first_and_reports_backlog(self) -> None:
        harness = Harness()
        as_of = _at(52)  # cutoff 50 -> five eligible windows, bound 3
        result = harness.runner().run(as_of)
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.BACKLOG_REMAINS)
        self.assertTrue(result.succeeded)
        self.assertEqual([item.window.start for item in result.window_outcomes], [_at(0), _at(10), _at(20)])
        self.assertEqual(result.backlog_remaining, 2)
        starts = harness.transport.requested_window_starts()
        self.assertEqual(starts, sorted(starts))
        self.assertNotIn(_at(30), starts)
        summary = result.summary()
        self.assertEqual(summary["backlog_remains"], "true")
        self.assertEqual(summary["backlog_remaining"], "2")
        self.assertEqual(summary["windows_acquired"], "3")
        self.assertEqual(summary["windows_eligible"], "5")
        self.assertEqual(summary["eligible_cutoff"], _at(50).isoformat())
        self.assertEqual(summary["feature_counts"].count("basis=10;open_interest_change=1"), 3)
        self.assertEqual(len(summary["dataset_content_hashes"].split(",")), 3)
        self.assertEqual(summary["provider_health_statuses"], "HEALTHY,HEALTHY,HEALTHY")
        # One invocation: 3 windows x 4 kinds x 2 pages x 3 adapter attempts.
        self.assertEqual(summary["worst_case_provider_requests"], "72")
        self.assertEqual(len(harness.transport.urls), 12)

        # The next invocation continues from the oldest remaining window.
        second = harness.runner().run(as_of)
        self.assertEqual(second.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual([item.window.start for item in second.window_outcomes], [_at(30), _at(40)])
        self.assertEqual(second.backlog_remaining, 0)
        self.assertEqual(second.summary()["backlog_remains"], "false")

    def test_bounded_invocation_is_a_successful_job_run(self) -> None:
        harness = Harness()
        summary = harness.runner().as_job_runner()(None, _at(52))  # type: ignore[arg-type]
        self.assertEqual(summary["outcome"], "BACKLOG_REMAINS")
        self.assertEqual(summary["backlog_remains"], "true")
        for key in (
            "authorization_version",
            "scheduled_occurrence",
            "eligible_cutoff",
            "windows_considered",
            "windows_replayed",
            "windows_acquired",
            "first_failed_window",
            "backlog_remaining",
            "dataset_versions",
            "dataset_content_hashes",
            "provider_health_statuses",
            "feature_counts",
        ):
            self.assertIn(key, summary)
        self.assertTrue(all(isinstance(value, str) for value in summary.values()))

    def test_failure_stops_later_windows_and_retries_identically(self) -> None:
        harness = Harness(authorization=_authorization(maximum_catchup_windows_per_invocation=4))
        harness.transport.fail_window_starts.add(_at(20))
        as_of = _at(42)  # four eligible windows W0..W3
        result = harness.runner().run(as_of)
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
        self.assertFalse(result.succeeded)
        self.assertEqual(
            [(item.window.start, item.status) for item in result.window_outcomes],
            [
                (_at(0), AcquisitionStatus.SUCCEEDED),
                (_at(10), AcquisitionStatus.SUCCEEDED),
                (_at(20), AcquisitionStatus.PROVIDER_FAILED),
            ],
        )
        self.assertNotIn(_at(30), harness.transport.requested_window_starts())
        failed = result.first_failed_window
        assert failed is not None
        self.assertEqual(result.summary()["first_failed_window"], f"[{_at(20).isoformat()},{_at(30).isoformat()})")
        self.assertEqual(result.summary()["first_failed_status"], "PROVIDER_FAILED")
        self.assertEqual(result.backlog_remaining, 2)
        self.assertNotIn(failed.dataset_version, result.summary()["dataset_versions"])

        with self.assertRaises(JobExecutionFailed) as caught:
            harness.runner().as_job_runner()(None, as_of)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.summary["outcome"], "WINDOW_FAILED")
        self.assertEqual(caught.exception.summary["windows_acquired"], "0")

        # Provider recovers: the restart retries exactly W2, then W3.
        harness.transport.fail_window_starts.clear()
        retry = harness.runner().run(as_of)
        self.assertEqual(retry.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual([item.window.start for item in retry.window_outcomes], [_at(20), _at(30)])
        self.assertEqual(retry.window_outcomes[0].dataset_version, failed.dataset_version)
        self.assertEqual(retry.window_outcomes[0].acquisition_fingerprint, failed.acquisition_fingerprint)
        self.assertEqual(retry.window_outcomes[0].window, failed.window)

    def test_restart_yields_identical_missing_windows(self) -> None:
        harness = Harness()
        harness.transport.fail_window_starts.add(_at(0))
        first = harness.runner().run(_at(52))
        second = harness.runner().run(_at(52))
        self.assertEqual(first.windows_considered, second.windows_considered)
        self.assertEqual(
            [item.acquisition_fingerprint for item in first.window_outcomes],
            [item.acquisition_fingerprint for item in second.window_outcomes],
        )

    def test_slow_runs_do_not_drift_window_boundaries(self) -> None:
        fast = Harness()
        slow = Harness()

        def slow_provider(_url: str) -> None:
            slow.clock.now += timedelta(minutes=7)  # latency/normalization/features

        slow.transport = SyntheticBybitTransport(on_request=slow_provider)
        slow.clock.now = fast.clock.now
        fast.runner().run(_at(32))
        slow.runner().run(_at(32))
        # Next trigger lands at an arbitrary, execution-dependent time; the
        # windows it considers are the same slots for both deployments.
        next_fast = fast.runner().run(_at(63))
        next_slow = slow.runner().run(_at(63) + timedelta(seconds=41))
        self.assertEqual(next_fast.windows_considered, next_slow.windows_considered)
        self.assertEqual([window.start for window in next_slow.windows_considered], [_at(30), _at(40), _at(50)])
        for window in next_slow.windows_considered:
            self.assertEqual((window.start - ANCHOR) % WINDOW, timedelta(0))
        self.assertEqual(
            [item.dataset_version for item in next_fast.window_outcomes],
            [item.dataset_version for item in next_slow.window_outcomes],
        )

    def test_completed_windows_replay_with_zero_provider_fetches(self) -> None:
        harness = Harness()
        self.assertEqual(harness.runner().run(_at(32)).outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        requests = len(harness.transport.urls)
        again = harness.runner().run(_at(32))
        self.assertEqual(again.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(again.windows_considered, ())
        self.assertEqual(again.windows_completed_before, 3)
        self.assertEqual(len(harness.transport.urls), requests)

    def test_sealed_window_with_incomplete_features_resumes_via_replay_without_fetch(self) -> None:
        harness = Harness()
        harness.materializer.fail_dataset_count = 1
        first = harness.runner().run(_at(22))
        self.assertEqual(first.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
        self.assertEqual(first.window_outcomes[0].status, AcquisitionStatus.FEATURE_FAILED)
        self.assertEqual(len(first.window_outcomes), 1)  # W1 not attempted past the gap
        requests_after_first = len(harness.transport.urls)
        self.assertEqual(requests_after_first, 4)

        second = harness.runner().run(_at(22))
        self.assertEqual(second.windows_feature_incomplete, 1)
        self.assertEqual(second.window_outcomes[0].window, first.window_outcomes[0].window)
        self.assertTrue(second.window_outcomes[0].already_completed)
        self.assertEqual(second.window_outcomes[0].basis_feature_count, 10)
        self.assertEqual(second.window_outcomes[0].open_interest_change_feature_count, 1)
        self.assertEqual(second.summary()["provider_health_statuses"].split(",")[0], "REPLAY_NO_PROVIDER_CALL")
        # Only W1 was fetched; W0 replayed from its sealed dataset.
        self.assertEqual(len(harness.transport.urls), requests_after_first + 4)
        self.assertEqual(second.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)

    def test_maximum_pages_per_kind_is_enforced(self) -> None:
        harness = Harness()
        harness.transport.endless_open_interest_cursor = True
        result = harness.runner().run(_at(12))
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
        self.assertEqual(result.window_outcomes[0].status, AcquisitionStatus.PAGINATION_FAILED)
        open_interest_requests = [url for url in harness.transport.urls if "open-interest" in url]
        self.assertEqual(len(open_interest_requests), 2)
        self.assertEqual(len(harness.transport.urls), 3 + 2)

    def test_provider_minimum_request_interval_paces_across_windows(self) -> None:
        harness = Harness()
        result = harness.runner().run(_at(32))  # three windows, 12 requests
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(len(harness.transport.urls), 12)
        # Monotonic time only advances by sleeping, so every request after the
        # first -- including the first request of windows 2 and 3 -- waits the
        # full configured interval: one shared pacer, never reset per window.
        self.assertEqual(harness.clock.sleeps, [INTERVAL.total_seconds()] * 11)

    def test_contended_window_stops_without_provider_calls(self) -> None:
        harness = Harness()
        first_window = plan_scheduled_windows(harness.authorization, _at(32), ()).pending[0]
        harness.lock.held_elsewhere.add(window_lock_key(harness.authorization, first_window))
        result = harness.runner().run(_at(32))
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.WINDOW_CONTENDED)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.contended_window, first_window)
        self.assertEqual(result.window_outcomes, ())
        self.assertEqual(harness.transport.urls, [])
        self.assertEqual(result.backlog_remaining, 3)

    def test_every_claimed_lock_is_released_even_on_failure(self) -> None:
        harness = Harness()
        harness.transport.fail_window_starts.add(_at(10))
        harness.runner().run(_at(32))
        self.assertEqual(harness.lock.claimed, harness.lock.released)
        self.assertEqual(len(harness.lock.claimed), 2)

    def test_unexpected_service_exception_is_a_window_failure(self) -> None:
        harness = Harness()
        runner = harness.runner()

        class ExplodingService:
            def acquire(self, request: object, configuration: object) -> object:
                raise RuntimeError("database went away")

        runner._service = ExplodingService()  # type: ignore[assignment]
        result = runner.run(_at(22))
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
        self.assertEqual(result.failure_code, "unexpected_acquisition_error:RuntimeError")
        self.assertEqual(len(result.window_outcomes), 1)
        self.assertEqual(harness.lock.claimed, harness.lock.released)


# ---------------------------------------------------------------------------
# Sealed-dataset identity is the completion authority (not the version name)
# ---------------------------------------------------------------------------


def _expected_events(kind: ObservationKind, start: datetime, end: datetime) -> list[datetime]:
    if kind is ObservationKind.OPEN_INTEREST:
        count = int((end - start) / timedelta(minutes=5))
        return [start + index * timedelta(minutes=5) for index in range(count)]
    total = int((end - start) / timedelta(minutes=1))
    offset = 0 if kind is ObservationKind.OHLCV else 1
    return [start + (index + offset) * timedelta(minutes=1) for index in range(total)]


def _seed_sealed(
    harness: Harness,
    index: int,
    *,
    normalization_version: str | None = None,
    instrument_ids: frozenset[str] | None = None,
    provider_identifiers: frozenset[str] | None = None,
    provider_symbols: frozenset[str] | None = None,
    member_count_overrides: Mapping[ObservationKind, int] | None = None,
    shift_ohlcv_by: timedelta | None = None,
    features_complete: bool = False,
) -> ScheduledAcquisitionWindow:
    """Seal a dataset under window ``index``'s deterministic scheduled version.

    By default its persisted members are exactly what a correct acquisition of
    that window would have sealed; each override makes one aspect wrong while
    the *version name* still equals the deterministic scheduled one.
    """
    authorization = harness.authorization
    start = authorization.schedule_anchor + index * authorization.window_size
    window = ScheduledAcquisitionWindow(index, start, start + authorization.window_size)
    request = build_scheduled_request(authorization, window)
    events = {kind: _expected_events(kind, window.start, window.end) for kind in ALL_KINDS}
    if shift_ohlcv_by is not None:
        events[ObservationKind.OHLCV] = [item + shift_ohlcv_by for item in events[ObservationKind.OHLCV]]
    counts = {kind: len(values) for kind, values in events.items()}
    counts.update(member_count_overrides or {})
    dataset_id = uuid4()
    harness.store.forced_views[(SOURCE_ID, request.dataset_version)] = SealedDatasetView(
        dataset_version_id=dataset_id,
        content_hash=hashlib.sha256(request.dataset_version.encode()).hexdigest(),
        normalization_version=normalization_version or authorization.normalization_version,
        valid_from=window.start,
        valid_until=window.end,
        created_at=window.end,
        instrument_ids=instrument_ids or frozenset({BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID}),
        provider_identifiers=provider_identifiers or frozenset({BYBIT_BTCUSDT_SYMBOL}),
        provider_symbols=provider_symbols or frozenset({BYBIT_BTCUSDT_SYMBOL}),
        event_ats_by_kind={kind: frozenset(values) for kind, values in events.items()},
        member_count_by_kind=counts,
    )
    if features_complete:
        harness.store.feature_values[(dataset_id, BASIS_FEATURE_ID)] = 10
        harness.store.feature_values[(dataset_id, OPEN_INTEREST_FEATURE_ID)] = 1
    return window


class SealedDatasetIdentityAuthorityTests(unittest.TestCase):
    """A predictable version string must never bypass the 3B.1 replay identity proof."""

    def test_exact_sealed_window_without_features_is_complete(self) -> None:
        harness = Harness(authorization=_authorization(materialize_features=False))
        _seed_sealed(harness, 0)
        result = harness.runner().run(_at(12))  # exactly W0 eligible
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(result.windows_completed_before, 1)
        self.assertEqual(result.windows_sealed_conflicting, 0)
        self.assertEqual(result.window_outcomes, ())
        self.assertEqual(harness.transport.urls, [])

    def test_exact_sealed_window_with_complete_features_is_skipped_with_zero_fetches(self) -> None:
        harness = Harness()
        _seed_sealed(harness, 0, features_complete=True)
        result = harness.runner().run(_at(12))
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(result.windows_completed_before, 1)
        self.assertEqual(result.windows_feature_incomplete, 0)
        self.assertEqual(result.windows_considered, ())
        self.assertEqual(harness.transport.urls, [])
        self.assertEqual(harness.lock.claimed, [])

    def test_exact_sealed_window_with_incomplete_features_resumes_features_only(self) -> None:
        harness = Harness()
        window = _seed_sealed(harness, 0)
        result = harness.runner().run(_at(12))
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.UP_TO_DATE)
        self.assertEqual(result.windows_feature_incomplete, 1)
        self.assertEqual(result.windows_sealed_conflicting, 0)
        (outcome,) = result.window_outcomes
        self.assertEqual(outcome.window, window)
        self.assertTrue(outcome.already_completed)
        self.assertEqual((outcome.basis_feature_count, outcome.open_interest_change_feature_count), (10, 1))
        self.assertIsNone(outcome.provider_health_status)
        self.assertEqual(harness.transport.urls, [])
        self.assertEqual(harness.store.datasets, {})  # nothing re-sealed
        # Now complete: a later invocation skips it outright.
        again = harness.runner().run(_at(12))
        self.assertEqual((again.windows_completed_before, again.windows_considered), (1, ()))

    def test_version_name_alone_never_proves_completion(self) -> None:
        wrong_instrument = frozenset({"CRYPTO:BYBIT:ETHUSDT:PERP"})
        wrong_symbol = frozenset({"ETHUSDT"})
        cases: dict[str, dict[str, object]] = {
            "normalization_version": {"normalization_version": "some-other-normalization"},
            "instrument_identity": {"instrument_ids": wrong_instrument},
            "provider_identifier": {"provider_identifiers": wrong_symbol},
            "provider_symbol": {"provider_symbols": wrong_symbol},
            "wrong_window_timestamps": {"shift_ohlcv_by": timedelta(minutes=10)},
            "extra_member_at_covered_timestamp": {"member_count_overrides": {ObservationKind.OHLCV: 11}},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                harness = Harness()
                window = _seed_sealed(harness, 0, features_complete=True, **overrides)  # type: ignore[arg-type]
                key = (SOURCE_ID, scheduled_dataset_version(harness.authorization, window))
                seeded = harness.store.forced_views[key]
                # Even a perfectly healthy NEWER sealed window must not be reached.
                _seed_sealed(harness, 1, features_complete=True)

                result = harness.runner().run(_at(22))  # W0 and W1 eligible

                self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
                self.assertFalse(result.succeeded)
                self.assertEqual(result.windows_sealed_conflicting, 1)
                self.assertEqual(result.windows_completed_before, 1)  # only the exact W1
                (failed,) = result.window_outcomes
                self.assertEqual(failed.window, window)
                self.assertEqual(failed.status, AcquisitionStatus.SEAL_FAILED)
                self.assertTrue((failed.failure_code or "").startswith("dataset_version_conflict"))
                # Canonical replay refused it without any provider fetch...
                self.assertEqual(harness.transport.urls, [])
                self.assertEqual(harness.store.checkpoints, [])
                # ...never repaired/overwrote/renamed it, and never sealed anything new.
                self.assertIs(harness.store.forced_views[key], seeded)
                self.assertEqual(harness.store.datasets, {})
                self.assertEqual(harness.lock.claimed, [window_lock_key(harness.authorization, window)])
                summary = result.summary()
                self.assertEqual(summary["first_failed_status"], "SEAL_FAILED")
                self.assertEqual(summary["windows_sealed_conflicting"], "1")
                with self.assertRaises(JobExecutionFailed) as caught:
                    harness.runner().as_job_runner()(None, _at(22))  # type: ignore[arg-type]
                self.assertEqual(caught.exception.summary["outcome"], "WINDOW_FAILED")
                self.assertEqual(harness.transport.urls, [])

    def test_conflicting_window_is_not_skipped_even_when_newer_windows_are_missing(self) -> None:
        harness = Harness()
        _seed_sealed(harness, 0, normalization_version="some-other-normalization")
        result = harness.runner().run(_at(32))  # W0, W1, W2 eligible; W1/W2 unsealed
        self.assertEqual(result.outcome, ScheduledAcquisitionOutcome.WINDOW_FAILED)
        self.assertEqual([item.window.index for item in result.window_outcomes], [0])
        self.assertEqual(harness.transport.urls, [])
        self.assertEqual(result.backlog_remaining, 3)


class BybitAdapterPacingTests(unittest.TestCase):
    def test_default_adapter_paces_to_the_configured_interval(self) -> None:
        clock = FakeClock(_at(60))
        transport = SyntheticBybitTransport()
        pacer = MinimumIntervalRequestPacer(INTERVAL, monotonic=clock.monotonic, sleep=clock.sleep)
        adapter = BybitCryptoHistoricalAdapter(
            _configuration(), transport=transport, now=lambda: clock.now, sleep=clock.sleep, pacer=pacer
        )
        scope = {
            "observation_kind": "OHLCV",
            "category": "linear",
            "symbol": BYBIT_BTCUSDT_SYMBOL,
            "interval": "1",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "settlement_asset": "USDT",
            "start": _at(0).isoformat(),
            "end": _at(10).isoformat(),
        }
        adapter.fetch_raw_page(SOURCE_ID, scope, None)
        adapter.fetch_raw_page(SOURCE_ID, scope, None)
        self.assertEqual(clock.sleeps, [0.5])

    def test_zero_interval_keeps_phase3b1_behaviour(self) -> None:
        sleeps: list[float] = []
        adapter = BybitCryptoHistoricalAdapter(
            _configuration(minimum_request_interval=timedelta(0)),
            transport=SyntheticBybitTransport(),
            now=lambda: _at(60),
            sleep=sleeps.append,
        )
        scope = {
            "observation_kind": "OPEN_INTEREST",
            "category": "linear",
            "symbol": BYBIT_BTCUSDT_SYMBOL,
            "interval": "5min",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "settlement_asset": "USDT",
            "start": _at(0).isoformat(),
            "end": _at(10).isoformat(),
        }
        for _ in range(3):
            adapter.fetch_raw_page(SOURCE_ID, scope, None)
        self.assertEqual(sleeps, [])

    def test_conflicting_shared_pacer_is_rejected(self) -> None:
        with self.assertRaises(ProviderConfigurationError):
            BybitCryptoHistoricalAdapter(
                _configuration(),
                transport=SyntheticBybitTransport(),
                pacer=MinimumIntervalRequestPacer(timedelta(seconds=5)),
            )


if __name__ == "__main__":
    unittest.main()
