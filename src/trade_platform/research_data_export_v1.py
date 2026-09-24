"""Phase R2B -- PostgreSQL authority -> columnar research frames, and back to the catalog.

``RESEARCH_ONLY``. Reads sealed historical datasets and Feature Authority rows;
writes only content-addressed Parquet objects (outside Git) and immutable
catalog rows in ``research_frame_manifests``. No provider call, no economic
assumption, no evidence-tier change.

* :func:`export_dataset_frames_v1` streams a sealed dataset's members, per
  observation kind, into :mod:`~trade_platform.research_data_plane_v1` frames
  with a server-side cursor, so memory is bounded by one fetch plus one row
  group whatever the dataset size.
* :func:`reconcile_frame_with_source_v1` proves lineage: per-kind member
  counts, and an identity-and-value digest computed *independently* by
  PostgreSQL and from the Parquet rows, must match exactly.
* :func:`build_mark_index_basis_frame_v1` derives the basis feature frame from
  the exported reference-price frame (DuckDB for ranking and pairing, Python
  ``Decimal`` for the arithmetic) under a deterministic cache key.
* :func:`compare_feature_frame_with_authority_v1` proves value parity with the
  existing Feature Authority materialization after its declared ``NUMERIC(38,12)``
  quantization.

Market knowledge stays exactly as defined by the evidence: the exporter never
derives a knowledge time, so ``market_knowledge_at`` is written NULL and the
manifest records the source's granted evidence tier. A T1 dataset stays T1.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

import duckdb

from .evidence_tier_authority_v1 import authorized_timing_contracts_v1
from .persistence import PostgresDatabase
from .research_data_plane_v1 import (
    FEATURE_FRAME,
    OHLCV_FRAME,
    OPEN_INTEREST_FRAME,
    REFERENCE_PRICE_FRAME,
    FrameManifestV1,
    FrameSchemaV1,
    ResearchDataPlaneError,
    ResearchFrameStoreV1,
    feature_frame_cache_key_v1,
)

EXPORT_IMPLEMENTATION_VERSION: Final = "research-data-export-r2b-v1"
BASIS_FRAME_IMPLEMENTATION_VERSION: Final = "mark-index-basis-frame-r2b-v1"
_FETCH_ROWS: Final = 20_000
_FEATURE_SCALE: Final = Decimal("1E-12")

#: Every statement below is a fixed string; only values are bound.
_MEMBERS = (
    "FROM historical_dataset_members m "
    "JOIN historical_dataset_versions d ON d.dataset_version_id=m.dataset_version_id "
    "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
    "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
)
_IDENTITY_COLUMNS = (
    "n.instrument_id, r.observation_kind, r.provider_identifier, r.revision, "
    "n.normalized_observation_id::text, r.raw_observation_id::text, "
    "r.event_at, r.effective_at, NULL::timestamptz, r.ingested_at, "
    "GREATEST(n.normalized_at, d.created_at)"
)
_ORDER = (
    ' ORDER BY n.instrument_id COLLATE "C", r.observation_kind COLLATE "C", r.event_at, '
    'r.provider_identifier COLLATE "C", r.revision, r.raw_observation_id::text COLLATE "C"'
)


@dataclass(frozen=True, slots=True)
class _KindExport:
    frame: FrameSchemaV1
    kinds: tuple[str, ...]
    select: str
    join: str
    #: SQL text expressions, in order, for the independent reconciliation digest.
    digest_values: tuple[str, ...]


_EXPORTS: Final = (
    _KindExport(
        REFERENCE_PRICE_FRAME, ("MARK_PRICE", "INDEX_PRICE"),
        "p.price, p.price_asset",
        "JOIN crypto_reference_price_observations p ON p.normalized_observation_id=n.normalized_observation_id ",
        ("p.price::numeric(38,18)::text", "p.price_asset"),
    ),
    _KindExport(
        OHLCV_FRAME, ("OHLCV",),
        "n.normalized_value->>'open', n.normalized_value->>'high', n.normalized_value->>'low', "
        "n.normalized_value->>'close', n.normalized_value->>'volume', "
        "n.normalized_value->>'interval'",
        "",
        tuple(
            f"(n.normalized_value->>'{name}')::numeric(38,18)::text"
            for name in ("open", "high", "low", "close", "volume")
        ) + ("n.normalized_value->>'interval'",),
    ),
    _KindExport(
        OPEN_INTEREST_FRAME, ("OPEN_INTEREST",),
        "o.open_interest, o.unit, o.unit_asset",
        "JOIN open_interest_observations o ON o.normalized_observation_id=n.normalized_observation_id ",
        ("o.open_interest::numeric(38,18)::text", "o.unit", "coalesce(o.unit_asset, '')"),
    ),
)


def _stream(database: PostgresDatabase, statement: str, params: tuple[object, ...]) -> Iterator[tuple[Any, ...]]:
    """One snapshot, streamed through a server-side cursor in bounded fetches."""
    with database.transaction() as connection:
        cursor = connection.cursor(name=f"research_export_{uuid4().hex}")
        cursor.execute(statement, params)
        while True:
            rows = cursor.fetchmany(_FETCH_ROWS)
            if not rows:
                break
            yield from rows
        cursor.close()


def _dataset_facts(database: PostgresDatabase, dataset_version_id: UUID) -> dict[str, Any]:
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, content_hash, source_id, created_at, version FROM "
            "historical_dataset_versions WHERE dataset_version_id=%s",
            (dataset_version_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ResearchDataPlaneError("dataset_not_found")
        if str(row[0]) != "SEALED":
            raise ResearchDataPlaneError("dataset_not_sealed")
        cursor.execute(
            "SELECT r.observation_kind, count(*) " + _MEMBERS +  # nosec B608 - fixed fragment
            "WHERE m.dataset_version_id=%s GROUP BY 1 ORDER BY 1",
            (dataset_version_id,),
        )
        counts = {str(kind): int(count) for kind, count in cursor.fetchall()}
    source_id = UUID(str(row[2]))
    granted = next(
        (c.granted_tier for c in authorized_timing_contracts_v1() if c.source_id == source_id),
        "NO_AUTHORIZED_TIMING_CONTRACT",
    )
    return {
        "dataset_version_id": str(dataset_version_id),
        "dataset_content_hash": str(row[1]),
        "dataset_version": str(row[4]),
        "source_id": str(source_id),
        "dataset_created_at": row[3].astimezone(UTC).isoformat(),
        "member_count_by_kind": counts,
        "source_granted_evidence_tier": granted,
        "market_knowledge_at": "not_derived_by_export_null_in_every_row",
    }


def export_dataset_frames_v1(
    database: PostgresDatabase,
    store: ResearchFrameStoreV1,
    dataset_version_id: UUID,
    *,
    on_row: Callable[[int], None] | None = None,
) -> dict[str, FrameManifestV1]:
    """Export every supported observation kind of one sealed dataset. Deterministic.

    Kinds the dataset does not contain produce no frame. A member of an
    unsupported kind fails closed: an export that silently dropped rows would
    misstate the dataset.
    """
    facts = _dataset_facts(database, dataset_version_id)
    supported = {kind for export in _EXPORTS for kind in export.kinds}
    unsupported = sorted(set(facts["member_count_by_kind"]) - supported)
    if unsupported:
        raise ResearchDataPlaneError("dataset_has_unsupported_kinds:" + ",".join(unsupported))
    manifests: dict[str, FrameManifestV1] = {}
    for export in _EXPORTS:
        expected = sum(facts["member_count_by_kind"].get(kind, 0) for kind in export.kinds)
        if expected == 0:
            continue
        statement = (
            f"SELECT {_IDENTITY_COLUMNS}, {export.select} " + _MEMBERS + export.join  # nosec B608
            + "WHERE m.dataset_version_id=%s AND r.observation_kind=ANY(%s)" + _ORDER
        )
        rows = _stream(database, statement, (dataset_version_id, list(export.kinds)))
        if on_row is not None:
            rows = _counting(rows, on_row)
        lineage = {
            **facts,
            "frame_observation_kinds": list(export.kinds),
            "source_member_count": expected,
            "implementation_version": EXPORT_IMPLEMENTATION_VERSION,
        }
        manifest = store.write_frame(export.frame, rows, lineage=lineage)
        if manifest.row_count != expected:
            raise ResearchDataPlaneError("exported_row_count_differs_from_dataset_members")
        manifests[export.frame.kind] = manifest
    return manifests


def _counting(rows: Iterator[tuple[Any, ...]], on_row: Callable[[int], None]) -> Iterator[tuple[Any, ...]]:
    for count, row in enumerate(rows, start=1):
        on_row(count)
        yield row


# ---------------------------------------------------------------------------
# Lineage reconciliation
# ---------------------------------------------------------------------------


_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO: Final = timedelta(microseconds=1)


def _micros(value: datetime) -> int:
    """Integer microseconds since the epoch -- PostgreSQL's ``extract(epoch)*1e6``."""
    return (value.astimezone(UTC) - _EPOCH) // _MICRO


