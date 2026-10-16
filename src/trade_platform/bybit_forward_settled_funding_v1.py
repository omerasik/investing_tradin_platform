"""OR-6 F3 forward -- Bybit-*published* settled funding observed going forward, with knowledge times.

``RESEARCH_ONLY``. Forward paper incubation needs funding evidence whose
observation time is defensible. :mod:`~trade_platform.bybit_funding_history_v1`
is the retrospective T2 path behind the holdout gate (``T2_EVENT_TIME``, no
knowledge time); this module is a separate source contract with its own
identity namespace for the same public record kind observed *forward*:
``/v5/market/funding/history`` (``category=linear``), each response bound to
the host instant it arrived at and an explicit observation-clock bound.

Authorization (fail closed)
---------------------------
No roadmap entry or owner decision authorizes forward funding acquisition, so
:func:`resolve_forward_funding_acquisition_authorization_v1` resolves to
``UNRESOLVED_OWNER_AUTHORIZATION`` -- the only state v1 can represent -- and
:func:`acquire_forward_settled_funding_v1` refuses before anything else. This
module performs no network call and imports no HTTP client. A registry-issued
:class:`~trade_platform.strategy_lab_validation_v1.HoldoutOpeningV1` is refused
explicitly: it admits holdout days to the historical path and is never
authority to fetch forward data. The historical holdout gate is untouched.

What is published and what is calculated
----------------------------------------
Published (provider, untrusted, strictly parsed by the historical parser): the
settled ``(fundingRateTimestamp, fundingRate)`` records and the response's
server ``time``. Calculated here: the knowledge instant, coverage, and
completeness. The T4 ``tickers`` funding fields (``fundingRate`` with
``nextFundingTime``, ``fundingIntervalHour``, ``fundingCap``) are the venue's
*indicative* state for the current interval: :func:`classify_bybit_funding_payload_v1`
recognises them and an observation built from them is refused -- an indicative
rate is never settled evidence.

Two clocks
----------
* Event time: the venue settlement instant ``fundingRateTimestamp``.
* Knowledge time: ``ceil_us(host arrival) + ceil_us(max(0, venue-minus-host
  upper bound))`` -- the T4 doctrine of
  :func:`~trade_platform.first_party_t4_normalization_v1.market_knowledge_micros_v1`.
  The bound is never assumed: the host clock is known to drift (W32Time was
  stopped), so an observation without :class:`ObservationClockBoundV1` evidence
  is stored but ``NOT_AVAILABLE`` at every instant. A settlement or server time
  later than the knowledge bound contradicts the bound and is refused.

Identity and immutability
-------------------------
``content_hash`` is the published content only (source contract, canonical
symbol, request window, every ``(settlement instant, exact rate text)``), so two
observations of the same records share it. ``provenance`` (raw-body SHA-256,
URL, server time, host arrival, clock bound, knowledge instant) is hashed
separately, and the ``idempotency_key`` binds both: re-appending one observation
is a no-op, a different observation is a new record. :class:`ForwardSettledFundingStoreV1`
is append-only (create-exclusive files, raw bodies content-addressed) and
re-derives every record from its raw body on load.

As-of windows (:func:`forward_funding_window_as_of_v1`)
-------------------------------------------------------
Only observations whose knowledge instant is at or before ``as_of`` are seen;
nothing about a later observation (not even a count) enters the result, so
later acquisition can never change or back-date an earlier answer. The window
``[start, end)`` is ``COMPLETE`` only if it has closed as of ``as_of``, the
available observations' coverage (request window capped at the published
server time) spans it, a published-interval evidence item is known as of
``as_of``, every spacing equals that interval and both edges are tight.
Anything else is ``IRREGULAR_REFUSED`` with reasons -- never interpolated,
never filled with a constant. Two available observations that publish
different rate text for one instant are refused as a conflict.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid5

from .bybit_funding_history_v1 import (
    COMPLETE,
    FUNDING_HISTORY_SCHEMA_VERSION_V1,
    IRREGULAR_REFUSED,
    PUBLISHED_SEMANTICS_V1,
    FundingEventV1,
    FundingHistoryError,
    funding_history_url_v1,
    parse_funding_page_v1,
)
from .first_party_t4_normalization_v1 import market_knowledge_micros_v1
from .first_party_t4_quotes_v1 import FUNDING_SEMANTICS_V1 as T4_INDICATIVE_FUNDING_SEMANTICS_V1
from .strategy_lab_study_v1 import identity_hash_v1
from .strategy_lab_validation_v1 import HoldoutOpeningV1

FORWARD_FUNDING_SCHEMA_VERSION_V1: Final = "bybit-forward-settled-funding-v1"
FORWARD_FUNDING_PROVENANCE_SCHEMA_VERSION_V1: Final = "bybit-forward-settled-funding-provenance-v1"
FORWARD_FUNDING_WINDOW_SCHEMA_VERSION_V1: Final = "bybit-forward-settled-funding-window-v1"

CANONICAL_SYMBOLS_V1: Final = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

UNRESOLVED_OWNER_AUTHORIZATION: Final = "UNRESOLVED_OWNER_AUTHORIZATION"

SETTLED_FUNDING_HISTORY: Final = "SETTLED_FUNDING_HISTORY"
INDICATIVE_TICKER_FUNDING_STATE: Final = "INDICATIVE_TICKER_FUNDING_STATE"
UNRECOGNIZED_FUNDING_PAYLOAD: Final = "UNRECOGNIZED_FUNDING_PAYLOAD"

AVAILABLE_FROM_KNOWLEDGE_TIME: Final = "AVAILABLE_FROM_KNOWLEDGE_TIME"
NOT_AVAILABLE_CLOCK_UNCERTAINTY_MISSING: Final = "NOT_AVAILABLE_CLOCK_UNCERTAINTY_MISSING"

SOURCE_CONTRACT_V1: Final = {
    "source_id": "bybit-v5-forward-settled-funding-v1",
    "venue": "BYBIT",
    "category": "linear",
    "endpoint": "https://api.bybit.com/v5/market/funding/history",
    "access": "PUBLIC_UNAUTHENTICATED_GET",
    "record_kind": "SETTLED_PUBLISHED",
    "event_time": "VENUE_SETTLEMENT_INSTANT_fundingRateTimestamp",
    "knowledge_time": "CEIL_US_HOST_ARRIVAL_PLUS_CEIL_US_MAX0_VENUE_MINUS_HOST_UPPER_BOUND",
    "missing_clock_bound": "NOT_AVAILABLE",
    "coverage": "REQUEST_WINDOW_CAPPED_AT_PUBLISHED_SERVER_TIME",
    "evidence_tier": "FORWARD_OBSERVED_SETTLED",
    "published_semantics": PUBLISHED_SEMANTICS_V1,
    "distinct_from": [FUNDING_HISTORY_SCHEMA_VERSION_V1, T4_INDICATIVE_FUNDING_SEMANTICS_V1],
}
SOURCE_CONTRACT_HASH_V1: Final = identity_hash_v1(SOURCE_CONTRACT_V1)

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.bybit_forward_settled_funding_v1")
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_HOUR_MS: Final = 3_600_000
# Field names only the tickers stream (and its T4_FUNDING frame) carries: indicative state.
_INDICATIVE_KEYS: Final = frozenset({"nextFundingTime", "fundingIntervalHour", "fundingCap",
                                     "next_funding_at", "published_funding_rate"})


class ForwardFundingError(ValueError):
    """Raised when forward funding evidence is unauthorized, malformed, contradictory or not provable."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _iso_ms(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()


