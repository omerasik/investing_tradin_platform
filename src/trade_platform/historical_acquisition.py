"""Operator-controlled real historical acquisition workflow (Phase 3B.1).

This module turns the proven Phase 3A one-off scratchpad pilot -- operator
request -> provider ingestion -> checkpoint -> normalization -> completeness
validation -> one sealed dataset -> optional canonical feature materialization
-- into a single, reusable *application service* that the caller invokes with
one request instead of hand-orchestrating every step. The first (and, in this
phase, only) supported configuration is the already-onboarded Bybit BTCUSDT
linear perpetual (:mod:`trade_platform.bybit_instrument_onboarding`).

Hard scope limits, all deliberate and all preserved from the pilot:

* **Explicit operator invocation only.** Nothing here is registered with
  :func:`trade_platform.scheduler.default_job_registry`, reachable from worker
  startup, or wired into any recurring job. Scheduling/cadence is a later phase;
  Phase 3B.1 requires an operator to call :meth:`HistoricalAcquisitionService.
  acquire` directly with an already-constructed :class:`ProviderConfiguration`.
* **No new HTTP or pagination logic.** Acquisition is delegated verbatim to the
  existing :class:`~trade_platform.bybit_crypto_provider.BybitCryptoHistoricalAdapter`
  and :func:`~trade_platform.provider_ingestion.ingest_raw_historical_pages`; the
  existing :class:`~trade_platform.historical_market_data.PostgresHistoricalMarketDataPipeline`
  owns capture, normalization and sealing; the existing crypto-derivatives and
  open-interest calculators own feature materialization. This service composes
  them, it does not reimplement them.
* **No funding.** Only ``OHLCV``, ``MARK_PRICE``, ``INDEX_PRICE`` and
  ``OPEN_INTEREST`` may be requested; funding kinds are rejected before any
  network call. No account/order endpoint, no websocket, no volume semantics.
* **No fabricated data.** Completeness is validated against the request window's
  own fixed-grid timestamp semantics (1-minute klines, 5-minute open interest);
  a missing required provider point blocks sealing and no bar is synthesised,
  interpolated, forward-filled or nearest-matched.

**Durable idempotency without new schema.** The already-existing sealed-dataset
identity is the durable idempotency ledger: ``historical_dataset_versions``
carries both ``UNIQUE(source_id, version)`` and ``UNIQUE(content_hash)``, and
``historical_raw_observations`` deduplicates on
``(source_id, provider_identifier, observation_kind, event_at, revision)``. A
repeated acquisition therefore re-captures the *same* immutable raw rows and the
sealed dataset for a ``(source_id, dataset_version)`` pair can exist at most
once. The caller-supplied ``idempotency_key`` is bound to the request semantics
by requiring it to equal :func:`acquisition_fingerprint` -- so re-using one key
with a different window, source, instrument, provider symbol, kind set,
normalization version or dataset version fails closed as a precondition
mismatch, and an identical already-completed acquisition is proven purely from
the existing sealed dataset and returned unchanged. No acquisition-run table is
required and none is added.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from .bybit_crypto_provider import (
    BYBIT_EXCHANGE,
    BYBIT_KLINE_INTERVAL,
    BYBIT_LINEAR_CATEGORY,
    BYBIT_OPEN_INTEREST_INTERVAL,
    BYBIT_PROVIDER_NAME,
    BYBIT_V5_SYMBOL_NAMESPACE,
    BybitCryptoHistoricalAdapter,
)
from .bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    BYBIT_BTCUSDT_SYMBOL,
)
from .crypto_derivatives_features import (
    CRYPTO_MARK_INDEX_BASIS,
    PostgresCryptoDerivativesFeatureCalculator,
    crypto_mark_index_basis_definition,
)
from .crypto_instruments import (
    CryptoInstrumentError,
    CryptoInstrumentKind,
    PostgresCryptoInstrumentAuthority,
    ReferencePriceRequirement,
    SettlementStyle,
)
from .data_providers import (
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderHealthRegistry,
    ProviderOperationalStatus,
)
from .feature_authority import FeatureDefinitionVersion, PostgresFeatureAuthority
from .historical_market_data import (
    AssetScope,
    HistoricalDataQualityError,
    HistoricalDataResolutionError,
    HistoricalDatasetVersion,
    HistoricalMarketDataError,
    NormalizedHistoricalObservation,
    ObservationKind,
    PostgresHistoricalMarketDataPipeline,
    QualityStatus,
    RawHistoricalObservation,
)
from .open_interest_features import (
    OPEN_INTEREST_CHANGE,
    PostgresOpenInterestFeatureCalculator,
    open_interest_change_definition,
)
from .persistence import PostgresDatabase
from .provider_ingestion import (
    HistoricalIngestionRequest,
    ProviderIngestionCheckpoint,
    RawHistoricalAdapter,
    RawHistoricalCaptureSink,
    ingest_raw_historical_pages,
)

__all__ = [
    "AcquisitionCheckpointRef",
    "AcquisitionStatus",
    "CanonicalAcquisitionEvidence",
    "CryptoSpecView",
    "FeatureMaterializer",
    "HistoricalAcquisitionError",
    "HistoricalAcquisitionPipeline",
    "HistoricalAcquisitionRequest",
    "HistoricalAcquisitionResult",
    "HistoricalAcquisitionService",
    "IngestionCheckpointStore",
    "MaterializedFeatureCounts",
    "PostgresAcquisitionFeatureMaterializer",
    "PostgresCanonicalAcquisitionEvidence",
    "SealedDatasetView",
    "SourceProfile",
    "acquisition_fingerprint",
]


# ---------------------------------------------------------------------------
# Fixed Bybit v1 acquisition constants
# ---------------------------------------------------------------------------

#: The only observation kinds a Bybit v1 acquisition may request. Funding is
#: excluded here exactly as it is excluded from the onboarded source's
#: capabilities: see :data:`trade_platform.bybit_instrument_onboarding.
#: BYBIT_AUTHORIZED_OBSERVATION_KINDS`.
ALLOWED_OBSERVATION_KINDS: frozenset[ObservationKind] = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)

#: The deterministic order kinds are acquired in. A failure on one kind stops
#: every later kind, so the order is part of the contract, not incidental.
_ACQUISITION_KIND_ORDER: tuple[ObservationKind, ...] = (
    ObservationKind.OHLCV,
    ObservationKind.MARK_PRICE,
    ObservationKind.INDEX_PRICE,
    ObservationKind.OPEN_INTEREST,
)

#: Fixed-grid interval semantics of the supported Bybit v1 endpoints.
_KLINE_KINDS: frozenset[ObservationKind] = frozenset(
    {ObservationKind.OHLCV, ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE}
)
_KLINE_GRID: timedelta = timedelta(minutes=1)
_OPEN_INTEREST_GRID: timedelta = timedelta(minutes=5)
_ZERO: timedelta = timedelta(0)

#: The exact canonical crypto contract identity this workflow requires. Every
#: value is rechecked against the resolved 3H.2 specification before any network
#: call, so a request can never acquire data for a different contract.
_REQUIRED_INSTRUMENT_KIND = CryptoInstrumentKind.PERPETUAL
_REQUIRED_VENUE = BYBIT_EXCHANGE
_REQUIRED_BASE_ASSET = "BTC"
_REQUIRED_QUOTE_ASSET = "USDT"
_REQUIRED_SETTLEMENT_ASSET = "USDT"
_REQUIRED_SETTLEMENT_STYLE = SettlementStyle.LINEAR
_REQUIRED_REFERENCE_PRICE_REQUIREMENT = ReferencePriceRequirement.MARK_AND_INDEX


class HistoricalAcquisitionError(ValueError):
    """Base class for programmer-visible misuse of this module's public API."""


