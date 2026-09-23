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
A session is one process's one connection lifetime, identified by a UUID, and a
monotonic counter is never compared across sessions. Coverage is a set of
declared half-open ``[start, end)`` arrival windows that begin only when the
exchange has acknowledged the subscriptions -- not when the socket opened --
and end at a clean close, a discontinuity, or the last verified record of a
process that died. That last case is recorded as
:data:`END_PROOF_LAST_OBSERVED_RECORD` rather than presented as a clean stop.

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
file checksum and every record content hash, re-checks monotonic ordering, and
yields the identical record sequence. A record whose hash does not reproduce,
or a file whose checksum does not match the manifest, fails closed rather than
being skipped.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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

END_PROOF_CLEAN_CLOSE: Final = "CLEAN_CLOSE"
END_PROOF_LAST_OBSERVED_RECORD: Final = "LAST_OBSERVED_RECORD"
END_PROOF_CLOCK_DISCONTINUITY: Final = "CLOCK_DISCONTINUITY"
END_PROOF_CONNECTION_LOST: Final = "CONNECTION_LOST"

PARTITION_STATUS_COMPLETE: Final = "COMPLETE"
PARTITION_STATUS_PARTIAL: Final = "PARTIAL"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.first_party_capture_archive_v1")


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
    SESSION_CLOSED = "SESSION_CLOSED"


class CaptureGapKindV1(StrEnum):
    """Why an interval is not covered. Never inferred from quiet traffic."""

    CONNECTION_LOSS = "CONNECTION_LOSS"
    CLOCK_DISCONTINUITY = "CLOCK_DISCONTINUITY"
    SESSION_BOUNDARY = "SESSION_BOUNDARY"
    RECORDER_NOT_RUNNING = "RECORDER_NOT_RUNNING"


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
    """UTC datetime for an integer-nanosecond wall clock, truncated to microseconds.

    Offered for interoperability only. The integer nanoseconds stay the
    authoritative value: a ``datetime`` cannot hold them, and truncation moves
    an instant *earlier*, which is the unsafe direction.
    """
    return datetime.fromtimestamp(nanos // 1_000_000_000, tz=UTC).replace(
        microsecond=(nanos % 1_000_000_000) // 1_000
    )


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
        raw = json.loads(line)
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

    message_type = parsed.get("type")
    if message_type is not None and not isinstance(message_type, str):
        raise FirstPartyCaptureArchiveError("captured_message_type_is_malformed")
    if message_type is not None and message_type not in {
        item.value for item in BybitMessageTypeV1
    }:
        raise FirstPartyCaptureArchiveError("captured_message_type_is_not_recognized")

    exchange_ts = parsed.get("ts")
    if exchange_ts is not None and not isinstance(exchange_ts, int):
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
    """Where the exchange sends a symbol in the body, it must match the topic."""
    data = parsed.get("data")
    if channel == BybitPublicChannelV1.TICKERS.value and isinstance(data, dict):
        symbol = data.get("symbol")
        if isinstance(symbol, str) and symbol != expected_symbol:
            raise FirstPartyCaptureArchiveError("captured_payload_symbol_disagrees_with_topic")
    elif channel == BybitPublicChannelV1.PUBLIC_TRADE.value and isinstance(data, list):
        for trade in data:
            if isinstance(trade, dict):
                symbol = trade.get("s")
                if isinstance(symbol, str) and symbol != expected_symbol:
                    raise FirstPartyCaptureArchiveError(
                        "captured_payload_symbol_disagrees_with_topic"
                    )


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

    def to_json_line(self) -> str:
        return _canonical_json(
            {
                "kind": self.kind,
                "arrival_utc_nanos": self.arrival_utc_nanos,
                "arrival_monotonic_nanos": self.arrival_monotonic_nanos,
                "detail": self.detail,
            }
        )

    @classmethod
    def from_json_line(cls, line: str) -> CaptureLifecycleEventV1:
        raw = json.loads(line)
        return cls(
            kind=raw["kind"],
            arrival_utc_nanos=int(raw["arrival_utc_nanos"]),
            arrival_monotonic_nanos=int(raw["arrival_monotonic_nanos"]),
            detail=raw.get("detail"),
        )


