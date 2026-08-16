"""Immutable correlated observability and SRE engineering evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase


class SreV2Error(ValueError):
    pass


class Environment(StrEnum):
    LOCAL = "LOCAL"
    CI = "CI"
    STAGING_CANDIDATE = "STAGING_CANDIDATE"


class TelemetryKind(StrEnum):
    LOG = "LOG"
    SPAN = "SPAN"
    METRIC = "METRIC"


class ProbeStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_16 = re.compile(r"^[0-9a-f]{16}$")
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_SENSITIVE = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SreV2Error(f"{name}_must_be_timezone_aware")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _identity(namespace: str, payload: object) -> tuple[UUID, str]:
    digest = _digest(payload)
    return uuid5(NAMESPACE_URL, f"sre-v2:{namespace}:{digest}"), digest


def safe_attributes(attributes: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """Reject secret-shaped telemetry rather than relying on best-effort masking."""
    keys = tuple(key for key, _ in attributes)
    if (
        len(set(keys)) != len(keys)
        or any(not key.strip() or _SENSITIVE.search(key) for key in keys)
        or any(_SENSITIVE.search(value) for _, value in attributes)
    ):
        raise SreV2Error("sensitive_or_invalid_telemetry_attribute")
    return tuple(sorted(attributes))


@dataclass(frozen=True, slots=True)
class ServiceVersionV2:
    service_id: UUID
    service_name: str
    version: str
    build_sha: str
    environment: Environment
    dependencies: tuple[str, ...]
    created_at: datetime
    deployment_status: str = "EVIDENCE_ONLY"
    service_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "service_created_at")
        if (
            not self.service_name.strip()
            or not self.version.strip()
            or not _HEX_40.fullmatch(self.build_sha)
            or not self.dependencies
            or len(set(self.dependencies)) != len(self.dependencies)
            or any(not item.strip() for item in self.dependencies)
            or self.deployment_status != "EVIDENCE_ONLY"
        ):
            raise SreV2Error("invalid_service_version")
        identity, digest = _identity("service", self.payload())
        object.__setattr__(self, "service_version_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "service_id": str(self.service_id), "service_name": self.service_name,
            "version": self.version, "build_sha": self.build_sha,
            "environment": self.environment.value, "dependencies": self.dependencies,
            "created_at": self.created_at, "deployment_status": self.deployment_status,
        }


@dataclass(frozen=True, slots=True)
class CandidateSloPolicyV2:
    service_version_id: UUID
    name: str
    indicator: str
    objective: Decimal
    window_seconds: int
    minimum_baseline_windows: int
    created_at: datetime
    status: str = "CANDIDATE_ONLY"
    approved: bool = False
    slo_policy_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "slo_created_at")
        if (
            not self.name.strip() or not self.indicator.strip()
            or not Decimal("0") < self.objective <= Decimal("1")
            or self.window_seconds <= 0 or self.minimum_baseline_windows <= 0
            or self.status != "CANDIDATE_ONLY" or self.approved
        ):
            raise SreV2Error("invalid_candidate_slo")
        identity, digest = _identity("slo", self.payload())
        object.__setattr__(self, "slo_policy_version_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "service_version_id": str(self.service_version_id), "name": self.name,
            "indicator": self.indicator, "objective": str(self.objective),
            "window_seconds": self.window_seconds,
            "minimum_baseline_windows": self.minimum_baseline_windows,
            "created_at": self.created_at, "status": self.status, "approved": self.approved,
        }


@dataclass(frozen=True, slots=True)
class TelemetryEventV2:
    service_version_id: UUID
    trace_id: str
    span_id: str
    parent_span_id: str | None
    event_kind: TelemetryKind
    name: str
    level: str
    occurred_at: datetime
    duration_ms: Decimal | None
    attributes: tuple[tuple[str, str], ...]
    telemetry_event_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.occurred_at, "telemetry_occurred_at")
        attributes = safe_attributes(self.attributes)
        if (
            not _HEX_32.fullmatch(self.trace_id)
            or not _HEX_16.fullmatch(self.span_id)
            or (self.parent_span_id is not None and not _HEX_16.fullmatch(self.parent_span_id))
            or not self.name.strip()
            or self.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
            or self.duration_ms is not None and self.duration_ms < 0
        ):
            raise SreV2Error("invalid_telemetry_event")
        object.__setattr__(self, "attributes", attributes)
        identity, digest = _identity("telemetry", self.payload())
        object.__setattr__(self, "telemetry_event_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "service_version_id": str(self.service_version_id), "trace_id": self.trace_id,
            "span_id": self.span_id, "parent_span_id": self.parent_span_id,
            "event_kind": self.event_kind.value, "name": self.name, "level": self.level,
            "occurred_at": self.occurred_at,
            "duration_ms": None if self.duration_ms is None else str(self.duration_ms),
            "attributes": self.attributes,
        }


@dataclass(frozen=True, slots=True)
class DependencyProbeV2:
    service_version_id: UUID
    dependency: str
    probe_type: str
    status: ProbeStatus
    checked_at: datetime
    latency_ms: Decimal | None
    reason: str | None
    trace_id: str
    evidence: tuple[tuple[str, str], ...]
    probe_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.checked_at, "probe_checked_at")
        evidence = safe_attributes(self.evidence)
        if (
            not self.dependency.strip() or not self.probe_type.strip()
            or not _HEX_32.fullmatch(self.trace_id)
            or self.latency_ms is not None and self.latency_ms < 0
            or self.status is not ProbeStatus.HEALTHY and not (self.reason and self.reason.strip())
        ):
            raise SreV2Error("invalid_dependency_probe")
        object.__setattr__(self, "evidence", evidence)
        identity, digest = _identity("probe", self.payload())
        object.__setattr__(self, "probe_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "service_version_id": str(self.service_version_id), "dependency": self.dependency,
            "probe_type": self.probe_type, "status": self.status.value,
            "checked_at": self.checked_at,
            "latency_ms": None if self.latency_ms is None else str(self.latency_ms),
            "reason": self.reason, "trace_id": self.trace_id, "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class SliWindowV2:
    slo_policy_version_id: UUID
    window_start: datetime
    window_end: datetime
    good_events: int
    total_events: int
    baseline_window_count: int
    minimum_baseline_windows: int
    measured_ratio: Decimal | None = field(init=False)
    evidence_state: str = field(init=False)
    claim_status: str = "ENGINEERING_EVIDENCE_ONLY"
    sli_window_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.window_start, "sli_window_start")
        _aware(self.window_end, "sli_window_end")
        if (
            self.window_end <= self.window_start or self.good_events < 0
            or self.total_events < 0 or self.good_events > self.total_events
            or self.baseline_window_count < 0 or self.minimum_baseline_windows <= 0
            or self.claim_status != "ENGINEERING_EVIDENCE_ONLY"
        ):
            raise SreV2Error("invalid_sli_window")
        ratio = None if self.total_events == 0 else Decimal(self.good_events) / Decimal(self.total_events)
        state = (
            "MEASURED"
            if self.baseline_window_count >= self.minimum_baseline_windows and ratio is not None
            else "INSUFFICIENT_BASELINE"
        )
        object.__setattr__(self, "measured_ratio", ratio)
        object.__setattr__(self, "evidence_state", state)
        identity, digest = _identity("sli-window", self.payload())
        object.__setattr__(self, "sli_window_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "slo_policy_version_id": str(self.slo_policy_version_id),
            "window_start": self.window_start, "window_end": self.window_end,
            "good_events": self.good_events, "total_events": self.total_events,
            "baseline_window_count": self.baseline_window_count,
            "minimum_baseline_windows": self.minimum_baseline_windows,
            "measured_ratio": None if self.measured_ratio is None else str(self.measured_ratio),
            "evidence_state": self.evidence_state, "claim_status": self.claim_status,
        }


@dataclass(frozen=True, slots=True)
class AlertPolicyV2:
    service_version_id: UUID
    code: str
    severity: AlertSeverity
    owner: str
    runbook_uri: str
    escalation_policy: str
    deduplication_window_seconds: int
    condition: tuple[tuple[str, str], ...]
    created_at: datetime
    status: str = "EVIDENCE_ONLY"
    alert_policy_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "alert_policy_created_at")
        condition = safe_attributes(self.condition)
        if (
            not all(value.strip() for value in (self.code, self.owner, self.runbook_uri, self.escalation_policy))
            or not self.runbook_uri.startswith("runbook://")
            or self.deduplication_window_seconds <= 0 or not condition
            or self.status != "EVIDENCE_ONLY"
        ):
            raise SreV2Error("invalid_alert_policy")
        object.__setattr__(self, "condition", condition)
        identity, digest = _identity("alert-policy", self.payload())
        object.__setattr__(self, "alert_policy_version_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "service_version_id": str(self.service_version_id), "code": self.code,
            "severity": self.severity.value, "owner": self.owner,
            "runbook_uri": self.runbook_uri, "escalation_policy": self.escalation_policy,
            "deduplication_window_seconds": self.deduplication_window_seconds,
            "condition": self.condition, "created_at": self.created_at, "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class AlertV2:
    alert_policy_version_id: UUID
    resource: str
    opened_at: datetime
    evidence_uri: str
    trace_id: str
    alert_id: UUID = field(init=False)
    fingerprint: str = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.opened_at, "alert_opened_at")
        if not self.resource.strip() or not self.evidence_uri.strip() or not _HEX_32.fullmatch(self.trace_id):
            raise SreV2Error("invalid_sre_alert")
        fingerprint = _digest((str(self.alert_policy_version_id), self.resource))
        object.__setattr__(self, "fingerprint", fingerprint)
        identity, digest = _identity("alert", self.payload())
        object.__setattr__(self, "alert_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "alert_policy_version_id": str(self.alert_policy_version_id),
            "resource": self.resource, "opened_at": self.opened_at,
            "evidence_uri": self.evidence_uri, "trace_id": self.trace_id,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class AlertEventV2:
    alert_id: UUID
    sequence: int
    status: AlertStatus
    actor: str
    occurred_at: datetime
    details: tuple[tuple[str, str], ...]
    alert_event_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.occurred_at, "alert_event_occurred_at")
        details = safe_attributes(self.details)
        if self.sequence < 0 or not self.actor.strip() or (self.sequence == 0) != (self.status is AlertStatus.OPEN):
            raise SreV2Error("invalid_alert_event")
        object.__setattr__(self, "details", details)
        identity, digest = _identity("alert-event", self.payload())
        object.__setattr__(self, "alert_event_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {"alert_id": str(self.alert_id), "sequence": self.sequence,
                "status": self.status.value, "actor": self.actor,
                "occurred_at": self.occurred_at, "details": self.details}


@dataclass(frozen=True, slots=True)
class IncidentV2:
    alert_id: UUID
    severity: AlertSeverity
    owner: str
    runbook_uri: str
    declared_at: datetime
    resolved_at: datetime | None
    post_incident_review_uri: str | None
    summary: tuple[tuple[str, str], ...]
    incident_id: UUID = field(init=False)
    status: str = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.declared_at, "incident_declared_at")
        if self.resolved_at is not None:
            _aware(self.resolved_at, "incident_resolved_at")
        summary = safe_attributes(self.summary)
        resolved = self.resolved_at is not None
        if (
            self.severity is AlertSeverity.INFO
            or not self.owner.strip()
            or not self.runbook_uri.startswith("runbook://")
            or not summary
            or (resolved and self.resolved_at is not None and self.resolved_at < self.declared_at)
            or resolved != bool(self.post_incident_review_uri)
        ):
            raise SreV2Error("invalid_incident")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "status", "RESOLVED" if resolved else "DECLARED")
        identity, digest = _identity("incident", self.payload())
        object.__setattr__(self, "incident_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "alert_id": str(self.alert_id), "severity": self.severity.value,
            "owner": self.owner, "runbook_uri": self.runbook_uri,
            "declared_at": self.declared_at, "resolved_at": self.resolved_at,
            "post_incident_review_uri": self.post_incident_review_uri,
            "status": self.status, "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class FailureDrillV2:
    service_version_id: UUID
    scenario: str
    injected_fault: str
    expected_protection: str
    observed_protection: str
    started_at: datetime
    completed_at: datetime
    recovery_seconds: Decimal
    evidence_uri: str
    environment: Environment
    drill_run_id: UUID = field(init=False)
    passed: bool = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.started_at, "drill_started_at")
        _aware(self.completed_at, "drill_completed_at")
        if (
            not all(value.strip() for value in (self.scenario, self.injected_fault, self.expected_protection, self.observed_protection, self.evidence_uri))
            or self.completed_at < self.started_at or self.recovery_seconds < 0
        ):
            raise SreV2Error("invalid_failure_drill")
        object.__setattr__(self, "passed", self.expected_protection == self.observed_protection)
        identity, digest = _identity("drill", self.payload())
        object.__setattr__(self, "drill_run_id", identity)
        object.__setattr__(self, "content_hash", digest)

    def payload(self) -> dict[str, object]:
        return {
            "service_version_id": str(self.service_version_id), "scenario": self.scenario,
            "injected_fault": self.injected_fault,
            "expected_protection": self.expected_protection,
            "observed_protection": self.observed_protection,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "recovery_seconds": str(self.recovery_seconds), "passed": self.passed,
            "evidence_uri": self.evidence_uri, "environment": self.environment.value,
        }


class PostgresSreV2Store:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def publish_service_bundle(
        self, service: ServiceVersionV2, slo: CandidateSloPolicyV2,
        alert_policy: AlertPolicyV2,
    ) -> None:
        if slo.service_version_id != service.service_version_id or alert_policy.service_version_id != service.service_version_id:
            raise SreV2Error("sre_service_bundle_mismatch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO sre_service_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT (service_version_id) DO NOTHING", (service.service_version_id,service.service_id,service.service_name,service.version,service.build_sha,service.environment.value,service.deployment_status,_canonical(service.dependencies),service.created_at,service.content_hash))
                cursor.execute("INSERT INTO sre_slo_policy_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s) ON CONFLICT (slo_policy_version_id) DO NOTHING", (slo.slo_policy_version_id,slo.service_version_id,slo.name,slo.indicator,slo.objective,slo.window_seconds,slo.minimum_baseline_windows,slo.status,slo.created_at,slo.content_hash))
                cursor.execute("INSERT INTO sre_alert_policy_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT (alert_policy_version_id) DO NOTHING", (alert_policy.alert_policy_version_id,alert_policy.service_version_id,alert_policy.code,alert_policy.severity.value,alert_policy.owner,alert_policy.runbook_uri,alert_policy.escalation_policy,alert_policy.deduplication_window_seconds,_canonical(dict(alert_policy.condition)),alert_policy.status,alert_policy.created_at,alert_policy.content_hash))
        except Exception as error:
            raise SreV2Error("sre_service_bundle_publish_failed") from error

    def append_evidence(
        self, telemetry: tuple[TelemetryEventV2, ...], probes: tuple[DependencyProbeV2, ...],
        windows: tuple[SliWindowV2, ...], drills: tuple[FailureDrillV2, ...],
    ) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                for telemetry_item in telemetry:
                    cursor.execute("INSERT INTO sre_telemetry_events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (telemetry_event_id) DO NOTHING", (telemetry_item.telemetry_event_id,telemetry_item.service_version_id,telemetry_item.trace_id,telemetry_item.span_id,telemetry_item.parent_span_id,telemetry_item.event_kind.value,telemetry_item.name,telemetry_item.level,telemetry_item.occurred_at,telemetry_item.duration_ms,_canonical(dict(telemetry_item.attributes)),telemetry_item.content_hash))
                for probe_item in probes:
                    cursor.execute("INSERT INTO sre_dependency_probes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (probe_id) DO NOTHING", (probe_item.probe_id,probe_item.service_version_id,probe_item.dependency,probe_item.probe_type,probe_item.status.value,probe_item.checked_at,probe_item.latency_ms,probe_item.reason,probe_item.trace_id,_canonical(dict(probe_item.evidence)),probe_item.content_hash))
                for window_item in windows:
                    cursor.execute("INSERT INTO sre_sli_windows VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (sli_window_id) DO NOTHING", (window_item.sli_window_id,window_item.slo_policy_version_id,window_item.window_start,window_item.window_end,window_item.good_events,window_item.total_events,window_item.measured_ratio,window_item.baseline_window_count,window_item.evidence_state,window_item.claim_status,window_item.content_hash))
                for drill_item in drills:
                    cursor.execute("INSERT INTO sre_failure_drill_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (drill_run_id) DO NOTHING", (drill_item.drill_run_id,drill_item.service_version_id,drill_item.scenario,drill_item.injected_fault,drill_item.expected_protection,drill_item.observed_protection,drill_item.started_at,drill_item.completed_at,drill_item.recovery_seconds,drill_item.passed,drill_item.evidence_uri,drill_item.environment.value,drill_item.content_hash))
        except Exception as error:
            raise SreV2Error("sre_evidence_publish_failed") from error

    def publish_alert(self, alert: AlertV2, events: tuple[AlertEventV2, ...]) -> UUID:
        if not events or any(item.alert_id != alert.alert_id for item in events):
            raise SreV2Error("alert_events_required")
        ordered = sorted(events, key=lambda item: item.sequence)
        if tuple(item.sequence for item in ordered) != tuple(range(len(ordered))):
            raise SreV2Error("alert_event_sequence_gap")
        allowed = {(AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED), (AlertStatus.OPEN, AlertStatus.RESOLVED), (AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED)}
        if any((left.status, right.status) not in allowed for left, right in pairwise(ordered)):
            raise SreV2Error("invalid_alert_status_transition")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO sre_alerts VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (alert_id) DO NOTHING", (alert.alert_id,alert.alert_policy_version_id,alert.fingerprint,alert.resource,alert.opened_at,alert.evidence_uri,alert.trace_id,alert.content_hash))
                for item in ordered:
                    cursor.execute("INSERT INTO sre_alert_events VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (alert_event_id) DO NOTHING", (item.alert_event_id,item.alert_id,item.sequence,item.status.value,item.actor,item.occurred_at,_canonical(dict(item.details)),item.content_hash))
        except Exception as error:
            raise SreV2Error("sre_alert_publish_failed") from error
        return alert.alert_id

    def latest_alert_status(self, alert_id: UUID) -> AlertStatus:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT status FROM sre_alert_events WHERE alert_id=%s ORDER BY sequence DESC LIMIT 1", (alert_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(str(alert_id))
        return AlertStatus(str(row[0]))

    def publish_incident(self, incident: IncidentV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sre_incidents VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (incident_id) DO NOTHING",
                    (incident.incident_id,incident.alert_id,incident.severity.value,
                     incident.owner,incident.runbook_uri,incident.declared_at,
                     incident.resolved_at,incident.post_incident_review_uri,
                     incident.status,_canonical(dict(incident.summary)),incident.content_hash),
                )
        except Exception as error:
            raise SreV2Error("sre_incident_publish_failed") from error
        return incident.incident_id
