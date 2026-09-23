"""Phase 3D.9S.2A -- immutable raw-capture evidence for Tardis historical Bybit WebSocket data.

``RESEARCH_ENGINEERING_EVIDENCE_ONLY``. This module is a zero-cost engineering
proof that a *captured* feed can carry the decision-time evidence the canonical
Bybit V5 REST path cannot (3D.9S.1's ``UNPROVEN_DISTINCT_FEATURE_DECISION_TIMES``
blocker). It is deliberately a **separate** narrow module: it does not touch,
extend or re-interpret :mod:`trade_platform.bybit_crypto_provider`, the sealed
150-day REST dataset, or :mod:`trade_platform.real_market_data_provenance_v1`.
Nothing here is ``REAL_DATA_RESEARCH_EVIDENCE`` and nothing here authorizes
Tardis as a canonical source -- that decision belongs to Phase 3D.9S.2B.

**A captured message is evidence of exactly one observation, at exactly one
arrival instant.** The Tardis raw data feed emits one line per recorded
WebSocket message, ``<recorder arrival timestamp> <exchange-native JSON>``. Both
halves are preserved verbatim: the arrival timestamp is the *measured* instant
at which the evidence became knowable, and the exchange-native body is the
untouched vendor payload. Neither is normalized, rounded or reconstructed.

**The recorder clock is 100-nanosecond precision; ``datetime`` is not.** Python
cannot hold the seventh fractional digit, so truncating it would silently
destroy ordering evidence. Every record therefore carries three views of the
same single source value -- the exact captured text
(:attr:`TardisRawCaptureRecordV1.local_timestamp_text`, the only form that is
hashed), an exact integer nanosecond count used for all ordering and
monotonicity proofs, and a truncated :class:`datetime` offered only for
interoperability. No timestamp is ever invented, widened or re-derived.

Four refusals define acceptance.

**A generated record is not a capture.** Tardis marks synthesized rows with
``"generated": true``. Such a row is a vendor reconstruction, not something a
recorder observed arriving, so it is rejected outright rather than downgraded.

**A record without a usable arrival timestamp has no decision time**, which is
the entire reason this path exists, so it is rejected rather than back-filled
from the exchange timestamp.

**Identity is proven, never assumed.** ``topic`` must name the expected channel
and symbol, and the payload ``symbol`` (where the exchange sends one) must agree
with it. A mismatch is malformed identity, not a relabelling opportunity.

**Arrival order is monotonic within one replay stream.** A regression means the
stream is not the single ordered recording it claims to be, so
:func:`parse_tardis_capture_stream` fails closed instead of sorting the problem
away.

**Coverage is a declared window, not the span between the first and last
message that happened to arrive.** A quiet channel emits nothing for seconds at
a time; reading coverage off the outermost records would silently shrink a
captured window down to its traffic and invent gaps out of idle time. Every
stream therefore declares the half-open arrival window
``[declared_start, declared_end)`` it was retrieved for, and every record must
fall inside it.

**Coverage is positive, and gaps are never bridged.** A
:class:`TardisCaptureCoverageV1` holds only the windows actually captured. An
instant outside every window is ``UNAVAILABLE`` -- downstream research fails
closed rather than interpolating across the hole. Two windows that abut exactly
are one continuous recording and produce no gap; anything else does.
:func:`join_contiguous_streams` is the only supported way to treat several
retrieved windows as one stream, and it refuses the moment they stop abutting,
so no state machine can walk across an outage by accident.

Bybit's ``cs`` cross-sequence is dense enough to *disprove* continuity (a
regression, or an advance across a hole, proves messages exist that this capture
does not hold) but not dense enough per channel to *prove* it, so this module
uses it only in the direction it genuinely supports and says so in
:class:`TardisCaptureGapV1`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

#: Bumping this invalidates every downstream content hash on purpose: a parser
#: semantic change means the same bytes may now mean something different.
TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION: Final = "1.0.0"

#: Tardis' own exchange identifier for Bybit USDT-margined linear contracts.
TARDIS_EXCHANGE_ID_BYBIT: Final = "bybit"

#: The venue that actually produced the messages, kept distinct from the
#: recorder's identifier for it so provenance never collapses the two.
ORIGINATING_EXCHANGE_BYBIT: Final = "BYBIT"

CAPTURE_LIFECYCLE_V1: Final = "RESEARCH_ENGINEERING_EVIDENCE_ONLY"

_NANOS_PER_SECOND: Final = 1_000_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.tardis_capture_evidence_v1")


class TardisCaptureEvidenceError(ValueError):
    """Raised for any malformed, generated, unidentifiable or out-of-order record."""


class TardisChannelV1(StrEnum):
    """The two Bybit V5 public channels this pilot reconstructs."""

    TICKERS = "tickers"
    PUBLIC_TRADE = "publicTrade"


class TardisMessageTypeV1(StrEnum):
    """Bybit's own ``type`` discriminator, preserved rather than reinterpreted."""

    SNAPSHOT = "snapshot"
    DELTA = "delta"


