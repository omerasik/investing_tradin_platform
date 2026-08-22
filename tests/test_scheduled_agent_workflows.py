import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from tests.test_agent_answer_evaluation import fixture as answer_fixture
from tests.test_agent_answer_evaluation import policy as answer_policy
from tests.test_research_retrieval import NOW
from tests.test_research_retrieval import policy as retrieval_policy
from trade_platform.agent_answer_evaluation import evaluate_agent_answer
from trade_platform.agent_research import ResearchAgentRole
from trade_platform.operational_jobs import (
    OperationalJobStatus,
    build_job_policy,
    build_job_run,
)
from trade_platform.research_retrieval import (
    InternalEvidenceChunk,
    retrieve_internal_evidence,
)
from trade_platform.scheduled_agent_workflows import (
    AgentWorkflowGovernancePolicy,
    AgentWorkflowPurpose,
    AgentWorkflowSchedulePolicy,
    ScheduledAgentWorkflowCandidate,
    ScheduledAgentWorkflowError,
    ScheduledAgentWorkflowOutcome,
    evaluate_scheduled_agent_workflows,
)


def fresh_candidate(
    *, input_tokens: int = 400, output_tokens: int = 100,
    answer_evaluated_at=NOW + timedelta(minutes=2),
) -> ScheduledAgentWorkflowCandidate:
    original_retrieval, output, original_bindings = answer_fixture()
    request = replace(
        original_retrieval.request, request_id=uuid4(), workflow_id=uuid4(),
    )
    chunks = tuple(
        InternalEvidenceChunk.create(
            item.chunk_id, item.source_document_id, item.source_version, item.source_kind,
            request.instrument_id, item.title, item.excerpt, item.observed_at,
            item.available_at, None, (ResearchAgentRole.FUNDAMENTAL,),
        )
        for item in original_retrieval.results
    )
    retrieval = retrieve_internal_evidence(retrieval_policy(), request, chunks)
    output = replace(
        output, workflow_id=request.workflow_id,
        source_references=retrieval.allowed_source_references,
    )
    bindings = tuple(
        replace(binding, source_references=(retrieval.allowed_source_references[index],))
        for index, binding in enumerate(original_bindings)
    )
    answer = evaluate_agent_answer(
        answer_policy(), retrieval, output, bindings,
        evaluated_at=answer_evaluated_at,
    )
    return ScheduledAgentWorkflowCandidate(
        AgentWorkflowPurpose.RESEARCH_ASSISTANCE, retrieval, answer,
        input_tokens, output_tokens, Decimal("1.250000000000"),
    )


def governed_fixture(*, enabled: bool = True):
    governance = AgentWorkflowGovernancePolicy.create(
        policy_id=uuid4(),
        version=f"governance-{uuid4()}",
        allowed_purposes=(
            AgentWorkflowPurpose.RESEARCH_ASSISTANCE,
            AgentWorkflowPurpose.REPORT_GENERATION,
        ),
        allowed_roles=(ResearchAgentRole.FUNDAMENTAL,),
        minimum_schedule_interval_seconds=3600,
        maximum_workflows_per_run=3,
        maximum_input_tokens_per_workflow=600,
        maximum_output_tokens_per_workflow=200,
        maximum_total_tokens_per_run=1000,
        maximum_estimated_cost_per_run=Decimal("5.000000000000"),
        cost_currency="EUR",
        require_complete_retrieval=True,
        require_review_eligible_answer=True,
        approved_by="agent-governance",
        approved_at=NOW - timedelta(days=2),
        enabled=enabled,
    )
    job_policy = build_job_policy(
        job_name=f"000-cycle229-agent-review-{uuid4()}",
        version="v1",
        interval=timedelta(days=1),
        grace=timedelta(hours=1),
        owner="research-operations",
        runbook_uri="runbooks/scheduled-agent-review",
        approved_by="operations-reviewer",
        approved_at=NOW - timedelta(days=2),
    )
    schedule = AgentWorkflowSchedulePolicy.create(
        governance,
        job_policy,
        schedule_policy_id=uuid4(),
        schedule_name=f"cycle229-agent-review-{uuid4()}",
        version="v1",
        allowed_purposes=(AgentWorkflowPurpose.RESEARCH_ASSISTANCE,),
        allowed_roles=(ResearchAgentRole.FUNDAMENTAL,),
        effective_from=NOW,
        approved_by="agent-governance",
        approved_at=NOW - timedelta(days=1),
    )
    job_run = build_job_run(
        policy_id=job_policy.policy_id,
        idempotency_key=f"cycle229-{uuid4()}",
        scheduled_for=NOW + timedelta(hours=1),
        started_at=NOW + timedelta(hours=1, seconds=1),
        completed_at=NOW + timedelta(hours=1, seconds=2),
        status=OperationalJobStatus.SUCCEEDED,
        summary={"source": "fixture", "authority": "none"},
    )
    candidate = fresh_candidate()
    return governance, schedule, job_policy, job_run, candidate


