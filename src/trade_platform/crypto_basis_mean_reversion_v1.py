"""Module 3J.2b.2a -- pure basis mean-reversion strategy core + deterministic trade ledger.

``RESEARCH_ONLY``. The first genuinely tradable crypto-perpetual strategy
hypothesis built on top of Module 3J.2b.1's infrastructure
(``SubjectAwareTradableResearchEvidenceV2``, the dataset-bound tradable-bar
reader, the signed research-exposure abstraction, and the funding-free
OPEN-to-OPEN price-return primitive). This module introduces no new feature
store, bar store, dataset authority, signal authority, or execution engine,
and creates no table -- every input type is reused unmodified from 3J.0/3J.1/
3J.2a/3J.2b.1.

Execution convention: ``feature event -> decision -> future OPEN -> future
OPEN``. This is deliberately NOT close-to-close, so it is never passed through
``run_vectorized_backtest()`` -- see ``signed_price_return_v2.py`` for why.

Hypothesis (Sec 7 of the architecture proposal): pure basis mean reversion.
The sole v1 signal feature is ``crypto_mark_index_basis`` (semantic version
``1.0.0``). A supplied feature bundle containing any additional feature
(``open_interest_change``, ``crypto_realized_funding_annualized``,
``crypto_funding_forecast_error``, or any other) is rejected outright rather
than silently ignored -- this baseline must stay genuinely basis-only, never
accidentally multi-factor.

Funding is excluded from v1 in full (Architecture 1 only): this module never
imports ``paper_execution`` and never calls ``apply_funding()``. It must not
be described as a funding strategy, carry strategy, funding capture, or
funding arbitrage -- its only claimed edge is basis convergence (price
return).

No optimization of any kind lives here: one caller-supplied, preregisterable
``CryptoBasisMeanReversionDefinitionV1`` plus one
``SubjectAwareTradableResearchEvidenceV2`` produces exactly one deterministic
``BasisMeanReversionResearchRunV1``. This module does not compute or claim
Sharpe, Sortino, Calmar, PBO, DSR, validated OOS alpha, capacity, execution
realism, or paper eligibility -- it exposes only raw decision/trade counts and
an ordered ``trade_returns`` tuple. Those claims belong to a later,
separately reviewed validation-orchestration module (3J.2b.2b).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from .crypto_instruments import CryptoInstrumentKind
from .feature_authority import FeatureSubjectType
from .research import CostModel
from .signed_price_return_v2 import compute_signed_open_to_open_return
from .signed_research_exposure_v2 import SignedResearchSignalObservationV2
from .tradable_bar_evidence_v2 import AuthoritativeTradableBarV2
from .tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

#: v1 interval is pinned to ``1m`` only, matching ``tradable_bar_evidence_v2``'s
#: own v1 interval boundary (Sec 4/8 of the architecture proposal).
_ONE_BAR_INTERVAL = timedelta(minutes=1)


class CryptoBasisMeanReversionV1Error(ValueError):
    """Raised for any invalid definition, evidence pairing, or run request."""


class CryptoBasisMeanReversionLifecycleV1(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"


class BasisMeanReversionOutcomeV1(StrEnum):
    """The deterministic, auditable outcome of exactly one feature event."""

    FLAT = "FLAT"
    EXECUTED = "EXECUTED"
    IGNORED_ACTIVE_TRADE = "IGNORED_ACTIVE_TRADE"
    EXCLUDED_MISSING_ENTRY = "EXCLUDED_MISSING_ENTRY"
    EXCLUDED_MISSING_EXIT = "EXCLUDED_MISSING_EXIT"


def _canonical_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise CryptoBasisMeanReversionV1Error(f"{field_name}_must_be_finite")


@dataclass(frozen=True, slots=True)
class CryptoBasisMeanReversionDefinitionV1:
    """Immutable v1 strategy definition. Exactly three tunable parameters.

    Every other field is a fixed identity constant, not a tunable parameter --
    changing one of those is a strategy-identity change, not a preregistered
    parameter sweep.
    """

    basis_entry_threshold: Decimal
    holding_horizon_bars: int
    maximum_absolute_exposure: Decimal
    strategy_name: str = "crypto_basis_mean_reversion"
    semantic_version: str = "1.0.0"
    lifecycle: CryptoBasisMeanReversionLifecycleV1 = CryptoBasisMeanReversionLifecycleV1.RESEARCH_ONLY
    required_subject_type: FeatureSubjectType = FeatureSubjectType.INSTRUMENT
    required_instrument_kind: CryptoInstrumentKind = CryptoInstrumentKind.PERPETUAL
    required_feature_name: str = "crypto_mark_index_basis"
    required_feature_semantic_version: str = "1.0.0"

    def validate(self) -> None:
        _finite(self.basis_entry_threshold, "basis_entry_threshold")
        if self.basis_entry_threshold <= 0:
            raise CryptoBasisMeanReversionV1Error("basis_entry_threshold_must_be_positive")
        if self.holding_horizon_bars < 1:
            raise CryptoBasisMeanReversionV1Error("holding_horizon_bars_must_be_at_least_one")
        _finite(self.maximum_absolute_exposure, "maximum_absolute_exposure")
        if not (Decimal("0") < self.maximum_absolute_exposure <= Decimal("1")):
            raise CryptoBasisMeanReversionV1Error("maximum_absolute_exposure_out_of_bounds")
        if not self.strategy_name.strip() or not self.semantic_version.strip():
            raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_identity_missing")

    @property
    def definition_id(self) -> UUID:
        return uuid5(NAMESPACE_URL, f"crypto-basis-mean-reversion-definition-v1:{self.content_hash()}")

    def content_hash(self) -> str:
        payload = {
            "strategy_name": self.strategy_name,
            "semantic_version": self.semantic_version,
            "lifecycle": self.lifecycle.value,
            "required_subject_type": self.required_subject_type.value,
            "required_instrument_kind": self.required_instrument_kind.value,
            "required_feature_name": self.required_feature_name,
            "required_feature_semantic_version": self.required_feature_semantic_version,
            "basis_entry_threshold": str(self.basis_entry_threshold),
            "holding_horizon_bars": self.holding_horizon_bars,
            "maximum_absolute_exposure": str(self.maximum_absolute_exposure),
        }
        return _canonical_hash(payload)


@dataclass(frozen=True, slots=True)
class BasisMeanReversionTradeV1:
    """One executed, non-overlapping trade with full decision/price provenance."""

    feature_materialization_id: UUID
    feature_materialization_content_hash: str
    decision_at: datetime
    basis_value: Decimal
    exposure: Decimal
    entry_bar: AuthoritativeTradableBarV2
    exit_bar: AuthoritativeTradableBarV2
    entry_time: datetime
    exit_time: datetime
    entry_open: Decimal
    exit_open: Decimal
    cost_model_version: str
    gross_return: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_return: Decimal

    @property
    def direction(self) -> str:
        if self.exposure > 0:
            return "LONG"
        if self.exposure < 0:
            return "SHORT"
        return "FLAT"


@dataclass(frozen=True, slots=True)
class BasisMeanReversionDecisionV1:
    """One auditable outcome for exactly one ``crypto_mark_index_basis`` event."""

    feature_materialization_id: UUID
    feature_materialization_content_hash: str
    basis_value: Decimal
    outcome: BasisMeanReversionOutcomeV1
    signal_observation: SignedResearchSignalObservationV2
    trade: BasisMeanReversionTradeV1 | None

    @property
    def decision_at(self) -> datetime:
        return self.signal_observation.decision_at

    @property
    def exposure(self) -> Decimal:
        return self.signal_observation.exposure

    @property
    def evidence_content_hash(self) -> str:
        return self.signal_observation.evidence_content_hash


@dataclass(frozen=True, slots=True)
class BasisMeanReversionResearchRunV1:
    """A deterministic, content-hashed, in-memory-only research run ledger.

    No durable persistence: this is immutable/content-hashed evidence held
    only in application memory, exactly like every upstream 3J.2b.1 artifact.
    Exposes only raw decision/trade counts and an ordered ``trade_returns``
    tuple -- never Sharpe, Sortino, Calmar, PBO, DSR, or any other claim that
    belongs to a separately reviewed validation-orchestration module.
    """

    definition: CryptoBasisMeanReversionDefinitionV1
    evidence_content_hash: str
    dataset_version_id: UUID
    instrument_id: str
    cost_model_version: str
    decisions: tuple[BasisMeanReversionDecisionV1, ...]
    content_hash: str
    run_id: UUID

    @property
    def executed_trades(self) -> tuple[BasisMeanReversionTradeV1, ...]:
        return tuple(decision.trade for decision in self.decisions if decision.trade is not None)

    @property
    def trade_returns(self) -> tuple[Decimal, ...]:
        return tuple(trade.net_return for trade in self.executed_trades)

    @property
    def executed_trade_count(self) -> int:
        return len(self.executed_trades)

    @property
    def flat_decision_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.outcome is BasisMeanReversionOutcomeV1.FLAT)

    @property
    def ignored_count(self) -> int:
        return sum(
            1 for decision in self.decisions if decision.outcome is BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE
        )

    @property
    def excluded_count(self) -> int:
        return sum(
            1
            for decision in self.decisions
            if decision.outcome
            in (BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_ENTRY, BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_EXIT)
        )


def _classify(basis_value: Decimal, threshold: Decimal, cap: Decimal) -> Decimal:
    """Exact, non-fuzzy symmetric direction rule. Equality at the threshold is FLAT."""
    if basis_value > threshold:
        return -cap
    if basis_value < -threshold:
        return cap
    return Decimal("0")


def _decision_evidence_hash(
    *,
    definition_content_hash: str,
    evidence_content_hash: str,
    materialization_id: UUID,
    materialization_content_hash: str,
    basis_value: Decimal,
    decision_at: datetime,
    exposure: Decimal,
) -> str:
    payload = {
        "strategy_definition_content_hash": definition_content_hash,
        "composite_research_evidence_content_hash": evidence_content_hash,
        "feature_materialization_id": str(materialization_id),
        "feature_materialization_content_hash": materialization_content_hash,
        "basis_value": str(basis_value),
        "decision_at": decision_at.isoformat(),
        "exposure": str(exposure),
    }
    return _canonical_hash(payload)


def _validate_cost_model(cost_model: CostModel) -> None:
    for field_name in ("fixed_per_turnover", "percentage_per_turnover", "spread_fraction_per_turnover"):
        value: Decimal = getattr(cost_model, field_name)
        if not value.is_finite() or value < 0:
            raise CryptoBasisMeanReversionV1Error(f"cost_model_{field_name}_invalid")


def run_crypto_basis_mean_reversion_research(
    *,
    definition: CryptoBasisMeanReversionDefinitionV1,
    evidence: SubjectAwareTradableResearchEvidenceV2,
    instrument_kind: CryptoInstrumentKind,
    cost_model: CostModel,
    cost_model_version: str,
) -> BasisMeanReversionResearchRunV1:
    """Process one sealed composite evidence bundle into one deterministic run.

    Iterates the sole ``crypto_mark_index_basis`` materialization series
    chronologically. Each materialization produces exactly one auditable
    decision: ``FLAT``, ``EXECUTED``, ``IGNORED_ACTIVE_TRADE``,
    ``EXCLUDED_MISSING_ENTRY``, or ``EXCLUDED_MISSING_EXIT``. No optimization,
    no forward-fill, no synthetic entry/exit, no funding.
    """
    definition.validate()
    # Re-invoke each upstream artifact's OWN authoritative validation first --
    # this module never re-implements those checks, only calls them.
    evidence.feature_bundle.validate()
    evidence.bar_series.validate()
    if not cost_model_version.strip():
        raise CryptoBasisMeanReversionV1Error("cost_model_version_required")
    _validate_cost_model(cost_model)

    if evidence.feature_bundle.subject_type is not definition.required_subject_type:
        raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_wrong_subject_type")
    if instrument_kind is not definition.required_instrument_kind:
        raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_requires_perpetual_instrument")

    feature_series = evidence.feature_bundle.feature_series
    if len(feature_series) != 1:
        raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_requires_exactly_one_feature")
    series = feature_series[0]
    if (
        series.requirement.name != definition.required_feature_name
        or series.requirement.semantic_version != definition.required_feature_semantic_version
    ):
        raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_wrong_required_feature")

    threshold = definition.basis_entry_threshold
    cap = definition.maximum_absolute_exposure
    horizon = definition.holding_horizon_bars * _ONE_BAR_INTERVAL
    bar_series = evidence.bar_series
    definition_content_hash = definition.content_hash()

    decisions: list[BasisMeanReversionDecisionV1] = []
    last_executed_exit_time: datetime | None = None

    for materialization in series.materializations:
        decision_at = max(
            materialization.event_at,
            materialization.effective_at,
            materialization.knowledge_at,
            materialization.computed_at,
        )
        if decision_at > evidence.feature_bundle.decision_at:
            # Cannot occur once evidence.feature_bundle.validate() (above) has
            # passed -- defense in depth, not a reachable branch in normal use.
            raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_future_feature_knowledge")

        basis_value = materialization.value
        if basis_value is None or not basis_value.is_finite():
            raise CryptoBasisMeanReversionV1Error("crypto_basis_mean_reversion_non_finite_basis")

        exposure = _classify(basis_value, threshold, cap)
        evidence_content_hash = _decision_evidence_hash(
            definition_content_hash=definition_content_hash,
            evidence_content_hash=evidence.content_hash,
            materialization_id=materialization.materialization_id,
            materialization_content_hash=materialization.content_hash,
            basis_value=basis_value,
            decision_at=decision_at,
            exposure=exposure,
        )
        signal_observation = SignedResearchSignalObservationV2(
            instrument_id=bar_series.instrument_id,
            decision_at=decision_at,
            exposure=exposure,
            maximum_absolute_exposure=cap,
            evidence_content_hash=evidence_content_hash,
        )
        signal_observation.validate()

        outcome: BasisMeanReversionOutcomeV1
        trade: BasisMeanReversionTradeV1 | None = None

        if exposure == 0:
            outcome = BasisMeanReversionOutcomeV1.FLAT
        elif last_executed_exit_time is not None and decision_at <= last_executed_exit_time:
            outcome = BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE
        else:
            entry_bar = bar_series.first_eligible_bar_after(decision_at)
            if entry_bar is None:
                outcome = BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_ENTRY
            else:
                exit_open_at = entry_bar.bar_open_at + horizon
                exit_bar = next((bar for bar in bar_series.bars if bar.bar_open_at == exit_open_at), None)
                if exit_bar is None:
                    outcome = BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_EXIT
                else:
                    computed_return = compute_signed_open_to_open_return(
                        entry_bar=entry_bar,
                        exit_bar=exit_bar,
                        exposure=exposure,
                        maximum_absolute_exposure=cap,
                        cost_model=cost_model,
                    )
                    trade = BasisMeanReversionTradeV1(
                        feature_materialization_id=materialization.materialization_id,
                        feature_materialization_content_hash=materialization.content_hash,
                        decision_at=decision_at,
                        basis_value=basis_value,
                        exposure=exposure,
                        entry_bar=entry_bar,
                        exit_bar=exit_bar,
                        entry_time=computed_return.entry_time,
                        exit_time=computed_return.exit_time,
                        entry_open=computed_return.entry_open,
                        exit_open=computed_return.exit_open,
                        cost_model_version=cost_model_version,
                        gross_return=computed_return.gross_return,
                        entry_cost=computed_return.entry_cost,
                        exit_cost=computed_return.exit_cost,
                        net_return=computed_return.net_return,
                    )
                    outcome = BasisMeanReversionOutcomeV1.EXECUTED
                    last_executed_exit_time = exit_bar.bar_open_at

        decisions.append(
            BasisMeanReversionDecisionV1(
                feature_materialization_id=materialization.materialization_id,
                feature_materialization_content_hash=materialization.content_hash,
                basis_value=basis_value,
                outcome=outcome,
                signal_observation=signal_observation,
                trade=trade,
            )
        )

    run_content_hash = _run_content_hash(
        definition_content_hash=definition_content_hash,
        evidence=evidence,
        cost_model=cost_model,
        cost_model_version=cost_model_version,
        decisions=tuple(decisions),
    )
    run_id = uuid5(NAMESPACE_URL, f"crypto-basis-mean-reversion-run-v1:{run_content_hash}")
    return BasisMeanReversionResearchRunV1(
        definition=definition,
        evidence_content_hash=evidence.content_hash,
        dataset_version_id=evidence.feature_bundle.dataset_version_id,
        instrument_id=bar_series.instrument_id,
        cost_model_version=cost_model_version,
        decisions=tuple(decisions),
        content_hash=run_content_hash,
        run_id=run_id,
    )


def _run_content_hash(
    *,
    definition_content_hash: str,
    evidence: SubjectAwareTradableResearchEvidenceV2,
    cost_model: CostModel,
    cost_model_version: str,
    decisions: tuple[BasisMeanReversionDecisionV1, ...],
) -> str:
    payload = {
        "strategy_definition_content_hash": definition_content_hash,
        "composite_research_evidence_content_hash": evidence.content_hash,
        "dataset_version_id": str(evidence.feature_bundle.dataset_version_id),
        "instrument_id": evidence.bar_series.instrument_id,
        "evaluation_cutoff": evidence.feature_bundle.decision_at.isoformat(),
        "cost_model": {
            "fixed_per_turnover": str(cost_model.fixed_per_turnover),
            "percentage_per_turnover": str(cost_model.percentage_per_turnover),
            "spread_fraction_per_turnover": str(cost_model.spread_fraction_per_turnover),
        },
        "cost_model_version": cost_model_version,
        "decisions": [
            {
                "feature_materialization_id": str(decision.feature_materialization_id),
                "feature_materialization_content_hash": decision.feature_materialization_content_hash,
                "decision_at": decision.decision_at.isoformat(),
                "basis_value": str(decision.basis_value),
                "exposure": str(decision.exposure),
                "outcome": decision.outcome.value,
                "evidence_content_hash": decision.evidence_content_hash,
                "trade": None
                if decision.trade is None
                else {
                    "entry_time": decision.trade.entry_time.isoformat(),
                    "exit_time": decision.trade.exit_time.isoformat(),
                    "entry_open": str(decision.trade.entry_open),
                    "exit_open": str(decision.trade.exit_open),
                    "gross_return": str(decision.trade.gross_return),
                    "entry_cost": str(decision.trade.entry_cost),
                    "exit_cost": str(decision.trade.exit_cost),
                    "net_return": str(decision.trade.net_return),
                    "entry_bar_raw_observation_id": str(decision.trade.entry_bar.raw_observation_id),
                    "exit_bar_raw_observation_id": str(decision.trade.exit_bar.raw_observation_id),
                },
            }
            for decision in decisions
        ],
    }
    return _canonical_hash(payload)