def _strict_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForwardFundingError(f"{name}_must_be_an_integer")
    return value


# ---------------------------------------------------------------------------
# Canonical symbol and authorization
# ---------------------------------------------------------------------------


def canonical_symbol_identity_v1(symbol: str) -> dict[str, str]:
    """The canonical identity of one in-universe Bybit linear perpetual; anything else is refused."""
    if symbol not in CANONICAL_SYMBOLS_V1:
        raise ForwardFundingError("forward_funding_symbol_outside_the_universe")
    return {"venue": "BYBIT", "category": "linear", "contract": "LINEAR_PERPETUAL", "symbol": symbol,
            "canonical_key": f"BYBIT:linear:{symbol}"}


@dataclass(frozen=True, slots=True)
class ForwardFundingAcquisitionAuthorizationV1:
    """The resolved authorization for forward acquisition. v1 can only represent the unresolved state."""

    state: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state != UNRESOLVED_OWNER_AUTHORIZATION:
            raise ForwardFundingError("forward_funding_authorization_state_not_defined_in_v1")


def resolve_forward_funding_acquisition_authorization_v1() -> ForwardFundingAcquisitionAuthorizationV1:
    return ForwardFundingAcquisitionAuthorizationV1(UNRESOLVED_OWNER_AUTHORIZATION, (
        "NO_ROADMAP_OR_OWNER_AUTHORIZATION_FOR_FORWARD_FUNDING_ACQUISITION",
        "FORWARD_ACQUISITION_RUNNER_NOT_IMPLEMENTED_IN_V1",
    ))


