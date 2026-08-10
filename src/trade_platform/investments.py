"""Long-horizon investment theses and allocation recommendations, deliberately without order execution."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .domain import utc_now
from .fundamentals import SQLiteFundamentalStore
from .operational_alerts import AlertSeverity, SQLiteOperationalAlertStore


class InvestmentValidationError(ValueError):
    pass


class ThesisStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ThesisReviewOutcome(StrEnum):
    REAFFIRM = "REAFFIRM"
    REVISE = "REVISE"
    INVALIDATE = "INVALIDATE"


@dataclass(frozen=True, slots=True)
class InvestmentThesis:
    thesis_id: UUID
    instrument_id: str
    version: str
    statement: str
    horizon_days: int
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    status: ThesisStatus
    created_at: datetime

    def __post_init__(self) -> None:
        if (not self.instrument_id or not self.version or not self.statement or self.horizon_days < 1
                or not self.risks or not self.evidence_ids):
            raise InvestmentValidationError("invalid_investment_thesis")


@dataclass(frozen=True, slots=True)
class CompanyResearchRecord:
    """Versioned human-reviewed investment case, separated from executable trading signals."""

    record_id: UUID
    thesis_id: UUID
    instrument_id: str
    as_of: datetime
    bull_case: str
    base_case: str
    bear_case: str
    catalysts: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    position_sizing_rationale: str
    replacement_candidates: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not self.instrument_id or not self.bull_case or not self.base_case or not self.bear_case
                or not self.catalysts or not self.invalidation_conditions or not self.position_sizing_rationale
                or not self.evidence_ids or self.as_of.tzinfo is None or self.as_of.utcoffset() is None):
            raise InvestmentValidationError("invalid_company_research_record")


@dataclass(frozen=True, slots=True)
class ThemeDiscoveryRule:
    theme: str
    phrases: tuple[str, ...]
    rule_version: str

    def __post_init__(self) -> None:
        if not self.theme or not self.phrases or not self.rule_version or any(not value.strip() for value in self.phrases):
            raise InvestmentValidationError("invalid_theme_discovery_rule")


@dataclass(frozen=True, slots=True)
class InvestmentThemeDiscovery:
    discovery_id: UUID
    thesis_id: UUID
    instrument_id: str
    theme: str
    score: Decimal
    company_research_id: UUID
    matched_phrases: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    rule_version: str
    discovered_at: datetime

    def __post_init__(self) -> None:
        if (not self.instrument_id or not self.theme or not Decimal("0") < self.score <= Decimal("1") or not self.matched_phrases
                or not self.evidence_ids or not self.rule_version or self.discovered_at.tzinfo is None or self.discovered_at.utcoffset() is None):
            raise InvestmentValidationError("invalid_investment_theme_discovery")


@dataclass(frozen=True, slots=True)
class InvestmentRecommendation:
    recommendation_id: UUID
    thesis_id: UUID
    target_weight: Decimal
    maximum_weight: Decimal
    confidence: Decimal
    expected_return_low: Decimal
    expected_return_high: Decimal
    rationale_evidence_ids: tuple[str, ...]
    as_of: datetime

    def __post_init__(self) -> None:
        if (not Decimal("0") <= self.target_weight <= self.maximum_weight <= Decimal("1")
                or not Decimal("0") <= self.confidence <= Decimal("1")
                or self.expected_return_low > self.expected_return_high or not self.rationale_evidence_ids):
            raise InvestmentValidationError("invalid_investment_recommendation")


@dataclass(frozen=True, slots=True)
class SourceBackedFact:
    """A point-in-time investment fact; values are never silently replaced."""

    fact_id: UUID
    instrument_id: str
    metric: str
    value: Decimal
    unit: str
    observed_at: datetime
    available_at: datetime
    ingested_at: datetime
    source_reference: str
    source_url: str
    revision: int = 0

    def __post_init__(self) -> None:
        timestamps = (self.observed_at, self.available_at, self.ingested_at)
        if (not self.instrument_id or not self.metric or not self.unit or not self.source_reference
                or not self.source_url or self.revision < 0 or not self.value.is_finite()
                or any(value.tzinfo is None or value.utcoffset() is None for value in timestamps)
                or self.available_at < self.observed_at):
            raise InvestmentValidationError("invalid_source_backed_fact")


@dataclass(frozen=True, slots=True)
class DcfValuation:
    """Transparent finite-horizon DCF, intentionally a research record rather than a price target."""

    valuation_id: UUID
    thesis_id: UUID
    instrument_id: str
    as_of: datetime
    currency: str
    current_free_cash_flow: Decimal
    growth_rate: Decimal
    discount_rate: Decimal
    terminal_growth_rate: Decimal
    forecast_years: int
    shares_outstanding: Decimal
    source_fact_ids: tuple[UUID, ...]
    model_version: str = "finite-dcf-v1"

    def __post_init__(self) -> None:
        if (not self.instrument_id or not self.currency or not self.model_version or self.forecast_years < 1
                or self.current_free_cash_flow <= 0 or self.shares_outstanding <= 0
                or self.discount_rate <= self.terminal_growth_rate or self.discount_rate <= Decimal("-1")
                or self.growth_rate <= Decimal("-1") or not self.source_fact_ids
                or self.as_of.tzinfo is None or self.as_of.utcoffset() is None):
            raise InvestmentValidationError("invalid_dcf_valuation")

    def intrinsic_value_per_share(self) -> Decimal:
        """Return the deterministic present value implied by the recorded assumptions."""
        cash_flow = self.current_free_cash_flow
        present_value = Decimal("0")
        for year in range(1, self.forecast_years + 1):
            cash_flow *= Decimal("1") + self.growth_rate
            present_value += cash_flow / ((Decimal("1") + self.discount_rate) ** year)
        terminal_value = cash_flow * (Decimal("1") + self.terminal_growth_rate) / (self.discount_rate - self.terminal_growth_rate)
        present_value += terminal_value / ((Decimal("1") + self.discount_rate) ** self.forecast_years)
        return present_value / self.shares_outstanding


@dataclass(frozen=True, slots=True)
class ThesisExpectation:
    metric: str
    minimum: Decimal | None
    maximum: Decimal | None

    def __post_init__(self) -> None:
        if (not self.metric or (self.minimum is None and self.maximum is None)
                or (self.minimum is not None and self.maximum is not None and self.minimum > self.maximum)):
            raise InvestmentValidationError("invalid_thesis_expectation")


@dataclass(frozen=True, slots=True)
class ThesisDriftAssessment:
    thesis_id: UUID
    as_of: datetime
    checked_fact_ids: tuple[UUID, ...]
    breached_metrics: tuple[str, ...]

    @property
    def drift_detected(self) -> bool:
        return bool(self.breached_metrics)


@dataclass(frozen=True, slots=True)
class ThesisReview:
    review_id: UUID
    thesis_id: UUID
    outcome: ThesisReviewOutcome
    reviewed_at: datetime
    rationale: str
    evidence_ids: tuple[str, ...]
    drift_assessment: ThesisDriftAssessment
    next_review_at: datetime

    def __post_init__(self) -> None:
        if (not self.rationale or not self.evidence_ids or self.reviewed_at.tzinfo is None
                or self.next_review_at.tzinfo is None or self.next_review_at <= self.reviewed_at
                or self.drift_assessment.thesis_id != self.thesis_id):
            raise InvestmentValidationError("invalid_thesis_review")


@dataclass(frozen=True, slots=True)
class InvestmentReviewSchedule:
    thesis_id: UUID
    cadence_days: int
    expectations: tuple[ThesisExpectation, ...]
    next_review_at: datetime
    approved_by: str

    def __post_init__(self) -> None:
        if self.cadence_days < 1 or not self.expectations or not self.approved_by or self.next_review_at.tzinfo is None or self.next_review_at.utcoffset() is None:
            raise InvestmentValidationError("invalid_investment_review_schedule")


@dataclass(frozen=True, slots=True)
class FundamentalAnalytics:
    analysis_id: UUID
    thesis_id: UUID
    as_of: datetime
    unit: str
    revenue_growth: Decimal
    net_margin: Decimal
    free_cash_flow_margin: Decimal
    debt_to_equity: Decimal
    net_debt: Decimal
    source_fact_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (not self.unit or not self.source_fact_ids or self.as_of.tzinfo is None or self.as_of.utcoffset() is None
                or not all(value.is_finite() for value in (self.revenue_growth, self.net_margin, self.free_cash_flow_margin, self.debt_to_equity, self.net_debt))):
            raise InvestmentValidationError("invalid_fundamental_analytics")


@dataclass(frozen=True, slots=True)
class InvestmentPortfolioPolicy:
    portfolio_id: str
    base_currency: str
    maximum_investment_value: Decimal
    maximum_single_weight: Decimal
    approved_by: str
    approved_at: datetime

    def __post_init__(self) -> None:
        if (not self.portfolio_id or not self.base_currency or not self.approved_by or self.maximum_investment_value <= 0
                or not Decimal('0') < self.maximum_single_weight <= Decimal('1') or self.approved_at.tzinfo is None):
            raise InvestmentValidationError('invalid_investment_portfolio_policy')


@dataclass(frozen=True, slots=True)
class InvestmentHolding:
    portfolio_id: str
    instrument_id: str
    market_value: Decimal
    observed_at: datetime
    source_reference: str

    def __post_init__(self) -> None:
        if not self.portfolio_id or not self.instrument_id or self.market_value < 0 or not self.source_reference or self.observed_at.tzinfo is None:
            raise InvestmentValidationError('invalid_investment_holding')


@dataclass(frozen=True, slots=True)
class InvestmentPortfolioAssessment:
    portfolio_id: str
    as_of: datetime
    total_value: Decimal
    approved: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InvestmentThemeExposure:
    instrument_id: str
    theme: str
    exposure: Decimal
    observed_at: datetime
    source_reference: str

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.theme or not Decimal('0') <= self.exposure <= Decimal('1') or not self.source_reference or self.observed_at.tzinfo is None:
            raise InvestmentValidationError('invalid_investment_theme_exposure')


@dataclass(frozen=True, slots=True)
class InvestmentMacroSensitivity:
    instrument_id: str
    macro_series_id: str
    sensitivity: Decimal
    observed_at: datetime
    source_reference: str

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.macro_series_id or not self.sensitivity.is_finite() or not self.source_reference or self.observed_at.tzinfo is None:
            raise InvestmentValidationError('invalid_investment_macro_sensitivity')


@dataclass(frozen=True, slots=True)
class InvestmentRebalanceDecision:
    decision_id: UUID
    portfolio_id: str
    decided_at: datetime
    rationale: str
    evidence_ids: tuple[str, ...]
    target_weights: dict[str, Decimal]
    approved_by: str

    def __post_init__(self) -> None:
        if (not self.portfolio_id or not self.rationale or not self.evidence_ids or not self.target_weights or not self.approved_by
                or self.decided_at.tzinfo is None or any(value < 0 for value in self.target_weights.values()) or sum(self.target_weights.values(), Decimal('0')) > Decimal('1')):
            raise InvestmentValidationError('invalid_investment_rebalance_decision')


@dataclass(frozen=True, slots=True)
class InvestmentPerformanceSnapshot:
    portfolio_id: str
    observed_at: datetime
    net_asset_value: Decimal
    cumulative_return: Decimal
    source_reference: str

    def __post_init__(self) -> None:
        if not self.portfolio_id or self.net_asset_value < 0 or not self.cumulative_return.is_finite() or not self.source_reference or self.observed_at.tzinfo is None:
            raise InvestmentValidationError('invalid_investment_performance_snapshot')


@dataclass(frozen=True, slots=True)
class RebalanceAction:
    instrument_id: str
    current_weight: Decimal
    target_weight: Decimal
    delta_weight: Decimal
    rationale: str


def plan_rebalance(current_weights: dict[str, Decimal], targets: dict[str, Decimal], maximum_total_weight: Decimal = Decimal("1")) -> list[RebalanceAction]:
    if maximum_total_weight <= 0 or any(weight < 0 for weight in current_weights.values()) or any(weight < 0 for weight in targets.values()):
        raise InvestmentValidationError("invalid_rebalance_weights")
    if sum(targets.values(), Decimal("0")) > maximum_total_weight:
        raise InvestmentValidationError("target_allocation_exceeds_limit")
    actions = []
    for instrument_id in sorted(set(current_weights) | set(targets)):
        current, target = current_weights.get(instrument_id, Decimal("0")), targets.get(instrument_id, Decimal("0"))
        if current != target:
            actions.append(RebalanceAction(instrument_id, current, target, target - current, "target_allocation_change"))
    return actions


def scenario_value(current_value: Decimal, allocation_weight: Decimal, shock: Decimal) -> Decimal:
    if current_value < 0 or not Decimal("0") <= allocation_weight <= Decimal("1") or shock < Decimal("-1"):
        raise InvestmentValidationError("invalid_investment_scenario")
    return current_value * allocation_weight * shock


class SQLiteInvestmentStore:
    """Append-only local evidence registry for human-reviewed investment decisions."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_theses (
            thesis_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, version TEXT NOT NULL, statement TEXT NOT NULL,
            horizon_days INTEGER NOT NULL, catalysts_json TEXT NOT NULL, risks_json TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_company_research (
            record_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, instrument_id TEXT NOT NULL, as_of TEXT NOT NULL,
            bull_case TEXT NOT NULL, base_case TEXT NOT NULL, bear_case TEXT NOT NULL, catalysts_json TEXT NOT NULL,
            invalidation_conditions_json TEXT NOT NULL, position_sizing_rationale TEXT NOT NULL,
            replacement_candidates_json TEXT NOT NULL, evidence_ids_json TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_theme_discoveries (
            discovery_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, instrument_id TEXT NOT NULL, theme TEXT NOT NULL,
            score TEXT NOT NULL, company_research_id TEXT NOT NULL, matched_phrases_json TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL, rule_version TEXT NOT NULL, discovered_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_facts (
            fact_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, metric TEXT NOT NULL, value TEXT NOT NULL,
            unit TEXT NOT NULL, observed_at TEXT NOT NULL, available_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
            source_reference TEXT NOT NULL, source_url TEXT NOT NULL, revision INTEGER NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_valuations (
            valuation_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, instrument_id TEXT NOT NULL, as_of TEXT NOT NULL,
            currency TEXT NOT NULL, current_free_cash_flow TEXT NOT NULL, growth_rate TEXT NOT NULL,
            discount_rate TEXT NOT NULL, terminal_growth_rate TEXT NOT NULL, forecast_years INTEGER NOT NULL,
            shares_outstanding TEXT NOT NULL, source_fact_ids_json TEXT NOT NULL, model_version TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_recommendations (
            recommendation_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, target_weight TEXT NOT NULL,
            maximum_weight TEXT NOT NULL, confidence TEXT NOT NULL, expected_return_low TEXT NOT NULL,
            expected_return_high TEXT NOT NULL, rationale_evidence_ids_json TEXT NOT NULL, as_of TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_thesis_reviews (
            review_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, outcome TEXT NOT NULL, reviewed_at TEXT NOT NULL,
            rationale TEXT NOT NULL, evidence_ids_json TEXT NOT NULL, checked_fact_ids_json TEXT NOT NULL,
            breached_metrics_json TEXT NOT NULL, assessment_as_of TEXT NOT NULL, next_review_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_review_schedules (
            thesis_id TEXT PRIMARY KEY, cadence_days INTEGER NOT NULL, expectations_json TEXT NOT NULL, next_review_at TEXT NOT NULL, approved_by TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_scheduled_drift_assessments (
            assessment_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, assessed_at TEXT NOT NULL, checked_fact_ids_json TEXT NOT NULL,
            breached_metrics_json TEXT NOT NULL, scheduled_for TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_fundamental_materialization_commands (
            idempotency_key TEXT PRIMARY KEY, actor TEXT NOT NULL, instrument_id TEXT NOT NULL, as_of TEXT NOT NULL,
            fact_names_json TEXT NOT NULL, status TEXT NOT NULL, fact_ids_json TEXT, error TEXT, requested_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS investment_fundamental_analytics (
            analysis_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL, as_of TEXT NOT NULL, unit TEXT NOT NULL,
            revenue_growth TEXT NOT NULL, net_margin TEXT NOT NULL, free_cash_flow_margin TEXT NOT NULL,
            debt_to_equity TEXT NOT NULL, net_debt TEXT NOT NULL, source_fact_ids_json TEXT NOT NULL)""")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_portfolio_policies (portfolio_id TEXT PRIMARY KEY, base_currency TEXT NOT NULL, maximum_investment_value TEXT NOT NULL, maximum_single_weight TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL)")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_holdings (portfolio_id TEXT NOT NULL, instrument_id TEXT NOT NULL, market_value TEXT NOT NULL, observed_at TEXT NOT NULL, source_reference TEXT NOT NULL, PRIMARY KEY (portfolio_id, instrument_id, observed_at))")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_theme_exposures (instrument_id TEXT NOT NULL, theme TEXT NOT NULL, exposure TEXT NOT NULL, observed_at TEXT NOT NULL, source_reference TEXT NOT NULL, PRIMARY KEY (instrument_id, theme, observed_at))")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_macro_sensitivities (instrument_id TEXT NOT NULL, macro_series_id TEXT NOT NULL, sensitivity TEXT NOT NULL, observed_at TEXT NOT NULL, source_reference TEXT NOT NULL, PRIMARY KEY (instrument_id, macro_series_id, observed_at))")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_rebalance_decisions (decision_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, decided_at TEXT NOT NULL, rationale TEXT NOT NULL, evidence_ids_json TEXT NOT NULL, target_weights_json TEXT NOT NULL, approved_by TEXT NOT NULL)")
        self._connection.execute("CREATE TABLE IF NOT EXISTS investment_performance_snapshots (portfolio_id TEXT NOT NULL, observed_at TEXT NOT NULL, net_asset_value TEXT NOT NULL, cumulative_return TEXT NOT NULL, source_reference TEXT NOT NULL, PRIMARY KEY (portfolio_id, observed_at))")
        self._connection.commit()

    def close(self) -> None:
        """Release the local research ledger explicitly (important for backup/reopen workflows)."""
        self._connection.close()

    def set_investment_portfolio_policy(self, policy: InvestmentPortfolioPolicy) -> None:
        self._connection.execute("INSERT OR REPLACE INTO investment_portfolio_policies VALUES (?, ?, ?, ?, ?, ?)", (policy.portfolio_id, policy.base_currency, str(policy.maximum_investment_value), str(policy.maximum_single_weight), policy.approved_by, policy.approved_at.isoformat())); self._connection.commit()

    def append_company_research(self, record: CompanyResearchRecord) -> None:
        thesis = self._connection.execute("SELECT instrument_id FROM investment_theses WHERE thesis_id = ?", (str(record.thesis_id),)).fetchone()
        if thesis is None or thesis[0] != record.instrument_id:
            raise InvestmentValidationError("company_research_thesis_unavailable")
        self._connection.execute("INSERT INTO investment_company_research VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(record.record_id), str(record.thesis_id), record.instrument_id, record.as_of.isoformat(), record.bull_case, record.base_case, record.bear_case, json.dumps(record.catalysts), json.dumps(record.invalidation_conditions), record.position_sizing_rationale, json.dumps(record.replacement_candidates), json.dumps(record.evidence_ids))); self._connection.commit()

    def company_research_for_thesis(self, thesis_id: UUID, as_of: datetime) -> tuple[tuple[object, ...], ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise InvestmentValidationError("investment_as_of_must_be_timezone_aware")
        return tuple(self._connection.execute("SELECT * FROM investment_company_research WHERE thesis_id = ? AND as_of <= ? ORDER BY as_of, record_id", (str(thesis_id), as_of.isoformat())).fetchall())

    def append_theme_discovery(self, discovery: InvestmentThemeDiscovery) -> None:
        thesis = self.get_thesis(discovery.thesis_id)
        if thesis.instrument_id != discovery.instrument_id:
            raise InvestmentValidationError("theme_discovery_instrument_mismatch")
        research = self._connection.execute("SELECT 1 FROM investment_company_research WHERE record_id = ? AND thesis_id = ?", (str(discovery.company_research_id), str(discovery.thesis_id))).fetchone()
        if research is None: raise InvestmentValidationError("theme_discovery_research_unavailable")
        self._connection.execute("INSERT OR IGNORE INTO investment_theme_discoveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(discovery.discovery_id), str(discovery.thesis_id), discovery.instrument_id, discovery.theme, str(discovery.score), str(discovery.company_research_id), json.dumps(discovery.matched_phrases), json.dumps(discovery.evidence_ids), discovery.rule_version, discovery.discovered_at.isoformat())); self._connection.commit()

    def theme_discoveries_for_thesis(self, thesis_id: UUID, as_of: datetime) -> tuple[InvestmentThemeDiscovery, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None: raise InvestmentValidationError("investment_as_of_must_be_timezone_aware")
        rows = self._connection.execute("SELECT * FROM investment_theme_discoveries WHERE thesis_id = ? AND discovered_at <= ? ORDER BY discovered_at, discovery_id", (str(thesis_id), as_of.isoformat())).fetchall()
        return tuple(InvestmentThemeDiscovery(UUID(row[0]), UUID(row[1]), row[2], row[3], Decimal(row[4]), UUID(row[5]), tuple(json.loads(row[6])), tuple(json.loads(row[7])), row[8], datetime.fromisoformat(row[9])) for row in rows)

    def append_investment_holding(self, holding: InvestmentHolding) -> None:
        policy = self._connection.execute("SELECT 1 FROM investment_portfolio_policies WHERE portfolio_id = ?", (holding.portfolio_id,)).fetchone()
        if policy is None: raise InvestmentValidationError('investment_portfolio_policy_unavailable')
        self._connection.execute("INSERT INTO investment_holdings VALUES (?, ?, ?, ?, ?)", (holding.portfolio_id, holding.instrument_id, str(holding.market_value), holding.observed_at.isoformat(), holding.source_reference)); self._connection.commit()

    def append_theme_exposure(self, exposure: InvestmentThemeExposure) -> None:
        self._connection.execute("INSERT INTO investment_theme_exposures VALUES (?, ?, ?, ?, ?)", (exposure.instrument_id, exposure.theme, str(exposure.exposure), exposure.observed_at.isoformat(), exposure.source_reference)); self._connection.commit()

    def append_macro_sensitivity(self, sensitivity: InvestmentMacroSensitivity) -> None:
        self._connection.execute("INSERT INTO investment_macro_sensitivities VALUES (?, ?, ?, ?, ?)", (sensitivity.instrument_id, sensitivity.macro_series_id, str(sensitivity.sensitivity), sensitivity.observed_at.isoformat(), sensitivity.source_reference)); self._connection.commit()

    def append_rebalance_decision(self, decision: InvestmentRebalanceDecision) -> None:
        policy = self._connection.execute("SELECT maximum_single_weight FROM investment_portfolio_policies WHERE portfolio_id = ?", (decision.portfolio_id,)).fetchone()
        if policy is None: raise InvestmentValidationError('investment_portfolio_policy_unavailable')
        if any(weight > Decimal(policy[0]) for weight in decision.target_weights.values()):
            raise InvestmentValidationError('investment_rebalance_single_weight_exceeded')
        self._connection.execute("INSERT INTO investment_rebalance_decisions VALUES (?, ?, ?, ?, ?, ?, ?)", (str(decision.decision_id), decision.portfolio_id, decision.decided_at.isoformat(), decision.rationale, json.dumps(decision.evidence_ids), json.dumps({key: str(value) for key, value in decision.target_weights.items()}), decision.approved_by)); self._connection.commit()

    def append_performance_snapshot(self, snapshot: InvestmentPerformanceSnapshot) -> None:
        if self._connection.execute("SELECT 1 FROM investment_portfolio_policies WHERE portfolio_id = ?", (snapshot.portfolio_id,)).fetchone() is None: raise InvestmentValidationError('investment_portfolio_policy_unavailable')
        self._connection.execute("INSERT INTO investment_performance_snapshots VALUES (?, ?, ?, ?, ?)", (snapshot.portfolio_id, snapshot.observed_at.isoformat(), str(snapshot.net_asset_value), str(snapshot.cumulative_return), snapshot.source_reference)); self._connection.commit()

    def assess_investment_portfolio(self, portfolio_id: str, as_of: datetime) -> InvestmentPortfolioAssessment:
        row = self._connection.execute("SELECT maximum_investment_value, maximum_single_weight FROM investment_portfolio_policies WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
        if row is None: raise InvestmentValidationError('investment_portfolio_policy_unavailable')
        rows = self._connection.execute("""SELECT h.instrument_id, h.market_value FROM investment_holdings h JOIN (
            SELECT instrument_id, MAX(observed_at) AS latest_at FROM investment_holdings
            WHERE portfolio_id = ? AND observed_at <= ? GROUP BY instrument_id
        ) latest ON latest.instrument_id = h.instrument_id AND latest.latest_at = h.observed_at
        WHERE h.portfolio_id = ?""", (portfolio_id, as_of.isoformat(), portfolio_id)).fetchall()
        values = [Decimal(item[1]) for item in rows]; total = sum(values, Decimal('0')); reasons = []
        if total > Decimal(row[0]): reasons.append('investment_budget_exceeded')
        if total and any(value / total > Decimal(row[1]) for value in values): reasons.append('investment_single_weight_exceeded')
        return InvestmentPortfolioAssessment(portfolio_id, as_of, total, not reasons, tuple(reasons))

    def investment_portfolio_evidence(self, portfolio_id: str, as_of: datetime) -> tuple[tuple[object, ...], tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
        policy = self._connection.execute("SELECT * FROM investment_portfolio_policies WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
        if policy is None: raise InvestmentValidationError('investment_portfolio_policy_unavailable')
        themes = self._connection.execute("SELECT * FROM investment_theme_exposures WHERE observed_at <= ? ORDER BY observed_at", (as_of.isoformat(),)).fetchall()
        macro = self._connection.execute("SELECT * FROM investment_macro_sensitivities WHERE observed_at <= ? ORDER BY observed_at", (as_of.isoformat(),)).fetchall()
        return policy, tuple(themes), tuple(macro)

    def investment_portfolio_history(self, portfolio_id: str, as_of: datetime) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
        holdings = self._connection.execute("SELECT instrument_id, market_value, observed_at, source_reference FROM investment_holdings WHERE portfolio_id = ? AND observed_at <= ? ORDER BY observed_at", (portfolio_id, as_of.isoformat())).fetchall()
        decisions = self._connection.execute("SELECT * FROM investment_rebalance_decisions WHERE portfolio_id = ? AND decided_at <= ? ORDER BY decided_at", (portfolio_id, as_of.isoformat())).fetchall()
        performance = self._connection.execute("SELECT * FROM investment_performance_snapshots WHERE portfolio_id = ? AND observed_at <= ? ORDER BY observed_at", (portfolio_id, as_of.isoformat())).fetchall()
        return tuple(holdings), tuple(decisions), tuple(performance)

    def append_thesis(self, thesis: InvestmentThesis) -> None:
        try:
            self._connection.execute("INSERT INTO investment_theses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                str(thesis.thesis_id), thesis.instrument_id, thesis.version, thesis.statement, thesis.horizon_days,
                json.dumps(thesis.catalysts), json.dumps(thesis.risks), json.dumps(thesis.evidence_ids), thesis.status,
                thesis.created_at.isoformat()))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvestmentValidationError("duplicate_investment_thesis") from error

    def get_thesis(self, thesis_id: UUID) -> InvestmentThesis:
        row = self._connection.execute("SELECT * FROM investment_theses WHERE thesis_id = ?", (str(thesis_id),)).fetchone()
        if row is None:
            raise KeyError(str(thesis_id))
        return InvestmentThesis(UUID(row[0]), row[1], row[2], row[3], row[4], tuple(json.loads(row[5])), tuple(json.loads(row[6])), tuple(json.loads(row[7])), ThesisStatus(row[8]), datetime.fromisoformat(row[9]))

    def append_fact(self, fact: SourceBackedFact) -> None:
        try:
            self._connection.execute("INSERT INTO investment_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                str(fact.fact_id), fact.instrument_id, fact.metric, str(fact.value), fact.unit,
                fact.observed_at.isoformat(), fact.available_at.isoformat(), fact.ingested_at.isoformat(),
                fact.source_reference, fact.source_url, fact.revision))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvestmentValidationError("duplicate_investment_fact") from error

    def _append_fact_if_absent(self, fact: SourceBackedFact) -> None:
        self._connection.execute("INSERT OR IGNORE INTO investment_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            str(fact.fact_id), fact.instrument_id, fact.metric, str(fact.value), fact.unit, fact.observed_at.isoformat(),
            fact.available_at.isoformat(), fact.ingested_at.isoformat(), fact.source_reference, fact.source_url, fact.revision))

    def ingest_source_backed_facts(self, facts: tuple[SourceBackedFact, ...]) -> None:
        """Idempotently persist an already-normalized provider batch as immutable evidence."""
        if not facts:
            raise InvestmentValidationError("empty_investment_provider_fact_batch")
        for fact in facts:
            self._append_fact_if_absent(fact)
        self._connection.commit()

    def materialization_command(self, idempotency_key: str) -> tuple[str, str, str, tuple[UUID, ...], str | None] | None:
        row = self._connection.execute("SELECT actor, instrument_id, as_of, fact_ids_json, error FROM investment_fundamental_materialization_commands WHERE idempotency_key = ? AND status = 'SUCCEEDED'", (idempotency_key,)).fetchone()
        return None if row is None else (row[0], row[1], row[2], tuple(UUID(value) for value in json.loads(row[3])), row[4])

    def materialization_commands(self, instrument_id: str) -> tuple[tuple[str, str, str, str, tuple[UUID, ...], str | None, datetime], ...]:
        rows = self._connection.execute("SELECT idempotency_key, actor, as_of, status, fact_ids_json, error, requested_at FROM investment_fundamental_materialization_commands WHERE instrument_id = ? ORDER BY requested_at, idempotency_key", (instrument_id,)).fetchall()
        return tuple((row[0], row[1], row[2], row[3], () if row[4] is None else tuple(UUID(value) for value in json.loads(row[4])), row[5], datetime.fromisoformat(row[6])) for row in rows)

    def facts_by_ids(self, fact_ids: tuple[UUID, ...]) -> tuple[SourceBackedFact, ...]:
        if not fact_ids:
            return ()
        marks = ",".join("?" for _ in fact_ids)
        rows = self._connection.execute(f"SELECT * FROM investment_facts WHERE fact_id IN ({marks})", tuple(str(value) for value in fact_ids)).fetchall()
        values = {UUID(row[0]): SourceBackedFact(UUID(row[0]), row[1], row[2], Decimal(row[3]), row[4], datetime.fromisoformat(row[5]), datetime.fromisoformat(row[6]), datetime.fromisoformat(row[7]), row[8], row[9], row[10]) for row in rows}
        if set(values) != set(fact_ids):
            raise InvestmentValidationError("materialization_facts_missing")
        return tuple(values[value] for value in fact_ids)

    def facts_as_of(self, instrument_id: str, as_of: datetime) -> tuple[SourceBackedFact, ...]:
        if not instrument_id or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise InvestmentValidationError("invalid_fact_as_of")
        rows = self._connection.execute("""SELECT * FROM investment_facts WHERE instrument_id = ?
            AND available_at <= ? AND ingested_at <= ? ORDER BY metric, observed_at DESC, revision DESC, fact_id""",
            (instrument_id, as_of.isoformat(), as_of.isoformat())).fetchall()
        latest: dict[str, SourceBackedFact] = {}
        for row in rows:
            if row[2] not in latest:
                latest[row[2]] = SourceBackedFact(UUID(row[0]), row[1], row[2], Decimal(row[3]), row[4],
                    datetime.fromisoformat(row[5]), datetime.fromisoformat(row[6]), datetime.fromisoformat(row[7]),
                    row[8], row[9], row[10])
        return tuple(latest[key] for key in sorted(latest))

    def fact_history_as_of(self, instrument_id: str, as_of: datetime) -> tuple[SourceBackedFact, ...]:
        if not instrument_id or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise InvestmentValidationError("invalid_fact_as_of")
        rows = self._connection.execute("SELECT * FROM investment_facts WHERE instrument_id = ? AND available_at <= ? AND ingested_at <= ? ORDER BY metric, observed_at DESC, revision DESC, fact_id", (instrument_id, as_of.isoformat(), as_of.isoformat())).fetchall()
        values: list[SourceBackedFact] = []; seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row[2], row[5])
            if key not in seen:
                seen.add(key); values.append(SourceBackedFact(UUID(row[0]), row[1], row[2], Decimal(row[3]), row[4], datetime.fromisoformat(row[5]), datetime.fromisoformat(row[6]), datetime.fromisoformat(row[7]), row[8], row[9], row[10]))
        return tuple(values)

    def append_fundamental_analytics(self, analysis: FundamentalAnalytics) -> None:
        thesis = self.get_thesis(analysis.thesis_id)
        visible = {fact.fact_id for fact in self.fact_history_as_of(thesis.instrument_id, analysis.as_of)}
        if not set(analysis.source_fact_ids).issubset(visible):
            raise InvestmentValidationError("analytics_uses_unavailable_fact")
        try:
            self._connection.execute("INSERT INTO investment_fundamental_analytics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(analysis.analysis_id), str(analysis.thesis_id), analysis.as_of.isoformat(), analysis.unit, str(analysis.revenue_growth), str(analysis.net_margin), str(analysis.free_cash_flow_margin), str(analysis.debt_to_equity), str(analysis.net_debt), json.dumps([str(value) for value in analysis.source_fact_ids])))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvestmentValidationError("duplicate_fundamental_analytics") from error

    def fundamental_analytics_for_thesis(self, thesis_id: UUID, as_of: datetime) -> tuple[FundamentalAnalytics, ...]:
        rows = self._connection.execute("SELECT * FROM investment_fundamental_analytics WHERE thesis_id = ? AND as_of <= ? ORDER BY as_of, analysis_id", (str(thesis_id), as_of.isoformat())).fetchall()
        return tuple(FundamentalAnalytics(UUID(row[0]), UUID(row[1]), datetime.fromisoformat(row[2]), row[3], Decimal(row[4]), Decimal(row[5]), Decimal(row[6]), Decimal(row[7]), Decimal(row[8]), tuple(UUID(value) for value in json.loads(row[9]))) for row in rows)

    def assess_thesis_drift(self, thesis_id: UUID, expectations: tuple[ThesisExpectation, ...], as_of: datetime) -> ThesisDriftAssessment:
        thesis = self.get_thesis(thesis_id)
        if not expectations or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise InvestmentValidationError("invalid_thesis_drift_request")
        facts = {fact.metric: fact for fact in self.facts_as_of(thesis.instrument_id, as_of)}
        checked, breached = [], []
        for expectation in expectations:
            fact = facts.get(expectation.metric)
            if fact is None:
                breached.append(f"missing:{expectation.metric}")
                continue
            checked.append(fact.fact_id)
            if ((expectation.minimum is not None and fact.value < expectation.minimum)
                    or (expectation.maximum is not None and fact.value > expectation.maximum)):
                breached.append(expectation.metric)
        return ThesisDriftAssessment(thesis_id, as_of, tuple(sorted(checked, key=str)), tuple(sorted(breached)))

    def append_valuation(self, valuation: DcfValuation) -> None:
        thesis = self.get_thesis(valuation.thesis_id)
        if thesis.instrument_id != valuation.instrument_id:
            raise InvestmentValidationError("valuation_instrument_mismatch")
        visible_ids = {fact.fact_id for fact in self.facts_as_of(valuation.instrument_id, valuation.as_of)}
        if not set(valuation.source_fact_ids).issubset(visible_ids):
            raise InvestmentValidationError("valuation_uses_unavailable_fact")
        try:
            self._connection.execute("INSERT INTO investment_valuations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                str(valuation.valuation_id), str(valuation.thesis_id), valuation.instrument_id, valuation.as_of.isoformat(),
                valuation.currency, str(valuation.current_free_cash_flow), str(valuation.growth_rate),
                str(valuation.discount_rate), str(valuation.terminal_growth_rate), valuation.forecast_years,
                str(valuation.shares_outstanding), json.dumps([str(value) for value in valuation.source_fact_ids]),
                valuation.model_version))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvestmentValidationError("duplicate_investment_valuation") from error

    def append_recommendation(self, recommendation: InvestmentRecommendation) -> None:
        self.get_thesis(recommendation.thesis_id)
        try:
            self._connection.execute("INSERT INTO investment_recommendations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                str(recommendation.recommendation_id), str(recommendation.thesis_id), str(recommendation.target_weight),
                str(recommendation.maximum_weight), str(recommendation.confidence), str(recommendation.expected_return_low),
                str(recommendation.expected_return_high), json.dumps(recommendation.rationale_evidence_ids), recommendation.as_of.isoformat()))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvestmentValidationError("duplicate_investment_recommendation") from error

    def recommendations_for_thesis(self, thesis_id: UUID) -> tuple[InvestmentRecommendation, ...]:
        rows = self._connection.execute("""SELECT * FROM investment_recommendations WHERE thesis_id = ?
            ORDER BY as_of, recommendation_id""", (str(thesis_id),)).fetchall()
        return tuple(InvestmentRecommendation(UUID(row[0]), UUID(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4]),
            Decimal(row[5]), Decimal(row[6]), tuple(json.loads(row[7])), datetime.fromisoformat(row[8])) for row in rows)

    def valuations_for_thesis(self, thesis_id: UUID) -> tuple[DcfValuation, ...]:
        rows = self._connection.execute("SELECT * FROM investment_valuations WHERE thesis_id = ? ORDER BY as_of, valuation_id", (str(thesis_id),)).fetchall()
        return tuple(DcfValuation(UUID(row[0]), UUID(row[1]), row[2], datetime.fromisoformat(row[3]), row[4], Decimal(row[5]),
            Decimal(row[6]), Decimal(row[7]), Decimal(row[8]), row[9], Decimal(row[10]),
            tuple(UUID(value) for value in json.loads(row[11])), row[12]) for row in rows)

    def append_review(self, review: ThesisReview) -> None:
        self.get_thesis(review.thesis_id)
        try:
            self._connection.execute("INSERT INTO investment_thesis_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                str(review.review_id), str(review.thesis_id), review.outcome, review.reviewed_at.isoformat(), review.rationale,
                json.dumps(review.evidence_ids), json.dumps([str(value) for value in review.drift_assessment.checked_fact_ids]),
                json.dumps(review.drift_assessment.breached_metrics), review.drift_assessment.as_of.isoformat(), review.next_review_at.isoformat()))
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvestmentValidationError("duplicate_thesis_review") from error

    def reviews_for_thesis(self, thesis_id: UUID) -> tuple[ThesisReview, ...]:
        rows = self._connection.execute("SELECT * FROM investment_thesis_reviews WHERE thesis_id = ? ORDER BY reviewed_at, review_id", (str(thesis_id),)).fetchall()
        return tuple(ThesisReview(UUID(row[0]), UUID(row[1]), ThesisReviewOutcome(row[2]), datetime.fromisoformat(row[3]), row[4],
            tuple(json.loads(row[5])), ThesisDriftAssessment(UUID(row[1]), datetime.fromisoformat(row[8]),
            tuple(UUID(value) for value in json.loads(row[6])), tuple(json.loads(row[7]))), datetime.fromisoformat(row[9])) for row in rows)

    def set_review_schedule(self, schedule: InvestmentReviewSchedule) -> None:
        self.get_thesis(schedule.thesis_id)
        expectations = [{"metric": item.metric, "minimum": None if item.minimum is None else str(item.minimum), "maximum": None if item.maximum is None else str(item.maximum)} for item in schedule.expectations]
        self._connection.execute("INSERT OR REPLACE INTO investment_review_schedules VALUES (?, ?, ?, ?, ?)", (str(schedule.thesis_id), schedule.cadence_days, json.dumps(expectations), schedule.next_review_at.isoformat(), schedule.approved_by)); self._connection.commit()

    def due_review_schedules(self, as_of: datetime) -> tuple[InvestmentReviewSchedule, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None: raise InvestmentValidationError("invalid_investment_review_as_of")
        rows = self._connection.execute("SELECT * FROM investment_review_schedules WHERE next_review_at <= ? ORDER BY next_review_at, thesis_id", (as_of.isoformat(),)).fetchall()
        return tuple(InvestmentReviewSchedule(UUID(row[0]), int(row[1]), tuple(ThesisExpectation(item["metric"], None if item["minimum"] is None else Decimal(item["minimum"]), None if item["maximum"] is None else Decimal(item["maximum"])) for item in json.loads(row[2])), datetime.fromisoformat(row[3]), row[4]) for row in rows)

    def review_schedule_for_thesis(self, thesis_id: UUID) -> InvestmentReviewSchedule | None:
        row = self._connection.execute("SELECT * FROM investment_review_schedules WHERE thesis_id = ?", (str(thesis_id),)).fetchone()
        if row is None: return None
        return InvestmentReviewSchedule(UUID(row[0]), int(row[1]), tuple(ThesisExpectation(item["metric"], None if item["minimum"] is None else Decimal(item["minimum"]), None if item["maximum"] is None else Decimal(item["maximum"])) for item in json.loads(row[2])), datetime.fromisoformat(row[3]), row[4])

    def scheduled_drift_history_for_thesis(self, thesis_id: UUID, as_of: datetime) -> tuple[tuple[object, ...], ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None: raise InvestmentValidationError("invalid_investment_review_as_of")
        return tuple(self._connection.execute("SELECT * FROM investment_scheduled_drift_assessments WHERE thesis_id = ? AND assessed_at <= ? ORDER BY assessed_at, assessment_id", (str(thesis_id), as_of.isoformat())).fetchall())

    def run_due_thesis_drift(self, as_of: datetime) -> tuple[ThesisDriftAssessment, ...]:
        results = []
        for schedule in self.due_review_schedules(as_of):
            assessment = self.assess_thesis_drift(schedule.thesis_id, schedule.expectations, as_of)
            self._connection.execute("INSERT INTO investment_scheduled_drift_assessments VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), str(schedule.thesis_id), as_of.isoformat(), json.dumps([str(item) for item in assessment.checked_fact_ids]), json.dumps(assessment.breached_metrics), schedule.next_review_at.isoformat()))
            self._connection.execute("UPDATE investment_review_schedules SET next_review_at = ? WHERE thesis_id = ?", ((as_of + timedelta(days=schedule.cadence_days)).isoformat(), str(schedule.thesis_id)))
            results.append(assessment)
        self._connection.commit(); return tuple(results)


def run_due_thesis_drift_with_alerts(store: SQLiteInvestmentStore, alerts: SQLiteOperationalAlertStore, as_of: datetime) -> tuple[ThesisDriftAssessment, ...]:
    """Run due investment review checks and create deduplicated local operator alerts for drift only."""
    assessments = store.run_due_thesis_drift(as_of)
    for assessment in assessments:
        if assessment.drift_detected:
            alerts.raise_alert(source="investment_review", code="THESIS_DRIFT_DETECTED", severity=AlertSeverity.WARNING,
                resource=f"thesis:{assessment.thesis_id}", details={"assessed_at": assessment.as_of.isoformat(), "breached_metrics": ",".join(assessment.breached_metrics)})
    return assessments


def draft_thesis(instrument_id: str, statement: str, horizon_days: int, risks: tuple[str, ...], evidence_ids: tuple[str, ...]) -> InvestmentThesis:
    return InvestmentThesis(uuid4(), instrument_id, "v1", statement, horizon_days, (), risks, evidence_ids, ThesisStatus.DRAFT, utc_now())


def discover_themes_from_company_research(store: SQLiteInvestmentStore, thesis_id: UUID, rules: tuple[ThemeDiscoveryRule, ...], as_of: datetime) -> tuple[InvestmentThemeDiscovery, ...]:
    """Deterministically discover configured themes from cited company research text.

    The result is evidence-bound research metadata, not a trade signal, exposure
    estimate, or automatic portfolio allocation.
    """
    if not rules or as_of.tzinfo is None or as_of.utcoffset() is None: raise InvestmentValidationError("invalid_theme_discovery_request")
    discoveries: list[InvestmentThemeDiscovery] = []
    for row in store.company_research_for_thesis(thesis_id, as_of):
        corpus = " ".join((row[4], row[5], row[6], *json.loads(row[7]))).casefold()
        for rule in rules:
            matched = tuple(sorted({phrase for phrase in rule.phrases if phrase.casefold() in corpus}, key=str.casefold))
            if not matched: continue
            identity = f"{row[0]}|{rule.theme}|{rule.rule_version}|{'|'.join(matched)}"
            discovery = InvestmentThemeDiscovery(uuid5(NAMESPACE_URL, identity), thesis_id, row[2], rule.theme, Decimal(len(matched)) / Decimal(len(set(rule.phrases))), UUID(row[0]), matched, tuple(json.loads(row[11])), rule.rule_version, as_of)
            store.append_theme_discovery(discovery); discoveries.append(discovery)
    return tuple(discoveries)


def materialize_fundamental_facts(
    investment_store: SQLiteInvestmentStore,
    fundamental_store: SQLiteFundamentalStore,
    instrument_id: str,
    as_of: datetime,
    *,
    fact_names: tuple[str, ...],
) -> tuple[SourceBackedFact, ...]:
    """Copy only point-in-time-visible fundamental evidence into the investment ledger.

    The deterministic ID binds every investment fact to provider/source/revision;
    callers cannot invent a source reference for valuation provenance.
    """
    if not instrument_id or not fact_names or any(not value.strip() for value in fact_names):
        raise InvestmentValidationError("invalid_fundamental_materialization_request")
    selected: list[SourceBackedFact] = []
    for fact_name in sorted(set(fact_names)):
        for fundamental in fundamental_store.available_as_of(instrument_id, as_of, fact_name=fact_name):
            source_reference = f"fundamental:{fundamental.provider}:{fundamental.source_identifier}:{fundamental.revision}"
            stable_identity = f"{fundamental.instrument_id}|{source_reference}|{fundamental.reporting_period_end.isoformat()}"
            selected.append(SourceBackedFact(
                uuid5(NAMESPACE_URL, stable_identity), fundamental.instrument_id, fundamental.fact_name,
                fundamental.as_reported_value, fundamental.unit, fundamental.reporting_period_end,
                fundamental.effective_at, fundamental.ingested_at, source_reference,
                f"fundamental://{fundamental.provider}/{fundamental.source_identifier}", fundamental.revision,
            ))
    for fact in selected:
        investment_store.append_fact(fact)
    return tuple(selected)


def materialize_fundamental_facts_authorized(
    investment_store: SQLiteInvestmentStore, fundamental_store: SQLiteFundamentalStore, instrument_id: str, as_of: datetime,
    *, fact_names: tuple[str, ...], actor: str, idempotency_key: str,
) -> tuple[SourceBackedFact, ...]:
    if (not instrument_id or not fact_names or any(not value.strip() for value in fact_names)
            or as_of.tzinfo is None or as_of.utcoffset() is None):
        raise InvestmentValidationError("invalid_fundamental_materialization_request")
    if not actor.strip() or not idempotency_key.strip():
        raise InvestmentValidationError("materialization_actor_and_idempotency_required")
    normalized_names = tuple(sorted(set(fact_names)))
    existing = investment_store._connection.execute("SELECT actor, instrument_id, as_of, fact_names_json, status, fact_ids_json FROM investment_fundamental_materialization_commands WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    payload = (actor, instrument_id, as_of.isoformat(), json.dumps(normalized_names))
    if existing is not None:
        if tuple(existing[:4]) != payload:
            raise InvestmentValidationError("materialization_idempotency_conflict")
        if existing[4] != "SUCCEEDED":
            raise InvestmentValidationError("materialization_command_not_successful")
        return investment_store.facts_by_ids(tuple(UUID(value) for value in json.loads(existing[5])))
    try:
        selected: list[SourceBackedFact] = []
        for fact_name in normalized_names:
            for fundamental in fundamental_store.available_as_of(instrument_id, as_of, fact_name=fact_name):
                reference = f"fundamental:{fundamental.provider}:{fundamental.source_identifier}:{fundamental.revision}"
                identity = f"{fundamental.instrument_id}|{reference}|{fundamental.reporting_period_end.isoformat()}"
                selected.append(SourceBackedFact(uuid5(NAMESPACE_URL, identity), fundamental.instrument_id, fundamental.fact_name,
                    fundamental.as_reported_value, fundamental.unit, fundamental.reporting_period_end, fundamental.effective_at,
                    fundamental.ingested_at, reference, f"fundamental://{fundamental.provider}/{fundamental.source_identifier}", fundamental.revision))
        for fact in selected:
            investment_store._append_fact_if_absent(fact)
        investment_store._connection.execute("INSERT INTO investment_fundamental_materialization_commands VALUES (?, ?, ?, ?, ?, 'SUCCEEDED', ?, NULL, ?)",
            (idempotency_key, actor, instrument_id, as_of.isoformat(), json.dumps(normalized_names), json.dumps([str(value.fact_id) for value in selected]), utc_now().isoformat()))
        investment_store._connection.commit()
    except Exception as error:
        investment_store._connection.rollback()
        investment_store._connection.execute("INSERT OR IGNORE INTO investment_fundamental_materialization_commands VALUES (?, ?, ?, ?, ?, 'FAILED', NULL, ?, ?)",
            (idempotency_key, actor, instrument_id, as_of.isoformat(), json.dumps(normalized_names), str(error)[:500], utc_now().isoformat()))
        investment_store._connection.commit()
        raise
    return tuple(selected)


def calculate_fundamental_analytics(store: SQLiteInvestmentStore, thesis_id: UUID, as_of: datetime) -> FundamentalAnalytics:
    thesis = store.get_thesis(thesis_id)
    history = store.fact_history_as_of(thesis.instrument_id, as_of)
    by_metric: dict[str, list[SourceBackedFact]] = {}
    for fact in history:
        by_metric.setdefault(fact.metric, []).append(fact)
    required = ("revenue", "net_income", "free_cash_flow", "total_debt", "cash_and_equivalents", "total_equity")
    if any(name not in by_metric for name in required) or len(by_metric.get("revenue", ())) < 2:
        raise InvestmentValidationError("fundamental_analytics_inputs_unavailable")
    current_revenue, prior_revenue = by_metric["revenue"][:2]
    current = {name: by_metric[name][0] for name in required if name != "revenue"}
    facts = (current_revenue, prior_revenue, current["net_income"], current["free_cash_flow"], current["total_debt"], current["cash_and_equivalents"], current["total_equity"])
    if len({fact.unit for fact in facts}) != 1 or prior_revenue.value <= 0 or current_revenue.value <= 0 or current["total_equity"].value <= 0:
        raise InvestmentValidationError("fundamental_analytics_units_or_denominators_invalid")
    return FundamentalAnalytics(uuid4(), thesis_id, as_of, current_revenue.unit,
        current_revenue.value / prior_revenue.value - Decimal("1"), current["net_income"].value / current_revenue.value,
        current["free_cash_flow"].value / current_revenue.value, current["total_debt"].value / current["total_equity"].value,
        current["total_debt"].value - current["cash_and_equivalents"].value, tuple(fact.fact_id for fact in facts))
