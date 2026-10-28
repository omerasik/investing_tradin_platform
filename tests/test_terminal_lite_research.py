"""Offline Lite admission tests; all database identities here are synthetic mocks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts import terminal_lite_research as pilot
from scripts.strategy_lab import run_strategy_lab_command
from trade_platform.strategy_lab_worker_v1 import WorkerReportV1


class LitePilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.patch = patch.object(pilot, "STATE", self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.study = SimpleNamespace(study_id=pilot.STUDY_ID, content_hash="synthetic-study-hash", identity=dict)
        self.expected = {"container_id": "synthetic-container", "server_address": "192.0.2.1"}
        self.identity = ("trade_terminal_lite", "lite_owner", 160014, "192.0.2.1", "synthetic-cluster")
        self.schema = [("20261020_0067",)]
        self.reader = (False, False, False, False, ["default_transaction_read_only=on"])
        self.privileges = (False,)
        self.studies = []
        self.persisted = []
        self.size = 20 * 1024**2
        self.database = MagicMock()
        self.connection = self.database.transaction.return_value.__enter__.return_value
        self.connection.execute.side_effect = self.execute

    def execute(self, query, parameters=()):
        self.assertTrue(query.startswith(("SELECT", "SET TRANSACTION READ ONLY")))
        result = MagicMock()
        if "pg_control_system" in query:
            result.fetchone.return_value = self.identity
        elif "alembic_version" in query:
            result.fetchall.return_value = self.schema
        elif "FROM pg_roles WHERE rolname" in query:
            result.fetchone.return_value = self.reader
        elif "has_database_privilege" in query:
            result.fetchone.return_value = self.privileges
        elif "FROM strategy_lab_studies" in query:
            result.fetchall.return_value = self.studies
        elif "FROM strategy_lab_trials" in query:
            result.fetchall.return_value = self.persisted
        elif "pg_database_size" in query:
            result.fetchone.return_value = (self.size,)
        elif query != "SET TRANSACTION READ ONLY":
            self.fail(f"unexpected SQL: {query}")
        return result

    def test_original_remote_alias_and_reader_targets_refuse_before_connect(self):
        for dsn in (
            "postgresql://lite_owner@127.0.0.1:5439/trade_platform_research",
            "postgresql://lite_owner@remote:5441/trade_terminal_lite",
            "postgresql://lite_reader@127.0.0.1:5441/trade_terminal_lite",
            "postgresql://lite_owner@127.0.0.1:5441/trade_terminal_lite?hostaddr=192.0.2.5",
        ):
            with self.subTest(dsn=dsn), self.assertRaisesRegex(ValueError, "writer_target_forbidden"):
                pilot.checked_writer_dsn(dsn)

    def test_verified_server_is_pinned_without_database_writes(self):
        proof = pilot.database_proof(self.database, self.expected, self.study)
        self.assertEqual("synthetic-cluster", proof["system_identifier"])
        self.assertEqual("synthetic-cluster", json.loads((self.state / "research-cluster.local.json").read_text())["system_identifier"])

    def test_mismatched_server_schema_reader_study_and_size_refuse_before_search(self):
        cases = [
            ("identity", ("trade_platform_research", *self.identity[1:]), "server_identity"),
            ("identity", (*self.identity[:3], "192.0.2.2", self.identity[4]), "server_identity"),
            ("schema", [("0062",)], "schema_mismatch"),
            ("reader", (True, *self.reader[1:]), "select_only"),
            ("privileges", (True,), "select_only"),
            ("studies", [(pilot.STUDY_ID, "different-hash")], "another_study"),
            ("size", pilot.DATABASE_CEILING + 1, "exceeds_budget"),
        ]
        for field, changed, code in cases:
            previous = getattr(self, field)
            with self.subTest(field=field), patch("scripts.strategy_lab.run_strategy_lab_command") as search:
                setattr(self, field, changed)
                with self.assertRaisesRegex(ValueError, code):
                    pilot.execute_pilot(self.database, "unused", self.state, self.study, self.expected)
                search.assert_not_called()
            setattr(self, field, previous)

    def test_cluster_replacement_refuses_before_search(self):
        (self.state / "research-cluster.local.json").write_text("{}")
        with patch("scripts.strategy_lab.run_strategy_lab_command") as search:
            with self.assertRaisesRegex(ValueError, "cluster_pin"):
                pilot.execute_pilot(self.database, "unused", self.state, self.study, self.expected)
            search.assert_not_called()

    def test_existing_trial_definition_mismatch_refuses_before_search(self):
        self.studies = [(self.study.study_id, self.study.content_hash, {})]
        self.study.trials = lambda: [SimpleNamespace(trial_id="synthetic-trial", content_hash="canonical",
                                                    ordinal=0, space_index=0, parameters={"entry_bars": "60"})]
        self.persisted = [("synthetic-trial", "changed", 0, 0, {"entry_bars": "60"})]
        with patch("scripts.strategy_lab.run_strategy_lab_command") as search:
            with self.assertRaisesRegex(ValueError, "trial_identity_mismatch"):
                pilot.execute_pilot(self.database, "unused", self.state, self.study, self.expected)
            search.assert_not_called()

    def test_other_dataset_window_or_incomplete_btc_refuses_before_study_construction(self):
        btc = {"symbol": "BTCUSDT", "dataset_version_id": str(pilot.DATASET_ID),
               "content_hash": pilot.DATASET_HASH, "rows": 20160}
        for field, changed in (("dataset_version_id", str(pilot.STUDY_ID)),
                               ("content_hash", "different-window-hash"), ("rows", 20159)):
            report = {"first_utc_day": "2026-08-01", "last_utc_day": "2026-08-14", "datasets": [{**btc, field: changed}]}
            with (
                self.subTest(field=field),
                patch("trade_platform.terminal_lite_evidence_v1.verify_lite_subset", return_value=report),
                patch("scripts.strategy_lab.build_study") as build,
                self.assertRaisesRegex(ValueError, "exact_verified_btc_window"),
            ):
                pilot.checked_study(self.state)
            build.assert_not_called()

    def test_host_admission_and_runtime_floors_are_not_weakened(self):
        good = {"available_physical_bytes": 1024**3, "commit_headroom_bytes": 1024**3,
                "disk_free_bytes": pilot.DISK_FLOOR}
        self.assertTrue(pilot.admitted(good, initial=True))
        for key in good:
            bad = {**good, key: 0}
            self.assertFalse(pilot.admitted(bad, initial=True))
            self.assertFalse(pilot.admitted(bad, initial=False))

    def test_in_process_uses_the_same_single_worker_and_never_a_pool(self):
        study = SimpleNamespace(study_id=pilot.STUDY_ID, planned_trial_count=84, label="synthetic",
                                authority=lambda: SimpleNamespace(reasons=[]), timing_lag=None)
        with (
            patch("scripts.strategy_lab.PostgresStrategyLabLedgerV1") as ledger,
            patch("scripts.strategy_lab.run_trial_worker_v1", return_value=WorkerReportV1("fixture", 0, 0, 0, 0, False)) as worker,
            patch("scripts.strategy_lab.run_study_pool_v1") as pool,
        ):
            ledger.return_value.progress.return_value = SimpleNamespace(states={"PENDING": 84}, finished=False)
            output = run_strategy_lab_command("search", study=study, dsn="unused", database=self.database,
                                              data_root=self.state, workers=1, in_process=True)
            self.assertFalse(output["finished"])
            worker.assert_called_once()
            pool.assert_not_called()
            for command, workers in [("search", 2), ("status", 1)]:
                with self.assertRaisesRegex(ValueError, "one_worker"):
                    run_strategy_lab_command(command, study=study, dsn="unused", database=self.database,
                                             data_root=self.state, workers=workers, in_process=True)
