"""Exact Arrow/SDK parity and fail-closed, bounded Strategy Lab cache behavior."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from tests.test_strategy_lab_walk_forward_v1 import RULE, _plan
from trade_platform import strategy_lab_data_loading_v1 as loading
from trade_platform import strategy_sdk_v1 as sdk
from trade_platform.public_archive_research_bars_v1 import ResearchBarsError
from trade_platform.research_data_plane_v1 import (
    FRAME_SCHEMAS,
    ROW_GROUP_ROWS,
    ResearchDataPlaneError,
    ResearchFrameStoreV1,
    _python_columns_v1,
)
from trade_platform.strategy_lab_study_v1 import ParameterSpaceV1, SearchModeV1, SearchPlanV1
from trade_platform.strategy_lab_walk_forward_v1 import run_walk_forward_v1


def reference_rows(store: ResearchFrameStoreV1, manifest: object):
    """Unchanged pre-P2A reader, retained as the independent conversion oracle."""
    frame = FRAME_SCHEMAS.get(manifest.frame_kind)
    if frame is None or frame.fingerprint() != manifest.frame_schema_fingerprint:
        raise ResearchDataPlaneError("frame_schema_not_recognised")
    for item in manifest.objects:
        parquet = pq.ParquetFile(str(store.object_path(item)))
        if not parquet.schema_arrow.equals(frame.schema):
            raise ResearchDataPlaneError("parquet_schema_differs_from_frame_schema")
        for batch in parquet.iter_batches(batch_size=ROW_GROUP_ROWS):
            yield from zip(*(column.to_pylist() for column in batch.columns), strict=True)


class DecimalMaterializationTests(unittest.TestCase):
    def test_nulls_scales_and_extreme_exact_values_match_original_columns(self) -> None:
        values = [Decimal("-99999999999999999999.123456789012345678"), None,
                  Decimal("0.000000000000000001"), Decimal("1.230000000000000000")] * 8
        columns = [pa.array(values, type=pa.decimal128(38, 18)),
                   pa.array(values, type=pa.decimal128(38, 18)),
                   pa.array([Decimal("1.2345"), None] * 16, type=pa.decimal128(12, 4)),
                   pa.array(["a", None] * 16)]
        batch = pa.RecordBatch.from_arrays(columns, names=["open", "close", "other_scale", "text"])
        result = _python_columns_v1(batch)
        self.assertEqual(result, [column.to_pylist() for column in columns])
        for index in (0, 2, 3):
            self.assertEqual(result[0][index].as_tuple(), values[index].as_tuple())
            self.assertIs(result[0][index], result[1][index])

    def test_high_cardinality_and_non_decimal_batches_keep_exact_values(self) -> None:
        for batch in (
            pa.record_batch([pa.array([Decimal(i) / 100 for i in range(100)], type=pa.decimal128(38, 18))],
                            names=["value"]),
            pa.record_batch([pa.array(["a", None, "b"]), pa.array([1, 2, 3])], names=["text", "count"]),
        ):
            self.assertEqual(_python_columns_v1(batch), [column.to_pylist() for column in batch.columns])


class VerifiedLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="verified-loading-")
        self.root = Path(self.temp.name)
        self.study, self.data = build_window_and_study(self.root, family="trend_ma_cross")
        self.binding, = self.study.datasets
        self.store = ResearchFrameStoreV1(self.data)
        self.catalog = self.store.root / "datasets/public-archive-bars" / f"{self.binding.dataset_version_id}.json"
        self.payload = json.loads(self.catalog.read_text(encoding="utf-8"))
        self.manifest = self.store.load_manifest(self.payload["bar_frame_manifest_hash"])
        loading.clear_bars_cache_v1()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(loading.clear_bars_cache_v1)

    def load(self, root: Path | None = None):
        return loading.load_bars_v1(root or self.data, self.binding.dataset_version_id,
                                    expected_content_hash=self.binding.content_hash)

    def test_cold_and_warm_loads_reverify_and_preserve_reference_bars_and_hashes(self) -> None:
        with patch.object(ResearchFrameStoreV1, "iter_rows", new=reference_rows):
            reference = sdk.load_bars_v1(self.data, self.binding.dataset_version_id,
                                         expected_content_hash=self.binding.content_hash)
        loading.clear_bars_cache_v1()
        verify = ResearchFrameStoreV1.verify
        verified = []

        def counted(store, manifest):
            verified.append(manifest.manifest_hash)
            return verify(store, manifest)

        with patch.object(ResearchFrameStoreV1, "verify", new=counted):
            optimized = self.load()
            self.assertIs(optimized, self.load())
        self.assertEqual(verified, [self.manifest.manifest_hash] * 2)
        for name, expected in vars(reference).items():
            actual = getattr(optimized, name)
            if isinstance(expected, np.ndarray):
                np.testing.assert_array_equal(expected, actual)
                self.assertFalse(actual.flags.writeable)
            elif isinstance(expected, tuple):
                self.assertEqual([v.as_tuple() for v in expected], [v.as_tuple() for v in actual])
            else:
                self.assertEqual(expected, actual)
        self.store.verify(self.manifest)
        self.assertEqual(self.payload, json.loads(self.catalog.read_text(encoding="utf-8")))

    def test_warm_cache_refuses_changed_object_and_discards_stale_bars(self) -> None:
        self.load()
        path = self.store.object_path(self.manifest.objects[0])
        content = bytearray(path.read_bytes())
        content[len(content) // 2] ^= 1
        path.write_bytes(content)
        with self.assertRaisesRegex(ResearchDataPlaneError, "frame_object_corrupt"):
            self.load()
        self.assertFalse(sdk._BARS_CACHE)

    def test_warm_cache_refuses_changed_catalog_manifest_and_missing_object(self) -> None:
        self.load()
        self.catalog.write_text(json.dumps({**self.payload, "content_hash": "0" * 64}), encoding="utf-8")
        with self.assertRaisesRegex(ResearchBarsError, "research_bar_dataset_identity_mismatch"):
            self.load()
        self.catalog.write_text(json.dumps(self.payload), encoding="utf-8")
        self.load()
        manifest_path = self.store.manifest_path(self.manifest.manifest_hash)
        original = manifest_path.read_text(encoding="utf-8")
        payload = json.loads(original)
        payload["row_count"] += 1
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ResearchDataPlaneError, "frame_manifest_hash_mismatch"):
            self.load()
        manifest_path.write_text(original, encoding="utf-8")
        self.load()
        self.store.object_path(self.manifest.objects[0]).unlink()
        with self.assertRaisesRegex(ResearchDataPlaneError, "frame_object_missing"):
            self.load()

    def test_cache_does_not_cross_roots_and_over_budget_inputs_are_not_retained(self) -> None:
        self.load()
        with self.assertRaises(ResearchBarsError):
            self.load(self.root / "missing-root")
        self.assertFalse(sdk._BARS_CACHE)
        with patch.object(loading, "_CACHE_BYTES_V1", 1):
            bars = self.load()
            self.assertEqual(bars.size, 2880)
            self.assertFalse(sdk._BARS_CACHE)

    def test_cache_retains_at_most_one_dataset(self) -> None:
        self.load()
        other, root = build_window_and_study(self.root, family="trend_ma_cross", days=1)
        binding, = other.datasets
        loading.load_bars_v1(root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
        self.assertEqual([(str(binding.dataset_version_id), binding.content_hash)], list(sdk._BARS_CACHE))

    def test_existing_evaluator_decimal_and_walk_forward_reports_keep_exact_identities(self) -> None:
        for family in sdk.FAMILIES_V1.values():
            space = ParameterSpaceV1.of(*(dataclasses.replace(d, values=d.values[:2])
                                         for d in family.parameter_space().domains))
            study = dataclasses.replace(self.study, strategy=family.spec(), parameter_space=space,
                search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality))
            trial = next(t for t in study.trials()
                         if family.inadmissible(space.typed_point(t.parameters)) is None)
            params = space.typed_point(trial.parameters)
            original_identity = (study.content_hash, tuple(study.trials()))
            loading.clear_bars_cache_v1()
            with patch.object(ResearchFrameStoreV1, "iter_rows", new=reference_rows):
                original = sdk.load_bars_v1(self.data, self.binding.dataset_version_id,
                                            expected_content_hash=self.binding.content_hash)
                reference = sdk.BarStrategyEvaluatorV1(self.data)(study, params)
                report = run_walk_forward_v1(study, _plan(), RULE, bars=original)
            loading.clear_bars_cache_v1()
            optimized = self.load()
            with self.subTest(family=family.family):
                self.assertEqual(reference, loading.BarStrategyEvaluatorV1(self.data)(study, params))
                self.assertEqual(family.targets_decimal(original, params), family.targets_decimal(optimized, params))
                self.assertEqual(report.identity, run_walk_forward_v1(study, _plan(), RULE, bars=optimized).identity)
                self.assertEqual(original_identity, (study.content_hash, tuple(study.trials())))


if __name__ == "__main__":
    unittest.main()
