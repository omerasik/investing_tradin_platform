"""Phase R9 -- account contexts and owner-controlled account policies (no economic defaults).

``RESEARCH_ONLY`` / paper only. An :class:`AccountContextV1` names one paper
account (personal or a future prop account); an :class:`AccountPolicyV1` is a
content-addressed, owner-approved version of *every* economic value that
account's risk and sizing use: paper capital, sizing, leverage, loss and
drawdown limits, order/position/daily notional caps, data-quality, spread,
slippage, event-risk and market-age limits, the per-trade stop controls, the
allowed symbols and, for a prop account, the prop firm's own rules.

Nothing here has a default. Every missing value is an ``MISSING_OWNER_*_OR_11``
reason and the policy is ``UNCONFIGURED``; only a complete, owner-approved
version is ``ACTIVE``, and only an active version yields a
:class:`~trade_platform.domain.RiskPolicy` -- built field by field from the
policy, never from the dataclass defaults. The existing
:class:`~trade_platform.risk.RiskEngine` and pre-trade assessment then enforce
it unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .domain import RiskPolicy
from .persistence import PostgresDatabase
from .strategy_lab_study_v1 import identity_hash_v1

ACCOUNT_POLICY_SCHEMA_VERSION_V1: Final = "account-policy-v1"
STATUS_ACTIVE: Final = "ACTIVE"
STATUS_UNCONFIGURED: Final = "UNCONFIGURED"

_SLUG: Final = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.account_policy_v1")


class AccountPolicyError(ValueError):
    """Raised when an account or policy is malformed or used before it is complete."""


class AccountKindV1(StrEnum):
    PERSONAL_PAPER = "PERSONAL_PAPER"
    PROP_PAPER = "PROP_PAPER"


#: name -> (kind, constraint). Every one is an owner decision (OR-11).
RISK_FIELDS_V1: Final[dict[str, tuple[str, str]]] = {
    "minimum_data_quality": ("decimal", "unit"),
    "maximum_spread_fraction": ("decimal", "unit"),
    "maximum_order_notional": ("decimal", "positive"),
    "maximum_position_notional": ("decimal", "positive"),
    "maximum_daily_order_notional": ("decimal", "positive"),
    "maximum_event_risk": ("decimal", "unit"),
    "maximum_expected_slippage_fraction": ("decimal", "unit"),
    "max_market_age_seconds": ("int", "positive"),
    "maximum_per_trade_loss": ("decimal", "positive"),
    "maximum_stop_distance_fraction": ("decimal", "unit_positive"),
    "stop_gap_buffer_fraction": ("decimal", "unit_below_one"),
}
ACCOUNT_FIELDS_V1: Final[dict[str, tuple[str, str]]] = {
    "paper_starting_capital": ("decimal", "positive"),
    "paper_order_notional": ("decimal", "positive"),
    "maximum_leverage": ("decimal", "positive"),
    "daily_loss_limit": ("decimal", "positive"),
    "maximum_drawdown_limit": ("decimal", "positive"),
    "allowed_symbols": ("symbols", "nonempty"),
}
PROP_FIELDS_V1: Final[dict[str, tuple[str, str]]] = {
    "prop_firm": ("text", "nonempty"),
    "prop_daily_loss_limit": ("decimal", "positive"),
    "prop_max_trailing_drawdown": ("decimal", "positive"),
    "prop_profit_target": ("decimal", "positive"),
    "prop_min_trading_days": ("int", "positive"),
}


def _parse(name: str, kind: str, constraint: str, raw: object) -> Any:
    if isinstance(raw, (bool, float)):
        raise AccountPolicyError(f"{name}_must_be_text_or_int_never_float")
    if kind == "text":
        if not isinstance(raw, str) or not raw.strip():
            raise AccountPolicyError(f"{name}_must_be_nonblank_text")
        return raw.strip()
    if kind == "symbols":
        if not isinstance(raw, (list, tuple)) or not raw or not all(
            isinstance(item, str) and re.fullmatch(r"[A-Z0-9]{2,20}", item) for item in raw
        ):
            raise AccountPolicyError(f"{name}_must_be_a_nonempty_symbol_list")
        return sorted(set(raw))
    if kind == "int":
        if not isinstance(raw, int) or raw < 1:
            raise AccountPolicyError(f"{name}_must_be_a_positive_int")
        return raw
    try:
        value = Decimal(str(raw))
    except InvalidOperation as error:
        raise AccountPolicyError(f"{name}_unparseable") from error
    if not value.is_finite():
        raise AccountPolicyError(f"{name}_must_be_finite")
    ok = {
        "positive": value > 0,
        "unit": Decimal(0) <= value <= Decimal(1),
        "unit_positive": Decimal(0) < value <= Decimal(1),
        "unit_below_one": Decimal(0) <= value < Decimal(1),
    }[constraint]
    if not ok:
        raise AccountPolicyError(f"{name}_out_of_range")
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


@dataclass(frozen=True, slots=True)
class AccountContextV1:
    account_id: str
    kind: AccountKindV1
    display_name: str
    base_currency: str

    def __post_init__(self) -> None:
        if not _SLUG.match(self.account_id):
            raise AccountPolicyError("account_id_must_be_a_lowercase_slug")
        if not isinstance(self.kind, AccountKindV1):
            raise AccountPolicyError("account_kind_unknown")
        if not self.display_name.strip() or not re.fullmatch(r"[A-Z]{3,5}", self.base_currency):
            raise AccountPolicyError("account_needs_a_name_and_a_currency_code")

    def payload(self) -> dict[str, str]:
        return {"account_id": self.account_id, "kind": self.kind.value, "display_name": self.display_name,
                "base_currency": self.base_currency}


@dataclass(frozen=True)
class AccountPolicyV1:
    account: AccountContextV1
    values: Mapping[str, Any]
    approved_by: str | None = None
    approved_on: str | None = None

    def __post_init__(self) -> None:
        required = self.required_fields()
        unknown = set(self.values) - set(required)
        if unknown:
            raise AccountPolicyError(f"unknown_policy_fields:{','.join(sorted(unknown))}")
        parsed = {name: _parse(name, *required[name], raw) for name, raw in sorted(self.values.items())}
        if self.approved_on is not None:
            date.fromisoformat(self.approved_on)
        object.__setattr__(self, "values", parsed)

    def required_fields(self) -> dict[str, tuple[str, str]]:
        fields = {**RISK_FIELDS_V1, **ACCOUNT_FIELDS_V1}
        if self.account.kind is AccountKindV1.PROP_PAPER:
            fields.update(PROP_FIELDS_V1)
        return fields

    @property
    def unresolved(self) -> tuple[str, ...]:
        reasons = [f"MISSING_OWNER_{name.upper()}_OR_11" for name in self.required_fields() if name not in self.values]
        if not (self.approved_by or "").strip() or not self.approved_on:
            reasons.append("MISSING_OWNER_APPROVAL_OR_11")
        return tuple(reasons)

    @property
    def status(self) -> str:
        return STATUS_ACTIVE if not self.unresolved else STATUS_UNCONFIGURED

    def identity(self) -> dict[str, Any]:
        return {"schema_version": ACCOUNT_POLICY_SCHEMA_VERSION_V1, "account": self.account.payload(),
                "values": dict(self.values), "approved_by": self.approved_by, "approved_on": self.approved_on,
                "status": self.status, "unresolved": list(self.unresolved)}

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    @property
    def policy_version_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"account-policy:{self.content_hash}")

    def risk_policy(self) -> RiskPolicy:
        """The RiskEngine policy, every field explicit from this version. Refused unless ACTIVE."""
        if self.status != STATUS_ACTIVE:
            raise AccountPolicyError("account_policy_not_active:" + ",".join(self.unresolved))
        v = self.values
        return RiskPolicy(
            minimum_data_quality=Decimal(v["minimum_data_quality"]),
            maximum_spread_fraction=Decimal(v["maximum_spread_fraction"]),
            maximum_order_notional=Decimal(v["maximum_order_notional"]),
            maximum_position_notional=Decimal(v["maximum_position_notional"]),
            maximum_daily_order_notional=Decimal(v["maximum_daily_order_notional"]),
            maximum_event_risk=Decimal(v["maximum_event_risk"]),
            maximum_expected_slippage_fraction=Decimal(v["maximum_expected_slippage_fraction"]),
            max_market_age_seconds=int(v["max_market_age_seconds"]),
            maximum_per_trade_loss=Decimal(v["maximum_per_trade_loss"]),
            maximum_stop_distance_fraction=Decimal(v["maximum_stop_distance_fraction"]),
            stop_gap_buffer_fraction=Decimal(v["stop_gap_buffer_fraction"]),
        )

    def risk_policy_document_payload(self) -> dict[str, Any]:
        """The same values in the policy-registry document shape (strict resolution reads every one)."""
        if self.status != STATUS_ACTIVE:
            raise AccountPolicyError("account_policy_not_active")
        return {name: self.values[name] for name in RISK_FIELDS_V1}


class PostgresAccountPolicyStoreV1:
    """Accounts and append-only policy versions (migration 20261008_0059)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register_account(self, account: AccountContextV1) -> None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO account_contexts (account_id, kind, display_name, base_currency, created_at) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (account_id) DO NOTHING",
                (account.account_id, account.kind.value, account.display_name, account.base_currency,
                 datetime.now(UTC)),
            )
            cursor.execute("SELECT kind, display_name, base_currency FROM account_contexts WHERE account_id=%s",
                           (account.account_id,))
            row = cursor.fetchone()
        if row is None or (row[0], row[1], row[2]) != (account.kind.value, account.display_name,
                                                        account.base_currency):
            raise AccountPolicyError("account_context_conflict")

    def record_policy(self, policy: AccountPolicyV1) -> bool:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO account_policy_versions (policy_version_id, content_hash, account_id, status, identity, "
                "recorded_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (policy_version_id) DO NOTHING "
                "RETURNING policy_version_id",
                (policy.policy_version_id, policy.content_hash, policy.account.account_id, policy.status,
                 json.dumps(policy.identity(), sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def latest_active(self, account: AccountContextV1) -> AccountPolicyV1 | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT identity FROM account_policy_versions WHERE account_id=%s AND status='ACTIVE' "
                           "ORDER BY recorded_at DESC, policy_version_id LIMIT 1", (account.account_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        identity = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        policy = AccountPolicyV1(account, identity["values"], identity["approved_by"], identity["approved_on"])
        if policy.identity() != identity:
            raise AccountPolicyError("stored_account_policy_does_not_reproduce")
        return policy


__all__ = [
    "ACCOUNT_FIELDS_V1",
    "PROP_FIELDS_V1",
    "RISK_FIELDS_V1",
    "STATUS_ACTIVE",
    "STATUS_UNCONFIGURED",
    "AccountContextV1",
    "AccountKindV1",
    "AccountPolicyError",
    "AccountPolicyV1",
    "PostgresAccountPolicyStoreV1",
]
