"""Private verified snapshot lifetime, exact parity and failure boundaries."""

from __future__ import annotations

import dataclasses
import gc
import json
import tempfile
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tests.test_strategy_lab_e2e_fixture import build_window_and_study
from trade_platform import strategy_lab_data_loading_v1 as loading
from trade_platform import strategy_sdk_v1 as sdk
from trade_platform.research_data_plane_v1 import ResearchDataPlaneError, ResearchFrameStoreV1
from trade_platform.strategy_lab_evaluation_scope_v1 import (
    EvaluationScopeError,
    VerifiedEvaluationScopeV1,
)
from trade_platform.strategy_lab_study_v1 import ParameterSpaceV1, SearchModeV1, SearchPlanV1


class VerifiedEvaluationScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evaluation-scope-")
        self.addCleanup(self.temp.cleanup)
        self.study, self.root = build_window_and_study(Path(self.temp.name), family="trend_ma_cross")
        self.binding, = self.study.datasets
        self.params = self.study.parameter_space.typed_point(next(self.study.trials()).parameters)
        loading.clear_bars_cache_v1()
        self.addCleanup(loading.clear_bars_cache_v1)

    def scope(self, **kw):
        return VerifiedEvaluationScopeV1(self.study, self.root, **kw)

    def object_path(self):
        from trade_platform.public_archive_research_bars_v1 import load_research_bar_dataset_v1

        store = ResearchFrameStoreV1(self.root)
        dataset = load_research_bar_dataset_v1(store, self.binding.dataset_version_id)
        return store.object_path(store.load_manifest(dataset.bar_frame_manifest_hash).objects[0])

    def test_one_full_verification_per_scope_and_standalone_hits_still_revalidate(self):
        original = ResearchFrameStoreV1.verify
        calls = []

        def counted(store, manifest):
            calls.append(manifest.manifest_hash)
            return original(store, manifest)

        with patch.object(ResearchFrameStoreV1, "verify", counted):
            for _ in range(2):
                with self.scope(maximum_evaluations=3) as active:
                    for _ in range(3):
                        active(self.study, self.params)
            self.assertEqual(len(calls), 2)
            for _ in range(2):
                loading.load_bars_v1(self.root, self.binding.dataset_version_id, expected_content_hash=self.binding.content_hash)
            self.assertEqual(len(calls), 4)

    def test_shared_arrays_have_immutable_backing_and_leave_no_scope_retention(self):
        bars = loading.load_bars_v1(self.root, self.binding.dataset_version_id, expected_content_hash=self.binding.content_hash)
        with self.scope() as active:
            for name, value in vars(bars).items():
                actual = getattr(active._bars, name)
                if isinstance(value, np.ndarray):
                    np.testing.assert_array_equal(value, actual)
                    with self.assertRaises(ValueError):
                        actual.setflags(write=True)
                else:
                    self.assertEqual(value, actual)
            reference = weakref.ref(active._bars.open_f)
            # The scope's proof identifies root, dataset, content and study.
            self.assertEqual(active.identity["root"], str(self.root.resolve()))
            self.assertEqual(active.identity["study_hash"], self.study.content_hash)
            self.assertEqual(active.identity["dataset_id"], str(self.binding.dataset_version_id))
        del actual
        gc.collect()
        self.assertIsNone(active._bars)
        self.assertIsNone(reference())

    def test_all_family_metrics_decimal_decisions_and_study_trial_hashes_match(self):
        for family in sdk.FAMILIES_V1.values():
            space = ParameterSpaceV1.of(*(dataclasses.replace(d, values=d.values[:2]) for d in family.parameter_space().domains))
            study = dataclasses.replace(self.study, strategy=family.spec(), parameter_space=space,
                                        search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality))
            trials = tuple(study.trials())
            identity = study.identity()
            reference = loading.BarStrategyEvaluatorV1(self.root)
            with VerifiedEvaluationScopeV1(study, self.root, maximum_evaluations=len(trials) + 1) as active:
                for trial in trials:
                    parameters = space.typed_point(trial.parameters)
                    self.assertEqual(reference(study, parameters), active(study, parameters))
                np.testing.assert_array_equal(family.targets_decimal(active._bars, parameters), family.targets_f64(active._bars, parameters)[0])
            self.assertEqual(study.identity(), identity)
            self.assertEqual(tuple(study.trials()), trials)

    def test_later_file_corruption_does_not_mutate_admitted_snapshot_but_blocks_new_scope(self):
        expected = loading.BarStrategyEvaluatorV1(self.root)(self.study, self.params)
        path = self.object_path()
        with self.scope(maximum_evaluations=2) as active:
            content = bytearray(path.read_bytes())
            content[len(content) // 2] ^= 1
            path.write_bytes(content)
            self.assertEqual(active(self.study, self.params), expected)
            with self.assertRaises(ResearchDataPlaneError), self.scope():
                pass
        with self.assertRaises(ResearchDataPlaneError):
            loading.BarStrategyEvaluatorV1(self.root)(self.study, self.params)

    def test_missing_root_and_conflicting_study_or_dataset_fail_without_cache_fallback(self):
        loading.load_bars_v1(self.root, self.binding.dataset_version_id, expected_content_hash=self.binding.content_hash)
        with self.assertRaises(ValueError), VerifiedEvaluationScopeV1(self.study, self.root / "missing"):
            pass
        wrong_binding = dataclasses.replace(self.binding, content_hash="0" * 64)
        wrong_study = dataclasses.replace(self.study, datasets=(wrong_binding,))
        with self.assertRaises(ValueError), VerifiedEvaluationScopeV1(wrong_study, self.root):
            pass
        with self.scope() as active:
            with self.assertRaisesRegex(EvaluationScopeError, "study"):
                active(wrong_study, self.params)
            self.assertIsNone(active._bars)

    def test_limit_timeout_cancellation_and_calculation_error_release_input(self):
        with self.scope(maximum_evaluations=1) as active:
            active(self.study, self.params)
            with self.assertRaises(EvaluationScopeError):
                active(self.study, self.params)
            self.assertIsNone(active._bars)
        clock = [0.0]
        with self.scope(maximum_seconds=1, monotonic=lambda: clock[0]) as active:
            clock[0] = 2
            with self.assertRaises(EvaluationScopeError):
                active(self.study, self.params)
            self.assertIsNone(active._bars)
        stop = threading.Event()
        with self.scope(should_stop=stop.is_set) as active:
            stop.set()
            with self.assertRaises(EvaluationScopeError):
                active(self.study, self.params)
            self.assertIsNone(active._bars)
        with self.scope() as active:
            with patch.object(type(sdk.FAMILIES_V1[self.study.strategy.family]), "targets_f64", side_effect=MemoryError("fixture")), \
                    self.assertRaises(MemoryError):
                active(self.study, self.params)
            self.assertIsNone(active._bars)

    def test_foreign_thread_access_is_rejected_but_explicit_cancellation_cleans_up(self):
        with self.scope() as active, ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(EvaluationScopeError, "owner"):
                pool.submit(active, self.study, self.params).result()
            active(self.study, self.params)
            pool.submit(active.cancel).result()
            self.assertIsNone(active._bars)
            with self.assertRaises(EvaluationScopeError):
                active(self.study, self.params)

    def test_independent_concurrent_scopes_each_verify_their_own_input(self):
        def evaluate():
            with self.scope() as active:
                return active(self.study, self.params)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: evaluate(), range(2)))
        self.assertEqual(results[0], results[1])

    def test_failed_worker_admission_preserves_exception_type_and_inadmissibility_precedence(self):
        catalog = self.root / "v1/datasets/public-archive-bars" / f"{self.binding.dataset_version_id}.json"
        catalog.write_text("{", encoding="utf-8")
        evaluator = loading.BarStrategyEvaluatorV1(self.root)
        invalid = {**self.params, "fast_bars": 120, "slow_bars": 60}
        expected = evaluator(self.study, invalid)
        with evaluator.batch_scope_v1(self.study, maximum_evaluations=3) as active:
            self.assertEqual(active(self.study, invalid), expected)
            for _ in range(2):
                with self.assertRaises(json.JSONDecodeError):
                    active(self.study, self.params)

    def test_retained_budget_and_cancellation_inside_calculation_leave_no_input(self):
        scope = self.scope(maximum_snapshot_bytes=1)
        with self.assertRaises(MemoryError), scope:
            pass
        self.assertIsNone(scope._bars)
        kind = type(sdk.FAMILIES_V1[self.study.strategy.family])
        original = kind.targets_f64
        with self.scope() as active:
            def cancelled(*args, **kw):
                active.cancel()
                return original(*args, **kw)

            with patch.object(kind, "targets_f64", cancelled), self.assertRaises(EvaluationScopeError):
                active(self.study, self.params)
            self.assertIsNone(active._bars)
