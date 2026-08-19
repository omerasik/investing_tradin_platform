"""PostgreSQL authorities for instrument context, validated signals and model approval."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .data_health import DataHealthBlockedError, PostgresDataHealthStore
from .domain import AssetClass, Instrument, OrderSide, Signal, SignalStatus, utc_now
from .instruments import (
    InstrumentAlreadyExistsError,
    InstrumentNotFoundError,
    InstrumentRiskProfile,
    TradingCalendarError,
    TradingSession,
)
from .model_registry import (
    ModelApprovalEvent,
    ModelStatus,
    ModelValidation,
    ModelVersion,
)
from .persistence import PostgresDatabase
from .signal_engine import (
    _SIGNAL_TRANSITIONS,
    DetailedSignalStatus,
    SignalEngineError,
    SignalLifecycleEvent,
    SignalProposal,
    SignalValidationResult,
)


class PostgresInstrumentStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, instrument: Instrument) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_instruments VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        instrument.instrument_id,
                        instrument.symbol,
                        instrument.venue,
                        instrument.asset_class.value,
                        instrument.quote_currency,
                        instrument.tick_size,
                        instrument.lot_size,
                        instrument.delisted_at,
                    ),
                )
        except Exception as error:
            raise InstrumentAlreadyExistsError(instrument.instrument_id) from error

    def get(self, instrument_id: str) -> Instrument:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT instrument_id, symbol, venue, asset_class, quote_currency, tick_size, lot_size, delisted_at FROM runtime_instruments WHERE instrument_id = %s",
                    (instrument_id,),
                )
                row = cursor.fetchone()
            if row is None:
                raise InstrumentNotFoundError(instrument_id)
            return Instrument(
                str(row[0]),
                str(row[1]),
                str(row[2]),
                AssetClass(str(row[3])),
                str(row[4]),
                Decimal(row[5]),
                Decimal(row[6]),
                row[7],
            )
        except InstrumentNotFoundError:
            raise
        except Exception as error:
            raise InstrumentNotFoundError(instrument_id) from error

    def append_risk_profile(self, profile: InstrumentRiskProfile) -> None:
        self.get(profile.instrument_id)
        if (
            not profile.sector.strip()
            or not profile.country.strip()
            or not profile.correlation_cluster.strip()
            or profile.effective_at.tzinfo is None
            or profile.ingested_at.tzinfo is None
            or profile.ingested_at < profile.effective_at
            or not all(name.strip() for name in profile.factors)
        ):
            raise ValueError("invalid_instrument_risk_profile")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_instrument_risk_profiles (instrument_id, sector, country, correlation_cluster, beta, duration, factors, effective_at, ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
                    (
                        profile.instrument_id,
                        profile.sector,
                        profile.country,
                        profile.correlation_cluster,
                        profile.beta,
                        profile.duration,
                        json.dumps({key: str(value) for key, value in profile.factors.items()}, sort_keys=True),
                        profile.effective_at,
                        profile.ingested_at,
                    ),
                )
        except Exception as error:
            raise ValueError("postgres_instrument_risk_profile_persistence_failed") from error

    def risk_profile_as_of(self, instrument_id: str, decision_at: datetime) -> InstrumentRiskProfile:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT instrument_id, sector, country, correlation_cluster, beta, duration, factors, effective_at, ingested_at FROM runtime_instrument_risk_profiles WHERE instrument_id = %s AND effective_at <= %s AND ingested_at <= %s ORDER BY effective_at DESC, ingested_at DESC, profile_id DESC LIMIT 1",
                    (instrument_id, decision_at, decision_at),
                )
                row = cursor.fetchone()
            if row is None:
                raise InstrumentNotFoundError(f"risk profile unavailable: {instrument_id}")
            return InstrumentRiskProfile(
                str(row[0]), str(row[1]), str(row[2]), str(row[3]), Decimal(row[4]),
                Decimal(row[5]), {str(key): Decimal(value) for key, value in dict(row[6]).items()},
                row[7], row[8],
            )
        except InstrumentNotFoundError:
            raise
        except Exception as error:
            raise InstrumentNotFoundError(f"risk profile unavailable: {instrument_id}") from error

    def add_trading_session(self, session: TradingSession) -> None:
        if not session.venue.strip() or not 0 <= session.weekday <= 6 or session.closes_at <= session.opens_at:
            raise TradingCalendarError("invalid_trading_session")
        try:
            ZoneInfo(session.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise TradingCalendarError(f"unknown timezone: {session.timezone_name}") from error
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_trading_sessions VALUES (%s,%s,%s,%s,%s)",
                    (session.venue, session.weekday, session.opens_at, session.closes_at, session.timezone_name),
                )
        except Exception as error:
            raise TradingCalendarError("duplicate_trading_session") from error

    def is_trading_session(self, venue: str, observed_at: datetime) -> bool:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise TradingCalendarError("observed_at must be timezone-aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT weekday, opens_at, closes_at, timezone_name FROM runtime_trading_sessions WHERE venue = %s",
                    (venue,),
                )
                rows = cursor.fetchall()
            for weekday, opens_at, closes_at, timezone_name in rows:
                local = observed_at.astimezone(ZoneInfo(str(timezone_name)))
                local_time = local.time().replace(tzinfo=None)
                if local.weekday() == int(weekday) and opens_at <= local_time < closes_at:
                    return True
            return False
        except TradingCalendarError:
            raise
        except Exception as error:
            raise TradingCalendarError("postgres_trading_session_read_failed") from error

    def close(self) -> None:
        return None


def _signal_payload(proposal: SignalProposal) -> dict[str, object]:
    return {
        name: value.value
        if isinstance(value, Enum)
        else str(value)
        if isinstance(value, (Decimal, timedelta, UUID, datetime))
        else list(value)
        if isinstance(value, tuple)
        else value
        for name, value in (
            (name, getattr(proposal, name)) for name in proposal.__dataclass_fields__
        )
    }


class PostgresSignalStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def append(self, proposal: SignalProposal) -> None:
        proposal.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_signal_proposals VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                    (proposal.signal_id, proposal.instrument_id, proposal.strategy_version, proposal.created_at, proposal.expires_at, json.dumps(_signal_payload(proposal), sort_keys=True)),
                )
        except Exception as error:
            raise SignalEngineError("postgres_signal_persistence_failed") from error

    def append_validation(self, result: SignalValidationResult) -> None:
        if result.assessed_at.tzinfo is None or result.assessed_at.utcoffset() is None:
            raise SignalEngineError("signal_validation_time_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT p.instrument_id,p.strategy_version,i.asset_class FROM runtime_signal_proposals p JOIN runtime_instruments i ON i.instrument_id=p.instrument_id WHERE p.signal_id=%s",
                    (result.signal_id,),
                )
                proposal = cursor.fetchone()
                if proposal is None:
                    raise SignalEngineError("unknown_signal")
            if result.status is DetailedSignalStatus.VALIDATED:
                PostgresDataHealthStore(self._database).require_signal_validation_allowed(
                    str(proposal[0]), str(proposal[1]), str(proposal[2]), result.assessed_at
                )
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM runtime_signal_proposals WHERE signal_id=%s FOR UPDATE", (result.signal_id,))
                if cursor.fetchone() is None:
                    raise SignalEngineError("unknown_signal")
                cursor.execute("SELECT 1 FROM runtime_signal_validations WHERE signal_id=%s LIMIT 1", (result.signal_id,))
                if cursor.fetchone() is not None:
                    raise SignalEngineError("signal_already_validated")
                cursor.execute(
                    "INSERT INTO runtime_signal_validations VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s)",
                    (result.assessment_id, result.signal_id, result.status.value, json.dumps([value.value for value in result.passed_stages]), json.dumps([value.value for value in result.failures]), result.assessed_at),
                )
                failures = ",".join(stage.value for stage in result.failures)
                reason = "all_validation_stages_passed" if not failures else f"validation_failed:{failures}"
                cursor.execute(
                    "INSERT INTO runtime_signal_lifecycle_events (event_id,signal_id,from_status,to_status,actor,reason,evidence_references,occurred_at) VALUES (%s,%s,'CANDIDATE',%s,'signal_validation',%s,%s::jsonb,%s)",
                    (uuid4(), result.signal_id, result.status.value, reason,
                     json.dumps([f"signal-validation:{result.assessment_id}"]), result.assessed_at),
                )
        except DataHealthBlockedError as error:
            raise SignalEngineError("signal_validation_blocked_by_data_health") from error
        except SignalEngineError:
            raise
        except Exception as error:
            raise SignalEngineError("postgres_signal_validation_persistence_failed") from error

    def status(self, signal_id: UUID, *, as_of: datetime | None = None) -> DetailedSignalStatus:
        decision_at = utc_now() if as_of is None else as_of
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise SignalEngineError("signal_status_as_of_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT p.signal_id,e.to_status,v.status FROM runtime_signal_proposals p "
                    "LEFT JOIN LATERAL (SELECT to_status FROM runtime_signal_lifecycle_events WHERE signal_id=p.signal_id AND occurred_at<=%s ORDER BY event_sequence DESC LIMIT 1) e ON TRUE "
                    "LEFT JOIN LATERAL (SELECT status FROM runtime_signal_validations WHERE signal_id=p.signal_id AND assessed_at<=%s ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1) v ON TRUE "
                    "WHERE p.signal_id=%s AND p.created_at<=%s",
                    (decision_at, decision_at, signal_id, decision_at),
                )
                row = cursor.fetchone()
            if row is None:
                raise SignalEngineError("unknown_signal")
            return DetailedSignalStatus(str(row[1] or row[2] or DetailedSignalStatus.CANDIDATE.value))
        except SignalEngineError:
            raise
        except Exception as error:
            raise SignalEngineError("postgres_signal_status_read_failed") from error

    def transition(
        self,
        signal_id: UUID,
        to_status: DetailedSignalStatus,
        *,
        actor: str,
        reason: str,
        evidence_references: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
    ) -> SignalLifecycleEvent:
        if not actor.strip():
            raise SignalEngineError("signal_transition_actor_required")
        if not reason.strip():
            raise SignalEngineError("signal_transition_reason_required")
        if any(not reference.strip() for reference in evidence_references):
            raise SignalEngineError("signal_transition_evidence_reference_invalid")
        timestamp = utc_now() if occurred_at is None else occurred_at
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise SignalEngineError("signal_transition_time_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT created_at FROM runtime_signal_proposals WHERE signal_id=%s FOR UPDATE", (signal_id,))
                proposal = cursor.fetchone()
                if proposal is None or proposal[0] > timestamp:
                    raise SignalEngineError("unknown_signal")
                cursor.execute(
                    "SELECT to_status,occurred_at FROM runtime_signal_lifecycle_events WHERE signal_id=%s ORDER BY event_sequence DESC LIMIT 1",
                    (signal_id,),
                )
                row = cursor.fetchone()
                if row is not None and row[1] > timestamp:
                    raise SignalEngineError("signal_transition_time_regression")
                current = DetailedSignalStatus.CANDIDATE if row is None else DetailedSignalStatus(str(row[0]))
                if to_status not in _SIGNAL_TRANSITIONS[current]:
                    raise SignalEngineError(f"invalid_signal_transition:{current.value}:{to_status.value}")
                event = SignalLifecycleEvent(
                    uuid4(), signal_id, current, to_status, actor.strip(), reason.strip(),
                    evidence_references, timestamp,
                )
                cursor.execute(
                    "INSERT INTO runtime_signal_lifecycle_events (event_id,signal_id,from_status,to_status,actor,reason,evidence_references,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                    (event.event_id, event.signal_id, current.value, event.to_status.value, event.actor,
                     event.reason, json.dumps(event.evidence_references), event.occurred_at),
                )
            return event
        except SignalEngineError:
            raise
        except Exception as error:
            raise SignalEngineError("postgres_signal_transition_failed") from error

    def lifecycle_events(self, signal_id: UUID, *, as_of: datetime | None = None) -> tuple[SignalLifecycleEvent, ...]:
        decision_at = utc_now() if as_of is None else as_of
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise SignalEngineError("signal_lifecycle_as_of_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM runtime_signal_proposals WHERE signal_id=%s AND created_at<=%s", (signal_id, decision_at))
                if cursor.fetchone() is None:
                    raise SignalEngineError("unknown_signal")
                cursor.execute(
                    "SELECT event_id,signal_id,from_status,to_status,actor,reason,evidence_references,occurred_at FROM runtime_signal_lifecycle_events WHERE signal_id=%s AND occurred_at<=%s ORDER BY event_sequence",
                    (signal_id, decision_at),
                )
                rows = cursor.fetchall()
            return tuple(SignalLifecycleEvent(
                UUID(str(row[0])), UUID(str(row[1])),
                None if row[2] is None else DetailedSignalStatus(str(row[2])),
                DetailedSignalStatus(str(row[3])), str(row[4]), str(row[5]),
                tuple(str(item) for item in row[6]), row[7],
            ) for row in rows)
        except SignalEngineError:
            raise
        except Exception as error:
            raise SignalEngineError("postgres_signal_lifecycle_read_failed") from error

    def expire_due(self, *, as_of: datetime, actor: str = "signal_expiry_scheduler", limit: int = 500) -> tuple[SignalLifecycleEvent, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise SignalEngineError("signal_expiry_as_of_must_be_timezone_aware")
        if not actor.strip():
            raise SignalEngineError("signal_transition_actor_required")
        if not 1 <= limit <= 1000:
            raise SignalEngineError("signal_expiry_limit_out_of_bounds")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT p.signal_id,p.expires_at,COALESCE(e.to_status,v.status,'CANDIDATE') status "
                    "FROM runtime_signal_proposals p "
                    "LEFT JOIN LATERAL (SELECT to_status FROM runtime_signal_lifecycle_events WHERE signal_id=p.signal_id ORDER BY event_sequence DESC LIMIT 1) e ON TRUE "
                    "LEFT JOIN LATERAL (SELECT status FROM runtime_signal_validations WHERE signal_id=p.signal_id ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1) v ON TRUE "
                    "WHERE p.expires_at<=%s AND COALESCE(e.to_status,v.status,'CANDIDATE') IN ('VALIDATED','WAITING_FOR_ENTRY','ACTIVE') "
                    "ORDER BY p.expires_at,p.signal_id LIMIT %s FOR UPDATE OF p SKIP LOCKED",
                    (as_of, limit),
                )
                rows = cursor.fetchall()
                events: list[SignalLifecycleEvent] = []
                for signal_value, expires_at, current_value in rows:
                    event = SignalLifecycleEvent(
                        uuid4(), UUID(str(signal_value)), DetailedSignalStatus(str(current_value)),
                        DetailedSignalStatus.EXPIRED, actor.strip(), "signal_expiry_reached",
                        (f"signal-expiry:{expires_at.isoformat()}",), as_of,
                    )
                    cursor.execute(
                        "INSERT INTO runtime_signal_lifecycle_events (event_id,signal_id,from_status,to_status,actor,reason,evidence_references,occurred_at) VALUES (%s,%s,%s,'EXPIRED',%s,%s,%s::jsonb,%s)",
                        (event.event_id, event.signal_id, str(current_value), event.actor, event.reason,
                         json.dumps(event.evidence_references), event.occurred_at),
                    )
                    events.append(event)
            return tuple(events)
        except Exception as error:
            raise SignalEngineError("postgres_signal_expiry_failed") from error

    def risk_signal(self, signal_id: UUID, *, as_of: datetime) -> Signal:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise SignalEngineError("risk_signal_as_of_must_be_timezone_aware")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT p.payload,p.expires_at,p.created_at,COALESCE(e.to_status,v.status,'CANDIDATE') FROM runtime_signal_proposals p "
                    "LEFT JOIN LATERAL (SELECT to_status FROM runtime_signal_lifecycle_events WHERE signal_id=p.signal_id AND occurred_at<=%s ORDER BY event_sequence DESC LIMIT 1) e ON TRUE "
                    "LEFT JOIN LATERAL (SELECT status FROM runtime_signal_validations WHERE signal_id=p.signal_id AND assessed_at<=%s ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1) v ON TRUE "
                    "WHERE p.signal_id=%s AND p.created_at<=%s",
                    (as_of, as_of, signal_id, as_of),
                )
                row = cursor.fetchone()
            if row is None:
                raise SignalEngineError("unknown_signal")
            if row[3] != DetailedSignalStatus.VALIDATED.value:
                raise SignalEngineError("risk_signal_requires_current_validated_status")
            if row[1] <= as_of:
                raise SignalEngineError("risk_signal_is_expired")
            payload = dict(row[0])
            return Signal(
                signal_id, str(payload["instrument_id"]), str(payload["strategy_version"]),
                SignalStatus.VALIDATED, row[2], row[1],
                Decimal(str(payload["data_quality_score"])),
                OrderSide(str(payload["direction"])), str(payload["explanation"]),
                Decimal(str(payload["entry_low"])), Decimal(str(payload["entry_high"])),
                Decimal(str(payload["stop_level"])),
            )
        except SignalEngineError:
            raise
        except Exception as error:
            raise SignalEngineError("postgres_signal_read_failed") from error

    def close(self) -> None:
        return None


class PostgresModelRegistry:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, model: ModelVersion) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO runtime_models VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (model.model_id, model.name, model.version, model.task, model.feature_version, model.dataset_version, model.artifact_reference, model.created_at))
        except Exception as error:
            raise ValueError("postgres_model_persistence_failed") from error

    def get(self, model_id: UUID) -> ModelVersion:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT model_id, name, model_version, task, feature_version, dataset_version, artifact_reference, created_at FROM runtime_models WHERE model_id=%s", (model_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(model_id))
        return ModelVersion(UUID(str(row[0])), str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]), str(row[6]), row[7])

    def append_validation(self, validation: ModelValidation) -> None:
        self.get(validation.model_id)
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO runtime_model_validations VALUES (%s,%s,%s::jsonb,%s,%s,%s)", (validation.validation_id, validation.model_id, json.dumps({key: str(value) for key, value in validation.metrics.items()}, sort_keys=True), validation.economic_value_after_cost, validation.evidence_reference, validation.validated_at))
        except Exception as error:
            raise ValueError("postgres_model_validation_persistence_failed") from error

    def status(self, model_id: UUID) -> ModelStatus:
        self.get(model_id)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT status FROM runtime_model_approval_events WHERE model_id=%s ORDER BY event_sequence DESC LIMIT 1", (model_id,))
            row = cursor.fetchone()
        return ModelStatus.REGISTERED if row is None else ModelStatus(str(row[0]))

    def approve(self, model_id: UUID, *, validation_id: UUID, actor: str, reason: str, explicit_reapproval: bool = False) -> ModelApprovalEvent:
        if not actor.strip() or not reason.strip():
            raise ValueError("approval_actor_and_reason_are_required")
        if self.status(model_id) is ModelStatus.DISABLED and not explicit_reapproval:
            raise PermissionError("disabled_model_requires_explicit_reapproval")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT model_id FROM runtime_model_validations WHERE validation_id=%s", (validation_id,))
            row = cursor.fetchone()
            if row is None or UUID(str(row[0])) != model_id:
                raise ValueError("approval_requires_matching_validation")
            event = ModelApprovalEvent(uuid4(), model_id, ModelStatus.APPROVED, actor, reason, validation_id, utc_now())
            cursor.execute("INSERT INTO runtime_model_approval_events (event_id, model_id, status, actor, reason, validation_id, occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (event.event_id, event.model_id, event.status.value, event.actor, event.reason, event.validation_id, event.occurred_at))
            return event

    def is_approved(self, model_id: UUID) -> bool:
        return self.status(model_id) is ModelStatus.APPROVED

    def close(self) -> None:
        return None