class TardisCaptureGapKindV1(StrEnum):
    """Why a hole between two captured intervals is evidence, not an inference."""

    #: Adjacent declared intervals leave a wall-clock hole. Nothing was captured
    #: there, so nothing may be asserted about it.
    UNCAPTURED_INTERVAL = "UNCAPTURED_INTERVAL"

    #: The later interval's first cross-sequence is ahead of the earlier
    #: interval's last one: the exchange demonstrably produced messages that this
    #: capture does not hold. Positive proof of loss, not of continuity.
    SEQUENCE_ADVANCED_ACROSS_HOLE = "SEQUENCE_ADVANCED_ACROSS_HOLE"

    #: The later interval's first cross-sequence is at or behind the earlier
    #: interval's last one. The stream is not a single ordered recording.
    SEQUENCE_REGRESSION = "SEQUENCE_REGRESSION"


def canonical_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_recorder_timestamp_nanos(text: str) -> int:
    """Parse a Tardis recorder arrival timestamp to exact integer nanoseconds.

    Accepts the 100-nanosecond form the raw feed actually emits
    (``2026-05-01T00:00:00.0301131Z``) as well as any other fractional width, and
    refuses anything that is not an explicit UTC instant. The fraction is read as
    an exact integer rather than a float so no precision is lost on the way in.
    """
    raw = text.strip()
    if not raw.endswith("Z"):
        raise TardisCaptureEvidenceError("tardis_local_timestamp_must_be_utc")
    body = raw[:-1]
    fraction_digits = ""
    if "." in body:
        body, fraction_digits = body.split(".", 1)
    if not fraction_digits.isdigit() and fraction_digits:
        raise TardisCaptureEvidenceError("tardis_local_timestamp_fraction_malformed")
    try:
        whole = datetime.strptime(body, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise TardisCaptureEvidenceError("tardis_local_timestamp_malformed") from exc
    nanos_of_second = int((fraction_digits + "000000000")[:9]) if fraction_digits else 0
    return int((whole - _EPOCH).total_seconds()) * _NANOS_PER_SECOND + nanos_of_second


def nanos_to_datetime(nanos: int) -> datetime:
    """Truncating view of an exact nanosecond instant. Never used for ordering."""
    return _EPOCH + timedelta(microseconds=nanos // 1000)


def _exchange_timestamp(raw_millis: object) -> datetime | None:
    if raw_millis is None:
        return None
    if isinstance(raw_millis, bool) or not isinstance(raw_millis, int):
        raise TardisCaptureEvidenceError("tardis_exchange_timestamp_malformed")
    if raw_millis <= 0:
        raise TardisCaptureEvidenceError("tardis_exchange_timestamp_malformed")
    return _EPOCH + timedelta(milliseconds=raw_millis)


@dataclass(frozen=True, slots=True)
class TardisRawCaptureRecordV1:
    """One immutable captured WebSocket message with full audit identity.

    ``raw_message`` is the exchange-native JSON text exactly as recorded. It is
    what the content hash binds, so an accepted record can always be audited back
    to the precise bytes that were observed.
    """

    tardis_exchange_id: str
    originating_exchange: str
    channel: TardisChannelV1
    symbol: str
    message_type: TardisMessageTypeV1
    raw_message: str
    exchange_timestamp: datetime | None
    local_timestamp_text: str
    local_timestamp_nanos: int
    source_date: date
    cross_sequence: int | None
    parser_semantic_version: str
    content_hash: str

    @property
    def local_timestamp(self) -> datetime:
        """Microsecond-truncated arrival instant, for interoperability only."""
        return nanos_to_datetime(self.local_timestamp_nanos)

    @property
    def record_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"tardis-raw-capture-record-v1:{self.content_hash}")

    @property
    def payload(self) -> Mapping[str, Any]:
        """The parsed exchange-native body. Re-parsed from the hashed raw text."""
        parsed: Mapping[str, Any] = json.loads(self.raw_message)
        return parsed


def _record_content_hash(
    *,
    tardis_exchange_id: str,
    originating_exchange: str,
    channel: TardisChannelV1,
    symbol: str,
    message_type: TardisMessageTypeV1,
    raw_message: str,
    exchange_timestamp: datetime | None,
    local_timestamp_text: str,
    source_date: date,
    cross_sequence: int | None,
) -> str:
    return canonical_hash(
        {
            "parser_semantic_version": TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
            "tardis_exchange_id": tardis_exchange_id,
            "originating_exchange": originating_exchange,
            "channel": channel.value,
            "symbol": symbol,
            "message_type": message_type.value,
            # The exact vendor bytes, hashed as text so no re-serialization can
            # change what "the message" means.
            "raw_message_sha256": hashlib.sha256(raw_message.encode()).hexdigest(),
            "exchange_timestamp": None if exchange_timestamp is None else exchange_timestamp.isoformat(),
            "local_timestamp_text": local_timestamp_text,
            "source_date": source_date.isoformat(),
            "cross_sequence": cross_sequence,
        }
    )


def parse_tardis_capture_line(
    line: str,
    *,
    channel: TardisChannelV1,
    symbol: str,
    source_date: date,
    tardis_exchange_id: str = TARDIS_EXCHANGE_ID_BYBIT,
) -> TardisRawCaptureRecordV1:
    """Parse one ``<arrival timestamp> <exchange-native JSON>`` capture line.

    Fails closed on a generated record, a missing or malformed arrival
    timestamp, malformed JSON, or a topic/symbol identity that does not match
    what the caller declared it was reading.
    """
    if not symbol.strip():
        raise TardisCaptureEvidenceError("tardis_symbol_required")
    stripped = line.strip()
    if not stripped:
        raise TardisCaptureEvidenceError("tardis_capture_line_empty")
    parts = stripped.split(" ", 1)
    if len(parts) != 2:
        raise TardisCaptureEvidenceError("tardis_capture_line_missing_arrival_timestamp")
    local_timestamp_text, raw_message = parts[0], parts[1].strip()
    local_timestamp_nanos = parse_recorder_timestamp_nanos(local_timestamp_text)

    try:
        payload: Any = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise TardisCaptureEvidenceError("tardis_capture_message_malformed_json") from exc
    if not isinstance(payload, dict):
        raise TardisCaptureEvidenceError("tardis_capture_message_not_an_object")
    if payload.get("generated") is True:
        raise TardisCaptureEvidenceError("tardis_generated_record_rejected")

    topic = payload.get("topic")
    if not isinstance(topic, str) or topic != f"{channel.value}.{symbol}":
        raise TardisCaptureEvidenceError("tardis_capture_topic_identity_mismatch")

    raw_type = payload.get("type")
    if not isinstance(raw_type, str):
        raise TardisCaptureEvidenceError("tardis_capture_message_type_missing")
    try:
        message_type = TardisMessageTypeV1(raw_type)
    except ValueError as exc:
        raise TardisCaptureEvidenceError("tardis_capture_message_type_unknown") from exc

    data = payload.get("data")
    if isinstance(data, dict):
        payload_symbol = data.get("symbol")
        if payload_symbol is not None and payload_symbol != symbol:
            raise TardisCaptureEvidenceError("tardis_capture_payload_symbol_mismatch")
        if data.get("generated") is True:
            raise TardisCaptureEvidenceError("tardis_generated_record_rejected")
    elif isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                raise TardisCaptureEvidenceError("tardis_capture_data_entry_not_an_object")
            if entry.get("generated") is True:
                raise TardisCaptureEvidenceError("tardis_generated_record_rejected")
            entry_symbol = entry.get("s")
            if entry_symbol is not None and entry_symbol != symbol:
                raise TardisCaptureEvidenceError("tardis_capture_payload_symbol_mismatch")
    else:
        raise TardisCaptureEvidenceError("tardis_capture_data_missing")

    raw_sequence = payload.get("cs")
    if raw_sequence is not None and (isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int)):
        raise TardisCaptureEvidenceError("tardis_cross_sequence_malformed")
    exchange_timestamp = _exchange_timestamp(payload.get("ts"))

    content_hash = _record_content_hash(
        tardis_exchange_id=tardis_exchange_id,
        originating_exchange=ORIGINATING_EXCHANGE_BYBIT,
        channel=channel,
        symbol=symbol,
        message_type=message_type,
        raw_message=raw_message,
        exchange_timestamp=exchange_timestamp,
        local_timestamp_text=local_timestamp_text,
        source_date=source_date,
        cross_sequence=raw_sequence,
    )
    return TardisRawCaptureRecordV1(
        tardis_exchange_id=tardis_exchange_id,
        originating_exchange=ORIGINATING_EXCHANGE_BYBIT,
        channel=channel,
        symbol=symbol,
        message_type=message_type,
        raw_message=raw_message,
        exchange_timestamp=exchange_timestamp,
        local_timestamp_text=local_timestamp_text,
        local_timestamp_nanos=local_timestamp_nanos,
        source_date=source_date,
        cross_sequence=raw_sequence,
        parser_semantic_version=TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
        content_hash=content_hash,
    )


