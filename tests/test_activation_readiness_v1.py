"""Activation readiness -- gate codes and the window catalogue (offline)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from trade_platform.activation_readiness_v1 import (
    OR6,
    OR7,
    OR9,
    OR11,
    gate_of,
    read_window_catalogue_v1,
)


class GateCodeTests(unittest.TestCase):
    def test_owner_gates_are_named_exactly_and_engineering_is_not_a_gate(self) -> None:
        self.assertEqual(("OR-6", "OR-7", "OR-9", "OR-11"),
                         tuple(gate_of(f"{code}:detail") for code in (OR6, OR7, OR9, OR11)))
        self.assertEqual("BLOCKED_OWNER_DECISION_OR_7", OR7)
        self.assertIsNone(gate_of("STUDY_SEARCH_NOT_FINISHED:3/8"))
        self.assertIsNone(gate_of("ENGINEERING:AUTHORITY_RERUN_SELECTION_NOT_ESTABLISHED"))


class WindowCatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="readiness-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_the_catalogue_lists_windows_and_says_when_it_is_not_configured(self) -> None:
        from tests.test_strategy_lab_e2e_fixture import build_window_and_study

        self.assertIsNone(read_window_catalogue_v1(None))
        self.assertEqual([], read_window_catalogue_v1(self.temp / "empty"))
        _, data_root = build_window_and_study(self.temp, family="trend_ma_cross")
        (window,) = read_window_catalogue_v1(data_root) or []
        self.assertEqual(("BTCUSDT", True, []), (window["symbol"], window["continuous"], window["rejected_days"]))
        self.assertGreater(window["bars"], 0)


if __name__ == "__main__":
    unittest.main()
