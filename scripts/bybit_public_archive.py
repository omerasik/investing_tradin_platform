"""Phase R3B operator entry point: Bybit's free public trade archive.

    python scripts/bybit_public_archive.py acquire --symbol BTCUSDT --from 2026-09-21 --to 2026-09-23
    python scripts/bybit_public_archive.py build --symbol BTCUSDT --from ... --to ... [--dsn ...]

Downloads only from https://public.bybit.com/trading/ (unauthenticated public
market data), resumably, into ``~/.trade_platform/public-archive``. Reports
engineering counts only -- never prices, returns or P&L. The T2 publication
lag stays UNSET (owner decision OR-5): every frame's market_knowledge_at is NULL.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("acquire", "build"))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="first", required=True)
    parser.add_argument("--to", dest="last", required=True)
    parser.add_argument("--root", type=Path, default=Path.home() / ".trade_platform" / "public-archive")
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--dsn")
    args = parser.parse_args()
    contract = bybit_public_trade_archive_contract_v1()
    days = _days(args.first, args.last)
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
