"""Authorized, provider-neutral PostgreSQL historical market-data pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

from .domain import AssetClass
from .persistence import PostgresDatabase
from .professional_instruments import (
    InstrumentResolutionError,
    PostgresProfessionalInstrumentMaster,
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


class AdjustmentStatus(StrEnum):
    RAW = "RAW"
    AS_REPORTED = "AS_REPORTED"
    POINT_IN_TIME_ADJUSTED = "POINT_IN_TIME_ADJUSTED"
    LATEST_ADJUSTED = "LATEST_ADJUSTED"


class QualityStatus(StrEnum):
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


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
    asset_scope: str = "US_EQUITIES_ETFS"
    source_id: UUID = field(default_factory=uuid4)

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
        if self.asset_scope != "US_EQUITIES_ETFS" or self.created_at < self.authorized_at:
            raise HistoricalDataAuthorizationError("invalid_historical_source_authorization")


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

    def register_source(self, source: AuthorizedHistoricalSource) -> None:
        source.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO historical_data_sources VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (source.source_id, source.provider, source.dataset_name,
                     source.provider_identifier_namespace, source.provider_terms_version,
                     source.authorization_reference, source.authorized_at, source.asset_scope,
                     source.created_at),
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
                "SELECT r.source_id,r.observation_kind,r.provider_identifier,r.exchange,r.event_at,r.ingested_at,r.raw_payload,s.provider_identifier_namespace "
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
        if instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}:
            raise HistoricalDataResolutionError("historical_source_asset_out_of_scope")
        issues: list[str] = []
        raw_exchange = str(row[3])
        if raw_exchange == CONSOLIDATED_TAPE_EXCHANGE:
            if instrument.venue not in _CONSOLIDATED_TAPE_ELIGIBLE_VENUES:
                issues.append("exchange_instrument_mismatch")
        elif raw_exchange not in {instrument.venue, instrument.mic}:
            issues.append("exchange_instrument_mismatch")
        payload = cast(dict[str, object], row[6])
        normalized, payload_issues = normalize_payload(ObservationKind(str(row[1])), payload)
        issues.extend(payload_issues)
        result = NormalizedHistoricalObservation(
            uuid4(), raw_observation_id, instrument.instrument_id, normalization_version,
            normalized, QualityStatus.REJECTED if issues else QualityStatus.VALIDATED,
            tuple(dict.fromkeys(issues)), normalized_at,
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
        except Exception as error:
            raise HistoricalMarketDataError("historical_normalization_persistence_failed") from error
        return result

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
                "SELECT n.normalized_observation_id,n.normalization_version,n.quality_status,n.normalized_value,n.normalized_at,r.source_id,r.event_at,r.ingested_at,r.raw_payload_sha256 "
                f"FROM historical_normalized_observations n JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id WHERE n.normalized_observation_id IN ({placeholders})",  # nosec B608 - placeholders only
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
            digest.update(f"{row[0]}|{row[8]}|{_canonical(cast(dict[str, object], row[3]))}".encode())
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
            "ROW_NUMBER() OVER (PARTITION BY r.source_id,r.provider_identifier,r.observation_kind,r.event_at " 
            "ORDER BY r.revision DESC,r.ingested_at DESC) AS rank " 
            "FROM historical_dataset_members m " 
            "JOIN historical_dataset_versions d ON d.dataset_version_id=m.dataset_version_id " 
            "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id " 
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id " 
            "JOIN historical_data_sources s ON s.source_id=r.source_id " 
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
        return tuple(
            HistoricalResearchObservation(
                instrument_id=str(row[0]), observation_kind=ObservationKind(str(row[1])),
                provider=str(row[2]), provider_identifier=str(row[3]), provider_symbol=str(row[4]),
                exchange=str(row[5]), event_at=cast(datetime, row[6]),
                effective_at=cast(datetime, row[7]), ingested_at=cast(datetime, row[8]),
                adjustment_status=AdjustmentStatus(str(row[9])), revision=int(str(row[10])),
                provenance_uri=str(row[11]), raw_payload_sha256=str(row[12]),
                normalized_value=cast(dict[str, object], row[13]), data_version=str(row[14]),
            )
            for row in rows
        )
