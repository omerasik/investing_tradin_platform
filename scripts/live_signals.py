"""Phase R8 operator entry point: live T4 signals for watched frozen candidates.

    python scripts/live_signals.py run --dsn ... --family breakout_channel --dataset <window id> \\
        --symbol BTCUSDT (--watchlist <id> | --rerun <rerun_hash>) [--capture-root ...]
    python scripts/live_signals.py recent --dsn ...

Which candidates to watch is an owner decision (OR-9). This entry point only
watches what it is told to: the owner's ACTIVE watch list entries for this
study and symbol (``--watchlist``), or the whole Decimal-authoritative selection
of one ESTABLISHED rerun named explicitly (``--rerun``), both RESEARCH_WATCH.
INCUBATING candidates are watched only by
``scripts/paper_incubation.py``, the single writer of their signals and fills,
so two processes never record different decisions for one signal. It reads the
local capture archive only; no network call, no order, no account. Signals are
proposals labelled NOT_VALIDATED_RESEARCH_WATCH.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.first_party_capture_archive_v1 import default_archive_root
from trade_platform.first_party_capture_authority_v1 import first_party_bybit_universe_contract_v1
from trade_platform.live_signals_v1 import (
    LiveBarFeedV1,
    LiveHoldoutGateV1,
    LiveStrategyRunnerV1,
    PostgresLiveSignalStoreV1,
    watched_from_rerun_v1,
)
from trade_platform.persistence import PostgresDatabase


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "recent"))
    parser.add_argument("--dsn", default=os.environ.get("TRADE_PLATFORM_RESEARCH_DSN"),
                        help="PostgreSQL DSN (default: TRADE_PLATFORM_RESEARCH_DSN, so it stays out of argv)")
    parser.add_argument("--family")
    parser.add_argument("--dataset", type=UUID)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--symbol", default="BTCUSDT")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--rerun", help="hash of an ESTABLISHED authority rerun (RESEARCH_WATCH, its whole top-k)")
    choice.add_argument("--watchlist", help="id of the owner's ACTIVE research watch list (OR-9)")
    parser.add_argument("--capture-root", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("--dsn or TRADE_PLATFORM_RESEARCH_DSN is required")
    database = PostgresDatabase(args.dsn)
    store = PostgresLiveSignalStoreV1(database)
    if args.command == "recent":
        print(json.dumps(store.recent(limit=50), indent=1, default=str))
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from strategy_lab import build_study  # the same study the search registered

    from trade_platform.strategy_lab_authority_rerun_v1 import PostgresAuthorityRerunStoreV1
    from trade_platform.strategy_lab_validation_v1 import (
        CURRENT_CYCLE_V1,
        PostgresHoldoutRegistryV1,
    )

    study = build_study(args.family, args.dataset, args.data_root)
    registry = PostgresHoldoutRegistryV1(database)
    states = registry.states(study.study_id)
    rerun_hash, chosen = args.rerun, None
    if args.watchlist:
        from trade_platform.research_watchlist_v1 import PostgresResearchWatchlistStoreV1

        watchlist = PostgresResearchWatchlistStoreV1(database).latest_active(args.watchlist)
        if watchlist is None:
            raise SystemExit("BLOCKED_OWNER_DECISION_OR_9:no_active_watch_list")
        entries = [e for e in watchlist.entries if e.study_id == study.study_id and e.symbol == args.symbol]
        if not entries or len({e.rerun_hash for e in entries}) != 1:
            raise SystemExit("watch_list_has_no_single_rerun_for_this_study_and_symbol")
        rerun_hash, chosen = entries[0].rerun_hash, {str(e.trial_id) for e in entries}
    reruns = [r for r in PostgresAuthorityRerunStoreV1(database).for_study(study.study_id)
              if r.rerun_hash == rerun_hash]
    if not reruns:
        raise SystemExit("rerun_not_found_for_this_study")
    candidates = watched_from_rerun_v1(study, reruns[0], states=states, symbol=args.symbol)
    if chosen is not None:  # only the owner's OR-9 selection, never the whole top-k
        candidates = [c for c in candidates if c.trial_id in chosen]
    if not candidates:
        raise SystemExit("no_candidates_to_watch")
    gate = LiveHoldoutGateV1.for_cycle(CURRENT_CYCLE_V1, registry.opening(CURRENT_CYCLE_V1.cycle_id))
    root = args.capture_root or default_archive_root().parent / "capture-universe-r1b"
    feed = LiveBarFeedV1(root, first_party_bybit_universe_contract_v1(args.symbol))
    runner = LiveStrategyRunnerV1(candidates, holdout_gate=gate)
    print(json.dumps({"watching": [c.trial_id for c in candidates], "authority": candidates[0].authority,
                      "holdout": gate.payload(), "capture_root": str(root)}), flush=True)
    if gate.holdout_end_exclusive is None:
        print(json.dumps({"note": "holdout not opened (OR-7): no forward bar of this cycle is evaluated"}),
              flush=True)
    while True:
        for signal in runner.on_bars(feed.poll()):
            store.record(signal)
            print(json.dumps({"signal_id": str(signal.signal_id), "decided_at": signal.decided_at.isoformat(),
                              "symbol": signal.identity["symbol"], "from": signal.identity["target_from"],
                              "to": signal.identity["target_to"], "claim": signal.identity["claim"]}), flush=True)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
