"""Operator-authorized scheduled historical acquisition (Phase 3C).

This module is the first -- and deliberately narrow -- scheduled boundary around
the Phase 3B.1 :class:`~trade_platform.historical_acquisition.HistoricalAcquisitionService`
for the already-onboarded ``CRYPTO:BYBIT:BTCUSDT:PERP`` public Bybit V5 historical
endpoints. It never reimplements ingestion, normalization, coverage, sealing or
feature materialization: every window is one deterministic
:class:`~trade_platform.historical_acquisition.HistoricalAcquisitionRequest` handed
to :meth:`HistoricalAcquisitionService.acquire`.

**Two independent gates.** A provider call is possible only when BOTH hold:

1. the deployment explicitly composed a runner from a complete, immutable
   :class:`ScheduledHistoricalAcquisitionAuthorizationV1` (see
   :func:`scheduled_acquisition_from_environment`; absent configuration means no
   runner exists at all, so ``worker_app`` stays provider-network-inert); and
2. the latest durable :class:`~trade_platform.operational_jobs.OperationalJobPolicy`
   for the authorization's ``job_name`` is enabled, operator-approved, and carries
   exactly the authorization's ``job_policy_version``.

Neither gate alone enables execution. Provider terms acceptance stays explicit on
the :class:`~trade_platform.data_providers.ProviderConfiguration` (never flipped
here) and public Bybit v1 still requires ``secret_reference is None``.

**Windows are slots, not "last run + interval".** Every window is
``[anchor + k*window_size, anchor + (k+1)*window_size)`` in UTC. The eligible set
for an invocation is a pure function of the authorization and ``as_of``: a window
is eligible iff ``end <= as_of - finality_lag``. When the outer job runs, how long
a previous run took, or whether the worker restarted never moves a window
boundary. The dataset version is derived from the authorization's data identity
and the exact window, and the idempotency key is still
:func:`~trade_platform.historical_acquisition.acquisition_fingerprint`, so the same
window always resolves to the same request and sealed dataset identity.

**Completed windows are proven by sealed datasets.** No mutable cursor or
watermark exists: a window is complete iff a dataset sealed under its
deterministic version is *exactly* that window's acquisition -- the same
`sealed_dataset_matches_request` identity proof Phase 3B.1 replay uses (a version
name alone is never proof) -- and, when feature materialization is authorized,
its canonical feature counts are exact. A sealed dataset that squats on a
scheduled version but fails the proof is left missing, so its window is submitted
to `acquire()` and fails closed there. Catch-up processes the oldest missing
windows first, at most ``maximum_catchup_windows_per_invocation`` per invocation,
and stops at the first failed or contended window, so a newer window is never
acquired past an older gap.

**Concurrency.** Each window is guarded by a PostgreSQL session advisory lock whose
key binds the authorization identity and the exact window, so two workers can never
both run provider acquisition for the same window; the loser stops and reports it.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from .bybit_crypto_provider import (
    BYBIT_DEFAULT_BASE_URL,
    BYBIT_KLINE_PAGE_LIMIT,
    BYBIT_OPEN_INTEREST_PAGE_LIMIT,
    BYBIT_PROVIDER_NAME,
    BybitCryptoHistoricalAdapter,
)
from .bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    BYBIT_BTCUSDT_SYMBOL,
)
from .data_providers import (
    HttpTransport,
    MinimumIntervalRequestPacer,
    ProviderConfiguration,
    ProviderConfigurationError,
    RetryPolicy,
)
from .historical_acquisition import (
    AcquisitionStatus,
    HistoricalAcquisitionRequest,
    HistoricalAcquisitionResult,
    HistoricalAcquisitionService,
    PostgresAcquisitionFeatureMaterializer,
    PostgresCanonicalAcquisitionEvidence,
    SealedDatasetView,
    acquisition_fingerprint,
    sealed_dataset_matches_request,
)
from .historical_market_data import ObservationKind
from .operational_jobs import OperationalJobPolicy, PostgresOperationalJobStore
from .persistence import PostgresDatabase
from .provider_ingestion import RawHistoricalAdapter
from .scheduler import JobContext, JobExecutionFailed, JobRunner, _release, _try_claim

__all__ = [
    "SCHEDULED_ACQUISITION_AUTHORIZATION_ENV",
    "SCHEDULED_ACQUISITION_MODE_ENV",
    "SCHEDULED_ACQUISITION_MODE_V1",
    "SCHEDULED_ACQUISITION_OBSERVATION_KINDS",
    "SCHEDULED_ACQUISITION_TERMS_ENV",
    "FeatureIdentityResolver",
    "JobPolicyGate",
    "PostgresAdvisoryWindowLock",
    "PostgresJobPolicyGate",
    "PostgresScheduledWindowEvidence",
    "ScheduledAcquisitionConfigurationError",
    "ScheduledAcquisitionOutcome",
    "ScheduledAcquisitionRuntimeConfig",
    "ScheduledAcquisitionWindow",
    "ScheduledHistoricalAcquisitionAuthorizationV1",
    "ScheduledHistoricalAcquisitionRunnerV1",
    "ScheduledInvocationResult",
    "ScheduledWindowEvidence",
    "ScheduledWindowOutcome",
    "ScheduledWindowPlan",
    "WindowLock",
    "authorization_from_json",
    "build_postgres_scheduled_acquisition_runner",
    "build_scheduled_request",
    "plan_scheduled_windows",
    "scheduled_acquisition_from_environment",
    "scheduled_dataset_version",
    "scheduled_dataset_version_prefix",
    "window_lock_key",
]

#: The only observation kinds the canonical scheduled Bybit path acquires --
#: exactly these, never a subset and never funding.
SCHEDULED_ACQUISITION_OBSERVATION_KINDS: frozenset[ObservationKind] = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)
_FUNDING_KINDS: frozenset[ObservationKind] = frozenset(
    {ObservationKind.FUNDING_RATE_REALIZED, ObservationKind.FUNDING_RATE_INDICATIVE}
)

_SCHEME = "trade_platform.scheduled_historical_acquisition.v1"
_DATASET_VERSION_SCHEME = "bybit-sched-v1"
_IDENTITY_PREFIX_LENGTH = 32
_LOCK_NAMESPACE = "trade_platform:scheduled_historical_acquisition_v1"
_MINUTE = timedelta(minutes=1)
_OPEN_INTEREST_GRID = timedelta(minutes=5)
_ZERO = timedelta(0)
_WINDOW_STAMP = "%Y%m%dT%H%MZ"

#: Explicit deployment opt-in. The mode value is a fixed token -- never a truthy
#: flag -- so no accidental value (``1``, ``yes``, ``on``) can enable providers.
SCHEDULED_ACQUISITION_MODE_ENV = "TRADE_PLATFORM_SCHEDULED_HISTORICAL_ACQUISITION"
SCHEDULED_ACQUISITION_MODE_V1 = "bybit_btcusdt_perp_v1"
SCHEDULED_ACQUISITION_AUTHORIZATION_ENV = (
    "TRADE_PLATFORM_SCHEDULED_HISTORICAL_ACQUISITION_AUTHORIZATION"
)
SCHEDULED_ACQUISITION_TERMS_ENV = (
    "TRADE_PLATFORM_SCHEDULED_HISTORICAL_ACQUISITION_PROVIDER_TERMS_ACCEPTED"
)
_ENVIRONMENT_KEYS = (
    SCHEDULED_ACQUISITION_MODE_ENV,
    SCHEDULED_ACQUISITION_AUTHORIZATION_ENV,
    SCHEDULED_ACQUISITION_TERMS_ENV,
)


class ScheduledAcquisitionConfigurationError(ValueError):
    """An authorization/configuration that must never yield a runnable provider path."""


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduledHistoricalAcquisitionAuthorizationV1:
    """One immutable operator authorization for scheduled Bybit acquisition.

    Every field is required; nothing here has an operational default. The
    *data identity* (:attr:`identity_hash`) binds what the sealed datasets mean
    -- authorization version, provider, source, instrument, symbol, kinds,
    normalization version, window size and anchor -- and therefore feeds the
    deterministic dataset version. The *operational bounds* (finality lag, page
    and catch-up bounds, request interval, job binding, actor) are bound by
    :attr:`content_hash` and reported with every invocation, but changing them
    cannot rename an already-sealed window.
    """

    authorization_version: str
    job_name: str
    job_policy_version: str
    provider: str
    source_id: UUID
    instrument_id: str
    provider_symbol: str
    observation_kinds: frozenset[ObservationKind]
    normalization_version: str
    materialize_features: bool
    window_size: timedelta
    schedule_anchor: datetime
    finality_lag: timedelta
    maximum_pages_per_kind: int
    maximum_catchup_windows_per_invocation: int
    minimum_request_interval: timedelta
    authorized_by: str
    authorization_reference: str
    authorized_at: datetime

    def validate(self) -> None:
        for name in (
            "authorization_version",
            "job_name",
            "job_policy_version",
            "normalization_version",
            "authorized_by",
            "authorization_reference",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ScheduledAcquisitionConfigurationError(f"invalid_{name}")
        if self.provider != BYBIT_PROVIDER_NAME:
            raise ScheduledAcquisitionConfigurationError("unsupported_provider")
        if self.instrument_id != BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID:
            raise ScheduledAcquisitionConfigurationError("unsupported_instrument")
        if self.provider_symbol != BYBIT_BTCUSDT_SYMBOL:
            raise ScheduledAcquisitionConfigurationError("unsupported_provider_symbol")
        if self.observation_kinds & _FUNDING_KINDS:
            raise ScheduledAcquisitionConfigurationError("funding_not_supported")
        if self.observation_kinds != SCHEDULED_ACQUISITION_OBSERVATION_KINDS:
            raise ScheduledAcquisitionConfigurationError(
                "observation_kinds_must_be_exactly_ohlcv_mark_index_open_interest"
            )
        _require_utc(self.schedule_anchor, "schedule_anchor")
        _require_utc(self.authorized_at, "authorized_at")
        if self.schedule_anchor.second or self.schedule_anchor.microsecond:
            raise ScheduledAcquisitionConfigurationError("schedule_anchor_not_minute_aligned")
        if self.schedule_anchor.minute % 5:
            raise ScheduledAcquisitionConfigurationError(
                "schedule_anchor_not_open_interest_aligned"
            )
        if self.window_size <= _ZERO or self.window_size % _MINUTE != _ZERO:
            raise ScheduledAcquisitionConfigurationError("window_size_must_be_whole_minutes")
        if self.window_size % _OPEN_INTEREST_GRID != _ZERO:
            raise ScheduledAcquisitionConfigurationError(
                "window_size_not_open_interest_aligned"
            )
        if self.finality_lag <= _ZERO:
            raise ScheduledAcquisitionConfigurationError("finality_lag_must_be_positive")
        if self.minimum_request_interval <= _ZERO:
            raise ScheduledAcquisitionConfigurationError(
                "minimum_request_interval_must_be_positive"
            )
        if self.maximum_catchup_windows_per_invocation < 1:
            raise ScheduledAcquisitionConfigurationError(
                "maximum_catchup_windows_per_invocation_must_be_positive"
            )
        if self.maximum_pages_per_kind < self.minimum_required_pages_per_kind():
            # A bound too small to ever cover one window would fail every window
            # forever; refuse the authorization instead of scheduling that.
            raise ScheduledAcquisitionConfigurationError(
                "maximum_pages_per_kind_cannot_cover_one_window"
            )

    def minimum_required_pages_per_kind(self) -> int:
        """Smallest page bound that can cover one window for every kind."""
        kline_points = self.window_size // _MINUTE
        open_interest_points = self.window_size // _OPEN_INTEREST_GRID
        return max(
            -(-kline_points // BYBIT_KLINE_PAGE_LIMIT),
            -(-open_interest_points // BYBIT_OPEN_INTEREST_PAGE_LIMIT),
            1,
        )

    @property
    def identity_hash(self) -> str:
        """SHA-256 over the data-identity fields that name sealed datasets."""
        return _hash(
            {
                "scheme": _SCHEME,
                "authorization_version": self.authorization_version,
                "provider": self.provider,
                "source_id": str(self.source_id),
                "instrument_id": self.instrument_id,
                "provider_symbol": self.provider_symbol,
                "observation_kinds": sorted(kind.value for kind in self.observation_kinds),
                "normalization_version": self.normalization_version,
                "window_size_seconds": int(self.window_size.total_seconds()),
                "schedule_anchor": self.schedule_anchor.astimezone(UTC).isoformat(),
            }
        )

    @property
    def content_hash(self) -> str:
        """SHA-256 over every authorization field, operational bounds included."""
        return _hash(_authorization_payload(self))


def _authorization_payload(authorization: ScheduledHistoricalAcquisitionAuthorizationV1) -> dict[str, object]:
    return {
        "authorization_version": authorization.authorization_version,
        "job_name": authorization.job_name,
        "job_policy_version": authorization.job_policy_version,
        "provider": authorization.provider,
        "source_id": str(authorization.source_id),
        "instrument_id": authorization.instrument_id,
        "provider_symbol": authorization.provider_symbol,
        "observation_kinds": sorted(kind.value for kind in authorization.observation_kinds),
        "normalization_version": authorization.normalization_version,
        "materialize_features": authorization.materialize_features,
        "window_size_seconds": int(authorization.window_size.total_seconds()),
        "schedule_anchor": authorization.schedule_anchor.astimezone(UTC).isoformat(),
        "finality_lag_seconds": int(authorization.finality_lag.total_seconds()),
        "maximum_pages_per_kind": authorization.maximum_pages_per_kind,
        "maximum_catchup_windows_per_invocation": (
            authorization.maximum_catchup_windows_per_invocation
        ),
        "minimum_request_interval_seconds": authorization.minimum_request_interval.total_seconds(),
        "authorized_by": authorization.authorized_by,
        "authorization_reference": authorization.authorization_reference,
        "authorized_at": authorization.authorized_at.astimezone(UTC).isoformat(),
    }


_AUTHORIZATION_JSON_KEYS: frozenset[str] = frozenset(
    {
        "authorization_version",
        "job_name",
        "job_policy_version",
        "provider",
        "source_id",
        "instrument_id",
        "provider_symbol",
        "observation_kinds",
        "normalization_version",
        "materialize_features",
        "window_size_seconds",
        "schedule_anchor",
        "finality_lag_seconds",
        "maximum_pages_per_kind",
        "maximum_catchup_windows_per_invocation",
        "minimum_request_interval_seconds",
        "authorized_by",
        "authorization_reference",
        "authorized_at",
    }
)


def authorization_from_json(document: str) -> ScheduledHistoricalAcquisitionAuthorizationV1:
    """Parse and validate one complete authorization document; no field is defaulted."""
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as error:
        raise ScheduledAcquisitionConfigurationError("authorization_not_valid_json") from error
    if not isinstance(payload, dict):
        raise ScheduledAcquisitionConfigurationError("authorization_must_be_an_object")
    if payload.get("secret_reference") is not None:
        raise ScheduledAcquisitionConfigurationError(
            "public_bybit_scheduled_acquisition_rejects_secret_reference"
        )
    payload.pop("secret_reference", None)
    missing = _AUTHORIZATION_JSON_KEYS - payload.keys()
    if missing:
        raise ScheduledAcquisitionConfigurationError(
            "authorization_missing_fields:" + ",".join(sorted(missing))
        )
    unknown = payload.keys() - _AUTHORIZATION_JSON_KEYS
    if unknown:
        raise ScheduledAcquisitionConfigurationError(
            "authorization_unknown_fields:" + ",".join(sorted(unknown))
        )
    try:
        kinds_raw = payload["observation_kinds"]
        if not isinstance(kinds_raw, list) or not all(isinstance(item, str) for item in kinds_raw):
            raise ScheduledAcquisitionConfigurationError("invalid_observation_kinds")
        kinds = [ObservationKind(item) for item in kinds_raw]
        if len(kinds) != len(set(kinds)):
            raise ScheduledAcquisitionConfigurationError("duplicate_observation_kinds")
        authorization = ScheduledHistoricalAcquisitionAuthorizationV1(
            authorization_version=_json_str(payload, "authorization_version"),
            job_name=_json_str(payload, "job_name"),
            job_policy_version=_json_str(payload, "job_policy_version"),
            provider=_json_str(payload, "provider"),
            source_id=UUID(_json_str(payload, "source_id")),
            instrument_id=_json_str(payload, "instrument_id"),
            provider_symbol=_json_str(payload, "provider_symbol"),
            observation_kinds=frozenset(kinds),
            normalization_version=_json_str(payload, "normalization_version"),
            materialize_features=_json_bool(payload, "materialize_features"),
            window_size=timedelta(seconds=_json_int(payload, "window_size_seconds")),
            schedule_anchor=_json_datetime(payload, "schedule_anchor"),
            finality_lag=timedelta(seconds=_json_int(payload, "finality_lag_seconds")),
            maximum_pages_per_kind=_json_int(payload, "maximum_pages_per_kind"),
            maximum_catchup_windows_per_invocation=_json_int(
                payload, "maximum_catchup_windows_per_invocation"
            ),
            minimum_request_interval=timedelta(
                seconds=_json_number(payload, "minimum_request_interval_seconds")
            ),
            authorized_by=_json_str(payload, "authorized_by"),
            authorization_reference=_json_str(payload, "authorization_reference"),
            authorized_at=_json_datetime(payload, "authorized_at"),
        )
    except ScheduledAcquisitionConfigurationError:
        raise
    except (TypeError, ValueError) as error:
        raise ScheduledAcquisitionConfigurationError("authorization_field_invalid") from error
    authorization.validate()
    return authorization


def _json_str(payload: Mapping[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ScheduledAcquisitionConfigurationError(f"authorization_field_must_be_string:{key}")
    return value


def _json_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ScheduledAcquisitionConfigurationError(f"authorization_field_must_be_boolean:{key}")
    return value


def _json_int(payload: Mapping[str, object], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScheduledAcquisitionConfigurationError(f"authorization_field_must_be_integer:{key}")
    return value


def _json_number(payload: Mapping[str, object], key: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ScheduledAcquisitionConfigurationError(f"authorization_field_must_be_number:{key}")
    return float(value)


def _json_datetime(payload: Mapping[str, object], key: str) -> datetime:
    value = datetime.fromisoformat(_json_str(payload, key))
    _require_utc(value, key)
    return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Windows, identity, planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduledAcquisitionWindow:
    """One immutable ``[start, end)`` UTC acquisition slot, ``index`` from the anchor."""

    index: int
    start: datetime
    end: datetime


def _window(authorization: ScheduledHistoricalAcquisitionAuthorizationV1, index: int) -> ScheduledAcquisitionWindow:
    start = authorization.schedule_anchor + index * authorization.window_size
    return ScheduledAcquisitionWindow(index, start, start + authorization.window_size)


def scheduled_dataset_version_prefix(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1,
) -> str:
    return f"{_DATASET_VERSION_SCHEME}:{authorization.identity_hash[:_IDENTITY_PREFIX_LENGTH]}:"


def scheduled_dataset_version(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1, window: ScheduledAcquisitionWindow
) -> str:
    """Deterministic dataset version: authorization data identity + exact window."""
    return (
        f"{scheduled_dataset_version_prefix(authorization)}"
        f"{window.start.strftime(_WINDOW_STAMP)}:{window.end.strftime(_WINDOW_STAMP)}"
    )


def _window_index_for_version(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1, version: str
) -> int | None:
    """Invert :func:`scheduled_dataset_version`, or ``None`` for any foreign version."""
    prefix = scheduled_dataset_version_prefix(authorization)
    if not version.startswith(prefix):
        return None
    try:
        start = datetime.strptime(version[len(prefix) :].split(":", 1)[0], _WINDOW_STAMP).replace(
            tzinfo=UTC
        )
    except ValueError:
        return None
    offset = start - authorization.schedule_anchor
    if offset < _ZERO or offset % authorization.window_size != _ZERO:
        return None
    index = offset // authorization.window_size
    if scheduled_dataset_version(authorization, _window(authorization, index)) != version:
        return None
    return index


def window_lock_key(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1, window: ScheduledAcquisitionWindow
) -> str:
    """Advisory-lock identity: the authorization's data identity + the exact window."""
    return (
        f"{_LOCK_NAMESPACE}:{authorization.identity_hash}:"
        f"{window.start.isoformat()}:{window.end.isoformat()}"
    )


