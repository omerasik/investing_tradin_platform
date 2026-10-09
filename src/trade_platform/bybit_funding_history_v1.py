"""OR-6 F3 -- Bybit-published historical funding for cost-complete holdout evaluation.

``RESEARCH_ONLY``. Public market data only: the one network call is an
unauthenticated GET of ``https://api.bybit.com/v5/market/funding/history``
(``category=linear``). No credential, no account, no order path, no paid source.

Owner direction (OR-6, F3, 2026-10-09): cost-complete research uses real,
official, zero-cost Bybit-published historical funding. No funding is invented,
no constant is assumed, and a missing observation is never inferred.

Provider-published semantics (kept apart from what is calculated here)
---------------------------------------------------------------------
:data:`PUBLISHED_SEMANTICS_V1` records what Bybit states, with its sources:
a positive rate means longs pay shorts and a negative rate means shorts pay
longs; the fee is position value times rate, position value being quantity
times *mark* price; a position fully closed before the funding time neither
pays nor receives; each symbol has its own interval, which Bybit may change
without a separate announcement. The history endpoint returns the *settled*
rate with its settlement timestamp (``fundingRateTimestamp``) and serves it
only after that instant.

Evidence tier and timing
------------------------
A record is ``T2_EVENT_TIME``: the venue's settlement instant is its event time;
the source publishes no knowledge time and this acquisition is retrospective.
It is consumed only as a realized cost *outcome* at its own settlement instant,
never as a decision input, so it cannot lift anything: a holdout charged with it
keeps its ``CONDITIONAL_T2`` claim ceiling.

Acquisition (:func:`acquire_funding_history_v1`)
------------------------------------------------
One page per closed UTC day (``startTime`` = day start, ``endTime`` = day end -
1 ms, ``limit`` 200), checkpointed: a stored page is re-parsed, never refetched,
and a page is stored only after it parses strictly. Days inside the untouched
holdout are refused unless a registry-issued opening admits every one of them
(the research-bars gate, reused). A day that has not closed is refused.

Identity and completeness
-------------------------
The identity is the published content only -- symbol, window, every
``(settlement instant, exact rate text)`` -- so re-acquiring identical records
gives the identical hash; each raw page's SHA-256 lives in a separately hashed
provenance block, and :func:`load_funding_history_v1` re-parses the stored
pages and must re-derive both. The window is ``COMPLETE`` only if every spacing
between consecutive records is one and the same interval and both window edges
are tighter than it; anything else (an interval change, a missing record, an
empty window) is ``IRREGULAR_REFUSED`` with the offending spacings listed --
recorded, never interpolated or bridged. Bybit publishes no interval history,
so a genuine interval change inside a holdout span cannot be told from a
missing record and refuses that validation until the owner supplies schedule
evidence. A stored page is authoritative: a page the venue later corrects is
re-acquired only by moving the stored page aside (it is never rewritten).

Charge rule (:func:`funding_steps_v1`, calculated, :data:`CALCULATED_SEMANTICS_V1`)
---------------------------------------------------------------------------------
The Strategy Lab Decimal model holds ``held_j`` units of equity over
``[open_j, open_{j+1})``. A funding instant ``T`` that is a bar open ``open_k``:

* the same position ``p`` before and after ``T``: charge ``-p * rate *
  open_k / open_{k-1}`` to interval ``k-1`` (position value at ``T`` relative
  to the equity that set it; the first print at or after ``T`` -- the bar open
  -- stands in for the mark price, which T2 bars do not carry);
* flat before and after: provably flat, nothing is charged;
* a position change filled at that very open: whether the fill precedes the
  settlement is not knowable from bars, so the *worse* of the two outcomes is
  charged (never both, never the better one), as a cash flow at ``T`` booked
  on interval ``k-1`` (the post-fill outcome is ``after * rate`` times that
  interval's growth, so it compounds from ``T`` exactly).

A funding instant strictly inside an interval (its bar is missing) is charged
nothing if that interval is flat; otherwise the candidate is not costable
(no reference price, nothing interpolated) and fails closed upstream.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .bybit_public_archive_v1 import HttpResponseV1
from .public_archive_research_bars_v1 import _days
from .strategy_lab_study_v1 import identity_hash_v1
from .strategy_sdk_v1 import BarsV1

FUNDING_HISTORY_SCHEMA_VERSION_V1: Final = "bybit-funding-history-v1"
FUNDING_PROVENANCE_SCHEMA_VERSION_V1: Final = "bybit-funding-history-provenance-v1"
FUNDING_CHARGE_RULE_V1: Final = "bybit-funding-charge-v1"
FUNDING_HISTORY_URL_PREFIX_V1: Final = "https://api.bybit.com/v5/market/funding/history?category=linear&"
FUNDING_PAGE_LIMIT_V1: Final = 200

COMPLETE: Final = "COMPLETE"
IRREGULAR_REFUSED: Final = "IRREGULAR_REFUSED"

PUBLISHED_SEMANTICS_V1: Final = {
    "payer_rule": "POSITIVE_RATE_LONGS_PAY_SHORTS_NEGATIVE_RATE_SHORTS_PAY_LONGS",
    "fee_formula": "FEE_EQUALS_POSITION_VALUE_TIMES_RATE_POSITION_VALUE_EQUALS_QUANTITY_TIMES_MARK_PRICE",
    "flat_rule": "A_POSITION_FULLY_CLOSED_BEFORE_THE_FUNDING_TIME_NEITHER_PAYS_NOR_RECEIVES",
    "interval": "PER_SYMBOL_MAY_CHANGE_DYNAMICALLY_WITHOUT_SEPARATE_ANNOUNCEMENT",
    "history_record": "SETTLED_RATE_AT_fundingRateTimestamp_SERVED_ONLY_AFTER_SETTLEMENT",
    "sources": [
        "https://www.bybit.com/en/help-center/article/Funding-fee-calculation",
        "https://www.bybit.com/en/help-center/article/Introduction-to-Funding-Rate",
        "https://bybit-exchange.github.io/docs/v5/market/history-fund-rate",
    ],
    "read_on": "2026-10-09",
}
CALCULATED_SEMANTICS_V1: Final = {
    "rule": FUNDING_CHARGE_RULE_V1,
    "exposure_model": "STRATEGY_LAB_UNIT_EQUITY_FRACTION_PER_OPEN_TO_OPEN_INTERVAL",
    "position_value_reference": "BAR_OPEN_AT_THE_FUNDING_INSTANT_FIRST_PRINT_AT_OR_AFTER_IT_AS_MARK_PRICE_PROXY",
    "flat_at_the_instant": "NOT_CHARGED",
    "position_change_filled_at_the_funding_instant_open": "AMBIGUOUS_WORSE_OUTCOME_CHARGED_ONCE",
    "instant_inside_a_missing_bar": "FLAT_NOT_CHARGED_NON_FLAT_NOT_COSTABLE_FAIL_CLOSED",
    "completeness": "ONE_UNIFORM_PUBLISHED_SPACING_AND_TIGHT_WINDOW_EDGES_ELSE_REFUSED",
    "evidence_tier": "T2_EVENT_TIME_OUTCOME_ONLY_NEVER_A_DECISION_INPUT",
}

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.bybit_funding_history_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_DAY_MS: Final = 86_400_000
_FIELDS: Final = frozenset({"symbol", "fundingRate", "fundingRateTimestamp"})
_RATE_TEXT: Final = re.compile(r"-?[0-9]+(\.[0-9]+)?", re.ASCII)

#: ``(url, headers) -> response``. Injected so every rule is testable offline.
FundingFetchV1 = Callable[[str, Mapping[str, str]], HttpResponseV1]


class FundingHistoryError(ValueError):
    """Raised when funding evidence is malformed, incomplete or cannot be applied honestly."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _day_start_ms(day: date) -> int:
    return (datetime(day.year, day.month, day.day, tzinfo=UTC) - _EPOCH) // timedelta(milliseconds=1)


