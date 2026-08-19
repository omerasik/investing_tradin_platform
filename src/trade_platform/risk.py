"""Independent deterministic risk approval for internal paper order intents."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .domain import (
    MarketSnapshot,
    OrderIntent,
    PortfolioState,
    RiskDecision,
    RiskDecisionType,
    RiskPolicy,
    Signal,
    SignalStatus,
    utc_now,
)
from .operational_alerts import AlertSeverity, PostgresOperationalAlertStore
from .persistence import PersistenceError, PostgresDatabase


class KillSwitchRegistry:
    """Explicit kill switches; the registry never auto-reactivates a scope."""

    def __init__(self) -> None:
        self._active_scopes: set[str] = set()

    def activate(self, scope: str) -> None:
        _validate_kill_switch_scope(scope)
        self._active_scopes.add(scope)

    def deactivate(self, scope: str, *, explicit_operator_approval: bool) -> None:
        if not explicit_operator_approval:
            raise PermissionError("Kill switch reactivation requires explicit operator approval.")
        _validate_kill_switch_scope(scope)
        self._active_scopes.discard(scope)

    def blocks(
        self,
        instrument_id: str,
        strategy_version: str,
        *,
        account_id: str | None = None,
        broker_name: str | None = None,
        exchange: str | None = None,
        asset_class: str | None = None,
    ) -> bool:
        return bool(
            _kill_switch_candidates(
                instrument_id, strategy_version, account_id, broker_name, exchange, asset_class
            )
            & self._active_scopes
        )


@dataclass(frozen=True, slots=True)
class PreTradeExecutionContext:
    """Evidence from the execution/broker gates required for every risk assessment."""

    session_open: bool
    instrument_halted: bool
    quote_available: bool
    expected_slippage_fraction: Decimal
    event_risk: Decimal
    available_buying_power: Decimal
    broker_state_certain: bool
    strategy_enabled: bool
    model_approved: bool

    def validate(self) -> None:
        if (
            self.expected_slippage_fraction < 0
            or not Decimal(0) <= self.event_risk <= Decimal(1)
            or self.available_buying_power < 0
        ):
            raise ValueError("invalid_pre_trade_execution_context")


@dataclass(frozen=True, slots=True)
class PerTradeRiskEvidence:
    """Deterministic loss-at-stop and policy-buffered gap-loss evidence."""

    entry_price: Decimal
    stop_price: Decimal
    stop_distance_fraction: Decimal
    loss_at_stop: Decimal
    gap_buffer_fraction: Decimal
    gap_adjusted_stop_price: Decimal
    gap_adjusted_loss: Decimal
    maximum_per_trade_loss: Decimal
    maximum_stop_distance_fraction: Decimal

    def __post_init__(self) -> None:
        values = tuple(getattr(self, name) for name in self.__dataclass_fields__)
        if (
            any(not value.is_finite() for value in values)
            or self.entry_price <= 0
            or self.stop_price <= 0
            or self.gap_adjusted_stop_price <= 0
            or self.loss_at_stop < 0
            or self.gap_adjusted_loss < 0
            or self.maximum_per_trade_loss <= 0
            or not Decimal("0") <= self.stop_distance_fraction
            or not Decimal("0") <= self.gap_buffer_fraction < Decimal("1")
            or not Decimal("0") < self.maximum_stop_distance_fraction <= Decimal("1")
        ):
            raise ValueError("invalid_per_trade_risk_evidence")

    def to_payload(self) -> dict[str, str]:
        return {
            "entry_price": str(self.entry_price),
            "stop_price": str(self.stop_price),
            "stop_distance_fraction": str(self.stop_distance_fraction),
            "loss_at_stop": str(self.loss_at_stop),
            "gap_buffer_fraction": str(self.gap_buffer_fraction),
            "gap_adjusted_stop_price": str(self.gap_adjusted_stop_price),
            "gap_adjusted_loss": str(self.gap_adjusted_loss),
            "maximum_per_trade_loss": str(self.maximum_per_trade_loss),
            "maximum_stop_distance_fraction": str(self.maximum_stop_distance_fraction),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PerTradeRiskEvidence":
        try:
            return cls(**{name: Decimal(str(payload[name])) for name in cls.__dataclass_fields__})
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise ValueError("invalid_per_trade_risk_evidence") from error


def evaluate_per_trade_risk(
    intent: OrderIntent,
    signal: Signal,
    policy: RiskPolicy,
) -> tuple[PerTradeRiskEvidence | None, tuple[str, ...]]:
    """Calculate configured stop and gap losses without granting execution authority."""

    configured = (
        policy.maximum_per_trade_loss,
        policy.maximum_stop_distance_fraction,
        policy.stop_gap_buffer_fraction,
    )
    if all(value is None for value in configured):
        return None, ()
    if not policy.per_trade_controls_configured:
        return None, ("per_trade_risk_policy_incomplete",)
    maximum_loss = policy.maximum_per_trade_loss
    maximum_distance = policy.maximum_stop_distance_fraction
    gap_buffer = policy.stop_gap_buffer_fraction
    if maximum_loss is None or maximum_distance is None or gap_buffer is None:
        return None, ("per_trade_risk_policy_incomplete",)
    if (
        not maximum_loss.is_finite()
        or not maximum_distance.is_finite()
        or not gap_buffer.is_finite()
        or maximum_loss <= 0
        or not Decimal("0") < maximum_distance <= Decimal("1")
        or not Decimal("0") <= gap_buffer < Decimal("1")
    ):
        return None, ("per_trade_risk_policy_invalid",)
    if signal.stop_level is None or signal.stop_level <= 0:
        return None, ("protective_stop_required",)

    stop_price = signal.stop_level
    entry_price = intent.limit_price
    if entry_price <= 0 or intent.quantity <= 0:
        return None, ()
    stop_distance = abs(entry_price - stop_price)
    stop_distance_fraction = stop_distance / entry_price
    if intent.side.value == "BUY":
        gap_adjusted_stop = stop_price * (Decimal("1") - gap_buffer)
        stop_wrong_side = stop_price >= entry_price
    else:
        gap_adjusted_stop = stop_price * (Decimal("1") + gap_buffer)
        stop_wrong_side = stop_price <= entry_price
    loss_at_stop = stop_distance * intent.quantity
    gap_adjusted_loss = abs(entry_price - gap_adjusted_stop) * intent.quantity
    evidence = PerTradeRiskEvidence(
        entry_price,
        stop_price,
        stop_distance_fraction,
        loss_at_stop,
        gap_buffer,
        gap_adjusted_stop,
        gap_adjusted_loss,
        maximum_loss,
        maximum_distance,
    )
    reasons: list[str] = []
    if signal.direction is not intent.side:
        reasons.append("signal_order_direction_mismatch")
    if (signal.entry_low is None) != (signal.entry_high is None):
        reasons.append("signal_entry_range_incomplete")
    elif signal.entry_low is not None and signal.entry_high is not None:
        if signal.entry_low <= 0 or signal.entry_high < signal.entry_low:
            reasons.append("signal_entry_range_invalid")
        elif not signal.entry_low <= entry_price <= signal.entry_high:
            reasons.append("intent_outside_signal_entry_range")
    if stop_wrong_side:
        reasons.append("protective_stop_wrong_side")
    if stop_distance_fraction > maximum_distance:
        reasons.append("stop_distance_limit")
    if loss_at_stop > maximum_loss:
        reasons.append("per_trade_loss_limit")
    if gap_adjusted_loss > maximum_loss:
        reasons.append("per_trade_gap_loss_limit")
    return evidence, tuple(reasons)


def _validate_kill_switch_scope(scope: str) -> None:
    if scope == "GLOBAL":
        return
    prefix, separator, value = scope.partition(":")
    if (
        separator != ":"
        or not value.strip()
        or prefix not in {"ACCOUNT", "ASSET_CLASS", "BROKER", "EXCHANGE", "INSTRUMENT", "STRATEGY"}
    ):
        raise ValueError("invalid_kill_switch_scope")


def _kill_switch_candidates(
    instrument_id: str,
    strategy_version: str,
    account_id: str | None,
    broker_name: str | None,
    exchange: str | None,
    asset_class: str | None,
) -> set[str]:
    candidates = {"GLOBAL", f"INSTRUMENT:{instrument_id}", f"STRATEGY:{strategy_version}"}
    if account_id:
        candidates.add(f"ACCOUNT:{account_id}")
    if broker_name:
        candidates.add(f"BROKER:{broker_name}")
    if exchange:
        candidates.add(f"EXCHANGE:{exchange}")
    if asset_class:
        candidates.add(f"ASSET_CLASS:{asset_class}")
    return candidates


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    event_id: UUID
    scope: str
    active: bool
    actor: str
    occurred_at: datetime


class SQLiteKillSwitchRegistry:
    """Append-only switch events with restart-safe scope evaluation; reactivation remains explicit."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS kill_switch_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, active INTEGER NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL)"
        )
        self._connection.commit()

    def activate(self, scope: str, *, actor: str = "system") -> KillSwitchEvent:
        return self._append(scope, True, actor)

    def deactivate(
        self, scope: str, *, explicit_operator_approval: bool, actor: str = "operator"
    ) -> KillSwitchEvent:
        if not explicit_operator_approval:
            raise PermissionError("Kill switch reactivation requires explicit operator approval.")
        return self._append(scope, False, actor)

    def blocks(
        self,
        instrument_id: str,
        strategy_version: str,
        *,
        account_id: str | None = None,
        broker_name: str | None = None,
        exchange: str | None = None,
        asset_class: str | None = None,
    ) -> bool:
        candidates = _kill_switch_candidates(
            instrument_id, strategy_version, account_id, broker_name, exchange, asset_class
        )
        return bool(candidates & self.active_scopes())

    def active_scopes(self) -> set[str]:
        rows = self._connection.execute(
            "SELECT scope, active FROM kill_switch_events WHERE sequence IN (SELECT MAX(sequence) FROM kill_switch_events GROUP BY scope)"
        ).fetchall()
        return {row[0] for row in rows if bool(row[1])}

    def events(self, scope: str | None = None) -> tuple[KillSwitchEvent, ...]:
        if scope is not None:
            rows = self._connection.execute(
                "SELECT event_id, scope, active, actor, occurred_at FROM kill_switch_events WHERE scope = ? ORDER BY sequence",
                (scope,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT event_id, scope, active, actor, occurred_at FROM kill_switch_events ORDER BY sequence"
            ).fetchall()
        return tuple(
            KillSwitchEvent(
                UUID(row[0]), row[1], bool(row[2]), row[3], datetime.fromisoformat(row[4])
            )
            for row in rows
        )

    def _append(self, scope: str, active: bool, actor: str) -> KillSwitchEvent:
        _validate_kill_switch_scope(scope)
        if not actor.strip():
            raise ValueError("kill_switch_actor_required")
        event = KillSwitchEvent(uuid4(), scope, active, actor, utc_now())
        self._connection.execute(
            "INSERT INTO kill_switch_events (event_id, scope, active, actor, occurred_at) VALUES (?, ?, ?, ?, ?)",
            (str(event.event_id), scope, int(active), actor, event.occurred_at.isoformat()),
        )
        self._connection.commit()
        return event

    def close(self) -> None:
        self._connection.close()


class PostgresKillSwitchRegistry:
    """Append-only, restart-safe kill switches in the runtime authority."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def activate(self, scope: str, *, actor: str = "system") -> KillSwitchEvent:
        return self._append(scope, True, actor)

    def deactivate(
        self, scope: str, *, explicit_operator_approval: bool, actor: str = "operator"
    ) -> KillSwitchEvent:
        if not explicit_operator_approval:
            raise PermissionError("Kill switch reactivation requires explicit operator approval.")
        return self._append(scope, False, actor)

    def _append(self, scope: str, active: bool, actor: str) -> KillSwitchEvent:
        _validate_kill_switch_scope(scope)
        if not actor.strip():
            raise ValueError("kill_switch_actor_required")
        event = KillSwitchEvent(uuid4(), scope, active, actor, utc_now())
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO kill_switch_events (event_id, scope, active, actor, occurred_at) VALUES (%s,%s,%s,%s,%s)",
                    (event.event_id, event.scope, event.active, event.actor, event.occurred_at),
                )
            return event
        except Exception as error:
            raise PersistenceError("kill_switch_persistence_uncertain") from error

    def active_scopes(self) -> set[str]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT ON (scope) scope, active FROM kill_switch_events ORDER BY scope, occurred_at DESC, event_id DESC"
                )
                return {str(row[0]) for row in cursor.fetchall() if bool(row[1])}
        except Exception as error:
            # A read failure must be treated as active by the gate caller, not
            # as an empty set of switches.
            raise PersistenceError("kill_switch_state_uncertain") from error

    def blocks(
        self,
        instrument_id: str,
        strategy_version: str,
        *,
        account_id: str | None = None,
        broker_name: str | None = None,
        exchange: str | None = None,
        asset_class: str | None = None,
    ) -> bool:
        return bool(
            _kill_switch_candidates(
                instrument_id, strategy_version, account_id, broker_name, exchange, asset_class
            )
            & self.active_scopes()
        )


class PostgresRiskStoreError(PersistenceError):
    pass


class PostgresRiskStore:
    """One transaction for risk idempotency, reservation and immutable decision."""

    def __init__(
        self,
        database: PostgresDatabase,
        risk_policy_version_id: UUID,
        *,
        alert_store: PostgresOperationalAlertStore | None = None,
    ) -> None:
        self._database, self._risk_policy_version_id = database, risk_policy_version_id
        self._alert_store = alert_store

    def persist(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        *,
        business_day: date,
        daily_limit: Decimal,
        submission_approved: bool | None = None,
        violation_reasons: tuple[str, ...] | None = None,
    ) -> RiskDecision:
        if decision.intent_id != intent.intent_id or daily_limit <= 0:
            raise PostgresRiskStoreError("invalid_risk_persistence_request")
        final_approved = (
            decision.decision is RiskDecisionType.APPROVE
            if submission_approved is None
            else submission_approved
        )
        if final_approved and decision.decision is RiskDecisionType.REJECT:
            raise PostgresRiskStoreError("invalid_risk_persistence_request")
        final_reasons = decision.reasons if violation_reasons is None else violation_reasons
        if not final_approved and not final_reasons:
            raise PostgresRiskStoreError("risk_violation_reasons_required")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                # Both the intent lock and account/day lock make duplicate and
                # concurrent requests deterministic across processes.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"risk-intent:{intent.intent_id}",),
                )
                cursor.execute(
                    "SELECT risk_decision_id, approved, reasons, decided_at FROM risk_decisions WHERE intent_id = %s",
                    (intent.intent_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return RiskDecision(
                        UUID(str(existing[0])),
                        intent.intent_id,
                        RiskDecisionType.APPROVE if bool(existing[1]) else RiskDecisionType.REJECT,
                        tuple(existing[2]),
                        existing[3],
                    )
                approved = decision.decision is RiskDecisionType.APPROVE
                if approved and final_approved:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"risk-day:{intent.account_id}:{business_day.isoformat()}",),
                    )
                    cursor.execute(
                        "SELECT COALESCE(SUM(notional), 0) FROM risk_reservations WHERE account_id = %s AND business_date = %s",
                        (intent.account_id, business_day),
                    )
                    if Decimal(cursor.fetchone()[0]) + intent.notional > daily_limit:
                        decision = RiskDecision.create(
                            intent.intent_id,
                            RiskDecisionType.REJECT,
                            ["daily_order_notional_limit"],
                        )
                        approved = False
                        final_approved = False
                        final_reasons = decision.reasons
                    else:
                        cursor.execute(
                            "INSERT INTO risk_reservations (reservation_id, account_id, intent_id, business_date, notional, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                            (
                                uuid4(),
                                intent.account_id,
                                intent.intent_id,
                                business_day,
                                intent.notional,
                                decision.decided_at,
                            ),
                        )
                cursor.execute(
                    "INSERT INTO risk_decisions (risk_decision_id, intent_id, risk_policy_version_id, approved, reasons, decided_at) VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                    (
                        decision.decision_id,
                        intent.intent_id,
                        self._risk_policy_version_id,
                        approved,
                        json.dumps(decision.reasons),
                        decision.decided_at,
                    ),
                )
                if not final_approved and self._alert_store is not None:
                    severity = (
                        AlertSeverity.CRITICAL
                        if "kill_switch_active" in final_reasons
                        else AlertSeverity.WARNING
                    )
                    self._alert_store.raise_alert_in_transaction(
                        connection,
                        source="risk_engine",
                        code="PRETRADE_RISK_REJECTED",
                        severity=severity,
                        resource=f"intent:{intent.intent_id}",
                        details={
                            "account_id": intent.account_id,
                            "business_day": business_day.isoformat(),
                            "decision_id": str(decision.decision_id),
                            "instrument_id": intent.instrument_id,
                            "notional": str(intent.notional),
                            "policy_version_id": str(self._risk_policy_version_id),
                            "reasons": json.dumps(final_reasons, separators=(",", ":")),
                            "signal_id": str(intent.signal_id),
                        },
                        occurred_at=decision.decided_at,
                    )
                return decision
        except PostgresRiskStoreError:
            raise
        except Exception as error:
            raise PostgresRiskStoreError("risk_persistence_uncertain") from error


class IdempotencyLedger:
    """In-memory development ledger; production will persist this in the OMS store."""

    def __init__(self) -> None:
        self._seen_intents: set[object] = set()

    def claim(self, intent_id: object) -> bool:
        if intent_id in self._seen_intents:
            return False
        self._seen_intents.add(intent_id)
        return True


class SQLiteIdempotencyLedger:
    """Durable, insert-only idempotency claims for risk assessment requests."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS risk_idempotency_claims (intent_id TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)"
        )
        self._connection.commit()

    def claim(self, intent_id: object) -> bool:
        try:
            self._connection.execute(
                "INSERT INTO risk_idempotency_claims VALUES (?, ?)",
                (str(intent_id), utc_now().isoformat()),
            )
        except sqlite3.IntegrityError:
            return False
        self._connection.commit()
        return True

    def close(self) -> None:
        self._connection.close()


