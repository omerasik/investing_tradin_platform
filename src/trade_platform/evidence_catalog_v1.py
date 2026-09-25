"""Phase R5 UI-1 -- the Evidence & Data read model.

``RESEARCH_ONLY``. Read-only projections; no table, no migration, no write, no
acquisition, no economic assumption, no execution path. This module answers one
operator question: *what evidence does the platform actually hold, from which
source, and what timing claim could it ever support?*

It exists because the operator surfaces used to answer that question with
hard-coded text ("zero external market feeds authorized") that stopped being
true once the public Bybit V5 REST path, the first-party WebSocket recorder and
the free public trade archive were authorized. Every value below is derived at
request time from code authorities or from the append-only catalogs.

Two projections, deliberately separate:

:func:`read_evidence_catalog_v1`
    PostgreSQL catalogs (``historical_dataset_versions``,
    ``first_party_t4_datasets``, ``public_archive_datasets``,
    ``research_frame_manifests``) plus the closed timing-contract set of
    :mod:`trade_platform.evidence_tier_authority_v1`.
:func:`read_capture_availability_v1`
    The first-party capture archive on the recorder host's filesystem: proven
    coverage windows, derived gaps and excluded partitions per authorized
    capture contract, and the newest recorded clock-offset sample.

What this module never claims
-----------------------------
* **No tier verdict.** A tier shown for a source is that source's *ceiling*
  under its registered timing contract. A dataset reaches a tier only through
  :func:`trade_platform.evidence_tier_authority_v1.evaluate_evidence_tier_v1`
  over a verdict re-derived from raw evidence; a catalog row is not that
  verdict, so professional eligibility is never displayed from here.
* **No publication lag.** A T2 source's lag slot is shown exactly as stored:
  unset pending owner decision OR-5. Nothing here reads it as zero.
* **No liveness.** Files cannot prove a recorder is running. A partition
  without a manifest is reported as *not finalized* -- open or interrupted --
  and contributes no coverage, exactly as :mod:`first_party_capture_archive_v1`
  rules.
* **No bridging.** Gaps are the archive module's derived gaps, verbatim.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from .evidence_tier_authority_v1 import authorized_timing_contracts_v1
from .first_party_capture_archive_v1 import (
    derive_archive_availability_by_source_v1,
    disk_free_bytes_v1,
    find_partitions_v1,
    nanos_to_datetime,
    read_lifecycle_v1,
    session_source_id_v1,
)
from .first_party_capture_authority_v1 import first_party_bybit_capture_contract_v1

EVIDENCE_CATALOG_VERSION_V1: Final = "evidence-catalog-v1"
CAPTURE_AVAILABILITY_VERSION_V1: Final = "capture-availability-v1"

#: Catalog rows returned per list; the newest first. A display bound, not a
#: statement that older rows do not exist -- each list reports its total.
CATALOG_ROW_LIMIT_V1: Final = 100

#: A registered source's ceiling is shown as this when no timing contract names it.
NO_TIMING_AUTHORITY_V1: Final = "NO_TIMING_AUTHORITY"

_CLOCK_OFFSET_SAMPLE_KIND: Final = "CLOCK_OFFSET_SAMPLE"


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...] = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class TimingSourceView(BaseModel):
    """One source that the code grants (or pointedly does not grant) timing authority."""

    source_id: UUID
    label: str
    timing_authority: str
    #: The ceiling this source's timing contract grants; ``NO_TIMING_AUTHORITY``
    #: when it has none. Never a dataset verdict.
    tier_ceiling: str
    timing_contract_hash: str | None
    requires_declared_publication_lag: bool
    publication_lag_state: str
    tier_ceiling_reason: str | None


class HistoricalSourceSummaryView(BaseModel):
    """``historical_dataset_versions`` grouped by source, with that source's ceiling."""

    source_id: UUID
    provider: str
    dataset_name: str
    dataset_count: int
    sealed_count: int
    latest_created_at: datetime
    tier_ceiling: str
    synthetic_marker: bool


