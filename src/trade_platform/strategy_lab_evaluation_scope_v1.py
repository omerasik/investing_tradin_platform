"""Private, bounded evaluation input from the existing full dataset verifier.

This is a snapshot lifetime, not a persistent-evidence cache. Every entry invokes
the original catalog/manifest/object-byte/logical-row verifier. The rows retained
for SDK conversion are the very rows that verifier hashes: there is no unverified
second Parquet read. No existing verifier, standalone loader, SDK implementation,
study identity, numerical rule or evidence authority is replaced.

Later persistent file changes cannot mutate admitted bytes-backed arrays. They
are detected by the next independent scope/standalone load, not retroactively by
an active snapshot. Reuse ends on context exit, error, cancellation, task budget
or cooperative wall deadline. External process limits remain necessary for hard
preemption and total process memory; the retained-input budget is not total RAM.
Scopes are process/thread-owned and unpicklable; explicit cancel is thread-safe.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Self

import numpy as np

from . import strategy_lab_walk_forward_v1 as execution
from . import strategy_sdk_v1 as sdk
from .evidence_tier_authority_v1 import EvidenceTierV1
from .public_archive_research_bars_v1 import ResearchBarsError, load_research_bar_dataset_v1
from .research_data_plane_v1 import (
    T2_ARCHIVE_OHLCV_1M_FRAME,
    FrameManifestV1,
    ResearchFrameStoreV1,
    default_research_data_root,
)
from .strategy_lab_data_loading_v1 import _retained_upper_bound_v1
from .strategy_lab_ledger_v1 import TrialOutcomeV1
from .strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1, StudySpecV1
from .strategy_lab_worker_v1 import TrialEvaluationV1


class EvaluationScopeError(ValueError):
    pass


def _family(study: StudySpecV1, parameters: Mapping[str, Any] | None = None):
    family = sdk.FAMILIES_V1.get(study.strategy.family)
    if family is None or family.spec() != study.strategy:
        raise sdk.StrategySdkError("study_strategy_is_not_this_sdk_family_version")
    # Keep the SDK's parameter inadmissibility before its missing-timing error.
    reason = family.inadmissible(parameters) if parameters is not None else None
    if reason is None and study.timing_lag is None:
        raise sdk.StrategySdkError("t2_study_without_the_or_5_timing_policy")
    return family, reason


class _VerifiedRows(ResearchFrameStoreV1):
    """Observe the existing verifier's row stream; never substitute its checks."""

    def __init__(self, root: Path, maximum_rows: int, check: Callable[[], None]) -> None:
        super().__init__(root)
        self.rows: list[tuple[Any, ...]] = []
        self.maximum_rows, self.check = maximum_rows, check
        self.manifest: FrameManifestV1 | None = None
        self.manifest_bytes: bytes | None = None

    def load_manifest(self, manifest_hash: str) -> FrameManifestV1:
        path = self.manifest_path(manifest_hash)
        manifest = super().load_manifest(manifest_hash)
        original = path.read_bytes()
        if manifest != super().load_manifest(manifest_hash) or original != path.read_bytes():
            raise EvaluationScopeError("manifest_changed_during_scope_admission")
        self.manifest_bytes = original
        return manifest

    def iter_rows(self, manifest: FrameManifestV1) -> Iterator[tuple[Any, ...]]:
        if manifest.frame_kind != T2_ARCHIVE_OHLCV_1M_FRAME.kind or manifest.row_count > self.maximum_rows:
            raise EvaluationScopeError("scope_frame_or_row_budget_not_admissible")
        self.manifest = manifest
        for row in super().iter_rows(manifest):
            if len(self.rows) % 512 == 0:
                self.check()
            if len(self.rows) >= self.maximum_rows:
                raise EvaluationScopeError("scope_row_budget_exhausted")
            # Full rows still reach the original logical hasher. Only the SDK's
            # seven input fields are retained, without a duplicate full dataset.
            self.rows.append(tuple(row[:7]))
            yield row


def _immutable_bars(bars: sdk.BarsV1) -> sdk.BarsV1:
    arrays: dict[str, Any] = {}
    for name, value in vars(bars).items():
        if isinstance(value, np.ndarray):
            if value.dtype.hasobject:
                raise EvaluationScopeError("scope_object_array_not_admissible")
            # setflags(write=False) on an owning ndarray is reversible. Bytes
            # backing makes every view permanently read-only without an exposed
            # mutable buffer; Decimal tuples/scalars are already immutable.
            arrays[name] = np.frombuffer(value.tobytes(order="C"), dtype=value.dtype).reshape(value.shape)
    return dataclasses.replace(bars, **arrays)