class DailyNotionalLedger:
    """Paper-only daily reservation boundary; rejected intents never consume budget."""

    def __init__(self) -> None:
        self._reserved: dict[tuple[str, date], Decimal] = {}
        self._intent_ids: set[object] = set()

    def reserve(self, intent: OrderIntent, *, day: date, maximum: Decimal) -> bool:
        if intent.intent_id in self._intent_ids:
            return False
        key = (intent.account_id, day)
        total = self._reserved.get(key, Decimal(0))
        if total + intent.notional > maximum:
            return False
        self._reserved[key] = total + intent.notional
        self._intent_ids.add(intent.intent_id)
        return True


class SQLiteDailyNotionalLedger:
    """Atomic durable daily notional reservation for the paper risk gate."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(
            str(database_path), check_same_thread=False, isolation_level=None
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS risk_daily_notional (intent_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, trading_day TEXT NOT NULL, notional TEXT NOT NULL)"
        )

    def reserve(self, intent: OrderIntent, *, day: date, maximum: Decimal) -> bool:
        if maximum <= 0:
            raise ValueError("daily_notional_limit_must_be_positive")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                "SELECT 1 FROM risk_daily_notional WHERE intent_id = ?", (str(intent.intent_id),)
            ).fetchone()
            rows = self._connection.execute(
                "SELECT notional FROM risk_daily_notional WHERE account_id = ? AND trading_day = ?",
                (intent.account_id, day.isoformat()),
            ).fetchall()
            total = sum((Decimal(row[0]) for row in rows), Decimal(0))
            if existing is not None or total + intent.notional > maximum:
                self._connection.execute("ROLLBACK")
                return False
            self._connection.execute(
                "INSERT INTO risk_daily_notional VALUES (?, ?, ?, ?)",
                (str(intent.intent_id), intent.account_id, day.isoformat(), str(intent.notional)),
            )
            self._connection.execute("COMMIT")
            return True
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._connection.close()


class SQLiteRiskDecisionStore:
    """Append-only persisted decisions for audits; it does not grant execution authority."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS risk_decisions (decision_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, decision TEXT NOT NULL, reasons_json TEXT NOT NULL, decided_at TEXT NOT NULL)"
        )
        self._connection.commit()

    def append(self, decision: RiskDecision) -> None:
        self._connection.execute(
            "INSERT INTO risk_decisions VALUES (?, ?, ?, ?, ?)",
            (
                str(decision.decision_id),
                str(decision.intent_id),
                decision.decision.value,
                json.dumps(decision.reasons),
                decision.decided_at.isoformat(),
            ),
        )
        self._connection.commit()

    def get(self, decision_id: UUID) -> RiskDecision:
        row = self._connection.execute(
            "SELECT * FROM risk_decisions WHERE decision_id = ?", (str(decision_id),)
        ).fetchone()
        if row is None:
            raise KeyError(str(decision_id))
        return RiskDecision(
            UUID(row[0]),
            UUID(row[1]),
            RiskDecisionType(row[2]),
            tuple(json.loads(row[3])),
            datetime.fromisoformat(row[4]),
        )

    def decisions_for_intent(self, intent_id: UUID) -> tuple[RiskDecision, ...]:
        rows = self._connection.execute(
            "SELECT * FROM risk_decisions WHERE intent_id = ? ORDER BY decided_at, decision_id",
            (str(intent_id),),
        ).fetchall()
        return tuple(
            RiskDecision(
                UUID(row[0]),
                UUID(row[1]),
                RiskDecisionType(row[2]),
                tuple(json.loads(row[3])),
                datetime.fromisoformat(row[4]),
            )
            for row in rows
        )

    def close(self) -> None:
        self._connection.close()


