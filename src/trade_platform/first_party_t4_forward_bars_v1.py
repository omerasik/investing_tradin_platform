"""Forward paper incubation -- proven one-minute T4 bars and instant price references.

``RESEARCH_ONLY``. Pure and offline: no socket, no database, no migration. It
reads the frames of a sealed first-party T4 dataset
(:mod:`trade_platform.first_party_t4_seal_v1`) and re-exposes the bars the
canonical normalization (:mod:`trade_platform.first_party_t4_normalization_v1`)
already proved. It is **not** a second OHLCV authority: no bar is built, filled,
resampled or interpolated here. Every price is a value of the sealed
``T4_OHLCV_1M`` frame; the sealed ``T4_TRADE`` frame is read only for the clock
evidence of the records each bar depends on. Nothing here produces a signal,
an order or a paper fill.

Bars (:class:`T4ForwardBarV1`)
------------------------------
A bar exists only where the normalization emitted one: the segment saw a trade
before the minute opened and a trade at or after it closed
(:data:`~trade_platform.first_party_t4_normalization_v1.BAR_COMPLETION_RULE_V1`).
A minute without trades, without an open proof or still open at the segment
end has **no** bar, and nothing is carried into it from a neighbour.

Each bar carries its prices quantized -- exactly, never rounded -- to the
sealed frame's decimal scale (``decimal128(38, 18)``, :data:`PRICE_QUANTUM_V1`),
and its knowledge clocks re-derived from the raw arrival and clock-bound fields
of the trade frame: ``complete_market_knowledge_at`` must equal ``max(close
boundary, every contributing record's knowledge, the closing record's
knowledge)`` and ``open_market_knowledge_at`` the first trade's record
knowledge, or the bar is refused. Clock uncertainty is reported as the range of
venue-minus-host upper bounds over those records and the exact bracket
evidence references (the session's RTT-bounded samples) that justify them.
Every knowledge instant is an *upper* bound: conservative, never early.

:func:`forward_bars_from_segment_output_v1` builds the same objects from a
:class:`~trade_platform.first_party_t4_normalization_v1.T4SegmentOutputV1` --
the record-at-a-time normalizer the live path feeds -- so live and replay
produce identical :attr:`T4ForwardBarV1.bar_content_hash` values by
construction; the tests assert it on one segment.

Price conventions (:class:`T4PriceConventionV1`)
------------------------------------------------
A caller must declare one. There is no default: which price a funding instant
"means" is an economic choice this module does not make.

* ``FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT`` -- the ``open`` of the T4 bar
  ``[instant, instant + 1 min)``: the price of the first public trade whose
  *venue trade time* is at or after the instant, ordered by ``(T, seq)``. This
  is **not** Bybit's REST kline open, which is the previous trade's price (the
  last trade before the minute). A leading ``(T, seq)`` group with more than
  one price is refused as ambiguous.
* ``LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT`` -- the ``close`` of the T4 bar
  ``[instant - 1 min, instant)``: the price of the last public trade whose venue
  trade time is strictly before the instant. A trailing ``(T, seq)`` group with
  more than one price is refused as ambiguous.

Both are last-traded prices on the public trade stream; neither is the mark or
index price the venue uses to settle funding.

References (:func:`resolve_t4_price_reference_v1`)
--------------------------------------------------
A reference names exactly one bar of exactly one sealed dataset, and is refused
(:class:`T4PriceReferenceRefusalV1`, never a fallback) when

* the instant is not an aware UTC instant on the minute grid;
* no supplied dataset proved the bar, or more than one did;
* any UTC hour of host-arrival time in which the bar's records arrived (see
  *Clock domains*) is not COMPLETE for the dataset's exchange symbol under the
  supplied manifest-proven hourly acceptance
  (:mod:`trade_platform.first_party_capture_hourly_acceptance_v1`), is not yet
  judged, or was proven by another capture session than the dataset's;
* the price the convention selects is sequence-ambiguous;
* the bar's knowledge evidence does not re-derive, or the bar was not yet
  knowable at the caller's ``known_by`` instant.

A reference is knowable at its bar's ``complete_market_knowledge_at``: the
ambiguity flags it is checked against describe whole ``(T, seq)`` groups and
are only knowable then, even for the open. ``known_by`` is a query parameter,
not part of the reference identity, so the same sealed inputs give the same
:attr:`T4PriceReferenceV1.reference_hash` whenever they are resolved.

Clock domains
-------------
Four kinds of time meet here and are never interchanged:

* **Venue event time** -- the trade's ``T`` (venue clock). It alone decides
  which minute a trade belongs to, and so the bar grid and the instant.
* **Host arrival time** -- ``arrival_utc_nanos``, the capture host's wall clock
  when the record arrived. Capture coverage intervals, partition manifests and
  therefore every hourly verdict are in this domain.
* **Bounded knowledge time** -- ``arrival + clock_bound`` (the RTT-bracketed
  venue-minus-host upper bound): the earliest venue-clock instant the record is
  provably known by. It decides ``known_by`` and nothing about hours.
* **Manifest coverage time** -- the hour grid of host arrival time the hourly
  acceptance judges.

The host clock may lag or lead the venue by seconds (about 9.5 s behind on
2026-09-24), so a bar's records can arrive in another capture hour than its
venue minute names: the trades of the venue minute starting at ``H`` may
arrive before ``H`` on the host clock, and a minute ending at ``H`` may close
on a record arriving after it. The hour check therefore uses the records'
raw arrival span (:attr:`T4BarClockEvidenceV1.first_arrival_utc_nanos` ..
``last_arrival_utc_nanos``, contributing and closing records) and requires
*every* arrival hour it touches to be COMPLETE for the dataset's session. It
never guesses an adjacent hour from the bar minute.

Hourly segments and the open convention
---------------------------------------
The R1B universe recorder captures bounded hourly segments, and a bar needs a
trade *before* its minute in the same segment. The segment that starts just
after a UTC hour therefore cannot prove the bar of the hour's first minute; that
open reference is refused as a missing bar, never borrowed from the previous
segment. The previous segment's close of the last minute is provable when that
segment ran past the hour and saw a later trade.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from .first_party_capture_authority_v1 import resolve_t4_registered_first_party_capture_contract_v1
from .first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    HourlyAcceptanceV1,
    HourlySegmentV1,
)
from .first_party_t4_normalization_v1 import (
    BAR_COMPLETION_RULE_V1,
    T4_NORMALIZATION_SEMANTIC_VERSION_V1,
    T4MinuteBarV1,
    T4SegmentOutputV1,
    market_knowledge_micros_v1,
    micros_to_datetime_v1,
    sha256_json_v1,
)
from .first_party_t4_seal_v1 import FirstPartyT4SealV1, iter_frame_rows_v1
from .research_data_plane_v1 import T4_OHLCV_1M_FRAME, T4_TRADE_FRAME, ResearchFrameStoreV1

#: 1.1.0: bars carry their records' host-arrival span; references are judged
#: against every arrival hour of it, not the venue-minute hour.
T4_FORWARD_BARS_SEMANTIC_VERSION_V1: Final = "first-party-t4-forward-bars-1.1.0"

#: The sealed ``T4_OHLCV_1M`` price columns are ``decimal128(38, 18)``.
PRICE_QUANTUM_V1: Final = Decimal(1).scaleb(-T4_OHLCV_1M_FRAME.schema.field("open").type.scale)

_MICROS_PER_MINUTE: Final = 60_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class T4ForwardBarError(ValueError):
    """Raised when sealed bar evidence is malformed or does not re-derive."""


class T4PriceReferenceRefusalV1(ValueError):
    """A price reference that cannot be proven. ``code`` names why; nothing is substituted."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class T4PriceConventionV1(StrEnum):
    """Which proven bar price an instant refers to. Always declared by the caller."""

    FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT = "FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT"
    LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT = "LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT"


