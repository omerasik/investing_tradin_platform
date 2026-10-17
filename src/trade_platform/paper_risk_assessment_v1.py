"""Phase R9/R10 -- paper risk and sizing assessment under the owner's stored account policy.

``RESEARCH_ONLY`` / paper only. No order, no broker, no account activation, no
capital. Given one requested paper target for one (study, trial, symbol), this
module answers whether the account's owner-approved
:class:`~trade_platform.account_policy_v1.AccountPolicyV1` and the account's
recorded paper state admit it, and -- only then -- what paper size it has. The
answer is a deterministic, content-addressed assessment with a verdict:

* ``ALLOWED`` -- every authorized control passed (or is owner-declared not
  applicable for personal paper) on complete evidence;
* ``BLOCKED`` -- a control failed, or an input is inconsistent: a stored policy
  that does not re-derive, an account / policy / symbol / state mismatch, a
  superseded or unknown expected policy version, a look-ahead time, or any
  real-execution context;
* ``UNRESOLVED`` -- an owner decision or evidence is missing: no policy, an
  ``UNCONFIGURED`` latest version (each ``MISSING_OWNER_*_OR_11`` is carried
  through), an approval dated after the assessment, a prop account, missing
  paper state, market-knowledge time or reference price.

Authority comes from stored rows, never from the caller
---------------------------------------------------------
:func:`resolve_account_policy_v1` rebuilds every stored version of the account
(``account_policy_versions``) from its identity, re-derives its content hash,
version id and status and checks each against the row; any failure anywhere in
the account's history blocks. The authoritative version is the *latest version
recorded at or before* ``as_of`` -- whatever its status, so a newer incomplete
version is never silently skipped for an older active one (stricter than
``PostgresAccountPolicyStoreV1.latest_active``). A caller may name the version
it believes in (``expected_policy_version_id``); it is only ever compared
against the authoritative one, never trusted.

Recorded paper state
--------------------
:func:`paper_state_from_fills_v1` derives the state from the paper ledger
(:func:`~trade_platform.paper_account_v1.build_paper_account_ledger_v1`) over
the recorded fills, built for the instant just before ``as_of`` -- exactly what
a ledger decision at ``as_of`` would see (executions strictly before it). Equity
is the most severe cost scenario's, as in the ledger. The equity peak is the
starting capital and the equity after each execution (equity only moves at
executions); the UTC day's start equity is the equity before the day's first
ledger evaluation, or the current equity when this is the first. The day's
order notional is every executed leg of an order decided that UTC day, plus,
for each pending order, its approved opening and the leg it will close (the
key's held entry notional, or the previous pending opening of the key).

Controls (only the policy's own values; nothing has a default)
--------------------------------------------------------------
Mirroring the ledger's pre-trade checks for a risk-*increasing* leg:
allowed symbols, quote currency = account money unit, positive equity
(available capital; the buying-power headroom is reported), order notional, net
position notional per symbol (the schema's concentration control; the weight of
equity is reported), gross leverage, daily order notional, daily loss,
drawdown and market age. Gross/net exposure use
:func:`~trade_platform.portfolio_risk.gross_and_net`. OR-11 has no net-exposure
or single-weight limit, so those are ``EVIDENCE_ONLY`` and never satisfy or
block anything. A target of zero, or the already-intended target, increases no
risk: its controls are ``NOT_REQUIRED_RISK_REDUCING`` (closing is always
allowed, as in the ledger).

The seven controls with no paper-v1 evidence source
(:data:`~trade_platform.account_policy_v1.NOT_APPLICABLE_FIELDS_V1`) are
``NOT_APPLICABLE`` only where the owner declared so for ``PERSONAL_PAPER``; a
numeric value fails closed with its ``*_EVIDENCE_UNAVAILABLE_IN_PAPER_V1``
reason. In a ``REAL_EXECUTION`` context the verdict is always ``BLOCKED``
(research only), a not-applicable control is a failure
(``NOT_APPLICABLE_NEVER_SATISFIES_REAL_EXECUTION``), and the execution
:class:`~trade_platform.risk.RiskEngine` policy is requested through
:meth:`AccountPolicyV1.risk_policy` so its own refusal is recorded. The
``RiskEngine.assess`` path itself is not run: it reads the wall clock and
requires a ``VALIDATED`` signal, which a paper-incubation proposal never is.

Sizing
------
Present only for an ``ALLOWED`` paper opening: ``paper_order_notional`` per
unit of target over an explicit reference price known at or before ``as_of``
and within ``max_market_age_seconds``, rounded toward zero to
``QUANTITY_QUANTUM_V1`` (a zero quantity blocks). Otherwise ``sizing`` is
``None`` and ``sizing_absent_reasons`` say why; an unconfigured account never
yields a size.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from .account_policy_v1 import (
    NOT_APPLICABLE_FIELDS_V1,
    NOT_APPLICABLE_V1,
    STATUS_ACTIVE,
    AccountContextV1,
    AccountKindV1,
    AccountPolicyError,
    AccountPolicyV1,
)
from .paper_account_v1 import (
    EVIDENCE_CONTROLS_V1,
    MONEY_QUANTUM_V1,
    QUANTITY_QUANTUM_V1,
    PaperAccountError,
    build_paper_account_ledger_v1,
    cycle_cost_policy_v1,
    cycle_fill_rows_v1,
    fills_signals_from_rows,
)
from .portfolio_risk import PortfolioExposure, gross_and_net
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

PAPER_RISK_ASSESSMENT_SCHEMA_VERSION_V1: Final = "paper-risk-assessment-v1"
PAPER_STATE_SCHEMA_VERSION_V1: Final = "paper-account-state-v1"
ALLOWED: Final = "ALLOWED"
BLOCKED: Final = "BLOCKED"
UNRESOLVED: Final = "UNRESOLVED"
RATIO_QUANTUM_V1: Final = Decimal("1e-8")

PASS: Final = "PASS"
FAIL: Final = "FAIL"
NOT_APPLICABLE: Final = "NOT_APPLICABLE"
UNRESOLVED_CONTROL: Final = "UNRESOLVED"
NOT_EVALUATED: Final = "NOT_EVALUATED"
NOT_REQUIRED: Final = "NOT_REQUIRED_RISK_REDUCING"
EVIDENCE_ONLY: Final = "EVIDENCE_ONLY"

#: Each evaluated control and the policy field(s) it reads.
CONTROLS_V1: Final[dict[str, tuple[str, ...]]] = {
    "ALLOWED_SYMBOLS": ("allowed_symbols",),
    "ACCOUNT_CURRENCY": (),
    "AVAILABLE_CAPITAL": ("paper_starting_capital", "maximum_leverage"),
    "ORDER_NOTIONAL": ("maximum_order_notional",),
    "POSITION_NOTIONAL": ("maximum_position_notional",),
    "GROSS_LEVERAGE": ("maximum_leverage",),
    "NET_EXPOSURE": (),
    "DAILY_ORDER_NOTIONAL": ("maximum_daily_order_notional",),
    "DAILY_LOSS": ("daily_loss_limit",),
    "DRAWDOWN": ("maximum_drawdown_limit",),
    "MARKET_AGE": ("max_market_age_seconds",),
    **{f"EVIDENCE:{name}": (name,) for name in sorted(NOT_APPLICABLE_FIELDS_V1)},
}
#: The ledger's evidence reasons, completed for the two stop controls it covers via the per-trade loss.
_EVIDENCE_REASONS: Final[dict[str, str]] = {
    **EVIDENCE_CONTROLS_V1,
    "maximum_stop_distance_fraction": EVIDENCE_CONTROLS_V1["maximum_per_trade_loss"],
    "stop_gap_buffer_fraction": EVIDENCE_CONTROLS_V1["maximum_per_trade_loss"],
}
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.paper_risk_assessment_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO: Final = timedelta(microseconds=1)


class PaperRiskAssessmentError(ValueError):
    """Raised when a request or stored row is malformed (a caller error, not a verdict)."""


class ExecutionContextV1(StrEnum):
    PAPER_INCUBATION = "PAPER_INCUBATION"
    REAL_EXECUTION = "REAL_EXECUTION"


def _text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


def _money(value: Decimal) -> str:
    return _text(value.quantize(MONEY_QUANTUM_V1))


def _ratio(value: Decimal) -> str:
    return _text(value.quantize(RATIO_QUANTUM_V1))


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise PaperRiskAssessmentError(f"{name}_must_be_timezone_aware")
    return value.astimezone(UTC)


def _micros(value: datetime) -> int:
    return (value - _EPOCH) // _MICRO


def _at(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def _identity(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else json.loads(raw)


# --------------------------------------------------------------------------- request


@dataclass(frozen=True, slots=True)
class PaperRiskRequestV1:
    """One requested paper target. Nothing here carries authority; every value is checked."""

    account_id: str
    study_id: str
    trial_id: str
    symbol: str
    target_to: int
    as_of: datetime
    execution_context: ExecutionContextV1
    expected_policy_version_id: str | None = None
    market_knowledge_at: datetime | None = None  # the signal bar's complete market-knowledge time
    reference_price: Decimal | None = None
    reference_price_known_at: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.target_to, bool) or self.target_to not in (-1, 0, 1):
            raise PaperRiskAssessmentError("target_to_must_be_minus_one_zero_or_one")
        if not isinstance(self.execution_context, ExecutionContextV1):
            raise PaperRiskAssessmentError("execution_context_unknown")
        for name in ("account_id", "study_id", "trial_id", "symbol"):
            if not str(getattr(self, name)).strip():
                raise PaperRiskAssessmentError(f"{name}_required")
        object.__setattr__(self, "as_of", _aware(self.as_of, "as_of"))
        for name in ("market_knowledge_at", "reference_price_known_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _aware(value, name))
        if self.reference_price is not None:
            price = self.reference_price
            if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
                raise PaperRiskAssessmentError("reference_price_must_be_a_positive_finite_decimal")
            if self.reference_price_known_at is None:
                raise PaperRiskAssessmentError("reference_price_needs_its_market_knowledge_time")

    def payload(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "account_id": self.account_id, "study_id": self.study_id, "trial_id": self.trial_id,
            "symbol": self.symbol, "target_to": self.target_to, "as_of": self.as_of.isoformat(),
            "execution_context": self.execution_context.value,
            "expected_policy_version_id": self.expected_policy_version_id,
            "market_knowledge_at": iso(self.market_knowledge_at),
            "reference_price": None if self.reference_price is None else _text(self.reference_price),
            "reference_price_known_at": iso(self.reference_price_known_at),
        }


# --------------------------------------------------------------------------- policy resolution


@dataclass(frozen=True)
class PolicyResolutionV1:
    """The authoritative policy version of one account at ``as_of``, from stored rows only."""

    account_id: str
    as_of: datetime
    account: AccountContextV1 | None
    policy: AccountPolicyV1 | None
    policy_recorded_at: datetime | None
    blocking: tuple[str, ...]
    unresolved: tuple[str, ...]
    versions_recorded: int
    versions_known_at_as_of: int
    integrity_failures: tuple[dict[str, str], ...] = ()
    expected_policy_version_id: str | None = None

    @property
    def usable(self) -> bool:
        return (self.policy is not None and self.policy.status == STATUS_ACTIVE
                and not self.blocking and not self.unresolved)

    def payload(self) -> dict[str, Any]:
        policy = self.policy
        return {
            "account_id": self.account_id,
            "account": None if self.account is None else self.account.payload(),
            "policy_version_id": None if policy is None else str(policy.policy_version_id),
            "policy_content_hash": None if policy is None else policy.content_hash,
            "policy_status": None if policy is None else policy.status,
            "policy_recorded_at": None if self.policy_recorded_at is None else self.policy_recorded_at.isoformat(),
            "approved_by": None if policy is None else policy.approved_by,
            "approved_on": None if policy is None else policy.approved_on,
            "not_applicable_controls": [] if policy is None else list(policy.not_applicable),
            "expected_policy_version_id": self.expected_policy_version_id,
            "versions_recorded": self.versions_recorded,
            "versions_known_at_as_of": self.versions_known_at_as_of,
            "integrity_failures": [dict(item) for item in self.integrity_failures],
        }


def _row_failure(account: AccountContextV1, account_id: str, version_id: str, content_hash: Any, row_account: Any,
                 status: Any, raw: Any) -> str | AccountPolicyV1:
    """The rebuilt policy of one stored row, or the reason it does not re-derive."""
    if str(row_account) != account_id:
        return "POLICY_ACCOUNT_MISMATCH"
    try:
        identity = _identity(raw)
        if identity.get("account") != account.payload():
            return "POLICY_ACCOUNT_MISMATCH"
        policy = AccountPolicyV1(account, identity["values"], identity["approved_by"], identity["approved_on"])
    except (AccountPolicyError, KeyError, TypeError, ValueError):
        return "STORED_POLICY_DOES_NOT_REBUILD"
    if policy.identity() != identity:
        return "STORED_POLICY_DOES_NOT_REPRODUCE"
    if policy.content_hash != str(content_hash).strip():
        return "STORED_POLICY_CONTENT_HASH_MISMATCH"
    if str(policy.policy_version_id) != version_id:
        return "POLICY_VERSION_ID_MISMATCH"
    if policy.status != str(status):
        return "STORED_POLICY_STATUS_MISMATCH"
    return policy


def resolve_account_policy_v1(
    account_id: str, account_row: Sequence[Any] | None, policy_rows: Iterable[Sequence[Any]], *,
    as_of: datetime, expected_policy_version_id: str | None = None,
) -> PolicyResolutionV1:
    """Resolve and re-derive the account's policy from stored rows.

    ``account_row`` is ``(kind, display_name, base_currency)`` of ``account_contexts``;
    each policy row is ``(policy_version_id, content_hash, account_id, status, identity,
    recorded_at)`` of ``account_policy_versions``.
    """
    as_of = _aware(as_of, "as_of")
    rows = list(policy_rows)
    expected = None if expected_policy_version_id is None else str(expected_policy_version_id)

    def done(account: AccountContextV1 | None = None, policy: AccountPolicyV1 | None = None,
             recorded: datetime | None = None, blocking: Sequence[str] = (), unresolved: Sequence[str] = (),
             known: int = 0, failures: Sequence[dict[str, str]] = ()) -> PolicyResolutionV1:
        return PolicyResolutionV1(account_id, as_of, account, policy, recorded,
                                  tuple(dict.fromkeys(blocking)), tuple(dict.fromkeys(unresolved)),
                                  len(rows), known, tuple(failures), expected)

    if account_row is None:
        return done(unresolved=["ACCOUNT_NOT_REGISTERED"])
    try:
        account = AccountContextV1(account_id, AccountKindV1(str(account_row[0])), str(account_row[1]),
                                   str(account_row[2]))
    except (AccountPolicyError, ValueError, IndexError):
        return done(blocking=["STORED_ACCOUNT_CONTEXT_INVALID"])
    if not rows:
        return done(account, unresolved=["NO_ACCOUNT_POLICY_RECORDED"])

    failures: list[dict[str, str]] = []
    versions: list[tuple[datetime, str, AccountPolicyV1]] = []
    for row in rows:
        raw_id, content_hash, row_account, status, raw, recorded_at = row
        version_id = str(raw_id)
        recorded = _aware(recorded_at, "recorded_at")
        reason = _row_failure(account, account_id, version_id, content_hash, row_account, status, raw)
        if isinstance(reason, str):
            failures.append({"policy_version_id": version_id, "reason": reason})
        else:
            versions.append((recorded, version_id, reason))
    if failures:
        return done(account, blocking=[item["reason"] for item in failures], failures=failures)

    known = sorted((v for v in versions if v[0] <= as_of), key=lambda v: (v[0], v[1]))
    if not known:
        return done(account, unresolved=["NO_ACCOUNT_POLICY_KNOWN_AT_AS_OF"])
    recorded, version_id, policy = known[-1]
    blocking: list[str] = []
    unresolved: list[str] = []
    if len(known) > 1 and known[-2][0] == recorded:
        blocking.append("AMBIGUOUS_LATEST_POLICY_VERSION")
    if expected is not None and expected != version_id:
        if any(v[1] == expected for v in known):
            blocking.append("STALE_POLICY_VERSION_SUPERSEDED")
        elif any(v[1] == expected for v in versions):
            blocking.append("POLICY_VERSION_NOT_KNOWN_AT_AS_OF")
        else:
            blocking.append("POLICY_VERSION_NOT_RECORDED")
    if policy.status != STATUS_ACTIVE:
        unresolved.extend(["ACCOUNT_POLICY_UNCONFIGURED", *policy.unresolved])
    elif policy.approved_on is not None and date.fromisoformat(policy.approved_on) > as_of.date():
        unresolved.append("POLICY_APPROVED_AFTER_AS_OF")
    return done(account, policy, recorded, blocking, unresolved, len(known))


# --------------------------------------------------------------------------- paper state


@dataclass(frozen=True, slots=True)
class PaperPositionV1:
    study_id: str
    trial_id: str
    symbol: str
    units: int
    quantity: Decimal  # signed
    entry_price: Decimal

    @property
    def entry_notional(self) -> Decimal:
        return abs(self.quantity) * self.entry_price

    def payload(self) -> dict[str, Any]:
        return {"study_id": self.study_id, "trial_id": self.trial_id, "symbol": self.symbol, "units": self.units,
                "quantity": _text(self.quantity), "entry_price": _text(self.entry_price)}


@dataclass(frozen=True, slots=True)
class PendingOrderV1:
    """A decided paper order whose execution is not known before the assessment instant."""

    signal_id: str
    study_id: str
    trial_id: str
    symbol: str
    decided_at: datetime
    target_to: int
    approved_open: bool
    open_notional: Decimal  # unsigned; zero unless the opening was approved

    def payload(self) -> dict[str, Any]:
        return {"signal_id": self.signal_id, "study_id": self.study_id, "trial_id": self.trial_id,
                "symbol": self.symbol, "decided_at": self.decided_at.isoformat(), "target_to": self.target_to,
                "approved_open": self.approved_open, "open_notional": _money(self.open_notional)}


@dataclass(frozen=True)
class PaperAccountStateV1:
    """The account's recorded paper state known strictly before ``as_of`` (derived, never supplied)."""

    account_id: str
    policy_version_id: str
    policy_content_hash: str
    ledger_content_hash: str
    as_of: datetime
    risk_scenario: str
    money_unit: str
    equity: Decimal
    day_start_equity: Decimal
    peak_equity: Decimal
    day_order_notional: Decimal
    positions: tuple[PaperPositionV1, ...]
    pending: tuple[PendingOrderV1, ...]
    marks: Mapping[str, Decimal] = field(default_factory=dict)

    def identity(self) -> dict[str, Any]:
        return {
            "schema_version": PAPER_STATE_SCHEMA_VERSION_V1, "account_id": self.account_id,
            "policy_version_id": self.policy_version_id, "policy_content_hash": self.policy_content_hash,
            "ledger_content_hash": self.ledger_content_hash, "as_of": self.as_of.isoformat(),
            "risk_scenario": self.risk_scenario, "money_unit": self.money_unit,
            "equity": _money(self.equity), "day_start_equity": _money(self.day_start_equity),
            "peak_equity": _money(self.peak_equity), "day_order_notional": _money(self.day_order_notional),
            "positions": [p.payload() for p in self.positions], "pending": [p.payload() for p in self.pending],
            "marks": {symbol: _text(price) for symbol, price in sorted(self.marks.items())},
        }

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    def intended_units(self, study_id: str, trial_id: str) -> int:
        """The key's intended position: its latest pending decision, else what it holds (as the ledger)."""
        same = [p for p in self.pending if (p.study_id, p.trial_id) == (study_id, trial_id)]
        if same:
            latest = max(same, key=lambda p: (p.decided_at, p.signal_id))
            return latest.target_to if latest.approved_open else 0
        return next((p.units for p in self.positions if (p.study_id, p.trial_id) == (study_id, trial_id)), 0)


