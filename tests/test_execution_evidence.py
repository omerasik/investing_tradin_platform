import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trade_platform.domain import MarketSnapshot, OrderSide
from trade_platform.execution_evidence import EventRiskObservation, ExecutionEvidenceError, HaltObservation, SlippageEstimate, SQLiteExecutionEvidenceStore
from trade_platform.portfolio_risk import PortfolioExposure


class ExecutionEvidenceTests(unittest.TestCase):
    def test_point_in_time_snapshot_builds_traceable_pretrade_evidence(self) -> None:
        now = datetime.now(timezone.utc); store = SQLiteExecutionEvidenceStore()
        store.append_halt(HaltObservation(uuid4(), "US:NYSE:SPY", False, "venue", "halt-1", now, now))
        store.append_event_risk(EventRiskObservation(uuid4(), "US:NYSE:SPY", Decimal("0.1"), "events", "event-1", now, now))
        store.append_slippage(SlippageEstimate(uuid4(), "US:NYSE:SPY", OrderSide.BUY, Decimal("0.002"), "model", "slip-1", now, now))
        snapshot = store.snapshot_as_of("US:NYSE:SPY", OrderSide.BUY, now)
        market = MarketSnapshot("US:NYSE:SPY", now, Decimal("99"), Decimal("100"), Decimal("100"), Decimal("1"))
        evidence = snapshot.pretrade_input_evidence(market_reference="bars:v1", market=market, exposure_reference="positions:v1", exposure_observed_at=now, exposures=[PortfolioExposure("US:NYSE:SPY", Decimal("100"), "ETF", "INDEX")])
        self.assertFalse(snapshot.halt.halted); self.assertEqual(snapshot.event.risk, Decimal("0.1")); self.assertIn("halt-1", evidence.halt_reference)
        evidence.validate(observed_at=now, market=market, exposures=[PortfolioExposure("US:NYSE:SPY", Decimal("100"), "ETF", "INDEX")], maximum_age=timedelta(minutes=5))
        store.close()

    def test_future_ingestion_or_missing_side_specific_evidence_is_unavailable(self) -> None:
        now = datetime.now(timezone.utc); store = SQLiteExecutionEvidenceStore()
        store.append_halt(HaltObservation(uuid4(), "US:NYSE:SPY", False, "venue", "halt", now, now + timedelta(minutes=1)))
        store.append_event_risk(EventRiskObservation(uuid4(), "US:NYSE:SPY", Decimal("0.1"), "events", "event", now, now))
        store.append_slippage(SlippageEstimate(uuid4(), "US:NYSE:SPY", OrderSide.SELL, Decimal("0.002"), "model", "slip", now, now))
        with self.assertRaisesRegex(ExecutionEvidenceError, "unavailable"):
            store.snapshot_as_of("US:NYSE:SPY", OrderSide.BUY, now)
        store.close()


if __name__ == "__main__":
    unittest.main()
