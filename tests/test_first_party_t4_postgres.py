"""Phase R3A -- the first-party T4 dataset catalog against real PostgreSQL.

FIXTURE capture only (a synthetic 2026-09-20 session under a temporary root, all
timestamps in the past). No feature definition and no feature row is written, so
no shared-database invariant is touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FirstPartyT4CatalogPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")
        self.temp = Path(tempfile.mkdtemp(prefix="t4-pg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_register_is_idempotent_append_only_and_round_trips(self) -> None:
        from tests.test_first_party_t4_v1 import BASE, SECOND, FixtureArchive, _standard_samples
        from trade_platform.first_party_t4_dataset_v1 import (
            FirstPartyT4DatasetError,
            PostgresFirstPartyT4CatalogV1,
            t4_seal_from_catalog_v1,
        )
        from trade_platform.first_party_t4_seal_v1 import (
            discover_t4_segments_v1,
            restore_t4_seal_without_replay_v1,
            seal_t4_segment_v1,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        capture = self.temp / "capture"
        store = ResearchFrameStoreV1(self.temp / "research")
        FixtureArchive(capture).session(windows=[(BASE, BASE + 120 * SECOND)], samples=_standard_samples())
        plan = discover_t4_segments_v1(capture).segments[0]
        first_seal = datetime(2026, 9, 21, tzinfo=UTC)
        seal = seal_t4_segment_v1(plan, store=store, sealed_at=first_seal)

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        catalog = PostgresFirstPartyT4CatalogV1(database)
        stored = catalog.register(seal)
        self.assertEqual(stored.content_hash, seal.content_hash)
        again = seal_t4_segment_v1(plan, store=store, sealed_at=datetime(2026, 9, 25, tzinfo=UTC))
        self.assertEqual(catalog.register(again).sealed_at, first_seal)  # first seal wins
        self.assertIn(seal.dataset_version_id, catalog.dataset_ids())

        rebuilt = t4_seal_from_catalog_v1(catalog.load(seal.dataset_version_id), store=store, capture_root=capture)
        self.assertTrue(rebuilt.raw_replayed)
        self.assertEqual(rebuilt.dataset_version_id, seal.dataset_version_id)

        restored = restore_t4_seal_without_replay_v1(
            seal.identity, frame_manifests=seal.frame_manifests, sealed_at=first_seal, store=store
        )
        with self.assertRaises(FirstPartyT4DatasetError):
            catalog.register(restored)
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE first_party_t4_datasets SET sealed_at=%s WHERE dataset_version_id=%s",
                (datetime(2020, 1, 1, tzinfo=UTC), seal.dataset_version_id),
            )


if __name__ == "__main__":
    unittest.main()
