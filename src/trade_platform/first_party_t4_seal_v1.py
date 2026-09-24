"""Phase R3A -- first-party T4 dataset eligibility, segmentation and sealing.

``RESEARCH_ONLY``. Reads the immutable first-party capture archive
(:mod:`trade_platform.first_party_capture_archive_v1`), normalizes it with
:mod:`trade_platform.first_party_t4_normalization_v1`, writes the result as
columnar frames on the R2B data plane
(:mod:`trade_platform.research_data_plane_v1`), and issues a content-hashed
:class:`FirstPartyT4SealV1` -- the only object from which first-party
provenance (and hence ``T4_FIRST_PARTY_CAPTURE``) can be derived.

What is eligible
----------------
Only partitions of the *production* first-party contract
(:func:`~trade_platform.first_party_capture_authority_v1.first_party_bybit_capture_contract_v1`,
the one source registered with the evidence-tier authority). Capacity-measurement
partitions are another source and are ignored. A partition contributes only when
it is ``COMPLETE`` and replays in full through the archive's own proof
(:func:`~trade_platform.first_party_capture_archive_v1.verify_partition_v1`). A
``PARTIAL`` partition -- a crash, a killed process, a session still recording --
contributes nothing: no records, no coverage and no clock evidence.

One dataset per contiguous segment
----------------------------------
The capture contract says ``a_canonical_dataset_may_not_span_a_capture_gap``.
A *segment* is one declared coverage window intersected with one of its
session's admissible clock intervals (consecutive samples no more than
``MAX_CLOCK_BRACKET_NANOS`` apart with no barrier -- a detected clock step or a
non-COMPLETE same-session partition -- between them, see
:data:`~trade_platform.first_party_t4_normalization_v1.CLOCK_BOUND_RULE_V1`).
Each segment is sealed as its own dataset. Unbracketed arrivals are excluded and
their duration reported, never extrapolated. Gaps between segments are never
bridged and nothing crosses one: every segment starts from empty state.

A segment is sealed only when it is *final*: it ends at its window's end, or at
a bracket break that later evidence cannot change. A segment that ends only
because no later clock sample exists *yet* is reported ``SEGMENT_PENDING`` and
not sealed, so the same raw evidence never yields two overlapping datasets
depending on when discovery ran.

Identity
--------
``content_hash`` covers the source contract, the exact partitions (manifest hash
and the SHA-256 of the records the manifest binds), the window, the segment
bounds, the session's clock evidence hash, the normalization/clock/bar semantic
versions, every frame's *logical* content hash and row count, the counters and
the timing facts. It excludes every platform clock and every library version
(frame manifest hashes, which bind the pyarrow version, are carried beside it
for audit). The dataset id is ``uuid5`` of the content hash, so re-sealing the
same eligible raw evidence -- today, next week, on another host -- yields the
identical dataset id and content hash.

Verification
------------
:func:`verify_t4_dataset_v1` rebuilds the segment from raw capture and re-derives
every frame's logical hash, then re-verifies every stored frame object byte for
byte. Only a seal that came out of that rebuild is ``raw_replayed``, and only
such a seal may back a sealed-clock resolver (see
:mod:`trade_platform.first_party_t4_dataset_v1`).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .first_party_capture_archive_v1 import (
    END_PROOF_CLOCK_DISCONTINUITY,
    END_PROOF_UTC_DAY_ROLLOVER,
    MANIFEST_FILE_NAME,
    PARTITION_STATUS_COMPLETE,
    RECORDS_FILE_NAME,
    SESSION_FILE_NAME,
    CaptureCoverageIntervalV1,
    CaptureLifecycleKindV1,
    FirstPartyCaptureArchiveError,
    find_partitions_v1,
    read_lifecycle_v1,
    read_partition_status_v1,
    replay_partition_v1,
    session_source_id_v1,
    verify_partition_v1,
)
from .first_party_capture_authority_v1 import (
    FirstPartyCaptureContractV1,
    first_party_bybit_capture_contract_v1,
)
from .first_party_t4_normalization_v1 import (
    BAR_COMPLETION_RULE_V1,
    BASIS_FORMULA_V1,
    CLOCK_BOUND_RULE_V1,
    T4_NORMALIZATION_SEMANTIC_VERSION_V1,
    ClockOffsetSampleEvidenceV1,
    SessionClockEvidenceV1,
    T4BasisV1,
    T4NormalizationError,
    T4SegmentNormalizerV1,
    T4SegmentOutputV1,
    canonical_json_v1,
    micros_to_datetime_v1,
    parse_clock_offset_sample_v1,
    sha256_json_v1,
)
from .research_data_plane_v1 import (
    T4_BASIS_FRAME,
    T4_OHLCV_1M_FRAME,
    T4_REFERENCE_PRICE_FRAME,
    T4_TRADE_FRAME,
    FrameManifestV1,
    FrameSchemaV1,
    ResearchDataPlaneError,
    ResearchFrameStoreV1,
    logical_content_hash_v1,
)

T4_SEAL_SCHEMA_VERSION_V1: Final = "first-party-t4-seal-v1"
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.first_party_t4_seal_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
#: Process-local: seals are in-memory proofs, never persisted as objects.
_SEAL_KEY: Final = os.urandom(32)

#: Only this module may issue a :class:`FirstPartyT4SealV1`.
_ISSUER: Final = object()

T4_FRAMES_V1: Final[tuple[FrameSchemaV1, ...]] = (
    T4_REFERENCE_PRICE_FRAME,
    T4_BASIS_FRAME,
    T4_TRADE_FRAME,
    T4_OHLCV_1M_FRAME,
)


class FirstPartyT4SealError(ValueError):
    """Raised when evidence cannot be sealed, or a seal does not reproduce."""


def _micros_ts(micros: int) -> datetime:
    return micros_to_datetime_v1(micros)


def _flag(value: bool | None) -> str | None:
    return None if value is None else ("true" if value else "false")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4PartitionV1:
    """One COMPLETE production partition, with the identities the seal binds."""

    directory: Path
    session_id: UUID
    utc_day: str
    manifest_content_hash: str
    records_sha256: str
    record_count: int
    clock_resolution_nanos: int
    coverage: tuple[CaptureCoverageIntervalV1, ...]

    def binding(self, role: str) -> dict[str, Any]:
        return {
            "role": role,
            "session_id": str(self.session_id),
            "utc_day": self.utc_day,
            "manifest_content_hash": self.manifest_content_hash,
            "records_sha256": self.records_sha256,
            "record_count": self.record_count,
        }


@dataclass(frozen=True, slots=True)
class T4SegmentPlanV1:
    """One contiguous, clock-admissible stretch of one coverage window.

    ``clock`` holds exactly the *contiguous* run of session samples the segment
    uses -- from the last sample received at or before its start to the first
    sent at or after its end -- so the dataset binds only the clock evidence it
    depends on, and a later sample (the next day's partition completing) never
    changes an existing dataset's identity.
    """

    partition: T4PartitionV1
    window_index: int
    window: CaptureCoverageIntervalV1
    start_arrival_nanos: int
    end_arrival_nanos: int
    clock: SessionClockEvidenceV1
    clock_partitions: tuple[T4PartitionV1, ...]

    @property
    def excluded_head_nanos(self) -> int:
        return self.start_arrival_nanos - self.window.start_utc_nanos

    @property
    def excluded_tail_nanos(self) -> int:
        return self.window.last_proven_utc_nanos - self.end_arrival_nanos

    def admits(self, arrival_utc_nanos: int) -> bool:
        return self.start_arrival_nanos <= arrival_utc_nanos <= self.end_arrival_nanos


@dataclass(frozen=True, slots=True)
class T4ExclusionV1:
    """Evidence that contributes nothing, and why. Reported, never hidden."""

    scope: str
    reference: str
    reasons: tuple[str, ...]
    duration_nanos: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "reference": self.reference,
            "reasons": list(self.reasons),
            "duration_nanos": self.duration_nanos,
        }


@dataclass(frozen=True, slots=True)
class T4SessionClockV1:
    """Every admissible sample of one session (with the partition that sealed it) and its barriers."""

    session_id: UUID
    samples: tuple[tuple[ClockOffsetSampleEvidenceV1, T4PartitionV1], ...]
    barriers: tuple[tuple[int, int], ...] = ()
    #: Provably ended (no later sample can ever arrive): see :func:`_session_finished`.
    finished: bool = False

    def evidence(self) -> SessionClockEvidenceV1:
        return SessionClockEvidenceV1(
            self.session_id, (sample for sample, _ in self.samples), barriers=self.barriers
        )

    def used_run(self, start: int, end: int) -> tuple[int, int] | None:
        """Indices of the contiguous samples bracketing every arrival in ``[start, end]``."""
        clock = self.evidence()
        first, last = clock.bracket(start), clock.bracket(end)
        if first is None or last is None or last[1] < first[0]:
            return None
        return first[0], last[1]


@dataclass(frozen=True, slots=True)
class T4DiscoveryV1:
    segments: tuple[T4SegmentPlanV1, ...]
    exclusions: tuple[T4ExclusionV1, ...]
    complete_partitions: tuple[T4PartitionV1, ...]
    session_clocks: Mapping[UUID, T4SessionClockV1]


def _read_json(path: Path) -> dict[str, Any]:
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FirstPartyT4SealError(f"expected_json_object:{path.name}")
    return value


def _complete_partition(
    directory: Path, contract: FirstPartyCaptureContractV1
) -> tuple[T4PartitionV1 | None, tuple[str, ...]]:
    status = read_partition_status_v1(directory, contract=contract)
    if status.status != PARTITION_STATUS_COMPLETE or status.session_id is None:
        return None, status.reasons or ("partition_not_complete",)
    manifest = _read_json(directory / MANIFEST_FILE_NAME)
    session = _read_json(directory / SESSION_FILE_NAME)
    return (
        T4PartitionV1(
            directory=directory,
            session_id=status.session_id,
            utc_day=str(manifest["utc_day"]),
            manifest_content_hash=str(manifest["manifest_content_hash"]),
            records_sha256=str(manifest["files"][RECORDS_FILE_NAME]),
            record_count=int(manifest["record_count"]),
            clock_resolution_nanos=int(session["clock_resolution_nanos"]),
            coverage=status.coverage,
        ),
        (),
    )


def discover_t4_segments_v1(
    capture_root: Path, *, contract: FirstPartyCaptureContractV1 | None = None
) -> T4DiscoveryV1:
    """Every sealable segment under ``capture_root``, and everything excluded.

    Metadata-level and cheap: it proves partition status and parses clock
    evidence but does not replay records. Sealing replays (and so verifies)
    the segment's own partition; clock-only partitions are fully verified
    there too.
    """
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    wanted = str(authorized.source_id)
    exclusions: list[T4ExclusionV1] = []
    complete: list[T4PartitionV1] = []
    incomplete_days: dict[UUID, list[str]] = {}
    for directory in find_partitions_v1(capture_root):
        reference = directory.relative_to(capture_root).as_posix()
        claimed = session_source_id_v1(directory)
        if claimed is None:
            exclusions.append(T4ExclusionV1("PARTITION", reference, ("partition_session_is_unreadable",)))
            continue
        if claimed != wanted:
            continue  # another authorized source (e.g. capacity measurement): not T4 here
        partition, reasons = _complete_partition(directory, authorized)
        if partition is None:
            exclusions.append(T4ExclusionV1("PARTITION", reference, reasons))
            try:
                session = _read_json(directory / SESSION_FILE_NAME)
                incomplete_days.setdefault(UUID(str(session["session_id"])), []).append(
                    str(session["utc_day"])
                )
            except (OSError, ValueError, KeyError, FirstPartyT4SealError):
                pass  # unreadable: it cannot bracket anything either way
            continue
        complete.append(partition)

    by_session: dict[UUID, list[T4PartitionV1]] = {}
    for partition in complete:
        by_session.setdefault(partition.session_id, []).append(partition)

    segments: list[T4SegmentPlanV1] = []
    session_clocks: dict[UUID, T4SessionClockV1] = {}
    for session_id in sorted(by_session, key=str):
        partitions = sorted(by_session[session_id], key=lambda item: item.utc_day)
        resolutions = {item.clock_resolution_nanos for item in partitions}
        try:
            if len(resolutions) != 1:
                raise T4NormalizationError("session_partitions_disagree_on_clock_resolution")
            resolution = resolutions.pop()
            pairs = [
                (parse_clock_offset_sample_v1(event, session_resolution_nanos=resolution), partition)
                for partition in partitions
                for event in read_lifecycle_v1(partition.directory)
                if event.kind == CaptureLifecycleKindV1.CLOCK_OFFSET_SAMPLE.value
            ]
            started = _session_started_nanos(partitions)
            if started is not None:
                # A recorder may copy the previous session's latest sample into
                # a new session's log; one taken before this session started
                # (possibly before a host sleep or step) is not its evidence.
                pairs = [pair for pair in pairs if pair[0].host_send_utc_nanos >= started]
            pairs.sort(key=lambda pair: (pair[0].host_send_utc_nanos, pair[0].sample_hash))
            barriers = _session_barriers(partitions, incomplete_days.get(session_id, ()))
            session_clock = T4SessionClockV1(
                session_id, tuple(pairs), barriers,
                finished=_session_finished(partitions, incomplete_days.get(session_id, ())),
            )
            clock = session_clock.evidence()  # refuses overlapping samples
        except T4NormalizationError as error:
            for partition in partitions:
                exclusions.append(
                    T4ExclusionV1("SESSION_CLOCK", f"{session_id}:{partition.utc_day}", (str(error),))
                )
            continue
        session_clocks[session_id] = session_clock
        intervals = clock.admissible_intervals()
        last_send = clock.samples[-1].host_send_utc_nanos if clock.samples else None
        for partition in partitions:
            for index, window in enumerate(partition.coverage):
                reference = f"{session_id}:{partition.utc_day}:window={index}"
                window_duration = window.last_proven_utc_nanos - window.start_utc_nanos
                if window.record_count == 0:
                    exclusions.append(T4ExclusionV1("WINDOW", reference, ("window_has_no_records",), window_duration))
                    continue
                covered = 0
                for low, high in intervals:
                    start = max(window.start_utc_nanos, low)
                    end = min(window.last_proven_utc_nanos, high)
                    if start > end:
                        continue
                    if end < window.last_proven_utc_nanos and high == last_send and not session_clock.finished:
                        # The bracket ends only because no later sample exists
                        # *yet*: sealing now would make a dataset a later seal of
                        # the same window supersedes. Deferred, never truncated.
                        exclusions.append(
                            T4ExclusionV1("SEGMENT_PENDING", f"{reference}:start={start}",
                                          ("awaiting_a_later_clock_sample",), end - start)
                        )
                        covered += end - start
                        continue
                    segments.append(_plan(session_clock, partition, index, window, start, end))
                    covered += end - start
                if covered < window_duration:
                    why = (
                        "session_has_no_bracketing_clock_offset_samples"
                        if not intervals
                        else "arrivals_outside_every_admissible_clock_bracket"
                    )
                    exclusions.append(
                        T4ExclusionV1("WINDOW_UNBRACKETED", reference, (why,), window_duration - covered)
                    )
    segments.sort(key=lambda plan: (plan.start_arrival_nanos, str(plan.partition.session_id)))
    return T4DiscoveryV1(tuple(segments), tuple(exclusions), tuple(complete), session_clocks)


def _session_started_nanos(partitions: Sequence[T4PartitionV1]) -> int | None:
    starts = [
        event.arrival_utc_nanos
        for partition in partitions
        for event in read_lifecycle_v1(partition.directory)
        if event.kind == CaptureLifecycleKindV1.SESSION_STARTED.value
    ]
    return min(starts) if starts else None


def _session_finished(partitions: Sequence[T4PartitionV1], incomplete_days: Sequence[str]) -> bool:
    """True only on positive evidence that the session can record nothing more.

    The latest COMPLETE partition must be the session's last partition (no
    later, non-COMPLETE one -- which may be a crash *or* a session still
    recording, and the archive cannot tell them apart), and it must end in a
    ``SESSION_CLOSED`` event or a final window whose end proof is not a UTC
    rollover. A crashed session therefore stays pending: that loses its final
    bracket (fail closed) rather than risk sealing a still-growing interval.
    """
    if not partitions:
        return False
    last = partitions[-1]
    if any(day > last.utc_day for day in incomplete_days):
        return False
    if any(
        event.kind == CaptureLifecycleKindV1.SESSION_CLOSED.value
        for event in read_lifecycle_v1(last.directory)
    ):
        return True
    return bool(last.coverage) and last.coverage[-1].end_proof != END_PROOF_UTC_DAY_ROLLOVER


def _day_span_nanos(day: str) -> tuple[int, int]:
    start = (datetime.fromisoformat(day).replace(tzinfo=UTC) - _EPOCH) // timedelta(microseconds=1) * 1_000
    return start, start + 86_400 * 1_000_000_000


def _session_barriers(
    partitions: Sequence[T4PartitionV1], incomplete_days: Sequence[str]
) -> tuple[tuple[int, int], ...]:
    """Host-clock spans no clock bracket may cross.

    A detected wall-clock step (a window ending in ``CLOCK_DISCONTINUITY``, or
    a recorded discontinuity event) and the whole UTC day of any same-session
    partition that is not COMPLETE: its samples are not evidence, so nothing
    may be bracketed across it.
    """
    barriers: set[tuple[int, int]] = set()
    for partition in partitions:
        for window in partition.coverage:
            if window.end_proof == END_PROOF_CLOCK_DISCONTINUITY:
                barriers.add((window.last_proven_utc_nanos, window.last_proven_utc_nanos + 1))
        for event in read_lifecycle_v1(partition.directory):
            if event.kind == CaptureLifecycleKindV1.CLOCK_DISCONTINUITY.value:
                barriers.add((event.arrival_utc_nanos, event.arrival_utc_nanos + 1))
    for day in incomplete_days:
        barriers.add(_day_span_nanos(day))
    return tuple(sorted(barriers))


def _plan(
    session_clock: T4SessionClockV1,
    partition: T4PartitionV1,
    window_index: int,
    window: CaptureCoverageIntervalV1,
    start: int,
    end: int,
) -> T4SegmentPlanV1:
    run = session_clock.used_run(start, end)
    if run is None:
        raise FirstPartyT4SealError("segment_is_not_bracketed_by_session_samples")
    used = session_clock.samples[run[0]: run[1] + 1]
    low, high = used[0][0].host_receive_utc_nanos, used[-1][0].host_send_utc_nanos
    barriers = tuple(item for item in session_clock.barriers if item[0] < high and low < item[1])
    clock_partitions: dict[Path, T4PartitionV1] = {partition.directory: partition}
    for _, source in used:
        clock_partitions.setdefault(source.directory, source)
    return T4SegmentPlanV1(
        partition=partition,
        window_index=window_index,
        window=window,
        start_arrival_nanos=start,
        end_arrival_nanos=end,
        clock=SessionClockEvidenceV1(
            session_clock.session_id, (sample for sample, _ in used), barriers=barriers
        ),
        clock_partitions=tuple(sorted(clock_partitions.values(), key=lambda item: item.utc_day)),
    )


# ---------------------------------------------------------------------------
# Normalization of one segment
# ---------------------------------------------------------------------------


def normalize_t4_segment_v1(
    plan: T4SegmentPlanV1, *, contract: FirstPartyCaptureContractV1 | None = None
) -> tuple[T4SegmentOutputV1, dict[str, int], tuple[int, int, int]]:
    """Replay and normalize one segment. Raises unless the evidence reproduces.

    Returns the output, the counters and ``(record_count, first_sequence,
    last_sequence)`` of the admitted records. The window's partition is
    replayed in full (so every record-level claim is re-proven); the clock-only
    partitions are fully verified as well.
    """
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    for partition in plan.clock_partitions:
        if partition.directory != plan.partition.directory:
            verify_partition_v1(partition.directory, contract=authorized)
    normalizer = T4SegmentNormalizerV1(
        exchange_symbol=authorized.exchange_symbol, session_id=plan.partition.session_id
    )
    admitted = 0
    first_sequence = last_sequence = -1
    for record in replay_partition_v1(plan.partition.directory, contract=authorized):
        if not plan.window.contains(record.arrival_utc_nanos) or not plan.admits(record.arrival_utc_nanos):
            continue
        bound = plan.clock.bound_for(record.arrival_utc_nanos)
        if bound is None:  # unreachable inside the admissible range; refused, not skipped
            raise FirstPartyT4SealError("admitted_arrival_has_no_clock_bracket")
        normalizer.feed(record, bound)
        admitted += 1
        if first_sequence < 0:
            first_sequence = record.sequence
        last_sequence = record.sequence
    output = normalizer.finish()
    if admitted == 0:
        raise FirstPartyT4SealError("segment_admits_no_records")
    return output, normalizer.counts.payload(), (admitted, first_sequence, last_sequence)


def _reference_rows(output: T4SegmentOutputV1) -> Iterator[tuple[object, ...]]:
    for item in output.reference_prices:
        arrival = item.arrival
        yield (
            item.observation_reference, item.component, item.message_type,
            item.exchange_ts_millis, _micros_ts(item.event_micros),
            str(arrival.session_id), arrival.record_sequence, arrival.record_content_hash,
            arrival.arrival_utc_nanos, arrival.clock_bound_nanos, arrival.clock_bound_evidence,
            _micros_ts(arrival.market_knowledge_micros), item.price,
        )


def _emitting_sequence(item: T4BasisV1) -> int:
    # A basis is emitted only by a record that updated one of its components,
    # so the emitting record is always one of the two component records.
    for component in (item.mark, item.index):
        if component.arrival.record_content_hash == item.emitting_record_hash:
            return component.arrival.record_sequence
    raise FirstPartyT4SealError("basis_emitting_record_is_neither_component")


def _basis_rows(output: T4SegmentOutputV1) -> Iterator[tuple[object, ...]]:
    for item in output.basis:
        yield (
            _emitting_sequence(item), item.emitting_record_hash, item.mark.observation_reference,
            item.index.observation_reference, _micros_ts(item.event_micros),
            _micros_ts(item.market_knowledge_micros), item.mark.price, item.index.price, item.basis,
        )


def _trade_rows(output: T4SegmentOutputV1) -> Iterator[tuple[object, ...]]:
    for item in output.trades:
        arrival = item.arrival
        yield (
            item.observation_reference, item.entry_index, item.trade_id, item.trade_ts_millis,
            item.message_ts_millis, _micros_ts(item.trade_ts_millis * 1_000), item.venue_seq,
            item.side, _flag(item.block_trade), _flag(item.rpi),
            str(arrival.session_id), arrival.record_sequence, arrival.record_content_hash,
            arrival.arrival_utc_nanos, arrival.clock_bound_nanos, arrival.clock_bound_evidence,
            _micros_ts(arrival.market_knowledge_micros), item.price, item.quantity,
        )


def _bar_rows(output: T4SegmentOutputV1) -> Iterator[tuple[object, ...]]:
    for bar in output.bars:
        yield (
            _micros_ts(bar.bar_open_micros), _micros_ts(bar.bar_close_micros), bar.open_price,
            bar.high_price, bar.low_price, bar.close_price, bar.base_volume, bar.quote_turnover,
            bar.trade_count, bar.block_trade_count, bar.rpi_trade_count, bar.first_trade_reference,
            bar.last_trade_reference, _micros_ts(bar.open_market_knowledge_micros),
            _micros_ts(bar.complete_market_knowledge_micros),
            _flag(bar.open_is_sequence_ambiguous), _flag(bar.close_is_sequence_ambiguous),
            bar.closing_record_hash, bar.trade_manifest_hash,
        )


_FRAME_ROWS: Final[Mapping[str, Callable[[T4SegmentOutputV1], Iterator[tuple[object, ...]]]]] = {
    T4_REFERENCE_PRICE_FRAME.kind: _reference_rows,
    T4_BASIS_FRAME.kind: _basis_rows,
    T4_TRADE_FRAME.kind: _trade_rows,
    T4_OHLCV_1M_FRAME.kind: _bar_rows,
}


# ---------------------------------------------------------------------------
# The seal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class T4TimingFactsV1:
    """What the sealed observations carry, derived here -- never supplied by a caller."""

    observations_with_knowledge_time: int
    observations_missing_knowledge_time: int
    distinct_knowledge_time_count: int

    def as_tuple(self) -> tuple[int, int, int]:
        return (
            self.observations_with_knowledge_time,
            self.observations_missing_knowledge_time,
            self.distinct_knowledge_time_count,
        )


def _timing_facts(output: T4SegmentOutputV1) -> T4TimingFactsV1:
    """Provider-captured observations only: reference prices and trades."""
    instants = {item.arrival.market_knowledge_micros for item in output.reference_prices}
    instants.update(item.arrival.market_knowledge_micros for item in output.trades)
    return T4TimingFactsV1(
        observations_with_knowledge_time=len(output.reference_prices) + len(output.trades),
        observations_missing_knowledge_time=0,
        distinct_knowledge_time_count=len(instants),
    )


@dataclass(frozen=True, slots=True)
class FirstPartyT4SealV1:
    """A sealed first-party T4 segment dataset. Issued only by this module.

    ``identity`` is everything the ``content_hash`` covers. ``frame_manifests``
    (kind -> manifest hash) and ``sealed_at`` are audit facts outside it.
    ``raw_replayed`` is true only for a seal that came out of a full rebuild
    from raw capture (:func:`verify_t4_dataset_v1` or the sealing run itself).
    """

    schema_version: str
    identity: Mapping[str, Any]
    content_hash: str
    dataset_version_id: UUID
    source_id: UUID
    timing_facts: T4TimingFactsV1
    frame_manifests: Mapping[str, str]
    sealed_at: datetime
    raw_replayed: bool
    #: HMAC (process-local key) over the fields the identity hash does not
    #: cover -- ``raw_replayed``, ``sealed_at``, the frame manifests -- so
    #: ``dataclasses.replace(restored, raw_replayed=True)`` fails integrity.
    seal_token: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise FirstPartyT4SealError("first_party_t4_seal_is_issued_only_by_its_authority")

    def integrity_verified(self) -> bool:
        identity = dict(self.identity)
        return (
            self.schema_version == T4_SEAL_SCHEMA_VERSION_V1
            and self.content_hash == sha256_json_v1(identity)
            and self.dataset_version_id == t4_dataset_version_id_v1(self.content_hash)
            and str(self.source_id) == identity.get("source_id")
            and list(self.timing_facts.as_tuple())
            == [
                identity.get("timing_facts", {}).get(name)
                for name in (
                    "observations_with_knowledge_time",
                    "observations_missing_knowledge_time",
                    "distinct_knowledge_time_count",
                )
            ]
            and set(self.frame_manifests) == {frame.kind for frame in T4_FRAMES_V1}
            and self.sealed_at.tzinfo is not None
            and hmac.compare_digest(
                self.seal_token,
                _seal_token(self.content_hash, self.raw_replayed, self.sealed_at, self.frame_manifests),
            )
        )

    def frame_identity(self, kind: str) -> Mapping[str, Any]:
        frames = self.identity.get("frames")
        if not isinstance(frames, Mapping) or kind not in frames:
            raise FirstPartyT4SealError(f"seal_has_no_frame:{kind}")
        value = frames[kind]
        if not isinstance(value, Mapping):
            raise FirstPartyT4SealError(f"seal_frame_malformed:{kind}")
        return value

    @property
    def evidence_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"first-party-t4-seal-evidence:{self.content_hash}")

    @property
    def segment_first_knowledge_at(self) -> datetime:
        return _micros_ts(int(self.identity["knowledge_span"]["first_micros"]))

    @property
    def segment_last_knowledge_at(self) -> datetime:
        return _micros_ts(int(self.identity["knowledge_span"]["last_micros"]))


def _seal_token(
    content_hash: str, raw_replayed: bool, sealed_at: datetime, frame_manifests: Mapping[str, str]
) -> str:
    message = canonical_json_v1(
        {
            "content_hash": content_hash,
            "raw_replayed": raw_replayed,
            "sealed_at": sealed_at.astimezone(UTC).isoformat(),
            "frame_manifests": dict(sorted(frame_manifests.items())),
        }
    )
    return hmac.new(_SEAL_KEY, message.encode(), hashlib.sha256).hexdigest()


def t4_dataset_version_id_v1(content_hash: str) -> UUID:
    return uuid5(_NAMESPACE, f"first-party-t4-dataset:{content_hash}")


def _identity(
    plan: T4SegmentPlanV1,
    *,
    contract: FirstPartyCaptureContractV1,
    output: T4SegmentOutputV1,
    counts: Mapping[str, int],
    admitted: tuple[int, int, int],
    frames: Mapping[str, tuple[str, int]],
    timing: T4TimingFactsV1,
) -> dict[str, Any]:
    knowledge = [item.arrival.market_knowledge_micros for item in output.reference_prices]
    knowledge.extend(item.arrival.market_knowledge_micros for item in output.trades)
    if not knowledge:
        raise FirstPartyT4SealError("segment_has_no_provider_observation")
    partitions = [plan.partition.binding("records")]
    partitions.extend(
        item.binding("clock")
        for item in plan.clock_partitions
        if item.directory != plan.partition.directory
    )
    partitions.sort(key=lambda item: (item["utc_day"], item["role"]))
    return {
        "schema_version": T4_SEAL_SCHEMA_VERSION_V1,
        "normalization_semantic_version": T4_NORMALIZATION_SEMANTIC_VERSION_V1,
        "clock_bound_rule": CLOCK_BOUND_RULE_V1,
        "bar_completion_rule": BAR_COMPLETION_RULE_V1,
        "basis_formula": BASIS_FORMULA_V1,
        "source_id": str(contract.source_id),
        "contract_content_hash": contract.content_hash(),
        "capture_semantic_version": contract.capture_semantic_version,
        "instrument": contract.instrument_scope,
        "exchange_symbol": contract.exchange_symbol,
        "session_id": str(plan.partition.session_id),
        "partitions": partitions,
        "window": {
            "utc_day": plan.partition.utc_day,
            "window_index": plan.window_index,
            **plan.window.to_payload(),
        },
        "segment": {
            "declared_semantics": "host_arrival_clock_contiguous_covered_span",
            "start_arrival_nanos": plan.start_arrival_nanos,
            "end_arrival_nanos_inclusive": plan.end_arrival_nanos,
            "excluded_head_nanos": plan.excluded_head_nanos,
            "excluded_tail_nanos": plan.excluded_tail_nanos,
            "admitted_records": admitted[0],
            "first_record_sequence": admitted[1],
            "last_record_sequence": admitted[2],
        },
        "clock_evidence": {
            "rule": CLOCK_BOUND_RULE_V1,
            "sample_hashes": [sample.sample_hash for sample in plan.clock.samples],
            "clock_evidence_hash": plan.clock.evidence_hash(),
            "max_venue_minus_host_upper_nanos": max(
                sample.venue_minus_host_upper_nanos for sample in plan.clock.samples
            ),
            "min_venue_minus_host_upper_nanos": min(
                sample.venue_minus_host_upper_nanos for sample in plan.clock.samples
            ),
        },
        "knowledge_span": {"first_micros": min(knowledge), "last_micros": max(knowledge)},
        "frames": {
            kind: {"logical_content_hash": value[0], "row_count": value[1]}
            for kind, value in sorted(frames.items())
        },
        "counts": dict(sorted(counts.items())),
        "timing_facts": {
            "observations_with_knowledge_time": timing.observations_with_knowledge_time,
            "observations_missing_knowledge_time": timing.observations_missing_knowledge_time,
            "distinct_knowledge_time_count": timing.distinct_knowledge_time_count,
        },
    }


def _issue(
    identity: dict[str, Any],
    *,
    timing: T4TimingFactsV1,
    frame_manifests: Mapping[str, str],
    sealed_at: datetime,
    raw_replayed: bool,
) -> FirstPartyT4SealV1:
    content_hash = sha256_json_v1(identity)
    manifests = dict(sorted(frame_manifests.items()))
    return FirstPartyT4SealV1(
        schema_version=T4_SEAL_SCHEMA_VERSION_V1,
        identity=identity,
        content_hash=content_hash,
        dataset_version_id=t4_dataset_version_id_v1(content_hash),
        source_id=UUID(str(identity["source_id"])),
        timing_facts=timing,
        frame_manifests=manifests,
        sealed_at=sealed_at,
        raw_replayed=raw_replayed,
        seal_token=_seal_token(content_hash, raw_replayed, sealed_at, manifests),
        _issuer=_ISSUER,
    )


def seal_t4_segment_v1(
    plan: T4SegmentPlanV1,
    *,
    store: ResearchFrameStoreV1,
    sealed_at: datetime | None = None,
    contract: FirstPartyCaptureContractV1 | None = None,
) -> FirstPartyT4SealV1:
    """Normalize one segment, write its frames, and issue its seal.

    ``sealed_at`` is read on the recorder host's clock (the same clock the
    arrivals are on), which the doctrine's recorded-after-arrival check needs.
    It is audit-only: it is not part of the dataset identity.
    """
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    output, counts, admitted = normalize_t4_segment_v1(plan, contract=authorized)
    timing = _timing_facts(output)
    lineage_base = {
        "source_id": str(authorized.source_id),
        "session_id": str(plan.partition.session_id),
        "window": f"{plan.partition.utc_day}:{plan.window_index}",
        "segment_start_arrival_nanos": plan.start_arrival_nanos,
        "normalization_semantic_version": T4_NORMALIZATION_SEMANTIC_VERSION_V1,
    }
    manifests: dict[str, FrameManifestV1] = {}
    for frame in T4_FRAMES_V1:
        manifests[frame.kind] = store.write_frame(
            frame, _FRAME_ROWS[frame.kind](output), lineage={**lineage_base, "frame": frame.kind}
        )
    identity = _identity(
        plan, contract=authorized, output=output, counts=counts, admitted=admitted,
        frames={kind: (item.logical_content_hash, item.row_count) for kind, item in manifests.items()},
        timing=timing,
    )
    when = datetime.now(UTC) if sealed_at is None else sealed_at
    if when.tzinfo is None:
        raise FirstPartyT4SealError("sealed_at_must_be_timezone_aware")
    sealed_nanos = (when - _EPOCH) // timedelta(microseconds=1) * 1_000
    if sealed_nanos < plan.end_arrival_nanos:
        # Same host clock as the arrivals: sealing before the last admitted
        # arrival is an ordering violation, not skew.
        raise FirstPartyT4SealError("sealed_before_the_last_admitted_arrival")
    return _issue(
        identity, timing=timing,
        frame_manifests={kind: item.manifest_hash for kind, item in manifests.items()},
        sealed_at=when, raw_replayed=True,
    )


def _plan_from_identity(discovery: T4DiscoveryV1, identity: Mapping[str, Any]) -> T4SegmentPlanV1:
    """Re-derive the exact segment an identity names from today's COMPLETE evidence.

    The bound samples must still be present, contiguous in the session's
    sample series, and must yield -- under the rule -- exactly the sealed
    segment bounds. A wider or narrower claim is refused, never adjusted.
    """
    try:
        session_id = UUID(str(identity["session_id"]))
        window_ref = identity["window"]
        segment = identity["segment"]
        wanted_hashes = [str(item) for item in identity["clock_evidence"]["sample_hashes"]]
        start = int(segment["start_arrival_nanos"])
        end = int(segment["end_arrival_nanos_inclusive"])
    except (KeyError, TypeError, ValueError) as error:
        raise FirstPartyT4SealError("sealed_identity_malformed") from error
    session_clock = discovery.session_clocks.get(session_id)
    if session_clock is None:
        raise FirstPartyT4SealError("sealed_session_has_no_admissible_clock_evidence")
    partition = next(
        (
            item for item in discovery.complete_partitions
            if item.session_id == session_id and item.utc_day == window_ref.get("utc_day")
        ),
        None,
    )
    if partition is None:
        raise FirstPartyT4SealError("sealed_records_partition_is_not_complete")
    index = window_ref.get("window_index")
    if not isinstance(index, int) or not 0 <= index < len(partition.coverage):
        raise FirstPartyT4SealError("sealed_window_not_found")
    window = partition.coverage[index]
    run = session_clock.used_run(start, end)
    if run is None:
        raise FirstPartyT4SealError("sealed_segment_is_not_bracketed")
    used = [sample.sample_hash for sample, _ in session_clock.samples[run[0]: run[1] + 1]]
    if used != wanted_hashes:
        raise FirstPartyT4SealError("sealed_clock_samples_do_not_reproduce")
    clock = session_clock.evidence()
    containing = [
        (low, high) for low, high in clock.admissible_intervals() if low <= start and end <= high
    ]
    if len(containing) != 1:
        raise FirstPartyT4SealError("sealed_segment_is_not_inside_one_admissible_interval")
    low, high = containing[0]
    if start != max(window.start_utc_nanos, low) or end != min(window.last_proven_utc_nanos, high):
        raise FirstPartyT4SealError("sealed_segment_bounds_do_not_follow_the_rule")
    if (
        end < window.last_proven_utc_nanos
        and high == clock.samples[-1].host_send_utc_nanos
        and not session_clock.finished
    ):
        raise FirstPartyT4SealError("sealed_segment_is_not_final_under_current_evidence")
    return _plan(session_clock, partition, index, window, start, end)


def verify_t4_dataset_v1(
    identity: Mapping[str, Any],
    *,
    frame_manifests: Mapping[str, str],
    sealed_at: datetime,
    store: ResearchFrameStoreV1,
    capture_root: Path,
    contract: FirstPartyCaptureContractV1 | None = None,
) -> FirstPartyT4SealV1:
    """Rebuild a sealed segment from raw capture and re-issue its seal, or raise.

    Everything is re-derived: partition status and replay, clock samples, the
    segment bounds, every normalized observation, every frame's logical hash
    and every counter. The rebuilt identity must equal ``identity`` exactly,
    and every stored frame object must re-hash byte for byte and carry the
    rebuilt logical hash. Nothing is written.
    """
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    plan = _plan_from_identity(discover_t4_segments_v1(capture_root, contract=authorized), identity)
    if sealed_at.tzinfo is None or (sealed_at - _EPOCH) // timedelta(microseconds=1) * 1_000 < plan.end_arrival_nanos:
        raise FirstPartyT4SealError("sealed_before_the_last_admitted_arrival")
    output, counts, admitted = normalize_t4_segment_v1(plan, contract=authorized)
    timing = _timing_facts(output)
    frames: dict[str, tuple[str, int]] = {}
    for frame in T4_FRAMES_V1:
        frames[frame.kind] = logical_content_hash_v1(frame, _FRAME_ROWS[frame.kind](output))
    rebuilt = _identity(
        plan, contract=authorized, output=output, counts=counts, admitted=admitted,
        frames=frames, timing=timing,
    )
    if canonical_json_v1(rebuilt) != canonical_json_v1(dict(identity)):
        raise FirstPartyT4SealError("rebuilt_identity_differs_from_the_sealed_identity")
    if set(frame_manifests) != set(frames):
        raise FirstPartyT4SealError("sealed_frame_manifest_set_mismatch")
    for kind, manifest_hash in frame_manifests.items():
        try:
            manifest = store.load_manifest(manifest_hash)
            store.verify(manifest)
        except ResearchDataPlaneError as error:
            raise FirstPartyT4SealError(f"sealed_frame_not_verifiable:{kind}:{error}") from error
        if (manifest.frame_kind, manifest.logical_content_hash, manifest.row_count) != (
            kind, frames[kind][0], frames[kind][1]
        ):
            raise FirstPartyT4SealError(f"sealed_frame_disagrees_with_rebuild:{kind}")
    return _issue(
        rebuilt, timing=timing, frame_manifests=frame_manifests, sealed_at=sealed_at,
        raw_replayed=True,
    )


def restore_t4_seal_without_replay_v1(
    identity: Mapping[str, Any],
    *,
    frame_manifests: Mapping[str, str],
    sealed_at: datetime,
    store: ResearchFrameStoreV1,
) -> FirstPartyT4SealV1:
    """A catalogued seal whose *frames* re-verify, without re-reading raw capture.

    For audit and descriptive reads only: it proves the stored frames are the
    ones the identity names, not that the identity is what the raw evidence
    produces. It is issued with ``raw_replayed=False``, which no sealed-clock
    resolver accepts.
    """
    frames = identity.get("frames")
    if not isinstance(frames, Mapping) or set(frames) != set(frame_manifests):
        raise FirstPartyT4SealError("sealed_frame_manifest_set_mismatch")
    for kind, manifest_hash in frame_manifests.items():
        manifest = store.load_manifest(manifest_hash)
        store.verify(manifest)
        expected = frames[kind]
        if (manifest.frame_kind, manifest.logical_content_hash, manifest.row_count) != (
            kind, expected["logical_content_hash"], expected["row_count"]
        ):
            raise FirstPartyT4SealError(f"sealed_frame_disagrees_with_identity:{kind}")
    raw = identity.get("timing_facts", {})
    timing = T4TimingFactsV1(
        int(raw["observations_with_knowledge_time"]),
        int(raw["observations_missing_knowledge_time"]),
        int(raw["distinct_knowledge_time_count"]),
    )
    return _issue(
        dict(identity), timing=timing, frame_manifests=frame_manifests, sealed_at=sealed_at,
        raw_replayed=False,
    )


def iter_frame_rows_v1(
    seal: FirstPartyT4SealV1, kind: str, store: ResearchFrameStoreV1
) -> Iterator[tuple[object, ...]]:
    """Rows of one sealed frame, from the manifest the seal names."""
    if not seal.integrity_verified():
        raise FirstPartyT4SealError("seal_integrity_failed")
    manifest = store.load_manifest(seal.frame_manifests[kind])
    if manifest.logical_content_hash != seal.frame_identity(kind)["logical_content_hash"]:
        raise FirstPartyT4SealError(f"frame_manifest_not_the_sealed_frame:{kind}")
    yield from store.iter_rows(manifest)


__all__ = [
    "T4_FRAMES_V1",
    "T4_SEAL_SCHEMA_VERSION_V1",
    "FirstPartyCaptureArchiveError",
    "FirstPartyT4SealError",
    "FirstPartyT4SealV1",
    "T4DiscoveryV1",
    "T4ExclusionV1",
    "T4PartitionV1",
    "T4SegmentPlanV1",
    "T4TimingFactsV1",
    "discover_t4_segments_v1",
    "iter_frame_rows_v1",
    "normalize_t4_segment_v1",
    "restore_t4_seal_without_replay_v1",
    "seal_t4_segment_v1",
    "t4_dataset_version_id_v1",
    "verify_t4_dataset_v1",
]
