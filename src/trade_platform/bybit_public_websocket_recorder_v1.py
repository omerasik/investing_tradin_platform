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
data is flowing -- and ends at the last message that positively proved it.
Each close records *why*: an operator bounded stop, an operator interrupt, a
UTC-day rollover, a lost or peer-closed connection, a clock discontinuity, a
rejected message, or an unexpected recorder failure. The outer ``finally``
never decides that label, so a crash inside the recorder is never presented as
a process that finished. Every reconnect is a new coverage window with a gap
in front of it, and ticker state is never carried across it: Bybit
re-sends a ``snapshot`` on resubscribe, and
:mod:`trade_platform.bybit_ticker_state_reconstruction_v1` already resets
component state on a snapshot, so replayed capture feeds that proven logic
without this module duplicating it.

Only a positively identified Bybit operation reply (``subscribe``/``ping``/
``pong`` without a topic) passes without becoming a record. Everything else is
market data or a violation: a message that is not JSON, has an unknown shape,
or carries a topic but fails the authorized payload contract closes coverage
at the last proof, is kept verbatim in the lifecycle log as
``MESSAGE_REJECTED``, and resets the connection. Nothing the contract refuses
can disappear inside a window that still claims to be covered.

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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, NoReturn
from urllib.parse import urlparse
from uuid import UUID