class AcquisitionStatus(StrEnum):
    """The single terminal status of one acquisition attempt.

    Exactly one value is ``SUCCEEDED``; every other value names the boundary at
    which a fail-closed acquisition stopped. There is never a partial success.
    """

    SUCCEEDED = "SUCCEEDED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    PAGINATION_FAILED = "PAGINATION_FAILED"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    QUALITY_FAILED = "QUALITY_FAILED"
    COVERAGE_FAILED = "COVERAGE_FAILED"
    SEAL_FAILED = "SEAL_FAILED"
    FEATURE_FAILED = "FEATURE_FAILED"


# ---------------------------------------------------------------------------
# Request / result contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HistoricalAcquisitionRequest:
    """One fully-bound operator request for a real historical acquisition.

    The window must be timezone-aware UTC with ``start < end``; windows are never
    silently widened. ``idempotency_key`` must equal :func:`acquisition_fingerprint`
    of this exact request, which is what binds a re-used key to identical request
    semantics (see the module docstring).
    """

    source_id: UUID
    instrument_id: str
    provider: str
    provider_symbol: str
    start: datetime
    end: datetime
    observation_kinds: frozenset[ObservationKind]
    normalization_version: str
    dataset_version: str
    maximum_pages_per_kind: int
    materialize_features: bool
    idempotency_key: str

    def ordered_kinds(self) -> tuple[ObservationKind, ...]:
        """Requested kinds in the deterministic acquisition order."""
        return tuple(kind for kind in _ACQUISITION_KIND_ORDER if kind in self.observation_kinds)


@dataclass(frozen=True, slots=True)
class AcquisitionCheckpointRef:
    """The durable checkpoint recorded for one attempted observation kind."""

    observation_kind: ObservationKind
    checkpoint_id: UUID
    state: ProviderOperationalStatus


@dataclass(frozen=True, slots=True)
class MaterializedFeatureCounts:
    """How many canonical feature values each requested calculator materialized."""

    crypto_mark_index_basis: int
    open_interest_change: int


@dataclass(frozen=True, slots=True)
class HistoricalAcquisitionResult:
    """The immutable outcome/evidence object of one acquisition attempt."""

    status: AcquisitionStatus
    idempotency_key: str
    source_id: UUID
    instrument_id: str
    dataset_version: str
    already_completed: bool
    dataset_version_id: UUID | None = None
    dataset_content_hash: str | None = None
    raw_counts: Mapping[ObservationKind, int] = field(default_factory=dict)
    normalized_counts: Mapping[ObservationKind, int] = field(default_factory=dict)
    rejected_count: int = 0
    checkpoints: tuple[AcquisitionCheckpointRef, ...] = ()
    provider_health_status: ProviderOperationalStatus | None = None
    feature_counts: MaterializedFeatureCounts | None = None
    failure_code: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is AcquisitionStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Canonical evidence views + collaborator protocols
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """The authorized-source facts the identity check consults."""

    provider: str
    provider_identifier_namespace: str
    asset_scope: str
    capabilities: frozenset[ObservationKind]


