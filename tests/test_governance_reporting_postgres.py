from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.getenv("POSTGRES_TEST_DSN"), "requires disposable PostgreSQL")
class PostgresGovernanceReportingTests(unittest.TestCase):
    def test_restart_idempotency_exact_bindings_and_immutability(self) -> None:
        from trade_platform.governance_reporting import (
            BudgetMode,
            CostBudgetPolicy,
            CostCandidateType,
            GovernanceReportType,
            OperationalCostCategory,
            OperationalCostObservation,
            PostgresGovernanceReportingStore,
            ReportEvidenceClass,
            ReportSchedulePolicy,
            assess_cost_value,
            generate_governance_report,
        )
        from trade_platform.operational_alerts import PostgresOperationalAlertStore
        from trade_platform.operational_jobs import (
            OperationalJobStatus,
            PostgresOperationalJobStore,
            build_job_policy,
            build_job_run,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        suffix = str(uuid4())
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = datetime(2026, 9, 1, tzinfo=UTC)
        job_policy = build_job_policy(
            job_name=f"000-cycle228-monthly-cost-{suffix}",
            version="v1",
            interval=timedelta(days=31),
            grace=timedelta(hours=1),
            owner="operations",
            runbook_uri="runbooks/monthly-cost-report",
            approved_by="operations-reviewer",
            approved_at=start,
        )
        job_run = build_job_run(
            policy_id=job_policy.policy_id,
            idempotency_key=f"cycle228-monthly-cost-run-{suffix}",
            scheduled_for=end,
            started_at=end + timedelta(seconds=1),
            completed_at=end + timedelta(seconds=2),
            status=OperationalJobStatus.SUCCEEDED,
            summary={"source": "postgres-fixture", "authority": "none"},
        )
        jobs = PostgresOperationalJobStore(
            database, alerts=PostgresOperationalAlertStore(database),
        )
        jobs.append_policy(job_policy)
        jobs.append_run(job_run)

        schedule = ReportSchedulePolicy.create(
            policy_name=f"cycle228-cost-schedule-{suffix}",
            version="v1",
            report_type=GovernanceReportType.MONTHLY_COST,
            job_policy=job_policy,
            approved_by="governance-reviewer",
            approved_at=start,
        )
        budget = CostBudgetPolicy.create(
            policy_name=f"cycle228-cost-budget-{suffix}",
            version="v1",
            budget_mode=BudgetMode.LOCAL_RESEARCH,
            currency="EUR",
            period_start=start,
            period_end=end,
            total_limit=Decimal("100"),
            category_limits={category: Decimal("10") for category in OperationalCostCategory},
            minimum_value_to_cost_ratio=Decimal("1.25"),
            approved_by="budget-reviewer",
            approved_at=start - timedelta(days=1),
        )
        costs = tuple(
            OperationalCostObservation.create(
                category=category,
                service_reference=f"fixture/{suffix}/{category.value.lower()}",
                amount=Decimal("5"),
                currency="EUR",
                period_start=start,
                period_end=end,
                observed_at=end - timedelta(days=1, seconds=index),
                evidence_class=ReportEvidenceClass.FACT,
                evidence_reference=f"evidence/{suffix}/{category.value.lower()}",
            )
            for index, category in enumerate(OperationalCostCategory)
        )
        sections_input = {
            ReportEvidenceClass.FACT: ("All twelve fixture cost categories are supplied.",),
            ReportEvidenceClass.MODEL_ESTIMATE: (),
            ReportEvidenceClass.INFERENCE: (),
            ReportEvidenceClass.UNVERIFIED_INFORMATION: (),
            ReportEvidenceClass.MISSING_DATA: (),
        }
        report, sections = generate_governance_report(
            schedule,
            job_policy,
            job_run,
            period_start=start,
            period_end=end,
            generated_at=end + timedelta(minutes=1),
            sections=sections_input,
            cost_policy=budget,
            cost_observations=costs,
        )
        value_assessment = assess_cost_value(
            budget,
            candidate_type=CostCandidateType.DATASET,
            candidate_reference=f"dataset/{suffix}",
            evaluated_at=start + timedelta(days=2),
            incremental_cost=Decimal("10"),
            measurable_value_estimate=Decimal("15"),
            currency="EUR",
            evidence_references=(f"evidence/value/{suffix}", f"evidence/cost/{suffix}"),
            deterministic_alternative_available=False,
            proposed_ai_inference=False,
        )
        store = PostgresGovernanceReportingStore(database)
        store.append_schedule_policy(schedule)
        store.append_schedule_policy(schedule)
        store.append_cost_policy(budget)
        store.append_cost_policy(budget)
        store.append_cost_value_assessment(value_assessment)
        store.append_cost_value_assessment(value_assessment)
        for observation in costs:
            store.append_cost_observation(observation)
            store.append_cost_observation(observation)
        store.append_report(report, sections, costs)
        store.append_report(report, tuple(reversed(sections)), tuple(reversed(costs)))
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresGovernanceReportingStore(restarted)
        self.assertEqual(recovered.schedule_policy(schedule.policy_id), schedule)
        self.assertEqual(recovered.cost_policy(budget.policy_id), budget)
        self.assertEqual(
            recovered.cost_value_assessment(value_assessment.assessment_id),
            value_assessment,
        )
        self.assertEqual(recovered.report(report.report_id), report)
        self.assertEqual(recovered.report_sections(report.report_id), sections)
        self.assertEqual(
            recovered.report_cost_observations(report.report_id),
            tuple(sorted(costs, key=lambda item: (item.category.value, str(item.observation_id)))),
        )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE cost_value_assessments SET procurement_authority='PURCHASE' WHERE assessment_id=%s",
                (value_assessment.assessment_id,),
            )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE governance_reports SET execution_authority='LIVE' WHERE report_id=%s",
                (report.report_id,),
            )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE governance_report_sections SET entries='[]'::jsonb "
                "WHERE report_id=%s AND evidence_class='FACT'",
                (report.report_id,),
            )
        restarted.close()


if __name__ == "__main__":
    unittest.main()
