"""Paper risk/sizing assessment under the stored account policy (offline, synthetic values).

Every number here is a TEST FIXTURE, never an owner decision (OR-11).
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_platform.account_policy_v1 import (
    NOT_APPLICABLE_FIELDS_V1,
    NOT_APPLICABLE_V1,
    AccountContextV1,
    AccountKindV1,
    AccountPolicyV1,
)
from trade_platform.paper_risk_assessment_v1 import (
    ALLOWED,
    BLOCKED,
    CONTROLS_V1,
    UNRESOLVED,
    ExecutionContextV1,
    PaperRiskAssessmentError,
    PaperRiskRequestV1,
    assess_paper_risk_v1,
    paper_state_from_fills_v1,
    read_paper_risk_assessment_v1,
    resolve_account_policy_v1,
)
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

T0_DT = datetime(2026, 9, 2, tzinfo=UTC)
T0 = int(T0_DT.timestamp()) * 1_000_000
AS_OF = T0_DT + timedelta(hours=1)
RECORDED = datetime(2026, 9, 1, 10, tzinfo=UTC)
PAPER = AccountContextV1("fixture-paper", AccountKindV1.PERSONAL_PAPER, "Fixture", "USDT")
NA = {name: NOT_APPLICABLE_V1 for name in NOT_APPLICABLE_FIELDS_V1}
VALUES: dict[str, Any] = {
    "paper_starting_capital": "10000", "paper_order_notional": "1000", "maximum_leverage": "3",
    "daily_loss_limit": "500", "maximum_drawdown_limit": "150", "allowed_symbols": ["BTCUSDT"],
    "maximum_order_notional": "2000", "maximum_position_notional": "2500", "maximum_daily_order_notional": "100000",
    "max_market_age_seconds": 60, **NA,
}
NUMERIC_EVIDENCE = {
    "minimum_data_quality": "0.9", "maximum_spread_fraction": "0.003", "maximum_event_risk": "0.4",
    "maximum_expected_slippage_fraction": "0.002", "maximum_per_trade_loss": "20",
    "maximum_stop_distance_fraction": "0.03", "stop_gap_buffer_fraction": "0.01",
}


def policy(account: AccountContextV1 = PAPER, approved: tuple[str | None, str | None] = ("owner", "2026-09-01"),
           **overrides: Any) -> AccountPolicyV1:
    return AccountPolicyV1(account, {**VALUES, **overrides}, *approved)


def row(p: AccountPolicyV1, recorded: datetime = RECORDED, **tamper: Any) -> tuple[Any, ...]:
    identity = {**p.identity(), **tamper.pop("identity", {})}
    base = {"policy_version_id": p.policy_version_id, "content_hash": p.content_hash,
            "account_id": p.account.account_id, "status": p.status, "identity": json.dumps(identity),
            "recorded_at": recorded, **tamper}
    return tuple(base[k] for k in ("policy_version_id", "content_hash", "account_id", "status", "identity",
                                   "recorded_at"))


ACCOUNT_ROW = ("PERSONAL_PAPER", "Fixture", "USDT")


def fill(sig: str, minute: int, frm: int, to: int, price: str = "100", *, trial: str = "t1",
         symbol: str = "BTCUSDT") -> dict[str, Any]:
    decided = T0 + minute * 60_000_000 + 1_500_000
    bar = T0 + (minute + 1) * 60_000_000
    return {"signal_id": sig, "study_id": "s1", "trial_id": trial, "symbol": symbol, "target_from": frm,
            "target_to": to, "decided_at_micros": decided, "fill_bar_open_micros": bar, "status": "FILLED",
            "fill_price": price, "evidence": {"open_market_knowledge_micros": bar + 300_000}}


def signals(fills: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for f in fills:
        out[f["signal_id"]] = {"evidence": {"complete_market_knowledge_micros": f["decided_at_micros"] - 1_000_000}}
        f["signal_content_hash"] = identity_hash_v1(out[f["signal_id"]])
    return out


def request(target: int = 1, *, trial: str = "t1", symbol: str = "BTCUSDT", account_id: str = "fixture-paper",
            context: ExecutionContextV1 = ExecutionContextV1.PAPER_INCUBATION, expected: str | None = None,
            knowledge_age: timedelta | None = timedelta(seconds=1), price: Decimal | None = Decimal("100"),
            price_age: timedelta = timedelta(seconds=1), as_of: datetime = AS_OF) -> PaperRiskRequestV1:
    return PaperRiskRequestV1(account_id, "s1", trial, symbol, target, as_of, context, expected,
                              None if knowledge_age is None else as_of - knowledge_age,
                              price, None if price is None else as_of - price_age)


def assess(p: AccountPolicyV1 | None = None, fills: list[dict[str, Any]] | None = None,
           req: PaperRiskRequestV1 | None = None, rows: list[tuple[Any, ...]] | None = None) -> dict[str, Any]:
    p = p or policy()
    req = req or request()
    rows = [row(p)] if rows is None else rows
    resolution = resolve_account_policy_v1(req.account_id, ACCOUNT_ROW if p.account.kind.value == "PERSONAL_PAPER"
                                           else (p.account.kind.value, p.account.display_name,
                                                 p.account.base_currency),
                                           rows, as_of=req.as_of, expected_policy_version_id=req.expected_policy_version_id)
    state = None
    if resolution.usable and resolution.policy is not None and p.account.kind is AccountKindV1.PERSONAL_PAPER:
        fills = fills or []
        state = paper_state_from_fills_v1(resolution.policy, fills, signals(fills), gross_cost_policy_v1(),
                                          as_of=req.as_of)
    return assess_paper_risk_v1(req, resolution, state)


def control(out: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in out["controls"] if c["control"] == name)


class VerdictTests(unittest.TestCase):
    def test_active_policy_and_complete_evidence_allow_a_sized_paper_opening(self) -> None:
        out = assess()
        self.assertEqual(ALLOWED, out["verdict"], out["blocking_reasons"] + out["unresolved_reasons"])
        self.assertEqual("OPEN", out["order_action"])
        self.assertEqual("10", out["sizing"]["open_quantity"])
        self.assertEqual("1000", out["sizing"]["order_notional"])
        self.assertEqual("USDT", out["sizing"]["money_unit"])
        self.assertFalse(out["execution_authority"])
        self.assertEqual(set(CONTROLS_V1), {c["control"] for c in out["controls"]})
        short = assess(req=request(-1))
        self.assertEqual("-10", short["sizing"]["open_quantity"])

    def test_assessment_is_deterministic_and_content_addressed(self) -> None:
        a, b = assess(), assess(p=AccountPolicyV1(PAPER, dict(reversed(list(VALUES.items()))), "owner", "2026-09-01"))
        self.assertEqual(a["content_hash"], b["content_hash"])
        self.assertEqual(a["assessment_id"], b["assessment_id"])
        identity = {k: v for k, v in a.items() if k not in ("content_hash", "assessment_id")}
        self.assertEqual(a["content_hash"], identity_hash_v1(identity))
        self.assertNotEqual(a["content_hash"], assess(req=request(-1))["content_hash"])

    def test_an_unconfigured_account_is_unresolved_and_never_sized(self) -> None:
        empty = AccountPolicyV1(PAPER, {})
        out = assess(p=empty)
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertIn("ACCOUNT_POLICY_UNCONFIGURED", out["unresolved_reasons"])
        self.assertIn("MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11", out["unresolved_reasons"])
        self.assertIn("MISSING_OWNER_APPROVAL_OR_11", out["unresolved_reasons"])
        self.assertIsNone(out["sizing"])
        self.assertEqual(["VERDICT_UNRESOLVED"], out["sizing_absent_reasons"])
        self.assertEqual({"NOT_EVALUATED"}, {c["status"] for c in out["controls"]})
        partial = {k: v for k, v in VALUES.items() if k != "daily_loss_limit"}
        out = assess(p=AccountPolicyV1(PAPER, partial, "owner", "2026-09-01"))
        self.assertEqual(["ACCOUNT_POLICY_UNCONFIGURED", "MISSING_OWNER_DAILY_LOSS_LIMIT_OR_11"],
                         out["unresolved_reasons"])
        self.assertIsNone(out["sizing"])

    def test_an_unapproved_complete_policy_is_unresolved(self) -> None:
        out = assess(p=policy(approved=(None, None)))
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertEqual(["ACCOUNT_POLICY_UNCONFIGURED", "MISSING_OWNER_APPROVAL_OR_11"], out["unresolved_reasons"])
        self.assertIsNone(out["sizing"])

    def test_no_policy_or_no_account_is_unresolved(self) -> None:
        req = request()
        none = resolve_account_policy_v1("fixture-paper", ACCOUNT_ROW, [], as_of=AS_OF)
        self.assertEqual(["NO_ACCOUNT_POLICY_RECORDED"],
                         assess_paper_risk_v1(req, none, None)["unresolved_reasons"])
        missing = resolve_account_policy_v1("fixture-paper", None, [], as_of=AS_OF)
        self.assertEqual(UNRESOLVED, assess_paper_risk_v1(req, missing, None)["verdict"])
        self.assertEqual(("ACCOUNT_NOT_REGISTERED",), missing.unresolved)

    def test_a_prop_account_is_unresolved(self) -> None:
        prop = AccountContextV1("fixture-prop", AccountKindV1.PROP_PAPER, "Prop", "USDT")
        numeric = {**VALUES, **NUMERIC_EVIDENCE, "prop_firm": "fixture", "prop_daily_loss_limit": "1",
                   "prop_max_trailing_drawdown": "1", "prop_profit_target": "1", "prop_min_trading_days": 1}
        p = AccountPolicyV1(prop, numeric, "owner", "2026-09-01")
        out = assess(p=p, req=request(account_id="fixture-prop"))
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertIn("PROP_ACCOUNT_RULES_NOT_ENFORCED_BY_PAPER_V1", out["unresolved_reasons"])
        self.assertIsNone(out["sizing"])

    def test_missing_paper_state_is_unresolved(self) -> None:
        resolution = resolve_account_policy_v1("fixture-paper", ACCOUNT_ROW, [row(policy())], as_of=AS_OF)
        out = assess_paper_risk_v1(request(), resolution, None)
        self.assertEqual(["PAPER_STATE_MISSING"], out["unresolved_reasons"])
        out = assess_paper_risk_v1(request(), resolution, None, state_error="PAPER_STATE_UNDERIVABLE")
        self.assertEqual(BLOCKED, out["verdict"])


class StalenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = policy()
        self.new = policy(paper_order_notional="500")
        self.rows = [row(self.old), row(self.new, RECORDED + timedelta(minutes=5))]

    def test_the_latest_version_known_at_as_of_is_authoritative(self) -> None:
        out = assess(p=self.new, rows=self.rows)
        self.assertEqual(ALLOWED, out["verdict"])
        self.assertEqual(str(self.new.policy_version_id), out["resolution"]["policy_version_id"])
        self.assertEqual("5", out["sizing"]["open_quantity"])

    def test_a_superseded_expected_version_is_blocked(self) -> None:
        out = assess(p=self.new, rows=self.rows, req=request(expected=str(self.old.policy_version_id)))
        self.assertEqual(BLOCKED, out["verdict"])
        self.assertEqual(["STALE_POLICY_VERSION_SUPERSEDED"], out["blocking_reasons"])
        self.assertIsNone(out["sizing"])
        self.assertEqual(ALLOWED, assess(p=self.new, rows=self.rows,
                                         req=request(expected=str(self.new.policy_version_id)))["verdict"])

    def test_a_version_recorded_after_as_of_is_not_known(self) -> None:
        as_of = RECORDED + timedelta(minutes=2)
        out = assess(p=self.old, rows=self.rows, req=request(as_of=as_of))
        self.assertEqual(str(self.old.policy_version_id), out["resolution"]["policy_version_id"])
        out = assess(p=self.old, rows=self.rows, req=request(as_of=as_of, expected=str(self.new.policy_version_id)))
        self.assertEqual(["POLICY_VERSION_NOT_KNOWN_AT_AS_OF"], out["blocking_reasons"])
        out = assess(p=self.old, rows=self.rows, req=request(expected="00000000-0000-0000-0000-000000000000"))
        self.assertEqual(["POLICY_VERSION_NOT_RECORDED"], out["blocking_reasons"])
        out = assess(p=self.old, rows=self.rows, req=request(as_of=RECORDED - timedelta(minutes=1)))
        self.assertEqual(["NO_ACCOUNT_POLICY_KNOWN_AT_AS_OF"], out["unresolved_reasons"])

    def test_a_newer_unconfigured_version_is_never_skipped_for_an_older_active_one(self) -> None:
        draft = AccountPolicyV1(PAPER, {"paper_starting_capital": "10000"})
        out = assess(p=self.old, rows=[row(self.old), row(draft, RECORDED + timedelta(minutes=5))])
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertIn("ACCOUNT_POLICY_UNCONFIGURED", out["unresolved_reasons"])
        self.assertIsNone(out["sizing"])

    def test_an_approval_dated_after_as_of_is_unresolved(self) -> None:
        late = policy(approved=("owner", "2026-09-05"))
        out = assess(p=late)
        self.assertEqual(["POLICY_APPROVED_AFTER_AS_OF"], out["unresolved_reasons"])

    def test_two_versions_at_one_instant_are_ambiguous(self) -> None:
        out = assess(p=self.new, rows=[row(self.old), row(self.new)])
        self.assertIn("AMBIGUOUS_LATEST_POLICY_VERSION", out["blocking_reasons"])


class MismatchTests(unittest.TestCase):
    def test_stored_rows_that_do_not_rederive_block(self) -> None:
        p = policy()
        cases = {
            "STORED_POLICY_CONTENT_HASH_MISMATCH": row(p, content_hash="0" * 64),
            "POLICY_VERSION_ID_MISMATCH": row(p, policy_version_id="00000000-0000-0000-0000-000000000000"),
            "STORED_POLICY_STATUS_MISMATCH": row(p, status="UNCONFIGURED"),
            "STORED_POLICY_DOES_NOT_REPRODUCE": row(p, identity={"status": "UNCONFIGURED"}),
            "STORED_POLICY_DOES_NOT_REBUILD": row(p, identity={"values": {**VALUES, "maximum_leverage": "-1"}}),
            "POLICY_ACCOUNT_MISMATCH": row(p, account_id="other-paper"),
        }
        for reason, bad in cases.items():
            with self.subTest(reason=reason):
                out = assess(p=p, rows=[bad])
                self.assertEqual(BLOCKED, out["verdict"])
                self.assertEqual([reason], out["blocking_reasons"])
                self.assertIsNone(out["sizing"])
        other = policy(AccountContextV1("other-paper", AccountKindV1.PERSONAL_PAPER, "Fixture", "USDT"))
        out = assess(p=p, rows=[row(other, account_id="fixture-paper")])
        self.assertEqual(["POLICY_ACCOUNT_MISMATCH"], out["blocking_reasons"])

    def test_an_old_tampered_version_blocks_even_behind_a_good_one(self) -> None:
        old, new = policy(), policy(paper_order_notional="500")
        out = assess(p=new, rows=[row(old, content_hash="0" * 64), row(new, RECORDED + timedelta(minutes=5))])
        self.assertEqual(BLOCKED, out["verdict"])

    def test_request_account_and_symbol_mismatches_block(self) -> None:
        resolution = resolve_account_policy_v1("fixture-paper", ACCOUNT_ROW, [row(policy())], as_of=AS_OF)
        state = paper_state_from_fills_v1(policy(), [], {}, gross_cost_policy_v1(), as_of=AS_OF)
        out = assess_paper_risk_v1(request(account_id="other-paper"), resolution, state)
        self.assertIn("REQUEST_ACCOUNT_MISMATCH", out["blocking_reasons"])
        out = assess(req=request(symbol="ETHUSDT"))
        self.assertEqual(["SYMBOL_NOT_ALLOWED"], out["blocking_reasons"])
        out = assess(p=policy(allowed_symbols=["BTCUSD"]), req=request(symbol="BTCUSD"))
        self.assertEqual(["SYMBOL_NOT_QUOTED_IN_ACCOUNT_CURRENCY"], out["blocking_reasons"])

    def test_state_bound_to_another_policy_or_instant_blocks(self) -> None:
        resolution = resolve_account_policy_v1("fixture-paper", ACCOUNT_ROW, [row(policy())], as_of=AS_OF)
        other = paper_state_from_fills_v1(policy(paper_order_notional="500"), [], {}, gross_cost_policy_v1(),
                                          as_of=AS_OF)
        self.assertIn("STATE_POLICY_MISMATCH", assess_paper_risk_v1(request(), resolution, other)["blocking_reasons"])
        earlier = paper_state_from_fills_v1(policy(), [], {}, gross_cost_policy_v1(), as_of=AS_OF - timedelta(1))
        self.assertIn("STATE_AS_OF_MISMATCH", assess_paper_risk_v1(request(), resolution, earlier)["blocking_reasons"])

    def test_a_resolution_made_for_another_expectation_blocks(self) -> None:
        resolution = resolve_account_policy_v1("fixture-paper", ACCOUNT_ROW, [row(policy())], as_of=AS_OF)
        state = paper_state_from_fills_v1(policy(), [], {}, gross_cost_policy_v1(), as_of=AS_OF)
        out = assess_paper_risk_v1(request(expected="00000000-0000-0000-0000-000000000000"), resolution, state)
        self.assertIn("RESOLUTION_EXPECTED_VERSION_MISMATCH", out["blocking_reasons"])


class NotApplicableTests(unittest.TestCase):
    def test_owner_declared_not_applicable_controls_are_recorded_as_such_for_paper(self) -> None:
        out = assess()
        for name in NOT_APPLICABLE_FIELDS_V1:
            item = control(out, f"EVIDENCE:{name}")
            self.assertEqual("NOT_APPLICABLE", item["status"])
            self.assertEqual("OWNER_DECLARED_PERSONAL_PAPER_V1", item["evidence"]["basis"])
        self.assertEqual(sorted(NA), out["resolution"]["not_applicable_controls"])

    def test_numeric_evidence_controls_fail_closed_in_paper_v1(self) -> None:
        out = assess(p=policy(**NUMERIC_EVIDENCE))
        self.assertEqual(BLOCKED, out["verdict"])
        self.assertIn("SPREAD_EVIDENCE_UNAVAILABLE_IN_PAPER_V1", out["blocking_reasons"])
        self.assertIn("PROTECTIVE_STOP_UNAVAILABLE_STRATEGIES_EMIT_NO_STOP", out["blocking_reasons"])
        self.assertIsNone(out["sizing"])

    def test_not_applicable_never_passes_in_a_real_execution_context(self) -> None:
        out = assess(req=request(context=ExecutionContextV1.REAL_EXECUTION))
        self.assertEqual(BLOCKED, out["verdict"])
        self.assertIn("REAL_EXECUTION_NOT_AUTHORIZED_RESEARCH_ONLY", out["blocking_reasons"])
        self.assertIn("NOT_APPLICABLE_NEVER_SATISFIES_REAL_EXECUTION", out["blocking_reasons"])
        for name in NOT_APPLICABLE_FIELDS_V1:
            self.assertEqual("FAIL", control(out, f"EVIDENCE:{name}")["status"])
        self.assertIn("never_feed_the_execution_risk_engine", out["execution_risk_policy"])
        self.assertIsNone(out["sizing"])
        # A fully numeric policy builds the RiskEngine policy but real execution stays blocked.
        out = assess(p=policy(**NUMERIC_EVIDENCE), req=request(context=ExecutionContextV1.REAL_EXECUTION))
        self.assertEqual(BLOCKED, out["verdict"])
        self.assertEqual("BUILT_BUT_REAL_EXECUTION_IS_OUT_OF_SCOPE", out["execution_risk_policy"])
        # Even a close is never authorized for real execution.
        out = assess(req=request(0, context=ExecutionContextV1.REAL_EXECUTION),
                     fills=[fill("a", 0, 0, 1)])
        self.assertEqual(BLOCKED, out["verdict"])
        self.assertIsNone(out["sizing"])


class ControlTests(unittest.TestCase):
    def test_leverage_and_position_limits_use_recorded_positions(self) -> None:
        fills = [fill("a", 0, 0, 1, trial="t2"), fill("b", 1, 0, 1, trial="t3")]
        out = assess(fills=fills)
        self.assertEqual(["POSITION_NOTIONAL_LIMIT"], out["blocking_reasons"])
        self.assertEqual("3000", control(out, "POSITION_NOTIONAL")["evidence"]["projected_symbol_net_notional"])
        out = assess(p=policy(maximum_leverage="0.25", maximum_position_notional="5000"), fills=fills)
        self.assertEqual(["LEVERAGE_LIMIT"], out["blocking_reasons"])
        self.assertEqual("2000", control(out, "GROSS_LEVERAGE")["evidence"]["gross_before"])
        self.assertEqual("0.3", control(out, "GROSS_LEVERAGE")["evidence"]["leverage_after"])
        # An opposite opening nets the symbol down: within the position limit.
        self.assertEqual(ALLOWED, assess(req=request(-1), fills=fills)["verdict"])
        self.assertEqual("EVIDENCE_ONLY", control(out, "NET_EXPOSURE")["status"])

    def test_order_notional_limit(self) -> None:
        out = assess(p=policy(maximum_order_notional="999"))
        self.assertEqual(["ORDER_NOTIONAL_LIMIT"], out["blocking_reasons"])

    def test_drawdown_daily_loss_and_daily_notional_come_from_the_ledger(self) -> None:
        fills = [fill("a", 0, 0, 1, trial="t2"), fill("b", 10, 0, 1, "80", trial="t3")]
        out = assess(p=policy(maximum_position_notional="5000"), fills=fills)
        drawdown = control(out, "DRAWDOWN")
        self.assertEqual("10000", drawdown["evidence"]["peak_equity"])
        self.assertEqual("9800", drawdown["evidence"]["equity"])
        self.assertEqual(["DRAWDOWN_LIMIT_REACHED"], out["blocking_reasons"])
        out = assess(p=policy(maximum_drawdown_limit="1000", daily_loss_limit="200", maximum_position_notional="5000"), fills=fills)
        self.assertEqual(["DAILY_LOSS_LIMIT_REACHED"], out["blocking_reasons"])
        self.assertEqual("10000", control(out, "DAILY_LOSS")["evidence"]["day_start_equity"])
        out = assess(p=policy(maximum_drawdown_limit="1000", maximum_daily_order_notional="2500",
                                maximum_position_notional="5000"), fills=fills)
        self.assertEqual(["DAILY_ORDER_NOTIONAL_LIMIT"], out["blocking_reasons"])
        self.assertEqual("2000", control(out, "DAILY_ORDER_NOTIONAL")["evidence"]["day_order_notional"])

    def test_market_age(self) -> None:
        out = assess(req=request(knowledge_age=timedelta(seconds=120)))
        self.assertEqual(["STALE_MARKET_DATA"], out["blocking_reasons"])
        out = assess(req=request(knowledge_age=None))
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertEqual(["MARKET_AGE_UNASSESSABLE_SIGNAL_EVIDENCE_MISSING"], out["unresolved_reasons"])
        out = assess(req=request(knowledge_age=timedelta(seconds=-1)))
        self.assertEqual(["MARKET_KNOWLEDGE_AFTER_AS_OF"], out["blocking_reasons"])

    def test_execution_known_exactly_at_as_of_is_not_yet_state(self) -> None:
        f = fill("a", 0, 0, 1, trial="t2")
        known = datetime.fromtimestamp(f["evidence"]["open_market_knowledge_micros"] / 1_000_000, UTC)
        p = policy()
        at = paper_state_from_fills_v1(p, [f], signals([f]), gross_cost_policy_v1(), as_of=known)
        after = paper_state_from_fills_v1(p, [f], signals([f]), gross_cost_policy_v1(),
                                          as_of=known + timedelta(microseconds=1))
        self.assertEqual((), at.positions)
        self.assertEqual(1, len(at.pending))
        self.assertEqual(1, len(after.positions))


class SizingTests(unittest.TestCase):
    def test_sizing_needs_a_known_fresh_reference_price(self) -> None:
        out = assess(req=request(price=None))
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertEqual(["REFERENCE_PRICE_MISSING"], out["unresolved_reasons"])
        self.assertIsNone(out["sizing"])
        out = assess(req=request(price_age=timedelta(seconds=120)))
        self.assertEqual(["REFERENCE_PRICE_STALE"], out["blocking_reasons"])
        out = assess(req=request(price_age=timedelta(seconds=-1)))
        self.assertEqual(["REFERENCE_PRICE_KNOWN_AFTER_AS_OF"], out["blocking_reasons"])
        out = assess(req=request(price=Decimal("1e20")))
        self.assertEqual(["ZERO_QUANTITY_AFTER_ROUNDING"], out["blocking_reasons"])

    def test_quantity_rounds_toward_zero(self) -> None:
        out = assess(req=request(price=Decimal("3")))
        self.assertEqual("333.333333333333", out["sizing"]["open_quantity"])
        self.assertEqual("1000", out["sizing"]["booked_notional"])  # 999.999999999999 at the money quantum

    def test_close_and_already_at_target(self) -> None:
        fills = [fill("a", 0, 0, 1)]
        out = assess(req=request(0), fills=fills)
        self.assertEqual((ALLOWED, "CLOSE_ONLY"), (out["verdict"], out["order_action"]))
        self.assertEqual("-10", out["sizing"]["close_quantity"])
        self.assertEqual({"NOT_REQUIRED_RISK_REDUCING"}, {c["status"] for c in out["controls"]})
        out = assess(req=request(1), fills=fills)
        self.assertEqual("NO_ORDER_ALREADY_AT_TARGET", out["order_action"])
        self.assertEqual(["NO_ORDER_ALREADY_AT_TARGET"], out["sizing_absent_reasons"])
        flip = assess(req=request(-1), fills=fills)
        self.assertEqual("-10", flip["sizing"]["open_quantity"])
        self.assertEqual("-10", flip["sizing"]["close_quantity"])

    def test_request_validation(self) -> None:
        with self.assertRaises(PaperRiskAssessmentError):
            PaperRiskRequestV1("fixture-paper", "s1", "t1", "BTCUSDT", 2, AS_OF, ExecutionContextV1.PAPER_INCUBATION)
        with self.assertRaises(PaperRiskAssessmentError):
            PaperRiskRequestV1("fixture-paper", "s1", "t1", "BTCUSDT", 1, AS_OF.replace(tzinfo=None),
                               ExecutionContextV1.PAPER_INCUBATION)
        with self.assertRaises(PaperRiskAssessmentError):
            PaperRiskRequestV1("fixture-paper", "s1", "t1", "BTCUSDT", 1, AS_OF, ExecutionContextV1.PAPER_INCUBATION,
                               reference_price=100.0, reference_price_known_at=AS_OF)  # type: ignore[arg-type]


class _Cursor:
    def __init__(self, account: Any, rows: list[Any]) -> None:
        self.account, self.rows, self.sql = account, rows, ""

    def execute(self, sql: str, params: Any) -> None:
        self.sql = sql

    def fetchone(self) -> Any:
        return self.account

    def fetchall(self) -> list[Any]:
        return self.rows if "account_policy_versions" in self.sql else []


class ReadPathTests(unittest.TestCase):
    def test_read_resolves_from_stored_rows(self) -> None:
        out = read_paper_risk_assessment_v1(_Cursor(ACCOUNT_ROW, [row(policy())]), request(), cycle_id="c1")
        self.assertEqual(ALLOWED, out["verdict"])
        self.assertEqual("GROSS", out["paper_state"]["risk_scenario"])
        out = read_paper_risk_assessment_v1(_Cursor(None, []), request(), cycle_id="c1")
        self.assertEqual(["ACCOUNT_NOT_REGISTERED"], out["unresolved_reasons"])
        out = read_paper_risk_assessment_v1(_Cursor(ACCOUNT_ROW, [row(AccountPolicyV1(PAPER, {}))]), request(),
                                            cycle_id="c1")
        self.assertEqual(UNRESOLVED, out["verdict"])
        self.assertIsNone(out["paper_state"])


if __name__ == "__main__":
    unittest.main()
