"""Module 3J.2b.1 -- dataset-bound tradable-bar research evidence (read-only).

A read-only research boundary over the EXISTING sealed historical authority
(``historical_dataset_versions`` -> ``historical_dataset_members`` ->
``historical_normalized_observations`` -> ``historical_raw_observations``).
It is not another bar store: it creates no table, and it deliberately does
not read ``PostgresHistoricalBarStore`` (Module 3F) -- that store's
provenance is an asserted dataset tag, not derived sealed-dataset membership,
which this module's proposal (docs/MODULE_3J2B_PROPOSAL_CRYPTO_PERPETUAL_STRATEGY_RESEARCH.md
Sec 6.1) explicitly rejects as canonical provenance for this research path.

Bar timestamp contract (Sec 4 of the proposal). A bar's raw payload must carry
an explicit, immutable marker naming its timestamp semantics --
``BAR_TIMESTAMP_SEMANTICS_MARKER_V1``. For a marked bar:

    bar_open_at  = raw.event_at
    bar_close_at = raw.effective_at
    bar_close_at &gt; bar_open_at
    raw.ingested_at &gt;= bar_close_at
    normalized.normalized_at &gt;= raw.ingested_at

The marker lives only in the raw payload (never in ``normalized_value``,
which ``normalize_payload()`` does not copy it into) -- its own raw-payload
SHA-256 is already folded into sealed-dataset content-hash identity by
``seal_dataset()`` for every observation kind, typed or not, so no schema
change is needed to make the marker part of dataset identity.

v1 interval support is deliberately bounded to ``1m`` only (Sec 4): a bar
whose interval is not ``1m`` is treated as evidence this reader does not yet
support, not as a data-integrity failure -- ``series()`` returns an empty
series for it, matching this module's general "no matching evidence -&gt;
unavailable" convention (see below).

Two distinct failure modes are deliberately not conflated:

- **Unavailable** (empty ``AuthoritativeTradableBarSeriesV2.bars``): the
  dataset/instrument/interval/kind combination simply has no matching sealed
  evidence yet, the dataset is not sealed, the dataset was not yet sealed as
  of the caller's ``research_run_at`` cutoff, or the instrument is not a
  crypto ``PERPETUAL``. This is the "no such evidence" outcome, mirroring
  ``PostgresHistoricalMarketDataPipeline.research_query()``'s existing
  convention of returning an empty tuple rather than raising when nothing
  matches, and ``crypto_derivatives_features.py``'s convention of returning
  ``None`` rather than raising when an eligibility gate is not met.
- **Fail closed** (``TradableBarEvidenceV2Error`` raised): matching evidence
  exists but cannot be trusted as-is -- a missing/malformed/inconsistent
  timestamp-semantics marker, an impossible bar timestamp relationship, or
  more than one independent provider identifier surviving revision
  resolution for the same bar (ambiguous identity; no arbitrary tie-break by
  UUID, insertion order or symbol is performed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from uuid import UUID

from .crypto_instruments import (
    CryptoInstrumentError,
    CryptoInstrumentKind,
    PostgresCryptoInstrumentAuthority,
)
from .persistence import PostgresDatabase

#: The sole bar-timestamp-semantics contract this reader recognizes in v1.
#: ``event_at`` is the bar's open instant, ``effective_at`` is its close
#: instant. Absent, malformed or any other value fails closed.
BAR_TIMESTAMP_SEMANTICS_MARKER_V1 = "EVENT_AT_BAR_OPEN_EFFECTIVE_AT_BAR_CLOSE_V1"

#: v1 interval support is deliberately bounded to this single value.
_SUPPORTED_INTERVAL = "1m"
_SUPPORTED_INTERVAL_WIDTH = timedelta(minutes=1)

_OHLCV_VALUE_KEYS = ("open", "high", "low", "close", "volume")


class TradableBarEvidenceV2Error(ValueError):
    """Raised only for untrustworthy matching evidence, never for absent evidence."""


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TradableBarEvidenceV2Error(f"{field_name}_must_be_timezone_aware")


def _decimal(payload: dict[str, object], key: str) -> Decimal:
    raw = payload.get(key)
    if raw is None:
        raise TradableBarEvidenceV2Error(f"missing_bar_value:{key}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as error:
        raise TradableBarEvidenceV2Error(f"invalid_bar_value:{key}") from error
    if not value.is_finite():
        raise TradableBarEvidenceV2Error(f"non_finite_bar_value:{key}")
    return value


@dataclass(frozen=True, slots=True)
class AuthoritativeTradableBarV2:
    """One PIT-provenanced tradable OHLCV bar, projected for research use only."""

    dataset_version_id: UUID
    dataset_content_hash: str
    source_id: UUID
    normalized_observation_id: UUID
    raw_observation_id: UUID
    raw_payload_sha256: str
    instrument_id: str
    interval: str
    bar_open_at: datetime
    bar_close_at: datetime
    normalized_at: datetime
    revision: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    provenance_uri: str


@dataclass(frozen=True, slots=True)
class AuthoritativeTradableBarSeriesV2:
    """A dataset-bound, single-instrument, single-interval, chronological bar series."""

    dataset_version_id: UUID
    instrument_id: str
    interval: str
    bars: tuple[AuthoritativeTradableBarV2, ...]

    def validate(self) -> None:
        if not self.instrument_id.strip():
            raise TradableBarEvidenceV2Error("tradable_bar_series_instrument_missing")
        previous: datetime | None = None
        for bar in self.bars:
            if bar.dataset_version_id != self.dataset_version_id:
                raise TradableBarEvidenceV2Error("tradable_bar_dataset_mismatch")
            if bar.instrument_id != self.instrument_id:
                raise TradableBarEvidenceV2Error("tradable_bar_instrument_mismatch")
            if bar.interval != self.interval:
                raise TradableBarEvidenceV2Error("tradable_bar_interval_mismatch")
            if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                raise TradableBarEvidenceV2Error("non_positive_bar_price")
            if bar.volume < 0:
                raise TradableBarEvidenceV2Error("negative_bar_volume")
            if previous is not None and bar.bar_open_at <= previous:
                raise TradableBarEvidenceV2Error("tradable_bars_not_chronological")
            previous = bar.bar_open_at

    def first_eligible_bar_after(self, decision_at: datetime) -> AuthoritativeTradableBarV2 | None:
        """The earliest bar whose ``bar_open_at`` is strictly after ``decision_at``.

        Never ``&gt;=``: a bar opening at exactly ``decision_at`` is not eligible.
        This is a temporal-selection primitive only -- it does not create a
        trade or strategy decision; 3J.2b.2 owns entry/exit selection.
        """
        _aware(decision_at, "decision_at")
        candidates = [bar for bar in self.bars if bar.bar_open_at > decision_at]
        if not candidates:
            return None
        earliest = min(bar.bar_open_at for bar in candidates)
        tied = [bar for bar in candidates if bar.bar_open_at == earliest]
        if len(tied) > 1:
            raise TradableBarEvidenceV2Error("ambiguous_first_eligible_bar")
        return tied[0]


class TradableBarEvidenceReaderV2(Protocol):
    def series(
        self,
        *,
        dataset_version_id: UUID,
        instrument_id: str,
        interval: str,
        research_run_at: datetime,
    ) -> AuthoritativeTradableBarSeriesV2: ...


class PostgresTradableBarEvidenceReaderV2:
    """Derives tradable-bar evidence strictly from sealed dataset membership."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._crypto = PostgresCryptoInstrumentAuthority(database)

    def series(
        self,
        *,
        dataset_version_id: UUID,
        instrument_id: str,
        interval: str,
        research_run_at: datetime,
    ) -> AuthoritativeTradableBarSeriesV2:
        _aware(research_run_at, "research_run_at")
        empty = AuthoritativeTradableBarSeriesV2(dataset_version_id, instrument_id, interval, ())
        if interval != _SUPPORTED_INTERVAL:
            return empty

        dataset = self._dataset(dataset_version_id)
        if dataset is None:
            return empty
        source_id, dataset_content_hash, dataset_created_at, status = dataset
        if status != "SEALED" or dataset_created_at > research_run_at:
            return empty

        try:
            specification = self._crypto.get_specification(instrument_id, known_at=dataset_created_at)
        except CryptoInstrumentError:
            return empty
        if specification.kind is not CryptoInstrumentKind.PERPETUAL:
            return empty

        rows = self._bar_rows(dataset_version_id, source_id, instrument_id, interval)
        resolved = _resolve_revisions(rows)
        bars = tuple(
            sorted(
                (
                    _build_bar(
                        row,
                        dataset_version_id=dataset_version_id,
                        dataset_content_hash=dataset_content_hash,
                        instrument_id=instrument_id,
                        interval=interval,
                    )
                    for row in resolved
                ),
                key=lambda bar: bar.bar_open_at,
            )
        )
        series = AuthoritativeTradableBarSeriesV2(dataset_version_id, instrument_id, interval, bars)
        series.validate()
        return series

    def _dataset(self, dataset_version_id: UUID) -> tuple[UUID, str, datetime, str] | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_id, content_hash, created_at, status "
                "FROM historical_dataset_versions WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return (UUID(str(row[0])), str(row[1]), cast(datetime, row[2]), str(row[3]))

    def _bar_rows(
        self, dataset_version_id: UUID, source_id: UUID, instrument_id: str, interval: str
    ) -> list[tuple[object, ...]]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT n.normalized_observation_id, n.raw_observation_id, n.normalized_value, "
                "n.normalized_at, r.source_id, r.provider_identifier, r.event_at, r.effective_at, "
                "r.ingested_at, r.revision, r.provenance_uri, r.raw_payload, r.raw_payload_sha256 "
                "FROM historical_dataset_members m "
                "JOIN historical_normalized_observations n "
                "  ON n.normalized_observation_id=m.normalized_observation_id "
                "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                "WHERE m.dataset_version_id=%s AND n.instrument_id=%s "
                "AND r.observation_kind='OHLCV' AND r.source_id=%s "
                "AND n.quality_status='VALIDATED' AND n.normalized_value->>'interval'=%s",
                (dataset_version_id, instrument_id, source_id, interval),
            )
            return list(cursor.fetchall())


