"""Portable multi-day admission/rollback tests over isolated synthetic fixtures."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.terminal_lite import activate, active_root, prepare_subset
from tests.lite_fixtures import seed_lite_days
from tests.test_public_archive_research_bars_v1 import DAY
from trade_platform.public_archive_research_bars_v1 import _paths
from trade_platform.terminal_lite_evidence_v1 import (
    LITE_MAX_BYTES,
    report_days,
    verify_lite_subset,
    window_days,
)
from trade_platform.terminal_lite_v1 import create_lite_app


class LiteWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="synthetic-lite-window-")
        cls.root = Path(cls.fixture.name)
        cls.last = DAY + timedelta(days=6)
        seed_lite_days(cls.root, DAY, 7)
        cls.legacy = prepare_subset(
            cls.root / "archive", cls.root / "source", cls.root / "legacy", DAY
        )
        cls.report = prepare_subset(
            cls.root / "archive", cls.root / "source", cls.root / "window", DAY, cls.last
        )

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lite-window-check-")
        self.target = Path(self.temporary.name) / "subset"
        shutil.copytree(self.root / "window", self.target)

    def tearDown(self):
        self.temporary.cleanup()

    def write_report(self, report):
        (self.target / "lite-provenance.json").write_text(json.dumps(report), encoding="utf-8")

    def test_exact_window_identity_counts_and_legacy_remain_valid(self):
        report = verify_lite_subset(self.target)
        self.assertEqual(7, len(report_days(report)))
        self.assertEqual([10080] * 3, [item["rows"] for item in report["datasets"]])
        self.assertEqual([7] * 3, [len(item["day_frames"]) for item in report["datasets"]])
        self.assertNotEqual(
            self.legacy["datasets"][0]["dataset_version_id"],
            report["datasets"][0]["dataset_version_id"],
        )
        self.assertEqual(1, len(report_days(verify_lite_subset(self.root / "legacy"))))
        self.assertLess(
            sum(p.stat().st_size for p in self.target.rglob("*") if p.is_file()), LITE_MAX_BYTES
        )

    def test_missing_day_refuses_startup_before_database_connection(self):
        _paths(self.target / "archive-provenance", "BTCUSDT", DAY)["day_bars"].unlink()
        with (
            patch.dict(
                os.environ,
                {
                    "LITE_READ_DSN": "postgresql://lite_reader@127.0.0.1:5441/trade_terminal_lite",
                    "LITE_RESEARCH_ROOT": str(self.target),
                    "TRADE_PLATFORM_LITE": "1",
                },
            ),
            patch("trade_platform.terminal_lite_v1.PostgresDatabase") as database,
            self.assertRaisesRegex(ValueError, "lite_day_unavailable"),
        ):
            create_lite_app()
        database.assert_not_called()

    def test_corrupted_object_and_archive_identity_refuse(self):
        obj = next((self.target / "v1/objects").rglob("*.parquet"))
        obj.write_bytes(b"corrupt synthetic fixture")
        with self.assertRaises(ValueError):
            verify_lite_subset(self.target)

    def test_day_binding_cannot_be_substituted(self):
        report = json.loads((self.target / "lite-provenance.json").read_text())
        report["datasets"][0]["day_frames"][0]["manifest_hash"] = "0" * 64
        self.write_report(report)
        with self.assertRaisesRegex(ValueError, "day_bindings_mismatch"):
            verify_lite_subset(self.target)

    def test_retained_archive_manifest_identity_cannot_be_changed(self):
        path = _paths(self.target / "archive-provenance", "BTCUSDT", DAY)["manifest"]
        manifest = json.loads(path.read_text())
        manifest["sha256"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "day_provenance_mismatch"):
            verify_lite_subset(self.target)

    def test_wrong_dates_counts_and_holdout_are_refused(self):
        for change, code in [
            ({"day_count": 6}, "day_count_mismatch"),
            ({"utc_day": str(DAY)}, "ambiguous_window"),
            ({"first_utc_day": "2026-08-14", "last_utc_day": "2026-08-20"}, "holdout_forbidden"),
        ]:
            with self.subTest(change=change):
                report = dict(self.report)
                report.update(change)
                self.write_report(report)
                with self.assertRaisesRegex(ValueError, code):
                    verify_lite_subset(self.target)

    def test_resource_and_extra_catalogue_refusal(self):
        extra = self.target / "v1/datasets/public-archive-bars/uncatalogued.json"
        extra.write_text("{}")
        with self.assertRaisesRegex(ValueError, "catalogue_must_match"):
            verify_lite_subset(self.target)
        extra.unlink()
        with (self.target / "oversized.fixture").open("wb") as handle:
            handle.truncate(LITE_MAX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "exceeds_disk_budget"):
            verify_lite_subset(self.target)

    def test_missing_minute_is_not_filled_and_no_target_is_published(self):
        root = Path(self.temporary.name) / "incomplete"
        seed_lite_days(root, DAY, 1, omitted_minute=17)
        with self.assertRaisesRegex(ValueError, "day_frame_mismatch"):
            prepare_subset(root / "archive", root / "source", root / "refused", DAY)
        self.assertFalse((root / "refused").exists())

    def test_activation_is_pinned_and_previous_subset_is_recoverable(self):
        state = Path(self.temporary.name) / "state"
        shutil.copytree(self.root / "legacy", state / "evidence")
        shutil.copytree(self.target, state / "subsets/window")
        before = {
            str(p.relative_to(state / "evidence")): p.read_bytes()
            for p in (state / "evidence").rglob("*")
            if p.is_file()
        }
        with patch("scripts.terminal_lite.STATE", state):
            activate("subsets/window")
            self.assertEqual(state / "subsets/window", active_root())
            pointer = (state / "active-subset.local.json").read_bytes()
            with self.assertRaises(ValueError):
                activate("../outside")
            self.assertEqual(pointer, (state / "active-subset.local.json").read_bytes())
            obj = next((state / "subsets/window/v1/objects").rglob("*.parquet"))
            obj.write_bytes(b"corrupted active synthetic fixture")
            with self.assertRaises(ValueError):
                activate("evidence")
            self.assertEqual(pointer, (state / "active-subset.local.json").read_bytes())
            activate("evidence", recover=True)
            self.assertEqual(state / "evidence", active_root())
        self.assertEqual(
            before, {name: (state / "evidence" / name).read_bytes() for name in before}
        )

    def test_window_bounds_are_explicit_and_never_silently_shortened(self):
        with self.assertRaisesRegex(ValueError, "7_to_14_days"):
            window_days(DAY, DAY + timedelta(days=14))
        with self.assertRaisesRegex(ValueError, "7_to_14_days"):
            window_days(DAY, DAY - timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "7_to_14_days"):
            window_days(DAY, DAY + timedelta(days=5))
        with self.assertRaisesRegex(ValueError, "already_exists"):
            prepare_subset(self.root / "archive", self.root / "source", self.root / "legacy", DAY)
