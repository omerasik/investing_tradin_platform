import os
import unittest
from uuid import uuid4


@unittest.skipUnless(os.getenv("POSTGRES_TEST_DSN"), "requires disposable PostgreSQL")
class PostgresResearchRetrievalTests(unittest.TestCase):
    def test_restart_idempotency_registry_binding_and_immutability(self) -> None:
        from tests.test_research_retrieval import chunk, policy, request
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.research_retrieval import (
            PostgresResearchRetrievalStore,
            ResearchSourceKind,
            retrieve_internal_evidence,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        store = PostgresResearchRetrievalStore(database)
        retrieval_policy = policy()
        evidence = (
            chunk(f"filing:{uuid4()}", "Revenue update", "Revenue and margin improved."),
            chunk(f"risk:{uuid4()}", "Outlook", "Margin outlook uncertain.", kind=ResearchSourceKind.INTERNAL_RISK),
        )
        retrieval_request = request(retrieval_policy)
        report = retrieve_internal_evidence(retrieval_policy, retrieval_request, evidence)
        store.append_policy(retrieval_policy); store.append_policy(retrieval_policy)
        for item in evidence:
            store.append_chunk(item); store.append_chunk(item)
        store.append_report(report); store.append_report(report)
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        restarted_store = PostgresResearchRetrievalStore(restarted)
        self.assertEqual(restarted_store.report(report.report_id), report)
        self.assertEqual(restarted_store.report(report.report_id).agent_request("prompt-v3").workflow_id, report.request.workflow_id)
        with self.assertRaises(PersistenceError), restarted.transaction() as connection:
            connection.execute(
                "UPDATE research_retrieval_reports SET outcome='INSUFFICIENT_EVIDENCE' WHERE report_id=%s",
                (report.report_id,),
            )
        restarted.close()


if __name__ == "__main__":
    unittest.main()
