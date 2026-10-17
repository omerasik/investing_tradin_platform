"""OR-6 F3 forward -- forward funding evidence in the paper incubation report, NON-COSTABLE.

``RESEARCH_ONLY``, pure and import-light (the protected API builds the report; the
runtime image carries no numerical libraries, so the bridge module is not
imported: its constants are mirrored here and a test pins them equal). Nothing
here acquires, loads or evaluates anything, and nothing here is a cost.

What it binds
-------------
One candidate (study, trial, symbol, and -- when the report holds its OR-7
binding -- its validation identity) and the forward funding/price evidence the
caller holds for its symbol: issued
:class:`~trade_platform.forward_funding_price_bridge_v1.ForwardFundingPriceBridgeV1`
results (immutable settled-funding store, proven T4 trade proxy, venue mark
evidence beside it). Each bridge is accepted only if its identity re-derives its
content hash, its schema and contract are the v1.1 bridge's, its canonical
symbol is the candidate's, its settlements lie in its window, are ordered and
unique, are internally consistent (price reference of the declared convention
at the right bar, knowledge at or before the bridge's own ``as_of``), a
``COMPLETE`` funding window has closed by that ``as_of``, and the
bridge's ``as_of`` is at or before the report's (no evidence known after the
report's reading instant). Two bridges for one candidate may not overlap or mix
price conventions. Any of these is a refusal, never a downgrade.

Holding intervals are the report's own position chain (unit exposure):

* ``CLOSED_BY_FILL`` -- a booked round trip ``[entry bar open, exit bar open]``;
* ``OPEN_AT_AS_OF`` -- the open position ``[entry bar open, as_of]``;
* ``CHAIN_BROKEN_POSITION_UNKNOWN`` -- an entry whose chain broke (restart,
  unfilled signal, inconsistency) before an exit: ``[entry bar open, the
  breaking signal's decision]``, the position after the entry is not proven.

A settlement instant strictly inside a ``CLOSED_BY_FILL`` or ``OPEN_AT_AS_OF``
interval is ``HELD_AT_SETTLEMENT``; one equal to a fill instant is
``AT_FILL_INSTANT_HOLDING_AMBIGUOUS`` (whether a position filled at the
settlement instant is held at it is a convention nobody has decided -- the
historical T2 rule's "worse outcome once" is not applied forward); one inside a
broken interval is ``HOLDING_NOT_PROVEN_CHAIN_BROKEN``. Nothing is inferred
between settlements: an interval is ``COVERED`` only when one merged run of
``COMPLETE`` funding windows (uniform spacing, tight edges, closed as of the
bridge's instant) contains it, edges included; otherwise ``NOT_COVERED`` and no
settlement is invented.

States (:data:`FUNDING_EVIDENCE_STATES_V1`)
-------------------------------------------
``NO_FUNDING_EVIDENCE_SUPPLIED``
    No bridge for the candidate.
``EVIDENCE_LINKED``
    At least one accepted bridge; what is bound is listed with the reasons it is
    not complete (``completeness_blockers``).
``EVIDENCE_COMPLETE``
    At least one holding interval, every interval closed by a fill and
    ``COVERED``, every settlement in it ``HELD_AT_SETTLEMENT`` with bound trade
    proxy evidence, and no fill skipped at an unknown position.
``ECONOMICALLY_COSTABLE``
    Reserved; v1 never produces it. It would need an owner-approved settlement
    price convention, a verified OR-6 fee schedule, an owner decision that a
    published settlement is the paper account's settlement authority, and an
    owner authorization of forward funding acquisition. The bridge can represent
    only the unresolved convention and acquisition, and v1 represents no
    settlement authority, so ``economic_blockers`` is never empty.

Rates, price references (trade proxy and venue mark, never merged), provenance
and economic blockers are separate sections. No amount, charge, sign of a cash
flow or return is calculated; the report's gross and net figures are untouched
and its ``funding`` stays ``NOT_MODELLED``. Rate text is the venue's verbatim
(published semantics); prices are the bridge's quantized strings; this module
does no ``Decimal`` arithmetic, so nothing is hashed unquantized.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, Final

from .strategy_lab_study_v1 import identity_hash_v1

PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1: Final = "paper-funding-evidence-v1"

STATE_NO_FUNDING_EVIDENCE: Final = "NO_FUNDING_EVIDENCE_SUPPLIED"
STATE_EVIDENCE_LINKED: Final = "EVIDENCE_LINKED"
STATE_EVIDENCE_COMPLETE: Final = "EVIDENCE_COMPLETE"
#: Reserved; v1 never produces it.
STATE_ECONOMICALLY_COSTABLE_RESERVED: Final = "ECONOMICALLY_COSTABLE"
FUNDING_EVIDENCE_STATES_V1: Final = (STATE_NO_FUNDING_EVIDENCE, STATE_EVIDENCE_LINKED, STATE_EVIDENCE_COMPLETE,
                                     STATE_ECONOMICALLY_COSTABLE_RESERVED)

HOLDING_CLOSED_BY_FILL: Final = "CLOSED_BY_FILL"
HOLDING_OPEN_AT_AS_OF: Final = "OPEN_AT_AS_OF"
HOLDING_CHAIN_BROKEN: Final = "CHAIN_BROKEN_POSITION_UNKNOWN"
HOLDING_END_KINDS_V1: Final = (HOLDING_CLOSED_BY_FILL, HOLDING_OPEN_AT_AS_OF, HOLDING_CHAIN_BROKEN)

HELD_AT_SETTLEMENT: Final = "HELD_AT_SETTLEMENT"
AT_FILL_INSTANT_HOLDING_AMBIGUOUS: Final = "AT_FILL_INSTANT_HOLDING_AMBIGUOUS"
HOLDING_NOT_PROVEN_CHAIN_BROKEN: Final = "HOLDING_NOT_PROVEN_CHAIN_BROKEN"

COVERED: Final = "COVERED"
NOT_COVERED: Final = "NOT_COVERED"

# Completeness blockers.
NO_HOLDING_INTERVAL: Final = "NO_HOLDING_INTERVAL_TO_BIND"
OPEN_POSITION_AT_AS_OF: Final = "OPEN_POSITION_AT_AS_OF_NOT_CLOSED"
CHAIN_BROKEN_HOLDING: Final = "HOLDING_ENDED_BY_A_CHAIN_BREAK_POSITION_UNKNOWN"
FILLS_SKIPPED_UNKNOWN_POSITION: Final = "FILLS_SKIPPED_AT_AN_UNKNOWN_POSITION"
HOLDING_NOT_COVERED: Final = "HOLDING_INTERVAL_NOT_COVERED_BY_COMPLETE_FUNDING_WINDOWS"
SETTLEMENT_AT_FILL_INSTANT: Final = "SETTLEMENT_AT_A_FILL_INSTANT_HOLDING_AMBIGUOUS"
SETTLEMENT_PRICE_REFUSED: Final = "HELD_SETTLEMENT_PRICE_EVIDENCE_REFUSED"

# Economic blockers that v1 itself always carries.
CHARGE_NOT_CALCULATED_V1: Final = "FUNDING_CHARGE_NOT_CALCULATED_IN_V1"
UNRESOLVED_OWNER_DECISION_SETTLEMENT_AUTHORITY: Final = (
    "UNRESOLVED_OWNER_DECISION_PUBLISHED_SETTLEMENT_AS_PAPER_SETTLEMENT_AUTHORITY")
MISSING_VERIFIED_FEE_SCHEDULE: Final = "MISSING_VERIFIED_FEE_SCHEDULE_OR_6"

# Mirrors of :mod:`trade_platform.forward_funding_price_bridge_v1` and its inputs (not imported:
# that module pulls the numerical stack). A test pins every one equal.
BRIDGE_SCHEMA_VERSION_V1: Final = "forward-funding-price-bridge-v1.1"
#: The bridge contract's public identity hash (not a credential; pinned equal to the source by a test).
BRIDGE_CONTRACT_HASH_V1: Final = (
    "c0388508d52e71f58ce431235f6c90e0b783a8be9264e40c2aa3c55a9827bdf4"  # pragma: allowlist secret
)
FUNDING_WINDOW_COMPLETE: Final = "COMPLETE"
SETTLEMENT_EVIDENCE_BOUND: Final = "EVIDENCE_BOUND_TRADE_PROXY_UNAPPROVED"
SETTLEMENT_PRICE_EVIDENCE_REFUSED: Final = "PRICE_EVIDENCE_REFUSED"
SETTLEMENT_STATUSES: Final = (SETTLEMENT_EVIDENCE_BOUND, SETTLEMENT_PRICE_EVIDENCE_REFUSED)
BRIDGE_STATUSES: Final = ("REFUSED_FUNDING_EVIDENCE", "REFUSED_PRICE_EVIDENCE",
                          "EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED")
TRADE_PRICE_PROXY: Final = "TRADE_PRICE_PROXY"
VENUE_PUBLISHED_MARK_PRICE: Final = "VENUE_PUBLISHED_MARK_PRICE"
MARK_EVIDENCE_CLASSES: Final = ("EXACT", "NEARBY", "REFUSED", "UNAVAILABLE")
PAYER_DIRECTIONS: Final = ("LONGS_PAY_SHORTS", "SHORTS_PAY_LONGS", "ZERO_RATE_NO_TRANSFER")
CONVENTION_FIRST_TRADE_AT_INSTANT: Final = "FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT"
CONVENTION_LAST_TRADE_ENDING_AT_INSTANT: Final = "LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT"
#: Bar open of each convention's reference minute, relative to the settlement instant (micros).
_BAR_OFFSET_MICROS: Final = {CONVENTION_FIRST_TRADE_AT_INSTANT: 0, CONVENTION_LAST_TRADE_ENDING_AT_INSTANT: -60_000_000}
CANONICAL_SYMBOLS: Final = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION: Final = "UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION"
FORWARD_FUNDING_ACQUISITION_PREFIX: Final = "FORWARD_FUNDING_ACQUISITION:"

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_ISSUER: Final = object()


class PaperFundingEvidenceError(ValueError):
    """Raised when funding evidence cannot be attached to a candidate honestly."""


def _iso_ms(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()


def canonical_key_v1(symbol: str) -> str:
    if symbol not in CANONICAL_SYMBOLS:
        raise PaperFundingEvidenceError("funding_evidence_symbol_outside_the_universe")
    return f"BYBIT:linear:{symbol}"


# ---------------------------------------------------------------------------
# Bridge acceptance
# ---------------------------------------------------------------------------


def _int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaperFundingEvidenceError(code)
    return value


def _check_settlement(item: Mapping[str, Any], *, convention: str, as_of_micros: int,
                      start_ms: int, end_ms: int) -> None:
    at = _int(item.get("settled_at_ms"), "funding_settlement_instant_malformed")
    if not start_ms <= at < end_ms:
        raise PaperFundingEvidenceError("funding_settlement_outside_its_bridge_window")
    if item.get("settled_at") != _iso_ms(at):
        raise PaperFundingEvidenceError("funding_settlement_instant_text_disagrees")
    rate = item.get("rate")
    try:
        if not isinstance(rate, str) or not Decimal(rate).is_finite():
            raise PaperFundingEvidenceError("funding_settlement_rate_malformed")
    except InvalidOperation as error:
        raise PaperFundingEvidenceError("funding_settlement_rate_malformed") from error
    sign = Decimal(rate).compare(Decimal(0))
    if item.get("payer_direction") != PAYER_DIRECTIONS[0 if sign > 0 else 1 if sign < 0 else 2]:
        raise PaperFundingEvidenceError("funding_settlement_payer_direction_contradicts_its_rate")
    known = _int(item.get("funding_knowledge_at_micros"), "funding_settlement_knowledge_malformed")
    if known > as_of_micros or known < at * 1000:
        raise PaperFundingEvidenceError("funding_settlement_knowledge_outside_settlement_and_bridge_as_of")
    if not str(item.get("funding_observation_id") or "").strip():
        raise PaperFundingEvidenceError("funding_settlement_without_an_observation_reference")
    status = item.get("status")
    if status not in SETTLEMENT_STATUSES:
        raise PaperFundingEvidenceError("funding_settlement_status_unknown")
    price = item.get("price")
    if status == SETTLEMENT_EVIDENCE_BOUND:
        if not isinstance(price, Mapping) or item.get("price_refusal") is not None:
            raise PaperFundingEvidenceError("bound_settlement_without_a_price_reference")
        if price.get("evidence_kind") != TRADE_PRICE_PROXY or price.get("convention") != convention:
            raise PaperFundingEvidenceError("price_reference_is_not_the_bridge_convention_trade_proxy")
        if _int(price.get("bar_open_micros"), "price_reference_bar_malformed") != (
                at * 1000 + _BAR_OFFSET_MICROS[convention]):
            raise PaperFundingEvidenceError("price_reference_bar_is_not_the_convention_minute")
        knowable = _int(price.get("knowable_at_micros"), "price_reference_knowledge_malformed")
        evidence = _int(item.get("evidence_knowable_at_micros"), "settlement_knowledge_malformed")
        if evidence != max(known, knowable) or evidence > as_of_micros:
            raise PaperFundingEvidenceError("settlement_knowledge_disagrees_or_is_after_the_bridge_as_of")
        for key in ("price", "reference_hash", "dataset_version_id", "dataset_content_hash", "bar_content_hash"):
            if not str(price.get(key) or "").strip():
                raise PaperFundingEvidenceError("price_reference_without_its_source_reference")
    elif price is not None or not str(item.get("price_refusal") or "").strip():
        raise PaperFundingEvidenceError("refused_settlement_must_carry_its_refusal_and_no_price")
    mark = item.get("mark_price")
    if not isinstance(mark, Mapping) or mark.get("evidence_kind") != VENUE_PUBLISHED_MARK_PRICE \
            or mark.get("evidence_class") not in MARK_EVIDENCE_CLASSES:
        raise PaperFundingEvidenceError("mark_evidence_record_malformed")
    if mark.get("knowable_at_micros") is not None and int(mark["knowable_at_micros"]) > as_of_micros:
        raise PaperFundingEvidenceError("mark_evidence_known_after_the_bridge_as_of")


def _accept_bridge(bridge: object, symbol: str) -> Mapping[str, Any]:
    identity = getattr(bridge, "identity", None)
    content_hash = getattr(bridge, "content_hash", None)
    if not isinstance(identity, Mapping) or not isinstance(content_hash, str):
        raise PaperFundingEvidenceError("funding_evidence_must_be_a_forward_funding_price_bridge")
    if identity_hash_v1(dict(identity)) != content_hash:
        raise PaperFundingEvidenceError("funding_bridge_identity_does_not_rederive")
    if identity.get("schema_version") != BRIDGE_SCHEMA_VERSION_V1:
        raise PaperFundingEvidenceError("funding_bridge_schema_version_unknown")
    if identity.get("bridge_contract_hash") != BRIDGE_CONTRACT_HASH_V1:
        raise PaperFundingEvidenceError("funding_bridge_contract_is_not_the_v1_contract")
    canonical = identity.get("symbol")
    if not isinstance(canonical, Mapping) or canonical.get("canonical_key") != canonical_key_v1(symbol) \
            or canonical.get("symbol") != symbol:
        raise PaperFundingEvidenceError("funding_evidence_symbol_is_not_the_candidate_symbol")
    if identity.get("status") not in BRIDGE_STATUSES:
        raise PaperFundingEvidenceError("funding_bridge_status_unknown")
    convention = identity.get("convention")
    if convention not in _BAR_OFFSET_MICROS:
        raise PaperFundingEvidenceError("funding_bridge_price_convention_unknown")
    start_ms = _int(identity.get("start_ms"), "funding_bridge_window_malformed")
    end_ms = _int(identity.get("end_ms_exclusive"), "funding_bridge_window_malformed")
    as_of_micros = _int(identity.get("as_of_micros"), "funding_bridge_as_of_malformed")
    if end_ms <= start_ms:
        raise PaperFundingEvidenceError("funding_bridge_window_malformed")
    window = identity.get("funding_window")
    if not isinstance(window, Mapping) or not str(window.get("content_hash") or "").strip():
        raise PaperFundingEvidenceError("funding_bridge_window_reference_missing")
    if (window.get("status") == FUNDING_WINDOW_COMPLETE) == (identity["status"] == "REFUSED_FUNDING_EVIDENCE"):
        raise PaperFundingEvidenceError("funding_bridge_status_contradicts_its_window")
    if window.get("status") == FUNDING_WINDOW_COMPLETE and end_ms * 1000 > as_of_micros:
        # A window is COMPLETE only once it has closed as of the bridge's instant (the source contract).
        raise PaperFundingEvidenceError("funding_window_complete_before_it_closed_as_of_the_bridge")
    settlements = identity.get("settlements")
    if not isinstance(settlements, Sequence) or isinstance(settlements, str | bytes):
        raise PaperFundingEvidenceError("funding_bridge_settlements_malformed")
    for item in settlements:
        if not isinstance(item, Mapping):
            raise PaperFundingEvidenceError("funding_bridge_settlements_malformed")
        _check_settlement(item, convention=str(convention), as_of_micros=as_of_micros,
                          start_ms=start_ms, end_ms=end_ms)
    instants = [int(item["settled_at_ms"]) for item in settlements]
    if instants != sorted(set(instants)):
        raise PaperFundingEvidenceError("funding_bridge_settlements_not_ordered_and_unique")
    refused = any(item["status"] == SETTLEMENT_PRICE_EVIDENCE_REFUSED for item in settlements)
    if identity["status"] == "EVIDENCE_BOUND_NOT_COSTABLE_PRICE_CONVENTION_UNAPPROVED" and refused:
        raise PaperFundingEvidenceError("funding_bridge_status_contradicts_its_settlements")
    unresolved = identity.get("unresolved_decisions")
    if not isinstance(unresolved, Sequence) or isinstance(unresolved, str | bytes) \
            or not all(isinstance(item, str) for item in unresolved):
        raise PaperFundingEvidenceError("funding_bridge_unresolved_decisions_malformed")
    if UNRESOLVED_OWNER_DECISION_FUNDING_PRICE_CONVENTION not in unresolved or not any(
            item.startswith(FORWARD_FUNDING_ACQUISITION_PREFIX) for item in unresolved):
        raise PaperFundingEvidenceError("funding_bridge_omits_an_unresolved_owner_decision")
    return identity


@dataclass(frozen=True, slots=True)
class CandidateFundingEvidenceV1:
    """Forward funding bridges attached to one candidate. Issued only by :func:`candidate_funding_evidence_v1`."""

    study_id: str
    trial_id: str
    symbol: str
    validation_hash: str | None
    bridges: tuple[tuple[str, Mapping[str, Any]], ...]
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise PaperFundingEvidenceError("candidate_funding_evidence_is_issued_only_by_its_constructor")

    @property
    def key(self) -> tuple[str, str]:
        return (self.study_id, self.trial_id)


def candidate_funding_evidence_v1(*, study_id: str, trial_id: str, symbol: str,
                                  bridges: Sequence[object],
                                  validation_hash: str | None = None) -> CandidateFundingEvidenceV1:
    """Accept forward funding bridges for one candidate's symbol, or refuse.

    ``validation_hash`` names the candidate's OR-7 holdout validation; the
    report refuses it when the candidate's binding names another one (or has
    none). ``bridges`` must be non-empty: an absent candidate entry is how a
    caller states it holds no evidence.
    """
    if not str(study_id).strip() or not str(trial_id).strip():
        raise PaperFundingEvidenceError("funding_evidence_candidate_identity_missing")
    if isinstance(bridges, str | bytes) or not isinstance(bridges, Sequence) or not bridges:
        raise PaperFundingEvidenceError("funding_evidence_needs_at_least_one_bridge")
    accepted = sorted(((str(getattr(item, "content_hash", "")), _accept_bridge(item, symbol)) for item in bridges),
                      key=lambda pair: (int(pair[1]["start_ms"]), pair[0]))
    if len({pair[0] for pair in accepted}) != len(accepted):
        raise PaperFundingEvidenceError("funding_bridge_supplied_twice")
    if len({str(pair[1]["convention"]) for pair in accepted}) != 1:
        raise PaperFundingEvidenceError("funding_bridges_mix_price_conventions")
    for (_, left), (_, right) in pairwise(accepted):
        if int(right["start_ms"]) < int(left["end_ms_exclusive"]):
            raise PaperFundingEvidenceError("funding_bridge_windows_overlap")
    return CandidateFundingEvidenceV1(str(study_id), str(trial_id), symbol, validation_hash,
                                      tuple(accepted), _ISSUER)


# ---------------------------------------------------------------------------
# Binding to holding intervals
# ---------------------------------------------------------------------------


def _covered_runs(bridges: Sequence[tuple[str, Mapping[str, Any]]]) -> list[tuple[int, int]]:
    """Merged ``[start, end)`` micros of contiguous COMPLETE funding windows."""
    runs: list[tuple[int, int]] = []
    for _, identity in bridges:
        if identity["funding_window"]["status"] != FUNDING_WINDOW_COMPLETE:
            continue
        start, end = int(identity["start_ms"]) * 1000, int(identity["end_ms_exclusive"]) * 1000
        if runs and start == runs[-1][1]:
            runs[-1] = (runs[-1][0], end)
        else:
            runs.append((start, end))
    return runs


def funding_evidence_section_v1(evidence: CandidateFundingEvidenceV1 | None, *, study_id: str, trial_id: str,
                                symbol: str, holdings: Sequence[Mapping[str, Any]], fills_skipped: int,
                                as_of_micros: int, binding_validation_hash: str | None,
                                fee_schedule_verified: bool) -> dict[str, Any]:
    """The candidate's ``funding_evidence`` report section. Never a charge."""
    economic = [CHARGE_NOT_CALCULATED_V1, UNRESOLVED_OWNER_DECISION_SETTLEMENT_AUTHORITY]
    if not fee_schedule_verified:
        economic.append(MISSING_VERIFIED_FEE_SCHEDULE)
    section: dict[str, Any] = {
        "schema_version": PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1,
        "state": STATE_NO_FUNDING_EVIDENCE,
        "costable": False,
        "charge": "NOT_CALCULATED",
        "changes_returns": False,
        "candidate": {"study_id": study_id, "trial_id": trial_id, "symbol": symbol,
                      "canonical_symbol": canonical_key_v1(symbol) if symbol in CANONICAL_SYMBOLS else None,
                      "validation_hash": binding_validation_hash},
        "holding_intervals": [],
        "settlement_rates": [],
        "price_references": [],
        "provenance": {"bridges": [], "settlements": []},
        "settlements_outside_holdings": 0,
        "completeness_blockers": [],
        "economic_blockers": [],
    }
    if evidence is None:
        section["economic_blockers"] = sorted(set(economic))
        section["evidence_hash"] = identity_hash_v1(section)
        return section
    if not isinstance(evidence, CandidateFundingEvidenceV1):
        raise PaperFundingEvidenceError("candidate_funding_evidence_is_issued_only_by_its_constructor")
    if evidence.key != (study_id, trial_id) or evidence.symbol != symbol:
        raise PaperFundingEvidenceError("funding_evidence_is_of_another_candidate")
    if evidence.validation_hash is not None and evidence.validation_hash != binding_validation_hash:
        raise PaperFundingEvidenceError("funding_evidence_is_of_another_incubation_binding")
    for _, identity in evidence.bridges:
        if int(identity["as_of_micros"]) > as_of_micros:
            raise PaperFundingEvidenceError("funding_evidence_known_after_the_report_as_of")

    settlements: dict[int, tuple[str, Mapping[str, Any], Mapping[str, Any]]] = {}
    for content_hash, identity in evidence.bridges:
        for item in identity["settlements"]:
            settlements[int(item["settled_at_ms"])] = (content_hash, identity, item)
        unresolved = [str(item) for item in identity["unresolved_decisions"]]
        economic += unresolved
        section["provenance"]["bridges"].append({
            "content_hash": content_hash,
            "schema_version": identity["schema_version"],
            "bridge_contract_hash": identity["bridge_contract_hash"],
            "status": identity["status"],
            "start_ms": int(identity["start_ms"]),
            "end_ms_exclusive": int(identity["end_ms_exclusive"]),
            "as_of_micros": int(identity["as_of_micros"]),
            "convention": identity["convention"],
            "funding_window": {"content_hash": identity["funding_window"]["content_hash"],
                               "status": identity["funding_window"]["status"],
                               "irregularities": list(identity["funding_window"].get("irregularities") or [])},
            "price_datasets": list(identity.get("price_datasets") or []),
            "mark_evidence_datasets": [list(item) for item in identity.get("mark_evidence_datasets") or []],
            "reasons": list(identity.get("reasons") or []),
        })
    runs = _covered_runs(evidence.bridges)
    blockers: list[str] = []
    if fills_skipped:
        blockers.append(FILLS_SKIPPED_UNKNOWN_POSITION)
    if not holdings:
        blockers.append(NO_HOLDING_INTERVAL)
    bound: set[int] = set()
    for index, holding in enumerate(holdings):
        kind = holding["end_kind"]
        if kind not in HOLDING_END_KINDS_V1:  # pragma: no cover - built by the report
            raise PaperFundingEvidenceError("holding_end_kind_unknown")
        start, end = int(holding["start_micros"]), int(holding["end_micros"])
        if end > as_of_micros or end < start:
            raise PaperFundingEvidenceError("holding_interval_outside_the_report_as_of")
        coverage = COVERED if any(low <= start and end < high for low, high in runs) else NOT_COVERED
        inside = [at for at in sorted(settlements) if start <= at * 1000 <= end]
        if kind == HOLDING_OPEN_AT_AS_OF:
            blockers.append(OPEN_POSITION_AT_AS_OF)
        elif kind == HOLDING_CHAIN_BROKEN:
            blockers.append(CHAIN_BROKEN_HOLDING)
        if coverage != COVERED:
            blockers.append(HOLDING_NOT_COVERED)
        for at in inside:
            bound.add(at)
            content_hash, _, item = settlements[at]
            if kind == HOLDING_CHAIN_BROKEN:
                relation = HOLDING_NOT_PROVEN_CHAIN_BROKEN
            elif at * 1000 in (start, end):
                relation = AT_FILL_INSTANT_HOLDING_AMBIGUOUS
                blockers.append(SETTLEMENT_AT_FILL_INSTANT)
            else:
                relation = HELD_AT_SETTLEMENT
                if item["status"] != SETTLEMENT_EVIDENCE_BOUND:
                    blockers.append(SETTLEMENT_PRICE_REFUSED)
            section["settlement_rates"].append({
                "settled_at_ms": at, "settled_at": item["settled_at"], "rate": item["rate"],
                "payer_direction": item["payer_direction"], "holding_index": index,
                "position_direction": int(holding["direction"]), "relation": relation,
            })
            mark = item["mark_price"]
            section["price_references"].append({
                "settled_at_ms": at, "status": item["status"],
                "trade_proxy": None if item["price"] is None else dict(item["price"]),
                "price_refusal": item["price_refusal"], "structural_note": item.get("structural_note"),
                "mark_evidence_class": mark["evidence_class"], "mark_price": mark.get("mark_price"),
                "mark_refusal": mark.get("refusal"), "mark_meaning": mark.get("meaning"),
                "mark_resolution_hash": mark.get("resolution_hash"),
            })
            section["provenance"]["settlements"].append({
                "settled_at_ms": at, "bridge_content_hash": content_hash,
                "funding_observation_id": item["funding_observation_id"],
                "funding_knowledge_at_micros": int(item["funding_knowledge_at_micros"]),
                "evidence_knowable_at_micros": item["evidence_knowable_at_micros"],
            })
        section["holding_intervals"].append({
            "index": index, "direction": int(holding["direction"]), "start_micros": start, "end_micros": end,
            "end_kind": kind, "coverage": coverage, "settlements": inside,
        })
    section["settlements_outside_holdings"] = len(set(settlements) - bound)
    section["completeness_blockers"] = sorted(set(blockers))
    section["economic_blockers"] = sorted(set(economic))
    section["state"] = STATE_EVIDENCE_LINKED if blockers else STATE_EVIDENCE_COMPLETE
    section["evidence_hash"] = identity_hash_v1(section)
    return section


