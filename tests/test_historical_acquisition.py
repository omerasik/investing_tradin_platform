"""Focused unit tests for the Phase 3B.1 historical acquisition service.

Every test here is fully offline: acquisition is driven by an in-process fake
adapter (never :class:`UrlLibBybitTransport`, never a socket), and the pipeline,
checkpoint store, canonical evidence and feature materializer are in-memory
fakes. No live Bybit request, order or account call is ever made.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from trade_platform.bybit_crypto_provider import (
    BYBIT_EXCHANGE,
    BYBIT_PROVIDER_NAME,
    BYBIT_V5_SYMBOL_NAMESPACE,
)
from trade_platform.bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    BYBIT_BTCUSDT_SYMBOL,
)
from trade_platform.crypto_derivatives_features import CRYPTO_MARK_INDEX_BASIS
from trade_platform.crypto_instruments import (
    CryptoInstrumentKind,
    ReferencePriceRequirement,
    SettlementStyle,
)
from trade_platform.data_providers import (
    ProviderConfiguration,
    ProviderError,
    ProviderOperationalStatus,
)
from trade_platform.historical_acquisition import (
    AcquisitionStatus,
    CryptoSpecView,
    HistoricalAcquisitionRequest,
    HistoricalAcquisitionService,
    MaterializedFeatureCounts,
    NormalizedObservationView,
    SealedDatasetView,
    SourceProfile,
    acquisition_fingerprint,
)
from trade_platform.historical_market_data import (
    AdjustmentStatus,
    HistoricalDatasetVersion,
    NormalizedHistoricalObservation,
    ObservationKind,
    QualityStatus,
    RawHistoricalObservation,
)
from trade_platform.open_interest_features import OPEN_INTEREST_CHANGE
from trade_platform.provider_ingestion import RawHistoricalPage

SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")
INSTRUMENT_ID = BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID
SYMBOL = BYBIT_BTCUSDT_SYMBOL
START = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 6, 1, 0, 30, tzinfo=UTC)
NOW = END  # >= end, so the adapter would keep every in-window bar
NORMALIZATION_VERSION = "bybit-v5-md-v1"
DATASET_VERSION = "bybit-v5-linear-btcusdt-phase3b1"
PROVIDER_VERSION = "bybit-v5-public-market-v1"
_MINUTE = timedelta(minutes=1)
_FIVE = timedelta(minutes=5)

ALL_KINDS = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)


def _expected_events(kind: ObservationKind, start: datetime, end: datetime) -> list[datetime]:
    if kind is ObservationKind.OPEN_INTEREST:
        instants: list[datetime] = []
        instant = start
        while instant < end:
            instants.append(instant)
            instant += _FIVE
        return instants
    total = int((end - start) / _MINUTE)
    if kind is ObservationKind.OHLCV:
        return [start + index * _MINUTE for index in range(total)]
    return [start + (index + 1) * _MINUTE for index in range(total)]


def _raw(kind: ObservationKind, event_at: datetime, *, retrieved_at: datetime = NOW) -> RawHistoricalObservation:
    return RawHistoricalObservation(
        source_id=SOURCE_ID,
        observation_kind=kind,
        provider_identifier=SYMBOL,
        provider_symbol=SYMBOL,
        exchange=BYBIT_EXCHANGE,
        event_at=event_at,
        effective_at=event_at,
        ingested_at=retrieved_at,
        adjustment_status=AdjustmentStatus.RAW,
        revision=0,
        provenance_uri="bybit://v5/test/linear/BTCUSDT",
        raw_payload={"value": "1"},
    )


def _spec() -> CryptoSpecView:
    return CryptoSpecView(
        kind=CryptoInstrumentKind.PERPETUAL,
        venue=BYBIT_EXCHANGE,
        base_asset="BTC",
        quote_asset="USDT",
        settlement_asset="USDT",
        settlement_style=SettlementStyle.LINEAR,
        reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
    )


def _configuration(*, terms_accepted: bool = True, secret_reference: str | None = None) -> ProviderConfiguration:
    return ProviderConfiguration(
        provider=BYBIT_PROVIDER_NAME,
        base_url="https://api.bybit.com",
        terms_accepted=terms_accepted,
        secret_reference=secret_reference,
    )


def _request(
    *,
    kinds: frozenset[ObservationKind] = ALL_KINDS,
    materialize_features: bool = False,
    start: datetime = START,
    end: datetime = END,
    instrument_id: str = INSTRUMENT_ID,
    provider_symbol: str = SYMBOL,
    dataset_version: str = DATASET_VERSION,
    normalization_version: str = NORMALIZATION_VERSION,
    idempotency_key: str | None = None,
) -> HistoricalAcquisitionRequest:
    base = HistoricalAcquisitionRequest(
        source_id=SOURCE_ID,
        instrument_id=instrument_id,
        provider=BYBIT_PROVIDER_NAME,
        provider_symbol=provider_symbol,
        start=start,
        end=end,
        observation_kinds=kinds,
        normalization_version=normalization_version,
        dataset_version=dataset_version,
        maximum_pages_per_kind=8,
        materialize_features=materialize_features,
        idempotency_key="",
    )
    key = idempotency_key if idempotency_key is not None else acquisition_fingerprint(base)
    return HistoricalAcquisitionRequest(
        source_id=base.source_id,
        instrument_id=base.instrument_id,
        provider=base.provider,
        provider_symbol=base.provider_symbol,
        start=base.start,
        end=base.end,
        observation_kinds=base.observation_kinds,
        normalization_version=base.normalization_version,
        dataset_version=base.dataset_version,
        maximum_pages_per_kind=base.maximum_pages_per_kind,
        materialize_features=base.materialize_features,
        idempotency_key=key,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAdapter:
    """Returns scripted raw pages per kind, or raises a scripted provider error."""

    name = BYBIT_PROVIDER_NAME

    def __init__(
        self,
        pages: dict[ObservationKind, list[RawHistoricalPage]] | None = None,
        errors: dict[ObservationKind, Exception] | None = None,
    ) -> None:
        self._pages = {kind: list(seq) for kind, seq in (pages or {}).items()}
        self._errors = dict(errors or {})
        self.fetched_kinds: list[ObservationKind] = []

    def fetch_raw_page(
        self, source_id: UUID, scope: dict[str, object], cursor: str | None
    ) -> RawHistoricalPage:
        kind = ObservationKind(str(scope["observation_kind"]))
        self.fetched_kinds.append(kind)
        if kind in self._errors:
            raise self._errors[kind]
        pages = self._pages.get(kind, [])
        if not pages:
            return RawHistoricalPage((), None, PROVIDER_VERSION, NOW)
        return pages.pop(0)


def _single_page(records: list[RawHistoricalObservation]) -> RawHistoricalPage:
    return RawHistoricalPage(tuple(records), None, PROVIDER_VERSION, NOW)


def _complete_pages(
    kinds: frozenset[ObservationKind], *, drop_last: frozenset[ObservationKind] = frozenset()
) -> dict[ObservationKind, list[RawHistoricalPage]]:
    pages: dict[ObservationKind, list[RawHistoricalPage]] = {}
    for kind in kinds:
        events = _expected_events(kind, START, END)
        if kind in drop_last:
            events = events[:-1]
        pages[kind] = [_single_page([_raw(kind, event) for event in events])]
    return pages


class FakePipeline:
    """In-memory capture/normalize/seal with per-raw quality control.

    ``capture_raw`` deduplicates on ``(source_id, kind, event_at)`` exactly like
    the real ``historical_raw_observations`` natural key, so a retry that
    re-submits the same logical observation gets back the SAME
    ``raw_observation_id``. ``normalize`` records every persisted normalized
    row in ``normalized_by_raw`` (mirroring the real UNIQUE
    ``raw_observation_id`` constraint) and every actual call it received in
    ``normalize_calls``, so a test can assert a previously-normalized raw id is
    never normalized twice.
    """

    def __init__(
        self,
        *,
        reject: frozenset[tuple[ObservationKind, datetime]] = frozenset(),
        normalize_error: Exception | None = None,
        wrong_instrument_for: frozenset[tuple[ObservationKind, datetime]] = frozenset(),
    ) -> None:
        self._raws: dict[UUID, RawHistoricalObservation] = {}
        self._raw_id_by_key: dict[tuple[UUID, ObservationKind, datetime], UUID] = {}
        self._normalized_ids: dict[UUID, UUID] = {}
        self.normalized_by_raw: dict[UUID, NormalizedObservationView] = {}
        self.normalize_calls: list[UUID] = []
        self._counter = 0
        self.reject = reject
        self.normalize_error = normalize_error
        self.wrong_instrument_for = wrong_instrument_for
        self.sealed: list[tuple[str, tuple[UUID, ...]]] = []

    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]:
        ids: list[UUID] = []
        for observation in observations:
            key = (observation.source_id, observation.observation_kind, observation.event_at)
            existing_id = self._raw_id_by_key.get(key)
            if existing_id is not None:
                ids.append(existing_id)
                continue
            self._counter += 1
            raw_id = UUID(int=self._counter)
            self._raw_id_by_key[key] = raw_id
            self._raws[raw_id] = observation
            ids.append(raw_id)
        return tuple(ids)

    def normalize(
        self, raw_observation_id: UUID, normalization_version: str, normalized_at: datetime
    ) -> NormalizedHistoricalObservation:
        self.normalize_calls.append(raw_observation_id)
        if raw_observation_id in self.normalized_by_raw:
            # Mirrors the real UNIQUE(raw_observation_id) constraint: a second
            # normalize() call for an already-normalized raw id must never
            # happen in correct service code.
            raise AssertionError(
                f"normalize() called twice for already-normalized raw id {raw_observation_id}"
            )
        if self.normalize_error is not None:
            raise self.normalize_error
        observation = self._raws[raw_observation_id]
        key = (observation.observation_kind, observation.event_at)
        normalized_id = self._normalized_ids.setdefault(raw_observation_id, uuid4())
        quality = (
            QualityStatus.REJECTED if key in self.reject else QualityStatus.VALIDATED
        )
        instrument_id = (
            "CRYPTO:OTHER:XXXUSDT:PERP" if key in self.wrong_instrument_for else INSTRUMENT_ID
        )
        issues = () if quality is QualityStatus.VALIDATED else ("fixture_reject",)
        self.normalized_by_raw[raw_observation_id] = NormalizedObservationView(
            normalized_observation_id=normalized_id,
            raw_observation_id=raw_observation_id,
            instrument_id=instrument_id,
            normalization_version=normalization_version,
            quality_status=quality,
            quality_issues=issues,
        )
        return NormalizedHistoricalObservation(
            normalized_observation_id=normalized_id,
            raw_observation_id=raw_observation_id,
            instrument_id=instrument_id,
            normalization_version=normalization_version,
            normalized_value={},
            quality_status=quality,
            quality_issues=issues,
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
        self.sealed.append((version, normalized_ids))
        return HistoricalDatasetVersion(
            dataset_version_id=UUID("22222222-2222-2222-2222-222222222222"),
            source_id=source_id,
            version=version,
            normalization_version=normalization_version,
            content_hash="a" * 64,
            valid_from=created_at,
            valid_until=created_at,
            created_at=created_at,
        )


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.recorded: list[object] = []

    def record(self, checkpoint: object) -> None:
        self.recorded.append(checkpoint)


@dataclass
class FakeEvidence:
    profile: SourceProfile | None
    resolved: tuple[str, ...]
    spec: CryptoSpecView | None
    existing: SealedDatasetView | None = None
    feature_count_map: Mapping[str, int] = field(default_factory=dict)
    # Wired by `_build_service` to the SAME FakePipeline/FakeFeatureStore
    # instances the service itself uses, so a retry within one test method sees
    # the durable evidence the first attempt actually persisted.
    pipeline: FakePipeline | None = None
    feature_store: FakeFeatureStore | None = None

    def source_profile(self, source_id: UUID) -> SourceProfile | None:
        return self.profile

    def resolve_instrument_ids(
        self, namespace: str, provider_symbol: str, known_at: datetime
    ) -> tuple[str, ...]:
        return self.resolved

    def crypto_specification(
        self, instrument_id: str, known_at: datetime
    ) -> CryptoSpecView | None:
        return self.spec

    def existing_sealed_dataset(
        self, source_id: UUID, version: str
    ) -> SealedDatasetView | None:
        return self.existing

    def feature_counts(
        self, dataset_version_id: UUID, feature_names: tuple[str, ...]
    ) -> Mapping[str, int]:
        if self.feature_store is not None:
            return {
                name: self.feature_store.get(dataset_version_id, name) for name in feature_names
            }
        return self.feature_count_map

    def normalized_observation_for_raw(
        self, raw_observation_id: UUID
    ) -> NormalizedObservationView | None:
        if self.pipeline is None:
            return None
        return self.pipeline.normalized_by_raw.get(raw_observation_id)


class FakeFeatureStore:
    """Shared, durable feature-count state a `FakeFeatureMaterializer` writes to
    and `FakeEvidence.feature_counts` reads back from -- models the real
    idempotent, content-hash-agreeing feature authority closely enough that a
    resumed materialization only adds what is genuinely still missing."""

    def __init__(self) -> None:
        self._counts: dict[tuple[UUID, str], int] = {}

    def get(self, dataset_version_id: UUID, name: str) -> int:
        return self._counts.get((dataset_version_id, name), 0)

    def add(self, dataset_version_id: UUID, name: str, amount: int) -> None:
        if amount <= 0:
            return
        self._counts[(dataset_version_id, name)] = self.get(dataset_version_id, name) + amount


class FakeFeatureMaterializer:
    def __init__(
        self,
        *,
        raise_error: Exception | None = None,
        fail_first_n_calls: int = 0,
        store: FakeFeatureStore | None = None,
    ) -> None:
        self.raise_error = raise_error
        self.fail_first_n_calls = fail_first_n_calls
        self.store = store or FakeFeatureStore()
        self.calls: list[UUID] = []
        self.last_basis: tuple[datetime, ...] = ()
        self.last_open_interest: tuple[datetime, ...] = ()

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
        call_index = len(self.calls)
        self.calls.append(dataset_version_id)
        self.last_basis = basis_event_ats
        self.last_open_interest = open_interest_event_ats
        if self.raise_error is not None:
            raise self.raise_error
        if call_index < self.fail_first_n_calls:
            raise RuntimeError("feature_boom_transient")
        expected_basis = len(basis_event_ats)
        expected_open_interest = (
            max(0, len(open_interest_event_ats) - 1) if open_interest_event_ats else 0
        )
        existing_basis = self.store.get(dataset_version_id, CRYPTO_MARK_INDEX_BASIS)
        existing_open_interest = self.store.get(dataset_version_id, OPEN_INTEREST_CHANGE)
        self.store.add(
            dataset_version_id, CRYPTO_MARK_INDEX_BASIS, expected_basis - existing_basis
        )
        self.store.add(
            dataset_version_id,
            OPEN_INTEREST_CHANGE,
            expected_open_interest - existing_open_interest,
        )
        return MaterializedFeatureCounts(
            crypto_mark_index_basis=self.store.get(dataset_version_id, CRYPTO_MARK_INDEX_BASIS),
            open_interest_change=self.store.get(dataset_version_id, OPEN_INTEREST_CHANGE),
        )


def _default_evidence() -> FakeEvidence:
    return FakeEvidence(
        profile=SourceProfile(
            provider=BYBIT_PROVIDER_NAME,
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            asset_scope="CRYPTO",
            capabilities=ALL_KINDS,
        ),
        resolved=(INSTRUMENT_ID,),
        spec=_spec(),
    )


def _sealed_view_for_request(
    request: HistoricalAcquisitionRequest,
    *,
    dataset_version_id: UUID,
    content_hash: str = "b" * 64,
    instrument_id: str | None = None,
    provider_symbol: str | None = None,
    event_overrides: Mapping[ObservationKind, frozenset[datetime]] | None = None,
) -> SealedDatasetView:
    """Build a `SealedDatasetView` that -- unless overridden -- exactly matches
    what a real successful acquisition for `request` would have persisted, for
    use as `FakeEvidence.existing` in replay-path tests."""
    event_ats_by_kind: dict[ObservationKind, frozenset[datetime]] = {
        kind: frozenset(_expected_events(kind, request.start, request.end))
        for kind in request.ordered_kinds()
    }
    if event_overrides:
        event_ats_by_kind.update(event_overrides)
    all_events = sorted({event for events in event_ats_by_kind.values() for event in events})
    resolved_instrument = instrument_id if instrument_id is not None else request.instrument_id
    resolved_symbol = (
        provider_symbol if provider_symbol is not None else request.provider_symbol
    )
    return SealedDatasetView(
        dataset_version_id=dataset_version_id,
        content_hash=content_hash,
        normalization_version=request.normalization_version,
        valid_from=all_events[0],
        valid_until=all_events[-1],
        created_at=NOW,
        instrument_ids=frozenset({resolved_instrument}),
        provider_identifiers=frozenset({resolved_symbol}),
        provider_symbols=frozenset({resolved_symbol}),
        event_ats_by_kind=event_ats_by_kind,
    )


def _build_service(
    *,
    evidence: FakeEvidence | None = None,
    pipeline: FakePipeline | None = None,
    checkpoint_store: FakeCheckpointStore | None = None,
    materializer: FakeFeatureMaterializer | None = None,
    adapter: FakeAdapter | None = None,
    adapter_factory_flag: dict[str, bool] | None = None,
) -> tuple[HistoricalAcquisitionService, FakePipeline, FakeCheckpointStore, FakeFeatureMaterializer, FakeAdapter]:
    pipeline = pipeline or FakePipeline()
    checkpoint_store = checkpoint_store or FakeCheckpointStore()
    materializer = materializer or FakeFeatureMaterializer()
    adapter = adapter if adapter is not None else FakeAdapter(_complete_pages(ALL_KINDS))
    resolved_evidence = evidence or _default_evidence()
    # Every acquire() call shares these same fake collaborator instances, so a
    # retry within one test method sees exactly what the first attempt durably
    # persisted -- the same restart-safety contract the real Postgres-backed
    # collaborators provide.
    resolved_evidence.pipeline = pipeline
    resolved_evidence.feature_store = materializer.store

    def factory(configuration: ProviderConfiguration, now: object) -> FakeAdapter:
        if adapter_factory_flag is not None:
            adapter_factory_flag["called"] = True
        return adapter

    service = HistoricalAcquisitionService(
        evidence=resolved_evidence,
        pipeline=pipeline,
        checkpoint_store=checkpoint_store,
        feature_materializer=materializer,
        adapter_factory=factory,  # type: ignore[arg-type]
        now=lambda: NOW,
    )
    return service, pipeline, checkpoint_store, materializer, adapter


class HistoricalAcquisitionServiceTests(unittest.TestCase):
    # ---- precondition boundary --------------------------------------------

    def test_terms_not_accepted_performs_zero_network(self) -> None:
        flag: dict[str, bool] = {"called": False}
        service, _pipeline, checkpoints, _materializer, adapter = _build_service(
            adapter_factory_flag=flag
        )
        result = service.acquire(_request(), _configuration(terms_accepted=False))
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "provider_terms_not_accepted")
        self.assertFalse(flag["called"])
        self.assertEqual(adapter.fetched_kinds, [])
        self.assertEqual(checkpoints.recorded, [])

    def test_secret_reference_rejected(self) -> None:
        service, *_ = _build_service()
        result = service.acquire(_request(), _configuration(secret_reference="vault://x"))
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "public_bybit_v1_requires_no_secret")

    def test_wrong_source_provider_fails_precondition(self) -> None:
        evidence = _default_evidence()
        evidence.profile = SourceProfile(
            provider="databento",
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            asset_scope="CRYPTO",
            capabilities=ALL_KINDS,
        )
        service, *_ = _build_service(evidence=evidence)
        result = service.acquire(_request(), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "source_provider_mismatch")

    def test_missing_source_fails_precondition(self) -> None:
        evidence = _default_evidence()
        evidence.profile = None
        service, *_ = _build_service(evidence=evidence)
        result = service.acquire(_request(), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "historical_source_not_found")

    def test_wrong_instrument_fails_precondition(self) -> None:
        service, *_ = _build_service()
        result = service.acquire(
            _request(instrument_id="CRYPTO:BYBIT:ETHUSDT:PERP"), _configuration()
        )
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "unsupported_instrument")

    def test_wrong_provider_symbol_fails_precondition(self) -> None:
        service, *_ = _build_service()
        result = service.acquire(_request(provider_symbol="ETHUSDT"), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "unsupported_provider_symbol")

    def test_provider_symbol_resolving_to_other_instrument_fails(self) -> None:
        evidence = _default_evidence()
        evidence.resolved = ("CRYPTO:BYBIT:OTHER:PERP",)
        service, *_ = _build_service(evidence=evidence)
        result = service.acquire(_request(), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "provider_symbol_resolves_to_other_instrument")

    def test_unauthorized_requested_kind_fails_precondition(self) -> None:
        evidence = _default_evidence()
        evidence.profile = SourceProfile(
            provider=BYBIT_PROVIDER_NAME,
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            asset_scope="CRYPTO",
            capabilities=frozenset({ObservationKind.OHLCV}),
        )
        service, _pipeline, _cp, _mat, adapter = _build_service(evidence=evidence)
        result = service.acquire(_request(kinds=ALL_KINDS), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertTrue(result.failure_code.startswith("source_not_authorized_for_kind"))
        self.assertEqual(adapter.fetched_kinds, [])

    def test_funding_rejected_before_network(self) -> None:
        flag: dict[str, bool] = {"called": False}
        service, _pipeline, _cp, _mat, adapter = _build_service(adapter_factory_flag=flag)
        result = service.acquire(
            _request(kinds=frozenset({ObservationKind.FUNDING_RATE_REALIZED})),
            _configuration(),
        )
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "funding_not_supported")
        self.assertFalse(flag["called"])
        self.assertEqual(adapter.fetched_kinds, [])

    def test_spec_mismatch_fails_precondition(self) -> None:
        evidence = _default_evidence()
        evidence.spec = CryptoSpecView(
            kind=CryptoInstrumentKind.PERPETUAL,
            venue=BYBIT_EXCHANGE,
            base_asset="BTC",
            quote_asset="USDC",  # wrong
            settlement_asset="USDT",
            settlement_style=SettlementStyle.LINEAR,
            reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
        )
        service, *_ = _build_service(evidence=evidence)
        result = service.acquire(_request(), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "instrument_quote_asset_mismatch")

    def test_idempotency_key_not_binding_semantics_fails(self) -> None:
        request = _request(idempotency_key="operator-picked-freeform-key")
        service, *_ = _build_service()
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(
            result.failure_code, "idempotency_key_does_not_bind_request_semantics"
        )

    # ---- provider / pagination boundary -----------------------------------

    def test_one_provider_kind_failure_stops_later_kinds(self) -> None:
        pages = _complete_pages(ALL_KINDS)
        adapter = FakeAdapter(
            pages=pages,
            errors={ObservationKind.MARK_PRICE: ProviderError("bybit_http_status:500")},
        )
        service, pipeline, checkpoints, _mat, used = _build_service(adapter=adapter)
        result = service.acquire(_request(), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PROVIDER_FAILED)
        # OHLCV then MARK_PRICE attempted; INDEX_PRICE and OPEN_INTEREST never reached.
        self.assertEqual(
            used.fetched_kinds, [ObservationKind.OHLCV, ObservationKind.MARK_PRICE]
        )
        self.assertEqual(pipeline.sealed, [])
        # A checkpoint is persisted for each attempted kind, including the failure.
        self.assertEqual(len(checkpoints.recorded), 2)
        self.assertEqual(len(result.checkpoints), 2)
        self.assertEqual(
            [ref.observation_kind for ref in result.checkpoints],
            [ObservationKind.OHLCV, ObservationKind.MARK_PRICE],
        )
        self.assertEqual(result.checkpoints[-1].state, ProviderOperationalStatus.ERROR)

    def test_pagination_cursor_loop_maps_to_pagination_failed(self) -> None:
        looping = [
            RawHistoricalPage((), "c:stuck", PROVIDER_VERSION, NOW),
            RawHistoricalPage((), "c:stuck", PROVIDER_VERSION, NOW),
        ]
        adapter = FakeAdapter(pages={ObservationKind.OHLCV: looping})
        service, pipeline, checkpoints, *_ = _build_service(
            adapter=adapter,
        )
        result = service.acquire(_request(kinds=frozenset({ObservationKind.OHLCV})), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.PAGINATION_FAILED)
        self.assertEqual(result.failure_code, "provider_pagination_cursor_loop")
        self.assertEqual(pipeline.sealed, [])
        self.assertEqual(len(checkpoints.recorded), 1)

    # ---- normalization / quality boundary ---------------------------------

    def test_rejected_normalization_blocks_sealing(self) -> None:
        pipeline = FakePipeline(
            reject=frozenset({(ObservationKind.OHLCV, START)})
        )
        service, pipeline, _cp, _mat, _adapter = _build_service(pipeline=pipeline)
        result = service.acquire(_request(kinds=frozenset({ObservationKind.OHLCV})), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.QUALITY_FAILED)
        self.assertGreaterEqual(result.rejected_count, 1)
        self.assertEqual(pipeline.sealed, [])

    def test_normalization_error_maps_to_normalization_failed(self) -> None:
        from trade_platform.historical_market_data import HistoricalDataResolutionError

        pipeline = FakePipeline(
            normalize_error=HistoricalDataResolutionError("historical_instrument_resolution_failed")
        )
        service, pipeline, _cp, _mat, _adapter = _build_service(pipeline=pipeline)
        result = service.acquire(_request(kinds=frozenset({ObservationKind.OHLCV})), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.NORMALIZATION_FAILED)
        self.assertEqual(pipeline.sealed, [])

    # ---- coverage boundary -------------------------------------------------

    def test_missing_timestamp_blocks_sealing(self) -> None:
        adapter = FakeAdapter(
            pages=_complete_pages(
                frozenset({ObservationKind.OHLCV}), drop_last=frozenset({ObservationKind.OHLCV})
            )
        )
        service, pipeline, *_ = _build_service(adapter=adapter)
        result = service.acquire(_request(kinds=frozenset({ObservationKind.OHLCV})), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.COVERAGE_FAILED)
        self.assertTrue(result.failure_code.startswith("missing_required_observation:OHLCV"))
        self.assertEqual(pipeline.sealed, [])

    def test_duplicate_timestamp_blocks_sealing(self) -> None:
        events = _expected_events(ObservationKind.OHLCV, START, END)
        duplicated = [_raw(ObservationKind.OHLCV, event) for event in events]
        duplicated.append(_raw(ObservationKind.OHLCV, events[0]))  # duplicate of first bar
        adapter = FakeAdapter(pages={ObservationKind.OHLCV: [_single_page(duplicated)]})
        service, pipeline, *_ = _build_service(adapter=adapter)
        result = service.acquire(_request(kinds=frozenset({ObservationKind.OHLCV})), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.COVERAGE_FAILED)
        self.assertTrue(result.failure_code.startswith("duplicate_observation_timestamp:OHLCV"))
        self.assertEqual(pipeline.sealed, [])

    def test_out_of_window_evidence_is_rejected(self) -> None:
        events = _expected_events(ObservationKind.OHLCV, START, END)
        with_extra = [_raw(ObservationKind.OHLCV, event) for event in events]
        # A valid observation whose event is before the window start: never expected.
        with_extra.append(_raw(ObservationKind.OHLCV, START - _MINUTE))
        adapter = FakeAdapter(pages={ObservationKind.OHLCV: [_single_page(with_extra)]})
        service, pipeline, *_ = _build_service(adapter=adapter)
        result = service.acquire(_request(kinds=frozenset({ObservationKind.OHLCV})), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.COVERAGE_FAILED)
        self.assertTrue(
            result.failure_code.startswith("unexpected_out_of_window_observation:OHLCV")
        )
        self.assertEqual(pipeline.sealed, [])

    def test_misaligned_window_fails_precondition(self) -> None:
        service, *_ = _build_service()
        result = service.acquire(
            _request(
                kinds=frozenset({ObservationKind.OPEN_INTEREST}),
                start=START + _MINUTE,  # not 5-minute aligned
                end=END,
            ),
            _configuration(),
        )
        self.assertEqual(result.status, AcquisitionStatus.PRECONDITION_FAILED)
        self.assertEqual(result.failure_code, "window_start_not_open_interest_aligned")

    # ---- success paths -----------------------------------------------------

    def test_successful_four_kind_acquisition_without_features(self) -> None:
        service, pipeline, checkpoints, materializer, _adapter = _build_service()
        result = service.acquire(_request(materialize_features=False), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SUCCEEDED)
        self.assertFalse(result.already_completed)
        self.assertIsNotNone(result.dataset_version_id)
        self.assertEqual(result.dataset_content_hash, "a" * 64)
        self.assertEqual(result.rejected_count, 0)
        self.assertIsNone(result.feature_counts)
        self.assertEqual(
            result.normalized_counts,
            {
                ObservationKind.OHLCV: 30,
                ObservationKind.MARK_PRICE: 30,
                ObservationKind.INDEX_PRICE: 30,
                ObservationKind.OPEN_INTEREST: 6,
            },
        )
        self.assertEqual(result.raw_counts[ObservationKind.OPEN_INTEREST], 6)
        self.assertEqual(len(checkpoints.recorded), 4)
        self.assertEqual(len(result.checkpoints), 4)
        self.assertEqual(result.provider_health_status, ProviderOperationalStatus.HEALTHY)
        self.assertEqual(len(pipeline.sealed), 1)
        self.assertEqual(materializer.calls, [])

    def test_successful_acquisition_with_feature_materialization(self) -> None:
        service, _pipeline, _cp, materializer, _adapter = _build_service()
        result = service.acquire(_request(materialize_features=True), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SUCCEEDED)
        self.assertIsNotNone(result.feature_counts)
        assert result.feature_counts is not None
        self.assertEqual(result.feature_counts.crypto_mark_index_basis, 30)
        self.assertEqual(result.feature_counts.open_interest_change, 5)
        # Both feature families are materialized against the one sealed dataset.
        self.assertEqual(materializer.calls, [result.dataset_version_id])
        self.assertEqual(len(materializer.last_basis), 30)
        self.assertEqual(len(materializer.last_open_interest), 6)

    def test_feature_failure_produces_no_successful_result(self) -> None:
        materializer = FakeFeatureMaterializer(raise_error=RuntimeError("feature_boom"))
        service, pipeline, *_ = _build_service(materializer=materializer)
        result = service.acquire(_request(materialize_features=True), _configuration())
        self.assertEqual(result.status, AcquisitionStatus.FEATURE_FAILED)
        self.assertEqual(result.failure_code, "feature_boom")
        # The dataset was still sealed; only the feature step failed.
        self.assertEqual(len(pipeline.sealed), 1)

    # ---- idempotency -------------------------------------------------------

    def test_identical_completed_acquisition_replays_existing_result(self) -> None:
        request = _request()
        evidence = _default_evidence()
        evidence.existing = _sealed_view_for_request(
            request, dataset_version_id=UUID("33333333-3333-3333-3333-333333333333")
        )
        service, pipeline, checkpoints, _mat, adapter = _build_service(evidence=evidence)
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SUCCEEDED)
        self.assertTrue(result.already_completed)
        self.assertEqual(
            result.dataset_version_id, UUID("33333333-3333-3333-3333-333333333333")
        )
        self.assertEqual(result.dataset_content_hash, "b" * 64)
        # No fresh work is done on replay: nothing captured, nothing sealed.
        self.assertEqual(adapter.fetched_kinds, [])
        self.assertEqual(pipeline.sealed, [])
        self.assertEqual(checkpoints.recorded, [])

    def test_reused_dataset_version_with_different_semantics_conflicts(self) -> None:
        request = _request()
        evidence = _default_evidence()
        view = _sealed_view_for_request(
            request, dataset_version_id=UUID("33333333-3333-3333-3333-333333333333")
        )
        evidence.existing = replace(view, normalization_version="a-different-normalization")
        service, pipeline, *_ = _build_service(evidence=evidence)
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SEAL_FAILED)
        self.assertTrue(result.failure_code.startswith("dataset_version_conflict"))
        self.assertEqual(pipeline.sealed, [])

    def test_replay_identity_conflict_on_different_instrument(self) -> None:
        request = _request()
        evidence = _default_evidence()
        evidence.existing = _sealed_view_for_request(
            request,
            dataset_version_id=UUID("44444444-4444-4444-4444-444444444444"),
            instrument_id="CRYPTO:BYBIT:ETHUSDT:PERP",
        )
        service, pipeline, *_ = _build_service(evidence=evidence)
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SEAL_FAILED)
        self.assertTrue(result.failure_code.startswith("dataset_version_conflict"))
        self.assertEqual(pipeline.sealed, [])

    def test_replay_identity_conflict_on_different_provider_symbol(self) -> None:
        request = _request()
        evidence = _default_evidence()
        evidence.existing = _sealed_view_for_request(
            request,
            dataset_version_id=UUID("55555555-5555-5555-5555-555555555555"),
            provider_symbol="ETHUSDT",
        )
        service, pipeline, *_ = _build_service(evidence=evidence)
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SEAL_FAILED)
        self.assertTrue(result.failure_code.startswith("dataset_version_conflict"))
        self.assertEqual(pipeline.sealed, [])

    def test_replay_identity_conflict_on_shifted_timestamp_layout(self) -> None:
        request = _request(kinds=frozenset({ObservationKind.OHLCV}))
        expected_ohlcv = frozenset(_expected_events(ObservationKind.OHLCV, START, END))
        # Same COUNT (30) but one required instant swapped for one never expected.
        shifted = (expected_ohlcv - {START}) | {START - _MINUTE}
        self.assertEqual(len(shifted), len(expected_ohlcv))
        evidence = _default_evidence()
        evidence.existing = _sealed_view_for_request(
            request,
            dataset_version_id=UUID("66666666-6666-6666-6666-666666666666"),
            event_overrides={ObservationKind.OHLCV: shifted},
        )
        service, pipeline, *_ = _build_service(evidence=evidence)
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.SEAL_FAILED)
        self.assertTrue(result.failure_code.startswith("dataset_version_conflict"))
        self.assertEqual(pipeline.sealed, [])

    # ---- normalization restart-safety ---------------------------------------

    def test_retry_after_coverage_failure_reuses_normalized_evidence_and_seals_once(
        self,
    ) -> None:
        """A retry after a COVERAGE_FAILED boundary must not re-normalize any
        raw observation an earlier attempt already normalized -- that would hit
        the real UNIQUE(raw_observation_id) constraint -- and must seal exactly
        once, using both the reused and the newly-normalized evidence."""
        ohlcv_events = _expected_events(ObservationKind.OHLCV, START, END)
        incomplete_ohlcv_page = _single_page(
            [_raw(ObservationKind.OHLCV, event) for event in ohlcv_events[:-1]]
        )
        complete_ohlcv_page = _single_page(
            [_raw(ObservationKind.OHLCV, event) for event in ohlcv_events]
        )
        other_kinds = frozenset(
            {ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE, ObservationKind.OPEN_INTEREST}
        )
        other_pages = _complete_pages(other_kinds)
        adapter = FakeAdapter(
            pages={
                ObservationKind.OHLCV: [incomplete_ohlcv_page, complete_ohlcv_page],
                # The provider returns the SAME already-fully-covered evidence
                # again on retry, exactly as real historical data would.
                ObservationKind.MARK_PRICE: [
                    other_pages[ObservationKind.MARK_PRICE][0],
                    other_pages[ObservationKind.MARK_PRICE][0],
                ],
                ObservationKind.INDEX_PRICE: [
                    other_pages[ObservationKind.INDEX_PRICE][0],
                    other_pages[ObservationKind.INDEX_PRICE][0],
                ],
                ObservationKind.OPEN_INTEREST: [
                    other_pages[ObservationKind.OPEN_INTEREST][0],
                    other_pages[ObservationKind.OPEN_INTEREST][0],
                ],
            }
        )
        service, pipeline, checkpoints, _materializer, used_adapter = _build_service(
            adapter=adapter
        )
        request = _request()

        first = service.acquire(request, _configuration())
        self.assertEqual(first.status, AcquisitionStatus.COVERAGE_FAILED)
        self.assertEqual(pipeline.sealed, [])
        # 29 OHLCV + 30 MARK_PRICE + 30 INDEX_PRICE + 6 OPEN_INTEREST = 95.
        first_normalize_call_count = len(pipeline.normalize_calls)
        self.assertEqual(first_normalize_call_count, 29 + 30 + 30 + 6)

        second = service.acquire(request, _configuration())
        self.assertEqual(second.status, AcquisitionStatus.SUCCEEDED, second.failure_code)
        self.assertEqual(len(pipeline.sealed), 1)
        # Only the single previously-missing OHLCV bar is newly normalized; the
        # other 95 raw ids are reused via the canonical evidence lookup, never
        # handed to pipeline.normalize() a second time (which would raise).
        self.assertEqual(len(pipeline.normalize_calls), first_normalize_call_count + 1)
        self.assertEqual(
            second.normalized_counts,
            {
                ObservationKind.OHLCV: 30,
                ObservationKind.MARK_PRICE: 30,
                ObservationKind.INDEX_PRICE: 30,
                ObservationKind.OPEN_INTEREST: 6,
            },
        )
        # No duplicate normalized rows: exactly 96 distinct normalized ids exist.
        self.assertEqual(len(pipeline.normalized_by_raw), 96)
        # A checkpoint is recorded for every attempted kind on both attempts.
        self.assertEqual(len(checkpoints.recorded), 4 + 4)
        self.assertEqual(used_adapter.fetched_kinds.count(ObservationKind.OHLCV), 2)

    def test_retry_reuses_rejected_normalized_evidence_as_quality_failed(self) -> None:
        """A raw row normalized to REJECTED on a prior attempt must be read
        back as QUALITY_FAILED on retry -- never re-normalized."""
        events = _expected_events(ObservationKind.OHLCV, START, END)
        # The adapter returns the SAME historical page on both calls, exactly
        # as a real provider would for an already-elapsed window.
        page = _single_page([_raw(ObservationKind.OHLCV, event) for event in events])
        adapter = FakeAdapter(pages={ObservationKind.OHLCV: [page, page]})
        pipeline = FakePipeline(reject=frozenset({(ObservationKind.OHLCV, START)}))
        service, pipeline, *_ = _build_service(pipeline=pipeline, adapter=adapter)
        request = _request(kinds=frozenset({ObservationKind.OHLCV}))

        first = service.acquire(request, _configuration())
        self.assertEqual(first.status, AcquisitionStatus.QUALITY_FAILED)
        first_calls = len(pipeline.normalize_calls)

        second = service.acquire(request, _configuration())
        self.assertEqual(second.status, AcquisitionStatus.QUALITY_FAILED)
        self.assertGreaterEqual(second.rejected_count, 1)
        # The rejected raw id is read back, never normalized a second time.
        self.assertEqual(len(pipeline.normalize_calls), first_calls)

    # ---- feature resumption on replay ---------------------------------------

    def test_feature_resume_after_partial_failure_completes_and_succeeds(self) -> None:
        materializer = FakeFeatureMaterializer(fail_first_n_calls=1)
        evidence = _default_evidence()
        service, pipeline, _cp, materializer, adapter = _build_service(
            evidence=evidence, materializer=materializer
        )
        request = _request(materialize_features=True)

        first = service.acquire(request, _configuration())
        self.assertEqual(first.status, AcquisitionStatus.FEATURE_FAILED)
        # The dataset was still sealed; only the feature step failed.
        self.assertEqual(len(pipeline.sealed), 1)
        sealed_dataset_version_id = UUID("22222222-2222-2222-2222-222222222222")

        # Wire the evidence to report the now-sealed dataset for replay.
        evidence.existing = _sealed_view_for_request(
            request, dataset_version_id=sealed_dataset_version_id
        )
        fetched_before_retry = len(adapter.fetched_kinds)

        second = service.acquire(request, _configuration())
        self.assertEqual(second.status, AcquisitionStatus.SUCCEEDED, second.failure_code)
        self.assertTrue(second.already_completed)
        # Zero provider requests on the resume path.
        self.assertEqual(len(adapter.fetched_kinds), fetched_before_retry)
        # No second dataset is ever sealed.
        self.assertEqual(len(pipeline.sealed), 1)
        assert second.feature_counts is not None
        self.assertEqual(second.feature_counts.crypto_mark_index_basis, 30)
        self.assertEqual(second.feature_counts.open_interest_change, 5)
        self.assertEqual(
            materializer.store.get(sealed_dataset_version_id, CRYPTO_MARK_INDEX_BASIS), 30
        )
        self.assertEqual(
            materializer.store.get(sealed_dataset_version_id, OPEN_INTEREST_CHANGE), 5
        )

    def test_feature_resume_from_features_disabled_to_enabled(self) -> None:
        evidence = _default_evidence()
        service, pipeline, _cp, materializer, adapter = _build_service(evidence=evidence)
        disabled_request = _request(materialize_features=False)

        first = service.acquire(disabled_request, _configuration())
        self.assertEqual(first.status, AcquisitionStatus.SUCCEEDED)
        self.assertIsNone(first.feature_counts)
        self.assertEqual(materializer.calls, [])
        sealed_dataset_version_id = UUID("22222222-2222-2222-2222-222222222222")

        enabled_request = _request(materialize_features=True)
        evidence.existing = _sealed_view_for_request(
            enabled_request, dataset_version_id=sealed_dataset_version_id
        )
        fetched_before_retry = len(adapter.fetched_kinds)

        second = service.acquire(enabled_request, _configuration())
        self.assertEqual(second.status, AcquisitionStatus.SUCCEEDED, second.failure_code)
        self.assertTrue(second.already_completed)
        self.assertEqual(len(adapter.fetched_kinds), fetched_before_retry)
        self.assertEqual(len(pipeline.sealed), 1)
        assert second.feature_counts is not None
        self.assertEqual(second.feature_counts.crypto_mark_index_basis, 30)
        self.assertEqual(second.feature_counts.open_interest_change, 5)

    def test_replay_features_excess_count_fails_closed(self) -> None:
        request = _request(materialize_features=True)
        store = FakeFeatureStore()
        dataset_version_id = UUID("77777777-7777-7777-7777-777777777777")
        # More materializations already exist than this window could ever
        # produce -- an impossible/conflicting state that must fail closed
        # rather than being silently accepted or trimmed.
        store.add(dataset_version_id, CRYPTO_MARK_INDEX_BASIS, 999)
        materializer = FakeFeatureMaterializer(store=store)
        evidence = _default_evidence()
        evidence.existing = _sealed_view_for_request(
            request, dataset_version_id=dataset_version_id
        )
        service, _pipeline, _cp, materializer, adapter = _build_service(
            evidence=evidence, materializer=materializer
        )
        result = service.acquire(request, _configuration())
        self.assertEqual(result.status, AcquisitionStatus.FEATURE_FAILED)
        self.assertTrue(result.failure_code.startswith("existing_feature_count_exceeds_expected"))
        self.assertEqual(adapter.fetched_kinds, [])
        self.assertEqual(materializer.calls, [])


if __name__ == "__main__":
    unittest.main()
