"""Tests for the read-only paper economics projection (``paper_economics_v1``).

Fills are hand-built fill identities (the report's fixtures plus the fill schema
version), wrapped with their content hash. Funding evidence reuses the forward
funding bridge fixtures of the funding evidence tests.
"""

from __future__ import annotations

import dataclasses
import subprocess  # nosec B404 - runs this interpreter on a fixed snippet
import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from tests.test_forward_funding_price_bridge_v1 import AS_OF as BRIDGE_AS_OF
from tests.test_forward_funding_price_bridge_v1 import D0, _run
from tests.test_paper_funding_evidence_v1 import attach
from trade_platform import paper_economics_v1 as economics
from trade_platform import paper_incubation_v1
from trade_platform.paper_economics_v1 import (
    FEE_NO_VERIFIED_SCHEDULE,
    FEE_NOT_OWNER_APPROVED,
    FUNDING_NEVER_A_CHARGE_V1,
    INCOMPLETE_CHAIN_BROKEN,
    INCOMPLETE_SIGNALS_NOT_FILLED,
    NET_CONFIRMED,
    NET_INCOMPLETE,
    NET_NOT_COSTABLE,
    SLIPPAGE_NO_SCENARIO,
    SLIPPAGE_NOT_OWNER_APPROVED,
    SOURCE_CALLER_PROPOSED,
    STATUS_MISSING,
    STATUS_NON_COSTABLE_EVIDENCE,
    STATUS_PROPOSED_SENSITIVITY,
    STATUS_VERIFIED,
    UNREALIZED_AVAILABLE,
    UNREALIZED_MARK_NOT_AFTER_ENTRY,
    UNREALIZED_NO_PROVEN_MARK,
    UNREALIZED_NOT_APPLICABLE_FLAT,
    UNREALIZED_POSITION_UNKNOWN,
    UNREALIZED_UNAVAILABLE,
    OwnerCostApprovalV1,
    PaperEconomicsError,
    PaperEconomicsV1,
    ProvenMarkV1,
    net_status_v1,
    paper_economics_v1,
    verify_paper_economics_v1,
)
from trade_platform.paper_incubation_report_v1 import (
    FILL_STATUS_BAR_NOT_OBSERVED,
    FILL_STATUS_FILLED,
    PACKET_STATUS_AUTHORIZED,
)
from trade_platform.strategy_lab_policies_v1 import (
    CostPolicyV1,
    CostPolicyV2,
    FeeScheduleV1,
    SlippageScenarioV1,
    gross_cost_policy_v2,
)
from trade_platform.strategy_lab_study_v1 import identity_hash_v1

STUDY = "6f1c2a52-0000-4000-8000-000000000001"
TRIAL = "6f1c2a52-0000-4000-8000-0000000000a1"
MINUTE = 60_000_000
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
SINCE_MICROS = int(SINCE.timestamp()) * 1_000_000
AS_OF = SINCE + timedelta(days=3)
GROSS = gross_cost_policy_v2()
FEES = FeeScheduleV1("bybit", "linear", "fixture-tier", "2", "5.5", "fixture-verifier", "2026-09-01",
                     "fixture-reference")
APPROVED_SCENARIO = SlippageScenarioV1("fixture-stress", "2", "fixture")
VERIFIED_POLICY = CostPolicyV2(fee_schedule=FEES, slippage_scenarios=(APPROVED_SCENARIO,))
#: The prepared, unapproved CONSERVATIVE_MEDIUM packet: 1 / 2.5 / 5 bps per side.
CONSERVATIVE_MEDIUM = (SlippageScenarioV1("conservative-medium-low", "1", "prepared OR-6 packet, unapproved"),
                       SlippageScenarioV1("conservative-medium-mid", "2.5", "prepared OR-6 packet, unapproved"),
                       SlippageScenarioV1("conservative-medium-high", "5", "prepared OR-6 packet, unapproved"))
SOURCE_HASH = "d" * 64


def hashed(identity: dict[str, Any]) -> dict[str, Any]:
    return {"identity": identity, "content_hash": identity_hash_v1(identity)}


