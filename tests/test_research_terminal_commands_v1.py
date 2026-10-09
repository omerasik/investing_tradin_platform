"""Research terminal commands -- typed inputs, identities and gates (offline)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from trade_platform.research_terminal_commands_v1 import (
    INLINE_KINDS,
    INPUT_MODELS,
    OWNER_KINDS,
    READINESS_GATES,
    TerminalCommandError,
    command_identity_v1,
    gate_reasons_v1,
    parse_inputs_v1,
)

WINDOW = {"family": "trend_ma_cross", "dataset_version_id": str(uuid4())}


def readiness(**answers: tuple[str, list[str]]) -> SimpleNamespace:
    keys = ["research_run", "candidate_freeze", "decimal_rerun", "holdout_open", "research_watch",
            "paper_incubation"]
    return SimpleNamespace(cycle_id="cycle-2026-08-20", state_hash="0" * 64, answers=[
        SimpleNamespace(key=k, status=answers.get(k, ("READY", []))[0], reasons=answers.get(k, ("READY", []))[1],
                        subjects=[]) for k in keys])


class InputTests(unittest.TestCase):
    def test_the_kind_set_is_closed_and_has_no_order_or_live_command(self) -> None:
        self.assertEqual(set(INPUT_MODELS), set(READINESS_GATES))
        self.assertTrue(INLINE_KINDS <= set(INPUT_MODELS) and OWNER_KINDS <= set(INPUT_MODELS))
        for kind in INPUT_MODELS:
            self.assertFalse(any(word in kind for word in ("ORDER", "TRADE", "BROKER", "LIVE", "EXECUT")), kind)
        with self.assertRaisesRegex(TerminalCommandError, "unknown_command_kind"):
            parse_inputs_v1("PLACE_ORDER", {})

    def test_strategy_and_economic_parameters_are_never_defaulted(self) -> None:
        with self.assertRaisesRegex(TerminalCommandError, "metric|direction|top_k"):
            parse_inputs_v1("CANDIDATE_FREEZE", WINDOW)
        with self.assertRaisesRegex(TerminalCommandError, "workers"):
            parse_inputs_v1("STRATEGY_SEARCH", WINDOW)
        with self.assertRaisesRegex(TerminalCommandError, "workers"):
            parse_inputs_v1("STRATEGY_SEARCH", {**WINDOW, "workers": 12})
        with self.assertRaisesRegex(TerminalCommandError, "invalid_command_inputs:extra"):
            parse_inputs_v1("STRATEGY_SEARCH", {**WINDOW, "workers": 3, "extra": 1})
        with self.assertRaisesRegex(TerminalCommandError, "watchlist_id_OR_9"):
            parse_inputs_v1("RESEARCH_WATCH_START", {**WINDOW, "symbol": "SOLUSDT"})
        freeze = parse_inputs_v1("CANDIDATE_FREEZE", {**WINDOW, "metric": "sharpe_daily_annualized",
                                                      "direction": "HIGHER_IS_BETTER", "top_k": 10})
        self.assertEqual(10, freeze["top_k"])

    def test_a_preregistration_draft_may_omit_owner_fields_but_not_its_bindings(self) -> None:
        base = {**WINDOW, "rerun_hash": "a" * 64, "symbol": "SOLUSDT", "cycle_id": "cycle-2026-08-20"}
        draft = parse_inputs_v1("PREREGISTRATION_RECORD", base)
        self.assertIsNone(draft["holdout_end_exclusive"])
        self.assertEqual([], draft["acceptance_criteria"])
        with self.assertRaisesRegex(TerminalCommandError, "rerun_hash"):
            parse_inputs_v1("PREREGISTRATION_RECORD", {**base, "rerun_hash": "x"})

    def test_identity_is_bound_to_inputs_and_key(self) -> None:
        inputs = parse_inputs_v1("STRATEGY_SEARCH", {**WINDOW, "workers": 3})
        first = command_identity_v1("STRATEGY_SEARCH", inputs, "k-1")
        self.assertEqual(first, command_identity_v1("STRATEGY_SEARCH", inputs, "k-1"))
        self.assertNotEqual(first[0], command_identity_v1("STRATEGY_SEARCH", inputs, "k-2")[0])
        with self.assertRaisesRegex(TerminalCommandError, "idempotency_key"):
            command_identity_v1("STRATEGY_SEARCH", inputs, "has space")


class GateTests(unittest.TestCase):
    def test_a_blocked_answer_blocks_its_kind_with_the_exact_owner_reason(self) -> None:
        reason = "BLOCKED_OWNER_DECISION_OR_11:MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11"
        state = readiness(paper_incubation=("BLOCKED", [reason]))
        start = parse_inputs_v1("PAPER_INCUBATION_START", {**WINDOW, "symbol": "SOLUSDT"})
        self.assertEqual([reason], gate_reasons_v1("PAPER_INCUBATION_START", start, state))
        self.assertEqual([], gate_reasons_v1("PAPER_INCUBATION_STOP", {"start_command_id": str(uuid4())}, state))

    def test_holdout_opening_must_name_the_cycle_and_an_authorized_packet(self) -> None:
        opening = parse_inputs_v1("HOLDOUT_OPEN", {"preregistration_hash": "b" * 64,
                                                   "confirm_cycle_id": "cycle-2026-09-01", "opened_by": "owner"})
        self.assertTrue(gate_reasons_v1("HOLDOUT_OPEN", opening, readiness())[0].startswith(
            "HOLDOUT_CONFIRMATION_DOES_NOT_NAME_THIS_CYCLE"))
        here = {**opening, "confirm_cycle_id": "cycle-2026-08-20"}
        blocked = readiness(holdout_open=("BLOCKED", ["BLOCKED_OWNER_DECISION_OR_7:MISSING_OWNER_HOLDOUT_END_OR_7"]))
        self.assertEqual(["BLOCKED_OWNER_DECISION_OR_7:MISSING_OWNER_HOLDOUT_END_OR_7"],
                         gate_reasons_v1("HOLDOUT_OPEN", here, blocked))
        self.assertEqual(["PREREGISTRATION_NOT_AUTHORIZED"], gate_reasons_v1("HOLDOUT_OPEN", here, readiness()))


if __name__ == "__main__":
    unittest.main()