class ScheduledAgentWorkflowTests(unittest.TestCase):
    def test_complete_bounded_evidence_is_human_review_only(self) -> None:
        governance, schedule, job_policy, job_run, candidate = governed_fixture()
        assessment = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, job_run, (candidate,),
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(
            assessment.outcome,
            ScheduledAgentWorkflowOutcome.READY_FOR_HUMAN_REVIEW,
        )
        self.assertEqual(assessment.approval_requirement, "EXPLICIT_HUMAN_REVIEW")
        self.assertEqual(
            (
                assessment.tool_authority,
                assessment.model_invocation_authority,
                assessment.action_authority,
            ),
            ("NONE", "NONE", "NONE"),
        )

    def test_total_token_budget_breach_is_blocked(self) -> None:
        governance, schedule, job_policy, job_run, candidate = governed_fixture()
        second = replace(fresh_candidate(input_tokens=500), estimated_cost=Decimal("1.000000000000"))
        assessment = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, job_run, (candidate, second),
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(assessment.outcome, ScheduledAgentWorkflowOutcome.BLOCKED_BUDGET)

    def test_disabled_policy_blocks_without_granting_authority(self) -> None:
        governance, schedule, job_policy, job_run, candidate = governed_fixture(enabled=False)
        assessment = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, job_run, (candidate,),
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(
            assessment.outcome,
            ScheduledAgentWorkflowOutcome.BLOCKED_POLICY_DISABLED,
        )
        self.assertEqual(assessment.action_authority, "NONE")

    def test_failed_job_or_empty_evidence_blocks(self) -> None:
        governance, schedule, job_policy, job_run, candidate = governed_fixture()
        failed = build_job_run(
            policy_id=job_policy.policy_id,
            idempotency_key=f"failed-{uuid4()}",
            scheduled_for=job_run.scheduled_for,
            started_at=job_run.started_at,
            completed_at=job_run.completed_at,
            status=OperationalJobStatus.FAILED,
            summary={"source": "fixture"},
        )
        assessment = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, failed, (candidate,),
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(
            assessment.outcome,
            ScheduledAgentWorkflowOutcome.BLOCKED_INCOMPLETE_EVIDENCE,
        )
        empty = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, job_run, (),
            evaluated_at=NOW + timedelta(hours=2),
        )
        self.assertEqual(
            empty.outcome,
            ScheduledAgentWorkflowOutcome.BLOCKED_INCOMPLETE_EVIDENCE,
        )

    def test_unbound_or_tampered_evidence_is_rejected(self) -> None:
        governance, schedule, job_policy, job_run, candidate = governed_fixture()
        other_answer = fresh_candidate().answer_evaluation_report
        with self.assertRaisesRegex(ScheduledAgentWorkflowError, "unbound"):
            evaluate_scheduled_agent_workflows(
                governance, schedule, job_policy, job_run,
                (replace(candidate, answer_evaluation_report=other_answer),),
                evaluated_at=NOW + timedelta(hours=2),
            )
        with self.assertRaisesRegex(ValueError, "tampered"):
            evaluate_scheduled_agent_workflows(
                governance, schedule, job_policy, job_run,
                (replace(candidate, retrieval_report=replace(
                    candidate.retrieval_report, content_hash="0" * 64,
                )),),
                evaluated_at=NOW + timedelta(hours=2),
            )
        with self.assertRaisesRegex(ScheduledAgentWorkflowError, "unbound"):
            evaluate_scheduled_agent_workflows(
                governance, schedule, job_policy, job_run,
                (fresh_candidate(answer_evaluated_at=NOW + timedelta(hours=1, minutes=1)),),
                evaluated_at=NOW + timedelta(hours=2),
            )

    def test_schedule_rejects_overfrequent_job_and_disallowed_scope(self) -> None:
        governance, _, _, _, _ = governed_fixture()
        fast_job = build_job_policy(
            job_name=f"fast-{uuid4()}", version="v1", interval=timedelta(minutes=5),
            grace=timedelta(minutes=1), owner="research",
            runbook_uri="runbooks/fast", approved_by="reviewer",
            approved_at=NOW - timedelta(days=2),
        )
        with self.assertRaisesRegex(ScheduledAgentWorkflowError, "schedule_policy"):
            AgentWorkflowSchedulePolicy.create(
                governance, fast_job, schedule_policy_id=uuid4(), schedule_name="fast",
                version="v1", allowed_purposes=(AgentWorkflowPurpose.RESEARCH_ASSISTANCE,),
                allowed_roles=(ResearchAgentRole.FUNDAMENTAL,), effective_from=NOW,
                approved_by="reviewer", approved_at=NOW - timedelta(days=1),
            )


if __name__ == "__main__":
    unittest.main()