def fill(minute: int, *, previous: dict[str, Any] | None = None, target_from: int = 0, target_to: int = 1,
         price: str = "100", status: str = FILL_STATUS_FILLED, policy: CostPolicyV1 = GROSS,
         base: int = SINCE_MICROS) -> dict[str, Any]:
    opened = base + minute * MINUTE
    return {"schema_version": economics.PAPER_FILL_SCHEMA_VERSION_V1, "signal_id": str(uuid4()),
            "study_id": STUDY, "trial_id": TRIAL, "symbol": "BTCUSDT", "status": status,
            "target_from": target_from, "target_to": target_to,
            "previous_signal_id": None if previous is None else previous["signal_id"],
            "decided_at_micros": opened - MINUTE // 2, "fill_bar_open_micros": opened,
            "fill_price": price if status == FILL_STATUS_FILLED else None,
            "cost_policy_hash": policy.policy().content_hash}


def chain(*legs: tuple[int, int, str], policy: CostPolicyV1 = GROSS, base: int = SINCE_MICROS) -> list[dict[str, Any]]:
    """Linked fills of one runner: ``(minute, target_to, price)`` legs starting flat."""
    out: list[dict[str, Any]] = []
    for minute, target_to, price in legs:
        previous = out[-1] if out else None
        out.append(fill(minute, previous=previous, target_from=0 if previous is None else previous["target_to"],
                        target_to=target_to, price=price, policy=policy, base=base))
    return out


def packet(policy: CostPolicyV1 = VERIFIED_POLICY, **overrides: Any) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "schema_version": "strategy-lab-preregistration-v1", "study_id": STUDY, "candidates": [TRIAL],
        "symbol": "BTCUSDT", "cost_policy": policy.policy().payload, "authorized_by": "owner",
        "authorized_on": "2026-09-01", "status": PACKET_STATUS_AUTHORIZED, "unresolved": [],
    }
    identity.update(overrides)
    return identity


def approval(identity: dict[str, Any] | None = None) -> OwnerCostApprovalV1:
    identity = packet() if identity is None else identity
    return OwnerCostApprovalV1(identity, identity_hash_v1(identity))


def project(fills: list[dict[str, Any]], policy: CostPolicyV1 = GROSS, **kwargs: Any) -> PaperEconomicsV1:
    return paper_economics_v1([hashed(item) for item in fills], policy, as_of=kwargs.pop("as_of", AS_OF), **kwargs)


def only(result: PaperEconomicsV1) -> dict[str, Any]:
    (candidate,) = result.payload["candidates"]
    return candidate