from .first_party_capture_archive_v1 import (
    END_PROOF_CLOCK_DISCONTINUITY,
    END_PROOF_CONNECTION_LOST,
    END_PROOF_CONTRACT_VIOLATION,
    END_PROOF_OPERATOR_BOUNDED_STOP,
    END_PROOF_OPERATOR_INTERRUPT,
    END_PROOF_PEER_CLOSED,
    END_PROOF_RECORDER_FAILURE,
    END_PROOF_UTC_DAY_ROLLOVER,
    GAP_KIND_FOR_END_PROOF_V1,
    CaptureClockMonitorV1,
    CaptureClockReadingV1,
    CaptureCoverageIntervalV1,
    CaptureGapV1,
    CaptureLifecycleEventV1,
    CaptureLifecycleKindV1,
    CapturePartitionWriterV1,
    FirstPartyCaptureArchiveError,
    build_capture_record_v1,
    default_archive_root,
    measure_clock_resolution_nanos,
    nanos_to_datetime,
    new_session_id_v1,
    utc_day_of_nanos,
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


#: Bybit operation replies that carry no market data. Only these may pass the
#: recorder without becoming a record; anything else is data or a violation.
_CONTROL_OPERATIONS: Final = frozenset({"subscribe", "ping", "pong"})


class BybitPublicWebSocketError(RuntimeError):
    """Raised when the public feed cannot be reached or speaks an unexpected protocol."""


class BybitPublicPeerClosedError(BybitPublicWebSocketError):
    """The exchange sent a WebSocket close frame. Still an interruption, not our stop."""


class CaptureClockRegressionError(BybitPublicWebSocketError):
    """The wall clock or the UTC day went backwards within a session."""


class CaptureContractViolationError(BybitPublicWebSocketError):
    """A message failed the authorized contract. Coverage closed before it."""


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
        # The default context still negotiates TLS 1.0/1.1. Nothing this
        # recorder talks to needs them, so the floor is raised rather than
        # inherited: certificate and hostname verification stay on as well.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
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
                raise BybitPublicPeerClosedError("connection_closed_by_peer_close_frame")
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
    contract_violations: int = 0
    rejection_reasons: Mapping[str, int] = field(default_factory=dict)
    session_end_proof: str | None = None

    def summary(self) -> str:
        status = "COVERING" if (self.connected and self.coverage_open) else "NOT_COVERING"
        last = "never" if self.last_arrival_utc is None else self.last_arrival_utc.isoformat()
        rejected = ",".join(f"{reason}={count}" for reason, count in self.rejection_reasons.items())
        return (
            f"{status} session={self.session_id} records={self.records_written} "
            f"last_arrival={last} reconnects={self.reconnects} "
            f"clock_discontinuities={self.clock_discontinuities} gaps={self.gaps_recorded} "
            f"contract_violations={self.contract_violations}"
            + (f" rejected[{rejected}]" if rejected else "")
            + (f" end_proof={self.session_end_proof}" if self.session_end_proof else "")
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
        self._contract_violations = 0
        self._rejection_reasons: dict[str, int] = {}
        self._session_end_proof: str | None = None
        self._connected = False
        self._acknowledged = False
        self._coverage_open = False
        self._coverage_start_nanos: int | None = None
        self._coverage_last_proven_nanos: int | None = None
        self._coverage_records = 0
        #: Exclusive end of the last window this session closed. A new window
        #: may not start before it, whatever the (reset) clock monitor says.
        self._window_floor_nanos = 0
        #: A gap opened by an interruption, waiting for the next window's start
        #: to bound it. Flushed open-ended if the partition ends first.
        self._pending_gap: tuple[int, str, str] | None = None
        self._last_arrival_nanos: int | None = None
        self._resolution_nanos = measure_clock_resolution_nanos()

    # -- health -----------------------------------------------------------
    def health(self) -> CaptureHealthV1:
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
            contract_violations=self._contract_violations,
            rejection_reasons=dict(sorted(self._rejection_reasons.items())),
            session_end_proof=self._session_end_proof,
        )

    # -- partition lifecycle ---------------------------------------------
    def _writer_for(self, day: date) -> CapturePartitionWriterV1:
        if self._session_id is None:
            raise FirstPartyCaptureArchiveError("capture_session_was_never_opened")
        if self._writer is not None and self._day == day:
            return self._writer
        if self._writer is not None:
            if self._day is not None and day < self._day:
                raise CaptureClockRegressionError("utc_day_regressed_within_session")
            # A UTC-day rollover: finish the old partition before opening a new
            # one, so a partition is always one session's slice of one day.
            self._close_coverage(END_PROOF_UTC_DAY_ROLLOVER)
            self._finalize_writer()
        self._writer = CapturePartitionWriterV1(
            root=self._root,
            contract=self._contract,
            session_id=self._session_id,
            day=day,
            clock_resolution_nanos=self._resolution_nanos,
        )
        self._day = day
        return self._writer

    def _finalize_writer(self) -> None:
        writer = self._writer
        if writer is None:
            return
        if self._pending_gap is not None:
            # The partition ends before coverage was proven again: the gap is
            # open-ended here, and the next partition does not inherit it.
            start, kind, detail = self._pending_gap
            writer.declare_gap(
                CaptureGapV1(start_utc_nanos=start, end_utc_nanos=None, kind=kind, detail=detail)
            )
            self._pending_gap = None
        writer.finalize()
        self._writer = None

    def _lifecycle(
        self,
        kind: CaptureLifecycleKindV1,
        detail: str | None = None,
        *,
        reading: CaptureClockReadingV1 | None = None,
        payload_text: str | None = None,
    ) -> None:
        reading = CaptureClockReadingV1.now() if reading is None else reading
        event = CaptureLifecycleEventV1(
            kind=kind.value,
            arrival_utc_nanos=reading.arrival_utc_nanos,
            arrival_monotonic_nanos=reading.arrival_monotonic_nanos,
            detail=detail,
            payload_text=payload_text,
        )
        self._writer_for(utc_day_of_nanos(reading.arrival_utc_nanos)).append_lifecycle(event)

    def _open_coverage(self, at_nanos: int) -> None:
        """Start a coverage window at a positive proof, or leave an open one alone.

        Idempotent on purpose. Bybit can push a first ``snapshot`` before its
        subscribe acknowledgement arrives, and that data is itself proof the
        subscription is live. Re-opening on the later acknowledgement would move
        the window's start forward past records already written and reset their
        count, leaving captured evidence outside every declared window -- which
        is precisely the inconsistency this design exists to refuse.
        """
        if self._coverage_open:
            return
        if at_nanos < self._window_floor_nanos:
            # Windows must be disjoint. A wall reading earlier than the last
            # window's exclusive end is a regression across a reset monitor.
            raise CaptureClockRegressionError("coverage_would_start_inside_a_closed_window")
        if self._pending_gap is not None and self._writer is not None:
            start, kind, detail = self._pending_gap
            self._writer.declare_gap(
                CaptureGapV1(start_utc_nanos=start, end_utc_nanos=at_nanos, kind=kind, detail=detail)
            )
            self._pending_gap = None
        self._coverage_open = True
        self._coverage_start_nanos = at_nanos
        self._coverage_last_proven_nanos = at_nanos
        self._coverage_records = 0

    def _close_coverage(self, end_proof: str, detail: str | None = None) -> None:
        """Declare the open window, ending at its last positive proof.

        The window's exclusive end is ``last_proven + 1`` by construction, so
        the last record is inside it and nothing after it is claimed. Causes
        the recorder did not choose also open a gap at that exclusive end.
        """
        if not self._coverage_open or self._writer is None:
            return
        if self._coverage_start_nanos is None or self._coverage_last_proven_nanos is None:
            raise FirstPartyCaptureArchiveError("open_coverage_has_no_recorded_start")
        interval = CaptureCoverageIntervalV1(
            start_utc_nanos=self._coverage_start_nanos,
            last_proven_utc_nanos=self._coverage_last_proven_nanos,
            end_proof=end_proof,
            record_count=self._coverage_records,
        )
        self._writer.declare_coverage(interval)
        gap_kind = GAP_KIND_FOR_END_PROOF_V1.get(end_proof)
        if gap_kind is not None:
            self._pending_gap = (interval.end_utc_nanos, gap_kind.value, detail or end_proof)
            self._gaps_recorded += 1
        self._window_floor_nanos = interval.end_utc_nanos
        self._coverage_open = False
        self._coverage_start_nanos = None
        self._coverage_last_proven_nanos = None
        self._coverage_records = 0

    # -- capture ----------------------------------------------------------
    def run(self, *, max_records: int | None = None, max_seconds: float | None = None) -> CaptureHealthV1:
        """Capture until a bound is reached. Bounds exist so a smoke run is finite.

        Passing neither bound records indefinitely, which is the operator mode,
        stopped with Ctrl-C. How the session ended is decided by what actually
        happened, never by the fact that ``finally`` ran: reaching a bound is an
        operator bounded stop, ``KeyboardInterrupt`` an operator interrupt, and
        any other exception an unexpected recorder failure, which is recorded
        and re-raised. A hard crash runs none of this and leaves a PARTIAL
        partition, which claims nothing.
        """
        self._session_id = new_session_id_v1()
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        end_proof = END_PROOF_RECORDER_FAILURE
        failure: BaseException | None = None
        try:
            self._lifecycle(CaptureLifecycleKindV1.SESSION_STARTED, str(self._session_id))
            self._capture_loop(max_records=max_records, deadline=deadline)
            end_proof = END_PROOF_OPERATOR_BOUNDED_STOP
        except KeyboardInterrupt:
            end_proof = END_PROOF_OPERATOR_INTERRUPT
            raise
        except BaseException as error:
            failure = error
            raise
        finally:
            self._finish_session(end_proof, failure)
        return self.health()

    def _capture_loop(self, *, max_records: int | None, deadline: float | None) -> None:
        def bound_reached() -> bool:
            return (deadline is not None and time.monotonic() >= deadline) or (
                max_records is not None and self._records_written >= max_records
            )

        attempt = 0
        while not bound_reached():
            try:
                self._run_one_connection(max_records=max_records, deadline=deadline)
                attempt = 0
            except (BybitPublicWebSocketError, OSError, ssl.SSLError) as error:
                self._connected = False
                self._acknowledged = False
                # Clock and contract failures already closed their window with
                # their own proof; this is a no-op for them.
                end_proof = (
                    END_PROOF_PEER_CLOSED
                    if isinstance(error, BybitPublicPeerClosedError)
                    else END_PROOF_CONNECTION_LOST
                )
                self._close_coverage(end_proof, str(error))
                self._lifecycle(
                    CaptureLifecycleKindV1.CONNECTION_LOST, f"{type(error).__name__}:{error}"
                )
                if bound_reached():
                    break
                backoff = RECONNECT_BACKOFF_SECONDS_V1[
                    min(attempt, len(RECONNECT_BACKOFF_SECONDS_V1) - 1)
                ]
                attempt += 1
                self._reconnects += 1
                self._lifecycle(CaptureLifecycleKindV1.RECONNECT_STARTED, f"backoff={backoff}s")
                # State must not survive the hole: a fresh snapshot after
                # resubscribe is what re-establishes authoritative state.
                self._clock.reset()
                time.sleep(backoff)

    def _finish_session(self, end_proof: str, failure: BaseException | None) -> None:
        self._session_end_proof = end_proof
        if self._writer is None:
            return
        try:
            self._close_coverage(
                end_proof, None if failure is None else f"{type(failure).__name__}:{failure}"
            )
            if failure is not None:
                self._lifecycle(
                    CaptureLifecycleKindV1.RECORDER_FAILED, f"{type(failure).__name__}:{failure}"
                )
            self._lifecycle(
                CaptureLifecycleKindV1.SESSION_CLOSED,
                f"session={self._session_id} end_proof={end_proof}",
            )
            self._finalize_writer()
        except Exception:
            # Finalizing over state an unexpected failure may have damaged is
            # refused rather than forced: the partition stays honestly PARTIAL.
            if self._writer is not None:
                self._writer.close_without_finalizing()
                self._writer = None
            if failure is None:
                raise

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

                if pending and self._handle_subscription_reply(text, pending, reading):
                    continue
                self._record(text, reading)
        finally:
            client.close()
            self._connected = False

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
        if parsed.get("success") is not True:
            raise BybitPublicWebSocketError(f"subscription_rejected:{parsed.get('ret_msg')}")
        self._observe_clock(reading)
        pending.clear()
        self._acknowledged = True
        self._lifecycle(
            CaptureLifecycleKindV1.SUBSCRIPTIONS_ACKNOWLEDGED,
            ",".join(self._contract.topics()),
            reading=reading,
        )
        # Coverage starts at acknowledgement, not at socket open: an accepted
        # TCP connection proves nothing about whether data is flowing.
        self._open_coverage(reading.arrival_utc_nanos)
        self._coverage_last_proven_nanos = reading.arrival_utc_nanos
        return True

    def _observe_clock(self, reading: CaptureClockReadingV1) -> None:
        """Judge one message's clock reading. Every text message passes here once."""
        verdict = self._clock.observe(reading)
        if not verdict.accepted:
            # A regressed clock means this is not one ordered recording.
            reasons = ",".join(verdict.reasons)
            self._close_coverage(END_PROOF_CLOCK_DISCONTINUITY, reasons)
            self._clock_discontinuities += 1
            self._clock.reset()
            self._lifecycle(CaptureLifecycleKindV1.CLOCK_DISCONTINUITY, reasons)
            raise CaptureClockRegressionError("clock_regression_within_session")
        if verdict.discontinuity:
            reasons = ",".join(verdict.reasons)
            self._close_coverage(END_PROOF_CLOCK_DISCONTINUITY, reasons)
            self._clock_discontinuities += 1
            self._lifecycle(CaptureLifecycleKindV1.CLOCK_DISCONTINUITY, reasons, reading=reading)
            # A fresh continuity claim that starts at this reading, so the next
            # message is still compared against something.
            self._clock.reset()
            self._clock.observe(reading)

    @staticmethod
    def _is_positively_identified_control(parsed: Mapping[str, Any]) -> bool:
        """A Bybit operation reply: no topic, a known op, and not a failure."""
        return (
            "topic" not in parsed
            and parsed.get("op") in _CONTROL_OPERATIONS
            and parsed.get("success") is not False
        )

    def _reject(self, text: str, reading: CaptureClockReadingV1, reason: str) -> NoReturn:
        """Account for a refused message, end coverage before it, and drop the connection.

        Nothing that fails the contract may vanish inside a covered window. The
        window closes at its last proof, the refused text is kept verbatim in
        the lifecycle log, and the connection is reset: a missed ``tickers``
        delta would corrupt reconstructed state, and only the snapshot Bybit
        sends on resubscribe restores it.
        """
        self._close_coverage(END_PROOF_CONTRACT_VIOLATION, reason)
        self._contract_violations += 1
        self._rejection_reasons[reason] = self._rejection_reasons.get(reason, 0) + 1
        self._lifecycle(
            CaptureLifecycleKindV1.MESSAGE_REJECTED, reason, reading=reading, payload_text=text
        )
        raise CaptureContractViolationError(reason)

    def _record(self, text: str, reading: CaptureClockReadingV1) -> None:
        self._observe_clock(reading)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            self._reject(text, reading, "unidentified_message_is_not_valid_json")
        if not isinstance(parsed, dict):
            self._reject(text, reading, "unidentified_message_is_not_an_object")
        if "topic" not in parsed:
            if self._is_positively_identified_control(parsed):
                # A pong or subscribe reply is not market data and not evidence.
                return
            self._reject(text, reading, "unidentified_message_is_not_a_known_control_frame")

        try:
            record = build_capture_record_v1(
                contract=self._contract,
                session_id=self._session_id,  # type: ignore[arg-type]
                sequence=self._sequence,
                clock=reading,
                payload_text=text,
            )
        except FirstPartyCaptureArchiveError as error:
            # Topic-bearing traffic is market data. If it fails the contract --
            # a foreign topic, a changed schema, a wrong symbol, an unknown type
            # -- it is a violation to account for, never noise to drop.
            self._reject(text, reading, str(error))

        writer = self._writer_for(utc_day_of_nanos(reading.arrival_utc_nanos))
        self._open_coverage(reading.arrival_utc_nanos)
        writer.append_record(record)
        self._sequence += 1
        self._records_written += 1
        self._coverage_records += 1
        self._coverage_last_proven_nanos = reading.arrival_utc_nanos
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
    "BybitPublicPeerClosedError",
    "BybitPublicWebSocketError",
    "CaptureClockRegressionError",
    "CaptureContractViolationError",
    "CaptureHealthV1",
    "iter_health_lines",
]