def build_scheduled_request(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1, window: ScheduledAcquisitionWindow
) -> HistoricalAcquisitionRequest:
    """The exact Phase 3B.1 request for one window; key = ``acquisition_fingerprint``."""
    request = HistoricalAcquisitionRequest(
        source_id=authorization.source_id,
        instrument_id=authorization.instrument_id,
        provider=authorization.provider,
        provider_symbol=authorization.provider_symbol,
        start=window.start,
        end=window.end,
        observation_kinds=authorization.observation_kinds,
        normalization_version=authorization.normalization_version,
        dataset_version=scheduled_dataset_version(authorization, window),
        maximum_pages_per_kind=authorization.maximum_pages_per_kind,
        materialize_features=authorization.materialize_features,
        idempotency_key="",
    )
    return HistoricalAcquisitionRequest(
        source_id=request.source_id,
        instrument_id=request.instrument_id,
        provider=request.provider,
        provider_symbol=request.provider_symbol,
        start=request.start,
        end=request.end,
        observation_kinds=request.observation_kinds,
        normalization_version=request.normalization_version,
        dataset_version=request.dataset_version,
        maximum_pages_per_kind=request.maximum_pages_per_kind,
        materialize_features=request.materialize_features,
        idempotency_key=acquisition_fingerprint(request),
    )


