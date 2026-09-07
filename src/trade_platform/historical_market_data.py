"""Authorized, provider-neutral PostgreSQL historical market-data pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TypeAlias, cast
from uuid import UUID, uuid4

from .crypto_instruments import (
    CryptoInstrumentError,
    CryptoInstrumentSpecification,
    PostgresCryptoInstrumentAuthority,
)
from .crypto_market_observations import (
    FUNDING_ELIGIBLE_CRYPTO_KINDS,
    FUNDING_PAYLOAD_TABLE,
    REFERENCE_PRICE_PAYLOAD_TABLE,
    CryptoFundingPayload,
    CryptoReferencePricePayload,
    FundingObservationKind,
    ReferencePriceKind,
    parse_funding_payload,
    parse_reference_price_payload,
    validate_crypto_funding,
    validate_crypto_open_interest,
    validate_crypto_reference_price,
)
from .domain import AssetClass
from .futures_market_observations import (
    SETTLEMENT_PAYLOAD_TABLE,
    FuturesSettlementPayload,
    SettlementFinality,
    parse_settlement_payload,
)
from .market_observation_payloads import (
    CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
    FUTURES_SUPPORTED_OPEN_INTEREST_UNITS,
    OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY,
    OPEN_INTEREST_PAYLOAD_TABLE,
    OpenInterestPayload,
    OpenInterestUnit,
    canonical_payload_marker,
    parse_open_interest_payload,
)
from .persistence import PostgresDatabase
from .professional_instruments import (
    InstrumentResolutionError,
    InstrumentType,
    PostgresProfessionalInstrumentMaster,
    ProfessionalInstrument,
)


class HistoricalMarketDataError(ValueError):
    pass


class HistoricalDataAuthorizationError(HistoricalMarketDataError):
    pass


class HistoricalDataQualityError(HistoricalMarketDataError):
    pass


class HistoricalDataResolutionError(HistoricalMarketDataError):
    pass


class ObservationKind(StrEnum):
    OHLCV = "OHLCV"
    DIVIDEND = "DIVIDEND"
    SPLIT = "SPLIT"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    DELISTING = "DELISTING"
    # Module 3I.1. Both carry their financial value in a typed canonical
    # payload table, never in normalized_value -- see TYPED_PAYLOAD_KINDS.
    SETTLEMENT_PRICE = "SETTLEMENT_PRICE"
    OPEN_INTEREST = "OPEN_INTEREST"
    # Module 3I.2. Realized and indicative funding are deliberately two kinds,
    # not one kind with a status column: what a venue applied and what it
    # estimated it would apply are different evidence, and nothing may
    # substitute one for the other. MARK_PRICE and INDEX_PRICE are likewise
    # distinct from each other and from SETTLEMENT_PRICE, OHLCV and any last
    # trade -- there is no fallback between reference prices anywhere.
    FUNDING_RATE_REALIZED = "FUNDING_RATE_REALIZED"
    FUNDING_RATE_INDICATIVE = "FUNDING_RATE_INDICATIVE"
    MARK_PRICE = "MARK_PRICE"
    INDEX_PRICE = "INDEX_PRICE"


class AssetScope(StrEnum):
    """What an authorized source is permitted to supply, fail-closed by value."""

    US_EQUITIES_ETFS = "US_EQUITIES_ETFS"
    FUTURES = "FUTURES"
    CRYPTO = "CRYPTO"


#: Kinds whose canonical normalized payload lives in a typed table rather than
#: in ``historical_normalized_observations.normalized_value``.
TYPED_PAYLOAD_KINDS: frozenset[ObservationKind] = frozenset(
    {
        ObservationKind.SETTLEMENT_PRICE,
        ObservationKind.OPEN_INTEREST,
        ObservationKind.FUNDING_RATE_REALIZED,
        ObservationKind.FUNDING_RATE_INDICATIVE,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
    }
)

#: The Module 3I.2 crypto observation family. Each requires a 3H.2 crypto
#: instrument specification and is judged against that instrument's own
#: semantics; none of them may describe a futures or equity instrument.
CRYPTO_OBSERVATION_KINDS: frozenset[ObservationKind] = frozenset(
    {
        ObservationKind.FUNDING_RATE_REALIZED,
        ObservationKind.FUNDING_RATE_INDICATIVE,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
    }
)

#: Which funding evidence each funding kind is. The envelope kind is the single
#: authority; the typed table stores no discriminator that could disagree.
_FUNDING_KINDS: dict[ObservationKind, FundingObservationKind] = {
    ObservationKind.FUNDING_RATE_REALIZED: FundingObservationKind.REALIZED,
    ObservationKind.FUNDING_RATE_INDICATIVE: FundingObservationKind.INDICATIVE,
}

#: Likewise for the shared mark/index typed table.
_REFERENCE_PRICE_KINDS: dict[ObservationKind, ReferencePriceKind] = {
    ObservationKind.MARK_PRICE: ReferencePriceKind.MARK,
    ObservationKind.INDEX_PRICE: ReferencePriceKind.INDEX,
}

#: The equity corporate-action family that predates Module 3I.1.
_EQUITY_KINDS: frozenset[ObservationKind] = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.DIVIDEND,
        ObservationKind.SPLIT,
        ObservationKind.SYMBOL_CHANGE,
        ObservationKind.DELISTING,
    }
)

#: Which kinds each asset scope may authorize at all. A capability outside its
#: scope is rejected at source registration, so scope and capability cannot
#: disagree.
SCOPE_ELIGIBLE_KINDS: dict[AssetScope, frozenset[ObservationKind]] = {
    AssetScope.US_EQUITIES_ETFS: _EQUITY_KINDS,
    AssetScope.FUTURES: frozenset(
        {ObservationKind.OHLCV, ObservationKind.SETTLEMENT_PRICE, ObservationKind.OPEN_INTEREST}
    ),
    # OPEN_INTEREST appears under both derivative scopes because there is one
    # open-interest semantic authority, not a futures one and a crypto one.
    # Scope still binds: a FUTURES source may not write open interest for a
    # crypto instrument, which _require_source_scope_matches_instrument
    # enforces against the resolved instrument itself.
    AssetScope.CRYPTO: frozenset(
        {
            ObservationKind.OHLCV,
            ObservationKind.OPEN_INTEREST,
            ObservationKind.FUNDING_RATE_REALIZED,
            ObservationKind.FUNDING_RATE_INDICATIVE,
            ObservationKind.MARK_PRICE,
            ObservationKind.INDEX_PRICE,
        }
    ),
}



class AdjustmentStatus(StrEnum):
    RAW = "RAW"
    AS_REPORTED = "AS_REPORTED"
    POINT_IN_TIME_ADJUSTED = "POINT_IN_TIME_ADJUSTED"
    LATEST_ADJUSTED = "LATEST_ADJUSTED"


class QualityStatus(StrEnum):
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


#: Adjustment statuses that carry defined meaning for the typed kinds. A
#: settlement price and an open-interest count are not corporate-action
#: adjusted, so the equity-oriented adjusted statuses are rejected rather than
#: silently acquiring an undefined meaning for them.
TYPED_KIND_ADJUSTMENT_STATUSES: frozenset[AdjustmentStatus] = frozenset(
    {AdjustmentStatus.RAW, AdjustmentStatus.AS_REPORTED}
)

#: Which physical typed table is canonical for each typed kind. Funding shares
#: one table across its two kinds and mark/index share another: the financial
#: shape is identical, while the envelope kind stays the sole authority for
#: meaning and database constraint triggers refuse a payload attached to an
#: envelope outside its own kind set.
_TYPED_PAYLOAD_TABLES: dict[ObservationKind, str] = {
    ObservationKind.SETTLEMENT_PRICE: SETTLEMENT_PAYLOAD_TABLE,
    ObservationKind.OPEN_INTEREST: OPEN_INTEREST_PAYLOAD_TABLE,
    ObservationKind.FUNDING_RATE_REALIZED: FUNDING_PAYLOAD_TABLE,
    ObservationKind.FUNDING_RATE_INDICATIVE: FUNDING_PAYLOAD_TABLE,
    ObservationKind.MARK_PRICE: REFERENCE_PRICE_PAYLOAD_TABLE,
    ObservationKind.INDEX_PRICE: REFERENCE_PRICE_PAYLOAD_TABLE,
}

#: The canonical payload identity token written into ``normalized_value`` and
#: folded into sealed dataset hashes. It equals the physical table name for
#: every kind except OPEN_INTEREST, whose token is frozen at its pre-3I.2 value
#: so that renaming the physical table did not change the identity of any
#: already-sealed dataset. See ``market_observation_payloads``.
_CANONICAL_PAYLOAD_IDENTITIES: dict[ObservationKind, str] = {
    **_TYPED_PAYLOAD_TABLES,
    ObservationKind.OPEN_INTEREST: OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY,
}

#: Instrument-master types that a CRYPTO-scoped source may describe. Module 3H.2
#: keeps these distinct from ``FUTURE`` precisely so a crypto perpetual can never
#: satisfy a futures contract's required metadata, and vice versa.
_CRYPTO_INSTRUMENT_TYPES: frozenset[InstrumentType] = frozenset(
    {
        InstrumentType.SPOT_CRYPTO,
        InstrumentType.CRYPTO_PERPETUAL,
        InstrumentType.CRYPTO_DATED_FUTURE,
    }
)

#: Which open-interest units each asset scope's instruments may declare. Units
#: outside the set fail closed; nothing is ever converted into another unit.
_SCOPE_OPEN_INTEREST_UNITS: dict[AssetScope, frozenset[OpenInterestUnit]] = {
    AssetScope.FUTURES: FUTURES_SUPPORTED_OPEN_INTEREST_UNITS,
    AssetScope.CRYPTO: CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS,
}

#: Every canonical typed payload the pipeline can hold. Each has the same three
#: obligations: a stable ``canonical_tuple`` folded into sealed dataset hashes,
#: an ``as_normalized_projection`` synthesized for research readers, and exactly
#: one durable row that the envelope cannot exist without.
TypedPayload: TypeAlias = (
    FuturesSettlementPayload
    | OpenInterestPayload
    | CryptoFundingPayload
    | CryptoReferencePricePayload
)

#: Column offsets of each typed payload block inside the joined rows read by
#: :meth:`PostgresHistoricalMarketDataPipeline.seal_dataset` and
#: :meth:`~PostgresHistoricalMarketDataPipeline.research_query`, relative to the
#: first typed column. Both queries select ``_TYPED_PAYLOAD_COLUMNS`` in this
#: order, so one hydration function serves both.
_SETTLEMENT_OFFSET = 0
_OPEN_INTEREST_OFFSET = 6
_FUNDING_OFFSET = 10
_REFERENCE_PRICE_OFFSET = 16

_TYPED_PAYLOAD_COLUMNS = (
    "st.settlement_price,st.price_currency,st.settlement_date,st.settlement_effective_at,"
    "st.finality,st.quote_unit,"
    "oi.open_interest,oi.unit,oi.observed_at,oi.unit_asset,"
    "fu.funding_rate,fu.target_funding_at,fu.published_at,fu.settlement_asset,"
    "fu.convention_id,fu.convention_version,"
    "rp.price,rp.price_asset,rp.observed_at,rp.methodology_reference"
)

_TYPED_PAYLOAD_JOINS = (
    f"LEFT JOIN {SETTLEMENT_PAYLOAD_TABLE} st "
    "ON st.normalized_observation_id=n.normalized_observation_id "
    f"LEFT JOIN {OPEN_INTEREST_PAYLOAD_TABLE} oi "
    "ON oi.normalized_observation_id=n.normalized_observation_id "
    f"LEFT JOIN {FUNDING_PAYLOAD_TABLE} fu "
    "ON fu.normalized_observation_id=n.normalized_observation_id "
    f"LEFT JOIN {REFERENCE_PRICE_PAYLOAD_TABLE} rp "
    "ON rp.normalized_observation_id=n.normalized_observation_id"
)


def _typed_payload_from_row(
    kind: ObservationKind, row: tuple[object, ...], offset: int
) -> TypedPayload | None:
    """Rebuild the canonical payload from its typed columns at read time."""
    if kind is ObservationKind.SETTLEMENT_PRICE:
        base = offset + _SETTLEMENT_OFFSET
        if row[base] is None:
            return None
        return FuturesSettlementPayload(
            settlement_price=Decimal(str(row[base])),
            price_currency=str(row[base + 1]),
            settlement_date=cast(date, row[base + 2]),
            settlement_effective_at=cast(datetime, row[base + 3]),
            finality=SettlementFinality(str(row[base + 4])),
            quote_unit=str(row[base + 5]),
        )
    if kind is ObservationKind.OPEN_INTEREST:
        base = offset + _OPEN_INTEREST_OFFSET
        if row[base] is None:
            return None
        return OpenInterestPayload(
            open_interest=Decimal(str(row[base])),
            unit=OpenInterestUnit(str(row[base + 1])),
            observed_at=cast(datetime, row[base + 2]),
            unit_asset=None if row[base + 3] is None else str(row[base + 3]),
        )
    if kind in _FUNDING_KINDS:
        base = offset + _FUNDING_OFFSET
        if row[base] is None:
            return None
        return CryptoFundingPayload(
            funding_rate=Decimal(str(row[base])),
            target_funding_at=cast(datetime, row[base + 1]),
            published_at=cast(datetime, row[base + 2]),
            settlement_asset=str(row[base + 3]),
            convention_id=None if row[base + 4] is None else UUID(str(row[base + 4])),
            convention_version=None if row[base + 5] is None else int(str(row[base + 5])),
        )
    if kind in _REFERENCE_PRICE_KINDS:
        base = offset + _REFERENCE_PRICE_OFFSET
        if row[base] is None:
            return None
        return CryptoReferencePricePayload(
            price=Decimal(str(row[base])),
            price_asset=str(row[base + 1]),
            observed_at=cast(datetime, row[base + 2]),
            methodology_reference=None if row[base + 3] is None else str(row[base + 3]),
        )
    return None


def _sealed_typed_components(row: tuple[object, ...]) -> tuple[str, ...]:
    """Canonical typed values contributed to a sealed dataset's content hash."""
    kind = ObservationKind(str(row[9]))
    if kind not in TYPED_PAYLOAD_KINDS:
        return ()
    payload = _typed_payload_from_row(kind, row, 10)
    if payload is None:
        raise HistoricalMarketDataError(
            f"sealed_typed_observation_missing_canonical_payload:{row[0]}"
        )
    return payload.canonical_tuple()


