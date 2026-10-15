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

Timeframe candles (Stage B.1.1)
-------------------------------
:func:`read_chart_candles_v1` aggregates a frame with a *declared* 1-minute
bar interval (``T2_ARCHIVE_OHLCV_1M``, ``T4_OHLCV_1M``) into ``1m``/``1h``/
``4h``/``1d`` candles. A candle's bucket is ``floor(bar_open_at / timeframe)``
in UTC epoch time, so ``1h``/``4h``/``1d`` open at UTC hour/4-hour/midnight
boundaries and a candle never depends on where a page or window starts. The
same exact operations as above build it. A candle is ``complete`` only when
every one of its expected source minutes has a bar; otherwise it is served as
*partial* with ``bar_count`` of ``expected_bar_count`` -- never completed,
re-scaled or filled. Buckets with no source bar at all are absent and reported
as gaps. Source bars off the declared grid or with a duplicated open time make
the read fail closed. ``OHLCV`` frames declare no bar interval, so they have
no timeframes.

T2 research windows and the holdout
-----------------------------------
:func:`list_research_window_series_v1` adds the content-addressed T2 research
windows (:mod:`trade_platform.public_archive_research_bars_v1`) to the catalog
from their identity files; an identity whose hash does not re-derive is left
out. Any T2 bar at or after ``UNTOUCHED_HOLDOUT_BOUNDARY_V1`` is excluded from
every T2 read, and a window reaching the boundary is not catalogued: the
untouched holdout stays unopened here. T4 first-party capture is forward-only
and unaffected.

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
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from .evidence_catalog_v1 import NO_TIMING_AUTHORITY_V1
from .evidence_tier_authority_v1 import authorized_timing_contracts_v1
from .first_party_capture_authority_v1 import (
    capture_purpose_v1,
    resolve_t4_registered_first_party_capture_contract_v1,
)
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

INSTRUMENT_CHART_VERSION_V1: Final = "instrument-chart-v1"
MAX_CHART_POINTS_V1: Final = 1_000
DEFAULT_CHART_POINTS_V1: Final = 400

#: Display timeframes in seconds. Buckets align to UTC epoch multiples.
CHART_TIMEFRAMES_V1: Final[dict[str, int]] = {"1m": 60, "1h": 3_600, "4h": 14_400, "1d": 86_400}
DEFAULT_CHART_CANDLES_V1: Final = 2_000
MAX_CHART_CANDLES_V1: Final = 5_000

#: Frame kinds whose schema declares the source bar interval (seconds).
_SOURCE_BAR_SECONDS: Final[dict[str, int]] = {"T2_ARCHIVE_OHLCV_1M": 60, "T4_OHLCV_1M": 60}
#: Frame kinds that carry research bars and so respect the untouched holdout.
_HOLDOUT_FRAME_KINDS: Final = frozenset({"T2_ARCHIVE_OHLCV_1M"})
_MICROS: Final = 1_000_000

_MANIFEST_HASH_LENGTH: Final = 64


class InstrumentChartError(ValueError):
    """Raised when a series cannot be served. Always fail closed."""


class ChartSeriesNotFound(InstrumentChartError):
    """The manifest is not a catalogued chartable frame."""


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


FrameKind = Literal["OHLCV", "T2_ARCHIVE_OHLCV_1M", "T4_OHLCV_1M"]


class ChartTimeframeUnavailable(InstrumentChartError):
    """The frame declares no bar interval, or the timeframe is not offered."""


class ResearchWindowRefView(BaseModel):
    """A T2 research window's identity summary (its content hash re-derived on listing)."""

    content_hash: str
    source_id: str
    first_utc_day: str
    last_utc_day: str
    files: int
    not_published_days: list[str]
    rejected_days: list[str]
    day_gaps: list[dict[str, str]]
    bar_logical_content_hash: str
    last_bar_close_at: datetime


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
    #: Timeframes :func:`read_chart_candles_v1` serves; empty without a declared bar interval.
    timeframes: list[str] = []
    #: Set only for a catalogued T2 research window.
    window: ResearchWindowRefView | None = None


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
    holdout_excluded_bars: int = 0