@dataclass(frozen=True, slots=True)
class FrameReconciliationV1:
    frame_kind: str
    source_rows: int
    frame_rows: int
    source_digest: str
    frame_digest: str

    @property
    def reconciled(self) -> bool:
        return self.source_rows == self.frame_rows and self.source_digest == self.frame_digest


def reconcile_frame_with_source_v1(
    database: PostgresDatabase, store: ResearchFrameStoreV1, manifest: FrameManifestV1
) -> FrameReconciliationV1:
    """Recompute an identity+value digest on both sides, independently.

    PostgreSQL formats each member as ``normalized_id|event_us|effective_us|
    revision|value...`` with its own numeric and epoch arithmetic; the frame
    side formats the Parquet rows the same way from Python values. Both sides
    stream in the frame's sort order and are hashed incrementally, so memory
    stays bounded whatever the dataset size.
    """
    export = next((item for item in _EXPORTS if item.frame.kind == manifest.frame_kind), None)
    if export is None:
        raise ResearchDataPlaneError("frame_kind_has_no_source_reconciliation")
    dataset_version_id = UUID(str(manifest.lineage["dataset_version_id"]))
    line = " || '|' || ".join(
        f"({expression})"
        for expression in (
            "n.normalized_observation_id::text",
            "(extract(epoch from r.event_at)*1000000)::bigint::text",
            "(extract(epoch from r.effective_at)*1000000)::bigint::text",
            "r.revision::text",
            *export.digest_values,
        )
    )
    source_digest = hashlib.sha256()
    source_rows = 0
    for (text,) in _stream(
        database,
        f"SELECT {line} " + _MEMBERS + export.join  # nosec B608 - fixed fragments only
        + "WHERE m.dataset_version_id=%s AND r.observation_kind=ANY(%s)" + _ORDER,
        (dataset_version_id, list(export.kinds)),
    ):
        source_digest.update(str(text).encode() + b"\n")
        source_rows += 1

    schema = export.frame.schema
    index = {field.name: position for position, field in enumerate(schema)}
    value_names = [field.name for field in schema][11:]
    frame_digest = hashlib.sha256()
    frame_rows = 0
    for item in store.iter_rows(manifest):
        values = ["" if item[index[name]] is None else str(item[index[name]]) for name in value_names]
        text = "|".join(
            (
                str(item[index["normalized_observation_id"]]),
                str(_micros(item[index["event_at"]])),  # type: ignore[arg-type]
                str(_micros(item[index["effective_at"]])),  # type: ignore[arg-type]
                str(item[index["revision"]]),
                *values,
            )
        )
        frame_digest.update(text.encode() + b"\n")
        frame_rows += 1
    return FrameReconciliationV1(
        manifest.frame_kind, source_rows, frame_rows, source_digest.hexdigest(),
        frame_digest.hexdigest(),
    )