@dataclass(frozen=True, slots=True)
class CryptoSpecView:
    """The 3H.2 crypto specification fields the identity check consults."""

    kind: CryptoInstrumentKind
    venue: str
    base_asset: str
    quote_asset: str
    # The 3H.2 specification models these two as optional; a linear perpetual has
    # both, so ``None`` simply fails the exact-identity comparison closed.
    settlement_asset: str | None
    settlement_style: SettlementStyle | None
    reference_price_requirement: ReferencePriceRequirement


@dataclass(frozen=True, slots=True)
class SealedDatasetView:
    """An already-sealed dataset, used to prove idempotent replay or conflict."""

    dataset_version_id: UUID
    content_hash: str
    normalization_version: str
    valid_from: datetime
    valid_until: datetime
    created_at: datetime
    counts_by_kind: Mapping[ObservationKind, int]


class CanonicalAcquisitionEvidence(Protocol):
    """Read-only canonical evidence the service resolves identity/idempotency through."""

    def source_profile(self, source_id: UUID) -> SourceProfile | None: ...

    def resolve_instrument_ids(
        self, namespace: str, provider_symbol: str, known_at: datetime
    ) -> tuple[str, ...]: ...

    def crypto_specification(
        self, instrument_id: str, known_at: datetime
    ) -> CryptoSpecView | None: ...

    def existing_sealed_dataset(
        self, source_id: UUID, version: str
    ) -> SealedDatasetView | None: ...

    def feature_counts(
        self, dataset_version_id: UUID, feature_names: tuple[str, ...]
    ) -> Mapping[str, int]: ...


class HistoricalAcquisitionPipeline(Protocol):
    """The capture/normalize/seal surface of the existing historical pipeline."""

    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]: ...

    def normalize(
        self, raw_observation_id: UUID, normalization_version: str, normalized_at: datetime
    ) -> NormalizedHistoricalObservation: ...

    def seal_dataset(
        self,
        source_id: UUID,
        version: str,
        normalization_version: str,
        normalized_ids: tuple[UUID, ...],
        created_at: datetime,
    ) -> HistoricalDatasetVersion: ...


class IngestionCheckpointStore(Protocol):
    """The append-only checkpoint sink of the existing ingestion authority."""

    def record(self, checkpoint: ProviderIngestionCheckpoint) -> None: ...


class FeatureMaterializer(Protocol):
    """Materializes the canonical derivatives features on one sealed dataset."""

    def materialize_features(
        self,
        *,
        instrument_id: str,
        dataset_version_id: UUID,
        basis_event_ats: tuple[datetime, ...],
        open_interest_event_ats: tuple[datetime, ...],
        decision_at: datetime,
        definition_created_at: datetime,
    ) -> MaterializedFeatureCounts: ...


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def acquisition_fingerprint(request: HistoricalAcquisitionRequest) -> str:
    """Deterministic SHA-256 over the semantics an idempotency key must bind.

    Any change to the window, source, instrument, provider symbol, requested
    kind set, normalization version or dataset version produces a different
    fingerprint. The key is verified against this value, so it is impossible to
    present one key with two different sets of semantics.
    """
    kinds = ",".join(sorted(kind.value for kind in request.observation_kinds))
    canonical = "|".join(
        (
            "trade_platform.historical_acquisition.v1",
            request.provider,
            str(request.source_id),
            request.instrument_id,
            request.provider_symbol,
            request.start.astimezone(UTC).isoformat(),
            request.end.astimezone(UTC).isoformat(),
            kinds,
            request.normalization_version,
            request.dataset_version,
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Internal control flow
# ---------------------------------------------------------------------------


class _Abort(Exception):
    """Internal fail-closed signal carrying the boundary status and its code."""

    def __init__(self, status: AcquisitionStatus, code: str, *, rejected_count: int = 0) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.rejected_count = rejected_count


def _require_aware_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, f"{name}_must_be_timezone_aware")
    if value.utcoffset() != _ZERO:
        raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, f"{name}_must_be_utc")


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, code)


# ---------------------------------------------------------------------------
# Recording capture sink
# ---------------------------------------------------------------------------


