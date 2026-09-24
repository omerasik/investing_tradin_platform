"""Phase R3B -- Bybit's free public trade archive: source contract, acquisition, reconstruction.

``RESEARCH_ONLY``. Public market data only: ``https://public.bybit.com/trading/``
is served unauthenticated by Bybit (Amazon S3 behind CloudFront). No credential,
no account, no paid tier, no order path.

What the archive is (verified 2026-09-24)
-----------------------------------------
One gzip CSV per symbol per UTC day, ``<SYMBOL><YYYY-MM-DD>.csv.gz``
(BTCUSDT: 2,374 daily files, 2020-03-25 .. the previous UTC day; the day's
file appeared ~01:14Z the next morning). Header::

    timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,
    homeNotional,foreignNotional,RPI

``timestamp`` is the venue trade time in epoch seconds, printed as float text
with at most 14 significant digits -- up to four decimals, trailing zeros
dropped (2026-09-21 BTCUSDT: 3.30M of 3.66M rows carry four decimals). Its
resolution is therefore 100 microseconds and whether the publisher rounded or
truncated is undocumented, so the verbatim text is kept, the exact decimal
value is stored in integer microseconds, and a bar counts the trades whose
printed time lies within one resolution step of a minute boundary as
boundary-ambiguous. There is **no** venue sequence number: within one printed
instant the only order evidence is the row position the publisher wrote. There is no
publisher checksum (the S3 ETag is a multipart tag, not a content hash), so
integrity is what this module measures: the declared ``Content-Length``, a full
gzip decompression (CRC), our own SHA-256 of the exact bytes, and a strict
schema parse. The ``premium_index``/``spot_index`` directories hold only
inverse ``*USD`` symbols ending in 2020 and are out of scope.

What it can claim: ``T2_EVENT_TIME`` at most, and not yet
----------------------------------------------------------
The rows carry venue event times and nothing about when a subscriber could
first have known them. The evidence-tier authority grants ``T2`` only to a
*registered* timing contract with a *declared, bound* publication lag. The lag
is owner decision OR-5 and is **unset** here (:data:`T2_PUBLICATION_LAG_SLOT_V1`);
this contract is deliberately *not* registered with
:func:`~trade_platform.evidence_tier_authority_v1.authorized_timing_contracts_v1`,
so every evaluation today fails closed to at most ``T1_RETROSPECTIVE``, and
every frame written here has ``market_knowledge_at`` NULL. Nothing silently uses
zero lag. The HTTP ``Last-Modified`` of a file is recorded as audit only: it is
a mutable server header about the *file*, not a per-trade knowledge time.

Provider-published versus calculated
------------------------------------
``size``, ``price``, ``side``, ``tickDirection``, ``trdMatchID``, ``RPI`` and
``foreignNotional`` are kept as published (``grossValue`` is printed as a
binary float in scientific notation and is not carried). ``homeNotional`` must
equal ``size`` exactly for a linear contract, which is checked, not assumed.
Bars are the platform's own reconstruction and say so.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .research_data_plane_v1 import (
    T2_ARCHIVE_OHLCV_1M_FRAME,
    T2_ARCHIVE_TRADE_FRAME,
    FrameManifestV1,
    ResearchFrameStoreV1,
)

ARCHIVE_BASE_URL_V1: Final = "https://public.bybit.com/trading/"
ARCHIVE_SCHEMA_V1: Final = (
    "timestamp", "symbol", "side", "size", "price", "tickDirection", "trdMatchID",
    "grossValue", "homeNotional", "foreignNotional", "RPI",
)
ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1: Final = "bybit-public-trade-archive-normalization-1.0.0"
ARCHIVE_BAR_SEMANTIC_VERSION_V1: Final = "bybit-public-trade-archive-1m-event-time-1.0.0"
T2_PUBLICATION_LAG_SLOT_V1: Final = "UNSET_PENDING_OWNER_DECISION_OR_5"

ARCHIVE_TERMS_VERSION_V1: Final = (
    "operator-declared:bybit-public-trading-archive:v1:"
    "publicly-served-unauthenticated;terms-text-not-machine-verified-2026-09-24"
)
ARCHIVE_AUTHORIZATION_REFERENCE_V1: Final = (
    "Phase R3B under the repository's standing authorization of the public Bybit "
    "V5 historical acquisition path: download of Bybit's free public trade "
    "archive (https://public.bybit.com/trading/) for internal research only, no "
    "redistribution, no credential, no account, no paid tier. Bybit's Terms of "
    "Service text could not be machine-verified on 2026-09-24 and is an owner "
    "confirmation item. Public market data only: no broker, account, order, "
    "execution or live-trading authority is implied."
)

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.bybit_public_archive_v1")
_ISSUER: Final = object()
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICROS_PER_MINUTE: Final = 60_000_000
#: One step of the printed timestamp (four decimals of a second).
ARCHIVE_TIMESTAMP_RESOLUTION_MICROS: Final = 100
VOLUME_QUANTUM_V1: Final = Decimal("1E-8")
TURNOVER_QUANTUM_V1: Final = Decimal("1E-8")


class BybitPublicArchiveError(ValueError):
    """Raised on any integrity, schema, ordering or identity failure. Always fail closed."""


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Source contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicArchiveSourceContractV1:
    """The free-archive source. Issued only here; its ``source_id`` is derived, never a flag."""

    schema_version: str
    originating_exchange: str
    publisher: str
    base_url: str
    file_pattern: str
    archive_schema: tuple[str, ...]
    event_time_semantics: str
    ordering_semantics: str
    integrity_semantics: str
    timing_authority: str
    publication_lag_slot: str
    provider_terms_version: str
    authorization_reference: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise BybitPublicArchiveError("public_archive_contract_is_issued_only_here")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "originating_exchange": self.originating_exchange,
            "publisher": self.publisher,
            "base_url": self.base_url,
            "file_pattern": self.file_pattern,
            "archive_schema": list(self.archive_schema),
            "event_time_semantics": self.event_time_semantics,
            "ordering_semantics": self.ordering_semantics,
            "integrity_semantics": self.integrity_semantics,
            "timing_authority": self.timing_authority,
            "publication_lag_slot": self.publication_lag_slot,
            "provider_terms_version": self.provider_terms_version,
            "authorization_reference": self.authorization_reference,
        }

    def content_hash(self) -> str:
        return _sha256_json(self.payload())

    @property
    def source_id(self) -> UUID:
        identity = {
            key: self.payload()[key]
            for key in ("schema_version", "originating_exchange", "publisher", "base_url",
                        "file_pattern", "provider_terms_version", "authorization_reference")
        }
        return uuid5(_NAMESPACE, f"public-archive-source:{_sha256_json(identity)}")

    def file_name(self, symbol: str, day: date) -> str:
        if not symbol.isalnum() or not symbol.isupper():
            raise BybitPublicArchiveError("archive_symbol_malformed")
        return f"{symbol}{day.isoformat()}.csv.gz"

    def url(self, symbol: str, day: date) -> str:
        return f"{self.base_url}{symbol}/{self.file_name(symbol, day)}"


def bybit_public_trade_archive_contract_v1() -> PublicArchiveSourceContractV1:
    return PublicArchiveSourceContractV1(
        schema_version="public-archive-source-contract-v1",
        originating_exchange="BYBIT",
        publisher="BYBIT",
        base_url=ARCHIVE_BASE_URL_V1,
        file_pattern="{SYMBOL}/{SYMBOL}{YYYY-MM-DD}.csv.gz",
        archive_schema=ARCHIVE_SCHEMA_V1,
        event_time_semantics=(
            "timestamp_is_venue_trade_time_epoch_seconds_float_text_max_14_significant_digits;"
            "resolution_100us;rounding_direction_undocumented"
        ),
        ordering_semantics="no_venue_sequence;order_evidence_is_timestamp_then_published_row_position",
        integrity_semantics="no_publisher_checksum;content_length+gzip_crc+platform_sha256+strict_schema",
        timing_authority="VENUE_EVENT_TIMESTAMP",
        publication_lag_slot=T2_PUBLICATION_LAG_SLOT_V1,
        provider_terms_version=ARCHIVE_TERMS_VERSION_V1,
        authorization_reference=ARCHIVE_AUTHORIZATION_REFERENCE_V1,
        _issuer=_ISSUER,
    )


# ---------------------------------------------------------------------------
# Acquisition (resumable, checkpointed, corruption-detecting)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponseV1:
    status: int
    headers: Mapping[str, str]
    body: bytes


#: ``(url, headers) -> response``. Injected so every rule is testable offline.
FetchV1 = Callable[[str, Mapping[str, str]], HttpResponseV1]


def urllib_fetch_v1(url: str, headers: Mapping[str, str]) -> HttpResponseV1:
    """The one network call: an HTTPS GET to the public archive host only."""
    import urllib.error
    import urllib.request

    if not url.startswith(ARCHIVE_BASE_URL_V1):
        raise BybitPublicArchiveError("fetch_outside_the_public_archive")
    request = urllib.request.Request(url, headers=dict(headers))  # nosec B310 - fixed https host
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310 - https only
            return HttpResponseV1(response.status, {k.lower(): v for k, v in response.headers.items()},
                                  response.read())
    except urllib.error.HTTPError as error:
        return HttpResponseV1(error.code, {k.lower(): v for k, v in error.headers.items()}, b"")


@dataclass(frozen=True, slots=True)
class ArchiveFileManifestV1:
    source_id: str
    symbol: str
    utc_day: str
    url: str
    file_name: str
    bytes: int
    sha256: str
    uncompressed_bytes: int
    uncompressed_sha256: str
    http_last_modified: str | None
    http_etag: str | None
    retrieved_at: str

    def identity(self) -> dict[str, Any]:
        """What binds content: never the retrieval instant or HTTP headers."""
        return {
            "source_id": self.source_id, "symbol": self.symbol, "utc_day": self.utc_day,
            "file_name": self.file_name, "bytes": self.bytes, "sha256": self.sha256,
            "uncompressed_bytes": self.uncompressed_bytes,
            "uncompressed_sha256": self.uncompressed_sha256,
        }


def _file_paths(root: Path, contract: PublicArchiveSourceContractV1, symbol: str, day: date) -> tuple[Path, Path, Path]:
    directory = root / "v1" / f"source={contract.source_id}" / f"symbol={symbol}"
    name = contract.file_name(symbol, day)
    return directory / name, directory / (name + ".part"), directory / (name + ".manifest.json")


def _gzip_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
    except (OSError, EOFError, zlib.error) as error:
        raise BybitPublicArchiveError("archive_file_does_not_decompress") from error
    return digest.hexdigest(), size


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_archive_file_manifest_v1(path: Path) -> ArchiveFileManifestV1:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ArchiveFileManifestV1(**raw)


def verify_archive_file_v1(root: Path, manifest: ArchiveFileManifestV1) -> Path:
    """Re-prove a stored file against its manifest, byte for byte."""
    contract = bybit_public_trade_archive_contract_v1()
    final, _, _ = _file_paths(root, contract, manifest.symbol, date.fromisoformat(manifest.utc_day))
    if manifest.source_id != str(contract.source_id):
        raise BybitPublicArchiveError("archive_manifest_source_mismatch")
    if not final.exists() or final.stat().st_size != manifest.bytes or _file_sha256(final) != manifest.sha256:
        raise BybitPublicArchiveError("archive_file_checksum_mismatch")
    if _gzip_digest(final) != (manifest.uncompressed_sha256, manifest.uncompressed_bytes):
        raise BybitPublicArchiveError("archive_file_uncompressed_digest_mismatch")
    return final


def acquire_archive_day_v1(
    root: Path,
    symbol: str,
    day: date,
    *,
    fetch: FetchV1 = urllib_fetch_v1,
    chunk_bytes: int = 16 << 20,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ArchiveFileManifestV1:
    """Download one daily file with resumable ranged GETs, then prove it.

    Checkpointed: an existing manifest is re-verified and returned (a
    mismatch raises; nothing is overwritten). A ``.part`` file is resumed
    from its length. The finished file must match the server's total length,
    decompress completely and parse under the strict schema before its
    manifest is written; a failure deletes the partial and raises.
    """
    contract = bybit_public_trade_archive_contract_v1()
    final, part, manifest_path = _file_paths(root, contract, symbol, day)
    if manifest_path.exists():
        manifest = load_archive_file_manifest_v1(manifest_path)
        verify_archive_file_v1(root, manifest)
        return manifest
    final.parent.mkdir(parents=True, exist_ok=True)
    url = contract.url(symbol, day)
    total: int | None = None
    last_modified = etag = None
    while True:
        offset = part.stat().st_size if part.exists() else 0
        if total is not None and offset >= total:
            break
        response = fetch(url, {"Range": f"bytes={offset}-{offset + chunk_bytes - 1}"})
        if response.status == 404:
            raise BybitPublicArchiveError("archive_day_not_published")
        if response.status == 416 and total is not None and offset == total:
            break
        if response.status not in (200, 206):
            raise BybitPublicArchiveError(f"archive_http_status:{response.status}")
        content_range = response.headers.get("content-range")
        if response.status == 206:
            if not content_range or not content_range.startswith(f"bytes {offset}-"):
                raise BybitPublicArchiveError("archive_range_response_mismatch")
            announced = int(content_range.rsplit("/", 1)[1])
        else:
            if offset:
                part.unlink()  # server ignored the range: restart honestly
            announced = int(response.headers.get("content-length", len(response.body)))
        if total is not None and announced != total:
            raise BybitPublicArchiveError("archive_file_changed_during_download")
        total = announced
        new_etag = response.headers.get("etag")
        if etag is not None and new_etag != etag:
            raise BybitPublicArchiveError("archive_file_changed_during_download")
        etag, last_modified = new_etag, response.headers.get("last-modified")
        with part.open("ab") as handle:
            handle.write(response.body)
        if not response.body:
            raise BybitPublicArchiveError("archive_empty_range_response")
    if total is None or part.stat().st_size != total:
        part.unlink(missing_ok=True)
        raise BybitPublicArchiveError("archive_download_length_mismatch")
    try:
        uncompressed_sha, uncompressed_bytes = _gzip_digest(part)
        with gzip.open(part, "rt", encoding="utf-8", newline="") as handle:
            for _ in iter_archive_trades_v1(handle, symbol=symbol, day=day):
                pass
    except BybitPublicArchiveError:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, final)
    manifest = ArchiveFileManifestV1(
        source_id=str(contract.source_id), symbol=symbol, utc_day=day.isoformat(), url=url,
        file_name=final.name, bytes=total, sha256=_file_sha256(final),
        uncompressed_bytes=uncompressed_bytes, uncompressed_sha256=uncompressed_sha,
        http_last_modified=last_modified, http_etag=etag, retrieved_at=now().isoformat(),
    )
    staged = manifest_path.with_suffix(".tmp")
    staged.write_text(json.dumps(asdict(manifest), sort_keys=True, indent=1), encoding="utf-8")
    os.replace(staged, manifest_path)
    return manifest


# ---------------------------------------------------------------------------
# Strict parse -> source-neutral public trades
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveTradeV1:
    row_index: int
    trade_id: str
    timestamp_text: str
    trade_ts_micros: int
    side: str
    price: Decimal
    quantity: Decimal
    tick_direction: str
    rpi: bool
    published_foreign_notional: Decimal

    @property
    def ordering_key(self) -> tuple[int, int]:
        return (self.trade_ts_micros, self.row_index)


def _positive(text: str, name: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise BybitPublicArchiveError(f"archive_{name}_malformed") from error
    if not value.is_finite() or value <= 0:
        raise BybitPublicArchiveError(f"archive_{name}_not_positive")
    return value


def _micros(text: str) -> int:
    """The printed seconds as exact integer microseconds; finer than 1 us is refused."""
    try:
        seconds = Decimal(text)
    except InvalidOperation as error:
        raise BybitPublicArchiveError("archive_timestamp_malformed") from error
    micros = seconds * 1_000_000
    if not micros.is_finite() or micros != micros.to_integral_value():
        raise BybitPublicArchiveError("archive_timestamp_finer_than_microseconds")
    return int(micros)


def iter_archive_trades_v1(handle: io.TextIOBase | Any, *, symbol: str, day: date) -> Iterator[ArchiveTradeV1]:
    """Strict, ordered parse of one daily file. Raises on the first defect."""
    reader = csv.reader(handle)
    header = next(reader, None)
    if header is None or tuple(header) != ARCHIVE_SCHEMA_V1:
        raise BybitPublicArchiveError("archive_header_differs_from_contract_schema")
    day_start = (datetime(day.year, day.month, day.day, tzinfo=UTC) - _EPOCH) // timedelta(microseconds=1)
    day_end = day_start + 86_400_000_000
    previous = -1
    seen: set[str] = set()
    for index, row in enumerate(reader):
        if len(row) != len(ARCHIVE_SCHEMA_V1):
            raise BybitPublicArchiveError("archive_row_width_mismatch")
        ts, row_symbol, side, size, price, tick, match_id, _gross, home, foreign, rpi = row
        if row_symbol != symbol:
            raise BybitPublicArchiveError("archive_row_symbol_mismatch")
        micros = _micros(ts)
        if not day_start <= micros < day_end:
            raise BybitPublicArchiveError("archive_row_outside_its_utc_day")
        if micros < previous:
            raise BybitPublicArchiveError("archive_rows_not_in_time_order")
        previous = micros
        if side not in ("Buy", "Sell"):
            raise BybitPublicArchiveError("archive_side_malformed")
        if not match_id.strip():
            raise BybitPublicArchiveError("archive_trade_id_missing")
        if match_id in seen:
            raise BybitPublicArchiveError("archive_trade_id_repeated")
        seen.add(match_id)
        quantity = _positive(size, "size")
        if _positive(home, "home_notional") != quantity:
            raise BybitPublicArchiveError("archive_home_notional_differs_from_size")
        if rpi not in ("0", "1"):
            raise BybitPublicArchiveError("archive_rpi_flag_malformed")
        yield ArchiveTradeV1(
            row_index=index, trade_id=match_id, timestamp_text=ts, trade_ts_micros=micros, side=side,
            price=_positive(price, "price"), quantity=quantity, tick_direction=tick,
            rpi=rpi == "1", published_foreign_notional=_positive(foreign, "foreign_notional"),
        )


# ---------------------------------------------------------------------------
# Frames and 1m event-time bars
# ---------------------------------------------------------------------------

def _ts(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def _flag(value: bool) -> str:
    return "true" if value else "false"


def build_archive_bars_v1(trades: Sequence[ArchiveTradeV1]) -> list[tuple[object, ...]]:
    """1m event-time bars from one *complete* daily file. No empty bars, no fill.

    A published daily file is the publisher's statement of the whole UTC day,
    so every minute with trades is complete in event time. That is an event-time
    fact only: no bar here carries a knowledge time (NULL) until OR-5.
    """
    rows: list[tuple[object, ...]] = []
    bucket: list[ArchiveTradeV1] = []

    def close() -> None:
        if not bucket:
            return
        first, last = bucket[0], bucket[-1]
        minute = first.trade_ts_micros // _MICROS_PER_MINUTE * _MICROS_PER_MINUTE
        lead = {t.price for t in bucket if t.trade_ts_micros == first.trade_ts_micros}
        tail = {t.price for t in bucket if t.trade_ts_micros == last.trade_ts_micros}
        near_boundary = sum(
            1 for t in bucket
            if t.trade_ts_micros - minute < ARCHIVE_TIMESTAMP_RESOLUTION_MICROS
            or minute + _MICROS_PER_MINUTE - t.trade_ts_micros <= ARCHIVE_TIMESTAMP_RESOLUTION_MICROS
        )
        rows.append((
            _ts(minute), _ts(minute + _MICROS_PER_MINUTE), None, first.price,
            max(t.price for t in bucket), min(t.price for t in bucket), last.price,
            sum((t.quantity for t in bucket), Decimal(0)).quantize(VOLUME_QUANTUM_V1, ROUND_HALF_EVEN),
            sum((t.price * t.quantity for t in bucket), Decimal(0)).quantize(TURNOVER_QUANTUM_V1, ROUND_HALF_EVEN),
            len(bucket), sum(1 for t in bucket if t.rpi),
            _flag(len(lead) > 1), _flag(len(tail) > 1), near_boundary, first.trade_id, last.trade_id,
        ))
        bucket.clear()

    for trade in trades:
        if bucket and trade.trade_ts_micros // _MICROS_PER_MINUTE != bucket[0].trade_ts_micros // _MICROS_PER_MINUTE:
            close()
        bucket.append(trade)
    close()
    return rows


@dataclass(frozen=True, slots=True)
class ArchiveDatasetV1:
    """A deterministic reconstruction over verified archive files. T2 lag UNSET."""

    identity: Mapping[str, Any]
    content_hash: str
    dataset_version_id: UUID
    frame_manifests: Mapping[str, str]


def build_archive_dataset_v1(
    root: Path, manifests: Sequence[ArchiveFileManifestV1], *, store: ResearchFrameStoreV1
) -> ArchiveDatasetV1:
    """Re-verify every file, parse, and write trade + bar frames with one identity."""
    if not manifests:
        raise BybitPublicArchiveError("archive_dataset_requires_files")
    ordered = sorted(manifests, key=lambda item: (item.symbol, item.utc_day))
    symbols = {item.symbol for item in ordered}
    if len(symbols) != 1:
        raise BybitPublicArchiveError("archive_dataset_is_one_symbol")
    symbol = symbols.pop()
    days = [date.fromisoformat(item.utc_day) for item in ordered]
    if len(set(days)) != len(days):
        raise BybitPublicArchiveError("archive_dataset_repeats_a_day")
    contract = bybit_public_trade_archive_contract_v1()
    bars: list[tuple[object, ...]] = []

    def trade_rows() -> Iterator[tuple[object, ...]]:
        # Streamed one day at a time: memory is bounded by one day's trades
        # (needed for that day's bars), not by the dataset's length.
        for manifest in ordered:
            path = verify_archive_file_v1(root, manifest)
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                trades = list(
                    iter_archive_trades_v1(handle, symbol=symbol, day=date.fromisoformat(manifest.utc_day))
                )
            bars.extend(build_archive_bars_v1(trades))
            for t in trades:
                yield (manifest.utc_day, t.row_index, t.trade_id, t.timestamp_text, t.trade_ts_micros,
                       _ts(t.trade_ts_micros), None, t.side, t.tick_direction, _flag(t.rpi), t.price,
                       t.quantity, t.published_foreign_notional, manifest.sha256)
    lineage = {"source_id": str(contract.source_id), "symbol": symbol,
               "days": [item.utc_day for item in ordered]}
    written: dict[str, FrameManifestV1] = {}
    # The trade frame must be written first: streaming it fills ``bars``.
    written[T2_ARCHIVE_TRADE_FRAME.kind] = store.write_frame(T2_ARCHIVE_TRADE_FRAME, trade_rows(), lineage=lineage)
    written[T2_ARCHIVE_OHLCV_1M_FRAME.kind] = store.write_frame(T2_ARCHIVE_OHLCV_1M_FRAME, iter(bars), lineage=lineage)
    # Days are contiguous only if listed so; a missing day is a declared gap.
    gaps = [
        {"after": a.isoformat(), "before": b.isoformat()}
        for a, b in pairwise(days) if (b - a).days != 1
    ]
    identity = {
        "schema_version": "public-archive-dataset-v1",
        "source_contract_content_hash": contract.content_hash(),
        "source_id": str(contract.source_id),
        "normalization_semantic_version": ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1,
        "bar_semantic_version": ARCHIVE_BAR_SEMANTIC_VERSION_V1,
        "publication_lag_slot": T2_PUBLICATION_LAG_SLOT_V1,
        "market_knowledge_at": "NULL_UNTIL_OR_5",
        "symbol": symbol,
        "files": [item.identity() for item in ordered],
        "day_gaps": gaps,
        "frames": {kind: {"logical_content_hash": m.logical_content_hash, "row_count": m.row_count}
                   for kind, m in sorted(written.items())},
    }
    content_hash = _sha256_json(identity)
    return ArchiveDatasetV1(
        identity=identity, content_hash=content_hash,
        dataset_version_id=uuid5(_NAMESPACE, f"public-archive-dataset:{content_hash}"),
        frame_manifests={kind: m.manifest_hash for kind, m in written.items()},
    )


def verify_archive_dataset_v1(
    root: Path,
    identity: Mapping[str, Any],
    *,
    frame_manifests: Mapping[str, str],
    store: ResearchFrameStoreV1,
) -> ArchiveDatasetV1:
    """Rebuild a catalogued reconstruction from its files and require the same identity.

    Re-verifies every file byte for byte, re-parses it strictly, recomputes
    every frame's logical hash (without writing) and re-hashes the stored
    frame objects. Nothing is trusted from the catalog row.
    """
    from .research_data_plane_v1 import logical_content_hash_v1

    contract = bybit_public_trade_archive_contract_v1()
    files = identity.get("files")
    if not isinstance(files, list) or identity.get("source_contract_content_hash") != contract.content_hash():
        raise BybitPublicArchiveError("archive_identity_malformed_or_foreign")
    manifests = []
    for item in files:
        _, _, manifest_path = _file_paths(root, contract, str(item["symbol"]), date.fromisoformat(str(item["utc_day"])))
        manifest = load_archive_file_manifest_v1(manifest_path)
        if manifest.identity() != item:
            raise BybitPublicArchiveError("archive_file_manifest_differs_from_identity")
        manifests.append(manifest)
    trades_by_day = []
    for manifest in manifests:
        path = verify_archive_file_v1(root, manifest)
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            trades_by_day.append(
                (manifest, list(iter_archive_trades_v1(handle, symbol=manifest.symbol,
                                                       day=date.fromisoformat(manifest.utc_day))))
            )
    rows = [
        (m.utc_day, t.row_index, t.trade_id, t.timestamp_text, t.trade_ts_micros, _ts(t.trade_ts_micros), None, t.side,
         t.tick_direction, _flag(t.rpi), t.price, t.quantity, t.published_foreign_notional, m.sha256)
        for m, trades in trades_by_day for t in trades
    ]
    bars = [bar for _, trades in trades_by_day for bar in build_archive_bars_v1(trades)]
    rebuilt = {
        T2_ARCHIVE_TRADE_FRAME.kind: logical_content_hash_v1(T2_ARCHIVE_TRADE_FRAME, rows),
        T2_ARCHIVE_OHLCV_1M_FRAME.kind: logical_content_hash_v1(T2_ARCHIVE_OHLCV_1M_FRAME, bars),
    }
    expected = identity.get("frames", {})
    for kind, (logical, count) in rebuilt.items():
        if expected.get(kind) != {"logical_content_hash": logical, "row_count": count}:
            raise BybitPublicArchiveError(f"archive_frame_does_not_rebuild:{kind}")
        stored = store.load_manifest(frame_manifests[kind])
        store.verify(stored)
        if stored.logical_content_hash != logical:
            raise BybitPublicArchiveError(f"archive_stored_frame_differs:{kind}")
    content_hash = _sha256_json(dict(identity))
    return ArchiveDatasetV1(
        identity=dict(identity), content_hash=content_hash,
        dataset_version_id=uuid5(_NAMESPACE, f"public-archive-dataset:{content_hash}"),
        frame_manifests=dict(frame_manifests),
    )


class PostgresPublicArchiveCatalogV1:
    """``public_archive_datasets``: append-only catalog; the lag slot is always unset."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def register(self, dataset: ArchiveDatasetV1) -> None:
        identity = dataset.identity
        files = identity["files"]
        trade_rows = identity["frames"][T2_ARCHIVE_TRADE_FRAME.kind]["row_count"]
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public_archive_datasets (dataset_version_id, content_hash, source_id, "
                "symbol, first_utc_day, last_utc_day, file_count, trade_count, publication_lag_slot, "
                "identity, frame_manifests, registered_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) "
                "ON CONFLICT (dataset_version_id) DO NOTHING",
                (
                    dataset.dataset_version_id, dataset.content_hash, UUID(str(identity["source_id"])),
                    identity["symbol"], date.fromisoformat(files[0]["utc_day"]),
                    date.fromisoformat(files[-1]["utc_day"]), len(files), trade_rows,
                    identity["publication_lag_slot"], json.dumps(identity, sort_keys=True),
                    json.dumps(dict(dataset.frame_manifests), sort_keys=True), datetime.now(UTC),
                ),
            )
            cursor.execute(
                "SELECT content_hash FROM public_archive_datasets WHERE dataset_version_id=%s",
                (dataset.dataset_version_id,),
            )
            row = cursor.fetchone()
        if row is None or str(row[0]).strip() != dataset.content_hash:
            raise BybitPublicArchiveError("archive_catalog_conflict")


__all__ = [
    "ARCHIVE_SCHEMA_V1",
    "T2_ARCHIVE_OHLCV_1M_FRAME",
    "T2_ARCHIVE_TRADE_FRAME",
    "T2_PUBLICATION_LAG_SLOT_V1",
    "ArchiveDatasetV1",
    "ArchiveFileManifestV1",
    "ArchiveTradeV1",
    "BybitPublicArchiveError",
    "HttpResponseV1",
    "PublicArchiveSourceContractV1",
    "acquire_archive_day_v1",
    "build_archive_bars_v1",
    "build_archive_dataset_v1",
    "bybit_public_trade_archive_contract_v1",
    "iter_archive_trades_v1",
    "load_archive_file_manifest_v1",
    "urllib_fetch_v1",
    "verify_archive_file_v1",
]
