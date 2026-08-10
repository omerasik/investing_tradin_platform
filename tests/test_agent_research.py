from dataclasses import replace
from datetime import datetime, timezone
import unittest
from uuid import uuid4

from trade_platform.agent_research import AgentResearchError, AgentResearchOutput, AgentResearchRequest, AgentResearchReview, ConfiguredHttpsResearchAgent, ResearchAgentRole, ResearchModelConfiguration, SQLiteAgentResearchStore, assess_agent_research_output, run_structured_research_agent
from trade_platform.data_providers import HttpResponse, RetryPolicy, ProviderConfigurationError


NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def output(workflow_id, role, *, contradicts=()):
    return AgentResearchOutput(uuid4(), workflow_id, "US:NASDAQ:ACME", role, NOW, (f"fact:{role}",), (f"inference:{role}",), (f"source:{role}",), 0.6, ("missing:optional",), contradicts, "prompt-v1", "model-v1")


class FixtureAgent:
    provider_name = "fixture-agent"
    model_version = "fixture-v1"
    def __init__(self, response): self.response = response
    def generate(self, request): return self.response


class Responses:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def post(self, url, payload, timeout_seconds, secret_reference): self.calls.append((url, payload, secret_reference)); return self.responses.pop(0)


class AgentResearchTests(unittest.TestCase):
    def test_synthesis_is_fail_closed_until_all_required_roles_are_audited(self) -> None:
        store = SQLiteAgentResearchStore(); workflow_id = uuid4()
        with self.assertRaisesRegex(AgentResearchError, "requires_all_roles"):
            store.append(output(workflow_id, ResearchAgentRole.FINAL_SYNTHESIS))
        records = []
        for role in ResearchAgentRole:
            if role is ResearchAgentRole.FINAL_SYNTHESIS: continue
            record = output(workflow_id, role, contradicts=(records[-1].output_id,) if records else ())
            store.append(record); records.append(record)
        final = output(workflow_id, ResearchAgentRole.FINAL_SYNTHESIS, contradicts=(records[-1].output_id,)); store.append(final)
        self.assertEqual((len(store.outputs_for_workflow(workflow_id)), store.get(final.output_id).role, store.get(final.output_id).contradicts_output_ids), (10, ResearchAgentRole.FINAL_SYNTHESIS, (records[-1].output_id,)))

    def test_contradictions_cannot_cross_workflows_and_fields_are_separated(self) -> None:
        store = SQLiteAgentResearchStore(); first = output(uuid4(), ResearchAgentRole.TECHNICAL); store.append(first)
        with self.assertRaisesRegex(AgentResearchError, "outside_workflow"):
            store.append(output(uuid4(), ResearchAgentRole.FUNDAMENTAL, contradicts=(first.output_id,)))
        with self.assertRaisesRegex(AgentResearchError, "invalid_agent_research_output"):
            AgentResearchOutput(uuid4(), uuid4(), "x", ResearchAgentRole.NEWS, NOW, (), ("inference",), ("source",), 0.5, (), (), "p", "m")

    def test_structured_agent_response_is_source_bound_and_final_review_is_human_audited(self) -> None:
        workflow_id = uuid4(); request = AgentResearchRequest(workflow_id, "US:NASDAQ:ACME", ResearchAgentRole.TECHNICAL, "prompt-v2", ("filing:1",))
        response = {"facts":["Revenue is reported"], "inferences":["Trend may improve"], "source_references":["filing:1"], "confidence":0.7, "missing_data":["volume"], "contradicts_output_ids":[]}
        generated = run_structured_research_agent(FixtureAgent(response), request, created_at=NOW)
        self.assertEqual((generated.prompt_version, generated.model_version, generated.source_references), ("prompt-v2", "fixture-agent:fixture-v1", ("filing:1",)))
        with self.assertRaisesRegex(AgentResearchError, "unapproved_source"):
            run_structured_research_agent(FixtureAgent({**response, "source_references":["internet:unapproved"]}), request, created_at=NOW)
        store = SQLiteAgentResearchStore(); records = []
        for role in ResearchAgentRole:
            if role is not ResearchAgentRole.FINAL_SYNTHESIS:
                item = output(workflow_id, role); store.append(item); records.append(item)
        final = output(workflow_id, ResearchAgentRole.FINAL_SYNTHESIS); store.append(final)
        review = AgentResearchReview(uuid4(), workflow_id, final.output_id, "research-committee", True, "Evidence reviewed", NOW); store.append_review(review)
        self.assertEqual(store.reviews_for_workflow(workflow_id), (review,))

    def test_read_only_query_parser_is_audited_and_role_scoped(self) -> None:
        store = SQLiteAgentResearchStore(); workflow_id = uuid4(); technical = output(workflow_id, ResearchAgentRole.TECHNICAL); fundamental = output(workflow_id, ResearchAgentRole.FUNDAMENTAL); store.append(technical); store.append(fundamental)
        query, results = store.query_workflow(workflow_id, "show technical facts", actor="operator", requested_at=NOW)
        self.assertEqual((query.intent, results), ("facts", ({"output_id": str(technical.output_id), "role": "TECHNICAL", "value": ["fact:TECHNICAL"]},)))
        with self.assertRaisesRegex(AgentResearchError, "unsupported_agent_research_query"):
            store.query_workflow(workflow_id, "place an order", actor="operator", requested_at=NOW)

    def test_configured_https_agent_uses_secret_reference_only_and_retries_structured_response(self) -> None:
        response = {"facts":["reported fact"], "inferences":["bounded inference"], "source_references":["filing:1"], "confidence":0.5, "missing_data":[]}
        transport, sleeps = Responses(HttpResponse(429, "", {"Retry-After":"2"}), HttpResponse(200, __import__("json").dumps(response))), []
        configuration = ResearchModelConfiguration("research-model", "https://model.example", "model-v1", "RESEARCH_MODEL_API_KEY", terms_accepted=False)
        agent = ConfiguredHttpsResearchAgent(configuration, transport, retry_policy=RetryPolicy(2), sleep=sleeps.append); request = AgentResearchRequest(uuid4(), "US:NASDAQ:ACME", ResearchAgentRole.TECHNICAL, "prompt-v1", ("filing:1",))
        with self.assertRaisesRegex(ProviderConfigurationError, "terms_not_accepted"):
            agent.generate(request)
        agent = ConfiguredHttpsResearchAgent(ResearchModelConfiguration("research-model", "https://model.example", "model-v1", "RESEARCH_MODEL_API_KEY", terms_accepted=True), transport, retry_policy=RetryPolicy(2), sleep=sleeps.append)
        output = run_structured_research_agent(agent, request, created_at=NOW)
        self.assertEqual((sleeps, transport.calls[-1][2], output.model_version, transport.calls[-1][1]["response_schema"]), ([2.0], "RESEARCH_MODEL_API_KEY", "research-model:model-v1", "agent-research-v1"))

    def test_safety_assessment_rejects_direct_order_instructions_and_is_workflow_bound(self) -> None:
        store = SQLiteAgentResearchStore(); workflow_id = uuid4()
        unsafe = AgentResearchOutput(uuid4(), workflow_id, "US:NASDAQ:ACME", ResearchAgentRole.TECHNICAL, NOW, ("Reported fact",), ("Place an order immediately",), ("filing:1",), 0.5, (), (), "prompt-v1", "model-v1")
        store.append(unsafe)
        assessment = assess_agent_research_output(unsafe, evaluator="policy-engine", policy_version="research-safety-v1", assessed_at=NOW)
        store.append_safety_assessment(assessment)
        self.assertEqual((assessment.approved, assessment.reasons), (False, ("direct_operational_instruction_detected",)))
        outside = replace(assessment, assessment_id=uuid4(), workflow_id=uuid4())
        with self.assertRaisesRegex(AgentResearchError, "outside_workflow"):
            store.append_safety_assessment(outside)

    def test_operator_ready_view_requires_safe_outputs_and_latest_human_approval(self) -> None:
        store = SQLiteAgentResearchStore(); workflow_id = uuid4(); records = []
        for role in ResearchAgentRole:
            if role is not ResearchAgentRole.FINAL_SYNTHESIS:
                item = output(workflow_id, role); store.append(item); records.append(item)
        final = output(workflow_id, ResearchAgentRole.FINAL_SYNTHESIS); store.append(final); records.append(final)
        with self.assertRaisesRegex(AgentResearchError, "requires_safe_outputs"):
            store.operator_ready_outputs(workflow_id)
        for item in records:
            store.append_safety_assessment(assess_agent_research_output(item, evaluator="policy-engine", policy_version="research-safety-v1", assessed_at=NOW))
        with self.assertRaisesRegex(AgentResearchError, "requires_approved_review"):
            store.operator_ready_outputs(workflow_id)
        store.append_review(AgentResearchReview(uuid4(), workflow_id, final.output_id, "committee", False, "Need more evidence", NOW))
        with self.assertRaisesRegex(AgentResearchError, "requires_approved_review"):
            store.operator_ready_outputs(workflow_id)
        approved_at = NOW.replace(day=2)
        store.append_review(AgentResearchReview(uuid4(), workflow_id, final.output_id, "committee", True, "Approved after review", approved_at))
        expected = tuple(sorted(records, key=lambda item: (item.created_at, str(item.output_id))))
        self.assertEqual(store.operator_ready_outputs(workflow_id), expected)


if __name__ == "__main__":
    unittest.main()