@dataclass(frozen=True, slots=True)
class ScheduledWindowPlan:
    """The deterministic eligible-window view of one ``as_of``."""

    as_of: datetime
    eligible_cutoff: datetime
    windows_eligible: int
    windows_completed: int
    missing_windows: int
    pending: tuple[ScheduledAcquisitionWindow, ...]


def plan_scheduled_windows(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1,
    as_of: datetime,
    completed_versions: Collection[str],
) -> ScheduledWindowPlan:
    """Oldest-first missing windows for ``as_of``, bounded by the catch-up limit.

    A pure function of the authorization, ``as_of`` and the completed-dataset
    evidence: execution time, trigger drift and restarts cannot change it.
    """
    _require_utc(as_of, "as_of")
    cutoff = as_of - authorization.finality_lag
    offset = cutoff - authorization.schedule_anchor
    windows_eligible = 0 if offset < _ZERO else offset // authorization.window_size
    completed_indices = {
        index
        for index in (_window_index_for_version(authorization, version) for version in completed_versions)
        if index is not None and index < windows_eligible
    }
    pending: list[ScheduledAcquisitionWindow] = []
    index = 0
    bound = authorization.maximum_catchup_windows_per_invocation
    while index < windows_eligible and len(pending) < bound:
        if index not in completed_indices:
            pending.append(_window(authorization, index))
        index += 1
    return ScheduledWindowPlan(
        as_of=as_of,
        eligible_cutoff=cutoff,
        windows_eligible=windows_eligible,
        windows_completed=len(completed_indices),
        missing_windows=windows_eligible - len(completed_indices),
        pending=tuple(pending),
    )


