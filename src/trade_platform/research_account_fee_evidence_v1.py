"""Read-only actual-account fee evidence binding for development sensitivity.

Installed by the operator on the server, never accepted from API query input.
FeeScheduleV1 alone cannot attest an account or a historical coverage interval.
This adds no persistence, acquisition, approval or paper economics authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from .strategy_lab_policies_v1 import FeeScheduleV1
from .strategy_lab_study_v1 import identity_hash_v1


@dataclass(frozen=True, slots=True)
class DevelopmentFeeBindingV1:
    # Independent intended comparison context, not copied from the evidence.
    account_id: str
    venue: str
    product: str
    tier: str
    study_content_hash: str
    candidate_set_hash: str
    dataset_content_hashes: tuple[str, ...]
    observation_start: str
    observation_end_exclusive: str
    assessed_at: str
    evidence_hash: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AccountFeeAssessmentV1:
    status: str = "UNAVAILABLE"
    reasons: list[str] = field(default_factory=list)
    evidence_hash: str | None = None
    evidence: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    taker_fee_bps_per_side: str | None = None
    remaining_after_fee_bps_per_side: str | None = None
    owner_approval: str = "NOT_ASSERTED"
    applicability: str = "RETROSPECTIVE_DEVELOPMENT_PERIOD_ONLY_NOT_CURRENT_EXECUTION"


def _instant(raw: Any) -> datetime:
    if not isinstance(raw, str):
        raise TypeError("instant_requires_text")
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("instant_requires_timezone")
    return value


def assess_account_fees_v1(binding: DevelopmentFeeBindingV1 | None, *, study_content_hash: str | None,
                           candidate_set_hash: str | None, dataset_content_hashes: tuple[str, ...],
                           evaluation_end_exclusive: str | None) -> AccountFeeAssessmentV1:
    if binding is None:
        return AccountFeeAssessmentV1(reasons=["MISSING_VERIFIED_ACTUAL_ACCOUNT_FEE_EVIDENCE",
            "MISSING_EXACT_ACCOUNT_VENUE_PRODUCT_VIP_CONTEXT", "MISSING_FEE_PERIOD_BINDING"])
    context = {name: getattr(binding, name) for name in (
        "account_id", "venue", "product", "tier", "study_content_hash", "candidate_set_hash",
        "dataset_content_hashes", "observation_start", "observation_end_exclusive", "assessed_at")}
    reasons = []
    evidence = dict(binding.evidence)
    try:
        if identity_hash_v1(evidence) != binding.evidence_hash:
            reasons.append("FEE_EVIDENCE_IDENTITY_MISMATCH")
    except (TypeError, ValueError):
        return AccountFeeAssessmentV1(reasons=["MALFORMED_FEE_EVIDENCE_IDENTITY"],
                                      evidence_hash=binding.evidence_hash, context=context)
    if (binding.study_content_hash != study_content_hash or binding.candidate_set_hash != candidate_set_hash
            or sorted(binding.dataset_content_hashes) != sorted(dataset_content_hashes)
            or not dataset_content_hashes):
        reasons.append("FEE_COMPARISON_SOURCE_BINDING_MISMATCH")
    if evidence.get("schema_version") != "actual-account-fee-evidence-v1":
        reasons.append("FEE_EVIDENCE_SCHEMA_UNSUPPORTED")
    if (evidence.get("verification_status") != "VERIFIED"
            or evidence.get("verification_source") != "ACTUAL_ACCOUNT_FEE_STATEMENT"):
        reasons.append("FEE_EVIDENCE_NOT_ACTUAL_ACCOUNT_VERIFIED")
    if not binding.account_id.strip() or evidence.get("account_id") != binding.account_id:
        reasons.append("FEE_ACCOUNT_MISMATCH_OR_MISSING")
    schedule = None
    try:
        raw = evidence["fee_schedule"]
        if not isinstance(raw, Mapping) or not all(isinstance(raw.get(k), str)
                                                  for k in ("maker_fee_bps", "taker_fee_bps")):
            raise ValueError("fee_rates_require_exact_text")
        for key in ("maker_fee_bps", "taker_fee_bps"):
            if len(raw[key]) > 256:
                raise ValueError("fee_rate_too_long")
            value = Decimal(raw[key])
            if (not value.is_finite() or abs(int(value.as_tuple().exponent)) > 1000
                    or len(format(value, "f")) > 256):
                raise ValueError("fee_rate_invalid")
        with localcontext() as ctx:
            ctx.prec = 2300
            schedule = FeeScheduleV1(**raw)
        if any(getattr(schedule, key) != getattr(binding, key) or not getattr(binding, key).strip()
               for key in ("venue", "product", "tier")):
            reasons.append("FEE_VENUE_PRODUCT_VIP_MISMATCH")
        start, end, assessed = map(_instant, (binding.observation_start,
                                             binding.observation_end_exclusive, binding.assessed_at))
        valid_from, valid_until = map(_instant, (evidence["valid_from"], evidence["valid_until_exclusive"]))
        verified = date.fromisoformat(schedule.verified_on)
        if not (valid_from <= start < end <= valid_until and end <= assessed
                and verified <= assessed.date() and valid_from < valid_until):
            reasons.append("FEE_EVIDENCE_STALE_OR_OUTSIDE_OBSERVATION_PERIOD")
        if end != _instant(evaluation_end_exclusive):
            reasons.append("FEE_EVALUATION_PERIOD_MISMATCH")
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation):
        reasons.append("MISSING_OR_MALFORMED_FEE_SCHEDULE_VERIFICATION_OR_PERIOD")
    return AccountFeeAssessmentV1(
        status="UNAVAILABLE" if reasons else "VERIFIED", reasons=reasons,
        evidence_hash=binding.evidence_hash, evidence=evidence, context=context,
        taker_fee_bps_per_side=schedule.taker_fee_bps if schedule is not None and not reasons else None)
