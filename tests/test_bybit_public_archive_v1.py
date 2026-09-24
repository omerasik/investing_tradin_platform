"""Phase R3B -- the free public trade archive, offline.

Every archive file here is a FIXTURE served by an in-memory fake fetcher; no
network call is made.
"""

from __future__ import annotations

import gzip
import io
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from trade_platform.bybit_public_archive_v1 import (
    ARCHIVE_SCHEMA_V1,
    T2_PUBLICATION_LAG_SLOT_V1,
    BybitPublicArchiveError,
    HttpResponseV1,
    acquire_archive_day_v1,
    build_archive_bars_v1,
    build_archive_dataset_v1,
    bybit_public_trade_archive_contract_v1,
    iter_archive_trades_v1,
    verify_archive_file_v1,
)

DAY = date(2026, 9, 20)
DAY_START = int(datetime(2026, 9, 20, tzinfo=UTC).timestamp())


def _csv(rows: list[tuple[float | str, ...]]) -> str:
    lines = [",".join(ARCHIVE_SCHEMA_V1)]
    for index, (offset, price, size) in enumerate(rows):
        seconds = f"{DAY_START + float(offset):.3f}" if not isinstance(offset, str) else offset
        notional = str(round(float(price) * float(size), 4))
        lines.append(
            f"{seconds},BTCUSDT,{'Buy' if index % 2 else 'Sell'},{size},{price},PlusTick,"
            f"id-{index:06d},8.6e+09,{size},{notional},0"
        )
    return "\n".join(lines) + "\n"


STANDARD = [(0.5, "100.0", "0.001"), (10.25, "101.0", "0.002"), (59.999, "99.5", "0.001"),
            (60.0, "102.0", "0.003"), (60.0, "103.0", "0.001"), (185.0, "104.0", "0.001")]


