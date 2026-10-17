"""OR-6 F3 forward -- funding evidence in the paper incubation report: auditable, never a cost.

Offline. Bridges are built by the real
:func:`~trade_platform.forward_funding_price_bridge_v1.bridge_forward_funding_prices_v1`
over the bridge tests' synthetic fixtures (window 2026-06-01, settlements at
00/08/16 UTC, last-trade convention); fills are hand-built fill identities.
"""

from __future__ import annotations

import dataclasses
import subprocess  # nosec B404 - runs this interpreter on a fixed snippet
import sys
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from tests.test_forward_funding_price_bridge_v1 import (
    _H,
    AS_OF,
    D0,
    END,
    OPEN,
    _close_bars,
    _dataset,
    _open_bars,
    _run,
)
from tests.test_paper_incubation_report_v1 import GROSS, STUDY, TRIAL, bind
from trade_platform import bybit_forward_settled_funding_v1 as forward_funding
from trade_platform import first_party_t4_forward_bars_v1 as t4_bars
from trade_platform import forward_funding_price_bridge_v1 as bridge
from trade_platform import paper_funding_evidence_v1 as evidence
from trade_platform.paper_funding_evidence_v1 import (
    AT_FILL_INSTANT_HOLDING_AMBIGUOUS,
    CHAIN_BROKEN_HOLDING,
    CHARGE_NOT_CALCULATED_V1,
    COVERED,
    FILLS_SKIPPED_UNKNOWN_POSITION,
    HELD_AT_SETTLEMENT,
    HOLDING_CHAIN_BROKEN,
    HOLDING_CLOSED_BY_FILL,
    HOLDING_NOT_COVERED,
    HOLDING_NOT_PROVEN_CHAIN_BROKEN,
    HOLDING_OPEN_AT_AS_OF,
    MISSING_VERIFIED_FEE_SCHEDULE,
    NO_HOLDING_INTERVAL,
    NOT_COVERED,
    OPEN_POSITION_AT_AS_OF,
    SETTLEMENT_AT_FILL_INSTANT,
    SETTLEMENT_PRICE_REFUSED,
    STATE_ECONOMICALLY_COSTABLE_RESERVED,
    STATE_EVIDENCE_COMPLETE,
    STATE_EVIDENCE_LINKED,
    STATE_NO_FUNDING_EVIDENCE,
    UNRESOLVED_OWNER_DECISION_SETTLEMENT_AUTHORITY,
    CandidateFundingEvidenceV1,
    PaperFundingEvidenceError,
    candidate_funding_evidence_v1,
)
from trade_platform.paper_incubation_report_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    FILL_STATUS_FILLED,
    PaperIncubationError,
    incubation_report_v1,
)
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MINUTE_US = 60_000_000
T0 = D0 * 1000  # 2026-06-01T00:00Z in micros
SETTLE_8 = T0 + 8 * _H * 1000


