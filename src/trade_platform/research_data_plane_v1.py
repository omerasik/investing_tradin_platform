"""Phase R2B -- the columnar research data plane.

``RESEARCH_ONLY``. No provider call, no economic assumption, no evidence-tier
change. PostgreSQL stays the *authority and catalog* (identities, lineage,
manifests); the high-cardinality analytical payload lives here, in immutable,
content-addressed Parquet files outside the repository.

Stack (chosen by measurement, see ``scripts/research_data_plane.py bench``)
-------------------------------------------------------------------------
* **pyarrow** writes and reads Parquet with an exact, declared Arrow schema
  (``decimal128(38,18)`` for economic values, ``timestamp[us, UTC]`` for every
  clock). Its writer is byte-deterministic for fixed data and settings.
* **duckdb** is the query plane over those files (filters, joins, ranking,
  exact decimal sums), out-of-core by design.
* Economic arithmetic that must match an authority value is done in Python
  ``Decimal`` and written by pyarrow -- never in DuckDB or Polars, because
  both turn ``DECIMAL / DECIMAL`` into binary floating point (DuckDB returns
  ``DOUBLE`` and rounds half away from zero; the platform quantizes
  half-even). That boundary is the R2B evidence behind owner decision OR-3.

Identity
--------
Two hashes, for two different jobs:

``logical_content_hash``
    SHA-256 over a canonical, library-independent text encoding of every row
    in the frame's declared column order and sort order. It is the frame's
    *content identity*: the same source rows give the same hash whatever
    library version or row-group layout wrote them.
``file sha256``
    SHA-256 of each Parquet object's bytes. The object is stored under that
    hash (content addressing) and re-hashed on every verification, so a
    single flipped byte is detected.

The manifest binds both, the schema, the writer settings and the source
lineage; its own SHA-256 is the manifest identity that PostgreSQL catalogs.

Layout (``<root>`` defaults to ``~/.trade_platform/research-data``)::

    <root>/v1/objects/<aa>/<sha256>.parquet
    <root>/v1/manifests/<manifest_hash>.json

Three clocks
------------
Every observation frame carries ``event_at``, ``effective_at``,
``market_knowledge_at`` and the platform clocks ``ingested_at`` /
``platform_recorded_at`` as separate columns. ``market_knowledge_at`` is
written only when a verified derivation supplies it; the T1 composite exports
it as NULL for every row, because T1 has no defensible knowledge time.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

import pyarrow as pa
import pyarrow.parquet as pq

RESEARCH_DATA_PLANE_SCHEMA_VERSION: Final = "research-data-plane-v1"
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.research_data_plane_v1")

#: Rows per Parquet row group. Part of the writer settings (and so of the
#: manifest): a fixed row-group size makes the bytes independent of how the
#: source was fetched.
ROW_GROUP_ROWS: Final = 131_072
_WRITER_SETTINGS: Final[Mapping[str, Any]] = {
    "compression": "zstd",
    "compression_level": 3,
    "row_group_rows": ROW_GROUP_ROWS,
    "use_dictionary": False,
    "write_statistics": True,
    "data_page_version": "2.0",
}

_DECIMAL = pa.decimal128(38, 18)
_TS = pa.timestamp("us", tz="UTC")


class ResearchDataPlaneError(ValueError):
    """Raised on any integrity, schema or lineage failure. Always fail closed."""


def default_research_data_root() -> Path:
    return Path.home() / ".trade_platform" / "research-data"


# ---------------------------------------------------------------------------
# Frame schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrameSchemaV1:
    """One frame kind's exact Arrow schema, sort key and value scale."""

    kind: str
    version: str
    schema: pa.Schema
    sort_key: tuple[str, ...]

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "kind": self.kind,
                    "version": self.version,
                    "fields": [[field.name, str(field.type), field.nullable] for field in self.schema],
                    "sort_key": list(self.sort_key),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


