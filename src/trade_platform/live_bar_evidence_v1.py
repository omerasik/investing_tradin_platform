"""Exact consumed R8 bars, bound to append-only progress; never reconstructed from seals.

Provisional clocks remain verbatim. Warm-up is retained as consumed history but
never becomes a forward-processing claim. A missing legacy payload returns None.

Each progress identity embeds consumed_live_bars entries {live_bar, warmup, admitted}
(in actual consumption order), including gated and warm-up inputs. Only non-warmup
entries also appear in the existing bars progress list. for_candidate returns all
actual consumed bars; consumers match those entries to distinguish strategy history
from forward processing. capture_source_id originates at LiveBarFeedV1, is matched
to STARTED binding, and remains available for independent capture/seal crosschecks.
No raw provenance is invented: capture acceptance and sealed trade/closing hashes
must still be checked by the parity producer.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .first_party_t4_normalization_v1 import T4MinuteBarV1
from .live_signals_v1 import LIVE_PROVISIONAL, LiveBarV1
from .runner_liveness_v1 import RunnerLivenessError, RunnerProgressV1, _session_records
from .strategy_lab_study_v1 import identity_hash_v1

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DECIMALS = ('open_price', 'high_price', 'low_price', 'close_price', 'base_volume', 'quote_turnover')
_SCHEMA = 'runner-consumed-live-bar-v1'


def live_bar_payload_v1(bar: LiveBarV1) -> dict[str, Any]:
    data = asdict(bar.bar)
    for key in _DECIMALS:
        data[key] = str(data[key])
    payload = {'symbol': bar.symbol, 'feed_segment': bar.segment, 'capture_session_id': str(bar.session_id),
               'bar': data, 'completed_by_arrival_nanos': bar.completed_by_arrival_nanos,
               'clock_bound_nanos': bar.clock_bound_nanos, 'knowledge_kind': LIVE_PROVISIONAL,
               'latest_clock_sample_hash': bar.latest_clock_sample_hash,
               'capture_source_id': str(bar.capture_source_id) if bar.capture_source_id else None}
    restore_live_bar_v1(payload)
    return payload


def restore_live_bar_v1(payload: Mapping[str, Any]) -> LiveBarV1:
    """Decode exact Decimal prices, rejecting finalized or incomplete clock evidence."""
    try:
        if payload['knowledge_kind'] != LIVE_PROVISIONAL:
            raise ValueError('not provisional')
        for key in ('latest_clock_sample_hash',):
            if not re.fullmatch('[0-9a-f]{64}', str(payload[key])):
                raise ValueError('clock sample missing')
        data = dict(payload['bar'])
        for key in _DECIMALS:
            data[key] = Decimal(data[key])
            if not data[key].is_finite() or data[key] < 0:
                raise ValueError('invalid decimal')
        for key in ('closing_record_hash', 'trade_manifest_hash'):
            if not re.fullmatch('[0-9a-f]{64}', data[key]):
                raise ValueError('invalid capture hash')
        for key in ('open_is_sequence_ambiguous', 'close_is_sequence_ambiguous'):
            if not isinstance(data[key], bool):
                raise TypeError('ambiguity not boolean')
        for key in ('bar_open_micros', 'open_market_knowledge_micros', 'complete_market_knowledge_micros',
                    'trade_count', 'block_trade_count', 'rpi_trade_count'):
            if isinstance(data[key], bool) or not isinstance(data[key], int):
                raise TypeError('invalid integer')
        for key in ('feed_segment', 'completed_by_arrival_nanos', 'clock_bound_nanos'):
            if isinstance(payload[key], bool) or not isinstance(payload[key], int):
                raise TypeError('invalid integer')
        bar = T4MinuteBarV1(**data)
        if bar.bar_open_micros % 60_000_000 or bar.trade_count <= 0 or payload['feed_segment'] < 0:
            raise ValueError('invalid bar')
        # The completing arrival plus its actual provisional bound is the bar knowledge instant.
        expected = -(-(payload['completed_by_arrival_nanos'] + payload['clock_bound_nanos']) // 1000)
        if bar.complete_market_knowledge_micros < max(expected, bar.bar_close_micros, bar.open_market_knowledge_micros):
            raise ValueError('completion clock mismatch')
        return LiveBarV1(str(payload['symbol']), payload['feed_segment'], UUID(payload['capture_session_id']),
                         bar, payload['completed_by_arrival_nanos'], payload['clock_bound_nanos'],
                         payload['latest_clock_sample_hash'], UUID(payload['capture_source_id']))
    except (KeyError, TypeError, ValueError) as error:
        raise RunnerLivenessError('consumed_live_bar_malformed_or_clock_mismatch') from error


def _observations(progress: RunnerProgressV1, start: Mapping[str, Any]) -> list[dict[str, Any]]:
    identity = progress.identity
    out = []
    forward = []
    for index, item in enumerate(identity.get('consumed_live_bars', [])):
        bar = restore_live_bar_v1(item['live_bar'])
        warmup = bar.completed_by_arrival_nanos < int(start['host_wall_micros']) * 1000
        gate = start['binding']['holdout_gate']
        opened = _EPOCH + timedelta(microseconds=bar.bar.bar_open_micros)
        admitted = opened < datetime.fromisoformat(gate['holdout_start']) or (
            gate['holdout_end_exclusive'] is not None and
            opened >= datetime.fromisoformat(gate['holdout_end_exclusive']))
        if item['warmup'] is not warmup or item['admitted'] is not admitted:
            raise RunnerLivenessError('consumed_live_bar_admission_or_warmup_mismatch')
        if (bar.symbol != start['binding']['symbol'] or str(bar.capture_source_id) != start['binding']['capture_source_id']
                or bar.completed_by_arrival_nanos > identity['host_wall_micros'] * 1000):
            raise RunnerLivenessError('consumed_live_bar_progress_mismatch')
        if not warmup:
            forward.append({'capture_session_id': str(bar.session_id), 'feed_segment': bar.segment,
                            'bar_open_micros': bar.bar.bar_open_micros, 'closing_record_hash': bar.bar.closing_record_hash,
                            'trade_manifest_hash': bar.bar.trade_manifest_hash,
                            'completed_by_arrival_nanos': bar.completed_by_arrival_nanos, 'admitted': admitted})
        out.append({'schema_version': _SCHEMA, 'runner_session_id': identity['runner_session_id'],
                    'progress_hash': progress.content_hash, 'binding_hash': identity['binding_hash'],
                    'capture_source_id': start['binding']['capture_source_id'], 'index': index, **item})
    if identity.get('consumed_live_bars') and forward != identity['bars']:
        raise RunnerLivenessError('consumed_live_bars_disagree_with_progress')
    return out


def persist_consumed_live_bars_v1(cursor: Any, progress: RunnerProgressV1) -> None:
    """Called inside the progress transaction. A failed bar insert rolls progress back."""
    if not progress.identity.get('consumed_live_bars'):
        return
    cursor.execute('SELECT identity FROM runner_liveness_progress WHERE runner_session_id=%s AND sequence=0',
                   (UUID(progress.identity['runner_session_id']),))
    row = cursor.fetchone()
    if row is None:
        raise RunnerLivenessError('consumed_live_bars_missing_session_start')
    start = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    if identity_hash_v1(start['binding']) != progress.identity['binding_hash']:
        raise RunnerLivenessError('consumed_live_bars_binding_mismatch')
    for observation in _observations(progress, start):
        digest = identity_hash_v1(observation)
        cursor.execute('INSERT INTO runner_consumed_live_bars '
                       '(observation_id, content_hash, runner_session_id, progress_hash, observation_index, feed_segment, bar_open_micros, identity, recorded_at) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                       (uuid5(NAMESPACE_URL, _SCHEMA + ':' + digest), digest,
                        UUID(observation['runner_session_id']), observation['progress_hash'], observation['index'],
                        observation['live_bar']['feed_segment'], observation['live_bar']['bar']['bar_open_micros'],
                        json.dumps(observation, sort_keys=True), datetime.now(UTC)))


def _verified_progress_row_v1(digest: Any, identity: Mapping[str, Any], columns: Mapping[str, Any],
                              as_of: datetime) -> dict[str, Any]:
    """Validate scalar query/filter authority against the hashed progress payload."""
    try:
        progress = RunnerProgressV1(identity, str(digest).strip())
        binding = identity['binding']
        expected = {
            'progress_id': str(progress.progress_id), 'idempotency_key': progress.idempotency_key,
            'content_hash': progress.content_hash, 'runner_session_id': identity['runner_session_id'],
            'sequence': identity['sequence'], 'kind': identity['kind'], 'binding_hash': identity['binding_hash'],
            'previous_progress_hash': identity['previous_progress_hash'], 'bar_count': len(identity['bars']),
            'symbol': None if binding is None else binding['symbol'],
            'capture_source_id': None if binding is None else binding['capture_source_id'],
        }
        host_wall = datetime.fromisoformat(columns['host_wall_at'])
        recorded = datetime.fromisoformat(columns['recorded_at'])
        if (identity_hash_v1(dict(identity)) != progress.content_hash
                or any(columns.get(key) != value for key, value in expected.items())
                or host_wall.tzinfo is None or recorded.tzinfo is None
                or host_wall != _EPOCH + timedelta(microseconds=identity['host_wall_micros'])
                or host_wall > as_of or recorded > as_of):
            raise ValueError('progress scalar or as-of mismatch')
        return {'content_hash': progress.content_hash, 'identity': identity}
    except (KeyError, TypeError, ValueError) as error:
        raise RunnerLivenessError('consumed_live_bars_progress_scalar_or_as_of_mismatch') from error


class PostgresLiveBarStoreV1:
    """Verified, as-of reader. None denotes legacy/missing storage, never replay synthesis."""
    def __init__(self, database: Any) -> None:
        self._database = database

    def for_candidate(self, *, study_id: str, trial_id: str, symbol: str, as_of: datetime,
                      watched_as: Mapping[str, str] | None = None) -> list[LiveBarV1] | None:
        if as_of.tzinfo is None:
            raise RunnerLivenessError('as_of_must_be_timezone_aware')
        wanted = json.dumps([{'study_id': study_id, 'trial_id': trial_id}])
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT p.content_hash,p.identity,to_jsonb(p) FROM runner_liveness_progress p WHERE '
                           'p.recorded_at<=%s AND p.host_wall_at<=%s AND p.runner_session_id IN '
                           "(SELECT runner_session_id FROM runner_liveness_progress WHERE kind='STARTED' AND symbol=%s "
                           "AND identity->'binding'->'candidates' @> %s::jsonb) ORDER BY p.runner_session_id,p.sequence",
                           (as_of, as_of, symbol, wanted))
            sessions: dict[str, list[dict[str, Any]]] = {}
            for digest, identity, columns in cursor.fetchall():
                identity = identity if isinstance(identity, dict) else json.loads(identity)
                columns = columns if isinstance(columns, dict) else json.loads(columns)
                verified = _verified_progress_row_v1(digest, identity, columns, as_of)
                sessions.setdefault(identity['runner_session_id'], []).append(verified)
            result = []
            for session, rows in sorted(sessions.items(), key=lambda pair: (pair[1][0]['identity']['host_wall_micros'], pair[0])):
                try:
                    chain = _session_records(rows)
                except Exception as error:
                    raise RunnerLivenessError('consumed_live_bars_progress_chain_invalid') from error
                start = chain[0][1]
                binding = start['binding']
                entries = [c for c in binding['candidates'] if c['study_id'] == study_id and c['trial_id'] == trial_id]
                if len(entries) != 1 or binding['symbol'] != symbol:
                    raise RunnerLivenessError('consumed_live_bars_candidate_mismatch')
                if watched_as is not None and any(entries[0].get(k) != v for k, v in watched_as.items()):
                    continue
                cursor.execute('SELECT content_hash,identity,observation_id,runner_session_id,progress_hash,observation_index,feed_segment,bar_open_micros '
                               'FROM runner_consumed_live_bars WHERE runner_session_id=%s '
                               'AND recorded_at<=%s ORDER BY progress_hash,observation_index', (UUID(session), as_of))
                stored = {}
                for digest, observation, observation_id, stored_session, progress_hash, index, segment, minute in cursor.fetchall():
                    observation = observation if isinstance(observation, dict) else json.loads(observation)
                    actual_hash = identity_hash_v1(observation)
                    if (actual_hash != str(digest).strip()
                            or str(observation_id) != str(uuid5(NAMESPACE_URL, _SCHEMA + ':' + actual_hash))
                            or str(stored_session) != observation['runner_session_id']
                            or str(progress_hash).strip() != observation['progress_hash']
                            or index != observation['index']
                            or segment != observation['live_bar']['feed_segment']
                            or minute != observation['live_bar']['bar']['bar_open_micros']):
                        raise RunnerLivenessError('consumed_live_bar_hash_mismatch')
                    stored[(observation['progress_hash'], observation['index'])] = observation
                expected_keys = set()
                previous_sequence = -1
                previous_order = None
                for digest, identity in chain:
                    if identity['sequence'] != previous_sequence + 1:
                        raise RunnerLivenessError('consumed_live_bars_progress_sequence_gap')
                    previous_sequence = identity['sequence']
                    observations = _observations(RunnerProgressV1(identity, digest), start)
                    # A legacy forward progress record cannot establish a complete live history.
                    if identity['bars'] and not observations:
                        return None
                    for observation in observations:
                        key = (digest, observation['index'])
                        expected_keys.add(key)
                        if stored.get(key) != observation:
                            raise RunnerLivenessError('consumed_live_bar_missing_or_progress_mismatch')
                        bar = restore_live_bar_v1(observation['live_bar'])
                        order = (bar.segment, bar.bar.bar_open_micros)
                        if previous_order is not None and order <= previous_order:
                            raise RunnerLivenessError('consumed_live_bars_order_conflict')
                        previous_order = order
                        cutoff = int((as_of - _EPOCH) // timedelta(microseconds=1))
                        if bar.bar.complete_market_knowledge_micros <= cutoff:
                            result.append(bar)
                if set(stored) != expected_keys:
                    raise RunnerLivenessError('consumed_live_bar_unbound_observation')
        return result or None