# ---------------------------------------------------------------------------
# Collaborator protocols
# ---------------------------------------------------------------------------


class ScheduledWindowEvidence(Protocol):
    """Read-only sealed-dataset / feature evidence the planner trusts."""

    def sealed_datasets(
        self, source_id: UUID, version_prefix: str
    ) -> Mapping[str, SealedDatasetView]: ...

    def feature_counts(
        self, dataset_version_ids: tuple[UUID, ...], feature_ids: tuple[UUID, ...]
    ) -> Mapping[tuple[UUID, UUID], int]: ...


class FeatureIdentityResolver(Protocol):
    """Exact canonical feature identities (same authority as the materializer)."""

    def resolve_basis_feature_id(self, definition_created_at: datetime) -> UUID: ...

    def resolve_open_interest_feature_id(self, definition_created_at: datetime) -> UUID: ...


class WindowLock(Protocol):
    def try_claim(self, key: str) -> bool: ...

    def release(self, key: str) -> None: ...


class JobPolicyGate(Protocol):
    def effective_policy(self, job_name: str, as_of: datetime) -> OperationalJobPolicy | None: ...


class AcquisitionService(Protocol):
    def acquire(
        self, request: HistoricalAcquisitionRequest, configuration: ProviderConfiguration
    ) -> HistoricalAcquisitionResult: ...


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class ScheduledAcquisitionOutcome(StrEnum):
    UP_TO_DATE = "UP_TO_DATE"
    BACKLOG_REMAINS = "BACKLOG_REMAINS"
    WINDOW_CONTENDED = "WINDOW_CONTENDED"
    WINDOW_FAILED = "WINDOW_FAILED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


