"""Module 3J.2b.1 -- generic signed OPEN-to-OPEN price-return research primitive.

``run_vectorized_backtest()`` (``research.py``) is documented and implemented
as close-to-close accounting over a continuously held position; passing
``OPEN`` prices into its ``closes`` parameter would silently misuse its
contract. This module adds one small, deterministic, funding-free,
strategy-agnostic decomposition primitive for a single non-overlapping,
bounded-exposure research trade between two tradable bars' ``OPEN`` prices:

    gross_return   = exposure * (exit_open / entry_open - 1)
    entry_turnover = abs(exposure)
    exit_turnover  = abs(exposure)
    entry_cost     = CostModel.cost(entry_turnover)
    exit_cost      = CostModel.cost(exit_turnover)
    net_return     = gross_return - entry_cost - exit_cost

This is a research return decomposition, not a complete backtest engine, and
it is never fed into ``run_vectorized_backtest()``. It is funding-free by
construction: it does not import ``paper_execution`` and never calls
``apply_funding()``. It operates directly on two
``AuthoritativeTradableBarV2`` bars (never on raw floats) so "same instrument"
and "same sealed dataset" are structural preconditions checked here, not
caller-trusted assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .research import CostModel
from .tradable_bar_evidence_v2 import AuthoritativeTradableBarV2


class SignedPriceReturnV2Error(ValueError):
    """Raised for any invalid signed OPEN-to-OPEN return computation."""


@dataclass(frozen=True, slots=True)
class SignedOpenToOpenReturnV2:
    """A single non-overlapping trade's price-return decomposition. Funding-free."""

    instrument_id: str
    dataset_version_id: UUID
    entry_time: datetime
    exit_time: datetime
    exposure: Decimal
    entry_open: Decimal
    exit_open: Decimal
    gross_return: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_return: Decimal


def compute_signed_open_to_open_return(
    *,
    entry_bar: AuthoritativeTradableBarV2,
    exit_bar: AuthoritativeTradableBarV2,
    exposure: Decimal,
    maximum_absolute_exposure: Decimal,
    cost_model: CostModel,
) -> SignedOpenToOpenReturnV2:
    if entry_bar.instrument_id != exit_bar.instrument_id:
        raise SignedPriceReturnV2Error("signed_open_to_open_return_instrument_mismatch")
    if entry_bar.dataset_version_id != exit_bar.dataset_version_id:
        raise SignedPriceReturnV2Error("signed_open_to_open_return_dataset_mismatch")
    if exit_bar.bar_open_at <= entry_bar.bar_open_at:
        raise SignedPriceReturnV2Error("signed_open_to_open_return_exit_not_after_entry")

    entry_open = entry_bar.open
    exit_open = exit_bar.open
    if not entry_open.is_finite() or not exit_open.is_finite():
        raise SignedPriceReturnV2Error("signed_open_to_open_return_non_finite_price")
    if entry_open <= 0 or exit_open <= 0:
        raise SignedPriceReturnV2Error("signed_open_to_open_return_non_positive_price")
    if not exposure.is_finite() or not maximum_absolute_exposure.is_finite():
        raise SignedPriceReturnV2Error("signed_open_to_open_return_non_finite_exposure")
    if not (Decimal("0") < maximum_absolute_exposure <= Decimal("1")):
        raise SignedPriceReturnV2Error("maximum_absolute_exposure_out_of_bounds")
    if not (-maximum_absolute_exposure <= exposure <= maximum_absolute_exposure):
        raise SignedPriceReturnV2Error("signed_exposure_out_of_bounds")

    gross_return = exposure * (exit_open / entry_open - Decimal("1"))
    entry_turnover = abs(exposure)
    exit_turnover = abs(exposure)
    entry_cost = cost_model.cost(entry_turnover)
    exit_cost = cost_model.cost(exit_turnover)
    net_return = gross_return - entry_cost - exit_cost

    return SignedOpenToOpenReturnV2(
        instrument_id=entry_bar.instrument_id,
        dataset_version_id=entry_bar.dataset_version_id,
        entry_time=entry_bar.bar_open_at,
        exit_time=exit_bar.bar_open_at,
        exposure=exposure,
        entry_open=entry_open,
        exit_open=exit_open,
        gross_return=gross_return,
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        net_return=net_return,
    )
