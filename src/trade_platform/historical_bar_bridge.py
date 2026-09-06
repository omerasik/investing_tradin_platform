"""Bridges ``historical_market_data.py``'s normalized OHLCV observations into the
provider-neutral historical bar authority (``market_data.py``/``postgres_market_data.py``).

One raw capture, one instrument resolution, and one normalization pass now serve both
existing consumers: the sealed-dataset/Feature-Authority research path
(``historical_market_data.py``, unchanged) and the Data-Health-gated internal bar
authority (``PostgresHistoricalBarStore``, Module 3F, unchanged). This module adds no
second raw fetch, no second instrument master, and no second normalization pass -- it
only translates an already-validated result from the first pipeline into the input
shape the second already accepts. It is provider-neutral: any
``AuthorizedHistoricalSource``-backed provider, not only Databento, can use it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .domain import OHLCVBar
from .historical_market_data import (
    HistoricalMarketDataError,
    NormalizedHistoricalObservation,
    ObservationKind,
    QualityStatus,
    RawHistoricalObservation,
)

_SUPPORTED_INTERVALS = frozenset({"1d", "1m"})


class HistoricalBarBridgeError(HistoricalMarketDataError):
    pass


def _bar_decimal(normalized_value: dict[str, object], key: str) -> Decimal:
    value = normalized_value.get(key)
    if not isinstance(value, str):
        raise HistoricalBarBridgeError(f"normalized_ohlcv_missing_{key}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise HistoricalBarBridgeError(f"normalized_ohlcv_invalid_{key}") from error
    if not parsed.is_finite():
        raise HistoricalBarBridgeError(f"normalized_ohlcv_invalid_{key}")
    return parsed


def normalized_ohlcv_to_bar(
    normalized: NormalizedHistoricalObservation, raw: RawHistoricalObservation, provider: str,
) -> OHLCVBar:
    """Translate one VALIDATED, normalized OHLCV observation into an ``OHLCVBar``.

    Fails closed rather than repairing or guessing: refuses anything that has not
    actually cleared normalization (``quality_status`` must be ``VALIDATED``), is not
    an OHLCV observation, does not correspond to the same raw record, carries an
    interval outside this module's approved daily/minute scope, or has a normalized
    price/volume field ``normalize_payload`` did not leave in a parseable state.

    ``ingested_at`` is deliberately the *normalization* timestamp, not the raw
    capture timestamp: a bar is not actually knowable/available-as-of until
    normalization (which can still reject it) has completed, so using the raw
    capture time here would let a decision timestamp between capture and
    normalization incorrectly see a bar that had not yet cleared validation.

    ``original_timezone`` is recorded as ``"UTC"``: unlike a market-local daily-close
    provider (e.g. Stooq), Databento's own ``ts_event`` -- and any other consolidated
    US-equities historical source this bridge might serve -- is natively UTC, so
    that is the honest original timezone, not an assumption.
    """
    if normalized.raw_observation_id != raw.raw_observation_id:
        raise HistoricalBarBridgeError("normalized_and_raw_observation_mismatch")
    if raw.observation_kind is not ObservationKind.OHLCV:
        raise HistoricalBarBridgeError("bar_bridge_requires_ohlcv_observation_kind")
    if normalized.quality_status is not QualityStatus.VALIDATED:
        raise HistoricalBarBridgeError("rejected_normalized_observation_cannot_become_a_bar")
    if not provider.strip():
        raise HistoricalBarBridgeError("bar_bridge_requires_a_provider_name")
    interval = normalized.normalized_value.get("interval")
    if not isinstance(interval, str) or interval not in _SUPPORTED_INTERVALS:
        raise HistoricalBarBridgeError("unsupported_bar_bridge_interval")
    open_price, high, low, close = (
        _bar_decimal(normalized.normalized_value, key) for key in ("open", "high", "low", "close")
    )
    volume = _bar_decimal(normalized.normalized_value, "volume")
    return OHLCVBar(
        instrument_id=normalized.instrument_id, interval=interval, event_at=raw.event_at,
        effective_at=raw.effective_at, ingested_at=normalized.normalized_at,
        open=open_price, high=high, low=low, close=close, volume=volume,
        provider=provider, source_identifier=str(raw.raw_observation_id),
        original_timezone="UTC", revision=raw.revision,
        data_version=normalized.normalization_version,
    )
