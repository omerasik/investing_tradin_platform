"""Research terminal commands -- guarded, identity-bound requests to existing authorities.

``RESEARCH_ONLY``. The terminal's write surface. A command is one immutable
request of a closed set of kinds with explicit, typed inputs and an idempotency
key; its outcome is a chain of append-only events (migration 20261009_0062).
This module adds no new authority and no orchestration of its own: every kind
is carried out by the authority that already owns it --

==========================  ====================================================
``STRATEGY_SEARCH``          Strategy Lab study registration + leased worker pool
``CANDIDATE_FREEZE``         study manifest + frozen top-k (explicit metric, k)
``DECIMAL_RERUN``            OR-3 Decimal authority rerun of a frozen set
``PREREGISTRATION_RECORD``   R6 preregistration (DRAFT until every owner field)
``HOLDOUT_OPEN``             R6 one-shot opening of an AUTHORIZED packet
``HOLDOUT_VALIDATE``         R6 holdout validation of the opened packet
``WATCHLIST_RECORD``         OR-9 watch list version (inline)
``ACCOUNT_REGISTER``         R9 paper account context (inline)
``ACCOUNT_POLICY_RECORD``    R9 account policy version, owner values only (inline)
``RESEARCH_WATCH_START/STOP``  R8 runner for an ACTIVE watch list
``PAPER_INCUBATION_START/STOP``  R10 runner for INCUBATING candidates
==========================  ====================================================

Inline kinds are light and run inside the protected API; the others are claimed
by ``scripts/research_terminal_worker.py``, which calls the same functions the
operator CLIs call. Before a command is accepted, and again when the worker
claims it, its gate is read from :mod:`trade_platform.activation_readiness_v1`:
a blocked command records ``BLOCKED`` with the exact reasons (for example
``BLOCKED_OWNER_DECISION_OR_7:...``) and never runs. No input has a default that
carries strategy or economic meaning: metric, direction, top-k, worker count,
every owner field and every approval are explicit or the request is refused
(owner fields may be absent only where the authority records a DRAFT).

There is no order, broker, account-credential or live-trading kind.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Final, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .persistence import PersistenceError, PostgresDatabase
from .strategy_lab_study_v1 import identity_hash_v1

COMMAND_SCHEMA_VERSION_V1: Final = "research-terminal-command-v1"
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.research_terminal_commands_v1")

#: Kinds the protected API executes itself (light, no numerical libraries).
INLINE_KINDS: Final = frozenset({"WATCHLIST_RECORD", "ACCOUNT_REGISTER", "ACCOUNT_POLICY_RECORD"})
#: Kinds that record or act on an owner decision; they need the REVIEW_RISK permission.
OWNER_KINDS: Final = frozenset({"WATCHLIST_RECORD", "ACCOUNT_REGISTER", "ACCOUNT_POLICY_RECORD",
                                "PREREGISTRATION_RECORD", "HOLDOUT_OPEN"})
TERMINAL_STATES: Final = frozenset({"BLOCKED", "SUCCEEDED", "FAILED", "STOPPED", "EXITED"})
_FAMILY: Final = r"^[a-z][a-z0-9_]{2,63}$"
_SYMBOL: Final = r"^[A-Z0-9]{2,20}$"
_HASH: Final = r"^[0-9a-f]{64}$"


class TerminalCommandError(ValueError):
    """Raised when a command request is malformed, conflicting or not executable."""


class _Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Window(_Inputs):
    family: str = Field(pattern=_FAMILY)
    dataset_version_id: UUID


class StrategySearchInputs(_Window):
    workers: int = Field(ge=1, le=4)  # host constraint: research shares the box with capture


class CandidateSelectionInputs(_Window):
    metric: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    direction: Literal["HIGHER_IS_BETTER", "LOWER_IS_BETTER"]
    top_k: int = Field(ge=1, le=50)


class DecimalRerunInputs(CandidateSelectionInputs):
    workers: int = Field(ge=1, le=4)


class CriterionInputs(_Inputs):
    metric: str = Field(min_length=1, max_length=64)
    comparator: Literal[">", ">=", "<", "<="]
    threshold: str = Field(min_length=1, max_length=40)


class PreregistrationInputs(_Window):
    rerun_hash: str = Field(pattern=_HASH)
    symbol: str = Field(pattern=_SYMBOL)
    cycle_id: str = Field(pattern=r"^cycle-\d{4}-\d{2}-\d{2}$")
    holdout_end_exclusive: date | None = None
    acceptance_criteria: list[CriterionInputs] = Field(default_factory=list, max_length=10)
    minimum_trades: int | None = Field(default=None, ge=1)
    cost_policy: dict[str, Any] | None = None
    incubation_days: int | None = Field(default=None, ge=1)
    authorized_by: str | None = Field(default=None, min_length=1, max_length=120)
    authorized_on: date | None = None

    @field_validator("cost_policy")
    @classmethod
    def _bounded_cost_policy(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > 8_192:
            raise ValueError("cost_policy_too_large")
        return value


class HoldoutOpenInputs(_Inputs):
    preregistration_hash: str = Field(pattern=_HASH)
    confirm_cycle_id: str = Field(pattern=r"^cycle-\d{4}-\d{2}-\d{2}$")
    opened_by: str = Field(min_length=1, max_length=120)


class HoldoutValidateInputs(_Inputs):
    preregistration_hash: str = Field(pattern=_HASH)


class WatchEntryInputs(_Inputs):
    study_id: UUID
    rerun_hash: str = Field(pattern=_HASH)
    trial_id: UUID
    symbol: str = Field(pattern=_SYMBOL)


class WatchlistInputs(_Inputs):
    watchlist_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    entries: list[WatchEntryInputs] = Field(max_length=30)
    approved_by: str | None = Field(default=None, min_length=1, max_length=120)
    approved_on: date | None = None
    note: str = Field(default="", max_length=500)


class AccountRegisterInputs(_Inputs):
    account_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    kind: Literal["PERSONAL_PAPER", "PROP_PAPER"]
    display_name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(pattern=r"^[A-Z]{3,5}$")


class AccountPolicyInputs(_Inputs):
    account_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    values: dict[str, str | int | list[str]] = Field(max_length=32)
    approved_by: str | None = Field(default=None, min_length=1, max_length=120)
    approved_on: date | None = None

    @field_validator("values")
    @classmethod
    def _bounded_values(cls, value: dict[str, str | int | list[str]]) -> dict[str, str | int | list[str]]:
        for name, item in value.items():
            if len(name) > 64 or (isinstance(item, str) and len(item) > 64) or (
                    isinstance(item, list) and (len(item) > 20 or any(len(s) > 20 for s in item))):
                raise ValueError("account_policy_value_too_large")
        return value


class RunnerStartInputs(_Window):
    symbol: str = Field(pattern=_SYMBOL)
    watchlist_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")


class RunnerStopInputs(_Inputs):
    start_command_id: UUID


INPUT_MODELS: Final[dict[str, type[_Inputs]]] = {
    "STRATEGY_SEARCH": StrategySearchInputs,
    "CANDIDATE_FREEZE": CandidateSelectionInputs,
    "DECIMAL_RERUN": DecimalRerunInputs,
    "PREREGISTRATION_RECORD": PreregistrationInputs,
    "HOLDOUT_OPEN": HoldoutOpenInputs,
    "HOLDOUT_VALIDATE": HoldoutValidateInputs,
    "WATCHLIST_RECORD": WatchlistInputs,
    "ACCOUNT_REGISTER": AccountRegisterInputs,
    "ACCOUNT_POLICY_RECORD": AccountPolicyInputs,
    "RESEARCH_WATCH_START": RunnerStartInputs,
    "RESEARCH_WATCH_STOP": RunnerStopInputs,
    "PAPER_INCUBATION_START": RunnerStartInputs,
    "PAPER_INCUBATION_STOP": RunnerStopInputs,
}

#: Which readiness answer gates a kind before it may run (``None``: the authority itself checks).
READINESS_GATES: Final[dict[str, str | None]] = {
    "STRATEGY_SEARCH": "research_run",
    "CANDIDATE_FREEZE": None,  # needs the study id; the worker checks the study is finished
    "DECIMAL_RERUN": None,
    "PREREGISTRATION_RECORD": None,  # a DRAFT is always recordable; the packet names its gaps
    "HOLDOUT_OPEN": "holdout_open",
    "HOLDOUT_VALIDATE": None,
    "WATCHLIST_RECORD": None,
    "ACCOUNT_REGISTER": None,
    "ACCOUNT_POLICY_RECORD": None,
    "RESEARCH_WATCH_START": "research_watch",
    "RESEARCH_WATCH_STOP": None,
    "PAPER_INCUBATION_START": "paper_incubation",
    "PAPER_INCUBATION_STOP": None,
}


def parse_inputs_v1(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Typed, canonical inputs for ``kind``; anything unknown, missing or malformed is refused."""
    model = INPUT_MODELS.get(kind)
    if model is None:
        raise TerminalCommandError(f"unknown_command_kind:{kind}")
    if kind == "RESEARCH_WATCH_START" and not raw.get("watchlist_id"):
        raise TerminalCommandError("research_watch_requires_the_owners_watchlist_id_OR_9")
    try:
        parsed = model.model_validate(dict(raw))
    except ValidationError as error:
        fields = sorted({".".join(str(p) for p in item["loc"]) for item in error.errors()})
        raise TerminalCommandError("invalid_command_inputs:" + ",".join(fields)) from error
    return json.loads(parsed.model_dump_json())


