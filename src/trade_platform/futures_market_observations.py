"""Typed canonical payloads for futures settlement and open-interest observations.

Module 3I.1 (roadmap NEXT-03 phase 1). This module defines the **canonical
normalized financial payload** for two new observation kinds. It is not a
second pipeline: capture, normalization, sealing, Data Health and research
reads all remain
``historical_market_data.PostgresHistoricalMarketDataPipeline``, which imports
these contracts and writes the typed row inside the same transaction as the
normalized envelope it belongs to.

**Single-authority rule.** For these kinds the typed table is the only durable
authority for the financial value. ``historical_normalized_observations
.normalized_value`` stores a non-financial pointer marker
(:func:`canonical_payload_marker`) instead of a second copy, dataset sealing
hashes the canonical typed values, and the backward-compatible JSON projection
research readers expect is synthesized deterministically at read time by
:meth:`FuturesSettlementPayload.as_normalized_projection`. A typed row and its
envelope are one-to-one and cannot diverge: the envelope is enforced by the
typed table's primary-key foreign key, and the typed row by a deferred
constraint trigger on the envelope.

Two things this module deliberately refuses to do:

* **Settlement is not a close.** ``SETTLEMENT_PRICE`` is a distinct kind from
  ``OHLCV``; an exchange settlement price is a published, often
  formula-derived value that is not the last trade, and nothing here lets one
  stand in for the other.
* **Open interest is never an unqualified number.** Every observation carries
  an explicit :class:`OpenInterestUnit`, and no conversion between units is
  performed. A contract count and a base-asset quantity are different
  measurements of different things.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

SETTLEMENT_PAYLOAD_TABLE: Final = "futures_settlement_observations"
OPEN_INTEREST_PAYLOAD_TABLE: Final = "futures_open_interest_observations"


class FuturesObservationPayloadError(ValueError):
    """Raised only for programmer error; provider data problems become issues."""


class SettlementFinality(StrEnum):
    """Exchanges publish a preliminary settlement, then confirm or restate it."""

    PRELIMINARY = "PRELIMINARY"
    FINAL = "FINAL"


class OpenInterestUnit(StrEnum):
    #: A count of exchange contracts -- the canonical futures representation.
    CONTRACTS = "CONTRACTS"
    #: A quantity of the base asset (reserved for 3I.2 crypto venues).
    BASE_ASSET = "BASE_ASSET"
    #: A notional quantity in the quote asset (reserved for 3I.2).
    QUOTE_NOTIONAL = "QUOTE_NOTIONAL"


#: Units whose meaning depends on naming the asset they are counted in.
_UNITS_REQUIRING_ASSET = frozenset({OpenInterestUnit.BASE_ASSET, OpenInterestUnit.QUOTE_NOTIONAL})

#: 3I.1 accepts only the exchange contract count for a futures instrument.
#: BASE_ASSET/QUOTE_NOTIONAL are modelled so the unit dimension is honest from
#: the start, but a futures source supplying one fails closed here rather than
#: being silently converted into contracts -- that conversion needs a contract
#: multiplier and a price, neither of which an open-interest record carries.
FUTURES_SUPPORTED_OPEN_INTEREST_UNITS: Final = frozenset({OpenInterestUnit.CONTRACTS})


def canonical_payload_marker(table: str) -> dict[str, object]:
    """The non-financial marker stored in ``normalized_value`` for a typed kind."""
    return {"canonical_payload_table": table}


def _decimal(payload: dict[str, object], key: str, issues: list[str]) -> Decimal | None:
    raw = payload.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        issues.append(f"missing_{key}")
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        issues.append(f"invalid_{key}")
        return None
    if not value.is_finite():
        issues.append(f"invalid_{key}")
        return None
    return value


def _text(payload: dict[str, object], key: str, issues: list[str]) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        issues.append(f"missing_{key}")
    return value


def _date(payload: dict[str, object], key: str, issues: list[str]) -> date | None:
    raw = payload.get(key)
    if raw is None:
        issues.append(f"missing_{key}")
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        issues.append(f"invalid_{key}")
        return None


def _instant(payload: dict[str, object], key: str, issues: list[str]) -> datetime | None:
    raw = payload.get(key)
    if raw is None:
        issues.append(f"missing_{key}")
        return None
    if isinstance(raw, datetime):
        value = raw
    else:
        try:
            value = datetime.fromisoformat(str(raw))
        except ValueError:
            issues.append(f"invalid_{key}")
            return None
    if value.tzinfo is None or value.utcoffset() is None:
        issues.append(f"naive_{key}")
        return None
    return value


@dataclass(frozen=True, slots=True)
class FuturesSettlementPayload:
    """One exchange-published settlement price. Never an OHLCV close or last trade."""

    settlement_price: Decimal
    price_currency: str
    settlement_date: date
    settlement_effective_at: datetime
    finality: SettlementFinality
    quote_unit: str

    def canonical_tuple(self) -> tuple[str, ...]:
        """Stable serialization contributed to a sealed dataset's content hash."""
        return (
            SETTLEMENT_PAYLOAD_TABLE,
            str(self.settlement_price),
            self.price_currency,
            self.settlement_date.isoformat(),
            self.settlement_effective_at.isoformat(),
            self.finality.value,
            self.quote_unit,
        )

    def as_normalized_projection(self) -> dict[str, object]:
        """Backward-compatible read projection derived from the canonical row.

        Synthesized at read time so research callers that expect a
        ``normalized_value`` mapping keep working without a second stored copy
        of the value that could drift from this one.
        """
        return {
            "settlement_price": str(self.settlement_price),
            "price_currency": self.price_currency,
            "settlement_date": self.settlement_date.isoformat(),
            "settlement_effective_at": self.settlement_effective_at.isoformat(),
            "finality": self.finality.value,
            "quote_unit": self.quote_unit,
        }


