"""PostgreSQL authority for versioned, point-in-time feature materializations.

The older ``feature_platform`` module remains a small deterministic calculator.
This module is the durable authority: every definition version and every value
is explicit about dataset, knowledge time, input manifest and calculation code.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from .persistence import PostgresDatabase


class FeatureAuthorityError(ValueError):
    pass


class FeatureFamily(StrEnum):
    PRICE_RETURNS = "PRICE_RETURNS"
    TREND = "TREND"
    MOMENTUM = "MOMENTUM"
    VOLATILITY = "VOLATILITY"
    LIQUIDITY = "LIQUIDITY"
    FUNDAMENTAL = "FUNDAMENTAL"
    MACRO = "MACRO"


class FeatureQualityStatus(StrEnum):
    VALIDATED = "VALIDATED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class TransparentMarketWindow:
    """Offline, ordered market inputs used only for deterministic calculation."""

    closes: tuple[Decimal, ...]
    highs: tuple[Decimal, ...]
    lows: tuple[Decimal, ...]
    volumes: tuple[Decimal, ...]
    shares_outstanding: Decimal | None = None

    def validate(self) -> None:
        if (
            len(self.closes) < 2
            or len(self.closes) != len(self.highs) != len(self.lows) != len(self.volumes)
            or any(value <= 0 for value in self.closes)
            or any(value <= 0 for value in self.highs)
            or any(value <= 0 for value in self.lows)
            or any(value < 0 for value in self.volumes)
            or any(low > close or close > high for low, close, high in zip(self.lows, self.closes, self.highs, strict=True))
            or (self.shares_outstanding is not None and self.shares_outstanding <= 0)
        ):
            raise FeatureAuthorityError("invalid_transparent_market_window")


class TransparentFeatureCalculator:
    """Auditable baselines; it never fills an unavailable fundamental/macro input."""

    @staticmethod
    def calculate(name: str, window: TransparentMarketWindow) -> Decimal:
        window.validate()
        closes, highs, lows, volumes = window.closes, window.highs, window.lows, window.volumes
        returns = tuple(closes[index] / closes[index - 1] - Decimal("1") for index in range(1, len(closes)))
        average_close = sum(closes, Decimal("0")) / Decimal(len(closes))
        if name in {"simple_return", "multi_horizon_return", "multi_horizon_momentum"}:
            return closes[-1] / closes[0] - Decimal("1")
        if name == "log_return":
            return (closes[-1] / closes[0]).ln()
        if name == "sma":
            return average_close
        if name == "ema":
            alpha = Decimal("2") / Decimal(len(closes) + 1)
            result = closes[0]
            for close in closes[1:]:
                result = alpha * close + (Decimal("1") - alpha) * result
            return result
        if name == "moving_average_distance":
            return closes[-1] / average_close - Decimal("1")
        if name == "breakout":
            return closes[-1] / max(closes) - Decimal("1")
        if name == "trend_slope":
            indexes: tuple[Decimal, ...] = tuple(Decimal(index) for index in range(len(closes)))
            mean_index = sum(indexes, Decimal("0")) / Decimal(len(indexes))
            numerator = sum(((index - mean_index) * (close - average_close) for index, close in zip(indexes, closes, strict=True)), Decimal("0"))
            denominator = sum(((index - mean_index) ** 2 for index in indexes), Decimal("0"))
            return numerator / denominator
        if name in {"realized_volatility", "downside_volatility"}:
            selected = returns if name == "realized_volatility" else tuple(min(item, Decimal("0")) for item in returns)
            average = sum(selected, Decimal("0")) / Decimal(len(selected))
            return (sum((item - average) ** 2 for item in selected) / Decimal(len(selected))).sqrt()
        if name == "range_volatility":
            return sum(((high - low) / close for high, low, close in zip(highs, lows, closes, strict=True)), Decimal("0")) / Decimal(len(closes))
        if name == "risk_adjusted_momentum":
            volatility = TransparentFeatureCalculator.calculate("realized_volatility", window)
            if volatility == 0:
                raise FeatureAuthorityError("risk_adjusted_momentum_zero_volatility")
            return TransparentFeatureCalculator.calculate("multi_horizon_momentum", window) / volatility
        if name == "dollar_volume":
            return closes[-1] * volumes[-1]
        if name == "adv":
            return sum((close * volume for close, volume in zip(closes, volumes, strict=True)), Decimal("0")) / Decimal(len(closes))
        if name == "turnover":
            if window.shares_outstanding is None:
                raise FeatureAuthorityError("turnover_requires_shares_outstanding")
            return volumes[-1] / window.shares_outstanding
        if name == "amihud_proxy":
            denominators = tuple(close * volume for close, volume in zip(closes[1:], volumes[1:], strict=True))
            if any(item == 0 for item in denominators):
                raise FeatureAuthorityError("amihud_requires_positive_dollar_volume")
            return sum((abs(change) / dollar_volume for change, dollar_volume in zip(returns, denominators, strict=True)), Decimal("0")) / Decimal(len(returns))
        raise FeatureAuthorityError("unsupported_transparent_feature")


def transparent_feature_definitions(created_at: datetime) -> tuple[FeatureDefinitionVersion, ...]:
    """Definitions, not values: unavailable real inputs intentionally stay absent."""
    market = (
        ("simple_return", FeatureFamily.PRICE_RETURNS), ("log_return", FeatureFamily.PRICE_RETURNS),
        ("multi_horizon_return", FeatureFamily.PRICE_RETURNS), ("sma", FeatureFamily.TREND),
        ("ema", FeatureFamily.TREND), ("moving_average_distance", FeatureFamily.TREND),
        ("breakout", FeatureFamily.TREND), ("trend_slope", FeatureFamily.TREND),
        ("multi_horizon_momentum", FeatureFamily.MOMENTUM), ("risk_adjusted_momentum", FeatureFamily.MOMENTUM),
        ("realized_volatility", FeatureFamily.VOLATILITY), ("downside_volatility", FeatureFamily.VOLATILITY),
        ("range_volatility", FeatureFamily.VOLATILITY), ("dollar_volume", FeatureFamily.LIQUIDITY),
        ("adv", FeatureFamily.LIQUIDITY), ("turnover", FeatureFamily.LIQUIDITY),
        ("amihud_proxy", FeatureFamily.LIQUIDITY),
    )
    fundamentals = ("revenue_growth", "operating_margin", "fcf", "debt", "dilution", "roic_components")
    macro = ("rate_level_change", "curve_slope", "inflation_change", "employment_change", "revision_or_surprise")
    definitions: list[FeatureDefinitionVersion] = []
    for name, family in market:
        definitions.append(FeatureDefinitionVersion(name, family, "1.0.0", "quant", f"Transparent {name} baseline.", ("OHLCV",), ("close", "event_at", "knowledge_at"), "1d", "event/effective/knowledge bounded", 1, {}, "reject", "reject", "reject_future_knowledge", None, None, "decimal", "transparent-market-v1", created_at))
    for name, family, dataset, fields in (
        *((name, FeatureFamily.FUNDAMENTAL, "FUNDAMENTALS", ("accepted_at", "value", "revision")) for name in fundamentals),
        *((name, FeatureFamily.MACRO, "MACRO", ("release_at", "actual", "revision")) for name in macro),
    ):
        definitions.append(FeatureDefinitionVersion(name, family, "1.0.0", "quant", f"Declared {name} baseline; materialize only with authorized PIT input.", (dataset,), fields, "release", "event/effective/knowledge bounded", 0, {}, "reject", "reject", "reject_future_knowledge", None, None, "decimal", "transparent-input-v1", created_at))
    return tuple(definitions)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureAuthorityError("feature_timestamp_must_be_timezone_aware")


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class FeatureDefinitionVersion:
    name: str
    family: FeatureFamily
    semantic_version: str
    owner: str
    description: str
    required_dataset_types: tuple[str, ...]
    required_fields: tuple[str, ...]
    frequency: str
    timestamp_semantics: str
    lookback: int
    parameters: Mapping[str, Any]
    missing_value_policy: str
    outlier_policy: str
    leakage_policy: str
    expected_minimum: Decimal | None
    expected_maximum: Decimal | None
    units: str
    calculation_version: str
    created_at: datetime
    retired_at: datetime | None = None
    feature_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        required = (
            self.name, self.semantic_version, self.owner, self.description,
            self.frequency, self.timestamp_semantics, self.missing_value_policy,
            self.outlier_policy, self.leakage_policy, self.units,
            self.calculation_version,
        )
        if not all(item.strip() for item in required) or self.lookback < 0:
            raise FeatureAuthorityError("invalid_feature_definition")
        if not self.required_dataset_types or not self.required_fields:
            raise FeatureAuthorityError("feature_requirements_missing")
        if (
            self.expected_minimum is not None
            and self.expected_maximum is not None
            and self.expected_minimum > self.expected_maximum
        ):
            raise FeatureAuthorityError("invalid_feature_expected_range")
        _aware(self.created_at)
        if self.retired_at is not None:
            _aware(self.retired_at)
            if self.retired_at < self.created_at:
                raise FeatureAuthorityError("feature_retired_before_creation")


@dataclass(frozen=True, slots=True)
class FeatureMaterialization:
    feature_id: UUID
    instrument_id: str
    dataset_version: str
    event_at: datetime
    effective_at: datetime
    knowledge_at: datetime
    computed_at: datetime
    source_observation_manifest: tuple[str, ...]
    value: Decimal | None
    quality_status: FeatureQualityStatus
    content_hash: str
    materialization_id: UUID = field(default_factory=uuid4)

    @classmethod
    def create(
        cls,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version: str,
        event_at: datetime,
        effective_at: datetime,
        knowledge_at: datetime,
        computed_at: datetime,
        source_observation_manifest: tuple[str, ...],
        value: Decimal | None,
        quality_status: FeatureQualityStatus,
    ) -> FeatureMaterialization:
        payload = {
            "feature_id": str(feature_id), "instrument_id": instrument_id,
            "dataset_version": dataset_version, "event_at": event_at.isoformat(),
            "effective_at": effective_at.isoformat(), "knowledge_at": knowledge_at.isoformat(),
            "computed_at": computed_at.isoformat(),
            "source_observation_manifest": source_observation_manifest,
            "value": None if value is None else str(value),
            "quality_status": quality_status.value,
        }
        return cls(
            feature_id, instrument_id, dataset_version, event_at, effective_at,
            knowledge_at, computed_at, source_observation_manifest, value,
            quality_status, hashlib.sha256(_canonical(payload).encode()).hexdigest(),
        )

    def validate(self) -> None:
        if not self.instrument_id.strip() or not self.dataset_version.strip():
            raise FeatureAuthorityError("feature_materialization_identity_missing")
        if len(self.content_hash) != 64 or not self.source_observation_manifest:
            raise FeatureAuthorityError("feature_materialization_provenance_missing")
        for timestamp in (self.event_at, self.effective_at, self.knowledge_at, self.computed_at):
            _aware(timestamp)
        if self.effective_at < self.event_at or self.knowledge_at < self.effective_at:
            raise FeatureAuthorityError("feature_materialization_invalid_temporal_order")
        if self.computed_at < self.knowledge_at:
            raise FeatureAuthorityError("feature_computed_before_knowledge")
        if self.quality_status is FeatureQualityStatus.VALIDATED and self.value is None:
            raise FeatureAuthorityError("validated_feature_requires_value")


class PostgresFeatureAuthority:
    """Append-only definition/value authority with strict decision-time reads."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, definition: FeatureDefinitionVersion) -> None:
        definition.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_definition_versions VALUES ("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        definition.feature_id, definition.name, definition.family.value,
                        definition.semantic_version, definition.owner, definition.description,
                        json.dumps(definition.required_dataset_types), json.dumps(definition.required_fields),
                        definition.frequency, definition.timestamp_semantics, definition.lookback,
                        json.dumps(dict(definition.parameters), sort_keys=True),
                        definition.missing_value_policy, definition.outlier_policy,
                        definition.leakage_policy, definition.expected_minimum,
                        definition.expected_maximum, definition.units, definition.calculation_version,
                        definition.created_at, definition.retired_at,
                    ),
                )
        except Exception as error:
            raise FeatureAuthorityError("feature_definition_registration_failed") from error

    def materialize(self, value: FeatureMaterialization) -> None:
        value.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_materializations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (feature_id,instrument_id,dataset_version,event_at,effective_at,knowledge_at) DO NOTHING "
                    "RETURNING content_hash",
                    (
                        value.materialization_id, value.feature_id, value.instrument_id,
                        value.dataset_version, value.event_at, value.effective_at,
                        value.knowledge_at, value.computed_at,
                        json.dumps(value.source_observation_manifest), value.value,
                        value.quality_status.value, value.content_hash,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM feature_materializations WHERE "
                        "feature_id=%s AND instrument_id=%s AND dataset_version=%s "
                        "AND event_at=%s AND effective_at=%s AND knowledge_at=%s",
                        (value.feature_id, value.instrument_id, value.dataset_version,
                         value.event_at, value.effective_at, value.knowledge_at),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != value.content_hash:
                        raise FeatureAuthorityError("feature_materialization_conflict")
        except FeatureAuthorityError:
            raise
        except Exception as error:
            raise FeatureAuthorityError("feature_materialization_failed") from error

    def latest_as_of(
        self, feature_id: UUID, instrument_id: str, dataset_version: str, decision_at: datetime
    ) -> tuple[FeatureMaterialization, ...]:
        _aware(decision_at)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY event_at "
                "ORDER BY knowledge_at DESC, computed_at DESC) rank FROM feature_materializations m "
                "WHERE feature_id=%s AND instrument_id=%s AND dataset_version=%s "
                "AND event_at<=%s AND effective_at<=%s AND knowledge_at<=%s AND computed_at<=%s) x "
                "WHERE rank=1 ORDER BY event_at",
                (feature_id, instrument_id, dataset_version, decision_at, decision_at,
                 decision_at, decision_at),
            )
            rows = cursor.fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: tuple[Any, ...]) -> FeatureMaterialization:
        manifest = row[8] if isinstance(row[8], list) else json.loads(row[8])
        return FeatureMaterialization(
            UUID(str(row[1])), str(row[2]), str(row[3]), row[4], row[5], row[6], row[7],
            tuple(str(item) for item in manifest), None if row[9] is None else Decimal(str(row[9])),
            FeatureQualityStatus(str(row[10])), str(row[11]), UUID(str(row[0])),
        )
