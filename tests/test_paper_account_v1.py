"""Phase R10.2 -- the paper account ledger and explicit not-applicable controls (offline, synthetic values).

Every number here is a TEST FIXTURE, never an owner decision.
"""

from __future__ import annotations

import random
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trade_platform.account_policy_v1 import (
    NOT_APPLICABLE_V1,
    AccountContextV1,
    AccountKindV1,
    AccountPolicyError,
    AccountPolicyV1,
)
from trade_platform.paper_account_v1 import PaperAccountError, build_paper_account_ledger_v1
from trade_platform.strategy_lab_policies_v1 import (
    CostPolicyV1,
    FeeScheduleV1,
    SlippageScenarioV1,
    gross_cost_policy_v1,
)

T0 = int(datetime(2026, 11, 2, tzinfo=UTC).timestamp()) * 1_000_000
AS_OF = datetime(2026, 12, 1, tzinfo=UTC)
PAPER = AccountContextV1("fixture-paper", AccountKindV1.PERSONAL_PAPER, "Fixture", "USDT")
NA = {name: NOT_APPLICABLE_V1 for name in (
    "minimum_data_quality", "maximum_spread_fraction", "maximum_event_risk", "maximum_expected_slippage_fraction",
    "maximum_per_trade_loss", "maximum_stop_distance_fraction", "stop_gap_buffer_fraction")}
VALUES: dict[str, Any] = {
    "paper_starting_capital": "10000", "paper_order_notional": "1000", "maximum_leverage": "3",
    "daily_loss_limit": "500", "maximum_drawdown_limit": "150", "allowed_symbols": ["BTCUSDT"],
    "maximum_order_notional": "2000", "maximum_position_notional": "2000", "maximum_daily_order_notional": "100000",
    "max_market_age_seconds": 60, **NA,
}


def policy(**overrides: Any) -> AccountPolicyV1:
    return AccountPolicyV1(PAPER, {**VALUES, **overrides}, "fixture-owner", "2026-10-09")


def fill(sig: str, minute: int, frm: int, to: int, price: str = "100", *, status: str = "FILLED",
         symbol: str = "BTCUSDT", trial: str = "t1") -> dict[str, Any]:
    decided = T0 + minute * 60_000_000 + 1_500_000
    bar = T0 + (minute + 1) * 60_000_000
    return {"signal_id": sig, "study_id": "s1", "trial_id": trial, "symbol": symbol, "target_from": frm,
            "target_to": to, "decided_at_micros": decided, "fill_bar_open_micros": bar, "status": status,
            "fill_price": price if status == "FILLED" else None,
            "evidence": {"open_market_knowledge_micros": bar + 300_000}}


def signals(*fills: dict[str, Any], age_s: int = 1) -> dict[str, Any]:
    return {f["signal_id"]: {"evidence": {"complete_market_knowledge_micros": f["decided_at_micros"] - age_s * 1_000_000}}
            for f in fills}


def ledger(fills: list[dict[str, Any]], *, p: AccountPolicyV1 | None = None, cost: CostPolicyV1 | None = None,
           age_s: int = 1) -> dict[str, Any]:
    return build_paper_account_ledger_v1(p or policy(), fills, signals(*fills, age_s=age_s),
                                         cost or gross_cost_policy_v1(), as_of=AS_OF)


class NotApplicableTests(unittest.TestCase):
    def test_not_applicable_is_explicit_paper_only_and_never_reaches_the_execution_engine(self) -> None:
        active = policy()
        self.assertEqual("ACTIVE", active.status)
        self.assertEqual(tuple(sorted(NA)), active.not_applicable)
        with self.assertRaisesRegex(AccountPolicyError, "never_feed_the_execution_risk_engine"):
            active.risk_policy()
        with self.assertRaisesRegex(AccountPolicyError, "cannot_be_not_applicable"):
            policy(daily_loss_limit=NOT_APPLICABLE_V1)
        with self.assertRaisesRegex(AccountPolicyError, "all_set_or_all_not_applicable"):
            policy(maximum_per_trade_loss="50")
        prop = AccountContextV1("fixture-prop", AccountKindV1.PROP_PAPER, "Prop", "USDT")
        with self.assertRaisesRegex(AccountPolicyError, "cannot_be_not_applicable"):
            AccountPolicyV1(prop, {"maximum_event_risk": NOT_APPLICABLE_V1})
        missing = {k: v for k, v in VALUES.items() if k != "maximum_spread_fraction"}
        self.assertIn("MISSING_OWNER_MAXIMUM_SPREAD_FRACTION_OR_11",
                      AccountPolicyV1(PAPER, missing, "o", "2026-10-09").unresolved)  # absent is never NA