class IdentityTests(unittest.TestCase):
    def test_identity_is_deterministic_and_rederives(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"))
        first, second = project(fills), project(list(reversed(fills)))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.economics_id, second.economics_id)
        self.assertEqual(first.content_hash, identity_hash_v1(dict(first.payload)))
        verify_paper_economics_v1(first)

    def test_any_input_change_changes_the_identity(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"))
        base = project(fills).content_hash
        self.assertNotEqual(base, project(fills, sensitivity_scenarios=CONSERVATIVE_MEDIUM).content_hash)
        self.assertNotEqual(base, project(fills, as_of=AS_OF + timedelta(minutes=1)).content_hash)

    def test_a_tampered_result_is_refused(self) -> None:
        result = project(chain((1, 1, "100"), (5, 0, "101")))
        payload = dict(result.payload)
        payload["claim"] = "VALIDATED"
        with self.assertRaisesRegex(PaperEconomicsError, "does_not_rederive"):
            verify_paper_economics_v1(PaperEconomicsV1(payload, result.content_hash))

    def test_provenance_names_every_source(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"))
        result = project(fills)
        candidate = only(result)
        self.assertEqual(candidate["provenance"]["fill_content_hashes"],
                         sorted(identity_hash_v1(item) for item in fills))
        self.assertEqual(result.payload["cost_policy"]["slot"], GROSS.policy().slot)
        self.assertEqual(len(result.payload["incubation_report_hash"]), 64)
        self.assertEqual(candidate["funding"]["evidence_hash"], candidate["provenance"]["funding_evidence_hash"])


class TamperTests(unittest.TestCase):
    def test_a_fill_that_does_not_rederive_is_refused(self) -> None:
        good = chain((1, 1, "100"), (5, 0, "101"))
        item = hashed(good[1])
        item["identity"] = {**good[1], "fill_price": "150"}
        with self.assertRaisesRegex(PaperEconomicsError, "fill_identity_does_not_rederive"):
            paper_economics_v1([hashed(good[0]), item], GROSS, as_of=AS_OF)

    def test_a_fill_without_its_hash_or_schema_is_refused(self) -> None:
        (entry,) = chain((1, 1, "100"))
        with self.assertRaisesRegex(PaperEconomicsError, "identity_and_content_hash"):
            paper_economics_v1([entry], GROSS, as_of=AS_OF)
        foreign = {**entry, "schema_version": "other"}
        with self.assertRaisesRegex(PaperEconomicsError, "fill_schema_version_unknown"):
            paper_economics_v1([hashed(foreign)], GROSS, as_of=AS_OF)

    def test_a_price_beyond_the_column_scale_is_refused(self) -> None:
        (entry,) = chain((1, 1, "100.0000000000000000001"))
        with self.assertRaisesRegex(PaperEconomicsError, "column_scale"):
            project([entry])

    def test_a_fill_supplied_twice_or_of_another_policy_is_refused(self) -> None:
        (entry,) = chain((1, 1, "100"))
        with self.assertRaisesRegex(PaperEconomicsError, "fill_supplied_twice"):
            project([entry, entry])
        with self.assertRaisesRegex(PaperEconomicsError, "another_cost_policy"):
            project([entry], VERIFIED_POLICY)

    def test_a_tampered_or_unauthorized_approval_is_refused(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"), policy=VERIFIED_POLICY)
        tampered = approval()
        tampered = OwnerCostApprovalV1({**tampered.packet_identity, "authorized_by": "someone"}, tampered.packet_hash)
        with self.assertRaisesRegex(PaperEconomicsError, "does_not_rederive"):
            project(fills, VERIFIED_POLICY, cost_approval=tampered)
        with self.assertRaisesRegex(PaperEconomicsError, "not_authorized"):
            project(fills, VERIFIED_POLICY, cost_approval=approval(packet(status="DRAFT",
                                                                          unresolved=["X"], authorized_by=None)))
        other = CostPolicyV2(fee_schedule=FEES, slippage_scenarios=(SlippageScenarioV1("other", "9", "x"),))
        with self.assertRaisesRegex(PaperEconomicsError, "another_cost_policy"):
            project(fills, VERIFIED_POLICY, cost_approval=approval(packet(other)))

    def test_a_mark_known_after_as_of_or_off_scale_is_refused(self) -> None:
        fills = chain((1, 1, "100"))
        late = ProvenMarkV1("BTCUSDT", "101", SINCE_MICROS, int(AS_OF.timestamp() * 1_000_000) + 1, SOURCE_HASH)
        with self.assertRaisesRegex(PaperEconomicsError, "mark_known_after_the_as_of"):
            project(fills, marks=[late])
        with self.assertRaisesRegex(PaperEconomicsError, "mark_price_not_exact"):
            ProvenMarkV1("BTCUSDT", "1e-19", SINCE_MICROS, SINCE_MICROS, SOURCE_HASH)
        with self.assertRaisesRegex(PaperEconomicsError, "evidence_kind"):
            ProvenMarkV1("BTCUSDT", "101", SINCE_MICROS, SINCE_MICROS, SOURCE_HASH, "VENUE_MARK")
        with self.assertRaisesRegex(PaperEconomicsError, "not_in_the_projection"):
            project(fills, marks=[ProvenMarkV1("ETHUSDT", "1", SINCE_MICROS, SINCE_MICROS, SOURCE_HASH)])

    def test_duplicate_scenario_names_are_refused(self) -> None:
        with self.assertRaisesRegex(PaperEconomicsError, "unique"):
            project(chain((1, 1, "100")), sensitivity_scenarios=(*CONSERVATIVE_MEDIUM, CONSERVATIVE_MEDIUM[0]))


class QuantizationAndSignTests(unittest.TestCase):
    def test_long_and_short_realized_signs(self) -> None:
        long_win = only(project(chain((1, 1, "100"), (5, 0, "103"))))
        short_win = only(project(chain((1, -1, "100"), (5, 0, "97"))))
        short_loss = only(project(chain((1, -1, "100"), (5, 0, "103"))))
        self.assertEqual(long_win["gross_realized"]["value"], "0.030000000000")
        self.assertEqual(short_win["gross_realized"]["value"], "0.030000000000")
        self.assertEqual(short_loss["gross_realized"]["value"], "-0.030000000000")
        self.assertEqual(short_loss["exposure"]["round_trips"][0]["direction"], -1)

    def test_values_are_quantized_to_the_return_scale(self) -> None:
        candidate = only(project(chain((1, 1, "3"), (5, 0, "7")), sensitivity_scenarios=CONSERVATIVE_MEDIUM))
        for value in [candidate["gross_realized"]["value"], *[row["value"] for row in candidate["sensitivity_rows"]]]:
            self.assertEqual(-Decimal(value).as_tuple().exponent, 12, value)

    def test_verified_costs_are_signed_nonpositive_contributions(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"), policy=VERIFIED_POLICY)
        candidate = only(project(fills, VERIFIED_POLICY, cost_approval=approval()))
        self.assertEqual(candidate["fees"]["status"], STATUS_VERIFIED)
        self.assertEqual(candidate["fees"]["value"], "-0.001100000000")  # 5.5 bps x 2 sides
        self.assertEqual(candidate["fees"]["verification"]["source_reference"], "fixture-reference")
        self.assertEqual(candidate["slippage"]["status"], STATUS_VERIFIED)
        self.assertEqual(candidate["slippage"]["by_scenario"]["fixture-stress"]["value"], "-0.000400000000")

    def test_unrealized_requires_a_proven_mark_after_entry(self) -> None:
        fills = chain((1, -1, "100"))
        entry_at = SINCE_MICROS + MINUTE
        missing = only(project(fills))["gross_unrealized"]
        self.assertEqual((missing["status"], missing["reasons"], missing["exposure"]),
                         (UNREALIZED_UNAVAILABLE, [UNREALIZED_NO_PROVEN_MARK], -1))
        stale = ProvenMarkV1("BTCUSDT", "90", entry_at, entry_at, SOURCE_HASH)
        self.assertEqual(only(project(fills, marks=[stale]))["gross_unrealized"]["reasons"],
                         [UNREALIZED_MARK_NOT_AFTER_ENTRY])
        mark = ProvenMarkV1("BTCUSDT", "90", entry_at + MINUTE, entry_at + MINUTE + 1, SOURCE_HASH)
        marked = only(project(fills, marks=[mark]))
        self.assertEqual((marked["gross_unrealized"]["status"], marked["gross_unrealized"]["value"]),
                         (UNREALIZED_AVAILABLE, "0.100000000000"))  # short from 100, marked at 90
        self.assertEqual(marked["provenance"]["mark_content_hash"], mark.content_hash)
        self.assertEqual(marked["net"]["scope"], economics.NET_SCOPE_V1)

    def test_flat_and_unknown_positions(self) -> None:
        flat = only(project(chain((1, 1, "100"), (5, 0, "101"))))["gross_unrealized"]
        self.assertEqual(flat["status"], UNREALIZED_NOT_APPLICABLE_FLAT)
        entry = fill(1)
        broken = [entry, fill(5, previous=entry, target_from=1, target_to=0, status=FILL_STATUS_BAR_NOT_OBSERVED)]
        unknown = only(project(broken))
        self.assertEqual((unknown["gross_unrealized"]["status"], unknown["gross_unrealized"]["reasons"]),
                         (UNREALIZED_UNAVAILABLE, [UNREALIZED_POSITION_UNKNOWN]))


class CostStatusTests(unittest.TestCase):
    def test_missing_fee_makes_net_not_costable(self) -> None:
        candidate = only(project(chain((1, 1, "100"), (5, 0, "101"))))
        self.assertEqual((candidate["fees"]["status"], candidate["fees"]["reasons"], candidate["fees"]["value"]),
                         (STATUS_MISSING, [FEE_NO_VERIFIED_SCHEDULE], None))
        self.assertEqual(candidate["slippage"]["reasons"], [SLIPPAGE_NO_SCENARIO])
        self.assertEqual(candidate["net"]["status"], NET_NOT_COSTABLE)
        self.assertIsNone(candidate["net"]["by_scenario"])
        self.assertIn(f"COMPONENT_NOT_VERIFIED:fees:{FEE_NO_VERIFIED_SCHEDULE}", candidate["net"]["reasons"])
        self.assertEqual(candidate["gross_realized"]["value"], "0.010000000000")

    def test_a_fee_schedule_without_owner_approval_is_missing(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"), policy=VERIFIED_POLICY)
        candidate = only(project(fills, VERIFIED_POLICY))
        self.assertEqual((candidate["fees"]["status"], candidate["fees"]["reasons"]),
                         (STATUS_MISSING, [FEE_NOT_OWNER_APPROVED]))
        self.assertEqual(candidate["fees"]["unapproved_input"], {"taker_fee_bps": "5.5", "applied": False})
        self.assertEqual((candidate["slippage"]["status"], candidate["slippage"]["reasons"]),
                         (STATUS_MISSING, [SLIPPAGE_NOT_OWNER_APPROVED]))
        (row,) = candidate["sensitivity_rows"]
        self.assertEqual((row["name"], row["status"], row["in_net"]),
                         ("fixture-stress", STATUS_PROPOSED_SENSITIVITY, False))

    def test_approval_of_another_candidate_verifies_nothing(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"), policy=VERIFIED_POLICY)
        candidate = only(project(fills, VERIFIED_POLICY, cost_approval=approval(packet(candidates=["other"]))))
        self.assertEqual(candidate["fees"]["reasons"], [economics.COST_APPROVAL_OF_ANOTHER_CANDIDATE])
        self.assertEqual(candidate["slippage"]["status"], STATUS_MISSING)

    def test_sensitivity_rows_never_alter_any_other_figure(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"), (9, -1, "102"), (20, 0, "100"))
        plain = only(project(fills))
        rows = only(project(fills, sensitivity_scenarios=CONSERVATIVE_MEDIUM))
        self.assertEqual({k: v for k, v in rows.items() if k != "sensitivity_rows"},
                         {k: v for k, v in plain.items() if k != "sensitivity_rows"})
        self.assertEqual(plain["sensitivity_rows"], [])
        by_name = {row["name"]: row for row in rows["sensitivity_rows"]}
        self.assertEqual({name: row["value"] for name, row in by_name.items()},
                         {"conservative-medium-low": "-0.000400000000", "conservative-medium-mid": "-0.001000000000",
                          "conservative-medium-high": "-0.002000000000"})  # 4 closed sides
        self.assertTrue(all(row["status"] == STATUS_PROPOSED_SENSITIVITY and row["in_net"] is False
                            and row["source"] == SOURCE_CALLER_PROPOSED for row in by_name.values()))
        self.assertEqual(rows["slippage"]["reasons"], [SLIPPAGE_NO_SCENARIO])
        self.assertEqual(rows["net"]["status"], NET_NOT_COSTABLE)

    def test_funding_is_never_charged(self) -> None:
        base = D0 * 1000
        fills = chain((60, 1, "100"), (600, 0, "101"), base=base)  # crosses the 08:00 settlement
        candidate = only(project(fills, as_of=BRIDGE_AS_OF, funding=[attach(_run())]))
        funding = candidate["funding"]
        self.assertEqual((funding["status"], funding["value"], funding["charge"]),
                         (STATUS_NON_COSTABLE_EVIDENCE, None, "NOT_CALCULATED"))
        self.assertEqual(funding["evidence_state"], "EVIDENCE_COMPLETE")
        self.assertIn(FUNDING_NEVER_A_CHARGE_V1, funding["reasons"])
        self.assertIn("FUNDING:UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION", funding["reasons"])
        self.assertTrue(candidate["settlement_boundaries"]["funding_settlements"])
        self.assertEqual(candidate["gross_realized"]["value"], "0.010000000000")
        self.assertEqual(candidate["net"]["status"], NET_NOT_COSTABLE)

    def test_net_is_never_confirmed_in_v1_even_with_verified_fees_and_slippage(self) -> None:
        fills = chain((1, 1, "100"), (5, 0, "101"), policy=VERIFIED_POLICY)
        candidate = only(project(fills, VERIFIED_POLICY, cost_approval=approval()))
        self.assertEqual(candidate["net"]["status"], NET_NOT_COSTABLE)
        self.assertIsNone(candidate["net"]["by_scenario"])
        self.assertTrue(all(reason.startswith("COMPONENT_NOT_VERIFIED:funding:")
                            for reason in candidate["net"]["reasons"]))

    def test_an_incomplete_chain_is_reported(self) -> None:
        entry = fill(1)
        broken = [entry, fill(5, previous=entry, target_from=1, target_to=0, status=FILL_STATUS_BAR_NOT_OBSERVED)]
        net = only(project(broken))["net"]
        self.assertIn(INCOMPLETE_CHAIN_BROKEN, net["reasons"])
        self.assertIn(INCOMPLETE_SIGNALS_NOT_FILLED, net["reasons"])


class NetRuleTests(unittest.TestCase):
    VERIFIED = (STATUS_VERIFIED, ())

    def test_confirmed_only_when_every_component_is_verified_and_complete(self) -> None:
        all_verified = {"fees": self.VERIFIED, "slippage": self.VERIFIED, "funding": self.VERIFIED}
        self.assertEqual(net_status_v1(all_verified, []), (NET_CONFIRMED, []))
        self.assertEqual(net_status_v1(all_verified, ["X"]), (NET_INCOMPLETE, ["X"]))
        for name in ("fees", "slippage", "funding"):
            for status in (STATUS_MISSING, STATUS_PROPOSED_SENSITIVITY, STATUS_NON_COSTABLE_EVIDENCE):
                status_out, reasons = net_status_v1({**all_verified, name: (status, ("WHY",))}, ["X"])
                self.assertEqual(status_out, NET_NOT_COSTABLE)
                self.assertEqual(reasons, sorted([f"COMPONENT_NOT_VERIFIED:{name}:WHY", "X"]))
        self.assertEqual(net_status_v1({"fees": self.VERIFIED, "slippage": self.VERIFIED}, [])[0], NET_NOT_COSTABLE)


class MirrorTests(unittest.TestCase):
    def test_mirrors_equal_the_fill_module(self) -> None:
        self.assertEqual(economics.PAPER_FILL_SCHEMA_VERSION_V1, paper_incubation_v1.PAPER_FILL_SCHEMA_VERSION_V1)
        self.assertEqual(economics.PRICE_QUANTUM_V1, paper_incubation_v1.PRICE_QUANTUM_V1)

    def test_a_real_engine_fill_is_accepted(self) -> None:
        fake = paper_incubation_v1.PaperFillV1({"x": 1}, uuid4(), identity_hash_v1({"x": 1}))
        with self.assertRaisesRegex(PaperEconomicsError, "fill_schema_version_unknown"):
            paper_economics_v1([fake], GROSS, as_of=AS_OF)
        identity = fill(1)
        real_shaped = paper_incubation_v1.PaperFillV1(identity, uuid4(), identity_hash_v1(identity))
        self.assertEqual(len(only(paper_economics_v1([real_shaped], GROSS, as_of=AS_OF))["provenance"][
            "fill_content_hashes"]), 1)

    def test_result_is_frozen(self) -> None:
        result = project(chain((1, 1, "100")))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.content_hash = "x"  # type: ignore[misc]

    def test_the_module_stays_import_light(self) -> None:
        code = ("import sys, trade_platform.paper_economics_v1; "
                "heavy = [m for m in ('numpy', 'polars', 'pandas', 'duckdb', 'pyarrow', 'trade_platform.paper_incubation_v1')"
                " if m in sys.modules]; print(heavy); sys.exit(1 if heavy else 0)")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # nosec
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
