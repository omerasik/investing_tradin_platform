"""Deterministic coverage for every Cycle 12 data-health detector."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_platform.data_health import (
    DataHealthAction,
    DataHealthCheck,
    DataHealthObservation,
    DataHealthPolicy,
    DataHealthScope,
    build_assessment,
    detect_data_health,
)


def observation(provider: str, event_at: datetime, **changes: object) -> DataHealthObservation:
    base = DataHealthObservation(
        provider, "US:XNAS:HEALTH", event_at, event_at + timedelta(minutes=1), 0,
        Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), Decimal("100"),
        "America/New_York", "America/New_York", True,
    )
    return replace(base, **changes)


class DataHealthTests(unittest.TestCase):
    def test_all_required_detectors_and_action_levels_are_explicit(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        records = [
            observation(
                "provider-a", start, high=Decimal("9"), volume=Decimal("-1"),
                original_timezone="UTC", session_valid=False,
                corporate_action_consistent=False,
            ),
            observation("provider-a", start),
            observation("provider-b", start, close=Decimal("20"), high=Decimal("21")),
            observation("provider-a", start + timedelta(days=4)),
            observation("provider-a", start + timedelta(days=2)),
        ]
        policy = DataHealthPolicy(
            "health-v1", start, start + timedelta(days=6), timedelta(days=1),
            timedelta(days=1), Decimal("0.05"), 10,
        )
        findings = detect_data_health(records, policy)
        self.assertEqual(
            {item.check_type for item in findings},
            set(DataHealthCheck),
        )
        self.assertEqual(
            tuple(DataHealthAction),
            (DataHealthAction.INFO, DataHealthAction.WARN,
             DataHealthAction.DEGRADE_CONFIDENCE, DataHealthAction.BLOCK_INSTRUMENT,
             DataHealthAction.BLOCK_STRATEGY, DataHealthAction.BLOCK_ASSET_CLASS,
             DataHealthAction.GLOBAL_BLOCK),
        )
        assessment = build_assessment(
            records, policy, scope_type=DataHealthScope.INSTRUMENT,
            scope_value="US:XNAS:HEALTH", evaluated_at=start + timedelta(days=7),
        )
        self.assertTrue(assessment.blocking)
        self.assertEqual(assessment.max_action, DataHealthAction.BLOCK_STRATEGY)

    def test_clean_dataset_is_nonblocking_and_empty_dataset_fails_closed(self) -> None:
        event = datetime(2024, 1, 1, tzinfo=UTC)
        policy = DataHealthPolicy(
            "health-v1", event, event, timedelta(days=1), timedelta(0), Decimal("0.05"), 1
        )
        clean = build_assessment(
            [observation("provider", event)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value="US:XNAS:HEALTH",
            evaluated_at=event + timedelta(minutes=2),
        )
        self.assertFalse(clean.blocking)
        self.assertEqual(clean.findings, ())
        empty = build_assessment(
            [], policy, scope_type=DataHealthScope.GLOBAL, scope_value="*",
            evaluated_at=event + timedelta(minutes=2),
        )
        self.assertTrue(empty.blocking)
        self.assertEqual(
            {item.check_type for item in empty.findings},
            {DataHealthCheck.MISSING_BARS, DataHealthCheck.INCOMPLETE_DATASET},
        )

    def test_interval_defaults_to_empty_and_distinguishes_otherwise_identical_assessments(self) -> None:
        """``interval`` is an explicit identity dimension, not an afterthought.

        Two assessments for the same instrument/scope/evaluated_at/findings but a
        different interval must be genuinely distinguishable -- including in
        ``content_hash``, which carries a database-level global UNIQUE constraint
        independent of the (scope_type, scope_value, interval, evaluated_at)
        uniqueness, so it must not coincidentally collide once scope_value alone no
        longer uniquely identifies a series.
        """
        event = datetime(2024, 1, 1, tzinfo=UTC)
        policy = DataHealthPolicy(
            "health-v1", event, event, timedelta(days=1), timedelta(0), Decimal("0.05"), 1
        )
        no_interval = build_assessment(
            [observation("provider", event)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value="US:XNAS:HEALTH",
            evaluated_at=event + timedelta(minutes=2),
        )
        self.assertEqual(no_interval.interval, "")

        daily = build_assessment(
            [observation("provider", event)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value="US:XNAS:HEALTH",
            evaluated_at=event + timedelta(minutes=2), interval="1d",
        )
        minute = build_assessment(
            [observation("provider", event)], policy,
            scope_type=DataHealthScope.INSTRUMENT, scope_value="US:XNAS:HEALTH",
            evaluated_at=event + timedelta(minutes=2), interval="1m",
        )
        self.assertEqual((daily.interval, minute.interval), ("1d", "1m"))
        self.assertEqual(len({no_interval.content_hash, daily.content_hash, minute.content_hash}), 3)