class T4DatasetCatalogView(BaseModel):
    """A catalogued first-party seal. Its identity, not a re-derived tier verdict."""

    dataset_version_id: UUID
    content_hash: str
    source_id: UUID
    session_id: UUID
    utc_day: date
    window_index: int
    first_market_knowledge_at: datetime
    last_market_knowledge_at: datetime
    observation_count: int
    distinct_knowledge_time_count: int
    sealed_at: datetime
    registered_at: datetime


class PublicArchiveDatasetCatalogView(BaseModel):
    """A catalogued free-archive (T2 event-time) dataset; its lag slot as stored."""

    dataset_version_id: UUID
    content_hash: str
    source_id: UUID
    symbol: str
    first_utc_day: date
    last_utc_day: date
    file_count: int
    trade_count: int
    publication_lag_slot: str
    registered_at: datetime


class ResearchFrameSummaryView(BaseModel):
    """``research_frame_manifests`` totals per frame kind (the R2B data plane)."""

    frame_kind: str
    manifest_count: int
    row_count: int
    total_bytes: int


class EvidenceCatalogView(BaseModel):
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    version: str = EVIDENCE_CATALOG_VERSION_V1
    as_of: datetime
    timing_sources: list[TimingSourceView]
    historical_sources: list[HistoricalSourceSummaryView]
    t4_dataset_total: int
    t4_datasets: list[T4DatasetCatalogView]
    public_archive_dataset_total: int
    public_archive_datasets: list[PublicArchiveDatasetCatalogView]
    research_frames: list[ResearchFrameSummaryView]
    limitations: list[str]


class CaptureWindowView(BaseModel):
    session_id: UUID
    start_at: datetime
    last_proven_at: datetime
    end_proof: str
    record_count: int


class CaptureGapView(BaseModel):
    start_at: datetime
    end_at: datetime | None
    kind: str
    detail: str | None


class CaptureExclusionView(BaseModel):
    #: Relative to the archive root, so no host path leaves the API.
    partition: str
    reasons: list[str]


class ClockOffsetSampleView(BaseModel):
    """The newest recorded RTT-bounded host-vs-venue offset. Evidence, never a correction."""

    sampled_at: datetime
    offset_estimate_seconds: float
    offset_bound_seconds: float
    partition: str


class CaptureSourceAvailabilityView(BaseModel):
    source_id: UUID
    exchange_symbol: str
    purpose: Literal["PRODUCTION", "MEASUREMENT"]
    proven_record_count: int
    proven_seconds: float
    windows: list[CaptureWindowView]
    gaps: list[CaptureGapView]
    not_finalized: list[CaptureExclusionView]
    latest_clock_offset: ClockOffsetSampleView | None


class CaptureAvailabilityView(BaseModel):
    state: Literal["AVAILABLE", "UNCONFIGURED", "UNAVAILABLE"]
    version: str = CAPTURE_AVAILABILITY_VERSION_V1
    as_of: datetime
    disk_free_bytes: int | None
    sources: list[CaptureSourceAvailabilityView]
    unattributed: list[CaptureExclusionView]
    limitations: list[str]


# ---------------------------------------------------------------------------
# Evidence catalog (PostgreSQL + code authorities)
# ---------------------------------------------------------------------------


def _public_archive_contract() -> Any | None:
    """The free-archive contract, or ``None`` where the analytics extra is absent.

    Its module needs pyarrow (the R2B ``analytics`` extra), which the hardened
    API container does not install. The API must still start there, so the
    import is deferred and a missing extra drops the row -- the catalog then
    says so in its limitations rather than inventing the contract.
    """
    try:
        from .bybit_public_archive_v1 import bybit_public_trade_archive_contract_v1
    except ImportError:
        return None
    return bybit_public_trade_archive_contract_v1()


