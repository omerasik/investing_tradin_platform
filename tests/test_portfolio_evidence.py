import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from trade_platform.broker_adapter import BrokerAccountSnapshot
from trade_platform.domain import AssetClass, Instrument, OrderIntent, OrderSide
from trade_platform.instruments import InstrumentRiskProfile, SQLiteInstrumentStore
from trade_platform.paper_execution import CashBalance, Position, ReconciliationResult
from trade_platform.paper_oms import SQLitePaperOms
from trade_platform.portfolio_evidence import (
    PortfolioEvidenceError,
    ReconciledAccountSnapshot,
    ReconciledPositionSnapshot,
    SQLiteReconciledAccountStore,
    SQLiteReconciledPositionStore,
)
from trade_platform.quotes import QuoteObservation, SQLiteQuoteStore


class PortfolioEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
        self.oms = SQLitePaperOms(); self.store = SQLiteReconciledPositionStore(); self.accounts = SQLiteReconciledAccountStore(); self.quotes = SQLiteQuoteStore(); self.instruments = SQLiteInstrumentStore()
        self.instruments.register(Instrument("TEST:SPY", "SPY", "TEST", AssetClass.ETF, "USD", Decimal("0.01"), Decimal("1")))
        self.instruments.append_risk_profile(InstrumentRiskProfile("TEST:SPY", "INDEX", "US", "US_INDEX", Decimal("1"), Decimal("0"), {"market": Decimal("1")}, self.now, self.now))
        self.quotes.append(QuoteObservation(uuid4(), "TEST:SPY", Decimal("99"), Decimal("101"), Decimal("100"), Decimal("1"), "paper-feed", "quote", self.now, self.now))

    def tearDown(self) -> None:
        self.oms.close(); self.store.close(); self.accounts.close(); self.quotes.close(); self.instruments.close()

    def test_snapshot_must_bind_to_complete_reconciliation_and_respects_ingestion_time(self) -> None:
        incomplete = self.oms.record_reconciliation("paper", "local", ReconciliationResult(False, ("positions",)), occurred_at=self.now)
        with self.assertRaisesRegex(PortfolioEvidenceError, "complete_reconciliation"):
            self.store.append(ReconciledPositionSnapshot(uuid4(), incomplete.run_id, "paper", "local", {}, self.now, self.now), self.oms)
        complete = self.oms.record_reconciliation("paper", "local", ReconciliationResult(True, ()), occurred_at=self.now)
        future = ReconciledPositionSnapshot(uuid4(), complete.run_id, "paper", "local", {}, self.now, self.now + timedelta(seconds=1))
        self.store.append(future, self.oms)
        with self.assertRaisesRegex(PortfolioEvidenceError, "unavailable_as_of"):
            self.store.latest_as_of("paper", self.now)

    def test_projected_exposure_marks_holdings_at_last_and_order_at_ask(self) -> None:
        reconciliation = self.oms.record_reconciliation("paper", "local", ReconciliationResult(True, ()), occurred_at=self.now)
        snapshot = ReconciledPositionSnapshot(uuid4(), reconciliation.run_id, "paper", "local", {"TEST:SPY": Position("TEST:SPY", Decimal("5"), Decimal("90"))}, self.now, self.now)
        self.store.append(snapshot, self.oms)
        intent = OrderIntent(uuid4(), uuid4(), "TEST:SPY", "paper", OrderSide.BUY, Decimal("2"), Decimal("100"), self.now)
        resolved, exposures = self.store.projected_exposures_as_of(account_id="paper", intent=intent, decision_at=self.now, instruments=self.instruments, quotes=self.quotes)
        self.assertEqual(resolved.exposure_reference, f"local:{snapshot.snapshot_id}")
        self.assertEqual(exposures[0].notional, Decimal("702"))
        self.assertEqual(exposures[0].exchange, "TEST")

    def test_account_snapshot_requires_complete_reconciliation_and_round_trips(self) -> None:
        reconciliation = self.oms.record_reconciliation("paper", "local", ReconciliationResult(True, ()), occurred_at=self.now)
        broker_snapshot = BrokerAccountSnapshot("paper", "local", self.now, CashBalance("USD", Decimal("1000")), Decimal("900"), {"TEST:SPY": Position("TEST:SPY", Decimal("5"), Decimal("90"))}, (uuid4(),), ("fill-1",), Decimal("2"), Decimal("1"))
        evidence = ReconciledAccountSnapshot(uuid4(), reconciliation.run_id, broker_snapshot, True, self.now)
        self.accounts.append(evidence, self.oms)
        self.assertEqual(self.accounts.latest_as_of("paper", self.now), evidence)
        intent = OrderIntent(uuid4(), uuid4(), "TEST:SPY", "paper", OrderSide.BUY, Decimal("2"), Decimal("100"), self.now)
        resolved, exposures = self.accounts.projected_exposures_as_of(account_id="paper", intent=intent, decision_at=self.now, instruments=self.instruments, quotes=self.quotes)
        self.assertEqual(resolved.exposure_reference, f"local:{evidence.evidence_id}")
        self.assertEqual(exposures[0].notional, Decimal("702"))
        self.assertEqual(self.accounts.portfolio_state_as_of("paper", self.now).position_notionals, {"TEST:SPY": Decimal("450")})
        incomplete = self.oms.record_reconciliation("paper", "local", ReconciliationResult(False, ("cash",)), occurred_at=self.now + timedelta(minutes=1))
        with self.assertRaisesRegex(PortfolioEvidenceError, "complete_reconciliation"):
            self.accounts.append(ReconciledAccountSnapshot(uuid4(), incomplete.run_id, BrokerAccountSnapshot("paper", "local", incomplete.occurred_at, CashBalance("USD", Decimal("1000")), Decimal("900"), {}, (), (), Decimal("0"), Decimal("0")), False, incomplete.occurred_at), self.oms)


if __name__ == "__main__":
    unittest.main()