class LedgerTests(unittest.TestCase):
    def test_a_round_trip_books_money_from_fixed_notional_sizing(self) -> None:
        out = ledger([fill("a", 0, 0, 1, "100"), fill("b", 10, 1, 0, "110")])
        self.assertEqual(["APPROVED_FILLED", "APPROVED_FILLED"], [o["decision"] for o in out["orders"]])
        self.assertEqual("10", out["orders"][0]["legs"][0]["quantity"])  # 1000 / 100
        self.assertEqual({"GROSS": "10100"}, out["equity_by_scenario"])
        self.assertEqual("PAPER_ACCOUNT_SIMULATION_NOT_EXECUTION_AUTHORITY", out["claim"])
        self.assertEqual([], out["open_positions"])

    def test_an_unobserved_fill_changes_nothing(self) -> None:
        out = ledger([fill("a", 0, 0, 1), fill("b", 5, 1, 0, status="FILL_BAR_NOT_OBSERVED"),
                      fill("c", 9, 0, 1, "105")])
        self.assertEqual(["APPROVED_FILLED", "NOT_FILLED", "NO_ORDER_ALREADY_AT_TARGET"],
                         [o["decision"] for o in out["orders"]])
        self.assertEqual(1, len(out["open_positions"]))

    def test_a_disallowed_symbol_is_rejected_and_the_account_stays_flat(self) -> None:
        out = ledger([fill("a", 0, 0, 1, symbol="SOLUSDT"), fill("b", 3, 1, 0, symbol="SOLUSDT")])
        self.assertEqual(["REJECTED", "NO_ORDER_ALREADY_AT_TARGET"], [o["decision"] for o in out["orders"]])
        self.assertEqual({"SYMBOL_NOT_ALLOWED": 1}, out["rejections_by_reason"])

    def test_drawdown_blocks_new_risk_but_never_a_close(self) -> None:
        out = ledger([fill("a", 0, 0, 1, "100"), fill("b", 10, 1, -1, "80"), fill("c", 20, -1, 1, "90")])
        # Long 10 @100 -> flip at 80 (-200 realized, drawdown 200 >= 150 only after the fill) -> the
        # opening of the second flip is checked against equity known at its decision.
        decisions = [o["decision"] for o in out["orders"]]
        self.assertEqual("APPROVED_FILLED", decisions[0])
        self.assertIn(decisions[2], {"OPENING_REJECTED_CLOSE_FILLED"})
        self.assertIn("DRAWDOWN_LIMIT_REACHED", out["orders"][2]["reasons"])
        self.assertEqual([], [p for p in out["open_positions"] if p["units"] == 1])

    def test_a_numeric_limit_without_evidence_fails_closed(self) -> None:
        out = ledger([fill("a", 0, 0, 1)], p=policy(maximum_spread_fraction="0.001"))
        self.assertEqual("REJECTED", out["orders"][0]["decision"])
        self.assertIn("SPREAD_EVIDENCE_UNAVAILABLE_IN_PAPER_V1", out["orders"][0]["reasons"])

    def test_stale_market_knowledge_is_refused(self) -> None:
        out = ledger([fill("a", 0, 0, 1)], age_s=120)
        self.assertIn("STALE_MARKET_DATA", out["orders"][0]["reasons"])

    def test_order_and_leverage_limits(self) -> None:
        out = ledger([fill("a", 0, 0, 1)], p=policy(maximum_order_notional="500"))
        self.assertIn("ORDER_NOTIONAL_LIMIT", out["orders"][0]["reasons"])
        out = ledger([fill("a", 0, 0, 1)], p=policy(maximum_leverage="0.05"))
        self.assertIn("LEVERAGE_LIMIT", out["orders"][0]["reasons"])

    def test_costs_per_declared_scenario_and_the_severe_one_drives_risk(self) -> None:
        cost = CostPolicyV1(
            fee_schedule=FeeScheduleV1("BYBIT", "USDT_PERPETUAL", "FIXTURE", "1", "5", "test", "2026-10-09",
                                       "test fixture, not a real schedule"),
            slippage_scenarios=(SlippageScenarioV1("fixture-low", "1", "fixture"),
                                SlippageScenarioV1("fixture-high", "5", "fixture")))
        out = ledger([fill("a", 0, 0, 1, "100"), fill("b", 10, 1, 0, "110")], cost=cost)
        self.assertEqual("fixture-high", out["risk_scenario"])
        low, high = Decimal(out["equity_by_scenario"]["fixture-low"]), Decimal(out["equity_by_scenario"]["fixture-high"])
        self.assertEqual(Decimal("10100") - (Decimal("1000") + Decimal("1100")) * Decimal("6") / 10_000, low)
        self.assertLess(high, low)

    def test_deterministic_regardless_of_input_order(self) -> None:
        fills = [fill("a", 0, 0, 1, "100"), fill("b", 10, 1, 0, "110"), fill("c", 20, 0, -1, "105")]
        shuffled = fills[:]
        random.Random(7).shuffle(shuffled)
        self.assertEqual(ledger(fills)["content_hash"], ledger(shuffled)["content_hash"])

    def test_an_inactive_policy_is_refused(self) -> None:
        with self.assertRaisesRegex(PaperAccountError, "active_policy"):
            build_paper_account_ledger_v1(AccountPolicyV1(PAPER, {}), [], {}, gross_cost_policy_v1(), as_of=AS_OF)


if __name__ == "__main__":
    unittest.main()
