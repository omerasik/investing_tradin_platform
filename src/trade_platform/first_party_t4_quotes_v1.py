"""Phase R3A.2 -- first-party T4 top-of-book and funding state from a sealed segment.

``RESEARCH_ONLY``. Derives two provider-published quote frames from exactly the
records a :class:`~trade_platform.first_party_t4_seal_v1.FirstPartyT4SealV1`
admitted, under exactly the clock bounds it admitted them with
(:func:`~trade_platform.first_party_t4_seal_v1.iter_admitted_segment_records_v1`).
It changes nothing about the seal: the seal's identity, frames and tier verdict
are untouched, and this module issues no verdict, no provenance and no
authority of its own. A quote dataset is a *sidecar*: its identity binds the
parent seal's content hash, and it exists only for a raw-replayed seal.

What the frames are -- and are not
----------------------------------
Both come from Bybit's V5 ``tickers`` stream, whose fields the venue publishes
conflated (one message per push interval, carrying only changed fields). So:

* ``T4_TOP_OF_BOOK`` is the venue-*published* level-1 quote (``bid1Price``,
  ``bid1Size``, ``ask1Price``, ``ask1Size``) as it stood after each message that
  changed it. It is not an order book, not every book change, and says nothing
  about depth beyond level 1. A spread measured from it is a measured
  level-1 spread at message granularity -- a cost *input*, never a fill model.
* ``T4_FUNDING`` is the venue-*published* funding state (``fundingRate``,
  ``nextFundingTime``, ``fundingIntervalHour``, ``fundingCap``) as observed. The
  published rate is the venue's figure for the current interval as seen at
  arrival; it is **not** a settled payment, and which published value settles
  is not inferred here.

State rules are the seal's: a ``snapshot`` replaces state (a field it does not
carry becomes unobserved), a ``delta`` changes only the fields it carries, and
state starts empty in every segment -- nothing crosses a gap. A row is emitted
only by a record that carried at least one of its fields, and only once every
field of the row is observed in the current segment. An empty level (an empty
price or a zero size) makes that side unobserved; it is counted, never filled.
A locked or crossed published quote is kept and labelled (``book_state``), never
dropped or repaired.

Every row keeps its emitting record's arrival and clock bound. Its
``market_knowledge_at`` is the latest doctrine knowledge time over the records
that set the fields it carries -- the seal's rule for combined state (a basis
takes ``max(mark, index)``) -- so a row is never known earlier than any value in
it, even where the session clock bound shrinks between consecutive records.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .first_party_capture_archive_v1 import FirstPartyCaptureRecordV1
from .first_party_capture_authority_v1 import (
    BybitMessageTypeV1,
    BybitPublicChannelV1,
    FirstPartyCaptureContractV1,
    first_party_bybit_capture_contract_v1,
)
from .first_party_t4_normalization_v1 import (
    ArrivalClockBoundEvidenceV1,
    T4ArrivalV1,
    micros_to_datetime_v1,
    sha256_json_v1,
)
from .first_party_t4_seal_v1 import (
    FirstPartyT4SealV1,
    iter_admitted_segment_records_v1,
    plan_for_sealed_identity_v1,
)
from .research_data_plane_v1 import (
    T4_FUNDING_FRAME,
    T4_TOP_OF_BOOK_FRAME,
    FrameSchemaV1,
    ResearchFrameStoreV1,
    logical_content_hash_v1,
)

T4_QUOTES_SEMANTIC_VERSION_V1: Final = "first-party-t4-quotes-1.0.0"
T4_QUOTES_SCHEMA_VERSION_V1: Final = "first-party-t4-quotes-v1"
TOP_OF_BOOK_SEMANTICS_V1: Final = "bybit-v5-tickers-published-level1-conflated"
FUNDING_SEMANTICS_V1: Final = "bybit-v5-tickers-published-funding-state-not-settlement"
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.first_party_t4_quotes_v1")

BOOK_NORMAL: Final = "NORMAL"
BOOK_LOCKED: Final = "LOCKED"
BOOK_CROSSED: Final = "CROSSED"

_BOOK_FIELDS: Final = ("bid1Price", "bid1Size", "ask1Price", "ask1Size")
_FUNDING_FIELDS: Final = ("fundingRate", "nextFundingTime", "fundingIntervalHour", "fundingCap")

T4_QUOTE_FRAMES_V1: Final[tuple[FrameSchemaV1, ...]] = (T4_TOP_OF_BOOK_FRAME, T4_FUNDING_FRAME)


class T4QuotesError(ValueError):
    """Raised when a quote sidecar cannot be derived or does not reproduce."""


def _text(raw: object, name: str) -> str:
    if isinstance(raw, bool) or not isinstance(raw, str | int):
        raise T4QuotesError(f"{name}_malformed")
    return str(raw).strip()


def _decimal(raw: object, name: str) -> Decimal:
    text = _text(raw, name)
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise T4QuotesError(f"{name}_malformed") from error
    if not value.is_finite():
        raise T4QuotesError(f"{name}_not_finite")
    return value


def _level_value(raw: object, name: str, *, is_price: bool) -> Decimal | None:
    """A level-1 price or size; ``None`` when the venue publishes the level as empty.

    An empty text or a zero *size* is an empty level. A zero or negative price is
    malformed, never an empty level.
    """
    if _text(raw, name) == "":
        return None
    value = _decimal(raw, name)
    if value < 0 or (is_price and value == 0):
        raise T4QuotesError(f"{name}_not_a_valid_level_value")
    return None if value == 0 else value


def _positive_int(raw: object, name: str) -> int:
    text = _text(raw, name)
    if not text.isdigit() or int(text) <= 0:
        raise T4QuotesError(f"{name}_not_a_positive_integer")
    return int(text)


@dataclass(frozen=True, slots=True)
class T4TopOfBookV1:
    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal
    exchange_ts_millis: int
    message_type: str
    arrival: T4ArrivalV1
    #: The latest doctrine knowledge time over the records that set the four
    #: fields -- never earlier than any value the row carries.
    market_knowledge_micros: int

    @property
    def book_state(self) -> str:
        if self.bid_price < self.ask_price:
            return BOOK_NORMAL
        return BOOK_LOCKED if self.bid_price == self.ask_price else BOOK_CROSSED

    @property
    def observation_reference(self) -> str:
        return f"t4-tob:{self.arrival.record_content_hash}"


@dataclass(frozen=True, slots=True)
class T4FundingStateV1:
    published_funding_rate: Decimal
    next_funding_time_millis: int
    funding_interval_hours: int
    funding_cap: Decimal
    exchange_ts_millis: int
    message_type: str
    arrival: T4ArrivalV1
    market_knowledge_micros: int

    @property
    def observation_reference(self) -> str:
        return f"t4-funding:{self.arrival.record_content_hash}"


@dataclass(slots=True)
class T4QuoteCountsV1:
    records: int = 0
    ticker_records: int = 0
    trade_records_ignored: int = 0
    book_field_records: int = 0
    top_of_book_rows: int = 0
    book_records_before_state_complete: int = 0
    empty_level_updates: int = 0
    locked_rows: int = 0
    crossed_rows: int = 0
    funding_field_records: int = 0
    funding_rows: int = 0
    funding_records_before_state_complete: int = 0

    def payload(self) -> dict[str, int]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


class T4QuoteNormalizerV1:
    """Record-at-a-time level-1 and funding state for one segment (replay or live)."""

    def __init__(self, *, exchange_symbol: str, session_id: UUID) -> None:
        if not exchange_symbol.strip():
            raise T4QuotesError("exchange_symbol_required")
        self._symbol = exchange_symbol
        self._session_id = session_id
        # Each observed field keeps its value and the knowledge time of the record that set it.
        self._book: dict[str, tuple[Decimal, int] | None] = dict.fromkeys(_BOOK_FIELDS)
        self._funding: dict[str, tuple[Decimal, int] | None] = dict.fromkeys(_FUNDING_FIELDS)
        self._book_complete = False
        self._last_sequence: int | None = None
        self._last_arrival: int | None = None
        self._last_ts: int | None = None
        self._last_cs: int | None = None
        self.counts = T4QuoteCountsV1()
        self.top_of_book: list[T4TopOfBookV1] = []
        self.funding: list[T4FundingStateV1] = []
        #: Arrivals at which a complete level-1 book stopped being fully observed.
        #: A quote stands at most until the next of these (see describe_level1_spread_v1).
        self.book_unobserved_from: list[int] = []

    def feed(self, record: FirstPartyCaptureRecordV1, bound: ArrivalClockBoundEvidenceV1) -> None:
        if record.session_id != self._session_id:
            raise T4QuotesError("record_from_another_session")
        if self._last_sequence is not None and record.sequence <= self._last_sequence:
            raise T4QuotesError("record_sequence_not_increasing")
        if self._last_arrival is not None and record.arrival_utc_nanos < self._last_arrival:
            raise T4QuotesError("record_arrival_regressed")
        self._last_sequence = record.sequence
        self._last_arrival = record.arrival_utc_nanos
        self.counts.records += 1
        if record.channel == BybitPublicChannelV1.PUBLIC_TRADE.value:
            self.counts.trade_records_ignored += 1
            return
        if record.channel != BybitPublicChannelV1.TICKERS.value:
            raise T4QuotesError("record_channel_not_normalizable")
        self.counts.ticker_records += 1
        payload = json.loads(record.payload_text)
        ts = payload.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, int) or ts != record.exchange_timestamp_millis:
            raise T4QuotesError("record_exchange_timestamp_disagrees_with_payload")
        arrival = T4ArrivalV1(
            session_id=record.session_id,
            record_sequence=record.sequence,
            record_content_hash=record.content_hash,
            arrival_utc_nanos=record.arrival_utc_nanos,
            clock_bound_nanos=bound.venue_minus_host_upper_bound_nanos,
            clock_bound_evidence=bound.evidence_reference,
        )
        if ts * 1_000 > arrival.market_knowledge_micros:
            raise T4QuotesError("clock_bound_places_knowledge_before_venue_event")
        if self._last_ts is not None and ts < self._last_ts:
            raise T4QuotesError("ticker_ts_regressed_within_segment")
        self._last_ts = ts
        cs = payload.get("cs")
        if cs is not None:
            if isinstance(cs, bool) or not isinstance(cs, int):
                raise T4QuotesError("ticker_cross_sequence_malformed")
            if self._last_cs is not None and cs < self._last_cs:
                raise T4QuotesError("ticker_cross_sequence_regressed")
            self._last_cs = cs
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("symbol") != self._symbol:
            raise T4QuotesError("ticker_payload_malformed")
        message_type = str(record.message_type)
        if message_type == BybitMessageTypeV1.SNAPSHOT.value:
            self._book = dict.fromkeys(_BOOK_FIELDS)
            self._funding = dict.fromkeys(_FUNDING_FIELDS)
        elif message_type != BybitMessageTypeV1.DELTA.value:
            raise T4QuotesError("ticker_message_type_not_recognized")
        self._feed_book(data, ts, message_type, arrival)
        self._feed_funding(data, ts, message_type, arrival)

    def _feed_book(self, data: Mapping[str, Any], ts: int, message_type: str, arrival: T4ArrivalV1) -> None:
        carried = [name for name in _BOOK_FIELDS if name in data]
        if not carried and not all(self._book.values()):
            # A snapshot without level-1 fields emptied a complete book.
            self._mark_incomplete(arrival)
        if not carried:
            return
        self.counts.book_field_records += 1
        knowledge = arrival.market_knowledge_micros
        for name in carried:
            value = _level_value(data[name], f"ticker_{name}", is_price=name.endswith("Price"))
            if value is None:
                self.counts.empty_level_updates += 1
            self._book[name] = None if value is None else (value, knowledge)
        bid, bid_size = self._book["bid1Price"], self._book["bid1Size"]
        ask, ask_size = self._book["ask1Price"], self._book["ask1Size"]
        if bid is None or bid_size is None or ask is None or ask_size is None:
            self.counts.book_records_before_state_complete += 1
            self._mark_incomplete(arrival)
            return
        self._book_complete = True
        quote = T4TopOfBookV1(
            bid[0], bid_size[0], ask[0], ask_size[0], ts, message_type, arrival,
            market_knowledge_micros=max(bid[1], bid_size[1], ask[1], ask_size[1]),
        )
        state = quote.book_state
        if state == BOOK_LOCKED:
            self.counts.locked_rows += 1
        elif state == BOOK_CROSSED:
            self.counts.crossed_rows += 1
        self.top_of_book.append(quote)
        self.counts.top_of_book_rows += 1

    def _feed_funding(self, data: Mapping[str, Any], ts: int, message_type: str, arrival: T4ArrivalV1) -> None:
        carried = [name for name in _FUNDING_FIELDS if name in data]
        if not carried:
            return
        self.counts.funding_field_records += 1
        knowledge = arrival.market_knowledge_micros
        for name in carried:
            raw = data[name]
            if _text(raw, name) == "":
                self._funding[name] = None
            elif name in ("fundingRate", "fundingCap"):
                self._funding[name] = (_decimal(raw, f"ticker_{name}"), knowledge)
            else:  # integers, held exactly as Decimal and converted back on emission
                self._funding[name] = (Decimal(_positive_int(raw, f"ticker_{name}")), knowledge)
        rate, next_time = self._funding["fundingRate"], self._funding["nextFundingTime"]
        interval, cap = self._funding["fundingIntervalHour"], self._funding["fundingCap"]
        if rate is None or next_time is None or interval is None or cap is None:
            self.counts.funding_records_before_state_complete += 1
            return
        self.funding.append(
            T4FundingStateV1(
                rate[0], int(next_time[0]), int(interval[0]), cap[0], ts, message_type, arrival,
                market_knowledge_micros=max(rate[1], next_time[1], interval[1], cap[1]),
            )
        )
        self.counts.funding_rows += 1

    def _mark_incomplete(self, arrival: T4ArrivalV1) -> None:
        if self._book_complete:
            self.book_unobserved_from.append(arrival.arrival_utc_nanos)
            self._book_complete = False


def _arrival_cells(arrival: T4ArrivalV1, market_knowledge_micros: int) -> tuple[object, ...]:
    """The emitting record's clocks, with the row's own (max over its fields) knowledge time."""
    return (
        str(arrival.session_id), arrival.record_sequence, arrival.record_content_hash,
        arrival.arrival_utc_nanos, arrival.clock_bound_nanos, arrival.clock_bound_evidence,
        micros_to_datetime_v1(market_knowledge_micros),
    )


def _top_of_book_rows(items: Sequence[T4TopOfBookV1]) -> Iterator[tuple[object, ...]]:
    for item in items:
        yield (
            item.observation_reference, item.message_type, item.exchange_ts_millis,
            micros_to_datetime_v1(item.exchange_ts_millis * 1_000),
            *_arrival_cells(item.arrival, item.market_knowledge_micros),
            item.bid_price, item.bid_size, item.ask_price, item.ask_size, item.book_state,
        )


def _funding_rows(items: Sequence[T4FundingStateV1]) -> Iterator[tuple[object, ...]]:
    for item in items:
        yield (
            item.observation_reference, item.message_type, item.exchange_ts_millis,
            micros_to_datetime_v1(item.exchange_ts_millis * 1_000),
            *_arrival_cells(item.arrival, item.market_knowledge_micros),
            item.published_funding_rate, micros_to_datetime_v1(item.next_funding_time_millis * 1_000),
            item.funding_interval_hours, item.funding_cap,
        )


@dataclass(frozen=True, slots=True)
class T4QuoteDatasetV1:
    """A derived, deterministic quote sidecar of one raw-replayed T4 seal."""

    identity: Mapping[str, Any]
    content_hash: str
    quote_dataset_id: UUID
    parent_dataset_version_id: UUID
    frame_manifests: Mapping[str, str]
    top_of_book: tuple[T4TopOfBookV1, ...] = field(repr=False)
    funding: tuple[T4FundingStateV1, ...] = field(repr=False)
    book_unobserved_from: tuple[int, ...] = field(repr=False)


def derive_t4_quotes_v1(
    seal: FirstPartyT4SealV1,
    *,
    capture_root: Path,
    store: ResearchFrameStoreV1 | None = None,
    contract: FirstPartyCaptureContractV1 | None = None,
) -> T4QuoteDatasetV1:
    """Derive the quote sidecar of ``seal`` from raw capture; write frames when ``store`` is given.

    Refuses a seal that did not come out of a raw rebuild, and refuses when the
    re-admitted record set differs from the one the seal binds (count and first
    and last sequence). Without a store only the logical hashes are computed, so
    a rebuild can be compared with an earlier derivation without writing.
    """
    if not seal.integrity_verified() or not seal.raw_replayed:
        raise T4QuotesError("quotes_require_a_raw_replayed_seal")
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    plan = plan_for_sealed_identity_v1(seal.identity, capture_root=capture_root, contract=authorized)
    normalizer = T4QuoteNormalizerV1(exchange_symbol=authorized.exchange_symbol, session_id=plan.partition.session_id)
    admitted = 0
    first_sequence = last_sequence = -1
    for record, bound in iter_admitted_segment_records_v1(plan, contract=authorized):
        normalizer.feed(record, bound)
        admitted += 1
        if first_sequence < 0:
            first_sequence = record.sequence
        last_sequence = record.sequence
    segment = seal.identity["segment"]
    if (admitted, first_sequence, last_sequence) != (
        segment["admitted_records"], segment["first_record_sequence"], segment["last_record_sequence"]
    ):
        raise T4QuotesError("quote_admission_differs_from_the_sealed_segment")
    rows = {
        T4_TOP_OF_BOOK_FRAME.kind: lambda: _top_of_book_rows(normalizer.top_of_book),
        T4_FUNDING_FRAME.kind: lambda: _funding_rows(normalizer.funding),
    }
    frames: dict[str, tuple[str, int]] = {}
    manifests: dict[str, str] = {}
    for frame in T4_QUOTE_FRAMES_V1:
        if store is None:
            frames[frame.kind] = logical_content_hash_v1(frame, rows[frame.kind]())
            continue
        manifest = store.write_frame(
            frame, rows[frame.kind](),
            lineage={"parent_seal_content_hash": seal.content_hash, "frame": frame.kind,
                     "quotes_semantic_version": T4_QUOTES_SEMANTIC_VERSION_V1},
        )
        frames[frame.kind] = (manifest.logical_content_hash, manifest.row_count)
        manifests[frame.kind] = manifest.manifest_hash
    identity = {
        "schema_version": T4_QUOTES_SCHEMA_VERSION_V1,
        "quotes_semantic_version": T4_QUOTES_SEMANTIC_VERSION_V1,
        "top_of_book_semantics": TOP_OF_BOOK_SEMANTICS_V1,
        "funding_semantics": FUNDING_SEMANTICS_V1,
        "parent_seal_content_hash": seal.content_hash,
        "parent_dataset_version_id": str(seal.dataset_version_id),
        "frames": {kind: {"logical_content_hash": value[0], "row_count": value[1]}
                   for kind, value in sorted(frames.items())},
        "counts": dict(sorted(normalizer.counts.payload().items())),
        "book_unobserved_from_arrival_nanos": list(normalizer.book_unobserved_from),
    }
    content_hash = sha256_json_v1(identity)
    return T4QuoteDatasetV1(
        identity=identity,
        content_hash=content_hash,
        quote_dataset_id=uuid5(_NAMESPACE, f"first-party-t4-quotes:{content_hash}"),
        parent_dataset_version_id=seal.dataset_version_id,
        frame_manifests=dict(sorted(manifests.items())),
        top_of_book=tuple(normalizer.top_of_book),
        funding=tuple(normalizer.funding),
        book_unobserved_from=tuple(normalizer.book_unobserved_from),
    )


# ---------------------------------------------------------------------------
# Descriptive level-1 spread evidence (an OR-6 input; no cost model)
# ---------------------------------------------------------------------------

_QUANTILES: Final = ("0.5", "0.9", "0.99")
_BPS: Final = Decimal(10_000)


def _nearest_rank(pairs: Sequence[tuple[Decimal, int]], q: Decimal) -> Decimal:
    """Weighted nearest-rank quantile over ``(value, weight)``; exact, no interpolation."""
    ordered = sorted(pairs)
    total = sum(weight for _, weight in ordered)
    target = q * total
    running = 0
    for value, weight in ordered:
        running += weight
        if running >= target:
            return value
    return ordered[-1][0]


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def describe_level1_spread_v1(
    quotes: Sequence[T4TopOfBookV1], *, unobserved_from: Sequence[int], tick_size: Decimal
) -> dict[str, Any]:
    """Level-1 spread and size distributions of ONE segment, by message and by time the quote stood.

    A quote *stands* from its arrival until the earlier of the next quote's
    arrival and the next arrival at which the book stopped being fully observed
    (``unobserved_from``, from the same normalizer); the last quote has no
    observed end and carries no time weight. Quotes must come from one session in
    strictly increasing record order, so no weight can span a gap between
    segments. Locked and crossed quotes are counted and excluded from the spread
    distributions. Relative spread uses the mid ``(bid + ask) / 2``. Quantiles
    are exact nearest-rank values. This is post-hoc descriptive evidence (it
    looks at the next quote), never a decision-time input and never a cost model.
    """
    if tick_size <= 0:
        raise T4QuotesError("tick_size_must_be_positive")
    if len({quote.arrival.session_id for quote in quotes}) > 1 or any(
        later.arrival.record_sequence <= earlier.arrival.record_sequence
        for earlier, later in pairwise(quotes)
    ):
        raise T4QuotesError("spread_description_needs_one_ordered_segment")
    breaks = sorted(unobserved_from)
    by_message: list[tuple[Decimal, int]] = []
    by_time: list[tuple[Decimal, int]] = []
    rel_by_time: list[tuple[Decimal, int]] = []
    size_by_time: list[tuple[Decimal, int]] = []
    states = {BOOK_NORMAL: 0, BOOK_LOCKED: 0, BOOK_CROSSED: 0}
    for position, quote in enumerate(quotes):
        states[quote.book_state] += 1
        if quote.book_state != BOOK_NORMAL:
            continue
        spread_ticks = (quote.ask_price - quote.bid_price) / tick_size
        by_message.append((spread_ticks, 1))
        if position + 1 < len(quotes):
            start = quote.arrival.arrival_utc_nanos
            end = quotes[position + 1].arrival.arrival_utc_nanos
            cut = bisect_right(breaks, start)
            if cut < len(breaks):
                end = min(end, breaks[cut])
            held = end - start
            if held > 0:
                mid = (quote.bid_price + quote.ask_price) / 2
                by_time.append((spread_ticks, held))
                rel_by_time.append((((quote.ask_price - quote.bid_price) / mid * _BPS).quantize(Decimal("1E-6")), held))
                size_by_time.append((min(quote.bid_size, quote.ask_size), held))
    if not by_message:
        return {"quotes": len(quotes), "book_states": states, "normal_quotes": 0}
    held_total = sum(weight for _, weight in by_time)
    one_tick = sum(weight for value, weight in by_time if value == 1)
    return {
        "quotes": len(quotes),
        "book_states": states,
        "tick_size": str(tick_size),
        "observed_nanos_time_weighted": held_total,
        "spread_ticks_by_message": {q: _plain(_nearest_rank(by_message, Decimal(q))) for q in _QUANTILES}
        | {"max": _plain(max(value for value, _ in by_message))},
        "spread_ticks_by_time": {q: _plain(_nearest_rank(by_time, Decimal(q))) for q in _QUANTILES} if by_time else {},
        "share_of_time_at_one_tick": str((Decimal(one_tick) / Decimal(held_total)).quantize(Decimal("1E-6")))
        if held_total else None,
        "relative_spread_bps_by_time": {q: str(_nearest_rank(rel_by_time, Decimal(q))) for q in _QUANTILES}
        if rel_by_time else {},
        "min_level1_size_by_time": {q: str(_nearest_rank(size_by_time, Decimal(q))) for q in ("0.01", "0.1", "0.5")}
        if size_by_time else {},
    }


__all__ = [
    "BOOK_CROSSED",
    "BOOK_LOCKED",
    "BOOK_NORMAL",
    "FUNDING_SEMANTICS_V1",
    "T4_QUOTES_SEMANTIC_VERSION_V1",
    "T4_QUOTE_FRAMES_V1",
    "TOP_OF_BOOK_SEMANTICS_V1",
    "T4FundingStateV1",
    "T4QuoteCountsV1",
    "T4QuoteDatasetV1",
    "T4QuoteNormalizerV1",
    "T4QuotesError",
    "T4TopOfBookV1",
    "derive_t4_quotes_v1",
    "describe_level1_spread_v1",
]
