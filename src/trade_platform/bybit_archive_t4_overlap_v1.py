"""Phase R3B.3 -- trade-level overlap of the free public archive (T2) and first-party capture (T4).

``RESEARCH_ONLY``. No network call, no frame written, no value changed. Compares
the trades Bybit later publishes in its daily archive with the trades our own
recorder received on the public WebSocket, joined on the venue trade id, over
the event-time span one sealed T4 segment observed without a gap.

Why
---
Two open questions need evidence rather than assumption:

* **Is the archive the same trade tape the public feed disseminated?** If every
  archive trade inside a contiguously captured span is one our recorder
  received live -- same id, price, size, side and RPI flag -- then the archive's
  content was public in real time, which is the factual premise any T2
  knowledge-time semantics (owner decision OR-5) has to rest on. This module
  states whether that premise holds on the overlap; it sets no lag.
* **Did our capture miss anything?** An archive trade inside the span that the
  recorder never received is a first-party completeness failure the capture
  archive's own proofs cannot see.

The span
--------
A sealed segment is one coverage window intersected with one admissible clock
interval, so the recorder was connected throughout it. The comparison span is
``[first, last]`` of the segment's captured *venue trade times* (milliseconds).
Trades stamped exactly the first or last millisecond may straddle the segment
edge and are counted separately as *boundary* trades rather than as misses.
Archive times are printed seconds (100 us resolution on the verified source);
they are compared by their millisecond floor, and the sub-millisecond remainder
is reported as a distribution, never used to reorder anything.

What is reported
----------------
Counts only, and trade ids for disagreements (never prices): matched trades;
archive-only trades inside the span (capture misses) and at its boundary;
T4-only trades (archive omissions); per-field disagreements (price, quantity,
side, RPI, millisecond time); and, as *context for OR-5 and never a lag value*,
two separately labelled facts: the archive files' HTTP ``Last-Modified`` (the
only positively proven publication instant) and the distribution of T4
``market_knowledge_at - event_at`` on matched trades, which is an upper bound
under the session clock rule, not a latency and not a T2 lag.

The identity binds the parent seal's content hash and every archive file's
SHA-256, so the same inputs always give the same ``content_hash``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any, Final

from .bybit_public_archive_v1 import ArchiveTradeV1
from .research_data_plane_v1 import T4_TRADE_FRAME

ARCHIVE_T4_OVERLAP_SCHEMA_VERSION_V1: Final = "bybit-archive-t4-overlap-v1"
_MAX_LISTED_IDS: Final = 20
_QUANTILES: Final = ("0.5", "0.9", "0.99", "1")
_T4_COLUMNS: Final = {field.name: index for index, field in enumerate(T4_TRADE_FRAME.schema)}
_MICROSECOND: Final = timedelta(microseconds=1)
_MILLIS_PER_DAY: Final = 86_400_000


def _utc_day(ms: int) -> str:
    return (date(1970, 1, 1) + timedelta(days=ms // _MILLIS_PER_DAY)).isoformat()


class ArchiveT4OverlapError(ValueError):
    """Raised when the overlap cannot be computed honestly."""


@dataclass(frozen=True, slots=True)
class T4TradeRowV1:
    trade_id: str
    trade_ts_millis: int
    side: str
    rpi: str | None
    price: Decimal
    quantity: Decimal
    event_at: datetime
    market_knowledge_at: datetime


def t4_trades_from_frame_rows_v1(rows: Iterable[Sequence[object]]) -> list[T4TradeRowV1]:
    """Project sealed ``T4_TRADE`` frame rows onto the fields the overlap compares."""
    out = []
    for row in rows:
        out.append(
            T4TradeRowV1(
                trade_id=str(row[_T4_COLUMNS["trade_id"]]),
                trade_ts_millis=int(row[_T4_COLUMNS["trade_ts_millis"]]),  # type: ignore[call-overload]
                side=str(row[_T4_COLUMNS["side"]]),
                rpi=None if row[_T4_COLUMNS["rpi"]] is None else str(row[_T4_COLUMNS["rpi"]]),
                price=Decimal(str(row[_T4_COLUMNS["price"]])),
                quantity=Decimal(str(row[_T4_COLUMNS["quantity"]])),
                event_at=row[_T4_COLUMNS["event_at"]],  # type: ignore[arg-type]
                market_knowledge_at=row[_T4_COLUMNS["market_knowledge_at"]],  # type: ignore[arg-type]
            )
        )
    return out


def _nearest_rank(values: Sequence[int], q: Decimal) -> int:
    ordered = sorted(values)
    index = max(0, int((q * len(ordered)).to_integral_value(rounding=ROUND_CEILING)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _distribution(values: Sequence[int]) -> dict[str, int]:
    return {q: _nearest_rank(values, Decimal(q)) for q in _QUANTILES} if values else {}


def _listed(ids: list[str]) -> list[str]:
    return sorted(ids)[:_MAX_LISTED_IDS]


@dataclass(frozen=True, slots=True)
class ArchiveT4OverlapReportV1:
    identity: Mapping[str, Any]
    content_hash: str


def compare_archive_with_t4_v1(
    *,
    parent_seal_content_hash: str,
    t4_trades: Sequence[T4TradeRowV1],
    archive_trades: Iterable[ArchiveTradeV1],
    archive_files: Mapping[str, Mapping[str, str]],
) -> ArchiveT4OverlapReportV1:
    """Join archive and T4 trades on trade id over the segment's captured event-time span.

    ``archive_files`` maps each verified archive file name to its identity
    (``utc_day``, ``sha256`` and, where published, ``http_last_modified``). The caller must
    pass every archive trade of every UTC day the span touches; a day whose file
    is absent would turn real trades into false T4-only counts, so the span's
    days are checked against the files given.
    """
    if not t4_trades:
        raise ArchiveT4OverlapError("segment_has_no_captured_trades")
    t4_by_id: dict[str, T4TradeRowV1] = {}
    for trade in t4_trades:
        if trade.trade_id in t4_by_id:
            raise ArchiveT4OverlapError("t4_trade_id_repeated")
        t4_by_id[trade.trade_id] = trade
    first_ms = min(trade.trade_ts_millis for trade in t4_trades)
    last_ms = max(trade.trade_ts_millis for trade in t4_trades)
    first_day = _utc_day(first_ms)
    last_day = _utc_day(last_ms)
    covered_days = {str(value.get("utc_day")) for value in archive_files.values()}
    for day in {first_day, last_day}:
        if day not in covered_days:
            raise ArchiveT4OverlapError(f"archive_file_missing_for_span_day:{day}")

    matched = 0
    archive_only_inside: list[str] = []
    archive_only_boundary: list[str] = []
    disagreements: dict[str, list[str]] = {name: [] for name in ("price", "quantity", "side", "rpi", "millisecond")}
    sub_ms_remainder_micros: list[int] = []
    knowledge_minus_event_micros: list[int] = []
    seen: set[str] = set()
    archive_in_span = 0
    for archived in archive_trades:
        ms = archived.trade_ts_micros // 1_000
        if ms < first_ms or ms > last_ms:
            continue
        archive_in_span += 1
        if archived.trade_id in seen:
            raise ArchiveT4OverlapError("archive_trade_id_repeated")
        seen.add(archived.trade_id)
        t4 = t4_by_id.get(archived.trade_id)
        if t4 is None:
            (archive_only_boundary if ms in (first_ms, last_ms) else archive_only_inside).append(archived.trade_id)
            continue
        matched += 1
        if archived.price != t4.price:
            disagreements["price"].append(archived.trade_id)
        if archived.quantity != t4.quantity:
            disagreements["quantity"].append(archived.trade_id)
        if archived.side != t4.side:
            disagreements["side"].append(archived.trade_id)
        if t4.rpi is not None and (t4.rpi == "true") != archived.rpi:
            disagreements["rpi"].append(archived.trade_id)
        if ms != t4.trade_ts_millis:
            disagreements["millisecond"].append(archived.trade_id)
        sub_ms_remainder_micros.append(archived.trade_ts_micros - t4.trade_ts_millis * 1_000)
        knowledge_minus_event_micros.append((t4.market_knowledge_at - t4.event_at) // _MICROSECOND)
    t4_only = [trade_id for trade_id in t4_by_id if trade_id not in seen]

    identity = {
        "schema_version": ARCHIVE_T4_OVERLAP_SCHEMA_VERSION_V1,
        "parent_seal_content_hash": parent_seal_content_hash,
        "archive_files": {name: dict(sorted(value.items())) for name, value in sorted(archive_files.items())},
        "span": {"first_trade_ts_millis": first_ms, "last_trade_ts_millis": last_ms},
        "counts": {
            "t4_trades": len(t4_trades),
            "archive_trades_in_span": archive_in_span,
            "matched": matched,
            "archive_only_inside_span": len(archive_only_inside),
            "archive_only_at_span_boundary": len(archive_only_boundary),
            "t4_only": len(t4_only),
            **{f"{name}_disagreements": len(ids) for name, ids in sorted(disagreements.items())},
        },
        "ids": {
            "archive_only_inside_span": _listed(archive_only_inside),
            "t4_only": _listed(t4_only),
            **{f"{name}_disagreements": _listed(ids) for name, ids in sorted(disagreements.items())},
        },
        "archive_sub_millisecond_remainder_micros": _distribution(sub_ms_remainder_micros),
        "context_for_or5_not_a_lag": {
            "t4_market_knowledge_minus_event_micros_upper_bound": _distribution(knowledge_minus_event_micros),
            "archive_http_last_modified": {
                name: value.get("http_last_modified") for name, value in sorted(archive_files.items())
            },
        },
    }
    content_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return ArchiveT4OverlapReportV1(identity=identity, content_hash=content_hash)


__all__ = [
    "ARCHIVE_T4_OVERLAP_SCHEMA_VERSION_V1",
    "ArchiveT4OverlapError",
    "ArchiveT4OverlapReportV1",
    "T4TradeRowV1",
    "compare_archive_with_t4_v1",
    "t4_trades_from_frame_rows_v1",
]
