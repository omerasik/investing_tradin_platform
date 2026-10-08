"""Phase R3B operator entry point: Bybit's free public trade archive.

    python scripts/bybit_public_archive.py acquire --symbol BTCUSDT --from 2026-09-21 --to 2026-09-23
    python scripts/bybit_public_archive.py build --symbol BTCUSDT --from ... --to ... [--dsn ...]
    python scripts/bybit_public_archive.py crosscheck --symbol BTCUSDT --from ... --to ...
    python scripts/bybit_public_archive.py overlap --symbol BTCUSDT --from D --to D --dsn ... --t4-dataset <id>
    python scripts/bybit_public_archive.py research-bars --symbol ETHUSDT --from 2025-08-20 --to 2026-08-19
    python scripts/bybit_public_archive.py window --symbol ETHUSDT --from 2025-08-20 --to 2026-08-18

Downloads only from https://public.bybit.com/trading/ and, for ``crosscheck``,
the public REST kline endpoint (unauthenticated public market data), resumably,
into ``~/.trade_platform/public-archive``. ``crosscheck`` reports disagreements
between the archive-reconstructed bars and the REST klines; it bridges nothing. Reports
engineering counts only -- never prices, returns or P&L. The T2 publication
lag stays UNSET (owner decision OR-5): every frame's market_knowledge_at is NULL.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.bybit_public_archive_v1 import (
    PostgresPublicArchiveCatalogV1,
    acquire_archive_day_v1,
    build_archive_dataset_v1,
    bybit_public_trade_archive_contract_v1,
    load_archive_file_manifest_v1,
)
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1


def _days(first: str, last: str) -> list[date]:
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _overlap(args: argparse.Namespace, manifests: list[Any]) -> None:
    """R3B.3: join verified archive days with one raw-replayed sealed T4 segment's trades."""
    from trade_platform.bybit_archive_t4_overlap_v1 import (
        compare_archive_with_t4_v1,
        t4_trades_from_frame_rows_v1,
    )
    from trade_platform.bybit_public_archive_v1 import (
        iter_archive_trades_v1,
        verify_archive_file_v1,
    )
    from trade_platform.first_party_capture_archive_v1 import default_archive_root
    from trade_platform.first_party_t4_dataset_v1 import (
        PostgresFirstPartyT4CatalogV1,
        t4_seal_from_catalog_v1,
    )
    from trade_platform.first_party_t4_seal_v1 import iter_frame_rows_v1
    from trade_platform.persistence import PostgresDatabase
    from trade_platform.research_data_plane_v1 import T4_TRADE_FRAME

    if not (args.dsn and args.t4_dataset):
        raise SystemExit("overlap needs --dsn and --t4-dataset")
    store = ResearchFrameStoreV1(args.data_root)
    seal = t4_seal_from_catalog_v1(
        PostgresFirstPartyT4CatalogV1(PostgresDatabase(args.dsn)).load(UUID(args.t4_dataset)),
        store=store, capture_root=args.capture_root or default_archive_root(),
    )
    t4 = t4_trades_from_frame_rows_v1(iter_frame_rows_v1(seal, T4_TRADE_FRAME.kind, store))

    def archive_trades() -> Iterator[Any]:
        for manifest in manifests:
            path = verify_archive_file_v1(args.root, manifest)
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                yield from iter_archive_trades_v1(handle, symbol=args.symbol, day=date.fromisoformat(manifest.utc_day))

    report = compare_archive_with_t4_v1(
        parent_seal_content_hash=seal.content_hash, t4_trades=t4, archive_trades=archive_trades(),
        archive_files={m.file_name: {"utc_day": m.utc_day, "sha256": m.sha256,
                                     "http_last_modified": m.http_last_modified or ""} for m in manifests},
    )
    print(json.dumps({"content_hash": report.content_hash, **report.identity}, indent=1, default=str))


