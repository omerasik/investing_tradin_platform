from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from trade_platform.broker_adapter import BrokerAccountSnapshot
from trade_platform.domain import OrderIntent, OrderSide, OrderStatus
from trade_platform.paper_execution import CashBalance, PaperOrder, Position, ReconciliationResult
from trade_platform.paper_oms import PaperOmsError, SQLitePaperOms


NOW = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)


def order() -> PaperOrder:
    return PaperOrder(OrderIntent(uuid4(), uuid4(), "US:NYSE:SPY", "paper-account", OrderSide.BUY, Decimal("10"), Decimal("100"), NOW))


class PaperOmsTests(unittest.TestCase):
    def test_order_lifecycle_and_events_survive_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "oms.sqlite"
            store = SQLitePaperOms(path)
            created = store.create(order())
            for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED):
                store.transition(created.intent.intent_id, status)
            partial = store.ingest_fill("sandbox-fill-1", created.intent.intent_id, Decimal("4"), Decimal("101"), occurred_at=NOW)
            final = store.ingest_fill("sandbox-fill-2", created.intent.intent_id, Decimal("6"), Decimal("99"), occurred_at=NOW)
            self.assertEqual((partial.status, final.status, final.average_fill_price), (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, Decimal("99.8")))
            store.close()
            reopened = SQLitePaperOms(path)
            try:
                self.assertEqual(reopened.get(created.intent.intent_id), final)
                self.assertEqual([item.event_type for item in reopened.events(created.intent.intent_id)], ["ORDER_CREATED", *(["ORDER_STATUS_CHANGED"] * 5), "FILL_INGESTED", "FILL_INGESTED"])
                self.assertEqual([item.external_fill_id for item in reopened.fills(created.intent.intent_id)], ["sandbox-fill-1", "sandbox-fill-2"])
            finally:
                reopened.close()

    def test_idempotency_and_duplicate_fill_protection_do_not_create_exposure_twice(self) -> None:
        store = SQLitePaperOms()
        created = store.create(order())
        self.assertEqual(store.create(created), created)
        conflict = PaperOrder(OrderIntent(created.intent.intent_id, uuid4(), "US:NYSE:SPY", "paper-account", OrderSide.BUY, Decimal("9"), Decimal("100"), NOW))
        with self.assertRaisesRegex(PaperOmsError, "idempotency_key_payload_conflict"):
            store.create(conflict)
        for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED):
            store.transition(created.intent.intent_id, status)
        once = store.ingest_fill("fill-id", created.intent.intent_id, Decimal("2"), Decimal("100"), occurred_at=NOW)
        self.assertEqual(store.ingest_fill("fill-id", created.intent.intent_id, Decimal("2"), Decimal("100"), occurred_at=NOW), once)
        self.assertEqual(len(store.fills(created.intent.intent_id)), 1)
        with self.assertRaisesRegex(PaperOmsError, "external_fill_id_conflict"):
            store.ingest_fill("fill-id", created.intent.intent_id, Decimal("1"), Decimal("100"), occurred_at=NOW)

    def test_cancel_and_reconciliation_evidence_are_durable(self) -> None:
        store = SQLitePaperOms()
        created = store.create(order())
        for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED):
            store.transition(created.intent.intent_id, status)
        self.assertEqual(store.cancel(created.intent.intent_id).status, OrderStatus.CANCELLED)
        with self.assertRaisesRegex(PaperOmsError, "order_not_cancellable|invalid_order_transition"):
            store.cancel(created.intent.intent_id)
        record = store.record_reconciliation("paper-account", "fixture-sandbox", ReconciliationResult(False, ("cash_mismatch",)), occurred_at=NOW)
        self.assertEqual(store.latest_reconciliation("paper-account"), record)

    def test_reconciled_account_is_committed_with_complete_reconciliation_only(self) -> None:
        store = SQLitePaperOms()
        snapshot = BrokerAccountSnapshot("paper-account", "fixture-sandbox", NOW, CashBalance("USD", Decimal("1000")), Decimal("900"), {"TEST:SPY": Position("TEST:SPY", Decimal("5"), Decimal("90"))}, (), (), Decimal("0"), Decimal("0"))
        complete, account = store.record_reconciliation_with_account(snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=NOW)
        self.assertIsNotNone(account)
        self.assertEqual(store.reconciliation(account.reconciliation_id), complete)
        self.assertEqual(store.latest_reconciled_account("paper-account", NOW).snapshot, snapshot)
        incomplete_snapshot = BrokerAccountSnapshot("other-account", "fixture-sandbox", NOW, CashBalance("USD", Decimal("1000")), Decimal("900"), {}, (), (), Decimal("0"), Decimal("0"))
        incomplete, missing = store.record_reconciliation_with_account(incomplete_snapshot, ReconciliationResult(False, ("cash",)), healthy=False, ingested_at=NOW)
        self.assertFalse(incomplete.complete)
        self.assertIsNone(missing)
        self.assertIsNone(store.latest_reconciled_account("other-account", NOW))
        store.close()

    def test_reconciled_account_survives_reopen_without_partial_record(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "oms.sqlite"
            store = SQLitePaperOms(path)
            snapshot = BrokerAccountSnapshot("paper-account", "fixture-sandbox", NOW, CashBalance("USD", Decimal("1000")), Decimal("900"), {}, (), (), Decimal("0"), Decimal("0"))
            complete, evidence = store.record_reconciliation_with_account(snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=NOW)
            store.close()
            reopened = SQLitePaperOms(path)
            try:
                restored = reopened.latest_reconciled_account("paper-account", NOW)
                self.assertEqual((restored.reconciliation_id, restored.snapshot, restored.healthy), (complete.run_id, snapshot, True))
                self.assertIsNotNone(evidence)
            finally:
                reopened.close()

    def test_broker_confirmed_amendment_preserves_intent_and_fill_safety(self) -> None:
        store = SQLitePaperOms()
        created = store.create(order())
        for status in (OrderStatus.RISK_PENDING, OrderStatus.APPROVED, OrderStatus.SUBMISSION_PENDING, OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED):
            store.transition(created.intent.intent_id, status)
        amended = store.amend(created.intent.intent_id, quantity=Decimal("12"), limit_price=Decimal("101"), actor="broker")
        self.assertEqual((amended.intent.intent_id, amended.intent.quantity, amended.intent.limit_price), (created.intent.intent_id, Decimal("12"), Decimal("101")))
        partially_filled = store.ingest_fill("fill-amend", amended.intent.intent_id, Decimal("10"), Decimal("100"), occurred_at=NOW)
        with self.assertRaisesRegex(PaperOmsError, "amendment"):
            store.amend(partially_filled.intent.intent_id, quantity=Decimal("9"), limit_price=Decimal("100"))
        store.close()