class _RecordingCaptureSink:
    """Wraps the pipeline's capture so the service knows exactly what it captured.

    ``capture_raw`` returns one persisted id per input observation (an existing
    id for a deduplicated row), so zipping the inputs with the result records the
    ``(kind, event_at)`` of every captured row. That is the only evidence the
    coverage check reads -- never a source-wide historical scan.
    """

    def __init__(self, inner: RawHistoricalCaptureSink) -> None:
        self._inner = inner
        self.records: list[tuple[UUID, ObservationKind, datetime]] = []

    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]:
        ids = self._inner.capture_raw(observations)
        for observation, raw_id in zip(observations, ids, strict=True):
            self.records.append((raw_id, observation.observation_kind, observation.event_at))
        return ids


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def _expected_event_ats(
    kind: ObservationKind, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """The exact event timestamps a complete aligned window must contain.

    Derived from the request window and each kind's fixed-grid interval
    semantics, never from any observed row count. ``OHLCV`` events are the
    1-minute bar opens ``[start, end)``; ``MARK_PRICE``/``INDEX_PRICE`` events are
    the matching bar closes ``(start, end]``; ``OPEN_INTEREST`` events are the
    5-minute grid instants ``[start, end)``.
    """
    if kind is ObservationKind.OPEN_INTEREST:
        instants: list[datetime] = []
        instant = start
        while instant < end:
            instants.append(instant)
            instant += _OPEN_INTEREST_GRID
        return tuple(instants)
    total_minutes = int((end - start) / _KLINE_GRID)
    if kind is ObservationKind.OHLCV:
        return tuple(start + index * _KLINE_GRID for index in range(total_minutes))
    return tuple(start + (index + 1) * _KLINE_GRID for index in range(total_minutes))


def _validate_window_alignment(request: HistoricalAcquisitionRequest) -> None:
    """Reject any window that is not exactly aligned to every requested grid.

    A misaligned window has no well-defined expected timestamp set, so it is
    refused before any network call rather than acquired and then failed for a
    coverage gap it would always exhibit.
    """
    if request.start.second != 0 or request.start.microsecond != 0:
        raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "window_start_not_minute_aligned")
    span = request.end - request.start
    if span % _KLINE_GRID != _ZERO:
        raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "window_not_whole_minutes")
    if request.observation_kinds & _KLINE_KINDS:
        # klines always align to the minute grid the window is already checked
        # against; nothing further is required for them.
        pass
    if ObservationKind.OPEN_INTEREST in request.observation_kinds:
        if request.start.minute % 5 != 0:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED, "window_start_not_open_interest_aligned"
            )
        if span % _OPEN_INTEREST_GRID != _ZERO:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED, "window_span_not_open_interest_aligned"
            )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HistoricalAcquisitionService:
    """The single canonical operator-invoked historical acquisition service.

    One :meth:`acquire` call performs authorization/config validation, canonical
    identity resolution, provider ingestion with checkpoint persistence,
    normalization, completeness/quality validation, exactly one sealed dataset,
    optional canonical feature materialization and an immutable result -- so the
    caller never orchestrates the pipeline by hand. It is never registered with
    any scheduler and never runs at worker startup.
    """

    def __init__(
        self,
        *,
        evidence: CanonicalAcquisitionEvidence,
        pipeline: HistoricalAcquisitionPipeline,
        checkpoint_store: IngestionCheckpointStore,
        feature_materializer: FeatureMaterializer,
        adapter_factory: Callable[
            [ProviderConfiguration, Callable[[], datetime]], RawHistoricalAdapter
        ]
        | None = None,
        health_factory: Callable[
            [Callable[[], datetime]], ProviderHealthRegistry
        ] = ProviderHealthRegistry,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._evidence = evidence
        self._pipeline = pipeline
        self._checkpoint_store = checkpoint_store
        self._feature_materializer = feature_materializer
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._health_factory = health_factory
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def for_postgres(
        cls,
        database: PostgresDatabase,
        *,
        adapter_factory: Callable[
            [ProviderConfiguration, Callable[[], datetime]], RawHistoricalAdapter
        ]
        | None = None,
        feature_materializer: FeatureMaterializer | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> HistoricalAcquisitionService:
        """Wire the real PostgreSQL-backed collaborators around one database."""
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        return cls(
            evidence=PostgresCanonicalAcquisitionEvidence(database),
            pipeline=pipeline,
            checkpoint_store=_PostgresCheckpointStore(database),
            feature_materializer=feature_materializer
            or PostgresAcquisitionFeatureMaterializer(database),
            adapter_factory=adapter_factory or _default_adapter_factory,
            now=now,
        )

    def acquire(
        self, request: HistoricalAcquisitionRequest, configuration: ProviderConfiguration
    ) -> HistoricalAcquisitionResult:
        """Run one operator-authorized acquisition and return its evidence object.

        Every enumerated failure boundary produces a fail-closed
        :class:`HistoricalAcquisitionResult` rather than raising; the sealed
        dataset is written only when normalization, quality and completeness all
        pass, and features are materialized only after a successful seal.
        """
        checkpoints: list[AcquisitionCheckpointRef] = []
        raw_counts: dict[ObservationKind, int] = {}
        normalized_counts: dict[ObservationKind, int] = {}
        health_status: ProviderOperationalStatus | None = None
        try:
            self._validate_request(request)
            self._validate_configuration(request, configuration)
            spec = self._validate_identity(request)
            _validate_window_alignment(request)

            replay = self._replay_if_complete(request)
            if replay is not None:
                return replay

            adapter = self._adapter_factory(configuration, self._now)
            health = self._health_factory(self._now)
            sink = _RecordingCaptureSink(self._pipeline)

            captured: dict[ObservationKind, tuple[UUID, ...]] = {}
            for kind in request.ordered_kinds():
                outcome = ingest_raw_historical_pages(
                    adapter,
                    HistoricalIngestionRequest(
                        source_id=request.source_id,
                        scope=self._scope(request, spec, kind),
                        maximum_pages=request.maximum_pages_per_kind,
                    ),
                    sink,
                    health,
                    now=self._now,
                )
                self._checkpoint_store.record(outcome.checkpoint)
                checkpoints.append(
                    AcquisitionCheckpointRef(
                        kind, outcome.checkpoint.checkpoint_id, outcome.state
                    )
                )
                health_status = _health_status(health)
                raw_counts[kind] = len(outcome.captured_raw_ids)
                if outcome.state is not ProviderOperationalStatus.HEALTHY:
                    # Stop immediately; no later kind is attempted, nothing is
                    # sealed, and already-captured raw evidence stays immutable.
                    raise _Abort(_ingestion_boundary(outcome.state, outcome.checkpoint), _ingestion_code(outcome.checkpoint))
                captured[kind] = outcome.captured_raw_ids

            self._reject_duplicate_timestamps(sink)
            normalized_ids = self._normalize(request, captured, normalized_counts)
            self._validate_coverage(request, sink)

            dataset = self._seal(request, normalized_ids)
            feature_counts = self._maybe_materialize_features(request, spec, dataset)

            return HistoricalAcquisitionResult(
                status=AcquisitionStatus.SUCCEEDED,
                idempotency_key=request.idempotency_key,
                source_id=request.source_id,
                instrument_id=request.instrument_id,
                dataset_version=request.dataset_version,
                already_completed=False,
                dataset_version_id=dataset.dataset_version_id,
                dataset_content_hash=dataset.content_hash,
                raw_counts=dict(raw_counts),
                normalized_counts=dict(normalized_counts),
                rejected_count=0,
                checkpoints=tuple(checkpoints),
                provider_health_status=health_status,
                feature_counts=feature_counts,
            )
        except _Abort as abort:
            return HistoricalAcquisitionResult(
                status=abort.status,
                idempotency_key=request.idempotency_key,
                source_id=request.source_id,
                instrument_id=request.instrument_id,
                dataset_version=request.dataset_version,
                already_completed=False,
                raw_counts=dict(raw_counts),
                normalized_counts=dict(normalized_counts),
                rejected_count=abort.rejected_count,
                checkpoints=tuple(checkpoints),
                provider_health_status=health_status,
                failure_code=abort.code,
            )

    # ---- validation --------------------------------------------------------

    def _validate_request(self, request: HistoricalAcquisitionRequest) -> None:
        _require(request.provider == BYBIT_PROVIDER_NAME, "unsupported_provider")
        _require(
            request.instrument_id == BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
            "unsupported_instrument",
        )
        _require(request.provider_symbol == BYBIT_BTCUSDT_SYMBOL, "unsupported_provider_symbol")
        _require(bool(request.observation_kinds), "no_observation_kind_requested")
        funding = {
            ObservationKind.FUNDING_RATE_REALIZED,
            ObservationKind.FUNDING_RATE_INDICATIVE,
        } & request.observation_kinds
        if funding:
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "funding_not_supported")
        outside = request.observation_kinds - ALLOWED_OBSERVATION_KINDS
        if outside:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED,
                "unsupported_observation_kind:"
                + ",".join(sorted(kind.value for kind in outside)),
            )
        _require(bool(request.normalization_version.strip()), "invalid_normalization_version")
        _require(bool(request.dataset_version.strip()), "invalid_dataset_version")
        _require(request.maximum_pages_per_kind >= 1, "maximum_pages_per_kind_must_be_positive")
        _require_aware_utc(request.start, "start")
        _require_aware_utc(request.end, "end")
        _require(request.start < request.end, "invalid_acquisition_window")
        _require(bool(request.idempotency_key.strip()), "invalid_idempotency_key")
        if request.idempotency_key != acquisition_fingerprint(request):
            # A key that does not bind these exact semantics is refused, which is
            # how "same key, different semantics" fails closed as a conflict.
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED,
                "idempotency_key_does_not_bind_request_semantics",
            )
        if self._now() < request.end:
            # The provider adapter never returns a bar whose close is after the
            # retrieval instant; acquiring before the window closes would silently
            # drop the tail. Refuse rather than under-cover.
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "acquisition_clock_before_window_end")

    def _validate_configuration(
        self, request: HistoricalAcquisitionRequest, configuration: ProviderConfiguration
    ) -> None:
        try:
            configuration.validate()  # https base_url, positive timeout, non-negative interval
        except ProviderConfigurationError as error:
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, str(error)) from error
        _require(configuration.provider == BYBIT_PROVIDER_NAME, "configuration_provider_mismatch")
        _require(configuration.base_url.startswith("https://"), "configuration_requires_https")
        # Never internally flip terms_accepted; the operator opts in explicitly.
        if not configuration.terms_accepted:
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "provider_terms_not_accepted")
        if configuration.secret_reference is not None:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED, "public_bybit_v1_requires_no_secret"
            )

    def _validate_identity(self, request: HistoricalAcquisitionRequest) -> CryptoSpecView:
        known_at = self._now()
        profile = self._evidence.source_profile(request.source_id)
        if profile is None:
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "historical_source_not_found")
        _require(profile.provider == BYBIT_PROVIDER_NAME, "source_provider_mismatch")
        _require(
            profile.provider_identifier_namespace == BYBIT_V5_SYMBOL_NAMESPACE,
            "source_identifier_namespace_mismatch",
        )
        _require(profile.asset_scope == AssetScope.CRYPTO.value, "source_asset_scope_mismatch")
        missing = request.observation_kinds - profile.capabilities
        if missing:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED,
                "source_not_authorized_for_kind:"
                + ",".join(sorted(kind.value for kind in missing)),
            )

        resolved = self._evidence.resolve_instrument_ids(
            BYBIT_V5_SYMBOL_NAMESPACE, request.provider_symbol, known_at
        )
        if not resolved:
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "provider_symbol_unresolved")
        if len(set(resolved)) > 1:
            raise _Abort(AcquisitionStatus.PRECONDITION_FAILED, "provider_symbol_ambiguous")
        if resolved[0] != request.instrument_id:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED, "provider_symbol_resolves_to_other_instrument"
            )

        spec = self._evidence.crypto_specification(request.instrument_id, known_at)
        if spec is None:
            raise _Abort(
                AcquisitionStatus.PRECONDITION_FAILED, "instrument_has_no_crypto_specification"
            )
        _require(spec.kind is _REQUIRED_INSTRUMENT_KIND, "instrument_not_perpetual")
        _require(spec.venue == _REQUIRED_VENUE, "instrument_venue_mismatch")
        _require(spec.base_asset == _REQUIRED_BASE_ASSET, "instrument_base_asset_mismatch")
        _require(spec.quote_asset == _REQUIRED_QUOTE_ASSET, "instrument_quote_asset_mismatch")
        _require(
            spec.settlement_asset == _REQUIRED_SETTLEMENT_ASSET, "instrument_settlement_asset_mismatch"
        )
        _require(
            spec.settlement_style is _REQUIRED_SETTLEMENT_STYLE, "instrument_settlement_style_mismatch"
        )
        _require(
            spec.reference_price_requirement is _REQUIRED_REFERENCE_PRICE_REQUIREMENT,
            "instrument_reference_price_requirement_mismatch",
        )
        return spec

    # ---- idempotent replay -------------------------------------------------

    def _replay_if_complete(
        self, request: HistoricalAcquisitionRequest
    ) -> HistoricalAcquisitionResult | None:
        existing = self._evidence.existing_sealed_dataset(
            request.source_id, request.dataset_version
        )
        if existing is None:
            return None
        expected = {
            kind: _expected_event_ats(kind, request.start, request.end)
            for kind in request.ordered_kinds()
        }
        all_events = tuple(sorted({event for events in expected.values() for event in events}))
        if not all_events:
            raise _Abort(AcquisitionStatus.SEAL_FAILED, "dataset_version_conflict:empty_expectation")
        conflicts = (
            existing.normalization_version != request.normalization_version
            or existing.valid_from != all_events[0]
            or existing.valid_until != all_events[-1]
            or dict(existing.counts_by_kind)
            != {kind: len(events) for kind, events in expected.items()}
        )
        if conflicts:
            raise _Abort(
                AcquisitionStatus.SEAL_FAILED,
                "dataset_version_conflict:existing_dataset_semantics_differ",
            )
        feature_counts: MaterializedFeatureCounts | None = None
        if request.materialize_features:
            counts = self._evidence.feature_counts(
                existing.dataset_version_id, (CRYPTO_MARK_INDEX_BASIS, OPEN_INTEREST_CHANGE)
            )
            feature_counts = MaterializedFeatureCounts(
                crypto_mark_index_basis=counts.get(CRYPTO_MARK_INDEX_BASIS, 0),
                open_interest_change=counts.get(OPEN_INTEREST_CHANGE, 0),
            )
        return HistoricalAcquisitionResult(
            status=AcquisitionStatus.SUCCEEDED,
            idempotency_key=request.idempotency_key,
            source_id=request.source_id,
            instrument_id=request.instrument_id,
            dataset_version=request.dataset_version,
            already_completed=True,
            dataset_version_id=existing.dataset_version_id,
            dataset_content_hash=existing.content_hash,
            raw_counts=dict(existing.counts_by_kind),
            normalized_counts=dict(existing.counts_by_kind),
            rejected_count=0,
            provider_health_status=None,
            feature_counts=feature_counts,
        )

    # ---- acquisition helpers ----------------------------------------------

    @staticmethod
    def _scope(
        request: HistoricalAcquisitionRequest, spec: CryptoSpecView, kind: ObservationKind
    ) -> dict[str, object]:
        interval = (
            BYBIT_OPEN_INTEREST_INTERVAL
            if kind is ObservationKind.OPEN_INTEREST
            else BYBIT_KLINE_INTERVAL
        )
        return {
            "observation_kind": kind.value,
            "category": BYBIT_LINEAR_CATEGORY,
            "symbol": request.provider_symbol,
            "interval": interval,
            "base_asset": spec.base_asset,
            "quote_asset": spec.quote_asset,
            "settlement_asset": spec.settlement_asset,
            "start": request.start.astimezone(UTC).isoformat(),
            "end": request.end.astimezone(UTC).isoformat(),
        }

    @staticmethod
    def _reject_duplicate_timestamps(sink: _RecordingCaptureSink) -> None:
        seen: set[tuple[ObservationKind, datetime]] = set()
        for _raw_id, kind, event_at in sink.records:
            key = (kind, event_at)
            if key in seen:
                raise _Abort(
                    AcquisitionStatus.COVERAGE_FAILED,
                    f"duplicate_observation_timestamp:{kind.value}:{event_at.isoformat()}",
                )
            seen.add(key)

    def _normalize(
        self,
        request: HistoricalAcquisitionRequest,
        captured: Mapping[ObservationKind, tuple[UUID, ...]],
        normalized_counts: dict[ObservationKind, int],
    ) -> tuple[UUID, ...]:
        normalized_at = self._now()
        rejected = 0
        member_ids: list[UUID] = []
        for kind in request.ordered_kinds():
            normalized_counts[kind] = 0
            for raw_id in dict.fromkeys(captured.get(kind, ())):
                try:
                    result = self._pipeline.normalize(
                        raw_id, request.normalization_version, normalized_at
                    )
                except HistoricalDataQualityError as error:
                    raise _Abort(AcquisitionStatus.QUALITY_FAILED, str(error)) from error
                except (HistoricalDataResolutionError, HistoricalMarketDataError) as error:
                    raise _Abort(AcquisitionStatus.NORMALIZATION_FAILED, str(error)) from error
                if result.instrument_id != request.instrument_id:
                    raise _Abort(
                        AcquisitionStatus.NORMALIZATION_FAILED,
                        f"normalized_to_other_instrument:{result.instrument_id}",
                    )
                if result.quality_status is not QualityStatus.VALIDATED:
                    rejected += 1
                    raise _Abort(
                        AcquisitionStatus.QUALITY_FAILED,
                        "rejected_observation:" + ",".join(result.quality_issues),
                        rejected_count=rejected,
                    )
                normalized_counts[kind] += 1
                member_ids.append(result.normalized_observation_id)
        return tuple(member_ids)

    def _validate_coverage(
        self, request: HistoricalAcquisitionRequest, sink: _RecordingCaptureSink
    ) -> None:
        actual: dict[ObservationKind, set[datetime]] = {}
        for _raw_id, kind, event_at in sink.records:
            actual.setdefault(kind, set()).add(event_at)
        for kind in request.ordered_kinds():
            expected = set(_expected_event_ats(kind, request.start, request.end))
            observed = actual.get(kind, set())
            missing = expected - observed
            if missing:
                raise _Abort(
                    AcquisitionStatus.COVERAGE_FAILED,
                    f"missing_required_observation:{kind.value}:{len(missing)}",
                )
            extra = observed - expected
            if extra:
                raise _Abort(
                    AcquisitionStatus.COVERAGE_FAILED,
                    f"unexpected_out_of_window_observation:{kind.value}:{len(extra)}",
                )

    def _seal(
        self, request: HistoricalAcquisitionRequest, normalized_ids: tuple[UUID, ...]
    ) -> HistoricalDatasetVersion:
        try:
            return self._pipeline.seal_dataset(
                request.source_id,
                request.dataset_version,
                request.normalization_version,
                normalized_ids,
                self._now(),
            )
        except HistoricalMarketDataError as error:
            raise _Abort(AcquisitionStatus.SEAL_FAILED, str(error)) from error

    def _maybe_materialize_features(
        self,
        request: HistoricalAcquisitionRequest,
        spec: CryptoSpecView,
        dataset: HistoricalDatasetVersion,
    ) -> MaterializedFeatureCounts | None:
        if not request.materialize_features:
            return None
        basis_event_ats: tuple[datetime, ...] = ()
        if {ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE} <= request.observation_kinds:
            basis_event_ats = _expected_event_ats(
                ObservationKind.MARK_PRICE, request.start, request.end
            )
        open_interest_event_ats: tuple[datetime, ...] = ()
        if ObservationKind.OPEN_INTEREST in request.observation_kinds:
            open_interest_event_ats = _expected_event_ats(
                ObservationKind.OPEN_INTEREST, request.start, request.end
            )
        decision_at = self._now()
        try:
            return self._feature_materializer.materialize_features(
                instrument_id=request.instrument_id,
                dataset_version_id=dataset.dataset_version_id,
                basis_event_ats=basis_event_ats,
                open_interest_event_ats=open_interest_event_ats,
                decision_at=decision_at,
                definition_created_at=decision_at,
            )
        except Exception as error:
            # Any calculator failure is the feature boundary, never a partial success.
            raise _Abort(
                AcquisitionStatus.FEATURE_FAILED, str(error) or type(error).__name__
            ) from error


