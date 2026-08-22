import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from trade_platform.agent_research import ResearchAgentRole
from trade_platform.research_retrieval import (
    InternalEvidenceChunk,
    ResearchRetrievalError,
    ResearchRetrievalPolicy,
    ResearchRetrievalRequest,
    ResearchSourceKind,
    RetrievalOutcome,
    retrieve_internal_evidence,
)

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def policy() -> ResearchRetrievalPolicy:
    return ResearchRetrievalPolicy.create(
        UUID("24d2f896-68df-4ff2-aa91-7a8fc15414b0"), "retrieval-v1",
        (ResearchSourceKind.INTERNAL_FILING, ResearchSourceKind.INTERNAL_RISK),
        2, 2, 3, Decimal("0.66"), "research-governance", NOW - timedelta(days=2),
    )


def chunk(
    source: str, title: str, text: str, *, kind: ResearchSourceKind = ResearchSourceKind.INTERNAL_FILING,
    available_at: datetime = NOW - timedelta(hours=2), invalidated_at: datetime | None = None,
    role: ResearchAgentRole = ResearchAgentRole.FUNDAMENTAL,
) -> InternalEvidenceChunk:
    return InternalEvidenceChunk.create(
        uuid4(), source, "v1", kind, "US:NASDAQ:ACME", title, text,
        available_at - timedelta(hours=1), available_at, invalidated_at, (role,),
    )


def request(retrieval_policy: ResearchRetrievalPolicy) -> ResearchRetrievalRequest:
    return ResearchRetrievalRequest(
        UUID("93211d37-f96d-4868-9057-d7aa58077750"), uuid4(), retrieval_policy.policy_id,
        "US:NASDAQ:ACME", ResearchAgentRole.FUNDAMENTAL,
        "revenue margin outlook", NOW,
    )


class ResearchRetrievalTests(unittest.TestCase):
    def test_point_in_time_access_scoped_retrieval_binds_agent_sources(self) -> None:
        retrieval_policy = policy()
        evidence = (
            chunk("filing:1", "Revenue update", "Revenue improved and margin was stable."),
            chunk("risk:1", "Margin outlook", "Margin outlook remains uncertain.", kind=ResearchSourceKind.INTERNAL_RISK),
            chunk("future:1", "Revenue outlook", "Revenue margin outlook later.", available_at=NOW + timedelta(hours=1)),
            chunk("wrong-role:1", "Revenue outlook", "Revenue margin outlook.", role=ResearchAgentRole.TECHNICAL),
        )
        report = retrieve_internal_evidence(retrieval_policy, request(retrieval_policy), evidence)
        agent_request = report.agent_request("prompt-v3")
        self.assertEqual(report.outcome, RetrievalOutcome.COMPLETE)
        self.assertEqual([item.source_document_id for item in report.results], ["filing:1", "risk:1"])
        self.assertEqual(len(agent_request.allowed_source_references), 2)
        self.assertTrue(all(item.startswith(f"retrieval:{report.report_id}:") for item in agent_request.allowed_source_references))

    def test_insufficient_evidence_is_explicit_and_cannot_bind_agent(self) -> None:
        retrieval_policy = policy()
        report = retrieve_internal_evidence(
            retrieval_policy, request(retrieval_policy),
            (chunk("filing:1", "Revenue", "Revenue improved."),),
        )
        self.assertEqual(report.outcome, RetrievalOutcome.INSUFFICIENT_EVIDENCE)
        self.assertIn("minimum_results_not_met", report.reasons)
        with self.assertRaisesRegex(ResearchRetrievalError, "insufficient_retrieval_evidence"):
            report.agent_request("prompt-v3")

    def test_invalidated_disallowed_and_global_sources_fail_closed(self) -> None:
        retrieval_policy = policy()
        evidence = (
            chunk("old:1", "Revenue margin outlook", "Revenue margin outlook.", invalidated_at=NOW - timedelta(minutes=1)),
            chunk("event:1", "Revenue margin outlook", "Revenue margin outlook.", kind=ResearchSourceKind.INTERNAL_EVENT),
        )
        report = retrieve_internal_evidence(retrieval_policy, request(retrieval_policy), evidence)
        self.assertEqual((report.results, report.query_term_coverage), ((), Decimal("0")))

    def test_policy_must_precede_request_and_hashes_are_verified(self) -> None:
        retrieval_policy = policy()
        future_policy = ResearchRetrievalPolicy.create(
            retrieval_policy.policy_id, retrieval_policy.version,
            retrieval_policy.allowed_source_kinds, retrieval_policy.minimum_results,
            retrieval_policy.minimum_distinct_sources, retrieval_policy.maximum_results,
            retrieval_policy.minimum_query_term_coverage, retrieval_policy.approved_by,
            NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ResearchRetrievalError, "not_approved"):
            retrieve_internal_evidence(future_policy, request(retrieval_policy), ())
        with self.assertRaisesRegex(ResearchRetrievalError, "invalid_internal_evidence_chunk"):
            replace(chunk("filing:1", "Revenue", "Revenue outlook."), text="tampered")

    def test_report_is_deterministic_and_contains_no_execution_authority(self) -> None:
        retrieval_policy = policy(); retrieval_request = request(retrieval_policy)
        evidence = (
            chunk("filing:1", "Revenue update", "Revenue and margin improved."),
            chunk("risk:1", "Outlook", "Margin outlook uncertain.", kind=ResearchSourceKind.INTERNAL_RISK),
        )
        first = retrieve_internal_evidence(retrieval_policy, retrieval_request, evidence)
        second = retrieve_internal_evidence(retrieval_policy, retrieval_request, tuple(reversed(evidence)))
        self.assertEqual((first.report_id, first.content_hash), (second.report_id, second.content_hash))
        self.assertIn("retrieval_has_no_model_tool_signal_order_risk_or_approval_authority", first.limitations)


if __name__ == "__main__":
    unittest.main()
