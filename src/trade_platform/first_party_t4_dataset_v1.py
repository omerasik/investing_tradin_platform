"""Phase R3A -- first-party T4 datasets: catalog, sealed-clock resolver, tier and V3 features.

``RESEARCH_ONLY``. Wires a :class:`~trade_platform.first_party_t4_seal_v1.FirstPartyT4SealV1`
into the platform's existing authorities without widening any of them:

* **Catalog.** :class:`PostgresFirstPartyT4CatalogV1` records a sealed segment's
  identity and frame manifests in ``first_party_t4_datasets``. PostgreSQL holds
  identity, manifest, lineage and the audit ``sealed_at``; the observations stay
  in Parquet.
* **Provenance and tier.** :func:`issue_t4_evidence_tier_v1` derives provenance
  with :func:`~trade_platform.real_market_data_provenance_v1.evaluate_first_party_capture_provenance_v1`
  and the evidence tier with the unchanged
  :func:`~trade_platform.evidence_tier_authority_v1.evaluate_evidence_tier_v1`,
  feeding it only the timing facts the provenance verdict binds. The first-party
  source's registered timing contract grants ``T4_FIRST_PARTY_CAPTURE``; the
  source name grants nothing.
* **Sealed clocks.** :class:`FirstPartyT4SealedClockResolverV1` is the reviewed
  resolver through which T4 arrival clocks reach the doctrine. It accepts only a
  seal rebuilt from raw capture (``raw_replayed``) and answers only references
  that exist in that seal's frames, reading the clocks from the frames whose
  logical hashes the rebuild re-derived.
* **Features.** :func:`build_t4_basis_features_v3` materializes the canonical
  ``crypto_mark_index_basis`` formula as R2A.2 three-clock (V3) rows from actual
  T4 observations, one per minute: the *first* basis observation whose venue
  event time is at or after each minute boundary. Choosing the first after a
  boundary is causal (it is known to be first the moment it arrives); choosing
  the *last* of a minute would leak the absence of later updates.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from .crypto_derivatives_features import _MARK_INDEX_REQUIRED_FIELDS, CRYPTO_MARK_INDEX_BASIS
from .evidence_tier_authority_v1 import (
    EvidenceTierVerdictV1,
    evaluate_evidence_tier_v1,
    first_party_sealed_timing_facts_v1,
)
from .feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterializationV3,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from .first_party_capture_archive_v1 import nanos_to_datetime
from .first_party_t4_seal_v1 import (
    FirstPartyT4SealError,
    FirstPartyT4SealV1,
    iter_frame_rows_v1,
    verify_t4_dataset_v1,
)
from .knowledge_time_doctrine_v1 import (
    HostClockBoundV1,
    ObservationKnowledgeV1,
    SealedObservationClocksV1,
    derive_observation_knowledge_v1,
)
from .persistence import PostgresDatabase
from .real_market_data_provenance_v1 import (
    RealMarketDataProvenanceV1,
    evaluate_first_party_capture_provenance_v1,
)
from .research_data_plane_v1 import (
    T4_BASIS_FRAME,
    T4_REFERENCE_PRICE_FRAME,
    T4_TRADE_FRAME,
    ResearchFrameStoreV1,
)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

#: The canonical basis feature's value scale (Feature Authority ``NUMERIC(38,12)``).
_VALUE_SCALE: Final = Decimal("1E-12")
T4_BASIS_FEATURE_SAMPLING_V1: Final = "first-basis-observation-at-or-after-each-minute-boundary-v1"
_MICROS_PER_MINUTE: Final = 60_000_000


class FirstPartyT4DatasetError(ValueError):
    """Raised when a T4 dataset, resolver or feature cannot be proven."""


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CataloguedT4DatasetV1:
    dataset_version_id: UUID
    content_hash: str
    identity: Mapping[str, Any]
    frame_manifests: Mapping[str, str]
    sealed_at: datetime


def _json(value: Any) -> Any:
    return value if isinstance(value, (dict, list)) else json.loads(value)


class PostgresFirstPartyT4CatalogV1:
    """``first_party_t4_datasets``: append-only catalog of sealed T4 segments."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, seal: FirstPartyT4SealV1) -> CataloguedT4DatasetV1:
        """Catalog a seal (idempotent by identity). Returns the stored row.

        Re-registering the same identity keeps the first ``sealed_at``: the
        dataset is the same evidence, and the first seal is when the platform
        first held it.
        """
        if not seal.integrity_verified() or not seal.raw_replayed:
            raise FirstPartyT4DatasetError("only_a_raw_replayed_intact_seal_may_be_catalogued")
        identity = seal.identity
        window = identity["window"]
        segment = identity["segment"]
        timing = seal.timing_facts
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO first_party_t4_datasets (dataset_version_id, content_hash, "
                "schema_version, source_id, session_id, utc_day, window_index, "
                "start_arrival_nanos, end_arrival_nanos_inclusive, first_market_knowledge_at, "
                "last_market_knowledge_at, observation_count, distinct_knowledge_time_count, "
                "identity, frame_manifests, sealed_at, registered_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) "
                "ON CONFLICT (dataset_version_id) DO NOTHING",
                (
                    seal.dataset_version_id, seal.content_hash, seal.schema_version,
                    seal.source_id, UUID(str(identity["session_id"])),
                    date.fromisoformat(str(window["utc_day"])), int(window["window_index"]),
                    int(segment["start_arrival_nanos"]),
                    int(segment["end_arrival_nanos_inclusive"]),
                    seal.segment_first_knowledge_at, seal.segment_last_knowledge_at,
                    timing.observations_with_knowledge_time, timing.distinct_knowledge_time_count,
                    json.dumps(dict(identity), sort_keys=True, default=str),
                    json.dumps(dict(seal.frame_manifests), sort_keys=True),
                    seal.sealed_at, datetime.now(UTC),
                ),
            )
        stored = self.load(seal.dataset_version_id)
        if stored.content_hash != seal.content_hash or json.dumps(
            dict(stored.identity), sort_keys=True, default=str
        ) != json.dumps(dict(identity), sort_keys=True, default=str):
            raise FirstPartyT4DatasetError("t4_catalog_conflict")
        return stored

    def load(self, dataset_version_id: UUID) -> CataloguedT4DatasetV1:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash, identity, frame_manifests, sealed_at "
                "FROM first_party_t4_datasets WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise FirstPartyT4DatasetError("t4_dataset_not_catalogued")
        return CataloguedT4DatasetV1(
            dataset_version_id=dataset_version_id,
            content_hash=str(row[0]).strip(),
            identity=_json(row[1]),
            frame_manifests={str(key): str(value) for key, value in _json(row[2]).items()},
            sealed_at=row[3],
        )

    def dataset_ids(self) -> tuple[UUID, ...]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT dataset_version_id FROM first_party_t4_datasets "
                "ORDER BY start_arrival_nanos, dataset_version_id"
            )
            return tuple(UUID(str(row[0])) for row in cursor.fetchall())


