"""Phase R5 UI-1b -- instrument chart series over the R2B research data plane.

``RESEARCH_ONLY``. Read-only; no write, no provider call, no economic
assumption. It answers: *show me this instrument's bars from this exact sealed
frame, and tell me what claim those bars can support.*

Series come only from catalogued frame manifests -- never from a path or a
query the caller supplies:

* ``OHLCV`` frames in ``research_frame_manifests`` (the T1 REST composite),
* ``T2_ARCHIVE_OHLCV_1M`` frames named by ``public_archive_datasets``,
* ``T4_OHLCV_1M`` frames named by ``first_party_t4_datasets``.

Each series carries its source's tier *ceiling* (see
:mod:`trade_platform.evidence_catalog_v1`) as the chart's claim ceiling. A chart
is a picture of values; it is never a tier verdict, a knowledge-time claim or a
signal.

Downsampling is display-only and exact
--------------------------------------
A long series is bucketed into at most ``max_points`` consecutive-bar groups.
Each bucket uses only operations that are exact on ``DECIMAL``: the first
bar's open, the last bar's close, ``MAX(high)``, ``MIN(low)`` and a decimal
``SUM(volume)``. No division or average ever touches a value (DuckDB turns
``DECIMAL / DECIMAL`` into floating point -- the OR-3 evidence), and values
leave as decimal text. Gaps are not filled: a bucket spans only the bars that
exist, and its first/last bar times are reported.

Integrity per read
------------------
The manifest's own hash and every Parquet object's size and SHA-256 are
re-checked before any row is read. The full logical-content re-hash
(:meth:`ResearchFrameStoreV1.verify`) is left to the pipelines that sealed the
frame; a chart does not re-prove 216,000 rows on every page view.

pyarrow/duckdb are the ``analytics`` extra. They are imported lazily so the API
still starts without them; a series read then fails closed as unavailable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from .evidence_catalog_v1 import NO_TIMING_AUTHORITY_V1
from .evidence_tier_authority_v1 import (
    authorized_timing_contracts_v1,
    first_party_bybit_capture_timing_contract_v1,
)

INSTRUMENT_CHART_VERSION_V1: Final = "instrument-chart-v1"
MAX_CHART_POINTS_V1: Final = 1_000
DEFAULT_CHART_POINTS_V1: Final = 400

_MANIFEST_HASH_LENGTH: Final = 64


class InstrumentChartError(ValueError):
    """Raised when a series cannot be served. Always fail closed."""


class ChartSeriesNotFound(InstrumentChartError):
    """The manifest is not a catalogued chartable frame."""


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


FrameKind = Literal["OHLCV", "T2_ARCHIVE_OHLCV_1M", "T4_OHLCV_1M"]


class ChartSeriesRefView(BaseModel):
    manifest_hash: str
    frame_kind: FrameKind
    label: str
    dataset_version_id: UUID
    #: The instrument for single-instrument frames; ``None`` when the frame
    #: carries an ``instrument_id`` column (pick one when reading).
    instrument: str | None
    row_count: int
    tier_ceiling: str


class ChartSeriesRefPage(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    version: str = INSTRUMENT_CHART_VERSION_V1
    items: list[ChartSeriesRefView]


class ChartBucketView(BaseModel):
    first_bar_at: datetime
    last_bar_at: datetime
    bar_count: int
    open: str
    high: str
    low: str
    close: str
    volume: str


class ChartSeriesView(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    version: str = INSTRUMENT_CHART_VERSION_V1
    series: ChartSeriesRefView
    instrument: str
    instruments: list[str]
    bars_in_frame_for_instrument: int
    bars_per_bucket: int
    buckets: list[ChartBucketView]
    limitations: list[str]


# ---------------------------------------------------------------------------
# Catalog (PostgreSQL)
# ---------------------------------------------------------------------------


def _tier_for_source(source_id: UUID | None) -> str:
    for contract in authorized_timing_contracts_v1():
        if contract.source_id == source_id:
            return contract.granted_tier
    return NO_TIMING_AUTHORITY_V1


def list_chart_series_v1(cursor: _Cursor) -> ChartSeriesRefPage:
    """Every catalogued chartable frame. Caller owns the (read-only) transaction."""
    items: list[ChartSeriesRefView] = []
    cursor.execute(
        "SELECT r.manifest_hash, r.dataset_version_id, r.row_count, h.source_id, h.version "
        "FROM research_frame_manifests r "
        "JOIN historical_dataset_versions h ON h.dataset_version_id=r.dataset_version_id "
        "WHERE r.frame_kind='OHLCV' ORDER BY r.registered_at DESC, r.manifest_hash"
    )
    for row in cursor.fetchall():
        items.append(ChartSeriesRefView(
            manifest_hash=str(row[0]).strip(), frame_kind="OHLCV", label=f"{row[4]} (OHLCV)",
            dataset_version_id=row[1], instrument=None, row_count=int(row[2]),
            tier_ceiling=_tier_for_source(row[3]),
        ))
    cursor.execute(
        "SELECT dataset_version_id, symbol, first_utc_day, last_utc_day, "
        "frame_manifests->>'T2_ARCHIVE_OHLCV_1M' "
        "FROM public_archive_datasets ORDER BY registered_at DESC, dataset_version_id"
    )
    for row in cursor.fetchall():
        if row[4] is None:
            continue
        items.append(ChartSeriesRefView(
            manifest_hash=str(row[4]), frame_kind="T2_ARCHIVE_OHLCV_1M",
            label=f"{row[1]} public archive {row[2]}..{row[3]} (1m event-time bars)",
            dataset_version_id=row[0], instrument=str(row[1]), row_count=0,
            # The archive's T2 timing contract is withheld until OR-5.
            tier_ceiling=NO_TIMING_AUTHORITY_V1,
        ))
    cursor.execute(
        "SELECT dataset_version_id, utc_day, window_index, frame_manifests->>'T4_OHLCV_1M', "
        "identity->>'exchange_symbol' "
        "FROM first_party_t4_datasets ORDER BY utc_day DESC, start_arrival_nanos DESC"
    )
    t4_ceiling = first_party_bybit_capture_timing_contract_v1().granted_tier
    for row in cursor.fetchall():
        if row[3] is None:
            continue
        items.append(ChartSeriesRefView(
            manifest_hash=str(row[3]), frame_kind="T4_OHLCV_1M",
            label=f"First-party capture {row[1]} window {row[2]} (1m bars)",
            dataset_version_id=row[0], instrument=None if row[4] is None else str(row[4]),
            row_count=0, tier_ceiling=t4_ceiling,
        ))
    return ChartSeriesRefPage(state="AVAILABLE" if items else "UNAVAILABLE", items=items)


def find_chart_series_v1(cursor: _Cursor, manifest_hash: str) -> ChartSeriesRefView:
    """The catalogued series for exactly ``manifest_hash``; never an uncatalogued file."""
    if len(manifest_hash) != _MANIFEST_HASH_LENGTH or any(c not in "0123456789abcdef" for c in manifest_hash):
        raise ChartSeriesNotFound("chart_series_manifest_hash_malformed")
    for item in list_chart_series_v1(cursor).items:
        if item.manifest_hash == manifest_hash:
            return item
    raise ChartSeriesNotFound("chart_series_not_catalogued")


# ---------------------------------------------------------------------------
# Series (research data plane)
# ---------------------------------------------------------------------------

_COLUMNS: Final[dict[str, dict[str, str]]] = {
    "OHLCV": {"at": "event_at", "volume": "volume"},
    "T2_ARCHIVE_OHLCV_1M": {"at": "bar_open_at", "volume": "base_volume"},
    "T4_OHLCV_1M": {"at": "bar_open_at", "volume": "base_volume"},
}


def _check_objects(store: Any, manifest: Any) -> None:
    if not manifest.integrity_verified():
        raise InstrumentChartError("frame_manifest_integrity_failed")
    for item in manifest.objects:
        path: Path = store.object_path(item)
        if not path.exists() or path.stat().st_size != item.bytes:
            raise InstrumentChartError("frame_object_missing_or_resized")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != item.sha256:
            raise InstrumentChartError("frame_object_corrupt")


def _micros(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000, tz=UTC)


def read_chart_series_v1(
    series: ChartSeriesRefView,
    *,
    research_root: Path,
    instrument: str | None = None,
    max_points: int = DEFAULT_CHART_POINTS_V1,
) -> ChartSeriesView:
    """Exact, bucketed bars of one catalogued frame for one instrument."""
    if not 1 <= max_points <= MAX_CHART_POINTS_V1:
        raise InstrumentChartError("chart_max_points_out_of_range")
    try:
        import duckdb

        from .research_data_plane_v1 import ResearchFrameStoreV1
    except ImportError as error:
        raise InstrumentChartError("analytics_extra_not_installed") from error

    store = ResearchFrameStoreV1(research_root)
    manifest = store.load_manifest(series.manifest_hash)
    if manifest.frame_kind != series.frame_kind:
        raise InstrumentChartError("frame_kind_differs_from_catalog")
    _check_objects(store, manifest)
    relation = store.duckdb_relation_sql(manifest)
    columns = _COLUMNS[series.frame_kind]
    connection = duckdb.connect()
    try:
        if series.frame_kind == "OHLCV":
            instruments = [
                str(row[0]) for row in connection.execute(
                    f"SELECT DISTINCT instrument_id FROM {relation} ORDER BY 1"  # nosec B608 - catalogued paths only
                ).fetchall()
            ]
            chosen = instrument if instrument is not None else (instruments[0] if instruments else None)
            if chosen is None or chosen not in instruments:
                raise ChartSeriesNotFound("chart_instrument_not_in_frame")
            where, params = "WHERE instrument_id = ?", [chosen]
        else:
            chosen = series.instrument or "UNKNOWN"
            if instrument is not None and instrument != chosen:
                raise ChartSeriesNotFound("chart_instrument_not_in_frame")
            instruments = [chosen]
            where, params = "", []
        total = int(connection.execute(
            f"SELECT COUNT(*) FROM {relation} {where}", params  # nosec B608 - catalogued paths only
        ).fetchall()[0][0])
        per_bucket = max(1, -(-total // max_points))
        at, volume = columns["at"], columns["volume"]
        rows = connection.execute(
            # Only exact DECIMAL operations: arg_min/arg_max pick values, MAX/MIN
            # compare, SUM adds. Values leave as text, times as epoch micros.
            f"WITH bars AS (SELECT {at} AS bar_at, open, high, low, close, {volume} AS volume, "  # nosec B608
            f"(ROW_NUMBER() OVER (ORDER BY {at}) - 1) // ? AS bucket FROM {relation} {where}) "
            "SELECT epoch_us(MIN(bar_at)), epoch_us(MAX(bar_at)), COUNT(*), "
            "CAST(arg_min(open, bar_at) AS VARCHAR), CAST(MAX(high) AS VARCHAR), "
            "CAST(MIN(low) AS VARCHAR), CAST(arg_max(close, bar_at) AS VARCHAR), "
            "CAST(SUM(volume) AS VARCHAR) FROM bars GROUP BY bucket ORDER BY bucket",
            [per_bucket, *params],
        ).fetchall()
    finally:
        connection.close()

    buckets = [
        ChartBucketView(
            first_bar_at=_micros(int(row[0])), last_bar_at=_micros(int(row[1])), bar_count=int(row[2]),
            open=str(row[3]), high=str(row[4]), low=str(row[5]), close=str(row[6]), volume=str(row[7]),
        )
        for row in rows
    ]
    return ChartSeriesView(
        state="AVAILABLE" if buckets else "UNAVAILABLE",
        series=series.model_copy(update={"row_count": manifest.row_count}),
        instrument=chosen,
        instruments=instruments,
        bars_in_frame_for_instrument=total,
        bars_per_bucket=per_bucket,
        buckets=buckets,
        limitations=[
            f"Claim ceiling {series.tier_ceiling}: a picture of values, never a tier verdict or a signal.",
            "Buckets use first open, last close, max high, min low and summed volume; nothing is averaged.",
            "Missing bars are not filled; a bucket spans only bars present in the frame.",
            "Manifest and object hashes are re-checked per read; row-level re-hashing is not.",
        ],
    )


def chart_series_payload_hash_v1(view: ChartSeriesView) -> str:
    """SHA-256 of a view's buckets -- lets a reader confirm two reads saw the same bars."""
    payload = [bucket.model_dump(mode="json") for bucket in view.buckets]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = [
    "DEFAULT_CHART_POINTS_V1",
    "INSTRUMENT_CHART_VERSION_V1",
    "MAX_CHART_POINTS_V1",
    "ChartSeriesNotFound",
    "ChartSeriesRefPage",
    "ChartSeriesView",
    "InstrumentChartError",
    "chart_series_payload_hash_v1",
    "find_chart_series_v1",
    "list_chart_series_v1",
    "read_chart_series_v1",
]
