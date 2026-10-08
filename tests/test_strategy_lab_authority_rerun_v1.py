"""Phase R4.7 -- Decimal metrics and the OR-3 rerun set, offline."""

from __future__ import annotations

import unittest
from decimal import Decimal

from tests.test_strategy_sdk_v1 import _rows, _walk
from trade_platform.strategy_lab_authority_rerun_v1 import (
    REASON_CUTOFF_BAND,
    REASON_FROZEN,
    REASON_NEAR_TIE,
    AuthorityRerunError,
    decimal_metrics_v1,
    rerun_set_v1,
)
from trade_platform.strategy_lab_manifest_v1 import CandidateSetV1, StudyManifestV1
from trade_platform.strategy_sdk_v1 import (
    FAMILIES_V1,
    BarsV1,
    held_positions_v1,
    search_metrics_f64,
)


def _row(trial: str, value: str | None, ties: int = 0, outcome: str = "EVALUATED") -> dict[str, object]:
    return {"trial_id": trial, "trial_content_hash": trial * 8, "result_content_hash": "r" + trial,
            "outcome": outcome, "metrics": {"m": value, "near_tie_decisions": ties}}


class DecimalMetricsTests(unittest.TestCase):
    def test_decimal_metrics_match_the_float_search_definitions(self) -> None:
        bars = BarsV1.from_rows(_rows(_walk(3000, seed=5)))
        family = FAMILIES_V1["mean_reversion_z"]
        params = {"lookback_bars": 60, "entry_z": Decimal("2"), "exit_z": Decimal("0.5"), "direction": "long_short"}
        held = held_positions_v1(bars, family.targets_decimal(bars, params), 2_000_000)
        exact = decimal_metrics_v1(bars, held)
        fast = search_metrics_f64(bars, held, near_ties=0)
        self.assertEqual(exact["trades"], fast["trades"])
        for key in ("total_return", "max_drawdown", "turnover", "exposure", "break_even_bps_per_side"):
            with self.subTest(metric=key):
                self.assertAlmostEqual(float(exact[key]), float(fast[key]), places=9)
        self.assertEqual("DECIMAL_AUTHORITATIVE", exact["numeric_tier"])
        self.assertTrue(all(not isinstance(v, float) for v in exact.values()))


class RerunSetTests(unittest.TestCase):
    def _sets(self, rows: list[dict[str, object]], frozen: list[tuple[str, str]]) -> tuple[StudyManifestV1, CandidateSetV1]:
        manifest = StudyManifestV1("s", {"results": rows, "planned_trial_count": len(rows)}, "h" * 64)
        identity = {"rule": {"metric": "m", "direction": "HIGHER_IS_BETTER", "top_k": len(frozen)},
                    "candidates": [{"trial_id": t, "metric_value": v} for t, v in frozen]}
        return manifest, CandidateSetV1("s", "h" * 64, identity, "c" * 64)

    def test_frozen_band_and_flagged_candidates_are_all_rerun(self) -> None:
        rows = [_row("a", "2.0"), _row("b", "1.5"), _row("c", "1.5000001"), _row("d", "0.1", ties=3),
                _row("e", "0.2"), _row("f", None, outcome="INADMISSIBLE_PARAMETERS")]
        manifest, candidates = self._sets(rows, [("a", "2.0"), ("b", "1.5")])
        reasons = rerun_set_v1(manifest, candidates)
        self.assertEqual([REASON_FROZEN], reasons["a"])
        self.assertIn(REASON_FROZEN, reasons["b"])
        self.assertEqual([REASON_CUTOFF_BAND], reasons["c"])  # within 1e-6 relative of the cutoff 1.5
        self.assertEqual([REASON_NEAR_TIE], reasons["d"])  # flagged anywhere in the study
        self.assertNotIn("e", reasons)
        self.assertNotIn("f", reasons)

    def test_a_candidate_set_from_another_manifest_is_refused(self) -> None:
        manifest, candidates = self._sets([_row("a", "1")], [("a", "1")])
        foreign = CandidateSetV1("s", "x" * 64, candidates.identity, "c" * 64)
        with self.assertRaises(AuthorityRerunError):
            rerun_set_v1(manifest, foreign)


if __name__ == "__main__":
    unittest.main()
