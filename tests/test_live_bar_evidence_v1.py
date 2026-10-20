"""Consumed live evidence: exact values, provenance, immutable progress and as-of reads."""
from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import NAMESPACE_URL, uuid4, uuid5

from tests.test_live_signals_v1 import _bar
from tests.test_runner_liveness_v1 import (
    SOURCE,
    STUDY,
    SYMBOL,
    TRIAL,
    Clocks,
    Gate,
    MemoryStore,
    at,
    candidate,
)
from trade_platform.live_bar_evidence_v1 import (
    PostgresLiveBarStoreV1,
    _observations,
    _verified_progress_row_v1,
    live_bar_payload_v1,
    restore_live_bar_v1,
)
from trade_platform.live_signals_v1 import LiveBarV1
from trade_platform.runner_liveness_v1 import (
    RunnerLivenessError,
    RunnerProgressRecorderV1,
    RunnerProgressV1,
)
from trade_platform.strategy_lab_study_v1 import identity_hash_v1


def fixture(*, warmup: bool = False):
    clock = Clocks(at(1 if warmup else 0))
    store = MemoryStore()
    recorder = RunnerProgressRecorderV1(store, runner='test', symbol=SYMBOL, capture_source_id=SOURCE,
                                       candidates=[candidate()], holdout_gate=Gate(), wall_clock=lambda: clock.wall,
                                       monotonic_clock=lambda: clock.monotonic)
    start = recorder.start()
    value = _bar(0, Decimal('11.00000000'), Decimal('10.00000000'))
    value = replace(value, closing_record_hash='a' * 64, trade_manifest_hash='b' * 64,
                    complete_market_knowledge_micros=value.bar_open_micros + 60_000_001)
    bar = LiveBarV1(SYMBOL, 0, uuid4(), value, value.bar_open_micros * 1000 + 60_000_000_000,
                    1000, 'c' * 64, SOURCE)
    if warmup:
        bar = replace(bar, completed_by_arrival_nanos=bar.completed_by_arrival_nanos - 1000,
                      bar=replace(value, complete_market_knowledge_micros=value.bar_open_micros + 60_000_000))
    clock.advance_to(at(2))
    progress = recorder.on_poll([bar])
    assert progress is not None
    return start, progress, bar


def progress_row(progress):
    identity = progress.identity
    binding = identity['binding']
    columns = {
        'progress_id': str(progress.progress_id), 'idempotency_key': progress.idempotency_key,
        'content_hash': progress.content_hash, 'runner_session_id': identity['runner_session_id'],
        'sequence': identity['sequence'], 'kind': identity['kind'], 'binding_hash': identity['binding_hash'],
        'previous_progress_hash': identity['previous_progress_hash'], 'bar_count': len(identity['bars']),
        'symbol': None if binding is None else binding['symbol'],
        'capture_source_id': None if binding is None else binding['capture_source_id'],
        'host_wall_at': (datetime(1970,1,1,tzinfo=UTC) + timedelta(microseconds=identity['host_wall_micros'])).isoformat(),
        'recorded_at': at(2).isoformat(),
    }
    return progress.content_hash, identity, columns


def reader(start, progress, observations=None):
    database = MagicMock()
    cursor = database.transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    observations = _observations(progress, start.identity) if observations is None else observations
    cursor.fetchall.side_effect = [
        [progress_row(start), progress_row(progress)],
        [(identity_hash_v1(o), o, uuid5(NAMESPACE_URL, "runner-consumed-live-bar-v1:" + identity_hash_v1(o)),
          o["runner_session_id"], o["progress_hash"], o["index"], o["live_bar"]["feed_segment"],
          o["live_bar"]["bar"]["bar_open_micros"]) for o in observations],
    ]
    return PostgresLiveBarStoreV1(database)


