"""Phase R2B -- the columnar research data plane, without a database.

Every frame here is written to a temporary root; nothing touches the default
research-data root or the repository.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

_ANALYTICS = all(importlib.util.find_spec(name) for name in ("pyarrow", "duckdb"))

START = datetime(2025, 3, 1, 8, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def _reference_rows(count: int, *, instrument: str = "TEST:INSTRUMENT") -> list[tuple[Any, ...]]:
    rows = []
    for minute in range(count):
        event = START + MINUTE * minute
        for kind, price in (("INDEX_PRICE", Decimal("100") + minute), ("MARK_PRICE", Decimal("100.5") + minute)):
            rows.append(
                (instrument, kind, "PROVIDER", 0, f"n-{kind}-{minute:06d}", f"r-{kind}-{minute:06d}",
                 event, event, None, event, event + timedelta(days=1), price, "USDT")
            )
    # The frame's declared sort key: instrument, kind, event, provider, revision, raw id.
    return sorted(rows, key=lambda row: (row[0], row[1], row[6], row[2], row[3], row[5]))


@unittest.skipUnless(_ANALYTICS, "analytics extra (pyarrow, duckdb) not installed")
class ResearchFrameStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        self.root = Path(tempfile.mkdtemp(prefix="r2b-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = ResearchFrameStoreV1(self.root)

    def _write(self, rows: list[tuple[Any, ...]], **kwargs: Any) -> Any:
        from trade_platform.research_data_plane_v1 import REFERENCE_PRICE_FRAME

        return self.store.write_frame(REFERENCE_PRICE_FRAME, rows, lineage={"test": True}, **kwargs)

    def test_repeat_write_is_byte_and_content_identical(self) -> None:
        first = self._write(_reference_rows(50))
        second = self._write(_reference_rows(50))
        self.assertEqual(first.manifest_hash, second.manifest_hash)
        self.assertEqual(first.logical_content_hash, second.logical_content_hash)
        self.assertEqual([o.sha256 for o in first.objects], [o.sha256 for o in second.objects])
        self.assertEqual(100, first.row_count)

    def test_logical_identity_does_not_depend_on_file_layout(self) -> None:
        whole = self._write(_reference_rows(50))
        split = self._write(_reference_rows(50), rows_per_object=30)
        self.assertGreater(len(split.objects), len(whole.objects))
        self.assertEqual(whole.logical_content_hash, split.logical_content_hash)
        self.assertNotEqual(whole.manifest_hash, split.manifest_hash)
        self.store.verify(split)

    def test_rows_round_trip_exactly(self) -> None:
        rows = _reference_rows(5)
        manifest = self._write(rows)
        read = list(self.store.iter_rows(manifest))
        self.assertEqual(len(rows), len(read))
        self.assertEqual([row[11] for row in rows], [row[11] for row in read])
        self.assertEqual([row[6] for row in rows], [row[6] for row in read])
        mark = next(row for row in read if row[1] == "MARK_PRICE")
        self.assertEqual("100.500000000000000000", str(mark[11]))
        self.assertTrue(all(row[8] is None for row in read))  # market knowledge stays undefined

    def test_a_flipped_byte_is_detected(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchDataPlaneError

        manifest = self._write(_reference_rows(20))
        self.store.verify(manifest)
        path = self.store.object_path(manifest.objects[0])
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0x01
        path.write_bytes(bytes(data))
        with self.assertRaisesRegex(ResearchDataPlaneError, "corrupt"):
            self.store.verify(manifest)

    def test_a_missing_object_or_edited_manifest_is_detected(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchDataPlaneError

        manifest = self._write(_reference_rows(20))
        with self.assertRaisesRegex(ResearchDataPlaneError, "integrity"):
            self.store.verify(replace(manifest, row_count=manifest.row_count + 1))
        self.store.object_path(manifest.objects[0]).unlink()
        with self.assertRaisesRegex(ResearchDataPlaneError, "missing"):
            self.store.verify(manifest)

    def test_values_beyond_the_frame_scale_fail_closed(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchDataPlaneError

        rows = _reference_rows(2)
        rows[0] = (*rows[0][:11], Decimal("1.0000000000000000001"), "USDT")
        with self.assertRaisesRegex(ResearchDataPlaneError, "exceeds_frame_scale"):
            self._write(rows)

    def test_nulls_and_unsorted_rows_fail_closed(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchDataPlaneError

        rows = _reference_rows(3)
        with self.assertRaisesRegex(ResearchDataPlaneError, "null_in_non_nullable"):
            self._write([(*rows[0][:11], None, "USDT")])
        with self.assertRaisesRegex(ResearchDataPlaneError, "sort_key"):
            self._write(list(reversed(rows)))

    def test_duckdb_reads_exactly_the_frame(self) -> None:
        import duckdb

        manifest = self._write(_reference_rows(40), rows_per_object=25)
        count, total = duckdb.sql(
            f"SELECT count(*), sum(price) FROM {self.store.duckdb_relation_sql(manifest)}"
        ).fetchone()  # type: ignore[misc]
        self.assertEqual(80, count)
        expected = sum((row[11] for row in _reference_rows(40)), Decimal(0))
        self.assertEqual(expected, total)


@unittest.skipUnless(_ANALYTICS, "analytics extra (pyarrow, duckdb) not installed")
class BasisFeatureFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

        self.root = Path(tempfile.mkdtemp(prefix="r2b-basis-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = ResearchFrameStoreV1(self.root)

    def _reference(self, rows: list[tuple[Any, ...]]) -> Any:
        from trade_platform.research_data_plane_v1 import REFERENCE_PRICE_FRAME

        return self.store.write_frame(REFERENCE_PRICE_FRAME, rows, lineage={"dataset_version_id": None})

    def _basis(self, reference: Any, **kwargs: Any) -> Any:
        from trade_platform.research_data_export_v1 import build_mark_index_basis_frame_v1

        return build_mark_index_basis_frame_v1(
            self.store, reference, semantic_version="1.0.0",
            calculation_version="derivatives-crypto-mark-index-basis-3j1c-v1",
            eligible_instrument=kwargs.get("eligible", lambda instrument: True),
        )

    def test_values_are_decimal_exact_and_quantized_half_even(self) -> None:
        # (mark - index) / index = 5E-13 exactly: half-even gives 0, while
        # DuckDB's DECIMAL division returns a DOUBLE and rounds half away.
        rows = [
            ("TEST:I", "INDEX_PRICE", "P", 0, "n1", "r1", START, START, None, START, START,
             Decimal("1"), "USDT"),
            ("TEST:I", "MARK_PRICE", "P", 0, "n2", "r2", START, START, None, START, START,
             Decimal("1.0000000000005"), "USDT"),
        ]
        frame, cache_hit = self._basis(self._reference(rows))
        self.assertFalse(cache_hit)
        (row,) = list(self.store.iter_rows(frame))
        self.assertEqual(Decimal("0E-12"), row[4])
        self.assertEqual(
            ((Decimal("1.0000000000005") - 1) / 1).quantize(Decimal("1E-12")), row[4]
        )

    def test_basis_matches_the_reference_formula_and_caches(self) -> None:
        reference = self._reference(_reference_rows(30))
        frame, hit = self._basis(reference)
        rows = list(self.store.iter_rows(frame))
        self.assertEqual(30, len(rows))
        for minute, row in enumerate(rows):
            mark, index = Decimal("100.5") + minute, Decimal("100") + minute
            self.assertEqual(((mark - index) / index).quantize(Decimal("1E-12")), row[4])
            self.assertIsNone(row[3])
        again, hit = self._basis(reference)
        self.assertTrue(hit)
        self.assertEqual(frame.manifest_hash, again.manifest_hash)

    def test_latest_revision_wins_and_ambiguity_fails_closed(self) -> None:
        from trade_platform.research_data_plane_v1 import ResearchDataPlaneError

        rows = _reference_rows(2)
        mark = next(row for row in rows if row[1] == "MARK_PRICE" and row[6] == START)
        revised = (*mark[:3], 1, "n-rev", "r-rev", *mark[6:11], Decimal("200"), "USDT")

        def ordered(items: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
            return sorted(items, key=lambda r: (r[0], r[1], r[6], r[2], r[3], r[5]))

        frame, _ = self._basis(self._reference(ordered([*rows, revised])))
        first = next(iter(self.store.iter_rows(frame)))
        self.assertEqual(((Decimal("200") - 100) / 100).quantize(Decimal("1E-12")), first[4])
        other = (mark[0], mark[1], "OTHER", *mark[3:])
        with self.assertRaisesRegex(ResearchDataPlaneError, "ambiguous"):
            self._basis(self._reference(ordered([*rows, other])))

    def test_ineligible_instruments_produce_nothing(self) -> None:
        frame, _ = self._basis(self._reference(_reference_rows(3)), eligible=lambda instrument: False)
        self.assertEqual(0, frame.row_count)

    def test_cache_key_moves_with_any_input(self) -> None:
        from trade_platform.research_data_plane_v1 import feature_frame_cache_key_v1

        one = self._reference(_reference_rows(3))
        two = self._reference(_reference_rows(4))

        def key(manifest: Any, **overrides: Any) -> str:
            values: dict[str, Any] = {
                "feature_name": "f", "semantic_version": "1", "calculation_version": "c",
                "input_manifests": (manifest,), "parameters": {"a": 1},
                "implementation_version": "i",
            }
            values.update(overrides)
            return feature_frame_cache_key_v1(**values)

        self.assertEqual(key(one), key(one))
        self.assertNotEqual(key(one), key(two))
        self.assertNotEqual(key(one), key(one, parameters={"a": 2}))
        self.assertNotEqual(key(one), key(one, implementation_version="j"))


if __name__ == "__main__":
    unittest.main()