def require_forward_funding_acquisition_authorized_v1(authorization: object) -> NoReturn:
    """Always refuses in v1; a holdout opening is named as the wrong kind of authority."""
    if isinstance(authorization, HoldoutOpeningV1):
        raise ForwardFundingError("holdout_opening_is_not_forward_funding_authorization")
    if not isinstance(authorization, ForwardFundingAcquisitionAuthorizationV1):
        raise ForwardFundingError("forward_funding_authorization_missing")
    raise ForwardFundingError(f"forward_funding_acquisition_not_authorized:{authorization.state}")


def acquire_forward_settled_funding_v1(*_: object, authorization: object = None, **__: object) -> NoReturn:
    """The forward network runner. Disabled: refuses at the authorization gate before any work."""
    require_forward_funding_acquisition_authorized_v1(authorization)


# ---------------------------------------------------------------------------
# Payload kind and clock evidence
# ---------------------------------------------------------------------------


def _keys(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def classify_bybit_funding_payload_v1(body: bytes) -> str:
    """``SETTLED_FUNDING_HISTORY``, ``INDICATIVE_TICKER_FUNDING_STATE`` or ``UNRECOGNIZED_FUNDING_PAYLOAD``."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return UNRECOGNIZED_FUNDING_PAYLOAD
    keys = set(_keys(payload))
    if keys & _INDICATIVE_KEYS:
        return INDICATIVE_TICKER_FUNDING_STATE
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = result.get("list") if isinstance(result, dict) else None
    if isinstance(rows, list) and all(isinstance(row, dict) and "fundingRateTimestamp" in row for row in rows):
        return SETTLED_FUNDING_HISTORY
    return UNRECOGNIZED_FUNDING_PAYLOAD


@dataclass(frozen=True, slots=True)
class ObservationClockBoundV1:
    """An explicit upper bound on (venue clock - host clock) at arrival, with the evidence behind it."""

    venue_minus_host_upper_bound_nanos: int
    evidence_reference: str

    def __post_init__(self) -> None:
        _strict_int(self.venue_minus_host_upper_bound_nanos, "clock_bound_nanos")
        if not isinstance(self.evidence_reference, str) or not self.evidence_reference.strip():
            raise ForwardFundingError("clock_bound_evidence_reference_missing")


@dataclass(frozen=True, slots=True)
class PublishedFundingIntervalV1:
    """A venue-published funding interval (e.g. a T4 ``fundingIntervalHour``) and when it was known."""

    hours: int
    source_reference: str
    knowledge_at_micros: int

    def __post_init__(self) -> None:
        if _strict_int(self.hours, "published_interval_hours") <= 0:
            raise ForwardFundingError("published_interval_hours_must_be_positive")
        _strict_int(self.knowledge_at_micros, "published_interval_knowledge_at_micros")
        if not isinstance(self.source_reference, str) or not self.source_reference.strip():
            raise ForwardFundingError("published_interval_source_reference_missing")


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForwardFundingObservationV1:
    """One forward response: published content, separate provenance, and the key binding both."""

    identity: Mapping[str, Any]
    content_hash: str
    provenance: Mapping[str, Any]
    provenance_hash: str
    idempotency_key: str

    @property
    def observation_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"observation:{self.idempotency_key}")

    @property
    def symbol(self) -> str:
        return str(self.identity["symbol"]["symbol"])

    @property
    def knowledge_at_micros(self) -> int | None:
        value = self.provenance["knowledge_at_micros"]
        return None if value is None else int(value)

    @property
    def availability(self) -> str:
        return str(self.provenance["availability"])

    @property
    def coverage_ms(self) -> tuple[int, int]:
        """Inclusive ``[first, last]`` ms the response speaks for: its window capped at server time."""
        return int(self.identity["request_start_ms"]), int(self.provenance["coverage_end_ms"])

    def events(self) -> list[FundingEventV1]:
        return [FundingEventV1(int(item["settled_at_ms"]), str(item["rate"])) for item in self.identity["events"]]

    def is_available_at(self, as_of_micros: int) -> bool:
        known = self.knowledge_at_micros
        return known is not None and known <= as_of_micros


def observe_forward_settled_funding_v1(
    body: bytes, *, symbol: str, start_ms: int, end_ms: int, observed_at_host_utc_nanos: int,
    clock_bound: ObservationClockBoundV1 | None,
) -> ForwardFundingObservationV1:
    """Strictly build one observation from a raw response body and the clocks it arrived under."""
    canonical = canonical_symbol_identity_v1(symbol)
    _strict_int(start_ms, "request_start_ms")
    _strict_int(end_ms, "request_end_ms")
    if end_ms < start_ms:
        raise ForwardFundingError("request_window_end_before_start")
    if _strict_int(observed_at_host_utc_nanos, "observed_at_host_utc_nanos") <= 0:
        raise ForwardFundingError("observed_at_host_utc_nanos_must_be_positive")
    if clock_bound is not None and not isinstance(clock_bound, ObservationClockBoundV1):
        raise ForwardFundingError("clock_bound_must_be_observation_clock_bound_evidence")
    kind = classify_bybit_funding_payload_v1(body)
    if kind == INDICATIVE_TICKER_FUNDING_STATE:
        raise ForwardFundingError("indicative_ticker_funding_is_never_settled_evidence")
    if kind != SETTLED_FUNDING_HISTORY:
        raise ForwardFundingError("funding_payload_is_not_settled_history")
    try:
        url = funding_history_url_v1(symbol, start_ms, end_ms)
        events = parse_funding_page_v1(body, symbol=symbol, start_ms=start_ms, end_ms=end_ms)
    except FundingHistoryError as error:
        raise ForwardFundingError(str(error)) from error
    server_time_ms = json.loads(body).get("time")
    if isinstance(server_time_ms, bool) or not isinstance(server_time_ms, int) or server_time_ms <= 0:
        raise ForwardFundingError("funding_server_time_missing_or_malformed")

    knowledge: int | None = None
    if clock_bound is not None:
        knowledge = market_knowledge_micros_v1(observed_at_host_utc_nanos,
                                               clock_bound.venue_minus_host_upper_bound_nanos)
        if server_time_ms * 1000 > knowledge:
            raise ForwardFundingError("server_time_contradicts_the_observation_clock_bound")
        if any(event.settled_at_ms * 1000 > knowledge for event in events):
            raise ForwardFundingError("settlement_after_the_observation_knowledge_bound")
    if any(event.settled_at_ms > server_time_ms for event in events):
        raise ForwardFundingError("settlement_after_the_published_server_time")

    identity = {
        "schema_version": FORWARD_FUNDING_SCHEMA_VERSION_V1,
        "source_contract_hash": SOURCE_CONTRACT_HASH_V1,
        "symbol": canonical,
        "request_start_ms": start_ms,
        "request_end_ms": end_ms,
        "events": [{"settled_at_ms": e.settled_at_ms, "settled_at": _iso_ms(e.settled_at_ms), "rate": e.rate_text}
                   for e in events],
    }
    content_hash = identity_hash_v1(identity)
    provenance = {
        "schema_version": FORWARD_FUNDING_PROVENANCE_SCHEMA_VERSION_V1,
        "content_hash": content_hash,
        "url": url,
        "raw_sha256": _sha256(body),
        "published_server_time_ms": server_time_ms,
        "coverage_end_ms": min(end_ms, server_time_ms),
        "observed_at_host_utc_nanos": observed_at_host_utc_nanos,
        "clock_bound_nanos": None if clock_bound is None else clock_bound.venue_minus_host_upper_bound_nanos,
        "clock_bound_evidence": None if clock_bound is None else clock_bound.evidence_reference,
        "knowledge_at_micros": knowledge,
        "availability": NOT_AVAILABLE_CLOCK_UNCERTAINTY_MISSING if knowledge is None
        else AVAILABLE_FROM_KNOWLEDGE_TIME,
    }
    provenance_hash = identity_hash_v1(provenance)
    key = identity_hash_v1({"schema_version": FORWARD_FUNDING_SCHEMA_VERSION_V1, "content_hash": content_hash,
                            "provenance_hash": provenance_hash})
    return ForwardFundingObservationV1(identity, content_hash, provenance, provenance_hash, key)


# ---------------------------------------------------------------------------
# Append-only store
# ---------------------------------------------------------------------------


def _record(observation: ForwardFundingObservationV1) -> bytes:
    return json.dumps({"identity": observation.identity, "content_hash": observation.content_hash,
                       "provenance": observation.provenance, "provenance_hash": observation.provenance_hash,
                       "idempotency_key": observation.idempotency_key},
                      sort_keys=True, indent=1).encode("utf-8")


def _write_once(path: Path, payload: bytes) -> bool:
    """Create-exclusive write. ``False`` when the identical bytes already exist; differing bytes refuse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ForwardFundingError(f"immutable_record_conflict:{path.name}") from None
        return False
    return True


class ForwardSettledFundingStoreV1:
    """Append-only, file-backed: raw bodies by SHA-256, one create-exclusive record per observation."""

    def __init__(self, root: Path) -> None:
        self._root = root / "forward-settled-funding" / "bybit-v5"

    def _symbol_root(self, symbol: str) -> Path:
        canonical_symbol_identity_v1(symbol)
        return self._root / f"symbol={symbol}"

    def append(self, observation: ForwardFundingObservationV1, raw_body: bytes) -> bool:
        """Store one observation; ``False`` when that exact observation is already stored (idempotent)."""
        if _sha256(raw_body) != observation.provenance["raw_sha256"]:
            raise ForwardFundingError("raw_body_does_not_match_the_observation")
        if not _reproduces(observation, _rebuild(observation, raw_body)):
            raise ForwardFundingError("observation_does_not_reproduce_from_its_raw_body")
        base = self._symbol_root(observation.symbol)
        _write_once(base / "raw" / f"{observation.provenance['raw_sha256']}.json", raw_body)
        return _write_once(base / "observations" / f"{observation.observation_id}.json", _record(observation))

    def load(self, symbol: str) -> list[ForwardFundingObservationV1]:
        """Every stored observation, each re-derived from its raw body; any drift refuses the whole load."""
        folder = self._symbol_root(symbol) / "observations"
        loaded: list[ForwardFundingObservationV1] = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            stored = json.loads(path.read_text(encoding="utf-8"))
            claimed = ForwardFundingObservationV1(stored["identity"], stored["content_hash"], stored["provenance"],
                                                  stored["provenance_hash"], stored["idempotency_key"])
            raw = (self._symbol_root(symbol) / "raw" / f"{claimed.provenance['raw_sha256']}.json").read_bytes()
            if _sha256(raw) != claimed.provenance["raw_sha256"]:
                raise ForwardFundingError("forward_funding_raw_body_changed")
            rebuilt = _rebuild(claimed, raw)
            if not _reproduces(claimed, rebuilt) or path.stem != str(rebuilt.observation_id):
                raise ForwardFundingError("forward_funding_observation_does_not_reproduce")
            loaded.append(rebuilt)
        return loaded


def _reproduces(claimed: ForwardFundingObservationV1, rebuilt: ForwardFundingObservationV1) -> bool:
    """Every field, not only the hashes: a record whose content drifted from its hashes is refused."""
    return (dict(claimed.identity), claimed.content_hash, dict(claimed.provenance), claimed.provenance_hash,
            claimed.idempotency_key) == (dict(rebuilt.identity), rebuilt.content_hash, dict(rebuilt.provenance),
                                         rebuilt.provenance_hash, rebuilt.idempotency_key)


def _rebuild(observation: ForwardFundingObservationV1, raw_body: bytes) -> ForwardFundingObservationV1:
    provenance = observation.provenance
    bound = None
    if provenance["clock_bound_nanos"] is not None:
        bound = ObservationClockBoundV1(int(provenance["clock_bound_nanos"]), str(provenance["clock_bound_evidence"]))
    return observe_forward_settled_funding_v1(
        raw_body, symbol=observation.symbol, start_ms=int(observation.identity["request_start_ms"]),
        end_ms=int(observation.identity["request_end_ms"]),
        observed_at_host_utc_nanos=int(provenance["observed_at_host_utc_nanos"]), clock_bound=bound)


# ---------------------------------------------------------------------------
# As-of window
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForwardFundingWindowV1:
    """What the available forward evidence proves about ``[start, end)`` as of one instant."""

    identity: Mapping[str, Any]
    content_hash: str

    @property
    def status(self) -> str:
        return str(self.identity["completeness"]["status"])

    @property
    def irregularities(self) -> list[str]:
        return list(self.identity["completeness"]["irregularities"])

    def events(self) -> list[FundingEventV1]:
        return [FundingEventV1(int(item["settled_at_ms"]), str(item["rate"])) for item in self.identity["events"]]


def _uncovered(spans: Sequence[tuple[int, int]], start_ms: int, last_ms: int) -> list[str]:
    """Gaps of the inclusive ms range ``[start_ms, last_ms]`` not covered by inclusive ``spans``."""
    gaps: list[str] = []
    cursor = start_ms
    for first, last in sorted(span for span in spans if span[0] <= span[1]):
        if first > cursor:
            gaps.append(f"NOT_COVERED:{_iso_ms(cursor)}->{_iso_ms(min(first, last_ms + 1))}")
        cursor = max(cursor, last + 1)
        if cursor > last_ms:
            return gaps
    if cursor <= last_ms:
        gaps.append(f"NOT_COVERED:{_iso_ms(cursor)}->{_iso_ms(last_ms + 1)}")
    return gaps


def forward_funding_window_as_of_v1(
    observations: Iterable[ForwardFundingObservationV1], *, symbol: str, start_ms: int, end_ms: int,
    as_of_micros: int, published_interval: PublishedFundingIntervalV1 | None,
) -> ForwardFundingWindowV1:
    """Prove ``[start_ms, end_ms)`` from observations known at ``as_of_micros`` only."""
    canonical = canonical_symbol_identity_v1(symbol)
    _strict_int(as_of_micros, "as_of_micros")
    if _strict_int(end_ms, "window_end_ms") <= _strict_int(start_ms, "window_start_ms"):
        raise ForwardFundingError("window_end_not_after_start")
    available = sorted((o for o in observations if o.symbol == symbol and o.is_available_at(as_of_micros)),
                       key=lambda o: str(o.observation_id))
    irregular: list[str] = []
    if end_ms * 1000 > as_of_micros:
        irregular.append("WINDOW_NOT_CLOSED_AS_OF")
    irregular += _uncovered([o.coverage_ms for o in available], start_ms, end_ms - 1)

    rates: dict[int, set[str]] = {}
    for observation in available:
        for event in observation.events():
            if start_ms <= event.settled_at_ms < end_ms:
                rates.setdefault(event.settled_at_ms, set()).add(event.rate_text)
    irregular += [f"SETTLED_RATE_CONFLICT:{_iso_ms(at)}" for at in sorted(rates) if len(rates[at]) > 1]
    events = [FundingEventV1(at, min(rates[at])) for at in sorted(rates) if len(rates[at]) == 1]

    interval_ms: int | None = None
    if published_interval is None:
        irregular.append("PUBLISHED_INTERVAL_EVIDENCE_MISSING")
    elif published_interval.knowledge_at_micros > as_of_micros:
        irregular.append("PUBLISHED_INTERVAL_NOT_KNOWN_AS_OF")
    else:
        interval_ms = published_interval.hours * _HOUR_MS
    if not events:
        irregular.append("NO_RECORD_IN_WINDOW")
    elif len(events) == 1:
        irregular.append("ONE_RECORD_PROVES_NO_INTERVAL")
    if interval_ms is not None and events:
        irregular += [f"SPACING:{_iso_ms(a.settled_at_ms)}->{_iso_ms(b.settled_at_ms)}:{b.settled_at_ms - a.settled_at_ms}"
                      for a, b in pairwise(events) if b.settled_at_ms - a.settled_at_ms != interval_ms]
        if events[0].settled_at_ms - start_ms >= interval_ms:
            irregular.append(f"LEADING_EDGE_NOT_PROVEN:{_iso_ms(events[0].settled_at_ms)}")
        if end_ms - events[-1].settled_at_ms > interval_ms:
            irregular.append(f"TRAILING_EDGE_NOT_PROVEN:{_iso_ms(events[-1].settled_at_ms)}")

    identity = {
        "schema_version": FORWARD_FUNDING_WINDOW_SCHEMA_VERSION_V1,
        "source_contract_hash": SOURCE_CONTRACT_HASH_V1,
        "symbol": canonical,
        "start_ms": start_ms,
        "end_ms_exclusive": end_ms,
        "as_of_micros": as_of_micros,
        "published_interval": None if published_interval is None else {
            "hours": published_interval.hours, "source_reference": published_interval.source_reference,
            "knowledge_at_micros": published_interval.knowledge_at_micros},
        "observations": [str(o.observation_id) for o in available],
        "events": [{"settled_at_ms": e.settled_at_ms, "settled_at": _iso_ms(e.settled_at_ms), "rate": e.rate_text}
                   for e in events],
        "completeness": {"status": IRREGULAR_REFUSED if irregular else COMPLETE,
                         "interval_ms": None if irregular else interval_ms, "irregularities": irregular},
    }
    return ForwardFundingWindowV1(identity, identity_hash_v1(identity))


__all__ = [
    "AVAILABLE_FROM_KNOWLEDGE_TIME",
    "CANONICAL_SYMBOLS_V1",
    "COMPLETE",
    "FORWARD_FUNDING_PROVENANCE_SCHEMA_VERSION_V1",
    "FORWARD_FUNDING_SCHEMA_VERSION_V1",
    "FORWARD_FUNDING_WINDOW_SCHEMA_VERSION_V1",
    "INDICATIVE_TICKER_FUNDING_STATE",
    "IRREGULAR_REFUSED",
    "NOT_AVAILABLE_CLOCK_UNCERTAINTY_MISSING",
    "SETTLED_FUNDING_HISTORY",
    "SOURCE_CONTRACT_HASH_V1",
    "SOURCE_CONTRACT_V1",
    "UNRECOGNIZED_FUNDING_PAYLOAD",
    "UNRESOLVED_OWNER_AUTHORIZATION",
    "ForwardFundingAcquisitionAuthorizationV1",
    "ForwardFundingError",
    "ForwardFundingObservationV1",
    "ForwardFundingWindowV1",
    "ForwardSettledFundingStoreV1",
    "ObservationClockBoundV1",
    "PublishedFundingIntervalV1",
    "acquire_forward_settled_funding_v1",
    "canonical_symbol_identity_v1",
    "classify_bybit_funding_payload_v1",
    "forward_funding_window_as_of_v1",
    "observe_forward_settled_funding_v1",
    "require_forward_funding_acquisition_authorized_v1",
    "resolve_forward_funding_acquisition_authorization_v1",
]
