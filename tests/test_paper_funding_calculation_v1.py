"""Offline calculation over real bridge/store/resolver code and synthetic evidence."""

from __future__ import annotations

import dataclasses
import shutil
import unittest
from copy import deepcopy
from datetime import timedelta
from decimal import ROUND_DOWN, localcontext
from typing import Any

from tests import test_forward_funding_price_bridge_v1 as source
from tests.test_paper_funding_evidence_v1 import (
    GROSS,
    SETTLE_8,
    T0,
    attach,
    fill_at,
    rehashed,
)
from trade_platform.paper_economics_v1 import (
    PAPER_FILL_SCHEMA_VERSION_V1,
    PaperEconomicsError,
    paper_economics_v1,
)
from trade_platform.paper_funding_calculation_v1 import (
    HYPOTHETICAL,
    PUBLISHED_RATE_HYPOTHESIS,
    STRICT_INTERIOR,
    UNIT_ENTRY_NOTIONAL,
    FundingCalculationAssumptionsV1,
    PaperFundingCalculationError,
    calculate_paper_funding_v1,
)
from trade_platform.paper_funding_evidence_v1 import PaperFundingEvidenceError
from trade_platform.paper_incubation_report_v1 import PaperIncubationError
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

HOUR = 3_600_000_000
ASSUMPTIONS = FundingCalculationAssumptionsV1(
    price_reference_kind="TRADE_PRICE_PROXY", price_convention=source.CLOSE.value,
    settlement_authority=PUBLISHED_RATE_HYPOTHESIS, holding_at_settlement=STRICT_INTERIOR,
    position_value_basis=UNIT_ENTRY_NOTIONAL)


