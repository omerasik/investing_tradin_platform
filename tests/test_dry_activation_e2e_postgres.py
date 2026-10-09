"""Dry activation, end to end, on ENGINEERING evidence only (real PostgreSQL).

study -> freeze -> Decimal rerun (terminal commands + the command worker) -> preregistration ->
engineering holdout opening and validation -> INCUBATING -> live/replay signals -> risk check ->
paper fill / non-fill -> replay reconciliation -> paper account ledger -> terminal read models.

Everything here is a FIXTURE: synthetic archive days, a TEST research cycle (2026-06-01) issued
with the private test issuers because the database correctly refuses to register a past cycle or
open a holdout before its span has passed, fixture OR-6/OR-7/OR-11 values that are never owner
decisions, and synthetic live bars. The real cycle-2026-08-20 is never opened, read or summarized:
the test asserts it is still UNOPENED and BLOCKED on OR-7 at the end.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ENGINEERING = "engineering-dry-activation"
REAL_CYCLE = "cycle-2026-08-20"


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class DryActivationE2EPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="dry-activation-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _research_through_commands(self, database: Any) -> tuple[Any, Path]:
        """search -> freeze -> Decimal rerun, requested over the protected API and run by the worker."""
        from fastapi.testclient import TestClient

        from tests.test_strategy_lab_e2e_fixture import build_window_and_study
        from trade_platform.api import build_app
        from trade_platform.audit import SQLiteAuditStore
        from trade_platform.config import PlatformConfig
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.research_terminal_commands_v1 import PostgresTerminalCommandLedgerV1
        from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator, OperatorRole

        study, data_root = build_window_and_study(self.temp, family="breakout_channel")
        client = TestClient(build_app(
            PlatformConfig(), SQLiteAuditStore(), OperatorAuthenticator("t", ENGINEERING, OperatorRole.RESEARCHER),
            InMemoryRateLimiter(max_requests=1000), operator_dashboard_queries=PostgresOperatorDashboardQueries(database),
            terminal_commands=PostgresTerminalCommandLedgerV1(database), research_data_root=data_root),
            headers={"Authorization": "Bearer t"})
        window = {"family": "breakout_channel", "dataset_version_id": str(study.datasets[0].dataset_version_id)}
        run = uuid4().hex[:8]
        # ENGINEERING selection rule: funding is not modelled for T2, so a candidate that holds across a
        # funding window fails closed; ranking by crossings reaches the fixture's 20 zero-crossing trials.
        rule = {"metric": "funding_window_crossings", "direction": "LOWER_IS_BETTER", "top_k": 20}
        ids = set()
        for kind, inputs in (("STRATEGY_SEARCH", {**window, "workers": 1}), ("CANDIDATE_FREEZE", {**window, **rule}),
                             ("DECIMAL_RERUN", {**window, **rule, "workers": 1})):
            response = client.post("/operator-dashboard/research-terminal/commands",
                                   json={"kind": kind, "inputs": inputs, "idempotency_key": f"{run}-{kind}"})
            self.assertEqual(202, response.status_code, response.text)
            ids.add(response.json()["command_id"])
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from research_terminal_worker import Worker

        Worker(argparse.Namespace(dsn=os.environ["POSTGRES_TEST_DSN"], data_root=data_root,
                                  archive_root=self.temp / "archive", capture_root=None, poll_seconds=0.1,
                                  once=True, disk_guard_gib=0.0)).run()
        states = {c["kind"]: c["state"] for c in client.get(
            "/operator-dashboard/research-terminal/commands?limit=200").json() if c["command_id"] in ids}
        self.assertEqual({"STRATEGY_SEARCH": "SUCCEEDED", "CANDIDATE_FREEZE": "SUCCEEDED",
                          "DECIMAL_RERUN": "SUCCEEDED"}, states)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from strategy_lab import build_study

        return build_study("breakout_channel", study.datasets[0].dataset_version_id, data_root), data_root

    def test_the_whole_activation_chain_runs_on_engineering_evidence_and_the_real_holdout_stays_closed(self) -> None:
        from tests.test_live_signals_v1 import _live_bars
        from tests.test_strategy_lab_e2e_fixture import FIRST_DAY, SyntheticDays
        from tests.test_strategy_lab_validation_v1 import (
            fixture_cost_policy,
            opening_for,
            test_cycle,
        )
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform.account_policy_v1 import (
            AccountContextV1,
            AccountKindV1,
            AccountPolicyV1,
            PostgresAccountPolicyStoreV1,
        )
        from trade_platform.live_signals_v1 import (
            LiveHoldoutGateV1,
            LiveStrategyRunnerV1,
            PostgresLiveSignalStoreV1,
            watched_from_rerun_v1,
            watched_from_states_v1,
        )
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
        from trade_platform.paper_account_v1 import (
            build_paper_account_ledger_v1,
            cost_policy_from_payload_v1,
            fills_signals_from_rows,
            summarize_ledger_v1,
        )
        from trade_platform.paper_incubation_v1 import (
            PaperIncubationEngineV1,
            PaperIncubationError,
            PendingSignalV1,
            PostgresPaperIncubationStoreV1,
            expected_fill_bar_open_micros_v1,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.public_archive_research_bars_v1 import (
            acquire_and_derive_day_v1,
            build_research_bar_dataset_v1,
        )
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
        from trade_platform.research_watchlist_v1 import (
            PostgresResearchWatchlistStoreV1,
            ResearchWatchlistV1,
            WatchEntryV1,
        )
        from trade_platform.strategy_lab_authority_rerun_v1 import PostgresAuthorityRerunStoreV1
        from trade_platform.strategy_lab_validation_v1 import (
            STATUS_AUTHORIZED,
            CriterionV1,
            PreregistrationV1,
            validate_on_holdout_v1,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            self.assertEqual(0, _real_openings(database), "the real holdout must be closed before the test")
            # 1. Research through the terminal command surface.
            study, data_root = self._research_through_commands(database)
            (rerun,) = [r for r in PostgresAuthorityRerunStoreV1(database).for_study(study.study_id)
                        if r.selection_status == "ESTABLISHED"
                        and r.identity["authoritative_selection"]["rule"]["metric"] == "funding_window_crossings"][:1]

            # 1b. OR-9 ENGINEERING watch list over the Decimal selection, before any R6 state exists (the
            # authority refuses a trial that already has one). Its candidates are RESEARCH_WATCH only.
            selected = [str(i["trial_id"]) for i in rerun.identity["authoritative_selection"]["selected"]]
            watch_id = f"eng-dry-{uuid4().hex[:8]}"
            PostgresResearchWatchlistStoreV1(database).record(ResearchWatchlistV1(
                watch_id, tuple(WatchEntryV1(study.study_id, rerun.rerun_hash, UUID(t), "BTCUSDT") for t in selected),
                ENGINEERING, "2026-10-09", "engineering dry activation"))
            watchlist = PostgresResearchWatchlistStoreV1(database).latest_active(watch_id)
            self.assertIsNotNone(watchlist)
            chosen = {str(e.trial_id) for e in watchlist.entries}
            watched = [c for c in watched_from_rerun_v1(study, rerun, states=[], symbol="BTCUSDT")
                       if c.trial_id in chosen]  # exactly the operator CLI's (live_signals.py) filter
            self.assertEqual(chosen, {c.trial_id for c in watched})

            # 2. ENGINEERING preregistration on the TEST cycle, fixture OR-6/OR-7 values (never owner values).
            packet = PreregistrationV1(
                study=study, rerun=rerun, symbol="BTCUSDT", cycle=test_cycle(),
                holdout_end_exclusive=test_cycle().holdout_start + timedelta(days=2),
                acceptance_criteria=(CriterionV1("trades", ">=", "1"),), minimum_trades=1,
                cost_policy=fixture_cost_policy(), incubation_days=14,
                authorized_by=ENGINEERING, authorized_on="2026-10-09")
            self.assertEqual(STATUS_AUTHORIZED, packet.status)
            opening = opening_for(packet)  # private test issuer: the DB refuses a past cycle, by design

            # 3. Engineering holdout validation on fixture days.
            store = ResearchFrameStoreV1(data_root)
            archive = data_root.parent / "archive"
            for offset in range(2):
                acquire_and_derive_day_v1(archive, "BTCUSDT", FIRST_DAY + timedelta(days=offset), store=store,
                                          evict=True, fetch=SyntheticDays())
            holdout = build_research_bar_dataset_v1(archive, "BTCUSDT", FIRST_DAY, FIRST_DAY + timedelta(days=1),
                                                    store=store)
            run = validate_on_holdout_v1(packet, opening, holdout, store=store)
            states = [{**event, "evidence_hash": run.content_hash} for event in run.state_events]
            incubating = [s for s in states if s["state"] == "INCUBATING"]
            self.assertTrue(incubating, [(s["trial_id"], s["reasons"]) for s in states])

            # 4. Live signals of the INCUBATING candidates on synthetic forward bars after the holdout.
            candidates = watched_from_states_v1(study, states, symbol="BTCUSDT")
            gate = LiveHoldoutGateV1.for_cycle(test_cycle(), opening)
            bars = _live_bars(_walk(900, seed=33))
            runner = LiveStrategyRunnerV1(candidates, holdout_gate=gate, clock=lambda: _first_open(bars))
            signals = [signal for bar in bars for signal in runner.on_bars([bar])]
            self.assertTrue(signals)
            self.assertTrue(all(s.identity["claim"] == "NOT_VALIDATED_INCUBATING" for s in signals))
            live_store = PostgresLiveSignalStoreV1(database)
            for signal in signals:
                live_store.record(signal)
            # The OR-9 research watch emits on the same bars, but its signals are never paper-incubated.
            watch_runner = LiveStrategyRunnerV1(watched, holdout_gate=gate, clock=lambda: _first_open(bars))
            watch_signals = [signal for bar in bars for signal in watch_runner.on_bars([bar])]
            self.assertTrue(watch_signals)
            self.assertTrue(all(s.identity["claim"] == "NOT_VALIDATED_RESEARCH_WATCH" for s in watch_signals))
            with self.assertRaisesRegex(PaperIncubationError, "only_incubating"):
                PendingSignalV1.from_signal(watch_signals[0])

            # 5. Paper fills, with one forced non-fill: the first signal's fill bar -- the first minute
            # boundary strictly after its decision, derived by the fill authority itself -- is never observed.
            cost = cost_policy_from_payload_v1(packet.frozen["cost_policy"])
            missing = expected_fill_bar_open_micros_v1(signals[0].decided_at)
            self.assertGreater(missing, int((signals[0].decided_at - _first_open(bars)) / timedelta(microseconds=1))
                               + bars[0].bar.bar_open_micros)
            observed = [bar for bar in bars if bar.bar.bar_open_micros != missing]
            fills = _paper(PaperIncubationEngineV1(cost), signals, observed, PendingSignalV1)
            self.assertIn("FILL_BAR_NOT_OBSERVED", {f.status for f in fills})
            self.assertIn("FILLED", {f.status for f in fills})
            fill_store = PostgresPaperIncubationStoreV1(database)
            for item in fills:
                fill_store.record(item)

            # 6. Reconciliation: a replay of the same evidence gives byte-identical fills.
            replay = _paper(PaperIncubationEngineV1(cost), signals, observed, PendingSignalV1)
            self.assertEqual([f.content_hash for f in fills], [f.content_hash for f in replay])
            for item in replay:
                fill_store.record(item)  # idempotent: the same fill is accepted again, nothing duplicated

            # 7. Risk check + money P&L under a fixture account policy (ENGINEERING values).
            accounts = PostgresAccountPolicyStoreV1(database)
            account = AccountContextV1(f"eng-dry-{uuid4().hex[:8]}", AccountKindV1.PERSONAL_PAPER,
                                       "Engineering dry activation", "USDT")
            accounts.register_account(account)
            na = dict.fromkeys(("minimum_data_quality", "maximum_spread_fraction", "maximum_event_risk",
                                "maximum_expected_slippage_fraction", "maximum_per_trade_loss",
                                "maximum_stop_distance_fraction", "stop_gap_buffer_fraction"), "NOT_APPLICABLE")
            accounts.record_policy(AccountPolicyV1(account, {
                "paper_starting_capital": "10000", "paper_order_notional": "1000", "maximum_leverage": "2",
                "daily_loss_limit": "1000", "maximum_drawdown_limit": "2000", "allowed_symbols": ["BTCUSDT"],
                "maximum_order_notional": "1000", "maximum_position_notional": "1000",
                "maximum_daily_order_notional": "3000", "max_market_age_seconds": 120, **na}, ENGINEERING, "2026-10-09"))
            # The engineering cycle is not in the registry (its issuer is the test's), so its ledger is built
            # from the RECORDED rows -- re-derived against their hashes -- with the functions the read model uses.
            ours = [str(s.signal_id) for s in signals]
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT content_hash, identity FROM paper_incubation_fills WHERE signal_id::text = "
                               "ANY(%s) ORDER BY decided_at, signal_id", (ours,))
                fill_rows = cursor.fetchall()
                cursor.execute("SELECT signal_id, content_hash, identity FROM live_strategy_signals WHERE "
                               "signal_id::text = ANY(%s)", (ours,))
                recorded_fills, recorded_signals = fills_signals_from_rows(fill_rows, cursor.fetchall())
            ledger = build_paper_account_ledger_v1(
                accounts_policy(accounts, account), recorded_fills, recorded_signals, cost,
                as_of=_first_open(bars) + timedelta(days=2))
            decisions = summarize_ledger_v1(ledger)["decisions"]
            self.assertGreater(decisions.get("APPROVED_FILLED", 0), 0, decisions)
            self.assertGreater(decisions.get("NOT_FILLED", 0), 0, decisions)
            self.assertIn("DAILY_ORDER_NOTIONAL_LIMIT", ledger["rejections_by_reason"])
            self.assertEqual("PAPER_ACCOUNT_SIMULATION_NOT_EXECUTION_AUTHORITY", ledger["claim"])
            self.assertTrue(ledger["promotable"] is not None and ledger["cost_complete"] is False)
            self.assertEqual(ledger["content_hash"], build_paper_account_ledger_v1(
                accounts_policy(accounts, account), list(reversed(recorded_fills)), recorded_signals, cost,
                as_of=_first_open(bars) + timedelta(days=2))["content_hash"])  # replay-identical

            # 8. Terminal read models of the REAL cycle: engineering evidence never enters them, and the
            # real holdout was never opened.
            queries = PostgresOperatorDashboardQueries(database)
            incubation = queries.terminal_incubation()
            self.assertNotIn(str(study.study_id), {c["study_id"] for c in incubation.report["candidates"]})
            paper = queries.terminal_paper_account(account.account_id)
            self.assertEqual("AVAILABLE", paper["state"], paper)
            self.assertEqual([], [o for o in paper["ledger"]["orders"] if o["signal_id"] in ours])
            recent = {str(view.signal_id): view for view in queries.terminal_signals(limit=500)}
            self.assertTrue(all(recent[s].claim == "NOT_VALIDATED_INCUBATING" for s in ours if s in recent))
            readiness = {a.key: a for a in queries.terminal_activation().answers}
            self.assertEqual("BLOCKED", readiness["holdout_open"].status)
            self.assertIn("OR-7", readiness["holdout_open"].owner_gates)
            self.assertEqual("UNOPENED", queries.terminal_overview().cycle.holdout_state)
            self.assertEqual(0, _real_openings(database), "the real holdout must still be closed")
        finally:
            database.close()


def accounts_policy(store: Any, account: Any) -> Any:
    """The account's latest ACTIVE policy as recorded, re-derived from its stored identity."""
    from trade_platform.account_policy_v1 import AccountPolicyV1

    with store._database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT identity FROM account_policy_versions WHERE account_id=%s AND status='ACTIVE' "
                       "ORDER BY recorded_at DESC, policy_version_id LIMIT 1", (account.account_id,))
        stored = cursor.fetchone()[0]
    policy = AccountPolicyV1(account, stored["values"], stored["approved_by"], stored["approved_on"])
    assert policy.identity() == stored
    return policy


def _real_openings(database: Any) -> int:
    """Openings recorded for the real research cycle (must stay zero)."""
    from trade_platform.strategy_lab_validation_v1 import CURRENT_CYCLE_V1

    assert CURRENT_CYCLE_V1.cycle_id == REAL_CYCLE
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM strategy_lab_holdout_openings WHERE cycle_id=%s", (REAL_CYCLE,))
        return int(cursor.fetchone()[0])


def _first_open(bars: list[Any]) -> Any:
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=bars[0].bar.bar_open_micros)


def _paper(engine: Any, signals: list[Any], bars: list[Any], pending: Any) -> list[Any]:
    """Live order: each bar first resolves pending fills, then adds the signals decided on it."""
    by_bar: dict[int, list[Any]] = {}
    for signal in signals:
        by_bar.setdefault(int(signal.identity["evidence"]["bar_open_micros"]), []).append(signal)
    fills = []
    for bar in bars:
        fills.extend(engine.on_bars([bar]))
        engine.add(pending.from_signal(s) for s in by_bar.get(bar.bar.bar_open_micros, []))
    return fills


if __name__ == "__main__":
    unittest.main()