@dataclass(frozen=True, slots=True)
class TardisCaptureStreamV1:
    """One retrieved capture window: an ordered, hashed run of records inside it.

    The window is a claim about the *recording*, not about the exchange: it says
    these records are everything the recorder held whose arrival fell in
    ``[declared_start_nanos, declared_end_nanos)``. Reconnects inside the window
    are preserved as ``snapshot`` records, never smoothed away.
    """

    channel: TardisChannelV1
    symbol: str
    source_date: date
    declared_start_nanos: int
    declared_end_nanos: int
    records: tuple[TardisRawCaptureRecordV1, ...]
    manifest_hash: str

    @property
    def declared_start_at(self) -> datetime:
        return nanos_to_datetime(self.declared_start_nanos)

    @property
    def declared_end_at(self) -> datetime:
        return nanos_to_datetime(self.declared_end_nanos)

    @property
    def snapshot_count(self) -> int:
        return sum(1 for r in self.records if r.message_type is TardisMessageTypeV1.SNAPSHOT)

    @property
    def delta_count(self) -> int:
        return sum(1 for r in self.records if r.message_type is TardisMessageTypeV1.DELTA)

    def interval(self) -> TardisCaptureIntervalV1:
        """The declared capture interval this stream positively covers."""
        sequences = [r.cross_sequence for r in self.records if r.cross_sequence is not None]
        return TardisCaptureIntervalV1(
            channel=self.channel,
            symbol=self.symbol,
            source_date=self.source_date,
            start_nanos=self.declared_start_nanos,
            end_nanos=self.declared_end_nanos,
            record_count=len(self.records),
            first_cross_sequence=sequences[0] if sequences else None,
            last_cross_sequence=sequences[-1] if sequences else None,
            reconnect_snapshot_count=self.snapshot_count,
            manifest_hash=self.manifest_hash,
        )