def timing_sources_v1() -> list[TimingSourceView]:
    """Every source the code knows how to time, including the one with no authority yet.

    The two registered timing contracts come from the closed set in
    :func:`authorized_timing_contracts_v1`. The free trade archive is listed
    beside them because it is an authorized *acquisition* source whose timing
    contract is withheld until OR-5 declares a publication lag -- showing it as
    anything above ``NO_TIMING_AUTHORITY`` would be a claim the code does not make.
    """
    labels = {
        "NONE": "Bybit V5 public REST (historical klines)",
        "PLATFORM_RECORDER_ARRIVAL_TIMESTAMP": "First-party Bybit V5 public WebSocket capture",
    }
    views = [
        TimingSourceView(
            source_id=contract.source_id,
            label=labels.get(contract.timing_authority, contract.timing_authority),
            timing_authority=contract.timing_authority,
            tier_ceiling=contract.granted_tier,
            timing_contract_hash=contract.content_hash(),
            requires_declared_publication_lag=contract.requires_declared_publication_lag,
            publication_lag_state="NOT_APPLICABLE",
            tier_ceiling_reason=contract.tier_ceiling_reason,
        )
        for contract in authorized_timing_contracts_v1()
    ]
    archive = _public_archive_contract()
    if archive is None:
        return views
    views.append(TimingSourceView(
        source_id=archive.source_id,
        label="Bybit free public trade archive (public.bybit.com)",
        timing_authority=archive.timing_authority,
        tier_ceiling=NO_TIMING_AUTHORITY_V1,
        timing_contract_hash=None,
        requires_declared_publication_lag=True,
        publication_lag_state=archive.publication_lag_slot,
        tier_ceiling_reason="t2_timing_contract_withheld_until_publication_lag_is_declared",
    ))
    return views


def _tier_ceilings() -> dict[UUID, str]:
    return {contract.source_id: contract.granted_tier for contract in authorized_timing_contracts_v1()}


