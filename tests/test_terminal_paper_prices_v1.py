"""Explicit server configuration and terminal consumption of fixture-only sealed prices."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from tests.test_first_party_t4_forward_bars_v1 import (
    LATER,
    _acceptance,
    _ForwardFixture,
    _micros,
    _venue_minute,
)
from tests.test_paper_economics_v1 import GROSS, STUDY, TRIAL, fill
from trade_platform.research_terminal_read_model_v1 import _candidate_economics
from trade_platform.strategy_lab_study_v1 import identity_hash_v1
from trade_platform.terminal_paper_prices_v1 import TerminalPaperPricesV1


class TerminalPricesTests(_ForwardFixture):
    def setUp(self):
        super().setUp()
        self.sealed = self.seal()
        self.entry = fill(0, base=_micros(_venue_minute(1)), price="84000")
        self.entry["evidence"] = {"session_id": str(self.session_id)}
        self.policy = {
            "schema_version": "terminal-paper-price-selection-v1", "policy_reference": "fixture/operator-selection/1",
            "declared_at": (LATER - timedelta(hours=2)).isoformat(),
            "selections": [{"cycle_id": "fixture-cycle", "study_id": STUDY, "trial_id": TRIAL,
                "entry_fill_hash": identity_hash_v1(self.entry), "symbol": "BTCUSDT",
                "source_id": str(self.sealed.source_id), "session_id": str(self.session_id),
                "instrument": self.sealed.identity["instrument"], "instant": _venue_minute(2).isoformat(),
                "max_age_micros": 3 * 86400 * 1_000_000, "capture_max_unproven_nanos": 30_000_000_000}],
        }

    def source(self, policy=None, expected_hash=None):
        policy = self.policy if policy is None else policy
        with patch("trade_platform.terminal_paper_prices_v1.datetime", wraps=datetime) as clock:
            clock.now.return_value = LATER - timedelta(hours=1)
            return TerminalPaperPricesV1(policy, expected_hash=expected_hash or identity_hash_v1(policy),
                                         research_root=self.store().root.parent, capture_root=self.capture)

    def cursor(self, seals=True, holdout_start=None):
        cursor = Mock()
        cursor.fetchone.side_effect = [(holdout_start or LATER + timedelta(days=1),), None]
        cursor.fetchall.return_value = [(self.sealed.dataset_version_id, self.sealed.content_hash,
                                         self.sealed.identity, self.sealed.frame_manifests,
                                         self.sealed.sealed_at)] if seals else []
        return cursor

    def project(self, source=None, cursor=None):
        context = SimpleNamespace(
            cycle=SimpleNamespace(cost=GROSS, resolution=None, as_of=LATER, cycle_id="fixture-cycle"),
            refusal=None, cost_approval=None, prices=self.source() if source is None else source,
            cursor=self.cursor() if cursor is None else cursor)
        with patch("trade_platform.paper_proven_prices_v1.derive_hourly_acceptance_v1",
                   return_value=_acceptance(self.session_id)):
            return _candidate_economics(context, STUDY, TRIAL, [self.entry])

    def test_terminal_resolves_bound_entry_and_preserves_clocks_and_units(self):
        view = self.project()
        self.assertEqual("AVAILABLE", view.gross_unrealized.status, view.price_resolution)
        evidence = view.price_resolution["evidence"]
        self.assertEqual(_micros(_venue_minute(1)), evidence["entry_bar_open_micros"])
        self.assertEqual(str(self.sealed.source_id), evidence["source_id"])
        self.assertEqual(str(self.session_id), evidence["reference"]["session_id"])
        self.assertEqual(_micros(LATER), evidence["as_of_micros"])
        self.assertEqual(_micros(self.sealed.sealed_at), evidence["sealed_at_micros"])
        self.assertIn("clock", evidence["bar"])
        self.assertTrue(view.price_resolution["selection_policy"]["policy_hash"])
        self.assertEqual("NONE_NOT_A_CURRENCY_AMOUNT", view.money)
        self.assertEqual("NOT_COSTABLE", view.net.status)
        self.assertIsNone(view.net.by_scenario)
        self.assertFalse(view.gross_unrealized.in_net)
        self.assertEqual("0.000000000000", view.gross_realized["value"])

    def test_missing_seals_stale_session_and_lookahead_show_exact_refusals(self):
        cases = [({}, False, "NO_SEALED_T4_PRICE_DATASET"),
                 ({"max_age_micros": 60_000_000}, True, "PROVEN_T4_PRICE_STALE"),
                 ({"session_id": str(uuid4())}, True, "PRICE_SELECTION_STORED_ENTRY_SESSION_MISMATCH"),
                 ({"instant": (LATER + timedelta(minutes=1)).isoformat()}, True, "PRICE_OBSERVATION_AFTER_AS_OF")]
        for changes, seals, reason in cases:
            with self.subTest(reason=reason):
                policy = deepcopy(self.policy)
                policy["selections"][0].update(changes)
                view = self.project(self.source(policy), self.cursor(seals))
                self.assertEqual("UNAVAILABLE", view.gross_unrealized.status)
                self.assertIsNone(view.gross_unrealized.value)
                self.assertEqual([reason], view.gross_unrealized.reasons)
                self.assertEqual([reason], view.price_resolution["reasons"])
                self.assertEqual("NOT_COSTABLE", view.net.status)

    def test_wrong_entry_ambiguous_policy_malformed_and_future_policy_refuse(self):
        for changes, reason in [
            ({"entry_fill_hash": "0" * 64}, "PRICE_SELECTION_STORED_ENTRY_MISMATCH"),
            ({"max_age_micros": True}, "PRICE_SELECTION_MAX_AGE_MICROS_MUST_BE_INTEGER"),
        ]:
            policy = deepcopy(self.policy)
            policy["selections"][0].update(changes)
            self.assertEqual([reason], self.project(self.source(policy)).gross_unrealized.reasons)
        for mutate, reason in [
            (lambda p: p.update(declared_at=(LATER + timedelta(seconds=1)).isoformat()),
             "PRICE_SELECTION_POLICY_NOT_KNOWN_BY_AS_OF"),
            (lambda p: p.update(policy_reference=""), "PRICE_SELECTION_POLICY_PROVENANCE_MISSING"),
            (lambda p: p["selections"].append(p["selections"][0]), "NO_UNIQUE_CANDIDATE_PRICE_SELECTION"),
            (lambda p: p["selections"][0].pop("max_age_micros"), "PRICE_SELECTION_POLICY_MALFORMED"),
        ]:
            policy = deepcopy(self.policy)
            mutate(policy)
            self.assertEqual([reason], self.project(self.source(policy)).gross_unrealized.reasons)
        self.assertEqual(["PRICE_SELECTION_POLICY_HASH_MISMATCH"],
                         self.project(self.source(expected_hash="0" * 64)).gross_unrealized.reasons)

    def test_installation_prevents_self_asserted_historical_availability(self):
        source = TerminalPaperPricesV1(self.policy, expected_hash=identity_hash_v1(self.policy),
                                       research_root=self.store().root.parent, capture_root=self.capture)
        self.assertEqual(["PRICE_SELECTION_POLICY_NOT_KNOWN_BY_AS_OF"], self.project(source).gross_unrealized.reasons)

    def test_holdout_is_refused_before_catalogue_or_raw_artifact_read(self):
        cursor = self.cursor(holdout_start=_venue_minute(1))
        view = self.project(cursor=cursor)
        self.assertEqual(["EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT"], view.gross_unrealized.reasons)
        cursor.fetchall.assert_not_called()

    def test_pre_boundary_selection_never_replays_a_partition_spanning_unopened_holdout(self):
        cursor = self.cursor(holdout_start=_venue_minute(4))
        with patch("trade_platform.first_party_t4_dataset_v1.t4_seal_from_catalog_v1") as replay:
            view = self.project(cursor=cursor)
        self.assertEqual(["PRICE_REPLAY_SCOPE_TOUCHES_UNOPENED_HOLDOUT_OR_UNPROVEN"],
                         view.gross_unrealized.reasons)
        replay.assert_not_called()

    def test_protected_existing_api_serializes_resolution_without_accepting_price_inputs(self):
        from fastapi.testclient import TestClient

        from tests.test_research_terminal_read_model_v1 import _paper
        from trade_platform.api import build_app
        from trade_platform.security import OperatorAuthenticator

        paper = _paper(uuid4(), uuid4())
        paper.economics = self.project()
        queries = Mock()
        queries.terminal_candidate_paper_evidence.return_value = paper
        client = TestClient(build_app(authenticator=OperatorAuthenticator("fixture-token"),
                                      operator_dashboard_queries=queries))
        path = f"/operator-dashboard/research-terminal/candidates/{paper.study_id}/{paper.trial_id}/paper-evidence"
        self.assertEqual(401, client.get(path).status_code)
        response = client.get(path, headers={"Authorization": "Bearer fixture-token"})
        self.assertEqual(200, response.status_code)
        economics = response.json()["economics"]
        self.assertEqual("AVAILABLE", economics["price_resolution"]["status"])
        self.assertEqual("NOT_COSTABLE", economics["net"]["status"])
        self.assertIsNone(economics["net"]["by_scenario"])

    def test_later_recorded_entry_is_excluded_from_historical_context(self):
        from trade_platform.research_terminal_read_model_v1 import _CycleContext, read_incubation_v1

        cursor = Mock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [(identity_hash_v1(self.entry), LATER + timedelta(seconds=1))]
        with patch("trade_platform.paper_account_v1.cycle_fill_rows_v1",
                   return_value=([(identity_hash_v1(self.entry), self.entry)], [])), \
                patch("trade_platform.paper_account_v1.cycle_cost_policy_v1", return_value=GROSS), \
                patch("trade_platform.paper_incubation_registry_v1.resolve_incubation_bindings_v1", return_value=None):
            context = _CycleContext(cursor, LATER)
            incubation = read_incubation_v1(cursor, now=LATER)
        self.assertEqual([], context.fills)
        self.assertEqual("NO_FILLS", incubation.state)
        self.assertEqual([], incubation.report["candidates"])