def _stream_manifest_hash(
    *,
    tardis_exchange_id: str,
    channel: TardisChannelV1,
    symbol: str,
    source_date: date,
    declared_start_nanos: int,
    declared_end_nanos: int,
    records: Sequence[TardisRawCaptureRecordV1],
) -> str:
    return canonical_hash(
        {
            "parser_semantic_version": TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
            "tardis_exchange_id": tardis_exchange_id,
            "originating_exchange": ORIGINATING_EXCHANGE_BYBIT,
            "channel": channel.value,
            "symbol": symbol,
            "source_date": source_date.isoformat(),
            "declared_start_nanos": declared_start_nanos,
            "declared_end_nanos": declared_end_nanos,
            "record_content_hashes": [r.content_hash for r in records],
        }
    )


def parse_tardis_capture_stream(
    lines: Iterable[str],
    *,
    channel: TardisChannelV1,
    symbol: str,
    source_date: date,
    declared_start_nanos: int,
    declared_end_nanos: int,
    tardis_exchange_id: str = TARDIS_EXCHANGE_ID_BYBIT,
) -> TardisCaptureStreamV1:
    """Parse and validate one retrieved capture window. Fails closed on disorder.

    An empty window is accepted: a channel can genuinely be silent for a whole
    window, and refusing that would push the caller toward inventing coverage.
    """
    if declared_end_nanos <= declared_start_nanos:
        raise TardisCaptureEvidenceError("tardis_capture_declared_window_empty")
    records: list[TardisRawCaptureRecordV1] = []
    previous_nanos: int | None = None
    for line in lines:
        if not line.strip():
            continue
        record = parse_tardis_capture_line(
            line,
            channel=channel,
            symbol=symbol,
            source_date=source_date,
            tardis_exchange_id=tardis_exchange_id,
        )
        if previous_nanos is not None and record.local_timestamp_nanos < previous_nanos:
            raise TardisCaptureEvidenceError("tardis_capture_arrival_order_regression")
        if not (declared_start_nanos <= record.local_timestamp_nanos < declared_end_nanos):
            raise TardisCaptureEvidenceError("tardis_capture_record_outside_declared_window")
        previous_nanos = record.local_timestamp_nanos
        records.append(record)
    return TardisCaptureStreamV1(
        channel=channel,
        symbol=symbol,
        source_date=source_date,
        declared_start_nanos=declared_start_nanos,
        declared_end_nanos=declared_end_nanos,
        records=tuple(records),
        manifest_hash=_stream_manifest_hash(
            tardis_exchange_id=tardis_exchange_id,
            channel=channel,
            symbol=symbol,
            source_date=source_date,
            declared_start_nanos=declared_start_nanos,
            declared_end_nanos=declared_end_nanos,
            records=records,
        ),
    )


