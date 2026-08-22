from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_platform.governance_reporting import (
    REPORT_CADENCE,
    BudgetMode,
    CostBudgetPolicy,
    CostCandidateType,
    CostValueOutcome,
    GovernanceReportingError,
    GovernanceReportOutcome,
    GovernanceReportType,
    OperationalCostCategory,
    OperationalCostObservation,
    ReportCadence,
    ReportEvidenceClass,
    ReportSchedulePolicy,
    assess_cost_value,
    generate_governance_report,
)
from trade_platform.operational_jobs import OperationalJobStatus, build_job_policy, build_job_run

START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 9, 1, tzinfo=UTC)


class GovernanceReportingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.job_policy = build_job_policy(
            job_name="monthly-cost-report",
            version="v1",
            interval=timedelta(days=31),
            grace=timedelta(hours=1),
            owner="operations",
            runbook_uri="runbooks/monthly-cost-report",
            approved_by="operations-reviewer",
            approved_at=START - timedelta(days=2),
        )
        self.schedule = ReportSchedulePolicy.create(
            policy_name="monthly-cost-governance",
            version="v1",
            report_type=GovernanceReportType.MONTHLY_COST,
            job_policy=self.job_policy,
            approved_by="governance-reviewer",
            approved_at=START - timedelta(days=1),
        )
        self.run = build_job_run(
            policy_id=self.job_policy.policy_id,
            idempotency_key="monthly-cost-report:2026-08",
            scheduled_for=END,
            started_at=END + timedelta(seconds=1),
            completed_at=END + timedelta(seconds=2),
            status=OperationalJobStatus.SUCCEEDED,
            summary={"source": "fixture", "authority": "none"},
        )

    def budget(self, *, mode: BudgetMode = BudgetMode.LOCAL_RESEARCH) -> CostBudgetPolicy:
        return CostBudgetPolicy.create(
            policy_name=f"august-cost-budget-{mode.value.lower()}",
            version="v1",
            budget_mode=mode,
            currency="EUR",
            period_start=START,
            period_end=END,
            total_limit=Decimal("100"),
            category_limits={category: Decimal("10") for category in OperationalCostCategory},
            minimum_value_to_cost_ratio=Decimal("1.25"),
            approved_by="budget-reviewer",
            approved_at=START - timedelta(days=1),
        )

    def costs(self, *, breach: bool = False) -> tuple[OperationalCostObservation, ...]:
        observations = []
        for index, category in enumerate(OperationalCostCategory):
            amount = Decimal("20") if breach and index == 0 else Decimal("5")
            observations.append(
                OperationalCostObservation.create(
                    category=category,
                    service_reference=f"fixture/{category.value.lower()}",
                    amount=amount,
                    currency="EUR",
                    period_start=START,
                    period_end=END,
                    observed_at=END - timedelta(days=1, seconds=index),
                    evidence_class=(
                        ReportEvidenceClass.MODEL_ESTIMATE
                        if category is OperationalCostCategory.AI_INFERENCE
                        else ReportEvidenceClass.FACT
                    ),
                    evidence_reference=f"evidence/{category.value.lower()}",
                )
            )
        return tuple(observations)

    @staticmethod
    def sections(*, facts: bool = True, missing: tuple[str, ...] = ()) -> dict[ReportEvidenceClass, tuple[str, ...]]:
        return {
            ReportEvidenceClass.FACT: ("Cost observations are content-addressed fixtures.",) if facts else (),
            ReportEvidenceClass.MODEL_ESTIMATE: ("AI inference cost is an attributed estimate.",),
            ReportEvidenceClass.INFERENCE: (),
            ReportEvidenceClass.UNVERIFIED_INFORMATION: (),
            ReportEvidenceClass.MISSING_DATA: missing,
        }

    def generate(
        self,
        *,
        budget: CostBudgetPolicy | None = None,
        costs: tuple[OperationalCostObservation, ...] | None = None,
        sections: dict[ReportEvidenceClass, tuple[str, ...]] | None = None,
    ):
        return generate_governance_report(
            self.schedule,
            self.job_policy,
            self.run,
            period_start=START,
            period_end=END,
            generated_at=END + timedelta(minutes=1),
            sections=self.sections() if sections is None else sections,
            cost_policy=self.budget() if budget is None else budget,
            cost_observations=self.costs() if costs is None else costs,
        )

    def test_requirement_catalogues_are_complete_and_cadence_bound(self) -> None:
        self.assertEqual(len(GovernanceReportType), 13)
        self.assertEqual(set(REPORT_CADENCE), set(GovernanceReportType))
        self.assertEqual(len(OperationalCostCategory), 12)
        self.assertEqual(len(BudgetMode), 5)
        self.assertEqual(
            {cadence for cadence in REPORT_CADENCE.values()},
            set(ReportCadence),
        )
        with self.assertRaisesRegex(GovernanceReportingError, "invalid_report_schedule_policy"):
            ReportSchedulePolicy.create(
                policy_name="invalid-daily-schedule",
                version="v1",
                report_type=GovernanceReportType.DAILY_RISK,
                job_policy=self.job_policy,
                approved_by="governance-reviewer",
                approved_at=START - timedelta(days=1),
            )

    def test_non_cost_report_uses_same_sections_and_job_boundary(self) -> None:
        daily_job = build_job_policy(
            job_name="daily-risk-report",
            version="v1",
            interval=timedelta(days=1),
            grace=timedelta(minutes=15),
            owner="risk-operations",
            runbook_uri="runbooks/daily-risk-report",
            approved_by="risk-reviewer",
            approved_at=START - timedelta(days=1),
        )
        schedule = ReportSchedulePolicy.create(
            policy_name="daily-risk-governance",
            version="v1",
            report_type=GovernanceReportType.DAILY_RISK,
            job_policy=daily_job,
            approved_by="governance-reviewer",
            approved_at=START - timedelta(hours=12),
        )
        period_end = START + timedelta(days=1)
        run = build_job_run(
            policy_id=daily_job.policy_id,
            idempotency_key="daily-risk-report:2026-08-01",
            scheduled_for=period_end,
            started_at=period_end,
            completed_at=period_end + timedelta(seconds=1),
            status=OperationalJobStatus.SUCCEEDED,
            summary={"source": "fixture"},
        )
        report, report_sections = generate_governance_report(
            schedule,
            daily_job,
            run,
            period_start=START,
            period_end=period_end,
            generated_at=period_end + timedelta(seconds=2),
            sections=self.sections(),
        )
        self.assertEqual(report.outcome, GovernanceReportOutcome.READY_FOR_REVIEW)
        self.assertIsNone(report.total_observed)
        self.assertEqual(len(report_sections), len(ReportEvidenceClass))
        self.assertEqual(report.execution_authority, "NONE")

    def test_complete_cost_report_is_deterministic_and_non_authoritative(self) -> None:
        costs = self.costs()
        budget = self.budget()
        report, sections = self.generate(budget=budget, costs=costs)
        reordered, reordered_sections = self.generate(budget=budget, costs=tuple(reversed(costs)))
        self.assertEqual(report, reordered)
        self.assertEqual(sections, reordered_sections)
        self.assertEqual(report.outcome, GovernanceReportOutcome.READY_FOR_REVIEW)
        self.assertEqual(report.total_observed, Decimal("60"))
        self.assertEqual(report.execution_authority, "NONE")
        self.assertIn("LIVE_TRADING_DISABLED", report.limitations)

    def test_budget_breach_requires_review_even_for_live_named_mode(self) -> None:
        report, _ = self.generate(
            budget=self.budget(mode=BudgetMode.LIMITED_LIVE),
            costs=self.costs(breach=True),
        )
        self.assertEqual(report.outcome, GovernanceReportOutcome.BUDGET_BREACH_REVIEW_REQUIRED)
        self.assertIn("operational_cost_budget_threshold_exceeded", report.reasons)
        self.assertEqual(report.execution_authority, "NONE")

    def test_value_for_cost_requires_threshold_and_prefers_deterministic_code(self) -> None:
        budget = self.budget()
        justified = assess_cost_value(
            budget,
            candidate_type=CostCandidateType.DATASET,
            candidate_reference="dataset/candidate-v1",
            evaluated_at=START + timedelta(days=2),
            incremental_cost=Decimal("10"),
            measurable_value_estimate=Decimal("15"),
            currency="EUR",
            evidence_references=("evidence/value", "evidence/cost"),
            deterministic_alternative_available=False,
            proposed_ai_inference=False,
        )
        reordered = assess_cost_value(
            budget,
            candidate_type=CostCandidateType.DATASET,
            candidate_reference="dataset/candidate-v1",
            evaluated_at=START + timedelta(days=2),
            incremental_cost=Decimal("10"),
            measurable_value_estimate=Decimal("15"),
            currency="EUR",
            evidence_references=("evidence/cost", "evidence/value"),
            deterministic_alternative_available=False,
            proposed_ai_inference=False,
        )
        self.assertEqual(justified, reordered)
        self.assertEqual(justified.outcome, CostValueOutcome.JUSTIFIED_FOR_REVIEW)
        self.assertEqual(justified.procurement_authority, "NONE")
        ai_candidate = assess_cost_value(
            budget,
            candidate_type=CostCandidateType.MODEL,
            candidate_reference="model/ai-inference-v1",
            evaluated_at=START + timedelta(days=2),
            incremental_cost=Decimal("10"),
            measurable_value_estimate=Decimal("100"),
            currency="EUR",
            evidence_references=("evidence/model-value",),
            deterministic_alternative_available=True,
            proposed_ai_inference=True,
        )
        self.assertEqual(ai_candidate.outcome, CostValueOutcome.NOT_JUSTIFIED_REVIEW_REQUIRED)
        self.assertIn("deterministic_code_preferred_over_ai_inference", ai_candidate.reasons)

    def test_missing_cost_category_fails_closed_and_requires_disclosure(self) -> None:
        costs = self.costs()[:-1]
        with self.assertRaisesRegex(GovernanceReportingError, "missing_cost_categories_require_disclosure"):
            self.generate(costs=costs)
        report, _ = self.generate(
            costs=costs,
            sections=self.sections(missing=("Backup cost evidence is missing.",)),
        )
        self.assertEqual(report.outcome, GovernanceReportOutcome.BLOCKED_INCOMPLETE_EVIDENCE)
        self.assertIn("incomplete_operational_cost_category_coverage", report.reasons)

    def test_tampered_or_failed_job_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "operational_job_run_hash_mismatch"):
            generate_governance_report(
                self.schedule,
                self.job_policy,
                replace(self.run, completed_at=self.run.completed_at + timedelta(seconds=1)),
                period_start=START,
                period_end=END,
                generated_at=END + timedelta(minutes=1),
                sections=self.sections(),
                cost_policy=self.budget(),
                cost_observations=self.costs(),
            )
        failed = build_job_run(
            policy_id=self.job_policy.policy_id,
            idempotency_key="monthly-cost-report:failed",
            scheduled_for=END,
            started_at=END,
            completed_at=END,
            status=OperationalJobStatus.FAILED,
            summary={"reason": "fixture_failure"},
        )
        with self.assertRaisesRegex(GovernanceReportingError, "invalid_governance_report_job_binding"):
            generate_governance_report(
                self.schedule,
                self.job_policy,
                failed,
                period_start=START,
                period_end=END,
                generated_at=END + timedelta(minutes=1),
                sections=self.sections(),
                cost_policy=self.budget(),
                cost_observations=self.costs(),
            )

    def test_factless_report_can_only_be_blocked_with_missing_data(self) -> None:
        report, _ = self.generate(
            sections=self.sections(facts=False, missing=("No independently observed invoices were supplied.",)),
        )
        self.assertEqual(report.outcome, GovernanceReportOutcome.BLOCKED_INCOMPLETE_EVIDENCE)
        self.assertIn("no_fact_evidence_supplied", report.reasons)


if __name__ == "__main__":
    unittest.main()