# ---------------------------------------------------------------------------
# Provenance and tier
# ---------------------------------------------------------------------------


def issue_t4_evidence_tier_v1(
    seal: FirstPartyT4SealV1,
) -> tuple[RealMarketDataProvenanceV1, EvidenceTierVerdictV1]:
    """Provenance, then the evidence tier, both derived from the seal alone."""
    provenance = evaluate_first_party_capture_provenance_v1(seal)
    if not provenance.is_proven_real():
        raise FirstPartyT4DatasetError(
            "t4_provenance_not_proven:" + ",".join(provenance.reasons or ("unknown",))
        )
    verdict = evaluate_evidence_tier_v1(first_party_sealed_timing_facts_v1(provenance), provenance)
    return provenance, verdict


# ---------------------------------------------------------------------------
# Sealed clock resolver
# ---------------------------------------------------------------------------


class FirstPartyT4SealedClockResolverV1:
    """Sealed T4 clock facts for the observations of *one* rebuilt seal.

    ``event_at`` is the venue timestamp Bybit stamped (trade time ``T`` for a
    trade, message ``ts`` for a reference price), ``arrival_at`` the recorder
    arrival (rounded up to the microsecond), ``arrival_clock_bound`` the bound
    the clock rule derived from the session's sealed samples, and
    ``platform_recorded_at`` the catalogued host-clock seal instant. A
    reference outside this seal, or a request for another dataset, resolves to
    nothing -- which the doctrine turns into a refusal.
    """

    __slots__ = ("_clocks", "_seal", "_store")

    def __init__(self, seal: FirstPartyT4SealV1, *, store: ResearchFrameStoreV1) -> None:
        self._require(seal)
        # The frames the clocks are read from are re-hashed byte for byte here,
        # not only when the seal was built.
        for kind in (T4_REFERENCE_PRICE_FRAME.kind, T4_TRADE_FRAME.kind):
            store.verify(store.load_manifest(seal.frame_manifests[kind]))
        self._seal = seal
        self._store = store
        self._clocks: dict[str, SealedObservationClocksV1] | None = None

    @staticmethod
    def _require(seal: object) -> None:
        if not isinstance(seal, FirstPartyT4SealV1) or not seal.integrity_verified():
            raise FirstPartyT4DatasetError("t4_resolver_requires_an_intact_seal")
        if not seal.raw_replayed:
            raise FirstPartyT4DatasetError("t4_resolver_requires_a_seal_rebuilt_from_raw_capture")

    @property
    def dataset_version_id(self) -> UUID:
        return self._seal.dataset_version_id

    def _load(self) -> dict[str, SealedObservationClocksV1]:
        if self._clocks is not None:
            return self._clocks
        clocks: dict[str, SealedObservationClocksV1] = {}
        recorded = self._seal.sealed_at
        for frame in (T4_REFERENCE_PRICE_FRAME, T4_TRADE_FRAME):
            kind = frame.kind
            names = frame.schema.names
            ref_i, event_i = names.index("observation_reference"), names.index("event_at")
            arrival_i, bound_i = names.index("arrival_utc_nanos"), names.index("clock_bound_nanos")
            evidence_i = names.index("clock_bound_evidence")
            for row in iter_frame_rows_v1(self._seal, kind, self._store):
                reference = str(row[ref_i])
                if reference in clocks:
                    raise FirstPartyT4DatasetError("t4_observation_reference_not_unique")
                clocks[reference] = SealedObservationClocksV1(
                    event_at=_aware(row[event_i]),
                    platform_recorded_at=recorded,
                    arrival_at=nanos_to_datetime(int(str(row[arrival_i]))),
                    arrival_clock_bound=HostClockBoundV1(
                        venue_minus_host_upper_bound_nanos=int(str(row[bound_i])),
                        evidence_reference=str(row[evidence_i]),
                    ),
                )
        self._clocks = clocks
        return clocks

    def __call__(
        self, dataset_version_id: UUID, references: Sequence[str]
    ) -> Mapping[str, SealedObservationClocksV1]:
        # Re-checked on every call: a seal swapped or edited after construction
        # (in-process tampering) is refused rather than trusted from the cache.
        self._require(self._seal)
        if dataset_version_id != self._seal.dataset_version_id:
            return {}
        clocks = self._load()
        return {reference: clocks[reference] for reference in references if reference in clocks}


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise FirstPartyT4DatasetError("frame_instant_malformed")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# V3 basis features from T4 observations
# ---------------------------------------------------------------------------


