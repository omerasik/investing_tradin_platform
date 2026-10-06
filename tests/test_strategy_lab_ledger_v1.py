from __future__ import annotations

import unittest

from trade_platform.strategy_lab_ledger_v1 import (
    StrategyLabLedgerError,
    TrialOutcomeV1,
    result_content_hash_v1,
)

TRIAL = "e" * 64


class ResultHashTests(unittest.TestCase):
    def test_hash_is_deterministic_and_binds_outcome_and_metrics(self) -> None:
        base = result_content_hash_v1(trial_content_hash=TRIAL, outcome=TrialOutcomeV1.EVALUATED,
                                      metrics={"trades": 12, "gross_return": "0.0125"})
        self.assertEqual(base, result_content_hash_v1(trial_content_hash=TRIAL, outcome=TrialOutcomeV1.EVALUATED,
                                                      metrics={"gross_return": "0.0125", "trades": 12}))
        self.assertNotEqual(base, result_content_hash_v1(
            trial_content_hash=TRIAL, outcome=TrialOutcomeV1.INADMISSIBLE_PARAMETERS,
            metrics={"trades": 12, "gross_return": "0.0125"}))
        self.assertNotEqual(base, result_content_hash_v1(
            trial_content_hash=TRIAL, outcome=TrialOutcomeV1.EVALUATED, metrics={"trades": 13, "gross_return": "0.0125"}))
        self.assertNotEqual(base, result_content_hash_v1(
            trial_content_hash="f" * 64, outcome=TrialOutcomeV1.EVALUATED,
            metrics={"trades": 12, "gross_return": "0.0125"}))

    def test_float_metrics_are_refused(self) -> None:
        with self.assertRaises(StrategyLabLedgerError):
            result_content_hash_v1(trial_content_hash=TRIAL, outcome=TrialOutcomeV1.EVALUATED,
                                   metrics={"gross_return": 0.0125})
        with self.assertRaises(StrategyLabLedgerError):
            result_content_hash_v1(trial_content_hash=TRIAL, outcome=TrialOutcomeV1.EVALUATED,
                                   metrics={"curve": ["0.1", 0.2]})


if __name__ == "__main__":
    unittest.main()