def _research_bars(args: argparse.Namespace, days: list[date]) -> None:
    """Newest day first: acquire (resumable), derive day bars, evict raw unless --keep-raw.

    Every day is idempotent, so a killed run resumes where it stopped. A
    transient fetch failure is retried a few times, then the run stops loudly:
    a day is never skipped silently.
    """
    import time

    from trade_platform.public_archive_research_bars_v1 import acquire_and_derive_day_v1

    store = ResearchFrameStoreV1(args.data_root)
    for day in sorted(days, reverse=True):
        for attempt in range(4):
            try:
                step = acquire_and_derive_day_v1(args.root, args.symbol, day, store=store, evict=not args.keep_raw)
                break
            except (OSError, TimeoutError) as error:  # network: bounded retry, then stop
                if attempt == 3:
                    raise
                print(json.dumps({"utc_day": day.isoformat(), "retry": attempt + 1, "error": str(error)}), flush=True)
                time.sleep(30 * (attempt + 1))
        print(json.dumps({"utc_day": step.utc_day, "state": step.state, "bars": step.bars,
                          "evicted": step.evicted}), flush=True)


def _window(args: argparse.Namespace) -> None:
    from trade_platform.public_archive_research_bars_v1 import build_research_bar_dataset_v1

    dataset = build_research_bar_dataset_v1(
        args.root, args.symbol, date.fromisoformat(args.first), date.fromisoformat(args.last),
        store=ResearchFrameStoreV1(args.data_root),
    )
    print(json.dumps({"dataset_version_id": str(dataset.dataset_version_id), "content_hash": dataset.content_hash,
                      "bars": dataset.identity["bar_frame"]["row_count"], "files": len(dataset.identity["files"]),
                      "not_published_days": dataset.identity["not_published_days"],
                      "day_gaps": dataset.identity["day_gaps"],
                      "last_bar_close_at": dataset.identity["last_bar_close_at"]}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("acquire", "build", "crosscheck", "overlap", "research-bars", "window"))
    parser.add_argument("--keep-raw", action="store_true", help="research-bars: do not evict raw files (OR-1)")
    parser.add_argument("--t4-dataset", help="overlap: sealed T4 dataset_version_id")
    parser.add_argument("--capture-root", type=Path, default=None, help="overlap: first-party capture root")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="first", required=True)
    parser.add_argument("--to", dest="last", required=True)
    parser.add_argument("--root", type=Path, default=Path.home() / ".trade_platform" / "public-archive")
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--dsn")
    args = parser.parse_args()
    contract = bybit_public_trade_archive_contract_v1()
    days = _days(args.first, args.last)
    if args.command == "research-bars":
        _research_bars(args, days)
        return
    if args.command == "window":
        _window(args)
        return
    if args.command == "acquire":
        for day in days:
            manifest = acquire_archive_day_v1(args.root, args.symbol, day)
            print(json.dumps({"utc_day": manifest.utc_day, "bytes": manifest.bytes,
                              "sha256": manifest.sha256, "last_modified": manifest.http_last_modified}))
        return
    directory = args.root / "v1" / f"source={contract.source_id}" / f"symbol={args.symbol}"
    manifests = [
        load_archive_file_manifest_v1(directory / f"{args.symbol}{day.isoformat()}.csv.gz.manifest.json")
        for day in days
    ]
    if args.command == "overlap":
        _overlap(args, manifests)
        return
    if args.command == "crosscheck":
        from trade_platform.bybit_archive_rest_crosscheck_v1 import (
            acquire_rest_klines_day_v1,
            build_crosscheck_report_v1,
            write_crosscheck_report_v1,
        )

        rest_days = [acquire_rest_klines_day_v1(args.root, args.symbol, day) for day in days]
        report = build_crosscheck_report_v1(args.root, manifests, rest_days)
        path = write_crosscheck_report_v1(args.root, report)
        print(json.dumps({"content_hash": report.content_hash, "report": str(path),
                          "totals": report.identity["totals"],
                          "rpi_trades": sum(d["archive_rpi_trade_count"] for d in report.identity["days"]),
                          "trades": sum(d["archive_trade_count"] for d in report.identity["days"])},
                         indent=1))
        return
    dataset = build_archive_dataset_v1(args.root, manifests, store=ResearchFrameStoreV1(args.data_root))
    if args.dsn:
        from trade_platform.persistence import PostgresDatabase

        PostgresPublicArchiveCatalogV1(PostgresDatabase(args.dsn)).register(dataset)
    print(json.dumps({"dataset_version_id": str(dataset.dataset_version_id),
                      "content_hash": dataset.content_hash,
                      "source_id": str(contract.source_id),
                      "frames": dataset.identity["frames"],
                      "day_gaps": dataset.identity["day_gaps"],
                      "publication_lag_slot": dataset.identity["publication_lag_slot"],
                      "catalogued": bool(args.dsn)}, indent=1))


if __name__ == "__main__":
    main()