def paper_state_from_fills_v1(
    policy: AccountPolicyV1, fills: Sequence[Mapping[str, Any]], signals: Mapping[str, Mapping[str, Any]],
    cost_policy: CostPolicyV1, *, as_of: datetime,
) -> PaperAccountStateV1:
    """Derive the state from the paper ledger at the instant just before ``as_of`` (see module docstring).

    Raises :class:`~trade_platform.paper_account_v1.PaperAccountError` when the ledger refuses its inputs.
    """
    as_of = _aware(as_of, "as_of")
    before = as_of - _MICRO  # a decision at as_of sees executions strictly before it

    def ledger_at(instant: datetime) -> dict[str, Any]:
        return build_paper_account_ledger_v1(policy, fills, signals, cost_policy, as_of=instant)

    def equity_of(ledger: Mapping[str, Any]) -> Decimal:
        return Decimal(ledger["equity_by_scenario"][ledger["risk_scenario"]])

    ledger = ledger_at(before)
    cutoff = _micros(before)
    capital = Decimal(policy.values["paper_starting_capital"])
    unit = Decimal(policy.values["paper_order_notional"])
    by_signal = {str(f["signal_id"]): f for f in fills}

    executed = sorted({int(f["evidence"]["open_market_knowledge_micros"]) for f in fills
                       if str(f["status"]) == "FILLED" and int(f["decided_at_micros"]) <= cutoff
                       and int(f["evidence"]["open_market_knowledge_micros"]) <= cutoff})
    peak = max([capital, *(equity_of(ledger_at(_at(instant))) for instant in executed), equity_of(ledger)])

    day = as_of.date()
    day_orders = [o for o in ledger["orders"] if datetime.fromisoformat(o["decided_at"]).date() == day
                  and o["decision"] != "NO_ORDER_ALREADY_AT_TARGET"]
    if day_orders:
        first = min(int(by_signal[o["signal_id"]]["decided_at_micros"]) for o in day_orders)
        day_start = equity_of(ledger_at(_at(first - 1)))
    else:
        day_start = equity_of(ledger)

    positions = tuple(PaperPositionV1(p["study_id"], p["trial_id"], p["symbol"], int(p["units"]),
                                      Decimal(p["quantity"]), Decimal(p["entry_price"]))
                      for p in ledger["open_positions"])
    pending = tuple(sorted(
        (PendingOrderV1(o["signal_id"], o["study_id"], o["trial_id"], o["symbol"],
                        datetime.fromisoformat(o["decided_at"]), int(o["target_to"]),
                        int(o["target_to"]) != 0 and not o["reasons"],
                        unit * abs(int(o["target_to"])) if int(o["target_to"]) != 0 and not o["reasons"]
                        else Decimal(0))
         for o in ledger["orders"] if o["decision"] == "PENDING_EXECUTION"),
        key=lambda p: (p.decided_at, p.signal_id)))

    day_notional = sum((Decimal(leg["notional"]) for o in day_orders for leg in o.get("legs", ())), Decimal(0))
    previous_open: dict[tuple[str, str], Decimal] = {
        (p.study_id, p.trial_id): p.entry_notional for p in positions}
    for item in pending:
        key = (item.study_id, item.trial_id)
        if item.decided_at.date() == day:
            day_notional += previous_open.get(key, Decimal(0)) + item.open_notional
        previous_open[key] = item.open_notional

    # The latest paper fill price of each symbol known before as_of (the ledger's marking).
    known_fills = sorted(
        ((int(f["evidence"]["open_market_knowledge_micros"]), str(f["signal_id"]), f) for f in fills
         if str(f["status"]) == "FILLED" and int(f["decided_at_micros"]) <= cutoff
         and int(f["evidence"]["open_market_knowledge_micros"]) <= cutoff), key=lambda item: item[:2])
    marks = {str(f["symbol"]): Decimal(str(f["fill_price"])) for _known, _sig, f in known_fills}

    return PaperAccountStateV1(
        policy.account.account_id, str(policy.policy_version_id), policy.content_hash, ledger["content_hash"],
        as_of, ledger["risk_scenario"], ledger["money_unit"], equity_of(ledger), day_start, peak, day_notional,
        positions, pending, marks)


