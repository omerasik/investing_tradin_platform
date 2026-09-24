"""Operator entry point for the Phase R2B columnar research data plane.

Research only: reads sealed datasets and Feature Authority rows from PostgreSQL,
writes content-addressed Parquet objects outside the repository (default
``~/.trade_platform/research-data``) and catalog rows in PostgreSQL. No provider
call, no economic assumption, no evidence-tier change.

Export a sealed dataset, reconcile it with PostgreSQL, build the basis feature
frame and prove parity with the persisted feature values::

    .venv\\Scripts\\python scripts/research_data_plane.py export <dataset-version-id> \\
        --dsn postgresql://... [--basis-feature-id <uuid>] [--register]

Re-verify a stored frame byte-for-byte and row-for-row::

    .venv\\Scripts\\python scripts/research_data_plane.py verify <manifest-hash>

Measure the streaming write path at scale on synthetic rows (no database)::

    .venv\\Scripts\\python scripts/research_data_plane.py bench --rows 5000000
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.research_data_plane_v1 import (
    REFERENCE_PRICE_FRAME,
    ResearchFrameStoreV1,
)


def peak_memory_bytes() -> int | None:
    """Peak resident memory of this process (Windows working set, POSIX maxrss)."""
    if os.name == "nt":
        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        query = kernel32.K32GetProcessMemoryInfo
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Counters), ctypes.c_ulong]
        query.restype = ctypes.c_int
        if query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
        return None
    import resource

    usage = getattr(resource, "getrusage")(getattr(resource, "RUSAGE_SELF"))  # noqa: B009 - POSIX only
    return int(usage.ru_maxrss) * 1024


def _mib(value: int | None) -> str:
    return "n/a" if value is None else f"{value / 2**20:.1f} MiB"


def _export(args: argparse.Namespace) -> int:
    from trade_platform.crypto_instruments import (
        CryptoInstrumentError,
        PostgresCryptoInstrumentAuthority,
        ReferencePriceRequirement,
    )
    from trade_platform.persistence import PostgresDatabase
    from trade_platform.research_data_export_v1 import (
        PostgresResearchFrameCatalogV1,
        build_mark_index_basis_frame_v1,
        compare_feature_frame_with_authority_v1,
        export_dataset_frames_v1,
        reconcile_frame_with_source_v1,
    )

    database = PostgresDatabase(args.dsn)
    store = ResearchFrameStoreV1(Path(args.root) if args.root else None)
    dataset_version_id = UUID(args.dataset)
    report: dict[str, object] = {"dataset_version_id": str(dataset_version_id)}

    started = time.perf_counter()
    manifests = export_dataset_frames_v1(database, store, dataset_version_id)
    export_seconds = time.perf_counter() - started
    rows = sum(item.row_count for item in manifests.values())
    report["export"] = {
        "seconds": round(export_seconds, 2),
        "rows": rows,
        "rows_per_second": round(rows / export_seconds) if export_seconds else None,
        "frames": {
            kind: {
                "manifest_hash": item.manifest_hash,
                "logical_content_hash": item.logical_content_hash,
                "rows": item.row_count,
                "parquet_bytes": item.total_bytes,
                "bytes_per_row": round(item.total_bytes / item.row_count, 2),
                "objects": [o.sha256 for o in item.objects],
            }
            for kind, item in manifests.items()
        },
        "source_granted_evidence_tier": next(iter(manifests.values())).lineage.get(
            "source_granted_evidence_tier"
        ),
    }

    started = time.perf_counter()
    reconciliations = {
        kind: reconcile_frame_with_source_v1(database, store, item)
        for kind, item in manifests.items()
    }
    report["reconciliation"] = {
        "seconds": round(time.perf_counter() - started, 2),
        **{
            kind: {"reconciled": r.reconciled, "source_rows": r.source_rows,
                   "frame_rows": r.frame_rows, "digest": r.frame_digest}
            for kind, r in reconciliations.items()
        },
    }

    started = time.perf_counter()
    for item in manifests.values():
        store.verify(item)
    report["verify_seconds"] = round(time.perf_counter() - started, 2)

    if REFERENCE_PRICE_FRAME.kind in manifests and args.basis_feature_id:
        crypto = PostgresCryptoInstrumentAuthority(database)

        def eligible(instrument_id: str) -> bool:
            try:
                specification = crypto.get_specification(instrument_id, known_at=datetime.now(UTC))
            except CryptoInstrumentError:
                return False
            return specification.reference_price_requirement is ReferencePriceRequirement.MARK_AND_INDEX

        started = time.perf_counter()
        frame, cache_hit = build_mark_index_basis_frame_v1(
            store, manifests[REFERENCE_PRICE_FRAME.kind], semantic_version="1.0.0",
            calculation_version="derivatives-crypto-mark-index-basis-3j1c-v1",
            eligible_instrument=eligible,
        )
        build_seconds = time.perf_counter() - started
        parity = compare_feature_frame_with_authority_v1(
            database, store, frame, feature_id=UUID(args.basis_feature_id),
            dataset_version=str(dataset_version_id),
        )
        report["basis_feature_frame"] = {
            "manifest_hash": frame.manifest_hash,
            "logical_content_hash": frame.logical_content_hash,
            "cache_key": frame.cache_key,
            "cache_hit": cache_hit,
            "build_seconds": round(build_seconds, 2),
            "rows": frame.row_count,
            "parquet_bytes": frame.total_bytes,
            "parity_equal": parity.equal,
            "authority_rows": parity.authority_rows,
            "mismatches": parity.mismatches,
            "first_mismatch": parity.first_mismatch,
        }
        manifests["FEATURE"] = frame

    if args.register:
        catalog = PostgresResearchFrameCatalogV1(database)
        for item in manifests.values():
            catalog.register(item)
        report["registered"] = sorted(item.manifest_hash for item in manifests.values())

    report["peak_memory"] = _mib(peak_memory_bytes())
    print(json.dumps(report, indent=2, default=str))
    ok = all(r.reconciled for r in reconciliations.values()) and report.get(
        "basis_feature_frame", {"parity_equal": True}
    )["parity_equal"]  # type: ignore[index]
    return 0 if ok else 1


def _verify(args: argparse.Namespace) -> int:
    store = ResearchFrameStoreV1(Path(args.root) if args.root else None)
    manifest = store.load_manifest(args.manifest_hash)
    store.verify(manifest)
    print(f"verified {manifest.frame_kind} rows={manifest.row_count} "
          f"logical={manifest.logical_content_hash}")
    return 0


def _bench(args: argparse.Namespace) -> int:
    """Stream synthetic reference-price rows through the real write path."""
    store = ResearchFrameStoreV1(Path(args.root) if args.root else None)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    minute = timedelta(minutes=1)
    count = args.rows

    def rows():
        # Already in the frame's sort order: instrument, kind, event.
        for offset, kind in enumerate(("INDEX_PRICE", "MARK_PRICE")):
            for i in range(count // 2):
                event = start + minute * i
                n = 2 * i + offset
                price = Decimal(60000 + (n * 7919) % 5000) + Decimal(n % 997).scaleb(-3)
                yield ("BENCH:SYNTHETIC", kind, "BENCH", 0, f"n{n:012d}", f"r{n:012d}",
                       event, event, None, event, event, price, "USDT")

    started = time.perf_counter()
    manifest = store.write_frame(
        REFERENCE_PRICE_FRAME, rows(),
        lineage={"synthetic_benchmark": True, "rows": count},
        rows_per_object=args.rows_per_object,
    )
    write_seconds = time.perf_counter() - started
    started = time.perf_counter()
    store.verify(manifest)
    verify_seconds = time.perf_counter() - started
    print(json.dumps({
        "rows": count,
        "write_seconds": round(write_seconds, 2),
        "rows_per_second": round(count / write_seconds),
        "verify_seconds": round(verify_seconds, 2),
        "parquet_bytes": manifest.total_bytes,
        "bytes_per_row": round(manifest.total_bytes / count, 2),
        "objects": len(manifest.objects),
        "peak_memory": _mib(peak_memory_bytes()),
        "manifest_hash": manifest.manifest_hash,
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help="research data root (default ~/.trade_platform/research-data)")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("dataset")
    export.add_argument("--dsn", default=os.environ.get("RESEARCH_DATABASE_DSN"), required=False)
    export.add_argument("--basis-feature-id")
    export.add_argument("--register", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("manifest_hash")
    bench = sub.add_parser("bench")
    bench.add_argument("--rows", type=int, default=2_000_000)
    bench.add_argument("--rows-per-object", type=int, default=None)
    args = parser.parse_args(argv)
    if args.command == "export":
        if not args.dsn:
            parser.error("--dsn or RESEARCH_DATABASE_DSN is required")
        return _export(args)
    if args.command == "verify":
        return _verify(args)
    return _bench(args)


if __name__ == "__main__":
    raise SystemExit(main())