def _observation_knowledge(
    verdict: EvidenceTierVerdictV1,
    seal: FirstPartyT4SealV1,
    reference: str,
    clocks: SealedObservationClocksV1,
) -> ObservationKnowledgeV1:
    return derive_observation_knowledge_v1(
        verdict,
        dataset_version_id=seal.dataset_version_id,
        dataset_content_hash=seal.content_hash,
        observation_reference=reference,
        event_at=clocks.event_at,
        platform_recorded_at=clocks.platform_recorded_at,
        arrival_at=clocks.arrival_at,
        arrival_clock_bound=clocks.arrival_clock_bound,
    )


def select_t4_basis_samples_v1(
    seal: FirstPartyT4SealV1, store: ResearchFrameStoreV1
) -> list[tuple[str, str, datetime, Decimal, Decimal]]:
    """``(mark_ref, index_ref, event_at, mark, index)`` -- first basis at/after each minute.

    The basis frame is in emission (arrival) order and its venue event times
    are checked to be non-decreasing, so "first at or after the boundary" is
    decided the moment that observation arrives, from nothing later.
    """
    names = T4_BASIS_FRAME.schema.names
    mark_i, index_i = names.index("mark_reference"), names.index("index_reference")
    event_i = names.index("event_at")
    mark_p, index_p = names.index("mark_price"), names.index("index_price")
    samples: list[tuple[str, str, datetime, Decimal, Decimal]] = []
    last_minute: int | None = None
    previous_event: datetime | None = None
    for row in iter_frame_rows_v1(seal, T4_BASIS_FRAME.kind, store):
        event = _aware(row[event_i])
        if previous_event is not None and event < previous_event:
            raise FirstPartyT4DatasetError("t4_basis_event_time_regressed")
        previous_event = event
        micros = (event - _EPOCH) // timedelta(microseconds=1)
        minute = micros // _MICROS_PER_MINUTE
        if minute == last_minute:
            continue
        last_minute = minute
        samples.append(
            (str(row[mark_i]), str(row[index_i]), event,
             Decimal(str(row[mark_p])), Decimal(str(row[index_p])))
        )
    return samples


