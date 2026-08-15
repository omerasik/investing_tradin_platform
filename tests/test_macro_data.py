import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from trade_platform.macro_data import MacroDataError, MacroRelease, SQLiteMacroReleaseStore

UTC = timezone.utc
OBS_START = datetime(2025, 1, 1, tzinfo=UTC)
OBS_END = datetime(2025, 1, 31, 23, 59, tzinfo=UTC)
RELEASE = datetime(2025, 2, 7, 13, 30, tzinfo=UTC)


def release(actual: str, revision: int = 0, *, released_at: datetime = RELEASE, ingested_at: datetime | None = None) -> MacroRelease:
    return MacroRelease("US:CPI", OBS_START, OBS_END, released_at, ingested_at or released_at, Decimal(actual), "fixture-macro", "cpi-2025-01", revision, Decimal("0.3"), Decimal("0.2"), Decimal("0.1"), "macro-v1")


class MacroDataTests(unittest.TestCase):
    def test_as_of_query_excludes_unreleased_and_future_ingested_revisions(self) -> None:
        store = SQLiteMacroReleaseStore()
        original = release("0.4")
        revision_at = RELEASE + timedelta(days=30)
        revised = release("0.2", 1, released_at=revision_at)
        delayed_ingestion = release("0.1", 2, released_at=revision_at + timedelta(days=1), ingested_at=revision_at + timedelta(days=3))
        store.ingest([original, revised, delayed_ingestion])
        self.assertEqual([item.actual for item in store.available_as_of("US:CPI", RELEASE)], [Decimal("0.4")])
        self.assertEqual([item.actual for item in store.available_as_of("US:CPI", revision_at)], [Decimal("0.2")])
        self.assertEqual([item.actual for item in store.available_as_of("US:CPI", delayed_ingestion.ingested_at)], [Decimal("0.1")])

    def test_append_only_history_survives_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "macro.sqlite"
            store = SQLiteMacroReleaseStore(path)
            store.ingest([release("0.4"), release("0.2", 1, released_at=RELEASE + timedelta(days=30))])
            store.close()
            reopened = SQLiteMacroReleaseStore(path)
            try:
                self.assertEqual([item.actual for item in reopened.history("US:CPI", OBS_END)], [Decimal("0.4"), Decimal("0.2")])
            finally:
                reopened.close()

    def test_invalid_timestamps_and_duplicate_revisions_are_rejected(self) -> None:
        store = SQLiteMacroReleaseStore()
        with self.assertRaisesRegex(MacroDataError, "timestamp_semantics"):
            store.ingest([MacroRelease("US:CPI", OBS_START, OBS_END, OBS_END - timedelta(seconds=1), RELEASE, Decimal("1"), "fixture", "x")])
        store.ingest([release("0.4")])
        with self.assertRaisesRegex(MacroDataError, "duplicate"):
            store.ingest([release("0.5")])