@dataclass(frozen=True, slots=True)
class ScheduledWindowOutcome:
    window: ScheduledAcquisitionWindow
    dataset_version: str
    acquisition_fingerprint: str
    status: AcquisitionStatus
    already_completed: bool
    dataset_version_id: UUID | None
    dataset_content_hash: str | None
    provider_health_status: str | None
    basis_feature_count: int | None
    open_interest_change_feature_count: int | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ScheduledInvocationResult:
    """Structured evidence of one scheduled invocation."""

    outcome: ScheduledAcquisitionOutcome
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1
    job_policy_id: UUID | None
    as_of: datetime
    eligible_cutoff: datetime
    windows_eligible: int
    windows_completed_before: int
    windows_considered: tuple[ScheduledAcquisitionWindow, ...]
    window_outcomes: tuple[ScheduledWindowOutcome, ...]
    backlog_remaining: int
    contended_window: ScheduledAcquisitionWindow | None = None
    failure_code: str | None = None
    windows_feature_incomplete: int = 0
    windows_sealed_conflicting: int = 0
    worst_case_provider_requests: int = 0

    @property
    def succeeded(self) -> bool:
        return self.outcome not in {
            ScheduledAcquisitionOutcome.WINDOW_FAILED,
            ScheduledAcquisitionOutcome.NOT_AUTHORIZED,
        }

    @property
    def first_failed_window(self) -> ScheduledWindowOutcome | None:
        return next((item for item in self.window_outcomes if item.status is not AcquisitionStatus.SUCCEEDED), None)

    def summary(self) -> dict[str, str]:
        succeeded = [item for item in self.window_outcomes if item.status is AcquisitionStatus.SUCCEEDED]
        failed = self.first_failed_window
        latest_end = (
            self.authorization.schedule_anchor + self.windows_eligible * self.authorization.window_size
            if self.windows_eligible
            else None
        )
        return {
            "scheme": _SCHEME,
            "outcome": self.outcome.value,
            "authorization_version": self.authorization.authorization_version,
            "authorization_content_hash": self.authorization.content_hash,
            "authorization_identity_hash": self.authorization.identity_hash,
            "authorization_reference": self.authorization.authorization_reference,
            "authorized_by": self.authorization.authorized_by,
            "job_policy_id": "" if self.job_policy_id is None else str(self.job_policy_id),
            "job_policy_version": self.authorization.job_policy_version,
            "scheduled_occurrence": self.as_of.astimezone(UTC).isoformat(),
            "eligible_cutoff": self.eligible_cutoff.astimezone(UTC).isoformat(),
            "latest_eligible_window_end": "" if latest_end is None else latest_end.isoformat(),
            "windows_eligible": str(self.windows_eligible),
            "windows_completed_before": str(self.windows_completed_before),
            "windows_feature_incomplete": str(self.windows_feature_incomplete),
            "windows_sealed_conflicting": str(self.windows_sealed_conflicting),
            "windows_considered": ",".join(_window_label(window) for window in self.windows_considered),
            "windows_replayed": str(sum(1 for item in succeeded if item.already_completed)),
            "windows_acquired": str(sum(1 for item in succeeded if not item.already_completed)),
            "first_failed_window": "" if failed is None else _window_label(failed.window),
            "first_failed_status": "" if failed is None else failed.status.value,
            "failure_code": self.failure_code or "",
            "contended_window": (
                "" if self.contended_window is None else _window_label(self.contended_window)
            ),
            "backlog_remaining": str(self.backlog_remaining),
            "backlog_remains": "true" if self.backlog_remaining else "false",
            "dataset_versions": ",".join(item.dataset_version for item in succeeded),
            "dataset_version_ids": ",".join(str(item.dataset_version_id) for item in succeeded),
            "dataset_content_hashes": ",".join(item.dataset_content_hash or "" for item in succeeded),
            "acquisition_fingerprints": ",".join(item.acquisition_fingerprint for item in self.window_outcomes),
            "provider_health_statuses": ",".join(
                item.provider_health_status or "REPLAY_NO_PROVIDER_CALL" for item in self.window_outcomes
            ),
            "feature_counts": ",".join(
                f"basis={item.basis_feature_count};open_interest_change={item.open_interest_change_feature_count}"
                for item in succeeded
            ),
            "maximum_pages_per_kind": str(self.authorization.maximum_pages_per_kind),
            "maximum_catchup_windows_per_invocation": str(
                self.authorization.maximum_catchup_windows_per_invocation
            ),
            "minimum_request_interval_seconds": str(
                self.authorization.minimum_request_interval.total_seconds()
            ),
            "worst_case_provider_requests": str(self.worst_case_provider_requests),
        }


