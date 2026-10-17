"""R10 paper economics -- a read-only, auditable projection of paper incubation P&L by component.

``RESEARCH_ONLY``, pure and import-light (no numerical libraries: the protected
API may import it). Nothing here fills, books, persists or promotes anything.

What it projects
----------------
:func:`paper_economics_v1` runs the existing
:func:`~trade_platform.paper_incubation_report_v1.incubation_report_v1` on the
caller's *hashed* fills (each fill identity must re-derive its content hash) and
splits each candidate's economics into separate components. The fill rule, the
restart-safe position chain and the gross round-trip returns are the report's,
read back unchanged; this module adds no accounting identity of its own.

Units and signs
---------------
Sizing is unit exposure until OR-11 (:data:`UNITS_V1`): every figure is a return
per unit of notional, summed over round trips, never compounded, never money.
Exposure is signed (+1 long, -1 short). Every component value is a *signed
contribution to P&L*: positive adds, negative subtracts, so a cost is never
positive. ``Decimal`` throughout, under the OR-3 authoritative context
(precision 50, half-even), quantized to :data:`RETURN_QUANTUM_V1` (the report's
return scale) and prices to :data:`PRICE_QUANTUM_V1` (``NUMERIC(38,18)``, the
fill-price column) before anything is hashed.

Components
----------
``gross_realized``
    The report's closed round trips (entry and exit fills of one known chain).
``gross_unrealized``
    Only for an open position on an intact chain *and* a :class:`ProvenMarkV1`
    of the symbol: a proven T4 bar-open first trade (the fills' own price
    class -- never a venue mark, never a later bar standing in) observed after
    the entry and known by ``as_of``. Otherwise ``UNAVAILABLE`` with its reason,
    or ``NOT_APPLICABLE_FLAT``. Never part of net.
``fees``
    ``VERIFIED`` only when the cost policy carries a verified fee schedule *and*
    an :class:`OwnerCostApprovalV1` -- an ``AUTHORIZED`` preregistration packet
    of this candidate that froze exactly this cost policy -- records the owner's
    approval (OR-6: approval is recorded only by an authorized preregistration,
    never by a cost payload). Charged at the taker rate (fills are TAKER) on the
    two sides of each closed round trip. Otherwise ``MISSING`` with reasons.
``slippage``
    ``VERIFIED`` per scenario only for the scenarios of an owner-approved cost
    policy. Unapproved scenarios -- in the cost policy, or caller-proposed
    (e.g. the prepared CONSERVATIVE_MEDIUM 1 / 2.5 / 5 bps) -- become
    ``PROPOSED_SENSITIVITY_SCENARIO`` rows (``in_net: false``) and the slippage
    component stays ``MISSING``. A sensitivity row never changes any other figure.
``funding``
    Always ``NON_COSTABLE_EVIDENCE`` in v1, value ``None``: the forward funding
    evidence section (:mod:`trade_platform.paper_funding_evidence_v1`) is
    carried with its state, evidence hash and every completeness and economic
    blocker as reason codes. It never becomes a charge while the settlement
    price convention, the settlement authority and forward acquisition are owner
    decisions nobody has made.
``net``
    Scope: closed round trips only (realized). ``CONFIRMED`` -- with one figure
    per approved slippage scenario -- only when fees, slippage and funding are
    all ``VERIFIED`` and the position chain is complete (no break, skipped,
    inconsistent or unfilled signal). A non-verified component makes it
    ``NOT_COSTABLE``; verified costs over an incomplete chain make it
    ``INCOMPLETE``. Either way no net figure is given and every reason is
    listed. Because funding is never ``VERIFIED`` in v1, v1 never confirms net
    (:func:`net_status_v1` is the rule, tested on its own).

Identity
--------
The result payload is content-addressed (:attr:`PaperEconomicsV1.content_hash`,
``economics_id``) and carries its provenance: fill content hashes, the cost
policy slot, the approval packet hash, the incubation report hash, funding
evidence hashes, mark and sensitivity-scenario hashes.
:func:`verify_paper_economics_v1` refuses a payload that does not re-derive.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .paper_funding_evidence_v1 import CandidateFundingEvidenceV1
from .paper_incubation_report_v1 import (
    FILL_STATUS_FILLED,
    PACKET_STATUS_AUTHORIZED,
    RETURN_QUANTUM_V1,
    SIZING_UNIT_EXPOSURE_PENDING_OR_11,
    IncubationBindingV1,
    IncubationResolutionV1,
    PaperIncubationError,
    _micros,
    incubation_report_v1,
)
from .strategy_lab_policies_v1 import CostPolicyV1, SlippageScenarioV1
from .strategy_lab_study_v1 import identity_hash_v1

PAPER_ECONOMICS_SCHEMA_VERSION_V1: Final = "paper-economics-v1"
#: Mirror of ``paper_incubation_v1.PAPER_FILL_SCHEMA_VERSION_V1`` (not imported: that module
#: pulls the live-signal and persistence stack). A test pins it equal.
PAPER_FILL_SCHEMA_VERSION_V1: Final = "paper-incubation-fill-v1"
#: Mirror of ``paper_incubation_v1.PRICE_QUANTUM_V1``: ``paper_incubation_fills.fill_price`` is NUMERIC(38,18).
PRICE_QUANTUM_V1: Final = Decimal("1e-18")
UNITS_V1: Final = "RETURN_PER_UNIT_NOTIONAL_SUMMED_NOT_COMPOUNDED_PENDING_OR_11"
SIGN_CONVENTION_V1: Final = "SIGNED_CONTRIBUTION_TO_PNL_POSITIVE_ADDS_COSTS_NONPOSITIVE"
NET_SCOPE_V1: Final = "CLOSED_ROUND_TRIPS_REALIZED_ONLY_UNREALIZED_EXCLUDED"
CLAIM_V1: Final = "PAPER_ECONOMICS_PROJECTION_RESEARCH_ONLY_NOT_VALIDATED"

# Cost component statuses.
STATUS_VERIFIED: Final = "VERIFIED"
STATUS_PROPOSED_SENSITIVITY: Final = "PROPOSED_SENSITIVITY_SCENARIO"
STATUS_MISSING: Final = "MISSING"
STATUS_NON_COSTABLE_EVIDENCE: Final = "NON_COSTABLE_EVIDENCE"
COMPONENT_STATUSES_V1: Final = (STATUS_VERIFIED, STATUS_PROPOSED_SENSITIVITY, STATUS_MISSING,
                                STATUS_NON_COSTABLE_EVIDENCE)
COST_COMPONENTS_V1: Final = ("fees", "slippage", "funding")

# Net statuses.
NET_CONFIRMED: Final = "CONFIRMED"
NET_NOT_COSTABLE: Final = "NOT_COSTABLE"
NET_INCOMPLETE: Final = "INCOMPLETE"
NET_STATUSES_V1: Final = (NET_CONFIRMED, NET_NOT_COSTABLE, NET_INCOMPLETE)

# Unrealized statuses.
UNREALIZED_AVAILABLE: Final = "AVAILABLE"
UNREALIZED_UNAVAILABLE: Final = "UNAVAILABLE"
UNREALIZED_NOT_APPLICABLE_FLAT: Final = "NOT_APPLICABLE_FLAT"

# Reason codes.
FEE_NO_VERIFIED_SCHEDULE: Final = "MISSING_VERIFIED_FEE_SCHEDULE_OR_6"
FEE_NOT_OWNER_APPROVED: Final = "FEE_SCHEDULE_NOT_OWNER_APPROVED_NO_AUTHORIZED_PREREGISTRATION"
COST_APPROVAL_OF_ANOTHER_CANDIDATE: Final = "COST_APPROVAL_IS_NOT_FOR_THIS_CANDIDATE"
SLIPPAGE_NOT_OWNER_APPROVED: Final = "SLIPPAGE_ENVELOPE_NOT_OWNER_APPROVED_OR_6"
SLIPPAGE_NO_SCENARIO: Final = "NO_SLIPPAGE_SCENARIO_DECLARED"
FUNDING_NEVER_A_CHARGE_V1: Final = "FUNDING_EVIDENCE_IS_NEVER_A_CHARGE_IN_V1"
FUNDING_REASON_PREFIX: Final = "FUNDING:"
INCOMPLETE_CHAIN_BROKEN: Final = "POSITION_CHAIN_BROKEN"
INCOMPLETE_FILLS_SKIPPED: Final = "FILLS_SKIPPED_AT_AN_UNKNOWN_POSITION"
INCOMPLETE_CHAIN_INCONSISTENT: Final = "POSITION_CHAIN_INCONSISTENT"
INCOMPLETE_SIGNALS_NOT_FILLED: Final = "SIGNALS_NOT_FILLED"
NET_COMPONENT_PREFIX: Final = "COMPONENT_NOT_VERIFIED:"
UNREALIZED_POSITION_UNKNOWN: Final = "POSITION_UNKNOWN_CHAIN_NOT_INTACT"
UNREALIZED_NO_PROVEN_MARK: Final = "NO_PROVEN_MARK_SUPPLIED"
UNREALIZED_MARK_NOT_AFTER_ENTRY: Final = "PROVEN_MARK_NOT_AFTER_THE_ENTRY_FILL"

#: The one mark class v1 accepts: the fills' own price class (a proven T4 bar's first trade).
MARK_KIND_T4_BAR_OPEN_FIRST_TRADE: Final = "T4_PROVEN_BAR_OPEN_FIRST_TRADE"
MARK_KINDS_V1: Final = (MARK_KIND_T4_BAR_OPEN_FIRST_TRADE,)

SOURCE_COST_POLICY_UNAPPROVED: Final = "COST_POLICY_SCENARIO_NOT_OWNER_APPROVED"
SOURCE_CALLER_PROPOSED: Final = "CALLER_PROPOSED_PREPARED_PACKET"

#: OR-3 authoritative tier (precision 50, half-even).
_CONTEXT: Final = Context(prec=50, rounding=ROUND_HALF_EVEN)
_BPS: Final = Decimal(10_000)
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.paper_economics_v1")


class PaperEconomicsError(ValueError):
    """Raised when an input cannot be projected honestly (tampered, inconsistent or look-ahead)."""


def _quantize_return(value: Decimal) -> str:
    return format(_CONTEXT.quantize(value, RETURN_QUANTUM_V1), "f")


def _exact_price(value: object, code: str) -> Decimal:
    if not isinstance(value, str):
        raise PaperEconomicsError(code)
    try:
        number = Decimal(value)
        quantized = number.quantize(PRICE_QUANTUM_V1, context=_CONTEXT)
    except InvalidOperation as error:
        raise PaperEconomicsError(code) from error
    if not number.is_finite() or quantized != number or quantized <= 0:
        raise PaperEconomicsError(code)
    return quantized


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise PaperEconomicsError(code)
    return value


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenMarkV1:
    """A proven T4 bar-open first trade of ``symbol``, used only to mark an open position.

    ``source_content_hash`` names the bar's trade evidence (e.g. its trade
    manifest hash). The caller attests the bar is proven; this module checks
    the price is exact at the fill-price column scale and the instants are
    consistent and known by the reading instant.
    """

    symbol: str
    price: str
    bar_open_micros: int
    knowable_at_micros: int
    source_content_hash: str
    evidence_kind: str = MARK_KIND_T4_BAR_OPEN_FIRST_TRADE

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperEconomicsError("mark_symbol_required")
        if self.evidence_kind not in MARK_KINDS_V1:
            raise PaperEconomicsError("mark_evidence_kind_not_a_proven_t4_bar_trade")
        object.__setattr__(self, "price", format(_exact_price(self.price, "mark_price_not_exact_at_column_scale"),
                                                 "f"))
        for name in ("bar_open_micros", "knowable_at_micros"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise PaperEconomicsError(f"mark_{name}_malformed")
        if self.knowable_at_micros < self.bar_open_micros:
            raise PaperEconomicsError("mark_known_before_its_bar_opened")
        _sha256(self.source_content_hash, "mark_source_content_hash_malformed")

    def payload(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "price": self.price, "bar_open_micros": self.bar_open_micros,
                "knowable_at_micros": self.knowable_at_micros, "source_content_hash": self.source_content_hash,
                "evidence_kind": self.evidence_kind}

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.payload())


@dataclass(frozen=True, slots=True)
class OwnerCostApprovalV1:
    """A stored ``AUTHORIZED`` preregistration packet identity and its hash.

    The only record of owner approval of an OR-6 fee schedule and slippage
    envelope. It approves the cost policy it froze, for its own study, trial
    candidates and symbol -- nothing else.
    """

    packet_identity: Mapping[str, Any]
    packet_hash: str


def _hashed_fill(item: object) -> tuple[str, Mapping[str, Any]]:
    if isinstance(item, Mapping):
        identity, content_hash = item.get("identity"), item.get("content_hash")
    else:
        identity, content_hash = getattr(item, "identity", None), getattr(item, "content_hash", None)
    if not isinstance(identity, Mapping) or not isinstance(content_hash, str):
        raise PaperEconomicsError("fill_must_carry_its_identity_and_content_hash")
    if identity_hash_v1(dict(identity)) != content_hash:
        raise PaperEconomicsError("fill_identity_does_not_rederive")
    if identity.get("schema_version") != PAPER_FILL_SCHEMA_VERSION_V1:
        raise PaperEconomicsError("fill_schema_version_unknown")
    if identity.get("status") == FILL_STATUS_FILLED:
        _exact_price(identity.get("fill_price"), "fill_price_not_exact_at_column_scale")
    return content_hash, identity


def _approved_policy(approval: OwnerCostApprovalV1 | None, cost_policy: CostPolicyV1) -> Mapping[str, Any] | None:
    """The approving packet if ``approval`` is an authorized packet of exactly this policy; refuses otherwise."""
    if approval is None:
        return None
    if not isinstance(approval, OwnerCostApprovalV1):
        raise PaperEconomicsError("cost_approval_must_be_an_owner_cost_approval")
    packet = approval.packet_identity
    if not isinstance(packet, Mapping) or identity_hash_v1(dict(packet)) != approval.packet_hash:
        raise PaperEconomicsError("cost_approval_packet_does_not_rederive")
    by, on = packet.get("authorized_by"), packet.get("authorized_on")
    if packet.get("status") != PACKET_STATUS_AUTHORIZED or packet.get("unresolved") != [] \
            or not (isinstance(by, str) and by.strip()) or not (isinstance(on, str) and on.strip()):
        raise PaperEconomicsError("cost_approval_packet_is_not_authorized")
    frozen = packet.get("cost_policy")
    if not isinstance(frozen, Mapping) or identity_hash_v1(dict(frozen)) != cost_policy.policy().content_hash:
        raise PaperEconomicsError("cost_approval_is_of_another_cost_policy")
    if cost_policy.fee_schedule is None or not cost_policy.slippage_scenarios:
        raise PaperEconomicsError("cost_approval_of_a_policy_without_a_verified_cost_basis")
    return packet


def _approves(packet: Mapping[str, Any] | None, study_id: str, trial_id: str, symbol: str) -> bool:
    if packet is None:
        return False
    return (str(packet.get("study_id")) == study_id and str(packet.get("symbol")) == symbol
            and trial_id in [str(item) for item in packet.get("candidates") or []])


# ---------------------------------------------------------------------------
# Net rule
# ---------------------------------------------------------------------------


def net_status_v1(components: Mapping[str, tuple[str, Sequence[str]]],
                  incompleteness: Sequence[str]) -> tuple[str, list[str]]:
    """``CONFIRMED`` only if every cost component is ``VERIFIED`` and the chain is complete.

    ``components`` maps each of :data:`COST_COMPONENTS_V1` to ``(status, reasons)``;
    a missing component counts as not verified. Returns the status and every reason.
    """
    reasons: list[str] = []
    for name in COST_COMPONENTS_V1:
        status, why = components.get(name, (STATUS_MISSING, ("COMPONENT_ABSENT",)))
        if status != STATUS_VERIFIED:
            reasons += [f"{NET_COMPONENT_PREFIX}{name}:{reason}" for reason in (why or (status,))]
    if reasons:
        return NET_NOT_COSTABLE, sorted(set(reasons) | set(incompleteness))
    if incompleteness:
        return NET_INCOMPLETE, sorted(set(incompleteness))
    return NET_CONFIRMED, []


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _side_cost(bps: str, sides: int) -> str:
    return _quantize_return(_CONTEXT.minus(_CONTEXT.multiply(_CONTEXT.divide(Decimal(bps), _BPS), Decimal(sides))))


def _unrealized(candidate: Mapping[str, Any], items: Sequence[Mapping[str, Any]],
                mark: ProvenMarkV1 | None) -> dict[str, Any]:
    position = candidate["open_position"]
    if position is None:
        return {"status": UNREALIZED_UNAVAILABLE, "value": None, "reasons": [UNREALIZED_POSITION_UNKNOWN],
                "exposure": None, "mark": None}
    if position == 0:
        return {"status": UNREALIZED_NOT_APPLICABLE_FLAT, "value": None, "reasons": [], "exposure": 0, "mark": None}
    # On an intact chain the last filled fill is the entry of the open position.
    entry = [item for item in items if item["status"] == FILL_STATUS_FILLED][-1]
    if int(entry["target_to"]) != position:  # pragma: no cover - the report's chain guarantees it
        raise PaperEconomicsError("open_position_is_not_the_last_fill_target")
    entry_price = _exact_price(entry["fill_price"], "fill_price_not_exact_at_column_scale")
    entry_at = int(entry["fill_bar_open_micros"])
    out: dict[str, Any] = {"exposure": position, "entry_bar_open_micros": entry_at,
                           "entry_price": format(entry_price, "f"), "mark": None}
    if mark is None:
        return {**out, "status": UNREALIZED_UNAVAILABLE, "value": None, "reasons": [UNREALIZED_NO_PROVEN_MARK]}
    out["mark"] = {**mark.payload(), "content_hash": mark.content_hash}
    if mark.bar_open_micros <= entry_at:
        return {**out, "status": UNREALIZED_UNAVAILABLE, "value": None, "reasons": [UNREALIZED_MARK_NOT_AFTER_ENTRY]}
    ratio = _CONTEXT.subtract(_CONTEXT.divide(Decimal(mark.price), entry_price), Decimal(1))
    return {**out, "status": UNREALIZED_AVAILABLE, "value": _quantize_return(_CONTEXT.multiply(Decimal(position), ratio)),
            "reasons": []}


def _candidate(candidate: Mapping[str, Any], items: Sequence[Mapping[str, Any]], hashes: Sequence[str],
               cost_policy: CostPolicyV1, unapproved: str | None, sensitivity: Sequence[SlippageScenarioV1],
               mark: ProvenMarkV1 | None) -> dict[str, Any]:
    trips = candidate["round_trips"]
    sides = int(candidate["closed_sides"])
    if sides != 2 * len(trips):  # pragma: no cover - the report's identity
        raise PaperEconomicsError("closed_sides_disagree_with_round_trips")
    gross = _CONTEXT.quantize(sum((Decimal(trip["gross_return"]) for trip in trips), Decimal(0)), RETURN_QUANTUM_V1)
    if format(gross, "f") != candidate["gross_return_sum"]:  # pragma: no cover - the report's identity
        raise PaperEconomicsError("gross_realized_disagrees_with_the_report")
    fee_schedule = cost_policy.fee_schedule
    rows: list[tuple[SlippageScenarioV1, str]] = []

    # Fees.
    fees: dict[str, Any] = {"status": STATUS_MISSING, "value": None, "reasons": [], "liquidity": "TAKER",
                            "rate_bps_per_side": None, "verification": None, "unapproved_input": None}
    if fee_schedule is None:
        fees["reasons"] = [FEE_NO_VERIFIED_SCHEDULE]
    elif unapproved is not None:
        fees["reasons"] = [unapproved]
        fees["unapproved_input"] = {"taker_fee_bps": fee_schedule.taker_fee_bps, "applied": False}
    else:
        fees.update(status=STATUS_VERIFIED, value=_side_cost(fee_schedule.taker_fee_bps, sides),
                    rate_bps_per_side=fee_schedule.taker_fee_bps,
                    verification={"verified_by": fee_schedule.verified_by, "verified_on": fee_schedule.verified_on,
                                  "source_reference": fee_schedule.source_reference,
                                  "venue": fee_schedule.venue, "product": fee_schedule.product,
                                  "tier": fee_schedule.tier})

    # Slippage: approved scenarios are verified; everything else is a labelled sensitivity row.
    slippage: dict[str, Any] = {"status": STATUS_MISSING, "by_scenario": None, "reasons": []}
    if unapproved is None:
        slippage.update(status=STATUS_VERIFIED, by_scenario={
            item.name: {"cost_bps_per_side": item.cost_bps_per_side, "value": _side_cost(item.cost_bps_per_side, sides)}
            for item in cost_policy.slippage_scenarios})
    else:
        # Depends on the bound cost policy only: proposed sensitivity rows change no other figure.
        slippage["reasons"] = [SLIPPAGE_NOT_OWNER_APPROVED if cost_policy.slippage_scenarios else SLIPPAGE_NO_SCENARIO]
        if unapproved == COST_APPROVAL_OF_ANOTHER_CANDIDATE:
            slippage["reasons"].append(unapproved)
        rows += [(item, SOURCE_COST_POLICY_UNAPPROVED) for item in cost_policy.slippage_scenarios]
    rows += [(item, SOURCE_CALLER_PROPOSED) for item in sensitivity]
    sensitivity_rows = [{"name": item.name, "status": STATUS_PROPOSED_SENSITIVITY, "source": source,
                         "cost_bps_per_side": item.cost_bps_per_side, "rationale": item.rationale,
                         "value": _side_cost(item.cost_bps_per_side, sides), "in_net": False,
                         "scenario_hash": identity_hash_v1(item.payload())}
                        for item, source in sorted(rows, key=lambda pair: pair[0].name)]

    # Funding: carried as evidence, never a charge.
    evidence = candidate["funding_evidence"]
    funding = {
        "status": STATUS_NON_COSTABLE_EVIDENCE, "value": None, "charge": "NOT_CALCULATED",
        "evidence_state": evidence["state"], "evidence_hash": evidence["evidence_hash"],
        "reasons": sorted({FUNDING_NEVER_A_CHARGE_V1} | {
            f"{FUNDING_REASON_PREFIX}{item}" for item in [*evidence["economic_blockers"],
                                                          *evidence["completeness_blockers"]]}),
    }

    incompleteness = []
    if candidate["chain_breaks"]:
        incompleteness.append(INCOMPLETE_CHAIN_BROKEN)
    if candidate["fills_skipped_unknown_position"]:
        incompleteness.append(INCOMPLETE_FILLS_SKIPPED)
    if candidate["chain_inconsistencies"]:
        incompleteness.append(INCOMPLETE_CHAIN_INCONSISTENT)
    if any(count for status, count in candidate["fills_by_status"].items() if status != FILL_STATUS_FILLED):
        incompleteness.append(INCOMPLETE_SIGNALS_NOT_FILLED)
    status, reasons = net_status_v1({"fees": (fees["status"], fees["reasons"]),
                                     "slippage": (slippage["status"], slippage["reasons"]),
                                     "funding": (funding["status"], funding["reasons"])}, incompleteness)
    by_scenario = None
    if status == NET_CONFIRMED:  # unreachable in v1 (funding is never VERIFIED); kept as the rule's other side
        by_scenario = {name: _quantize_return(_CONTEXT.add(_CONTEXT.add(gross, Decimal(fees["value"])),
                                                           Decimal(row["value"])))
                       for name, row in slippage["by_scenario"].items()}

    return {
        "study_id": candidate["study_id"], "trial_id": candidate["trial_id"], "symbol": candidate["symbol"],
        "exposure": {
            "sizing": SIZING_UNIT_EXPOSURE_PENDING_OR_11,
            "open_position_signed": candidate["open_position"],
            "closed_round_trips": len(trips),
            "closed_sides": sides,
            "round_trips": [{"direction": int(trip["direction"]), "entry_bar_open_micros": trip["entry_bar_open_micros"],
                             "exit_bar_open_micros": trip["exit_bar_open_micros"],
                             "gross_return": trip["gross_return"]} for trip in trips],
        },
        "gross_realized": {"value": format(gross, "f"), "basis": "CLOSED_ROUND_TRIPS_UNIT_EXPOSURE_FILL_TO_FILL"},
        "gross_unrealized": _unrealized(candidate, items, mark),
        "fees": fees,
        "slippage": slippage,
        "funding": funding,
        "sensitivity_rows": sensitivity_rows,
        "net": {"status": status, "scope": NET_SCOPE_V1, "by_scenario": by_scenario, "reasons": reasons},
        "settlement_boundaries": {
            "realized_through_micros": max((int(trip["exit_bar_open_micros"]) for trip in trips), default=None),
            "funding_settlements": [{"settled_at_ms": int(item["settled_at_ms"]), "relation": item["relation"],
                                     "holding_index": int(item["holding_index"]),
                                     "position_direction": int(item["position_direction"])}
                                    for item in evidence["settlement_rates"]],
        },
        "provenance": {"fill_content_hashes": sorted(hashes), "funding_evidence_hash": evidence["evidence_hash"],
                       "mark_content_hash": None if mark is None else mark.content_hash},
    }


@dataclass(frozen=True, slots=True)
class PaperEconomicsV1:
    """The projection payload and its identity. Re-check with :func:`verify_paper_economics_v1`."""

    payload: Mapping[str, Any]
    content_hash: str

    @property
    def economics_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"paper-economics:{self.content_hash}")


def verify_paper_economics_v1(result: PaperEconomicsV1) -> None:
    """Refuse a result whose payload does not re-derive its content hash."""
    if not isinstance(result, PaperEconomicsV1) or identity_hash_v1(dict(result.payload)) != result.content_hash:
        raise PaperEconomicsError("paper_economics_identity_does_not_rederive")
    if result.payload.get("schema_version") != PAPER_ECONOMICS_SCHEMA_VERSION_V1:
        raise PaperEconomicsError("paper_economics_schema_version_unknown")


def paper_economics_v1(fills: Sequence[object], cost_policy: CostPolicyV1, *, as_of: datetime,
                       cost_approval: OwnerCostApprovalV1 | None = None,
                       sensitivity_scenarios: Sequence[SlippageScenarioV1] = (),
                       marks: Sequence[ProvenMarkV1] = (),
                       funding: Sequence[CandidateFundingEvidenceV1] = (),
                       bindings: Sequence[IncubationBindingV1] = (),
                       resolution: IncubationResolutionV1 | None = None) -> PaperEconomicsV1:
    """Project per-candidate paper economics by component; never confirms an incomplete net.

    ``fills`` are hashed fills (``PaperFillV1`` or ``{"identity", "content_hash"}``);
    each must re-derive. ``cost_policy`` is the policy the fills were recorded
    under. ``cost_approval`` is the authorized preregistration that approved it
    (absent: nothing is verified). ``sensitivity_scenarios`` are proposed,
    unapproved slippage scenarios reported as labelled rows only. ``marks`` hold
    at most one proven mark per symbol, known by ``as_of``. ``funding``,
    ``bindings`` and ``resolution`` pass to the incubation report unchanged.
    """
    if not isinstance(cost_policy, CostPolicyV1):
        raise PaperEconomicsError("cost_policy_must_be_an_or6_cost_policy")
    as_of_micros = _micros(as_of)
    hashed = [_hashed_fill(item) for item in fills]
    if len({content_hash for content_hash, _ in hashed}) != len(hashed):
        raise PaperEconomicsError("fill_supplied_twice")
    packet = _approved_policy(cost_approval, cost_policy)
    names = [item.name for item in cost_policy.slippage_scenarios]
    for item in sensitivity_scenarios:
        if not isinstance(item, SlippageScenarioV1):
            raise PaperEconomicsError("sensitivity_scenario_must_be_a_slippage_scenario")
        names.append(item.name)
    if len(set(names)) != len(names):
        raise PaperEconomicsError("slippage_scenario_names_must_be_unique")
    by_symbol: dict[str, ProvenMarkV1] = {}
    for mark in marks:
        if not isinstance(mark, ProvenMarkV1):
            raise PaperEconomicsError("mark_must_be_a_proven_mark")
        if mark.symbol in by_symbol:
            raise PaperEconomicsError("one_mark_per_symbol")
        if mark.knowable_at_micros > as_of_micros:
            raise PaperEconomicsError("mark_known_after_the_as_of")
        by_symbol[mark.symbol] = mark
    try:
        report = incubation_report_v1([identity for _, identity in hashed], cost_policy, as_of=as_of,
                                      bindings=bindings, resolution=resolution, funding=funding)
    except PaperIncubationError as error:
        raise PaperEconomicsError(str(error)) from error
    groups: dict[tuple[str, str, str], list[tuple[str, Mapping[str, Any]]]] = {}
    for content_hash, identity in hashed:
        key = (str(identity["study_id"]), str(identity["trial_id"]), str(identity["symbol"]))
        groups.setdefault(key, []).append((content_hash, identity))
    candidates = []
    for candidate in report["candidates"]:
        key = (candidate["study_id"], candidate["trial_id"], candidate["symbol"])
        pairs = sorted(groups.get(key, []), key=lambda pair: (int(pair[1]["decided_at_micros"]),
                                                              str(pair[1]["signal_id"])))
        unapproved = (FEE_NOT_OWNER_APPROVED if packet is None
                      else None if _approves(packet, *key) else COST_APPROVAL_OF_ANOTHER_CANDIDATE)
        candidates.append(_candidate(candidate, [identity for _, identity in pairs], [h for h, _ in pairs],
                                     cost_policy, unapproved, sensitivity_scenarios,
                                     by_symbol.get(candidate["symbol"])))
    marked = {candidate["symbol"] for candidate in report["candidates"]}
    if set(by_symbol) - marked:
        raise PaperEconomicsError("mark_for_a_symbol_not_in_the_projection")
    policy = cost_policy.policy()
    payload = {
        "schema_version": PAPER_ECONOMICS_SCHEMA_VERSION_V1,
        "as_of": as_of.astimezone(UTC).isoformat(),
        "as_of_micros": as_of_micros,
        "units": UNITS_V1,
        "sign_convention": SIGN_CONVENTION_V1,
        "numeric": {"arithmetic": "PYTHON_DECIMAL", "precision": _CONTEXT.prec, "rounding": "ROUND_HALF_EVEN",
                    "return_quantum": format(RETURN_QUANTUM_V1, "f"), "price_quantum": format(PRICE_QUANTUM_V1, "f")},
        "cost_policy": {"slot": policy.slot, "mode": cost_policy.mode},
        "cost_approval": None if packet is None else {
            "packet_hash": cost_approval.packet_hash if cost_approval is not None else None,
            "authorized_by": packet["authorized_by"], "authorized_on": packet["authorized_on"]},
        "incubation_report_hash": identity_hash_v1(report),
        "candidates": candidates,
        "claim": CLAIM_V1,
    }
    return PaperEconomicsV1(payload, identity_hash_v1(payload))


__all__ = [
    "CLAIM_V1",
    "COMPONENT_STATUSES_V1",
    "COST_APPROVAL_OF_ANOTHER_CANDIDATE",
    "COST_COMPONENTS_V1",
    "FEE_NOT_OWNER_APPROVED",
    "FEE_NO_VERIFIED_SCHEDULE",
    "FUNDING_NEVER_A_CHARGE_V1",
    "FUNDING_REASON_PREFIX",
    "INCOMPLETE_CHAIN_BROKEN",
    "INCOMPLETE_CHAIN_INCONSISTENT",
    "INCOMPLETE_FILLS_SKIPPED",
    "INCOMPLETE_SIGNALS_NOT_FILLED",
    "MARK_KINDS_V1",
    "MARK_KIND_T4_BAR_OPEN_FIRST_TRADE",
    "NET_COMPONENT_PREFIX",
    "NET_CONFIRMED",
    "NET_INCOMPLETE",
    "NET_NOT_COSTABLE",
    "NET_SCOPE_V1",
    "NET_STATUSES_V1",
    "PAPER_ECONOMICS_SCHEMA_VERSION_V1",
    "PAPER_FILL_SCHEMA_VERSION_V1",
    "PRICE_QUANTUM_V1",
    "SIGN_CONVENTION_V1",
    "SLIPPAGE_NOT_OWNER_APPROVED",
    "SLIPPAGE_NO_SCENARIO",
    "SOURCE_CALLER_PROPOSED",
    "SOURCE_COST_POLICY_UNAPPROVED",
    "STATUS_MISSING",
    "STATUS_NON_COSTABLE_EVIDENCE",
    "STATUS_PROPOSED_SENSITIVITY",
    "STATUS_VERIFIED",
    "UNITS_V1",
    "UNREALIZED_AVAILABLE",
    "UNREALIZED_MARK_NOT_AFTER_ENTRY",
    "UNREALIZED_NOT_APPLICABLE_FLAT",
    "UNREALIZED_NO_PROVEN_MARK",
    "UNREALIZED_POSITION_UNKNOWN",
    "UNREALIZED_UNAVAILABLE",
    "OwnerCostApprovalV1",
    "PaperEconomicsError",
    "PaperEconomicsV1",
    "ProvenMarkV1",
    "net_status_v1",
    "paper_economics_v1",
    "verify_paper_economics_v1",
]
