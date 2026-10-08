"""Phase R4.6 -- owner research policies as versioned, identity-bound objects (OR-3, OR-5, OR-6).

``RESEARCH_ONLY``. The owner decided the numeric doctrine (OR-3), the T2
research timing (OR-5) and the research cost methodology (OR-6) on 2026-10-08.
Each decision lives here as one immutable payload whose SHA-256 is its
identity; a study binds the identity, so changing a policy is a new identity,
never a silent re-reading. Nothing here evaluates a strategy.

OR-3 -- numeric doctrine (:func:`or3_numeric_policy_v1`)
    float64 is the SEARCH tier only; every search result is
    ``SEARCH_NON_AUTHORITATIVE``. ``Decimal`` is the authoritative tier. The
    complete signal and economic path is recomputed in ``Decimal`` for the
    scopes listed in ``authoritative_rerun_scope``. At a decision boundary
    ``Decimal`` wins; a threshold decision whose authority cannot be
    established fails closed. The near-tie flag compares the two sides of every
    decision with a *relative* tolerance (an engineering numeric tolerance on
    float rounding, not an economic threshold); a flagged candidate joins the
    rerun set.

OR-5 -- T2 research timing (:func:`or5_t2_timing_policy_v1`)
    A conditional T2 observation is treated as known at event time plus the
    owner-declared dissemination lag of 2 s. That is a research policy, not a
    measured venue latency and not archive publication time (archive
    ``Last-Modified`` stays a separate clock). The 5 s, 30 s and 60 s sweep is
    mandatory; every sweep result is kept and a material change under it fails
    promotion. T2 results stay ``CONDITIONAL``.

OR-6 -- research costs (:class:`CostPolicyV1`)
    Venue fees, observed spread, slippage and funding are four separate
    components. Fees are an owner/operator input (:class:`FeeScheduleV1`); with
    none supplied the policy is ``GROSS_NON_PROMOTABLE``: search reports gross
    economics and the *break-even* cost per side, invents no fee, and nothing
    cost-dependent may be promoted. Observed T4 spread is diagnostic evidence,
    never a slippage model. Slippage is a list of explicit, named, versioned
    stress scenarios supplied by the operator; this module ships none and never
    picks one. Funding is not modelled for pre-holdout T2 research: holding
    across funding windows is reported, and a cost-complete promotion of such a
    strategy fails closed until admissible funding evidence exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from .strategy_lab_study_v1 import identity_hash_v1

OR3_SCHEMA_VERSION_V1: Final = "or3-numeric-policy-v1"
OR5_SCHEMA_VERSION_V1: Final = "or5-t2-timing-policy-v1"
OR6_SCHEMA_VERSION_V1: Final = "or6-cost-policy-v1"

NUMERIC_TIER_SEARCH: Final = "SEARCH_NON_AUTHORITATIVE"
NUMERIC_TIER_AUTHORITATIVE: Final = "DECIMAL_AUTHORITATIVE"

#: Decimal context precision for the authoritative tier (digits).
AUTHORITATIVE_DECIMAL_PRECISION_V1: Final = 50
#: Relative float tolerance for near-tie flags at decision comparisons. A
#: numeric-rounding tolerance chosen by the OR-3 evidence packet (2026-10-06:
#: it caught every float/Decimal signal divergence), not an economic threshold.
NEAR_TIE_RELATIVE_TOLERANCE_V1: Final = "1E-9"
#: Relative band around the float selection cutoff inside which a candidate's
#: rank may be an artefact of float rounding in the *metric*; every candidate in
#: it is recomputed in Decimal. A numeric tolerance (float64 metric error over a
#: year of 1-minute bars is far below it), not an economic threshold.
SELECTION_CUTOFF_ERROR_BAND_RELATIVE_V1: Final = "1E-6"

OR3_RERUN_SCOPE_V1: Final = (
    "EVERY_FROZEN_CANDIDATE",
    "EVERY_CANDIDATE_INSIDE_THE_SELECTION_CUTOFF_ERROR_BAND",
    "EVERY_NEAR_TIE_FLAGGED_CANDIDATE",
    "ALL_VALIDATION_HOLDOUT_AND_PROMOTION_RESULTS",
    "ALL_PERSISTED_AUTHORITATIVE_FINANCIAL_METRICS",
    "LIVE_SIGNAL_DECISIONS_WHOSE_FINANCIAL_STATE_DEPENDS_ON_ARITHMETIC",
    "PAPER_POSITIONS_CASH_FILLS_AND_PNL",
)

OR5_BASELINE_LAG_V1: Final = timedelta(seconds=2)
OR5_SWEEP_LAGS_V1: Final = (timedelta(seconds=5), timedelta(seconds=30), timedelta(seconds=60))

COST_MODE_GROSS: Final = "GROSS_NON_PROMOTABLE"
COST_MODE_FEE_SCHEDULED: Final = "FEE_SCHEDULED"
FUNDING_NOT_MODELLED_T2: Final = "NOT_MODELLED_REPORT_EXPOSURE_FAIL_CLOSED_FOR_COST_COMPLETE_PROMOTION"
SPREAD_DIAGNOSTIC_ONLY: Final = "T4_OBSERVED_SPREAD_DIAGNOSTIC_ONLY_NEVER_A_SLIPPAGE_MODEL"

_SLUG: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class StrategyLabPolicyError(ValueError):
    """Raised when a policy is incomplete, invented or not the owner-approved one."""


def _micros(value: timedelta) -> int:
    return (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds


@dataclass(frozen=True, slots=True)
class PolicyV1:
    """One immutable policy payload and its identity."""

    payload: dict[str, Any]

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.payload)

    @property
    def slot(self) -> str:
        """The value a study's policy slot carries: ``<schema>:<sha256>``."""
        return f"{self.payload['schema_version']}:{self.content_hash}"