def _health_status(health: ProviderHealthRegistry) -> ProviderOperationalStatus | None:
    record = health.get(BYBIT_PROVIDER_NAME)
    return None if record is None else record.operational_status


def _ingestion_boundary(
    state: ProviderOperationalStatus, checkpoint: ProviderIngestionCheckpoint
) -> AcquisitionStatus:
    if (
        state is ProviderOperationalStatus.ERROR
        and checkpoint.error_code is not None
        and "pagination" in checkpoint.error_code
    ):
        return AcquisitionStatus.PAGINATION_FAILED
    return AcquisitionStatus.PROVIDER_FAILED


def _ingestion_code(checkpoint: ProviderIngestionCheckpoint) -> str:
    if checkpoint.error_code is not None:
        return checkpoint.error_code
    return f"provider_state:{checkpoint.state.value}"


def _default_adapter_factory(
    configuration: ProviderConfiguration, now: Callable[[], datetime]
) -> RawHistoricalAdapter:
    """Build the real Bybit adapter. Never invoked when a precondition fails."""
    return BybitCryptoHistoricalAdapter(configuration, now=now)


# ---------------------------------------------------------------------------
# PostgreSQL-backed collaborators
# ---------------------------------------------------------------------------


class _PostgresCheckpointStore:
    """Thin adapter so the service depends on the checkpoint protocol, not psycopg."""

    def __init__(self, database: PostgresDatabase) -> None:
        from .provider_ingestion import PostgresHistoricalIngestionCheckpointStore

        self._store = PostgresHistoricalIngestionCheckpointStore(database)

    def record(self, checkpoint: ProviderIngestionCheckpoint) -> None:
        self._store.record(checkpoint)