CONSOLIDATED_TAPE_EXCHANGE = "CONSOLIDATED_TAPE"
"""Sentinel ``RawHistoricalObservation.exchange`` value for genuinely multi-venue data.

US equities/ETFs trade under the CTA/UTP consolidated-tape system: a composite,
cross-venue print/quote stream that -- by design -- carries no single execution-venue
attribution per record (e.g. Databento's ``EQUS.SUMMARY``/``EQUS.MINI``, documented as
consolidated across "all NMS exchanges and ATSs"). A raw observation whose provider
genuinely produced consolidated data, not a specific execution venue, declares that
honestly with this exact value rather than claiming a venue it has no basis for. This
is provider-neutral: any future consolidated-tape source uses the same sentinel, and
any single-venue provider (e.g. a direct exchange feed) is unaffected and still must
match the instrument's actual registered ``venue``/``mic``.
"""

_CONSOLIDATED_TAPE_ELIGIBLE_VENUES = frozenset({"XNAS", "ARCX"})
"""Listing venues this repository's instrument master actually models as eligible for
the US consolidated tape (``professional_instruments.mvp_instrument_universe``). This
is deliberately the exhaustive set this codebase currently knows about, not a general
claim about every US exchange -- extend it only as new US equity/ETF listing venues are
onboarded to the instrument master, never speculatively.
"""


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalMarketDataError(f"{name}_must_be_timezone_aware")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise HistoricalMarketDataError(f"invalid_{name}")