def command_identity_v1(kind: str, inputs: Mapping[str, Any], idempotency_key: str) -> tuple[UUID, str]:
    if not re.fullmatch(r"[A-Za-z0-9:_.-]{1,200}", idempotency_key):
        raise TerminalCommandError("idempotency_key_malformed")
    content_hash = identity_hash_v1({"schema_version": COMMAND_SCHEMA_VERSION_V1, "kind": kind,
                                     "inputs": dict(inputs)})
    return uuid5(_NAMESPACE, f"{idempotency_key}:{content_hash}"), content_hash


def readiness_cycle_v1(kind: str, inputs: Mapping[str, Any]) -> str | None:
    """The cycle whose readiness gates ``kind`` (``None``: the current cycle)."""
    return str(inputs["confirm_cycle_id"]) if kind == "HOLDOUT_OPEN" else None


def gate_reasons_v1(kind: str, inputs: Mapping[str, Any], readiness: Any) -> list[str]:
    """The readiness reasons that block ``kind`` now (empty when it may run)."""
    key = READINESS_GATES[kind]
    if key is None:
        return []
    answer = next(a for a in readiness.answers if a.key == key)
    if kind == "HOLDOUT_OPEN":
        if inputs["confirm_cycle_id"] != readiness.cycle_id:
            return [f"HOLDOUT_CONFIRMATION_DOES_NOT_NAME_THIS_CYCLE:{readiness.cycle_id}"]
        mine = [s for s in answer.subjects if s.identities.get("preregistration_hash") == inputs["preregistration_hash"]]
        if answer.status != "READY" or not mine or mine[0].status != "READY":
            return list(answer.reasons) or [r for s in mine for r in s.reasons] or ["PREREGISTRATION_NOT_AUTHORIZED"]
        return []
    return [] if answer.status == "READY" else list(answer.reasons)


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class CommandView(BaseModel):
    command_id: UUID
    kind: str
    inputs: dict[str, Any]
    requested_by: str
    requested_at: datetime
    state: str
    detail: dict[str, Any]
    updated_at: datetime


