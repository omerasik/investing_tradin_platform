"""Development-only headroom over recorded Decimal gross metrics; never net P&L.

The threshold is the rerun's stored sum-of-step-returns / turnover * 10,000,
quantized there to 1e-18. It is NOT the zero of compounded total_return.
No rerun, fee, spread conversion, execution assumption or approval is created.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from .strategy_lab_policies_v1 import SlippageScenarioV1, StrategyLabPolicyError

# Existing prepared proposal, not a new cost estimate or an approved envelope.
PROPOSAL_SOURCE_V1 = "docs/current-phase.yaml@8f56062:prepared_packets.or6:CONSERVATIVE_MEDIUM"
PREPARED_SCENARIOS_V1 = tuple(
    SlippageScenarioV1(f"conservative_medium_{name}", value,
                       "Existing prepared CONSERVATIVE_MEDIUM proposal; no owner approval asserted")
    for name, value in (("1bps", "1"), ("2_5bps", "2.5"), ("5bps", "5"))
)


@dataclass(frozen=True, slots=True)
class ScenarioHeadroomV1:
    name: str
    cost_bps_per_side: str
    rationale: str
    source: str
    status: str
    remaining_bps_per_side: str | None
    relation: str
    in_net: bool = False


@dataclass(frozen=True, slots=True)
class CostFragilityV1:
    status: str
    threshold_bps_per_side: str | None
    search_threshold_bps_per_side: str | None
    lag_micros: int | None
    scenarios: list[ScenarioHeadroomV1]
    reasons: list[str]
    claim: str = "DEVELOPMENT_GROSS_COST_SENSITIVITY_NOT_VALIDATED"
    units: str = "BPS_PER_SIDE"
    metric: str = "break_even_bps_per_side"
    basis: str = "SUM_OF_GROSS_STEP_RETURNS_DIVIDED_BY_TURNOVER_TIMES_10000_NOT_COMPOUNDED"
    threshold_precision: str = "RECORDED_DECIMAL_RERUN_QUANTUM_1E-18"
    verified_account_fees: str = "UNAVAILABLE"
    observed_spread_comparison: str = "UNAVAILABLE_NO_CANDIDATE_AND_WINDOW_BOUND_SPREAD_EVIDENCE"
    funding: str = "EXCLUDED_FROM_GROSS_HEADROOM_FORWARD_FUNDING_REMAINS_SEPARATE"
    net_profitability: str = "UNAVAILABLE"
    independent_validation: str = "NOT_INFERRED"


def _decimal(raw: Any) -> Decimal | None:
    # Financial text only: float/bool coercion would confer unsupported precision.
    if not isinstance(raw, str) or len(raw) > 256:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and abs(int(value.as_tuple().exponent)) <= 1000 else None


def cost_fragility_v1(*, baseline: Mapping[str, Any], search: Mapping[str, Any],
                      lag_micros: int | None, cost_policy: Mapping[str, Any],
                      before_holdout: bool | None, selection_established: bool) -> CostFragilityV1:
    """Restate a stored baseline; subtract only explicitly named proposed slippage.

    Remainder excludes fees, spread and funding and is never called net or a
    total execution-cost estimate. Search numbers are visible but never used
    as the numerical authority for a headroom decision.
    """
    threshold_text = baseline.get("break_even_bps_per_side")
    threshold = _decimal(threshold_text)
    turnover = _decimal(baseline.get("turnover"))
    reasons = []
    if not selection_established:
        reasons.append("DECIMAL_SELECTION_NOT_ESTABLISHED")
    if before_holdout is not True:
        reasons.append("DEVELOPMENT_BOUNDARY_NOT_ESTABLISHED")
    if lag_micros is None:
        reasons.append("BASELINE_LAG_UNAVAILABLE")
    if baseline.get("numeric_tier") != "DECIMAL_AUTHORITATIVE":
        reasons.append("NO_AUTHORITATIVE_DECIMAL_BASELINE")
    if baseline.get("cost_mode") != "GROSS_NON_PROMOTABLE" or baseline.get("funding_mode") is not None:
        reasons.append("BASELINE_NOT_UNFUNDED_GROSS")
    if threshold is None:
        reasons.append("MISSING_OR_NON_NUMERIC_DECIMAL_THRESHOLD")
    if turnover is None or turnover <= 0:
        reasons.append("MISSING_OR_NONPOSITIVE_TURNOVER")
    available = not reasons
    scenarios = [(item, PROPOSAL_SOURCE_V1) for item in PREPARED_SCENARIOS_V1]
    for raw in cost_policy.get("slippage_scenarios") or []:
        try:
            if not isinstance(raw, Mapping) or _decimal(raw.get("cost_bps_per_side")) is None:
                raise StrategyLabPolicyError("scenario_cost_not_exact_text")
            with localcontext() as ctx:
                ctx.prec = 2300
                item = SlippageScenarioV1(**raw)
        except (TypeError, ValueError):
            reasons.append("MALFORMED_BOUND_SCENARIO_UNAVAILABLE")
            continue
        scenarios.append((item, "STUDY_BOUND_COST_POLICY_NOT_APPROVAL"))
    rows = []
    for item, source in scenarios:
        cost = _decimal(item.cost_bps_per_side)
        remainder = None
        relation = "UNAVAILABLE"
        if available and threshold is not None and cost is not None:
            # Enough digits for exact subtraction, independent of the ambient context.
            with localcontext() as ctx:
                ctx.prec = max(threshold.adjusted(), cost.adjusted()) - min(
                    int(threshold.as_tuple().exponent), int(cost.as_tuple().exponent)) + 3
                remainder = format(threshold - cost, "f")
            relation = "ABOVE" if threshold > cost else "EQUAL" if threshold == cost else "BELOW"
        rows.append(ScenarioHeadroomV1(item.name, item.cost_bps_per_side, item.rationale, source,
                                      "PROPOSED_SENSITIVITY_SCENARIO", remainder, relation))
    fee_reason = ("MISSING_VERIFIED_ACCOUNT_FEES" if cost_policy.get("venue_fees") is None
                  else "ACCOUNT_FEES_NOT_VERIFIED_BY_THIS_PROJECTION")
    reasons.extend([fee_reason, "SPREAD_IS_DIAGNOSTIC_NEVER_A_SLIPPAGE_MODEL",
                    "SCENARIO_REMAINDER_EXCLUDES_FEES_SPREAD_AND_FUNDING"])
    return CostFragilityV1(
        status=("POSITIVE_GROSS_HEADROOM" if threshold is not None and threshold > 0
                else "NO_POSITIVE_GROSS_HEADROOM") if available else "UNAVAILABLE",
        threshold_bps_per_side=str(threshold_text) if available else None,
        search_threshold_bps_per_side=(str(search["break_even_bps_per_side"])
                                      if search.get("break_even_bps_per_side") is not None else None),
        lag_micros=lag_micros, scenarios=rows, reasons=reasons,
    )
