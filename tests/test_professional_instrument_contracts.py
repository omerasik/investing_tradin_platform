import unittest
from datetime import UTC, datetime

from trade_platform.professional_instruments import (
    InstrumentMasterError,
    InstrumentType,
    RepresentationKind,
    mvp_instrument_universe,
)


class ProfessionalInstrumentContractTests(unittest.TestCase):
    def test_mvp_universe_is_explicit_and_provider_neutral(self) -> None:
        universe = mvp_instrument_universe(datetime(2024, 1, 1, tzinfo=UTC))
        self.assertEqual(len(universe), 6)
        self.assertEqual(len({instrument.instrument_id for instrument in universe}), 6)
        gold = next(instrument for instrument in universe if instrument.canonical_symbol == "GLD")
        self.assertEqual(gold.instrument_type, InstrumentType.ETF)
        self.assertEqual(gold.representation_kind, RepresentationKind.ETF_PROXY)
        self.assertIn("not XAUUSD spot or GC futures", gold.underlying_reference)

    def test_timestamps_must_be_timezone_aware(self) -> None:
        naive = datetime(2024, 1, 1)  # noqa: DTZ001 - deliberate rejection fixture
        with self.assertRaisesRegex(InstrumentMasterError, "registered_at_must_be_timezone_aware"):
            mvp_instrument_universe(naive)


if __name__ == "__main__":
    unittest.main()