def build_t4_basis_features_v3(
    seal: FirstPartyT4SealV1,
    *,
    verdict: EvidenceTierVerdictV1,
    resolver: FirstPartyT4SealedClockResolverV1,
    store: ResearchFrameStoreV1,
    feature_id: UUID,
    computed_at: datetime,
) -> list[FeatureMaterializationV3]:
    """The canonical basis formula as V3 rows over actual T4 observations.

    Every input's clocks come from ``resolver`` -- the same sealed facts the
    verification path re-derives from -- so a row built here verifies against
    the same resolver and verdict, and cannot carry a clock the evidence lacks.
    ``computed_at`` is operational only; it is not part of any identity.
    """
    if verdict.dataset_version_id != seal.dataset_version_id:
        raise FirstPartyT4DatasetError("t4_verdict_dataset_mismatch")
    if resolver.dataset_version_id != seal.dataset_version_id:
        raise FirstPartyT4DatasetError("t4_resolver_dataset_mismatch")
    samples = select_t4_basis_samples_v1(seal, store)
    references = sorted({ref for sample in samples for ref in sample[:2]})
    clocks = resolver(seal.dataset_version_id, references)
    instrument = str(seal.identity["instrument"])
    rows: list[FeatureMaterializationV3] = []
    for mark_ref, index_ref, event_at, mark, index in samples:
        mark_clock, index_clock = clocks.get(mark_ref), clocks.get(index_ref)
        if mark_clock is None or index_clock is None:
            raise FirstPartyT4DatasetError("t4_basis_input_not_in_sealed_evidence")
        inputs = (
            _observation_knowledge(verdict, seal, mark_ref, mark_clock),
            _observation_knowledge(verdict, seal, index_ref, index_clock),
        )
        if event_at != max(mark_clock.event_at, index_clock.event_at):
            raise FirstPartyT4DatasetError("t4_basis_event_disagrees_with_its_components")
        with localcontext() as context:  # the canonical V2/V3 basis arithmetic, context fixed
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            value = ((mark - index) / index).quantize(_VALUE_SCALE, rounding=ROUND_HALF_EVEN)
        rows.append(
            FeatureMaterializationV3.create(
                feature_id=feature_id,
                subject_type=FeatureSubjectType.INSTRUMENT,
                subject_id=instrument,
                dataset_version=str(seal.dataset_version_id),
                event_at=event_at,
                effective_at=event_at,
                inputs=inputs,
                computed_at=computed_at,
                source_observation_manifest=(
                    f"t4_dataset_version_id:{seal.dataset_version_id}",
                    f"t4_dataset_content_hash:{seal.content_hash}",
                    f"source_id:{seal.source_id}",
                    f"evidence_tier_verdict_id:{verdict.evidence_id}",
                    f"instrument_id:{instrument}",
                    f"sampling:{T4_BASIS_FEATURE_SAMPLING_V1}",
                    f"mark_observation_reference:{mark_ref}",
                    f"index_observation_reference:{index_ref}",
                    f"mark_event_at:{mark_clock.event_at.astimezone(UTC).isoformat()}",
                    f"index_event_at:{index_clock.event_at.astimezone(UTC).isoformat()}",
                ),
                value=value,
                quality_status=FeatureQualityStatus.VALIDATED,
            )
        )
    return rows


