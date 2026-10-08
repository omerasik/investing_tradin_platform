"""Shared fixture: synthetic archive days -> research bars -> window -> policy-bound study.

Every byte here is a FIXTURE served by an in-memory fetcher; no network call.
"""

from __future__ import annotations

import random
import unittest
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.test_bybit_public_archive_v1 import FakeArchive, _gz
from trade_platform.bybit_public_archive_v1 import ARCHIVE_SCHEMA_V1, HttpResponseV1
from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
from trade_platform.public_archive_research_bars_v1 import (
    acquire_and_derive_day_v1,
    build_research_bar_dataset_v1,
)
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
from trade_platform.strategy_lab_policies_v1 import (
    gross_cost_policy_v1,
    or3_numeric_policy_v1,
    or5_t2_timing_policy_v1,
)
from trade_platform.strategy_lab_study_v1 import (
    UNTOUCHED_HOLDOUT_BOUNDARY_V1,
    DatasetBindingV1,
    ParameterSpaceV1,
    SearchModeV1,
    SearchPlanV1,
    StudySpecV1,
)
from trade_platform.strategy_sdk_v1 import FAMILIES_V1

FIRST_DAY = date(2026, 6, 1)


def day_csv(day: date, seed: int) -> str:
    """Two trades per minute for the whole UTC day, on a seeded random walk."""
    rng = random.Random(seed)
    start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    lines = [",".join(ARCHIVE_SCHEMA_V1)]
    price = 30_000.0 + seed
    index = 0
    for minute in range(1440):
        for second in (5, 35):
            price = round(price + rng.choice(range(-30, 31)) / 10, 1)
            size = "0.010"
            notional = str(round(price * 0.01, 4))
            lines.append(f"{start + minute * 60 + second}.000,BTCUSDT,{'Buy' if index % 2 else 'Sell'},"
                         f"{size},{price:.1f},PlusTick,id-{index:07d},8.6e+09,{size},{notional},0")
            index += 1
    return "\n".join(lines) + "\n"


class SyntheticDays:
    def __init__(self) -> None:
        self._bodies: dict[date, bytes] = {}

    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResponseV1:
        day = date.fromisoformat(url.rsplit("BTCUSDT", 1)[1].removesuffix(".csv.gz"))
        if day not in self._bodies:
            self._bodies[day] = _gz(day_csv(day, (day - FIRST_DAY).days))
        return FakeArchive(self._bodies[day])(url, headers)


def build_window_and_study(temp: Path, *, family: str, days: int = 2,
                           space: ParameterSpaceV1 | None = None) -> tuple[StudySpecV1, Path]:
    archive_root, data_root = temp / "archive", temp / "data"
    store = ResearchFrameStoreV1(data_root)
    fetch = SyntheticDays()
    for offset in range(days):
        acquire_and_derive_day_v1(archive_root, "BTCUSDT", FIRST_DAY + timedelta(days=offset), store=store,
                                  evict=True, fetch=fetch)
    window = build_research_bar_dataset_v1(archive_root, "BTCUSDT", FIRST_DAY,
                                           FIRST_DAY + timedelta(days=days - 1), store=store)
    strategy = FAMILIES_V1[family]
    space = space or strategy.parameter_space()
    binding = DatasetBindingV1(
        role="bars", dataset_version_id=window.dataset_version_id, content_hash=window.content_hash,
        evidence_tier=EvidenceTierV1.T2_EVENT_TIME,
        knowledge_upper_bound_exclusive=window.knowledge_upper_bound_exclusive(timedelta(seconds=60)),
    )
    study = StudySpecV1(
        strategy=strategy.spec(), parameter_space=space, datasets=(binding,),
        search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality),
        evaluation_upper_bound_exclusive=UNTOUCHED_HOLDOUT_BOUNDARY_V1,
        policies={"numeric": or3_numeric_policy_v1().payload, "timing": or5_t2_timing_policy_v1().payload,
                  "cost": gross_cost_policy_v1().policy().payload},
        label=f"fixture {family}",
    )
    return study, data_root


class FixtureSanityTests(unittest.TestCase):
    def test_the_fixture_window_has_full_days_of_bars(self) -> None:
        import shutil
        import tempfile

        temp = Path(tempfile.mkdtemp(prefix="e2e-fixture-"))
        try:
            study, data_root = build_window_and_study(temp, family="trend_ma_cross")
            from trade_platform.public_archive_research_bars_v1 import load_research_bar_dataset_v1

            (binding,) = study.datasets
            dataset = load_research_bar_dataset_v1(ResearchFrameStoreV1(data_root), binding.dataset_version_id)
            self.assertEqual(2880, dataset.identity["bar_frame"]["row_count"])
        finally:
            shutil.rmtree(temp, ignore_errors=True)


def _unused(value: Any) -> Any:
    return value


if __name__ == "__main__":
    unittest.main()
