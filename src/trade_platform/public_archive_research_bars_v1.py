"""Phase R4.D -- T2 research bars from the free public archive, with OR-1 retention.

``RESEARCH_ONLY``. Turns verified daily archive files
(:mod:`trade_platform.bybit_public_archive_v1`) into the 1-minute event-time bar
frames Strategy Lab studies bind, without keeping every raw file on the
internal disk. It reuses the archive authority unchanged: the same strict
parse, the same :func:`~trade_platform.bybit_public_archive_v1.build_archive_bars_v1`,
the same ``T2_ARCHIVE_OHLCV_1M`` frame schema. Nothing here downloads except
through :func:`~trade_platform.bybit_public_archive_v1.acquire_archive_day_v1`.

Day bars
--------
:func:`derive_archive_day_bars_v1` re-proves one raw file against its manifest
(size, SHA-256, decompressed digest), parses it strictly, writes that day's
bar frame and re-hashes the stored frame byte for byte. The result is a day
record next to the file manifest that binds the file identity to the bar frame.

Retention (owner decision OR-1, 2026-10-08)
-------------------------------------------
* The file manifest -- canonical URL, SHA-256, size, decompressed digest, ETag,
  Last-Modified -- and the derived bar frame are kept forever.
* :func:`evict_archive_raw_v1` deletes an *exploratory* raw file only after the
  day record exists for exactly that file and its frame re-verifies. It writes
  a retention record; it never deletes a manifest.
* :func:`pin_archive_raw_v1` marks a file as underlying a frozen,
  preregistered, holdout-opened or promoted artifact. A pinned file is never
  evicted, and pinning an evicted file first restores it.
* :func:`restore_archive_raw_v1` re-downloads an evicted file into a staging
  root and accepts it only if it is byte-identical to the recorded manifest.
  A publisher that now serves different bytes makes the file -- and every
  artifact that depends on it -- ``UNVERIFIABLE``: recorded, refused, never
  silently rebuilt from changed history.

Window datasets
---------------
:func:`build_research_bar_dataset_v1` concatenates verified day frames for one
symbol and a contiguous day range into one content-addressed dataset. A day
the publisher never published is a declared gap; a day that was not acquired
or not derived is refused, never skipped. A day whose published file the
strict parse **rejected** (see :mod:`trade_platform.bybit_public_archive_v1`)
is refused by default; with ``admit_rejected_days=True`` it becomes a declared
gap listed under ``rejected_days`` with its refusal reason and file SHA-256 --
never interpolated, bridged or repaired, and the bar frame simply has no rows
for it (the SDK breaks rolling windows at a missing day). The key is absent
when no day was rejected, so windows without one keep their identity.
``require_continuous=True`` refuses any window with a gap of either kind, and
:func:`contiguous_derived_spans_v1` lists the gap-free spans that exist. The frame carries no knowledge time
(``market_knowledge_at`` is NULL): T2 knowledge time is event time plus the
owner-declared OR-5 dissemination lag, which a study applies through its
declared timing policy, so the lag sensitivity sweep never needs another frame.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .bybit_public_archive_v1 import (
    ARCHIVE_BAR_SEMANTIC_VERSION_V1,
    ARCHIVE_DAY_REJECTED,
    ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1,
    REJECTED_STRICT_PARSE,
    ArchiveFileManifestV1,
    BybitPublicArchiveError,
    FetchV1,
    _file_paths,
    acquire_archive_day_v1,
    build_archive_bars_v1,
    bybit_public_trade_archive_contract_v1,
    iter_archive_trades_v1,
    load_archive_day_rejection_v1,
    load_archive_file_manifest_v1,
    urllib_fetch_v1,
    verify_archive_file_v1,
)
from .research_data_plane_v1 import T2_ARCHIVE_OHLCV_1M_FRAME, ResearchFrameStoreV1
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, identity_hash_v1

DAY_BARS_SCHEMA_VERSION_V1: Final = "public-archive-day-bars-v1"
BAR_DATASET_SCHEMA_VERSION_V1: Final = "public-archive-bar-dataset-v1"
RETENTION_SCHEMA_VERSION_V1: Final = "public-archive-raw-retention-v1"

RETAINED: Final = "RETAINED"
EVICTED: Final = "EVICTED_AFTER_VERIFIED_DERIVATION"
PINNED: Final = "PINNED"
UNVERIFIABLE: Final = "UNVERIFIABLE_PUBLISHER_BYTES_CHANGED"

#: A day the publisher answered 404 for: a declared gap, with its evidence.
NOT_PUBLISHED: Final = "NOT_PUBLISHED"
#: A published day the strict parse refused: never derived, never repaired.
REJECTED: Final = REJECTED_STRICT_PARSE
#: A day with a verified day-bars record.
DERIVED: Final = "DERIVED"
#: Neither derived nor declared: not yet acquired (a window refuses it).
NOT_ACQUIRED: Final = "NOT_ACQUIRED"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.public_archive_research_bars_v1")


class ResearchBarsError(ValueError):
    """Raised when a derivation, eviction, restore or window cannot be proven."""


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(dict(payload), sort_keys=True, indent=1), encoding="utf-8")
    os.replace(staged, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(root: Path, symbol: str, day: date) -> dict[str, Path]:
    contract = bybit_public_trade_archive_contract_v1()
    raw, part, manifest = _file_paths(root, contract, symbol, day)
    return {
        "raw": raw, "part": part, "manifest": manifest,
        "day_bars": raw.with_name(raw.name + ".day-bars.json"),
        "retention": raw.with_name(raw.name + ".retention.json"),
        "not_published": raw.with_name(raw.name + ".not-published.json"),
    }


# ---------------------------------------------------------------------------
# Day bars
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayBarsV1:
    """One archive file's derived bar frame, bound to the file's identity."""

    symbol: str
    utc_day: str
    file_identity: Mapping[str, Any]
    bar_frame_manifest_hash: str
    bar_logical_content_hash: str
    bar_count: int

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": DAY_BARS_SCHEMA_VERSION_V1,
            "symbol": self.symbol,
            "utc_day": self.utc_day,
            "file_identity": dict(self.file_identity),
            "normalization_semantic_version": ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1,
            "bar_semantic_version": ARCHIVE_BAR_SEMANTIC_VERSION_V1,
            "bar_frame_manifest_hash": self.bar_frame_manifest_hash,
            "bar_logical_content_hash": self.bar_logical_content_hash,
            "bar_count": self.bar_count,
        }


def _day_bars_from_payload(payload: Mapping[str, Any]) -> DayBarsV1:
    if (
        payload.get("schema_version") != DAY_BARS_SCHEMA_VERSION_V1
        or payload.get("normalization_semantic_version") != ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1
        or payload.get("bar_semantic_version") != ARCHIVE_BAR_SEMANTIC_VERSION_V1
    ):
        raise ResearchBarsError("day_bars_record_from_another_derivation_version")
    return DayBarsV1(
        symbol=str(payload["symbol"]), utc_day=str(payload["utc_day"]),
        file_identity=dict(payload["file_identity"]),
        bar_frame_manifest_hash=str(payload["bar_frame_manifest_hash"]),
        bar_logical_content_hash=str(payload["bar_logical_content_hash"]),
        bar_count=int(payload["bar_count"]),
    )


def derive_archive_day_bars_v1(
    root: Path, manifest: ArchiveFileManifestV1, *, store: ResearchFrameStoreV1
) -> DayBarsV1:
    """Re-prove the raw file, derive its bar frame, re-verify the stored frame, record it."""
    paths = _paths(root, manifest.symbol, date.fromisoformat(manifest.utc_day))
    existing = load_day_bars_v1(root, manifest.symbol, date.fromisoformat(manifest.utc_day))
    if existing is not None:
        if existing.file_identity != manifest.identity():
            raise ResearchBarsError("day_bars_record_binds_a_different_file")
        store.verify(store.load_manifest(existing.bar_frame_manifest_hash))
        return existing
    path = verify_archive_file_v1(root, manifest)
    day = date.fromisoformat(manifest.utc_day)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        trades = list(iter_archive_trades_v1(handle, symbol=manifest.symbol, day=day))
    bars = build_archive_bars_v1(trades)
    if not bars:
        raise ResearchBarsError("archive_day_has_no_trades")
    contract = bybit_public_trade_archive_contract_v1()
    written = store.write_frame(
        T2_ARCHIVE_OHLCV_1M_FRAME, iter(bars),
        lineage={"source_id": str(contract.source_id), "symbol": manifest.symbol,
                 "utc_day": manifest.utc_day, "source_file_sha256": manifest.sha256,
                 "derivation": DAY_BARS_SCHEMA_VERSION_V1},
    )
    store.verify(store.load_manifest(written.manifest_hash))
    record = DayBarsV1(
        symbol=manifest.symbol, utc_day=manifest.utc_day, file_identity=manifest.identity(),
        bar_frame_manifest_hash=written.manifest_hash,
        bar_logical_content_hash=written.logical_content_hash, bar_count=written.row_count,
    )
    _write_json(paths["day_bars"], record.payload())
    return record


def load_day_bars_v1(root: Path, symbol: str, day: date) -> DayBarsV1 | None:
    path = _paths(root, symbol, day)["day_bars"]
    return _day_bars_from_payload(_read_json(path)) if path.exists() else None


def record_not_published_v1(root: Path, symbol: str, day: date, *, observed_at: datetime) -> None:
    """A 404 for this day is a declared gap: record the evidence, invent nothing."""
    paths = _paths(root, symbol, day)
    paths["raw"].parent.mkdir(parents=True, exist_ok=True)
    contract = bybit_public_trade_archive_contract_v1()
    _write_json(paths["not_published"], {
        "schema_version": RETENTION_SCHEMA_VERSION_V1, "state": NOT_PUBLISHED,
        "symbol": symbol, "utc_day": day.isoformat(), "url": contract.url(symbol, day),
        "http_status": 404, "observed_at": observed_at.astimezone(UTC).isoformat(),
    })


def is_not_published_v1(root: Path, symbol: str, day: date) -> bool:
    return _paths(root, symbol, day)["not_published"].exists()


def archive_day_status_v1(root: Path, symbol: str, day: date, *, holdout_opening: Any = None) -> str:
    """DERIVED, NOT_PUBLISHED, REJECTED or NOT_ACQUIRED. Contradictory evidence fails closed."""
    _refuse_holdout(day, holdout_opening)
    states = [
        state for state, present in (
            (DERIVED, _paths(root, symbol, day)["day_bars"].exists()),
            (NOT_PUBLISHED, is_not_published_v1(root, symbol, day)),
            (REJECTED, load_archive_day_rejection_v1(root, symbol, day) is not None),
        ) if present
    ]
    if len(states) > 1:
        raise ResearchBarsError(f"archive_day_has_contradictory_records:{symbol}:{day.isoformat()}:{','.join(states)}")
    return states[0] if states else NOT_ACQUIRED


def contiguous_derived_spans_v1(root: Path, symbol: str, first: date, last: date) -> list[dict[str, Any]]:
    """Every maximal run of consecutive DERIVED days in ``[first, last]`` (pre-holdout only).

    Reads day records only, never bar rows. The caller picks a span; nothing
    here chooses one or joins spans across a gap.
    """
    spans: list[dict[str, Any]] = []
    run: list[date] = []
    for day in [*_days(first, last), None]:
        if day is not None and archive_day_status_v1(root, symbol, day) == DERIVED:
            run.append(day)
            continue
        if run:
            spans.append({"first_utc_day": run[0].isoformat(), "last_utc_day": run[-1].isoformat(),
                          "days": len(run)})
            run = []
    return spans


# ---------------------------------------------------------------------------
# Retention (OR-1)
# ---------------------------------------------------------------------------


def retention_state_v1(root: Path, symbol: str, day: date) -> str:
    path = _paths(root, symbol, day)["retention"]
    if path.exists():
        return str(_read_json(path)["state"])
    return RETAINED


def _record_retention(paths: Mapping[str, Path], manifest: ArchiveFileManifestV1, state: str,
                      *, at: datetime, detail: Mapping[str, Any] | None = None) -> None:
    _write_json(paths["retention"], {
        "schema_version": RETENTION_SCHEMA_VERSION_V1, "state": state,
        "file_identity": manifest.identity(), "url": manifest.url,
        "http_etag": manifest.http_etag, "http_last_modified": manifest.http_last_modified,
        "recorded_at": at.astimezone(UTC).isoformat(), "detail": dict(detail or {}),
    })


def evict_archive_raw_v1(
    root: Path, manifest: ArchiveFileManifestV1, *, store: ResearchFrameStoreV1,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """Delete an exploratory raw file after a verified derivation. ``False`` if kept.

    A pinned or unverifiable file is never evicted. The day record must bind
    exactly this file and its frame must re-verify; the raw file must still
    match its manifest (so a corrupted file is never "evicted" as if it were
    the published bytes).
    """
    day = date.fromisoformat(manifest.utc_day)
    paths = _paths(root, manifest.symbol, day)
    state = retention_state_v1(root, manifest.symbol, day)
    if state in {PINNED, UNVERIFIABLE}:
        return False
    if state == EVICTED:
        return True
    record = load_day_bars_v1(root, manifest.symbol, day)
    if record is None or record.file_identity != manifest.identity():
        raise ResearchBarsError("eviction_requires_a_verified_derivation_of_this_file")
    store.verify(store.load_manifest(record.bar_frame_manifest_hash))
    verify_archive_file_v1(root, manifest)
    _record_retention(paths, manifest, EVICTED, at=now(),
                      detail={"bar_frame_manifest_hash": record.bar_frame_manifest_hash})
    paths["raw"].unlink()
    return True


def restore_archive_raw_v1(
    root: Path, manifest: ArchiveFileManifestV1, *, fetch: FetchV1 = urllib_fetch_v1,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Bring an evicted file back, byte-identical to its manifest, or fail closed.

    The refetch lands in a staging root through the unchanged acquisition path;
    it is moved into place only if its identity equals the recorded one. A
    different identity marks the file ``UNVERIFIABLE`` and raises.
    """
    day = date.fromisoformat(manifest.utc_day)
    paths = _paths(root, manifest.symbol, day)
    if paths["raw"].exists():
        return verify_archive_file_v1(root, manifest)
    if retention_state_v1(root, manifest.symbol, day) == UNVERIFIABLE:
        raise ResearchBarsError("archive_file_is_unverifiable")
    staging = Path(tempfile.mkdtemp(prefix="archive-restore-", dir=paths["raw"].parent))
    try:
        try:
            fetched = acquire_archive_day_v1(staging, manifest.symbol, day, fetch=fetch, now=now)
        except BybitPublicArchiveError as error:
            refused = load_archive_day_rejection_v1(staging, manifest.symbol, day)
            if str(error) == ARCHIVE_DAY_REJECTED and refused is not None:
                # The publisher now serves bytes the strict parse refuses: history changed.
                _record_retention(paths, manifest, UNVERIFIABLE, at=now(), detail={"refetch_rejected": refused})
                raise ResearchBarsError("archive_publisher_bytes_changed_artifact_unverifiable") from error
            # A fetch that cannot even be proven is not a changed history: refuse, record nothing.
            raise ResearchBarsError(f"archive_restore_failed:{error}") from error
        if fetched.identity() != manifest.identity():
            _record_retention(paths, manifest, UNVERIFIABLE, at=now(), detail={
                "refetched_identity": fetched.identity(),
                "refetched_etag": fetched.http_etag,
                "refetched_last_modified": fetched.http_last_modified,
            })
            raise ResearchBarsError("archive_publisher_bytes_changed_artifact_unverifiable")
        staged_raw, _, _ = _file_paths(staging, bybit_public_trade_archive_contract_v1(), manifest.symbol, day)
        os.replace(staged_raw, paths["raw"])
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    path = verify_archive_file_v1(root, manifest)
    if retention_state_v1(root, manifest.symbol, day) == EVICTED:
        paths["retention"].unlink()
    return path


