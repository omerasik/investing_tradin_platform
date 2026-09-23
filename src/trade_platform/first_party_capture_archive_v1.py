"""Phase 3Z.2 -- the immutable first-party capture archive: records, clock, partitions, replay.

``RESEARCH_ONLY``. Pure and offline: nothing here opens a socket. The network
lives in :mod:`trade_platform.bybit_public_websocket_recorder_v1`, so every rule
below is testable without a feed and every exceptional condition -- a clock
step, a reconnect, a crashed process -- can be exercised from fixtures.

What one record proves
----------------------
A captured message is evidence of exactly one observation arriving at exactly
one instant. :class:`FirstPartyCaptureRecordV1` keeps the exchange-native
payload **verbatim** as the text the socket yielded; it is never parsed and
re-serialized, because re-serialization silently normalizes key order, number
formatting and whitespace, and the archive would then no longer hold what Bybit
actually sent. Everything the platform adds -- arrival, ordering, identity --
sits beside the payload, never inside it.

The clock, and what it is honestly worth
----------------------------------------
Two clocks are recorded per message and they answer different questions.
``arrival_utc_nanos`` is the platform wall clock, which is what a decision time
must eventually be expressed in but which NTP can step forwards or backwards.
``arrival_monotonic_nanos`` is a process-local counter that cannot go backwards
but has no meaning outside its process. The archive stores both, plus the
operating system's *measured* clock resolution
(:func:`measure_clock_resolution_nanos`), because Python's integer-nanosecond
API says nothing about the underlying clock -- on Windows the system clock
commonly ticks at well under nanosecond precision, and claiming otherwise from
the API shape alone would be an invented guarantee.

:class:`CaptureClockMonitorV1` turns that pair into fail-closed evidence. Within
one session the monotonic counter must not regress and the wall clock must not
regress; either is refused outright. A wall-versus-monotonic divergence beyond
:data:`CLOCK_DIVERGENCE_TOLERANCE_NANOS` is a *discontinuity*: an NTP step, a
host suspend, or a virtual-machine pause. It is never smoothed. It closes the
current coverage interval and opens an explicit gap, because the platform
cannot prove what it was or was not receiving across it.

Sessions, coverage and gaps
---------------------------
A session is one recorder process's lifetime, identified by a UUID, and a
monotonic counter is never compared across sessions. Coverage is a set of
declared half-open ``[start, end)`` arrival windows. A window starts at the
first positive proof that data is flowing -- the subscription acknowledgement
or an authorized record, never the socket opening -- and its measured
``last_proven_utc_nanos`` is the last such proof. Its exclusive ``end`` is the
representational bound ``last_proven + 1`` (see
:class:`CaptureCoverageIntervalV1`), so the first and the last proven
observation both lie inside it and nothing after the last one is claimed.

Every window names its end proof: a bounded or interrupted operator stop, a UTC
day rollover, a lost or peer-closed connection, a clock discontinuity, a
rejected message, or an unexpected recorder failure. The non-operator causes
also open an explicit gap starting at the window's exclusive end. There is no
generic "clean close": a ``finally`` block running is not evidence that capture
ended well.

What a hard crash leaves is exactly what it can prove -- nothing. It cannot run
``finally``, so its partition keeps its OPEN marker and has no manifest: it is
PARTIAL, it declares no window, and it is excluded from
:func:`derive_archive_availability_v1`. That function is how a consumer learns
that the stretch between one session's last proven observation and the next
session's first is unavailable; the gap is derived from positive evidence,
never declared by a process that was not there to declare it.

Anything outside every window is ``UNAVAILABLE``. A quiet market and an
unobserved market are therefore different states, which is the entire point: a
silent ``tickers`` channel inside a covered window is evidence of no update,
while the same silence outside one is evidence of nothing at all.

Partitions and immutability
---------------------------
Evidence is written under a UTC-day partition in a layout that maps directly
onto object-storage keys, so a later move to object storage is a copy rather
than a migration. A partition being written carries an
:data:`OPEN_MARKER_NAME` sentinel and no manifest. Finalizing writes
``MANIFEST.json`` -- per-file SHA-256, record count, arrival bounds, coverage,
gaps and clock facts -- and removes the sentinel. A partition is COMPLETE only
when the manifest exists and verifies, so a partial partition can never be
mistaken for a finished one, and finalizing refuses to overwrite a manifest
that already exists.

Raw capture never enters PostgreSQL and never enters Git. It is high-cardinality
exchange traffic; the low-cardinality evidence tables would be the wrong home
for it and a migration to hold it would be unjustified. The archive root
therefore lives outside the working tree by default.

Replay
------
:func:`replay_partition_v1` re-reads a finalized partition, re-verifies every
file checksum and every record content hash, re-derives every record from its
verbatim payload under the authorized contract, re-checks ordering, places
every record inside a declared window and reconciles the counts, and yields the
identical record sequence. Anything that does not reproduce fails closed rather
than being skipped. :func:`verify_partition_v1` exhausts a replay for callers
that need the whole proof before using any of it.
"""

from __future__ import annotations

import hashlib
import json
import time
from bisect import bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .first_party_capture_authority_v1 import (
    FIRST_PARTY_RECORD_SCHEMA_VERSION_V1,
    BybitMessageTypeV1,
    BybitPublicChannelV1,
    FirstPartyCaptureContractV1,
    first_party_bybit_capture_contract_v1,
)

ARCHIVE_LAYOUT_VERSION: Final = "v1"
MANIFEST_SCHEMA_VERSION: Final = "first-party-capture-manifest-v1"
SESSION_SCHEMA_VERSION: Final = "first-party-capture-session-v1"

RECORDS_FILE_NAME: Final = "records.ndjson"
LIFECYCLE_FILE_NAME: Final = "lifecycle.ndjson"
SESSION_FILE_NAME: Final = "session.json"
MANIFEST_FILE_NAME: Final = "MANIFEST.json"
OPEN_MARKER_NAME: Final = "OPEN"

#: How far the wall clock may drift from the monotonic counter between two
#: consecutive messages before the interval is declared discontinuous. Generous
#: enough to absorb ordinary scheduling jitter, far tighter than any NTP step,
#: host suspend or VM pause worth detecting. A pure engineering tolerance: it
#: carries no economic meaning and no market assumption.
CLOCK_DIVERGENCE_TOLERANCE_NANOS: Final = 2_000_000_000

#: Why a coverage window stopped. Every value names a cause the recorder
#: actually observed; there is deliberately no generic "clean close", because a
#: ``finally`` block running proves only that Python unwound, not that capture
#: ended well. A hard crash has no end proof at all: it leaves no manifest.
END_PROOF_OPERATOR_BOUNDED_STOP: Final = "OPERATOR_BOUNDED_STOP"
END_PROOF_OPERATOR_INTERRUPT: Final = "OPERATOR_INTERRUPT"
END_PROOF_UTC_DAY_ROLLOVER: Final = "UTC_DAY_ROLLOVER"
END_PROOF_CONNECTION_LOST: Final = "CONNECTION_LOST"
END_PROOF_PEER_CLOSED: Final = "PEER_CLOSED"
END_PROOF_CLOCK_DISCONTINUITY: Final = "CLOCK_DISCONTINUITY"
END_PROOF_CONTRACT_VIOLATION: Final = "CONTRACT_VIOLATION"
END_PROOF_RECORDER_FAILURE: Final = "RECORDER_FAILURE"

