"""Operator entry point: produce paper parity and forward funding evidence from what is already stored.

    python scripts/paper_evidence.py --dsn ... --family mean_reversion_z --dataset <window id> --symbol BTCUSDT \\
        [--data-root ...] [--frame-root ...] [--capture-root ...] [--funding-root ...] \\
        [--funding-convention LAST_TRADE_OF_MINUTE_ENDING_AT_INSTANT --funding-start-ms ... --funding-end-ms ... \\
         --capture-max-unproven-nanos ... [--mark-tolerance-ms ...]] [--as-of 2026-10-18T00:00:00+00:00] [--persist]

A dry run by default: it reads the stored signals, fills, runner progress, sealed
datasets (rebuilt from raw capture when ``--frame-root`` and ``--capture-root`` are
given) and the forward funding store (``--funding-root``), rebuilds the evidence with
the unchanged resolvers and prints the run report as JSON -- per candidate and kind
PERSISTED / ALREADY_STORED / REFUSED / DRY_RUN_WOULD_RECORD with reason codes, plus
the missing inputs. Only ``--persist`` writes parity/funding, through the verified paper evidence
store, in one transaction. Economics is always a read-only projection. Proven
unrealized prices require all six explicit declarations: ``--paper-price-source``,
``--paper-price-session``, ``--paper-price-instrument``, ``--paper-price-instant``,
``--paper-price-max-age-micros`` and ``--paper-price-capture-max-unproven-nanos``.
None defaults to a market source, a latest observation or a valuation convention. No runner, capture, acquisition or network call; no
order, no account, no capital. The funding price convention has no default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.first_party_t4_forward_bars_v1 import T4PriceConventionV1
from trade_platform.live_signals_v1 import LiveHoldoutGateV1, watched_from_states_v1
from trade_platform.paper_evidence_production_v1 import (
    FundingDeclarationV1,
    PaperPriceDeclarationV1,
    PostgresPaperEvidenceSourcesV1,
    StorePaperEvidenceSinkV1,
    run_paper_evidence_production_v1,
)
from trade_platform.persistence import PostgresDatabase


def _aware(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise SystemExit("--as-of must carry a UTC offset")
    return value.astimezone(UTC)


def _declaration(args: argparse.Namespace) -> FundingDeclarationV1 | None:
    if args.funding_convention is None:
        return None  # undeclared: funding is refused FUNDING_PRICE_CONVENTION_NOT_DECLARED, never defaulted
    if None in (args.funding_start_ms, args.funding_end_ms, args.capture_max_unproven_nanos):
        raise SystemExit("--funding-convention needs --funding-start-ms, --funding-end-ms and "
                         "--capture-max-unproven-nanos")
    return FundingDeclarationV1(T4PriceConventionV1(args.funding_convention), args.funding_start_ms,
                                args.funding_end_ms, args.mark_tolerance_ms, args.capture_max_unproven_nanos,
                                None)  # no venue funding interval store exists: the window stays IRREGULAR


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("TRADE_PLATFORM_RESEARCH_DSN"),
                        help="PostgreSQL DSN (default: TRADE_PLATFORM_RESEARCH_DSN, so it stays out of argv)")
    parser.add_argument("--family", required=True)
    parser.add_argument("--dataset", type=UUID, required=True)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--frame-root", type=Path, default=None, help="research frame store of the sealed T4 data")
    parser.add_argument("--capture-root", type=Path, default=None, help="raw capture archive the seals rebuild from")
    parser.add_argument("--funding-root", type=Path, default=None, help="append-only forward settled funding store")
    parser.add_argument("--funding-convention", choices=[item.value for item in T4PriceConventionV1], default=None)
    parser.add_argument("--funding-start-ms", type=int, default=None)
    parser.add_argument("--funding-end-ms", type=int, default=None)
    parser.add_argument("--mark-tolerance-ms", type=int, default=None)
    parser.add_argument("--capture-max-unproven-nanos", type=int, default=None)
    parser.add_argument("--paper-price-source", type=UUID, default=None)
    parser.add_argument("--paper-price-session", type=UUID, default=None)
    parser.add_argument("--paper-price-instrument", default=None)
    parser.add_argument("--paper-price-instant", type=_aware, default=None)
    parser.add_argument("--paper-price-max-age-micros", type=int, default=None)
    parser.add_argument("--paper-price-capture-max-unproven-nanos", type=int, default=None)
    parser.add_argument("--as-of", type=_aware, default=None, help="reading instant (default: now)")
    parser.add_argument("--persist", action="store_true", help="write through the verified store (default: dry run)")
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("--dsn or TRADE_PLATFORM_RESEARCH_DSN is required")
    declaration = _declaration(args)
    price_values = (args.paper_price_source, args.paper_price_session, args.paper_price_instrument,
                    args.paper_price_instant, args.paper_price_max_age_micros,
                    args.paper_price_capture_max_unproven_nanos)
    prices = None
    if any(value is not None for value in price_values):
        if any(value is None for value in price_values):
            raise SystemExit("paper price requires explicit source, session, instrument, instant, "
                             "max-age-micros and capture-max-unproven-nanos")
        prices = {args.symbol: PaperPriceDeclarationV1(*price_values)}

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import psycopg
    from strategy_lab import (
        build_study,  # the same study the search registered and the runner watched
    )

    from trade_platform.paper_account_v1 import cycle_cost_policy_v1
    from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
    from trade_platform.strategy_lab_validation_v1 import (
        CURRENT_CYCLE_V1,
        PostgresHoldoutRegistryV1,
    )

    database = PostgresDatabase(args.dsn)
    study = build_study(args.family, args.dataset, args.data_root)
    registry = PostgresHoldoutRegistryV1(database)
    candidates = watched_from_states_v1(study, registry.states(study.study_id), symbol=args.symbol)
    if not candidates:
        raise SystemExit("no_incubating_candidates")
    gate = LiveHoldoutGateV1.for_cycle(CURRENT_CYCLE_V1, registry.opening(CURRENT_CYCLE_V1.cycle_id))
    recorded_at = datetime.now(UTC)
    as_of = args.as_of or recorded_at
    frame_store = None if args.frame_root is None else ResearchFrameStoreV1(args.frame_root)
    with database.transaction() as connection, connection.cursor() as cursor:
        sources = PostgresPaperEvidenceSourcesV1(cursor, frame_store=frame_store, capture_root=args.capture_root,
                                                 funding_root=args.funding_root)
        run = run_paper_evidence_production_v1(
            sources, cycle_id=CURRENT_CYCLE_V1.cycle_id, candidates=candidates, holdout_gate=gate,
            cost_policy=cycle_cost_policy_v1(cursor, CURRENT_CYCLE_V1.cycle_id), as_of=as_of,
            recorded_at=recorded_at, funding=declaration, prices=prices,
            sink=StorePaperEvidenceSinkV1(cursor) if args.persist else None)
        if not args.persist:
            raise psycopg.Rollback()  # a dry run leaves nothing behind, not even a read's transaction
    print(json.dumps(run.payload(), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