#: Owner approval fields; each must name the authenticated subject who requests the command.
_OWNER_FIELDS: Final = ("authorized_by", "approved_by", "opened_by")


def _unique_violation(error: BaseException) -> bool:
    cause = error.__cause__
    return cause is not None and getattr(cause, "sqlstate", None) == "23505"


def _event(cursor: Any, command_id: UUID, state: str, detail: Mapping[str, Any], actor: str) -> None:
    cursor.execute(
        "INSERT INTO research_terminal_command_events (event_id, command_id, state, detail, actor, occurred_at) "
        "VALUES (%s,%s,%s,%s::jsonb,%s,%s)",
        (uuid4(), command_id, state, json.dumps(dict(detail), sort_keys=True, default=str), actor, datetime.now(UTC)))


def _latest(cursor: Any, command_id: UUID) -> tuple[str, dict[str, Any], datetime] | None:
    cursor.execute("SELECT state, detail, occurred_at FROM research_terminal_command_events WHERE command_id=%s "
                   "ORDER BY occurred_at DESC, event_id DESC LIMIT 1", (command_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    detail = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return str(row[0]), detail, row[2]


class PostgresTerminalCommandLedgerV1:
    """Requests and outcome events (migration 20261009_0062)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @property
    def database(self) -> PostgresDatabase:
        return self._database

    def request(self, kind: str, raw_inputs: Mapping[str, Any], *, idempotency_key: str, requested_by: str,
                readiness: Any) -> CommandView:
        """Record a request (idempotent per key) and its gate verdict: REQUESTED or BLOCKED."""
        inputs = parse_inputs_v1(kind, raw_inputs)
        command_id, content_hash = command_identity_v1(kind, inputs, idempotency_key)
        subject = requested_by.strip()
        if not subject:
            raise TerminalCommandError("requested_by_required")
        for name in _OWNER_FIELDS:
            # An approval is the authenticated principal's own act, never free text typed for someone else.
            if inputs.get(name) is not None and inputs[name] != subject:
                raise TerminalCommandError(f"{name}_must_be_the_authenticated_subject")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT command_id FROM research_terminal_commands WHERE idempotency_key=%s",
                               (idempotency_key,))
                existing = cursor.fetchone()
                if existing is not None:
                    if existing[0] != command_id:
                        raise TerminalCommandError("idempotency_key_reused_for_a_different_command")
                else:
                    cursor.execute(
                        "INSERT INTO research_terminal_commands (command_id, idempotency_key, kind, inputs, "
                        "content_hash, requested_by, requested_at) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)",
                        (command_id, idempotency_key, kind, json.dumps(inputs, sort_keys=True), content_hash,
                         subject, datetime.now(UTC)))
                    reasons = gate_reasons_v1(kind, inputs, readiness)
                    if reasons:
                        _event(cursor, command_id, "BLOCKED", {"reasons": reasons, "at": "request",
                                                              "readiness_state_hash": readiness.state_hash}, "api")
                    else:
                        _event(cursor, command_id, "REQUESTED", {"readiness_state_hash": readiness.state_hash}, "api")
        except PersistenceError as error:
            if not _unique_violation(error):
                raise
            # A concurrent request with the same key won the insert: it is the same command or a conflict.
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT command_id FROM research_terminal_commands WHERE idempotency_key=%s",
                               (idempotency_key,))
                row = cursor.fetchone()
            if row is None or row[0] != command_id:
                raise TerminalCommandError("idempotency_key_reused_for_a_different_command") from error
        return self.get(command_id)

    def record(self, command_id: UUID, state: str, detail: Mapping[str, Any], *, actor: str) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                latest = _latest(cursor, command_id)
                if latest is None:
                    raise TerminalCommandError("command_not_found")
                if latest[0] in TERMINAL_STATES:
                    raise TerminalCommandError(f"command_already_terminal:{latest[0]}")
                _event(cursor, command_id, state, detail, actor)
        except PersistenceError as error:
            if _unique_violation(error):  # a concurrent writer recorded the outcome first
                raise TerminalCommandError("command_outcome_already_recorded") from error
            raise

    def claim(self, *, worker: str, kinds: frozenset[str]) -> CommandView | None:
        """The oldest REQUESTED command of ``kinds``. The one-claim index makes a second claimer lose."""
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT c.command_id FROM research_terminal_commands c WHERE c.kind = ANY(%s) AND "
                    "(SELECT e.state FROM research_terminal_command_events e WHERE e.command_id = c.command_id "
                    " ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1) = 'REQUESTED' "
                    "ORDER BY c.requested_at, c.command_id LIMIT 1 FOR UPDATE OF c SKIP LOCKED",
                    (sorted(kinds),))
                row = cursor.fetchone()
                if row is None:
                    return None
                _event(cursor, row[0], "CLAIMED", {"worker": worker}, worker)
        except PersistenceError as error:
            if _unique_violation(error):
                return None
            raise
        return self.get(row[0])

    def abandon_stale_claims(self, *, worker_prefix: str, actor: str) -> int:
        """At worker start: a CLAIMED command of a previous worker on this host never finished -> FAILED."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.command_id FROM research_terminal_commands c WHERE "
                "(SELECT e.state || '|' || COALESCE(e.detail->>'worker','') FROM research_terminal_command_events e "
                " WHERE e.command_id = c.command_id ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1) "
                "LIKE %s", (f"CLAIMED|{worker_prefix}%",))
            stale = [r[0] for r in cursor.fetchall()]
            for command_id in stale:
                _event(cursor, command_id, "FAILED", {"reason": "WORKER_RESTARTED_BEFORE_COMPLETION_REQUEST_AGAIN"},
                       actor)
        return len(stale)

    def get(self, command_id: UUID) -> CommandView:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            views = read_commands_v1(cursor, command_id=command_id)
        if not views:
            raise TerminalCommandError("command_not_found")
        return views[0]

    def running(self, kinds: frozenset[str]) -> list[CommandView]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            return [v for v in read_commands_v1(cursor, limit=500) if v.kind in kinds and v.state == "RUNNING"]


def read_commands_v1(cursor: Any, *, limit: int = 50, command_id: UUID | None = None) -> list[CommandView]:
    """Commands newest first with their latest event (read-only)."""
    where, params = ("WHERE c.command_id=%s", (command_id,)) if command_id else ("", ())
    # where is one of two fixed literals; every value is a bound parameter.
    cursor.execute(
        "SELECT c.command_id, c.kind, c.inputs, c.requested_by, c.requested_at, e.state, e.detail, e.occurred_at "  # nosec B608
        "FROM research_terminal_commands c JOIN LATERAL (SELECT state, detail, occurred_at FROM "
        "research_terminal_command_events x WHERE x.command_id = c.command_id ORDER BY occurred_at DESC, "
        f"event_id DESC LIMIT 1) e ON TRUE {where} ORDER BY c.requested_at DESC, c.command_id LIMIT %s",
        (*params, limit))
    out = []
    for row in cursor.fetchall():
        inputs = row[2] if isinstance(row[2], dict) else json.loads(row[2])
        detail = row[6] if isinstance(row[6], dict) else json.loads(row[6])
        out.append(CommandView(command_id=row[0], kind=str(row[1]), inputs=inputs, requested_by=str(row[3]),
                               requested_at=row[4], state=str(row[5]), detail=detail, updated_at=row[7]))
    return out


# ---------------------------------------------------------------------------
# Inline execution (light authorities only)
# ---------------------------------------------------------------------------


def execute_inline_v1(database: PostgresDatabase, command: CommandView) -> dict[str, Any]:
    """Run a light owner-record kind through its own authority. Raises on refusal."""
    inputs = command.inputs
    if command.kind == "WATCHLIST_RECORD":
        from .research_watchlist_v1 import (
            PostgresResearchWatchlistStoreV1,
            ResearchWatchlistV1,
            WatchEntryV1,
        )

        watchlist = ResearchWatchlistV1(
            inputs["watchlist_id"], tuple(WatchEntryV1.from_payload(e) for e in inputs["entries"]),
            inputs.get("approved_by"), inputs.get("approved_on"), inputs.get("note", ""))
        recorded = PostgresResearchWatchlistStoreV1(database).record(watchlist)
        return {"watchlist_hash": watchlist.content_hash, "status": watchlist.status,
                "unresolved": list(watchlist.unresolved), "newly_recorded": recorded}
    if command.kind in {"ACCOUNT_REGISTER", "ACCOUNT_POLICY_RECORD"}:
        from .account_policy_v1 import (
            AccountContextV1,
            AccountKindV1,
            AccountPolicyV1,
            PostgresAccountPolicyStoreV1,
        )

        store = PostgresAccountPolicyStoreV1(database)
        if command.kind == "ACCOUNT_REGISTER":
            account = AccountContextV1(inputs["account_id"], AccountKindV1(inputs["kind"]), inputs["display_name"],
                                       inputs["base_currency"])
            store.register_account(account)
            return {"account_id": account.account_id}
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT kind, display_name, base_currency FROM account_contexts WHERE account_id=%s",
                           (inputs["account_id"],))
            row = cursor.fetchone()
        if row is None:
            raise TerminalCommandError("account_not_registered")
        account = AccountContextV1(inputs["account_id"], AccountKindV1(str(row[0])), str(row[1]), str(row[2]))
        policy = AccountPolicyV1(account, inputs["values"], inputs.get("approved_by"), inputs.get("approved_on"))
        recorded = store.record_policy(policy)
        return {"policy_version_id": str(policy.policy_version_id), "status": policy.status,
                "unresolved": list(policy.unresolved), "newly_recorded": recorded}
    raise TerminalCommandError(f"not_an_inline_kind:{command.kind}")


__all__ = [
    "INLINE_KINDS",
    "INPUT_MODELS",
    "OWNER_KINDS",
    "READINESS_GATES",
    "TERMINAL_STATES",
    "CommandView",
    "PostgresTerminalCommandLedgerV1",
    "TerminalCommandError",
    "command_identity_v1",
    "execute_inline_v1",
    "gate_reasons_v1",
    "parse_inputs_v1",
    "read_commands_v1",
    "readiness_cycle_v1",
]