class ChartCandleView(BaseModel):
    open_at: datetime
    close_at: datetime
    first_bar_at: datetime
    last_bar_at: datetime
    bar_count: int
    expected_bar_count: int
    complete: bool
    open: str
    high: str
    low: str
    close: str
    volume: str


class ChartGapView(BaseModel):
    """Candles with no source bar at all between two served candles."""

    after_open_at: datetime
    before_open_at: datetime
    missing_candles: int


class ChartCoverageView(BaseModel):
    """The whole frame (after the holdout cut), not only the served page."""

    first_bar_at: datetime | None
    last_bar_close_at: datetime | None
    source_bars: int
    source_bar_seconds: int
    expected_source_bars: int
    missing_source_bars: int
    holdout_boundary: datetime | None
    holdout_excluded_bars: int


class ChartCandlesView(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    version: str = INSTRUMENT_CHART_VERSION_V1
    series: ChartSeriesRefView
    timeframe: str
    coverage: ChartCoverageView
    candles: list[ChartCandleView]
    partial_candles: int
    gaps: list[ChartGapView]
    #: Candles open strictly before ``end`` (exclusive); ``None`` means the latest.
    end: datetime | None
    #: Pass as ``end`` to page back; ``None`` when nothing earlier exists.
    earlier_end: datetime | None
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
            tier_ceiling=NO_TIMING_AUTHORITY_V1, timeframes=list(CHART_TIMEFRAMES_V1),
        ))
    cursor.execute(
        "SELECT dataset_version_id, utc_day, window_index, frame_manifests->>'T4_OHLCV_1M', "
        "identity->>'exchange_symbol', source_id "
        "FROM first_party_t4_datasets ORDER BY utc_day DESC, start_arrival_nanos DESC"
    )
    # The ceiling is the row's own source's timing contract; a source outside the
    # closed set shows no timing authority rather than borrowing production's.
    ceilings = {contract.source_id: contract.granted_tier for contract in authorized_timing_contracts_v1()}
    for row in cursor.fetchall():
        if row[3] is None:
            continue
        capture = resolve_t4_registered_first_party_capture_contract_v1(row[5])
        source = "unregistered source" if capture is None else (
            f"{capture.exchange_symbol} {capture_purpose_v1(capture).value.lower()}"
        )
        items.append(ChartSeriesRefView(
            manifest_hash=str(row[3]), frame_kind="T4_OHLCV_1M",
            label=f"First-party capture ({source}) {row[1]} window {row[2]} (1m bars)",
            dataset_version_id=row[0], instrument=None if row[4] is None else str(row[4]),
            row_count=0,
            tier_ceiling=(
                NO_TIMING_AUTHORITY_V1 if capture is None
                else ceilings.get(capture.source_id, NO_TIMING_AUTHORITY_V1)
            ),
            timeframes=list(CHART_TIMEFRAMES_V1),
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


def _research_window_ref(payload: dict[str, Any]) -> ChartSeriesRefView | None:
    """One window identity file as a series, or ``None`` when it does not prove itself."""
    try:
        identity = payload["identity"]
        content_hash = identity_hash_v1(identity)
        manifest_hash = str(payload["bar_frame_manifest_hash"])
        if content_hash != payload.get("content_hash") or len(manifest_hash) != _MANIFEST_HASH_LENGTH:
            return None
        last_day = datetime.fromisoformat(f"{identity['last_utc_day']}T00:00:00+00:00")
        if last_day >= UNTOUCHED_HOLDOUT_BOUNDARY_V1:
            return None  # a window inside the untouched holdout is never charted
        symbol = str(identity["symbol"])
        first, last = str(identity["first_utc_day"]), str(identity["last_utc_day"])
        return ChartSeriesRefView(
            manifest_hash=manifest_hash, frame_kind="T2_ARCHIVE_OHLCV_1M",
            label=f"{symbol} T2 research window {first}..{last} (1m event-time bars)",
            dataset_version_id=UUID(str(payload["dataset_version_id"])), instrument=symbol,
            row_count=int(identity["bar_frame"]["row_count"]),
            # The archive's T2 timing contract is withheld until OR-5.
            tier_ceiling=NO_TIMING_AUTHORITY_V1, timeframes=list(CHART_TIMEFRAMES_V1),
            window=ResearchWindowRefView(
                content_hash=content_hash, source_id=str(identity["source_id"]),
                first_utc_day=first, last_utc_day=last, files=len(identity["files"]),
                not_published_days=[str(day) for day in identity["not_published_days"]],
                rejected_days=[str(item["utc_day"]) for item in identity.get("rejected_days", [])],
                day_gaps=[{"after": str(gap["after"]), "before": str(gap["before"])} for gap in identity["day_gaps"]],
                bar_logical_content_hash=str(identity["bar_frame"]["logical_content_hash"]),
                last_bar_close_at=datetime.fromisoformat(str(identity["last_bar_close_at"])),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def list_research_window_series_v1(research_root: Path | None) -> list[ChartSeriesRefView]:
    """Catalogued T2 research windows, by symbol, latest and longest first.

    Reads identity files only (``ResearchFrameStoreV1`` layout), so the API needs
    no analytics import to list them. A file that does not re-derive its content
    hash, or a window reaching the untouched holdout, is left out.
    """
    if research_root is None:
        return []
    directory = Path(research_root) / "v1" / "datasets" / "public-archive-bars"
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        item = _research_window_ref(payload) if isinstance(payload, dict) else None
        if item is not None and item.window is not None:
            items.append(item)
    items.sort(key=lambda item: (
        item.instrument or "", _desc(item.window.last_utc_day if item.window else ""),
        item.window.first_utc_day if item.window else "", str(item.dataset_version_id),
    ))
    return items


def _desc(text: str) -> tuple[int, ...]:
    return tuple(-ord(char) for char in text)


def find_research_window_series_v1(research_root: Path | None, manifest_hash: str) -> ChartSeriesRefView | None:
    for item in list_research_window_series_v1(research_root):
        if item.manifest_hash == manifest_hash:
            return item
    return None


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
    except ImportError as error:
        raise InstrumentChartError("analytics_extra_not_installed") from error

    manifest, relation = _load_checked_frame(series, research_root)
    columns = _COLUMNS[series.frame_kind]
    connection = duckdb.connect()
    params: list[object]
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
        at, volume = columns["at"], columns["volume"]
        excluded = 0
        if series.frame_kind in _HOLDOUT_FRAME_KINDS:
            cut = _epoch_us(UNTOUCHED_HOLDOUT_BOUNDARY_V1)
            excluded = int(connection.execute(
                f"SELECT COUNT(*) FROM {relation} WHERE epoch_us({at}) >= ?", [cut]  # nosec B608
            ).fetchall()[0][0])
            where, params = f"WHERE epoch_us({at}) < ?", [cut]
        total = int(connection.execute(
            f"SELECT COUNT(*) FROM {relation} {where}", params  # nosec B608 - catalogued paths only
        ).fetchall()[0][0])
        per_bucket = max(1, -(-total // max_points))
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
            *_holdout_limitations(series, excluded),
        ],
        holdout_excluded_bars=excluded,
    )


def _epoch_us(instant: datetime) -> int:
    delta = instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * _MICROS + delta.microseconds


def _holdout_limitations(series: ChartSeriesRefView, excluded: int) -> list[str]:
    if series.frame_kind not in _HOLDOUT_FRAME_KINDS:
        return []
    return [
        (f"T2 research bars at or after {UNTOUCHED_HOLDOUT_BOUNDARY_V1.date().isoformat()} are the untouched "
         f"holdout and are never charted ({excluded} bars of this frame excluded)."),
    ]


def _load_checked_frame(series: ChartSeriesRefView, research_root: Path) -> tuple[Any, str]:
    """The catalogued frame's manifest and DuckDB relation, after the per-read checks."""
    try:
        from .research_data_plane_v1 import ResearchFrameStoreV1
    except ImportError as error:
        raise InstrumentChartError("analytics_extra_not_installed") from error
    store = ResearchFrameStoreV1(research_root)
    manifest = store.load_manifest(series.manifest_hash)
    if manifest.frame_kind != series.frame_kind:
        raise InstrumentChartError("frame_kind_differs_from_catalog")
    if series.window is not None and (
        manifest.logical_content_hash != series.window.bar_logical_content_hash
        or manifest.row_count != series.row_count
    ):
        raise InstrumentChartError("research_window_frame_differs_from_its_identity")
    _check_objects(store, manifest)
    return manifest, store.duckdb_relation_sql(manifest)


def read_chart_candles_v1(
    series: ChartSeriesRefView,
    *,
    research_root: Path,
    timeframe: str,
    end: datetime | None = None,
    limit: int = DEFAULT_CHART_CANDLES_V1,
) -> ChartCandlesView:
    """Exact UTC-aligned candles of one catalogued 1-minute frame; partial candles flagged."""
    if timeframe not in CHART_TIMEFRAMES_V1 or timeframe not in series.timeframes:
        raise ChartTimeframeUnavailable("chart_timeframe_not_offered_for_this_frame")
    source_seconds = _SOURCE_BAR_SECONDS.get(series.frame_kind)
    if source_seconds is None:
        raise ChartTimeframeUnavailable("chart_frame_declares_no_bar_interval")
    if not 1 <= limit <= MAX_CHART_CANDLES_V1:
        raise InstrumentChartError("chart_candle_limit_out_of_range")
    if end is not None and end.tzinfo is None:
        raise InstrumentChartError("chart_end_must_be_timezone_aware")
    try:
        import duckdb
    except ImportError as error:
        raise InstrumentChartError("analytics_extra_not_installed") from error
    _, relation = _load_checked_frame(series, research_root)
    at, volume = _COLUMNS[series.frame_kind]["at"], _COLUMNS[series.frame_kind]["volume"]
    source_us = source_seconds * _MICROS
    bucket_us = CHART_TIMEFRAMES_V1[timeframe] * _MICROS
    expected = CHART_TIMEFRAMES_V1[timeframe] // source_seconds
    holdout = UNTOUCHED_HOLDOUT_BOUNDARY_V1 if series.frame_kind in _HOLDOUT_FRAME_KINDS else None
    cut = _epoch_us(holdout) if holdout is not None else None
    where, params = (f"WHERE epoch_us({at}) < ?", [cut]) if cut is not None else ("", [])
    connection = duckdb.connect()
    try:
        excluded = 0 if cut is None else int(connection.execute(
            f"SELECT COUNT(*) FROM {relation} WHERE epoch_us({at}) >= ?", [cut]  # nosec B608 - catalogued paths only
        ).fetchall()[0][0])
        count, distinct, first_us, last_us, off_grid = connection.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT epoch_us({at})), MIN(epoch_us({at})), MAX(epoch_us({at})), "  # nosec B608
            f"COUNT(*) FILTER (WHERE epoch_us({at}) % ? <> 0) FROM {relation} {where}",
            [source_us, *params],
        ).fetchall()[0]
        if int(off_grid):
            raise InstrumentChartError("source_bars_off_the_declared_interval_grid")
        if int(count) != int(distinct):
            raise InstrumentChartError("source_bars_share_an_open_time")
        end_clause, end_params = "", []
        if end is not None:
            # Candles that open strictly before ``end``; the bucket holding ``end`` is never cut.
            end_clause, end_params = "HAVING MIN(k) * ? < ?", [bucket_us, _epoch_us(end)]
        rows = connection.execute(
            # Only exact DECIMAL operations, as in read_chart_series_v1.
            f"WITH bars AS (SELECT epoch_us({at}) AS t, open, high, low, close, {volume} AS volume "  # nosec B608
            f"FROM {relation} {where}), keyed AS (SELECT t // ? AS k, * FROM bars) "
            "SELECT k, COUNT(*), MIN(t), MAX(t), CAST(arg_min(open, t) AS VARCHAR), CAST(MAX(high) AS VARCHAR), "
            "CAST(MIN(low) AS VARCHAR), CAST(arg_max(close, t) AS VARCHAR), CAST(SUM(volume) AS VARCHAR) "
            f"FROM keyed GROUP BY k {end_clause} ORDER BY k DESC LIMIT ?",
            [*params, bucket_us, *end_params, limit],
        ).fetchall()
    finally:
        connection.close()

    candles: list[ChartCandleView] = []
    for row in reversed(rows):
        bar_count = int(row[1])
        if bar_count > expected:
            raise InstrumentChartError("candle_has_more_source_bars_than_minutes")
        open_us = int(row[0]) * bucket_us
        candles.append(ChartCandleView(
            open_at=_micros(open_us), close_at=_micros(open_us + bucket_us),
            first_bar_at=_micros(int(row[2])), last_bar_at=_micros(int(row[3])),
            bar_count=bar_count, expected_bar_count=expected, complete=bar_count == expected,
            open=str(row[4]), high=str(row[5]), low=str(row[6]), close=str(row[7]), volume=str(row[8]),
        ))
    gaps = [
        ChartGapView(after_open_at=a.open_at, before_open_at=b.open_at,
                     missing_candles=(_epoch_us(b.open_at) - _epoch_us(a.open_at)) // bucket_us - 1)
        for a, b in pairwise(candles)
        if _epoch_us(b.open_at) - _epoch_us(a.open_at) > bucket_us
    ]
    total = int(count)
    expected_source = 0 if not total else (int(last_us) - int(first_us)) // source_us + 1
    coverage = ChartCoverageView(
        first_bar_at=_micros(int(first_us)) if total else None,
        last_bar_close_at=_micros(int(last_us) + source_us) if total else None,
        source_bars=total, source_bar_seconds=source_seconds,
        expected_source_bars=expected_source, missing_source_bars=expected_source - total,
        holdout_boundary=holdout, holdout_excluded_bars=excluded,
    )
    earlier = bool(candles) and total > 0 and int(first_us) < _epoch_us(candles[0].open_at)
    return ChartCandlesView(
        state="AVAILABLE" if candles else "UNAVAILABLE",
        series=series, timeframe=timeframe, coverage=coverage, candles=candles,
        partial_candles=sum(1 for candle in candles if not candle.complete), gaps=gaps,
        end=end, earlier_end=candles[0].open_at if earlier else None,
        limitations=[
            f"Claim ceiling {series.tier_ceiling}: historical bars, never a live price, a tier verdict or a signal.",
            (f"{timeframe} candles open on UTC {timeframe} boundaries and use first open, last close, max high, "
             "min low and summed volume of the 1-minute source bars; nothing is averaged."),
            f"A candle with fewer than {expected} source bars is PARTIAL and shown as such, never completed.",
            "Periods with no source bar are gaps: never filled, interpolated or carried forward.",
            "Manifest and object hashes are re-checked per read; row-level re-hashing is not.",
            *_holdout_limitations(series, excluded),
        ],
    )


def chart_series_payload_hash_v1(view: ChartSeriesView) -> str:
    """SHA-256 of a view's buckets -- lets a reader confirm two reads saw the same bars."""
    payload = [bucket.model_dump(mode="json") for bucket in view.buckets]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = [
    "CHART_TIMEFRAMES_V1",
    "DEFAULT_CHART_CANDLES_V1",
    "DEFAULT_CHART_POINTS_V1",
    "INSTRUMENT_CHART_VERSION_V1",
    "MAX_CHART_CANDLES_V1",
    "MAX_CHART_POINTS_V1",
    "ChartCandlesView",
    "ChartSeriesNotFound",
    "ChartSeriesRefPage",
    "ChartSeriesView",
    "ChartTimeframeUnavailable",
    "InstrumentChartError",
    "chart_series_payload_hash_v1",
    "find_chart_series_v1",
    "find_research_window_series_v1",
    "list_chart_series_v1",
    "list_research_window_series_v1",
    "read_chart_candles_v1",
    "read_chart_series_v1",
]