def _window_label(window: ScheduledAcquisitionWindow) -> str:
    return f"[{window.start.isoformat()},{window.end.isoformat()})"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class ScheduledHistoricalAcquisitionRunnerV1:
    """Executes one scheduled invocation: plan, lock, acquire oldest-first, stop on failure."""

    def __init__(
        self,
        *,
        authorization: ScheduledHistoricalAcquisitionAuthorizationV1,
        configuration: ProviderConfiguration,
        service: AcquisitionService,
        evidence: ScheduledWindowEvidence,
        feature_identity: FeatureIdentityResolver,
        window_lock: WindowLock,
        policy_gate: JobPolicyGate,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        authorization.validate()
        _validate_configuration(authorization, configuration)
        self.authorization = authorization
        self.configuration = configuration
        self._service = service
        self._evidence = evidence
        self._feature_identity = feature_identity
        self._lock = window_lock
        self._policy_gate = policy_gate
        self._retry_attempts = (retry_policy or RetryPolicy()).maximum_attempts

    def run(self, as_of: datetime) -> ScheduledInvocationResult:
        authorization = self.authorization
        _require_utc(as_of, "as_of")
        cutoff = as_of - authorization.finality_lag
        if as_of < authorization.authorized_at:
            return self._not_authorized(as_of, cutoff, None, "authorization_not_yet_effective")

        policy = self._policy_gate.effective_policy(authorization.job_name, as_of)
        if policy is None:
            return self._not_authorized(as_of, cutoff, None, "operational_job_policy_missing")
        policy_failure = _policy_failure(authorization, policy)
        if policy_failure is not None:
            return self._not_authorized(as_of, cutoff, policy, policy_failure)
        try:
            _validate_configuration(authorization, self.configuration)
        except ScheduledAcquisitionConfigurationError as error:
            return self._not_authorized(as_of, cutoff, policy, str(error))

        completed, feature_incomplete, sealed_conflicting = self._completed_versions(as_of)
        plan = plan_scheduled_windows(authorization, as_of, completed)
        outcomes: list[ScheduledWindowOutcome] = []
        contended: ScheduledAcquisitionWindow | None = None
        failure_code: str | None = None
        for window in plan.pending:
            key = window_lock_key(authorization, window)
            if not self._lock.try_claim(key):
                # Another worker owns this exact window. Never skip past it to a
                # newer window: stop here and let a later invocation observe it.
                contended = window
                break
            try:
                outcome = self._acquire_window(window)
            finally:
                self._lock.release(key)
            outcomes.append(outcome)
            if outcome.status is not AcquisitionStatus.SUCCEEDED:
                failure_code = outcome.failure_code or outcome.status.value
                break

        acquired = sum(1 for item in outcomes if item.status is AcquisitionStatus.SUCCEEDED)
        backlog = plan.missing_windows - acquired
        if failure_code is not None:
            status = ScheduledAcquisitionOutcome.WINDOW_FAILED
        elif contended is not None:
            status = ScheduledAcquisitionOutcome.WINDOW_CONTENDED
        elif backlog:
            status = ScheduledAcquisitionOutcome.BACKLOG_REMAINS
        else:
            status = ScheduledAcquisitionOutcome.UP_TO_DATE
        return ScheduledInvocationResult(
            outcome=status,
            authorization=authorization,
            job_policy_id=policy.policy_id,
            as_of=as_of,
            eligible_cutoff=plan.eligible_cutoff,
            windows_eligible=plan.windows_eligible,
            windows_completed_before=plan.windows_completed,
            windows_considered=plan.pending,
            window_outcomes=tuple(outcomes),
            backlog_remaining=backlog,
            contended_window=contended,
            failure_code=failure_code,
            windows_feature_incomplete=feature_incomplete,
            windows_sealed_conflicting=sealed_conflicting,
            worst_case_provider_requests=(
                len(plan.pending)
                * len(authorization.observation_kinds)
                * authorization.maximum_pages_per_kind
                * self._retry_attempts
            ),
        )

    def as_job_runner(self) -> JobRunner:
        """Adapt to :data:`~trade_platform.scheduler.JobRunner`; failure => FAILED run."""

        def runner(_context: JobContext, as_of: datetime) -> Mapping[str, str]:
            result = self.run(as_of)
            summary = result.summary()
            if not result.succeeded:
                raise JobExecutionFailed(result.failure_code or result.outcome.value, summary)
            return summary

        return runner

    # ---- internals ---------------------------------------------------------

    def _completed_versions(self, as_of: datetime) -> tuple[frozenset[str], int, int]:
        """Windows proven complete: ``(completed versions, feature-incomplete, conflicting)``.

        A predictable dataset version name is never proof. A sealed dataset counts
        as complete only if :func:`sealed_dataset_matches_request` -- the exact
        Phase 3B.1 replay identity proof -- accepts it against this window's own
        request (and, when features are authorized, its canonical feature counts
        are exact). A sealed dataset that squats on a scheduled version name but
        fails that proof stays *missing*, so the oldest such window is submitted to
        ``acquire()``, whose own replay check then fails closed without a provider
        fetch; nothing is renamed, overwritten or skipped.
        """
        authorization = self.authorization
        sealed = self._evidence.sealed_datasets(
            authorization.source_id, scheduled_dataset_version_prefix(authorization)
        )
        matching: dict[str, UUID] = {}
        conflicting = 0
        for version, view in sealed.items():
            index = _window_index_for_version(authorization, version)
            if index is None:
                continue
            request = build_scheduled_request(authorization, _window(authorization, index))
            if sealed_dataset_matches_request(view, request):
                matching[version] = view.dataset_version_id
            else:
                conflicting += 1
        if not authorization.materialize_features or not matching:
            return frozenset(matching), 0, conflicting
        basis_id = self._feature_identity.resolve_basis_feature_id(as_of)
        open_interest_id = self._feature_identity.resolve_open_interest_feature_id(as_of)
        counts = self._evidence.feature_counts(
            tuple(matching.values()), (basis_id, open_interest_id)
        )
        expected_basis = authorization.window_size // _MINUTE
        expected_open_interest = authorization.window_size // _OPEN_INTEREST_GRID - 1
        complete = frozenset(
            version
            for version, dataset_id in matching.items()
            if counts.get((dataset_id, basis_id), 0) == expected_basis
            and counts.get((dataset_id, open_interest_id), 0) == expected_open_interest
        )
        # A sealed, identity-exact window whose features are incomplete stays
        # "missing": it is re-submitted in order, and acquire()'s replay path
        # resumes the features with zero provider calls.
        return complete, len(matching) - len(complete), conflicting

    def _acquire_window(self, window: ScheduledAcquisitionWindow) -> ScheduledWindowOutcome:
        request = build_scheduled_request(self.authorization, window)
        try:
            result = self._service.acquire(request, self.configuration)
        except Exception as error:  # noqa: BLE001 - any escape is this window's failure, never success
            return ScheduledWindowOutcome(
                window, request.dataset_version, request.idempotency_key,
                AcquisitionStatus.PRECONDITION_FAILED, False, None, None, None, None, None,
                f"unexpected_acquisition_error:{type(error).__name__}",
            )
        status = result.status
        failure_code = result.failure_code
        if status is AcquisitionStatus.SUCCEEDED and (
            result.dataset_version != request.dataset_version
            or result.idempotency_key != request.idempotency_key
            or result.dataset_version_id is None
            or (self.authorization.materialize_features and result.feature_counts is None)
        ):
            status = AcquisitionStatus.SEAL_FAILED
            failure_code = "acquisition_result_does_not_bind_scheduled_window"
        features = result.feature_counts
        return ScheduledWindowOutcome(
            window=window,
            dataset_version=request.dataset_version,
            acquisition_fingerprint=request.idempotency_key,
            status=status,
            already_completed=result.already_completed,
            dataset_version_id=result.dataset_version_id,
            dataset_content_hash=result.dataset_content_hash,
            provider_health_status=(
                None if result.provider_health_status is None else result.provider_health_status.value
            ),
            basis_feature_count=None if features is None else features.crypto_mark_index_basis,
            open_interest_change_feature_count=None if features is None else features.open_interest_change,
            failure_code=failure_code,
        )

    def _not_authorized(
        self,
        as_of: datetime,
        cutoff: datetime,
        policy: OperationalJobPolicy | None,
        code: str,
    ) -> ScheduledInvocationResult:
        return ScheduledInvocationResult(
            outcome=ScheduledAcquisitionOutcome.NOT_AUTHORIZED,
            authorization=self.authorization,
            job_policy_id=None if policy is None else policy.policy_id,
            as_of=as_of,
            eligible_cutoff=cutoff,
            windows_eligible=0,
            windows_completed_before=0,
            windows_considered=(),
            window_outcomes=(),
            backlog_remaining=0,
            failure_code=code,
        )


def _policy_failure(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1, policy: OperationalJobPolicy
) -> str | None:
    if policy.job_name != authorization.job_name:
        return "operational_job_policy_job_name_mismatch"
    if not policy.enabled:
        return "operational_job_policy_disabled"
    if policy.version != authorization.job_policy_version:
        return "operational_job_policy_version_not_authorized"
    return None


def _validate_configuration(
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1, configuration: ProviderConfiguration
) -> None:
    try:
        configuration.validate()
    except ProviderConfigurationError as error:
        raise ScheduledAcquisitionConfigurationError(str(error)) from error
    if configuration.provider != authorization.provider:
        raise ScheduledAcquisitionConfigurationError("configuration_provider_mismatch")
    if not configuration.base_url.startswith("https://"):
        raise ScheduledAcquisitionConfigurationError("configuration_requires_https")
    # Never flipped here: the operator's configuration must already carry it.
    if configuration.terms_accepted is not True:
        raise ScheduledAcquisitionConfigurationError("provider_terms_not_accepted")
    if configuration.secret_reference is not None:
        raise ScheduledAcquisitionConfigurationError("public_bybit_v1_requires_no_secret")
    if configuration.minimum_request_interval != authorization.minimum_request_interval:
        raise ScheduledAcquisitionConfigurationError(
            "configuration_minimum_request_interval_not_authorized"
        )


# ---------------------------------------------------------------------------
# PostgreSQL collaborators
# ---------------------------------------------------------------------------


class PostgresAdvisoryWindowLock:
    """Session-level PostgreSQL advisory lock, released on unlock or disconnect."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def try_claim(self, key: str) -> bool:
        return _try_claim(self._database, key)

    def release(self, key: str) -> None:
        _release(self._database, key)


class PostgresJobPolicyGate:
    """The latest approved durable policy for a job name, exactly as the scheduler sees it."""

    def __init__(self, job_store: PostgresOperationalJobStore) -> None:
        self._job_store = job_store

    def effective_policy(self, job_name: str, as_of: datetime) -> OperationalJobPolicy | None:
        for state in self._job_store.due_jobs(as_of):
            if state.policy.job_name == job_name:
                return state.policy
        return None


class PostgresScheduledWindowEvidence:
    """Reads sealed scheduled-window datasets (full member identity) and feature counts."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._canonical = PostgresCanonicalAcquisitionEvidence(database)

    def sealed_datasets(
        self, source_id: UUID, version_prefix: str
    ) -> Mapping[str, SealedDatasetView]:
        return self._canonical.sealed_datasets_by_version_prefix(source_id, version_prefix)

    def feature_counts(
        self, dataset_version_ids: tuple[UUID, ...], feature_ids: tuple[UUID, ...]
    ) -> Mapping[tuple[UUID, UUID], int]:
        if not dataset_version_ids or not feature_ids:
            return {}
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT dataset_version,feature_id,COUNT(*) FROM feature_materializations "
                "WHERE dataset_version=ANY(%s) AND feature_id=ANY(%s) "
                "GROUP BY dataset_version,feature_id",
                (
                    [str(item) for item in dataset_version_ids],
                    [str(item) for item in feature_ids],
                ),
            )
            rows = cursor.fetchall()
        return {(UUID(str(row[0])), UUID(str(row[1]))): int(row[2]) for row in rows}


