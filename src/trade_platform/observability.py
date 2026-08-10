"""Small dependency-free observability primitives for local development."""

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .domain import utc_now


@dataclass(slots=True)
class MetricsRegistry:
    _counters: Counter[str] = field(default_factory=Counter)

    def increment(self, name: str) -> None:
        self._counters[name] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self._counters)


class StructuredEventLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("trade_platform")

    def emit(self, event_type: str, **attributes: object) -> None:
        payload = {"timestamp": utc_now().isoformat(), "event_type": event_type, **attributes}
        self._logger.info(json.dumps(payload, sort_keys=True, default=str))