class RiskEngine:
    """Owns approval/rejection; strategy code cannot change this policy in place."""

    def __init__(
        self,
        policy: RiskPolicy,
        kill_switches: KillSwitchRegistry | SQLiteKillSwitchRegistry | None = None,
        idempotency_ledger: IdempotencyLedger | SQLiteIdempotencyLedger | None = None,
        daily_notional_ledger: DailyNotionalLedger | SQLiteDailyNotionalLedger | None = None,
        decision_store: SQLiteRiskDecisionStore | None = None,
        postgres_store: PostgresRiskStore | None = None,
    ) -> None:
        self._policy = policy
        self._kill_switches = kill_switches or KillSwitchRegistry()
        self._idempotency_ledger = idempotency_ledger or IdempotencyLedger()
        self._daily_notional_ledger = daily_notional_ledger or DailyNotionalLedger()
        self._decision_store = decision_store
        self._postgres_store = postgres_store

    def assess(
        self,
        intent: OrderIntent,
        signal: Signal,
        market: MarketSnapshot,
        portfolio: PortfolioState,
        execution: PreTradeExecutionContext | None = None,
    ) -> RiskDecision:
        reasons: list[str] = []
        if self._postgres_store is None and not self._idempotency_ledger.claim(intent.intent_id):
            duplicate_decision = RiskDecision.create(
                intent.intent_id, RiskDecisionType.REJECT, ["duplicate_intent"]
            )
            self._record(duplicate_decision)
            return duplicate_decision
        now = utc_now()
        if signal.signal_id != intent.signal_id:
            reasons.append("signal_intent_mismatch")
        if signal.instrument_id != intent.instrument_id:
            reasons.append("signal_instrument_mismatch")
        if signal.status is not SignalStatus.VALIDATED or signal.is_expired:
            reasons.append("signal_not_validated_or_expired")
        if market.instrument_id != intent.instrument_id:
            reasons.append("market_instrument_mismatch")
        if market.observed_at < now - timedelta(seconds=self._policy.max_market_age_seconds):
            reasons.append("stale_market_data")
        if (
            min(market.data_quality_score, signal.data_quality_score)
            < self._policy.minimum_data_quality
        ):
            reasons.append("insufficient_data_quality")
        if market.spread_fraction > self._policy.maximum_spread_fraction:
            reasons.append("excessive_spread")
        if intent.quantity <= 0 or intent.limit_price <= 0:
            reasons.append("invalid_order_quantity_or_price")
        if intent.notional > self._policy.maximum_order_notional:
            reasons.append("order_notional_limit")
        _per_trade_evidence, per_trade_reasons = evaluate_per_trade_risk(
            intent, signal, self._policy
        )
        reasons.extend(per_trade_reasons)
        signed_notional = intent.notional if intent.side.value == "BUY" else -intent.notional
        projected = portfolio.position_notional(intent.instrument_id) + signed_notional
        if abs(projected) > self._policy.maximum_position_notional:
            reasons.append("position_notional_limit")
        if not portfolio.reconciliation_complete or not portfolio.risk_increase_allowed:
            reasons.append("reconciliation_or_risk_increase_block")
        try:
            if self._kill_switches.blocks(
                intent.instrument_id, signal.strategy_version, account_id=intent.account_id
            ):
                reasons.append("kill_switch_active")
        except PersistenceError:
            reasons.append("kill_switch_state_uncertain")
        if execution is None:
            reasons.append("missing_execution_context")
        else:
            execution.validate()
            if not execution.session_open:
                reasons.append("invalid_trading_session")
            if execution.instrument_halted:
                reasons.append("instrument_halted")
            if not execution.quote_available:
                reasons.append("quote_missing")
            if (
                execution.expected_slippage_fraction
                > self._policy.maximum_expected_slippage_fraction
            ):
                reasons.append("expected_slippage_limit")
            if execution.event_risk > self._policy.maximum_event_risk:
                reasons.append("event_risk_limit")
            if intent.side.value == "BUY" and intent.notional > execution.available_buying_power:
                reasons.append("insufficient_buying_power")
            if not execution.broker_state_certain:
                reasons.append("broker_state_uncertain")
            if not execution.strategy_enabled:
                reasons.append("strategy_disabled")
            if not execution.model_approved:
                reasons.append("model_not_approved")
        if (
            not reasons
            and self._postgres_store is None
            and not self._daily_notional_ledger.reserve(
                intent, day=now.date(), maximum=self._policy.maximum_daily_order_notional
            )
        ):
            reasons.append("daily_order_notional_limit")
        decision = RiskDecisionType.REJECT if reasons else RiskDecisionType.APPROVE
        result = RiskDecision.create(intent.intent_id, decision, reasons)
        if self._postgres_store is not None:
            try:
                return self._postgres_store.persist(
                    intent,
                    result,
                    business_day=now.date(),
                    daily_limit=self._policy.maximum_daily_order_notional,
                )
            except PostgresRiskStoreError:
                # No approval is usable if the durable outcome is uncertain.
                return RiskDecision.create(
                    intent.intent_id, RiskDecisionType.REJECT, ["risk_persistence_uncertain"]
                )
        self._record(result)
        return result

    def reject_unassessable(self, intent_id: UUID, reason: str) -> RiskDecision:
        """Persist a fail-closed rejection when required evidence cannot form a risk input."""
        if not reason.strip():
            raise ValueError("unassessable_rejection_reason_required")
        result = RiskDecision.create(intent_id, RiskDecisionType.REJECT, [reason])
        self._record(result)
        return result

    def _record(self, decision: RiskDecision) -> None:
        if self._decision_store is not None:
            self._decision_store.append(decision)