def hashed_fills(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in deepcopy(items):
        item["schema_version"] = PAPER_FILL_SCHEMA_VERSION_V1
        item["evidence"] = {"bar_open_micros": item["fill_bar_open_micros"],
                            "tier": "T4_FIRST_PARTY_CAPTURE_FORWARD_UNSEALED",
                            "open_market_knowledge_micros": item["fill_bar_open_micros"] + 1_000_000,
                            "trade_manifest_hash": "a" * 64, "session_id": str(source.SESSION),
                            "first_trade_reference": "fixture-first-trade"}
        out.append({"identity": item, "content_hash": identity_hash_v1(item)})
    return out


def trip(*, direction: int = 1, entry: int = T0 + HOUR, exit: int = T0 + 10 * HOUR,
         entry_price: str = "100") -> list[dict[str, Any]]:
    opened = fill_at(entry, target_to=direction, price=entry_price)
    return [opened, fill_at(exit, previous=opened, target_from=direction, target_to=0, price="101")]


class CalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root_index = len(source._ROOTS)
        self.bridge = source._run(datasets=[source._dataset(source._close_bars(close_price="125"))])

    def tearDown(self) -> None:
        for root in source._ROOTS[self.root_index:]:
            shutil.rmtree(root, ignore_errors=True)
        del source._ROOTS[self.root_index:]

    def project(self, *, items: list[dict[str, Any]] | None = None, bridge: Any = None,
                assumptions: Any = ASSUMPTIONS, **kwargs: Any) -> Any:
        return calculate_paper_funding_v1(
            hashed_fills(trip() if items is None else items), GROSS,
            funding=attach(self.bridge if bridge is None else bridge), assumptions=assumptions,
            as_of=kwargs.pop("as_of", source.AS_OF), **kwargs)

    def test_negative_rate_pays_longs_and_charges_shorts_with_entry_normalization(self) -> None:
        for direction, expected in ((1, "0.000062500000"), (-1, "-0.000062500000")):
            with self.subTest(direction=direction):
                result = self.project(items=trip(direction=direction))
                self.assertEqual(expected, result.payload["hypothetical_funding_return_sum"])
                (row,) = result.payload["settlements"]
                self.assertEqual(("-0.00005", "SHORTS_PAY_LONGS", direction),
                                 (row["published_rate"], row["payer_direction"], row["position_direction"]))
                self.assertEqual(self.bridge.content_hash, row["bridge_content_hash"])
                self.assertEqual(SETTLE_8 // 1000, row["settled_at_ms"])
                self.assertEqual("125.000000000000000000", row["trade_proxy"]["price"])
                self.assertEqual("UNAVAILABLE", row["venue_mark_evidence_unused"]["evidence_class"])
                self.assertEqual(identity_hash_v1(result.payload), result.content_hash)

    def test_positive_rate_payer_direction_and_zero_rate(self) -> None:
        for rate, long_expected, short_expected in (("0.0001", "-0.000125000000", "0.000125000000"),
                                                   ("0", "0.000000000000", "0.000000000000")):
            rates = {**source.RATES, source.D0 + 8 * source._H: rate}
            bridge = source._run([source._observe(rates)],
                                 datasets=[source._dataset(source._close_bars(close_price="125"))])
            for direction, expected in ((1, long_expected), (-1, short_expected)):
                with self.subTest(rate=rate, direction=direction):
                    value = self.project(items=trip(direction=direction), bridge=bridge)
                    # Decimal signed zero has no economic meaning; the serialized result canonicalizes it.
                    self.assertEqual(expected, value.payload["hypothetical_funding_return_sum"])

    def test_half_even_rounding_is_per_settlement_and_independent_of_global_context(self) -> None:
        for price, expected in (("1.000001", "0.000000500000"), ("1.000003", "0.000000500002")):
            bridge = source._run(datasets=[source._dataset(source._close_bars(close_price=price))])
            fills, funding = hashed_fills(trip()), attach(bridge)
            normal = calculate_paper_funding_v1(fills, GROSS, funding=funding,
                                                assumptions=ASSUMPTIONS, as_of=source.AS_OF)
            with localcontext() as context:
                context.prec, context.rounding = 6, ROUND_DOWN
                low_precision = calculate_paper_funding_v1(fills, GROSS, funding=funding,
                                                           assumptions=ASSUMPTIONS, as_of=source.AS_OF)
            self.assertEqual(expected, normal.payload["hypothetical_funding_return_sum"])
            self.assertEqual(normal, low_precision)

    def test_all_settlements_are_retained_and_total_is_not_compounded(self) -> None:
        result = self.project(items=trip(exit=T0 + 20 * HOUR))
        self.assertEqual(2, len(result.payload["settlements"]))
        self.assertEqual("-0.000093750000", result.payload["hypothetical_funding_return_sum"])

    def test_total_sums_already_quantized_rows(self) -> None:
        rates = {at: "-0.00005" for at in source.RATES}
        bridge = source._run([source._observe(rates)],
                             datasets=[source._dataset(source._close_bars(close_price="1.000001"))])
        result = self.project(items=trip(exit=T0 + 20 * HOUR), bridge=bridge)
        self.assertEqual(["0.000000500000"] * 2,
                         [row["signed_return"] for row in result.payload["settlements"]])
        self.assertEqual("0.000001000000", result.payload["hypothetical_funding_return_sum"])

    def test_reproducibility_input_order_and_nested_mutation_refusal(self) -> None:
        fills = hashed_fills(trip())
        funding = attach(self.bridge)
        args = {"funding": funding, "assumptions": ASSUMPTIONS, "as_of": source.AS_OF}
        one = calculate_paper_funding_v1(fills, GROSS, **args)
        two = calculate_paper_funding_v1(list(reversed(fills)), GROSS, **args)
        self.assertEqual(one, two)
        funding.bridges[0][1]["settlements"][1]["rate"] = "0.9"
        with self.assertRaisesRegex(PaperFundingEvidenceError, "does_not_rederive"):
            calculate_paper_funding_v1(fills, GROSS, **args)

    def test_fill_hash_tampering_refuses(self) -> None:
        fills = hashed_fills(trip())
        fills[0]["identity"]["fill_price"] = "1"
        with self.assertRaisesRegex(PaperEconomicsError, "does_not_rederive"):
            calculate_paper_funding_v1(fills, GROSS, funding=attach(self.bridge),
                                       assumptions=ASSUMPTIONS, as_of=source.AS_OF)

    def test_price_payer_and_clock_source_mismatches_refuse_even_if_rehashed(self) -> None:
        changes = [lambda p: p["settlements"][1].update(payer_direction="LONGS_PAY_SHORTS"),
                   lambda p: p["settlements"][1]["price"].update(evidence_kind="VENUE_PUBLISHED_MARK_PRICE"),
                   lambda p: p["settlements"][1]["price"].update(reference_hash="unverified"),
                   lambda p: p["settlements"][1]["price"].update(price="125.0000000000000000001"),
                   lambda p: p["settlements"][1]["price"].update(price="1e20")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(
                    (PaperFundingCalculationError, PaperFundingEvidenceError, PaperEconomicsError)):
                self.project(bridge=rehashed(deepcopy(self.bridge), change))

    def test_fill_proof_is_required_and_must_be_causal(self) -> None:
        for proof_update in ({"tier": "T2_EVENT_TIME"}, {"bar_open_micros": SETTLE_8},
                             {"open_market_knowledge_micros": T0}, {"first_trade_reference": None}):
            fills = hashed_fills(trip())
            fills[0]["identity"]["evidence"].update(proof_update)
            fills[0]["content_hash"] = identity_hash_v1(fills[0]["identity"])
            with self.subTest(update=proof_update), self.assertRaises(PaperFundingCalculationError):
                calculate_paper_funding_v1(fills, GROSS, funding=attach(self.bridge),
                                           assumptions=ASSUMPTIONS, as_of=source.AS_OF)

    def test_entry_exit_and_reversal_at_settlement_refuse_without_double_charging(self) -> None:
        for items in (trip(entry=SETTLE_8), trip(exit=SETTLE_8)):
            with self.subTest(items=items), self.assertRaisesRegex(
                    PaperFundingCalculationError, "HOLDING_AMBIGUOUS"):
                self.project(items=items)
        items = trip(exit=SETTLE_8)
        items[-1]["target_to"] = -1
        items.append(fill_at(T0 + 10 * HOUR, previous=items[-1], target_from=-1, target_to=0))
        with self.assertRaisesRegex(PaperFundingCalculationError, "HOLDING_AMBIGUOUS"):
            self.project(items=items)

    def test_complete_interval_with_no_settlement_is_explicit_zero(self) -> None:
        result = self.project(items=trip(exit=T0 + 2 * HOUR))
        self.assertEqual([], result.payload["settlements"])
        self.assertEqual("0.000000000000", result.payload["hypothetical_funding_return_sum"])

    def test_missing_funding_interval_and_incomplete_coverage_refuse(self) -> None:
        cases = [(trip(), source._run(interval=None)),
                 (trip(exit=T0 + 26 * HOUR), self.bridge)]
        for items, bridge in cases:
            with self.subTest(items=items), self.assertRaisesRegex(
                    PaperFundingCalculationError, "NOT_COVERED"):
                self.project(items=items, bridge=bridge, as_of=source.DAY0 + timedelta(hours=27))

    def test_missing_held_proxy_refuses(self) -> None:
        bridge = source._run(datasets=[source._dataset([b for b in source._close_bars()
                                                       if b.bar_open_micros != SETTLE_8 - 60_000_000])])
        with self.assertRaisesRegex(PaperFundingCalculationError, "PRICE_EVIDENCE_REFUSED"):
            self.project(bridge=bridge)

    def test_broken_inconsistent_open_and_unfilled_positions_refuse(self) -> None:
        broken = trip()
        broken[-1]["previous_signal_id"] = "foreign-runner"
        inconsistent = trip()
        inconsistent[-1]["target_from"] = -1
        skipped = trip()
        skipped[0]["target_from"] = 1
        unfilled = trip()
        unfilled[-1]["status"] = "BAR_NOT_OBSERVED"
        for items in (broken, inconsistent, skipped, unfilled, trip()[:1]):
            with self.subTest(items=items), self.assertRaises((PaperFundingCalculationError, PaperIncubationError)):
                self.project(items=items)

    def test_same_bar_multiple_fills_are_ambiguous(self) -> None:
        items = trip()
        items[-1]["decided_at_micros"] = items[0]["decided_at_micros"]
        items[-1]["fill_bar_open_micros"] = items[0]["fill_bar_open_micros"]
        with self.assertRaisesRegex(PaperFundingCalculationError, "not_strictly_ordered"):
            self.project(items=items)

    def test_candidate_symbol_and_validation_binding_cannot_be_reassigned(self) -> None:
        for field in ("study_id", "trial_id", "symbol"):
            items = trip()
            items[0][field] = "ETHUSDT" if field == "symbol" else "foreign-candidate"
            with self.subTest(field=field), self.assertRaisesRegex(PaperFundingCalculationError, "another_funding_candidate"):
                self.project(items=items)
        with self.assertRaisesRegex(PaperIncubationError, "another_incubation_binding"):
            calculate_paper_funding_v1(hashed_fills(trip()), GROSS,
                                       funding=attach(self.bridge, validation_hash="f" * 64),
                                       assumptions=ASSUMPTIONS, as_of=source.AS_OF)

    def test_price_convention_and_venue_mark_never_fall_back(self) -> None:
        other = dataclasses.replace(ASSUMPTIONS, price_convention=source.OPEN.value)
        with self.assertRaisesRegex(PaperFundingCalculationError, "differs_from_evidence"):
            self.project(assumptions=other)
        with self.assertRaisesRegex(PaperFundingCalculationError, "price_reference_kind"):
            dataclasses.replace(ASSUMPTIONS, price_reference_kind="VENUE_PUBLISHED_MARK_PRICE")
        bridge = source._run(marks=[source._marks()],
                             datasets=[source._dataset(source._close_bars(close_price="125"))])
        result = self.project(bridge=bridge)
        row = result.payload["settlements"][0]
        self.assertEqual("EXACT", row["venue_mark_evidence_unused"]["evidence_class"])
        self.assertIn("NOT_THE_PROVEN_SETTLEMENT_MARK", row["venue_mark_evidence_unused"]["meaning"])
        self.assertEqual(row["trade_proxy"]["price"], self.bridge.settlements[1]["price"]["price"])

    def test_future_bridge_funding_proxy_mark_and_fill_knowledge_refuse(self) -> None:
        with self.assertRaisesRegex(PaperIncubationError, "after_the_report_as_of"):
            self.project(as_of=source.AS_OF - timedelta(minutes=1))
        for part, field in ((None, "funding_knowledge_at_micros"), ("price", "knowable_at_micros"),
                            ("mark_price", "knowable_at_micros")):
            def change(identity: Any, part: Any = part, field: str = field) -> None:
                item = identity["settlements"][1]
                target = item if part is None else item[part]
                target[field] = identity["as_of_micros"] + 1
            with self.subTest(part=part), self.assertRaises(PaperFundingEvidenceError):
                self.project(bridge=rehashed(deepcopy(self.bridge), change))
        fills = hashed_fills(trip())
        fills[0]["identity"]["evidence"]["open_market_knowledge_micros"] = T0 + 30 * HOUR
        fills[0]["content_hash"] = identity_hash_v1(fills[0]["identity"])
        with self.assertRaisesRegex(PaperFundingCalculationError, "fill_knowledge"):
            calculate_paper_funding_v1(fills, GROSS, funding=attach(self.bridge),
                                       assumptions=ASSUMPTIONS, as_of=source.AS_OF)

    def test_all_declarations_are_required_and_approval_objects_cannot_grant_authority(self) -> None:
        for field in dataclasses.asdict(ASSUMPTIONS):
            for invalid in (True, "VERIFIED", ""):
                with self.subTest(field=field, value=invalid), self.assertRaises(PaperFundingCalculationError):
                    dataclasses.replace(ASSUMPTIONS, **{field: invalid})
        for invalid in (True, {"approved": True}, None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(PaperFundingCalculationError, "not_approval"):
                self.project(assumptions=invalid)
        with self.assertRaises(TypeError):
            self.project(approval=True)
        result = self.project()
        self.assertEqual(HYPOTHETICAL, result.payload["status"])
        self.assertFalse(result.payload["in_paper_net"])
        self.assertEqual({"status": "NOT_COSTABLE", "value": None}, result.payload["net"])

    def test_v1_economics_is_unchanged_and_diagnostic_cannot_be_used_as_cost_evidence(self) -> None:
        fills, funding = hashed_fills(trip()), attach(self.bridge)
        before = paper_economics_v1(fills, GROSS, as_of=source.AS_OF, funding=(funding,))
        result = calculate_paper_funding_v1(fills, GROSS, as_of=source.AS_OF,
                                           assumptions=ASSUMPTIONS, funding=funding)
        after = paper_economics_v1(fills, GROSS, as_of=source.AS_OF, funding=(funding,))
        self.assertEqual(before, after)
        self.assertEqual("NOT_COSTABLE", after.payload["candidates"][0]["net"]["status"])
        self.assertIsNone(after.payload["candidates"][0]["funding"]["value"])
        with self.assertRaises(PaperEconomicsError):
            paper_economics_v1(fills, GROSS, as_of=source.AS_OF, funding=(result,))


if __name__ == "__main__":
    unittest.main()
