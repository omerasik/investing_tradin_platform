"""Lite isolation/provenance regressions; fixtures never enter the real Lite store.

Portable checks run under normal CI discovery. Real-data PostgreSQL acceptance
is explicit in tests.terminal_lite_acceptance through the Lite verify command.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.terminal_lite import clean_env, initialize_database, prepare_subset
from tests.test_public_archive_research_bars_v1 import DAY, DayArchive
from trade_platform.instrument_chart_v1 import _chart_query_config
from trade_platform.public_archive_research_bars_v1 import (
    acquire_and_derive_day_v1,
    load_day_bars_v1,
    load_research_bar_dataset_v1,
)
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
from trade_platform.strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1
from trade_platform.terminal_lite_v1 import create_lite_app


class LiteIsolationTests(unittest.TestCase):
    def test_lite_image_has_its_own_minimal_build_context(self):
        root = Path(__file__).resolve().parents[1]
        entries = {
            line.strip()
            for line in (root / "Dockerfile.lite.dockerignore")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        }
        self.assertEqual(
            entries,
            {
                "**",
                "!Dockerfile.lite",
                "!Dockerfile.lite.dockerignore",
                "!requirements-runtime.txt",
                "!requirements-lite.txt",
                "!src/",
                "!src/**",
            },
        )

    def test_original_database_is_refused_before_connecting(self):
        with (
            patch.dict(
                os.environ,
                {"LITE_READ_DSN": "postgresql://postgres@127.0.0.1:5439/trade_platform_research"},
            ),
            self.assertRaisesRegex(ValueError, "separate_database"),
        ):
            create_lite_app()

    def test_inherited_database_and_token_targets_are_removed(self):
        with patch.dict(
            os.environ,
            {
                "POSTGRES_TEST_DSN": "private",
                "POSTGRES_DSN": "private",
                "DATABASE_URL": "private",
                "TRADE_PLATFORM_OPERATOR_TOKEN": "private",
            },
        ):
            result = clean_env()
        self.assertNotIn("POSTGRES_TEST_DSN", result)
        self.assertNotIn("POSTGRES_DSN", result)
        self.assertNotIn("DATABASE_URL", result)
        self.assertNotIn("TRADE_PLATFORM_OPERATOR_TOKEN", result)

    def test_bootstrap_refuses_original_and_non_loopback_targets(self):
        for dsn in (
            "postgresql://postgres@127.0.0.1:5439/trade_platform_research",
            "postgresql://lite_owner@remote:5441/trade_terminal_lite",
        ):
            with self.assertRaisesRegex(ValueError, "dedicated_loopback_target"):
                initialize_database(dsn, "fixture-test-password")

    def test_holdout_is_refused_before_reading_any_source(self):
        with self.assertRaisesRegex(ValueError, "holdout_forbidden"):
            prepare_subset(
                Path("missing"),
                Path("missing"),
                Path("other"),
                UNTOUCHED_HOLDOUT_BOUNDARY_V1.date(),
            )

    def test_copy_preserves_exact_rows_and_does_not_modify_source(self):
        # Deliberately synthetic offline archive fixture, isolated from real data.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, source, target = root / "archive", root / "source", root / "lite"
            store = ResearchFrameStoreV1(source)
            acquire_and_derive_day_v1(
                archive, "BTCUSDT", DAY, store=store, evict=True, fetch=DayArchive()
            )
            before = {
                str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
            }
            with patch("scripts.terminal_lite.SYMBOLS", ("BTCUSDT",)):
                report = prepare_subset(archive, source, target, DAY)
            from uuid import UUID

            original = load_day_bars_v1(archive, "BTCUSDT", DAY)
            copy = load_research_bar_dataset_v1(
                ResearchFrameStoreV1(target), UUID(report["datasets"][0]["dataset_version_id"])
            )
            self.assertEqual(
                original.bar_logical_content_hash,
                copy.identity["bar_frame"]["logical_content_hash"],
            )
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})
            with self.assertRaisesRegex(ValueError, "separate_from_source"):
                prepare_subset(archive, source, source, DAY)
            # Altering a retained object fails closed, before a destination exists.
            manifest = store.load_manifest(original.bar_frame_manifest_hash)
            store.object_path(manifest.objects[0]).write_bytes(b"corrupted fixture")
            with (
                patch("scripts.terminal_lite.SYMBOLS", ("BTCUSDT",)),
                self.assertRaises(ValueError),
            ):
                prepare_subset(archive, source, root / "refused", DAY)
            self.assertFalse((root / "refused").exists())

    def test_lite_charts_have_memory_thread_and_spill_bounds(self):
        with patch.dict(os.environ, {"TRADE_PLATFORM_LITE": "1"}):
            self.assertEqual(
                {"threads": 1, "memory_limit": "128MB", "max_temp_directory_size": "0B"},
                _chart_query_config(),
            )
