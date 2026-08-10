from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from trade_platform.corporate_actions import (CorporateAction, CorporateActionError, CorporateActionType, SQLiteCorporateActionStore, apply_actions)
from trade_platform.paper_execution import CashBalance, Position


UTC = timezone.utc
ANNOUNCED = datetime(2025, 1, 1, tzinfo=UTC)
EFFECTIVE = datetime(2025, 1, 5, tzinfo=UTC)


def action(action_type: CorporateActionType, **changes: object) -> CorporateAction:
    values: dict[str, object] = dict(instrument_id="US:NYSE:ACME", action_type=action_type, announced_at=ANNOUNCED, effective_at=EFFECTIVE, ingested_at=ANNOUNCED, provider="fixture-actions", source_identifier=action_type.value.lower())
    if action_type is CorporateActionType.SPLIT:
        values["ratio"] = Decimal("2")
    elif action_type is CorporateActionType.CASH_DIVIDEND:
        values.update(amount_per_share=Decimal("1"), currency="USD")
    elif action_type is CorporateActionType.SYMBOL_CHANGE:
        values["successor_instrument_id"] = "US:NYSE:NEWACME"
    else:
        values.update(cash_out_price=Decimal("12"), currency="USD")
    values.update(changes)
    return CorporateAction(**values)


class CorporateActionTests(unittest.TestCase):
    def test_actions_apply_splits_dividends_symbol_changes_and_delisting(self) -> None:
        position = Position("US:NYSE:ACME", Decimal("10"), Decimal("20"))
        cash = CashBalance("USD", Decimal("100"))
        split = action(CorporateActionType.SPLIT)
        dividend = action(CorporateActionType.CASH_DIVIDEND, effective_at=EFFECTIVE + timedelta(days=1))
        symbol_change = action(CorporateActionType.SYMBOL_CHANGE, effective_at=EFFECTIVE + timedelta(days=2))
        result = apply_actions(position, cash, [split, dividend, symbol_change], as_of=EFFECTIVE + timedelta(days=2))
        self.assertEqual((result.position.instrument_id, result.position.quantity, result.position.average_cost, result.cash.amount), ("US:NYSE:NEWACME", Decimal("20"), Decimal("10"), Decimal("120")))
        with self.assertRaisesRegex(CorporateActionError, "future"):
            apply_actions(position, cash, [split], as_of=EFFECTIVE - timedelta(seconds=1))
        delisting = action(CorporateActionType.DELISTING, instrument_id="US:NYSE:NEWACME", effective_at=EFFECTIVE + timedelta(days=3))
        exited = apply_actions(result.position, result.cash, [delisting], as_of=delisting.effective_at)
        self.assertEqual((exited.position.quantity, exited.cash.amount), (Decimal("0"), Decimal("360")))

    def test_as_of_query_excludes_unannounced_or_uningested_revisions(self) -> None:
        store = SQLiteCorporateActionStore()
        initial = action(CorporateActionType.SPLIT)
        future_revision = action(CorporateActionType.SPLIT, revision=1, announced_at=ANNOUNCED + timedelta(days=10), effective_at=EFFECTIVE + timedelta(days=10), ingested_at=ANNOUNCED + timedelta(days=10), ratio=Decimal("3"))
        store.ingest([initial, future_revision])
        self.assertEqual([item.ratio for item in store.available_as_of("US:NYSE:ACME", ANNOUNCED)], [Decimal("2")])
        self.assertEqual([item.ratio for item in store.available_as_of("US:NYSE:ACME", future_revision.ingested_at)], [Decimal("3")])

    def test_action_contract_rejects_invalid_shape_and_duplicate_revision(self) -> None:
        store = SQLiteCorporateActionStore()
        with self.assertRaisesRegex(CorporateActionError, "dividend_requires"):
            store.ingest([CorporateAction("US:NYSE:ACME", CorporateActionType.CASH_DIVIDEND, ANNOUNCED, EFFECTIVE, ANNOUNCED, "fixture", "bad")])
        store.ingest([action(CorporateActionType.SPLIT)])
        with self.assertRaisesRegex(CorporateActionError, "duplicate"):
            store.ingest([action(CorporateActionType.SPLIT)])
