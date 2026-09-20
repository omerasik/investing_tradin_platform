"""Canonical multi-day research dataset composition (Phase 3D.3).

The liquidity/capacity and strategy research authorities are deliberately
*dataset-bound*: a run, its tradable-bar series, its feature values and its
capacity evaluation must all name the same sealed ``dataset_version_id``. That
invariant is never relaxed here. A multi-day study over evidence that was
acquired as several separate sealed daily datasets therefore needs **one new
sealed dataset** holding the exact evidence union. This module composes it.

What a composition is
---------------------
A :class:`DatasetCompositionRequest` names an ordered tuple of already-sealed
*parent* datasets (:class:`CompositionParent`) and the combined ``[start, end)``
window they must exactly tile. Its identity
(:func:`composition_identity_hash`) binds the composition semantic version,
source, instrument, provider, provider symbol, normalization version, observation
kinds, combined window and, for every parent in order, ``dataset_version_id``,
dataset version, content hash and window. The target dataset version
(:func:`composed_dataset_version`) is a pure function of that identity, so it is
never random and never caller-chosen. ``created_at`` and ``materialize_features``
are operational and are excluded from identity.

Proof before any write
----------------------
Nothing is written until every parent is independently proven:

* the parent exists, is ``SEALED`` under the request's source, and its
  ``dataset_version_id`` and content hash equal the ones the request declares
  (a version name alone is never proof);
* the Phase 3B.1 authority :func:`~trade_platform.historical_acquisition.
  sealed_dataset_matches_request` -- reused verbatim, not re-implemented --
  proves the parent's normalization version, instrument, provider identifier and
  symbol, per-kind event-timestamp set and persisted member multiplicity against
  the parent's own window;
* parent windows are chronological, contiguous, non-overlapping, gap-free and
  tile exactly the combined window;
* the union of parent member ids contains each normalized observation once.

Member selection boundary
-------------------------
Membership is built **only** from the ``historical_dataset_members`` rows of the
authorized parents. No "all observations for this source between X and Y" query
exists in this module, so an unrelated normalized observation that merely sits in
the same database can never enter the composition.

Sealing, replay and features
----------------------------
The union is sealed through the existing
:meth:`~trade_platform.historical_market_data.PostgresHistoricalMarketDataPipeline.
seal_dataset` -- there is no second sealing algorithm and no direct INSERT of a
dataset or member. The stored result is then read back and proven against the
combined window with the same identity authority, and its member set must equal
the proven union. If the deterministic target version already exists and proves
exactly, it is returned without resealing; if it exists with different semantics
or members the composition fails closed with a deterministic conflict and nothing
is overwritten. Optional canonical features are **recomputed** on the new dataset
identity (never copied from a parent), which is what makes the open-interest
predecessor of a parent's first observation the last observation of the previous
parent.

No provider, broker or account call is made; no raw or normalized observation is
created.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from .bybit_crypto_provider import BYBIT_PROVIDER_NAME, BYBIT_V5_SYMBOL_NAMESPACE
from .bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    BYBIT_BTCUSDT_SYMBOL,
)
from .historical_acquisition import (
    ALLOWED_OBSERVATION_KINDS,
    CanonicalAcquisitionEvidence,
    FeatureMaterializer,
    HistoricalAcquisitionPipeline,
    HistoricalAcquisitionRequest,
    MaterializedFeatureCounts,
    PostgresAcquisitionFeatureMaterializer,
    PostgresCanonicalAcquisitionEvidence,
    SealedDatasetView,
    acquisition_fingerprint,
    expected_feature_plan,
    sealed_dataset_matches_request,
)
from .historical_market_data import (
    AssetScope,
    HistoricalMarketDataError,
    ObservationKind,
    PostgresHistoricalMarketDataPipeline,
)
from .persistence import PostgresDatabase

__all__ = [
    "COMPOSITION_SEMANTIC_VERSION",
    "CompositionEvidence",
    "CompositionParent",
    "DatasetCompositionError",
    "DatasetCompositionRequest",
    "DatasetCompositionResult",
    "HistoricalDatasetCompositionService",
    "PostgresCompositionEvidence",
    "composed_dataset_version",
    "composition_identity_hash",
]

COMPOSITION_SEMANTIC_VERSION = "trade_platform.historical_dataset_composition.v1"
_TARGET_VERSION_PREFIX = "bybit-research-composite-v1"
_IDENTITY_HASH_PREFIX_LENGTH = 16
_MINUTE = timedelta(minutes=1)
_FIVE_MINUTES = timedelta(minutes=5)
_ZERO = timedelta(0)
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


class DatasetCompositionError(ValueError):
    """A fail-closed composition refusal carrying a stable machine-readable ``code``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CompositionParent:
    """The immutable identity of one authorized sealed parent dataset."""

    dataset_version_id: UUID
    dataset_version: str
    content_hash: str
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class DatasetCompositionRequest:
    """One fully-bound request to compose sealed parents into a single dataset.

    ``parents`` must be supplied already in chronological order; an unordered
    tuple is refused rather than silently sorted, so lineage is never ambiguous.
    ``materialize_features`` is operational and does not affect identity.
    """

    source_id: UUID
    instrument_id: str
    provider: str
    provider_symbol: str
    normalization_version: str
    observation_kinds: frozenset[ObservationKind]
    start: datetime
    end: datetime
    parents: tuple[CompositionParent, ...]
    materialize_features: bool = False