def build_postgres_scheduled_acquisition_runner(
    database: PostgresDatabase,
    job_store: PostgresOperationalJobStore,
    authorization: ScheduledHistoricalAcquisitionAuthorizationV1,
    configuration: ProviderConfiguration,
    *,
    transport: HttpTransport | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], datetime] | None = None,
    feature_materializer: PostgresAcquisitionFeatureMaterializer | None = None,
) -> ScheduledHistoricalAcquisitionRunnerV1:
    """Compose the runner around the real PostgreSQL authorities and Bybit adapter.

    One :class:`MinimumIntervalRequestPacer` built from the configuration's own
    ``minimum_request_interval`` is shared by every adapter this runner creates, so
    pacing holds across all windows of an invocation and across invocations.
    ``transport``/``sleep``/``monotonic``/``now`` exist only so tests can script the
    provider offline; production passes none of them.
    """
    authorization.validate()
    _validate_configuration(authorization, configuration)
    sleep_fn = sleep or time.sleep
    pacer = MinimumIntervalRequestPacer(
        configuration.minimum_request_interval,
        monotonic=monotonic or time.monotonic,
        sleep=sleep_fn,
    )

    def adapter_factory(
        adapter_configuration: ProviderConfiguration, adapter_now: Callable[[], datetime]
    ) -> RawHistoricalAdapter:
        return BybitCryptoHistoricalAdapter(
            adapter_configuration, transport=transport, now=adapter_now, sleep=sleep_fn, pacer=pacer
        )

    materializer = feature_materializer or PostgresAcquisitionFeatureMaterializer(database)
    service = HistoricalAcquisitionService.for_postgres(
        database, adapter_factory=adapter_factory, feature_materializer=materializer, now=now
    )
    return ScheduledHistoricalAcquisitionRunnerV1(
        authorization=authorization,
        configuration=configuration,
        service=service,
        evidence=PostgresScheduledWindowEvidence(database),
        feature_identity=materializer,
        window_lock=PostgresAdvisoryWindowLock(database),
        policy_gate=PostgresJobPolicyGate(job_store),
    )