_OBSERVATION_CLOCKS = [
    pa.field("event_at", _TS, nullable=False),
    pa.field("effective_at", _TS, nullable=False),
    pa.field("market_knowledge_at", _TS, nullable=True),
    pa.field("ingested_at", _TS, nullable=False),
    pa.field("platform_recorded_at", _TS, nullable=False),
]
_OBSERVATION_IDENTITY = [
    pa.field("instrument_id", pa.string(), nullable=False),
    pa.field("observation_kind", pa.string(), nullable=False),
    pa.field("provider_identifier", pa.string(), nullable=False),
    pa.field("revision", pa.int32(), nullable=False),
    pa.field("normalized_observation_id", pa.string(), nullable=False),
    pa.field("raw_observation_id", pa.string(), nullable=False),
]
_OBSERVATION_SORT = ("instrument_id", "observation_kind", "event_at", "provider_identifier",
                     "revision", "raw_observation_id")

REFERENCE_PRICE_FRAME: Final = FrameSchemaV1(
    "REFERENCE_PRICE", "1",
    pa.schema([*_OBSERVATION_IDENTITY, *_OBSERVATION_CLOCKS,
               pa.field("price", _DECIMAL, nullable=False),
               pa.field("price_asset", pa.string(), nullable=False)]),
    _OBSERVATION_SORT,
)
OHLCV_FRAME: Final = FrameSchemaV1(
    "OHLCV", "1",
    pa.schema([*_OBSERVATION_IDENTITY, *_OBSERVATION_CLOCKS,
               pa.field("open", _DECIMAL, nullable=False),
               pa.field("high", _DECIMAL, nullable=False),
               pa.field("low", _DECIMAL, nullable=False),
               pa.field("close", _DECIMAL, nullable=False),
               pa.field("volume", _DECIMAL, nullable=False),
               pa.field("interval", pa.string(), nullable=False)]),
    _OBSERVATION_SORT,
)
OPEN_INTEREST_FRAME: Final = FrameSchemaV1(
    "OPEN_INTEREST", "1",
    pa.schema([*_OBSERVATION_IDENTITY, *_OBSERVATION_CLOCKS,
               pa.field("open_interest", _DECIMAL, nullable=False),
               pa.field("unit", pa.string(), nullable=False),
               pa.field("unit_asset", pa.string(), nullable=True)]),
    _OBSERVATION_SORT,
)
#: A derived feature frame. ``value`` is quantized to the Feature Authority's
#: ``NUMERIC(38,12)`` column scale, so it equals the persisted value exactly.
FEATURE_FRAME: Final = FrameSchemaV1(
    "FEATURE", "1",
    pa.schema([pa.field("subject_id", pa.string(), nullable=False),
               pa.field("event_at", _TS, nullable=False),
               pa.field("effective_at", _TS, nullable=False),
               pa.field("market_knowledge_at", _TS, nullable=True),
               pa.field("value", pa.decimal128(38, 12), nullable=True)]),
    ("subject_id", "event_at"),
)

FRAME_SCHEMAS: Final = {
    frame.kind: frame for frame in (REFERENCE_PRICE_FRAME, OHLCV_FRAME, OPEN_INTEREST_FRAME,
                                    FEATURE_FRAME)
}


# ---------------------------------------------------------------------------
# Canonical row encoding (logical identity)
# ---------------------------------------------------------------------------


