"""Deterministic data-health detection, persistence and operational gating."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import cast
from uuid import UUID, uuid4

from .persistence import PostgresDatabase


class DataHealthError(ValueError):
    pass


class DataHealthBlockedError(DataHealthError):
    pass


class DataHealthCheck(StrEnum):
    # Module 3I.1 additions for the futures settlement / open-interest families.
    MISSING_EXPECTED_SESSIONS = "MISSING_EXPECTED_SESSIONS"
    NON_POSITIVE_SETTLEMENT = "NON_POSITIVE_SETTLEMENT"
    NEGATIVE_OPEN_INTEREST = "NEGATIVE_OPEN_INTEREST"
    OPEN_INTEREST_UNIT_INCONSISTENCY = "OPEN_INTEREST_UNIT_INCONSISTENCY"
    SETTLEMENT_FINALITY_REGRESSION = "SETTLEMENT_FINALITY_REGRESSION"
    MISSING_BARS = "MISSING_BARS"
    DUPLICATE_BARS = "DUPLICATE_BARS"
    TIMESTAMP_REGRESSION = "TIMESTAMP_REGRESSION"
    IMPOSSIBLE_OHLC = "IMPOSSIBLE_OHLC"
    INVALID_VOLUME = "INVALID_VOLUME"
    STALE_OBSERVATIONS = "STALE_OBSERVATIONS"
    GAPS = "GAPS"
    CORPORATE_ACTION_MISMATCH = "CORPORATE_ACTION_MISMATCH"
    PROVIDER_DISAGREEMENT = "PROVIDER_DISAGREEMENT"
    TIMEZONE_SESSION_MISMATCH = "TIMEZONE_SESSION_MISMATCH"
    INCOMPLETE_DATASET = "INCOMPLETE_DATASET"


class DataHealthAction(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    DEGRADE_CONFIDENCE = "DEGRADE_CONFIDENCE"
    BLOCK_INSTRUMENT = "BLOCK_INSTRUMENT"
    BLOCK_STRATEGY = "BLOCK_STRATEGY"
    BLOCK_ASSET_CLASS = "BLOCK_ASSET_CLASS"
    GLOBAL_BLOCK = "GLOBAL_BLOCK"


class DataHealthScope(StrEnum):
    INSTRUMENT = "INSTRUMENT"
    STRATEGY = "STRATEGY"
    ASSET_CLASS = "ASSET_CLASS"
    GLOBAL = "GLOBAL"


#: Checks the OHLCV bar detector (:func:`detect_data_health`) can raise.
OHLCV_DATA_HEALTH_CHECKS: frozenset[DataHealthCheck] = frozenset(
    {
        DataHealthCheck.MISSING_BARS,
        DataHealthCheck.DUPLICATE_BARS,
        DataHealthCheck.TIMESTAMP_REGRESSION,
        DataHealthCheck.IMPOSSIBLE_OHLC,
        DataHealthCheck.INVALID_VOLUME,
        DataHealthCheck.STALE_OBSERVATIONS,
        DataHealthCheck.GAPS,
        DataHealthCheck.CORPORATE_ACTION_MISMATCH,
        DataHealthCheck.PROVIDER_DISAGREEMENT,
        DataHealthCheck.TIMEZONE_SESSION_MISMATCH,
        DataHealthCheck.INCOMPLETE_DATASET,
    }
)

#: Checks the futures settlement / open-interest detector can raise
#: (:func:`detect_futures_series_health`).
FUTURES_SERIES_DATA_HEALTH_CHECKS: frozenset[DataHealthCheck] = frozenset(
    {
        DataHealthCheck.MISSING_EXPECTED_SESSIONS,
        DataHealthCheck.NON_POSITIVE_SETTLEMENT,
        DataHealthCheck.NEGATIVE_OPEN_INTEREST,
        DataHealthCheck.OPEN_INTEREST_UNIT_INCONSISTENCY,
        DataHealthCheck.SETTLEMENT_FINALITY_REGRESSION,
        DataHealthCheck.TIMESTAMP_REGRESSION,
        DataHealthCheck.STALE_OBSERVATIONS,
    }
)

_ACTION_RANK = {action: rank for rank, action in enumerate(DataHealthAction)}
_BLOCKING_ACTIONS = frozenset(
    {
        DataHealthAction.BLOCK_INSTRUMENT,
        DataHealthAction.BLOCK_STRATEGY,
        DataHealthAction.BLOCK_ASSET_CLASS,
        DataHealthAction.GLOBAL_BLOCK,
    }
)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataHealthError(f"{name}_must_be_timezone_aware")


@dataclass(frozen=True, slots=True)
class DataHealthObservation:
    provider: str
    instrument_id: str
    event_at: datetime
    ingested_at: datetime
    revision: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    original_timezone: str
    expected_timezone: str
    session_valid: bool
    corporate_action_consistent: bool = True

    def validate_identity(self) -> None:
        if not self.provider.strip() or not self.instrument_id.strip() or self.revision < 0:
            raise DataHealthError("invalid_data_health_observation_identity")
        _aware(self.event_at, "event_at")
        _aware(self.ingested_at, "ingested_at")


@dataclass(frozen=True, slots=True)
class DataHealthPolicy:
    version: str
    expected_start: datetime
    expected_end: datetime
    expected_interval: timedelta
    stale_after: timedelta
    provider_disagreement_tolerance: Decimal
    minimum_observations: int

    def validate(self) -> None:
        _aware(self.expected_start, "expected_start")
        _aware(self.expected_end, "expected_end")
        if (
            not self.version.strip()
            or self.expected_end < self.expected_start
            or self.expected_interval <= timedelta(0)
            or self.stale_after < timedelta(0)
            or self.provider_disagreement_tolerance < 0
            or self.minimum_observations < 1
        ):
            raise DataHealthError("invalid_data_health_policy")


@dataclass(frozen=True, slots=True)
class DataHealthFinding:
    check_type: DataHealthCheck
    action: DataHealthAction
    observed_at: datetime | None
    detail: dict[str, object]
    finding_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class DataHealthAssessment:
    assessment_id: UUID
    dataset_version_id: UUID | None
    scope_type: DataHealthScope
    scope_value: str
    policy_version: str
    evaluated_at: datetime
    expected_start: datetime
    expected_end: datetime
    max_action: DataHealthAction
    blocking: bool
    findings: tuple[DataHealthFinding, ...]
    content_hash: str
    interval: str = ""
    """Empty for scopes with no per-series dimension (GLOBAL/STRATEGY/ASSET_CLASS, or an
    INSTRUMENT-wide assessment not tied to one series). Non-empty for a specific
    (instrument, interval) series -- this is what lets the SAME instrument have
    independently-tracked, coexisting assessments for e.g. "1d" and "1m" without one
    colliding with or masking the other. See ``DataHealthScope.INSTRUMENT`` usage in
    ``scheduler.run_data_health_evaluation``.
    """
    observation_kind: str = ""
    """Module 3I.1: which observation family this assessment covers.

    Empty preserves the pre-3I.1 meaning (an OHLCV bar-series assessment, or a
    scope with no series dimension at all). Non-empty names the kind -- e.g.
    ``SETTLEMENT_PRICE`` or ``OPEN_INTEREST`` -- so one futures contract can
    carry independent, coexisting health for its bars, its settlement series and
    its open-interest series. Without this dimension they would collide on the
    assessment uniqueness constraint exactly as "1d" and "1m" did before
    migration 0039 added [[interval]].
    """
    source_id: UUID | None = None
    """The authorized source this assessment evaluated, when it is source-specific.

    ``None`` for assessments that are not attributable to one source. Recording
    it lets two providers of the same series be judged separately rather than
    one provider's outage silently condemning the instrument.
    """


def _finding(
    check: DataHealthCheck, action: DataHealthAction, observed_at: datetime | None,
    **detail: object,
) -> DataHealthFinding:
    return DataHealthFinding(check, action, observed_at, detail)


def detect_data_health(
    observations: list[DataHealthObservation], policy: DataHealthPolicy,
) -> tuple[DataHealthFinding, ...]:
    """Run all required detectors without silently changing the observations."""
    policy.validate()
    for observation in observations:
        observation.validate_identity()
    findings: list[DataHealthFinding] = []
    if not observations:
        return (
            _finding(DataHealthCheck.MISSING_BARS, DataHealthAction.BLOCK_INSTRUMENT, None, reason="empty_dataset"),
            _finding(DataHealthCheck.INCOMPLETE_DATASET, DataHealthAction.BLOCK_STRATEGY, None, reason="empty_dataset"),
        )

    seen: set[tuple[str, str, datetime, int]] = set()
    previous = observations[0].event_at
    for index, observation in enumerate(observations):
        key = (observation.provider, observation.instrument_id, observation.event_at, observation.revision)
        if key in seen:
            findings.append(_finding(DataHealthCheck.DUPLICATE_BARS, DataHealthAction.DEGRADE_CONFIDENCE, observation.event_at, provider=observation.provider))
        seen.add(key)
        if index and observation.event_at < previous:
            findings.append(_finding(DataHealthCheck.TIMESTAMP_REGRESSION, DataHealthAction.BLOCK_INSTRUMENT, observation.event_at, previous=previous.isoformat()))
        previous = observation.event_at
        if (
            min(observation.open, observation.high, observation.low, observation.close) <= 0
            or observation.high < max(observation.open, observation.close)
            or observation.low > min(observation.open, observation.close)
        ):
            findings.append(_finding(DataHealthCheck.IMPOSSIBLE_OHLC, DataHealthAction.BLOCK_INSTRUMENT, observation.event_at))
        if not observation.volume.is_finite() or observation.volume < 0:
            findings.append(_finding(DataHealthCheck.INVALID_VOLUME, DataHealthAction.BLOCK_INSTRUMENT, observation.event_at, volume=str(observation.volume)))
        if not observation.corporate_action_consistent:
            findings.append(_finding(DataHealthCheck.CORPORATE_ACTION_MISMATCH, DataHealthAction.BLOCK_INSTRUMENT, observation.event_at))
        if observation.original_timezone != observation.expected_timezone or not observation.session_valid:
            findings.append(_finding(DataHealthCheck.TIMEZONE_SESSION_MISMATCH, DataHealthAction.BLOCK_INSTRUMENT, observation.event_at, original_timezone=observation.original_timezone, expected_timezone=observation.expected_timezone, session_valid=observation.session_valid))

    ordered_times = sorted({item.event_at for item in observations})
    for previous_time, current_time in pairwise(ordered_times):
        if current_time - previous_time > policy.expected_interval:
            missing = max(1, int((current_time - previous_time) / policy.expected_interval) - 1)
            findings.append(_finding(DataHealthCheck.MISSING_BARS, DataHealthAction.WARN, current_time, missing_intervals=missing))
            findings.append(_finding(DataHealthCheck.GAPS, DataHealthAction.DEGRADE_CONFIDENCE, current_time, gap_seconds=int((current_time - previous_time).total_seconds())))

    latest = max(ordered_times)
    if policy.expected_end - latest > policy.stale_after:
        findings.append(_finding(DataHealthCheck.STALE_OBSERVATIONS, DataHealthAction.BLOCK_STRATEGY, latest, stale_seconds=int((policy.expected_end - latest).total_seconds())))

    by_time: dict[tuple[str, datetime], list[DataHealthObservation]] = {}
    for observation in observations:
        by_time.setdefault((observation.instrument_id, observation.event_at), []).append(observation)
    for (_, event_at), records in by_time.items():
        provider_closes = {record.provider: record.close for record in records}
        if len(provider_closes) > 1:
            low, high = min(provider_closes.values()), max(provider_closes.values())
            if low <= 0 or (high - low) / low > policy.provider_disagreement_tolerance:
                findings.append(_finding(DataHealthCheck.PROVIDER_DISAGREEMENT, DataHealthAction.DEGRADE_CONFIDENCE, event_at, closes={key: str(value) for key, value in sorted(provider_closes.items())}))

    if (
        len(observations) < policy.minimum_observations
        or min(ordered_times) > policy.expected_start
        or latest < policy.expected_end
    ):
        findings.append(_finding(DataHealthCheck.INCOMPLETE_DATASET, DataHealthAction.BLOCK_STRATEGY, latest, observation_count=len(observations), minimum_observations=policy.minimum_observations))
    return tuple(findings)


def build_assessment(
    observations: list[DataHealthObservation], policy: DataHealthPolicy, *,
    scope_type: DataHealthScope, scope_value: str, evaluated_at: datetime,
    dataset_version_id: UUID | None = None, interval: str = "",
) -> DataHealthAssessment:
    """Build one immutable assessment.

    ``interval`` is part of this assessment's identity alongside
    ``(scope_type, scope_value, evaluated_at)`` -- pass the series' own interval
    (e.g. ``"1d"``, ``"1m"``) for a per-series INSTRUMENT assessment so it coexists
    with, rather than collides with or masks, other intervals of the same
    instrument evaluated at the same timestamp. Leave it empty for scopes with no
    per-series dimension (GLOBAL/STRATEGY/ASSET_CLASS) or a genuinely
    instrument-wide (not series-specific) assessment.
    """
    _aware(evaluated_at, "evaluated_at")
    if not scope_value.strip() or (scope_type is DataHealthScope.GLOBAL and scope_value != "*"):
        raise DataHealthError("invalid_data_health_scope")
    findings = detect_data_health(observations, policy)
    max_action = max((item.action for item in findings), key=_ACTION_RANK.__getitem__, default=DataHealthAction.INFO)
    canonical = json.dumps(
        {
            "dataset_version_id": None if dataset_version_id is None else str(dataset_version_id),
            "scope_type": scope_type.value, "scope_value": scope_value, "interval": interval,
            "policy_version": policy.version, "evaluated_at": evaluated_at.isoformat(),
            "findings": [
                {"check": item.check_type.value, "action": item.action.value,
                 "observed_at": None if item.observed_at is None else item.observed_at.isoformat(),
                 "detail": item.detail}
                for item in findings
            ],
        },
        sort_keys=True, separators=(",", ":"),
    )
    return DataHealthAssessment(
        uuid4(), dataset_version_id, scope_type, scope_value, policy.version,
        evaluated_at, policy.expected_start, policy.expected_end, max_action,
        max_action in _BLOCKING_ACTIONS, findings,
        hashlib.sha256(canonical.encode()).hexdigest(), interval,
    )


@dataclass(frozen=True, slots=True)
class FuturesSeriesObservation:
    """One settlement or open-interest record, as Data Health sees it."""

    source_id: UUID
    instrument_id: str
    observation_kind: str
    event_at: datetime
    ingested_at: datetime
    revision: int
    #: Settlement price, or ``None`` for an open-interest record.
    settlement_price: Decimal | None = None
    finality: str | None = None
    #: Open interest, or ``None`` for a settlement record.
    open_interest: Decimal | None = None
    open_interest_unit: str | None = None


def detect_futures_series_health(
    observations: list[FuturesSeriesObservation],
    *,
    expected_sessions: tuple[date, ...] | None = None,
    stale_after: timedelta | None = None,
    evaluated_at: datetime | None = None,
) -> tuple[DataHealthFinding, ...]:
    """Health checks for the futures settlement / open-interest families.

    ``expected_sessions`` is **never inferred**. Settlement and open interest
    have no universal cadence -- an exchange publishes them on its own session
    calendar, some contracts settle daily and some do not publish open interest
    at all -- so completeness is only evaluated when a caller supplies the
    sessions it actually expects from explicit contract or source semantics.
    Passing ``None`` reports every other check and simply makes no completeness
    claim, which is the honest outcome when the cadence is unknown.
    """
    findings: list[DataHealthFinding] = []
    ordered = sorted(observations, key=lambda item: (item.event_at, item.revision))

    previous_event: datetime | None = None
    latest_finality: dict[tuple[str, datetime], tuple[int, str]] = {}
    units: dict[str, set[str]] = {}
    for observation in ordered:
        if observation.settlement_price is not None and observation.settlement_price <= 0:
            findings.append(_finding(
                DataHealthCheck.NON_POSITIVE_SETTLEMENT, DataHealthAction.BLOCK_INSTRUMENT,
                observation.event_at, instrument_id=observation.instrument_id,
                settlement_price=str(observation.settlement_price),
            ))
        if observation.open_interest is not None and observation.open_interest < 0:
            findings.append(_finding(
                DataHealthCheck.NEGATIVE_OPEN_INTEREST, DataHealthAction.BLOCK_INSTRUMENT,
                observation.event_at, instrument_id=observation.instrument_id,
                open_interest=str(observation.open_interest),
            ))
        if observation.open_interest_unit is not None:
            units.setdefault(observation.instrument_id, set()).add(observation.open_interest_unit)
        if previous_event is not None and observation.event_at < previous_event:
            findings.append(_finding(
                DataHealthCheck.TIMESTAMP_REGRESSION, DataHealthAction.BLOCK_INSTRUMENT,
                observation.event_at, previous_event=previous_event.isoformat(),
            ))
        previous_event = observation.event_at

        if observation.finality is not None:
            key = (observation.instrument_id, observation.event_at)
            seen = latest_finality.get(key)
            if seen is not None and seen[1] == "FINAL" and observation.finality == "PRELIMINARY":
                # A later revision may restate a final settlement, but it may
                # not demote it back to preliminary -- that would mean the
                # provider's own finality contract is not being honoured.
                findings.append(_finding(
                    DataHealthCheck.SETTLEMENT_FINALITY_REGRESSION,
                    DataHealthAction.DEGRADE_CONFIDENCE, observation.event_at,
                    instrument_id=observation.instrument_id,
                    previous_revision=seen[0], revision=observation.revision,
                ))
            if seen is None or observation.revision >= seen[0]:
                latest_finality[key] = (observation.revision, observation.finality)

    for instrument_id, observed_units in sorted(units.items()):
        if len(observed_units) > 1:
            # Two different units in one series cannot be compared, and this
            # module never converts between them.
            findings.append(_finding(
                DataHealthCheck.OPEN_INTEREST_UNIT_INCONSISTENCY,
                DataHealthAction.BLOCK_INSTRUMENT, None,
                instrument_id=instrument_id, units=sorted(observed_units),
            ))

    if expected_sessions is not None:
        observed_dates = {item.event_at.date() for item in ordered}
        missing = sorted(set(expected_sessions) - observed_dates)
        if missing:
            findings.append(_finding(
                DataHealthCheck.MISSING_EXPECTED_SESSIONS, DataHealthAction.BLOCK_INSTRUMENT,
                None, missing_sessions=[value.isoformat() for value in missing],
                expected_session_count=len(expected_sessions),
            ))

    if stale_after is not None and evaluated_at is not None and ordered:
        _aware(evaluated_at, "evaluated_at")
        newest = max(item.event_at for item in ordered)
        if evaluated_at - newest > stale_after:
            findings.append(_finding(
                DataHealthCheck.STALE_OBSERVATIONS, DataHealthAction.DEGRADE_CONFIDENCE,
                newest, stale_after_seconds=stale_after.total_seconds(),
            ))
    return tuple(findings)


class PostgresDataHealthStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def persist(self, assessment: DataHealthAssessment) -> None:
        summary = json.dumps(
            {"finding_count": len(assessment.findings), "checks": sorted({item.check_type.value for item in assessment.findings})},
            sort_keys=True,
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO data_health_assessments VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                    (assessment.assessment_id, assessment.dataset_version_id,
                     assessment.scope_type.value, assessment.scope_value,
                     assessment.policy_version, assessment.evaluated_at,
                     assessment.expected_start, assessment.expected_end,
                     assessment.max_action.value, assessment.blocking,
                     assessment.content_hash, summary, assessment.interval,
                     assessment.observation_kind, assessment.source_id),
                )
                for sequence, finding in enumerate(assessment.findings):
                    detail = json.dumps(finding.detail, sort_keys=True, separators=(",", ":"))
                    content_hash = hashlib.sha256(f"{finding.check_type.value}|{finding.action.value}|{finding.observed_at}|{detail}".encode()).hexdigest()
                    cursor.execute(
                        "INSERT INTO data_health_findings VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
                        (finding.finding_id, assessment.assessment_id,
                         sequence, finding.check_type.value, finding.action.value,
                         finding.observed_at, detail, content_hash),
                    )
        except Exception as error:
            raise DataHealthError("data_health_persistence_failed") from error

    def active_blocks(
        self, instrument_id: str, strategy_version: str, asset_class: str, as_of: datetime,
    ) -> tuple[tuple[DataHealthScope, str, str, DataHealthAction], ...]:
        """Every currently-blocking scope relevant to this instrument/strategy/asset class.

        Partitions the "latest as of ``as_of``" lookup by ``interval`` as well as
        ``(scope_type, scope_value)``: an INSTRUMENT scope with independently-evaluated
        series (e.g. "1d" and "1m") is blocked if *any* of its series' own latest
        assessment is blocking -- a healthy daily series can never mask a blocked
        minute series, or vice versa. GLOBAL/STRATEGY/ASSET_CLASS assessments carry no
        interval (always ``""``), so this partitioning is a no-op for them and their
        prior single-latest-row behaviour is unchanged.
        """
        _aware(as_of, "data_health_as_of")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                # Partitioned by observation_kind as well as interval for the
                # same reason interval was added: a healthy bar series must not
                # mask a blocked settlement or open-interest series on the same
                # instrument. Pre-3I.1 rows carry '' and are unaffected.
                "SELECT scope_type,scope_value,interval,max_action FROM ("
                "SELECT DISTINCT ON (scope_type,scope_value,interval,observation_kind) "
                "scope_type,scope_value,interval,observation_kind,max_action,blocking "
                "FROM data_health_assessments WHERE evaluated_at<=%s AND ((scope_type='GLOBAL' AND scope_value='*') OR "
                "(scope_type='ASSET_CLASS' AND scope_value=%s) OR (scope_type='STRATEGY' AND scope_value=%s) OR "
                "(scope_type='INSTRUMENT' AND scope_value=%s)) "
                "ORDER BY scope_type,scope_value,interval,observation_kind,evaluated_at DESC"
                ") latest WHERE blocking ORDER BY scope_type,scope_value,interval,observation_kind",
                (as_of, asset_class, strategy_version, instrument_id),
            )
            rows = cursor.fetchall()
        return tuple(
            (DataHealthScope(str(row[0])), str(row[1]), str(row[2]), DataHealthAction(str(row[3])))
            for row in rows
        )

    def require_signal_validation_allowed(
        self, instrument_id: str, strategy_version: str, asset_class: str, as_of: datetime,
    ) -> None:
        blocks = self.active_blocks(instrument_id, strategy_version, asset_class, as_of)
        if blocks:
            detail = ",".join(
                f"{scope.value}:{value}:{interval or '-'}:{action.value}"
                for scope, value, interval, action in blocks
            )
            raise DataHealthBlockedError(f"signal_validation_blocked_by_data_health:{detail}")

    def get(self, assessment_id: UUID) -> DataHealthAssessment:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM data_health_assessments WHERE assessment_id=%s", (assessment_id,))
            row = cursor.fetchone()
            cursor.execute(
                "SELECT finding_id,check_type,action,observed_at,detail FROM data_health_findings WHERE assessment_id=%s ORDER BY finding_sequence",
                (assessment_id,),
            )
            finding_rows = cursor.fetchall()
        if row is None:
            raise DataHealthError("data_health_assessment_not_found")
        findings = tuple(
            DataHealthFinding(DataHealthCheck(str(item[1])), DataHealthAction(str(item[2])),
                              cast(datetime | None, item[3]), cast(dict[str, object], item[4]), UUID(str(item[0])))
            for item in finding_rows
        )
        return DataHealthAssessment(
            UUID(str(row[0])), None if row[1] is None else UUID(str(row[1])),
            DataHealthScope(str(row[2])), str(row[3]), str(row[4]), cast(datetime, row[5]),
            cast(datetime, row[6]), cast(datetime, row[7]), DataHealthAction(str(row[8])),
            bool(row[9]), findings, str(row[10]), str(row[12]), str(row[13]),
            None if row[14] is None else UUID(str(row[14])),
        )