def pin_archive_raw_v1(
    root: Path, manifest: ArchiveFileManifestV1, *, artifact: str, fetch: FetchV1 = urllib_fetch_v1,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Keep this file recoverable for ``artifact``; restores it first if it was evicted."""
    if not artifact:
        raise ResearchBarsError("pin_requires_an_artifact_reference")
    day = date.fromisoformat(manifest.utc_day)
    paths = _paths(root, manifest.symbol, day)
    restore_archive_raw_v1(root, manifest, fetch=fetch, now=now)
    artifacts: list[str] = []
    if retention_state_v1(root, manifest.symbol, day) == PINNED:
        artifacts = list(_read_json(paths["retention"])["detail"].get("artifacts", []))
    _record_retention(paths, manifest, PINNED, at=now(),
                      detail={"artifacts": sorted({*artifacts, artifact})})


# ---------------------------------------------------------------------------
# Window datasets
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchBarDatasetV1:
    """A content-addressed T2 bar window for one symbol. Knowledge time not in the frame."""

    identity: Mapping[str, Any]
    content_hash: str
    dataset_version_id: UUID
    bar_frame_manifest_hash: str

    @property
    def symbol(self) -> str:
        return str(self.identity["symbol"])

    @property
    def last_bar_close_at(self) -> datetime:
        return datetime.fromisoformat(str(self.identity["last_bar_close_at"]))

    def knowledge_upper_bound_exclusive(self, max_lag: timedelta) -> datetime:
        """The latest knowledge instant this window can supply under ``max_lag``, exclusive."""
        return self.last_bar_close_at + max_lag + timedelta(microseconds=1)


def _refuse_holdout(day: date, opening: Any = None) -> None:
    """Refuse a day inside the untouched holdout unless an R6 opening admits it.

    The span is opened once per cycle by an authorized preregistration
    (:class:`~trade_platform.strategy_lab_validation_v1.HoldoutOpeningV1`, which
    only the registry can issue); nothing else can unlock it.
    """
    if day < UNTOUCHED_HOLDOUT_BOUNDARY_V1.date():
        return
    if opening is not None:
        from .strategy_lab_validation_v1 import HoldoutOpeningV1

        if isinstance(opening, HoldoutOpeningV1) and opening.admits_day(day):
            return
    raise ResearchBarsError("research_bars_refuse_the_untouched_holdout")


def _days(first: date, last: date, opening: Any = None) -> list[date]:
    if last < first:
        raise ResearchBarsError("window_last_day_before_first_day")
    if first < UNTOUCHED_HOLDOUT_BOUNDARY_V1.date() <= last:
        # A window never straddles the boundary: search data and holdout data stay apart.
        raise ResearchBarsError("window_straddles_the_untouched_holdout_boundary")
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    for day in days:  # every day, not only the last: an opening admits only its own span
        _refuse_holdout(day, opening)
    return days


def build_research_bar_dataset_v1(
    root: Path, symbol: str, first: date, last: date, *, store: ResearchFrameStoreV1,
    holdout_opening: Any = None, require_continuous: bool = False, admit_rejected_days: bool = False,
) -> ResearchBarDatasetV1:
    """Concatenate verified day frames into one window dataset; gaps declared, never filled.

    A rejected day refuses the window unless ``admit_rejected_days``; then it is
    a declared gap. ``require_continuous`` refuses any gap at all.
    """
    contract = bybit_public_trade_archive_contract_v1()
    records: list[DayBarsV1] = []
    gaps: list[str] = []
    rejected: list[dict[str, Any]] = []
    for day in _days(first, last, holdout_opening):
        status = archive_day_status_v1(root, symbol, day, holdout_opening=holdout_opening)
        record = load_day_bars_v1(root, symbol, day) if status == DERIVED else None
        rejection = load_archive_day_rejection_v1(root, symbol, day) if status == REJECTED else None
        if record is not None:
            records.append(record)
        elif status == NOT_PUBLISHED:
            gaps.append(day.isoformat())
        elif rejection is not None:
            if not admit_rejected_days:
                raise ResearchBarsError(
                    f"window_requires_a_rejected_day:{symbol}:{day.isoformat()}:{rejection['reason']}")
            rejected.append({"utc_day": day.isoformat(), "reason": rejection["reason"],
                             "file_sha256": rejection["file_identity"]["sha256"]})
        else:
            raise ResearchBarsError(f"window_day_not_derived:{symbol}:{day.isoformat()}")
    if require_continuous and (gaps or rejected):
        missing = sorted([*gaps, *(item["utc_day"] for item in rejected)])
        raise ResearchBarsError(f"window_not_continuous:{symbol}:{','.join(missing)}")
    if not records:
        raise ResearchBarsError("window_has_no_published_day")
    manifests = []
    for record in records:
        manifest = store.load_manifest(record.bar_frame_manifest_hash)
        store.verify(manifest)
        if manifest.logical_content_hash != record.bar_logical_content_hash:
            raise ResearchBarsError("day_frame_differs_from_its_record")
        manifests.append(manifest)

    def rows() -> Iterator[tuple[object, ...]]:
        for manifest in manifests:
            yield from store.iter_rows(manifest)

    last_close: list[Any] = []

    def tracked() -> Iterator[tuple[object, ...]]:
        for row in rows():
            last_close[:] = [row[1]]
            yield row

    written = store.write_frame(
        T2_ARCHIVE_OHLCV_1M_FRAME, tracked(),
        lineage={"source_id": str(contract.source_id), "symbol": symbol,
                 "window": f"{first.isoformat()}..{last.isoformat()}",
                 "derivation": BAR_DATASET_SCHEMA_VERSION_V1},
    )
    store.verify(store.load_manifest(written.manifest_hash))
    identity = {
        "schema_version": BAR_DATASET_SCHEMA_VERSION_V1,
        "source_contract_content_hash": contract.content_hash(),
        "source_id": str(contract.source_id),
        "symbol": symbol,
        "evidence_tier": "T2_EVENT_TIME",
        "market_knowledge_at": "NULL_IN_FRAME_APPLIED_BY_DECLARED_TIMING_POLICY",
        "normalization_semantic_version": ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1,
        "bar_semantic_version": ARCHIVE_BAR_SEMANTIC_VERSION_V1,
        "first_utc_day": first.isoformat(),
        "last_utc_day": last.isoformat(),
        "files": [dict(record.file_identity) for record in records],
        "not_published_days": gaps,
        "day_gaps": [
            {"after": a.utc_day, "before": b.utc_day}
            for a, b in pairwise(records)
            if (date.fromisoformat(b.utc_day) - date.fromisoformat(a.utc_day)).days != 1
        ],
        "bar_frame": {"logical_content_hash": written.logical_content_hash, "row_count": written.row_count},
        "last_bar_close_at": last_close[0].astimezone(UTC).isoformat(),
    }
    if rejected:  # absent when none, so every window without a rejected day keeps its identity
        identity["rejected_days"] = rejected
    content_hash = identity_hash_v1(identity)
    dataset = ResearchBarDatasetV1(
        identity=identity, content_hash=content_hash,
        dataset_version_id=uuid5(_NAMESPACE, f"public-archive-bar-dataset:{content_hash}"),
        bar_frame_manifest_hash=written.manifest_hash,
    )
    _write_json(_dataset_path(store, dataset.dataset_version_id), {
        "identity": identity, "content_hash": content_hash,
        "dataset_version_id": str(dataset.dataset_version_id),
        "bar_frame_manifest_hash": written.manifest_hash,
    })
    return dataset


def _dataset_path(store: ResearchFrameStoreV1, dataset_version_id: UUID) -> Path:
    directory = store.root / "datasets" / "public-archive-bars"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{dataset_version_id}.json"


def load_research_bar_dataset_v1(store: ResearchFrameStoreV1, dataset_version_id: UUID) -> ResearchBarDatasetV1:
    """A catalogued window, re-proven: identity hash, id and the bar frame bytes."""
    path = _dataset_path(store, dataset_version_id)
    if not path.exists():
        raise ResearchBarsError("research_bar_dataset_not_found")
    payload = _read_json(path)
    identity = payload["identity"]
    content_hash = identity_hash_v1(identity)
    dataset = ResearchBarDatasetV1(
        identity=identity, content_hash=content_hash,
        dataset_version_id=uuid5(_NAMESPACE, f"public-archive-bar-dataset:{content_hash}"),
        bar_frame_manifest_hash=str(payload["bar_frame_manifest_hash"]),
    )
    if dataset.dataset_version_id != dataset_version_id or payload.get("content_hash") != content_hash:
        raise ResearchBarsError("research_bar_dataset_identity_mismatch")
    manifest = store.load_manifest(dataset.bar_frame_manifest_hash)
    store.verify(manifest)
    if manifest.logical_content_hash != identity["bar_frame"]["logical_content_hash"]:
        raise ResearchBarsError("research_bar_dataset_frame_mismatch")
    return dataset


def list_research_bar_datasets_v1(store: ResearchFrameStoreV1) -> list[dict[str, Any]]:
    directory = store.root / "datasets" / "public-archive-bars"
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        payload = _read_json(path)
        identity = payload["identity"]
        out.append({"dataset_version_id": payload["dataset_version_id"], "symbol": identity["symbol"],
                    "first_utc_day": identity["first_utc_day"], "last_utc_day": identity["last_utc_day"],
                    "bars": identity["bar_frame"]["row_count"], "files": len(identity["files"]),
                    "not_published_days": len(identity["not_published_days"]),
                    "rejected_days": len(identity.get("rejected_days", []))})
    return out


# ---------------------------------------------------------------------------
# One day, end to end (the background acquisition step)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayStepV1:
    symbol: str
    utc_day: str
    state: str
    bars: int
    evicted: bool


def acquire_and_derive_day_v1(
    root: Path, symbol: str, day: date, *, store: ResearchFrameStoreV1, evict: bool,
    fetch: FetchV1 = urllib_fetch_v1, now: Callable[[], datetime] = lambda: datetime.now(UTC),
    holdout_opening: Any = None,
) -> DayStepV1:
    """Acquire (resumable), derive, and optionally evict one day. Idempotent.

    A rejected day returns ``REJECTED`` (from its record, without any download)
    so a long acquisition moves on to the next day; it is never derived.
    """
    _refuse_holdout(day, holdout_opening)
    status = archive_day_status_v1(root, symbol, day, holdout_opening=holdout_opening)
    if status in (NOT_PUBLISHED, REJECTED):
        return DayStepV1(symbol, day.isoformat(), status, 0, False)
    record = load_day_bars_v1(root, symbol, day)
    paths = _paths(root, symbol, day)
    if record is not None and not paths["raw"].exists():
        # Already derived and evicted (or pinned elsewhere): the frame still proves itself.
        store.verify(store.load_manifest(record.bar_frame_manifest_hash))
        return DayStepV1(symbol, day.isoformat(), retention_state_v1(root, symbol, day), record.bar_count, False)
    try:
        manifest = acquire_archive_day_v1(root, symbol, day, fetch=fetch, now=now)
    except BybitPublicArchiveError as error:
        if str(error) == "archive_day_not_published":
            record_not_published_v1(root, symbol, day, observed_at=now())
            return DayStepV1(symbol, day.isoformat(), NOT_PUBLISHED, 0, False)
        if str(error) == ARCHIVE_DAY_REJECTED:
            return DayStepV1(symbol, day.isoformat(), REJECTED, 0, False)
        raise
    record = derive_archive_day_bars_v1(root, manifest, store=store)
    evicted = evict and evict_archive_raw_v1(root, manifest, store=store, now=now)
    return DayStepV1(symbol, day.isoformat(), retention_state_v1(root, symbol, day), record.bar_count, evicted)


def load_file_manifest_v1(root: Path, symbol: str, day: date) -> ArchiveFileManifestV1:
    path = _paths(root, symbol, day)["manifest"]
    if not path.exists():
        raise ResearchBarsError("archive_file_manifest_not_found")
    return load_archive_file_manifest_v1(path)


def dataset_file_manifests_v1(root: Path, dataset: ResearchBarDatasetV1) -> Sequence[ArchiveFileManifestV1]:
    """The file manifests a window binds, each checked against the window identity."""
    out = []
    for item in dataset.identity["files"]:
        manifest = load_file_manifest_v1(root, str(item["symbol"]), date.fromisoformat(str(item["utc_day"])))
        if manifest.identity() != item:
            raise ResearchBarsError("window_file_manifest_differs_from_identity")
        out.append(manifest)
    return out


__all__ = [
    "BAR_DATASET_SCHEMA_VERSION_V1",
    "DAY_BARS_SCHEMA_VERSION_V1",
    "DERIVED",
    "EVICTED",
    "NOT_ACQUIRED",
    "NOT_PUBLISHED",
    "PINNED",
    "REJECTED",
    "RETAINED",
    "UNVERIFIABLE",
    "DayBarsV1",
    "DayStepV1",
    "ResearchBarDatasetV1",
    "ResearchBarsError",
    "acquire_and_derive_day_v1",
    "archive_day_status_v1",
    "build_research_bar_dataset_v1",
    "contiguous_derived_spans_v1",
    "dataset_file_manifests_v1",
    "derive_archive_day_bars_v1",
    "evict_archive_raw_v1",
    "is_not_published_v1",
    "list_research_bar_datasets_v1",
    "load_day_bars_v1",
    "load_file_manifest_v1",
    "load_research_bar_dataset_v1",
    "pin_archive_raw_v1",
    "record_not_published_v1",
    "restore_archive_raw_v1",
    "retention_state_v1",
]
