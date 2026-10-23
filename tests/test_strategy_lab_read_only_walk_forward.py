"""Read-only CLI exports, using synthetic offline data, never real evaluation evidence."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import strategy_lab as cli
from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_lab_walk_forward_v1 import RULE, SPACE, _plan
from trade_platform.strategy_lab_study_v1 import canonical_identity_json_v1, identity_hash_v1
from trade_platform.strategy_lab_walk_forward_v1 import WalkForwardReportV1


class ReadOnlyDatabase:
    """Runs the real ledger lookup; refuses everything except its exact SELECT."""

    def __init__(self, study):
        self.row = (study.content_hash, study.planned_trial_count)
        self.statements = []
        self.read_only = False

    @contextmanager
    def transaction(self):
        yield self

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql, parameters=()):
        self.statements.append((sql, parameters))
        if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
            self.read_only = True
        else:
            assert self.read_only
            assert sql == "SELECT content_hash, planned_trial_count FROM strategy_lab_studies WHERE study_id=%s"

    def fetchone(self):
        return self.row


class ReadOnlyWalkForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix="wf-export-fixture-"))
        cls.study, cls.root = build_window_and_study(cls.temp, family="trend_ma_cross", space=SPACE)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp)

    def setUp(self):
        self.exports = Path(tempfile.mkdtemp(prefix="wf-export-test-"))
        self.db = ReadOnlyDatabase(self.study)

    def tearDown(self):
        shutil.rmtree(self.exports)

    def run_export(self):
        return cli.run_read_only_walk_forward_command(
            study=self.study, database=self.db, data_root=self.root, plan=_plan(), rule=RULE,
            export_dir=self.exports)

    def read_export(self, result):
        path = Path(result["export_path"])
        content = path.read_bytes()
        envelope = json.loads(content)
        self.assertEqual(content, canonical_identity_json_v1(envelope).encode("ascii"))
        self.assertEqual(path.stem, identity_hash_v1(envelope))
        self.assertEqual(path.stem, result["export_hash"])
        return envelope

    def test_real_ledger_select_only_and_complete_export_matches_persisted_calculation(self):
        with patch.object(cli, "PostgresWalkForwardStoreV1", side_effect=AssertionError("no reports table")):
            result = self.run_export()
        envelope = self.read_export(result)
        self.assertEqual("CALCULATED", result["outcome"])
        self.assertEqual("NOT_REGISTERED", envelope["registration_status"])
        self.assertTrue(envelope["registered_study_verified"])
        self.assertEqual(self.study.study_id, self.db.statements[1][1][0])
        self.assertEqual(2, len(self.db.statements))
        self.assertEqual(self.study.identity(), envelope["study_identity"])
        self.assertEqual(identity_hash_v1(envelope["report_identity"]), envelope["report_hash"])
        self.assertEqual(self.study.datasets[0].content_hash, identity_hash_v1(envelope["verified_dataset_identity"]))
        self.assertEqual(6, len(envelope["report_identity"]["folds"]))
        self.assertEqual("WALK_FORWARD_DEVELOPMENT", envelope["evaluation_class"])
        self.assertEqual("SEARCH_NON_AUTHORITATIVE", envelope["numeric_tier"])
        self.assertEqual("GROSS_NON_PROMOTABLE", envelope["cost_mode"])
        # The unchanged normal command uses exactly the same engine/report identity.
        with patch.object(cli, "PostgresWalkForwardStoreV1") as store:
            normal = cli.run_walk_forward_command(
                study=self.study, database=self.db, data_root=self.root, plan=_plan(), rule=RULE)
        recorded = store.return_value.record.call_args.args[0]
        self.assertEqual(recorded.identity, envelope["report_identity"])
        self.assertEqual(normal["report_hash"], envelope["report_hash"])

    def test_serialization_is_deterministic_and_existing_file_is_not_overwritten(self):
        first = self.run_export()
        self.assertEqual(first, self.run_export())
        path = Path(first["export_path"])
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "existing_export_content_mismatch"):
            self.run_export()
        self.assertEqual(b"tampered", path.read_bytes())

    def test_absent_or_mismatched_registered_study_refuses_before_loading_or_calculating(self):
        for row in (None, ("f" * 64, 8), (self.study.content_hash, 9)):
            with self.subTest(row=row), patch.object(cli, "load_research_bar_dataset_v1") as loader, \
                    patch.object(cli, "run_walk_forward_v1") as engine:
                self.db.row = row
                result = self.run_export()
                self.assertEqual("UNAVAILABLE", result["outcome"])
                self.assertFalse(self.read_export(result)["registered_study_verified"])
                loader.assert_not_called()
                engine.assert_not_called()

    def test_mismatched_bound_dataset_refuses_even_with_cached_bars(self):
        with patch.object(cli, "load_research_bar_dataset_v1") as loader, \
                patch.object(cli, "run_walk_forward_v1") as engine:
            loader.return_value.content_hash = "f" * 64
            result = self.run_export()
        self.assertEqual("bound_dataset_content_hash_mismatch", result["reason"])
        self.assertIsNone(self.read_export(result)["report_identity"])
        engine.assert_not_called()

    def test_real_loader_refuses_tampered_dataset_identity(self):
        path = self.root / "v1" / "datasets" / "public-archive-bars" / f"{self.study.datasets[0].dataset_version_id}.json"
        original = path.read_bytes()
        try:
            payload = json.loads(original)
            payload["identity"]["symbol"] = "OTHERUSDT"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_export()
            self.assertEqual("UNAVAILABLE", result["outcome"])
            self.assertIn("identity_mismatch", result["reason"])
            self.assertIsNone(self.read_export(result)["report_hash"])
        finally:
            path.write_bytes(original)

    def test_real_loader_rechecks_object_bytes_despite_a_cached_bar_window(self):
        self.run_export()  # populate the existing SDK cache
        store = cli.ResearchFrameStoreV1(self.root)
        dataset = cli.load_research_bar_dataset_v1(store, self.study.datasets[0].dataset_version_id)
        manifest = store.load_manifest(dataset.bar_frame_manifest_hash)
        path = store.object_path(manifest.objects[0])
        original = path.read_bytes()
        try:
            path.write_bytes(b"corrupt")
            result = self.run_export()
            self.assertEqual("UNAVAILABLE", result["outcome"])
            self.assertIn("frame_object_corrupt", result["reason"])
            self.assertIsNone(self.read_export(result)["verified_dataset_identity"])
        finally:
            path.write_bytes(original)

    def test_unavailable_and_resource_limited_outcomes_have_no_partial_or_registered_result(self):
        for error, outcome in ((ValueError("split_unavailable"), "UNAVAILABLE"),
                               (MemoryError(), "RESOURCE_LIMITED")):
            with self.subTest(outcome=outcome), patch.object(cli, "run_walk_forward_v1", side_effect=error):
                result = self.run_export()
            envelope = self.read_export(result)
            self.assertEqual(outcome, result["outcome"])
            self.assertIsNone(envelope["report_hash"])
            self.assertIsNone(envelope["report_identity"])
            self.assertEqual("NOT_REGISTERED", envelope["registration_status"])
            self.assertEqual(_plan().payload(), envelope["plan"])

    def test_bad_report_hash_refuses_without_exporting_untrusted_report(self):
        with patch.object(cli, "run_walk_forward_v1", return_value=WalkForwardReportV1("study", {}, "f" * 64)):
            result = self.run_export()
        self.assertEqual("report_hash_mismatch", result["reason"])
        self.assertIsNone(self.read_export(result)["report_identity"])

    def test_excluded_folds_are_exported_without_reselection(self):
        identity = {"folds": [{"fold": 0, "status": "EXCLUDED_NO_ELIGIBLE_TRIAL_ON_TRAIN", "selected": None}]}
        report = WalkForwardReportV1(str(self.study.study_id), identity, identity_hash_v1(identity))
        with patch.object(cli, "run_walk_forward_v1", return_value=report) as engine:
            result = self.run_export()
        self.assertEqual(identity, self.read_export(result)["report_identity"])
        engine.assert_called_once()

    def test_cli_export_flags_cannot_apply_to_writes_or_implicit_workers(self):
        base = ["strategy_lab.py", "--dsn", "postgresql://fixture.invalid/test", "--family", "trend_ma_cross",
                "--dataset", str(self.study.datasets[0].dataset_version_id)]
        for flags in (["search", "--read-only", "--export-dir", str(self.exports), "--workers", "1"],
                      ["walk-forward", "--read-only"], ["walk-forward", "--export-dir", str(self.exports)],
                      ["walk-forward", "--read-only", "--export-dir", str(self.exports)]):
            with self.subTest(flags=flags), patch("sys.argv", base + flags), \
                    patch.object(cli, "PostgresDatabase") as db, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main()
            self.assertEqual(2, error.exception.code)
            db.assert_not_called()

    def test_cli_reports_unavailable_with_nonzero_exit_and_closes_database(self):
        argv = ["strategy_lab.py", "walk-forward", "--dsn", "postgresql://fixture.invalid/test",
                "--family", "trend_ma_cross", "--dataset", str(self.study.datasets[0].dataset_version_id),
                "--read-only", "--export-dir", str(self.exports), "--workers", "1", "--mode", "ROLLING",
                "--train-duration", "12h", "--test-duration", "6h", "--step", "6h", "--embargo", "10m",
                "--first-test-start", "2026-06-01T12:00:00Z", "--evaluation-end", "2026-06-03T00:00:00Z",
                "--selection-metric", "total_return", "--selection-direction", "HIGHER_IS_BETTER"]
        with patch("sys.argv", argv), patch.object(cli, "build_study", return_value=self.study), \
                patch.object(cli, "PostgresDatabase") as db, \
                patch.object(cli, "run_read_only_walk_forward_command", return_value={"outcome": "UNAVAILABLE"}), \
                patch.object(cli, "run_walk_forward_command") as persisted, \
                contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(2, error.exception.code)
        self.assertEqual({"outcome": "UNAVAILABLE"}, json.loads(output.getvalue()))
        db.return_value.close.assert_called_once()
        persisted.assert_not_called()


if __name__ == "__main__":
    unittest.main()