def join_contiguous_streams(
    streams: Sequence[TardisCaptureStreamV1],
    *,
    tardis_exchange_id: str = TARDIS_EXCHANGE_ID_BYBIT,
) -> TardisCaptureStreamV1:
    """Reassemble exactly-abutting retrieved windows into one continuous stream.

    Retrieval windowing is an artifact of how the capture was fetched, not of the
    recording. Windows that abut exactly are one continuous recording and may be
    replayed as one. Anything else -- a hole, an overlap, a different channel,
    symbol or day -- is refused, so a ticker state machine can never walk across
    a collection outage because the caller happened to concatenate two lists.
    """
    if not streams:
        raise TardisCaptureEvidenceError("tardis_capture_join_requires_streams")
    ordered = tuple(sorted(streams, key=lambda s: s.declared_start_nanos))
    first = ordered[0]
    for previous, following in pairwise(ordered):
        if (following.channel, following.symbol, following.source_date) != (
            previous.channel,
            previous.symbol,
            previous.source_date,
        ):
            raise TardisCaptureEvidenceError("tardis_capture_join_mixed_identity")
        if following.declared_start_nanos != previous.declared_end_nanos:
            raise TardisCaptureEvidenceError("tardis_capture_join_windows_not_contiguous")
    records = tuple(record for stream in ordered for record in stream.records)
    return TardisCaptureStreamV1(
        channel=first.channel,
        symbol=first.symbol,
        source_date=first.source_date,
        declared_start_nanos=first.declared_start_nanos,
        declared_end_nanos=ordered[-1].declared_end_nanos,
        records=records,
        manifest_hash=_stream_manifest_hash(
            tardis_exchange_id=tardis_exchange_id,
            channel=first.channel,
            symbol=first.symbol,
            source_date=first.source_date,
            declared_start_nanos=first.declared_start_nanos,
            declared_end_nanos=ordered[-1].declared_end_nanos,
            records=records,
        ),
    )


