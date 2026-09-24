"""Phase 3D.9A -- immutable preregistration packet for one real professional validation run.

``RESEARCH_ONLY``. This module adds no table, no migration, no engine and no
economic assumption. It exists for exactly one reason: the first *real-data*
invocation of
:func:`~trade_platform.open_to_open_validation_orchestration_v1.run_open_to_open_professional_validation_v1`
must have every methodology input fixed, hashed and owner-authorized **before**
the untouched holdout is opened, and that commitment must itself be auditable
and replayable.

What a packet binds
-------------------
The dataset identity and content hash, the canonical real-market-data
provenance verdict's identity, the mechanically derived evaluation span and
holdout boundary, and every remaining input of
:class:`~trade_platform.open_to_open_validation_orchestration_v1.OpenToOpenProfessionalValidationRequestV1`
that is an owner decision rather than an architecture constant.

Fail closed, never fill in
--------------------------
Every owner-decided field is ``| None`` and defaults to absent. A packet whose
owner fields are not all present is :data:`STATUS_DRAFT` with one
:data:`UNRESOLVED_*` reason per gap -- it is *not* an error and *not* a partial
run, it is the honest state of an unfinished preregistration. This module never
invents a threshold, a horizon, an exposure, a fee, a spread, a seed or a
resample count, and never derives one from a unit-test constant.

Capacity is deliberately NOT an owner gap. The canonical capacity path answers
``BLOCKED``/``MISSING_AUTHORIZED_CAPACITY_POLICY`` without a policy, and that is
the correct answer, not a failure -- so an absent
:class:`~trade_platform.crypto_liquidity_capacity_v1.LiquidityCapacityPolicyV1`
never blocks statistical research and never appears as an unresolved reason.

The holdout guard
-----------------
:func:`require_authorized_for_holdout` is the single operational gate. Nothing
in this repository may read, evaluate, summarize or compute a strategy
statistic at or after :attr:`OpenToOpenPreregistrationV1.holdout_start` unless
that call has passed. :func:`pre_holdout_upper_bound` gives research code the
exclusive upper bound it must stay under while the packet is still DRAFT.

Phase 3Z.1 adds :func:`require_authorized_for_holdout_with_evidence_tier_v1`
*beside* it rather than inside it. The packet dataclass, its bound fields, its
content hash and its ``preregistration_id`` are untouched, so every existing
3D.9A identity is byte-for-byte unchanged; the new gate is a strictly stronger
adjacent call that additionally demands a professional-eligible evidence-tier
verdict bound to the same dataset identity and content hash. Phase R2A.2 adds
:func:`require_professional_historical_decisions_v1` beside that in turn: the
feature values themselves must be three-clock rows whose historical decision
times are professional, so a platform ingestion instant can never pass as
market knowledge. It is fail closed
by construction -- the verdict is a required argument, so there is no default
that quietly admits T0/T1/T2 evidence. The tier authority itself stays
strategy-agnostic: this function is where the crypto open-to-open path states
that professional-grade *evidence quality* is a precondition of its own
preregistered methodology, not a substitute for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .crypto_basis_mean_reversion_v1 import CryptoBasisMeanReversionDefinitionV1
from .crypto_liquidity_capacity_v1 import LiquidityCapacityPolicyV1
from .evidence_tier_authority_v1 import (
    EvidenceTierVerdictV1,
    require_professional_evidence_tier_v1,
)
from .feature_authority import FeatureMaterializationV3
from .knowledge_time_doctrine_v1 import ClaimCeilingV1, DeclaredComputeLatencyV1
from .open_to_open_validation_orchestration_v1 import (
    OpenToOpenEvaluationSpanV1,
    OpenToOpenNeighborStepsV1,
    OpenToOpenValidationOrchestrationV1Error,
    OpenToOpenWalkForwardProtocolV1,
    _content_hash,
    count_distinct_historical_decision_times_v1,
)
from .real_market_data_provenance_v1 import RealMarketDataProvenanceV1
from .research import CostModel

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.open_to_open_preregistration_v1")

#: Bump only when the *meaning* of a bound field changes. Two packets with the
#: same methodology version and the same content hash describe the same run.
METHODOLOGY_VERSION: Final = "open-to-open-professional-validation-preregistration-v1"

STATUS_DRAFT: Final = "DRAFT"
STATUS_AUTHORIZED: Final = "AUTHORIZED"

#: A DRAFT packet is explicitly not authorized to open the untouched holdout.
DRAFT_DISPOSITION: Final = "NOT_AUTHORIZED_FOR_HOLDOUT"

UNRESOLVED_STRATEGY_DEFINITION: Final = "MISSING_AUTHORIZED_STRATEGY_DEFINITION"
UNRESOLVED_COST_MODEL: Final = "MISSING_AUTHORIZED_COST_MODEL"
UNRESOLVED_WALK_FORWARD_PROTOCOL: Final = "MISSING_AUTHORIZED_WALK_FORWARD_PROTOCOL"
UNRESOLVED_NEIGHBOR_STEPS: Final = "MISSING_AUTHORIZED_NEIGHBOR_STEPS"
UNRESOLVED_BOOTSTRAP: Final = "MISSING_AUTHORIZED_BOOTSTRAP_SEED_AND_RESAMPLES"
UNRESOLVED_MONTE_CARLO: Final = "MISSING_AUTHORIZED_MONTE_CARLO_SEED_AND_SIMULATIONS"
UNRESOLVED_NULL_CONTROL: Final = "MISSING_AUTHORIZED_NULL_CONTROL_SEED"
UNRESOLVED_ADVERSE_EXIT_SHOCKS: Final = "MISSING_AUTHORIZED_ADVERSE_EXIT_SHOCK_MAGNITUDES"
UNRESOLVED_MISSING_EXIT_STRESS: Final = "MISSING_AUTHORIZED_MISSING_EXIT_STRESS_SPECIFICATION"
UNRESOLVED_REAL_DATA_PROVENANCE: Final = "MISSING_PROVEN_REAL_MARKET_DATA_PROVENANCE"
UNRESOLVED_HOLDOUT_SPAN: Final = "EVALUATION_SPAN_HOLDOUT_NOT_AVAILABLE"
#: Every canonical basis materialization sharing one knowledge/computed instant
#: collapses every ``decision_at`` onto that instant, so no decision can fall in
#: any evaluation segment and the run would be vacuous. Proving that the
#: canonical feature evidence carries more than one distinct decision time is a
#: mechanical precondition, not an economic assumption.
UNRESOLVED_FEATURE_DECISION_TIMES: Final = "UNPROVEN_DISTINCT_FEATURE_DECISION_TIMES"

_MINIMUM_DISTINCT_FEATURE_DECISION_TIMES: Final = 2


class OpenToOpenPreregistrationV1Error(ValueError):
    """Raised when a packet is malformed, or when an unauthorized packet is used."""


@dataclass(frozen=True, slots=True)
class OpenToOpenPreregistrationV1:
    """One immutable, content-hashed commitment to a professional validation run."""

    methodology_version: str
    dataset_version_id: UUID
    dataset_content_hash: str
    provenance_evidence_id: UUID | None
    provenance_content_hash: str | None
    provenance_proven_real: bool
    evaluation_start: datetime
    evaluation_end: datetime
    raw_holdout_start: datetime
    holdout_start: datetime
    pre_holdout_complete_days: int
    holdout_complete_days: int
    evaluation_span_content_hash: str
    evaluation_span_id: UUID
    baseline_definition_content_hash: str | None
    basis_entry_threshold: Decimal | None
    holding_horizon_bars: int | None
    maximum_absolute_exposure: Decimal | None
    cost_model_content_hash: str | None
    cost_model_version: str | None
    protocol_content_hash: str | None
    neighbor_steps_content_hash: str | None
    bootstrap_seed: int | None
    bootstrap_resamples: int | None
    monte_carlo_seed: int | None
    monte_carlo_simulations: int | None
    null_seed: int | None
    adverse_exit_shock_magnitudes: tuple[Decimal, ...]
    missing_exit_stress_bar_open_times: tuple[datetime, ...]
    capacity_policy_content_hash: str | None
    distinct_feature_decision_at_count: int | None
    created_at: datetime
    status: str
    disposition: str
    unresolved_reasons: tuple[str, ...]
    content_hash: str
    preregistration_id: UUID

    @property
    def authorized_for_holdout(self) -> bool:
        return self.status == STATUS_AUTHORIZED and not self.unresolved_reasons


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpenToOpenPreregistrationV1Error(f"{name}_must_be_timezone_aware")


def build_open_to_open_preregistration_v1(
    *,
    dataset_version_id: UUID,
    dataset_content_hash: str,
    evaluation_span: OpenToOpenEvaluationSpanV1,
    created_at: datetime,
    market_data_provenance: RealMarketDataProvenanceV1 | None = None,
    baseline_definition: CryptoBasisMeanReversionDefinitionV1 | None = None,
    cost_model: CostModel | None = None,
    cost_model_version: str | None = None,
    protocol: OpenToOpenWalkForwardProtocolV1 | None = None,
    neighbor_steps: OpenToOpenNeighborStepsV1 | None = None,
    bootstrap_seed: int | None = None,
    bootstrap_resamples: int | None = None,
    monte_carlo_seed: int | None = None,
    monte_carlo_simulations: int | None = None,
    null_seed: int | None = None,
    adverse_exit_shock_magnitudes: tuple[Decimal, ...] = (),
    missing_exit_stress_bar_open_times: tuple[datetime, ...] = (),
    capacity_policy: LiquidityCapacityPolicyV1 | None = None,
    distinct_feature_decision_at_count: int | None = None,
) -> OpenToOpenPreregistrationV1:
    """Bind whatever is decided, name every gap, and hash the result.

    Supplying nothing but the dataset identity and the span is legitimate: the
    result is a DRAFT packet that states exactly which owner decisions are still
    outstanding. Nothing here is defaulted into existence.
    """
    if not dataset_content_hash.strip():
        raise OpenToOpenPreregistrationV1Error("dataset_content_hash_required")
    _require_aware(created_at, "created_at")
    for timestamp in missing_exit_stress_bar_open_times:
        _require_aware(timestamp, "missing_exit_stress_bar_open_at")

    if market_data_provenance is not None:
        if not market_data_provenance.integrity_verified():
            raise OpenToOpenPreregistrationV1Error("market_data_provenance_integrity_failed")
        if market_data_provenance.dataset_version_id != dataset_version_id:
            raise OpenToOpenPreregistrationV1Error("market_data_provenance_dataset_mismatch")
        if (
            market_data_provenance.is_proven_real()
            and market_data_provenance.dataset_content_hash != dataset_content_hash.strip()
        ):
            raise OpenToOpenPreregistrationV1Error("market_data_provenance_content_hash_mismatch")
    if baseline_definition is not None:
        baseline_definition.validate()
    if protocol is not None:
        protocol.validate()
        protocol.folds(
            evaluation_start=evaluation_span.evaluation_start,
            holdout_start=evaluation_span.holdout_start,
        )
    if neighbor_steps is not None:
        neighbor_steps.validate()
    if capacity_policy is not None:
        capacity_policy.validate()

    proven_real = (
        market_data_provenance is not None and market_data_provenance.is_proven_real()
    )

    reasons: list[str] = []
    if not proven_real:
        reasons.append(UNRESOLVED_REAL_DATA_PROVENANCE)
    if evaluation_span.holdout_unavailable_reasons:
        reasons.append(UNRESOLVED_HOLDOUT_SPAN)
    if baseline_definition is None:
        reasons.append(UNRESOLVED_STRATEGY_DEFINITION)
    if cost_model is None or cost_model_version is None or not cost_model_version.strip():
        reasons.append(UNRESOLVED_COST_MODEL)
    if protocol is None:
        reasons.append(UNRESOLVED_WALK_FORWARD_PROTOCOL)
    if neighbor_steps is None:
        reasons.append(UNRESOLVED_NEIGHBOR_STEPS)
    if bootstrap_seed is None or bootstrap_resamples is None or bootstrap_resamples < 1:
        reasons.append(UNRESOLVED_BOOTSTRAP)
    if (
        monte_carlo_seed is None
        or monte_carlo_simulations is None
        or monte_carlo_simulations < 1
    ):
        reasons.append(UNRESOLVED_MONTE_CARLO)
    if null_seed is None:
        reasons.append(UNRESOLVED_NULL_CONTROL)
    if not adverse_exit_shock_magnitudes:
        reasons.append(UNRESOLVED_ADVERSE_EXIT_SHOCKS)
    if not missing_exit_stress_bar_open_times:
        reasons.append(UNRESOLVED_MISSING_EXIT_STRESS)
    if (
        distinct_feature_decision_at_count is None
        or distinct_feature_decision_at_count < _MINIMUM_DISTINCT_FEATURE_DECISION_TIMES
    ):
        reasons.append(UNRESOLVED_FEATURE_DECISION_TIMES)

    unresolved = tuple(reasons)
    status = STATUS_DRAFT if unresolved else STATUS_AUTHORIZED
    cost_model_content_hash = (
        None
        if cost_model is None
        else _content_hash(
            {
                "fixed_per_turnover": cost_model.fixed_per_turnover,
                "percentage_per_turnover": cost_model.percentage_per_turnover,
                "spread_fraction_per_turnover": cost_model.spread_fraction_per_turnover,
            }
        )
    )
    payload = {
        "methodology_version": METHODOLOGY_VERSION,
        "dataset_version_id": dataset_version_id,
        "dataset_content_hash": dataset_content_hash.strip(),
        "provenance_evidence_id": (
            None if market_data_provenance is None else market_data_provenance.evidence_id
        ),
        "provenance_content_hash": (
            None if market_data_provenance is None else market_data_provenance.content_hash
        ),
        "provenance_proven_real": proven_real,
        "evaluation_span_content_hash": evaluation_span.content_hash,
        "evaluation_span_id": evaluation_span.span_id,
        "evaluation_start": evaluation_span.evaluation_start,
        "evaluation_end": evaluation_span.evaluation_end,
        "raw_holdout_start": evaluation_span.raw_holdout_start,
        "holdout_start": evaluation_span.holdout_start,
        "pre_holdout_complete_days": evaluation_span.pre_holdout_complete_days,
        "holdout_complete_days": evaluation_span.holdout_complete_days,
        "baseline_definition_content_hash": (
            None if baseline_definition is None else baseline_definition.content_hash()
        ),
        "cost_model_content_hash": cost_model_content_hash,
        "cost_model_version": cost_model_version,
        "protocol_content_hash": None if protocol is None else protocol.content_hash(),
        "neighbor_steps_content_hash": (
            None if neighbor_steps is None else neighbor_steps.content_hash()
        ),
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_resamples": bootstrap_resamples,
        "monte_carlo_seed": monte_carlo_seed,
        "monte_carlo_simulations": monte_carlo_simulations,
        "null_seed": null_seed,
        "adverse_exit_shock_magnitudes": adverse_exit_shock_magnitudes,
        "missing_exit_stress_bar_open_times": missing_exit_stress_bar_open_times,
        "capacity_policy_content_hash": (
            None if capacity_policy is None else capacity_policy.content_hash()
        ),
        "distinct_feature_decision_at_count": distinct_feature_decision_at_count,
        "created_at": created_at,
        "status": status,
        "unresolved_reasons": unresolved,
    }
    content_hash = _content_hash(payload)
    return OpenToOpenPreregistrationV1(
        methodology_version=METHODOLOGY_VERSION,
        dataset_version_id=dataset_version_id,
        dataset_content_hash=dataset_content_hash.strip(),
        provenance_evidence_id=(
            None if market_data_provenance is None else market_data_provenance.evidence_id
        ),
        provenance_content_hash=(
            None if market_data_provenance is None else market_data_provenance.content_hash
        ),
        provenance_proven_real=proven_real,
        evaluation_start=evaluation_span.evaluation_start,
        evaluation_end=evaluation_span.evaluation_end,
        raw_holdout_start=evaluation_span.raw_holdout_start,
        holdout_start=evaluation_span.holdout_start,
        pre_holdout_complete_days=evaluation_span.pre_holdout_complete_days,
        holdout_complete_days=evaluation_span.holdout_complete_days,
        evaluation_span_content_hash=evaluation_span.content_hash,
        evaluation_span_id=evaluation_span.span_id,
        baseline_definition_content_hash=(
            None if baseline_definition is None else baseline_definition.content_hash()
        ),
        basis_entry_threshold=(
            None if baseline_definition is None else baseline_definition.basis_entry_threshold
        ),
        holding_horizon_bars=(
            None if baseline_definition is None else baseline_definition.holding_horizon_bars
        ),
        maximum_absolute_exposure=(
            None
            if baseline_definition is None
            else baseline_definition.maximum_absolute_exposure
        ),
        cost_model_content_hash=cost_model_content_hash,
        cost_model_version=cost_model_version,
        protocol_content_hash=None if protocol is None else protocol.content_hash(),
        neighbor_steps_content_hash=(
            None if neighbor_steps is None else neighbor_steps.content_hash()
        ),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
        monte_carlo_seed=monte_carlo_seed,
        monte_carlo_simulations=monte_carlo_simulations,
        null_seed=null_seed,
        adverse_exit_shock_magnitudes=adverse_exit_shock_magnitudes,
        missing_exit_stress_bar_open_times=missing_exit_stress_bar_open_times,
        capacity_policy_content_hash=(
            None if capacity_policy is None else capacity_policy.content_hash()
        ),
        distinct_feature_decision_at_count=distinct_feature_decision_at_count,
        created_at=created_at,
        status=status,
        disposition=DRAFT_DISPOSITION if unresolved else STATUS_AUTHORIZED,
        unresolved_reasons=unresolved,
        content_hash=content_hash,
        preregistration_id=uuid5(
            _NAMESPACE, f"open-to-open-preregistration-v1:{content_hash}"
        ),
    )


def pre_holdout_upper_bound(packet: OpenToOpenPreregistrationV1) -> datetime:
    """The exclusive upper bound all pre-holdout research must stay strictly under."""
    return packet.holdout_start


def require_authorized_for_holdout(packet: OpenToOpenPreregistrationV1) -> None:
    """Gate every holdout read. Raises unless the packet is fully authorized.

    Deliberately blunt: there is no partial, per-metric or "just looking"
    variant. Either the owner froze the whole methodology first, or the
    untouched holdout stays untouched.
    """
    if packet.methodology_version != METHODOLOGY_VERSION:
        raise OpenToOpenPreregistrationV1Error("preregistration_methodology_version_mismatch")
    if not packet.authorized_for_holdout:
        raise OpenToOpenPreregistrationV1Error(
            "untouched_holdout_requires_authorized_preregistration:"
            + ",".join(packet.unresolved_reasons or (DRAFT_DISPOSITION,))
        )


def require_authorized_for_holdout_with_evidence_tier_v1(
    packet: OpenToOpenPreregistrationV1,
    evidence_tier: EvidenceTierVerdictV1,
) -> None:
    """The Phase 3Z.1 gate: an authorized packet *and* professional-grade evidence.

    Strictly stronger than :func:`require_authorized_for_holdout`, which it
    calls first and never weakens. ``evidence_tier`` is positional and required
    precisely so a caller cannot omit it and inherit a permissive default.

    Both conditions are necessary and neither is sufficient. A professional
    evidence tier is a statement about evidence *quality*; it never certifies
    that this evidence suits this strategy. That judgement stays in the packet's
    own frozen methodology, which this call still enforces in full.

    The canonical Bybit REST dataset cannot pass: its source's timing authority
    is ``NONE``, so it resolves to ``T1_RETROSPECTIVE`` and is not professional
    evidence. That is the intended, unchanged answer, not a regression.
    """
    require_authorized_for_holdout(packet)
    if not evidence_tier.integrity_verified():
        raise OpenToOpenPreregistrationV1Error("evidence_tier_verdict_integrity_failed")
    if evidence_tier.dataset_version_id != packet.dataset_version_id:
        raise OpenToOpenPreregistrationV1Error("evidence_tier_verdict_dataset_mismatch")
    if evidence_tier.provenance_evidence_id != packet.provenance_evidence_id:
        raise OpenToOpenPreregistrationV1Error("evidence_tier_verdict_provenance_mismatch")
    require_professional_evidence_tier_v1(
        evidence_tier,
        dataset_version_id=packet.dataset_version_id,
        dataset_content_hash=packet.dataset_content_hash,
    )


def require_professional_historical_decisions_v1(
    packet: OpenToOpenPreregistrationV1,
    evidence_tier: EvidenceTierVerdictV1,
    materializations: Sequence[FeatureMaterializationV3],
    *,
    compute_latency: DeclaredComputeLatencyV1,
) -> None:
    """The Phase R2A.2 gate: professional evidence *and* professional decision times.

    Strictly stronger than
    :func:`require_authorized_for_holdout_with_evidence_tier_v1`, which it
    calls first. A professional dataset verdict is not enough on its own: a
    value derived from T4 capture through the legacy V2 path would still carry
    a platform ingestion instant as its "knowledge". So every feature value
    the run would use must also be a three-clock (V3) row of this exact
    dataset, every input of which re-derives from *this* verdict (so a
    hand-built row, or one derived from another verdict, refuses), whose
    historical decision -- market knowledge plus the declared
    compute latency, no operational clock -- is admissible at
    ``PROFESSIONAL``, and the distinct decision instants they carry must be at
    least two and exactly the count the packet bound. A legacy V2 value, a T1
    value or a T2 (conditional) value refuses; nothing is skipped.

    The packet's fields and identity are unchanged. It does not yet bind
    ``compute_latency`` itself; a methodology version that does is the
    professional-validation phase's job, and until then the latency is a
    required, referenced argument with no default.
    """
    require_authorized_for_holdout_with_evidence_tier_v1(packet, evidence_tier)
    if not materializations:
        raise OpenToOpenPreregistrationV1Error("professional_decisions_require_materializations")
    for materialization in materializations:
        if not isinstance(materialization, FeatureMaterializationV3):
            raise OpenToOpenPreregistrationV1Error(
                "professional_decisions_require_three_clock_materializations"
            )
        if materialization.dataset_version != str(packet.dataset_version_id):
            raise OpenToOpenPreregistrationV1Error("professional_decision_dataset_mismatch")
    try:
        count = count_distinct_historical_decision_times_v1(
            materializations,
            compute_latency=compute_latency,
            evidence_tiers={evidence_tier.evidence_id: evidence_tier},
            minimum_claim=ClaimCeilingV1.PROFESSIONAL,
        )
    except OpenToOpenValidationOrchestrationV1Error as error:
        raise OpenToOpenPreregistrationV1Error(
            f"professional_decision_not_admissible:{error}"
        ) from error
    if count < _MINIMUM_DISTINCT_FEATURE_DECISION_TIMES:
        raise OpenToOpenPreregistrationV1Error(UNRESOLVED_FEATURE_DECISION_TIMES)
    if count != packet.distinct_feature_decision_at_count:
        raise OpenToOpenPreregistrationV1Error("distinct_decision_count_differs_from_packet")
