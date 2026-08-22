import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from tests.test_research_retrieval import NOW, chunk, request
from tests.test_research_retrieval import policy as retrieval_policy
from trade_platform.agent_answer_evaluation import (
    AgentAnswerEvaluationError,
    AgentAnswerEvaluationPolicy,
    AgentAnswerOutcome,
    ClaimEvidenceBinding,
    ClaimKind,
    evaluate_agent_answer,
)
from trade_platform.agent_research import AgentResearchOutput, ResearchAgentRole
from trade_platform.research_retrieval import ResearchSourceKind, retrieve_internal_evidence


def policy() -> AgentAnswerEvaluationPolicy:
    return AgentAnswerEvaluationPolicy.create(
        UUID("53b4cd96-467b-47b3-80ae-32330a8e55c5"), "answer-v1",
        Decimal("1"), Decimal("0.30"), Decimal("1"), 2, Decimal("0.90"),
        True, "research-governance", NOW - timedelta(days=1),
    )


def fixture():
    rp = retrieval_policy()
    rq = replace(request(rp), query_text="revenue margin outlook guidance")
    evidence = (
        chunk("filing:answer", "Revenue margin", "Revenue improved while margin remained stable."),
        chunk("risk:answer", "Outlook uncertainty", "Outlook remains uncertain.", kind=ResearchSourceKind.INTERNAL_RISK),
    )
    retrieval = retrieve_internal_evidence(rp, rq, evidence)
    refs = retrieval.allowed_source_references
    output = AgentResearchOutput(
        uuid4(), rq.workflow_id, rq.instrument_id, ResearchAgentRole.FUNDAMENTAL,
        NOW + timedelta(minutes=1), ("Revenue improved and margin remained stable.",),
        ("Outlook remains uncertain.",), refs, 0.6, ("future guidance unavailable",),
        (), "prompt-v1", "fixture:v1",
    )
    bindings = (
        ClaimEvidenceBinding(ClaimKind.FACT, 0, (refs[0],)),
        ClaimEvidenceBinding(ClaimKind.INFERENCE, 0, (refs[1],)),
    )
    return retrieval, output, bindings


class AgentAnswerEvaluationTests(unittest.TestCase):
    def test_retrieval_bound_supported_answer_is_review_eligible(self) -> None:
        retrieval, output, bindings = fixture()
        report = evaluate_agent_answer(policy(), retrieval, output, bindings, evaluated_at=NOW + timedelta(minutes=2))
        self.assertEqual(report.outcome, AgentAnswerOutcome.REVIEW_ELIGIBLE)
        self.assertEqual(report.metrics["claim_support_rate"], Decimal("1.000000000000"))

    def test_unsupported_causal_language_and_overconfidence_block(self) -> None:
        retrieval, output, bindings = fixture()
        output = replace(output, inferences=("Outlook worsened because revenue caused risk.",), confidence=0.99)
        report = evaluate_agent_answer(policy(), retrieval, output, bindings, evaluated_at=NOW + timedelta(minutes=2))
        self.assertEqual(report.outcome, AgentAnswerOutcome.BLOCKED)
        self.assertIn("unsupported_causal_language", report.reasons)
        self.assertIn("confidence_exceeds_retrieval_evidence", report.reasons)

    def test_every_claim_requires_exact_retrieved_bindings(self) -> None:
        retrieval, output, bindings = fixture()
        with self.assertRaisesRegex(AgentAnswerEvaluationError, "exact_claim_bindings"):
            evaluate_agent_answer(policy(), retrieval, output, bindings[:1], evaluated_at=NOW + timedelta(minutes=2))
        bad = replace(bindings[0], source_references=("internet:unapproved",))
        with self.assertRaisesRegex(AgentAnswerEvaluationError, "unretrieved_source"):
            evaluate_agent_answer(policy(), retrieval, output, (bad, bindings[1]), evaluated_at=NOW + timedelta(minutes=2))

    def test_context_and_policy_time_must_align(self) -> None:
        retrieval, output, bindings = fixture()
        with self.assertRaisesRegex(AgentAnswerEvaluationError, "context_mismatch"):
            evaluate_agent_answer(policy(), retrieval, replace(output, workflow_id=uuid4()), bindings, evaluated_at=NOW + timedelta(minutes=2))
        future = AgentAnswerEvaluationPolicy.create(
            uuid4(), "future", Decimal("1"), Decimal("0.3"), Decimal("1"), 1,
            Decimal("0.9"), True, "governance", output.created_at + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(AgentAnswerEvaluationError, "not_approved"):
            evaluate_agent_answer(future, retrieval, output, bindings, evaluated_at=NOW + timedelta(minutes=2))

    def test_incomplete_retrieval_requires_missing_data_disclosure(self) -> None:
        retrieval, output, bindings = fixture()
        report = evaluate_agent_answer(policy(), retrieval, replace(output, missing_data=()), bindings, evaluated_at=NOW + timedelta(minutes=2))
        self.assertEqual(report.outcome, AgentAnswerOutcome.BLOCKED)
        self.assertIn("incomplete_retrieval_requires_missing_data_declaration", report.reasons)


if __name__ == "__main__":
    unittest.main()