def or3_numeric_policy_v1() -> PolicyV1:
    """The owner-approved OR-3 numeric doctrine (2026-10-08)."""
    return PolicyV1({
        "schema_version": OR3_SCHEMA_VERSION_V1,
        "decision": "OR-3 approved by the owner 2026-10-08",
        "search_tier": {"arithmetic": "IEEE754_FLOAT64", "label": NUMERIC_TIER_SEARCH},
        "authoritative_tier": {"arithmetic": "PYTHON_DECIMAL", "precision": AUTHORITATIVE_DECIMAL_PRECISION_V1,
                               "rounding": "ROUND_HALF_EVEN", "label": NUMERIC_TIER_AUTHORITATIVE},
        "authoritative_rerun_scope": list(OR3_RERUN_SCOPE_V1),
        "rerun_recomputes": "COMPLETE_SIGNAL_AND_ECONOMIC_PATH",
        "near_tie_relative_tolerance": NEAR_TIE_RELATIVE_TOLERANCE_V1,
        "selection_cutoff_error_band_relative": SELECTION_CUTOFF_ERROR_BAND_RELATIVE_V1,
        "near_tie_rerun_scope": "EVERY_FLAGGED_CANDIDATE_OF_THE_STUDY",
        "boundary_rule": "DECIMAL_WINS",
        "unestablished_authority_rule": "FAIL_CLOSED",
        "library_float_behaviour_in_identity": False,
    })


def or5_t2_timing_policy_v1() -> PolicyV1:
    """The owner-approved OR-5 conditional T2 timing policy (2026-10-08)."""
    return PolicyV1({
        "schema_version": OR5_SCHEMA_VERSION_V1,
        "decision": "OR-5 approved by the owner 2026-10-08",
        "applies_to": "T2_EVENT_TIME_CONDITIONAL_RESEARCH_ONLY",
        "baseline_dissemination_lag_micros": _micros(OR5_BASELINE_LAG_V1),
        "mandatory_sweep_lags_micros": [_micros(lag) for lag in OR5_SWEEP_LAGS_V1],
        "nature": "OWNER_DECLARED_CONSERVATIVE_RESEARCH_POLICY_NOT_A_MEASURED_VENUE_LATENCY",
        "supporting_evidence": "R3B.3 T2/T4 overlap: 114,031 matched trades, T4 knowledge-minus-event "
                               "upper bound p99 <= 0.38 s on 2026-10-02",
        "archive_last_modified": "SEPARATE_CLOCK_FILE_AVAILABILITY_NOT_DISSEMINATION",
        "sweep_rule": "MATERIAL_CHANGE_IN_SIGNAL_SELECTION_SIGN_OR_VERDICT_FAILS_PROMOTION",
        "sweep_results": "ALL_RETAINED_NO_BEST_LAG_SELECTION",
        "missing_timing_evidence": "FAIL_CLOSED",
        "claim_ceiling": "CONDITIONAL_NEVER_T3_OR_T4_PROFESSIONAL",
    })


def or5_lags_v1() -> tuple[timedelta, ...]:
    """Baseline first, then the mandatory sweep, in order."""
    return (OR5_BASELINE_LAG_V1, *OR5_SWEEP_LAGS_V1)


def _decimal_text(value: object, name: str) -> str:
    if isinstance(value, (float, bool)):
        raise StrategyLabPolicyError(f"{name}_must_be_decimal_or_text")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise StrategyLabPolicyError(f"{name}_unparseable") from error
    if not number.is_finite() or number < 0:
        raise StrategyLabPolicyError(f"{name}_must_be_finite_and_nonnegative")
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


