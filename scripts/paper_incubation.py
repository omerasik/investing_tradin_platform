"""Phase R10 operator entry point: paper incubation of a study's INCUBATING candidates.

    python scripts/paper_incubation.py run --dsn ... --family breakout_channel --dataset <window id> \\
        --symbol BTCUSDT [--capture-root ~/.trade_platform/capture-universe-r1b]
    python scripts/paper_incubation.py report --dsn ... [--symbol BTCUSDT]

``run`` watches only the candidates an R6 holdout validation made INCUBATING
(through the R8 holdout gate), records their live signals and fills each at the
next strictly later proven bar open. Restartable: unfilled signals are reloaded
and resolved from the replayed capture. ``report`` prints the policy-neutral
incubation report: unit exposure (OR-11 open), gross unless a verified fee
schedule exists (OR-6), required length UNRESOLVED until OR-7. Local capture and
database only: no network call, no order, no account, no capital.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
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
    watched_from_states_v1,
)
from trade_platform.paper_incubation_v1 import (
    PaperIncubationEngineV1,
    PostgresPaperIncubationStoreV1,
    incubation_report_v1,
)
from trade_platform.persistence import PostgresDatabase
from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report"))
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--family")
    parser.add_argument("--dataset", type=UUID)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--capture-root", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    database = PostgresDatabase(args.dsn)
    fills = PostgresPaperIncubationStoreV1(database)
    # OR-6: no verified fee schedule exists yet, so incubation economics are gross.
    cost_policy = gross_cost_policy_v1()
    if args.command == "report":
        report = incubation_report_v1(fills.fills(symbol=args.symbol), cost_policy, as_of=datetime.now(UTC))
        print(json.dumps(report, indent=1))
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from strategy_lab import build_study  # the same study the search registered

    from trade_platform.strategy_lab_validation_v1 import (
        CURRENT_CYCLE_V1,
        PostgresHoldoutRegistryV1,
    )

    study = build_study(args.family, args.dataset, args.data_root)
    registry = PostgresHoldoutRegistryV1(database)
    candidates = watched_from_states_v1(study, registry.states(study.study_id), symbol=args.symbol)
    if not candidates:
        raise SystemExit("no_incubating_candidates")
    gate = LiveHoldoutGateV1.for_cycle(CURRENT_CYCLE_V1, registry.opening(CURRENT_CYCLE_V1.cycle_id))
    root = args.capture_root or default_archive_root().parent / "capture-universe-r1b"
    feed = LiveBarFeedV1(root, first_party_bybit_universe_contract_v1(args.symbol))
    runner = LiveStrategyRunnerV1(candidates, holdout_gate=gate)
    signals = PostgresLiveSignalStoreV1(database)
    engine = PaperIncubationEngineV1(cost_policy)
    engine.add(fills.pending_signals(args.symbol))
    print(json.dumps({"incubating": [c.trial_id for c in candidates], "holdout": gate.payload(),
                      "pending_fills": engine.pending, "capture_root": str(root)}), flush=True)
    while True:
        for bar in feed.poll():
            for fill in engine.on_bars([bar]):  # earlier decisions fill before this bar's own signals
                fills.record(fill)
                print(json.dumps({"fill": fill.identity["signal_id"], "status": fill.status,
                                  "price": fill.identity["fill_price"]}), flush=True)
            new = runner.on_bars([bar])
            for signal in new:
                signals.record(signal)
            if new:  # fill from the stored signals: their decision instant is the recorded one
                engine.add(fills.pending_signals(args.symbol))
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