END_PROOFS_V1: Final = frozenset(
    {
        END_PROOF_OPERATOR_BOUNDED_STOP,
        END_PROOF_OPERATOR_INTERRUPT,
        END_PROOF_UTC_DAY_ROLLOVER,
        END_PROOF_CONNECTION_LOST,
        END_PROOF_PEER_CLOSED,
        END_PROOF_CLOCK_DISCONTINUITY,
        END_PROOF_CONTRACT_VIOLATION,
        END_PROOF_RECORDER_FAILURE,
    }
)

PARTITION_STATUS_COMPLETE: Final = "COMPLETE"
PARTITION_STATUS_PARTIAL: Final = "PARTIAL"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.first_party_capture_archive_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class FirstPartyCaptureArchiveError(ValueError):
    """Raised when captured evidence is malformed, unprovable or would be overwritten."""


class CaptureLifecycleKindV1(StrEnum):
    """Transport and clock events that bound or interrupt coverage."""

    SESSION_STARTED = "SESSION_STARTED"
    CONNECTION_OPENED = "CONNECTION_OPENED"
    SUBSCRIPTIONS_ACKNOWLEDGED = "SUBSCRIPTIONS_ACKNOWLEDGED"
    CONNECTION_LOST = "CONNECTION_LOST"
    RECONNECT_STARTED = "RECONNECT_STARTED"
    CLOCK_DISCONTINUITY = "CLOCK_DISCONTINUITY"
    MESSAGE_REJECTED = "MESSAGE_REJECTED"
    RECORDER_FAILED = "RECORDER_FAILED"
    SESSION_CLOSED = "SESSION_CLOSED"


class CaptureGapKindV1(StrEnum):
    """Why an interval is not covered. Never inferred from quiet traffic.

    There is deliberately no "recorder not running" kind: nothing written by a
    process can prove the process was absent (a crashed session may well have
    been running and left only a PARTIAL partition). Between two sessions'
    proven windows the honest statement is :attr:`SESSION_BOUNDARY` -- nothing
    is proven there -- and that is derived, not declared.
    """

    CONNECTION_LOSS = "CONNECTION_LOSS"
    CLOCK_DISCONTINUITY = "CLOCK_DISCONTINUITY"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    RECORDER_FAILURE = "RECORDER_FAILURE"
    PARTITION_ROLLOVER = "PARTITION_ROLLOVER"
    SESSION_BOUNDARY = "SESSION_BOUNDARY"