# --------------------------------------------------------------------------- assessment


def _control(name: str, status: str, reason: str | None = None, **evidence: Any) -> dict[str, Any]:
    return {"control": name, "policy_fields": list(CONTROLS_V1[name]), "status": status, "reason": reason,
            "evidence": evidence}


def _not_evaluated(reason: str) -> list[dict[str, Any]]:
    return [_control(name, NOT_EVALUATED, reason) for name in CONTROLS_V1]


def assess_paper_risk_v1(
    request: PaperRiskRequestV1, resolution: PolicyResolutionV1, state: PaperAccountStateV1 | None, *,
    state_error: str | None = None,
) -> dict[str, Any]:
    """The deterministic, content-addressed paper risk/sizing assessment (see module docstring)."""
    blocking: list[str] = []
    unresolved: list[str] = []
    real = request.execution_context is ExecutionContextV1.REAL_EXECUTION
    if real:
        blocking.append("REAL_EXECUTION_NOT_AUTHORIZED_RESEARCH_ONLY")
    if resolution.account_id != request.account_id:
        blocking.append("REQUEST_ACCOUNT_MISMATCH")
    if resolution.as_of != request.as_of:
        blocking.append("RESOLUTION_AS_OF_MISMATCH")
    if resolution.expected_policy_version_id != request.expected_policy_version_id:
        blocking.append("RESOLUTION_EXPECTED_VERSION_MISMATCH")  # resolved without the caller's expectation
    blocking.extend(resolution.blocking)
    unresolved.extend(resolution.unresolved)
    policy = resolution.policy
    execution_risk_policy: str | None = None
    action = "NO_ORDER_ALREADY_AT_TARGET"
    controls: list[dict[str, Any]]
    closing_quantity: Decimal | None = None
    opening_notional = Decimal(0)

    if policy is None or not resolution.usable:
        controls = _not_evaluated("ACCOUNT_POLICY_NOT_RESOLVED")
    elif policy.account.kind is not AccountKindV1.PERSONAL_PAPER:
        unresolved.append("PROP_ACCOUNT_RULES_NOT_ENFORCED_BY_PAPER_V1")
        controls = _not_evaluated("PROP_ACCOUNT_RULES_NOT_ENFORCED_BY_PAPER_V1")
    elif state_error is not None:
        blocking.append(state_error)
        controls = _not_evaluated(state_error)
    elif state is None:
        unresolved.append("PAPER_STATE_MISSING")
        controls = _not_evaluated("PAPER_STATE_MISSING")
    else:
        if state.account_id != policy.account.account_id:
            blocking.append("STATE_ACCOUNT_MISMATCH")
        if (state.policy_version_id, state.policy_content_hash) != (str(policy.policy_version_id),
                                                                   policy.content_hash):
            blocking.append("STATE_POLICY_MISMATCH")
        if state.as_of != request.as_of:
            blocking.append("STATE_AS_OF_MISMATCH")
        if state.money_unit != policy.account.base_currency:
            blocking.append("STATE_MONEY_UNIT_MISMATCH")
        if real:
            try:
                policy.risk_policy()
                execution_risk_policy = "BUILT_BUT_REAL_EXECUTION_IS_OUT_OF_SCOPE"
            except AccountPolicyError as error:
                execution_risk_policy = str(error)
        if set(blocking) & {"STATE_ACCOUNT_MISMATCH", "STATE_POLICY_MISMATCH", "STATE_AS_OF_MISMATCH",
                            "STATE_MONEY_UNIT_MISMATCH"}:
            controls = _not_evaluated("PAPER_STATE_DOES_NOT_MATCH")
        else:
            action, controls, closing_quantity, opening_notional = _evaluate(request, policy, state, real)
            for item in controls:
                if item["status"] == FAIL:
                    blocking.append(item["reason"])
                elif item["status"] == UNRESOLVED_CONTROL:
                    unresolved.append(item["reason"])

    sizing: dict[str, Any] | None = None
    if action == "OPEN" and not blocking and not unresolved and policy is not None and not real:
        sizing, reason = _size(request, policy, opening_notional, closing_quantity)
        if reason is not None:
            (unresolved if reason == "REFERENCE_PRICE_MISSING" else blocking).append(reason)
            sizing = None
    elif action == "CLOSE_ONLY" and not blocking and not unresolved and not real:
        sizing = {"basis": "CLOSE_HELD_POSITION", "open_quantity": None,
                  "close_quantity": None if closing_quantity is None else _text(closing_quantity)}

    blocking_t = tuple(dict.fromkeys(blocking))
    unresolved_t = tuple(dict.fromkeys(unresolved))
    verdict = BLOCKED if blocking_t else UNRESOLVED if unresolved_t else ALLOWED
    absent: list[str] = []
    if sizing is None:
        if verdict != ALLOWED:
            absent = [f"VERDICT_{verdict}"]
        elif action == "NO_ORDER_ALREADY_AT_TARGET":
            absent = ["NO_ORDER_ALREADY_AT_TARGET"]
    identity = {
        "schema_version": PAPER_RISK_ASSESSMENT_SCHEMA_VERSION_V1,
        "request": request.payload(),
        "resolution": resolution.payload(),
        "paper_state_hash": None if state is None else state.content_hash,
        "paper_state": None if state is None else state.identity(),
        "order_action": action,
        "controls": controls,
        "verdict": verdict,
        "blocking_reasons": list(blocking_t),
        "unresolved_reasons": list(unresolved_t),
        "sizing": sizing,
        "sizing_absent_reasons": absent,
        "execution_risk_policy": execution_risk_policy,
        "execution_authority": False,
        "claim": "PAPER_RISK_ASSESSMENT_NOT_EXECUTION_AUTHORITY",
    }
    content_hash = identity_hash_v1(identity)
    return {**identity, "content_hash": content_hash,
            "assessment_id": str(uuid5(_NAMESPACE, f"paper-risk-assessment:{content_hash}"))}


