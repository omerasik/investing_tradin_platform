"""TEST FIXTURE risk limits: explicit values for fixture policy documents.

R9 made reviewed risk-policy documents strict: every base limit must be present,
nothing is inherited from the ``RiskPolicy`` dataclass defaults. Fixtures that
used to rely on that silent fill now state their limits here, explicitly. These
are test values, not owner decisions and not platform defaults.
"""

from __future__ import annotations

FIXTURE_BASE_RISK_LIMITS: dict[str, object] = {
    "minimum_data_quality": "0.95",
    "maximum_spread_fraction": "0.01",
    "maximum_order_notional": "10000",
    "maximum_position_notional": "25000",
    "maximum_daily_order_notional": "50000",
    "maximum_event_risk": "0.50",
    "maximum_expected_slippage_fraction": "0.02",
    "max_market_age_seconds": 60,
}


def fixture_risk_payload(**overrides: object) -> dict[str, object]:
    """Fixture base limits overridden by the test's own explicit values."""
    return {**FIXTURE_BASE_RISK_LIMITS, **overrides}
