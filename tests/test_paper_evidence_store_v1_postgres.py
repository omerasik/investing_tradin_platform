"""Paper evidence store on real PostgreSQL: append-only rows, PIT reads, idempotency, registry and holdout checks.

Fixtures are real evidence (see :mod:`tests.test_paper_evidence_store_v1`), all dated in the past
and before the current holdout. Rows are tampered with only in memory (the tables are immutable)
and replayed through a stub cursor, to prove a row that does not re-derive raises on read.
"""

from __future__ import annotations

import os
import shutil
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from tests.test_paper_evidence_store_v1 import (
    PARITY_AS_OF,
    PARITY_DAY0,
    Fixtures,
    Scripted,
    bridge,
    funding_as_of,
    funding_hours,
)

CYCLE = "cycle-2026-08-20"
FUTURE_CYCLE = "cycle-2031-03-17"  # registered prospectively by this test (idempotently)


class Replay:
    """A cursor whose every SELECT returns the given rows (a tampered copy of stored rows)."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PaperEvidenceStorePostgresTests(unittest.TestCase):
    fx: Fixtures
    database: Any

    @classmethod
    def setUpClass(cls) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.persistence import PostgresDatabase
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        cls.fx = Fixtures.get()
        cls.database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        PostgresStrategyLabLedgerV1(cls.database).register_study(cls.fx.study)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        if Fixtures._built is not None:
            shutil.rmtree(Fixtures._built.temp, ignore_errors=True)

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            yield cursor

    def _refused(self, code: str, call: Any) -> None:
        from trade_platform.paper_evidence_store_v1 import PaperEvidenceStoreError

        with self.assertRaises(PaperEvidenceStoreError) as refused, self.cursor() as cursor:
            call(cursor)
        self.assertEqual(code, str(refused.exception))

    @property
    def ids(self) -> tuple[UUID, UUID]:
        return self.fx.study.study_id, UUID(self.fx.trial)

    # -- fill parity ----------------------------------------------------------------------

    def test_parity_is_append_only_point_in_time_and_idempotent(self) -> None:
        from trade_platform.paper_evidence_store_v1 import (
            read_fill_parity_v1,
            record_fill_parity_v1,
        )

        study_id, trial_id = self.ids
        reports = [("INCOMPLETE", self.fx.report(replay=self.fx.bars[:60]), PARITY_AS_OF),
                   ("FAIL", self.fx.report(replay=self.fx.failing_replay()), PARITY_AS_OF + timedelta(days=1)),
                   ("PASS", self.fx.report(), PARITY_AS_OF + timedelta(days=2))]

        def read(at: datetime) -> Any:
            with self.cursor() as cursor:
                return read_fill_parity_v1(cursor, cycle_id=CYCLE, study_id=study_id, trial_id=trial_id, as_of=at)

        self.assertIsNone(read(PARITY_AS_OF - timedelta(seconds=1)))  # ABSENT, never INCOMPLETE
        stored = {}
        for status, report, as_of in reports:
            with self.cursor() as cursor:
                (row,) = record_fill_parity_v1(cursor, report, cycle_id=CYCLE, as_of=as_of,
                                               recorded_at=as_of + timedelta(hours=1))
            self.assertEqual((status, report.content_hash, study_id, trial_id, "BTCUSDT"),
                             (row.status, row.report_content_hash, row.study_id, row.trial_id, row.symbol))
            self.assertEqual(dict(report.identity), dict(row.identity))
            self.assertEqual(tuple(report.reasons), row.reasons)
            self.assertEqual(PARITY_DAY0, row.window_start)
            self.assertEqual((PARITY_DAY0 + timedelta(minutes=89)), row.window_end)
            self.assertEqual(("v" * 64, None), (row.authority_evidence_hash, row.holdout_end_exclusive))
            stored[status] = row
        # Known only once recorded: between the FAIL's as_of and its recorded_at the INCOMPLETE stands.
        self.assertEqual("INCOMPLETE", read(PARITY_AS_OF + timedelta(days=1, minutes=30)).status)
        self.assertEqual(stored["FAIL"], read(PARITY_AS_OF + timedelta(days=1, hours=2)))
        self.assertEqual(stored["PASS"], read(PARITY_AS_OF + timedelta(days=3)))
        # An identical re-record returns the stored row unchanged (earliest knowledge stands).
        with self.cursor() as cursor:
            (again,) = record_fill_parity_v1(cursor, reports[2][1], cycle_id=CYCLE,
                                             as_of=PARITY_AS_OF + timedelta(days=5),
                                             recorded_at=PARITY_AS_OF + timedelta(days=5))
        self.assertEqual(stored["PASS"], again)
        with self.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM paper_fill_parity_assessments WHERE study_id=%s AND trial_id=%s "
                           "AND report_content_hash=%s", (study_id, trial_id, reports[2][1].content_hash))
            self.assertEqual(1, cursor.fetchone()[0])
        self._assert_immutable("paper_fill_parity_assessments", "assessment_id", stored["PASS"].assessment_id)
        self._assert_parity_tamper_is_refused(stored["PASS"])

    def _assert_parity_tamper_is_refused(self, row: Any) -> None:
        from trade_platform import paper_evidence_store_v1 as store

        with self.cursor() as cursor:
            cursor.execute(f"SELECT {', '.join(store._PARITY_COLUMNS)} FROM paper_fill_parity_assessments "  # nosec
                           "WHERE assessment_id=%s", (row.assessment_id,))
            original = cursor.fetchone()
        study_id, trial_id = self.ids
        index = {name: position for position, name in enumerate(store._PARITY_COLUMNS)}

        def tampered(**changes: Any) -> tuple[Any, ...]:
            values = list(original)
            for name, value in changes.items():
                values[index[name]] = value
            return tuple(values)

        identity = dict(original[index["identity"]])
        cases = [
            tampered(status="FAIL", reasons=["BAR_DIVERGED"]),
            tampered(identity={**identity, "tolerance": "1bp"}),
            tampered(window_end=row.window_end + timedelta(minutes=1)),
            tampered(as_of=row.window_end),
            tampered(symbol="ETHUSDT"),
            tampered(source_hashes={**row.source_hashes, "live_fill_hashes": []}),
            tampered(holdout_end_exclusive=datetime(2026, 9, 1, tzinfo=UTC)),
            tampered(cycle_id=FUTURE_CYCLE),
            tampered(assessment_id=uuid4()),
        ]
        for case in cases:
            with self.assertRaises(store.PaperEvidenceStoreError) as refused:
                store.read_fill_parity_v1(Replay([original, case]), cycle_id=CYCLE, study_id=study_id,
                                          trial_id=trial_id, as_of=datetime(2026, 10, 1, tzinfo=UTC))
            self.assertEqual(store.STORED_FILL_PARITY_DOES_NOT_REDERIVE, str(refused.exception))
        # The same key with a different payload is a conflict, never an overwrite.
        values = dict(zip(store._PARITY_COLUMNS, original, strict=True)) | {"authority_evidence_hash": "w" * 64}
        with self.assertRaises(store.PaperEvidenceStoreError) as refused:
            store._insert_parity(Scripted([original]), values)
        self.assertEqual(store.CONFLICTING_DUPLICATE_FILL_PARITY, str(refused.exception))

    def test_parity_registry_and_holdout_refusals(self) -> None:
        from tests.test_live_signals_v1 import _live_bars
        from tests.test_strategy_sdk_v1 import _walk
        from trade_platform import paper_evidence_store_v1 as store

        report = self.fx.report()

        def record(cycle_id: str, item: Any, as_of: datetime = PARITY_AS_OF) -> Any:
            return lambda cursor: store.record_fill_parity_v1(cursor, item, cycle_id=cycle_id, as_of=as_of,
                                                              recorded_at=as_of)

        self._refused(store.CYCLE_UNKNOWN, record("cycle-1999-01-01", report))
        with self.cursor() as cursor:
            cursor.execute("INSERT INTO strategy_lab_research_cycles (cycle_id, holdout_start, note) VALUES "
                           "(%s, TIMESTAMPTZ '2031-03-17 00:00:00+00', 'paper evidence store test cycle') "
                           "ON CONFLICT DO NOTHING", (FUTURE_CYCLE,))
        self._refused(store.HOLDOUT_GATE_NOT_OF_THIS_CYCLE, record(FUTURE_CYCLE, report))
        # A gate claiming an opening the registry does not hold.
        claimed = self.fx.report(gate=self.fx.gate(datetime(2026, 9, 1, tzinfo=UTC)))
        self._refused(store.HOLDOUT_GATE_OPENING_NOT_REGISTERED, record(CYCLE, claimed))
        # Forward bars inside the unopened holdout: the recorded gate never admitted them.
        forward = _live_bars(_walk(90, seed=21))
        inside = self.fx.report(live_bars=forward, replay=forward)
        self.assertEqual("INCOMPLETE", inside.status)
        self._refused(store.EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT,
                      record(CYCLE, inside, datetime(2026, 10, 2, tzinfo=UTC)))
        # Live signals evaluated under another (opened) gate, assessed under the unopened one.
        previewed = self.fx.report(live_bars=forward, replay=forward,
                                   live_gate=self.fx.gate(datetime(2026, 9, 1, tzinfo=UTC)))
        self.assertEqual("FAIL", previewed.status)
        self._refused(store.EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT,
                      record(CYCLE, previewed, datetime(2026, 10, 2, tzinfo=UTC)))
        with self.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM paper_fill_parity_assessments WHERE report_content_hash IN "
                           "(%s, %s, %s)", (claimed.content_hash, inside.content_hash, previewed.content_hash))
            self.assertEqual(0, cursor.fetchone()[0])

    # -- funding evidence -----------------------------------------------------------------

    def test_funding_is_append_only_point_in_time_and_idempotent(self) -> None:
        from trade_platform.paper_evidence_store_v1 import (
            read_funding_evidence_v1,
            record_funding_evidence_v1,
        )

        study_id, trial_id = self.ids
        evidence = self.fx.evidence(bridge())
        base = funding_as_of()
        plan = [
            ("NO_FUNDING_EVIDENCE_SUPPLIED", None, self.fx.fills((funding_hours(1), funding_hours(10))), base),
            ("EVIDENCE_LINKED", evidence, self.fx.fills((funding_hours(1), None)), base + timedelta(hours=1)),
            ("EVIDENCE_COMPLETE", evidence, self.fx.fills((funding_hours(1), funding_hours(10))),
             base + timedelta(hours=2)),
        ]

        def read(at: datetime) -> Any:
            with self.cursor() as cursor:
                return read_funding_evidence_v1(cursor, cycle_id=CYCLE, study_id=study_id, trial_id=trial_id,
                                                as_of=at)

        self.assertIsNone(read(base - timedelta(seconds=1)))
        stored = {}
        for state, item, fills, as_of in plan:
            section = self.fx.section(fills, *([] if item is None else [item]), as_of=as_of)
            with self.cursor() as cursor:
                row = record_funding_evidence_v1(cursor, item, section=section, cycle_id=CYCLE, as_of=as_of,
                                                 recorded_at=as_of + timedelta(minutes=5))
            self.assertEqual((state, section, section["evidence_hash"]), (row.state, dict(row.evidence),
                                                                           row.evidence_hash))
            self.assertEqual((tuple(section["completeness_blockers"]), tuple(section["economic_blockers"])),
                             (row.completeness_blockers, row.economic_blockers))
            self.assertEqual({} if item is None else {item.bridges[0][0]: item.bridges[0][1]["funding_window"][
                "content_hash"]}, dict(row.bridge_hashes))
            stored[state] = row
            self.assertEqual(row, read(as_of + timedelta(minutes=5)))  # verbatim, distinct from ABSENT
        self.assertIsNone(stored["NO_FUNDING_EVIDENCE_SUPPLIED"].window_start)
        self.assertEqual("NO_FUNDING_EVIDENCE_SUPPLIED", read(base + timedelta(hours=1, minutes=1)).state)
        self.assertEqual(stored["EVIDENCE_COMPLETE"], read(base + timedelta(days=1)))
        complete_section = self.fx.section(plan[2][2], evidence, as_of=plan[2][3])
        with self.cursor() as cursor:
            again = record_funding_evidence_v1(cursor, evidence, section=complete_section, cycle_id=CYCLE,
                                               as_of=base + timedelta(days=2), recorded_at=base + timedelta(days=2))
        self.assertEqual(stored["EVIDENCE_COMPLETE"], again)
        self._assert_immutable("paper_funding_evidence_records", "record_id", again.record_id)
        self._assert_funding_tamper_is_refused(again)

    def _assert_funding_tamper_is_refused(self, row: Any) -> None:
        from trade_platform import paper_evidence_store_v1 as store

        with self.cursor() as cursor:
            cursor.execute(f"SELECT {', '.join(store._FUNDING_COLUMNS)} FROM paper_funding_evidence_records "  # nosec
                           "WHERE record_id=%s", (row.record_id,))
            original = cursor.fetchone()
        study_id, trial_id = self.ids
        index = {name: position for position, name in enumerate(store._FUNDING_COLUMNS)}

        def tampered(**changes: Any) -> tuple[Any, ...]:
            values = list(original)
            for name, value in changes.items():
                values[index[name]] = value
            return tuple(values)

        evidence = dict(original[index["evidence"]])
        bridges = list(original[index["bridges"]])
        first_identity = dict(bridges[0]["identity"]) | {"status": "REFUSED_PRICE_EVIDENCE"}
        cases = [
            tampered(state="EVIDENCE_LINKED"),
            tampered(evidence={**evidence, "settlements_outside_holdings": 0}),
            tampered(bridges=[{**bridges[0], "identity": first_identity}]),
            tampered(evidence_validation_hash="a" * 64),
            tampered(as_of=row.as_of - timedelta(days=1)),
            tampered(economic_blockers=["FUNDING_CHARGE_NOT_CALCULATED_IN_V1"]),
            tampered(window_end=row.window_end + timedelta(minutes=1)),
            tampered(bridge_hashes={}),
            tampered(record_id=uuid4()),
        ]
        for case in cases:
            with self.assertRaises(store.PaperEvidenceStoreError) as refused:
                store.read_funding_evidence_v1(Replay([original, case]), cycle_id=CYCLE, study_id=study_id,
                                               trial_id=trial_id, as_of=datetime(2026, 10, 1, tzinfo=UTC))
            self.assertEqual(store.STORED_FUNDING_EVIDENCE_DOES_NOT_REDERIVE, str(refused.exception))
        values = dict(zip(store._FUNDING_COLUMNS, original, strict=True)) | {"evidence_validation_hash": "a" * 64}
        with self.assertRaises(store.PaperEvidenceStoreError) as refused:
            store._insert_funding(Scripted([original]), values)
        self.assertEqual(store.CONFLICTING_DUPLICATE_FUNDING_EVIDENCE, str(refused.exception))

    def test_funding_registry_and_holdout_refusals(self) -> None:
        from trade_platform import paper_evidence_store_v1 as store
        from trade_platform.paper_funding_evidence_v1 import candidate_funding_evidence_v1

        evidence = self.fx.evidence(bridge())
        section = self.fx.section(self.fx.fills((funding_hours(1), funding_hours(10))), evidence)

        def record(item: Any, body: dict[str, Any], *, cycle_id: str = CYCLE, as_of: datetime | None = None) -> Any:
            at = as_of or funding_as_of()
            return lambda cursor: store.record_funding_evidence_v1(cursor, item, section=body, cycle_id=cycle_id,
                                                                   as_of=at, recorded_at=at)

        self._refused(store.CYCLE_UNKNOWN, record(evidence, section, cycle_id="cycle-1999-01-01"))
        # Holdings inside the unopened holdout (fills on 2026-09-01) are refused.
        september = datetime(2026, 9, 1, tzinfo=UTC)
        opened = int(september.timestamp()) * 1_000_000
        inside_as_of = september + timedelta(days=1)
        inside = self.fx.section(self.fx.fills((opened + 3_600_000_000, opened + 36_000_000_000)), evidence,
                                 as_of=inside_as_of)
        self._refused(store.EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT, record(evidence, inside, as_of=inside_as_of))
        # A candidate the Strategy Lab ledger does not hold.
        stranger, trial = str(uuid4()), str(uuid4())
        fills = self.fx.fills((funding_hours(1), funding_hours(10)))
        for fill in fills:
            fill.update(study_id=stranger, trial_id=trial)
        unknown = candidate_funding_evidence_v1(study_id=stranger, trial_id=trial, symbol="BTCUSDT",
                                                bridges=[bridge()])
        self._refused(store.CANDIDATE_NOT_IN_STRATEGY_LAB,
                      record(unknown, self.fx.section(fills, unknown)))

    # -- immutability ---------------------------------------------------------------------

    def _assert_immutable(self, table: str, key: str, value: UUID) -> None:
        from trade_platform.persistence import PersistenceError

        for statement in (f"UPDATE {table} SET recorded_at = recorded_at WHERE {key}=%s",  # nosec B608
                          f"DELETE FROM {table} WHERE {key}=%s"):  # nosec B608
            with self.assertRaises(PersistenceError), self.cursor() as cursor:
                cursor.execute(statement, (value,))
        with self.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE {key}=%s", (value,))  # nosec B608
            self.assertEqual(1, cursor.fetchone()[0])


if __name__ == "__main__":
    unittest.main()
