"""Typed canonical payload for exchange-published futures settlement prices.

Module 3I.1 (roadmap NEXT-03 phase 1). This module defines the **canonical
normalized financial payload** for the ``SETTLEMENT_PRICE`` observation kind. It
is not a second pipeline: capture, normalization, sealing, Data Health and
research reads all remain
``historical_market_data.PostgresHistoricalMarketDataPipeline``, which imports
these contracts and writes the typed row inside the same transaction as the
normalized envelope it belongs to.

Module 3I.2 moved the open-interest payload out of this module into
``market_observation_payloads``, because open interest is a cross-asset
measurement and its authority must not live inside a futures-named module. Its
canonical serialization was frozen across that move; see that module's
docstring. Settlement stays here: an exchange settlement price is a
futures-market concept with delivery-cycle semantics, and 3I.2 deliberately did
not extend it to crypto dated futures.

**Single-authority rule.** For this kind the typed table is the only durable
authority for the financial value. ``historical_normalized_observations
.normalized_value`` stores a non-financial pointer marker
(``canonical_payload_marker``) instead of a second copy, dataset sealing hashes
the canonical typed values, and the backward-compatible JSON projection research
readers expect is synthesized deterministically at read time by
:meth:`FuturesSettlementPayload.as_normalized_projection`. A typed row and its
envelope are one-to-one and cannot diverge: the envelope is enforced by the
typed table's primary-key foreign key, and the typed row by a deferred
constraint trigger on the envelope.

**Settlement is not a close.** ``SETTLEMENT_PRICE`` is a distinct kind from
``OHLCV``; an exchange settlement price is a published, often formula-derived
value that is not the last trade, and nothing here lets one stand in for the
other. Module 3I.2 extends that same refusal to ``MARK_PRICE`` and
``INDEX_PRICE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from .market_observation_payloads import (
    ObservationPayloadError,
    date_field,
    decimal_field,
    instant_field,
    text_field,
)

SETTLEMENT_PAYLOAD_TABLE: Final = "futures_settlement_observations"

#: Retained for callers that predate Module 3I.2's shared payload module.
FuturesObservationPayloadError = ObservationPayloadError


class SettlementFinality(StrEnum):
    """Exchanges publish a preliminary settlement, then confirm or restate it."""

    PRELIMINARY = "PRELIMINARY"
    FINAL = "FINAL"


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


def parse_settlement_payload(
    payload: dict[str, object],
) -> tuple[FuturesSettlementPayload | None, tuple[str, ...]]:
    """Parse without repairing. Any problem yields issues, never a guessed value."""
    issues: list[str] = []
    price = decimal_field(payload, "settlement_price", issues)
    currency = text_field(payload, "price_currency", issues).upper()
    settlement_date = date_field(payload, "settlement_date", issues)
    effective_at = instant_field(payload, "settlement_effective_at", issues)
    quote_unit = text_field(payload, "quote_unit", issues)
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