# ---------------------------------------------------------------------------
# Basis feature frame and parity
# ---------------------------------------------------------------------------


def build_mark_index_basis_frame_v1(
    store: ResearchFrameStoreV1,
    reference_prices: FrameManifestV1,
    *,
    semantic_version: str,
    calculation_version: str,
    eligible_instrument: Callable[[str], bool],
) -> tuple[FrameManifestV1, bool]:
    """The ``crypto_mark_index_basis`` frame from a reference-price frame.

    Returns ``(manifest, cache_hit)``. The rules are the Feature Authority
    calculator's: per ``(instrument, kind, provider, event_at)`` keep the
    highest revision (latest ingestion on ties); an instant with more than one
    provider identity for a kind is ambiguous and fails closed; pair MARK with
    INDEX on the exact ``event_at`` and price asset; only instruments the
    caller certifies as ``MARK_AND_INDEX`` are eligible. DuckDB does the
    ranking and pairing -- exact operations only; the division and its
    half-even quantization to 12 places are Python ``Decimal``.
    """
    if reference_prices.frame_kind != REFERENCE_PRICE_FRAME.kind:
        raise ResearchDataPlaneError("basis_frame_requires_reference_price_frame")
    store.verify(reference_prices)
    cache_key = feature_frame_cache_key_v1(
        feature_name="crypto_mark_index_basis",
        semantic_version=semantic_version,
        calculation_version=calculation_version,
        input_manifests=(reference_prices,),
        parameters={"value_scale": str(_FEATURE_SCALE)},
        implementation_version=BASIS_FRAME_IMPLEMENTATION_VERSION,
    )
    cached = store.find_cached(cache_key)
    if cached is not None:
        return cached, True
    source = store.duckdb_relation_sql(reference_prices)
    connection = duckdb.connect()
    try:
        ambiguous = connection.execute(
            f"SELECT count(*) FROM (SELECT instrument_id, observation_kind, event_at "  # nosec B608
            f"FROM {source} GROUP BY 1,2,3 HAVING count(DISTINCT provider_identifier) > 1)"
        ).fetchone()
        if ambiguous is None or int(ambiguous[0]) != 0:
            raise ResearchDataPlaneError("ambiguous_reference_price_observation_identity")
        cursor = connection.execute(
            "WITH ranked AS (SELECT *, row_number() OVER (PARTITION BY instrument_id, "  # nosec B608
            "observation_kind, provider_identifier, event_at ORDER BY revision DESC, "
            f"ingested_at DESC) AS rnk FROM {source}), latest AS (SELECT * FROM ranked "
            "WHERE rnk = 1) SELECT m.instrument_id, epoch_us(m.event_at), "
            "epoch_us(greatest(m.effective_at, i.effective_at)), m.price, i.price FROM latest m "
            "JOIN latest i ON i.instrument_id = m.instrument_id AND i.event_at = m.event_at "
            "AND i.observation_kind = 'INDEX_PRICE' AND i.price_asset = m.price_asset "
            "WHERE m.observation_kind = 'MARK_PRICE' ORDER BY m.instrument_id, m.event_at"
        )

        def rows() -> Iterator[tuple[object, ...]]:
            eligible: dict[str, bool] = {}
            while True:
                batch = cursor.fetchmany(_FETCH_ROWS)
                if not batch:
                    return
                for instrument_id, event_at, effective_at, mark, index in batch:
                    instrument = str(instrument_id)
                    if instrument not in eligible:
                        eligible[instrument] = eligible_instrument(instrument)
                    if not eligible[instrument]:
                        continue
                    value = ((Decimal(mark) - Decimal(index)) / Decimal(index)).quantize(
                        _FEATURE_SCALE
                    )
                    # Instants cross the DuckDB boundary as integer epoch
                    # microseconds: exact, and free of any time-zone library.
                    yield (
                        instrument, _EPOCH + _MICRO * int(event_at),
                        _EPOCH + _MICRO * int(effective_at), None, value,
                    )

        manifest = store.write_frame(
            FEATURE_FRAME, rows(),
            lineage={
                "feature_name": "crypto_mark_index_basis",
                "semantic_version": semantic_version,
                "calculation_version": calculation_version,
                "input_frames": [
                    {"frame_kind": reference_prices.frame_kind,
                     "manifest_hash": reference_prices.manifest_hash,
                     "logical_content_hash": reference_prices.logical_content_hash}
                ],
                "dataset_version_id": reference_prices.lineage.get("dataset_version_id"),
                "source_granted_evidence_tier": reference_prices.lineage.get(
                    "source_granted_evidence_tier"
                ),
                "market_knowledge_at": "not_derived_null_in_every_row",
                "implementation_version": BASIS_FRAME_IMPLEMENTATION_VERSION,
            },
            cache_key=cache_key,
        )
    finally:
        connection.close()
    return manifest, False