class PostgresCanonicalAcquisitionEvidence:
    """Reads canonical identity and sealed-dataset evidence from PostgreSQL.

    Every read is scoped and single-purpose; this class owns no write path to
    any table and introduces no second resolution authority. The crypto
    specification is read through the existing 3H.2 authority.
    """

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._crypto = PostgresCryptoInstrumentAuthority(database)

    def source_profile(self, source_id: UUID) -> SourceProfile | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT provider,provider_identifier_namespace,asset_scope "
                "FROM historical_data_sources WHERE source_id=%s",
                (source_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "SELECT observation_kind FROM historical_source_capabilities WHERE source_id=%s",
                (source_id,),
            )
            capability_rows = cursor.fetchall()
        return SourceProfile(
            provider=str(row[0]),
            provider_identifier_namespace=str(row[1]),
            asset_scope=str(row[2]),
            capabilities=frozenset(ObservationKind(str(item[0])) for item in capability_rows),
        )

    def resolve_instrument_ids(
        self, namespace: str, provider_symbol: str, known_at: datetime
    ) -> tuple[str, ...]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT instrument_id FROM professional_identifier_mappings "
                "WHERE namespace=%s AND identifier_value=%s AND valid_from<=%s "
                "AND (valid_until IS NULL OR valid_until>%s)",
                (namespace, provider_symbol, known_at, known_at),
            )
            rows = cursor.fetchall()
        return tuple(str(row[0]) for row in rows)

    def crypto_specification(
        self, instrument_id: str, known_at: datetime
    ) -> CryptoSpecView | None:
        try:
            spec = self._crypto.get_specification(instrument_id, known_at=known_at)
        except CryptoInstrumentError:
            return None
        return CryptoSpecView(
            kind=spec.kind,
            venue=spec.venue,
            base_asset=spec.base_asset,
            quote_asset=spec.quote_asset,
            settlement_asset=spec.settlement_asset,
            settlement_style=spec.settlement_style,
            reference_price_requirement=spec.reference_price_requirement,
        )

    def existing_sealed_dataset(
        self, source_id: UUID, version: str
    ) -> SealedDatasetView | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT dataset_version_id,content_hash,normalization_version,valid_from,"
                "valid_until,created_at FROM historical_dataset_versions "
                "WHERE source_id=%s AND version=%s AND status='SEALED'",
                (source_id, version),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            dataset_version_id = UUID(str(row[0]))
            cursor.execute(
                "SELECT r.observation_kind,COUNT(*) FROM historical_dataset_members m "
                "JOIN historical_normalized_observations n "
                "ON n.normalized_observation_id=m.normalized_observation_id "
                "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                "WHERE m.dataset_version_id=%s GROUP BY r.observation_kind",
                (dataset_version_id,),
            )
            count_rows = cursor.fetchall()
        return SealedDatasetView(
            dataset_version_id=dataset_version_id,
            content_hash=str(row[1]),
            normalization_version=str(row[2]),
            valid_from=row[3],
            valid_until=row[4],
            created_at=row[5],
            counts_by_kind={
                ObservationKind(str(item[0])): int(item[1]) for item in count_rows
            },
        )

    def feature_counts(
        self, dataset_version_id: UUID, feature_names: tuple[str, ...]
    ) -> Mapping[str, int]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT d.name,COUNT(*) FROM feature_materializations m "
                "JOIN feature_definition_versions d ON d.feature_id=m.feature_id "
                "WHERE m.dataset_version=%s AND d.name=ANY(%s) GROUP BY d.name",
                (str(dataset_version_id), list(feature_names)),
            )
            rows = cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}


