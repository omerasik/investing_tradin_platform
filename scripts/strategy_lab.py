"""Phase R4.6 operator entry point: Strategy Lab search over T2 research bar windows.

    python scripts/strategy_lab.py search --dsn ... --family trend_ma_cross --dataset <window id> --workers 12
    python scripts/strategy_lab.py status --dsn ... --family trend_ma_cross --dataset <window id>
    python scripts/strategy_lab.py freeze --dsn ... --family ... --dataset ... --metric sharpe_daily_annualized --top-k 10
    python scripts/strategy_lab.py rerun --dsn ... --family ... --dataset ... --metric ... --top-k 10 --workers 4

A study is declared by (family, its default parameter space, the bound window,
the OR-3/OR-5/OR-6 owner policies, the 2026-08-20 evaluation bound), so the
same command always yields the same study id: re-running is a resume and a
finished study is a no-op. Every result is SEARCH_NON_AUTHORITATIVE and gross
(no verified fee schedule): the Decimal authority rerun is the only door to an
authoritative number; ``rerun`` freezes (idempotently) and records it. Reports
engineering counts and search metrics only.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
from trade_platform.persistence import PostgresDatabase
from trade_platform.public_archive_research_bars_v1 import load_research_bar_dataset_v1
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
from trade_platform.strategy_lab_authority_rerun_v1 import (
    PostgresAuthorityRerunStoreV1,
    run_authority_rerun_v1,
)
from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1
from trade_platform.strategy_lab_manifest_v1 import (
    CandidateSelectionRuleV1,
    PostgresStrategyLabManifestStoreV1,
    RankDirectionV1,
    build_study_manifest_v1,
    freeze_candidates_v1,
)
from trade_platform.strategy_lab_policies_v1 import (
    OR5_SWEEP_LAGS_V1,
    gross_cost_policy_v1,
    or3_numeric_policy_v1,
    or5_t2_timing_policy_v1,
)
from trade_platform.strategy_lab_study_v1 import (
    UNTOUCHED_HOLDOUT_BOUNDARY_V1,
    DatasetBindingV1,
    SearchModeV1,
    SearchPlanV1,
    StudySpecV1,
)
from trade_platform.strategy_lab_worker_v1 import run_study_pool_v1
from trade_platform.strategy_sdk_v1 import FAMILIES_V1, BarStrategyEvaluatorV1


def build_study(family: str, dataset_id: UUID, data_root: Path | None) -> StudySpecV1:
    store = ResearchFrameStoreV1(data_root)
    window = load_research_bar_dataset_v1(store, dataset_id)
    strategy = FAMILIES_V1[family]
    space = strategy.parameter_space()
    # The binding's knowledge bound covers the longest mandatory OR-5 sweep lag,
    # so every later sweep rerun stays inside the declared bound.
    binding = DatasetBindingV1(
        role="bars", dataset_version_id=window.dataset_version_id, content_hash=window.content_hash,
        evidence_tier=EvidenceTierV1.T2_EVENT_TIME,
        knowledge_upper_bound_exclusive=window.knowledge_upper_bound_exclusive(max(OR5_SWEEP_LAGS_V1)),
    )
    return StudySpecV1(
        strategy=strategy.spec(), parameter_space=space, datasets=(binding,),
        search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality),
        evaluation_upper_bound_exclusive=UNTOUCHED_HOLDOUT_BOUNDARY_V1,
        policies={"numeric": or3_numeric_policy_v1().payload, "timing": or5_t2_timing_policy_v1().payload,
                  "cost": gross_cost_policy_v1().policy().payload},
        label=f"{family} on {window.symbol} {window.identity['first_utc_day']}..{window.identity['last_utc_day']}",
    )


def run_strategy_lab_command(
    command: str, *, study: StudySpecV1, dsn: str, database: PostgresDatabase, data_root: Path | None,
    workers: int, metric: str | None = None, direction: str | None = None, top_k: int | None = None,
) -> dict[str, object]:
    """search / status / freeze / rerun of one study; shared by this CLI and the terminal command worker."""
    ledger = PostgresStrategyLabLedgerV1(database)
    if command == "search":
        reports = run_study_pool_v1(dsn, study, BarStrategyEvaluatorV1(data_root), workers=workers)
        print(json.dumps([dataclasses.asdict(report) for report in reports], default=str), flush=True)
    progress = ledger.progress(study.study_id)
    out: dict[str, object] = {
        "study_id": str(study.study_id), "label": study.label, "planned": study.planned_trial_count,
        "states": dict(progress.states), "finished": progress.finished,
        "authority": list(study.authority().reasons), "lag": str(study.timing_lag or timedelta(0))}
    if progress.finished and command in {"search", "freeze", "rerun"}:
        manifest = build_study_manifest_v1(ledger, study)
        store = PostgresStrategyLabManifestStoreV1(database)
        store.record_manifest(manifest)
        out["manifest_hash"] = manifest.manifest_hash
        if command in {"freeze", "rerun"}:
            if metric is None or direction is None or top_k is None:
                raise ValueError("freeze_requires_an_explicit_metric_direction_and_top_k")
            rule = CandidateSelectionRuleV1(metric, RankDirectionV1(direction), top_k)
            candidates = freeze_candidates_v1(manifest, rule)
            store.record_candidate_set(candidates)
            out["candidate_set_hash"] = candidates.candidate_set_hash
            out["candidates"] = candidates.candidates
            out["cutoff_tie"] = candidates.identity["cutoff_tie"]
        if command == "rerun":
            rerun = run_authority_rerun_v1(study, manifest, candidates, data_root=data_root, workers=workers)
            PostgresAuthorityRerunStoreV1(database).record(rerun)
            out["rerun_hash"] = rerun.rerun_hash
            out["selection_status"] = rerun.selection_status
            out["authoritative_selection"] = rerun.identity["authoritative_selection"]
    elif command in {"freeze", "rerun"}:
        out[command] = "STUDY_NOT_FINISHED"
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("search", "status", "freeze", "rerun"))
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--family", required=True, choices=sorted(FAMILIES_V1))
    parser.add_argument("--dataset", required=True, type=UUID)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--metric", default="sharpe_daily_annualized")
    parser.add_argument("--direction", choices=("HIGHER_IS_BETTER", "LOWER_IS_BETTER"), default="HIGHER_IS_BETTER")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    study = build_study(args.family, args.dataset, args.data_root)
    database = PostgresDatabase(args.dsn)
    try:
        out = run_strategy_lab_command(args.command, study=study, dsn=args.dsn, database=database,
                                       data_root=args.data_root, workers=args.workers, metric=args.metric,
                                       direction=args.direction, top_k=args.top_k)
        print(json.dumps(out, indent=1, default=str))
    finally:
        database.close()


if __name__ == "__main__":
    main()