def _to_micros(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(microseconds=1)


def _aware_micros(value: object, name: str) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise T4ForwardBarError(f"{name}_not_an_aware_instant")
    return _to_micros(value.astimezone(UTC))


def _exact_price(value: object, name: str) -> Decimal:
    """``value`` at :data:`PRICE_QUANTUM_V1`: exact or refused, never rounded."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise T4ForwardBarError(f"{name}_not_a_positive_decimal")
    try:
        quantized = value.quantize(PRICE_QUANTUM_V1)
    except InvalidOperation as error:
        raise T4ForwardBarError(f"{name}_not_representable_at_frame_scale") from error
    if quantized != value:
        raise T4ForwardBarError(f"{name}_not_exact_at_frame_scale")
    return quantized


def _flag(value: object, name: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise T4ForwardBarError(f"{name}_flag_malformed")


# ---------------------------------------------------------------------------
# Record clocks and bars
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4RecordClockV1:
    """The clock facts of one capture record that a bar depends on."""

    record_content_hash: str
    arrival_utc_nanos: int
    clock_bound_nanos: int
    clock_bound_evidence: str

    @property
    def market_knowledge_micros(self) -> int:
        return market_knowledge_micros_v1(self.arrival_utc_nanos, self.clock_bound_nanos)


@dataclass(frozen=True, slots=True)
class _TradeClockRowV1:
    observation_reference: str
    trade_ts_millis: int
    clock: T4RecordClockV1


@dataclass(frozen=True, slots=True)
class T4BarClockEvidenceV1:
    """Knowledge-clock evidence of one bar, re-derived from its records' raw clocks.

    ``knowledge_decided_by`` is ``"CLOSE_BOUNDARY"`` when the minute's end is the
    latest of the completion terms, else the content hash of the record whose
    knowledge is. The bounds are venue-minus-host *upper* bounds in nanoseconds.
    """

    open_record_hash: str
    closing_record_hash: str
    knowledge_decided_by: str
    min_clock_bound_nanos: int
    max_clock_bound_nanos: int
    clock_bound_evidence: tuple[str, ...]
    contributing_record_count: int
    #: Host-arrival span of the same records (contributing and closing): the
    #: domain capture coverage and hourly verdicts are judged in.
    first_arrival_utc_nanos: int
    last_arrival_utc_nanos: int

    def payload(self) -> dict[str, Any]:
        return {
            "open_record_hash": self.open_record_hash,
            "closing_record_hash": self.closing_record_hash,
            "knowledge_decided_by": self.knowledge_decided_by,
            "min_clock_bound_nanos": self.min_clock_bound_nanos,
            "max_clock_bound_nanos": self.max_clock_bound_nanos,
            "clock_bound_evidence": list(self.clock_bound_evidence),
            "contributing_record_count": self.contributing_record_count,
            "first_arrival_utc_nanos": self.first_arrival_utc_nanos,
            "last_arrival_utc_nanos": self.last_arrival_utc_nanos,
        }


@dataclass(frozen=True, slots=True)
class T4ForwardBarV1:
    """One proven, COMPLETED one-minute T4 bar with its knowledge-clock evidence."""

    exchange_symbol: str
    bar_open_micros: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    trade_count: int
    first_trade_reference: str
    last_trade_reference: str
    open_market_knowledge_micros: int
    complete_market_knowledge_micros: int
    open_is_sequence_ambiguous: bool
    close_is_sequence_ambiguous: bool
    trade_manifest_hash: str
    clock: T4BarClockEvidenceV1

    @property
    def bar_close_micros(self) -> int:
        return self.bar_open_micros + _MICROS_PER_MINUTE

    @property
    def bar_open_at(self) -> datetime:
        return micros_to_datetime_v1(self.bar_open_micros)

    @property
    def complete_market_knowledge_at(self) -> datetime:
        return micros_to_datetime_v1(self.complete_market_knowledge_micros)

    def payload(self) -> dict[str, Any]:
        return {
            "semantic_version": T4_FORWARD_BARS_SEMANTIC_VERSION_V1,
            "normalization_semantic_version": T4_NORMALIZATION_SEMANTIC_VERSION_V1,
            "bar_completion_rule": BAR_COMPLETION_RULE_V1,
            "exchange_symbol": self.exchange_symbol,
            "bar_open_micros": self.bar_open_micros,
            "open_price": str(self.open_price),
            "high_price": str(self.high_price),
            "low_price": str(self.low_price),
            "close_price": str(self.close_price),
            "trade_count": self.trade_count,
            "first_trade_reference": self.first_trade_reference,
            "last_trade_reference": self.last_trade_reference,
            "open_market_knowledge_micros": self.open_market_knowledge_micros,
            "complete_market_knowledge_micros": self.complete_market_knowledge_micros,
            "open_is_sequence_ambiguous": self.open_is_sequence_ambiguous,
            "close_is_sequence_ambiguous": self.close_is_sequence_ambiguous,
            "trade_manifest_hash": self.trade_manifest_hash,
            "clock": self.clock.payload(),
        }

    @property
    def bar_content_hash(self) -> str:
        """Dataset-independent: the same bar from live or replay hashes identically."""
        return sha256_json_v1(self.payload())


def _assemble_bars(
    exchange_symbol: str,
    bars: Sequence[T4MinuteBarV1],
    trades: Iterable[_TradeClockRowV1],
) -> tuple[T4ForwardBarV1, ...]:
    """Attach re-derived clock evidence to every bar, or refuse the whole set."""
    wanted = {bar.bar_open_micros for bar in bars}
    if len(wanted) != len(bars):
        raise T4ForwardBarError("bar_minute_not_unique")
    closing_hashes = {bar.closing_record_hash for bar in bars}
    contributing: dict[int, dict[str, T4RecordClockV1]] = {minute: {} for minute in wanted}
    trade_counts: dict[int, int] = dict.fromkeys(wanted, 0)
    reference_records: dict[str, str] = {}
    closing: dict[str, T4RecordClockV1] = {}
    wanted_references = {ref for bar in bars for ref in (bar.first_trade_reference, bar.last_trade_reference)}
    for row in trades:
        clock = row.clock
        if clock.record_content_hash in closing_hashes:
            _same_clock(closing.setdefault(clock.record_content_hash, clock), clock)
        minute = (row.trade_ts_millis * 1_000) // _MICROS_PER_MINUTE * _MICROS_PER_MINUTE
        if minute not in contributing:
            continue
        records = contributing[minute]
        _same_clock(records.setdefault(clock.record_content_hash, clock), clock)
        trade_counts[minute] += 1
        if row.observation_reference in wanted_references:
            reference_records[row.observation_reference] = clock.record_content_hash
    return tuple(
        _forward_bar(exchange_symbol, bar, contributing[bar.bar_open_micros],
                     trade_counts[bar.bar_open_micros], reference_records, closing)
        for bar in sorted(bars, key=lambda item: item.bar_open_micros)
    )


def _same_clock(kept: T4RecordClockV1, seen: T4RecordClockV1) -> None:
    if kept != seen:
        raise T4ForwardBarError("record_clock_differs_between_its_trades")


def _forward_bar(
    exchange_symbol: str,
    bar: T4MinuteBarV1,
    records: Mapping[str, T4RecordClockV1],
    trade_count: int,
    reference_records: Mapping[str, str],
    closing: Mapping[str, T4RecordClockV1],
) -> T4ForwardBarV1:
    if bar.bar_open_micros % _MICROS_PER_MINUTE:
        raise T4ForwardBarError("bar_open_not_on_the_minute_grid")
    if not records or trade_count != bar.trade_count:
        raise T4ForwardBarError("bar_trade_count_does_not_rederive")
    closing_clock = closing.get(bar.closing_record_hash)
    open_hash = reference_records.get(bar.first_trade_reference)
    if closing_clock is None or open_hash is None or bar.last_trade_reference not in reference_records:
        raise T4ForwardBarError("bar_knowledge_evidence_missing")
    if records[open_hash].market_knowledge_micros != bar.open_market_knowledge_micros:
        raise T4ForwardBarError("bar_open_knowledge_does_not_rederive")
    terms = [("CLOSE_BOUNDARY", bar.bar_close_micros)]
    terms.extend((key, clock.market_knowledge_micros) for key, clock in sorted(records.items()))
    terms.append((closing_clock.record_content_hash, closing_clock.market_knowledge_micros))
    complete = max(value for _, value in terms)
    if complete != bar.complete_market_knowledge_micros:
        raise T4ForwardBarError("bar_completion_knowledge_does_not_rederive")
    decided_by = next(key for key, value in terms if value == complete)
    involved = [*records.values(), closing_clock]
    open_price = _exact_price(bar.open_price, "open_price")
    high_price = _exact_price(bar.high_price, "high_price")
    low_price = _exact_price(bar.low_price, "low_price")
    close_price = _exact_price(bar.close_price, "close_price")
    if not low_price <= min(open_price, close_price) <= max(open_price, close_price) <= high_price:
        raise T4ForwardBarError("bar_prices_incoherent")
    return T4ForwardBarV1(
        exchange_symbol=exchange_symbol,
        bar_open_micros=bar.bar_open_micros,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        trade_count=bar.trade_count,
        first_trade_reference=bar.first_trade_reference,
        last_trade_reference=bar.last_trade_reference,
        open_market_knowledge_micros=bar.open_market_knowledge_micros,
        complete_market_knowledge_micros=bar.complete_market_knowledge_micros,
        open_is_sequence_ambiguous=bar.open_is_sequence_ambiguous,
        close_is_sequence_ambiguous=bar.close_is_sequence_ambiguous,
        trade_manifest_hash=bar.trade_manifest_hash,
        clock=T4BarClockEvidenceV1(
            open_record_hash=open_hash,
            closing_record_hash=bar.closing_record_hash,
            knowledge_decided_by=decided_by,
            min_clock_bound_nanos=min(item.clock_bound_nanos for item in involved),
            max_clock_bound_nanos=max(item.clock_bound_nanos for item in involved),
            clock_bound_evidence=tuple(sorted({item.clock_bound_evidence for item in involved})),
            contributing_record_count=len(records),
            first_arrival_utc_nanos=min(item.arrival_utc_nanos for item in involved),
            last_arrival_utc_nanos=max(item.arrival_utc_nanos for item in involved),
        ),
    )


def forward_bars_from_segment_output_v1(
    output: T4SegmentOutputV1, *, exchange_symbol: str
) -> tuple[T4ForwardBarV1, ...]:
    """Bars straight from the record-at-a-time normalizer (the live path's shape).

    Bars only: a live bar has no sealed dataset yet, so it can never back a
    :class:`T4PriceReferenceV1`. It exists so live and replay are proven to
    agree bar for bar.
    """
    rows = (
        _TradeClockRowV1(
            observation_reference=trade.observation_reference,
            trade_ts_millis=trade.trade_ts_millis,
            clock=T4RecordClockV1(
                record_content_hash=trade.arrival.record_content_hash,
                arrival_utc_nanos=trade.arrival.arrival_utc_nanos,
                clock_bound_nanos=trade.arrival.clock_bound_nanos,
                clock_bound_evidence=trade.arrival.clock_bound_evidence,
            ),
        )
        for trade in output.trades
    )
    return _assemble_bars(exchange_symbol, output.bars, rows)


# ---------------------------------------------------------------------------
# Sealed datasets
# ---------------------------------------------------------------------------


def _sealed_minute_bars(seal: FirstPartyT4SealV1, store: ResearchFrameStoreV1) -> list[T4MinuteBarV1]:
    names = T4_OHLCV_1M_FRAME.schema.names
    index = {name: names.index(name) for name in names}
    bars: list[T4MinuteBarV1] = []
    for row in iter_frame_rows_v1(seal, T4_OHLCV_1M_FRAME.kind, store):
        open_micros = _aware_micros(row[index["bar_open_at"]], "bar_open_at")
        if _aware_micros(row[index["bar_close_at"]], "bar_close_at") != open_micros + _MICROS_PER_MINUTE:
            raise T4ForwardBarError("bar_is_not_one_minute")
        bars.append(
            T4MinuteBarV1(
                bar_open_micros=open_micros,
                open_price=_exact_price(row[index["open"]], "open_price"),
                high_price=_exact_price(row[index["high"]], "high_price"),
                low_price=_exact_price(row[index["low"]], "low_price"),
                close_price=_exact_price(row[index["close"]], "close_price"),
                base_volume=Decimal(str(row[index["base_volume"]])),
                quote_turnover=Decimal(str(row[index["quote_turnover"]])),
                trade_count=int(str(row[index["trade_count"]])),
                block_trade_count=int(str(row[index["block_trade_count"]])),
                rpi_trade_count=int(str(row[index["rpi_trade_count"]])),
                first_trade_reference=str(row[index["first_trade_reference"]]),
                last_trade_reference=str(row[index["last_trade_reference"]]),
                open_market_knowledge_micros=_aware_micros(
                    row[index["open_market_knowledge_at"]], "open_market_knowledge_at"
                ),
                complete_market_knowledge_micros=_aware_micros(
                    row[index["complete_market_knowledge_at"]], "complete_market_knowledge_at"
                ),
                open_is_sequence_ambiguous=_flag(row[index["open_is_sequence_ambiguous"]], "open_ambiguous"),
                close_is_sequence_ambiguous=_flag(row[index["close_is_sequence_ambiguous"]], "close_ambiguous"),
                closing_record_hash=str(row[index["closing_record_hash"]]),
                trade_manifest_hash=str(row[index["trade_manifest_hash"]]),
            )
        )
    return bars


def _sealed_trade_rows(seal: FirstPartyT4SealV1, store: ResearchFrameStoreV1) -> Iterable[_TradeClockRowV1]:
    names = T4_TRADE_FRAME.schema.names
    ref_i, ts_i = names.index("observation_reference"), names.index("trade_ts_millis")
    hash_i, arrival_i = names.index("record_content_hash"), names.index("arrival_utc_nanos")
    bound_i, evidence_i = names.index("clock_bound_nanos"), names.index("clock_bound_evidence")
    knowledge_i = names.index("market_knowledge_at")
    for row in iter_frame_rows_v1(seal, T4_TRADE_FRAME.kind, store):
        clock = T4RecordClockV1(
            record_content_hash=str(row[hash_i]),
            arrival_utc_nanos=int(str(row[arrival_i])),
            clock_bound_nanos=int(str(row[bound_i])),
            clock_bound_evidence=str(row[evidence_i]),
        )
        # The stored knowledge column is a claim; the raw clock fields decide.
        if _aware_micros(row[knowledge_i], "market_knowledge_at") != clock.market_knowledge_micros:
            raise T4ForwardBarError("trade_knowledge_does_not_rederive_from_its_clock")
        yield _TradeClockRowV1(str(row[ref_i]), int(str(row[ts_i])), clock)


@dataclass(frozen=True, slots=True)
class T4ForwardDatasetBarsV1:
    """Every proven bar of one sealed, raw-replayed T4 dataset."""

    dataset_version_id: UUID
    dataset_content_hash: str
    source_id: UUID
    session_id: UUID
    instrument: str
    exchange_symbol: str
    segment_clock_evidence_hash: str
    bars: tuple[T4ForwardBarV1, ...]

    def bar_at(self, bar_open_micros: int) -> T4ForwardBarV1 | None:
        return next((bar for bar in self.bars if bar.bar_open_micros == bar_open_micros), None)


def forward_bars_from_seal_v1(
    seal: FirstPartyT4SealV1, *, store: ResearchFrameStoreV1
) -> T4ForwardDatasetBarsV1:
    """The proven bars of one sealed dataset, read from its own frames.

    Only an intact seal rebuilt from raw capture (``raw_replayed``) of a
    T4-registered source is accepted -- the same rule the sealed-clock resolver
    applies. The frames are re-verified byte for byte before any row is read.
    """
    if not isinstance(seal, FirstPartyT4SealV1) or not seal.integrity_verified():
        raise T4ForwardBarError("forward_bars_require_an_intact_seal")
    if not seal.raw_replayed:
        raise T4ForwardBarError("forward_bars_require_a_seal_rebuilt_from_raw_capture")
    if resolve_t4_registered_first_party_capture_contract_v1(seal.source_id) is None:
        raise T4ForwardBarError("forward_bars_require_a_t4_registered_source")
    if seal.timing_facts.observations_missing_knowledge_time != 0:
        raise T4ForwardBarError("seal_has_observations_without_knowledge_time")
    for kind in (T4_OHLCV_1M_FRAME.kind, T4_TRADE_FRAME.kind):
        store.verify(store.load_manifest(seal.frame_manifests[kind]))
    identity = seal.identity
    try:
        exchange_symbol = str(identity["exchange_symbol"])
        instrument = str(identity["instrument"])
        session_id = UUID(str(identity["session_id"]))
        clock_hash = str(identity["clock_evidence"]["clock_evidence_hash"])
    except (KeyError, TypeError, ValueError) as error:
        raise T4ForwardBarError("seal_identity_malformed") from error
    bars = _assemble_bars(exchange_symbol, _sealed_minute_bars(seal, store), _sealed_trade_rows(seal, store))
    return T4ForwardDatasetBarsV1(
        dataset_version_id=seal.dataset_version_id,
        dataset_content_hash=seal.content_hash,
        source_id=seal.source_id,
        session_id=session_id,
        instrument=instrument,
        exchange_symbol=exchange_symbol,
        segment_clock_evidence_hash=clock_hash,
        bars=bars,
    )


# ---------------------------------------------------------------------------
# Instant price references
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4ArrivalHourV1:
    """One manifest-proven COMPLETE capture hour, in host-arrival time."""

    hour_start_utc_nanos: int
    unproven_nanos: int

    def payload(self) -> dict[str, Any]:
        return {"hour_start_utc_nanos": self.hour_start_utc_nanos, "unproven_nanos": self.unproven_nanos}


@dataclass(frozen=True, slots=True)
class T4HourEvidenceV1:
    """The manifest-proven COMPLETE capture hours a referenced bar's records arrived in.

    ``hours`` are every UTC hour of host-arrival time from the bar's first to its
    last record arrival, in order -- one, or two when the arrivals straddle an
    hour line -- each COMPLETE and proven by the same session.
    ``hour_start_utc_nanos`` is the first of them.
    """

    hours: tuple[T4ArrivalHourV1, ...]
    session_id: UUID
    max_unproven_nanos: int
    first_arrival_utc_nanos: int
    last_arrival_utc_nanos: int

    @property
    def hour_start_utc_nanos(self) -> int:
        return self.hours[0].hour_start_utc_nanos

    def payload(self) -> dict[str, Any]:
        return {
            "time_domain": "HOST_ARRIVAL_UTC",
            "hour_start_utc_nanos": self.hour_start_utc_nanos,
            "hours": [hour.payload() for hour in self.hours],
            "session_id": str(self.session_id),
            "max_unproven_nanos": self.max_unproven_nanos,
            "first_arrival_utc_nanos": self.first_arrival_utc_nanos,
            "last_arrival_utc_nanos": self.last_arrival_utc_nanos,
        }


@dataclass(frozen=True, slots=True)
class T4PriceReferenceV1:
    """An immutable price reference for one instant, under one declared convention."""

    instant_micros: int
    convention: T4PriceConventionV1
    price: Decimal
    instrument: str
    exchange_symbol: str
    dataset_version_id: UUID
    dataset_content_hash: str
    session_id: UUID
    segment_clock_evidence_hash: str
    bar: T4ForwardBarV1
    hour: T4HourEvidenceV1

    @property
    def instant(self) -> datetime:
        return micros_to_datetime_v1(self.instant_micros)

    @property
    def knowable_at(self) -> datetime:
        """When the reference became knowable: its bar's completion knowledge (an upper bound)."""
        return self.bar.complete_market_knowledge_at

    def payload(self) -> dict[str, Any]:
        return {
            "semantic_version": T4_FORWARD_BARS_SEMANTIC_VERSION_V1,
            "instant_micros": self.instant_micros,
            "convention": self.convention.value,
            "price": str(self.price),
            "price_quantum": str(PRICE_QUANTUM_V1),
            "instrument": self.instrument,
            "exchange_symbol": self.exchange_symbol,
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_content_hash": self.dataset_content_hash,
            "session_id": str(self.session_id),
            "segment_clock_evidence_hash": self.segment_clock_evidence_hash,
            "bar_content_hash": self.bar.bar_content_hash,
            "knowable_at_micros": self.bar.complete_market_knowledge_micros,
            "hour": self.hour.payload(),
        }

    @property
    def reference_hash(self) -> str:
        return sha256_json_v1(self.payload())


def _instant_micros(instant: object) -> int:
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise T4PriceReferenceRefusalV1("instant_must_be_an_aware_utc_instant")
    micros = _to_micros(instant.astimezone(UTC))
    if micros % _MICROS_PER_MINUTE:
        raise T4PriceReferenceRefusalV1("instant_not_on_the_minute_grid")
    return micros


def _complete_arrival_hours(
    acceptance: HourlyAcceptanceV1, exchange_symbol: str, session_id: UUID, bar: T4ForwardBarV1
) -> T4HourEvidenceV1:
    """Every arrival hour holding ``bar``'s records, each COMPLETE for ``session_id``.

    Hour verdicts judge capture coverage, which is host-arrival time
    (:class:`~trade_platform.first_party_capture_archive_v1.CaptureCoverageIntervalV1`);
    the bar minute is venue event time. The hours are therefore taken from the
    records' raw arrival readings -- never from the bar minute, and never from
    knowledge time, which adds the venue-minus-host bound and so belongs to
    neither clock's hour grid.
    """
    first_arrival = bar.clock.first_arrival_utc_nanos
    last_arrival = bar.clock.last_arrival_utc_nanos
    if last_arrival < first_arrival:
        raise T4PriceReferenceRefusalV1("bar_arrival_span_malformed")
    symbols = [item for item in acceptance.symbols if item.symbol == exchange_symbol]
    if len(symbols) != 1:
        raise T4PriceReferenceRefusalV1("hourly_acceptance_does_not_cover_the_symbol")
    first = acceptance.first_hour_start_utc_nanos
    hours: list[T4ArrivalHourV1] = []
    hour_start = first_arrival // HOUR_NANOS * HOUR_NANOS
    while hour_start <= last_arrival:
        if first is None or not first <= hour_start < first + acceptance.hours_judged * HOUR_NANOS:
            raise T4PriceReferenceRefusalV1("hour_not_judged")
        # One segment per judged hour, or the acceptance is not the evaluator's output.
        if len(symbols[0].segments) != acceptance.hours_judged:
            raise T4PriceReferenceRefusalV1("hourly_acceptance_malformed")
        segment: HourlySegmentV1 = symbols[0].segments[(hour_start - first) // HOUR_NANOS]
        if segment.hour_start_utc_nanos != hour_start:
            raise T4PriceReferenceRefusalV1("hourly_acceptance_malformed")
        if not segment.complete:
            raise T4PriceReferenceRefusalV1(f"hour_not_complete:{segment.verdict.value}")
        if segment.session_id is None or segment.unproven_nanos is None:
            raise T4PriceReferenceRefusalV1("hourly_acceptance_malformed")
        if segment.session_id != session_id:
            raise T4PriceReferenceRefusalV1("hour_proven_by_another_capture_session")
        hours.append(T4ArrivalHourV1(hour_start, segment.unproven_nanos))
        hour_start += HOUR_NANOS
    return T4HourEvidenceV1(
        hours=tuple(hours),
        session_id=session_id,
        max_unproven_nanos=acceptance.max_unproven_nanos,
        first_arrival_utc_nanos=first_arrival,
        last_arrival_utc_nanos=last_arrival,
    )


def resolve_t4_price_reference_v1(
    datasets: Sequence[T4ForwardDatasetBarsV1],
    *,
    instant: datetime,
    convention: T4PriceConventionV1,
    acceptance: HourlyAcceptanceV1,
    known_by: datetime,
) -> T4PriceReferenceV1:
    """The proven price at ``instant`` under ``convention``, or a refusal.

    ``datasets`` are the candidate sealed datasets of one exchange symbol;
    exactly one must have proven the selected bar. ``acceptance`` must be the
    hourly acceptance of the same capture source. ``known_by`` is the instant
    the caller decides at: a reference not yet knowable then is refused.
    """
    if not isinstance(convention, T4PriceConventionV1):
        raise T4PriceReferenceRefusalV1("price_convention_must_be_declared")
    instant_micros = _instant_micros(instant)
    if not isinstance(known_by, datetime) or known_by.tzinfo is None:
        raise T4PriceReferenceRefusalV1("known_by_must_be_an_aware_utc_instant")
    symbols = {item.exchange_symbol for item in datasets}
    if len(symbols) != 1:
        raise T4PriceReferenceRefusalV1("datasets_must_name_exactly_one_exchange_symbol")
    if convention is T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT:
        bar_open = instant_micros
    else:
        bar_open = instant_micros - _MICROS_PER_MINUTE
    found = [(item, bar) for item in datasets if (bar := item.bar_at(bar_open)) is not None]
    if not found:
        raise T4PriceReferenceRefusalV1("no_proven_bar_for_the_minute")
    if len({item.dataset_version_id for item, _ in found}) != 1:
        raise T4PriceReferenceRefusalV1("minute_proven_by_more_than_one_dataset")
    dataset, bar = found[0]
    hour = _complete_arrival_hours(acceptance, dataset.exchange_symbol, dataset.session_id, bar)
    if convention is T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT:
        if bar.open_is_sequence_ambiguous:
            raise T4PriceReferenceRefusalV1("open_price_is_sequence_ambiguous")
        price = bar.open_price
    else:
        if bar.close_is_sequence_ambiguous:
            raise T4PriceReferenceRefusalV1("close_price_is_sequence_ambiguous")
        price = bar.close_price
    if bar.complete_market_knowledge_micros > _to_micros(known_by.astimezone(UTC)):
        raise T4PriceReferenceRefusalV1("reference_not_yet_knowable_at_known_by")
    return T4PriceReferenceV1(
        instant_micros=instant_micros,
        convention=convention,
        price=price,
        instrument=dataset.instrument,
        exchange_symbol=dataset.exchange_symbol,
        dataset_version_id=dataset.dataset_version_id,
        dataset_content_hash=dataset.dataset_content_hash,
        session_id=dataset.session_id,
        segment_clock_evidence_hash=dataset.segment_clock_evidence_hash,
        bar=bar,
        hour=hour,
    )


__all__ = [
    "PRICE_QUANTUM_V1",
    "T4_FORWARD_BARS_SEMANTIC_VERSION_V1",
    "T4ArrivalHourV1",
    "T4BarClockEvidenceV1",
    "T4ForwardBarError",
    "T4ForwardBarV1",
    "T4ForwardDatasetBarsV1",
    "T4HourEvidenceV1",
    "T4PriceConventionV1",
    "T4PriceReferenceRefusalV1",
    "T4PriceReferenceV1",
    "T4RecordClockV1",
    "forward_bars_from_seal_v1",
    "forward_bars_from_segment_output_v1",
    "resolve_t4_price_reference_v1",
]
