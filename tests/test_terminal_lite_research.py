"""Offline Lite admission tests; all database identities here are synthetic mocks."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from scripts import terminal_lite_research as pilot
from scripts.strategy_lab import run_strategy_lab_command
from tests.test_strategy_lab_study_v1 import _study
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
        (self.state / "research-cluster.local.json").write_text(json.dumps({
            "database": self.identity[0], "writer": self.identity[1], "system_identifier": self.identity[4]}))
        self.schema = [("20261020_0067",)]
        self.reader = (False, False, False, False, ["default_transaction_read_only=on"])
        self.privileges = (False,)
        self.studies = []
        self.persisted = []
        self.size = 20 * 1024**2
        self.database = MagicMock()
        self.connection = self.database.transaction.return_value.__enter__.return_value
        self.connection.execute.side_effect = self.execute
        self.patch_studies = patch.object(pilot, "checked_studies", return_value={pilot.DATASET_ID: self.study})
        self.patch_studies.start()
        self.addCleanup(self.patch_studies.stop)

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
        proof = pilot.database_proof(self.database, self.expected, (self.study,))
        self.assertEqual("synthetic-cluster", proof["system_identifier"])
        self.assertEqual("synthetic-cluster", json.loads((self.state / "research-cluster.local.json").read_text())["system_identifier"])

    def test_mismatched_server_schema_reader_study_and_size_refuse_before_search(self):
        cases = [
            ("identity", ("trade_platform_research", *self.identity[1:]), "server_identity"),
            ("identity", (*self.identity[:3], "192.0.2.2", self.identity[4]), "server_identity"),
            ("schema", [("0062",)], "schema_mismatch"),
            ("reader", (True, *self.reader[1:]), "select_only"),
            ("privileges", (True,), "select_only"),
            ("studies", [(pilot.STUDY_ID, "different-hash", {})], "conflicting_study"),
            ("studies", [("unexpected-id", "different-hash", {})], "conflicting_study"),
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

    def test_missing_cluster_pin_refuses_without_repinning(self):
        pin = self.state / "research-cluster.local.json"
        pin.unlink()
        with self.assertRaisesRegex(ValueError, "cluster_pin"):
            pilot.database_proof(self.database, self.expected, (self.study,))
        self.assertFalse(pin.exists())

    def test_existing_trial_definition_mismatch_refuses_before_search(self):
        self.studies = [(self.study.study_id, self.study.content_hash, {})]
        self.study.trials = lambda: [SimpleNamespace(trial_id="synthetic-trial", content_hash="canonical",
                                                    ordinal=0, space_index=0, parameters={"entry_bars": "60"})]
        self.persisted = [("synthetic-trial", "changed", 0, 0, {"entry_bars": "60"})]
        with patch("scripts.strategy_lab.run_strategy_lab_command") as search:
            with self.assertRaisesRegex(ValueError, "trial_identity_mismatch"):
                pilot.execute_pilot(self.database, "unused", self.state, self.study, self.expected)
            search.assert_not_called()

    def test_other_dataset_window_or_incomplete_market_refuses_before_study_construction(self):
        self.patch_studies.stop()
        btc = {"symbol": "BTCUSDT", "dataset_version_id": str(pilot.DATASET_ID),
               "content_hash": pilot.DATASET_HASH, "rows": 20160}
        for field, changed in (("dataset_version_id", str(pilot.STUDY_ID)),
                               ("content_hash", "different-window-hash"), ("rows", 20159)):
            report = {"first_utc_day": "2026-08-01", "last_utc_day": "2026-08-14", "datasets": [{**btc, field: changed}]}
            with (
                self.subTest(field=field),
                patch("trade_platform.terminal_lite_evidence_v1.verify_lite_subset", return_value=report),
                patch("scripts.strategy_lab.build_study") as build,
                self.assertRaisesRegex(ValueError, "exact_verified_three_market_window"),
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

    def test_all_three_study_definitions_are_checked_before_any_results(self):
        others = [SimpleNamespace(study_id=key, content_hash="synthetic", identity=dict, trials=list)
                  for key in list(pilot.DATASETS)[1:]]
        self.study.trials = list
        allowed = (self.study, *others)
        self.studies = [(s.study_id, s.content_hash, {}) for s in reversed(allowed)]
        with patch.object(pilot, "verify_results") as audit:
            pilot.database_proof(self.database, self.expected, allowed)
            self.assertEqual(3, audit.call_count)
        self.studies[-1] = (self.study.study_id, "changed", {})
        with patch.object(pilot, "verify_results") as audit, self.assertRaisesRegex(ValueError, "conflicting_study"):
            pilot.database_proof(self.database, self.expected, allowed)
        audit.assert_not_called()

    def test_dataset_allowlist_rejects_unknown_before_evidence_or_launch(self):
        for method in (pilot.checked_study, lambda root, key: pilot.run(key)):
            with self.assertRaisesRegex(ValueError, "dataset_not_allowlisted"):
                method(self.state, UUID(int=0))

    def test_verified_three_market_identities_are_derived_not_relabelled(self):
        self.patch_studies.stop()
        report = {"first_utc_day": "2026-08-01", "last_utc_day": "2026-08-14", "datasets": [
            {"dataset_version_id": str(key), "symbol": symbol, "content_hash": digest, "rows": 20160}
            for key, (symbol, digest) in pilot.DATASETS.items()]}

        def build(family, key, root):
            self.assertEqual("breakout_channel", family)
            return SimpleNamespace(study_id=pilot.STUDY_ID if key == pilot.DATASET_ID else key,
                                   planned_trial_count=84, datasets=(SimpleNamespace(
                                       dataset_version_id=key, content_hash=pilot.DATASETS[key][1]),))

        with patch("trade_platform.terminal_lite_evidence_v1.verify_lite_subset", return_value=report), \
                patch("scripts.strategy_lab.build_study", side_effect=build) as builder:
            self.assertEqual(list(pilot.DATASETS), list(pilot.checked_studies(self.state)))
            self.assertEqual(3, builder.call_count)
            for item in report["datasets"]:
                original = item["rows"]
                item["rows"] = 20159
                builder.reset_mock()
                with self.assertRaisesRegex(ValueError, "exact_verified_three_market_window"):
                    pilot.checked_studies(self.state)
                builder.assert_not_called()
                item["rows"] = original

    def test_successor_requires_accepted_host_receipt(self):
        receipts = self.state / "research-pilot"
        receipts.mkdir()
        receipt = {"study_id": str(self.study.study_id), "study_content_hash": self.study.content_hash,
                   "finished": True, "states": {"SUCCEEDED": 84}, "supervisor_wall_seconds": 40,
                   "limits": {"memory_bytes": pilot.MEMORY_BYTES, "workers": 1, "cores": 1},
                   "database_bytes_after": 24 * 1024**2,
                   "host_minimum": {"available_physical_bytes": 1024**3, "commit_headroom_bytes": 1024**3,
                                    "disk_free_bytes": pilot.DISK_FLOOR},
                   "resources": {"peak_rss_bytes": 150 * 1024**2, "peak_commit_bytes": 180 * 1024**2}}
        path = receipts / "synthetic.json"
        with self.assertRaisesRegex(ValueError, "resource_acceptance"):
            pilot.accepted_predecessor(self.study)
        path.write_text(json.dumps(receipt))
        pilot.accepted_predecessor(self.study)
        for field, changed in (("finished", False), ("states", {"SUCCEEDED": 83, "FAILED": 1}),
                               ("supervisor_wall_seconds", 121), ("study_content_hash", "changed"),
                               ("database_bytes_after", pilot.DATABASE_CEILING + 1),
                               ("resources", {"peak_rss_bytes": pilot.MEMORY_BYTES + 1})):
            path.write_text(json.dumps({**receipt, field: changed}))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "resource_acceptance"):
                pilot.accepted_predecessor(self.study)

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

    def test_nonselected_result_corruption_refuses_before_worker(self):
        self.study.trials = list
        other = SimpleNamespace(study_id=UUID(int=1), content_hash="synthetic-other", identity=dict, trials=list)
        self.studies = [(s.study_id, s.content_hash, {}) for s in (self.study, other)]
        with patch.object(pilot, "checked_studies", return_value={pilot.DATASET_ID: self.study, UUID(int=1): other}), \
                patch.object(pilot, "verify_results", side_effect=[None, ValueError("synthetic_result_hash_mismatch")]), \
                patch("scripts.strategy_lab.run_strategy_lab_command") as worker:
            with self.assertRaisesRegex(ValueError, "result_hash_mismatch"):
                pilot.execute_pilot(self.database, "unused", self.state, self.study, self.expected)
            worker.assert_not_called()

    def test_completed_replay_compares_all_columns_and_events_not_only_counts(self):
        self.study.datasets = (SimpleNamespace(dataset_version_id=pilot.DATASET_ID),)
        self.study.trials = list
        counts = {name: 1 for name in pilot.TABLES}
        counts["strategy_lab_trials"] = 84
        manifest = SimpleNamespace(identity={"outcomes": {"EVALUATED": 64, "INADMISSIBLE_PARAMETERS": 20}})
        with patch.object(pilot, "database_proof", return_value={"database_bytes": 20 * 1024**2}), \
                patch.object(pilot, "verify_results", return_value=([], manifest)), \
                patch.object(pilot, "snapshot", return_value=counts), \
                patch.object(pilot, "study_digest", side_effect=["before", "after"]), \
                patch("scripts.strategy_lab.run_strategy_lab_command", return_value={
                    "finished": True, "states": {"SUCCEEDED": 84}}), \
                self.assertRaisesRegex(ValueError, "replay_changed_persisted_rows"):
            pilot.execute_pilot(self.database, "unused", self.state, self.study, self.expected)


class LiteResultIntegrityTests(unittest.TestCase):
    def setUp(self):
        from trade_platform.strategy_lab_ledger_v1 import (
            StudyProgressV1,
            TrialOutcomeV1,
            TrialResultV1,
            result_content_hash_v1,
        )
        from trade_platform.strategy_lab_manifest_v1 import build_study_manifest_v1

        self.study = _study()
        self.results = tuple(TrialResultV1(
            t.trial_id, self.study.study_id, 1, TrialOutcomeV1.EVALUATED, {"synthetic_metric": "0.01"},
            result_content_hash_v1(trial_content_hash=t.content_hash, outcome=TrialOutcomeV1.EVALUATED,
                                   metrics={"synthetic_metric": "0.01"}, cost_policy_slot=self.study.cost_policy_slot),
            "SEARCH_NON_AUTHORITATIVE", "fixture", None) for t in self.study.trials())
        self.ledger = MagicMock()
        self.ledger.stored_study.return_value = (self.study.content_hash, self.study.planned_trial_count)
        self.ledger.results.return_value = self.results
        self.ledger.progress.return_value = StudyProgressV1(self.study.study_id, 18, {"SUCCEEDED": 18}, 18)
        self.manifest = build_study_manifest_v1(self.ledger, self.study)
        self.database = MagicMock()
        self.stored = self.database.transaction.return_value.__enter__.return_value.execute.return_value
        self.stored.fetchall.return_value = [(self.manifest.manifest_hash, dict(self.manifest.identity))]
        patched = patch("trade_platform.strategy_lab_ledger_v1.PostgresStrategyLabLedgerV1", return_value=self.ledger)
        patched.start()
        self.addCleanup(patched.stop)

    def test_full_canonical_manifest_and_results_verify_repeatedly(self):
        for _ in range(2):
            results, manifest = pilot.verify_results(self.database, self.study, complete=True)
            self.assertEqual(self.results, results)
            self.assertEqual(self.manifest, manifest)

    def test_altered_metric_outcome_hash_or_tier_refuses(self):
        from trade_platform.strategy_lab_ledger_v1 import TrialOutcomeV1

        for changes in ({"metrics": {"synthetic_metric": "0.02"}}, {"result_content_hash": "changed"},
                        {"outcome": TrialOutcomeV1.INADMISSIBLE_PARAMETERS}, {"numeric_tier": "authoritative"}):
            self.ledger.results.return_value = (replace(self.results[0], **changes), *self.results[1:])
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "result_hash_mismatch"):
                pilot.verify_results(self.database, self.study)

    def test_missing_result_or_changed_manifest_refuses(self):
        self.ledger.results.return_value = self.results[:-1]
        with self.assertRaisesRegex(ValueError, "accounting_mismatch"):
            pilot.verify_results(self.database, self.study, complete=True)
        self.ledger.results.return_value = self.results
        self.stored.fetchall.return_value = [("changed", dict(self.manifest.identity))]
        with self.assertRaisesRegex(ValueError, "manifest_mismatch"):
            pilot.verify_results(self.database, self.study)
        self.stored.fetchall.return_value = []
        with self.assertRaisesRegex(ValueError, "complete_study_and_manifest"):
            pilot.verify_results(self.database, self.study, complete=True)

    def test_incomplete_denominator_refuses(self):
        self.ledger.progress.return_value = SimpleNamespace(states={"SUCCEEDED": 17}, finished=True)
        with self.assertRaisesRegex(ValueError, "accounting_mismatch"):
            pilot.verify_results(self.database, self.study)

    def test_changed_study_plan_refuses_before_reading_results(self):
        self.ledger.results.reset_mock()
        self.ledger.stored_study.return_value = (self.study.content_hash, 17)
        with self.assertRaisesRegex(ValueError, "study_plan_mismatch"):
            pilot.verify_results(self.database, self.study)
        self.ledger.results.assert_not_called()
