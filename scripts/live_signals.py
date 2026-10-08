"""Phase R8 operator entry point: live T4 signals for watched frozen candidates.

    python scripts/live_signals.py run --dsn ... --family breakout_channel --dataset <window id> \\
        --symbol BTCUSDT --rerun <rerun_hash> [--capture-root ~/.trade_platform/capture-universe-r1b]
    python scripts/live_signals.py recent --dsn ...

Which candidates to watch is an owner decision (OR-9). This entry point only
watches what it is told to: the Decimal-authoritative selection of one
ESTABLISHED rerun (RESEARCH_WATCH) or the candidates a holdout validation made
INCUBATING. It reads the local capture archive only; no network call, no
order, no account. Signals are proposals labelled NOT_VALIDATED_<authority>.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.first_party_capture_archive_v1 import default_archive_root
from trade_platform.first_party_capture_authority_v1 import first_party_bybit_universe_contract_v1
from trade_platform.live_signals_v1 import (
    LiveBarFeedV1,
    LiveStrategyRunnerV1,
    PostgresLiveSignalStoreV1,
    watched_from_rerun_v1,
    watched_from_states_v1,
)
from trade_platform.persistence import PostgresDatabase


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "recent"))
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--family")
    parser.add_argument("--dataset", type=UUID)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--rerun", help="hash of an ESTABLISHED authority rerun (RESEARCH_WATCH)")
    parser.add_argument("--incubating", action="store_true", help="watch the study's INCUBATING candidates")
    parser.add_argument("--capture-root", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    database = PostgresDatabase(args.dsn)
    store = PostgresLiveSignalStoreV1(database)
    if args.command == "recent":
        print(json.dumps(store.recent(limit=50), indent=1, default=str))
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from strategy_lab import build_study  # the same study the search registered

    from trade_platform.strategy_lab_authority_rerun_v1 import PostgresAuthorityRerunStoreV1
    from trade_platform.strategy_lab_validation_v1 import PostgresHoldoutRegistryV1

    study = build_study(args.family, args.dataset, args.data_root)
    if args.incubating:
        candidates = watched_from_states_v1(study, PostgresHoldoutRegistryV1(database).states(study.study_id),
                                            symbol=args.symbol)
    else:
        reruns = [r for r in PostgresAuthorityRerunStoreV1(database).for_study(study.study_id)
                  if r.rerun_hash == args.rerun]
        if not reruns:
            raise SystemExit("rerun_not_found_for_this_study")
        candidates = watched_from_rerun_v1(study, reruns[0], symbol=args.symbol)
    if not candidates:
        raise SystemExit("no_candidates_to_watch")
    root = args.capture_root or default_archive_root().parent / "capture-universe-r1b"
    feed = LiveBarFeedV1(root, first_party_bybit_universe_contract_v1(args.symbol))
    runner = LiveStrategyRunnerV1(candidates)
    print(json.dumps({"watching": [c.trial_id for c in candidates], "authority": candidates[0].authority,
                      "capture_root": str(root)}), flush=True)
    while True:
        for signal in runner.on_bars(feed.poll()):
            store.record(signal)
            print(json.dumps({"signal_id": str(signal.signal_id), "decided_at": signal.decided_at.isoformat(),
                              "symbol": signal.identity["symbol"], "from": signal.identity["target_from"],
                              "to": signal.identity["target_to"], "claim": signal.identity["claim"]}), flush=True)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