def read_evidence_catalog_v1(cursor: _Cursor, *, now: datetime | None = None) -> EvidenceCatalogView:
    """One bounded, read-only pass over the evidence catalogs. Caller owns the transaction."""
    ceilings = _tier_ceilings()
    cursor.execute(
        "SELECT h.source_id, s.provider, s.dataset_name, COUNT(*), "
        "COUNT(*) FILTER (WHERE h.status='SEALED'), MAX(h.created_at), "
        "BOOL_OR(s.provider='SYNTHETIC_DEMO_ENGINEERING_EVIDENCE' OR STARTS_WITH(h.version,'demo') "
        "OR STARTS_WITH(h.version,'module1b')) "
        "FROM historical_dataset_versions h JOIN historical_data_sources s USING(source_id) "
        "GROUP BY h.source_id, s.provider, s.dataset_name "
        "ORDER BY MAX(h.created_at) DESC, h.source_id"
    )
    historical = [
        HistoricalSourceSummaryView(
            source_id=row[0], provider=str(row[1]), dataset_name=str(row[2]),
            dataset_count=int(row[3]), sealed_count=int(row[4]), latest_created_at=row[5],
            tier_ceiling=ceilings.get(row[0], NO_TIMING_AUTHORITY_V1), synthetic_marker=bool(row[6]),
        )
        for row in cursor.fetchall()
    ]

    cursor.execute("SELECT COUNT(*) FROM first_party_t4_datasets")
    t4_total = int(cursor.fetchall()[0][0])
    cursor.execute(
        "SELECT dataset_version_id, content_hash, source_id, session_id, utc_day, window_index, "
        "first_market_knowledge_at, last_market_knowledge_at, observation_count, "
        "distinct_knowledge_time_count, sealed_at, registered_at "
        "FROM first_party_t4_datasets "
        "ORDER BY utc_day DESC, start_arrival_nanos DESC, dataset_version_id LIMIT %s",
        (CATALOG_ROW_LIMIT_V1,),
    )
    t4 = [
        T4DatasetCatalogView(
            dataset_version_id=row[0], content_hash=str(row[1]).strip(), source_id=row[2],
            session_id=row[3], utc_day=row[4], window_index=int(row[5]),
            first_market_knowledge_at=row[6], last_market_knowledge_at=row[7],
            observation_count=int(row[8]), distinct_knowledge_time_count=int(row[9]),
            sealed_at=row[10], registered_at=row[11],
        )
        for row in cursor.fetchall()
    ]

    cursor.execute("SELECT COUNT(*) FROM public_archive_datasets")
    archive_total = int(cursor.fetchall()[0][0])
    cursor.execute(
        "SELECT dataset_version_id, content_hash, source_id, symbol, first_utc_day, last_utc_day, "
        "file_count, trade_count, publication_lag_slot, registered_at "
        "FROM public_archive_datasets "
        "ORDER BY registered_at DESC, dataset_version_id LIMIT %s",
        (CATALOG_ROW_LIMIT_V1,),
    )
    archive = [
        PublicArchiveDatasetCatalogView(
            dataset_version_id=row[0], content_hash=str(row[1]).strip(), source_id=row[2],
            symbol=str(row[3]), first_utc_day=row[4], last_utc_day=row[5], file_count=int(row[6]),
            trade_count=int(row[7]), publication_lag_slot=str(row[8]), registered_at=row[9],
        )
        for row in cursor.fetchall()
    ]

    cursor.execute(
        "SELECT frame_kind, COUNT(*), COALESCE(SUM(row_count),0), COALESCE(SUM(total_bytes),0) "
        "FROM research_frame_manifests GROUP BY frame_kind ORDER BY frame_kind"
    )
    frames = [
        ResearchFrameSummaryView(
            frame_kind=str(row[0]), manifest_count=int(row[1]), row_count=int(row[2]),
            total_bytes=int(row[3]),
        )
        for row in cursor.fetchall()
    ]

    has_evidence = bool(historical or t4 or archive or frames)
    timing_sources = timing_sources_v1()
    runtime_limitations = (
        [] if _public_archive_contract() is not None
        else ["The public-archive source contract is not loadable in this runtime (analytics extra absent)."]
    )
    return EvidenceCatalogView(
        state="AVAILABLE" if has_evidence else "UNAVAILABLE",
        as_of=now or datetime.now(UTC),
        timing_sources=timing_sources,
        historical_sources=historical,
        t4_dataset_total=t4_total,
        t4_datasets=t4,
        public_archive_dataset_total=archive_total,
        public_archive_datasets=archive,
        research_frames=frames,
        limitations=[
            "Tier values are source ceilings from registered timing contracts, never dataset verdicts.",
            "A catalogued T4 seal is identity only; its tier is re-derived from raw capture when used.",
            "T2 archive datasets carry no publication lag until owner decision OR-5.",
            f"Dataset lists show at most {CATALOG_ROW_LIMIT_V1} newest rows; totals count all rows.",
            *runtime_limitations,
        ],
    )


# ---------------------------------------------------------------------------
# Capture availability (recorder-host filesystem)
# ---------------------------------------------------------------------------


def _relative(root: Path, directory: Path) -> str:
    try:
        return directory.relative_to(root).as_posix()
    except ValueError:
        return directory.name


def _latest_clock_offset(root: Path, source_id: str) -> ClockOffsetSampleView | None:
    """The newest CLOCK_OFFSET_SAMPLE across this source's partitions, or ``None``.

    A partition still being written may end in a half-written line; such a
    partition is skipped rather than guessed at.
    """
    latest: tuple[int, dict[str, Any], Path] | None = None
    for directory in find_partitions_v1(root):
        if session_source_id_v1(directory) != source_id:
            continue
        try:
            events = read_lifecycle_v1(directory)
        except (OSError, ValueError):
            continue
        for event in events:
            if event.kind != _CLOCK_OFFSET_SAMPLE_KIND or event.detail is None:
                continue
            if latest is not None and event.arrival_utc_nanos <= latest[0]:
                continue
            try:
                detail = json.loads(event.detail)
                int(detail["offset_estimate_nanos"])
                int(detail["offset_bound_nanos"])
            except (ValueError, KeyError, TypeError):
                continue
            latest = (event.arrival_utc_nanos, detail, directory)
    if latest is None:
        return None
    arrival, detail, directory = latest
    return ClockOffsetSampleView(
        sampled_at=nanos_to_datetime(arrival),
        offset_estimate_seconds=int(detail["offset_estimate_nanos"]) / 1e9,
        offset_bound_seconds=int(detail["offset_bound_nanos"]) / 1e9,
        partition=_relative(root, directory),
    )


