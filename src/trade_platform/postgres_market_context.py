"""Point-in-time PostgreSQL market, execution and return-history evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .domain import OrderSide
from .execution_evidence import (
    EventRiskObservation,
    ExecutionEvidenceError,
    ExecutionEvidenceSnapshot,
    HaltObservation,
    SlippageEstimate,
    _validate_record,
)
from .persistence import PostgresDatabase
from .quotes import QuoteDataError, QuoteObservation
from .return_history import (
    PortfolioReturnObservation,
    ReturnHistoryError,
    ReturnHistoryEvidence,
)


class PostgresQuoteStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append(self, quote: QuoteObservation) -> None:
        quote.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_quote_observations VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        quote.quote_id,
                        quote.instrument_id,
                        quote.bid,
                        quote.ask,
                        quote.last,
                        quote.quality_score,
                        quote.provider,
                        quote.source_reference,
                        quote.observed_at,
                        quote.ingested_at,
                    ),
                )
        except Exception as error:
            raise QuoteDataError("postgres_quote_persistence_failed") from error

    def latest_as_of(self, instrument_id: str, decision_at: datetime) -> QuoteObservation:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise QuoteDataError("quote_decision_time_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT quote_id, instrument_id, bid, ask, last, quality_score, provider, "
                    "source_reference, observed_at, ingested_at FROM runtime_quote_observations "
                    "WHERE instrument_id = %s AND observed_at <= %s AND ingested_at <= %s "
                    "ORDER BY observed_at DESC, ingested_at DESC, quote_id DESC LIMIT 1",
                    (instrument_id, decision_at, decision_at),
                )
                row = cursor.fetchone()
            if row is None:
                raise QuoteDataError("quote_unavailable_as_of")
            return QuoteObservation(
                UUID(str(row[0])),
                str(row[1]),
                Decimal(row[2]),
                Decimal(row[3]),
                Decimal(row[4]),
                Decimal(row[5]),
                str(row[6]),
                str(row[7]),
                row[8],
                row[9],
            )
        except QuoteDataError:
            raise
        except Exception as error:
            raise QuoteDataError("postgres_quote_read_failed") from error

    def close(self) -> None:
        return None


class PostgresExecutionEvidenceStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def _append(
        self,
        evidence_id: UUID,
        evidence_type: str,
        instrument_id: str,
        side: OrderSide | None,
        value: Decimal,
        source: str,
        source_reference: str,
        observed_at: datetime,
        ingested_at: datetime,
    ) -> None:
        _validate_record(
            instrument_id,
            source,
            source_reference,
            observed_at=observed_at,
            ingested_at=ingested_at,
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_execution_evidence VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        evidence_id,
                        evidence_type,
                        instrument_id,
                        None if side is None else side.value,
                        value,
                        source,
                        source_reference,
                        observed_at,
                        ingested_at,
                    ),
                )
        except ExecutionEvidenceError:
            raise
        except Exception as error:
            raise ExecutionEvidenceError("postgres_execution_evidence_persistence_failed") from error

    def append_halt(self, observation: HaltObservation) -> None:
        self._append(
            observation.observation_id,
            "HALT",
            observation.instrument_id,
            None,
            Decimal(int(observation.halted)),
            observation.source,
            observation.source_reference,
            observation.observed_at,
            observation.ingested_at,
        )

    def append_event_risk(self, observation: EventRiskObservation) -> None:
        if not Decimal(0) <= observation.risk <= Decimal(1):
            raise ExecutionEvidenceError("event_risk_outside_unit_interval")
        self._append(
            observation.observation_id,
            "EVENT_RISK",
            observation.instrument_id,
            None,
            observation.risk,
            observation.source,
            observation.source_reference,
            observation.observed_at,
            observation.ingested_at,
        )

    def append_slippage(self, estimate: SlippageEstimate) -> None:
        if estimate.expected_fraction < 0:
            raise ExecutionEvidenceError("negative_slippage_estimate")
        self._append(
            estimate.estimate_id,
            "SLIPPAGE",
            estimate.instrument_id,
            estimate.side,
            estimate.expected_fraction,
            estimate.source,
            estimate.source_reference,
            estimate.observed_at,
            estimate.ingested_at,
        )

    def _latest(
        self, evidence_type: str, instrument_id: str, decision_at: datetime, side: OrderSide | None
    ):
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT evidence_id, instrument_id, side, value, source, source_reference, "
                "observed_at, ingested_at FROM runtime_execution_evidence "
                "WHERE evidence_type = %s AND instrument_id = %s AND side IS NOT DISTINCT FROM %s "
                "AND observed_at <= %s AND ingested_at <= %s "
                "ORDER BY observed_at DESC, ingested_at DESC, evidence_id DESC LIMIT 1",
                (
                    evidence_type,
                    instrument_id,
                    None if side is None else side.value,
                    decision_at,
                    decision_at,
                ),
            )
            return cursor.fetchone()

    def snapshot_as_of(
        self, instrument_id: str, side: OrderSide, decision_at: datetime
    ) -> ExecutionEvidenceSnapshot:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ExecutionEvidenceError("decision_time_must_be_timezone_aware")
        try:
            halt = self._latest("HALT", instrument_id, decision_at, None)
            event = self._latest("EVENT_RISK", instrument_id, decision_at, None)
            slip = self._latest("SLIPPAGE", instrument_id, decision_at, side)
            if halt is None or event is None or slip is None:
                raise ExecutionEvidenceError("execution_evidence_unavailable_as_of")
            return ExecutionEvidenceSnapshot(
                HaltObservation(
                    UUID(str(halt[0])), str(halt[1]), bool(Decimal(halt[3])), str(halt[4]), str(halt[5]), halt[6], halt[7]
                ),
                EventRiskObservation(
                    UUID(str(event[0])), str(event[1]), Decimal(event[3]), str(event[4]), str(event[5]), event[6], event[7]
                ),
                SlippageEstimate(
                    UUID(str(slip[0])), str(slip[1]), OrderSide(str(slip[2])), Decimal(slip[3]), str(slip[4]), str(slip[5]), slip[6], slip[7]
                ),
            )
        except ExecutionEvidenceError:
            raise
        except Exception as error:
            raise ExecutionEvidenceError("postgres_execution_evidence_read_failed") from error

    def close(self) -> None:
        return None


class PostgresPortfolioReturnStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append(self, observation: PortfolioReturnObservation) -> None:
        observation.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_portfolio_returns VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        observation.observation_id,
                        observation.account_id,
                        observation.period_start,
                        observation.period_end,
                        observation.return_fraction,
                        observation.provider,
                        observation.source_reference,
                        observation.ingested_at,
                    ),
                )
        except Exception as error:
            raise ReturnHistoryError("postgres_portfolio_return_persistence_failed") from error

    def observations_as_of(
        self, account_id: str, decision_at: datetime
    ) -> list[PortfolioReturnObservation]:
        if not account_id.strip() or decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ReturnHistoryError("invalid_return_history_query")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT observation_id, account_id, period_start, period_end, return_fraction, "
                    "provider, source_reference, ingested_at FROM runtime_portfolio_returns "
                    "WHERE account_id = %s AND period_end <= %s AND ingested_at <= %s "
                    "ORDER BY period_end, ingested_at, observation_id",
                    (account_id, decision_at, decision_at),
                )
                rows = cursor.fetchall()
            return [
                PortfolioReturnObservation(
                    UUID(str(row[0])),
                    str(row[1]),
                    row[2],
                    row[3],
                    Decimal(row[4]),
                    str(row[5]),
                    str(row[6]),
                    row[7],
                )
                for row in rows
            ]
        except ReturnHistoryError:
            raise
        except Exception as error:
            raise ReturnHistoryError("postgres_portfolio_return_read_failed") from error

    def returns_as_of(self, account_id: str, decision_at: datetime) -> list[Decimal]:
        return [item.return_fraction for item in self.observations_as_of(account_id, decision_at)]

    def evidence_for_policy(
        self,
        account_id: str,
        decision_at: datetime,
        *,
        minimum_observations: int,
        window_observations: int,
        maximum_age_seconds: int,
    ) -> ReturnHistoryEvidence:
        if (
            minimum_observations < 1
            or window_observations < minimum_observations
            or maximum_age_seconds < 0
        ):
            raise ReturnHistoryError("invalid_return_history_policy_requirements")
        observations = self.observations_as_of(account_id, decision_at)
        if len(observations) < window_observations:
            raise ReturnHistoryError("insufficient_return_history_window")
        selected = observations[-window_observations:]
        if (decision_at - selected[-1].period_end).total_seconds() > maximum_age_seconds:
            raise ReturnHistoryError("stale_return_history")
        return ReturnHistoryEvidence(tuple(selected))

    def returns_for_policy(self, account_id: str, decision_at: datetime, **requirements) -> list[Decimal]:
        return self.evidence_for_policy(account_id, decision_at, **requirements).returns

    def close(self) -> None:
        return None
