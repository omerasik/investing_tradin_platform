"""Phase R4.D -- T2 research bars and OR-1 retention, offline (fixture archive files only)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tests.test_bybit_public_archive_v1 import STANDARD, FakeArchive, _csv, _gz
from trade_platform.bybit_public_archive_v1 import HttpResponseV1, build_archive_dataset_v1
from trade_platform.public_archive_research_bars_v1 import (
    EVICTED,
    NOT_PUBLISHED,
    PINNED,
    REJECTED,
    UNVERIFIABLE,
    ResearchBarsError,
    acquire_and_derive_day_v1,
    archive_day_status_v1,
    build_research_bar_dataset_v1,
    contiguous_derived_spans_v1,
    dataset_file_manifests_v1,
    evict_archive_raw_v1,
    list_research_bar_datasets_v1,
    load_file_manifest_v1,
    load_research_bar_dataset_v1,
    pin_archive_raw_v1,
    restore_archive_raw_v1,
    retention_state_v1,
)
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

#: The archive fixtures are stamped on 2026-09-20; research days sit before the holdout.
FIXTURE_DAY = date(2026, 9, 20)
DAY = date(2026, 7, 20)
NOW = datetime(2026, 10, 8, tzinfo=UTC)


class DayArchive:
    """Serves a fixture file per day (shifted onto that day), 404 for days in ``missing``."""

    def __init__(self, *, missing: frozenset[date] = frozenset(), tamper: bool = False,
                 duplicate: frozenset[date] = frozenset()) -> None:
        self.missing = missing
        self.tamper = tamper
        self.duplicate = duplicate
        self.calls = 0

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponseV1:
        self.calls += 1
        stamp = url.rsplit("BTCUSDT", 1)[1].removesuffix(".csv.gz")
        day = date.fromisoformat(stamp)
        if day in self.missing:
            return HttpResponseV1(404, {}, b"")
        shift = (day - FIXTURE_DAY).days * 86_400
        rows = [(offset + shift, price, size) for offset, price, size in STANDARD]
        if self.tamper:
            rows = [*rows, (300 + shift, "105.0", "0.001")]
        text = _csv(rows)
        if day in self.duplicate:
            text = text.replace("id-000003", "id-000002")
        return FakeArchive(_gz(text))(url, headers)


class ResearchBarsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="research-bars-"))
        self.root = self.temp / "archive"
        self.store = ResearchFrameStoreV1(self.temp / "data")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _step(self, day: date, fetch: DayArchive, *, evict: bool = True):
        return acquire_and_derive_day_v1(self.root, "BTCUSDT", day, store=self.store, evict=evict,
                                         fetch=fetch, now=lambda: NOW)

    def test_day_bars_equal_the_unchanged_archive_bars_and_raw_is_evicted(self) -> None:
        step = self._step(DAY, DayArchive())
        self.assertEqual((EVICTED, True), (step.state, step.evicted))
        manifest = load_file_manifest_v1(self.root, "BTCUSDT", DAY)
        self.assertEqual([], list(self.root.rglob("*.csv.gz")))
        # Same bars as the R3B full dataset over the same file (built from a fresh copy).
        other = self.temp / "archive-full"
        fetch = DayArchive()
        from trade_platform.bybit_public_archive_v1 import acquire_archive_day_v1

        full_manifest = acquire_archive_day_v1(other, "BTCUSDT", DAY, fetch=fetch)
        self.assertEqual(manifest.identity(), full_manifest.identity())
        full = build_archive_dataset_v1(other, [full_manifest], store=ResearchFrameStoreV1(self.temp / "d2"))
        window = build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, DAY, store=self.store)
        self.assertEqual(full.identity["frames"]["T2_ARCHIVE_OHLCV_1M"]["logical_content_hash"],
                         window.identity["bar_frame"]["logical_content_hash"])
        # Idempotent after eviction: nothing is re-downloaded.
        counting = DayArchive()
        self.assertEqual(EVICTED, self._step(DAY, counting).state)
        self.assertEqual(0, counting.calls)

    def test_a_missing_day_is_a_declared_gap_and_an_underived_day_is_refused(self) -> None:
        second = DAY + timedelta(days=1)
        third = DAY + timedelta(days=2)
        fetch = DayArchive(missing=frozenset({second}))
        for day in (DAY, second, third):
            self._step(day, fetch)
        self.assertEqual(NOT_PUBLISHED, self._step(second, fetch).state)
        window = build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, third, store=self.store)
        self.assertEqual([second.isoformat()], window.identity["not_published_days"])
        self.assertEqual([{"after": DAY.isoformat(), "before": third.isoformat()}], window.identity["day_gaps"])
        with self.assertRaisesRegex(ResearchBarsError, "window_day_not_derived"):
            build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, third + timedelta(days=1), store=self.store)

    def test_a_rejected_day_is_never_derived_bridged_or_refetched(self) -> None:
        second, third, fourth = (DAY + timedelta(days=n) for n in (1, 2, 3))
        fetch = DayArchive(duplicate=frozenset({second}))
        states = [self._step(day, fetch).state for day in (DAY, second, third, fourth)]
        self.assertEqual([EVICTED, REJECTED, EVICTED, EVICTED], states)  # acquisition moved on
        self.assertEqual(REJECTED, archive_day_status_v1(self.root, "BTCUSDT", second))
        counting = DayArchive()
        self.assertEqual(REJECTED, self._step(second, counting).state)
        self.assertEqual(0, counting.calls)
        # A window that needs the day is refused, with the reason, unless the gap is admitted.
        with self.assertRaisesRegex(ResearchBarsError, "window_requires_a_rejected_day:.*archive_trade_id_repeated"):
            build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, fourth, store=self.store)
        admitted = build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, fourth, store=self.store,
                                                 admit_rejected_days=True)
        (rejected,) = admitted.identity["rejected_days"]
        self.assertEqual((second.isoformat(), "archive_trade_id_repeated"), (rejected["utc_day"], rejected["reason"]))
        self.assertEqual([{"after": DAY.isoformat(), "before": third.isoformat()}], admitted.identity["day_gaps"])
        self.assertEqual(3, len(admitted.identity["files"]))
        days_in_frame = {row[0].date() for row in self.store.iter_rows(self.store.load_manifest(
            admitted.bar_frame_manifest_hash))}
        self.assertNotIn(second, days_in_frame)  # no row invented for the rejected day
        # Any continuity requirement fails on it.
        with self.assertRaisesRegex(ResearchBarsError, f"window_not_continuous:BTCUSDT:{second.isoformat()}"):
            build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, fourth, store=self.store,
                                          admit_rejected_days=True, require_continuous=True)
        # A window on either side proceeds with its exact bounds, without the key.
        after = build_research_bar_dataset_v1(self.root, "BTCUSDT", third, fourth, store=self.store,
                                              require_continuous=True)
        self.assertNotIn("rejected_days", after.identity)
        self.assertEqual(
            [{"first_utc_day": DAY.isoformat(), "last_utc_day": DAY.isoformat(), "days": 1},
             {"first_utc_day": third.isoformat(), "last_utc_day": fourth.isoformat(), "days": 2}],
            contiguous_derived_spans_v1(self.root, "BTCUSDT", DAY, fourth),
        )

    def test_window_identity_is_deterministic_and_reloads_with_proof(self) -> None:
        fetch = DayArchive()
        for offset in range(3):
            self._step(DAY + timedelta(days=offset), fetch)
        first = build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, DAY + timedelta(days=2), store=self.store)
        again = build_research_bar_dataset_v1(self.root, "BTCUSDT", DAY, DAY + timedelta(days=2), store=self.store)
        self.assertEqual(first.dataset_version_id, again.dataset_version_id)
        loaded = load_research_bar_dataset_v1(self.store, first.dataset_version_id)
        self.assertEqual(first.content_hash, loaded.content_hash)
        self.assertEqual(3, len(dataset_file_manifests_v1(self.root, loaded)))
        self.assertEqual("NULL_IN_FRAME_APPLIED_BY_DECLARED_TIMING_POLICY", loaded.identity["market_knowledge_at"])
        self.assertEqual(1, len(list_research_bar_datasets_v1(self.store)))
        bound = loaded.knowledge_upper_bound_exclusive(timedelta(seconds=60))
        self.assertGreater(bound, loaded.last_bar_close_at + timedelta(seconds=60))

    def test_the_untouched_holdout_is_never_acquired_or_windowed(self) -> None:
        fetch = DayArchive()
        with self.assertRaisesRegex(ResearchBarsError, "untouched_holdout"):
            self._step(date(2026, 8, 20), fetch)
        self.assertEqual(0, fetch.calls)
        with self.assertRaisesRegex(ResearchBarsError, "untouched_holdout"):
            build_research_bar_dataset_v1(self.root, "BTCUSDT", date(2026, 8, 19), date(2026, 8, 20),
                                          store=self.store)

    def test_restore_is_byte_identical_or_marks_the_file_unverifiable(self) -> None:
        self._step(DAY, DayArchive())
        manifest = load_file_manifest_v1(self.root, "BTCUSDT", DAY)
        path = restore_archive_raw_v1(self.root, manifest, fetch=DayArchive(), now=lambda: NOW)
        self.assertTrue(path.exists())
        evict_archive_raw_v1(self.root, manifest, store=self.store, now=lambda: NOW)
        with self.assertRaisesRegex(ResearchBarsError, "publisher_bytes_changed"):
            restore_archive_raw_v1(self.root, manifest, fetch=DayArchive(tamper=True), now=lambda: NOW)
        self.assertEqual(UNVERIFIABLE, retention_state_v1(self.root, "BTCUSDT", DAY))
        # Fail closed from then on: no second chance with the original bytes.
        with self.assertRaisesRegex(ResearchBarsError, "unverifiable"):
            restore_archive_raw_v1(self.root, manifest, fetch=DayArchive(), now=lambda: NOW)

    def test_a_pinned_file_is_restored_and_never_evicted(self) -> None:
        self._step(DAY, DayArchive())
        manifest = load_file_manifest_v1(self.root, "BTCUSDT", DAY)
        pin_archive_raw_v1(self.root, manifest, artifact="candidate-set:abc", fetch=DayArchive(), now=lambda: NOW)
        self.assertEqual(PINNED, retention_state_v1(self.root, "BTCUSDT", DAY))
        self.assertFalse(evict_archive_raw_v1(self.root, manifest, store=self.store, now=lambda: NOW))
        self.assertEqual(PINNED, retention_state_v1(self.root, "BTCUSDT", DAY))

    def test_eviction_requires_the_derivation_of_exactly_this_file(self) -> None:
        self._step(DAY, DayArchive(), evict=False)
        manifest = load_file_manifest_v1(self.root, "BTCUSDT", DAY)
        record = self.root.rglob("*.day-bars.json").__next__()
        record.unlink()
        with self.assertRaisesRegex(ResearchBarsError, "verified_derivation"):
            evict_archive_raw_v1(self.root, manifest, store=self.store, now=lambda: NOW)


if __name__ == "__main__":
    unittest.main()
