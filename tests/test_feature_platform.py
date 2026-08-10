from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from trade_platform.domain import DataProcessingStatus, OHLCVBar
from trade_platform.feature_platform import FeatureDefinition, FeatureEngine, FeaturePlatformError, FeatureRegistry, SQLiteFeatureStore
from trade_platform.fundamentals import SQLiteFundamentalStore
from trade_platform.macro_data import SQLiteMacroReleaseStore
from tests.test_fundamentals import fact
from tests.test_macro_data import release


UTC = timezone.utc
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def bar(index: int, close: str, *, available_at: datetime | None = None) -> OHLCVBar:
    event = T0 + timedelta(days=index)
    return OHLCVBar("US:NYSE:SPY", "1d", event, event, available_at or event, Decimal(close), Decimal(close), Decimal(close), Decimal(close), Decimal("100"), "fixture", f"bar-{index}", "UTC", data_version="bars-v1", quality_score=Decimal("1"), processing_status=DataProcessingStatus.VALIDATED)


def registry() -> FeatureRegistry:
    result = FeatureRegistry()
    for name, sources, lookback in (("return", ("bars",), 2), ("sma", ("bars",), 2), ("vol", ("bars",), 2), ("revenue", ("fundamentals",), 1), ("cpi_surprise", ("macro",), 1)):
        result.register(FeatureDefinition(name, "v1", "quant", "1d", "available_at_close", lookback, sources, "reject", "reject", "prefix-invariance"))
    return result


class FeaturePlatformTests(unittest.TestCase):
    def test_price_features_are_prefix_invariant_and_reject_future_inputs(self) -> None:
        engine = FeatureEngine(registry())
        bars = [bar(0, "100"), bar(1, "110"), bar(2, "121")]
        decision = bars[-1].event_at
        self.assertEqual(engine.price_return("return", "v1", bars, decision_at=decision).value, Decimal("0.21"))
        self.assertEqual(engine.moving_average("sma", "v1", bars, decision_at=decision).value, Decimal("115.5"))
        self.assertEqual(engine.realized_volatility("vol", "v1", bars, decision_at=decision).value, Decimal("0"))
        with self.assertRaisesRegex(FeaturePlatformError, "not_available"):
            engine.price_return("return", "v1", [bar(0, "100"), bar(1, "110"), bar(2, "121", available_at=decision + timedelta(seconds=1))], decision_at=decision)

    def test_macro_fundamental_and_persistent_feature_values_are_versioned(self) -> None:
        engine = FeatureEngine(registry())
        fundamentals = SQLiteFundamentalStore(); fundamentals.ingest([fact("1000000")])
        macro = SQLiteMacroReleaseStore(); macro.ingest([release("0.4")])
        fundamental = engine.latest_fundamental("revenue", "v1", fundamentals, "US:NASDAQ:ACME", "revenue", decision_at=fact("1000000").ingested_at)
        surprise = engine.macro_surprise("cpi_surprise", "v1", macro, "US:CPI", decision_at=release("0.4").ingested_at)
        self.assertEqual((fundamental.value, surprise.value), (Decimal("1"), Decimal("0.1")))
        store = SQLiteFeatureStore(); store.append(fundamental)
        self.assertEqual(store.available_as_of("US:NASDAQ:ACME", "revenue", "v1", fundamental.as_of), [fundamental])
        with self.assertRaisesRegex(FeaturePlatformError, "duplicate"):
            store.append(fundamental)

