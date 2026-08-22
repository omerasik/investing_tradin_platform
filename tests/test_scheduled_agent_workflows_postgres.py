from __future__ import annotations

import os
import unittest

from tests.test_agent_answer_evaluation import policy as answer_policy
from tests.test_research_retrieval import policy as retrieval_policy
from tests.test_scheduled_agent_workflows import NOW, governed_fixture


@unittest.skipUnless(os.getenv("POSTGRES_TEST_DSN"), "requires disposable PostgreSQL")
class PostgresScheduledAgentWorkflowTests(unittest.TestCase):
    def test_restart_exact_bindings_idempotency_and_immutability(self) -> None:
        from datetime import timedelta

        from trade_platform.agent_answer_evaluation import PostgresAgentAnswerEvaluationStore
        from trade_platform.operational_alerts import PostgresOperationalAlertStore
        from trade_platform.operational_jobs import PostgresOperationalJobStore
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.research_retrieval import (
            InternalEvidenceChunk,
            PostgresResearchRetrievalStore,
        )
        from trade_platform.scheduled_agent_workflows import (
            PostgresScheduledAgentWorkflowStore,
            evaluate_scheduled_agent_workflows,
        )

        governance, schedule, job_policy, job_run, candidate = governed_fixture()
        assessment = evaluate_scheduled_agent_workflows(
            governance, schedule, job_policy, job_run, (candidate,),
            evaluated_at=NOW + timedelta(hours=2),
        )
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        jobs = PostgresOperationalJobStore(
            database, alerts=PostgresOperationalAlertStore(database),
        )
        jobs.append_policy(job_policy)
        jobs.append_run(job_run)

        retrievals = PostgresResearchRetrievalStore(database)
        retrievals.append_policy(retrieval_policy())
        for item in candidate.retrieval_report.results:
            retrievals.append_chunk(InternalEvidenceChunk.create(
                item.chunk_id, item.source_document_id, item.source_version,
                item.source_kind, candidate.retrieval_report.request.instrument_id,
                item.title, item.excerpt, item.observed_at, item.available_at, None,
                (candidate.role,),
            ))
        retrievals.append_report(candidate.retrieval_report)

        answers = PostgresAgentAnswerEvaluationStore(database)
        answers.append_policy(answer_policy())
        answers.append_report(candidate.answer_evaluation_report)

        store = PostgresScheduledAgentWorkflowStore(database)
        store.append_governance_policy(governance)
        store.append_governance_policy(governance)
        store.append_schedule_policy(schedule, governance, job_policy)
        store.append_schedule_policy(schedule, governance, job_policy)
        store.append_assessment(assessment, (candidate,))
        store.append_assessment(assessment, (candidate,))
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresScheduledAgentWorkflowStore(restarted)
        self.assertEqual(recovered.governance_policy(governance.policy_id), governance)
        self.assertEqual(recovered.schedule_policy(schedule.schedule_policy_id), schedule)
        self.assertEqual(recovered.assessment(assessment.assessment_id), (assessment, (candidate,)))
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE agent_workflow_governance_policy_versions "
                "SET tool_authority='WRITE' WHERE policy_id=%s", (governance.policy_id,),
            )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE agent_workflow_schedule_policy_versions "
                "SET scheduler_authority='RUN' WHERE schedule_policy_id=%s",
                (schedule.schedule_policy_id,),
            )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE scheduled_agent_workflow_assessments "
                "SET action_authority='LIVE' WHERE assessment_id=%s",
                (assessment.assessment_id,),
            )
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "DELETE FROM scheduled_agent_workflow_candidates WHERE assessment_id=%s",
                (assessment.assessment_id,),
            )
        restarted.close()


if __name__ == "__main__":
    unittest.main()