def _evaluate(request: PaperRiskRequestV1, policy: AccountPolicyV1, state: PaperAccountStateV1,
              real: bool) -> tuple[str, list[dict[str, Any]], Decimal | None, Decimal]:
    """(action, controls, the held quantity this order closes, the opening notional)."""
    values = policy.values
    key = (request.study_id, request.trial_id)
    held = next((p for p in state.positions if (p.study_id, p.trial_id) == key), None)
    closing_quantity = None if held is None else -held.quantity
    desired = request.target_to
    if desired == state.intended_units(*key):
        return "NO_ORDER_ALREADY_AT_TARGET", [_control(n, NOT_REQUIRED) for n in CONTROLS_V1], None, Decimal(0)
    if desired == 0:
        return "CLOSE_ONLY", [_control(n, NOT_REQUIRED) for n in CONTROLS_V1], closing_quantity, Decimal(0)

    symbol = request.symbol
    sign = 1 if desired > 0 else -1
    notional = Decimal(values["paper_order_notional"]) * abs(desired)
    equity = state.equity
    others = [p for p in state.positions if (p.study_id, p.trial_id) != key]
    reserved = [p for p in state.pending if p.approved_open and (p.study_id, p.trial_id) != key]
    exposures = ([PortfolioExposure(p.symbol, p.entry_notional * (1 if p.units > 0 else -1), "PAPER", "")
                  for p in others]
                 + [PortfolioExposure(p.symbol, p.open_notional * (1 if p.target_to > 0 else -1), "PAPER", "")
                    for p in reserved])
    gross_before, net_before = gross_and_net(exposures)
    projected = [*exposures, PortfolioExposure(symbol, notional * sign, "PAPER", "")]
    gross_after, net_after = gross_and_net(projected)
    symbol_net = sum((e.notional for e in projected if e.instrument_id == symbol), Decimal(0))
    same_key = [p for p in state.pending if (p.study_id, p.trial_id) == key]
    if same_key:
        closing_notional = max(same_key, key=lambda p: (p.decided_at, p.signal_id)).open_notional
    elif held is not None:
        closing_notional = abs(held.quantity) * state.marks.get(held.symbol, held.entry_price)
    else:
        closing_notional = Decimal(0)

    limit = {name: Decimal(values[name]) for name in (
        "maximum_order_notional", "maximum_position_notional", "maximum_daily_order_notional",
        "maximum_leverage", "daily_loss_limit", "maximum_drawdown_limit")}
    money = policy.account.base_currency
    controls: list[dict[str, Any]] = []

    def check(name: str, ok: bool, reason: str, **evidence: Any) -> None:
        controls.append(_control(name, PASS if ok else FAIL, None if ok else reason, **evidence))

    allowed = list(values["allowed_symbols"])
    check("ALLOWED_SYMBOLS", symbol in allowed, "SYMBOL_NOT_ALLOWED", symbol=symbol, allowed_symbols=allowed)
    check("ACCOUNT_CURRENCY", symbol.endswith(money) and len(symbol) > len(money),
          "SYMBOL_NOT_QUOTED_IN_ACCOUNT_CURRENCY", symbol=symbol, money_unit=money)
    headroom = equity * limit["maximum_leverage"] - gross_before
    check("AVAILABLE_CAPITAL", equity > 0, "NO_POSITIVE_EQUITY", equity=_money(equity),
          buying_power_headroom=_money(headroom), order_notional=_money(notional))
    check("ORDER_NOTIONAL", notional <= limit["maximum_order_notional"], "ORDER_NOTIONAL_LIMIT",
          order_notional=_money(notional), limit=_money(limit["maximum_order_notional"]))
    check("POSITION_NOTIONAL", abs(symbol_net) <= limit["maximum_position_notional"], "POSITION_NOTIONAL_LIMIT",
          projected_symbol_net_notional=_money(symbol_net), limit=_money(limit["maximum_position_notional"]),
          weight_of_equity=None if equity <= 0 else _ratio(abs(symbol_net) / equity))
    leverage = None if equity <= 0 else gross_after / equity
    check("GROSS_LEVERAGE", leverage is not None and leverage <= limit["maximum_leverage"], "LEVERAGE_LIMIT",
          gross_before=_money(gross_before), gross_after=_money(gross_after),
          leverage_after=None if leverage is None else _ratio(leverage), limit=_text(limit["maximum_leverage"]))
    controls.append(_control("NET_EXPOSURE", EVIDENCE_ONLY, None, net_before=_money(net_before),
                             net_after=_money(net_after), note="NO_OR11_NET_EXPOSURE_FIELD"))
    day_total = state.day_order_notional + closing_notional + notional
    check("DAILY_ORDER_NOTIONAL", day_total <= limit["maximum_daily_order_notional"], "DAILY_ORDER_NOTIONAL_LIMIT",
          day_order_notional=_money(state.day_order_notional), closing_notional=_money(closing_notional),
          projected=_money(day_total), limit=_money(limit["maximum_daily_order_notional"]))
    day_loss = state.day_start_equity - equity
    check("DAILY_LOSS", day_loss < limit["daily_loss_limit"], "DAILY_LOSS_LIMIT_REACHED",
          day_start_equity=_money(state.day_start_equity), equity=_money(equity), loss=_money(day_loss),
          limit=_money(limit["daily_loss_limit"]))
    drawdown = state.peak_equity - equity
    check("DRAWDOWN", drawdown < limit["maximum_drawdown_limit"], "DRAWDOWN_LIMIT_REACHED",
          peak_equity=_money(state.peak_equity), equity=_money(equity), drawdown=_money(drawdown),
          limit=_money(limit["maximum_drawdown_limit"]))
    max_age = timedelta(seconds=int(values["max_market_age_seconds"]))
    knowledge = request.market_knowledge_at
    if knowledge is None:
        controls.append(_control("MARKET_AGE", UNRESOLVED_CONTROL, "MARKET_AGE_UNASSESSABLE_SIGNAL_EVIDENCE_MISSING"))
    elif knowledge > request.as_of:
        controls.append(_control("MARKET_AGE", FAIL, "MARKET_KNOWLEDGE_AFTER_AS_OF",
                                 market_knowledge_at=knowledge.isoformat()))
    else:
        age = request.as_of - knowledge
        check("MARKET_AGE", age <= max_age, "STALE_MARKET_DATA", age_seconds=_text(Decimal(age // _MICRO) / 10**6),
              limit_seconds=int(values["max_market_age_seconds"]))
    for name in sorted(NOT_APPLICABLE_FIELDS_V1):
        control = f"EVIDENCE:{name}"
        if values[name] == NOT_APPLICABLE_V1:
            if real:
                controls.append(_control(control, FAIL, "NOT_APPLICABLE_NEVER_SATISFIES_REAL_EXECUTION",
                                         owner_value=NOT_APPLICABLE_V1))
            else:
                controls.append(_control(control, NOT_APPLICABLE, None, owner_value=NOT_APPLICABLE_V1,
                                         basis="OWNER_DECLARED_PERSONAL_PAPER_V1"))
        else:
            controls.append(_control(control, FAIL, _EVIDENCE_REASONS[name], owner_value=values[name]))
    return "OPEN", controls, closing_quantity, notional


def _size(request: PaperRiskRequestV1, policy: AccountPolicyV1, notional: Decimal,
          closing_quantity: Decimal | None) -> tuple[dict[str, Any] | None, str | None]:
    price, known = request.reference_price, request.reference_price_known_at
    if price is None or known is None:
        return None, "REFERENCE_PRICE_MISSING"
    if known > request.as_of:
        return None, "REFERENCE_PRICE_KNOWN_AFTER_AS_OF"
    if request.as_of - known > timedelta(seconds=int(policy.values["max_market_age_seconds"])):
        return None, "REFERENCE_PRICE_STALE"
    quantity = (notional / price).quantize(QUANTITY_QUANTUM_V1, rounding=ROUND_DOWN)
    if quantity == 0:
        return None, "ZERO_QUANTITY_AFTER_ROUNDING"
    sign = 1 if request.target_to > 0 else -1
    return {"basis": "PAPER_ORDER_NOTIONAL_PER_UNIT_TARGET_OVER_REFERENCE_PRICE",
            "money_unit": policy.account.base_currency,
            "order_notional": _money(notional), "reference_price": _text(price),
            "reference_price_known_at": known.isoformat(),
            "open_quantity": _text(quantity * sign),
            "booked_notional": _money(quantity * price),
            "close_quantity": None if closing_quantity is None else _text(closing_quantity)}, None


# --------------------------------------------------------------------------- repository read path


def read_paper_risk_assessment_v1(cursor: Any, request: PaperRiskRequestV1, *, cycle_id: str) -> dict[str, Any]:
    """Resolve the policy from stored rows, derive the cycle's paper state, assess (read-only)."""
    cursor.execute("SELECT kind, display_name, base_currency FROM account_contexts WHERE account_id=%s",
                   (request.account_id,))
    account_row = cursor.fetchone()
    cursor.execute("SELECT policy_version_id, content_hash, account_id, status, identity, recorded_at "
                   "FROM account_policy_versions WHERE account_id=%s ORDER BY recorded_at, policy_version_id",
                   (request.account_id,))
    resolution = resolve_account_policy_v1(request.account_id, account_row, cursor.fetchall(), as_of=request.as_of,
                                           expected_policy_version_id=request.expected_policy_version_id)
    state: PaperAccountStateV1 | None = None
    state_error: str | None = None
    if resolution.usable and resolution.policy is not None and (
            resolution.policy.account.kind is AccountKindV1.PERSONAL_PAPER):
        try:
            fills, signals = fills_signals_from_rows(*cycle_fill_rows_v1(cursor, cycle_id))
            state = paper_state_from_fills_v1(resolution.policy, fills, signals,
                                              cycle_cost_policy_v1(cursor, cycle_id), as_of=request.as_of)
        except (PaperAccountError, KeyError, TypeError, ValueError):
            state_error = "PAPER_STATE_UNDERIVABLE"
    return assess_paper_risk_v1(request, resolution, state, state_error=state_error)


__all__ = [
    "ALLOWED",
    "BLOCKED",
    "CONTROLS_V1",
    "PAPER_RISK_ASSESSMENT_SCHEMA_VERSION_V1",
    "UNRESOLVED",
    "ExecutionContextV1",
    "PaperAccountStateV1",
    "PaperPositionV1",
    "PaperRiskAssessmentError",
    "PaperRiskRequestV1",
    "PendingOrderV1",
    "PolicyResolutionV1",
    "assess_paper_risk_v1",
    "paper_state_from_fills_v1",
    "read_paper_risk_assessment_v1",
    "resolve_account_policy_v1",
]
