"""Explicit, hypothetical forward funding arithmetic; never economic authority.

Reuses hashed paper fills, the incubation report's position chain and the issued
candidate funding bridge. No data acquisition, persistence, promotion or v1
identity changes. Only closed, fully covered, intact holdings are supported.
Settlement at either fill boundary refuses the entire calculation.

For an explicitly declared fixed quantity of one unit of entry notional:
    signed contribution = -direction * published_rate * proxy_price / entry_price
Positive contributions are receipts (negative funding rates pay longs); these
are returns, not account-currency amounts. Each settlement is rounded half-even
to the existing 1e-12 return scale, then rounded rows are summed, not compounded.

The trade proxy is deliberately never a venue settlement mark. Even an EXACT
ticker mark is unsupported as a settlement price. All successful results are
HYPOTHETICAL, out of paper net. There is no approval argument or verified state:
the existing registries do not issue forward settlement economic authority.
Content hashes prove consistency of supplied evidence, not account settlement
truth. Existing fee/slippage components and paper net remain unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from itertools import pairwise
from types import SimpleNamespace
from typing import Any, Final

from .paper_economics_v1 import PRICE_QUANTUM_V1, _exact_price, _hashed_fill, _sha256
from .paper_funding_evidence_v1 import (
    CONVENTION_FIRST_TRADE_AT_INSTANT,
    CONVENTION_LAST_TRADE_ENDING_AT_INSTANT,
    STATE_EVIDENCE_COMPLETE,
    TRADE_PRICE_PROXY,
    CandidateFundingEvidenceV1,
    candidate_funding_evidence_v1,
)
from .paper_incubation_report_v1 import (
    FILL_STATUS_FILLED,
    RETURN_QUANTUM_V1,
    IncubationResolutionV1,
    _micros,
    incubation_report_v1,
)
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

SCHEMA_VERSION_V1: Final = "paper-funding-calculation-v1"
HYPOTHETICAL: Final = "HYPOTHETICAL_RESEARCH_ONLY"
STRICT_INTERIOR: Final = "STRICT_INTERIOR_REFUSE_FILL_INSTANT"
PUBLISHED_RATE_HYPOTHESIS: Final = "PUBLISHED_RATE_HYPOTHESIS_NOT_ACCOUNT_SETTLEMENT"
UNIT_ENTRY_NOTIONAL: Final = "ONE_UNIT_ENTRY_NOTIONAL_FIXED_QUANTITY"
_CONTEXT: Final = Context(prec=50, rounding=ROUND_HALF_EVEN)
_MINUTE: Final = 60_000_000


class PaperFundingCalculationError(ValueError):
    """A calculation is unavailable; never return a partial or verified total."""


@dataclass(frozen=True, slots=True)
class FundingCalculationAssumptionsV1:
    """Research declarations with no defaults and no approval semantics."""

    price_reference_kind: str
    price_convention: str
    settlement_authority: str
    holding_at_settlement: str
    position_value_basis: str

    def __post_init__(self) -> None:
        allowed = {
            "price_reference_kind": (TRADE_PRICE_PROXY,),
            "price_convention": (CONVENTION_FIRST_TRADE_AT_INSTANT, CONVENTION_LAST_TRADE_ENDING_AT_INSTANT),
            "settlement_authority": (PUBLISHED_RATE_HYPOTHESIS,),
            "holding_at_settlement": (STRICT_INTERIOR,),
            "position_value_basis": (UNIT_ENTRY_NOTIONAL,),
        }
        for name, options in allowed.items():
            if getattr(self, name) not in options:
                raise PaperFundingCalculationError(f"unsupported_or_undeclared_{name}")


@dataclass(frozen=True, slots=True)
class PaperFundingCalculationV1:
    payload: Mapping[str, Any]
    content_hash: str


def _integer(raw: object, reason: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PaperFundingCalculationError(reason)
    return raw


def _fills(fills: Sequence[object], funding: CandidateFundingEvidenceV1, as_of: int
           ) -> list[tuple[str, Mapping[str, Any]]]:
    pairs = [(_hash, deepcopy(identity)) for _hash, identity in map(_hashed_fill, fills)]
    if not pairs or len({_hash for _hash, _ in pairs}) != len(pairs):
        raise PaperFundingCalculationError("missing_or_duplicate_fills")
    for _, item in pairs:
        if (item.get("study_id"), item.get("trial_id"), item.get("symbol")) != (
                funding.study_id, funding.trial_id, funding.symbol):
            raise PaperFundingCalculationError("fill_is_of_another_funding_candidate")
        if item.get("status") != FILL_STATUS_FILLED:
            raise PaperFundingCalculationError("unfilled_or_ambiguous_signal")
        if _exact_price(item.get("fill_price"), "invalid_fill_price") >= Decimal("1e20"):
            raise PaperFundingCalculationError("price_outside_numeric_38_18")
        for target in ("target_from", "target_to"):
            if _integer(item.get(target), "invalid_unit_exposure") not in (-1, 0, 1):
                raise PaperFundingCalculationError("invalid_unit_exposure")
        decided = _integer(item.get("decided_at_micros"), "invalid_fill_decision")
        opened = _integer(item.get("fill_bar_open_micros"), "invalid_fill_instant")
        if opened != (decided // _MINUTE + 1) * _MINUTE:
            raise PaperFundingCalculationError("fill_not_at_next_strictly_later_minute")
        proof = item.get("evidence")
        if not isinstance(proof, Mapping) or proof.get("bar_open_micros") != opened:
            raise PaperFundingCalculationError("missing_or_mismatched_fill_bar_evidence")
        if proof.get("tier") != "T4_FIRST_PARTY_CAPTURE_FORWARD_UNSEALED":
            raise PaperFundingCalculationError("unsupported_fill_evidence_tier")
        known = _integer(proof.get("open_market_knowledge_micros"), "missing_fill_knowledge_time")
        if not opened <= known <= as_of:
            raise PaperFundingCalculationError("fill_knowledge_outside_bar_and_as_of")
        _sha256(proof.get("trade_manifest_hash"), "fill_trade_manifest_hash_malformed")
        if not proof.get("first_trade_reference") or not proof.get("session_id"):
            raise PaperFundingCalculationError("fill_source_references_missing")
    pairs.sort(key=lambda pair: (pair[1]["decided_at_micros"], pair[1]["signal_id"]))
    instants = [item["fill_bar_open_micros"] for _, item in pairs]
    if any(right <= left for left, right in pairwise(instants)):
        raise PaperFundingCalculationError("fill_instants_not_strictly_ordered")
    return pairs


def calculate_paper_funding_v1(fills: Sequence[object], cost_policy: CostPolicyV1, *,
                               funding: CandidateFundingEvidenceV1,
                               assumptions: FundingCalculationAssumptionsV1,
                               as_of: datetime,
                               resolution: IncubationResolutionV1 | None = None) -> PaperFundingCalculationV1:
    """Calculate one candidate's closed-holding diagnostic, or refuse the whole result.

    A registry-issued resolution can bind the actual OR-7 validation identity;
    it grants no funding authority. No owner approval/boolean is accepted.
    Raw fills need their own bar/source/knowledge evidence, not only a price.
    Existing source loaders must supply only records available by ``as_of``;
    this pure projection also checks their embedded knowledge times. It does
    not authenticate a database or retroactively prove missing fill parity.
    """
    if not isinstance(assumptions, FundingCalculationAssumptionsV1):
        raise PaperFundingCalculationError("explicit_calculation_assumptions_required_not_approval")
    if not isinstance(cost_policy, CostPolicyV1):
        raise PaperFundingCalculationError("existing_or6_cost_policy_required")
    # Revalidate even dataclasses.replace() copies; declarations cannot grant authority.
    assumptions = FundingCalculationAssumptionsV1(**asdict(assumptions))
    if not isinstance(funding, CandidateFundingEvidenceV1):
        raise PaperFundingCalculationError("issued_candidate_funding_evidence_required")
    # Nested identities are mappings: recheck all hashes and bridge contracts at use.
    funding = candidate_funding_evidence_v1(
        study_id=funding.study_id, trial_id=funding.trial_id, symbol=funding.symbol,
        validation_hash=funding.validation_hash,
        bridges=[SimpleNamespace(identity=deepcopy(identity), content_hash=content_hash)
                 for content_hash, identity in funding.bridges])
    if any(identity["convention"] != assumptions.price_convention for _, identity in funding.bridges):
        raise PaperFundingCalculationError("declared_price_convention_differs_from_evidence")
    as_of_micros = _micros(as_of)
    pairs = _fills(fills, funding, as_of_micros)
    with localcontext(_CONTEXT):
        report = incubation_report_v1([item for _, item in pairs], cost_policy, as_of=as_of,
                                      resolution=resolution, funding=(funding,))
        (candidate,) = report["candidates"]
        section = candidate["funding_evidence"]
        if section["state"] != STATE_EVIDENCE_COMPLETE or candidate["chain_breaks"] \
                or candidate["chain_inconsistencies"] or candidate["fills_skipped_unknown_position"]:
            raise PaperFundingCalculationError("incomplete_holding_or_funding:" + ",".join(
                section["completeness_blockers"] or ["POSITION_CHAIN_NOT_INTACT"]))
        # No raw rate, proxy or mark is inferred; settlement identity comes from the accepted bridges.
        settlements = {item["settled_at_ms"]: (bridge_hash, identity, item)
                       for bridge_hash, identity in funding.bridges for item in identity["settlements"]}
        by_open = {item["fill_bar_open_micros"]: (fill_hash, item) for fill_hash, item in pairs}
        rows = []
        for rate in section["settlement_rates"]:
            holding = section["holding_intervals"][rate["holding_index"]]
            entry_hash, entry = by_open[holding["start_micros"]]
            exit_hash, _ = by_open[holding["end_micros"]]
            bridge_hash, bridge, settlement = settlements[rate["settled_at_ms"]]
            price = settlement["price"]
            if price is None or price["evidence_kind"] != TRADE_PRICE_PROXY:
                raise PaperFundingCalculationError("unsupported_settlement_price")
            if price["knowable_at_micros"] < price["bar_open_micros"] + _MINUTE:
                raise PaperFundingCalculationError("proxy_known_before_its_bar_completed")
            for name in ("reference_hash", "dataset_content_hash", "bar_content_hash"):
                _sha256(price[name], "proxy_source_hash_malformed")
            entry_price = _exact_price(entry["fill_price"], "invalid_entry_price")
            proxy_price = _exact_price(price["price"], "invalid_proxy_price")
            if max(entry_price, proxy_price) >= Decimal("1e20"):
                raise PaperFundingCalculationError("price_outside_numeric_38_18")
            try:
                contribution = (-Decimal(holding["direction"]) * Decimal(rate["rate"]) * proxy_price
                                / entry_price).quantize(RETURN_QUANTUM_V1)
                if contribution == 0:
                    contribution = contribution.copy_abs()
            except DecimalException as error:
                raise PaperFundingCalculationError("funding_return_outside_decimal_scale") from error
            rows.append({
                "holding_index": holding["index"], "holding_hash": identity_hash_v1(holding),
                "entry_fill_hash": entry_hash, "exit_fill_hash": exit_hash,
                "entry_price": format(entry_price, "f"), "position_direction": holding["direction"],
                "settled_at_ms": rate["settled_at_ms"], "settled_at": rate["settled_at"],
                "published_rate": rate["rate"], "payer_direction": rate["payer_direction"],
                "signed_return": format(contribution, "f"),
                "funding_observation_id": settlement["funding_observation_id"],
                "funding_knowledge_at_micros": settlement["funding_knowledge_at_micros"],
                "evidence_knowable_at_micros": settlement["evidence_knowable_at_micros"],
                "bridge_content_hash": bridge_hash, "funding_window_hash": bridge["funding_window"]["content_hash"],
                "trade_proxy": deepcopy(price), "venue_mark_evidence_unused": deepcopy(settlement["mark_price"]),
            })
        rows.sort(key=lambda row: (row["settled_at_ms"], row["holding_index"]))
        total = sum((Decimal(row["signed_return"]) for row in rows), Decimal(0)).quantize(RETURN_QUANTUM_V1)
    payload = {
        "schema_version": SCHEMA_VERSION_V1, "status": HYPOTHETICAL,
        "candidate": dict(section["candidate"]), "as_of_micros": as_of_micros,
        "assumptions": asdict(assumptions), "assumptions_hash": identity_hash_v1(asdict(assumptions)),
        "numeric": {"precision": 50, "rounding": "ROUND_HALF_EVEN",
                    "price_quantum": str(PRICE_QUANTUM_V1), "return_quantum": str(RETURN_QUANTUM_V1),
                    "aggregation": "SUM_OF_QUANTIZED_SETTLEMENT_RETURNS_NOT_COMPOUNDED"},
        "units": "RETURN_PER_UNIT_ENTRY_NOTIONAL_NOT_ACCOUNT_MONEY",
        "sign_convention": "POSITIVE_RECEIPT_NEGATIVE_PAYMENT",
        "formula": "-direction * published_rate * trade_proxy_price / entry_fill_price",
        "holding_intervals": deepcopy(section["holding_intervals"]), "settlements": rows,
        "hypothetical_funding_return_sum": format(total, "f"),
        "economic_authority": "UNAVAILABLE_NO_REGISTERED_FORWARD_SETTLEMENT_AUTHORITY",
        "source_v1_economic_blockers": list(section["economic_blockers"]),
        "in_paper_net": False, "net": {"status": "NOT_COSTABLE", "value": None},
        "provenance": {"fill_content_hashes": sorted(fill_hash for fill_hash, _ in pairs),
                       "incubation_report_hash": identity_hash_v1(report),
                       "funding_evidence_hash": section["evidence_hash"],
                       "cost_policy_hash": cost_policy.policy().content_hash,
                       "bridge_content_hashes": [content_hash for content_hash, _ in funding.bridges]},
    }
    return PaperFundingCalculationV1(payload, identity_hash_v1(payload))