def _iso_ms(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()


def funding_history_url_v1(symbol: str, start_ms: int, end_ms: int) -> str:
    if not symbol.isalnum() or not symbol.isupper():
        raise FundingHistoryError("funding_symbol_malformed")
    return (f"{FUNDING_HISTORY_URL_PREFIX_V1}symbol={symbol}&startTime={start_ms}&endTime={end_ms}"
            f"&limit={FUNDING_PAGE_LIMIT_V1}")


def urllib_funding_fetch_v1(url: str, headers: Mapping[str, str]) -> HttpResponseV1:
    """The one network call: an HTTPS GET of the public linear funding-history endpoint only."""
    import urllib.error
    import urllib.request

    if not url.startswith(FUNDING_HISTORY_URL_PREFIX_V1):
        raise FundingHistoryError("fetch_outside_the_public_funding_history_endpoint")
    request = urllib.request.Request(url, headers=dict(headers))  # nosec B310 - fixed https host
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310 - https only
            return HttpResponseV1(response.status, {k.lower(): v for k, v in response.headers.items()},
                                  response.read())
    except urllib.error.HTTPError as error:
        return HttpResponseV1(error.code, {k.lower(): v for k, v in error.headers.items()}, b"")


# ---------------------------------------------------------------------------
# Strict parse
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundingEventV1:
    """One settled funding record exactly as published: settlement instant (ms) and rate text."""

    settled_at_ms: int
    rate_text: str

    @property
    def rate(self) -> Decimal:
        return Decimal(self.rate_text)


def parse_funding_page_v1(body: bytes, *, symbol: str, start_ms: int, end_ms: int) -> list[FundingEventV1]:
    """Strict parse of one page. Raises on the first defect; returns ascending settlement instants."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FundingHistoryError("funding_body_not_json") from error
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise FundingHistoryError("funding_ret_code_not_zero")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("category") != "linear":
        raise FundingHistoryError("funding_result_is_not_linear")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise FundingHistoryError("funding_list_missing")
    if len(rows) >= FUNDING_PAGE_LIMIT_V1:
        raise FundingHistoryError("funding_page_may_be_truncated")
    events: dict[int, FundingEventV1] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise FundingHistoryError("funding_row_fields_mismatch")
        if row["symbol"] != symbol:
            raise FundingHistoryError("funding_row_is_not_the_requested_symbol")
        stamp, rate = row["fundingRateTimestamp"], row["fundingRate"]
        if not isinstance(stamp, str) or not (stamp.isascii() and stamp.isdigit()):
            raise FundingHistoryError("funding_timestamp_malformed")
        settled = int(stamp)
        if not start_ms <= settled <= end_ms:
            raise FundingHistoryError("funding_timestamp_outside_the_requested_window")
        if settled in events:
            raise FundingHistoryError("funding_timestamp_repeated")
        if not isinstance(rate, str) or not _RATE_TEXT.fullmatch(rate):
            raise FundingHistoryError("funding_rate_malformed")  # plain ASCII decimal only, kept verbatim
        events[settled] = FundingEventV1(settled, rate)
    return [events[key] for key in sorted(events)]


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundingHistoryV1:
    """A content-addressed window of published settled funding for one symbol."""

    identity: Mapping[str, Any]
    content_hash: str
    provenance: Mapping[str, Any]
    provenance_hash: str

    @property
    def dataset_version_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"bybit-funding-history:{self.content_hash}")

    @property
    def symbol(self) -> str:
        return str(self.identity["symbol"])

    @property
    def status(self) -> str:
        return str(self.identity["completeness"]["status"])

    @property
    def window_ms(self) -> tuple[int, int]:
        return _day_start_ms(date.fromisoformat(self.identity["first_utc_day"])), _day_start_ms(
            date.fromisoformat(self.identity["last_utc_day"])) + _DAY_MS

    def events(self) -> list[FundingEventV1]:
        return [FundingEventV1(int(item["settled_at_ms"]), str(item["rate"])) for item in self.identity["events"]]


def _completeness(events: Sequence[FundingEventV1], start_ms: int, end_ms: int) -> dict[str, Any]:
    if not events:
        return {"status": IRREGULAR_REFUSED, "interval_ms": None, "irregularities": ["NO_RECORD_IN_WINDOW"]}
    spacings = [b.settled_at_ms - a.settled_at_ms for a, b in pairwise(events)]
    distinct = sorted(set(spacings))
    irregular: list[str] = []
    if len(distinct) > 1:
        irregular += [f"SPACING:{_iso_ms(a.settled_at_ms)}->{_iso_ms(b.settled_at_ms)}:{b.settled_at_ms - a.settled_at_ms}"
                      for a, b in pairwise(events) if b.settled_at_ms - a.settled_at_ms != distinct[0]]
    interval = distinct[0] if len(distinct) == 1 else None
    if interval is None and len(events) == 1:
        irregular.append("ONE_RECORD_PROVES_NO_INTERVAL")
    if interval is not None:
        if events[0].settled_at_ms - start_ms >= interval:
            irregular.append(f"LEADING_EDGE_NOT_PROVEN:{_iso_ms(events[0].settled_at_ms)}")
        if end_ms - events[-1].settled_at_ms > interval:
            irregular.append(f"TRAILING_EDGE_NOT_PROVEN:{_iso_ms(events[-1].settled_at_ms)}")
    return {"status": IRREGULAR_REFUSED if irregular else COMPLETE,
            "interval_ms": interval if not irregular else None, "irregularities": irregular}


def _window_urls(symbol: str, first: date, last: date) -> list[str]:
    """Exactly one canonical page per UTC day of ``[first, last]``, in order."""
    return [funding_history_url_v1(symbol, _day_start_ms(first) + i * _DAY_MS,
                                   _day_start_ms(first) + (i + 1) * _DAY_MS - 1)
            for i in range((last - first).days + 1)]


def _build(symbol: str, first: date, last: date, pages: Sequence[tuple[str, bytes]]) -> FundingHistoryV1:
    if [url for url, _ in pages] != _window_urls(symbol, first, last):
        raise FundingHistoryError("funding_pages_are_not_exactly_the_window_days")
    events: list[FundingEventV1] = []
    for url, body in pages:
        day_ms = int(url.split("startTime=")[1].split("&")[0])
        events.extend(parse_funding_page_v1(body, symbol=symbol, start_ms=day_ms, end_ms=day_ms + _DAY_MS - 1))
    start_ms = _day_start_ms(first)
    end_ms = _day_start_ms(last) + _DAY_MS
    identity = {
        "schema_version": FUNDING_HISTORY_SCHEMA_VERSION_V1,
        "venue": "BYBIT",
        "category": "linear",
        "symbol": symbol,
        "first_utc_day": first.isoformat(),
        "last_utc_day": last.isoformat(),
        "evidence_tier": "T2_EVENT_TIME",
        "event_time": "VENUE_SETTLEMENT_INSTANT_fundingRateTimestamp",
        "knowledge_time": "NOT_PUBLISHED_BY_THE_SOURCE_RETROSPECTIVE_OUTCOME_ONLY",
        "published_semantics": PUBLISHED_SEMANTICS_V1,
        "events": [{"settled_at_ms": e.settled_at_ms, "settled_at": _iso_ms(e.settled_at_ms), "rate": e.rate_text}
                   for e in events],
        "completeness": _completeness(events, start_ms, end_ms),
    }
    provenance = {
        "schema_version": FUNDING_PROVENANCE_SCHEMA_VERSION_V1,
        "endpoint": FUNDING_HISTORY_URL_PREFIX_V1,
        "pages": [{"url": url, "sha256": _sha256(body)} for url, body in pages],
    }
    return FundingHistoryV1(identity, identity_hash_v1(identity), provenance, identity_hash_v1(provenance))


def _page_path(root: Path, symbol: str, day: date) -> Path:
    return root / "funding-history" / "bybit-v5" / f"symbol={symbol}" / f"{symbol}{day.isoformat()}.json"


def _dataset_path(root: Path, dataset_version_id: UUID) -> Path:
    return root / "datasets" / "bybit-funding-history" / f"{dataset_version_id}.json"


def acquire_funding_history_v1(
    root: Path, symbol: str, first: date, last: date, *, holdout_opening: Any = None,
    fetch: FundingFetchV1 = urllib_funding_fetch_v1, now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FundingHistoryV1:
    """Acquire (or re-read) one window of closed UTC days and catalogue it under ``root``."""
    days = _days(first, last, holdout_opening)  # refuses the untouched holdout unless an opening admits each day
    if datetime(last.year, last.month, last.day, tzinfo=UTC) + timedelta(days=1) > now():
        raise FundingHistoryError("funding_window_day_not_closed")
    pages: list[tuple[str, bytes]] = []
    for day in days:
        start_ms = _day_start_ms(day)
        url = funding_history_url_v1(symbol, start_ms, start_ms + _DAY_MS - 1)
        path = _page_path(root, symbol, day)
        if path.exists():
            body = path.read_bytes()
        else:
            response = fetch(url, {"Accept": "application/json"})
            if response.status != 200:
                raise FundingHistoryError(f"funding_http_status:{response.status}")
            body = response.body
            parse_funding_page_v1(body, symbol=symbol, start_ms=start_ms, end_ms=start_ms + _DAY_MS - 1)
            path.parent.mkdir(parents=True, exist_ok=True)
            staged = path.with_suffix(".tmp")
            staged.write_bytes(body)
            os.replace(staged, path)
        pages.append((url, body))
    dataset = _build(symbol, first, last, pages)
    target = _dataset_path(root, dataset.dataset_version_id)
    if not target.exists():  # the first record of an identity is kept; a re-acquisition never rewrites it
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_suffix(".tmp")
        staged.write_text(json.dumps({"identity": dataset.identity, "content_hash": dataset.content_hash,
                                      "provenance": dataset.provenance, "provenance_hash": dataset.provenance_hash},
                                     sort_keys=True, indent=1), encoding="utf-8")
        os.replace(staged, target)
    return load_funding_history_v1(root, dataset.dataset_version_id, holdout_opening=holdout_opening)


def load_funding_history_v1(root: Path, dataset_version_id: UUID, *, holdout_opening: Any = None) -> FundingHistoryV1:
    """A catalogued window, re-proven: every raw page by SHA-256, re-parsed, both hashes re-derived.

    Holdout days are gated on read as on acquisition: a catalogued holdout window
    loads only with a registry-issued opening that admits each of its days.
    """
    path = _dataset_path(root, dataset_version_id)
    if not path.exists():
        raise FundingHistoryError("funding_history_dataset_not_found")
    stored = json.loads(path.read_text(encoding="utf-8"))
    identity = stored["identity"]
    symbol = str(identity["symbol"])
    first, last = date.fromisoformat(identity["first_utc_day"]), date.fromisoformat(identity["last_utc_day"])
    _days(first, last, holdout_opening)
    pages: list[tuple[str, bytes]] = []
    for item in stored["provenance"]["pages"]:
        day_ms = int(str(item["url"]).split("startTime=")[1].split("&")[0])
        body = _page_path(root, symbol, (_EPOCH + timedelta(milliseconds=day_ms)).date()).read_bytes()
        if _sha256(body) != item["sha256"]:
            raise FundingHistoryError("funding_raw_page_changed")
        pages.append((str(item["url"]), body))
    rebuilt = _build(symbol, first, last, pages)
    if (rebuilt.content_hash, rebuilt.provenance_hash) != (stored["content_hash"], stored["provenance_hash"]) or (
        rebuilt.dataset_version_id != dataset_version_id
    ):
        raise FundingHistoryError("funding_history_dataset_does_not_reproduce")
    return rebuilt


# ---------------------------------------------------------------------------
# Charge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundingStepsV1:
    """Per-interval funding as a fraction of the interval's starting equity, plus what was decided."""

    steps: tuple[Decimal, ...]
    events_in_span: int
    charged: int
    flat: int
    ambiguous_worse_charged: int
    not_costable: tuple[str, ...]


def require_covering_complete_v1(history: FundingHistoryV1, bars: BarsV1, symbol: str) -> None:
    """Refuse unless ``history`` is this symbol's COMPLETE window over the whole bar span."""
    if history.symbol != symbol:
        raise FundingHistoryError("funding_history_is_not_this_symbol")
    if history.status != COMPLETE:
        raise FundingHistoryError(f"funding_history_not_complete:{history.identity['completeness']['irregularities']}")
    start_ms, end_ms = history.window_ms
    if int(bars.open_us[0]) < start_ms * 1000 or int(bars.close_us[-1]) > end_ms * 1000:
        raise FundingHistoryError("funding_history_does_not_cover_the_evaluated_span")


def funding_steps_v1(
    bars: BarsV1, held: Sequence[int], history: FundingHistoryV1, *, cost_fraction: Decimal,
) -> FundingStepsV1:
    """Apply :data:`FUNDING_CHARGE_RULE_V1`. Caller holds the authoritative Decimal context.

    ``cost_fraction`` is the per-side cost as a fraction (``bps / 10_000``); it
    only decides which outcome of an ambiguous boundary is the worse one.
    """
    n = bars.size
    if len(held) != n:
        raise FundingHistoryError("held_positions_must_match_bars")
    opens = [int(v) for v in bars.open_us]
    span_start, span_end = opens[0], int(bars.close_us[-1])
    steps = [Decimal(0)] * n
    in_span = charged = flat = ambiguous = 0
    not_costable: list[str] = []
    for event in history.events():
        at = event.settled_at_ms * 1000
        if not span_start <= at < span_end:
            continue
        in_span += 1
        rate = event.rate
        k = bisect_left(opens, at)
        if k < n and opens[k] == at:
            before = held[k - 1] if k else 0  # no carry-in: the evaluated span starts flat
            after = held[k]
            if before == 0 and after == 0:
                flat += 1
            elif before == after:
                steps[k - 1] -= before * rate * bars.open_d[k] / bars.open_d[k - 1]
                charged += 1
            else:
                ambiguous += 1
                # Both outcomes in currency per unit of interval k-1's starting equity.
                pay_before = before * rate * bars.open_d[k] / bars.open_d[k - 1] if before else Decimal(0)
                growth = Decimal(1)
                if k:
                    previous = held[k - 2] if k >= 2 else 0
                    growth += held[k - 1] * (bars.open_d[k] / bars.open_d[k - 1] - 1) - cost_fraction * abs(
                        held[k - 1] - previous)
                pay_after = after * rate * growth if after else Decimal(0)
                # Either outcome is a cash flow at T, booked on interval k-1 so it compounds from T exactly.
                steps[k - 1 if k else 0] -= max(pay_before, pay_after)
        else:
            j = k - 1  # span_start <= at and at is not a bar open, so j >= 0
            if held[j] == 0:
                flat += 1
            else:
                not_costable.append(f"NO_BAR_AT_FUNDING_INSTANT_WHILE_HELD:{_iso_ms(event.settled_at_ms)}")
    return FundingStepsV1(tuple(steps), in_span, charged, flat, ambiguous, tuple(not_costable))


__all__ = [
    "CALCULATED_SEMANTICS_V1",
    "COMPLETE",
    "FUNDING_CHARGE_RULE_V1",
    "FUNDING_HISTORY_SCHEMA_VERSION_V1",
    "IRREGULAR_REFUSED",
    "PUBLISHED_SEMANTICS_V1",
    "FundingEventV1",
    "FundingHistoryError",
    "FundingHistoryV1",
    "FundingStepsV1",
    "acquire_funding_history_v1",
    "funding_history_url_v1",
    "funding_steps_v1",
    "load_funding_history_v1",
    "parse_funding_page_v1",
    "require_covering_complete_v1",
    "urllib_funding_fetch_v1",
]