@dataclass(frozen=True, slots=True)
class FuturesOpenInterestPayload:
    """One open-interest observation, always carrying its explicit unit."""

    open_interest: Decimal
    unit: OpenInterestUnit
    observed_at: datetime
    unit_asset: str | None = None

    def canonical_tuple(self) -> tuple[str, ...]:
        return (
            OPEN_INTEREST_PAYLOAD_TABLE,
            str(self.open_interest),
            self.unit.value,
            self.observed_at.isoformat(),
            self.unit_asset or "",
        )

    def as_normalized_projection(self) -> dict[str, object]:
        return {
            "open_interest": str(self.open_interest),
            "unit": self.unit.value,
            "observed_at": self.observed_at.isoformat(),
            "unit_asset": self.unit_asset,
        }


def parse_settlement_payload(
    payload: dict[str, object],
) -> tuple[FuturesSettlementPayload | None, tuple[str, ...]]:
    """Parse without repairing. Any problem yields issues, never a guessed value."""
    issues: list[str] = []
    price = _decimal(payload, "settlement_price", issues)
    currency = _text(payload, "price_currency", issues).upper()
    settlement_date = _date(payload, "settlement_date", issues)
    effective_at = _instant(payload, "settlement_effective_at", issues)
    quote_unit = _text(payload, "quote_unit", issues)
    finality_raw = str(payload.get("finality", "")).strip().upper()

    if price is not None and price <= 0:
        issues.append("non_positive_settlement_price")
    if currency and not 3 <= len(currency) <= 12:
        issues.append("invalid_price_currency")
    try:
        finality = SettlementFinality(finality_raw)
    except ValueError:
        issues.append("invalid_settlement_finality")
        finality = SettlementFinality.PRELIMINARY
    # Explicit narrowing rather than `assert`, which is stripped under -O.
    if issues or price is None or settlement_date is None or effective_at is None:
        return None, tuple(dict.fromkeys(issues)) or ("invalid_settlement_payload",)
    return (
        FuturesSettlementPayload(
            settlement_price=price,
            price_currency=currency,
            settlement_date=settlement_date,
            settlement_effective_at=effective_at,
            finality=finality,
            quote_unit=quote_unit,
        ),
        (),
    )


def parse_open_interest_payload(
    payload: dict[str, object], *, supported_units: frozenset[OpenInterestUnit]
) -> tuple[FuturesOpenInterestPayload | None, tuple[str, ...]]:
    """Parse without repairing, and without ever converting between units."""
    issues: list[str] = []
    value = _decimal(payload, "open_interest", issues)
    observed_at = _instant(payload, "observed_at", issues)
    unit_raw = str(payload.get("unit", "")).strip().upper()
    unit_asset = str(payload.get("unit_asset", "")).strip() or None

    if value is not None and value < 0:
        issues.append("negative_open_interest")
    if not unit_raw:
        issues.append("missing_open_interest_unit")
        unit = None
    else:
        try:
            unit = OpenInterestUnit(unit_raw)
        except ValueError:
            issues.append("unsupported_open_interest_unit")
            unit = None
    if unit is not None:
        if unit not in supported_units:
            issues.append("unsupported_open_interest_unit")
        if unit in _UNITS_REQUIRING_ASSET and not unit_asset:
            issues.append("open_interest_unit_requires_asset")
        if unit is OpenInterestUnit.CONTRACTS and unit_asset:
            issues.append("contract_count_cannot_declare_unit_asset")
    # Explicit narrowing rather than `assert`, which is stripped under -O.
    if issues or value is None or observed_at is None or unit is None:
        return None, tuple(dict.fromkeys(issues)) or ("invalid_open_interest_payload",)
    return (
        FuturesOpenInterestPayload(
            open_interest=value, unit=unit, observed_at=observed_at, unit_asset=unit_asset
        ),
        (),
    )