class PostgresAcquisitionFeatureMaterializer:
    """Materializes the two canonical derivatives features on a sealed dataset.

    The canonical ``crypto_mark_index_basis`` and ``open_interest_change``
    definitions are registered idempotently -- their existing
    ``(name, semantic_version)`` row is reused when present (and fails closed if
    its calculation version has drifted) -- so no new formula and no
    workflow-specific semantic version is ever introduced.
    """

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        basis_definition_factory: Callable[
            [datetime], FeatureDefinitionVersion
        ] = crypto_mark_index_basis_definition,
        open_interest_definition_factory: Callable[
            [datetime], FeatureDefinitionVersion
        ] = open_interest_change_definition,
    ) -> None:
        self._authority = PostgresFeatureAuthority(database)
        self._basis = PostgresCryptoDerivativesFeatureCalculator(database)
        self._open_interest = PostgresOpenInterestFeatureCalculator(database)
        self._database = database
        # Production uses the canonical 3J.1c/3J.1b definitions verbatim. The
        # factories are injectable ONLY so a shared-database integration test can
        # tag the fixture definition names/versions and not collide with the
        # canonical rows other tests assert as database-wide singletons; no new
        # production formula or semantic version is ever introduced.
        self._basis_definition_factory = basis_definition_factory
        self._open_interest_definition_factory = open_interest_definition_factory

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
        basis_count = 0
        if basis_event_ats:
            feature_id = self._feature_id(
                self._basis_definition_factory(definition_created_at)
            )
            for event_at in basis_event_ats:
                if (
                    self._basis.materialize_crypto_mark_index_basis(
                        feature_id=feature_id,
                        instrument_id=instrument_id,
                        dataset_version_id=dataset_version_id,
                        event_at=event_at,
                        decision_at=decision_at,
                    )
                    is not None
                ):
                    basis_count += 1
        open_interest_count = 0
        if open_interest_event_ats:
            feature_id = self._feature_id(
                self._open_interest_definition_factory(definition_created_at)
            )
            for event_at in open_interest_event_ats:
                if (
                    self._open_interest.materialize_open_interest_change(
                        feature_id=feature_id,
                        instrument_id=instrument_id,
                        dataset_version_id=dataset_version_id,
                        event_at=event_at,
                        decision_at=decision_at,
                    )
                    is not None
                ):
                    open_interest_count += 1
        return MaterializedFeatureCounts(
            crypto_mark_index_basis=basis_count, open_interest_change=open_interest_count
        )

    def _feature_id(self, definition: FeatureDefinitionVersion) -> UUID:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT feature_id,calculation_version FROM feature_definition_versions "
                "WHERE name=%s AND semantic_version=%s",
                (definition.name, definition.semantic_version),
            )
            row = cursor.fetchone()
        if row is None:
            self._authority.register(definition)
            return definition.feature_id
        if str(row[1]) != definition.calculation_version:
            raise HistoricalAcquisitionError(
                f"feature_definition_calculation_version_drift:{definition.name}"
            )
        return UUID(str(row[0]))
