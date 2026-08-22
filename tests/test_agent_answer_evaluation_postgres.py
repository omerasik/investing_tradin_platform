import os
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4


@unittest.skipUnless(os.getenv("POSTGRES_TEST_DSN"), "requires disposable PostgreSQL")
class PostgresAgentAnswerEvaluationTests(unittest.TestCase):
    def test_restart_idempotency_retrieval_binding_and_immutability(self) -> None:
        from tests.test_agent_answer_evaluation import policy as answer_policy
        from tests.test_research_retrieval import NOW, chunk, policy, request
        from trade_platform.agent_answer_evaluation import (
            ClaimEvidenceBinding,
            ClaimKind,
            PostgresAgentAnswerEvaluationStore,
            evaluate_agent_answer,
        )
        from trade_platform.agent_research import AgentResearchOutput, ResearchAgentRole
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.research_retrieval import (
            PostgresResearchRetrievalStore,
            ResearchRetrievalPolicy,
            ResearchSourceKind,
            retrieve_internal_evidence,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        retrieval_store = PostgresResearchRetrievalStore(database)
        base_policy = policy()
        unique_policy_id = uuid4()
        retrieval_policy = ResearchRetrievalPolicy.create(
            unique_policy_id, f"retrieval-{unique_policy_id}",
            base_policy.allowed_source_kinds, base_policy.minimum_results,
            base_policy.minimum_distinct_sources, base_policy.maximum_results,
            base_policy.minimum_query_term_coverage, base_policy.approved_by,
            base_policy.approved_at,
        )
        retrieval_request = replace(
            request(retrieval_policy), request_id=uuid4(),
            query_text="revenue margin outlook guidance",
        )
        evidence = (
            chunk(
                f"filing:{uuid4()}", "Revenue margin",
                "Revenue improved while margin remained stable.",
            ),
            chunk(
                f"risk:{uuid4()}", "Outlook uncertainty", "Outlook remains uncertain.",
                kind=ResearchSourceKind.INTERNAL_RISK,
            ),
        )
        retrieval = retrieve_internal_evidence(
            retrieval_policy, retrieval_request, evidence,
        )
        retrieval_store.append_policy(retrieval_policy)
        for item in evidence:
            retrieval_store.append_chunk(item)
        retrieval_store.append_report(retrieval)

        references = retrieval.allowed_source_references
        output = AgentResearchOutput(
            uuid4(), retrieval_request.workflow_id, retrieval_request.instrument_id,
            ResearchAgentRole.FUNDAMENTAL, NOW + timedelta(minutes=1),
            ("Revenue improved and margin remained stable.",),
            ("Outlook remains uncertain.",), references, 0.6,
            ("future guidance unavailable",), (), "prompt-v1", "fixture:v1",
        )
        bindings = (
            ClaimEvidenceBinding(ClaimKind.FACT, 0, (references[0],)),
            ClaimEvidenceBinding(ClaimKind.INFERENCE, 0, (references[1],)),
        )
        evaluation_policy = answer_policy()
        report = evaluate_agent_answer(
            evaluation_policy, retrieval, output, bindings,
            evaluated_at=NOW + timedelta(minutes=2),
        )
        store = PostgresAgentAnswerEvaluationStore(database)
        store.append_policy(evaluation_policy)
        store.append_policy(evaluation_policy)
        store.append_report(report)
        store.append_report(report)
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        restarted_store = PostgresAgentAnswerEvaluationStore(restarted)
        self.assertEqual(restarted_store.report(report.report_id), report)
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE agent_answer_evaluation_reports SET outcome='BLOCKED' "
                "WHERE report_id=%s", (report.report_id,),
            )
        restarted.close()


if __name__ == "__main__":
    unittest.main()