@dataclass(frozen=True, slots=True)
class TardisCaptureIntervalV1:
    """A half-open arrival-time window ``[start, end)`` that was positively captured."""

    channel: TardisChannelV1
    symbol: str
    source_date: date
    start_nanos: int
    end_nanos: int
    record_count: int
    first_cross_sequence: int | None
    last_cross_sequence: int | None
    reconnect_snapshot_count: int
    manifest_hash: str

    @property
    def start_at(self) -> datetime:
        return nanos_to_datetime(self.start_nanos)

    @property
    def end_at(self) -> datetime:
        return nanos_to_datetime(self.end_nanos)

    def covers_nanos(self, nanos: int) -> bool:
        return self.start_nanos <= nanos < self.end_nanos


@dataclass(frozen=True, slots=True)
class TardisCaptureGapV1:
    """One hole between adjacent captured intervals, with the rule that found it.

    ``sequence_evidence`` is the honest statement of what the Bybit ``cs``
    cross-sequence proves across this hole: it can prove that messages are
    missing, and it can prove disorder, but a channel-sparse cross-sequence can
    never prove that nothing is missing.
    """

    kind: TardisCaptureGapKindV1
    start_nanos: int
    end_nanos: int
    preceding_last_cross_sequence: int | None
    following_first_cross_sequence: int | None
    sequence_evidence: str

    @property
    def start_at(self) -> datetime:
        return nanos_to_datetime(self.start_nanos)

    @property
    def end_at(self) -> datetime:
        return nanos_to_datetime(self.end_nanos)

    @property
    def duration(self) -> timedelta:
        return timedelta(microseconds=(self.end_nanos - self.start_nanos) // 1000)


@dataclass(frozen=True, slots=True)
class TardisCaptureCoverageV1:
    """Exactly what a capture holds, and exactly where it holds nothing."""

    channel: TardisChannelV1
    symbol: str
    intervals: tuple[TardisCaptureIntervalV1, ...]
    gaps: tuple[TardisCaptureGapV1, ...]
    content_hash: str

    @property
    def captured_record_count(self) -> int:
        return sum(interval.record_count for interval in self.intervals)

    def covers_nanos(self, nanos: int) -> bool:
        return any(interval.covers_nanos(nanos) for interval in self.intervals)

    def gap_containing_nanos(self, nanos: int) -> TardisCaptureGapV1 | None:
        for gap in self.gaps:
            if gap.start_nanos < nanos < gap.end_nanos:
                return gap
        return None

    def require_covered(self, nanos: int) -> None:
        """Fail closed for any instant this capture does not positively hold."""
        if self.covers_nanos(nanos):
            return
        gap = self.gap_containing_nanos(nanos)
        if gap is not None:
            raise TardisCaptureEvidenceError("tardis_capture_instant_inside_collection_gap")
        raise TardisCaptureEvidenceError("tardis_capture_instant_outside_captured_coverage")


def build_tardis_capture_coverage(
    intervals: Sequence[TardisCaptureIntervalV1],
) -> TardisCaptureCoverageV1:
    """Order intervals by arrival time and record every hole between them."""
    if not intervals:
        raise TardisCaptureEvidenceError("tardis_capture_coverage_requires_intervals")
    channels = {interval.channel for interval in intervals}
    symbols = {interval.symbol for interval in intervals}
    if len(channels) != 1 or len(symbols) != 1:
        raise TardisCaptureEvidenceError("tardis_capture_coverage_mixed_identity")

    ordered = tuple(sorted(intervals, key=lambda i: (i.start_nanos, i.end_nanos)))
    gaps: list[TardisCaptureGapV1] = []
    for previous, following in pairwise(ordered):
        if following.start_nanos < previous.end_nanos:
            raise TardisCaptureEvidenceError("tardis_capture_intervals_overlap")
        if following.start_nanos == previous.end_nanos:
            # Exactly abutting windows are one continuous recording: the hole
            # between them has zero width, so there is nothing to represent.
            continue
        previous_sequence = previous.last_cross_sequence
        following_sequence = following.first_cross_sequence
        if previous_sequence is not None and following_sequence is not None:
            if following_sequence <= previous_sequence:
                kind = TardisCaptureGapKindV1.SEQUENCE_REGRESSION
                evidence = (
                    "following_first_cross_sequence_is_not_ahead_of_preceding_last: "
                    "the two intervals are not one ordered recording"
                )
            else:
                kind = TardisCaptureGapKindV1.SEQUENCE_ADVANCED_ACROSS_HOLE
                evidence = (
                    "cross_sequence_advanced_across_the_hole: messages provably exist "
                    "that this capture does not hold; a sparse per-channel cross-sequence "
                    "can never prove the converse"
                )
        else:
            kind = TardisCaptureGapKindV1.UNCAPTURED_INTERVAL
            evidence = "no_cross_sequence_on_either_side: hole established by declared capture bounds only"
        gaps.append(
            TardisCaptureGapV1(
                kind=kind,
                start_nanos=previous.end_nanos,
                end_nanos=following.start_nanos,
                preceding_last_cross_sequence=previous_sequence,
                following_first_cross_sequence=following_sequence,
                sequence_evidence=evidence,
            )
        )

    content_hash = canonical_hash(
        {
            "parser_semantic_version": TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
            "lifecycle": CAPTURE_LIFECYCLE_V1,
            "channel": ordered[0].channel.value,
            "symbol": ordered[0].symbol,
            "intervals": [
                {
                    "source_date": interval.source_date.isoformat(),
                    "start_nanos": interval.start_nanos,
                    "end_nanos": interval.end_nanos,
                    "record_count": interval.record_count,
                    "first_cross_sequence": interval.first_cross_sequence,
                    "last_cross_sequence": interval.last_cross_sequence,
                    "reconnect_snapshot_count": interval.reconnect_snapshot_count,
                    "manifest_hash": interval.manifest_hash,
                }
                for interval in ordered
            ],
            "gaps": [
                {
                    "kind": gap.kind.value,
                    "start_nanos": gap.start_nanos,
                    "end_nanos": gap.end_nanos,
                    "preceding_last_cross_sequence": gap.preceding_last_cross_sequence,
                    "following_first_cross_sequence": gap.following_first_cross_sequence,
                }
                for gap in gaps
            ],
        }
    )
    return TardisCaptureCoverageV1(
        channel=ordered[0].channel,
        symbol=ordered[0].symbol,
        intervals=ordered,
        gaps=tuple(gaps),
        content_hash=content_hash,
    )
