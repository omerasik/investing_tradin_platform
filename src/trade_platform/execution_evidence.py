"""Durable, point-in-time evidence inputs for paper pre-trade assessment."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from .domain import MarketSnapshot, OrderSide
from .portfolio_risk import PortfolioExposure
from .pretrade_assessment import PreTradeInputEvidence, projected_exposure_fingerprint


class ExecutionEvidenceError(ValueError):
    pass


def _validate_record(*values: str, observed_at: datetime, ingested_at: datetime) -> None:
    if any(not value.strip() for value in values) or observed_at.tzinfo is None or observed_at.utcoffset() is None or ingested_at.tzinfo is None or ingested_at.utcoffset() is None or ingested_at < observed_at:
        raise ExecutionEvidenceError("invalid_execution_evidence_record")


@dataclass(frozen=True, slots=True)
class HaltObservation:
    observation_id: UUID
    instrument_id: str
    halted: bool
    source: str
    source_reference: str
    observed_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class EventRiskObservation:
    observation_id: UUID
    instrument_id: str
    risk: Decimal
    source: str
    source_reference: str
    observed_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class SlippageEstimate:
    estimate_id: UUID
    instrument_id: str
    side: OrderSide
    expected_fraction: Decimal
    source: str
    source_reference: str
    observed_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceSnapshot:
    halt: HaltObservation
    event: EventRiskObservation
    slippage: SlippageEstimate

    def pretrade_input_evidence(self, *, market_reference: str, market: MarketSnapshot, exposure_reference: str, exposure_observed_at: datetime, exposures: list[PortfolioExposure]) -> PreTradeInputEvidence:
        return PreTradeInputEvidence(
            market_reference, market.observed_at,
            f"{self.halt.source}:{self.halt.source_reference}:{self.halt.observation_id}", self.halt.observed_at,
            f"{self.event.source}:{self.event.source_reference}:{self.event.observation_id}", self.event.observed_at,
            f"{self.slippage.source}:{self.slippage.source_reference}:{self.slippage.estimate_id}", self.slippage.observed_at,
            exposure_reference, exposure_observed_at, projected_exposure_fingerprint(exposures),
        )


class SQLiteExecutionEvidenceStore:
    """Append-only execution observations selected by source availability at decision time."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS halt_observations (
                observation_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, halted INTEGER NOT NULL,
                source TEXT NOT NULL, source_reference TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_risk_observations (
                observation_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, risk TEXT NOT NULL,
                source TEXT NOT NULL, source_reference TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS slippage_estimates (
                estimate_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, side TEXT NOT NULL, expected_fraction TEXT NOT NULL,
                source TEXT NOT NULL, source_reference TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL);
        """)
        self._connection.commit()

    def append_halt(self, observation: HaltObservation) -> None:
        _validate_record(observation.instrument_id, observation.source, observation.source_reference, observed_at=observation.observed_at, ingested_at=observation.ingested_at)
        self._connection.execute("INSERT INTO halt_observations VALUES (?, ?, ?, ?, ?, ?, ?)", (str(observation.observation_id), observation.instrument_id, int(observation.halted), observation.source, observation.source_reference, observation.observed_at.isoformat(), observation.ingested_at.isoformat()))
        self._connection.commit()

    def append_event_risk(self, observation: EventRiskObservation) -> None:
        _validate_record(observation.instrument_id, observation.source, observation.source_reference, observed_at=observation.observed_at, ingested_at=observation.ingested_at)
        if not Decimal("0") <= observation.risk <= Decimal("1"):
            raise ExecutionEvidenceError("event_risk_outside_unit_interval")
        self._connection.execute("INSERT INTO event_risk_observations VALUES (?, ?, ?, ?, ?, ?, ?)", (str(observation.observation_id), observation.instrument_id, str(observation.risk), observation.source, observation.source_reference, observation.observed_at.isoformat(), observation.ingested_at.isoformat()))
        self._connection.commit()

    def append_slippage(self, estimate: SlippageEstimate) -> None:
        _validate_record(estimate.instrument_id, estimate.source, estimate.source_reference, observed_at=estimate.observed_at, ingested_at=estimate.ingested_at)
        if estimate.expected_fraction < 0:
            raise ExecutionEvidenceError("negative_slippage_estimate")
        self._connection.execute("INSERT INTO slippage_estimates VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(estimate.estimate_id), estimate.instrument_id, estimate.side.value, str(estimate.expected_fraction), estimate.source, estimate.source_reference, estimate.observed_at.isoformat(), estimate.ingested_at.isoformat()))
        self._connection.commit()

    def snapshot_as_of(self, instrument_id: str, side: OrderSide, decision_at: datetime) -> ExecutionEvidenceSnapshot:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ExecutionEvidenceError("decision_time_must_be_timezone_aware")
        halt = self._latest("halt_observations", instrument_id, decision_at)
        event = self._latest("event_risk_observations", instrument_id, decision_at)
        slip = self._connection.execute("SELECT * FROM slippage_estimates WHERE instrument_id = ? AND side = ? AND observed_at <= ? AND ingested_at <= ? ORDER BY observed_at DESC, ingested_at DESC, rowid DESC LIMIT 1", (instrument_id, side.value, decision_at.isoformat(), decision_at.isoformat())).fetchone()
        if halt is None or event is None or slip is None:
            raise ExecutionEvidenceError("execution_evidence_unavailable_as_of")
        return ExecutionEvidenceSnapshot(
            HaltObservation(UUID(halt[0]), halt[1], bool(halt[2]), halt[3], halt[4], datetime.fromisoformat(halt[5]), datetime.fromisoformat(halt[6])),
            EventRiskObservation(UUID(event[0]), event[1], Decimal(event[2]), event[3], event[4], datetime.fromisoformat(event[5]), datetime.fromisoformat(event[6])),
            SlippageEstimate(UUID(slip[0]), slip[1], OrderSide(slip[2]), Decimal(slip[3]), slip[4], slip[5], datetime.fromisoformat(slip[6]), datetime.fromisoformat(slip[7])),
        )

    def _latest(self, table: str, instrument_id: str, decision_at: datetime):
        return self._connection.execute(f"SELECT * FROM {table} WHERE instrument_id = ? AND observed_at <= ? AND ingested_at <= ? ORDER BY observed_at DESC, ingested_at DESC, rowid DESC LIMIT 1", (instrument_id, decision_at.isoformat(), decision_at.isoformat())).fetchone()

    def close(self) -> None:
        self._connection.close()