def fill_at(open_micros: int, *, previous: dict[str, Any] | None = None, target_from: int = 0,
            target_to: int = 1, status: str = FILL_STATUS_FILLED, price: str = "100",
            symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {"signal_id": str(uuid4()), "study_id": STUDY, "trial_id": TRIAL, "symbol": symbol, "status": status,
            "target_from": target_from, "target_to": target_to,
            "previous_signal_id": None if previous is None else previous["signal_id"],
            "decided_at_micros": open_micros - MINUTE_US // 2, "fill_bar_open_micros": open_micros,
            "fill_price": price if status == FILL_STATUS_FILLED else None,
            "cost_policy_hash": GROSS.policy().content_hash}


def round_trip(entry_micros: int, exit_micros: int, *, exit_price: str = "101") -> list[dict[str, Any]]:
    entry = fill_at(entry_micros)
    return [entry, fill_at(exit_micros, previous=entry, target_from=1, target_to=0, price=exit_price)]


def attach(*bridges: Any, symbol: str = "BTCUSDT", validation_hash: str | None = None) -> CandidateFundingEvidenceV1:
    return candidate_funding_evidence_v1(study_id=STUDY, trial_id=TRIAL, symbol=symbol, bridges=list(bridges),
                                         validation_hash=validation_hash)


def report(fills: list[dict[str, Any]], *funding: CandidateFundingEvidenceV1, as_of: Any = AS_OF,
           **kwargs: Any) -> dict[str, Any]:
    return incubation_report_v1(fills, GROSS, as_of=as_of, funding=list(funding), **kwargs)


def section(fills: list[dict[str, Any]], *funding: CandidateFundingEvidenceV1, **kwargs: Any) -> dict[str, Any]:
    (candidate,) = report(fills, *funding, **kwargs)["candidates"]
    return candidate["funding_evidence"]


def rehashed(source: bridge.ForwardFundingPriceBridgeV1, change: Any) -> bridge.ForwardFundingPriceBridgeV1:
    """A tampered copy whose content hash is recomputed (so only the consistency checks can catch it)."""
    identity = dict(source.identity)
    identity["settlements"] = [dict(item) for item in source.identity["settlements"]]
    change(identity)
    return bridge.ForwardFundingPriceBridgeV1(identity, identity_hash_v1(identity))


class StateTests(unittest.TestCase):
    def test_a_closed_covered_holding_is_evidence_complete_and_still_not_costable(self) -> None:
        fills = round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000)
        result = section(fills, attach(_run()))
        self.assertEqual(result["state"], STATE_EVIDENCE_COMPLETE)
        self.assertEqual(result["completeness_blockers"], [])
        self.assertFalse(result["costable"])
        self.assertEqual(result["charge"], "NOT_CALCULATED")
        (holding,) = result["holding_intervals"]
        self.assertEqual((holding["end_kind"], holding["coverage"], holding["settlements"]),
                         (HOLDING_CLOSED_BY_FILL, COVERED, [SETTLE_8 // 1000]))
        (rate,) = result["settlement_rates"]
        self.assertEqual((rate["relation"], rate["rate"], rate["payer_direction"], rate["position_direction"]),
                         (HELD_AT_SETTLEMENT, "-0.00005", "SHORTS_PAY_LONGS", 1))
        self.assertNotIn("amount", rate)
        (price,) = result["price_references"]
        self.assertEqual(price["trade_proxy"]["evidence_kind"], "TRADE_PRICE_PROXY")
        self.assertEqual(price["mark_evidence_class"], "UNAVAILABLE")
        (provenance,) = result["provenance"]["settlements"]
        self.assertEqual(provenance["bridge_content_hash"], _run().content_hash)
        self.assertEqual(result["settlements_outside_holdings"], 2)
        for blocker in (CHARGE_NOT_CALCULATED_V1, UNRESOLVED_OWNER_DECISION_SETTLEMENT_AUTHORITY,
                        MISSING_VERIFIED_FEE_SCHEDULE, "UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION",
                        "FORWARD_FUNDING_ACQUISITION:UNRESOLVED_OWNER_AUTHORIZATION"):
            self.assertIn(blocker, result["economic_blockers"])

    def test_the_section_never_changes_returns_or_the_funding_flag(self) -> None:
        fills = round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000)
        without, with_evidence = report(fills), report(fills, attach(_run()))
        (plain,), (linked,) = without["candidates"], with_evidence["candidates"]
        self.assertEqual(plain["funding_evidence"]["state"], STATE_NO_FUNDING_EVIDENCE)
        strip = lambda candidate: {k: v for k, v in candidate.items() if k != "funding_evidence"}
        self.assertEqual(strip(plain), strip(linked))
        self.assertEqual(linked["gross_return_sum"], "0.010000000000")
        self.assertEqual((linked["funding"], linked["cost_complete"]), ("NOT_MODELLED", False))
        self.assertEqual((with_evidence["funding"], with_evidence["cost_complete"]), ("NOT_MODELLED", False))
        self.assertEqual(with_evidence["funding_evidence"]["charge"], "NOT_CALCULATED")
        self.assertFalse(with_evidence["funding_evidence"]["changes_returns"])

    def test_economically_costable_is_unreachable(self) -> None:
        scenarios = [
            section(round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000), attach(_run())),
            section([fill_at(T0 + 1 * _H * 1000)], attach(_run())),
            section(round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000)),
        ]
        for result in scenarios:
            self.assertNotEqual(result["state"], STATE_ECONOMICALLY_COSTABLE_RESERVED)
            self.assertFalse(result["costable"])
            self.assertIn(UNRESOLVED_OWNER_DECISION_SETTLEMENT_AUTHORITY, result["economic_blockers"])
            self.assertIn(CHARGE_NOT_CALCULATED_V1, result["economic_blockers"])

    def test_an_open_position_is_linked_not_complete(self) -> None:
        result = section([fill_at(T0 + 1 * _H * 1000)], attach(_run()))
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)
        (holding,) = result["holding_intervals"]
        as_of_micros = (AS_OF - _EPOCH) // timedelta(microseconds=1)
        self.assertEqual((holding["end_kind"], holding["end_micros"]), (HOLDING_OPEN_AT_AS_OF, as_of_micros))
        self.assertIn(OPEN_POSITION_AT_AS_OF, result["completeness_blockers"])
        self.assertIn(HOLDING_NOT_COVERED, result["completeness_blockers"])

    def test_a_fill_at_the_settlement_instant_is_ambiguous_never_assumed(self) -> None:
        result = section(round_trip(SETTLE_8, T0 + 10 * _H * 1000), attach(_run()))
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)
        self.assertEqual([r["relation"] for r in result["settlement_rates"]], [AT_FILL_INSTANT_HOLDING_AMBIGUOUS])
        self.assertIn(SETTLEMENT_AT_FILL_INSTANT, result["completeness_blockers"])

    def test_a_holding_beyond_the_complete_window_is_not_covered_and_nothing_is_invented(self) -> None:
        fills = round_trip(T0 + 17 * _H * 1000, END * 1000 + 30 * MINUTE_US)
        result = section(fills, attach(_run()))
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)
        (holding,) = result["holding_intervals"]
        self.assertEqual((holding["coverage"], holding["settlements"]), (NOT_COVERED, []))
        self.assertEqual(result["settlement_rates"], [])
        self.assertIn(HOLDING_NOT_COVERED, result["completeness_blockers"])

    def test_a_refused_price_at_a_held_settlement_blocks_completeness(self) -> None:
        bars = [bar for bar in _close_bars() if bar.bar_open_micros != SETTLE_8 - MINUTE_US]
        refused = _run(datasets=[_dataset(bars)])
        self.assertEqual(refused.status, bridge.REFUSED_PRICE_EVIDENCE)
        result = section(round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000), attach(refused))
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)
        self.assertEqual(result["completeness_blockers"], [SETTLEMENT_PRICE_REFUSED])
        (price,) = result["price_references"]
        self.assertIsNone(price["trade_proxy"])
        self.assertEqual(price["price_refusal"], "no_proven_bar_for_the_minute")

    def test_an_incomplete_funding_window_grants_no_coverage(self) -> None:
        incomplete = _run(interval=None)
        self.assertEqual(incomplete.status, bridge.REFUSED_FUNDING_EVIDENCE)
        result = section(round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000), attach(incomplete))
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)
        self.assertEqual(result["holding_intervals"][0]["coverage"], NOT_COVERED)
        self.assertNotEqual(result["provenance"]["bridges"][0]["funding_window"]["status"], "COMPLETE")

    def test_a_broken_chain_leaves_the_holding_unproven(self) -> None:
        entry = fill_at(T0 + 1 * _H * 1000)
        restart = fill_at(T0 + 10 * _H * 1000, target_from=0, target_to=1)  # no previous link: a restart
        result = section([entry, restart], attach(_run()))
        kinds = [h["end_kind"] for h in result["holding_intervals"]]
        self.assertEqual(kinds, [HOLDING_CHAIN_BROKEN, HOLDING_OPEN_AT_AS_OF])
        self.assertEqual(result["settlement_rates"][0]["relation"], HOLDING_NOT_PROVEN_CHAIN_BROKEN)
        self.assertIn(CHAIN_BROKEN_HOLDING, result["completeness_blockers"])

    def test_an_unfilled_exit_breaks_the_holding_too(self) -> None:
        entry = fill_at(T0 + 1 * _H * 1000)
        missed = fill_at(T0 + 10 * _H * 1000, previous=entry, target_from=1, target_to=0,
                         status=FILL_STATUS_BAR_NOT_OBSERVED)
        result = section([entry, missed], attach(_run()))
        (holding,) = result["holding_intervals"]
        self.assertEqual((holding["end_kind"], holding["end_micros"]),
                         (HOLDING_CHAIN_BROKEN, missed["decided_at_micros"]))

    def test_no_holding_and_skipped_fills_are_never_complete(self) -> None:
        skipped = fill_at(T0 + 1 * _H * 1000, target_from=1, target_to=0)
        result = section([skipped], attach(_run()))
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)
        self.assertEqual(result["completeness_blockers"], [FILLS_SKIPPED_UNKNOWN_POSITION, NO_HOLDING_INTERVAL])



