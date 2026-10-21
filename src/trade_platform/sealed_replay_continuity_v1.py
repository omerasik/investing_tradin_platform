"""Prove strategy continuity over sealed T4 dataset boundaries, without rebuilding bars.

Only same-session slices of the same manifest-proven coverage window can join.
They must overlap in identical trade/clock evidence and expose adjacent minute
bars. This permits a slice's independently normalized warmup overlap, but never
borrows a missing bar from a recorder restart. Seals and finalized bar knowledge
must already exist at ``as_of``. No live observation is manufactured here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, localcontext
from itertools import pairwise
from typing import Any

from .first_party_t4_forward_bars_v1 import _sealed_minute_bars, forward_bars_from_seal_v1
from .first_party_t4_seal_v1 import FirstPartyT4SealV1, iter_frame_rows_v1
from .live_signals_v1 import MAX_LIVE_CONTINUITY_GAP_NANOS
from .paper_fill_parity_v1 import PaperFillParityError, SealedReplayBarV1
from .research_data_plane_v1 import T4_REFERENCE_PRICE_FRAME, T4_TRADE_FRAME, ResearchFrameStoreV1
from .strategy_lab_study_v1 import identity_hash_v1


@dataclass(frozen=True)
class _Dataset:
    seal: FirstPartyT4SealV1
    bars: tuple[SealedReplayBarV1, ...]
    trades: dict[str, tuple[Any, ...]]
    arrival_breaks: tuple[int, ...]


def _same_coverage(left: _Dataset, right: _Dataset) -> bool:
    a, b = left.seal.identity, right.seal.identity
    # The COMPLETE partition's manifest and lifecycle bind the coverage window.
    # A different window may end at an interruption, even in the same session.
    return all(a[key] == b[key] for key in (
        "source_id", "session_id", "instrument", "exchange_symbol", "contract_content_hash",
        "normalization_semantic_version", "clock_bound_rule", "bar_completion_rule", "window",
    )) and a["partitions"] == b["partitions"]


def _joins(left: _Dataset, right: _Dataset) -> bool:
    if left.arrival_breaks or right.arrival_breaks:
        return False  # do not bridge even a manifest-covered raw-record outage
    if not _same_coverage(left, right):
        return False
    a, b = left.seal.identity, right.seal.identity
    low = max(a["segment"]["start_arrival_nanos"], b["segment"]["start_arrival_nanos"])
    high = min(a["segment"]["end_arrival_nanos_inclusive"], b["segment"]["end_arrival_nanos_inclusive"])
    if low > high:
        return False
    # Each raw-replayed slice brackets its entire coverage. A shared sample
    # establishes a common clock run; disjoint runs cannot bridge a clock break.
    if not set(a["clock_evidence"]["sample_hashes"]) & set(b["clock_evidence"]["sample_hashes"]):
        return False
    arrival_index = T4_TRADE_FRAME.schema.names.index("arrival_utc_nanos")
    left_overlap = {key: row for key, row in left.trades.items() if low <= int(row[arrival_index]) <= high}
    right_overlap = {key: row for key, row in right.trades.items() if low <= int(row[arrival_index]) <= high}
    # Compare the entire row: record/session references, sequence, event time,
    # arrival, finalized bounds, price and quantity. Never approximate clocks.
    return bool(left_overlap) and left_overlap == right_overlap


def sealed_replay_bars_from_seals_v1(
    seals: Sequence[FirstPartyT4SealV1], *, store: ResearchFrameStoreV1, as_of: datetime,
) -> tuple[SealedReplayBarV1, ...]:
    """Verified multi-dataset R8/R10 input; broken or unproven joins reset history.

    Duplicate minutes must agree exactly, including finalized clocks and capture
    provenance. Such duplicates choose the lexically first seal deterministically.
    Conflicting overlaps are refused instead of silently selecting a dataset.
    Missing intervals and new recorder sessions never retain strategy state.
    """
    if as_of.tzinfo is None:
        raise PaperFillParityError("replay_as_of_must_be_aware")
    known_micros = (as_of - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    datasets: list[_Dataset] = []
    seen: set[str] = set()
    for seal in seals:
        # Validate even future seals: an invalid seal is never a catalog fact.
        if not isinstance(seal, FirstPartyT4SealV1) or not seal.integrity_verified() or not seal.raw_replayed:
            raise PaperFillParityError("replay_requires_intact_raw_replayed_seals")
        if seal.sealed_at > as_of or seal.content_hash in seen:
            continue
        seen.add(seal.content_hash)
        # decimal128(38,18) proof must not depend on a caller's ambient
        # Decimal precision. The underlying exact quantizer still refuses
        # values which cannot be represented; no rounding tolerance is added.
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            forward = forward_bars_from_seal_v1(seal, store=store)
            minute = _sealed_minute_bars(seal, store)
        proven = {bar.bar_open_micros: bar for bar in forward.bars}
        if sorted(proven) != sorted(bar.bar_open_micros for bar in minute):
            raise PaperFillParityError("replay_minute_forward_count_mismatch")
        fields = ("open_price", "high_price", "low_price", "close_price", "trade_count",
                  "first_trade_reference", "last_trade_reference", "open_market_knowledge_micros",
                  "complete_market_knowledge_micros", "open_is_sequence_ambiguous",
                  "close_is_sequence_ambiguous", "trade_manifest_hash")
        for minute_bar in minute:
            forward_bar = proven[minute_bar.bar_open_micros]
            if (any(getattr(minute_bar, name) != getattr(forward_bar, name) for name in fields)
                    or minute_bar.closing_record_hash != forward_bar.clock.closing_record_hash):
                raise PaperFillParityError("replay_minute_forward_bar_mismatch")
        trades: dict[str, tuple[Any, ...]] = {}
        reference_index = T4_TRADE_FRAME.schema.names.index("observation_reference")
        for row in iter_frame_rows_v1(seal, T4_TRADE_FRAME.kind, store):
            reference = str(row[reference_index])
            if reference in trades:
                raise PaperFillParityError("replay_trade_reference_not_unique")
            trades[reference] = tuple(row)
        # Raw observation gaps trigger R8 restarts even inside a declared
        # coverage window. Use both subscribed streams; trade-only gaps would
        # mistake a quiet market with healthy ticker messages for an outage.
        arrivals = {int(row[T4_TRADE_FRAME.schema.names.index("arrival_utc_nanos")])
                    for row in trades.values()}
        frame = T4_REFERENCE_PRICE_FRAME
        store.verify(store.load_manifest(seal.frame_manifests[frame.kind]))
        arrival_i = frame.schema.names.index("arrival_utc_nanos")
        arrivals.update(int(row[arrival_i]) for row in iter_frame_rows_v1(seal, frame.kind, store))
        ordered_arrivals = sorted(arrivals)
        breaks = tuple(right for left, right in pairwise(ordered_arrivals)
                       if right - left > MAX_LIVE_CONTINUITY_GAP_NANOS)
        admitted: list[SealedReplayBarV1] = []
        for minute_bar in minute:
            clock = proven[minute_bar.bar_open_micros].clock
            if (minute_bar.complete_market_knowledge_micros > known_micros
                    or any(clock.first_arrival_utc_nanos < point <= clock.last_arrival_utc_nanos
                           for point in breaks)):
                continue  # a live normalizer drops the open minute at a restart
            internal_segment = sum(point <= clock.first_arrival_utc_nanos for point in breaks)
            admitted.append(SealedReplayBarV1(forward.exchange_symbol, internal_segment,
                                             forward.session_id, minute_bar, seal.content_hash))
        datasets.append(_Dataset(seal, tuple(admitted), trades, breaks))
    datasets.sort(key=lambda item: (str(item.seal.identity["exchange_symbol"]),
                                    item.seal.identity["segment"]["start_arrival_nanos"],
                                    item.seal.content_hash))
    proof = identity_hash_v1({"schema_version": "sealed-replay-continuity-v1",
                             "seal_content_hashes": sorted(item.seal.content_hash for item in datasets),
                             "rule": "same-manifest-window-identical-overlap-adjacent-minutes"})
    owners: dict[tuple[str, int], list[tuple[_Dataset, SealedReplayBarV1]]] = {}
    for dataset in datasets:
        for bar in dataset.bars:
            owners.setdefault((bar.symbol, bar.bar.bar_open_micros), []).append((dataset, bar))
    out: list[SealedReplayBarV1] = []
    previous: tuple[_Dataset, SealedReplayBarV1] | None = None
    segment = -1
    for key, values in sorted(owners.items()):
        values.sort(key=lambda value: value[1].seal_content_hash)
        dataset, bar = values[0]
        for other, twin in values[1:]:
            if bar.session_id != twin.session_id or bar.bar != twin.bar or not _joins(dataset, other):
                raise PaperFillParityError("replay_ambiguous_overlapping_minute")
        continuous = False
        if previous is not None:
            prior_dataset, prior = previous
            adjacent = prior.symbol == bar.symbol and prior.bar.bar_close_micros == bar.bar.bar_open_micros
            continuous = adjacent and ((prior_dataset is dataset and prior.segment == bar.segment)
                                       or (prior_dataset is not dataset and _joins(prior_dataset, dataset)))
        if not continuous:
            segment += 1
        out.append(replace(bar, segment=segment, continuity_hash=proof))
        previous = dataset, bar
    return tuple(out)


__all__ = ["sealed_replay_bars_from_seals_v1"]