class VerifiedEvaluationScopeV1:
    def __init__(
        self, study: StudySpecV1, data_root: Path | None, *, maximum_evaluations: int = 8,
        maximum_seconds: float = 120.0, maximum_rows: int = 1_000_000,
        maximum_snapshot_bytes: int = 512 * 1024 ** 2,
        should_stop: Callable[[], bool] | None = None, monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        for value, ceiling in ((maximum_evaluations, 1000), (maximum_rows, 1_000_000),
                               (maximum_snapshot_bytes, 512 * 1024 ** 2)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= ceiling:
                raise EvaluationScopeError("invalid_scope_resource_budget")
        if isinstance(maximum_seconds, bool) or not 0 < maximum_seconds <= 120:
            raise EvaluationScopeError("invalid_scope_wall_budget")
        self._study_hash = study.content_hash
        self._study = study
        self._root = Path(data_root or default_research_data_root()).resolve()
        self._maximum_evaluations, self._maximum_seconds = maximum_evaluations, maximum_seconds
        self._maximum_rows, self._maximum_snapshot_bytes = maximum_rows, maximum_snapshot_bytes
        self._stop, self._clock = should_stop or (lambda: False), monotonic
        self._lock, self._cancelled = threading.RLock(), threading.Event()
        self._owner_pid: int | None = None
        self._owner_thread: threading.Thread | None = None
        self._entered = self._closed = False
        self._deadline = 0.0
        self._used = 0
        self._bars: sdk.BarsV1 | None = None
        self._schedule: np.ndarray | None = None
        self._identity: Mapping[str, Any] = MappingProxyType({})

    @property
    def identity(self) -> Mapping[str, Any]:
        return self._identity

    def _owner(self) -> None:
        if os.getpid() != self._owner_pid or threading.current_thread() is not self._owner_thread:
            raise EvaluationScopeError("scope_owner_process_or_thread_mismatch")

    def _check(self) -> None:
        self._owner()
        if self._cancelled.is_set() or self._stop():
            raise EvaluationScopeError("scope_cancelled")
        if self._closed or not self._entered:
            raise EvaluationScopeError("scope_not_active")
        if self._clock() >= self._deadline:
            raise EvaluationScopeError("scope_wall_budget_exhausted")
        if self._study.content_hash != self._study_hash:
            raise EvaluationScopeError("scope_study_identity_changed")

    def __enter__(self) -> Self:
        if self._entered or self._closed:
            raise EvaluationScopeError("scope_cannot_be_reentered")
        self._owner_pid, self._owner_thread = os.getpid(), threading.current_thread()
        self._entered = True
        self._deadline = self._clock() + self._maximum_seconds
        store = _VerifiedRows(self._root, self._maximum_rows, self._check)
        mutable = prepared = schedule = indices = None
        try:
            self._check()
            _family(self._study)
            binding, = self._study.datasets
            catalog = store.root / "datasets/public-archive-bars" / f"{binding.dataset_version_id}.json"
            try:
                catalog_bytes = catalog.read_bytes()
            except FileNotFoundError as error:
                raise ResearchBarsError("research_bar_dataset_not_found") from error
            candidate = json.loads(catalog_bytes)
            # A conservative refusal before frame I/O, never a metadata-based
            # grant. The original loader below owns all identity verification.
            if datetime.fromisoformat(candidate["identity"]["last_bar_close_at"]) >= UNTOUCHED_HOLDOUT_BOUNDARY_V1:
                raise EvaluationScopeError("scope_refuses_holdout_dataset")
            dataset = load_research_bar_dataset_v1(store, binding.dataset_version_id)
            if dataset.content_hash != binding.content_hash:
                raise sdk.StrategySdkError("bound_dataset_content_hash_mismatch")
            if (binding.evidence_tier is not EvidenceTierV1.T2_EVENT_TIME
                    or dataset.identity.get("evidence_tier") != EvidenceTierV1.T2_EVENT_TIME.value):
                raise sdk.StrategySdkError("bound_dataset_is_not_t2_research_bars")
            policies = self._study.policies
            if policies is None:
                raise sdk.StrategySdkError("t2_study_without_the_or_5_timing_policy")
            timing = policies["timing"]
            max_lag = timedelta(microseconds=max(timing["mandatory_sweep_lags_micros"]))
            if dataset.knowledge_upper_bound_exclusive(max_lag) > binding.knowledge_upper_bound_exclusive:
                raise EvaluationScopeError("scope_dataset_knowledge_exceeds_study_binding")
            if (not store.rows or len(store.rows) != dataset.identity["bar_frame"]["row_count"]
                    or store.rows[-1][1] != dataset.last_bar_close_at):
                raise EvaluationScopeError("scope_dataset_row_or_time_binding_mismatch")
            manifest = store.manifest
            if (manifest is None or catalog_bytes != catalog.read_bytes()
                    or store.manifest_bytes != store.manifest_path(dataset.bar_frame_manifest_hash).read_bytes()):
                raise EvaluationScopeError("persistent_metadata_changed_during_scope_admission")
            mutable = sdk.BarsV1.from_rows(store.rows)
            prepared = _immutable_bars(sdk.restrict_to_bound_v1(
                mutable, self._study.evaluation_upper_bound_exclusive, self._study.timing_lag))
            # The existing walk-forward schedule depends only on the verified,
            # restricted bars and exact lag. Targets remain trial-dependent.
            lag_us = self._study.timing_lag // timedelta(microseconds=1)
            indices = execution._held_decision_indices_v1(prepared, lag_us)
            schedule = np.frombuffer(indices.tobytes(), dtype=indices.dtype)
            retained = (_retained_upper_bound_v1(prepared) + sys.getsizeof(schedule)
                        + sys.getsizeof(schedule.base))
            if retained > self._maximum_snapshot_bytes:
                raise MemoryError("scope_retained_input_budget_exhausted")
            self._check()
            self._identity = MappingProxyType({
                "root": str(self._root), "dataset_id": str(binding.dataset_version_id),
                "dataset_content_hash": dataset.content_hash, "study_id": str(self._study.study_id),
                "study_hash": self._study_hash, "manifest_hash": manifest.manifest_hash,
                "logical_content_hash": manifest.logical_content_hash,
                "catalog_bytes_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
                "manifest_bytes_sha256": hashlib.sha256(store.manifest_bytes).hexdigest(),
                "maximum_evaluations": self._maximum_evaluations, "maximum_seconds": self._maximum_seconds,
                "maximum_rows": self._maximum_rows, "maximum_snapshot_bytes": self._maximum_snapshot_bytes,
                "retained_input_upper_bound_bytes": retained,
                "execution_schedule_bytes": schedule.nbytes,
                "execution_schedule_lag_micros": lag_us,
            })
            self._bars = prepared
            self._schedule = schedule
            return self
        except BaseException:
            mutable = prepared = schedule = indices = None
            self.close()
            raise
        finally:
            store.rows.clear()

    def __call__(self, study: StudySpecV1, parameters: Mapping[str, Any]) -> TrialEvaluationV1:
        self._owner()  # Foreign access refuses without invalidating its owner.
        with self._lock:
            try:
                self._check()
                if study.content_hash != self._study_hash or study.study_id != self._study.study_id:
                    raise EvaluationScopeError("scope_study_binding_mismatch")
                if self._used >= self._maximum_evaluations:
                    raise EvaluationScopeError("scope_evaluation_budget_exhausted")
                self._used += 1
                parameters = dict(parameters)
                family, reason = _family(study, parameters)
                if reason is not None:
                    self._check()
                    if self._used == self._maximum_evaluations:
                        self.close()
                    return TrialEvaluationV1(TrialOutcomeV1.INADMISSIBLE_PARAMETERS, {"reason": reason})
                bars = self._bars
                if bars is None or self._schedule is None:
                    raise EvaluationScopeError("scope_has_no_verified_input")
                targets, ties = family.targets_f64(bars, parameters)
                self._check()
                # SDK metrics convert held values with np.asarray(dtype=float);
                # its unchanged Sequence[int] annotation excludes ndarrays.
                held: Any = execution._positions_from_schedule_v1(targets, self._schedule)
                self._check()
                result = TrialEvaluationV1(TrialOutcomeV1.EVALUATED, sdk.search_metrics_f64(bars, held, near_ties=ties))
                self._check()
                if self._used == self._maximum_evaluations:
                    self.close()
                return result
            except BaseException:
                self.close()
                raise

    def cancel(self) -> None:
        if self._owner_pid is not None and os.getpid() != self._owner_pid:
            raise EvaluationScopeError("scope_owner_process_mismatch")
        self._cancelled.set()
        self.close()

    def close(self) -> None:
        with self._lock:
            self._bars = None
            self._schedule = None
            self._closed = True

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __reduce__(self) -> Any:
        raise TypeError("verified_evaluation_scopes_are_process_local")


@contextmanager
def worker_evaluation_scope_v1(study: StudySpecV1, data_root: Any, **limits: Any) -> Iterator[Any]:
    """Map failed admission to normal per-trial worker failures, never fallback.

    No data is supplied after a failed admission. SDK parameter inadmissibility
    precedence is retained; every otherwise admissible trial raises the original
    admission exception type. Exception metadata survives, not its data traceback.
    """
    from contextlib import ExitStack

    study_hash = study.content_hash
    with ExitStack() as stack:
        try:
            scope = stack.enter_context(VerifiedEvaluationScopeV1(study, data_root, **limits))
        except Exception as error:  # noqa: BLE001 - map admission failure to existing per-trial failure, never fallback
            admission_error = copy.copy(error).with_traceback(None)
            admission_error.__cause__ = admission_error.__context__ = None
        else:
            yield scope
            return

        def unavailable(spec: StudySpecV1, parameters: Mapping[str, Any]) -> TrialEvaluationV1:
            if spec.content_hash != study_hash:
                raise EvaluationScopeError("scope_study_binding_mismatch")
            _, reason = _family(spec, parameters)
            if reason is not None:
                return TrialEvaluationV1(TrialOutcomeV1.INADMISSIBLE_PARAMETERS, {"reason": reason})
            raise admission_error.with_traceback(None)

        yield unavailable
