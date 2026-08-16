"""Point-in-time, research-only long-term investment analysis.

The module produces immutable evidence and review candidates.  It deliberately
has no signal, order, broker, or capital-allocation execution interface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase
from .pit_fundamentals import AvailableFilingFact, derive_research_metrics
from .pit_macro import PitMacroObservation


class InvestmentEngineV2Error(ValueError):
    pass


class InvestmentEvidenceState(StrEnum):
    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"


class InvestmentReviewStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ThesisResearchStatus(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    RETIRED = "RETIRED"


class RuleOperator(StrEnum):
    BELOW = "BELOW"
    ABOVE = "ABOVE"


FIXTURE_LIMITATIONS = (
    "DETERMINISTIC_ATTRIBUTED_FIXTURE_ONLY",
    "NOT_INVESTMENT_RECOMMENDATION",
    "NO_EXECUTION_AUTHORITY",
)

QUALITY_REQUIRED_METRICS = frozenset(
    {
        "revenue",
        "operating_income",
        "operating_cash_flow",
        "capital_expenditures",
        "total_debt",
        "shares_outstanding",
        "tax_rate",
        "invested_capital",
        "dividends_paid",
        "share_repurchases",
    }
)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvestmentEngineV2Error(f"{name}_must_be_timezone_aware")


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class InvalidationRuleV2:
    metric: str
    operator: RuleOperator
    threshold: Decimal

    def validate(self) -> None:
        if not self.metric.strip() or not self.threshold.is_finite():
            raise InvestmentEngineV2Error("invalid_invalidation_rule")

    def breached(self, value: Decimal) -> bool:
        return value < self.threshold if self.operator is RuleOperator.BELOW else value > self.threshold


@dataclass(frozen=True, slots=True)
class ValuationAssumptionsV2:
    growth_rate: Decimal
    discount_rate: Decimal
    terminal_growth_rate: Decimal
    forecast_years: int
    model_version: str = "finite-dcf-v2"

    def validate(self) -> None:
        if (
            not self.model_version.strip()
            or self.forecast_years < 1
            or self.growth_rate <= Decimal("-1")
            or self.discount_rate <= self.terminal_growth_rate
            or self.discount_rate <= Decimal("-1")
        ):
            raise InvestmentEngineV2Error("invalid_valuation_assumptions")


@dataclass(frozen=True, slots=True)
class InvestmentThesisVersionV2:
    thesis_id: UUID
    instrument_id: UUID
    pit_instrument_id: str
    version: str
    statement: str
    horizon_days: int
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    invalidation_rules: tuple[InvalidationRuleV2, ...]
    required_fundamental_metrics: tuple[str, ...]
    required_macro_series: tuple[str, ...]
    valuation: ValuationAssumptionsV2
    created_at: datetime
    knowledge_cutoff: datetime
    implementation_version: str
    status: ThesisResearchStatus = ThesisResearchStatus.RESEARCH_ONLY
    parent_thesis_id: UUID | None = None
    limitations: tuple[str, ...] = FIXTURE_LIMITATIONS
    thesis_schema_version: str = "investment-thesis-v2"

    def validate(self) -> None:
        for value in (
            self.pit_instrument_id,
            self.version,
            self.statement,
            self.implementation_version,
            self.thesis_schema_version,
        ):
            if not value.strip():
                raise InvestmentEngineV2Error("invalid_thesis_identity")
        _aware(self.created_at, "thesis_created_at")
        _aware(self.knowledge_cutoff, "thesis_knowledge_cutoff")
        if (
            self.horizon_days < 1
            or self.knowledge_cutoff > self.created_at
            or not self.catalysts
            or not self.risks
            or not self.invalidation_rules
            or not self.required_fundamental_metrics
            or not QUALITY_REQUIRED_METRICS.issubset(self.required_fundamental_metrics)
            or len(set(self.required_fundamental_metrics)) != len(self.required_fundamental_metrics)
            or len(set(self.required_macro_series)) != len(self.required_macro_series)
            or not self.limitations
        ):
            raise InvestmentEngineV2Error("invalid_thesis_contract")
        for rule in self.invalidation_rules:
            rule.validate()
        self.valuation.validate()

    def contract(self) -> dict[str, object]:
        return {
            "statement": self.statement,
            "horizon_days": self.horizon_days,
            "catalysts": self.catalysts,
            "risks": self.risks,
            "invalidation_rules": tuple(
                {"metric": rule.metric, "operator": rule.operator.value, "threshold": str(rule.threshold)}
                for rule in self.invalidation_rules
            ),
            "required_fundamental_metrics": self.required_fundamental_metrics,
            "required_macro_series": self.required_macro_series,
            "valuation": {
                "growth_rate": str(self.valuation.growth_rate),
                "discount_rate": str(self.valuation.discount_rate),
                "terminal_growth_rate": str(self.valuation.terminal_growth_rate),
                "forecast_years": self.valuation.forecast_years,
                "model_version": self.valuation.model_version,
            },
            "implementation_version": self.implementation_version,
            "limitations": self.limitations,
        }

    def content_hash(self) -> str:
        return _digest(
            {
                "thesis_id": self.thesis_id,
                "instrument_id": self.instrument_id,
                "pit_instrument_id": self.pit_instrument_id,
                "version": self.version,
                "parent_thesis_id": self.parent_thesis_id,
                "status": self.status.value,
                "created_at": self.created_at,
                "knowledge_cutoff": self.knowledge_cutoff,
                "schema": self.thesis_schema_version,
                "contract": self.contract(),
            }
        )


@dataclass(frozen=True, slots=True)
class MetricObservationV2:
    name: str
    state: InvestmentEvidenceState
    value: Decimal | None
    unit: str
    source_fact_ids: tuple[UUID, ...]

    def validate(self) -> None:
        if not self.name.strip() or not self.unit.strip():
            raise InvestmentEngineV2Error("invalid_investment_metric")
        if self.state is InvestmentEvidenceState.UNAVAILABLE:
            if self.value is not None or self.source_fact_ids:
                raise InvestmentEngineV2Error("unavailable_investment_metric_has_evidence")
        elif self.value is None or not self.source_fact_ids:
            raise InvestmentEngineV2Error("measured_investment_metric_missing_evidence")


@dataclass(frozen=True, slots=True)
class CompanyQualityEvidenceV2:
    state: InvestmentEvidenceState
    score: Decimal | None
    formula_version: str
    metrics: tuple[MetricObservationV2, ...]
    missing_metrics: tuple[str, ...]
    content_hash: str = field(init=False)
    evidence_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        for metric in self.metrics:
            metric.validate()
        if (
            not self.formula_version.strip()
            or (self.state is InvestmentEvidenceState.UNAVAILABLE) != (self.score is None)
            or (self.state is InvestmentEvidenceState.MEASURED and self.missing_metrics)
        ):
            raise InvestmentEngineV2Error("invalid_company_quality_evidence")
        payload = self.payload()
        content_hash = _digest(payload)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "evidence_id", uuid5(NAMESPACE_URL, f"investment-quality:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "score": None if self.score is None else str(self.score),
            "formula_version": self.formula_version,
            "metrics": tuple(
                {
                    "name": item.name,
                    "state": item.state.value,
                    "value": None if item.value is None else str(item.value),
                    "unit": item.unit,
                    "source_fact_ids": tuple(str(value) for value in item.source_fact_ids),
                }
                for item in self.metrics
            ),
            "missing_metrics": self.missing_metrics,
        }


@dataclass(frozen=True, slots=True)
class ValuationEvidenceV2:
    state: InvestmentEvidenceState
    intrinsic_value_per_share: Decimal | None
    currency: str | None
    model_version: str
    source_fact_ids: tuple[UUID, ...]
    assumption_manifest: dict[str, str]
    content_hash: str = field(init=False)
    evidence_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        if (
            not self.model_version.strip()
            or (self.state is InvestmentEvidenceState.UNAVAILABLE)
            != (self.intrinsic_value_per_share is None)
            or (self.state is InvestmentEvidenceState.MEASURED and (not self.currency or not self.source_fact_ids))
        ):
            raise InvestmentEngineV2Error("invalid_valuation_evidence")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "evidence_id", uuid5(NAMESPACE_URL, f"investment-valuation:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "intrinsic_value_per_share": None if self.intrinsic_value_per_share is None else str(self.intrinsic_value_per_share),
            "currency": self.currency,
            "model_version": self.model_version,
            "source_fact_ids": tuple(str(value) for value in self.source_fact_ids),
            "assumption_manifest": self.assumption_manifest,
            "limitation": "MODEL_OUTPUT_NOT_PRICE_TARGET_OR_RECOMMENDATION",
        }


@dataclass(frozen=True, slots=True)
class ThesisDriftEvidenceV2:
    state: InvestmentEvidenceState
    breached_rules: tuple[str, ...]
    evaluated_values: tuple[tuple[str, Decimal], ...]
    fundamental_fact_ids: tuple[UUID, ...]
    macro_observation_ids: tuple[UUID, ...]
    missing_inputs: tuple[str, ...]
    content_hash: str = field(init=False)
    evidence_id: UUID = field(init=False)

    def __post_init__(self) -> None:
        if self.state is InvestmentEvidenceState.MEASURED and self.missing_inputs:
            raise InvestmentEngineV2Error("measured_drift_has_missing_inputs")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "evidence_id", uuid5(NAMESPACE_URL, f"investment-drift:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "breached_rules": self.breached_rules,
            "evaluated_values": tuple((name, str(value)) for name, value in self.evaluated_values),
            "fundamental_fact_ids": tuple(str(value) for value in self.fundamental_fact_ids),
            "macro_observation_ids": tuple(str(value) for value in self.macro_observation_ids),
            "missing_inputs": self.missing_inputs,
        }


@dataclass(frozen=True, slots=True)
class InvestmentAnalysisPackageV2:
    thesis_id: UUID
    thesis_content_hash: str
    as_of: datetime
    quality: CompanyQualityEvidenceV2
    valuation: ValuationEvidenceV2
    drift: ThesisDriftEvidenceV2
    data_health_status: str
    data_health_assessment_ids: tuple[UUID, ...]
    status: InvestmentReviewStatus
    reasons: tuple[str, ...]
    limitations: tuple[str, ...] = FIXTURE_LIMITATIONS
    analysis_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.as_of, "analysis_as_of")
        if (
            len(self.thesis_content_hash) != 64
            or self.data_health_status not in {"HEALTHY", "BLOCKED"}
            or not self.data_health_assessment_ids
            or not self.limitations
        ):
            raise InvestmentEngineV2Error("invalid_analysis_binding")
        if self.status is InvestmentReviewStatus.BLOCKED and not self.reasons:
            raise InvestmentEngineV2Error("blocked_analysis_requires_reasons")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "analysis_id", uuid5(NAMESPACE_URL, f"investment-analysis:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "thesis_id": str(self.thesis_id),
            "thesis_content_hash": self.thesis_content_hash,
            "as_of": self.as_of,
            "quality_id": str(self.quality.evidence_id),
            "quality_hash": self.quality.content_hash,
            "valuation_id": str(self.valuation.evidence_id),
            "valuation_hash": self.valuation.content_hash,
            "drift_id": str(self.drift.evidence_id),
            "drift_hash": self.drift.content_hash,
            "data_health_status": self.data_health_status,
            "data_health_assessment_ids": tuple(str(value) for value in self.data_health_assessment_ids),
            "status": self.status.value,
            "reasons": self.reasons,
            "limitations": self.limitations,
        }


class FundamentalAuthority(Protocol):
    def available_as_of(
        self, instrument_id: str, as_of: datetime, *, standardized_metric: str | None = None,
    ) -> tuple[AvailableFilingFact, ...]: ...


class MacroAuthority(Protocol):
    def available_as_of(self, series_code: str, as_of: datetime) -> tuple[PitMacroObservation, ...]: ...


def _latest_standardized(
    facts: tuple[AvailableFilingFact, ...],
) -> dict[str, AvailableFilingFact]:
    latest: dict[str, AvailableFilingFact] = {}
    for item in facts:
        metric = item.fact.standardized_metric
        if metric is None or item.fact.standardized_value is None:
            continue
        previous = latest.get(metric)
        if previous is None or (
            item.filing.reporting_period_end,
            item.filing.accepted_at,
            item.filing.revision,
        ) > (
            previous.filing.reporting_period_end,
            previous.filing.accepted_at,
            previous.filing.revision,
        ):
            latest[metric] = item
    return latest


class InvestmentResearchOrchestratorV2:
    def __init__(self, fundamentals: FundamentalAuthority, macro: MacroAuthority) -> None:
        self._fundamentals = fundamentals
        self._macro = macro

    def analyze(
        self,
        thesis: InvestmentThesisVersionV2,
        *,
        as_of: datetime,
        data_health_status: str,
        data_health_assessment_ids: tuple[UUID, ...],
    ) -> InvestmentAnalysisPackageV2:
        thesis.validate()
        _aware(as_of, "analysis_as_of")
        if data_health_status not in {"HEALTHY", "BLOCKED"} or not data_health_assessment_ids:
            raise InvestmentEngineV2Error("data_health_evidence_required")
        if as_of < thesis.knowledge_cutoff:
            raise InvestmentEngineV2Error("analysis_precedes_thesis_knowledge_cutoff")
        facts = self._fundamentals.available_as_of(thesis.pit_instrument_id, as_of)
        if any(item.filing.accepted_at > as_of or item.filing.ingested_at > as_of for item in facts):
            raise InvestmentEngineV2Error("future_fundamental_evidence_rejected")
        latest = _latest_standardized(facts)
        missing = tuple(sorted(set(thesis.required_fundamental_metrics) - latest.keys()))
        quality = self._quality(latest, missing)
        valuation = self._valuation(thesis, latest)
        macro_latest: dict[str, PitMacroObservation] = {}
        for series in thesis.required_macro_series:
            observations = self._macro.available_as_of(series, as_of)
            if any(item.release_at > as_of or item.ingested_at > as_of for item in observations):
                raise InvestmentEngineV2Error("future_macro_evidence_rejected")
            if observations:
                macro_latest[series] = observations[-1]
        drift = self._drift(thesis, latest, macro_latest)
        reasons = []
        if quality.state is InvestmentEvidenceState.UNAVAILABLE:
            reasons.append("company_quality_unavailable")
        if valuation.state is InvestmentEvidenceState.UNAVAILABLE:
            reasons.append("valuation_unavailable")
        if drift.state is InvestmentEvidenceState.UNAVAILABLE:
            reasons.append("thesis_drift_unavailable")
        if drift.breached_rules:
            reasons.append("thesis_invalidation_breached")
        if data_health_status == "BLOCKED":
            reasons.append("data_health_blocked")
        status = InvestmentReviewStatus.BLOCKED if reasons else InvestmentReviewStatus.REVIEW_REQUIRED
        return InvestmentAnalysisPackageV2(
            thesis.thesis_id,
            thesis.content_hash(),
            as_of,
            quality,
            valuation,
            drift,
            data_health_status,
            data_health_assessment_ids,
            status,
            tuple(reasons),
        )

    @staticmethod
    def _quality(
        latest: dict[str, AvailableFilingFact], missing: tuple[str, ...],
    ) -> CompanyQualityEvidenceV2:
        if missing:
            return CompanyQualityEvidenceV2(
                InvestmentEvidenceState.UNAVAILABLE,
                None,
                "transparent-company-quality-v2",
                tuple(
                    MetricObservationV2(name, InvestmentEvidenceState.UNAVAILABLE, None, "ratio", ())
                    for name in missing
                ),
                missing,
            )
        values = {
            name: item.fact.standardized_value
            for name, item in latest.items()
            if item.fact.standardized_value is not None
        }
        metrics = derive_research_metrics(values, prior_shares=None)
        fcf_margin = metrics.free_cash_flow / metrics.revenue
        leverage = metrics.debt / metrics.revenue
        raw_score = Decimal("50") + metrics.operating_margin * Decimal("25") + fcf_margin * Decimal("25") + metrics.roic * Decimal("30") - leverage * Decimal("10")
        score = min(Decimal("100"), max(Decimal("0"), raw_score))
        source_ids = tuple(sorted((item.fact.fact_id for item in latest.values()), key=str))
        observations = (
            MetricObservationV2("operating_margin", InvestmentEvidenceState.MEASURED, metrics.operating_margin, "ratio", source_ids),
            MetricObservationV2("free_cash_flow_margin", InvestmentEvidenceState.MEASURED, fcf_margin, "ratio", source_ids),
            MetricObservationV2("roic", InvestmentEvidenceState.MEASURED, metrics.roic, "ratio", source_ids),
            MetricObservationV2("debt_to_revenue", InvestmentEvidenceState.MEASURED, leverage, "ratio", source_ids),
        )
        return CompanyQualityEvidenceV2(
            InvestmentEvidenceState.MEASURED,
            score,
            "transparent-company-quality-v2",
            observations,
            (),
        )

    @staticmethod
    def _valuation(
        thesis: InvestmentThesisVersionV2, latest: dict[str, AvailableFilingFact],
    ) -> ValuationEvidenceV2:
        fcf = latest.get("free_cash_flow")
        fcf_ids: tuple[UUID, ...]
        fcf_currency: str | None
        if fcf is None:
            operating_cash = latest.get("operating_cash_flow")
            capex = latest.get("capital_expenditures")
            if operating_cash is not None and capex is not None:
                fcf_value = operating_cash.fact.standardized_value
                capex_value = capex.fact.standardized_value
                if fcf_value is not None and capex_value is not None:
                    current_fcf = fcf_value - capex_value
                    fcf_ids = (operating_cash.fact.fact_id, capex.fact.fact_id)
                    fcf_currency = operating_cash.fact.currency
                else:
                    current_fcf = None
                    fcf_ids = ()
                    fcf_currency = None
            else:
                current_fcf = None
                fcf_ids = ()
                fcf_currency = None
        else:
            current_fcf = fcf.fact.standardized_value
            fcf_ids = (fcf.fact.fact_id,)
            fcf_currency = fcf.fact.currency
        shares = latest.get("shares_outstanding")
        shares_value = None if shares is None else shares.fact.standardized_value
        assumptions = {
            "growth_rate": str(thesis.valuation.growth_rate),
            "discount_rate": str(thesis.valuation.discount_rate),
            "terminal_growth_rate": str(thesis.valuation.terminal_growth_rate),
            "forecast_years": str(thesis.valuation.forecast_years),
        }
        if (
            current_fcf is None
            or current_fcf <= 0
            or shares is None
            or shares_value is None
            or shares_value <= 0
        ):
            return ValuationEvidenceV2(
                InvestmentEvidenceState.UNAVAILABLE,
                None,
                None,
                thesis.valuation.model_version,
                (),
                assumptions,
            )
        cash_flow = current_fcf
        present_value = Decimal("0")
        for year in range(1, thesis.valuation.forecast_years + 1):
            cash_flow *= Decimal("1") + thesis.valuation.growth_rate
            present_value += cash_flow / ((Decimal("1") + thesis.valuation.discount_rate) ** year)
        terminal = cash_flow * (Decimal("1") + thesis.valuation.terminal_growth_rate) / (thesis.valuation.discount_rate - thesis.valuation.terminal_growth_rate)
        present_value += terminal / ((Decimal("1") + thesis.valuation.discount_rate) ** thesis.valuation.forecast_years)
        currency = shares.fact.currency or fcf_currency
        if currency is None:
            return ValuationEvidenceV2(
                InvestmentEvidenceState.UNAVAILABLE,
                None,
                None,
                thesis.valuation.model_version,
                (),
                assumptions,
            )
        return ValuationEvidenceV2(
            InvestmentEvidenceState.MEASURED,
            present_value / shares_value,
            currency,
            thesis.valuation.model_version,
            tuple(sorted((*fcf_ids, shares.fact.fact_id), key=str)),
            assumptions,
        )

    @staticmethod
    def _drift(
        thesis: InvestmentThesisVersionV2,
        latest: dict[str, AvailableFilingFact],
        macro_latest: dict[str, PitMacroObservation],
    ) -> ThesisDriftEvidenceV2:
        values: dict[str, Decimal] = {}
        fact_ids: set[UUID] = set()
        macro_ids: set[UUID] = set()
        missing: list[str] = []
        breached: list[str] = []
        for rule in thesis.invalidation_rules:
            if rule.metric.startswith("macro:"):
                series = rule.metric.removeprefix("macro:")
                observation = macro_latest.get(series)
                value = None if observation is None else observation.actual
                if observation is not None:
                    macro_ids.add(observation.observation_id)
            else:
                metric = rule.metric.removeprefix("fundamental:")
                fact = latest.get(metric)
                value = None if fact is None else fact.fact.standardized_value
                if fact is not None:
                    fact_ids.add(fact.fact.fact_id)
            if value is None:
                missing.append(rule.metric)
                continue
            values[rule.metric] = value
            if rule.breached(value):
                breached.append(f"{rule.metric}:{rule.operator.value}:{rule.threshold}")
        state = InvestmentEvidenceState.UNAVAILABLE if missing else InvestmentEvidenceState.MEASURED
        return ThesisDriftEvidenceV2(
            state,
            tuple(breached),
            tuple(sorted(values.items())),
            tuple(sorted(fact_ids, key=str)),
            tuple(sorted(macro_ids, key=str)),
            tuple(sorted(missing)),
        )


@dataclass(frozen=True, slots=True)
class InvestmentPortfolioPolicyV2:
    policy_id: UUID
    version: str
    account_id: str
    base_currency: str
    maximum_investment_value: Decimal
    maximum_single_weight: Decimal
    minimum_cash_weight: Decimal
    maximum_rebalance_turnover: Decimal
    approved_by: str
    approved_at: datetime
    limitations: tuple[str, ...] = FIXTURE_LIMITATIONS
    capital_domain: str = "LONG_TERM_INVESTMENT"
    policy_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.approved_at, "policy_approved_at")
        if (
            not all(value.strip() for value in (self.version, self.account_id, self.approved_by))
            or len(self.base_currency) != 3
            or self.maximum_investment_value <= 0
            or not Decimal("0") < self.maximum_single_weight <= Decimal("1")
            or not Decimal("0") <= self.minimum_cash_weight < Decimal("1")
            or not Decimal("0") <= self.maximum_rebalance_turnover <= Decimal("2")
            or self.capital_domain != "LONG_TERM_INVESTMENT"
            or not self.limitations
        ):
            raise InvestmentEngineV2Error("invalid_investment_policy")
        payload = self.payload()
        content_hash = _digest(payload)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "policy_version_id", uuid5(NAMESPACE_URL, f"investment-policy:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "policy_id": str(self.policy_id), "version": self.version,
            "account_id": self.account_id, "base_currency": self.base_currency,
            "maximum_investment_value": str(self.maximum_investment_value),
            "maximum_single_weight": str(self.maximum_single_weight),
            "minimum_cash_weight": str(self.minimum_cash_weight),
            "maximum_rebalance_turnover": str(self.maximum_rebalance_turnover),
            "approved_by": self.approved_by, "approved_at": self.approved_at,
            "limitations": self.limitations, "capital_domain": self.capital_domain,
        }


@dataclass(frozen=True, slots=True)
class RebalanceCandidateV2:
    policy_version_id: UUID
    account_id: str
    as_of: datetime
    holdings_snapshot_hash: str
    analysis_evidence_ids: tuple[UUID, ...]
    current_weights: tuple[tuple[str, Decimal], ...]
    candidate_weights: tuple[tuple[str, Decimal], ...]
    turnover: Decimal
    status: InvestmentReviewStatus
    reasons: tuple[str, ...]
    limitations: tuple[str, ...] = FIXTURE_LIMITATIONS
    execution_authority: bool = False
    candidate_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.as_of, "rebalance_as_of")
        if (
            not self.account_id.strip()
            or len(self.holdings_snapshot_hash) != 64
            or not self.analysis_evidence_ids
            or self.turnover < 0
            or self.execution_authority
            or not self.limitations
            or (self.status is InvestmentReviewStatus.BLOCKED and not self.reasons)
        ):
            raise InvestmentEngineV2Error("invalid_rebalance_candidate")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "candidate_id", uuid5(NAMESPACE_URL, f"investment-rebalance:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "policy_version_id": str(self.policy_version_id), "account_id": self.account_id,
            "as_of": self.as_of, "holdings_snapshot_hash": self.holdings_snapshot_hash,
            "analysis_evidence_ids": tuple(str(value) for value in self.analysis_evidence_ids),
            "current_weights": tuple((name, str(value)) for name, value in self.current_weights),
            "candidate_weights": tuple((name, str(value)) for name, value in self.candidate_weights),
            "turnover": str(self.turnover), "status": self.status.value,
            "reasons": self.reasons, "limitations": self.limitations,
            "execution_authority": self.execution_authority,
        }


def build_rebalance_candidate(
    policy: InvestmentPortfolioPolicyV2,
    *,
    as_of: datetime,
    holdings_snapshot_hash: str,
    current_weights: dict[str, Decimal],
    candidate_weights: dict[str, Decimal],
    analyses: tuple[InvestmentAnalysisPackageV2, ...],
) -> RebalanceCandidateV2:
    if any(value < 0 for value in (*current_weights.values(), *candidate_weights.values())):
        raise InvestmentEngineV2Error("negative_investment_weight")
    reasons: list[str] = []
    maximum_allocated = Decimal("1") - policy.minimum_cash_weight
    if sum(candidate_weights.values(), Decimal("0")) > maximum_allocated:
        reasons.append("minimum_cash_weight_breached")
    if any(value > policy.maximum_single_weight for value in candidate_weights.values()):
        reasons.append("maximum_single_weight_breached")
    if any(item.status is InvestmentReviewStatus.BLOCKED for item in analyses):
        reasons.append("analysis_evidence_blocked")
    names = set(current_weights) | set(candidate_weights)
    turnover = sum(
        (abs(candidate_weights.get(name, Decimal("0")) - current_weights.get(name, Decimal("0"))) for name in names),
        Decimal("0"),
    )
    if turnover > policy.maximum_rebalance_turnover:
        reasons.append("maximum_rebalance_turnover_breached")
    status = InvestmentReviewStatus.BLOCKED if reasons else InvestmentReviewStatus.REVIEW_REQUIRED
    return RebalanceCandidateV2(
        policy.policy_version_id,
        policy.account_id,
        as_of,
        holdings_snapshot_hash,
        tuple(item.analysis_id for item in analyses),
        tuple(sorted(current_weights.items())),
        tuple(sorted(candidate_weights.items())),
        turnover,
        status,
        tuple(reasons),
    )


class PostgresInvestmentEngineV2Store:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def publish_thesis(self, thesis: InvestmentThesisVersionV2) -> UUID:
        thesis.validate()
        content_hash = thesis.content_hash()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM instruments WHERE instrument_id=%s", (thesis.instrument_id,))
                if cursor.fetchone() is None:
                    raise InvestmentEngineV2Error("investment_instrument_unavailable")
                cursor.execute("SELECT 1 FROM professional_instruments WHERE instrument_id=%s", (thesis.pit_instrument_id,))
                if cursor.fetchone() is None:
                    raise InvestmentEngineV2Error("pit_instrument_unavailable")
                if thesis.parent_thesis_id is not None:
                    cursor.execute("SELECT instrument_id FROM investment_theses WHERE thesis_id=%s", (thesis.parent_thesis_id,))
                    parent = cursor.fetchone()
                    if parent is None or UUID(str(parent[0])) != thesis.instrument_id:
                        raise InvestmentEngineV2Error("invalid_parent_thesis")
                cursor.execute(
                    "INSERT INTO investment_theses (thesis_id,instrument_id,version,status,created_at,thesis_schema_version,pit_instrument_id,parent_thesis_id,knowledge_cutoff,contract,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (thesis_id) DO NOTHING RETURNING content_hash",
                    (thesis.thesis_id, thesis.instrument_id, thesis.version, thesis.status.value, thesis.created_at,
                     thesis.thesis_schema_version, thesis.pit_instrument_id, thesis.parent_thesis_id,
                     thesis.knowledge_cutoff, _canonical(thesis.contract()), content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM investment_theses WHERE thesis_id=%s", (thesis.thesis_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != content_hash:
                        raise InvestmentEngineV2Error("thesis_replay_conflict")
            return thesis.thesis_id
        except InvestmentEngineV2Error:
            raise
        except Exception as error:
            raise InvestmentEngineV2Error("thesis_publication_failed") from error

    def publish_analysis(self, analysis: InvestmentAnalysisPackageV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT content_hash FROM investment_theses WHERE thesis_id=%s", (analysis.thesis_id,))
                thesis = cursor.fetchone()
                if thesis is None or str(thesis[0]) != analysis.thesis_content_hash:
                    raise InvestmentEngineV2Error("analysis_thesis_binding_mismatch")
                payload = analysis.payload()
                payload["quality"] = analysis.quality.payload()
                payload["valuation"] = analysis.valuation.payload()
                payload["drift"] = analysis.drift.payload()
                cursor.execute(
                    "INSERT INTO investment_evidence (investment_evidence_id,thesis_id,occurred_at,payload,evidence_type,knowledge_cutoff,content_hash) VALUES (%s,%s,%s,%s::jsonb,'ANALYSIS_PACKAGE_V2',%s,%s) ON CONFLICT (investment_evidence_id) DO NOTHING RETURNING content_hash",
                    (analysis.analysis_id, analysis.thesis_id, analysis.as_of, _canonical(payload), analysis.as_of, analysis.content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM investment_evidence WHERE investment_evidence_id=%s", (analysis.analysis_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != analysis.content_hash:
                        raise InvestmentEngineV2Error("analysis_replay_conflict")
                for assessment_id in analysis.data_health_assessment_ids:
                    cursor.execute(
                        "INSERT INTO investment_evidence_data_health_assessments VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (analysis.analysis_id, assessment_id),
                    )
                cursor.execute(
                    "SELECT assessment_id FROM investment_evidence_data_health_assessments WHERE investment_evidence_id=%s",
                    (analysis.analysis_id,),
                )
                persisted_assessments = {UUID(str(row[0])) for row in cursor.fetchall()}
                if persisted_assessments != set(analysis.data_health_assessment_ids):
                    raise InvestmentEngineV2Error("analysis_data_health_binding_mismatch")
            return analysis.analysis_id
        except InvestmentEngineV2Error:
            raise
        except Exception as error:
            raise InvestmentEngineV2Error("analysis_publication_failed") from error

    def publish_policy(self, policy: InvestmentPortfolioPolicyV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT account_type FROM accounts WHERE account_id=%s", (policy.account_id,))
                account = cursor.fetchone()
                if account is None or str(account[0]) != "INVESTMENT":
                    raise InvestmentEngineV2Error("investment_policy_requires_investment_account")
                cursor.execute(
                    "INSERT INTO investment_policy_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (policy_version_id) DO NOTHING RETURNING content_hash",
                    (policy.policy_version_id, policy.policy_id, policy.version, policy.account_id,
                     policy.capital_domain, policy.base_currency, policy.maximum_investment_value,
                     policy.maximum_single_weight, policy.minimum_cash_weight,
                     policy.maximum_rebalance_turnover, policy.approved_by, policy.approved_at,
                     _canonical(policy.limitations), policy.content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM investment_policy_versions WHERE policy_version_id=%s", (policy.policy_version_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != policy.content_hash:
                        raise InvestmentEngineV2Error("investment_policy_replay_conflict")
            return policy.policy_version_id
        except InvestmentEngineV2Error:
            raise
        except Exception as error:
            raise InvestmentEngineV2Error("investment_policy_publication_failed") from error

    def publish_rebalance_candidate(self, candidate: RebalanceCandidateV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT account_id FROM investment_policy_versions WHERE policy_version_id=%s", (candidate.policy_version_id,))
                policy = cursor.fetchone()
                if policy is None or str(policy[0]) != candidate.account_id:
                    raise InvestmentEngineV2Error("rebalance_policy_binding_mismatch")
                for evidence_id in candidate.analysis_evidence_ids:
                    cursor.execute("SELECT 1 FROM investment_evidence WHERE investment_evidence_id=%s AND evidence_type='ANALYSIS_PACKAGE_V2'", (evidence_id,))
                    if cursor.fetchone() is None:
                        raise InvestmentEngineV2Error("rebalance_analysis_evidence_unavailable")
                cursor.execute(
                    "INSERT INTO investment_rebalance_candidates VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,FALSE,%s) ON CONFLICT (candidate_id) DO NOTHING RETURNING content_hash",
                    (candidate.candidate_id, candidate.policy_version_id, candidate.account_id,
                     candidate.as_of, candidate.holdings_snapshot_hash,
                     _canonical(tuple(str(value) for value in candidate.analysis_evidence_ids)),
                     _canonical(dict(candidate.current_weights)), _canonical(dict(candidate.candidate_weights)),
                     candidate.turnover, candidate.status.value, _canonical(candidate.reasons),
                     _canonical(candidate.limitations), candidate.content_hash),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute("SELECT content_hash FROM investment_rebalance_candidates WHERE candidate_id=%s", (candidate.candidate_id,))
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != candidate.content_hash:
                        raise InvestmentEngineV2Error("rebalance_candidate_replay_conflict")
            return candidate.candidate_id
        except InvestmentEngineV2Error:
            raise
        except Exception as error:
            raise InvestmentEngineV2Error("rebalance_candidate_publication_failed") from error