class ConsumedLiveBarsTests(unittest.TestCase):
    def test_exact_decimal_round_trip_and_provisional_clock(self):
        _, _, bar = fixture()
        restored = restore_live_bar_v1(live_bar_payload_v1(bar))
        self.assertEqual(bar, restored)
        self.assertEqual('11.00000000', str(restored.bar.close_price))
        self.assertEqual('c' * 64, restored.latest_clock_sample_hash)
        self.assertTrue(restored.capture_source_id)

    def test_warmup_is_persisted_without_forward_claim(self):
        start, progress, bar = fixture(warmup=True)
        self.assertEqual([], progress.identity['bars'])
        self.assertEqual('HEARTBEAT', progress.identity['kind'])
        observation = _observations(progress, start.identity)[0]
        self.assertTrue(observation['warmup'])
        self.assertEqual([bar], reader(start, progress).for_candidate(
            study_id=str(STUDY), trial_id=TRIAL, symbol=SYMBOL, as_of=at(3)))

    def test_reader_rejects_missing_and_changed_bar_progress(self):
        start, progress, _ = fixture()
        with self.assertRaisesRegex(RunnerLivenessError, 'missing_or_progress'):
            reader(start, progress, []).for_candidate(study_id=str(STUDY), trial_id=TRIAL,
                                                       symbol=SYMBOL, as_of=at(3))
        changed = copy.deepcopy(progress.identity)
        changed['bars'][0]['trade_manifest_hash'] = 'e' * 64
        with self.assertRaisesRegex(RunnerLivenessError, 'disagree_with_progress'):
            _observations(RunnerProgressV1.build(changed), start.identity)

    def test_source_and_admission_mismatch_refused(self):
        start, progress, _ = fixture()
        changed = copy.deepcopy(progress.identity)
        changed['consumed_live_bars'][0]['live_bar']['capture_source_id'] = str(uuid4())
        with self.assertRaisesRegex(RunnerLivenessError, 'progress_mismatch'):
            _observations(RunnerProgressV1.build(changed), start.identity)
        changed = copy.deepcopy(progress.identity)
        changed['consumed_live_bars'][0]['admitted'] = False
        with self.assertRaisesRegex(RunnerLivenessError, 'admission'):
            _observations(RunnerProgressV1.build(changed), start.identity)

    def test_finalized_missing_sample_and_early_clock_refused(self):
        _, _, bar = fixture()
        for key, value in [('knowledge_kind', 'FINALIZED'), ('latest_clock_sample_hash', None)]:
            payload = live_bar_payload_v1(bar)
            payload[key] = value
            with self.assertRaises(RunnerLivenessError):
                restore_live_bar_v1(payload)
        with self.assertRaises(RunnerLivenessError):
            live_bar_payload_v1(replace(bar, bar=replace(bar.bar, complete_market_knowledge_micros=0)))

    def test_read_rejects_tampered_chain_and_filters_future_knowledge(self):
        start, progress, bar = fixture()
        changed = copy.deepcopy(progress.identity)
        changed['previous_progress_hash'] = 'd' * 64
        with self.assertRaisesRegex(RunnerLivenessError, 'chain_invalid'):
            reader(start, RunnerProgressV1.build(changed)).for_candidate(
                study_id=str(STUDY), trial_id=TRIAL, symbol=SYMBOL, as_of=at(3))
        future = replace(bar, bar=replace(bar.bar, complete_market_knowledge_micros=int(at(4).timestamp()*1e6)))
        changed = copy.deepcopy(progress.identity)
        changed['consumed_live_bars'][0]['live_bar'] = live_bar_payload_v1(future)
        progress = RunnerProgressV1.build(changed)
        self.assertIsNone(reader(start, progress).for_candidate(study_id=str(STUDY), trial_id=TRIAL,
                                                                symbol=SYMBOL, as_of=at(3)))

    def test_binding_filter_and_timezone(self):
        start, progress, _ = fixture()
        self.assertIsNone(reader(start, progress).for_candidate(
            study_id=str(STUDY), trial_id=TRIAL, symbol=SYMBOL, as_of=at(3), watched_as={'authority': 'OTHER'}))
        with self.assertRaises(RunnerLivenessError):
            reader(start, progress).for_candidate(study_id=str(STUDY), trial_id=TRIAL,
                                                  symbol=SYMBOL, as_of=at(3).replace(tzinfo=None))

    def test_relational_identity_tamper_is_refused(self):
        start, progress, _ = fixture()
        repository = reader(start, progress)
        cursor = repository._database.transaction.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        observation = _observations(progress, start.identity)[0]
        cursor.fetchall.side_effect = [
            [progress_row(start), progress_row(progress)],
            [(identity_hash_v1(observation), observation, uuid4(), observation['runner_session_id'],
              observation['progress_hash'], observation['index'], observation['live_bar']['feed_segment'],
              observation['live_bar']['bar']['bar_open_micros'])],
        ]
        with self.assertRaisesRegex(RunnerLivenessError, 'hash_mismatch'):
            repository.for_candidate(study_id=str(STUDY), trial_id=TRIAL, symbol=SYMBOL, as_of=at(3))

    def test_progress_scalar_and_as_of_authority_is_verified(self):
        start, progress, _ = fixture()
        digest, identity, columns = progress_row(progress)
        for key, value in [('host_wall_at', at(0).isoformat()), ('sequence', 99),
                           ('binding_hash', 'e' * 64), ('runner_session_id', str(uuid4())),
                           ('progress_id', str(uuid4())), ('recorded_at', at(4).isoformat())]:
            with self.subTest(key=key), self.assertRaisesRegex(RunnerLivenessError, 'scalar_or_as_of'):
                _verified_progress_row_v1(digest, identity, {**columns, key: value}, at(3))
        digest, start_identity, start_columns = progress_row(start)
        with self.assertRaisesRegex(RunnerLivenessError, 'scalar_or_as_of'):
            _verified_progress_row_v1(digest, start_identity,
                                      {**start_columns, 'capture_source_id': str(uuid4())}, at(3))
        # Even self-consistent future identity/scalars cannot bypass the as-of filter.
        future = copy.deepcopy(identity)
        future['host_wall_micros'] = int(at(4).timestamp()*1e6)
        digest, future, columns = progress_row(RunnerProgressV1.build(future))
        with self.assertRaisesRegex(RunnerLivenessError, 'scalar_or_as_of'):
            _verified_progress_row_v1(digest, future, columns, at(3))