def _canonical(payload: dict[str, object]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise HistoricalMarketDataError("raw_payload_not_canonical_json") from error


@dataclass(frozen=True, slots=True)
class AuthorizedHistoricalSource:
    provider: str
    dataset_name: str
    provider_identifier_namespace: str
    provider_terms_version: str
    authorization_reference: str
    authorized_at: datetime
    created_at: datetime
    asset_scope: str = AssetScope.US_EQUITIES_ETFS.value
    source_id: UUID = field(default_factory=uuid4)
    authorized_observation_kinds: frozenset[ObservationKind] | None = None
    """Exactly which observation kinds this source may write.

    ``None`` keeps the pre-3I.1 behaviour for ``US_EQUITIES_ETFS`` only, where
    it resolves to the five equity corporate-action kinds those sources already
    wrote. Every other scope must state its kinds explicitly, so no source can
    acquire SETTLEMENT_PRICE or OPEN_INTEREST authority merely by being
    authorized for the same asset class as an OHLCV feed.
    """

    def validate(self) -> None:
        if not self.authorization_reference.strip():
            raise HistoricalDataAuthorizationError("authorization_reference_required")
        for text_value, name in (
            (self.provider, "provider"),
            (self.dataset_name, "dataset_name"),
            (self.provider_identifier_namespace, "provider_identifier_namespace"),
            (self.provider_terms_version, "provider_terms_version"),
        ):
            _text(text_value, name)
        _aware(self.authorized_at, "authorized_at")
        _aware(self.created_at, "created_at")
        if self.created_at < self.authorized_at:
            raise HistoricalDataAuthorizationError("invalid_historical_source_authorization")
        self.resolved_capabilities()

    def scope(self) -> AssetScope:
        try:
            return AssetScope(self.asset_scope)
        except ValueError as error:
            raise HistoricalDataAuthorizationError(
                f"unsupported_asset_scope:{self.asset_scope}"
            ) from error

    def resolved_capabilities(self) -> frozenset[ObservationKind]:
        """The kinds this source may write, fail-closed against its asset scope."""
        scope = self.scope()
        eligible = SCOPE_ELIGIBLE_KINDS[scope]
        declared = self.authorized_observation_kinds
        if declared is None:
            if scope is not AssetScope.US_EQUITIES_ETFS:
                raise HistoricalDataAuthorizationError(
                    f"asset_scope_requires_explicit_observation_kinds:{scope.value}"
                )
            return eligible
        if not declared:
            raise HistoricalDataAuthorizationError("source_authorizes_no_observation_kind")
        outside = declared - eligible
        if outside:
            raise HistoricalDataAuthorizationError(
                "observation_kind_outside_asset_scope:"
                + ",".join(sorted(kind.value for kind in outside))
            )
        return frozenset(declared)


@dataclass(frozen=True, slots=True)
class RawHistoricalObservation:
    source_id: UUID
    observation_kind: ObservationKind
    provider_identifier: str
    provider_symbol: str
    exchange: str
    event_at: datetime
    effective_at: datetime
    ingested_at: datetime
    adjustment_status: AdjustmentStatus
    revision: int
    provenance_uri: str
    raw_payload: dict[str, object]
    raw_observation_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        for value, name in (
            (self.provider_identifier, "provider_identifier"),
            (self.provider_symbol, "provider_symbol"),
            (self.exchange, "exchange"),
            (self.provenance_uri, "provenance_uri"),
        ):
            _text(value, name)
        for timestamp, name in (
            (self.event_at, "event_at"),
            (self.effective_at, "effective_at"),
            (self.ingested_at, "ingested_at"),
        ):
            _aware(timestamp, name)
        if self.effective_at < self.event_at or self.ingested_at < self.event_at:
            raise HistoricalMarketDataError("invalid_historical_observation_timestamps")
        if self.revision < 0:
            raise HistoricalMarketDataError("invalid_historical_observation_revision")
        if (
            self.observation_kind in TYPED_PAYLOAD_KINDS
            and self.adjustment_status not in TYPED_KIND_ADJUSTMENT_STATUSES
        ):
            raise HistoricalMarketDataError(
                f"adjustment_status_undefined_for_kind:"
                f"{self.observation_kind.value}:{self.adjustment_status.value}"
            )
        _canonical(self.raw_payload)


@dataclass(frozen=True, slots=True)
class NormalizedHistoricalObservation:
    normalized_observation_id: UUID
    raw_observation_id: UUID
    instrument_id: str
    normalization_version: str
    normalized_value: dict[str, object]
    quality_status: QualityStatus
    quality_issues: tuple[str, ...]
    normalized_at: datetime


@dataclass(frozen=True, slots=True)
class HistoricalDatasetVersion:
    dataset_version_id: UUID
    source_id: UUID
    version: str
    normalization_version: str
    content_hash: str
    valid_from: datetime
    valid_until: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HistoricalResearchObservation:
    instrument_id: str
    observation_kind: ObservationKind
    provider: str
    provider_identifier: str
    provider_symbol: str
    exchange: str
    event_at: datetime
    effective_at: datetime
    ingested_at: datetime
    adjustment_status: AdjustmentStatus
    revision: int
    provenance_uri: str
    raw_payload_sha256: str
    normalized_value: dict[str, object]
    data_version: str


def _decimal(payload: dict[str, object], key: str, issues: list[str]) -> Decimal | None:
    try:
        value = Decimal(str(payload[key]))
        if not value.is_finite():
            raise InvalidOperation
        return value
    except (KeyError, InvalidOperation, ValueError):
        issues.append(f"invalid_{key}")
        return None


def normalize_payload(
    kind: ObservationKind, payload: dict[str, object]
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Normalize financial values to canonical decimal strings without repairing data."""
    issues: list[str] = []
    normalized: dict[str, object] = {}
    if kind is ObservationKind.OHLCV:
        values = {key: _decimal(payload, key, issues) for key in ("open", "high", "low", "close", "volume")}
        interval = str(payload.get("interval", "")).strip()
        if not interval:
            issues.append("invalid_interval")
        prices = [values[key] for key in ("open", "high", "low", "close")]
        if all(value is not None for value in prices):
            concrete = cast(list[Decimal], prices)
            if min(concrete) <= 0:
                issues.append("non_positive_price")
            if concrete[1] < max(concrete[0], concrete[3]) or concrete[2] > min(concrete[0], concrete[3]):
                issues.append("impossible_ohlc")
        if values["volume"] is not None and values["volume"] < 0:
            issues.append("negative_volume")
        normalized = {key: None if value is None else str(value) for key, value in values.items()}
        normalized["interval"] = interval
    elif kind is ObservationKind.DIVIDEND:
        amount = _decimal(payload, "amount", issues)
        currency = str(payload.get("currency", "")).upper()
        if amount is not None and amount < 0:
            issues.append("negative_dividend")
        if len(currency) != 3:
            issues.append("invalid_currency")
        normalized = {"amount": None if amount is None else str(amount), "currency": currency}
    elif kind is ObservationKind.SPLIT:
        ratio = _decimal(payload, "ratio", issues)
        if ratio is not None and ratio <= 0:
            issues.append("non_positive_split_ratio")
        normalized = {"ratio": None if ratio is None else str(ratio)}
    elif kind is ObservationKind.SYMBOL_CHANGE:
        old_symbol = str(payload.get("old_symbol", "")).strip()
        new_symbol = str(payload.get("new_symbol", "")).strip()
        if not old_symbol or not new_symbol or old_symbol == new_symbol:
            issues.append("invalid_symbol_change")
        normalized = {"old_symbol": old_symbol, "new_symbol": new_symbol}
    else:
        reason = str(payload.get("reason", "")).strip()
        if not reason:
            issues.append("invalid_delisting_reason")
        normalized = {"reason": reason}
        if "cash_out_price" in payload:
            price = _decimal(payload, "cash_out_price", issues)
            if price is not None and price < 0:
                issues.append("negative_cash_out_price")
            normalized["cash_out_price"] = None if price is None else str(price)
    return normalized, tuple(dict.fromkeys(issues))


class PostgresHistoricalMarketDataPipeline:
    """Raw capture, PIT normalization, sealed datasets and leakage-safe research reads."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._instruments = PostgresProfessionalInstrumentMaster(database)
        # The 3H.2 authority is read, never duplicated: crypto specifications and
        # funding conventions have exactly one home and this pipeline consults it.
        self._crypto = PostgresCryptoInstrumentAuthority(database)

    def register_source(self, source: AuthorizedHistoricalSource) -> None:
        source.validate()
        capabilities = source.resolved_capabilities()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_data_sources VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (source.source_id, source.provider, source.dataset_name,
                     source.provider_identifier_namespace, source.provider_terms_version,
                     source.authorization_reference, source.authorized_at, source.asset_scope,
                     source.created_at),
                )
                # The database enforces this through a composite foreign key on
                # historical_raw_observations, so an unauthorized kind cannot be
                # captured even by a caller that bypasses this class.
                for kind in sorted(capabilities, key=lambda item: item.value):
                    cursor.execute(
                        "INSERT INTO historical_source_capabilities VALUES (%s,%s,%s)",
                        (source.source_id, kind.value, source.authorized_at),
                    )
        except Exception as error:
            raise HistoricalDataAuthorizationError("historical_source_registration_failed") from error

    def capture_raw(self, observations: list[RawHistoricalObservation]) -> tuple[UUID, ...]:
        if not observations:
            raise HistoricalMarketDataError("empty_historical_observation_batch")
        for observation in observations:
            observation.validate()
        try:
            persisted_ids: list[UUID] = []
            with self._database.transaction() as connection, connection.cursor() as cursor:
                for observation in observations:
                    payload = _canonical(observation.raw_payload)
                    payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
                    cursor.execute(
                        "INSERT INTO historical_raw_observations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                        "ON CONFLICT (source_id,provider_identifier,observation_kind,event_at,revision) DO NOTHING "
                        "RETURNING raw_observation_id",
                        (observation.raw_observation_id, observation.source_id,
                         observation.observation_kind.value, observation.provider_identifier,
                         observation.provider_symbol, observation.exchange, observation.event_at,
                         observation.effective_at, observation.ingested_at,
                         observation.adjustment_status.value, observation.revision,
                         observation.provenance_uri, payload, payload_sha256),
                    )
                    inserted = cursor.fetchone()
                    if inserted is not None:
                        persisted_ids.append(UUID(str(inserted[0])))
                        continue
                    cursor.execute(
                        "SELECT raw_observation_id,raw_payload_sha256 FROM historical_raw_observations "
                        "WHERE source_id=%s AND provider_identifier=%s AND observation_kind=%s "
                        "AND event_at=%s AND revision=%s",
                        (observation.source_id, observation.provider_identifier,
                         observation.observation_kind.value, observation.event_at,
                         observation.revision),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[1]) != payload_sha256:
                        raise HistoricalMarketDataError("raw_historical_observation_conflict")
                    persisted_ids.append(UUID(str(existing[0])))
        except HistoricalMarketDataError:
            raise
        except Exception as error:
            raise HistoricalMarketDataError("raw_historical_capture_failed") from error
        return tuple(persisted_ids)

    def normalize(
        self, raw_observation_id: UUID, normalization_version: str, normalized_at: datetime
    ) -> NormalizedHistoricalObservation:
        _text(normalization_version, "normalization_version")
        _aware(normalized_at, "normalized_at")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT r.source_id,r.observation_kind,r.provider_identifier,r.exchange,r.event_at,r.ingested_at,r.raw_payload,s.provider_identifier_namespace,s.asset_scope "
                "FROM historical_raw_observations r JOIN historical_data_sources s ON s.source_id=r.source_id WHERE r.raw_observation_id=%s",
                (raw_observation_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise HistoricalMarketDataError("raw_historical_observation_not_found")
        event_at, ingested_at = cast(datetime, row[4]), cast(datetime, row[5])
        if normalized_at < ingested_at:
            raise HistoricalMarketDataError("normalization_precedes_ingestion")
        try:
            instrument = self._instruments.resolve_identifier_point_in_time(
                str(row[7]), str(row[2]), event_at, ingested_at
            )
        except InstrumentResolutionError as error:
            raise HistoricalDataResolutionError("historical_instrument_resolution_failed") from error
        kind = ObservationKind(str(row[1]))
        try:
            scope = AssetScope(str(row[8]))
        except ValueError as error:
            raise HistoricalDataAuthorizationError(
                f"unsupported_asset_scope:{row[8]}"
            ) from error
        # Kind eligibility first, so the most specific refusal is the one
        # reported; scope then catches the cases a kind cannot, such as a
        # futures-authorized source writing open interest -- a kind both
        # derivative scopes share -- for a crypto perpetual.
        self._require_instrument_eligible_for_kind(kind, instrument)
        self._require_source_scope_matches_instrument(scope, instrument)
        issues: list[str] = []
        raw_exchange = str(row[3])
        if raw_exchange == CONSOLIDATED_TAPE_EXCHANGE:
            if instrument.venue not in _CONSOLIDATED_TAPE_ELIGIBLE_VENUES:
                issues.append("exchange_instrument_mismatch")
        elif raw_exchange not in {instrument.venue, instrument.mic}:
            issues.append("exchange_instrument_mismatch")
        payload = cast(dict[str, object], row[6])

        typed: TypedPayload | None = None
        if kind in TYPED_PAYLOAD_KINDS:
            typed, typed_issues = self._parse_typed_payload(
                kind, payload, scope=scope, instrument_id=instrument.instrument_id,
                event_at=event_at, ingested_at=ingested_at,
            )
            issues.extend(typed_issues)
            # The canonical financial value lives in the typed table, so the
            # envelope stores a pointer marker rather than a second copy.
            normalized = canonical_payload_marker(_CANONICAL_PAYLOAD_IDENTITIES[kind])
        else:
            normalized, payload_issues = normalize_payload(kind, payload)
            issues.extend(payload_issues)

        result = NormalizedHistoricalObservation(
            uuid4(), raw_observation_id, instrument.instrument_id, normalization_version,
            normalized, QualityStatus.REJECTED if issues else QualityStatus.VALIDATED,
            tuple(dict.fromkeys(issues)), normalized_at,
        )
        if kind in TYPED_PAYLOAD_KINDS and issues:
            # The typed table is the single canonical authority for this value,
            # so a record judged invalid is not written there at all: a stored
            # row would look canonical while being evidence of nothing. When the
            # payload could not even be parsed there is nothing to persist, and
            # the deferred constraint trigger would refuse the envelope alone.
            # Reporting the quality failure is the honest outcome; no partial or
            # guessed row is written.
            raise HistoricalDataQualityError(
                "typed_observation_rejected:" + ",".join(result.quality_issues)
            )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_normalized_observations VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)",
                    (result.normalized_observation_id, result.raw_observation_id,
                     result.instrument_id, result.normalization_version,
                     _canonical(result.normalized_value), result.quality_status.value,
                     json.dumps(result.quality_issues), result.normalized_at),
                )
                if isinstance(typed, FuturesSettlementPayload):
                    cursor.execute(
                        f"INSERT INTO {SETTLEMENT_PAYLOAD_TABLE} "  # nosec B608 - fixed constant
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (result.normalized_observation_id, typed.settlement_price,
                         typed.price_currency, typed.settlement_date,
                         typed.settlement_effective_at, typed.finality.value, typed.quote_unit),
                    )
                elif isinstance(typed, OpenInterestPayload):
                    cursor.execute(
                        f"INSERT INTO {OPEN_INTEREST_PAYLOAD_TABLE} "  # nosec B608 - fixed constant
                        "VALUES (%s,%s,%s,%s,%s)",
                        (result.normalized_observation_id, typed.open_interest,
                         typed.unit.value, typed.unit_asset, typed.observed_at),
                    )
                elif isinstance(typed, CryptoFundingPayload):
                    cursor.execute(
                        f"INSERT INTO {FUNDING_PAYLOAD_TABLE} "  # nosec B608 - fixed constant
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (result.normalized_observation_id, typed.funding_rate,
                         typed.target_funding_at, typed.published_at, typed.settlement_asset,
                         typed.convention_id, typed.convention_version),
                    )
                elif isinstance(typed, CryptoReferencePricePayload):
                    cursor.execute(
                        f"INSERT INTO {REFERENCE_PRICE_PAYLOAD_TABLE} "  # nosec B608 - fixed constant
                        "VALUES (%s,%s,%s,%s,%s)",
                        (result.normalized_observation_id, typed.price, typed.price_asset,
                         typed.observed_at, typed.methodology_reference),
                    )
        except Exception as error:
            raise HistoricalMarketDataError("historical_normalization_persistence_failed") from error
        return result

    def _parse_typed_payload(
        self, kind: ObservationKind, payload: dict[str, object], *, scope: AssetScope,
        instrument_id: str, event_at: datetime, ingested_at: datetime,
    ) -> tuple[TypedPayload | None, tuple[str, ...]]:
        """Parse a typed payload and judge it against the instrument's own semantics."""
        if kind is ObservationKind.SETTLEMENT_PRICE:
            return parse_settlement_payload(payload)
        if kind is ObservationKind.OPEN_INTEREST:
            units = _SCOPE_OPEN_INTEREST_UNITS.get(
                scope, FUTURES_SUPPORTED_OPEN_INTEREST_UNITS
            )
            open_interest, issues = parse_open_interest_payload(payload, supported_units=units)
            if open_interest is None or scope is not AssetScope.CRYPTO:
                return open_interest, issues
            specification = self._crypto_specification(instrument_id, ingested_at)
            return open_interest, validate_crypto_open_interest(
                open_interest, specification=specification
            )
        specification = self._crypto_specification(instrument_id, ingested_at)
        if kind in _REFERENCE_PRICE_KINDS:
            reference, issues = parse_reference_price_payload(payload)
            if reference is None:
                return None, issues
            return reference, validate_crypto_reference_price(
                reference, kind=_REFERENCE_PRICE_KINDS[kind],
                specification=specification, event_at=event_at,
            )
        funding, issues = parse_funding_payload(payload)
        if funding is None:
            return None, issues
        if specification.kind not in FUNDING_ELIGIBLE_CRYPTO_KINDS:
            # Reported before the convention lookup so the refusal names the real
            # problem: a spot pair or dated future is not subject to funding at
            # all, and therefore legitimately has no funding schedule to miss.
            return funding, (f"funding_requires_perpetual:{specification.kind.value}",)
        # Two clocks, both taken from this observation and never from later
        # knowledge: the schedule in force at the funding instant being reported,
        # as this platform knew it when the record was ingested. A venue that
        # announces a new schedule afterwards cannot reach back into this replay.
        try:
            convention = self._crypto.funding_convention_point_in_time(
                instrument_id, effective_at=funding.target_funding_at, known_at=ingested_at
            )
        except CryptoInstrumentError:
            return funding, ("funding_convention_not_known_at_observation_knowledge_time",)
        return funding.bound_to(convention), validate_crypto_funding(
            funding, kind=_FUNDING_KINDS[kind], specification=specification,
            convention=convention, event_at=event_at, ingested_at=ingested_at,
        )

    def _crypto_specification(
        self, instrument_id: str, known_at: datetime
    ) -> CryptoInstrumentSpecification:
        try:
            return self._crypto.get_specification(instrument_id, known_at=known_at)
        except CryptoInstrumentError as error:
            raise HistoricalDataResolutionError(
                f"instrument_has_no_crypto_specification:{instrument_id}"
            ) from error

    @staticmethod
    def _require_source_scope_matches_instrument(
        scope: AssetScope, instrument: ProfessionalInstrument
    ) -> None:
        """A source's asset scope binds to the instrument it actually resolved to.

        Module 3I.2. ``OPEN_INTEREST`` is deliberately eligible under both the
        FUTURES and CRYPTO scopes -- there is one open-interest authority, not
        two -- so without this rule a futures-authorized source could write open
        interest for a crypto perpetual. Scope is checked against the resolved
        instrument rather than against the kind alone.
        """
        if scope is AssetScope.US_EQUITIES_ETFS:
            if instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}:
                raise HistoricalDataResolutionError("historical_source_asset_out_of_scope")
            return
        if scope is AssetScope.FUTURES:
            if instrument.instrument_type is not InstrumentType.FUTURE:
                raise HistoricalDataResolutionError("historical_source_asset_out_of_scope")
            return
        if (
            instrument.asset_class is not AssetClass.CRYPTO
            or instrument.instrument_type not in _CRYPTO_INSTRUMENT_TYPES
        ):
            raise HistoricalDataResolutionError("historical_source_asset_out_of_scope")

    def _require_instrument_eligible_for_kind(
        self, kind: ObservationKind, instrument: ProfessionalInstrument
    ) -> None:
        """Which instruments a given observation kind may legitimately describe."""
        if kind in _EQUITY_KINDS and kind is not ObservationKind.OHLCV:
            if instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}:
                raise HistoricalDataResolutionError("historical_source_asset_out_of_scope")
            return
        if kind in CRYPTO_OBSERVATION_KINDS:
            if instrument.instrument_type not in _CRYPTO_INSTRUMENT_TYPES:
                raise HistoricalDataResolutionError(
                    f"kind_requires_crypto_instrument:{kind.value}:"
                    f"{instrument.instrument_type.value}"
                )
            return
        if kind in TYPED_PAYLOAD_KINDS:
            # SETTLEMENT_PRICE and OPEN_INTEREST reach here. Open interest is
            # cross-asset, so a crypto instrument is legitimate for it and its
            # own eligibility (derivative only, correct unit asset) is judged by
            # validate_crypto_open_interest against the 3H.2 specification.
            if (
                kind is ObservationKind.OPEN_INTEREST
                and instrument.instrument_type in _CRYPTO_INSTRUMENT_TYPES
            ):
                return
            if instrument.instrument_type is not InstrumentType.FUTURE:
                raise HistoricalDataResolutionError(
                    f"kind_requires_futures_instrument:{kind.value}:"
                    f"{instrument.instrument_type.value}"
                )
            if instrument.continuous_parent_id is not None:
                # A continuous series is a derived research view over real
                # contracts (Module 3H.1). Attaching an exchange-published
                # settlement or open-interest record to it would invent a
                # measurement no exchange ever published.
                raise HistoricalDataResolutionError(
                    "continuous_series_cannot_carry_contract_observations"
                )
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM futures_contract_specifications WHERE instrument_id=%s",
                    (instrument.instrument_id,),
                )
                if cursor.fetchone() is None:
                    raise HistoricalDataResolutionError(
                        f"instrument_has_no_futures_contract_specification:"
                        f"{instrument.instrument_id}"
                    )

    def seal_dataset(
        self, source_id: UUID, version: str, normalization_version: str,
        normalized_ids: tuple[UUID, ...], created_at: datetime,
    ) -> HistoricalDatasetVersion:
        _text(version, "dataset_version")
        _text(normalization_version, "normalization_version")
        _aware(created_at, "dataset_created_at")
        if not normalized_ids or len(set(normalized_ids)) != len(normalized_ids):
            raise HistoricalMarketDataError("invalid_dataset_members")
        placeholders = ",".join(["%s"] * len(normalized_ids))
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                # The typed payload columns are joined in so the sealed content
                # hash covers the canonical financial value itself. Hashing
                # normalized_value alone would hash only the pointer marker for
                # typed kinds, and a settlement price could then change without
                # changing dataset identity.
                "SELECT n.normalized_observation_id,n.normalization_version,n.quality_status,"
                "n.normalized_value,n.normalized_at,r.source_id,r.event_at,r.ingested_at,"
                "r.raw_payload_sha256,r.observation_kind,"
                f"{_TYPED_PAYLOAD_COLUMNS} "
                "FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                f"{_TYPED_PAYLOAD_JOINS} "
                f"WHERE n.normalized_observation_id IN ({placeholders})",  # nosec B608 - fixed fragments and placeholders only
                normalized_ids,
            )
            rows = cursor.fetchall()
        if len(rows) != len(normalized_ids):
            raise HistoricalMarketDataError("dataset_member_not_found")
        if any(UUID(str(row[5])) != source_id for row in rows):
            raise HistoricalMarketDataError("dataset_source_mismatch")
        if any(str(row[1]) != normalization_version for row in rows):
            raise HistoricalMarketDataError("dataset_normalization_version_mismatch")
        if any(str(row[2]) != QualityStatus.VALIDATED.value for row in rows):
            raise HistoricalDataQualityError("rejected_observation_cannot_enter_dataset")
        if any(cast(datetime, row[4]) > created_at or cast(datetime, row[7]) > created_at for row in rows):
            raise HistoricalMarketDataError("dataset_created_before_member_available")
        digest = hashlib.sha256()
        for row in sorted(rows, key=lambda item: str(item[0])):
            canonical = "|".join(
                (
                    str(row[0]),
                    str(row[8]),
                    str(row[9]),
                    _canonical(cast(dict[str, object], row[3])),
                    *_sealed_typed_components(row),
                )
            )
            digest.update(canonical.encode())
        result = HistoricalDatasetVersion(
            uuid4(), source_id, version, normalization_version, digest.hexdigest(),
            min(cast(datetime, row[6]) for row in rows),
            max(cast(datetime, row[6]) for row in rows), created_at,
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_dataset_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'SEALED')",
                    (result.dataset_version_id, result.source_id, result.version,
                     result.normalization_version, result.content_hash, result.valid_from,
                     result.valid_until, result.created_at),
                )
                for normalized_id in normalized_ids:
                    cursor.execute(
                        "INSERT INTO historical_dataset_members VALUES (%s,%s)",
                        (result.dataset_version_id, normalized_id),
                    )
        except Exception as error:
            raise HistoricalMarketDataError("historical_dataset_seal_failed") from error
        return result

    def research_query(
        self, dataset_version_id: UUID, instrument_id: str, start: datetime, end: datetime,
        knowledge_at: datetime, *, allow_latest_adjusted: bool = False,
    ) -> tuple[HistoricalResearchObservation, ...]:
        for value, name in ((start, "start"), (end, "end"), (knowledge_at, "knowledge_at")):
            _aware(value, name)
        if end < start or knowledge_at < start:
            raise HistoricalMarketDataError("invalid_historical_research_window")
        latest_filter = "" if allow_latest_adjusted else "AND r.adjustment_status <> 'LATEST_ADJUSTED'"
        statement = (
            "WITH ranked AS (SELECT n.instrument_id,r.observation_kind,s.provider,r.provider_identifier," 
            "r.provider_symbol,r.exchange,r.event_at,r.effective_at,r.ingested_at,r.adjustment_status," 
            "r.revision,r.provenance_uri,r.raw_payload_sha256,n.normalized_value,d.version,"
            f"{_TYPED_PAYLOAD_COLUMNS},"
            "ROW_NUMBER() OVER (PARTITION BY r.source_id,r.provider_identifier,r.observation_kind,r.event_at "
            "ORDER BY r.revision DESC,r.ingested_at DESC) AS rank "
            "FROM historical_dataset_members m "
            "JOIN historical_dataset_versions d ON d.dataset_version_id=m.dataset_version_id "
            "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "JOIN historical_data_sources s ON s.source_id=r.source_id "
            f"{_TYPED_PAYLOAD_JOINS} "
            "WHERE d.dataset_version_id=%s AND d.status='SEALED' AND d.created_at<=%s "
            "AND n.instrument_id=%s AND n.quality_status='VALIDATED' AND n.normalized_at<=%s " 
            "AND r.event_at BETWEEN %s AND %s AND r.event_at<=%s AND r.ingested_at<=%s "
            f"{latest_filter}) SELECT * FROM ranked WHERE rank=1 ORDER BY event_at,observation_kind"  # nosec B608 - fixed policy fragment
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                statement,
                (dataset_version_id, knowledge_at, instrument_id, knowledge_at,
                 start, end, knowledge_at, knowledge_at),
            )
            rows = cursor.fetchall()
        return tuple(self._research_observation(row) for row in rows)

    @staticmethod
    def _research_observation(row: tuple[object, ...]) -> HistoricalResearchObservation:
        kind = ObservationKind(str(row[1]))
        typed = _typed_payload_from_row(kind, row, 15)
        if kind in TYPED_PAYLOAD_KINDS and typed is None:
            raise HistoricalMarketDataError(
                f"typed_observation_missing_canonical_payload:{row[3]}"
            )
        # For typed kinds the value is synthesized from the canonical row, so
        # research callers see one authority projected -- never a stored second
        # copy that could have drifted from it.
        normalized_value = (
            typed.as_normalized_projection()
            if typed is not None
            else cast("dict[str, object]", row[13])
        )
        return HistoricalResearchObservation(
            instrument_id=str(row[0]), observation_kind=kind,
            provider=str(row[2]), provider_identifier=str(row[3]), provider_symbol=str(row[4]),
            exchange=str(row[5]), event_at=cast(datetime, row[6]),
            effective_at=cast(datetime, row[7]), ingested_at=cast(datetime, row[8]),
            adjustment_status=AdjustmentStatus(str(row[9])), revision=int(str(row[10])),
            provenance_uri=str(row[11]), raw_payload_sha256=str(row[12]),
            normalized_value=normalized_value, data_version=str(row[14]),
        )
