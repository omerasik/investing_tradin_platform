"""Deterministic data-health detection, persistence and operational gating."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    dataset_version_id: UUID | None = None,
) -> DataHealthAssessment:
    _aware(evaluated_at, "evaluated_at")
    if not scope_value.strip() or (scope_type is DataHealthScope.GLOBAL and scope_value != "*"):
        raise DataHealthError("invalid_data_health_scope")
    findings = detect_data_health(observations, policy)
    max_action = max((item.action for item in findings), key=_ACTION_RANK.__getitem__, default=DataHealthAction.INFO)
    canonical = json.dumps(
        {
            "dataset_version_id": None if dataset_version_id is None else str(dataset_version_id),
            "scope_type": scope_type.value, "scope_value": scope_value,
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
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


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
                    "INSERT INTO data_health_assessments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (assessment.assessment_id, assessment.dataset_version_id,
                     assessment.scope_type.value, assessment.scope_value,
                     assessment.policy_version, assessment.evaluated_at,
                     assessment.expected_start, assessment.expected_end,
                     assessment.max_action.value, assessment.blocking,
                     assessment.content_hash, summary),
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
    ) -> tuple[tuple[DataHealthScope, str, DataHealthAction], ...]:
        _aware(as_of, "data_health_as_of")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT scope_type,scope_value,max_action FROM ("
                "SELECT DISTINCT ON (scope_type,scope_value) scope_type,scope_value,max_action,blocking "
                "FROM data_health_assessments WHERE evaluated_at<=%s AND ((scope_type='GLOBAL' AND scope_value='*') OR "
                "(scope_type='ASSET_CLASS' AND scope_value=%s) OR (scope_type='STRATEGY' AND scope_value=%s) OR "
                "(scope_type='INSTRUMENT' AND scope_value=%s)) ORDER BY scope_type,scope_value,evaluated_at DESC"
                ") latest WHERE blocking ORDER BY scope_type,scope_value",
                (as_of, asset_class, strategy_version, instrument_id),
            )
            rows = cursor.fetchall()
        return tuple(
            (DataHealthScope(str(row[0])), str(row[1]), DataHealthAction(str(row[2])))
            for row in rows
        )

    def require_signal_validation_allowed(
        self, instrument_id: str, strategy_version: str, asset_class: str, as_of: datetime,
    ) -> None:
        blocks = self.active_blocks(instrument_id, strategy_version, asset_class, as_of)
        if blocks:
            detail = ",".join(f"{scope.value}:{value}:{action.value}" for scope, value, action in blocks)
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
            bool(row[9]), findings, str(row[10]),
        )