# ---------------------------------------------------------------------------
# Deployment opt-in
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduledAcquisitionRuntimeConfig:
    """A complete, validated deployment opt-in: authorization + provider configuration."""

    authorization: ScheduledHistoricalAcquisitionAuthorizationV1
    configuration: ProviderConfiguration


def scheduled_acquisition_from_environment(
    env: Callable[[str], str | None],
) -> ScheduledAcquisitionRuntimeConfig | None:
    """``None`` when no scheduled-acquisition variable is set; otherwise all-or-nothing.

    Any partial configuration -- one or two of the three variables, an unknown
    mode token, a terms value other than the literal ``accepted``, or an
    incomplete/invalid authorization document -- raises, so a worker can never
    silently start with a partially authorized provider runner.
    """
    values = {key: env(key) for key in _ENVIRONMENT_KEYS}
    present = {key for key, value in values.items() if value is not None and value != ""}
    if not present:
        return None
    if present != set(_ENVIRONMENT_KEYS):
        raise ScheduledAcquisitionConfigurationError(
            "scheduled_acquisition_partial_configuration_missing:"
            + ",".join(sorted(set(_ENVIRONMENT_KEYS) - present))
        )
    if values[SCHEDULED_ACQUISITION_MODE_ENV] != SCHEDULED_ACQUISITION_MODE_V1:
        raise ScheduledAcquisitionConfigurationError("scheduled_acquisition_mode_not_recognized")
    if values[SCHEDULED_ACQUISITION_TERMS_ENV] != "accepted":
        raise ScheduledAcquisitionConfigurationError("provider_terms_not_accepted")
    authorization = authorization_from_json(values[SCHEDULED_ACQUISITION_AUTHORIZATION_ENV] or "")
    configuration = ProviderConfiguration(
        provider=authorization.provider,
        base_url=BYBIT_DEFAULT_BASE_URL,
        terms_accepted=True,
        secret_reference=None,
        minimum_request_interval=authorization.minimum_request_interval,
    )
    _validate_configuration(authorization, configuration)
    return ScheduledAcquisitionRuntimeConfig(authorization, configuration)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduledAcquisitionConfigurationError(f"{name}_must_be_timezone_aware")
    if value.utcoffset() != _ZERO:
        raise ScheduledAcquisitionConfigurationError(f"{name}_must_be_utc")


def _hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