def read_capture_availability_v1(
    root: Path | None, *, now: datetime | None = None,
) -> CaptureAvailabilityView:
    """Proven capture coverage under ``root``; ``UNCONFIGURED`` when there is no root.

    The API process sees an archive only when the operator points it at one
    (the recorder writes on its own host). An unconfigured or missing root is
    reported as such -- never as "no capture happened".
    """
    as_of = now or datetime.now(UTC)
    if root is None:
        return CaptureAvailabilityView(
            state="UNCONFIGURED", as_of=as_of, disk_free_bytes=None, sources=[], unattributed=[],
            limitations=["No capture archive root is configured for this API process."],
        )
    if not root.is_dir():
        return CaptureAvailabilityView(
            state="UNAVAILABLE", as_of=as_of, disk_free_bytes=None, sources=[], unattributed=[],
            limitations=["The configured capture archive root does not exist on this host."],
        )
    production = first_party_bybit_capture_contract_v1().source_id
    per_source, unattributed = derive_archive_availability_by_source_v1(root)
    sources = []
    for item in per_source:
        availability = item.availability
        windows = [
            CaptureWindowView(
                session_id=window.session_id,
                start_at=nanos_to_datetime(window.interval.start_utc_nanos),
                last_proven_at=nanos_to_datetime(window.interval.last_proven_utc_nanos),
                end_proof=window.interval.end_proof,
                record_count=window.interval.record_count,
            )
            for window in availability.windows
        ]
        sources.append(CaptureSourceAvailabilityView(
            source_id=item.contract.source_id,
            exchange_symbol=item.contract.exchange_symbol,
            purpose="PRODUCTION" if item.contract.source_id == production else "MEASUREMENT",
            proven_record_count=sum(window.record_count for window in windows),
            proven_seconds=sum(
                (window.interval.last_proven_utc_nanos - window.interval.start_utc_nanos) / 1e9
                for window in availability.windows
            ),
            windows=windows,
            gaps=[
                CaptureGapView(
                    start_at=nanos_to_datetime(gap.start_utc_nanos),
                    end_at=None if gap.end_utc_nanos is None else nanos_to_datetime(gap.end_utc_nanos),
                    kind=gap.kind, detail=gap.detail,
                )
                for gap in availability.gaps
            ],
            not_finalized=[
                CaptureExclusionView(partition=_relative(root, directory), reasons=list(reasons))
                for directory, reasons in availability.excluded
            ],
            latest_clock_offset=_latest_clock_offset(root, str(item.contract.source_id)),
        ))
    return CaptureAvailabilityView(
        state="AVAILABLE",
        as_of=as_of,
        disk_free_bytes=disk_free_bytes_v1(root),
        sources=sources,
        unattributed=[
            CaptureExclusionView(partition=_relative(root, directory), reasons=list(reasons))
            for directory, reasons in unattributed
        ],
        limitations=[
            "Only COMPLETE partitions prove coverage; open or interrupted partitions prove nothing.",
            "Files cannot show whether a recorder process is running right now.",
            "Clock offsets are recorded evidence; no arrival time is ever adjusted by them.",
        ],
    )


__all__ = [
    "CAPTURE_AVAILABILITY_VERSION_V1",
    "CATALOG_ROW_LIMIT_V1",
    "EVIDENCE_CATALOG_VERSION_V1",
    "NO_TIMING_AUTHORITY_V1",
    "CaptureAvailabilityView",
    "EvidenceCatalogView",
    "read_capture_availability_v1",
    "read_evidence_catalog_v1",
    "timing_sources_v1",
]