class PointInTimeTests(unittest.TestCase):
    def test_evidence_known_after_the_report_as_of_is_refused(self) -> None:
        fills = round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000)
        with self.assertRaisesRegex(PaperIncubationError, "funding_evidence_known_after_the_report_as_of"):
            report(fills, attach(_run()), as_of=AS_OF - timedelta(minutes=1))

    def test_the_section_is_deterministic(self) -> None:
        fills = round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000)
        first, second = section(fills, attach(_run())), section(fills, attach(_run()))
        self.assertEqual(first, second)
        self.assertEqual(first["evidence_hash"], identity_hash_v1({k: v for k, v in first.items()
                                                                   if k != "evidence_hash"}))

    def test_a_settlement_known_after_its_bridge_as_of_is_refused(self) -> None:
        def late(identity: dict[str, Any]) -> None:
            identity["settlements"][1]["funding_knowledge_at_micros"] = identity["as_of_micros"] + 1
        with self.assertRaisesRegex(PaperFundingEvidenceError, "knowledge_outside"):
            attach(rehashed(_run(), late))


class FailClosedTests(unittest.TestCase):
    def test_a_tampered_bridge_does_not_rederive(self) -> None:
        source = _run()
        identity = dict(source.identity, status=bridge.EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED,
                        start_ms=D0 - 1)
        with self.assertRaisesRegex(PaperFundingEvidenceError, "does_not_rederive"):
            attach(dataclasses.replace(source, identity=identity))

    def test_rehashed_inconsistencies_are_refused(self) -> None:
        cases = {
            "contract_is_not_the_v1": lambda i: i.update(bridge_contract_hash="0" * 64),
            "schema_version_unknown": lambda i: i.update(schema_version="forward-funding-price-bridge-v1"),
            "payer_direction_contradicts": lambda i: i["settlements"][0].update(rate="-0.0001"),
            "outside_its_bridge_window": lambda i: i.update(end_ms_exclusive=D0 + 10 * _H),
            "bar_is_not_the_convention_minute": lambda i: i["settlements"][0].update(
                price=dict(i["settlements"][0]["price"], bar_open_micros=T0)),
            "status_contradicts_its_window": lambda i: i.update(funding_window=dict(
                i["funding_window"], status="IRREGULAR_REFUSED")),
            "without_an_observation_reference": lambda i: i["settlements"][0].update(funding_observation_id=""),
            "omits_an_unresolved_owner_decision": lambda i: i.update(unresolved_decisions=[]),
            "not_ordered_and_unique": lambda i: i.update(settlements=list(reversed(i["settlements"]))),
            "refused_settlement_must_carry": lambda i: i["settlements"][0].update(
                status=bridge.PRICE_EVIDENCE_REFUSED),
        }
        for code, change in cases.items():
            with self.subTest(code), self.assertRaisesRegex(PaperFundingEvidenceError, code):
                attach(rehashed(_run(), change))

    def test_mismatched_identities_are_refused(self) -> None:
        fills = round_trip(T0 + 1 * _H * 1000, T0 + 10 * _H * 1000)
        with self.assertRaisesRegex(PaperFundingEvidenceError, "not_the_candidate_symbol"):
            attach(_run(), symbol="ETHUSDT")
        with self.assertRaisesRegex(PaperFundingEvidenceError, "outside_the_universe"):
            attach(_run(), symbol="DOGEUSDT")
        other = candidate_funding_evidence_v1(study_id=STUDY, trial_id=str(uuid4()), symbol="BTCUSDT",
                                              bridges=[_run()])
        with self.assertRaisesRegex(PaperIncubationError, "not_in_the_report"):
            report(fills, other)
        with self.assertRaisesRegex(PaperIncubationError, "another_candidate"):
            report([fill_at(T0 + 1 * _H * 1000, symbol="ETHUSDT")], attach(_run()))
        with self.assertRaisesRegex(PaperIncubationError, "another_incubation_binding"):
            report(fills, attach(_run(), validation_hash="f" * 64))
        with self.assertRaisesRegex(PaperIncubationError, "one_per_candidate"):
            report(fills, attach(_run()), attach(_run()))

    def test_a_bound_candidate_checks_its_validation_identity(self) -> None:
        binding = bind()
        since = binding.incubating_since_micros
        fills = round_trip(since + MINUTE_US, since + 10 * MINUTE_US)
        as_of = AS_OF + timedelta(days=200)
        with self.assertRaisesRegex(PaperIncubationError, "another_incubation_binding"):
            report(fills, attach(_run(), validation_hash="f" * 64), as_of=as_of, bindings=(binding,))
        result = section(fills, attach(_run(), validation_hash=binding.validation_hash), as_of=as_of,
                         bindings=(binding,))
        self.assertEqual(result["candidate"]["validation_hash"], binding.validation_hash)
        self.assertEqual(result["state"], STATE_EVIDENCE_LINKED)  # September holding, June window
        self.assertEqual(result["holding_intervals"][0]["coverage"], NOT_COVERED)

    def test_overlapping_duplicate_or_mixed_bridges_are_refused(self) -> None:
        with self.assertRaisesRegex(PaperFundingEvidenceError, "supplied_twice"):
            attach(_run(), _run())
        with self.assertRaisesRegex(PaperFundingEvidenceError, "windows_overlap"):
            attach(_run(), _run(as_of=AS_OF + timedelta(hours=1)))
        with self.assertRaisesRegex(PaperFundingEvidenceError, "mix_price_conventions"):
            attach(_run(), _run(datasets=[_dataset(_open_bars())], convention=OPEN))
        with self.assertRaisesRegex(PaperFundingEvidenceError, "at_least_one_bridge"):
            attach()
        with self.assertRaisesRegex(PaperFundingEvidenceError, "must_be_a_forward_funding_price_bridge"):
            attach(object())

    def test_evidence_is_issued_only_by_its_constructor(self) -> None:
        with self.assertRaisesRegex(PaperFundingEvidenceError, "issued_only"):
            CandidateFundingEvidenceV1(STUDY, TRIAL, "BTCUSDT", None, ())
        with self.assertRaisesRegex(PaperIncubationError, "issued_only"):
            report(round_trip(T0 + _H * 1000, T0 + 2 * _H * 1000), "not evidence")  # type: ignore[arg-type]


