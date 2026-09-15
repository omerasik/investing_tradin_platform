"""Source-backed onboarding of the Bybit BTCUSDT linear perpetual.

This module turns ONE real, captured Bybit V5 ``instruments-info`` response into
canonical platform identity, venue trading rules and historical-source
authority. It is the crypto counterpart of ``pilot_instruments`` (Module 3G.1e):
real researched records registered through the *existing* authorities --
``PostgresProfessionalInstrumentMaster``, ``PostgresCryptoInstrumentAuthority``
and ``PostgresHistoricalMarketDataPipeline`` -- with no second registry, no
parallel resolution path and no schema change.

Hard scope limits, all deliberate:

* **Nothing here makes a network call.** Not on import, not on object
  construction, not in any test. The single live request that produced
  :data:`CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1` was a one-off development and
  onboarding step, performed by hand against the unauthenticated public
  endpoint. CI consumes only the frozen snapshot committed below.
* **No historical market data is fetched or represented here.** This module
  grants a source the authority to supply OHLCV, mark price, index price and
  open interest later; it supplies none of them itself.
* **No funding convention is registered.** ``instruments-info`` exposes
  ``fundingInterval``, ``upperFundingRate`` and ``lowerFundingRate``, but it
  does NOT expose the canonical first-funding schedule offset that
  ``CryptoFundingConvention.first_funding_offset_hours`` requires. Inventing
  ``0`` there would fabricate a settlement schedule, so those three provider
  values are preserved inside the metadata snapshot only, and no funding
  convention row is written. A real convention comes later, from explicit
  funding-schedule evidence, and funding is deliberately excluded from this
  source's authorized observation kinds.

**Static identity versus venue-revised state.** The existing Module 3H.2
boundary is kept exactly as it is. What makes this instrument *this* instrument
-- venue, perpetual kind, BTC/USDT/USDT assets, linear settlement, launch time
-- goes into ``ProfessionalInstrument`` and ``CryptoInstrumentSpecification``
and never changes. What Bybit revises -- tick size, quantity step, minimum
quantity and minimum notional, and the precisions derived from them -- goes
into ``CryptoVenueTradingRules``, which is versioned with independent effective
and knowledge clocks. A later changed Bybit snapshot must become a new rules
*version*; nothing in this module ever mutates a recorded rule in place.

**Fail closed on semantic loss.** Bybit publishes two distinct maxima --
``maxOrderQty`` (limit orders) and ``maxMktOrderQty`` (market orders) -- while
``CryptoVenueTradingRules`` has a single ``max_quantity``. Those are not the
same concept, and collapsing them would silently assert a limit Bybit never
stated. ``max_quantity`` is therefore left ``None``, and both provider maxima
are retained verbatim in the immutable metadata snapshot. Representing them
properly needs an order-type-aware authority, which is a schema change and is
deliberately out of scope here.

**Knowledge time is never backdated.** ``listing_date``, ``valid_from`` and the
venue rules' ``effective_from`` describe the world. ``registered_at``,
``ingested_at``, ``known_at``, ``authorized_at`` and ``created_at`` describe
when *this platform* learned something, and are always the real onboarding
time -- never ``launchTime`` and never the retrieval time of some earlier
capture. The rules' ``effective_from`` is the retrieval instant, because that
is the only moment at which those limits were actually observed to be in
force; claiming they held since 2020 would assert an unrevised history this
capture cannot evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final, TypeVar
from uuid import UUID, uuid5

from .bybit_crypto_provider import (
    BYBIT_EXCHANGE,
    BYBIT_LINEAR_CATEGORY,
    BYBIT_PROVIDER_NAME,
    BYBIT_V5_SYMBOL_NAMESPACE,
)
from .crypto_instruments import (
    CryptoInstrumentKind,
    CryptoInstrumentSpecification,
    CryptoResolutionError,
    CryptoSettlementType,
    CryptoVenueRuleError,
    CryptoVenueTradingRules,
    PostgresCryptoInstrumentAuthority,
    ReferencePriceRequirement,
    SettlementStyle,
)
from .domain import AssetClass
from .historical_market_data import (
    AssetScope,
    AuthorizedHistoricalSource,
    ObservationKind,
    PostgresHistoricalMarketDataPipeline,
)
from .persistence import PostgresDatabase
from .professional_instruments import (
    IdentifierMapping,
    IdentifierSourceKind,
    InstrumentResolutionError,
    InstrumentType,
    LifecycleStatus,
    PostgresProfessionalInstrumentMaster,
    ProfessionalInstrument,
    RepresentationKind,
    SessionType,
    SymbolMapping,
)

_T = TypeVar("_T")

BYBIT_INSTRUMENTS_INFO_PATH: Final = "/v5/market/instruments-info"

#: Canonical platform identity for this contract. Venue is part of the identity:
#: ``BTCUSDT`` on Bybit and ``BTCUSDT`` elsewhere are different instruments with
#: different order books and different counterparty risk.
BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID: Final = "CRYPTO:BYBIT:BTCUSDT:PERP"
BYBIT_BTCUSDT_SYMBOL: Final = "BTCUSDT"
BYBIT_EXCHANGE_NAME: Final = "Bybit"

#: The exact kinds this pilot source may supply. Funding is deliberately absent:
#: see the module docstring. ``max``/``min`` order sizes are instrument metadata,
#: not observations, and appear nowhere in this set.
BYBIT_AUTHORIZED_OBSERVATION_KINDS: Final[frozenset[ObservationKind]] = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)

BYBIT_DATASET_NAME: Final = "bybit_v5_public_market_linear"

#: Not a Bybit-published document version. Bybit publishes no versioned terms
#: identifier for its unauthenticated public market endpoints, so this states
#: exactly what it is: an operator declaration, honest about its own basis.
BYBIT_PROVIDER_TERMS_VERSION: Final = (
    "operator-declared:bybit-v5-public-market-endpoints:unauthenticated:v1"
)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

#: Fixed namespace for the deterministic identifiers this onboarding writes.
#: Derived from stable identity (instrument, role) and never from the evidence
#: hash, so re-running the same onboarding addresses the same rows, while a
#: *different* snapshot collides on the primary key instead of quietly creating
#: a second, competing authority record.
_ONBOARDING_NAMESPACE: Final = UUID("6f2a1d0c-9b3e-5a47-8c21-7d4e0b9f13a5")

_EXPECTED_CONTRACT_TYPE: Final = "LinearPerpetual"
_EXPECTED_STATUS: Final = "Trading"
_EXPECTED_BASE_COIN: Final = "BTC"
_EXPECTED_QUOTE_COIN: Final = "USDT"
_EXPECTED_SETTLE_COIN: Final = "USDT"


def _bybit_source_id() -> UUID:
    """The one deterministic ``historical_data_sources`` id this module ever
    addresses. Shared by the builder and the existing-state reader so both
    always agree on which row they mean."""
    return uuid5(_ONBOARDING_NAMESPACE, f"source:{BYBIT_PROVIDER_NAME}:{BYBIT_DATASET_NAME}")


class BybitInstrumentOnboardingError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


class BybitInstrumentMetadataError(BybitInstrumentOnboardingError):
    """The captured provider response does not prove what onboarding requires."""


class BybitOnboardingConflictError(BybitInstrumentOnboardingError):
    """This instrument is already onboarded under *different* evidence."""


# --------------------------------------------------------------------------
# Captured live evidence
# --------------------------------------------------------------------------
#
# Retrieved once, by hand, on 2026-09-14T21:44:22.040394+00:00 UTC:
#
#     GET https://api.bybit.com/v5/market/instruments-info
#         ?category=linear&symbol=BTCUSDT
#
# HTTP 200, retCode 0 ("OK"), result.category "linear", result.list length 1.
# No credentials were used and no other endpoint was contacted. The object
# below is that response's single ``result.list`` entry, verbatim -- not a
# retyped approximation -- and every production value in this module is derived
# from it by :func:`parse_bybit_instrument_metadata`.

CAPTURED_BTCUSDT_REQUEST_URL: Final = (
    "https://api.bybit.com/v5/market/instruments-info?category=linear&symbol=BTCUSDT"
)
CAPTURED_BTCUSDT_HTTP_STATUS: Final = 200
CAPTURED_BTCUSDT_RETRIEVED_AT: Final = datetime(
    2026, 9, 14, 21, 44, 22, 40394, tzinfo=UTC
)
#: Bybit's own ``time`` envelope field from that response (milliseconds).
CAPTURED_BTCUSDT_PROVIDER_RESPONSE_TIME_MS: Final = 1789422263085

CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1: Final[dict[str, object]] = {
    "baseCoin": "BTC",
    "contractType": "LinearPerpetual",
    "copyTrading": "both",
    "deliveryFeeRate": "",
    "deliveryTime": "0",
    "displayName": "BTCUSDT",
    "forbidUplWithdrawal": False,
    "fullName": "Bitcoin",
    "fundingInterval": 480,
    "isPreListing": False,
    "launchTime": "1584230400000",
    "leverageFilter": {
        "leverageStep": "0.01",
        "maxLeverage": "150.00",
        "minLeverage": "1",
    },
    "lotSizeFilter": {
        "maxMktOrderQty": "150.000",
        "maxOrderQty": "1500.000",
        "minNotionalValue": "5",
        "minOrderQty": "0.001",
        "postOnlyMaxOrderQty": "1500.000",
        "qtyStep": "0.001",
    },
    "lowerFundingRate": "-0.00333",
    "marketRegion": "",
    "preListingInfo": None,
    "priceFilter": {
        "maxPrice": "1999999.80",
        "minPrice": "0.10",
        "tickSize": "0.10",
    },
    "priceScale": "2",
    "quoteCoin": "USDT",
    "riskParameters": {"priceLimitRatioX": "0.01", "priceLimitRatioY": "0.02"},
    "settleCoin": "USDT",
    "status": "Trading",
    "symbol": "BTCUSDT",
    "symbolId": 5,
    "symbolType": "",
    "underlyingTicker": "",
    "unifiedMarginTrade": True,
    "upperFundingRate": "0.00333",
}

#: SHA-256 of the canonical JSON of the object above, recorded at capture time.
#: Asserted on every parse, so a silent edit to the committed payload fails the
#: build rather than quietly re-basing this platform's source-backed evidence.
CAPTURED_BTCUSDT_PAYLOAD_SHA256: Final = (
    # Content hash of a public market-data response; carries no credential.
    "d89138353bea2b64b54164f42d97954c3dafdda3f779a61aab78670606c9f126"  # pragma: allowlist secret
)


def canonical_payload_hash(payload: dict[str, object]) -> str:
    """Deterministic content hash of one provider instrument object.

    Key order, separators and non-finite rejection are all pinned, so the hash
    depends on the provider's values alone -- never on dict insertion order, on
    when or where the capture happened, or on any local filesystem path.
    """
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise BybitInstrumentMetadataError("bybit_payload_not_canonical_json") from error
    return hashlib.sha256(canonical.encode()).hexdigest()


def _source_reference(payload_hash: str) -> str:
    """Provenance string carried by every record this onboarding writes.

    Names the exact endpoint, category and symbol, and binds the record to the
    exact snapshot it came from. Contains no local path and no wall-clock value,
    so it is stable across machines and runs.
    """
    return (
        f"bybit:v5:instruments-info:{BYBIT_LINEAR_CATEGORY}:"
        f"{BYBIT_BTCUSDT_SYMBOL}:sha256={payload_hash}"
    )


@dataclass(frozen=True, slots=True)
class BybitInstrumentMetadataSnapshotV1:
    """One immutable Bybit ``instruments-info`` observation, kept for audit.

    Every provider value needed to justify the records this onboarding writes --
    plus the two order-type-specific maxima and the three funding fields that
    deliberately do NOT reach any authority in this phase -- is preserved here
    exactly as Bybit reported it.
    """

    symbol: str
    contract_type: str
    status: str
    base_coin: str
    quote_coin: str
    settle_coin: str
    launch_time: datetime
    delivery_time: int
    tick_size: Decimal
    price_scale: int
    min_order_qty: Decimal
    qty_step: Decimal
    min_notional_value: Decimal
    max_order_qty: Decimal
    max_market_order_qty: Decimal
    funding_interval_minutes: int
    lower_funding_rate: Decimal
    upper_funding_rate: Decimal
    provider_response_time: datetime
    retrieved_at: datetime
    canonical_payload_hash: str
    source_reference: str

    @property
    def price_precision(self) -> int:
        """Decimal places implied by the exact ``tickSize`` representation."""
        return _step_precision(self.tick_size)

    @property
    def quantity_precision(self) -> int:
        """Decimal places implied by the exact ``qtyStep`` representation."""
        return _step_precision(self.qty_step)

    @property
    def listing_date(self) -> date:
        return self.launch_time.astimezone(UTC).date()


def _step_precision(step: Decimal) -> int:
    """Decimal places of a venue step, read off its exact representation.

    ``Decimal("0.10")`` carries exponent ``-2`` and therefore two decimal
    places; float arithmetic is never involved, because ``0.1`` is not exactly
    representable in binary and any float round-trip here would eventually
    produce a wrong precision for some venue step.
    """
    exponent = step.as_tuple().exponent
    if not isinstance(exponent, int):
        raise BybitInstrumentMetadataError("bybit_step_is_not_a_finite_decimal")
    return max(0, -exponent)


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BybitInstrumentMetadataError(f"bybit_unexpected_{name}_shape")
    return value


def _require_str(container: dict[str, object], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BybitInstrumentMetadataError(f"bybit_missing_or_invalid_field:{key}")
    return value


def _require_int(container: dict[str, object], key: str) -> int:
    value = container.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise BybitInstrumentMetadataError(f"bybit_missing_or_invalid_field:{key}")
    return value


def _require_bool(container: dict[str, object], key: str) -> bool:
    value = container.get(key)
    if not isinstance(value, bool):
        raise BybitInstrumentMetadataError(f"bybit_missing_or_invalid_field:{key}")
    return value


def _decimal(container: dict[str, object], key: str) -> Decimal:
    """Parse a provider decimal string, preserving its exact representation."""
    text = _require_str(container, key)
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise BybitInstrumentMetadataError(f"bybit_invalid_decimal:{key}:{text}") from error
    if not parsed.is_finite():
        raise BybitInstrumentMetadataError(f"bybit_invalid_decimal:{key}:{text}")
    return parsed


def _positive_decimal(container: dict[str, object], key: str) -> Decimal:
    parsed = _decimal(container, key)
    if parsed <= 0:
        raise BybitInstrumentMetadataError(f"bybit_non_positive_value:{key}:{parsed}")
    return parsed


def _milliseconds_to_datetime(value: int) -> datetime:
    # Exact integer millisecond arithmetic -- never a float division, which
    # would quietly round a provider timestamp.
    return _EPOCH + timedelta(milliseconds=value)


def _require_equals(actual: str, expected: str, field_name: str) -> str:
    """Reject any deviation. Unexpected provider values are never repaired."""
    if actual != expected:
        raise BybitInstrumentMetadataError(
            f"bybit_unexpected_{field_name}:{actual}!={expected}"
        )
    return actual


def parse_bybit_instrument_metadata(
    envelope: dict[str, object],
    *,
    retrieved_at: datetime,
    expected_payload_hash: str | None = None,
) -> BybitInstrumentMetadataSnapshotV1:
    """Validate one captured ``instruments-info`` envelope into a snapshot.

    Every condition this onboarding depends on is proved here, and any
    deviation raises: a non-zero ``retCode``, a result that is not the linear
    category, a list that is not exactly one entry, a different symbol, a
    contract that is not a trading linear perpetual on BTC/USDT/USDT, a
    pre-listing contract, a dated (non-zero ``deliveryTime``) contract, a
    non-positive launch time, tick size, quantity step, minimum quantity or
    minimum notional, or a ``priceScale`` that disagrees with the decimal
    places ``tickSize`` actually carries.
    """
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise BybitInstrumentMetadataError("retrieved_at_must_be_timezone_aware")

    return_code = envelope.get("retCode")
    if not isinstance(return_code, int) or isinstance(return_code, bool):
        raise BybitInstrumentMetadataError("bybit_unexpected_envelope_shape")
    if return_code != 0:
        message = envelope.get("retMsg")
        raise BybitInstrumentMetadataError(
            f"bybit_provider_error:{return_code}:{message if isinstance(message, str) else ''}"
        )
    provider_response_time_ms = envelope.get("time")
    if (
        not isinstance(provider_response_time_ms, int)
        or isinstance(provider_response_time_ms, bool)
        or provider_response_time_ms <= 0
    ):
        raise BybitInstrumentMetadataError("bybit_unexpected_envelope_shape")

    result = _require_mapping(envelope.get("result"), "result")
    _require_equals(_require_str(result, "category"), BYBIT_LINEAR_CATEGORY, "category")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise BybitInstrumentMetadataError("bybit_unexpected_result_shape")
    if len(rows) != 1:
        raise BybitInstrumentMetadataError(f"bybit_unexpected_list_length:{len(rows)}")

    payload = _require_mapping(rows[0], "instrument")
    payload_hash = canonical_payload_hash(payload)
    if expected_payload_hash is not None and payload_hash != expected_payload_hash:
        raise BybitInstrumentMetadataError(
            f"bybit_payload_hash_mismatch:{payload_hash}!={expected_payload_hash}"
        )

    symbol = _require_equals(
        _require_str(payload, "symbol"), BYBIT_BTCUSDT_SYMBOL, "symbol"
    )
    contract_type = _require_equals(
        _require_str(payload, "contractType"), _EXPECTED_CONTRACT_TYPE, "contract_type"
    )
    status = _require_equals(_require_str(payload, "status"), _EXPECTED_STATUS, "status")
    base_coin = _require_equals(
        _require_str(payload, "baseCoin"), _EXPECTED_BASE_COIN, "base_coin"
    )
    quote_coin = _require_equals(
        _require_str(payload, "quoteCoin"), _EXPECTED_QUOTE_COIN, "quote_coin"
    )
    settle_coin = _require_equals(
        _require_str(payload, "settleCoin"), _EXPECTED_SETTLE_COIN, "settle_coin"
    )

    if _require_bool(payload, "isPreListing"):
        raise BybitInstrumentMetadataError("bybit_contract_is_pre_listing")

    delivery_time_text = _require_str(payload, "deliveryTime")
    if not delivery_time_text.isdigit():
        raise BybitInstrumentMetadataError(
            f"bybit_invalid_delivery_time:{delivery_time_text}"
        )
    delivery_time = int(delivery_time_text)
    if delivery_time != 0:
        # A non-zero delivery time is a dated contract, which is a different
        # instrument kind entirely -- never a perpetual with an odd field.
        raise BybitInstrumentMetadataError(f"bybit_contract_has_delivery:{delivery_time}")

    launch_time_text = _require_str(payload, "launchTime")
    if not launch_time_text.isdigit():
        raise BybitInstrumentMetadataError(f"bybit_invalid_launch_time:{launch_time_text}")
    launch_time_ms = int(launch_time_text)
    if launch_time_ms <= 0:
        raise BybitInstrumentMetadataError(f"bybit_invalid_launch_time:{launch_time_ms}")

    price_filter = _require_mapping(payload.get("priceFilter"), "price_filter")
    lot_size_filter = _require_mapping(payload.get("lotSizeFilter"), "lot_size_filter")

    tick_size = _positive_decimal(price_filter, "tickSize")
    qty_step = _positive_decimal(lot_size_filter, "qtyStep")
    min_order_qty = _positive_decimal(lot_size_filter, "minOrderQty")
    min_notional_value = _positive_decimal(lot_size_filter, "minNotionalValue")
    max_order_qty = _positive_decimal(lot_size_filter, "maxOrderQty")
    max_market_order_qty = _positive_decimal(lot_size_filter, "maxMktOrderQty")

    price_scale_text = _require_str(payload, "priceScale")
    if not price_scale_text.isdigit():
        raise BybitInstrumentMetadataError(f"bybit_invalid_price_scale:{price_scale_text}")
    price_scale = int(price_scale_text)
    if price_scale != _step_precision(tick_size):
        # Bybit states the price scale and the tick size independently. If they
        # disagree we do not understand this contract's price representation,
        # and picking one would silently mis-round every price we ever store.
        raise BybitInstrumentMetadataError(
            f"bybit_price_scale_disagrees_with_tick_size:"
            f"{price_scale}!={_step_precision(tick_size)}"
        )

    funding_interval_minutes = _require_int(payload, "fundingInterval")
    if funding_interval_minutes <= 0:
        raise BybitInstrumentMetadataError(
            f"bybit_invalid_funding_interval:{funding_interval_minutes}"
        )
    lower_funding_rate = _decimal(payload, "lowerFundingRate")
    upper_funding_rate = _decimal(payload, "upperFundingRate")
    if lower_funding_rate > upper_funding_rate:
        raise BybitInstrumentMetadataError("bybit_funding_rate_floor_exceeds_cap")

    return BybitInstrumentMetadataSnapshotV1(
        symbol=symbol,
        contract_type=contract_type,
        status=status,
        base_coin=base_coin,
        quote_coin=quote_coin,
        settle_coin=settle_coin,
        launch_time=_milliseconds_to_datetime(launch_time_ms),
        delivery_time=delivery_time,
        tick_size=tick_size,
        price_scale=price_scale,
        min_order_qty=min_order_qty,
        qty_step=qty_step,
        min_notional_value=min_notional_value,
        max_order_qty=max_order_qty,
        max_market_order_qty=max_market_order_qty,
        funding_interval_minutes=funding_interval_minutes,
        lower_funding_rate=lower_funding_rate,
        upper_funding_rate=upper_funding_rate,
        provider_response_time=_milliseconds_to_datetime(provider_response_time_ms),
        retrieved_at=retrieved_at,
        canonical_payload_hash=payload_hash,
        source_reference=_source_reference(payload_hash),
    )


def captured_btcusdt_envelope_v1() -> dict[str, object]:
    """The captured response envelope, rebuilt around the verbatim payload."""
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "category": BYBIT_LINEAR_CATEGORY,
            "list": [dict(CAPTURED_BTCUSDT_INSTRUMENT_PAYLOAD_V1)],
        },
        "retExtInfo": {},
        "time": CAPTURED_BTCUSDT_PROVIDER_RESPONSE_TIME_MS,
    }


def captured_btcusdt_snapshot_v1() -> BybitInstrumentMetadataSnapshotV1:
    """The source-backed snapshot every production record here derives from."""
    return parse_bybit_instrument_metadata(
        captured_btcusdt_envelope_v1(),
        retrieved_at=CAPTURED_BTCUSDT_RETRIEVED_AT,
        expected_payload_hash=CAPTURED_BTCUSDT_PAYLOAD_SHA256,
    )


# --------------------------------------------------------------------------
# Record builders
# --------------------------------------------------------------------------


def _require_onboarding_time(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> None:
    if onboarded_at.tzinfo is None or onboarded_at.utcoffset() is None:
        raise BybitInstrumentOnboardingError("onboarded_at_must_be_timezone_aware")
    if onboarded_at < snapshot.retrieved_at:
        # Onboarding cannot precede the evidence it is based on.
        raise BybitInstrumentOnboardingError("onboarded_before_metadata_was_retrieved")


def bybit_btcusdt_professional_instrument(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> ProfessionalInstrument:
    """Canonical identity. ``listing_date`` is Bybit's ``launchTime``; the
    ``registered_at`` knowledge clock is the real onboarding time.

    ``tick_size``/``lot_size``/precisions are required non-null columns of the
    pre-existing instrument master and are populated from this same snapshot,
    but the *authoritative, revisable* copy of those venue limits is the
    ``CryptoVenueTradingRules`` row -- that is the record a later Bybit change
    versions, and the one every point-in-time read must consult.
    """
    _require_onboarding_time(snapshot, onboarded_at)
    return ProfessionalInstrument(
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.CRYPTO_PERPETUAL,
        exchange_name=BYBIT_EXCHANGE_NAME,
        venue=BYBIT_EXCHANGE,
        # Bybit is not an ISO 10383 MIC-registered venue for this contract, so
        # no MIC is claimed rather than one invented.
        mic=None,
        canonical_symbol=snapshot.symbol,
        listing_date=snapshot.listing_date,
        base_currency=snapshot.base_coin,
        quote_currency=snapshot.quote_coin,
        settlement_currency=snapshot.settle_coin,
        # Bybit's linear perpetual order ``qty`` is denominated in the base coin
        # itself, so one contract is one BTC and neither a multiplier nor a
        # contract size other than 1 applies. No inverse semantics anywhere.
        contract_multiplier=Decimal(1),
        contract_size=Decimal(1),
        tick_size=snapshot.tick_size,
        lot_size=snapshot.qty_step,
        price_precision=snapshot.price_precision,
        quantity_precision=snapshot.quantity_precision,
        trading_timezone="UTC",
        market_session_type=SessionType.CRYPTO_24X7,
        representation_kind=RepresentationKind.PERPETUAL,
        registered_at=onboarded_at,
        lifecycle_status=LifecycleStatus.ACTIVE,
    )


def bybit_btcusdt_symbol_mapping(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> SymbolMapping:
    """``BTCUSDT`` on Bybit, valid from launch, still open-ended today."""
    _require_onboarding_time(snapshot, onboarded_at)
    return SymbolMapping(
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        venue=BYBIT_EXCHANGE,
        symbol=snapshot.symbol,
        valid_from=snapshot.launch_time,
        valid_until=None,
        ingested_at=onboarded_at,
        source_reference=snapshot.source_reference,
    )


def bybit_btcusdt_identifier_mapping(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> IdentifierMapping:
    """Provider identifier, in the same namespace a Bybit source registers."""
    _require_onboarding_time(snapshot, onboarded_at)
    return IdentifierMapping(
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        source_kind=IdentifierSourceKind.PROVIDER,
        namespace=BYBIT_V5_SYMBOL_NAMESPACE,
        value=snapshot.symbol,
        valid_from=snapshot.launch_time,
        valid_until=None,
        ingested_at=onboarded_at,
        source_reference=snapshot.source_reference,
    )


def bybit_btcusdt_crypto_specification(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> CryptoInstrumentSpecification:
    """Static crypto contract identity. No expiry, no invented index.

    ``index_reference`` stays ``None``: ``instruments-info`` states no index
    methodology, and naming one from general knowledge would fabricate the
    single most consequential piece of a perpetual's reference-price semantics.
    ``MARK_AND_INDEX`` records only that this contract *requires* both -- never
    a value, and never a methodology.
    """
    _require_onboarding_time(snapshot, onboarded_at)
    return CryptoInstrumentSpecification(
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        venue=BYBIT_EXCHANGE,
        kind=CryptoInstrumentKind.PERPETUAL,
        base_asset=snapshot.base_coin,
        quote_asset=snapshot.quote_coin,
        settlement_asset=snapshot.settle_coin,
        settlement_style=SettlementStyle.LINEAR,
        settlement_type=CryptoSettlementType.CASH_SETTLED,
        contract_multiplier=Decimal(1),
        contract_size=Decimal(1),
        expiry_at=None,
        reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
        index_reference=None,
        registered_at=onboarded_at,
        source_reference=snapshot.source_reference,
    )


def bybit_btcusdt_venue_trading_rules(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> CryptoVenueTradingRules:
    """Venue-revised limits, version 1, with both clocks stated honestly.

    ``max_quantity`` is ``None`` on purpose. See the module docstring: Bybit's
    ``maxOrderQty`` and ``maxMktOrderQty`` are order-type-specific and this
    authority has one order-type-agnostic field, so both provider maxima stay
    in the snapshot and neither is promoted into a limit the venue never stated.
    """
    _require_onboarding_time(snapshot, onboarded_at)
    return CryptoVenueTradingRules(
        rule_id=uuid5(
            _ONBOARDING_NAMESPACE,
            f"rules:{BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID}:v1",
        ),
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        rule_version=1,
        tick_size=snapshot.tick_size,
        quantity_step=snapshot.qty_step,
        min_quantity=snapshot.min_order_qty,
        max_quantity=None,
        min_notional=snapshot.min_notional_value,
        price_precision=snapshot.price_precision,
        quantity_precision=snapshot.quantity_precision,
        # The only instant these limits were observed to be in force.
        effective_from=snapshot.retrieved_at,
        known_at=onboarded_at,
        source_reference=snapshot.source_reference,
        source_hash=snapshot.canonical_payload_hash,
    )


def bybit_authorized_historical_source(
    snapshot: BybitInstrumentMetadataSnapshotV1, onboarded_at: datetime
) -> AuthorizedHistoricalSource:
    """Operator-approved public Bybit V5 market-data pilot authority."""
    _require_onboarding_time(snapshot, onboarded_at)
    return AuthorizedHistoricalSource(
        source_id=_bybit_source_id(),
        provider=BYBIT_PROVIDER_NAME,
        dataset_name=BYBIT_DATASET_NAME,
        provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
        provider_terms_version=BYBIT_PROVIDER_TERMS_VERSION,
        authorization_reference=(
            "operator-approved public Bybit V5 market-data pilot authority: "
            "unauthenticated public /v5/market endpoints only (kline, "
            "mark-price-kline, index-price-kline, open-interest); no "
            "credentials, no order or account endpoint, and no funding-rate "
            "authority. Onboarding evidence: GET "
            f"{CAPTURED_BTCUSDT_REQUEST_URL} -> {snapshot.source_reference}"
        ),
        asset_scope=AssetScope.CRYPTO.value,
        authorized_at=onboarded_at,
        created_at=onboarded_at,
        authorized_observation_kinds=BYBIT_AUTHORIZED_OBSERVATION_KINDS,
    )


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BybitInstrumentOnboardingResult:
    """What one onboarding call actually did."""

    instrument_id: str
    source_id: UUID
    canonical_payload_hash: str
    #: ``True`` when this exact instrument was already onboarded under this
    #: exact evidence and the call was therefore a deterministic no-op.
    already_onboarded: bool


def onboard_bybit_btcusdt_perpetual_v1(
    database: PostgresDatabase,
    snapshot: BybitInstrumentMetadataSnapshotV1,
    onboarded_at: datetime,
) -> BybitInstrumentOnboardingResult:
    """Register Bybit BTCUSDT through the existing authorities, or fail closed.

    First-time onboarding is one PostgreSQL transaction. The professional
    instrument, its symbol and identifier mappings, the crypto specification,
    the venue trading rules, and the authorized historical source with its
    capability rows are written inside a single outer ``database.transaction()``
    opened here. Each authority still performs its own write through its own
    nested ``database.transaction()`` call -- no direct SQL and no second write
    path are introduced -- but because every authority shares this same
    ``PostgresDatabase`` connection, psycopg treats each nested call as a
    SAVEPOINT beneath the outer transaction rather than an independent commit.
    An exception at any stage therefore rolls back every earlier write from
    this same call: there is no window in which a partial onboarding is
    visible to any other reader.

    Repeating the *identical* onboarding is a deterministic no-op: every
    component a successful onboarding writes is read back and compared
    against this exact snapshot (see :func:`_resolve_existing_onboarding`) --
    not merely the specification and venue rules -- and a complete, matching
    state returns ``already_onboarded=True`` without writing anything. Any
    state short of that -- some but not all components present, or a
    component present but disagreeing with this snapshot -- raises
    :class:`BybitOnboardingConflictError`. Nothing already recorded is ever
    repaired piecemeal or overwritten; a later, genuinely revised Bybit
    snapshot is a future rules-versioning workflow, not this function's job.
    """
    _require_onboarding_time(snapshot, onboarded_at)

    master = PostgresProfessionalInstrumentMaster(database)
    crypto = PostgresCryptoInstrumentAuthority(database)
    pipeline = PostgresHistoricalMarketDataPipeline(database)

    existing = _resolve_existing_onboarding(database, master, crypto, snapshot, onboarded_at)
    if existing is not None:
        return existing

    source = bybit_authorized_historical_source(snapshot, onboarded_at)
    with database.transaction():
        master.register(bybit_btcusdt_professional_instrument(snapshot, onboarded_at))
        master.add_symbol_mapping(bybit_btcusdt_symbol_mapping(snapshot, onboarded_at))
        master.add_identifier_mapping(bybit_btcusdt_identifier_mapping(snapshot, onboarded_at))
        crypto.specify_instrument(bybit_btcusdt_crypto_specification(snapshot, onboarded_at))
        crypto.record_venue_trading_rules(
            bybit_btcusdt_venue_trading_rules(snapshot, onboarded_at)
        )
        pipeline.register_source(source)

    return BybitInstrumentOnboardingResult(
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        source_id=source.source_id,
        canonical_payload_hash=snapshot.canonical_payload_hash,
        already_onboarded=False,
    )


def _require_present(value: _T | None, what: str) -> _T:
    """Narrow an optional read result once its presence has already been
    proved by the ``presence`` classification in
    :func:`_resolve_existing_onboarding`. Raising here (rather than asserting)
    keeps this a real, always-active fail-closed check consistent with every
    other invariant in this module -- and, unlike ``assert``, one that bandit
    does not flag and that ``python -O`` cannot compile away.
    """
    if value is None:
        raise BybitOnboardingConflictError(f"bybit_onboarding_state_inconsistent:{what}")
    return value


def _optional(action: Callable[[], _T], *not_found: type[Exception]) -> _T | None:
    """Run ``action``, mapping only the given "not found" exceptions to
    ``None``. Any other exception -- including an ambiguous-resolution error,
    which is a subclass of some of these and would otherwise be swallowed --
    is deliberately allowed through unchanged rather than being read as
    "nothing onboarded yet"; the schema's own exclusion constraints make that
    particular ambiguity unreachable for this module's fixed venue/symbol/
    namespace keys in the first place, but this function never assumes that.
    """
    try:
        return action()
    except not_found:
        return None


@dataclass(frozen=True, slots=True)
class _PersistedSourceRow:
    """The columns of one ``historical_data_sources`` row this module cares
    about, read back for existing-state verification. A private mirror of
    ``register_source``'s own column order -- this module still owns no write
    path to this table."""

    provider: str
    dataset_name: str
    provider_identifier_namespace: str
    provider_terms_version: str
    authorization_reference: str
    asset_scope: str


def _read_persisted_source(
    database: PostgresDatabase, source_id: UUID
) -> _PersistedSourceRow | None:
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT provider,dataset_name,provider_identifier_namespace,"
            "provider_terms_version,authorization_reference,asset_scope "
            "FROM historical_data_sources WHERE source_id=%s",
            (source_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return _PersistedSourceRow(
        provider=str(row[0]),
        dataset_name=str(row[1]),
        provider_identifier_namespace=str(row[2]),
        provider_terms_version=str(row[3]),
        authorization_reference=str(row[4]),
        asset_scope=str(row[5]),
    )


def _read_persisted_capabilities(
    database: PostgresDatabase, source_id: UUID
) -> frozenset[ObservationKind]:
    with database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT observation_kind FROM historical_source_capabilities WHERE source_id=%s",
            (source_id,),
        )
        rows = cursor.fetchall()
    return frozenset(ObservationKind(str(row[0])) for row in rows)


def _instrument_matches(actual: ProfessionalInstrument, expected: ProfessionalInstrument) -> bool:
    """True when every field but ``registered_at`` -- a knowledge clock this
    module deliberately does not require a repeat call to reproduce -- is
    identical."""
    return (
        actual.instrument_id == expected.instrument_id
        and actual.asset_class == expected.asset_class
        and actual.instrument_type == expected.instrument_type
        and actual.exchange_name == expected.exchange_name
        and actual.venue == expected.venue
        and actual.mic == expected.mic
        and actual.canonical_symbol == expected.canonical_symbol
        and actual.listing_date == expected.listing_date
        and actual.base_currency == expected.base_currency
        and actual.quote_currency == expected.quote_currency
        and actual.settlement_currency == expected.settlement_currency
        and actual.contract_multiplier == expected.contract_multiplier
        and actual.contract_size == expected.contract_size
        and actual.tick_size == expected.tick_size
        and actual.lot_size == expected.lot_size
        and actual.price_precision == expected.price_precision
        and actual.quantity_precision == expected.quantity_precision
        and actual.trading_timezone == expected.trading_timezone
        and actual.market_session_type == expected.market_session_type
        and actual.representation_kind == expected.representation_kind
        and actual.lifecycle_status == expected.lifecycle_status
    )


def _specification_matches(
    actual: CryptoInstrumentSpecification, expected: CryptoInstrumentSpecification
) -> bool:
    """True when every field but ``registered_at`` is identical."""
    return (
        actual.instrument_id == expected.instrument_id
        and actual.venue == expected.venue
        and actual.kind == expected.kind
        and actual.base_asset == expected.base_asset
        and actual.quote_asset == expected.quote_asset
        and actual.settlement_asset == expected.settlement_asset
        and actual.settlement_style == expected.settlement_style
        and actual.settlement_type == expected.settlement_type
        and actual.contract_multiplier == expected.contract_multiplier
        and actual.contract_size == expected.contract_size
        and actual.expiry_at == expected.expiry_at
        and actual.reference_price_requirement == expected.reference_price_requirement
        and actual.index_reference == expected.index_reference
        and actual.source_reference == expected.source_reference
    )


def _rules_match(actual: CryptoVenueTradingRules, expected: CryptoVenueTradingRules) -> bool:
    """True when every field but ``known_at`` is identical."""
    return (
        actual.rule_id == expected.rule_id
        and actual.instrument_id == expected.instrument_id
        and actual.rule_version == expected.rule_version
        and actual.tick_size == expected.tick_size
        and actual.quantity_step == expected.quantity_step
        and actual.min_quantity == expected.min_quantity
        and actual.max_quantity == expected.max_quantity
        and actual.min_notional == expected.min_notional
        and actual.price_precision == expected.price_precision
        and actual.quantity_precision == expected.quantity_precision
        and actual.effective_from == expected.effective_from
        and actual.source_reference == expected.source_reference
        and actual.source_hash == expected.source_hash
    )


def _source_matches(actual: _PersistedSourceRow, expected: AuthorizedHistoricalSource) -> bool:
    """True when every field but ``authorized_at``/``created_at`` is
    identical."""
    return (
        actual.provider == expected.provider
        and actual.dataset_name == expected.dataset_name
        and actual.provider_identifier_namespace == expected.provider_identifier_namespace
        and actual.provider_terms_version == expected.provider_terms_version
        and actual.authorization_reference == expected.authorization_reference
        and actual.asset_scope == expected.asset_scope
    )


def _resolve_existing_onboarding(
    database: PostgresDatabase,
    master: PostgresProfessionalInstrumentMaster,
    crypto: PostgresCryptoInstrumentAuthority,
    snapshot: BybitInstrumentMetadataSnapshotV1,
    onboarded_at: datetime,
) -> BybitInstrumentOnboardingResult | None:
    """Classify what, if anything, of this onboarding already exists.

    Reads back every one of the seven records a successful onboarding writes
    -- the professional instrument, its exact BYBIT/BTCUSDT symbol mapping,
    its exact ``bybit_v5_symbol``/``BTCUSDT`` identifier mapping, the crypto
    perpetual specification, venue rules version 1, the deterministic Bybit
    historical source, and its exact four source capabilities -- through the
    existing authorities (plus a private read-only mirror of the source and
    capability tables; this module still owns no write path of its own to
    either). The classification is exactly:

    * nothing exists -> ``None``, so the caller performs a fresh atomic write;
    * everything exists and matches this exact snapshot in every field that
      does not describe *when this platform learned it* -> a deterministic
      no-op result, with zero writes;
    * anything else -- some but not all components exist, or a component
      exists but disagrees with this snapshot -- raises
      :class:`BybitOnboardingConflictError`. A partial onboarding is never
      completed piecemeal, and nothing already recorded is ever repaired or
      overwritten in place.

    A repeat call's ``onboarded_at`` need not equal the original persisted
    knowledge time: only fields that describe the world (identity, launch
    time, tick/quantity semantics, evidence hash, capability set) are
    compared here, never ``registered_at``/``known_at``/``authorized_at``/
    ``created_at``, which are expected to differ between onboarding attempts.
    """
    source_id = _bybit_source_id()

    instrument = _optional(
        lambda: master.get_as_of(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, onboarded_at),
        InstrumentResolutionError,
    )

    symbol_owner = _optional(
        lambda: master.resolve_symbol_point_in_time(
            snapshot.symbol, BYBIT_EXCHANGE, snapshot.launch_time, onboarded_at
        ),
        InstrumentResolutionError,
    )
    if (
        symbol_owner is not None
        and symbol_owner.instrument_id != BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID
    ):
        raise BybitOnboardingConflictError(
            "bybit_symbol_mapping_resolves_to_a_different_instrument:"
            f"{symbol_owner.instrument_id}"
        )

    identifier_owner = _optional(
        lambda: master.resolve_identifier_point_in_time(
            BYBIT_V5_SYMBOL_NAMESPACE, snapshot.symbol, snapshot.launch_time, onboarded_at
        ),
        InstrumentResolutionError,
    )
    if (
        identifier_owner is not None
        and identifier_owner.instrument_id != BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID
    ):
        raise BybitOnboardingConflictError(
            "bybit_identifier_mapping_resolves_to_a_different_instrument:"
            f"{identifier_owner.instrument_id}"
        )

    specification = _optional(
        lambda: crypto.get_specification(BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID, known_at=onboarded_at),
        CryptoResolutionError,
    )
    rules = _optional(
        lambda: crypto.venue_trading_rules_point_in_time(
            BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
            effective_at=snapshot.retrieved_at,
            known_at=onboarded_at,
        ),
        CryptoVenueRuleError,
    )
    source_row = _read_persisted_source(database, source_id)
    capabilities = (
        _read_persisted_capabilities(database, source_id)
        if source_row is not None
        else frozenset()
    )

    presence = {
        "professional_instrument": instrument is not None,
        "symbol_mapping": symbol_owner is not None,
        "identifier_mapping": identifier_owner is not None,
        "crypto_specification": specification is not None,
        "venue_trading_rules": rules is not None,
        "historical_source": source_row is not None,
        "historical_source_capabilities": bool(capabilities),
    }

    if not any(presence.values()):
        return None

    if not all(presence.values()):
        missing = sorted(key for key, present in presence.items() if not present)
        raise BybitOnboardingConflictError(
            "bybit_onboarding_partial_state:missing=" + ",".join(missing)
        )

    # Every component is present; the dict above already proved it, but mypy
    # cannot see that through a dict lookup, so re-narrow each explicitly.
    instrument = _require_present(instrument, "professional_instrument")
    symbol_owner = _require_present(symbol_owner, "symbol_mapping")
    identifier_owner = _require_present(identifier_owner, "identifier_mapping")
    specification = _require_present(specification, "crypto_specification")
    rules = _require_present(rules, "venue_trading_rules")
    source_row = _require_present(source_row, "historical_source")

    expected_instrument = bybit_btcusdt_professional_instrument(snapshot, onboarded_at)
    if not _instrument_matches(instrument, expected_instrument):
        raise BybitOnboardingConflictError(
            "bybit_instrument_already_onboarded_with_different_identity"
        )

    expected_specification = bybit_btcusdt_crypto_specification(snapshot, onboarded_at)
    if not _specification_matches(specification, expected_specification):
        raise BybitOnboardingConflictError(
            "bybit_instrument_already_onboarded_with_different_evidence:"
            f"{specification.source_reference}"
        )

    expected_rules = bybit_btcusdt_venue_trading_rules(snapshot, onboarded_at)
    if not _rules_match(rules, expected_rules):
        raise BybitOnboardingConflictError(
            f"bybit_venue_rules_already_recorded_from_different_snapshot:{rules.source_hash}"
        )

    expected_source = bybit_authorized_historical_source(snapshot, onboarded_at)
    if not _source_matches(source_row, expected_source):
        raise BybitOnboardingConflictError(
            "bybit_historical_source_already_registered_with_different_evidence"
        )
    if capabilities != expected_source.resolved_capabilities():
        raise BybitOnboardingConflictError(
            "bybit_historical_source_capability_set_differs:"
            + ",".join(sorted(kind.value for kind in capabilities))
        )

    return BybitInstrumentOnboardingResult(
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        source_id=source_id,
        canonical_payload_hash=snapshot.canonical_payload_hash,
        already_onboarded=True,
    )
