"""Phase 3D.9S.2A -- deterministic 1m OHLCV reconstruction from captured Bybit public trades.

``RESEARCH_ENGINEERING_EVIDENCE_ONLY``. Builds UTC one-minute bars from the
captured Bybit V5 ``publicTrade`` stream
(:mod:`trade_platform.tardis_capture_evidence_v1`). It creates no table, writes
no dataset, and produces no ``AuthoritativeTradableBarV2``: a bar here is
engineering evidence that the capture path can reconstruct tradable bars whose
availability instant is measured, nothing more.

**Three different instants, never conflated.** ``bar_open_at`` is the economic
open boundary of the minute; ``bar_close_at`` is its exclusive close boundary;
``research_available_at`` is when the *last contributing trade* actually arrived
at the recorder. The last one is always at or after the exchange timestamp of
the final trade and is the only one that may be used as a decision cutoff.

**Bucketing is by exchange trade time ``T``, on the half-open interval
``[open, close)``.** A trade exactly on a minute boundary opens the new minute;
it never closes the old one.

**Ordering is a declared key, never insertion order.** Trades are ordered by
``(T, seq, trade_id)``. ``seq`` is Bybit's own cross-sequence and is the
economically meaningful tiebreak within an exchange millisecond; ``trade_id``
is a final deterministic disambiguator only, carrying no economic meaning. Where
the leading or trailing ``(T, seq)`` group holds more than one distinct price,
the bar records that its OPEN or CLOSE was resolved by that non-economic
tiebreak (``open_is_sequence_ambiguous`` / ``close_is_sequence_ambiguous``)
instead of quietly presenting an arbitrary pick as fact.

**Units are carried, never assumed.** Turnover is computed only when the
declared contract semantics say the trade quantity is denominated in the base
asset -- true for the USDT-margined linear BTCUSDT perpetual, false for an
inverse contract, where ``price * quantity`` would be a meaningless number. The
resulting units travel with the bar as
:attr:`ReconstructedTradeBarV1.base_volume_unit` and
:attr:`ReconstructedTradeBarV1.quote_turnover_unit`.

**A minute with no trade has no bar.** There is no zero-volume candle, no
forward fill, and no interpolation across a collection gap: an absent bar is the
honest statement that nothing was observed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .tardis_capture_evidence_v1 import (
    CAPTURE_LIFECYCLE_V1,
    TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
    TardisChannelV1,
    TardisRawCaptureRecordV1,
    canonical_hash,
    nanos_to_datetime,
)

BAR_INTERVAL_V1: Final = timedelta(minutes=1)
BAR_BUILDER_SEMANTIC_VERSION_V1: Final = "tardis-capture-1m-ohlcv-1.0.0"

#: Quantization applied to the two summed quantities before hashing. Bybit's
#: BTCUSDT quantity step is 1e-3 BTC and its price step 0.1 USDT, so 1e-8 keeps
#: every sum exact while pinning the representation a hash is taken over.
VOLUME_QUANTUM_V1: Final = Decimal("1E-8")
TURNOVER_QUANTUM_V1: Final = Decimal("1E-8")

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.bybit_trade_bar_reconstruction_v1")


class BybitTradeBarReconstructionError(ValueError):
    """Raised for malformed trades or an unsupported contract quantity convention."""


@dataclass(frozen=True, slots=True)
class LinearContractQuantitySemanticsV1:
    """Explicit, caller-declared units for one contract. Never inferred from a symbol."""

    base_asset: str
    quote_asset: str
    #: True for a USDT-margined linear perpetual, where ``v`` is base quantity.
    #: False for an inverse contract, where ``price * quantity`` is not turnover.
    quantity_is_base_asset: bool

    def validate(self) -> None:
        if not self.base_asset.strip() or not self.quote_asset.strip():
            raise BybitTradeBarReconstructionError("contract_quantity_semantics_assets_required")


#: The authorized pilot instrument. Bybit's BTCUSDT linear perpetual quotes in
#: USDT and sizes in BTC, so quote turnover is a valid transformation.
BYBIT_BTCUSDT_LINEAR_PERPETUAL_V1: Final = LinearContractQuantitySemanticsV1(
    base_asset="BTC", quote_asset="USDT", quantity_is_base_asset=True
)


@dataclass(frozen=True, slots=True)
class CapturedPublicTradeV1:
    """One captured trade, bound to the record that carried it."""

    symbol: str
    trade_id: str
    exchange_timestamp: datetime
    price: Decimal
    quantity: Decimal
    side: str
    sequence: int
    local_timestamp_nanos: int
    record_content_hash: str
    record_id: UUID

    @property
    def local_timestamp(self) -> datetime:
        return nanos_to_datetime(self.local_timestamp_nanos)

    @property
    def ordering_key(self) -> tuple[datetime, int, str]:
        """Declared deterministic order. Independent of how trades were read."""
        return (self.exchange_timestamp, self.sequence, self.trade_id)


def _decimal_field(raw: Any, field_name: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise BybitTradeBarReconstructionError(f"bybit_trade_{field_name}_malformed")
    if isinstance(raw, str) and not raw.strip():
        raise BybitTradeBarReconstructionError(f"bybit_trade_{field_name}_empty")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise BybitTradeBarReconstructionError(f"bybit_trade_{field_name}_malformed") from exc
    if not value.is_finite():
        raise BybitTradeBarReconstructionError(f"bybit_trade_{field_name}_not_finite")
    if value <= 0:
        raise BybitTradeBarReconstructionError(f"bybit_trade_{field_name}_not_positive")
    return value


def parse_captured_public_trades(
    records: Sequence[TardisRawCaptureRecordV1],
    *,
    symbol: str,
) -> tuple[CapturedPublicTradeV1, ...]:
    """Flatten captured ``publicTrade`` messages into individual trades.

    Each Bybit message carries a ``data`` array; every entry becomes one trade
    bound to that record's arrival instant and content hash.
    """
    if not symbol.strip():
        raise BybitTradeBarReconstructionError("bybit_trade_symbol_required")
    trades: list[CapturedPublicTradeV1] = []
    for record in records:
        if record.channel is not TardisChannelV1.PUBLIC_TRADE:
            raise BybitTradeBarReconstructionError("bybit_trade_wrong_channel")
        if record.symbol != symbol:
            raise BybitTradeBarReconstructionError("bybit_trade_symbol_mismatch")
        data = record.payload.get("data")
        if not isinstance(data, list):
            raise BybitTradeBarReconstructionError("bybit_trade_data_not_a_list")
        for entry in data:
            if not isinstance(entry, dict):
                raise BybitTradeBarReconstructionError("bybit_trade_entry_not_an_object")
            trade_id = entry.get("i")
            if not isinstance(trade_id, str) or not trade_id.strip():
                raise BybitTradeBarReconstructionError("bybit_trade_id_missing")
            raw_exchange_millis = entry.get("T")
            if isinstance(raw_exchange_millis, bool) or not isinstance(raw_exchange_millis, int):
                raise BybitTradeBarReconstructionError("bybit_trade_exchange_timestamp_malformed")
            if raw_exchange_millis <= 0:
                raise BybitTradeBarReconstructionError("bybit_trade_exchange_timestamp_malformed")
            raw_sequence = entry.get("seq")
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                raise BybitTradeBarReconstructionError("bybit_trade_sequence_missing")
            side = entry.get("S")
            if side not in ("Buy", "Sell"):
                raise BybitTradeBarReconstructionError("bybit_trade_side_malformed")
            trades.append(
                CapturedPublicTradeV1(
                    symbol=symbol,
                    trade_id=trade_id,
                    exchange_timestamp=datetime.fromtimestamp(raw_exchange_millis / 1000, tz=UTC),
                    price=_decimal_field(entry.get("p"), "price"),
                    quantity=_decimal_field(entry.get("v"), "quantity"),
                    side=side,
                    sequence=raw_sequence,
                    local_timestamp_nanos=record.local_timestamp_nanos,
                    record_content_hash=record.content_hash,
                    record_id=record.record_id,
                )
            )
    return tuple(trades)


@dataclass(frozen=True, slots=True)
class ReconstructedTradeBarV1:
    """One deterministic UTC minute bar built only from observed trades."""

    symbol: str
    bar_open_at: datetime
    bar_close_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    base_volume: Decimal
    base_volume_unit: str
    quote_turnover: Decimal | None
    quote_turnover_unit: str | None
    trade_count: int
    first_trade_id: str
    last_trade_id: str
    research_available_at_nanos: int
    open_is_sequence_ambiguous: bool
    close_is_sequence_ambiguous: bool
    trade_manifest_hash: str
    builder_semantic_version: str
    content_hash: str

    @property
    def research_available_at(self) -> datetime:
        """When the final contributing trade arrived at the recorder."""
        return nanos_to_datetime(self.research_available_at_nanos)

    @property
    def bar_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"tardis-capture-1m-bar-v1:{self.content_hash}")


def _floor_to_minute(instant: datetime) -> datetime:
    return instant.replace(second=0, microsecond=0)


def _price_group_ambiguous(group: Sequence[CapturedPublicTradeV1]) -> bool:
    return len({trade.price for trade in group}) > 1


def build_one_minute_trade_bars(
    trades: Sequence[CapturedPublicTradeV1],
    *,
    symbol: str,
    contract: LinearContractQuantitySemanticsV1 = BYBIT_BTCUSDT_LINEAR_PERPETUAL_V1,
) -> tuple[ReconstructedTradeBarV1, ...]:
    """Build one bar per minute that actually contains trades. Never more."""
    contract.validate()
    if not symbol.strip():
        raise BybitTradeBarReconstructionError("bybit_trade_symbol_required")

    buckets: dict[datetime, list[CapturedPublicTradeV1]] = {}
    for trade in trades:
        if trade.symbol != symbol:
            raise BybitTradeBarReconstructionError("bybit_trade_symbol_mismatch")
        buckets.setdefault(_floor_to_minute(trade.exchange_timestamp), []).append(trade)

    bars: list[ReconstructedTradeBarV1] = []
    for bar_open_at in sorted(buckets):
        ordered = sorted(buckets[bar_open_at], key=lambda t: t.ordering_key)
        first, last = ordered[0], ordered[-1]
        leading = [t for t in ordered if (t.exchange_timestamp, t.sequence) == (first.exchange_timestamp, first.sequence)]
        trailing = [t for t in ordered if (t.exchange_timestamp, t.sequence) == (last.exchange_timestamp, last.sequence)]

        base_volume = sum((t.quantity for t in ordered), Decimal("0")).quantize(
            VOLUME_QUANTUM_V1, rounding=ROUND_HALF_EVEN
        )
        quote_turnover: Decimal | None = None
        quote_turnover_unit: str | None = None
        if contract.quantity_is_base_asset:
            quote_turnover = sum((t.price * t.quantity for t in ordered), Decimal("0")).quantize(
                TURNOVER_QUANTUM_V1, rounding=ROUND_HALF_EVEN
            )
            quote_turnover_unit = contract.quote_asset

        # Availability is the LAST contributing arrival, not the last trade's
        # exchange time: a bar cannot be held before its final input arrived.
        research_available_at_nanos = max(t.local_timestamp_nanos for t in ordered)
        trade_manifest_hash = canonical_hash(
            {
                "parser_semantic_version": TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
                "builder_semantic_version": BAR_BUILDER_SEMANTIC_VERSION_V1,
                "symbol": symbol,
                "bar_open_at": bar_open_at.isoformat(),
                "trades": [
                    {
                        "trade_id": t.trade_id,
                        "exchange_timestamp": t.exchange_timestamp.isoformat(),
                        "sequence": t.sequence,
                        "price": str(t.price),
                        "quantity": str(t.quantity),
                        "side": t.side,
                        "local_timestamp_nanos": t.local_timestamp_nanos,
                        "record_content_hash": t.record_content_hash,
                    }
                    for t in ordered
                ],
            }
        )
        content_hash = canonical_hash(
            {
                "lifecycle": CAPTURE_LIFECYCLE_V1,
                "builder_semantic_version": BAR_BUILDER_SEMANTIC_VERSION_V1,
                "symbol": symbol,
                "bar_open_at": bar_open_at.isoformat(),
                "bar_close_at": (bar_open_at + BAR_INTERVAL_V1).isoformat(),
                "open_price": str(first.price),
                "high_price": str(max(t.price for t in ordered)),
                "low_price": str(min(t.price for t in ordered)),
                "close_price": str(last.price),
                "base_volume": str(base_volume),
                "base_volume_unit": contract.base_asset,
                "quote_turnover": None if quote_turnover is None else str(quote_turnover),
                "quote_turnover_unit": quote_turnover_unit,
                "trade_count": len(ordered),
                "first_trade_id": first.trade_id,
                "last_trade_id": last.trade_id,
                "research_available_at_nanos": research_available_at_nanos,
                "open_is_sequence_ambiguous": _price_group_ambiguous(leading),
                "close_is_sequence_ambiguous": _price_group_ambiguous(trailing),
                "trade_manifest_hash": trade_manifest_hash,
            }
        )
        bars.append(
            ReconstructedTradeBarV1(
                symbol=symbol,
                bar_open_at=bar_open_at,
                bar_close_at=bar_open_at + BAR_INTERVAL_V1,
                open_price=first.price,
                high_price=max(t.price for t in ordered),
                low_price=min(t.price for t in ordered),
                close_price=last.price,
                base_volume=base_volume,
                base_volume_unit=contract.base_asset,
                quote_turnover=quote_turnover,
                quote_turnover_unit=quote_turnover_unit,
                trade_count=len(ordered),
                first_trade_id=first.trade_id,
                last_trade_id=last.trade_id,
                research_available_at_nanos=research_available_at_nanos,
                open_is_sequence_ambiguous=_price_group_ambiguous(leading),
                close_is_sequence_ambiguous=_price_group_ambiguous(trailing),
                trade_manifest_hash=trade_manifest_hash,
                builder_semantic_version=BAR_BUILDER_SEMANTIC_VERSION_V1,
                content_hash=content_hash,
            )
        )
    return tuple(bars)


def first_strictly_later_bar(
    bars: Sequence[ReconstructedTradeBarV1],
    *,
    research_available_at_nanos: int,
) -> ReconstructedTradeBarV1 | None:
    """The first bar whose OPEN boundary is strictly after an availability instant.

    Strictly: a bar opening exactly at the availability instant is refused. This
    is the capture-path equivalent of the existing research execution principle
    ``entry_bar.bar_open_at > research_available_at`` and admits no same-instant
    or earlier entry. The comparison is in exact nanoseconds -- comparing against
    a microsecond-truncated availability would move the cutoff *earlier* and
    could admit a bar that is not in fact strictly later.
    """
    for bar in sorted(bars, key=lambda b: b.bar_open_at):
        if bar_open_nanos(bar) > research_available_at_nanos:
            return bar
    return None


def bar_open_nanos(bar: ReconstructedTradeBarV1) -> int:
    """Exact nanosecond value of a minute-aligned bar open boundary."""
    return int(bar.bar_open_at.timestamp()) * 1_000_000_000
