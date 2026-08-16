import hashlib
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from trade_platform.fundamentals import StatementType
from trade_platform.investment_engine_v2 import (
    FIXTURE_LIMITATIONS,
    QUALITY_REQUIRED_METRICS,
    InvalidationRuleV2,
    InvestmentEngineV2Error,
    InvestmentEvidenceState,
    InvestmentPortfolioPolicyV2,
    InvestmentResearchOrchestratorV2,
    InvestmentReviewStatus,
    InvestmentThesisVersionV2,
    RuleOperator,
    ValuationAssumptionsV2,
    build_rebalance_candidate,
)
from trade_platform.pit_fundamentals import AvailableFilingFact, FilingFact, FundamentalFiling
from trade_platform.pit_macro import MacroCategory, PitMacroObservation

NOW = datetime(2025, 5, 1, tzinfo=UTC)
HEALTH_ID = uuid5(NAMESPACE_URL, "cycle203:data-health")


class FixtureFundamentals:
    def __init__(self, facts: tuple[AvailableFilingFact, ...]) -> None:
        self.facts = facts

    def available_as_of(
        self, instrument_id: str, as_of: datetime, *, standardized_metric: str | None = None,
    ) -> tuple[AvailableFilingFact, ...]:
        del instrument_id, as_of
        if standardized_metric is None:
            return self.facts
        return tuple(item for item in self.facts if item.fact.standardized_metric == standardized_metric)


class FixtureMacro:
    def __init__(self, observations: dict[str, tuple[PitMacroObservation, ...]]) -> None:
        self.observations = observations

    def available_as_of(self, series_code: str, as_of: datetime) -> tuple[PitMacroObservation, ...]:
        del as_of
        return self.observations.get(series_code, ())


def fixture_facts() -> tuple[AvailableFilingFact, ...]:
    filing_id = uuid5(NAMESPACE_URL, "cycle203:filing")
    filing = FundamentalFiling(
        uuid5(NAMESPACE_URL, "cycle203:source"),
        "US:XNAS:CYCLE203",
        "cycle203-10k",
        "10-K",
        NOW - timedelta(days=31),
        NOW - timedelta(days=30),
        date(2024, 1, 1),
        date(2024, 12, 31),
        2024,
        "FY",
        0,
        NOW - timedelta(days=29),
        "fixture://cycle203/filing",
        hashlib.sha256(b"cycle203-filing").hexdigest(),
        filing_id,
    )
    values = {
        "revenue": Decimal("1000"),
        "operating_income": Decimal("200"),
        "operating_cash_flow": Decimal("180"),
        "capital_expenditures": Decimal("50"),
        "total_debt": Decimal("300"),
        "shares_outstanding": Decimal("100"),
        "tax_rate": Decimal("0.25"),
        "invested_capital": Decimal("750"),
        "dividends_paid": Decimal("20"),
        "share_repurchases": Decimal("30"),
    }
    return tuple(
        AvailableFilingFact(
            filing,
            FilingFact(
                filing_id,
                "fixture",
                metric,
                StatementType.OTHER,
                value,
                "USD",
                "USD",
                metric,
                value,
                "fixture-map-v1",
                fact_id=uuid5(NAMESPACE_URL, f"cycle203:fact:{metric}"),
            ),
        )
        for metric, value in values.items()
    )


def fixture_macro(*, actual: Decimal = Decimal("3"), release_at: datetime = NOW - timedelta(days=2)) -> PitMacroObservation:
    return PitMacroObservation(
        uuid5(NAMESPACE_URL, "cycle203:macro-source"),
        MacroCategory.POLICY_RATE,
        "POLICY_RATE",
        date(2025, 4, 1),
        release_at,
        release_at,
        actual,
        Decimal("3"),
        Decimal("3"),
        0,
        release_at + timedelta(minutes=1),
        "fixture://cycle203/macro",
        hashlib.sha256(b"cycle203-macro").hexdigest(),
        uuid5(NAMESPACE_URL, f"cycle203:macro:{actual}:{release_at}"),
    )


def fixture_thesis(*, thesis_id: UUID | None = None) -> InvestmentThesisVersionV2:
    return InvestmentThesisVersionV2(
        thesis_id or uuid5(NAMESPACE_URL, "cycle203:thesis:v1"),
        uuid5(NAMESPACE_URL, "cycle203:base-instrument"),
        "US:XNAS:CYCLE203",
        "1.0.0",
        "Durable cash generation can support long-horizon compounding.",
        1095,
        ("operating leverage",),
        ("margin erosion", "capital misallocation"),
        (
            InvalidationRuleV2("fundamental:operating_income", RuleOperator.BELOW, Decimal("100")),
            InvalidationRuleV2("macro:POLICY_RATE", RuleOperator.ABOVE, Decimal("5")),
        ),
        tuple(sorted(QUALITY_REQUIRED_METRICS)),
        ("POLICY_RATE",),
        ValuationAssumptionsV2(Decimal("0.04"), Decimal("0.10"), Decimal("0.02"), 5),
        NOW,
        NOW - timedelta(days=29),
        "investment-engine-v2.0.0",
    )


