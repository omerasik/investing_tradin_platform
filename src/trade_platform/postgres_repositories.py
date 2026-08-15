"""Atomic PostgreSQL repositories for high-risk persistence boundaries.

They store decisions made by the independent domain services; they do not
calculate risk, choose a strategy, submit to a broker, or enable live trading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from .persistence import PersistenceError, PostgresDatabase


class PostgresRepositoryError(PersistenceError):
    pass


@dataclass(frozen=True, slots=True)
class ReservationResult:
    reservation_id: UUID
    risk_decision_id: UUID
    idempotent: bool


class PostgresCriticalRepository:
    """Transactional adapter for reservations, OMS evidence and packages."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @staticmethod
    def _uuid(value: UUID | str) -> UUID:
        return value if isinstance(value, UUID) else UUID(value)

    def reserve_and_record_decision(self, *, account_id: str, intent_id: UUID, policy_version_id: UUID,
                                    business_date: date, notional: Decimal, daily_limit: Decimal,
                                    approved: bool, reasons: tuple[str, ...], decided_at: datetime) -> ReservationResult:
        if not account_id.strip() or notional < 0 or daily_limit < 0 or decided_at.tzinfo is None:
            raise PostgresRepositoryError("invalid_risk_reservation")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                # Advisory lock serializes the account/day budget without holding
                # an application-process mutex or relying on client retries.
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"risk:{account_id}:{business_date.isoformat()}",))
                cursor.execute("SELECT reservation_id, notional FROM risk_reservations WHERE intent_id = %s", (intent_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    if Decimal(existing[1]) != notional:
                        raise PostgresRepositoryError("risk_reservation_idempotency_conflict")
                    cursor.execute("SELECT risk_decision_id FROM risk_decisions WHERE intent_id = %s", (intent_id,))
                    decision = cursor.fetchone()
                    if decision is None:
                        raise PostgresRepositoryError("risk_reservation_without_decision")
                    return ReservationResult(self._uuid(existing[0]), self._uuid(decision[0]), True)
                cursor.execute("SELECT COALESCE(SUM(notional), 0) FROM risk_reservations WHERE account_id = %s AND business_date = %s", (account_id, business_date))
                reserved = Decimal(cursor.fetchone()[0])
                if approved and reserved + notional > daily_limit:
                    raise PostgresRepositoryError("daily_notional_limit_exceeded")
                reservation_id, decision_id = uuid4(), uuid4()
                if approved:
                    cursor.execute("INSERT INTO risk_reservations (reservation_id, account_id, intent_id, business_date, notional, created_at) VALUES (%s, %s, %s, %s, %s, %s)", (reservation_id, account_id, intent_id, business_date, notional, decided_at))
                cursor.execute("INSERT INTO risk_decisions (risk_decision_id, intent_id, risk_policy_version_id, approved, reasons, decided_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s)", (decision_id, intent_id, policy_version_id, approved, json.dumps(reasons), decided_at))
                return ReservationResult(reservation_id, decision_id, False)
        except PostgresRepositoryError:
            raise
        except Exception as error:
            raise PostgresRepositoryError("risk_reservation_transaction_failed") from error

    def create_order_with_event(self, *, intent_id: UUID, signal_id: UUID, account_id: str, instrument_id: UUID,
                                side: str, quantity: Decimal, limit_price: Decimal, status: str, created_at: datetime) -> UUID:
        if side not in {"BUY", "SELL"} or quantity <= 0 or limit_price <= 0 or created_at.tzinfo is None:
            raise PostgresRepositoryError("invalid_paper_order")
        event_id = uuid4()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO paper_order_intents VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (intent_id, signal_id, account_id, instrument_id, side, quantity, limit_price, status, created_at))
                cursor.execute("INSERT INTO oms_events VALUES (%s, %s, %s, %s, %s::jsonb)", (event_id, intent_id, "ORDER_CREATED", created_at, json.dumps({"status": status})))
            return event_id
        except Exception as error:
            raise PostgresRepositoryError("paper_order_transaction_failed") from error

    def ingest_fill(self, *, external_fill_id: str, intent_id: UUID, quantity: Decimal, price: Decimal,
                    occurred_at: datetime) -> UUID:
        if not external_fill_id.strip() or quantity <= 0 or price <= 0 or occurred_at.tzinfo is None:
            raise PostgresRepositoryError("invalid_fill")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT fill_id, intent_id, quantity, price FROM fills WHERE external_fill_id = %s", (external_fill_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    if self._uuid(existing[1]) != intent_id or Decimal(existing[2]) != quantity or Decimal(existing[3]) != price:
                        raise PostgresRepositoryError("external_fill_id_conflict")
                    return self._uuid(existing[0])
                fill_id = uuid4()
                cursor.execute("INSERT INTO fills VALUES (%s, %s, %s, %s, %s, %s)", (fill_id, external_fill_id, intent_id, occurred_at, quantity, price))
                return fill_id
        except PostgresRepositoryError:
            raise
        except Exception as error:
            raise PostgresRepositoryError("fill_transaction_failed") from error

    def create_validation_package(self, *, package_id: UUID, strategy_version_id: UUID, dataset_version_id: UUID,
                                  cost_model_version: str, content_hash: str, status: str, created_at: datetime,
                                  limitations: tuple[str, ...], artifacts: dict[str, UUID]) -> None:
        del (
            package_id,
            strategy_version_id,
            dataset_version_id,
            cost_model_version,
            content_hash,
            status,
            created_at,
            limitations,
            artifacts,
        )
        # This former low-level entry point cannot prove canonical manifest or
        # evidence hashes. Keep it fail-closed so callers must use the single
        # integrity-verifying PostgresQuantValidationStore path.
        raise PostgresRepositoryError("canonical_validation_package_store_required")
