"""Local shadow-mode comparison and incident evidence; it never consumes a live broker feed."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from .domain import utc_now
from .paper_execution import PaperOrder


class ShadowModeValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    comparison_id: UUID
    primary_intent_id: UUID
    shadow_intent_id: UUID
    matched: bool
    differences: tuple[str, ...]
    compared_at: datetime


def compare_paper_orders(primary: PaperOrder, shadow: PaperOrder, quantity_tolerance: Decimal = Decimal("0"), price_tolerance: Decimal = Decimal("0.01")) -> ShadowComparison:
    if quantity_tolerance < 0 or price_tolerance < 0:
        raise ShadowModeValidationError("invalid_shadow_tolerance")
    differences: list[str] = []
    if primary.intent.instrument_id != shadow.intent.instrument_id or primary.intent.side != shadow.intent.side:
        differences.append("intent_identity_mismatch")
    if primary.status != shadow.status:
        differences.append("order_status_mismatch")
    if abs(primary.filled_quantity - shadow.filled_quantity) > quantity_tolerance:
        differences.append("filled_quantity_mismatch")
    if primary.average_fill_price is None or shadow.average_fill_price is None:
        if primary.average_fill_price != shadow.average_fill_price:
            differences.append("fill_price_availability_mismatch")
    elif abs(primary.average_fill_price - shadow.average_fill_price) > price_tolerance:
        differences.append("fill_price_mismatch")
    return ShadowComparison(uuid4(), primary.intent.intent_id, shadow.intent.intent_id, not differences, tuple(differences), utc_now())


@dataclass(frozen=True, slots=True)
class FailureDrillResult:
    drill_id: UUID
    scenario: str
    expected_protection: str
    observed_protection: str
    passed: bool
    recorded_at: datetime


def record_failure_drill(scenario: str, expected_protection: str, observed_protection: str) -> FailureDrillResult:
    if not scenario or not expected_protection or not observed_protection:
        raise ShadowModeValidationError("invalid_failure_drill")
    return FailureDrillResult(uuid4(), scenario, expected_protection, observed_protection, expected_protection == observed_protection, utc_now())


def requires_incident(comparison: ShadowComparison) -> bool:
    return not comparison.matched or any(reason in comparison.differences for reason in ("intent_identity_mismatch", "order_status_mismatch"))
