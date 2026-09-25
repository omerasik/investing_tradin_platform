"""Phase R3A operator entry point: first-party T4 discovery, sealing, verification, features.

    python scripts/first_party_t4.py discover
    python scripts/first_party_t4.py seal --dsn postgresql://...      # seals + catalogs every segment
    python scripts/first_party_t4.py verify --dsn ...                 # rebuilds every catalogued dataset
    python scripts/first_party_t4.py features --dsn ... --dataset <id> # V3 basis rows + decision-time proof

Reads the immutable capture archive (default ``~/.trade_platform/capture``) and
writes frames to the research data root (default
``~/.trade_platform/research-data``). Public market data only; no network call.
Reports engineering counts and clocks only -- never prices, returns or P&L.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.first_party_capture_archive_v1 import default_archive_root
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=1, sort_keys=True, default=str))


def _discover(args: argparse.Namespace) -> None:
    from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1

    discovery = discover_t4_segments_v1(args.capture_root)
    _print(
        {
            "complete_partitions": [
                {"session": str(p.session_id), "utc_day": p.utc_day, "records": p.record_count,
                 "windows": len(p.coverage)}
                for p in discovery.complete_partitions
            ],
            "segments": [
                {"session": str(s.partition.session_id), "utc_day": s.partition.utc_day,
                 "window": s.window_index, "start": s.start_arrival_nanos, "end": s.end_arrival_nanos,
                 "seconds": (s.end_arrival_nanos - s.start_arrival_nanos) / 1e9,
                 "clock_samples": len(s.clock.samples)}
                for s in discovery.segments
            ],
            "exclusions": [item.payload() for item in discovery.exclusions],
        }
    )


def _seal(args: argparse.Namespace) -> None:
    from trade_platform.first_party_t4_dataset_v1 import (
        PostgresFirstPartyT4CatalogV1,
        issue_t4_evidence_tier_v1,
    )
    from trade_platform.first_party_t4_seal_v1 import discover_t4_segments_v1, seal_t4_segment_v1
    from trade_platform.persistence import PostgresDatabase

    store = ResearchFrameStoreV1(args.data_root)
    catalog = PostgresFirstPartyT4CatalogV1(PostgresDatabase(args.dsn))
    results = []
    for plan in discover_t4_segments_v1(args.capture_root).segments:
        reference = f"{plan.partition.session_id}:{plan.partition.utc_day}:window={plan.window_index}"
        try:
            seal = seal_t4_segment_v1(plan, store=store)
        except (ValueError, OSError) as error:  # reported per segment; nothing partial is catalogued
            results.append({"segment": reference, "sealed": False, "reason": f"{type(error).__name__}:{error}"})
            continue
        stored = catalog.register(seal)
        provenance, verdict = issue_t4_evidence_tier_v1(seal)
        results.append(
            {"segment": reference, "sealed": True, "dataset_version_id": str(seal.dataset_version_id),
             "content_hash": seal.content_hash, "sealed_at": stored.sealed_at,
             "counts": seal.identity["counts"], "timing_facts": seal.identity["timing_facts"],
             "clock_evidence": {k: v for k, v in seal.identity["clock_evidence"].items() if k != "sample_hashes"},
             "segment_bounds": seal.identity["segment"],
             "provenance": {"status": provenance.status, "evidence_id": provenance.evidence_id},
             "tier": {"tier": verdict.tier, "reasons": verdict.reasons,
                      "professional": verdict.is_professional_evidence(),
                      "evidence_id": verdict.evidence_id}}
        )
    _print(results)


def _verify(args: argparse.Namespace) -> None:
    from trade_platform.first_party_t4_dataset_v1 import (
        PostgresFirstPartyT4CatalogV1,
        t4_seal_from_catalog_v1,
    )
    from trade_platform.persistence import PostgresDatabase

    store = ResearchFrameStoreV1(args.data_root)
    catalog = PostgresFirstPartyT4CatalogV1(PostgresDatabase(args.dsn))
    out = []
    for dataset_id in catalog.dataset_ids():
        try:
            seal = t4_seal_from_catalog_v1(catalog.load(dataset_id), store=store, capture_root=args.capture_root)
            out.append({"dataset_version_id": str(dataset_id), "rebuilt": True, "content_hash": seal.content_hash})
        except (ValueError, OSError) as error:
            out.append({"dataset_version_id": str(dataset_id), "rebuilt": False, "reason": str(error)})
    _print(out)


def _features(args: argparse.Namespace) -> None:
    from trade_platform.feature_authority import PostgresFeatureAuthority
    from trade_platform.first_party_t4_dataset_v1 import (
        FirstPartyT4SealedClockResolverV1,
        PostgresFirstPartyT4CatalogV1,
        build_t4_basis_features_v3,
        crypto_mark_index_basis_first_party_t4_definition,
        issue_t4_evidence_tier_v1,
        t4_seal_from_catalog_v1,
    )
    from trade_platform.knowledge_time_doctrine_v1 import ClaimCeilingV1, DeclaredComputeLatencyV1
    from trade_platform.open_to_open_validation_orchestration_v1 import (
        count_distinct_historical_decision_times_v1,
    )
    from trade_platform.persistence import PostgresDatabase

    database = PostgresDatabase(args.dsn)
    store = ResearchFrameStoreV1(args.data_root)
    catalog = PostgresFirstPartyT4CatalogV1(database)
    seal = t4_seal_from_catalog_v1(catalog.load(UUID(args.dataset)), store=store, capture_root=args.capture_root)
    _, verdict = issue_t4_evidence_tier_v1(seal)
    resolver = FirstPartyT4SealedClockResolverV1(seal, store=store)
    authority = PostgresFeatureAuthority(database)
    # feature_id is minted per instance: rows must carry the stored version's id,
    # and a definition that drifted from the stored one fails closed.
    definition = authority.register_or_resolve(
        crypto_mark_index_basis_first_party_t4_definition(datetime.now(UTC))
    )
    rows = build_t4_basis_features_v3(
        seal, verdict=verdict, resolver=resolver, store=store, feature_id=definition.feature_id,
        computed_at=datetime.now(UTC),
    )
    written = authority.materialize_subject_stream_v3(rows) if args.write else 0
    later = build_t4_basis_features_v3(
        seal, verdict=verdict, resolver=resolver, store=store, feature_id=definition.feature_id,
        computed_at=datetime(2031, 1, 1, tzinfo=UTC),
    )
    tiers = {verdict.evidence_id: verdict}
    counts = {}
    for latency in (0, 250_000_000):
        declared = DeclaredComputeLatencyV1(latency, "R3A engineering proof only; not a methodology declaration")
        counts[str(latency)] = count_distinct_historical_decision_times_v1(
            rows, compute_latency=declared, evidence_tiers=tiers, clock_resolver=resolver,
            minimum_claim=ClaimCeilingV1.PROFESSIONAL,
        )
    _print(
        {"dataset_version_id": str(seal.dataset_version_id), "feature_id": str(definition.feature_id),
         "rows": len(rows), "rows_written": written,
         "distinct_market_knowledge_times": len({row.market_knowledge_at for row in rows}),
         "distinct_decision_times_by_latency_nanos": counts,
         "recompute_identical_hashes": [r.content_hash for r in rows] == [r.content_hash for r in later],
         "claim_ceilings": sorted({row.claim_ceiling.name for row in rows}),
         "tier": verdict.tier, "verdict_evidence_id": verdict.evidence_id}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("discover", "seal", "verify", "features"))
    parser.add_argument("--capture-root", type=Path, default=default_archive_root())
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--dsn")
    parser.add_argument("--dataset")
    parser.add_argument("--write", action="store_true", help="features: persist V3 rows")
    args = parser.parse_args()
    if args.command != "discover" and not args.dsn:
        parser.error("--dsn is required")
    {"discover": _discover, "seal": _seal, "verify": _verify, "features": _features}[args.command](args)


if __name__ == "__main__":
    main()