def t4_seal_from_catalog_v1(
    catalogued: CataloguedT4DatasetV1,
    *,
    store: ResearchFrameStoreV1,
    capture_root: Path,
) -> FirstPartyT4SealV1:
    """Rebuild a catalogued dataset from raw capture (the only authority-grade read)."""
    seal = verify_t4_dataset_v1(
        catalogued.identity,
        frame_manifests=catalogued.frame_manifests,
        sealed_at=catalogued.sealed_at,
        store=store,
        capture_root=capture_root,
    )
    if seal.dataset_version_id != catalogued.dataset_version_id or seal.content_hash != catalogued.content_hash:
        raise FirstPartyT4SealError("catalogued_identity_does_not_reproduce")
    return seal


def distinct_instants(values: Iterable[datetime | None]) -> int:
    return len({value for value in values if value is not None})


CALCULATION_VERSION_MARK_INDEX_BASIS_T4: Final = (
    "derivatives-crypto-mark-index-basis-r3a-first-party-t4-v1"
)
CRYPTO_MARK_INDEX_BASIS_T4_SEMANTIC_VERSION: Final = "3.0.0"


def crypto_mark_index_basis_first_party_t4_definition(
    created_at: datetime,
) -> FeatureDefinitionVersion:
    """``crypto_mark_index_basis`` 3.0.0: the same formula over asynchronous T4 components.

    The 1.0.0/2.0.0 definitions require mark and index to share one
    ``event_at`` (the REST bar close). First-party capture never has that:
    Bybit sends the two components independently. So this version states the
    T4 semantics instead of bending the old ones -- each component keeps its
    own venue time and arrival, the value's ``event_at`` is the later of the
    two venue times, its market knowledge time is the later of the two
    doctrine knowledge times, and one value is sampled per minute by
    :data:`T4_BASIS_FEATURE_SAMPLING_V1`. Earlier definitions and their rows
    are untouched.
    """
    return FeatureDefinitionVersion(
        CRYPTO_MARK_INDEX_BASIS, FeatureFamily.DERIVATIVES,
        CRYPTO_MARK_INDEX_BASIS_T4_SEMANTIC_VERSION, "quant",
        "Crypto mark/index basis for one instrument, (mark_price - index_price) / "
        "index_price, over first-party T4 capture: mark and index are independent "
        "components of one sealed T4 segment dataset, each with its own venue time and "
        "recorder arrival; no synchronization is assumed and nothing crosses a segment "
        "boundary.",
        ("MARK_PRICE", "INDEX_PRICE"), _MARK_INDEX_REQUIRED_FIELDS, "as_observed",
        "event=max(mark venue ts, index venue ts); effective_at=event; "
        "market_knowledge_at=max over mark,index of recorder arrival plus the session's "
        "bracketed venue-clock bound (R2A doctrine, T4); platform_recorded_at=dataset seal "
        f"time, audit only; sampling={T4_BASIS_FEATURE_SAMPLING_V1}; computed_at operational "
        "only", 0, {}, "fail_closed_no_materialization", "reject", "reject_future_knowledge",
        None, None, "dimensionless", CALCULATION_VERSION_MARK_INDEX_BASIS_T4, created_at,
    )


__all__ = [
    "T4_BASIS_FEATURE_SAMPLING_V1",
    "CataloguedT4DatasetV1",
    "FirstPartyT4DatasetError",
    "FirstPartyT4SealedClockResolverV1",
    "PostgresFirstPartyT4CatalogV1",
    "build_t4_basis_features_v3",
    "distinct_instants",
    "issue_t4_evidence_tier_v1",
    "select_t4_basis_samples_v1",
    "t4_seal_from_catalog_v1",
]