class FakeArchive:
    def __init__(self, body: bytes, *, etag: str = '"e-1"', ignore_range: bool = False) -> None:
        self.body = body
        self.etag = etag
        self.ignore_range = ignore_range
        self.calls: list[Mapping[str, str]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponseV1:
        self.calls.append(dict(headers))
        start_text, end_text = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(start_text), int(end_text)
        if self.ignore_range:
            return HttpResponseV1(200, {"content-length": str(len(self.body)), "etag": self.etag}, self.body)
        if start >= len(self.body):
            return HttpResponseV1(416, {}, b"")
        chunk = self.body[start:end + 1]
        return HttpResponseV1(
            206,
            {"content-range": f"bytes {start}-{start + len(chunk) - 1}/{len(self.body)}",
             "etag": self.etag, "last-modified": "Mon, 21 Sep 2026 01:14:00 GMT"},
            chunk,
        )


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode(), mtime=0)


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="r3b-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_source_contract_is_deterministic_and_carries_no_lag(self) -> None:
        first = bybit_public_trade_archive_contract_v1()
        self.assertEqual(first.source_id, bybit_public_trade_archive_contract_v1().source_id)
        self.assertEqual(first.publication_lag_slot, T2_PUBLICATION_LAG_SLOT_V1)
        from trade_platform.evidence_tier_authority_v1 import authorized_timing_contracts_v1

        self.assertNotIn(first.source_id, {c.source_id for c in authorized_timing_contracts_v1()})

    def test_resumable_ranged_download_is_proven_and_checkpointed(self) -> None:
        fake = FakeArchive(_gz(_csv(STANDARD)))
        manifest = acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=fake, chunk_bytes=64)
        self.assertGreater(len(fake.calls), 2)
        self.assertEqual(manifest.bytes, len(fake.body))
        verify_archive_file_v1(self.temp, manifest)
        calls = len(fake.calls)
        again = acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=fake)
        self.assertEqual(again.sha256, manifest.sha256)
        self.assertEqual(len(fake.calls), calls)  # checkpoint: no refetch

    def test_an_interrupted_partial_resumes_from_its_length(self) -> None:
        body = _gz(_csv(STANDARD))
        contract = bybit_public_trade_archive_contract_v1()
        part = self.temp / "v1" / f"source={contract.source_id}" / "symbol=BTCUSDT" / "BTCUSDT2026-09-20.csv.gz.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(body[:50])
        fake = FakeArchive(body)
        acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=fake, chunk_bytes=1 << 20)
        self.assertEqual(fake.calls[0]["Range"], f"bytes=50-{50 + (1 << 20) - 1}")

    def test_a_file_that_changes_mid_download_is_refused(self) -> None:
        body = _gz(_csv(STANDARD))
        fake = FakeArchive(body)
        original = fake.__call__

        def flipping(url: str, headers: Mapping[str, str]) -> HttpResponseV1:
            response = original(url, headers)
            if len(fake.calls) > 1:
                return HttpResponseV1(response.status, {**response.headers, "etag": '"e-2"'}, response.body)
            return response

        with self.assertRaisesRegex(BybitPublicArchiveError, "changed_during_download"):
            acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=flipping, chunk_bytes=64)

    def test_corruption_is_detected_and_nothing_is_kept(self) -> None:
        body = bytearray(_gz(_csv(STANDARD)))
        body[len(body) // 2] ^= 0xFF
        with self.assertRaises(BybitPublicArchiveError):
            acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=FakeArchive(bytes(body)))
        self.assertEqual(list(self.temp.rglob("*.gz*")), [])

    def test_stored_file_tampering_fails_verification(self) -> None:
        manifest = acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=FakeArchive(_gz(_csv(STANDARD))))
        path = next(self.temp.rglob("*.csv.gz"))
        path.write_bytes(_gz(_csv(STANDARD[:-1])))
        with self.assertRaisesRegex(BybitPublicArchiveError, "checksum_mismatch"):
            verify_archive_file_v1(self.temp, manifest)

    def test_strict_parse_refuses_schema_order_and_unit_defects(self) -> None:
        def parse(text: str) -> list[object]:
            return list(iter_archive_trades_v1(io.StringIO(text), symbol="BTCUSDT", day=DAY))

        self.assertEqual(len(parse(_csv(STANDARD))), 6)
        with self.assertRaisesRegex(BybitPublicArchiveError, "header"):
            parse(_csv(STANDARD).replace("RPI", "rpi", 1))
        with self.assertRaisesRegex(BybitPublicArchiveError, "time_order"):
            parse(_csv([STANDARD[1], STANDARD[0]]))
        with self.assertRaisesRegex(BybitPublicArchiveError, "outside_its_utc_day"):
            parse(_csv([(86_400.0, "1", "0.001")]))
        self.assertEqual(parse(_csv([(f"{DAY_START}.2053", "1", "0.001")]))[0].trade_ts_micros,
                         DAY_START * 1_000_000 + 205_300)  # the real four-decimal format
        with self.assertRaisesRegex(BybitPublicArchiveError, "finer_than_microseconds"):
            parse(_csv([(f"{DAY_START}.0000005", "1", "0.001")]))
        text = _csv(STANDARD).splitlines()
        text[1] = text[1].replace(",0.001,0.1,0", ",0.002,0.1,0")  # homeNotional != size
        with self.assertRaisesRegex(BybitPublicArchiveError, "home_notional"):
            parse("\n".join(text) + "\n")

    def test_bars_are_event_time_with_no_knowledge_time_and_no_empty_minutes(self) -> None:
        trades = list(iter_archive_trades_v1(io.StringIO(_csv(STANDARD)), symbol="BTCUSDT", day=DAY))
        bars = build_archive_bars_v1(trades)
        self.assertEqual(len(bars), 3)  # minutes 0, 1, 3 -- minute 2 has no trade and no bar
        self.assertTrue(all(bar[2] is None for bar in bars))  # market_knowledge_at NULL (OR-5)
        minute_one = bars[1]
        self.assertEqual(str(minute_one[3]), "102.0")  # open by published row order
        self.assertEqual(minute_one[11], "true")  # same-instant open with two prices
        self.assertEqual(minute_one[13], 2)  # both trades print exactly on the boundary
        self.assertEqual(bars[0][13], 0)  # 59.999 s is 1 ms from the close: outside one 100 us step

    def test_dataset_identity_is_deterministic(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        manifest = acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=FakeArchive(_gz(_csv(STANDARD))))
        first = build_archive_dataset_v1(self.temp, [manifest], store=ResearchFrameStoreV1(self.temp / "a"))
        second = build_archive_dataset_v1(self.temp, [manifest], store=ResearchFrameStoreV1(self.temp / "b"))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.identity["market_knowledge_at"], "NULL_UNTIL_OR_5")

    def test_verification_rebuilds_the_identity_and_catches_a_swapped_file(self) -> None:
        from trade_platform.bybit_public_archive_v1 import verify_archive_dataset_v1
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        store = ResearchFrameStoreV1(self.temp / "a")
        manifest = acquire_archive_day_v1(self.temp, "BTCUSDT", DAY, fetch=FakeArchive(_gz(_csv(STANDARD))))
        dataset = build_archive_dataset_v1(self.temp, [manifest], store=store)
        rebuilt = verify_archive_dataset_v1(
            self.temp, dataset.identity, frame_manifests=dataset.frame_manifests, store=store
        )
        self.assertEqual(rebuilt.dataset_version_id, dataset.dataset_version_id)
        next(self.temp.rglob("*.csv.gz")).write_bytes(_gz(_csv(STANDARD[:-1])))
        with self.assertRaises(BybitPublicArchiveError):
            verify_archive_dataset_v1(
                self.temp, dataset.identity, frame_manifests=dataset.frame_manifests, store=store
            )


@unittest.skipUnless(__import__("os").environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ArchiveCatalogPostgresTests(unittest.TestCase):
    def test_catalog_is_append_only_and_refuses_any_lag(self) -> None:
        import os

        from alembic import command
        from alembic.config import Config

        from trade_platform.bybit_public_archive_v1 import PostgresPublicArchiveCatalogV1
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")
        temp = Path(tempfile.mkdtemp(prefix="r3b-pg-"))
        try:
            manifest = acquire_archive_day_v1(temp, "BTCUSDT", DAY, fetch=FakeArchive(_gz(_csv(STANDARD))))
            dataset = build_archive_dataset_v1(temp, [manifest], store=ResearchFrameStoreV1(temp / "s"))
            database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
            catalog = PostgresPublicArchiveCatalogV1(database)
            catalog.register(dataset)
            catalog.register(dataset)  # idempotent
            with (
                self.assertRaises(PersistenceError),
                database.transaction() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "UPDATE public_archive_datasets SET publication_lag_slot='0' "
                    "WHERE dataset_version_id=%s",
                    (dataset.dataset_version_id,),
                )
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
