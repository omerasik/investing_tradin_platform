"""Lite admission over canonical archive day frames and research window identities.

No database, acquisition or evaluator. The existing terminal-lite-v1 sidecar
accepts its legacy single utc_day or an explicit 7..14 day window. Dataset
format, row encoding and identity derivation remain owned by the data plane.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1

LITE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
LITE_MAX_BYTES = 16 * 1024 * 1024
LITE_MAX_DAYS = 14


def window_days(first: date, last: date) -> list[date]:
    count = (last - first).days + 1
    if count < 1 or count > LITE_MAX_DAYS or 1 < count < 7:
        raise ValueError("lite_window_requires_legacy_day_or_7_to_14_days")
    if last >= UNTOUCHED_HOLDOUT_BOUNDARY_V1.date():
        raise ValueError("lite_holdout_forbidden")
    return [first + timedelta(days=i) for i in range(count)]


def report_days(report: Mapping[str, Any]) -> list[date]:
    legacy = report.get("utc_day")
    first = date.fromisoformat(report.get("first_utc_day", legacy))
    last = date.fromisoformat(report.get("last_utc_day", legacy))
    days = window_days(first, last)
    if legacy is not None and (str(first) != legacy or first != last):
        raise ValueError("lite_ambiguous_window_declaration")
    if report.get("day_count", len(days)) != len(days):
        raise ValueError("lite_window_day_count_mismatch")
    return days


def verified_day(archive: Path, store: Any, symbol: str, day: date) -> tuple[Any, Any]:
    from .bybit_public_archive_v1 import bybit_public_trade_archive_contract_v1
    from .public_archive_research_bars_v1 import (
        DERIVED,
        archive_day_status_v1,
        load_day_bars_v1,
        load_file_manifest_v1,
    )

    window_days(day, day)  # refuse before touching a holdout day
    status = archive_day_status_v1(archive, symbol, day)
    if status != DERIVED:
        raise ValueError(f"lite_day_unavailable:{symbol}:{day}:{status}")
    record = load_day_bars_v1(archive, symbol, day)
    file = load_file_manifest_v1(archive, symbol, day)
    contract = bybit_public_trade_archive_contract_v1()
    if (
        record is None
        or record.symbol != symbol
        or record.utc_day != str(day)
        or record.file_identity != file.identity()
        or file.symbol != symbol
        or file.utc_day != str(day)
        or file.source_id != str(contract.source_id)
        or file.url != contract.url(symbol, day)
    ):
        raise ValueError("lite_day_provenance_mismatch")
    manifest = store.load_manifest(record.bar_frame_manifest_hash)
    if (
        manifest.frame_kind != "T2_ARCHIVE_OHLCV_1M"
        or manifest.row_count != 1440
        or record.bar_count != 1440
        or manifest.total_bytes > LITE_MAX_BYTES
        or manifest.logical_content_hash != record.bar_logical_content_hash
        or manifest.lineage.get("source_file_sha256") != file.sha256
        or manifest.lineage.get("symbol") != symbol
        or manifest.lineage.get("utc_day") != str(day)
    ):
        raise ValueError(f"lite_day_frame_mismatch:{symbol}:{day}")
    store.verify(manifest)
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    for index, row in enumerate(store.iter_rows(manifest)):
        if (
            row[0] != start + timedelta(minutes=index)
            or row[1] != row[0] + timedelta(minutes=1)
            or row[2] is not None
        ):
            raise ValueError(f"lite_minute_grid_or_knowledge_mismatch:{symbol}:{day}")
    return record, manifest


def day_binding(record: Any, manifest: Any) -> dict[str, Any]:
    return {
        "utc_day": record.utc_day,
        "manifest_hash": manifest.manifest_hash,
        "logical_content_hash": manifest.logical_content_hash,
        "rows": manifest.row_count,
        "file_identity": dict(record.file_identity),
    }


def verify_lite_subset(root: Path) -> dict[str, Any]:
    """Re-prove every retained day and the exact concatenation, before DB startup."""
    from .bybit_public_archive_v1 import (
        ARCHIVE_BAR_SEMANTIC_VERSION_V1,
        ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1,
        bybit_public_trade_archive_contract_v1,
    )
    from .public_archive_research_bars_v1 import (
        BAR_DATASET_SCHEMA_VERSION_V1,
        load_research_bar_dataset_v1,
    )
    from .research_data_plane_v1 import (
        T2_ARCHIVE_OHLCV_1M_FRAME,
        ResearchFrameStoreV1,
        logical_content_hash_v1,
    )

    if not (root / "lite-provenance.json").is_file():
        raise ValueError("lite_verified_subset_required")
    if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > LITE_MAX_BYTES:
        raise ValueError("lite_subset_exceeds_disk_budget")
    report = json.loads((root / "lite-provenance.json").read_text(encoding="utf-8"))
    if (
        report.get("version") != "terminal-lite-v1"
        or report.get("classification") != "REAL_HISTORICAL_T2_EVENT_TIME"
        or sorted(item["symbol"] for item in report["datasets"]) != sorted(LITE_SYMBOLS)
    ):
        raise ValueError("lite_three_market_subset_required")
    days = report_days(report)
    store = ResearchFrameStoreV1(root)
    expected = {f"{item['dataset_version_id']}.json" for item in report["datasets"]}
    if {p.name for p in (store.root / "datasets/public-archive-bars").glob("*.json")} != expected:
        raise ValueError("lite_catalogue_must_match_verified_subset")
    contract = bybit_public_trade_archive_contract_v1()
    for item in report["datasets"]:
        dataset = load_research_bar_dataset_v1(store, UUID(item["dataset_version_id"]))
        identity = dataset.identity
        if (
            dataset.content_hash != item["content_hash"]
            or dataset.bar_frame_manifest_hash != item["manifest_hash"]
            or identity["symbol"] != item["symbol"]
            or identity["first_utc_day"] != str(days[0])
            or identity["last_utc_day"] != str(days[-1])
            or identity["source_id"] != str(contract.source_id)
            or identity["source_contract_content_hash"] != contract.content_hash()
            or identity["schema_version"] != BAR_DATASET_SCHEMA_VERSION_V1
            or identity["normalization_semantic_version"]
            != ARCHIVE_NORMALIZATION_SEMANTIC_VERSION_V1
            or identity["bar_semantic_version"] != ARCHIVE_BAR_SEMANTIC_VERSION_V1
            or identity["evidence_tier"] != "T2_EVENT_TIME"
            or identity["market_knowledge_at"] != "NULL_IN_FRAME_APPLIED_BY_DECLARED_TIMING_POLICY"
            or identity["bar_frame"]["row_count"] != 1440 * len(days)
            or item["rows"] != 1440 * len(days)
            or identity["not_published_days"]
            or identity.get("rejected_days")
            or identity["day_gaps"]
        ):
            raise ValueError("lite_subset_identity_or_size_mismatch")
        originals = [
            verified_day(root / "archive-provenance", store, item["symbol"], day) for day in days
        ]
        if identity["files"] != [dict(record.file_identity) for record, _ in originals]:
            raise ValueError("lite_window_archive_identities_mismatch")
        if "utc_day" in report:
            record, manifest = originals[0]
            if item["source_day_manifest_hash"] != manifest.manifest_hash or item[
                "file_identity"
            ] != dict(record.file_identity):
                raise ValueError("lite_day_binding_mismatch")
        elif item.get("day_frames") != [day_binding(r, m) for r, m in originals]:
            raise ValueError("lite_window_day_bindings_mismatch")
        digest, count = logical_content_hash_v1(
            T2_ARCHIVE_OHLCV_1M_FRAME,
            (row for _, manifest in originals for row in store.iter_rows(manifest)),
        )
        if (
            digest != identity["bar_frame"]["logical_content_hash"]
            or count != 1440 * len(days)
            or dataset.last_bar_close_at
            != datetime.combine(days[-1] + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        ):
            raise ValueError("lite_window_differs_from_verified_days")
    return report