#: End proofs that interrupt a session the recorder did not choose to stop, and
#: therefore open an explicit gap at the window's exclusive end.
GAP_KIND_FOR_END_PROOF_V1: Final[Mapping[str, CaptureGapKindV1]] = {
    END_PROOF_CONNECTION_LOST: CaptureGapKindV1.CONNECTION_LOSS,
    END_PROOF_PEER_CLOSED: CaptureGapKindV1.CONNECTION_LOSS,
    END_PROOF_CLOCK_DISCONTINUITY: CaptureGapKindV1.CLOCK_DISCONTINUITY,
    END_PROOF_CONTRACT_VIOLATION: CaptureGapKindV1.CONTRACT_VIOLATION,
    END_PROOF_RECORDER_FAILURE: CaptureGapKindV1.RECORDER_FAILURE,
}


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    """Stream a file's SHA-256 so a large partition never has to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def measure_clock_resolution_nanos() -> int:
    """The operating system's own reported wall-clock resolution, in nanoseconds.

    Recorded with every session so nobody reads nanosecond *accuracy* into an
    integer-nanosecond API. On a typical Windows host this is far coarser than
    one nanosecond, and saying so is part of the evidence.
    """
    return max(1, round(time.get_clock_info("time").resolution * 1_000_000_000))


def nanos_to_datetime(nanos: int) -> datetime:
    """UTC datetime for an integer-nanosecond wall clock, rounded UP to microseconds.

    Offered for interoperability only. The integer nanoseconds stay the
    authoritative value: a ``datetime`` cannot hold them, and truncation would
    move an arrival *earlier* -- claiming knowledge before it existed, the
    unsafe direction. Rounding up can only make it look later. Pure integer
    arithmetic: a float would round sub-microsecond instants either way.
    """
    return _EPOCH + timedelta(microseconds=-(-nanos // 1_000))


def utc_day_of_nanos(nanos: int) -> date:
    """The UTC calendar day an integer-nanosecond instant falls in, exactly.

    ``nanos / 1e9`` in floating point carries roughly 0.4 microsecond error at
    current epochs, enough to assign an instant just before midnight to the
    next day. Integer floor division cannot.
    """
    return (_EPOCH + timedelta(seconds=nanos // 1_000_000_000)).date()


def knowledge_bound_utc_nanos(arrival_utc_nanos: int, clock_resolution_nanos: int) -> int:
    """The latest instant a record's true receipt can have happened, per the measured clock.

    A wall clock with resolution ``r`` reports the last tick at or before the
    true instant, so a reading ``R`` means the message was in hand somewhere in
    ``[R, R + r)``. A consumer that needs a knowledge time which can never be
    early (a decision time, a PIT join) must use this bound, never the raw
    reading. Phase 3Z.2 stores the raw reading and the measured resolution side
    by side and never widens coverage by the resolution: coverage stays in the
    reported-clock domain, where it is exact.
    """
    if clock_resolution_nanos < 1:
        raise FirstPartyCaptureArchiveError("clock_resolution_must_be_positive")
    return arrival_utc_nanos + clock_resolution_nanos


@dataclass(frozen=True, slots=True)
class CaptureClockReadingV1:
    """One paired read of the two clocks, taken the instant a message arrived."""

    arrival_utc_nanos: int
    arrival_monotonic_nanos: int

    @classmethod
    def now(cls) -> CaptureClockReadingV1:
        # Monotonic first: if the two reads straddle a wall-clock step, an
        # arrival that looks slightly late is safer than one that looks early.
        monotonic = time.monotonic_ns()
        return cls(arrival_utc_nanos=time.time_ns(), arrival_monotonic_nanos=monotonic)


@dataclass(frozen=True, slots=True)
class ClockVerdictV1:
    """What the monitor concluded about one reading, relative to the previous one."""

    accepted: bool
    discontinuity: bool
    reasons: tuple[str, ...]
    wall_delta_nanos: int | None
    monotonic_delta_nanos: int | None

    @property
    def divergence_nanos(self) -> int | None:
        if self.wall_delta_nanos is None or self.monotonic_delta_nanos is None:
            return None
        return self.wall_delta_nanos - self.monotonic_delta_nanos


class CaptureClockMonitorV1:
    """Fail-closed clock health for one session. Never smooths, never reorders."""

    __slots__ = ("_previous", "_tolerance_nanos")

    def __init__(self, *, tolerance_nanos: int = CLOCK_DIVERGENCE_TOLERANCE_NANOS) -> None:
        if tolerance_nanos < 0:
            raise FirstPartyCaptureArchiveError("clock_tolerance_must_not_be_negative")
        self._tolerance_nanos = tolerance_nanos
        self._previous: CaptureClockReadingV1 | None = None

    def observe(self, reading: CaptureClockReadingV1) -> ClockVerdictV1:
        """Judge one reading. A discontinuity is reported, never absorbed."""
        previous = self._previous
        if previous is None:
            self._previous = reading
            return ClockVerdictV1(
                accepted=True,
                discontinuity=False,
                reasons=(),
                wall_delta_nanos=None,
                monotonic_delta_nanos=None,
            )

        wall_delta = reading.arrival_utc_nanos - previous.arrival_utc_nanos
        monotonic_delta = reading.arrival_monotonic_nanos - previous.arrival_monotonic_nanos
        reasons: list[str] = []

        if monotonic_delta < 0:
            # A process-local counter cannot go backwards. If it did, this is
            # not one ordered recording and nothing here can be trusted.
            reasons.append("monotonic_counter_regressed_within_session")
        if wall_delta < 0:
            reasons.append("wall_clock_regressed_within_session")

        accepted = not reasons
        discontinuity = False
        if accepted and abs(wall_delta - monotonic_delta) > self._tolerance_nanos:
            # A step, a suspend or a VM pause. The platform cannot prove what it
            # was receiving across it, so coverage stops here.
            discontinuity = True
            reasons.append("wall_versus_monotonic_divergence_exceeds_tolerance")

        if accepted:
            self._previous = reading
        return ClockVerdictV1(
            accepted=accepted,
            discontinuity=discontinuity,
            reasons=tuple(reasons),
            wall_delta_nanos=wall_delta,
            monotonic_delta_nanos=monotonic_delta,
        )

    def reset(self) -> None:
        """Begin a fresh continuity claim, after a discontinuity or a reconnect."""
        self._previous = None


@dataclass(frozen=True, slots=True)
class FirstPartyCaptureRecordV1:
    """One captured message: the exchange's verbatim payload plus our evidence."""

    schema_version: str
    source_id: UUID
    session_id: UUID
    sequence: int
    arrival_utc_nanos: int
    arrival_monotonic_nanos: int
    exchange: str
    endpoint: str
    channel: str
    topic: str
    instrument: str
    message_type: str | None
    exchange_timestamp_millis: int | None
    #: Exactly the text the socket yielded. Never parsed and re-emitted.
    payload_text: str
    content_hash: str = field(default="")

    def hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": str(self.source_id),
            "session_id": str(self.session_id),
            "sequence": self.sequence,
            "arrival_utc_nanos": self.arrival_utc_nanos,
            "arrival_monotonic_nanos": self.arrival_monotonic_nanos,
            "exchange": self.exchange,
            "endpoint": self.endpoint,
            "channel": self.channel,
            "topic": self.topic,
            "instrument": self.instrument,
            "message_type": self.message_type,
            "exchange_timestamp_millis": self.exchange_timestamp_millis,
            "payload_sha256": _sha256_text(self.payload_text),
        }

    def compute_content_hash(self) -> str:
        return _sha256_text(_canonical_json(self.hash_payload()))

    def integrity_verified(self) -> bool:
        return bool(self.content_hash) and self.content_hash == self.compute_content_hash()

    @property
    def arrival_utc(self) -> datetime:
        return nanos_to_datetime(self.arrival_utc_nanos)

    def to_json_line(self) -> str:
        payload = self.hash_payload()
        payload["payload"] = self.payload_text
        payload["content_hash"] = self.content_hash
        return _canonical_json(payload)

    @classmethod
    def from_json_line(cls, line: str) -> FirstPartyCaptureRecordV1:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            # A torn line from a failed write is evidence damage, not noise.
            raise FirstPartyCaptureArchiveError("captured_record_line_is_not_valid_json") from error
        record = cls(
            schema_version=raw["schema_version"],
            source_id=UUID(raw["source_id"]),
            session_id=UUID(raw["session_id"]),
            sequence=int(raw["sequence"]),
            arrival_utc_nanos=int(raw["arrival_utc_nanos"]),
            arrival_monotonic_nanos=int(raw["arrival_monotonic_nanos"]),
            exchange=raw["exchange"],
            endpoint=raw["endpoint"],
            channel=raw["channel"],
            topic=raw["topic"],
            instrument=raw["instrument"],
            message_type=raw["message_type"],
            exchange_timestamp_millis=raw["exchange_timestamp_millis"],
            payload_text=raw["payload"],
            content_hash=raw["content_hash"],
        )
        if _sha256_text(record.payload_text) != raw["payload_sha256"]:
            raise FirstPartyCaptureArchiveError("captured_payload_does_not_match_its_digest")
        if not record.integrity_verified():
            raise FirstPartyCaptureArchiveError("captured_record_content_hash_mismatch")
        return record


def build_capture_record_v1(
    *,
    contract: FirstPartyCaptureContractV1,
    session_id: UUID,
    sequence: int,
    clock: CaptureClockReadingV1,
    payload_text: str,
) -> FirstPartyCaptureRecordV1:
    """Derive one record from a verbatim payload, proving identity positively.

    Refuses anything the contract does not authorize: a foreign topic, a symbol
    that disagrees with its own topic, or a vendor-style ``generated`` marker.
    None of these is a relabelling opportunity.
    """
    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError as error:
        raise FirstPartyCaptureArchiveError("captured_payload_is_not_valid_json") from error
    if not isinstance(parsed, dict):
        raise FirstPartyCaptureArchiveError("captured_payload_is_not_an_object")

    if parsed.get("generated") is True:
        # A synthesized row is a reconstruction, not something a recorder saw
        # arrive. Refused outright rather than downgraded.
        raise FirstPartyCaptureArchiveError("generated_records_are_not_capture_evidence")

    topic = parsed.get("topic")
    if not isinstance(topic, str) or topic not in contract.topics():
        raise FirstPartyCaptureArchiveError("captured_topic_is_not_authorized_by_the_contract")
    channel, _, symbol = topic.partition(".")
    if symbol != contract.exchange_symbol:
        raise FirstPartyCaptureArchiveError("captured_topic_symbol_mismatch")
    if channel not in contract.authorized_channels:
        raise FirstPartyCaptureArchiveError("captured_channel_is_not_authorized")

    _require_payload_symbol_agrees(parsed, channel, contract.exchange_symbol)

    # Every authorized message carries a recognised type and an integer venue
    # timestamp. Absence is a contract change, not an optional field: a
    # message that no longer says whether it is a snapshot or a delta cannot
    # be replayed into state safely.
    message_type = parsed.get("type")
    if not isinstance(message_type, str):
        raise FirstPartyCaptureArchiveError("captured_message_type_is_malformed")
    if message_type not in {item.value for item in BybitMessageTypeV1}:
        raise FirstPartyCaptureArchiveError("captured_message_type_is_not_recognized")

    exchange_ts = parsed.get("ts")
    if not isinstance(exchange_ts, int) or isinstance(exchange_ts, bool):
        raise FirstPartyCaptureArchiveError("captured_exchange_timestamp_is_malformed")

    record = FirstPartyCaptureRecordV1(
        schema_version=FIRST_PARTY_RECORD_SCHEMA_VERSION_V1,
        source_id=contract.source_id,
        session_id=session_id,
        sequence=sequence,
        arrival_utc_nanos=clock.arrival_utc_nanos,
        arrival_monotonic_nanos=clock.arrival_monotonic_nanos,
        exchange=contract.originating_exchange,
        endpoint=contract.endpoint,
        channel=channel,
        topic=topic,
        instrument=contract.instrument_scope,
        message_type=message_type,
        exchange_timestamp_millis=exchange_ts,
        payload_text=payload_text,
    )
    return _with_content_hash(record)