@dataclass(frozen=True, slots=True)
class CaptureCoverageIntervalV1:
    """A half-open ``[start, end)`` arrival window the recorder can positively prove."""

    start_utc_nanos: int
    end_utc_nanos: int
    end_proof: str
    record_count: int

    def __post_init__(self) -> None:
        if self.end_utc_nanos < self.start_utc_nanos:
            raise FirstPartyCaptureArchiveError("coverage_interval_ends_before_it_starts")

    def to_payload(self) -> dict[str, Any]:
        return {
            "start_utc_nanos": self.start_utc_nanos,
            "end_utc_nanos": self.end_utc_nanos,
            "end_proof": self.end_proof,
            "record_count": self.record_count,
        }


@dataclass(frozen=True, slots=True)
class CaptureGapV1:
    """An interval the recorder explicitly could not observe. Never bridged."""

    start_utc_nanos: int
    end_utc_nanos: int
    kind: str
    detail: str | None = None

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
    """

    __slots__ = (
        "_clock_resolution_nanos",
        "_contract",
        "_coverage",
        "_directory",
        "_finalized",
        "_gaps",
        "_lifecycle_handle",
        "_records_handle",
        "_session_id",
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
        self._finalized = False
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
        self._records_handle.write(record.to_json_line() + "\n")
        self._records_handle.flush()
        self.record_count += 1
        if self.first_arrival_utc_nanos is None:
            self.first_arrival_utc_nanos = record.arrival_utc_nanos
        self.last_arrival_utc_nanos = record.arrival_utc_nanos

    def append_lifecycle(self, event: CaptureLifecycleEventV1) -> None:
        if self._finalized:
            raise FirstPartyCaptureArchiveError("cannot_append_to_a_finalized_partition")
        self._lifecycle_handle.write(event.to_json_line() + "\n")
        self._lifecycle_handle.flush()

    def declare_coverage(self, interval: CaptureCoverageIntervalV1) -> None:
        self._coverage.append(interval)

    def declare_gap(self, gap: CaptureGapV1) -> None:
        self._gaps.append(gap)

    def finalize(self) -> Path:
        """Write the manifest and drop the sentinel. Refuses to overwrite one."""
        if self._finalized:
            raise FirstPartyCaptureArchiveError("partition_already_finalized")
        manifest_path = self._directory / MANIFEST_FILE_NAME
        if manifest_path.exists():
            raise FirstPartyCaptureArchiveError("refusing_to_overwrite_an_existing_manifest")
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


def read_partition_status_v1(directory: Path) -> CapturePartitionV1:
    """Classify a partition without trusting it. Absence of proof is PARTIAL."""
    manifest_path = directory / MANIFEST_FILE_NAME
    reasons: list[str] = []
    if not manifest_path.exists():
        return CapturePartitionV1(
            directory=directory,
            status=PARTITION_STATUS_PARTIAL,
            session_id=None,
            source_id=None,
            record_count=0,
            coverage=(),
            gaps=(),
            reasons=("partition_has_no_manifest",),
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stated = dict(manifest)
    declared_hash = stated.pop("manifest_content_hash", None)
    if declared_hash != _sha256_text(_canonical_json(stated)):
        reasons.append("manifest_content_hash_mismatch")
    if (directory / OPEN_MARKER_NAME).exists():
        reasons.append("finalized_partition_still_carries_an_open_marker")
    for name, digest in manifest.get("files", {}).items():
        path = directory / name
        if not path.exists():
            reasons.append(f"manifest_file_missing:{name}")
        elif sha256_file(path) != digest:
            reasons.append(f"manifest_file_checksum_mismatch:{name}")
    return CapturePartitionV1(
        directory=directory,
        status=PARTITION_STATUS_COMPLETE if not reasons else PARTITION_STATUS_PARTIAL,
        session_id=UUID(manifest["session_id"]),
        source_id=UUID(manifest["source_id"]),
        record_count=int(manifest["record_count"]),
        coverage=tuple(
            CaptureCoverageIntervalV1(
                start_utc_nanos=int(item["start_utc_nanos"]),
                end_utc_nanos=int(item["end_utc_nanos"]),
                end_proof=item["end_proof"],
                record_count=int(item["record_count"]),
            )
            for item in manifest.get("coverage", ())
        ),
        gaps=tuple(
            CaptureGapV1(
                start_utc_nanos=int(item["start_utc_nanos"]),
                end_utc_nanos=int(item["end_utc_nanos"]),
                kind=item["kind"],
                detail=item.get("detail"),
            )
            for item in manifest.get("gaps", ())
        ),
        reasons=tuple(reasons),
    )


def replay_partition_v1(
    directory: Path, *, require_complete: bool = True
) -> Iterator[FirstPartyCaptureRecordV1]:
    """Re-read a partition, re-proving every hash and the monotonic ordering.

    Yields the identical record sequence the recorder wrote. A bad checksum, a
    record whose content hash does not reproduce, a foreign session or an
    out-of-order monotonic counter raises rather than being skipped, because a
    replay that quietly drops evidence is worse than one that stops.
    """
    partition = read_partition_status_v1(directory)
    if require_complete and partition.status != PARTITION_STATUS_COMPLETE:
        raise FirstPartyCaptureArchiveError(
            "refusing_to_replay_an_unproven_partition:" + ",".join(partition.reasons)
        )

    records_path = directory / RECORDS_FILE_NAME
    if not records_path.exists():
        return

    previous_monotonic: int | None = None
    previous_sequence: int | None = None
    with records_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            record = FirstPartyCaptureRecordV1.from_json_line(text)
            if partition.session_id is not None and record.session_id != partition.session_id:
                raise FirstPartyCaptureArchiveError("replayed_record_belongs_to_another_session")
            if previous_monotonic is not None and record.arrival_monotonic_nanos < previous_monotonic:
                raise FirstPartyCaptureArchiveError("replayed_records_are_not_monotonically_ordered")
            if previous_sequence is not None and record.sequence != previous_sequence + 1:
                raise FirstPartyCaptureArchiveError("replayed_record_sequence_is_not_contiguous")
            previous_monotonic = record.arrival_monotonic_nanos
            previous_sequence = record.sequence
            yield record


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


def derive_session_gaps_v1(
    coverage: Sequence[CaptureCoverageIntervalV1],
) -> tuple[CaptureGapV1, ...]:
    """The holes between consecutive coverage windows. Abutting windows yield none.

    Coverage is positive evidence, so anything strictly between two windows is a
    gap by construction. Proximity is not continuity: two windows a microsecond
    apart still produce a gap, because nothing proves what happened between them.
    """
    ordered = sorted(coverage, key=lambda interval: interval.start_utc_nanos)
    gaps: list[CaptureGapV1] = []
    for previous, following in pairwise(ordered):
        if following.start_utc_nanos > previous.end_utc_nanos:
            gaps.append(
                CaptureGapV1(
                    start_utc_nanos=previous.end_utc_nanos,
                    end_utc_nanos=following.start_utc_nanos,
                    kind=CaptureGapKindV1.SESSION_BOUNDARY.value,
                )
            )
    return tuple(gaps)


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
    "END_PROOF_CLEAN_CLOSE",
    "END_PROOF_CLOCK_DISCONTINUITY",
    "END_PROOF_CONNECTION_LOST",
    "END_PROOF_LAST_OBSERVED_RECORD",
    "MANIFEST_FILE_NAME",
    "OPEN_MARKER_NAME",
    "PARTITION_STATUS_COMPLETE",
    "PARTITION_STATUS_PARTIAL",
    "CaptureClockMonitorV1",
    "CaptureClockReadingV1",
    "CaptureCoverageIntervalV1",
    "CaptureGapKindV1",
    "CaptureGapV1",
    "CaptureLifecycleEventV1",
    "CaptureLifecycleKindV1",
    "CapturePartitionV1",
    "CapturePartitionWriterV1",
    "ClockVerdictV1",
    "FirstPartyCaptureArchiveError",
    "FirstPartyCaptureRecordV1",
    "build_capture_record_v1",
    "default_archive_root",
    "derive_session_gaps_v1",
    "find_partitions_v1",
    "first_party_bybit_capture_contract_v1",
    "measure_clock_resolution_nanos",
    "nanos_to_datetime",
    "new_session_id_v1",
    "partition_directory",
    "read_lifecycle_v1",
    "read_partition_status_v1",
    "replay_partition_v1",
    "sha256_file",
]
