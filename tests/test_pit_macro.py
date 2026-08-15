import unittest

from trade_platform.pit_macro import MacroCategory


class PitMacroContractTests(unittest.TestCase):
    def test_controlled_catalogue_is_explicit(self) -> None:
        self.assertEqual(
            {item.value for item in MacroCategory},
            {"POLICY_RATE", "CPI", "EMPLOYMENT", "GDP", "YIELD_CURVE", "LIQUIDITY_CREDIT"},
        )