def _require_payload_symbol_agrees(
    parsed: Mapping[str, Any], channel: str, expected_symbol: str
) -> None:
    """The body has its channel's shape and names the topic's symbol, positively.

    ``tickers`` carries one object with ``symbol``; ``publicTrade`` carries a
    non-empty list of objects each with ``s``. A missing symbol is refused, not
    tolerated: it cannot be proven to belong to the subscribed instrument.
    """
    data = parsed.get("data")
    if channel == BybitPublicChannelV1.TICKERS.value:
        if not isinstance(data, dict):
            raise FirstPartyCaptureArchiveError("captured_ticker_data_is_not_an_object")
        symbols = [data.get("symbol")]
    elif channel == BybitPublicChannelV1.PUBLIC_TRADE.value:
        if not isinstance(data, list) or not data:
            raise FirstPartyCaptureArchiveError("captured_trade_data_is_not_a_non_empty_list")
        if not all(isinstance(trade, dict) for trade in data):
            raise FirstPartyCaptureArchiveError("captured_trade_entry_is_not_an_object")
        symbols = [trade.get("s") for trade in data]
    else:  # pragma: no cover - the channel was checked against the contract above
        raise FirstPartyCaptureArchiveError("captured_channel_is_not_authorized")
    for symbol in symbols:
        if not isinstance(symbol, str):
            raise FirstPartyCaptureArchiveError("captured_payload_symbol_is_missing")
        if symbol != expected_symbol:
            raise FirstPartyCaptureArchiveError("captured_payload_symbol_disagrees_with_topic")


def _with_content_hash(record: FirstPartyCaptureRecordV1) -> FirstPartyCaptureRecordV1:
    import dataclasses

    return dataclasses.replace(record, content_hash=record.compute_content_hash())


@dataclass(frozen=True, slots=True)
class CaptureLifecycleEventV1:
    """One transport or clock event, stamped with the same two clocks."""

    kind: str
    arrival_utc_nanos: int
    arrival_monotonic_nanos: int
    detail: str | None = None
    #: Verbatim text of a message the recorder refused, so a rejection is
    #: accounted for rather than disappearing. ``None`` for every other event.
    payload_text: str | None = None

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "arrival_utc_nanos": self.arrival_utc_nanos,
            "arrival_monotonic_nanos": self.arrival_monotonic_nanos,
            "detail": self.detail,
        }
        if self.payload_text is not None:
            payload["payload"] = self.payload_text
        return _canonical_json(payload)

    @classmethod
    def from_json_line(cls, line: str) -> CaptureLifecycleEventV1:
        raw = json.loads(line)
        return cls(
            kind=raw["kind"],
            arrival_utc_nanos=int(raw["arrival_utc_nanos"]),
            arrival_monotonic_nanos=int(raw["arrival_monotonic_nanos"]),
            detail=raw.get("detail"),
            payload_text=raw.get("payload"),
        )


@dataclass(frozen=True, slots=True)
class CaptureCoverageIntervalV1:
    """A half-open ``[start, end)`` arrival window the recorder can positively prove.

    Two different kinds of number live here, and they are kept apart on purpose.
    ``start_utc_nanos`` and ``last_proven_utc_nanos`` are *measured* readings:
    the first and the last observation the window proves (a subscription
    acknowledgement or a record). ``end_utc_nanos`` is *not* a measurement. It
    is the representational exclusive bound ``last_proven_utc_nanos + 1`` --
    the smallest half-open end that still contains the last proven instant in
    the integer-nanosecond domain -- and it is derived, never stored
    independently, so the two can never disagree.

    Why ``+ 1`` and not ``+ clock resolution``: coverage asserts which reported
    readings were observed, and ``+ 1`` claims exactly those and nothing after
    them. Widening by the resolution would claim up to one tick of observation
    past the last message nobody saw. The resolution matters to *knowledge
    time*, not to coverage -- see :func:`knowledge_bound_utc_nanos`.
    """

    start_utc_nanos: int
    last_proven_utc_nanos: int
    end_proof: str
    record_count: int

    def __post_init__(self) -> None:
        if self.last_proven_utc_nanos < self.start_utc_nanos:
            raise FirstPartyCaptureArchiveError("coverage_interval_ends_before_it_starts")
        if self.record_count < 0:
            raise FirstPartyCaptureArchiveError("coverage_record_count_must_not_be_negative")
        if self.end_proof not in END_PROOFS_V1:
            raise FirstPartyCaptureArchiveError(f"coverage_end_proof_not_recognized:{self.end_proof}")

    @property
    def end_utc_nanos(self) -> int:
        """Exclusive representational bound. Not a measured instant."""
        return self.last_proven_utc_nanos + 1

    def contains(self, utc_nanos: int) -> bool:
        return self.start_utc_nanos <= utc_nanos < self.end_utc_nanos

    def to_payload(self) -> dict[str, Any]:
        return {
            "start_utc_nanos": self.start_utc_nanos,
            "last_proven_utc_nanos": self.last_proven_utc_nanos,
            "end_utc_nanos_exclusive": self.end_utc_nanos,
            "end_proof": self.end_proof,
            "record_count": self.record_count,
        }

    @classmethod
    def from_payload(cls, item: Mapping[str, Any]) -> CaptureCoverageIntervalV1:
        interval = cls(
            start_utc_nanos=int(item["start_utc_nanos"]),
            last_proven_utc_nanos=int(item["last_proven_utc_nanos"]),
            end_proof=str(item["end_proof"]),
            record_count=int(item["record_count"]),
        )
        if int(item["end_utc_nanos_exclusive"]) != interval.end_utc_nanos:
            raise FirstPartyCaptureArchiveError("coverage_exclusive_end_disagrees_with_last_proven")
        return interval


