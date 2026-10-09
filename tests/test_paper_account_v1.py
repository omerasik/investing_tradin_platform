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
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

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
    out = {}
    for f in fills:
        out[f["signal_id"]] = {"evidence": {"complete_market_knowledge_micros": f["decided_at_micros"] - age_s * 1_000_000}}
        f["signal_content_hash"] = identity_hash_v1(out[f["signal_id"]])  # the fill binds its signal
    return out


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


S = 1_000_000


def live(sig: str, decided: int, frm: int, to: int, price: str = "100", *, status: str = "FILLED",
         trial: str = "t1") -> dict[str, Any]:
    """Live timing: a decision ~50 ms after its bar closes, the next open known 800 ms after it opens."""
    bar = (decided // (60 * S) + 1) * 60 * S
    return {"signal_id": sig, "study_id": "s1", "trial_id": trial, "symbol": "BTCUSDT", "target_from": frm,
            "target_to": to, "decided_at_micros": decided, "fill_bar_open_micros": bar, "status": status,
            "fill_price": price if status == "FILLED" else None,
            "evidence": {"open_market_knowledge_micros": bar + 800_000}}


class PendingSameKeyTests(unittest.TestCase):
    """A decision made before the previous order of its key is known (the normal live timing)."""

    def test_a_fill_never_overwrites_a_later_decision(self) -> None:
        out = ledger([live("s1", T0 + 30 * S, 0, 1), live("s2", T0 + 60 * S + 50_000, 1, 0, "101"),
                      live("s3", T0 + 120 * S + 50_000, 0, 1, "102")])
        self.assertEqual(["APPROVED_FILLED"] * 3, [o["decision"] for o in out["orders"]])
        self.assertEqual([1], [p["units"] for p in out["open_positions"]])

    def test_a_not_filled_opening_never_overwrites_a_later_decision(self) -> None:
        out = ledger([live("s1", T0 + 30 * S, 0, 1, status="FILL_BAR_NOT_OBSERVED"),
                      live("s2", T0 + 60 * S + 50_000, 1, -1), live("s3", T0 + 120 * S + 50_000, -1, 0)])
        self.assertEqual(["NOT_FILLED", "APPROVED_FILLED", "APPROVED_FILLED"], [o["decision"] for o in out["orders"]])
        self.assertEqual([], out["open_positions"])

    def test_a_rejected_flip_closes_a_still_pending_opening(self) -> None:
        out = ledger([live("s1", T0 + 30 * S, 0, 1), live("s2", T0 + 60 * S + 50_000, 1, -1, "101")],
                     p=policy(maximum_daily_order_notional="1500"))
        self.assertEqual(["APPROVED_FILLED", "OPENING_REJECTED_CLOSE_FILLED"], [o["decision"] for o in out["orders"]])
        self.assertIn("DAILY_ORDER_NOTIONAL_LIMIT", out["orders"][1]["reasons"])
        self.assertEqual([], out["open_positions"])

    def test_two_orders_on_one_fill_bar_execute_in_decision_order(self) -> None:
        for first, second in (("z", "a"), ("a", "z")):
            out = ledger([live(first, T0 + 10 * S, 0, 1), live(second, T0 + 20 * S, 1, 0)])
            self.assertEqual([], out["open_positions"], (first, second))
            self.assertEqual("APPROVED_FILLED", {o["signal_id"]: o for o in out["orders"]}[second]["decision"])

    def test_the_daily_notional_counts_the_close_of_a_pending_opening(self) -> None:
        out = ledger([live("s1", T0 + 30 * S, 0, 1), live("s2", T0 + 60 * S + 50_000, 1, -1)],
                     p=policy(maximum_daily_order_notional="2500"))
        self.assertIn("DAILY_ORDER_NOTIONAL_LIMIT", out["orders"][1]["reasons"])  # 1000 + 1000 + 1000 > 2500
        self.assertEqual([], out["open_positions"])

    def test_the_drawdown_peak_follows_marks_between_orders(self) -> None:
        out = ledger([live("a", T0 + 30 * S, 0, 1, "100", trial="t1"),
                      live("x", T0 + 300 * S, 1, 0, "130", trial="t2"), live("y", T0 + 420 * S, 1, 0, "100", trial="t2"),
                      live("c", T0 + 600 * S, 0, 1, "100", trial="t3")])  # peak 10300, now 10000: 300 > 150
        self.assertIn("DRAWDOWN_LIMIT_REACHED", {o["signal_id"]: o for o in out["orders"]}["c"]["reasons"])

    def test_a_signal_with_two_fills_is_refused(self) -> None:
        first, second = live("a", T0 + 30 * S, 0, 1), live("a", T0 + 90 * S, 1, 0)
        sig = signals(first)
        second["signal_content_hash"] = first["signal_content_hash"]
        with self.assertRaisesRegex(PaperAccountError, "more_than_one_fill"):
            build_paper_account_ledger_v1(policy(), [first, second], sig, gross_cost_policy_v1(), as_of=AS_OF)


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

    def test_no_decision_sees_a_fill_price_before_it_is_known(self) -> None:
        # A (trial t1) is long 10 @100 and decides flat; its fill at 50 is known only after the next bar
        # opens. B (trial t2) decides to open 1 s after A's decision: it must not see A's -500.
        a_open = fill("a", 0, 0, 1, "100", trial="t1")
        a_close = fill("b", 10, 1, 0, "50", trial="t1")
        b_open = fill("c", 10, 0, 1, "60", trial="t2")
        b_open["decided_at_micros"] = a_close["decided_at_micros"] + 1_000_000
        out = ledger([a_open, a_close, b_open], p=policy(daily_loss_limit="100", maximum_drawdown_limit="10000"))
        by_signal = {o["signal_id"]: o for o in out["orders"]}
        self.assertEqual("APPROVED_FILLED", by_signal["c"]["decision"], by_signal["c"])
        # After A's fill is known, a later opening is blocked by the daily loss.
        later = fill("d", 20, 0, -1, "55", trial="t3")
        out = ledger([a_open, a_close, b_open, later], p=policy(daily_loss_limit="100", maximum_drawdown_limit="10000"))
        self.assertIn("DAILY_LOSS_LIMIT_REACHED", {o["signal_id"]: o for o in out["orders"]}["d"]["reasons"])

    def test_an_execution_after_as_of_stays_pending(self) -> None:
        opening = fill("a", 0, 0, 1)
        out = build_paper_account_ledger_v1(policy(), [opening], signals(opening), gross_cost_policy_v1(),
                                            as_of=datetime.fromtimestamp((opening["decided_at_micros"] + 1) / 1e6, UTC))
        self.assertEqual(["PENDING_EXECUTION"], [o["decision"] for o in out["orders"]])

    def test_a_signal_that_does_not_match_its_fill_is_refused(self) -> None:
        opening = fill("a", 0, 0, 1)
        sig = signals(opening)
        sig["a"]["evidence"]["complete_market_knowledge_micros"] += 1
        with self.assertRaisesRegex(PaperAccountError, "does_not_match_its_fill"):
            build_paper_account_ledger_v1(policy(), [opening], sig, gross_cost_policy_v1(), as_of=AS_OF)

    def test_prop_accounts_are_refused_until_their_rules_are_enforced(self) -> None:
        prop = AccountContextV1("fixture-prop", AccountKindV1.PROP_PAPER, "Prop", "USDT")
        values = {k: v for k, v in VALUES.items() if k not in NA} | {
            "minimum_data_quality": "0.9", "maximum_spread_fraction": "0.01", "maximum_event_risk": "0.5",
            "maximum_expected_slippage_fraction": "0.01", "maximum_per_trade_loss": "50",
            "maximum_stop_distance_fraction": "0.05", "stop_gap_buffer_fraction": "0.1", "prop_firm": "fixture",
            "prop_daily_loss_limit": "100", "prop_max_trailing_drawdown": "200", "prop_profit_target": "300",
            "prop_min_trading_days": 5}
        with self.assertRaisesRegex(PaperAccountError, "prop_account_rules"):
            build_paper_account_ledger_v1(AccountPolicyV1(prop, values, "o", "2026-10-09"), [], {},
                                          gross_cost_policy_v1(), as_of=AS_OF)

    def test_a_quantity_that_rounds_to_zero_opens_nothing(self) -> None:
        out = ledger([fill("a", 0, 0, 1, "1e30"), fill("b", 5, 1, 0, "1e30")])
        self.assertEqual("OPENING_REFUSED_ZERO_QUANTITY_AFTER_ROUNDING", out["orders"][0]["decision"])
        self.assertIn("ZERO_QUANTITY_AFTER_ROUNDING", out["orders"][0]["reasons"])
        self.assertEqual([], out["open_positions"])
        self.assertEqual("NO_ORDER_ALREADY_AT_TARGET", out["orders"][1]["decision"])

    def test_a_fill_price_without_its_knowledge_time_is_refused(self) -> None:
        opening = fill("a", 0, 0, 1)
        opening["evidence"] = None
        with self.assertRaisesRegex(PaperAccountError, "without_its_market_knowledge_time"):
            ledger([opening])
        # A non-fill with no bar at all is known only once its minute has ended.
        unobserved = fill("b", 0, 0, 1, status="FILL_BAR_NOT_OBSERVED")
        unobserved["evidence"] = None
        cutoff = datetime.fromtimestamp((unobserved["fill_bar_open_micros"] + 59_000_000) / 1e6, UTC)
        out = build_paper_account_ledger_v1(policy(), [unobserved], signals(unobserved), gross_cost_policy_v1(),
                                            as_of=cutoff)
        self.assertEqual(["PENDING_EXECUTION"], [o["decision"] for o in out["orders"]])
        self.assertEqual(["NOT_FILLED"], [o["decision"] for o in ledger([unobserved])["orders"]])

    def test_money_never_mixes_currencies(self) -> None:
        euro = AccountContextV1("fixture-eur", AccountKindV1.PERSONAL_PAPER, "Fixture", "EUR")
        out = ledger([fill("a", 0, 0, 1)], p=AccountPolicyV1(euro, VALUES, "fixture-owner", "2026-10-09"))
        self.assertIn("SYMBOL_NOT_QUOTED_IN_ACCOUNT_CURRENCY", out["orders"][0]["reasons"])
        self.assertEqual("USDT", ledger([fill("a", 0, 0, 1)])["money_unit"])

    def test_a_stored_cost_policy_missing_a_field_is_refused_not_defaulted(self) -> None:
        from trade_platform.paper_account_v1 import cost_policy_from_payload_v1

        payload = dict(gross_cost_policy_v1().policy().payload)
        self.assertEqual(payload, cost_policy_from_payload_v1(payload).policy().payload)
        del payload["fill_liquidity"]
        with self.assertRaisesRegex(PaperAccountError, "does_not_rebuild"):
            cost_policy_from_payload_v1(payload)

    def test_an_opened_packet_without_its_cost_policy_never_falls_back_to_gross(self) -> None:
        from trade_platform.paper_account_v1 import cycle_cost_policy_v1

        class Cursor:
            def __init__(self, rows: list[Any]) -> None:
                self.rows = rows

            def execute(self, *_: Any) -> None:
                return None

            def fetchall(self) -> list[Any]:
                return self.rows

        self.assertIsNone(cycle_cost_policy_v1(Cursor([]), "cycle-x").fee_schedule)  # no opening: gross
        with self.assertRaisesRegex(PaperAccountError, "without_its_cost_policy"):
            cycle_cost_policy_v1(Cursor([({"cost_policy": None},)]), "cycle-x")
        with self.assertRaisesRegex(PaperAccountError, "does_not_rebuild"):
            cycle_cost_policy_v1(Cursor([({"cost_policy": {"venue_fees": {"bogus": 1}}},)]), "cycle-x")


if __name__ == "__main__":
    unittest.main()
