"""Persisted broker positions bound to complete OMS reconciliation evidence."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from .broker_adapter import BrokerAccountSnapshot
from .domain import OrderIntent, OrderSide, PortfolioState
from .instruments import SQLiteInstrumentStore
from .paper_execution import CashBalance, Position
from .paper_oms import PaperOmsError, SQLitePaperOms
from .portfolio_risk import PortfolioExposure
from .quotes import QuoteDataError, SQLiteQuoteStore


class PortfolioEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReconciledPositionSnapshot:
    snapshot_id: UUID
    reconciliation_id: UUID
    account_id: str
    source: str
    positions: dict[str, Position]
    as_of: datetime
    ingested_at: datetime

    @property
    def exposure_reference(self) -> str:
        return f"{self.source}:{self.snapshot_id}"

    def validate(self) -> None:
        if not self.account_id.strip() or not self.source.strip() or self.as_of.tzinfo is None or self.as_of.utcoffset() is None or self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None or self.ingested_at < self.as_of:
            raise PortfolioEvidenceError("invalid_reconciled_position_snapshot")
        if any(not instrument_id.strip() or position.instrument_id != instrument_id for instrument_id, position in self.positions.items()):
            raise PortfolioEvidenceError("invalid_reconciled_position_snapshot")

    def portfolio_state(self) -> PortfolioState:
        return PortfolioState(self.account_id, {instrument_id: position.quantity * position.average_cost for instrument_id, position in self.positions.items()}, True, True)


@dataclass(frozen=True, slots=True)
class ReconciledAccountSnapshot:
    evidence_id: UUID
    reconciliation_id: UUID
    snapshot: BrokerAccountSnapshot
    healthy: bool
    ingested_at: datetime

    @property
    def account_id(self) -> str:
        return self.snapshot.account_id

    @property
    def exposure_reference(self) -> str:
        return f"{self.snapshot.source}:{self.evidence_id}"

    def validate(self) -> None:
        if not self.snapshot.account_id.strip() or not self.snapshot.source.strip() or self.snapshot.as_of.tzinfo is None or self.snapshot.as_of.utcoffset() is None or self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None or self.ingested_at < self.snapshot.as_of or self.snapshot.buying_power < 0:
            raise PortfolioEvidenceError("invalid_reconciled_account_snapshot")
        if any(not instrument_id.strip() or position.instrument_id != instrument_id for instrument_id, position in self.snapshot.positions.items()):
            raise PortfolioEvidenceError("invalid_reconciled_account_snapshot")


class SQLiteReconciledAccountStore:
    """Append-only broker account snapshots bound to a complete OMS reconciliation."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS reconciled_account_snapshots (
            evidence_id TEXT PRIMARY KEY, reconciliation_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
            source TEXT NOT NULL, cash_currency TEXT NOT NULL, cash_amount TEXT NOT NULL, buying_power TEXT NOT NULL,
            positions_json TEXT NOT NULL, open_intent_ids_json TEXT NOT NULL, fill_ids_json TEXT NOT NULL,
            realized_pnl TEXT NOT NULL, fees TEXT NOT NULL, healthy INTEGER NOT NULL, as_of TEXT NOT NULL,
            ingested_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, evidence: ReconciledAccountSnapshot, oms: SQLitePaperOms) -> None:
        evidence.validate()
        try:
            reconciliation = oms.reconciliation(evidence.reconciliation_id)
        except PaperOmsError as error:
            raise PortfolioEvidenceError("unknown_reconciliation_evidence") from error
        snapshot = evidence.snapshot
        if not reconciliation.complete or reconciliation.account_id != snapshot.account_id or reconciliation.source != snapshot.source or reconciliation.occurred_at != snapshot.as_of:
            raise PortfolioEvidenceError("account_snapshot_not_bound_to_complete_reconciliation")
        positions = {instrument_id: [str(position.quantity), str(position.average_cost)] for instrument_id, position in snapshot.positions.items()}
        try:
            self._connection.execute("INSERT INTO reconciled_account_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(evidence.evidence_id), str(evidence.reconciliation_id), snapshot.account_id, snapshot.source, snapshot.cash.currency, str(snapshot.cash.amount), str(snapshot.buying_power), json.dumps(positions, sort_keys=True), json.dumps([str(value) for value in snapshot.open_intent_ids]), json.dumps(snapshot.fill_ids), str(snapshot.realized_pnl), str(snapshot.fees), int(evidence.healthy), snapshot.as_of.isoformat(), evidence.ingested_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise PortfolioEvidenceError("duplicate_reconciled_account_snapshot") from error
        self._connection.commit()

    def latest_as_of(self, account_id: str, decision_at: datetime) -> ReconciledAccountSnapshot:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise PortfolioEvidenceError("portfolio_decision_time_must_be_timezone_aware")
        row = self._connection.execute("SELECT * FROM reconciled_account_snapshots WHERE account_id = ? AND as_of <= ? AND ingested_at <= ? ORDER BY as_of DESC, ingested_at DESC, rowid DESC LIMIT 1", (account_id, decision_at.isoformat(), decision_at.isoformat())).fetchone()
        if row is None:
            raise PortfolioEvidenceError("reconciled_account_unavailable_as_of")
        positions = {instrument_id: Position(instrument_id, Decimal(values[0]), Decimal(values[1])) for instrument_id, values in json.loads(row[7]).items()}
        snapshot = BrokerAccountSnapshot(row[2], row[3], datetime.fromisoformat(row[13]), CashBalance(row[4], Decimal(row[5])), Decimal(row[6]), positions, tuple(UUID(value) for value in json.loads(row[8])), tuple(json.loads(row[9])), Decimal(row[10]), Decimal(row[11]))
        return ReconciledAccountSnapshot(UUID(row[0]), UUID(row[1]), snapshot, bool(row[12]), datetime.fromisoformat(row[14]))

    def projected_exposures_as_of(self, *, account_id: str, intent: OrderIntent, decision_at: datetime, instruments: SQLiteInstrumentStore, quotes: SQLiteQuoteStore) -> tuple[ReconciledAccountSnapshot, list[PortfolioExposure]]:
        evidence = self.latest_as_of(account_id, decision_at)
        if evidence.account_id != intent.account_id:
            raise PortfolioEvidenceError("intent_account_mismatch")
        snapshot = evidence.snapshot
        quantities = {instrument_id: position.quantity for instrument_id, position in snapshot.positions.items() if position.quantity != 0}
        order_quantity = intent.quantity if intent.side is OrderSide.BUY else -intent.quantity
        quantities[intent.instrument_id] = quantities.get(intent.instrument_id, Decimal("0")) + order_quantity
        exposures: list[PortfolioExposure] = []
        try:
            for instrument_id, quantity in sorted(quantities.items()):
                if quantity == 0:
                    continue
                instrument = instruments.get(instrument_id)
                quote = quotes.latest_as_of(instrument_id, decision_at)
                existing_quantity = snapshot.positions.get(instrument_id, Position(instrument_id, Decimal("0"), Decimal("0"))).quantity
                order_price = quote.ask if intent.side is OrderSide.BUY else quote.bid
                notional = existing_quantity * quote.last + (order_quantity * order_price if instrument_id == intent.instrument_id else Decimal("0"))
                exposures.append(PortfolioExposure(instrument_id, notional, instrument.asset_class.value, "", currency=instrument.quote_currency, broker=snapshot.source, exchange=instrument.venue))
        except (KeyError, QuoteDataError) as error:
            raise PortfolioEvidenceError("projected_exposure_market_or_instrument_evidence_unavailable") from error
        return evidence, exposures

    def portfolio_state_as_of(self, account_id: str, decision_at: datetime) -> PortfolioState:
        snapshot = self.latest_as_of(account_id, decision_at).snapshot
        return PortfolioState(snapshot.account_id, {instrument_id: position.quantity * position.average_cost for instrument_id, position in snapshot.positions.items()}, True, True)

    def close(self) -> None:
        self._connection.close()


class OmsReconciledAccountStore:
    """Read-only pre-trade view of account evidence atomically stored by the OMS."""

    def __init__(self, oms: SQLitePaperOms) -> None:
        self._oms = oms

    def latest_as_of(self, account_id: str, decision_at: datetime) -> ReconciledAccountSnapshot:
        try:
            record = self._oms.latest_reconciled_account(account_id, decision_at)
        except PaperOmsError as error:
            raise PortfolioEvidenceError("reconciled_account_unavailable_as_of") from error
        if record is None:
            raise PortfolioEvidenceError("reconciled_account_unavailable_as_of")
        return ReconciledAccountSnapshot(record.evidence_id, record.reconciliation_id, record.snapshot, record.healthy, record.ingested_at)

    def projected_exposures_as_of(self, *, account_id: str, intent: OrderIntent, decision_at: datetime, instruments: SQLiteInstrumentStore, quotes: SQLiteQuoteStore) -> tuple[ReconciledAccountSnapshot, list[PortfolioExposure]]:
        evidence = self.latest_as_of(account_id, decision_at)
        if evidence.account_id != intent.account_id:
            raise PortfolioEvidenceError("intent_account_mismatch")
        snapshot = evidence.snapshot
        quantities = {instrument_id: position.quantity for instrument_id, position in snapshot.positions.items() if position.quantity != 0}
        order_quantity = intent.quantity if intent.side is OrderSide.BUY else -intent.quantity
        quantities[intent.instrument_id] = quantities.get(intent.instrument_id, Decimal("0")) + order_quantity
        exposures: list[PortfolioExposure] = []
        try:
            for instrument_id, quantity in sorted(quantities.items()):
                if quantity == 0:
                    continue
                instrument = instruments.get(instrument_id); profile = instruments.risk_profile_as_of(instrument_id, decision_at); quote = quotes.latest_as_of(instrument_id, decision_at)
                existing_quantity = snapshot.positions.get(instrument_id, Position(instrument_id, Decimal("0"), Decimal("0"))).quantity
                order_price = quote.ask if intent.side is OrderSide.BUY else quote.bid
                exposures.append(PortfolioExposure(instrument_id, existing_quantity * quote.last + (order_quantity * order_price if instrument_id == intent.instrument_id else Decimal("0")), instrument.asset_class.value, profile.sector, country=profile.country, currency=instrument.quote_currency, broker=snapshot.source, exchange=instrument.venue, correlation_cluster=profile.correlation_cluster, beta=profile.beta, duration=profile.duration, factors=profile.factors))
        except (KeyError, QuoteDataError) as error:
            raise PortfolioEvidenceError("projected_exposure_market_or_instrument_evidence_unavailable") from error
        return evidence, exposures

    def portfolio_state_as_of(self, account_id: str, decision_at: datetime) -> PortfolioState:
        snapshot = self.latest_as_of(account_id, decision_at).snapshot
        return PortfolioState(snapshot.account_id, {instrument_id: position.quantity * position.average_cost for instrument_id, position in snapshot.positions.items()}, True, True)


class SQLiteReconciledPositionStore:
    """Append-only position snapshots whose identities are anchored to a completed OMS reconciliation."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""CREATE TABLE IF NOT EXISTS reconciled_position_snapshots (
            snapshot_id TEXT PRIMARY KEY, reconciliation_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
            source TEXT NOT NULL, positions_json TEXT NOT NULL, as_of TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
        self._connection.commit()

    def append(self, snapshot: ReconciledPositionSnapshot, oms: SQLitePaperOms) -> None:
        snapshot.validate()
        try:
            reconciliation = oms.reconciliation(snapshot.reconciliation_id)
        except PaperOmsError as error:
            raise PortfolioEvidenceError("unknown_reconciliation_evidence") from error
        if not reconciliation.complete or reconciliation.account_id != snapshot.account_id or reconciliation.source != snapshot.source or reconciliation.occurred_at != snapshot.as_of:
            raise PortfolioEvidenceError("position_snapshot_not_bound_to_complete_reconciliation")
        positions = {instrument_id: [str(position.quantity), str(position.average_cost)] for instrument_id, position in snapshot.positions.items()}
        try:
            self._connection.execute("INSERT INTO reconciled_position_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", (str(snapshot.snapshot_id), str(snapshot.reconciliation_id), snapshot.account_id, snapshot.source, json.dumps(positions, sort_keys=True), snapshot.as_of.isoformat(), snapshot.ingested_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise PortfolioEvidenceError("duplicate_reconciled_position_snapshot") from error
        self._connection.commit()

    def latest_as_of(self, account_id: str, decision_at: datetime) -> ReconciledPositionSnapshot:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise PortfolioEvidenceError("portfolio_decision_time_must_be_timezone_aware")
        row = self._connection.execute("SELECT * FROM reconciled_position_snapshots WHERE account_id = ? AND as_of <= ? AND ingested_at <= ? ORDER BY as_of DESC, ingested_at DESC, rowid DESC LIMIT 1", (account_id, decision_at.isoformat(), decision_at.isoformat())).fetchone()
        if row is None:
            raise PortfolioEvidenceError("reconciled_positions_unavailable_as_of")
        positions = {instrument_id: Position(instrument_id, Decimal(values[0]), Decimal(values[1])) for instrument_id, values in json.loads(row[4]).items()}
        return ReconciledPositionSnapshot(UUID(row[0]), UUID(row[1]), row[2], row[3], positions, datetime.fromisoformat(row[5]), datetime.fromisoformat(row[6]))

    def projected_exposures_as_of(self, *, account_id: str, intent: OrderIntent, decision_at: datetime, instruments: SQLiteInstrumentStore, quotes: SQLiteQuoteStore) -> tuple[ReconciledPositionSnapshot, list[PortfolioExposure]]:
        snapshot = self.latest_as_of(account_id, decision_at)
        if snapshot.account_id != intent.account_id:
            raise PortfolioEvidenceError("intent_account_mismatch")
        quantities = {instrument_id: position.quantity for instrument_id, position in snapshot.positions.items() if position.quantity != 0}
        order_quantity = intent.quantity if intent.side is OrderSide.BUY else -intent.quantity
        quantities[intent.instrument_id] = quantities.get(intent.instrument_id, Decimal("0")) + order_quantity
        exposures: list[PortfolioExposure] = []
        try:
            for instrument_id, quantity in sorted(quantities.items()):
                if quantity == 0:
                    continue
                instrument = instruments.get(instrument_id)
                quote = quotes.latest_as_of(instrument_id, decision_at)
                existing_quantity = snapshot.positions.get(instrument_id, Position(instrument_id, Decimal("0"), Decimal("0"))).quantity
                order_price = quote.ask if intent.side is OrderSide.BUY else quote.bid
                notional = existing_quantity * quote.last + (order_quantity * order_price if instrument_id == intent.instrument_id else Decimal("0"))
                exposures.append(PortfolioExposure(instrument_id, notional, instrument.asset_class.value, "", currency=instrument.quote_currency, broker=snapshot.source, exchange=instrument.venue))
        except (KeyError, QuoteDataError) as error:
            raise PortfolioEvidenceError("projected_exposure_market_or_instrument_evidence_unavailable") from error
        return snapshot, exposures

    def close(self) -> None:
        self._connection.close()