@dataclass(frozen=True, slots=True)
class DatasetCompositionResult:
    """The immutable outcome of one composition."""

    composition_identity_hash: str
    dataset_version: str
    dataset_version_id: UUID
    dataset_content_hash: str
    already_completed: bool
    member_count_by_kind: dict[ObservationKind, int]
    parent_dataset_version_ids: tuple[UUID, ...]
    feature_counts: MaterializedFeatureCounts | None = None


class CompositionEvidence(CanonicalAcquisitionEvidence, Protocol):
    """Canonical acquisition evidence plus exact dataset-membership reads."""

    def dataset_member_ids(self, dataset_version_id: UUID) -> tuple[UUID, ...]: ...


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def composition_identity_hash(request: DatasetCompositionRequest) -> str:
    """Deterministic SHA-256 over every semantic the composition binds.

    Includes each parent's id, version, content hash and window, in order.
    Excludes ``materialize_features`` and any creation time.
    """
    kinds = ",".join(sorted(kind.value for kind in request.observation_kinds))
    parents = ";".join(
        "|".join(
            (
                str(parent.dataset_version_id),
                parent.dataset_version,
                parent.content_hash,
                _iso(parent.start),
                _iso(parent.end),
            )
        )
        for parent in request.parents
    )
    canonical = "|".join(
        (
            COMPOSITION_SEMANTIC_VERSION,
            request.provider,
            str(request.source_id),
            request.instrument_id,
            request.provider_symbol,
            request.normalization_version,
            kinds,
            _iso(request.start),
            _iso(request.end),
            parents,
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def composed_dataset_version(request: DatasetCompositionRequest) -> str:
    """The deterministic target dataset version for a composition identity."""
    return ":".join(
        (
            _TARGET_VERSION_PREFIX,
            composition_identity_hash(request)[:_IDENTITY_HASH_PREFIX_LENGTH],
            request.start.astimezone(UTC).strftime(_TIMESTAMP_FORMAT),
            request.end.astimezone(UTC).strftime(_TIMESTAMP_FORMAT),
        )
    )


# ---------------------------------------------------------------------------
# Pure request validation
# ---------------------------------------------------------------------------


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise DatasetCompositionError(code)


def _validate_window(
    start: datetime, end: datetime, kinds: frozenset[ObservationKind], name: str
) -> None:
    for value, label in ((start, "start"), (end, "end")):
        _require(
            value.tzinfo is not None and value.utcoffset() == _ZERO,
            f"{name}_{label}_must_be_utc",
        )
    _require(start < end, f"{name}_window_invalid")
    _require(start.second == 0 and start.microsecond == 0, f"{name}_start_not_minute_aligned")
    _require((end - start) % _MINUTE == _ZERO, f"{name}_window_not_whole_minutes")
    if ObservationKind.OPEN_INTEREST in kinds:
        _require(start.minute % 5 == 0, f"{name}_start_not_open_interest_aligned")
        _require((end - start) % _FIVE_MINUTES == _ZERO, f"{name}_span_not_open_interest_aligned")


def validate_composition_request(request: DatasetCompositionRequest) -> None:
    """Refuse every request that is not a single unambiguous exact tiling."""
    _require(request.provider == BYBIT_PROVIDER_NAME, "unsupported_provider")
    _require(
        request.instrument_id == BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, "unsupported_instrument"
    )
    _require(request.provider_symbol == BYBIT_BTCUSDT_SYMBOL, "unsupported_provider_symbol")
    _require(bool(request.observation_kinds), "no_observation_kind_requested")
    outside = request.observation_kinds - ALLOWED_OBSERVATION_KINDS
    _require(
        not outside,
        "unsupported_observation_kind:" + ",".join(sorted(kind.value for kind in outside)),
    )
    _require(bool(request.normalization_version.strip()), "invalid_normalization_version")
    _validate_window(request.start, request.end, request.observation_kinds, "composition")
    _require(len(request.parents) >= 2, "composition_requires_at_least_two_parents")

    for parent in request.parents:
        _require(bool(parent.dataset_version.strip()), "invalid_parent_dataset_version")
        _require(bool(parent.content_hash.strip()), "invalid_parent_content_hash")
        _validate_window(parent.start, parent.end, request.observation_kinds, "parent")
    for label, values in (
        ("dataset_version_id", [parent.dataset_version_id for parent in request.parents]),
        ("dataset_version", [parent.dataset_version for parent in request.parents]),
        ("content_hash", [parent.content_hash for parent in request.parents]),
    ):
        _require(len(set(values)) == len(values), f"duplicate_parent:{label}")

    for previous, current in zip(request.parents, request.parents[1:], strict=False):
        _require(current.start >= previous.start, "parent_windows_unordered")
        _require(current.start >= previous.end, "parent_window_overlap")
        _require(current.start <= previous.end, "parent_window_gap")
    _require(
        request.parents[0].start == request.start and request.parents[-1].end == request.end,
        "parents_do_not_cover_requested_window",
    )


def _acquisition_request(
    request: DatasetCompositionRequest,
    *,
    dataset_version: str,
    start: datetime,
    end: datetime,
) -> HistoricalAcquisitionRequest:
    """The Phase 3B.1 request whose identity proof a dataset must satisfy."""
    acquisition = HistoricalAcquisitionRequest(
        source_id=request.source_id,
        instrument_id=request.instrument_id,
        provider=request.provider,
        provider_symbol=request.provider_symbol,
        start=start,
        end=end,
        observation_kinds=request.observation_kinds,
        normalization_version=request.normalization_version,
        dataset_version=dataset_version,
        maximum_pages_per_kind=1,
        materialize_features=False,
        idempotency_key="",
    )
    return replace(acquisition, idempotency_key=acquisition_fingerprint(acquisition))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HistoricalDatasetCompositionService:
    """Composes proven sealed parents into one deterministic sealed dataset."""

    def __init__(
        self,
        *,
        evidence: CompositionEvidence,
        pipeline: HistoricalAcquisitionPipeline,
        feature_materializer: FeatureMaterializer,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._evidence = evidence
        self._pipeline = pipeline
        self._feature_materializer = feature_materializer
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def for_postgres(
        cls,
        database: PostgresDatabase,
        *,
        feature_materializer: FeatureMaterializer | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> HistoricalDatasetCompositionService:
        return cls(
            evidence=PostgresCompositionEvidence(database),
            pipeline=PostgresHistoricalMarketDataPipeline(database),
            feature_materializer=feature_materializer
            or PostgresAcquisitionFeatureMaterializer(database),
            now=now,
        )

    def compose(self, request: DatasetCompositionRequest) -> DatasetCompositionResult:
        """Compose (or idempotently replay) one dataset; raises on any refusal."""
        validate_composition_request(request)
        self._validate_identity(request)
        union_ids = self._prove_parents(request)

        identity_hash = composition_identity_hash(request)
        target_version = composed_dataset_version(request)
        combined = _acquisition_request(
            request, dataset_version=target_version, start=request.start, end=request.end
        )

        existing = self._evidence.existing_sealed_dataset(request.source_id, target_version)
        if existing is not None:
            if not self._composed_matches(existing, combined, union_ids):
                raise DatasetCompositionError(
                    "composition_version_conflict:existing_dataset_semantics_differ"
                )
            return self._result(request, identity_hash, target_version, existing, True)

        try:
            self._pipeline.seal_dataset(
                request.source_id,
                target_version,
                request.normalization_version,
                union_ids,
                self._now(),
            )
        except HistoricalMarketDataError as error:
            # A concurrent identical composition may have sealed the same target
            # between the read above and this write; that is a replay, not a failure.
            raced = self._evidence.existing_sealed_dataset(request.source_id, target_version)
            if raced is not None and self._composed_matches(raced, combined, union_ids):
                return self._result(request, identity_hash, target_version, raced, True)
            raise DatasetCompositionError(f"composition_seal_failed:{error}") from error

        stored = self._evidence.existing_sealed_dataset(request.source_id, target_version)
        if stored is None or not self._composed_matches(stored, combined, union_ids):
            raise DatasetCompositionError("post_seal_identity_proof_failed")
        return self._result(request, identity_hash, target_version, stored, False)

    # ---- proofs ------------------------------------------------------------

    def _validate_identity(self, request: DatasetCompositionRequest) -> None:
        profile = self._evidence.source_profile(request.source_id)
        if profile is None:
            raise DatasetCompositionError("historical_source_not_found")
        _require(profile.provider == BYBIT_PROVIDER_NAME, "source_provider_mismatch")
        _require(
            profile.provider_identifier_namespace == BYBIT_V5_SYMBOL_NAMESPACE,
            "source_identifier_namespace_mismatch",
        )
        _require(profile.asset_scope == AssetScope.CRYPTO.value, "source_asset_scope_mismatch")
        missing = request.observation_kinds - profile.capabilities
        _require(
            not missing,
            "source_not_authorized_for_kind:" + ",".join(sorted(k.value for k in missing)),
        )
        resolved = set(
            self._evidence.resolve_instrument_ids(
                BYBIT_V5_SYMBOL_NAMESPACE, request.provider_symbol, self._now()
            )
        )
        _require(bool(resolved), "provider_symbol_unresolved")
        _require(resolved == {request.instrument_id}, "provider_symbol_instrument_mismatch")

    def _prove_parents(self, request: DatasetCompositionRequest) -> tuple[UUID, ...]:
        """Independently prove every parent, then return the exact member union."""
        latest_parent_created_at: datetime | None = None
        union: list[UUID] = []
        for parent in request.parents:
            view = self._evidence.existing_sealed_dataset(request.source_id, parent.dataset_version)
            if view is None:
                raise DatasetCompositionError(
                    f"parent_not_sealed_or_missing:{parent.dataset_version}"
                )
            _require(
                view.dataset_version_id == parent.dataset_version_id,
                f"parent_dataset_version_id_mismatch:{parent.dataset_version}",
            )
            _require(
                view.content_hash == parent.content_hash,
                f"parent_content_hash_mismatch:{parent.dataset_version}",
            )
            parent_request = _acquisition_request(
                request,
                dataset_version=parent.dataset_version,
                start=parent.start,
                end=parent.end,
            )
            _require(
                sealed_dataset_matches_request(view, parent_request),
                f"parent_identity_proof_failed:{parent.dataset_version}",
            )
            member_ids = self._evidence.dataset_member_ids(view.dataset_version_id)
            _require(
                len(member_ids) == sum(view.member_count_by_kind.values())
                and len(set(member_ids)) == len(member_ids),
                f"parent_member_set_inconsistent:{parent.dataset_version}",
            )
            union.extend(member_ids)
            if latest_parent_created_at is None or view.created_at > latest_parent_created_at:
                latest_parent_created_at = view.created_at
        _require(len(set(union)) == len(union), "duplicate_member_across_parents")
        # ``validate_composition_request`` guarantees at least two parents.
        if latest_parent_created_at is None or self._now() < latest_parent_created_at:
            raise DatasetCompositionError("composition_clock_precedes_parent_seal")
        return tuple(sorted(union, key=str))

    def _composed_matches(
        self,
        stored: SealedDatasetView,
        combined: HistoricalAcquisitionRequest,
        union_ids: tuple[UUID, ...],
    ) -> bool:
        """Combined-window identity proof AND exact member-set equality."""
        if not sealed_dataset_matches_request(stored, combined):
            return False
        stored_ids = self._evidence.dataset_member_ids(stored.dataset_version_id)
        return set(stored_ids) == set(union_ids) and len(stored_ids) == len(union_ids)

    # ---- result / features ---------------------------------------------------

    def _result(
        self,
        request: DatasetCompositionRequest,
        identity_hash: str,
        target_version: str,
        stored: SealedDatasetView,
        already_completed: bool,
    ) -> DatasetCompositionResult:
        feature_counts = (
            self._ensure_features(request, stored.dataset_version_id)
            if request.materialize_features
            else None
        )
        return DatasetCompositionResult(
            composition_identity_hash=identity_hash,
            dataset_version=target_version,
            dataset_version_id=stored.dataset_version_id,
            dataset_content_hash=stored.content_hash,
            already_completed=already_completed,
            member_count_by_kind=dict(stored.member_count_by_kind),
            parent_dataset_version_ids=tuple(p.dataset_version_id for p in request.parents),
            feature_counts=feature_counts,
        )

    def _ensure_features(
        self, request: DatasetCompositionRequest, dataset_version_id: UUID
    ) -> MaterializedFeatureCounts:
        """Recompute canonical features on the composed dataset, replay-safe.

        Expectations derive from the combined window alone. Existing counts that
        already match are a read-only replay; a shortfall is filled through the
        idempotent feature authority; an excess or a post-run mismatch fails closed.
        """
        combined = _acquisition_request(
            request, dataset_version="", start=request.start, end=request.end
        )
        basis_ats, open_interest_ats, expected_basis, expected_oi = expected_feature_plan(combined)
        resolve_at = self._now()
        try:
            basis_id = (
                self._feature_materializer.resolve_basis_feature_id(resolve_at)
                if basis_ats
                else None
            )
            oi_id = (
                self._feature_materializer.resolve_open_interest_feature_id(resolve_at)
                if open_interest_ats
                else None
            )
        except Exception as error:
            raise DatasetCompositionError(f"feature_identity_failed:{error}") from error
        feature_ids = tuple(fid for fid in (basis_id, oi_id) if fid is not None)

        def counts() -> tuple[int, int]:
            found = (
                self._evidence.feature_counts(dataset_version_id, feature_ids)
                if feature_ids
                else {}
            )
            return (
                found.get(basis_id, 0) if basis_id else 0,
                found.get(oi_id, 0) if oi_id else 0,
            )

        have_basis, have_oi = counts()
        if have_basis > expected_basis or have_oi > expected_oi:
            raise DatasetCompositionError(
                f"existing_feature_count_exceeds_expected:basis={have_basis}/{expected_basis},"
                f"open_interest_change={have_oi}/{expected_oi}"
            )
        if (have_basis, have_oi) != (expected_basis, expected_oi):
            decision_at = self._now()
            try:
                self._feature_materializer.materialize_features(
                    instrument_id=request.instrument_id,
                    dataset_version_id=dataset_version_id,
                    basis_event_ats=basis_ats,
                    open_interest_event_ats=open_interest_ats,
                    decision_at=decision_at,
                    definition_created_at=decision_at,
                )
            except Exception as error:
                raise DatasetCompositionError(
                    f"feature_materialization_failed:{error or type(error).__name__}"
                ) from error
            have_basis, have_oi = counts()
            if (have_basis, have_oi) != (expected_basis, expected_oi):
                raise DatasetCompositionError(
                    f"feature_materialization_incomplete:basis={have_basis}/{expected_basis},"
                    f"open_interest_change={have_oi}/{expected_oi}"
                )
        return MaterializedFeatureCounts(have_basis, have_oi)


class PostgresCompositionEvidence(PostgresCanonicalAcquisitionEvidence):
    """The acquisition evidence reads plus exact per-dataset member ids."""

    def dataset_member_ids(self, dataset_version_id: UUID) -> tuple[UUID, ...]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT normalized_observation_id FROM historical_dataset_members "
                "WHERE dataset_version_id=%s ORDER BY normalized_observation_id::text",
                (dataset_version_id,),
            )
            rows = cursor.fetchall()
        return tuple(UUID(str(row[0])) for row in rows)