def _canonical_cell(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ResearchDataPlaneError("naive_timestamp_in_frame")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    raise ResearchDataPlaneError(f"unsupported_cell_type:{type(value).__name__}")


def _sort_value(value: object) -> tuple[int, object]:
    """A totally ordered key: strings by code point (PostgreSQL ``COLLATE "C"``
    for ASCII), instants chronologically, integers numerically."""
    if isinstance(value, datetime):
        return (1, value.astimezone(UTC))
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ResearchDataPlaneError(f"unsortable_key_type:{type(value).__name__}")
    return (0, value) if isinstance(value, str) else (2, value)


def canonical_row_bytes(row: Sequence[object]) -> bytes:
    """One row's library-independent encoding: a JSON array plus a newline."""
    return (json.dumps([_canonical_cell(cell) for cell in row], separators=(",", ":")) + "\n").encode()


def _quantize_exact(value: Decimal, scale: int, name: str) -> Decimal:
    """Quantize to the column scale, refusing any change of value (never round)."""
    if not value.is_finite():
        raise ResearchDataPlaneError(f"non_finite_{name}")
    quantized = value.quantize(Decimal(1).scaleb(-scale))
    if quantized != value:
        raise ResearchDataPlaneError(f"{name}_exceeds_frame_scale")
    return quantized


def _prepare_row(frame: FrameSchemaV1, row: Sequence[object]) -> tuple[object, ...]:
    if len(row) != len(frame.schema):
        raise ResearchDataPlaneError("row_width_differs_from_frame_schema")
    prepared: list[object] = []
    for field, cell in zip(frame.schema, row, strict=True):
        if cell is None:
            if not field.nullable:
                raise ResearchDataPlaneError(f"null_in_non_nullable_column:{field.name}")
            prepared.append(None)
        elif pa.types.is_decimal(field.type):
            prepared.append(
                _quantize_exact(Decimal(str(cell)), field.type.scale, field.name)
            )
        else:
            prepared.append(cell)
    return tuple(prepared)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrameObjectV1:
    sha256: str
    bytes: int
    rows: int

    def relative_path(self) -> str:
        return f"objects/{self.sha256[:2]}/{self.sha256}.parquet"


@dataclass(frozen=True, slots=True)
class FrameManifestV1:
    schema_version: str
    frame_kind: str
    frame_schema_version: str
    frame_schema_fingerprint: str
    row_count: int
    logical_content_hash: str
    objects: tuple[FrameObjectV1, ...]
    writer: Mapping[str, Any]
    lineage: Mapping[str, Any]
    cache_key: str | None
    manifest_hash: str

    @property
    def manifest_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"frame-manifest:{self.manifest_hash}")

    @property
    def total_bytes(self) -> int:
        return sum(item.bytes for item in self.objects)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "frame_kind": self.frame_kind,
            "frame_schema_version": self.frame_schema_version,
            "frame_schema_fingerprint": self.frame_schema_fingerprint,
            "row_count": self.row_count,
            "logical_content_hash": self.logical_content_hash,
            "objects": [
                {"sha256": item.sha256, "bytes": item.bytes, "rows": item.rows}
                for item in self.objects
            ],
            "writer": dict(self.writer),
            "lineage": dict(self.lineage),
            "cache_key": self.cache_key,
        }

    @staticmethod
    def hash_payload(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def integrity_verified(self) -> bool:
        return self.manifest_hash == self.hash_payload(self.payload())

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> FrameManifestV1:
        manifest_hash = cls.hash_payload(payload)
        try:
            return cls(
                schema_version=str(payload["schema_version"]),
                frame_kind=str(payload["frame_kind"]),
                frame_schema_version=str(payload["frame_schema_version"]),
                frame_schema_fingerprint=str(payload["frame_schema_fingerprint"]),
                row_count=int(payload["row_count"]),
                logical_content_hash=str(payload["logical_content_hash"]),
                objects=tuple(
                    FrameObjectV1(str(item["sha256"]), int(item["bytes"]), int(item["rows"]))
                    for item in payload["objects"]
                ),
                writer=dict(payload["writer"]),
                lineage=dict(payload["lineage"]),
                cache_key=None if payload["cache_key"] is None else str(payload["cache_key"]),
                manifest_hash=manifest_hash,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ResearchDataPlaneError("frame_manifest_malformed") from error


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class ResearchFrameStoreV1:
    """Content-addressed Parquet objects plus JSON manifests under one root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_research_data_root()) / "v1"

    def object_path(self, item: FrameObjectV1) -> Path:
        return self.root / item.relative_path()

    def manifest_path(self, manifest_hash: str) -> Path:
        return self.root / "manifests" / f"{manifest_hash}.json"

    # -- writing -------------------------------------------------------------

    def write_frame(
        self,
        frame: FrameSchemaV1,
        rows: Iterable[Sequence[object]],
        *,
        lineage: Mapping[str, Any],
        cache_key: str | None = None,
        rows_per_object: int | None = None,
    ) -> FrameManifestV1:
        """Stream ``rows`` (already in ``frame.sort_key`` order) into Parquet objects.

        Memory is bounded by one row group. Rows are validated against the
        frame schema (nullability, exact decimal scale) and their sort order is
        checked, so an unsorted source fails closed rather than producing a
        frame whose identity depends on fetch order.
        """
        tmp_dir = self.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        key_index = [frame.schema.get_field_index(name) for name in frame.sort_key]
        digest = hashlib.sha256()
        objects: list[FrameObjectV1] = []
        pending: list[tuple[object, ...]] = []
        count = 0
        previous_key: tuple[tuple[int, Any], ...] | None = None
        writer: pq.ParquetWriter | None = None
        tmp_path: Path | None = None
        object_rows = 0

        def open_writer() -> None:
            nonlocal writer, tmp_path, object_rows
            handle, name = tempfile.mkstemp(suffix=".parquet", dir=tmp_dir)
            os.close(handle)
            tmp_path = Path(name)
            writer = pq.ParquetWriter(
                str(tmp_path), frame.schema,
                compression=_WRITER_SETTINGS["compression"],
                compression_level=_WRITER_SETTINGS["compression_level"],
                use_dictionary=_WRITER_SETTINGS["use_dictionary"],
                write_statistics=_WRITER_SETTINGS["write_statistics"],
                data_page_version=_WRITER_SETTINGS["data_page_version"],
            )
            object_rows = 0

        def close_writer() -> None:
            nonlocal writer, tmp_path
            if writer is None or tmp_path is None:
                return
            writer.close()
            sha = _file_sha256(tmp_path)
            item = FrameObjectV1(sha, tmp_path.stat().st_size, object_rows)
            final = self.object_path(item)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                if _file_sha256(final) != sha:
                    raise ResearchDataPlaneError("content_addressed_object_corrupt")
                tmp_path.unlink()
            else:
                os.replace(tmp_path, final)
            objects.append(item)
            writer = None
            tmp_path = None

        def flush() -> None:
            nonlocal object_rows
            if not pending:
                return
            if writer is None:
                open_writer()
            columns = list(zip(*pending, strict=True))
            table = pa.Table.from_arrays(
                [pa.array(list(column), type=field.type) for column, field in
                 zip(columns, frame.schema, strict=True)],
                schema=frame.schema,
            )
            if writer is None:
                raise ResearchDataPlaneError("parquet_writer_not_open")
            writer.write_table(table, row_group_size=ROW_GROUP_ROWS)
            object_rows += len(pending)
            pending.clear()
            if rows_per_object is not None and object_rows >= rows_per_object:
                close_writer()

        try:
            for raw in rows:
                row = _prepare_row(frame, raw)
                key = tuple(_sort_value(row[index]) for index in key_index)
                if previous_key is not None and key < previous_key:
                    raise ResearchDataPlaneError("frame_rows_not_in_sort_key_order")
                previous_key = key
                digest.update(canonical_row_bytes(row))
                pending.append(row)
                count += 1
                if len(pending) == ROW_GROUP_ROWS or (
                    rows_per_object is not None
                    and object_rows + len(pending) >= rows_per_object
                ):
                    flush()
            flush()
            close_writer()
        finally:
            if writer is not None:
                writer.close()
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

        payload = {
            "schema_version": RESEARCH_DATA_PLANE_SCHEMA_VERSION,
            "frame_kind": frame.kind,
            "frame_schema_version": frame.version,
            "frame_schema_fingerprint": frame.fingerprint(),
            "row_count": count,
            "logical_content_hash": digest.hexdigest(),
            "objects": [{"sha256": o.sha256, "bytes": o.bytes, "rows": o.rows} for o in objects],
            "writer": {**_WRITER_SETTINGS, "pyarrow": pa.__version__,
                       "rows_per_object": rows_per_object},
            "lineage": dict(lineage),
            "cache_key": cache_key,
        }
        manifest = FrameManifestV1.from_payload(payload)
        path = self.manifest_path(manifest.manifest_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, sort_keys=True, indent=1, default=str)
        if path.exists():
            if FrameManifestV1.from_payload(json.loads(path.read_text())).manifest_hash != manifest.manifest_hash:
                raise ResearchDataPlaneError("content_addressed_manifest_corrupt")
        else:
            path.write_text(text, encoding="utf-8")
        return manifest

    # -- reading and verification --------------------------------------------

    def load_manifest(self, manifest_hash: str) -> FrameManifestV1:
        path = self.manifest_path(manifest_hash)
        if not path.exists():
            raise ResearchDataPlaneError("frame_manifest_not_found")
        manifest = FrameManifestV1.from_payload(json.loads(path.read_text(encoding="utf-8")))
        if manifest.manifest_hash != manifest_hash:
            raise ResearchDataPlaneError("frame_manifest_hash_mismatch")
        return manifest

    def find_cached(self, cache_key: str) -> FrameManifestV1 | None:
        """A verified manifest built for ``cache_key``, or ``None`` (cache miss)."""
        directory = self.root / "manifests"
        if not directory.exists():
            return None
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("cache_key") == cache_key:
                manifest = self.load_manifest(path.stem)
                self.verify(manifest)
                return manifest
        return None

    def iter_rows(self, manifest: FrameManifestV1) -> Iterator[tuple[object, ...]]:
        frame = FRAME_SCHEMAS.get(manifest.frame_kind)
        if frame is None or frame.fingerprint() != manifest.frame_schema_fingerprint:
            raise ResearchDataPlaneError("frame_schema_not_recognised")
        for item in manifest.objects:
            parquet = pq.ParquetFile(str(self.object_path(item)))
            if not parquet.schema_arrow.equals(frame.schema):
                raise ResearchDataPlaneError("parquet_schema_differs_from_frame_schema")
            for batch in parquet.iter_batches(batch_size=ROW_GROUP_ROWS):
                columns = [column.to_pylist() for column in batch.columns]
                yield from zip(*columns, strict=True)

    def verify(self, manifest: FrameManifestV1) -> None:
        """Re-hash every object and every row. Raises on any divergence."""
        if not manifest.integrity_verified():
            raise ResearchDataPlaneError("frame_manifest_integrity_failed")
        for item in manifest.objects:
            path = self.object_path(item)
            if not path.exists():
                raise ResearchDataPlaneError("frame_object_missing")
            if path.stat().st_size != item.bytes or _file_sha256(path) != item.sha256:
                raise ResearchDataPlaneError("frame_object_corrupt")
        digest = hashlib.sha256()
        count = 0
        for row in self.iter_rows(manifest):
            digest.update(canonical_row_bytes(row))
            count += 1
        if count != manifest.row_count or digest.hexdigest() != manifest.logical_content_hash:
            raise ResearchDataPlaneError("frame_logical_content_mismatch")

    def duckdb_relation_sql(self, manifest: FrameManifestV1) -> str:
        """A DuckDB ``read_parquet`` expression over exactly this frame's objects."""
        paths = ", ".join(
            "'" + self.object_path(item).as_posix().replace("'", "''") + "'"
            for item in manifest.objects
        )
        return f"read_parquet([{paths}])"


def feature_frame_cache_key_v1(
    *,
    feature_name: str,
    semantic_version: str,
    calculation_version: str,
    input_manifests: Sequence[FrameManifestV1],
    parameters: Mapping[str, Any],
    implementation_version: str,
) -> str:
    """Deterministic identity of a feature frame *before* it is computed.

    Any change to the feature definition, its inputs' content, its parameters
    or the implementation that computes it gives a new key -- so a cache hit
    is always the same computation over the same content.
    """
    return hashlib.sha256(
        json.dumps(
            {
                "schema_version": RESEARCH_DATA_PLANE_SCHEMA_VERSION,
                "feature_name": feature_name,
                "semantic_version": semantic_version,
                "calculation_version": calculation_version,
                "inputs": sorted(
                    (item.frame_kind, item.logical_content_hash) for item in input_manifests
                ),
                "parameters": dict(parameters),
                "implementation_version": implementation_version,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


ProgressCallback = Callable[[int], None]