@dataclass(frozen=True, slots=True)
class FeeScheduleV1:
    """An owner/operator-verified venue fee schedule. Never defaulted, never scraped."""

    venue: str
    product: str
    tier: str
    maker_fee_bps: str
    taker_fee_bps: str
    verified_by: str
    verified_on: str
    source_reference: str

    def __post_init__(self) -> None:
        for name in ("venue", "product", "tier", "verified_by", "verified_on", "source_reference"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise StrategyLabPolicyError(f"fee_schedule_{name}_required")
        object.__setattr__(self, "maker_fee_bps", _decimal_text(self.maker_fee_bps, "maker_fee_bps"))
        object.__setattr__(self, "taker_fee_bps", _decimal_text(self.taker_fee_bps, "taker_fee_bps"))

    def payload(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (
            "venue", "product", "tier", "maker_fee_bps", "taker_fee_bps",
            "verified_by", "verified_on", "source_reference")}


@dataclass(frozen=True, slots=True)
class SlippageScenarioV1:
    """One explicit stress scenario: an additional cost per side, in bps, with a name and reason."""

    name: str
    cost_bps_per_side: str
    rationale: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _SLUG.match(self.name):
            raise StrategyLabPolicyError("slippage_scenario_name_must_be_a_slug")
        object.__setattr__(self, "cost_bps_per_side", _decimal_text(self.cost_bps_per_side, "slippage_bps"))
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise StrategyLabPolicyError("slippage_scenario_rationale_required")

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "cost_bps_per_side": self.cost_bps_per_side, "rationale": self.rationale}


@dataclass(frozen=True, slots=True)
class CostPolicyV1:
    """OR-6 research cost policy. ``fee_schedule=None`` means gross and non-promotable."""

    fee_schedule: FeeScheduleV1 | None = None
    slippage_scenarios: tuple[SlippageScenarioV1, ...] = ()
    fill_liquidity: str = "TAKER"

    def __post_init__(self) -> None:
        names = [scenario.name for scenario in self.slippage_scenarios]
        if len(set(names)) != len(names):
            raise StrategyLabPolicyError("slippage_scenario_names_must_be_unique")
        if self.fill_liquidity != "TAKER":
            # Bar-open fills cross the spread; a maker convention needs its own evidence.
            raise StrategyLabPolicyError("only_taker_fills_are_supported_by_the_bar_execution_convention")

    @property
    def mode(self) -> str:
        return COST_MODE_GROSS if self.fee_schedule is None else COST_MODE_FEE_SCHEDULED

    @property
    def promotable_cost_basis(self) -> bool:
        """Promotion needs a verified fee schedule and at least one approved stress scenario."""
        return self.fee_schedule is not None and bool(self.slippage_scenarios)

    def policy(self) -> PolicyV1:
        return PolicyV1({
            "schema_version": OR6_SCHEMA_VERSION_V1,
            "decision": "OR-6 approved by the owner 2026-10-08",
            "mode": self.mode,
            "components": ["VENUE_FEES", "OBSERVED_SPREAD", "SLIPPAGE", "FUNDING"],
            "venue_fees": None if self.fee_schedule is None else self.fee_schedule.payload(),
            "fill_liquidity": self.fill_liquidity,
            "observed_spread": SPREAD_DIAGNOSTIC_ONLY,
            "slippage_scenarios": [scenario.payload() for scenario in self.slippage_scenarios],
            "funding": FUNDING_NOT_MODELLED_T2,
            "gross_search_reports": "BREAK_EVEN_COST_BPS_PER_SIDE",
            "edge_vanishing_under_the_approved_envelope": "NOT_PROMOTABLE",
            "l2_slippage_model": "DEFERRED_UNTIL_DEPTH_CAPTURE_IS_JUSTIFIED",
        })

    def total_cost_bps_per_side(self, scenario: str) -> Decimal:
        """Fee plus one named stress scenario. Refuses when either is missing."""
        if self.fee_schedule is None:
            raise StrategyLabPolicyError("cost_dependent_result_requires_a_verified_fee_schedule")
        for item in self.slippage_scenarios:
            if item.name == scenario:
                return Decimal(self.fee_schedule.taker_fee_bps) + Decimal(item.cost_bps_per_side)
        raise StrategyLabPolicyError(f"slippage_scenario_not_declared:{scenario}")


def gross_cost_policy_v1() -> CostPolicyV1:
    """The OR-6 policy until the owner supplies a verified fee schedule."""
    return CostPolicyV1()


__all__ = [
    "AUTHORITATIVE_DECIMAL_PRECISION_V1",
    "COST_MODE_FEE_SCHEDULED",
    "COST_MODE_GROSS",
    "NEAR_TIE_RELATIVE_TOLERANCE_V1",
    "NUMERIC_TIER_AUTHORITATIVE",
    "NUMERIC_TIER_SEARCH",
    "OR3_RERUN_SCOPE_V1",
    "OR5_BASELINE_LAG_V1",
    "OR5_SWEEP_LAGS_V1",
    "SELECTION_CUTOFF_ERROR_BAND_RELATIVE_V1",
    "CostPolicyV1",
    "FeeScheduleV1",
    "PolicyV1",
    "SlippageScenarioV1",
    "StrategyLabPolicyError",
    "gross_cost_policy_v1",
    "or3_numeric_policy_v1",
    "or5_lags_v1",
    "or5_t2_timing_policy_v1",
]