class MirrorTests(unittest.TestCase):
    def test_mirrored_constants_equal_their_sources(self) -> None:
        self.assertEqual(evidence.BRIDGE_SCHEMA_VERSION_V1, bridge.FUNDING_PRICE_BRIDGE_SCHEMA_VERSION_V1)
        self.assertEqual(evidence.BRIDGE_CONTRACT_HASH_V1, bridge.BRIDGE_CONTRACT_HASH_V1)
        self.assertEqual(evidence.SETTLEMENT_EVIDENCE_BOUND, bridge.EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED)
        self.assertEqual(evidence.SETTLEMENT_PRICE_EVIDENCE_REFUSED, bridge.PRICE_EVIDENCE_REFUSED)
        self.assertEqual(set(evidence.BRIDGE_STATUSES), {
            bridge.REFUSED_FUNDING_EVIDENCE, bridge.REFUSED_PRICE_EVIDENCE,
            bridge.EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED})
        self.assertNotIn(bridge.CONFIRMED, evidence.BRIDGE_STATUSES)
        self.assertEqual(evidence.TRADE_PRICE_PROXY, bridge.TRADE_PRICE_PROXY)
        self.assertEqual(evidence.VENUE_PUBLISHED_MARK_PRICE, bridge.VENUE_PUBLISHED_MARK_PRICE)
        self.assertEqual(evidence.MARK_EVIDENCE_CLASSES, bridge.MARK_EVIDENCE_CLASSES_V1)
        self.assertEqual(evidence.PAYER_DIRECTIONS, (bridge.LONGS_PAY_SHORTS, bridge.SHORTS_PAY_LONGS,
                                                     bridge.ZERO_RATE_NO_TRANSFER))
        self.assertEqual({evidence.CONVENTION_FIRST_TRADE_AT_INSTANT, evidence.CONVENTION_LAST_TRADE_ENDING_AT_INSTANT},
                         {item.value for item in t4_bars.T4PriceConventionV1})
        self.assertEqual(evidence.CANONICAL_SYMBOLS, forward_funding.CANONICAL_SYMBOLS_V1)
        self.assertEqual(evidence.FUNDING_WINDOW_COMPLETE, forward_funding.COMPLETE)
        self.assertEqual(evidence.UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION,
                         bridge.UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION)
        for symbol in forward_funding.CANONICAL_SYMBOLS_V1:
            self.assertEqual(evidence.canonical_key_v1(symbol),
                             forward_funding.canonical_symbol_identity_v1(symbol)["canonical_key"])

    def test_the_report_stays_import_light(self) -> None:
        code = ("import sys, trade_platform.paper_incubation_report_v1; "
                "heavy = [m for m in ('numpy', 'polars', 'duckdb', 'trade_platform.forward_funding_price_bridge_v1')"
                " if m in sys.modules]; print(heavy); sys.exit(1 if heavy else 0)")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # nosec
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