@dataclass(frozen=True, slots=True)
class FeatureParityV1:
    frame_rows: int
    authority_rows: int
    mismatches: int
    first_mismatch: tuple[str, ...] | None

    @property
    def equal(self) -> bool:
        return self.frame_rows == self.authority_rows and self.mismatches == 0


def compare_feature_frame_with_authority_v1(
    database: PostgresDatabase,
    store: ResearchFrameStoreV1,
    feature_frame: FrameManifestV1,
    *,
    feature_id: UUID,
    dataset_version: str,
) -> FeatureParityV1:
    """Row-by-row parity with persisted Feature Authority values (``NUMERIC(38,12)``)."""
    store.verify(feature_frame)
    statement = (
        "SELECT subject_id, event_at, effective_at, value FROM feature_materializations "
        "WHERE feature_id=%s AND dataset_version=%s AND hash_version IN ('V1','V2') "
        'ORDER BY subject_id COLLATE "C", event_at'
    )
    authority = _stream(database, statement, (feature_id, dataset_version))
    frame_rows = 0
    authority_rows = 0
    mismatches = 0
    first: tuple[str, ...] | None = None
    frame_iter = store.iter_rows(feature_frame)
    sentinel = object()
    while True:
        left = next(frame_iter, sentinel)
        right = next(authority, sentinel)
        if left is sentinel and right is sentinel:
            break
        if left is not sentinel:
            frame_rows += 1
        if right is not sentinel:
            authority_rows += 1
        left_key = None if left is sentinel else (
            str(left[0]), left[1], left[2], left[4]  # type: ignore[index]
        )
        right_key = None if right is sentinel else (
            str(right[0]), right[1], right[2],  # type: ignore[index]
            None if right[3] is None else Decimal(str(right[3])).quantize(_FEATURE_SCALE),  # type: ignore[index]
        )
        if left_key != right_key:
            mismatches += 1
            if first is None:
                first = (repr(left_key), repr(right_key))
    return FeatureParityV1(frame_rows, authority_rows, mismatches, first)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class PostgresResearchFrameCatalogV1:
    """``research_frame_manifests``: PostgreSQL as the analytical catalog authority."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, manifest: FrameManifestV1) -> None:
        if not manifest.integrity_verified():
            raise ResearchDataPlaneError("frame_manifest_integrity_failed")
        dataset = manifest.lineage.get("dataset_version_id")
        payload = json.dumps(manifest.payload(), sort_keys=True, default=str)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO research_frame_manifests (manifest_id, manifest_hash, schema_version, "
                "frame_kind, frame_schema_fingerprint, logical_content_hash, row_count, "
                "total_bytes, dataset_version_id, cache_key, manifest, registered_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                "ON CONFLICT (manifest_hash) DO NOTHING",
                (
                    manifest.manifest_id, manifest.manifest_hash, manifest.schema_version,
                    manifest.frame_kind, manifest.frame_schema_fingerprint,
                    manifest.logical_content_hash, manifest.row_count, manifest.total_bytes,
                    None if dataset is None else UUID(str(dataset)), manifest.cache_key,
                    payload, datetime.now(UTC),
                ),
            )
            cursor.execute(
                "SELECT manifest_id, logical_content_hash FROM research_frame_manifests "
                "WHERE manifest_hash=%s",
                (manifest.manifest_hash,),
            )
            row = cursor.fetchone()
        if row is None or UUID(str(row[0])) != manifest.manifest_id or str(row[1]) != manifest.logical_content_hash:
            raise ResearchDataPlaneError("frame_catalog_conflict")

    def manifests_for_dataset(self, dataset_version_id: UUID) -> Sequence[Mapping[str, Any]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT manifest_hash, frame_kind, logical_content_hash, row_count, total_bytes "
                "FROM research_frame_manifests WHERE dataset_version_id=%s "
                "ORDER BY frame_kind, manifest_hash",
                (dataset_version_id,),
            )
            rows = cursor.fetchall()
        return [
            {"manifest_hash": str(r[0]), "frame_kind": str(r[1]), "logical_content_hash": str(r[2]),
             "row_count": int(r[3]), "total_bytes": int(r[4])}
            for r in rows
        ]