def fixture_analysis():
    return InvestmentResearchOrchestratorV2(
        FixtureFundamentals(fixture_facts()), FixtureMacro({"POLICY_RATE": (fixture_macro(),)})
    ).analyze(
        fixture_thesis(), as_of=NOW, data_health_status="HEALTHY",
        data_health_assessment_ids=(HEALTH_ID,),
    )


class InvestmentEngineV2Tests(unittest.TestCase):
    def test_complete_analysis_is_deterministic_bound_and_review_only(self) -> None:
        first = fixture_analysis()
        second = fixture_analysis()
        self.assertEqual(first, second)
        self.assertEqual(first.status, InvestmentReviewStatus.REVIEW_REQUIRED)
        self.assertEqual(first.quality.state, InvestmentEvidenceState.MEASURED)
        self.assertIsNotNone(first.valuation.intrinsic_value_per_share)
        self.assertEqual(first.drift.breached_rules, ())
        self.assertEqual(first.limitations, FIXTURE_LIMITATIONS)
        self.assertIn("NOT_INVESTMENT_RECOMMENDATION", first.limitations)

    def test_missing_fundamental_and_macro_inputs_fail_closed_without_zero_fill(self) -> None:
        facts = tuple(item for item in fixture_facts() if item.fact.standardized_metric != "revenue")
        analysis = InvestmentResearchOrchestratorV2(
            FixtureFundamentals(facts), FixtureMacro({})
        ).analyze(
            fixture_thesis(), as_of=NOW, data_health_status="HEALTHY",
            data_health_assessment_ids=(HEALTH_ID,),
        )
        self.assertEqual(analysis.status, InvestmentReviewStatus.BLOCKED)
        self.assertIsNone(analysis.quality.score)
        self.assertEqual(analysis.quality.metrics[0].state, InvestmentEvidenceState.UNAVAILABLE)
        self.assertIsNone(analysis.quality.metrics[0].value)
        self.assertIn("macro:POLICY_RATE", analysis.drift.missing_inputs)

    def test_future_authority_output_and_breached_invalidation_are_not_hidden(self) -> None:
        future = tuple(
            replace(item, filing=replace(item.filing, accepted_at=NOW + timedelta(days=1), ingested_at=NOW + timedelta(days=1)))
            for item in fixture_facts()
        )
        with self.assertRaisesRegex(InvestmentEngineV2Error, "future_fundamental"):
            InvestmentResearchOrchestratorV2(
                FixtureFundamentals(future), FixtureMacro({"POLICY_RATE": (fixture_macro(),)})
            ).analyze(
                fixture_thesis(), as_of=NOW, data_health_status="HEALTHY",
                data_health_assessment_ids=(HEALTH_ID,),
            )
        breached = InvestmentResearchOrchestratorV2(
            FixtureFundamentals(fixture_facts()),
            FixtureMacro({"POLICY_RATE": (fixture_macro(actual=Decimal("7")),)}),
        ).analyze(
            fixture_thesis(), as_of=NOW, data_health_status="HEALTHY",
            data_health_assessment_ids=(HEALTH_ID,),
        )
        self.assertEqual(breached.status, InvestmentReviewStatus.BLOCKED)
        self.assertIn("thesis_invalidation_breached", breached.reasons)

        unhealthy = InvestmentResearchOrchestratorV2(
            FixtureFundamentals(fixture_facts()),
            FixtureMacro({"POLICY_RATE": (fixture_macro(),)}),
        ).analyze(
            fixture_thesis(), as_of=NOW, data_health_status="BLOCKED",
            data_health_assessment_ids=(HEALTH_ID,),
        )
        self.assertEqual(unhealthy.status, InvestmentReviewStatus.BLOCKED)
        self.assertIn("data_health_blocked", unhealthy.reasons)

    def test_rebalance_candidate_is_policy_bounded_and_has_no_execution_authority(self) -> None:
        analysis = fixture_analysis()
        policy = InvestmentPortfolioPolicyV2(
            uuid5(NAMESPACE_URL, "cycle203:policy"), "1.0.0", "investment-cycle203", "USD",
            Decimal("100000"), Decimal("0.40"), Decimal("0.10"), Decimal("0.30"),
            "investment-committee", NOW,
        )
        candidate = build_rebalance_candidate(
            policy,
            as_of=NOW,
            holdings_snapshot_hash=hashlib.sha256(b"cycle203-holdings").hexdigest(),
            current_weights={"US:XNAS:CYCLE203": Decimal("0.20")},
            candidate_weights={"US:XNAS:CYCLE203": Decimal("0.30")},
            analyses=(analysis,),
        )
        self.assertEqual(candidate.status, InvestmentReviewStatus.REVIEW_REQUIRED)
        self.assertFalse(candidate.execution_authority)
        blocked = build_rebalance_candidate(
            policy,
            as_of=NOW,
            holdings_snapshot_hash=hashlib.sha256(b"cycle203-holdings").hexdigest(),
            current_weights={},
            candidate_weights={"US:XNAS:CYCLE203": Decimal("0.50")},
            analyses=(analysis,),
        )
        self.assertEqual(blocked.status, InvestmentReviewStatus.BLOCKED)
        self.assertIn("maximum_single_weight_breached", blocked.reasons)


if __name__ == "__main__":
    unittest.main()
