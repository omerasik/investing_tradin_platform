import time
import unittest
from uuid import uuid4

from trade_platform.audit import SQLiteAuditStore


class AuditStoreTests(unittest.TestCase):
    def test_append_and_read_are_lossless(self) -> None:
        store = SQLiteAuditStore()
        created = store.append("risk.rejected", "risk-engine", {"reason": "stale_market_data"})
        events = store.recent()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], created)

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SQLiteAuditStore().recent(0)

    def test_query_is_bounded_filtered_and_deterministically_ordered(self) -> None:
        store = SQLiteAuditStore()
        first = store.append("risk.rejected", "risk-engine", {"reason": "stale"})
        time.sleep(0.002)
        second = store.append("signal.blocked", "signal-engine", {"reason": "risk"})
        time.sleep(0.002)
        third = store.append("risk.rejected", "risk-engine", {"reason": "again"})

        all_items, has_more = store.query(limit=200, offset=0)
        self.assertEqual(([item.event_id for item in all_items], has_more), ([third.event_id, second.event_id, first.event_id], False))

        risk_only, _ = store.query(event_type="risk.rejected", limit=200, offset=0)
        self.assertEqual({item.event_id for item in risk_only}, {first.event_id, third.event_id})

        actor_only, _ = store.query(actor="signal-engine", limit=200, offset=0)
        self.assertEqual([item.event_id for item in actor_only], [second.event_id])

        page_one, has_more_page_one = store.query(limit=2, offset=0)
        self.assertEqual((len(page_one), has_more_page_one), (2, True))
        page_two, has_more_page_two = store.query(limit=2, offset=2)
        self.assertEqual((len(page_two), has_more_page_two), (1, False))

    def test_query_rejects_out_of_bounds_limit_or_offset(self) -> None:
        store = SQLiteAuditStore()
        with self.assertRaises(ValueError):
            store.query(limit=0, offset=0)
        with self.assertRaises(ValueError):
            store.query(limit=201, offset=0)
        with self.assertRaises(ValueError):
            store.query(limit=10, offset=-1)

    def test_get_returns_none_for_unknown_event(self) -> None:
        store = SQLiteAuditStore()
        created = store.append("risk.rejected", "risk-engine", {"reason": "stale"})
        self.assertEqual(store.get(created.event_id), created)
        self.assertIsNone(store.get(uuid4()))


if __name__ == "__main__":
    unittest.main()
