"""Revalidated, bounded Strategy Lab loading over the unchanged SDK and authority.

The SDK hashes its whole source into strategy identity and remains the reference.
This boundary reuses its loader/evaluator and the existing dataset verifier. A
warm cache is never evidence: dataset/manifest/object bytes and logical rows are
reverified before reuse. Keep at most one root-bound dataset, with a conservative
512 MiB retained-size ceiling; oversized inputs still load but are not cached.
No new dataset authority, numerical rule, time convention or persistent state.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import numpy as np

from . import strategy_sdk_v1 as sdk
from .evidence_tier_authority_v1 import EvidenceTierV1
from .public_archive_research_bars_v1 import load_research_bar_dataset_v1
from .research_data_plane_v1 import ResearchFrameStoreV1, default_research_data_root
from .strategy_lab_study_v1 import StudySpecV1
from .strategy_lab_worker_v1 import TrialEvaluationV1

_CACHE_BYTES_V1: Final = 512 * 1024 ** 2
_DECIMAL_BYTES_V1: Final = sys.getsizeof(Decimal("9" * 38))
_LOCK = threading.RLock()
_cache_root: Path | None = None


def clear_bars_cache_v1() -> None:
    """Release only the process-local SDK bar cache, never stored evidence."""
    global _cache_root
    with _LOCK:
        sdk._BARS_CACHE.clear()
        _cache_root = None


def _retained_upper_bound_v1(bars: sdk.BarsV1) -> int:
    # T2 values use the already-verified decimal128(38,18) schema. Counting every
    # tuple reference as a separate Decimal overestimates sharing safely; arrays
    # are likewise counted with both their Python size and payload bytes.
    total = sys.getsizeof(bars) + sys.getsizeof(vars(bars))
    for value in vars(bars).values():
        total += sys.getsizeof(value)
        if isinstance(value, np.ndarray):
            total += value.nbytes
        elif isinstance(value, tuple):
            total += len(value) * _DECIMAL_BYTES_V1
    return total


@contextmanager
def _verified_cache_v1(data_root: Any, dataset_version_id: Any, expected: str) -> Iterator[None]:
    global _cache_root
    root = Path(data_root or default_research_data_root()).resolve()
    key = (str(dataset_version_id), expected)
    with _LOCK:
        if root != _cache_root or key not in sdk._BARS_CACHE:
            sdk._BARS_CACHE.clear()
        try:
            if key in sdk._BARS_CACHE:
                dataset = load_research_bar_dataset_v1(ResearchFrameStoreV1(root), dataset_version_id)
                if dataset.content_hash != expected:
                    raise sdk.StrategySdkError("bound_dataset_content_hash_mismatch")
                if dataset.identity.get("evidence_tier") != EvidenceTierV1.T2_EVENT_TIME.value:
                    raise sdk.StrategySdkError("bound_dataset_is_not_t2_research_bars")
            # On a miss the original SDK loader performs all the same checks.
            yield
            bars = sdk._BARS_CACHE.get(key)
            sdk._BARS_CACHE.clear()
            if bars is not None and _retained_upper_bound_v1(bars) <= _CACHE_BYTES_V1:
                for value in vars(bars).values():
                    if isinstance(value, np.ndarray):
                        value.setflags(write=False)
                sdk._BARS_CACHE[key] = bars
            _cache_root = root
        except BaseException:
            sdk._BARS_CACHE.clear()
            _cache_root = None
            raise


def load_bars_v1(data_root: Any, dataset_version_id: Any, *, expected_content_hash: str) -> sdk.BarsV1:
    """Same exact SDK bars; full verification also applies on a cache hit."""
    with _verified_cache_v1(data_root, dataset_version_id, expected_content_hash):
        return sdk.load_bars_v1(data_root, dataset_version_id, expected_content_hash=expected_content_hash)


class BarStrategyEvaluatorV1(sdk.BarStrategyEvaluatorV1):
    """Use the unmodified SDK evaluator with the same verified cache boundary."""

    def __call__(self, study: StudySpecV1, parameters: Mapping[str, Any]) -> TrialEvaluationV1:
        family = sdk.FAMILIES_V1.get(study.strategy.family)
        # Retain the SDK's error/inadmissibility precedence without loading data
        # for a trial it would refuse before loading.
        if (family is None or family.spec() != study.strategy or
                family.inadmissible(parameters) is not None or study.timing_lag is None):
            return super().__call__(study, parameters)
        binding, = study.datasets
        with _verified_cache_v1(self.data_root, binding.dataset_version_id, binding.content_hash):
            return super().__call__(study, parameters)
