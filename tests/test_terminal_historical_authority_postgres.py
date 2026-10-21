"""Bounded historical lifecycle reads over immutable, isolated fixture authorities.

No historical version is invented: a read is permitted only after every current
authority it actually consumes was recorded. Later unrelated registrations and
unbound versions are irrelevant. Future changes of a consumed authority refuse.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class TerminalHistoricalAuthorityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1))
        command.upgrade(config, "head")

    def test_scoped_history_ignores_unrelated_registrations_and_unbound_policy_versions(self) -> None:
        from tests.test_activation_readiness_v1_postgres import recorded_established_study
        from trade_platform.account_policy_v1 import (
            AccountContextV1,
            AccountKindV1,
            AccountPolicyV1,
            PostgresAccountPolicyStoreV1,
        )
        from trade_platform.incubation_exit_criteria_v1 import (
            ExitCriteriaPacketV1,
            ForwardCriterionV1,
            PostgresExitCriteriaRegistryV1,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.research_terminal_read_model_v1 import read_candidate_lifecycle_v1
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
        from trade_platform.strategy_lab_policies_v1 import gross_cost_policy_v1
        from trade_platform.strategy_lab_study_v1 import SearchModeV1, SearchPlanV1
        from trade_platform.strategy_lab_validation_v1 import (
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
        )
        from trade_platform.strategy_lab_worker_v1 import run_trial_worker_v1
        from trade_platform.strategy_sdk_v1 import BarStrategyEvaluatorV1

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        try:
            # All nested real stores use savepoints; no controlled future record survives this test.
            with database.connection.transaction(force_rollback=True), \
                    tempfile.TemporaryDirectory(prefix="historical-authority-") as folder:
                fixture = recorded_established_study(Path(folder), database)
                # A unique immutable identity avoids other modules' append-only criteria.
                study = replace(fixture["study"], search=SearchPlanV1(
                    SearchModeV1.SEEDED_SAMPLE, 1, seed=uuid4().int % (2**31)))
                ledger = PostgresStrategyLabLedgerV1(database)
                run_trial_worker_v1(ledger, study, BarStrategyEvaluatorV1(fixture["data_root"]), worker="pit-fixture")
                manifest = build_study_manifest_v1(ledger, study)
                manifests = PostgresStrategyLabManifestStoreV1(database)
                manifests.record_manifest(manifest)
                candidates = freeze_candidates_v1(
                    manifest, CandidateSelectionRuleV1("trades", RankDirectionV1.HIGHER_IS_BETTER, 1))
                manifests.record_candidate_set(candidates)
                rerun = run_authority_rerun_v1(study, manifest, candidates, data_root=fixture["data_root"])
                PostgresAuthorityRerunStoreV1(database).record(rerun)
                packet = PreregistrationV1(study=study, rerun=rerun, symbol="BTCUSDT")
                PostgresHoldoutRegistryV1(database).record_preregistration(packet)
                trial = UUID(packet.identity()["candidates"][0])

                account = AccountContextV1(f"pit-{uuid4().hex[:12]}", AccountKindV1.PERSONAL_PAPER, "PIT fixture", "USDT")
                accounts = PostgresAccountPolicyStoreV1(database)
                accounts.register_account(account)
                policy = AccountPolicyV1(account, {})
                with patch("trade_platform.account_policy_v1.datetime", wraps=datetime) as clock:
                    clock.now.return_value = datetime.now(UTC) + timedelta(days=1)
                    accounts.record_policy(policy)
                criteria = ExitCriteriaPacketV1(
                    cycle_id="cycle-2026-08-20", study_id=str(study.study_id), trial_id=str(trial),
                    preregistration_hash=packet.content_hash, symbol="BTCUSDT",
                    performance_criteria=(ForwardCriterionV1("net_total_return", ">", "0"),),
                    minimum_evidence_coverage="0.9", minimum_round_trips=10, maximum_chain_breaks=0,
                    maximum_unfilled_signals=0, maximum_skipped_fills=0,
                    required_cost_policy_hash=gross_cost_policy_v1().policy().content_hash,
                    required_account_policy_hash=policy.content_hash,
                    authorized_by="fixture-owner", authorized_on="2026-08-19")
                PostgresExitCriteriaRegistryV1(database).record(criteria)
                with database.transaction() as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT recorded_at FROM strategy_lab_incubation_exit_criteria WHERE criteria_hash=%s",
                                   (criteria.content_hash,))
                    boundary = cursor.fetchone()[0]
                    # Derive a fixture boundary from actual consumed writes, not the guard's MAX query.
                    cursor.execute("SELECT recorded_at FROM account_policy_versions WHERE content_hash=%s",
                                   (policy.content_hash,))
                    boundary = max(boundary, cursor.fetchone()[0])
                    before = read_candidate_lifecycle_v1(cursor, study.study_id, trial, now=boundary)
                    self.assertEqual(criteria.content_hash, before.links.criteria_hash)
                    self.assertEqual(rerun.rerun_hash, before.links.rerun_hash)
                    with self.assertRaisesRegex(ValueError, "^terminal_historical_registry_knowledge_unavailable$"):
                        read_candidate_lifecycle_v1(cursor, study.study_id, trial,
                                                    now=boundary - timedelta(microseconds=1))

                future = boundary + timedelta(days=1)
                # Register in reverse chronological order through the real stores.
                for index, at in enumerate((future, boundary - timedelta(hours=1))):
                    unrelated = replace(study, search=SearchPlanV1(
                        SearchModeV1.SEEDED_SAMPLE, 1, seed=uuid4().int % (2**31)))
                    PostgresStrategyLabLedgerV1(database, clock=lambda at=at: at).register_study(unrelated)
                    other = AccountContextV1(f"other-{uuid4().hex[:12]}", AccountKindV1.PERSONAL_PAPER,
                                             f"Unrelated fixture {index}", "USDT")
                    with patch("trade_platform.account_policy_v1.datetime", wraps=datetime) as clock:
                        clock.now.return_value = at
                        accounts.register_account(other)
                        accounts.record_policy(AccountPolicyV1(other, {}))
                with patch("trade_platform.account_policy_v1.datetime", wraps=datetime) as clock:
                    clock.now.return_value = future
                    accounts.record_policy(AccountPolicyV1(account, {"paper_starting_capital": "100"}))

                with database.transaction() as connection, connection.cursor() as cursor:
                    after = read_candidate_lifecycle_v1(cursor, study.study_id, trial, now=boundary)
                    self.assertEqual(before.model_dump(), after.model_dump())
                    # Even when the new version is known, criteria remain bound to the old hash.
                    later = read_candidate_lifecycle_v1(cursor, study.study_id, trial, now=future)
                    self.assertEqual(before.links, later.links)
                    self.assertEqual(before.readiness, later.readiness)
                    self.assertIn("BLOCKED_OWNER_DECISION_OR_11:MISSING_OWNER_PAPER_STARTING_CAPITAL_OR_11",
                                  later.exit_criteria.blockers)

                # A known candidate-local row still has to re-derive; scoping the
                # historical guard must never bypass the immutable registry proof.
                with database.transaction() as connection, connection.cursor() as cursor:
                    cursor.execute("SAVEPOINT forged_criteria")
                    forged = criteria.identity()
                    forged["authorized_by"] = "forged-fixture-owner"
                    cursor.execute(
                        "INSERT INTO strategy_lab_incubation_exit_criteria "
                        "(criteria_hash, criteria_id, cycle_id, study_id, trial_id, preregistration_hash, status, identity) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'DRAFT',%s::jsonb)",
                        # A malformed draft still passes SQL shape checks, then fails re-derivation.
                        (uuid4().hex * 2, uuid4(), "cycle-2026-08-20", study.study_id, trial, packet.content_hash,
                         json.dumps({**forged, "status": "DRAFT", "unresolved": ["fixture-tamper"]})))
                    cursor.execute("SELECT clock_timestamp()")
                    known = max(cursor.fetchone()[0], future)
                    refused = read_candidate_lifecycle_v1(cursor, study.study_id, trial, now=known)
                    self.assertEqual("REFUSED", refused.exit_criteria.status)
                    self.assertIn("EXIT_CRITERIA_REGISTRY_REFUSED:stored_exit_criteria_identity_does_not_rederive",
                                  refused.exit_criteria.refusals)
                    # Roll back forged fixture evidence instead of mutating append-only rows.
                    cursor.execute("ROLLBACK TO SAVEPOINT forged_criteria")
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM strategy_lab_studies WHERE study_id=%s", (study.study_id,))
                self.assertEqual(0, cursor.fetchone()[0], "controlled fixture studies must be rolled back")
                cursor.execute("SELECT count(*) FROM account_contexts WHERE account_id=%s", (account.account_id,))
                self.assertEqual(0, cursor.fetchone()[0], "controlled fixture policies must be rolled back")
        finally:
            database.close()

