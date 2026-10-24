"""Phase R4.6 operator entry point: Strategy Lab search over T2 research bar windows.

    python scripts/strategy_lab.py search --dsn ... --family trend_ma_cross --dataset <window id> --workers 12
    python scripts/strategy_lab.py status --dsn ... --family trend_ma_cross --dataset <window id>
    python scripts/strategy_lab.py freeze --dsn ... --family ... --dataset ... --metric sharpe_daily_annualized --top-k 10
    python scripts/strategy_lab.py rerun --dsn ... --family ... --dataset ... --metric ... --top-k 10 --workers 4
    python scripts/strategy_lab.py walk-forward --dsn ... --family ... --dataset ... --mode ROLLING \
        --train-duration 30d --test-duration 7d --step 7d --embargo 1d \
        --first-test-start 2025-10-01T00:00:00Z --evaluation-end 2026-08-19T00:00:00Z \
        --selection-metric total_return --selection-direction HIGHER_IS_BETTER

A study is declared by (family, its default parameter space, the bound window,
the OR-3/OR-5/OR-6 owner policies, the 2026-08-20 evaluation bound), so the
same command always yields the same study id: re-running is a resume and a
finished study is a no-op. Every result is SEARCH_NON_AUTHORITATIVE and gross
(no verified fee schedule): the Decimal authority rerun is the only door to an
authoritative number; ``rerun`` freezes (idempotently) and records it. Reports
engineering counts and search metrics only.

``walk-forward`` (Stage C.1.1) evaluates a *registered* study under an explicit
purged walk-forward plan and selection rule and records the report
(WALK_FORWARD_DEVELOPMENT, never validation). Every duration, the embargo and
the selection rule are required: the example values above are syntax, not
recommendations, and nothing here supplies a default.

``walk-forward --read-only --export-dir <directory> --workers 1`` verifies
the registered study in a READ ONLY transaction and exports the complete
canonical report without accessing the reports table (0063 is not required).
Exports are NOT_REGISTERED development evidence, never promotion authority.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
from trade_platform.persistence import PersistenceError, PostgresDatabase
from trade_platform.public_archive_research_bars_v1 import load_research_bar_dataset_v1
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
from trade_platform.strategy_lab_authority_rerun_v1 import (
    PostgresAuthorityRerunStoreV1,
    run_authority_rerun_v1,
)
from trade_platform.strategy_lab_data_loading_v1 import BarStrategyEvaluatorV1, load_bars_v1
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
from trade_platform.strategy_lab_splits_v1 import SplitModeV1, WalkForwardPlanV1
from trade_platform.strategy_lab_study_v1 import (
    UNTOUCHED_HOLDOUT_BOUNDARY_V1,
    DatasetBindingV1,
    SearchModeV1,
    SearchPlanV1,
    StudySpecV1,
    canonical_identity_json_v1,
    identity_hash_v1,
)
from trade_platform.strategy_lab_walk_forward_v1 import (
    PostgresWalkForwardStoreV1,
    WalkForwardReportV1,
    WalkForwardSelectionRuleV1,
    run_walk_forward_v1,
)
from trade_platform.strategy_lab_worker_v1 import run_study_pool_v1
from trade_platform.strategy_sdk_v1 import FAMILIES_V1

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def parse_duration(text: str) -> timedelta:
    """``<int>m|h|d``; ``0m`` is the only spelling of a zero embargo."""
    match = _DURATION.match(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"duration must be <int>m|h|d, got {text!r}")
    return int(match.group(1)) * _UNITS[match.group(2)]


def parse_instant(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError("instant must carry a UTC offset")
    return value


def run_walk_forward_command(
    *, study: StudySpecV1, database: PostgresDatabase, data_root: Path | None, plan: WalkForwardPlanV1,
    rule: WalkForwardSelectionRuleV1,
) -> dict[str, object]:
    """Walk-forward of a study the ledger holds; records the report idempotently."""
    stored_hash, planned = PostgresStrategyLabLedgerV1(database).stored_study(study.study_id)
    if stored_hash != study.content_hash or planned != study.planned_trial_count:
        raise ValueError("study_declaration_differs_from_the_ledger")
    (binding,) = study.datasets
    bars = load_bars_v1(data_root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
    report = run_walk_forward_v1(study, plan, rule, bars=bars)
    recorded = PostgresWalkForwardStoreV1(database).record(report)
    return {"study_id": str(study.study_id), "label": study.label, "report_hash": report.report_hash,
            "recorded_now": recorded, "evaluation_class": report.identity["evaluation_class"],
            "authority": report.identity["authority_reasons"], "folds": report.folds}


def run_read_only_walk_forward_command(
    *, study: StudySpecV1, database: PostgresDatabase, data_root: Path | None, plan: WalkForwardPlanV1,
    rule: WalkForwardSelectionRuleV1, export_dir: Path,
) -> dict[str, object]:
    """Same calculation, no registration; failures export no partial report.

    Only stored_study's SELECT is issued, inside a server-enforced read-only
    transaction. No reports table or migration is needed. File name hashes the
    canonical export envelope; the unchanged original report has its own hash.
    Run in one fresh process under an external RAM/wall-clock budget.
    """
    registered = False
    dataset_identity = None
    report: WalkForwardReportV1 | None = None
    outcome, reason = "CALCULATED", None
    try:
        with database.transaction() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            stored_hash, planned = PostgresStrategyLabLedgerV1(database).stored_study(study.study_id)
            if stored_hash != study.content_hash or planned != study.planned_trial_count:
                raise ValueError("study_declaration_differs_from_the_ledger")
        registered = True
        (binding,) = study.datasets
        # Re-prove bytes even if load_bars_v1 has a process-local cache entry.
        dataset = load_research_bar_dataset_v1(ResearchFrameStoreV1(data_root), binding.dataset_version_id)
        if dataset.content_hash != binding.content_hash:
            raise ValueError("bound_dataset_content_hash_mismatch")
        dataset_identity = dict(dataset.identity)
        bars = load_bars_v1(data_root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
        report = run_walk_forward_v1(study, plan, rule, bars=bars)
        if identity_hash_v1(report.identity) != report.report_hash:
            raise ValueError("report_hash_mismatch")
    except MemoryError:
        outcome, reason = "RESOURCE_LIMITED", "memory_budget_exhausted_no_complete_report"
    except (ValueError, PersistenceError, FileNotFoundError) as error:
        outcome, reason = "UNAVAILABLE", str(error)
    if outcome != "CALCULATED":
        report = None
    envelope = {
        "schema_version": "strategy-lab-walk-forward-export-v1",
        "registration_status": "NOT_REGISTERED", "outcome": outcome, "reason": reason,
        "evaluation_class": "WALK_FORWARD_DEVELOPMENT", "numeric_tier": "SEARCH_NON_AUTHORITATIVE",
        "cost_mode": "GROSS_NON_PROMOTABLE", "promotable": False,
        "study_id": str(study.study_id), "study_content_hash": study.content_hash,
        "planned_trial_count": study.planned_trial_count, "study_identity": study.identity(),
        "registered_study_verified": registered, "verified_dataset_identity": dataset_identity,
        "plan": plan.payload(), "rule": rule.payload(),
        "report_hash": report.report_hash if report is not None else None,
        "report_identity": dict(report.identity) if report is not None else None,
    }
    export_hash = identity_hash_v1(envelope)
    content = canonical_identity_json_v1(envelope).encode("ascii")
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"{export_hash}.json"
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("existing_export_content_mismatch") from None
    return {"outcome": outcome, "registration_status": "NOT_REGISTERED", "export_hash": export_hash,
            "export_path": str(path.resolve()), "report_hash": envelope["report_hash"], "reason": reason}


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
    parser.add_argument("command", choices=("search", "status", "freeze", "rerun", "walk-forward"))
    parser.add_argument("--dsn", default=os.environ.get("TRADE_PLATFORM_RESEARCH_DSN"),
                        help="PostgreSQL DSN (default: TRADE_PLATFORM_RESEARCH_DSN, so it stays out of argv)")
    parser.add_argument("--family", required=True, choices=sorted(FAMILIES_V1))
    parser.add_argument("--dataset", required=True, type=UUID)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--metric", default="sharpe_daily_annualized")
    parser.add_argument("--direction", choices=("HIGHER_IS_BETTER", "LOWER_IS_BETTER"), default="HIGHER_IS_BETTER")
    parser.add_argument("--top-k", type=int, default=10)
    # walk-forward only; deliberately without defaults.
    parser.add_argument("--mode", choices=[mode.value for mode in SplitModeV1])
    parser.add_argument("--train-duration", type=parse_duration)
    parser.add_argument("--train-anchor", type=parse_instant)
    parser.add_argument("--test-duration", type=parse_duration)
    parser.add_argument("--step", type=parse_duration)
    parser.add_argument("--embargo", type=parse_duration)
    parser.add_argument("--first-test-start", type=parse_instant)
    parser.add_argument("--evaluation-end", type=parse_instant)
    parser.add_argument("--selection-metric")
    parser.add_argument("--selection-direction", choices=("HIGHER_IS_BETTER", "LOWER_IS_BETTER"))
    parser.add_argument("--read-only", action="store_true", help="walk-forward only; never record in PostgreSQL")
    parser.add_argument("--export-dir", type=Path, help="read-only walk-forward canonical export directory")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or TRADE_PLATFORM_RESEARCH_DSN is required")
    if (args.read_only or args.export_dir is not None) and (
            args.command != "walk-forward" or not args.read_only or args.export_dir is None or args.workers != 1):
        parser.error("read-only export requires walk-forward --read-only --export-dir <dir> --workers 1")
    if args.command == "walk-forward":
        missing = [name for name in ("mode", "test_duration", "step", "embargo", "first_test_start",
                                     "evaluation_end", "selection_metric", "selection_direction")
                   if getattr(args, name) is None]
        if missing:
            parser.error(f"walk-forward requires explicit --{', --'.join(n.replace('_', '-') for n in missing)}")
    study = build_study(args.family, args.dataset, args.data_root)
    database = PostgresDatabase(args.dsn)
    try:
        if args.command == "walk-forward":
            plan = WalkForwardPlanV1(
                mode=SplitModeV1(args.mode), test_duration=args.test_duration, step=args.step, embargo=args.embargo,
                first_test_start=args.first_test_start, evaluation_end_exclusive=args.evaluation_end,
                train_duration=args.train_duration, train_anchor=args.train_anchor)
            rule = WalkForwardSelectionRuleV1(args.selection_metric, RankDirectionV1(args.selection_direction))
            if args.read_only:
                assert args.export_dir is not None  # enforced before loading any data or opening PostgreSQL
                out = run_read_only_walk_forward_command(
                    study=study, database=database, data_root=args.data_root, plan=plan, rule=rule,
                    export_dir=args.export_dir)
            else:
                out = run_walk_forward_command(study=study, database=database, data_root=args.data_root,
                                               plan=plan, rule=rule)
        else:
            out = run_strategy_lab_command(args.command, study=study, dsn=args.dsn, database=database,
                                           data_root=args.data_root, workers=args.workers, metric=args.metric,
                                           direction=args.direction, top_k=args.top_k)
        print(json.dumps(out, indent=1, default=str))
        if args.read_only and out["outcome"] != "CALCULATED":
            raise SystemExit(2)
    finally:
        database.close()


if __name__ == "__main__":
    main()
