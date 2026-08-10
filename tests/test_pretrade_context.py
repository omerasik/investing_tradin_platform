import unittest
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trade_platform.broker_adapter import BrokerAccountSnapshot
from trade_platform.domain import AssetClass, Instrument
from trade_platform.instruments import SQLiteInstrumentStore, TradingSession
from trade_platform.model_registry import ModelValidation, ModelVersion, SQLiteModelRegistry
from trade_platform.paper_execution import CashBalance
from trade_platform.pretrade_context import build_pre_trade_execution_context


class PreTradeContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instruments = SQLiteInstrumentStore()
        self.instruments.register(Instrument("US:NYSE:SPY", "SPY", "NYSE", AssetClass.ETF, "USD", Decimal("0.01"), Decimal("1")))
        self.instruments.add_trading_session(TradingSession("NYSE", 0, time(9, 30), time(16), "America/New_York"))
        self.models = SQLiteModelRegistry()
        self.model = ModelVersion.create(name="direction", version="v1", task="return_direction_probability", feature_version="features:v1", dataset_version="bars:v1", artifact_reference="local://direction")
        self.models.register(self.model)
        validation = ModelValidation.create(model_id=self.model.model_id, metrics={"brier_score": Decimal("0.1")}, economic_value_after_cost=Decimal("0.02"), evidence_reference="research://validation/1")
        self.models.append_validation(validation)
        self.models.approve(self.model.model_id, validation_id=validation.validation_id, actor="reviewer", reason="review complete")
        self.observed_at = datetime(2025, 1, 6, 15, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.instruments.close(); self.models.close()

    def snapshot(self, as_of: datetime | None = None) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot("paper-main", "sandbox", self.observed_at if as_of is None else as_of, CashBalance("USD", Decimal("5000")), Decimal("5000"), {}, (), (), Decimal("0"), Decimal("0"))

    def test_builder_uses_calendar_broker_age_and_model_registry(self) -> None:
        context = build_pre_trade_execution_context(
            instrument_id="US:NYSE:SPY", account_id="paper-main", observed_at=self.observed_at,
            instruments=self.instruments, broker_snapshot=self.snapshot(), broker_healthy=True,
            models=self.models, model_id=self.model.model_id, strategy_enabled=True,
            instrument_halted=False, quote_available=True, expected_slippage_fraction=Decimal("0.001"), event_risk=Decimal("0.1"),
        )
        self.assertTrue(context.session_open)
        self.assertTrue(context.broker_state_certain)
        self.assertEqual(context.available_buying_power, Decimal("5000"))
        self.assertTrue(context.model_approved)

    def test_builder_fails_closed_for_missing_model_or_stale_broker_snapshot(self) -> None:
        context = build_pre_trade_execution_context(
            instrument_id="US:NYSE:SPY", account_id="paper-main", observed_at=self.observed_at,
            instruments=self.instruments, broker_snapshot=self.snapshot(self.observed_at - timedelta(minutes=6)), broker_healthy=True,
            models=self.models, model_id=uuid4(), strategy_enabled=True,
            instrument_halted=False, quote_available=True, expected_slippage_fraction=Decimal("0.001"), event_risk=Decimal("0.1"),
        )
        self.assertFalse(context.broker_state_certain)
        self.assertEqual(context.available_buying_power, Decimal("0"))
        self.assertFalse(context.model_approved)


if __name__ == "__main__":
    unittest.main()
