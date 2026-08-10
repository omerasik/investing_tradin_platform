import json
import logging
import unittest
from decimal import Decimal

from trade_platform.observability import MetricsRegistry, StructuredEventLogger


class CapturingLogger:
    def __init__(self) -> None:
        self.message = ""

    def info(self, message: str) -> None:
        self.message = message


class ObservabilityTests(unittest.TestCase):
    def test_metrics_increment_and_snapshot(self) -> None:
        metrics = MetricsRegistry(); metrics.increment("risk.rejected"); metrics.increment("risk.rejected")
        self.assertEqual(metrics.snapshot(), {"risk.rejected": 2})

    def test_structured_event_is_json(self) -> None:
        logger = CapturingLogger(); StructuredEventLogger(logger).emit("risk.rejected", amount=Decimal("1.2"))
        payload = json.loads(logger.message)
        self.assertEqual(payload["event_type"], "risk.rejected")
        self.assertEqual(payload["amount"], "1.2")


if __name__ == "__main__":
    unittest.main()
