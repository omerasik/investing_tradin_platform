"""Phase 3Z.2 -- the first-party recorder against Bybit's official public WebSocket.

``RESEARCH_ONLY``. Public market data only: no credential, no account, no
signature, no private topic, no order, no broker and no execution path. The
socket is opened to the official endpoint the capture contract names and to
nothing else.

Why the client is written here rather than added as a dependency
----------------------------------------------------------------
This repository pins an exact, CI-audited runtime dependency closure (see
``requirements-runtime.txt``, whose set is re-stated deliberately in
``requirements-migrate.txt``), and that closure is what the hardened container
is scanned and attested against. A research-only recorder that never runs inside
the API image is a poor reason to widen an audited supply-chain surface, so the
handful of RFC 6455 frames actually needed -- a client handshake, text frames,
continuation frames, ping/pong and close -- are implemented here on
:mod:`socket` and :mod:`ssl`. It is perhaps two hundred lines, it is auditable
in one sitting, and it adds nothing to the image.

The recorder is deliberately synchronous and single-connection. One instrument,
two topics, one socket: concurrency would buy nothing and would blur which clock
read belongs to which message.

Fail closed around every boundary
---------------------------------
Coverage opens only when Bybit has acknowledged both subscriptions, never when
the socket opened -- an accepted TCP connection proves nothing about whether
data is flowing. Coverage closes on a clean stop, a lost connection or a clock
discontinuity, and each close records *which*, so a process that died is never
presented as a process that finished. Every reconnect is a new coverage window
with a gap in front of it, and ticker state is never carried across it: Bybit
re-sends a ``snapshot`` on resubscribe, and
:mod:`trade_platform.bybit_ticker_state_reconstruction_v1` already resets
component state on a snapshot, so replayed capture feeds that proven logic
without this module duplicating it.

A UTC-day rollover finalizes the open partition and starts a new one, so a
partition is always exactly one session's slice of one day.

What this module does not do
-----------------------------
No normalization, no canonical observation, no dataset, no sealing, no feature,
no backtest, no performance number. It writes raw evidence and lifecycle facts,
and stops. Turning that evidence into canonical historical observations is Phase
3Z.3.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlparse
from uuid import UUID

from .first_party_capture_archive_v1 import (
    END_PROOF_CLEAN_CLOSE,
    END_PROOF_CLOCK_DISCONTINUITY,
    END_PROOF_CONNECTION_LOST,
    CaptureClockMonitorV1,
    CaptureClockReadingV1,
    CaptureCoverageIntervalV1,
    CaptureGapKindV1,
    CaptureGapV1,
    CaptureLifecycleEventV1,
    CaptureLifecycleKindV1,
    CapturePartitionWriterV1,
    FirstPartyCaptureArchiveError,
    build_capture_record_v1,
    default_archive_root,
    measure_clock_resolution_nanos,
    new_session_id_v1,
)
from .first_party_capture_authority_v1 import (
    FirstPartyCaptureContractV1,
    first_party_bybit_capture_contract_v1,
)

#: Bounded exponential backoff. Bybit's public feed is free and unmetered, but
#: hammering a public endpoint after a failure is neither polite nor useful.
RECONNECT_BACKOFF_SECONDS_V1: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)

#: Bybit closes an idle public connection; a client ping well inside that window
#: keeps it open without pretending silence is liveness.
PING_INTERVAL_SECONDS_V1: Final = 20.0

#: If nothing at all arrives for this long the connection is treated as dead
#: even without a socket error, because a silently half-open TCP connection is
#: indistinguishable from a quiet market until you stop assuming.
RECEIVE_TIMEOUT_SECONDS_V1: Final = 45.0

_WS_GUID: Final = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OPCODE_CONTINUATION: Final = 0x0
_OPCODE_TEXT: Final = 0x1
_OPCODE_BINARY: Final = 0x2
_OPCODE_CLOSE: Final = 0x8
_OPCODE_PING: Final = 0x9
_OPCODE_PONG: Final = 0xA

#: A public market-data frame far larger than this is not something this
#: contract subscribes to; refusing it bounds memory on a hostile or broken peer.
_MAX_FRAME_BYTES: Final = 8 * 1024 * 1024


class BybitPublicWebSocketError(RuntimeError):
    """Raised when the public feed cannot be reached or speaks an unexpected protocol."""


class _WebSocketClient:
    """The minimum RFC 6455 client this recorder needs. Text frames, ping, close."""

    __slots__ = ("_buffer", "_socket")

    def __init__(self, url: str, *, connect_timeout: float = 15.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "wss":
            # Public market data still travels over TLS; plaintext is refused so
            # a misconfigured endpoint cannot silently downgrade.
            raise BybitPublicWebSocketError("only_wss_endpoints_are_accepted")
        host = parsed.hostname
        if host is None:
            raise BybitPublicWebSocketError("endpoint_has_no_host")
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw = socket.create_connection((host, port), timeout=connect_timeout)
        context = ssl.create_default_context()
        self._socket = context.wrap_socket(raw, server_hostname=host)
        self._buffer = b""
        self._handshake(host, path)
        self._socket.settimeout(RECEIVE_TIMEOUT_SECONDS_V1)

    def _handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: trade-platform-first-party-capture/1.0\r\n"
            "\r\n"
        )
        self._socket.sendall(request.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise BybitPublicWebSocketError("handshake_closed_before_completion")
            header += chunk
            if len(header) > 64 * 1024:
                raise BybitPublicWebSocketError("handshake_response_too_large")
        head, _, rest = header.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if "101" not in status:
            raise BybitPublicWebSocketError(f"handshake_rejected:{status}")
        # RFC 6455 fixes SHA-1 for the accept-key echo. It is a protocol
        # constant proving the peer understood the handshake, not a security
        # primitive, so it is marked as such rather than "modernized" into a
        # hash the standard does not allow.
        digest = hashlib.sha1((key + _WS_GUID).encode(), usedforsecurity=False).digest()
        expected = base64.b64encode(digest).decode()
        lowered = head.decode(errors="replace").lower()
        if expected.lower() not in lowered:
            raise BybitPublicWebSocketError("handshake_accept_key_mismatch")
        self._buffer = rest

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise BybitPublicWebSocketError("connection_closed_by_peer")
            self._buffer += chunk
        taken, self._buffer = self._buffer[:count], self._buffer[count:]
        return taken

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(_OPCODE_TEXT, text.encode())

    def send_ping(self) -> None:
        self._send_frame(_OPCODE_PING, b"")

    def receive_text(self) -> str:
        """Next application text message, transparently handling control frames."""
        fragments: list[bytes] = []
        fragment_opcode: int | None = None
        while True:
            first, second = self._recv_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            if second & 0x80:
                raise BybitPublicWebSocketError("server_frames_must_not_be_masked")
            length = second & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", self._recv_exact(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._recv_exact(8))
            if length > _MAX_FRAME_BYTES:
                raise BybitPublicWebSocketError("frame_exceeds_maximum_size")
            payload = self._recv_exact(length)

            if opcode == _OPCODE_PING:
                self._send_frame(_OPCODE_PONG, payload)
                continue
            if opcode == _OPCODE_PONG:
                continue
            if opcode == _OPCODE_CLOSE:
                raise BybitPublicWebSocketError("connection_closed_by_peer")
            if opcode == _OPCODE_BINARY:
                raise BybitPublicWebSocketError("binary_frames_are_not_expected_on_this_feed")

            if opcode == _OPCODE_CONTINUATION:
                if fragment_opcode is None:
                    raise BybitPublicWebSocketError("continuation_without_a_started_message")
            else:
                fragment_opcode = opcode
            fragments.append(payload)
            if fin:
                if fragment_opcode != _OPCODE_TEXT:
                    raise BybitPublicWebSocketError("unexpected_non_text_message")
                return b"".join(fragments).decode()

    def close(self) -> None:
        try:
            self._send_frame(_OPCODE_CLOSE, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self._socket.close()
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class CaptureHealthV1:
    """What the operator needs to know to answer "is capture currently valid?"."""

    session_id: UUID | None
    connected: bool
    subscriptions_acknowledged: bool
    records_written: int
    coverage_open: bool
    last_arrival_utc: datetime | None
    reconnects: int
    clock_discontinuities: int
    gaps_recorded: int
    partition_directory: Path | None

    def summary(self) -> str:
        status = "COVERING" if (self.connected and self.coverage_open) else "NOT_COVERING"
        last = "never" if self.last_arrival_utc is None else self.last_arrival_utc.isoformat()
        return (
            f"{status} session={self.session_id} records={self.records_written} "
            f"last_arrival={last} reconnects={self.reconnects} "
            f"clock_discontinuities={self.clock_discontinuities} gaps={self.gaps_recorded}"
        )


class BybitPublicCaptureRecorderV1:
    """Record the official public Bybit feed into an immutable first-party archive.

    One instrument, two topics, one connection. Reconnects with bounded backoff,
    rolls partitions at UTC midnight, and represents every interruption as an
    explicit gap rather than as continuous coverage.
    """

    def __init__(
        self,
        *,
        archive_root: Path | None = None,
        contract: FirstPartyCaptureContractV1 | None = None,
        clock_monitor: CaptureClockMonitorV1 | None = None,
    ) -> None:
        self._contract = first_party_bybit_capture_contract_v1() if contract is None else contract
        self._root = default_archive_root() if archive_root is None else archive_root
        self._clock = CaptureClockMonitorV1() if clock_monitor is None else clock_monitor
        self._session_id: UUID | None = None
        self._writer: CapturePartitionWriterV1 | None = None
        self._day: date | None = None
        self._sequence = 0
        self._records_written = 0
        self._reconnects = 0
        self._clock_discontinuities = 0
        self._gaps_recorded = 0
        self._connected = False
        self._acknowledged = False
        self._coverage_open = False
        self._coverage_start_nanos: int | None = None
        self._coverage_records = 0
        self._last_arrival_nanos: int | None = None
        self._resolution_nanos = measure_clock_resolution_nanos()

    # -- health -----------------------------------------------------------
    def health(self) -> CaptureHealthV1:
        from .first_party_capture_archive_v1 import nanos_to_datetime

        return CaptureHealthV1(
            session_id=self._session_id,
            connected=self._connected,
            subscriptions_acknowledged=self._acknowledged,
            records_written=self._records_written,
            coverage_open=self._coverage_open,
            last_arrival_utc=(
                None
                if self._last_arrival_nanos is None
                else nanos_to_datetime(self._last_arrival_nanos)
            ),
            reconnects=self._reconnects,
            clock_discontinuities=self._clock_discontinuities,
            gaps_recorded=self._gaps_recorded,
            partition_directory=None if self._writer is None else self._writer.directory,
        )

    # -- partition lifecycle ---------------------------------------------
    def _writer_for(self, day: date) -> CapturePartitionWriterV1:
        if self._session_id is None:
            raise FirstPartyCaptureArchiveError("capture_session_was_never_opened")
        if self._writer is not None and self._day == day:
            return self._writer
        if self._writer is not None:
            # A UTC-day rollover: finish the old partition before opening a new
            # one, so a partition is always one session's slice of one day.
            self._close_coverage(END_PROOF_CLEAN_CLOSE)
            self._writer.finalize()
        self._writer = CapturePartitionWriterV1(
            root=self._root,
            contract=self._contract,
            session_id=self._session_id,
            day=day,
            clock_resolution_nanos=self._resolution_nanos,
        )
        self._day = day
        return self._writer

    def _lifecycle(self, kind: CaptureLifecycleKindV1, detail: str | None = None) -> None:
        reading = CaptureClockReadingV1.now()
        event = CaptureLifecycleEventV1(
            kind=kind.value,
            arrival_utc_nanos=reading.arrival_utc_nanos,
            arrival_monotonic_nanos=reading.arrival_monotonic_nanos,
            detail=detail,
        )
        writer = self._writer_for(datetime.fromtimestamp(
            reading.arrival_utc_nanos / 1_000_000_000, tz=UTC
        ).date())
        writer.append_lifecycle(event)

    def _open_coverage(self, at_nanos: int) -> None:
        """Start a coverage window, or leave an already-open one alone.

        Idempotent on purpose. Bybit can push a first ``snapshot`` before its
        subscribe acknowledgement arrives, and that data is itself proof the
        subscription is live. Re-opening on the later acknowledgement would move
        the window's start forward past records already written and reset their
        count, leaving captured evidence outside every declared window -- which
        is precisely the inconsistency this design exists to refuse.
        """
        if self._coverage_open:
            return
        self._coverage_open = True
        self._coverage_start_nanos = at_nanos
        self._coverage_records = 0

    def _close_coverage(self, end_proof: str, detail: str | None = None) -> None:
        if not self._coverage_open or self._writer is None:
            return
        if self._coverage_start_nanos is None:
            raise FirstPartyCaptureArchiveError("open_coverage_has_no_recorded_start")
        end_nanos = self._last_arrival_nanos or self._coverage_start_nanos
        self._writer.declare_coverage(
            CaptureCoverageIntervalV1(
                start_utc_nanos=self._coverage_start_nanos,
                end_utc_nanos=end_nanos,
                end_proof=end_proof,
                record_count=self._coverage_records,
            )
        )
        if end_proof != END_PROOF_CLEAN_CLOSE:
            kind = (
                CaptureGapKindV1.CLOCK_DISCONTINUITY
                if end_proof == END_PROOF_CLOCK_DISCONTINUITY
                else CaptureGapKindV1.CONNECTION_LOSS
            )
            # An open-ended gap: it starts where proof stopped. Its end is
            # whenever coverage is next proven, never guessed here.
            self._writer.declare_gap(
                CaptureGapV1(
                    start_utc_nanos=end_nanos,
                    end_utc_nanos=end_nanos,
                    kind=kind.value,
                    detail=detail or end_proof,
                )
            )
            self._gaps_recorded += 1
        self._coverage_open = False
        self._coverage_start_nanos = None

    # -- capture ----------------------------------------------------------
    def run(self, *, max_records: int | None = None, max_seconds: float | None = None) -> CaptureHealthV1:
        """Capture until a bound is reached. Bounds exist so a smoke run is finite.

        Passing neither bound records indefinitely, which is the operator mode.
        """
        self._session_id = new_session_id_v1()
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        try:
            self._lifecycle(CaptureLifecycleKindV1.SESSION_STARTED, str(self._session_id))
            attempt = 0
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if max_records is not None and self._records_written >= max_records:
                    break
                try:
                    self._run_one_connection(max_records=max_records, deadline=deadline)
                    attempt = 0
                    if (max_records is not None and self._records_written >= max_records) or (
                        deadline is not None and time.monotonic() >= deadline
                    ):
                        break
                except (BybitPublicWebSocketError, OSError, ssl.SSLError) as error:
                    self._connected = False
                    self._acknowledged = False
                    self._close_coverage(END_PROOF_CONNECTION_LOST, str(error))
                    self._lifecycle(CaptureLifecycleKindV1.CONNECTION_LOST, str(error))
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    if max_records is not None and self._records_written >= max_records:
                        break
                    backoff = RECONNECT_BACKOFF_SECONDS_V1[
                        min(attempt, len(RECONNECT_BACKOFF_SECONDS_V1) - 1)
                    ]
                    attempt += 1
                    self._reconnects += 1
                    self._lifecycle(
                        CaptureLifecycleKindV1.RECONNECT_STARTED, f"backoff={backoff}s"
                    )
                    # State must not survive the hole: a fresh snapshot after
                    # resubscribe is what re-establishes authoritative state.
                    self._clock.reset()
                    time.sleep(backoff)
        finally:
            self._close_coverage(END_PROOF_CLEAN_CLOSE)
            if self._writer is not None:
                self._lifecycle(CaptureLifecycleKindV1.SESSION_CLOSED, str(self._session_id))
                self._writer.finalize()
                self._writer = None
        return self.health()

    def _run_one_connection(
        self, *, max_records: int | None, deadline: float | None
    ) -> None:
        client = _WebSocketClient(self._contract.endpoint)
        self._connected = True
        self._lifecycle(CaptureLifecycleKindV1.CONNECTION_OPENED, self._contract.endpoint)
        try:
            topics = list(self._contract.topics())
            client.send_text(json.dumps({"op": "subscribe", "args": topics}))
            pending = set(topics)
            last_ping = time.monotonic()

            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    return
                if max_records is not None and self._records_written >= max_records:
                    return
                if time.monotonic() - last_ping >= PING_INTERVAL_SECONDS_V1:
                    client.send_ping()
                    last_ping = time.monotonic()

                text = client.receive_text()
                reading = CaptureClockReadingV1.now()

                if pending:
                    if self._handle_subscription_reply(text, pending, reading):
                        continue
                    if not self._is_data_message(text):
                        continue

                self._record(text, reading)
        finally:
            client.close()
            self._connected = False

    @staticmethod
    def _is_data_message(text: str) -> bool:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict) and "topic" in parsed

    def _handle_subscription_reply(
        self, text: str, pending: set[str], reading: CaptureClockReadingV1
    ) -> bool:
        """Consume Bybit's subscribe acknowledgement. Coverage opens only on success."""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed, dict) or parsed.get("op") != "subscribe":
            return False
        if parsed.get("success") is False:
            raise BybitPublicWebSocketError(f"subscription_rejected:{parsed.get('ret_msg')}")
        pending.clear()
        self._acknowledged = True
        self._lifecycle(
            CaptureLifecycleKindV1.SUBSCRIPTIONS_ACKNOWLEDGED,
            ",".join(self._contract.topics()),
        )
        # Coverage starts at acknowledgement, not at socket open: an accepted
        # TCP connection proves nothing about whether data is flowing.
        self._open_coverage(reading.arrival_utc_nanos)
        return True

    def _record(self, text: str, reading: CaptureClockReadingV1) -> None:
        verdict = self._clock.observe(reading)
        if not verdict.accepted:
            # A regressed clock means this is not one ordered recording.
            self._close_coverage(END_PROOF_CLOCK_DISCONTINUITY, ",".join(verdict.reasons))
            self._lifecycle(
                CaptureLifecycleKindV1.CLOCK_DISCONTINUITY, ",".join(verdict.reasons)
            )
            self._clock_discontinuities += 1
            self._clock.reset()
            raise BybitPublicWebSocketError("clock_regression_within_session")
        if verdict.discontinuity:
            self._close_coverage(END_PROOF_CLOCK_DISCONTINUITY, ",".join(verdict.reasons))
            self._lifecycle(
                CaptureLifecycleKindV1.CLOCK_DISCONTINUITY, ",".join(verdict.reasons)
            )
            self._clock_discontinuities += 1
            self._clock.reset()
            self._open_coverage(reading.arrival_utc_nanos)

        try:
            record = build_capture_record_v1(
                contract=self._contract,
                session_id=self._session_id,  # type: ignore[arg-type]
                sequence=self._sequence,
                clock=reading,
                payload_text=text,
            )
        except FirstPartyCaptureArchiveError:
            # Anything the contract does not authorize is not written. A
            # heartbeat or an unrelated control frame is simply not evidence.
            return

        day = datetime.fromtimestamp(reading.arrival_utc_nanos / 1_000_000_000, tz=UTC).date()
        writer = self._writer_for(day)
        if not self._coverage_open:
            self._open_coverage(reading.arrival_utc_nanos)
        writer.append_record(record)
        self._sequence += 1
        self._records_written += 1
        self._coverage_records += 1
        self._last_arrival_nanos = reading.arrival_utc_nanos


def iter_health_lines(root: Path | None = None) -> Iterator[str]:
    """Human-readable status of every partition under an archive root."""
    from .first_party_capture_archive_v1 import find_partitions_v1, read_partition_status_v1

    base = default_archive_root() if root is None else root
    for directory in find_partitions_v1(base):
        partition = read_partition_status_v1(directory)
        reasons = "" if not partition.reasons else " reasons=" + ",".join(partition.reasons)
        yield (
            f"{partition.status} records={partition.record_count} "
            f"coverage={len(partition.coverage)} gaps={len(partition.gaps)} "
            f"{directory}{reasons}"
        )


__all__: list[str] = [
    "PING_INTERVAL_SECONDS_V1",
    "RECEIVE_TIMEOUT_SECONDS_V1",
    "RECONNECT_BACKOFF_SECONDS_V1",
    "BybitPublicCaptureRecorderV1",
    "BybitPublicWebSocketError",
    "CaptureHealthV1",
    "iter_health_lines",
]