__all__ = [
    "AT_FILL_INSTANT_HOLDING_AMBIGUOUS",
    "BRIDGE_CONTRACT_HASH_V1",
    "BRIDGE_SCHEMA_VERSION_V1",
    "CHAIN_BROKEN_HOLDING",
    "CHARGE_NOT_CALCULATED_V1",
    "COVERED",
    "FILLS_SKIPPED_UNKNOWN_POSITION",
    "FUNDING_EVIDENCE_STATES_V1",
    "HELD_AT_SETTLEMENT",
    "HOLDING_CHAIN_BROKEN",
    "HOLDING_CLOSED_BY_FILL",
    "HOLDING_END_KINDS_V1",
    "HOLDING_NOT_COVERED",
    "HOLDING_NOT_PROVEN_CHAIN_BROKEN",
    "HOLDING_OPEN_AT_AS_OF",
    "MISSING_VERIFIED_FEE_SCHEDULE",
    "NOT_COVERED",
    "NO_HOLDING_INTERVAL",
    "OPEN_POSITION_AT_AS_OF",
    "PAPER_FUNDING_EVIDENCE_SCHEMA_VERSION_V1",
    "SETTLEMENT_AT_FILL_INSTANT",
    "SETTLEMENT_PRICE_REFUSED",
    "STATE_ECONOMICALLY_COSTABLE_RESERVED",
    "STATE_EVIDENCE_COMPLETE",
    "STATE_EVIDENCE_LINKED",
    "STATE_NO_FUNDING_EVIDENCE",
    "UNRESOLVED_OWNER_DECISION_SETTLEMENT_AUTHORITY",
    "CandidateFundingEvidenceV1",
    "PaperFundingEvidenceError",
    "candidate_funding_evidence_v1",
    "canonical_key_v1",
    "funding_evidence_section_v1",
]