def _resolve_revisions(rows: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    """Per (source_id, provider_identifier, event_at): highest revision, then latest ingested_at.

    Mirrors ``PostgresHistoricalMarketDataPipeline.research_query()``'s exact
    precedence rule. After resolving revisions, more than one distinct
    provider identifier surviving for the same ``event_at`` (bar-open
    timestamp) is a structurally different problem -- no canonical authority
    proves which one is the bar -- and fails closed rather than picking one.
    """
    best_by_provider: dict[tuple[object, object, object], tuple[object, ...]] = {}
    for row in rows:
        key = (row[4], row[5], row[6])
        current = best_by_provider.get(key)
        if current is None or (row[9], row[8]) > (current[9], current[8]):
            best_by_provider[key] = row
    survivors_by_event: dict[object, list[tuple[object, ...]]] = {}
    for row in best_by_provider.values():
        survivors_by_event.setdefault(row[6], []).append(row)
    resolved: list[tuple[object, ...]] = []
    for event_at, survivors in survivors_by_event.items():
        distinct_providers = {survivor[5] for survivor in survivors}
        if len(distinct_providers) > 1:
            raise TradableBarEvidenceV2Error(f"ambiguous_provider_identity_for_bar:{event_at}")
        resolved.append(survivors[0])
    return resolved


def _build_bar(
    row: tuple[object, ...],
    *,
    dataset_version_id: UUID,
    dataset_content_hash: str,
    instrument_id: str,
    interval: str,
) -> AuthoritativeTradableBarV2:
    (
        normalized_observation_id,
        raw_observation_id,
        normalized_value,
        normalized_at,
        source_id,
        _provider_identifier,
        event_at,
        effective_at,
        ingested_at,
        revision,
        provenance_uri,
        raw_payload,
        raw_payload_sha256,
    ) = row
    bar_open_at = cast(datetime, event_at)
    bar_close_at = cast(datetime, effective_at)
    ingested = cast(datetime, ingested_at)
    normalized_at_value = cast(datetime, normalized_at)

    payload = cast(dict[str, object], raw_payload)
    if payload.get("bar_timestamp_semantics") != BAR_TIMESTAMP_SEMANTICS_MARKER_V1:
        raise TradableBarEvidenceV2Error("missing_or_malformed_bar_timestamp_semantics_marker")
    if bar_close_at <= bar_open_at:
        raise TradableBarEvidenceV2Error("bar_close_not_after_bar_open")
    if ingested < bar_close_at:
        raise TradableBarEvidenceV2Error("bar_ingested_before_close")
    if normalized_at_value < ingested:
        raise TradableBarEvidenceV2Error("bar_normalized_before_ingested")
    if interval == _SUPPORTED_INTERVAL and bar_close_at - bar_open_at != _SUPPORTED_INTERVAL_WIDTH:
        raise TradableBarEvidenceV2Error("invalid_bar_interval_duration")

    values = cast(dict[str, object], normalized_value)
    ohlcv = {key: _decimal(values, key) for key in _OHLCV_VALUE_KEYS}

    return AuthoritativeTradableBarV2(
        dataset_version_id=dataset_version_id,
        dataset_content_hash=dataset_content_hash,
        source_id=UUID(str(source_id)),
        normalized_observation_id=UUID(str(normalized_observation_id)),
        raw_observation_id=UUID(str(raw_observation_id)),
        raw_payload_sha256=str(raw_payload_sha256),
        instrument_id=instrument_id,
        interval=interval,
        bar_open_at=bar_open_at,
        bar_close_at=bar_close_at,
        normalized_at=normalized_at_value,
        revision=int(cast(int, revision)),
        open=ohlcv["open"],
        high=ohlcv["high"],
        low=ohlcv["low"],
        close=ohlcv["close"],
        volume=ohlcv["volume"],
        provenance_uri=str(provenance_uri),
    )
