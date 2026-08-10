import unittest

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


if __name__ == "__main__":
    unittest.main()
