"""Point-in-time corporate-action records and deterministic position application."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from .paper_execution import CashBalance, Position


class CorporateActionError(ValueError):
    pass


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    DELISTING = "DELISTING"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    instrument_id: str
    action_type: CorporateActionType
    announced_at: datetime
    effective_at: datetime
    ingested_at: datetime
    provider: str
    source_identifier: str
    revision: int = 0
    ratio: Decimal | None = None
    amount_per_share: Decimal | None = None
    currency: str | None = None
    successor_instrument_id: str | None = None
    cash_out_price: Decimal | None = None

    def validate(self) -> None:
        timestamps = (self.announced_at, self.effective_at, self.ingested_at)
        if not self.instrument_id.strip() or not self.provider.strip() or not self.source_identifier.strip() or self.revision < 0:
            raise CorporateActionError("invalid_corporate_action_identity")
        if any(item.tzinfo is None for item in timestamps) or self.effective_at < self.announced_at or self.ingested_at < self.announced_at:
            raise CorporateActionError("invalid_corporate_action_timestamps")
        if self.action_type is CorporateActionType.SPLIT and (self.ratio is None or self.ratio <= 0):
            raise CorporateActionError("split_requires_positive_ratio")
        if self.action_type is CorporateActionType.CASH_DIVIDEND and (self.amount_per_share is None or self.amount_per_share < 0 or not self.currency):
            raise CorporateActionError("dividend_requires_amount_and_currency")
        if self.action_type is CorporateActionType.SYMBOL_CHANGE and not self.successor_instrument_id:
            raise CorporateActionError("symbol_change_requires_successor")
        if self.action_type is CorporateActionType.DELISTING and (self.cash_out_price is None or self.cash_out_price < 0 or not self.currency):
            raise CorporateActionError("delisting_requires_cash_out_price_and_currency")


@dataclass(frozen=True, slots=True)
class CorporateActionApplication:
    position: Position
    cash: CashBalance
    applied_actions: tuple[str, ...]


def apply_actions(position: Position, cash: CashBalance, actions: list[CorporateAction], *, as_of: datetime) -> CorporateActionApplication:
    """Apply effective actions in order; future or wrong-instrument actions never mutate holdings."""
    if as_of.tzinfo is None:
        raise CorporateActionError("action_application_timestamp_must_be_timezone_aware")
    applied: list[str] = []
    current_position, current_cash = position, cash
    for action in sorted(actions, key=lambda item: (item.effective_at, item.revision)):
        action.validate()
        if action.effective_at > as_of:
            raise CorporateActionError("cannot_apply_future_corporate_action")
        if action.instrument_id != current_position.instrument_id:
            raise CorporateActionError("corporate_action_instrument_mismatch")
        if action.action_type is CorporateActionType.SPLIT:
            current_position = Position(current_position.instrument_id, current_position.quantity * action.ratio, current_position.average_cost / action.ratio)
        elif action.action_type is CorporateActionType.CASH_DIVIDEND:
            if current_cash.currency != action.currency:
                raise CorporateActionError("corporate_action_currency_mismatch")
            current_cash = CashBalance(current_cash.currency, current_cash.amount + current_position.quantity * action.amount_per_share)
        elif action.action_type is CorporateActionType.SYMBOL_CHANGE:
            current_position = Position(action.successor_instrument_id, current_position.quantity, current_position.average_cost)
        elif action.action_type is CorporateActionType.DELISTING:
            if current_cash.currency != action.currency:
                raise CorporateActionError("corporate_action_currency_mismatch")
            current_cash = CashBalance(current_cash.currency, current_cash.amount + current_position.quantity * action.cash_out_price)
            current_position = Position(current_position.instrument_id, Decimal("0"), Decimal("0"))
        applied.append(f"{action.provider}:{action.source_identifier}:{action.revision}")
    return CorporateActionApplication(current_position, current_cash, tuple(applied))


class SQLiteCorporateActionStore:
    """Append-only action revisions; as-of reads are filtered by announcement and ingestion time."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_actions (
                instrument_id TEXT NOT NULL, action_type TEXT NOT NULL, announced_at TEXT NOT NULL,
                effective_at TEXT NOT NULL, ingested_at TEXT NOT NULL, provider TEXT NOT NULL,
                source_identifier TEXT NOT NULL, revision INTEGER NOT NULL, ratio TEXT,
                amount_per_share TEXT, currency TEXT, successor_instrument_id TEXT, cash_out_price TEXT,
                PRIMARY KEY (provider, source_identifier, revision)
            )
            """
        )
        self._connection.commit()

    def ingest(self, actions: list[CorporateAction]) -> None:
        if not actions:
            raise CorporateActionError("empty_corporate_action_batch")
        for action in actions:
            action.validate()
            try:
                self._connection.execute(
                    "INSERT INTO corporate_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (action.instrument_id, action.action_type.value, action.announced_at.isoformat(), action.effective_at.isoformat(), action.ingested_at.isoformat(), action.provider, action.source_identifier, action.revision, self._decimal_or_none(action.ratio), self._decimal_or_none(action.amount_per_share), action.currency, action.successor_instrument_id, self._decimal_or_none(action.cash_out_price)),
                )
            except sqlite3.IntegrityError as error:
                self._connection.rollback()
                raise CorporateActionError("duplicate_corporate_action_revision") from error
        self._connection.commit()

    def available_as_of(self, instrument_id: str, decision_at: datetime) -> list[CorporateAction]:
        if not instrument_id.strip() or decision_at.tzinfo is None:
            raise CorporateActionError("invalid_corporate_action_as_of_query")
        rows = self._connection.execute(
            "SELECT * FROM corporate_actions WHERE instrument_id = ? AND announced_at <= ? AND ingested_at <= ? ORDER BY announced_at DESC, revision DESC, ingested_at DESC, effective_at",
            (instrument_id, decision_at.isoformat(), decision_at.isoformat()),
        ).fetchall()
        latest: dict[tuple[str, str], CorporateAction] = {}
        for row in rows:
            action = self._from_row(row)
            latest.setdefault((action.provider, action.source_identifier), action)
        return sorted(latest.values(), key=lambda item: (item.effective_at, item.provider, item.source_identifier))

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _decimal_or_none(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> CorporateAction:
        return CorporateAction(str(row[0]), CorporateActionType(str(row[1])), datetime.fromisoformat(str(row[2])), datetime.fromisoformat(str(row[3])), datetime.fromisoformat(str(row[4])), str(row[5]), str(row[6]), int(row[7]), None if row[8] is None else Decimal(str(row[8])), None if row[9] is None else Decimal(str(row[9])), None if row[10] is None else str(row[10]), None if row[11] is None else str(row[11]), None if row[12] is None else Decimal(str(row[12])))