@dataclass(frozen=True, slots=True)
class CaptureGapV1:
    """A half-open ``[start, end)`` interval the recorder explicitly could not observe.

    ``start_utc_nanos`` is always the preceding window's exclusive end, so a gap
    begins strictly outside proven coverage. ``end_utc_nanos`` is the next
    window's start when one followed, or ``None`` -- open-ended -- when the
    partition ended before coverage was proven again. Never bridged.
    """

    start_utc_nanos: int
    end_utc_nanos: int | None
    kind: str
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.end_utc_nanos is not None and self.end_utc_nanos < self.start_utc_nanos:
            raise FirstPartyCaptureArchiveError("gap_ends_before_it_starts")
        if self.kind not in {item.value for item in CaptureGapKindV1}:
            raise FirstPartyCaptureArchiveError(f"gap_kind_not_recognized:{self.kind}")

    def overlaps(self, interval: CaptureCoverageIntervalV1) -> bool:
        """Whether this gap shares any instant with a coverage window."""
        if interval.end_utc_nanos <= self.start_utc_nanos:
            return False
        return self.end_utc_nanos is None or interval.start_utc_nanos < self.end_utc_nanos

    @classmethod
    def from_payload(cls, item: Mapping[str, Any]) -> CaptureGapV1:
        end = item["end_utc_nanos"]
        return cls(
            start_utc_nanos=int(item["start_utc_nanos"]),
            end_utc_nanos=None if end is None else int(end),
            kind=str(item["kind"]),
            detail=item.get("detail"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "start_utc_nanos": self.start_utc_nanos,
            "end_utc_nanos": self.end_utc_nanos,
            "kind": self.kind,
            "detail": self.detail,
        }


def partition_directory(
    root: Path, contract: FirstPartyCaptureContractV1, day: date, session_id: UUID
) -> Path:
    """The UTC-day partition path, laid out so an object-storage key maps one to one."""
    instrument_key = contract.instrument_scope.replace(":", "-")
    return (
        root
        / ARCHIVE_LAYOUT_VERSION
        / f"exchange={contract.originating_exchange}"
        / f"instrument={instrument_key}"
        / f"date={day.isoformat()}"
        / f"session={session_id}"
    )


class CapturePartitionWriterV1:
    """Append-only writer for one session's slice of one UTC day.

    Records are flushed as they arrive, so a killed process leaves a readable
    partial partition rather than an empty one. The partition stays PARTIAL --
    marked by an :data:`OPEN_MARKER_NAME` sentinel and the absence of a manifest
    -- until :meth:`finalize` succeeds.

    The writer does not trust its caller's coverage bookkeeping. Coverage
    windows and gaps must be declared in time order without overlapping, every
    record appended since the previous window must fall inside the window that
    claims it and match its count, and :meth:`finalize` refuses to seal a
    partition holding a record no window accounts for.
    """

    __slots__ = (
        "_clock_resolution_nanos",
        "_contract",
        "_coverage",
        "_day",
        "_directory",
        "_finalized",
        "_frontier_nanos",
        "_gaps",
        "_lifecycle_handle",
        "_records_handle",
        "_session_id",
        "_unclaimed_count",
        "_unclaimed_first_nanos",
        "_unclaimed_last_nanos",
        "first_arrival_utc_nanos",
        "last_arrival_utc_nanos",
        "record_count",
    )

    def __init__(
        self,
        *,
        root: Path,
        contract: FirstPartyCaptureContractV1,
        session_id: UUID,
        day: date,
        clock_resolution_nanos: int | None = None,
    ) -> None:
        self._contract = contract
        self._session_id = session_id
        self._clock_resolution_nanos = (
            measure_clock_resolution_nanos()
            if clock_resolution_nanos is None
            else clock_resolution_nanos
        )
        self._directory = partition_directory(root, contract, day, session_id)
        if (self._directory / MANIFEST_FILE_NAME).exists():
            # A finalized partition is immutable. Reopening one to append would
            # silently invalidate its manifest, so it is refused.
            raise FirstPartyCaptureArchiveError("finalized_partition_must_not_be_reopened")
        self._directory.mkdir(parents=True, exist_ok=True)
        (self._directory / OPEN_MARKER_NAME).write_text(
            f"session={session_id}\n", encoding="utf-8"
        )
        (self._directory / SESSION_FILE_NAME).write_text(
            _canonical_json(
                {
                    "schema_version": SESSION_SCHEMA_VERSION,
                    "session_id": str(session_id),
                    "source_id": str(contract.source_id),
                    "contract_content_hash": contract.content_hash(),
                    "capture_semantic_version": contract.capture_semantic_version,
                    "endpoint": contract.endpoint,
                    "exchange": contract.originating_exchange,
                    "instrument": contract.instrument_scope,
                    "topics": list(contract.topics()),
                    "utc_day": day.isoformat(),
                    "clock_resolution_nanos": self._clock_resolution_nanos,
                    "clock_semantics": list(contract.clock_semantics),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self._records_handle = (self._directory / RECORDS_FILE_NAME).open("a", encoding="utf-8")
        self._lifecycle_handle = (self._directory / LIFECYCLE_FILE_NAME).open(
            "a", encoding="utf-8"
        )
        self._coverage: list[CaptureCoverageIntervalV1] = []
        self._gaps: list[CaptureGapV1] = []
        self._day = day
        self._finalized = False
        #: Nothing may be declared before this instant; ``None`` after an
        #: open-ended gap, which forbids any later declaration.
        self._frontier_nanos: int | None = 0
        self._unclaimed_count = 0
        self._unclaimed_first_nanos: int | None = None
        self._unclaimed_last_nanos: int | None = None
        self.record_count = 0
        self.first_arrival_utc_nanos: int | None = None
        self.last_arrival_utc_nanos: int | None = None

    @property
    def directory(self) -> Path:
        return self._directory

    def append_record(self, record: FirstPartyCaptureRecordV1) -> None:
        if self._finalized:
            raise FirstPartyCaptureArchiveError("cannot_append_to_a_finalized_partition")
        if not record.integrity_verified():
            raise FirstPartyCaptureArchiveError("refusing_to_write_a_record_that_fails_its_hash")
        if record.session_id != self._session_id:
            raise FirstPartyCaptureArchiveError("record_belongs_to_another_capture_session")
        if utc_day_of_nanos(record.arrival_utc_nanos) != self._day:
            raise FirstPartyCaptureArchiveError("record_arrival_is_outside_the_partition_utc_day")
        self._records_handle.write(record.to_json_line() + "\n")
        self._records_handle.flush()
        self.record_count += 1
        if self.first_arrival_utc_nanos is None:
            self.first_arrival_utc_nanos = record.arrival_utc_nanos
        self.last_arrival_utc_nanos = record.arrival_utc_nanos
        self._unclaimed_count += 1
        if self._unclaimed_first_nanos is None:
            self._unclaimed_first_nanos = record.arrival_utc_nanos
        self._unclaimed_last_nanos = record.arrival_utc_nanos

    def append_lifecycle(self, event: CaptureLifecycleEventV1) -> None:
        if self._finalized:
            raise FirstPartyCaptureArchiveError("cannot_append_to_a_finalized_partition")
        self._lifecycle_handle.write(event.to_json_line() + "\n")
        self._lifecycle_handle.flush()

    def _require_after_frontier(self, start_utc_nanos: int) -> None:
        if self._frontier_nanos is None:
            raise FirstPartyCaptureArchiveError("nothing_may_follow_an_open_ended_gap")
        if start_utc_nanos < self._frontier_nanos:
            raise FirstPartyCaptureArchiveError("declarations_must_be_ordered_and_disjoint")

    def declare_coverage(self, interval: CaptureCoverageIntervalV1) -> None:
        """Claim every record appended since the previous window for ``interval``."""
        if self._finalized:
            raise FirstPartyCaptureArchiveError("cannot_declare_on_a_finalized_partition")
        self._require_after_frontier(interval.start_utc_nanos)
        if interval.record_count != self._unclaimed_count:
            raise FirstPartyCaptureArchiveError("coverage_record_count_disagrees_with_records_written")
        if self._unclaimed_count and not (
            interval.contains(self._unclaimed_first_nanos)  # type: ignore[arg-type]
            and interval.contains(self._unclaimed_last_nanos)  # type: ignore[arg-type]
        ):
            raise FirstPartyCaptureArchiveError("claimed_record_falls_outside_its_coverage_window")
        self._coverage.append(interval)
        self._frontier_nanos = interval.end_utc_nanos
        self._unclaimed_count = 0
        self._unclaimed_first_nanos = None
        self._unclaimed_last_nanos = None

    def declare_gap(self, gap: CaptureGapV1) -> None:
        if self._finalized:
            raise FirstPartyCaptureArchiveError("cannot_declare_on_a_finalized_partition")
        if self._unclaimed_count:
            # A gap cannot open while records wait for a window: they would
            # sit inside the unobserved interval.
            raise FirstPartyCaptureArchiveError("gap_declared_over_unclaimed_records")
        self._require_after_frontier(gap.start_utc_nanos)
        self._gaps.append(gap)
        self._frontier_nanos = gap.end_utc_nanos

    def finalize(self) -> Path:
        """Write the manifest and drop the sentinel. Refuses to overwrite one."""
        if self._finalized:
            raise FirstPartyCaptureArchiveError("partition_already_finalized")
        manifest_path = self._directory / MANIFEST_FILE_NAME
        if manifest_path.exists():
            raise FirstPartyCaptureArchiveError("refusing_to_overwrite_an_existing_manifest")
        if self._unclaimed_count:
            raise FirstPartyCaptureArchiveError("partition_holds_records_no_coverage_window_claims")
        self._records_handle.close()
        self._lifecycle_handle.close()

        files = {
            name: sha256_file(self._directory / name)
            for name in (SESSION_FILE_NAME, RECORDS_FILE_NAME, LIFECYCLE_FILE_NAME)
            if (self._directory / name).exists()
        }
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "session_id": str(self._session_id),
            "source_id": str(self._contract.source_id),
            "contract_content_hash": self._contract.content_hash(),
            "utc_day": self._day.isoformat(),
            "record_count": self.record_count,
            "first_arrival_utc_nanos": self.first_arrival_utc_nanos,
            "last_arrival_utc_nanos": self.last_arrival_utc_nanos,
            "clock_resolution_nanos": self._clock_resolution_nanos,
            "coverage": [interval.to_payload() for interval in self._coverage],
            "gaps": [gap.to_payload() for gap in self._gaps],
            "files": files,
        }
        manifest["manifest_content_hash"] = _sha256_text(_canonical_json(manifest))
        manifest_path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
        (self._directory / OPEN_MARKER_NAME).unlink(missing_ok=True)
        self._finalized = True
        return manifest_path

    def close_without_finalizing(self) -> None:
        """Release handles and leave the partition honestly PARTIAL."""
        if not self._finalized:
            self._records_handle.close()
            self._lifecycle_handle.close()


@dataclass(frozen=True, slots=True)
class CapturePartitionV1:
    """A partition read back from disk, with its proven status."""

    directory: Path
    status: str
    session_id: UUID | None
    source_id: UUID | None
    record_count: int
    coverage: tuple[CaptureCoverageIntervalV1, ...]
    gaps: tuple[CaptureGapV1, ...]
    reasons: tuple[str, ...]


def _partial(directory: Path, reason: str) -> CapturePartitionV1:
    return CapturePartitionV1(
        directory=directory,
        status=PARTITION_STATUS_PARTIAL,
        session_id=None,
        source_id=None,
        record_count=0,
        coverage=(),
        gaps=(),
        reasons=(reason,),
    )


def _metadata_reasons(
    manifest: Mapping[str, Any],
    session: Mapping[str, Any],
    coverage: Sequence[CaptureCoverageIntervalV1],
    gaps: Sequence[CaptureGapV1],
    contract: FirstPartyCaptureContractV1,
) -> list[str]:
    """Internal consistency a manifest must have beyond its own hash."""
    reasons: list[str] = []
    for key in ("session_id", "source_id", "contract_content_hash", "utc_day"):
        if session.get(key) != manifest.get(key):
            reasons.append(f"session_and_manifest_disagree:{key}")
    if manifest.get("source_id") != str(contract.source_id):
        reasons.append("manifest_source_is_not_the_authorized_first_party_source")
    if manifest.get("contract_content_hash") != contract.content_hash():
        reasons.append("manifest_contract_hash_is_not_the_authorized_contract")

    for previous, following in pairwise(coverage):
        if following.start_utc_nanos < previous.end_utc_nanos:
            reasons.append("coverage_windows_overlap_or_are_unordered")
            break
    for gap in gaps:
        if any(gap.overlaps(interval) for interval in coverage):
            reasons.append("declared_gap_overlaps_proven_coverage")
            break

    record_count = int(manifest.get("record_count", -1))
    if sum(interval.record_count for interval in coverage) != record_count:
        reasons.append("coverage_record_counts_do_not_sum_to_the_manifest_count")
    first = manifest.get("first_arrival_utc_nanos")
    last = manifest.get("last_arrival_utc_nanos")
    if record_count == 0:
        if first is not None or last is not None:
            reasons.append("empty_partition_declares_arrival_bounds")
    elif first is None or last is None:
        reasons.append("non_empty_partition_lacks_arrival_bounds")
    elif not any(interval.contains(int(first)) for interval in coverage) or not any(
        interval.contains(int(last)) for interval in coverage
    ):
        reasons.append("arrival_bounds_fall_outside_declared_coverage")
    return reasons


def read_partition_status_v1(
    directory: Path, *, contract: FirstPartyCaptureContractV1 | None = None
) -> CapturePartitionV1:
    """Classify a partition without trusting it. Absence of proof is PARTIAL.

    COMPLETE means: a manifest exists, its own hash and every file checksum
    reproduce, no OPEN marker survives, and its metadata is internally
    consistent and belongs to the authorized first-party contract. It does not
    read records; :func:`verify_partition_v1` proves the record-level claims.
    """
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    manifest_path = directory / MANIFEST_FILE_NAME
    reasons: list[str] = []
    if not manifest_path.exists():
        # A crashed session ends here: records may be on disk, but nothing
        # proves where coverage ended, so nothing is claimed.
        return _partial(directory, "partition_has_no_manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        session = json.loads((directory / SESSION_FILE_NAME).read_text(encoding="utf-8"))
        coverage = tuple(
            CaptureCoverageIntervalV1.from_payload(item) for item in manifest.get("coverage", ())
        )
        gaps = tuple(CaptureGapV1.from_payload(item) for item in manifest.get("gaps", ()))
        session_id = UUID(manifest["session_id"])
        source_id = UUID(manifest["source_id"])
        record_count = int(manifest["record_count"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        return _partial(directory, f"partition_metadata_is_unreadable:{type(error).__name__}")

    stated = dict(manifest)
    declared_hash = stated.pop("manifest_content_hash", None)
    if declared_hash != _sha256_text(_canonical_json(stated)):
        reasons.append("manifest_content_hash_mismatch")
    if (directory / OPEN_MARKER_NAME).exists():
        reasons.append("finalized_partition_still_carries_an_open_marker")
    files = manifest.get("files", {})
    for required in (SESSION_FILE_NAME, RECORDS_FILE_NAME, LIFECYCLE_FILE_NAME):
        if required not in files:
            reasons.append(f"manifest_does_not_cover_file:{required}")
    for name, digest in files.items():
        path = directory / name
        if not path.exists():
            reasons.append(f"manifest_file_missing:{name}")
        elif sha256_file(path) != digest:
            reasons.append(f"manifest_file_checksum_mismatch:{name}")
    reasons.extend(_metadata_reasons(manifest, session, coverage, gaps, authorized))
    return CapturePartitionV1(
        directory=directory,
        status=PARTITION_STATUS_COMPLETE if not reasons else PARTITION_STATUS_PARTIAL,
        session_id=session_id,
        source_id=source_id,
        record_count=record_count,
        coverage=coverage,
        gaps=gaps,
        reasons=tuple(reasons),
    )


def replay_partition_v1(
    directory: Path,
    *,
    require_complete: bool = True,
    contract: FirstPartyCaptureContractV1 | None = None,
) -> Iterator[FirstPartyCaptureRecordV1]:
    """Re-read a partition, re-proving every hash, ordering and coverage claim.

    Yields the identical record sequence the recorder wrote. Each record must
    reproduce its content hash, be re-derivable from its verbatim payload under
    the authorized contract, belong to the partition's session and UTC day,
    keep sequence and monotonic order, and fall inside a declared coverage
    window. On exhaustion a COMPLETE partition must also reconcile: the replayed
    count equals the manifest count and each window's declared count. Any
    failure raises rather than skipping, because a replay that quietly drops
    evidence is worse than one that stops -- so a consumer must exhaust the
    iterator (or call :func:`verify_partition_v1`) before relying on it.
    """
    authorized = first_party_bybit_capture_contract_v1() if contract is None else contract
    partition = read_partition_status_v1(directory, contract=authorized)
    complete = partition.status == PARTITION_STATUS_COMPLETE
    if require_complete and not complete:
        raise FirstPartyCaptureArchiveError(
            "refusing_to_replay_an_unproven_partition:" + ",".join(partition.reasons)
        )

    session_day: date | None = None
    try:
        session_meta = json.loads((directory / SESSION_FILE_NAME).read_text(encoding="utf-8"))
        session_day = date.fromisoformat(session_meta["utc_day"])
    except (OSError, ValueError, KeyError):
        if complete:
            raise FirstPartyCaptureArchiveError("partition_session_metadata_is_unreadable") from None

    records_path = directory / RECORDS_FILE_NAME
    starts = [interval.start_utc_nanos for interval in partition.coverage]
    per_window = [0] * len(partition.coverage)
    count = 0
    first_arrival: int | None = None
    last_arrival: int | None = None
    previous_monotonic: int | None = None
    previous_sequence: int | None = None
    if records_path.exists():
        with records_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                record = FirstPartyCaptureRecordV1.from_json_line(text)
                _require_replayed_record_is_authorized(record, authorized)
                if partition.session_id is not None and record.session_id != partition.session_id:
                    raise FirstPartyCaptureArchiveError("replayed_record_belongs_to_another_session")
                if session_day is not None and utc_day_of_nanos(record.arrival_utc_nanos) != session_day:
                    raise FirstPartyCaptureArchiveError("replayed_record_is_outside_the_partition_day")
                if (
                    previous_monotonic is not None
                    and record.arrival_monotonic_nanos < previous_monotonic
                ):
                    raise FirstPartyCaptureArchiveError(
                        "replayed_records_are_not_monotonically_ordered"
                    )
                if previous_sequence is not None and record.sequence != previous_sequence + 1:
                    raise FirstPartyCaptureArchiveError("replayed_record_sequence_is_not_contiguous")
                if complete:
                    index = bisect_right(starts, record.arrival_utc_nanos) - 1
                    if index < 0 or not partition.coverage[index].contains(record.arrival_utc_nanos):
                        raise FirstPartyCaptureArchiveError(
                            "replayed_record_is_outside_every_declared_coverage_window"
                        )
                    per_window[index] += 1
                previous_monotonic = record.arrival_monotonic_nanos
                previous_sequence = record.sequence
                count += 1
                if first_arrival is None:
                    first_arrival = record.arrival_utc_nanos
                last_arrival = record.arrival_utc_nanos
                yield record

    if complete:
        if count != partition.record_count:
            raise FirstPartyCaptureArchiveError("replayed_record_count_disagrees_with_manifest")
        if per_window != [interval.record_count for interval in partition.coverage]:
            raise FirstPartyCaptureArchiveError(
                "replayed_records_per_window_disagree_with_declared_coverage"
            )
        manifest = json.loads((directory / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
        if (first_arrival, last_arrival) != (
            manifest.get("first_arrival_utc_nanos"),
            manifest.get("last_arrival_utc_nanos"),
        ):
            raise FirstPartyCaptureArchiveError("replayed_arrival_bounds_disagree_with_manifest")


def _require_replayed_record_is_authorized(
    record: FirstPartyCaptureRecordV1, contract: FirstPartyCaptureContractV1
) -> None:
    """A stored record must be exactly what the contract would admit today."""
    rebuilt = build_capture_record_v1(
        contract=contract,
        session_id=record.session_id,
        sequence=record.sequence,
        clock=CaptureClockReadingV1(
            arrival_utc_nanos=record.arrival_utc_nanos,
            arrival_monotonic_nanos=record.arrival_monotonic_nanos,
        ),
        payload_text=record.payload_text,
    )
    if rebuilt.content_hash != record.content_hash:
        raise FirstPartyCaptureArchiveError("replayed_record_is_not_reproducible_under_the_contract")


@dataclass(frozen=True, slots=True)
class CapturePartitionVerificationV1:
    """The outcome of fully replaying one COMPLETE partition."""

    directory: Path
    session_id: UUID
    record_count: int
    records_by_channel: Mapping[str, int]
    coverage: tuple[CaptureCoverageIntervalV1, ...]
    gaps: tuple[CaptureGapV1, ...]


def verify_partition_v1(
    directory: Path, *, contract: FirstPartyCaptureContractV1 | None = None
) -> CapturePartitionVerificationV1:
    """Exhaust a replay so every record-level claim is proven, or raise."""
    by_channel: dict[str, int] = {}
    count = 0
    for record in replay_partition_v1(directory, contract=contract):
        by_channel[record.channel] = by_channel.get(record.channel, 0) + 1
        count += 1
    partition = read_partition_status_v1(directory, contract=contract)
    if partition.session_id is None:  # pragma: no cover - replay already required COMPLETE
        raise FirstPartyCaptureArchiveError("verified_partition_has_no_session")
    return CapturePartitionVerificationV1(
        directory=directory,
        session_id=partition.session_id,
        record_count=count,
        records_by_channel=dict(sorted(by_channel.items())),
        coverage=partition.coverage,
        gaps=partition.gaps,
    )


def read_lifecycle_v1(directory: Path) -> tuple[CaptureLifecycleEventV1, ...]:
    path = directory / LIFECYCLE_FILE_NAME
    if not path.exists():
        return ()
    return tuple(
        CaptureLifecycleEventV1.from_json_line(line.strip())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def find_partitions_v1(root: Path) -> tuple[Path, ...]:
    """Every session partition under an archive root, in stable sorted order."""
    base = root / ARCHIVE_LAYOUT_VERSION
    if not base.exists():
        return ()
    return tuple(sorted(path.parent for path in base.rglob(SESSION_FILE_NAME)))


@dataclass(frozen=True, slots=True)
class ProvenWindowV1:
    """One declared coverage window, tagged with the session that proved it."""

    session_id: UUID
    interval: CaptureCoverageIntervalV1


@dataclass(frozen=True, slots=True)
class CaptureArchiveAvailabilityV1:
    """What an archive positively proves, and every hole between those proofs.

    ``windows`` come only from COMPLETE partitions. ``gaps`` are the holes
    between consecutive proven windows, derived rather than declared. Before
    the first window and after the last one nothing is proven either; those
    unbounded stretches are simply not listed. ``excluded`` names every
    partition that contributed nothing -- a crashed session's PARTIAL
    partition among them -- with the reasons.
    """

    windows: tuple[ProvenWindowV1, ...]
    gaps: tuple[CaptureGapV1, ...]
    excluded: tuple[tuple[Path, tuple[str, ...]], ...]


def derive_capture_gaps_v1(windows: Sequence[ProvenWindowV1]) -> tuple[CaptureGapV1, ...]:
    """The holes between consecutive proven windows. Abutting windows yield none.

    Coverage is positive evidence, so anything strictly between two windows is a
    gap by construction. Proximity is not continuity: two windows a nanosecond
    apart still produce a gap. Across sessions the gap is
    :attr:`CaptureGapKindV1.SESSION_BOUNDARY` whatever happened -- a clean stop,
    a crash, a reboot or a sleeping host are indistinguishable from the archive
    and none of them is claimed. Within one session the preceding window's end
    proof names the cause. Overlapping windows (two recorders at once) merge.
    """
    ordered = sorted(windows, key=lambda window: window.interval.start_utc_nanos)
    gaps: list[CaptureGapV1] = []
    reach: ProvenWindowV1 | None = None
    for window in ordered:
        if reach is not None and window.interval.start_utc_nanos > reach.interval.end_utc_nanos:
            if window.session_id != reach.session_id:
                kind = CaptureGapKindV1.SESSION_BOUNDARY
            elif reach.interval.end_proof == END_PROOF_UTC_DAY_ROLLOVER:
                kind = CaptureGapKindV1.PARTITION_ROLLOVER
            else:
                kind = GAP_KIND_FOR_END_PROOF_V1.get(
                    reach.interval.end_proof, CaptureGapKindV1.SESSION_BOUNDARY
                )
            gaps.append(
                CaptureGapV1(
                    start_utc_nanos=reach.interval.end_utc_nanos,
                    end_utc_nanos=window.interval.start_utc_nanos,
                    kind=kind.value,
                    detail=f"after:{reach.interval.end_proof}",
                )
            )
        if reach is None or window.interval.end_utc_nanos > reach.interval.end_utc_nanos:
            reach = window
    return tuple(gaps)


def derive_archive_availability_v1(
    root: Path, *, contract: FirstPartyCaptureContractV1 | None = None
) -> CaptureArchiveAvailabilityV1:
    """Proven coverage across every session under an archive root.

    This is how a consumer learns that the stretch between one session's last
    proven observation and the next session's first is unavailable: it is not
    inside any COMPLETE partition's declared window. A PARTIAL partition -- a
    hard crash, a killed process -- contributes no window, so it can never make
    time look covered. Metadata-level only; a dataset built on these windows
    must still :func:`verify_partition_v1` each partition it uses.
    """
    windows: list[ProvenWindowV1] = []
    excluded: list[tuple[Path, tuple[str, ...]]] = []
    for directory in find_partitions_v1(root):
        partition = read_partition_status_v1(directory, contract=contract)
        if partition.status != PARTITION_STATUS_COMPLETE or partition.session_id is None:
            excluded.append((directory, partition.reasons))
            continue
        windows.extend(
            ProvenWindowV1(session_id=partition.session_id, interval=interval)
            for interval in partition.coverage
        )
    windows.sort(key=lambda window: window.interval.start_utc_nanos)
    return CaptureArchiveAvailabilityV1(
        windows=tuple(windows),
        gaps=derive_capture_gaps_v1(windows),
        excluded=tuple(excluded),
    )


def new_session_id_v1() -> UUID:
    """A fresh session identity. A restart is a new session, never a continuation."""
    return uuid4()


def default_archive_root() -> Path:
    """Where raw capture lives by default: outside the working tree, never in Git.

    Exchange traffic is high-cardinality vendor data. It does not belong in
    source control and it does not belong in the low-cardinality PostgreSQL
    evidence tables, so no migration is introduced to hold it.
    """
    return Path.home() / ".trade_platform" / "capture"


__all__ = [
    "ARCHIVE_LAYOUT_VERSION",
    "CLOCK_DIVERGENCE_TOLERANCE_NANOS",
    "END_PROOFS_V1",
    "END_PROOF_CLOCK_DISCONTINUITY",
    "END_PROOF_CONNECTION_LOST",
    "END_PROOF_CONTRACT_VIOLATION",
    "END_PROOF_OPERATOR_BOUNDED_STOP",
    "END_PROOF_OPERATOR_INTERRUPT",
    "END_PROOF_PEER_CLOSED",
    "END_PROOF_RECORDER_FAILURE",
    "END_PROOF_UTC_DAY_ROLLOVER",
    "GAP_KIND_FOR_END_PROOF_V1",
    "MANIFEST_FILE_NAME",
    "OPEN_MARKER_NAME",
    "PARTITION_STATUS_COMPLETE",
    "PARTITION_STATUS_PARTIAL",
    "CaptureArchiveAvailabilityV1",
    "CaptureClockMonitorV1",
    "CaptureClockReadingV1",
    "CaptureCoverageIntervalV1",
    "CaptureGapKindV1",
    "CaptureGapV1",
    "CaptureLifecycleEventV1",
    "CaptureLifecycleKindV1",
    "CapturePartitionV1",
    "CapturePartitionVerificationV1",
    "CapturePartitionWriterV1",
    "ClockVerdictV1",
    "FirstPartyCaptureArchiveError",
    "FirstPartyCaptureRecordV1",
    "ProvenWindowV1",
    "build_capture_record_v1",
    "default_archive_root",
    "derive_archive_availability_v1",
    "derive_capture_gaps_v1",
    "find_partitions_v1",
    "first_party_bybit_capture_contract_v1",
    "knowledge_bound_utc_nanos",
    "measure_clock_resolution_nanos",
    "nanos_to_datetime",
    "new_session_id_v1",
    "partition_directory",
    "read_lifecycle_v1",
    "read_partition_status_v1",
    "replay_partition_v1",
    "sha256_file",
    "utc_day_of_nanos",
    "verify_partition_v1",
]
