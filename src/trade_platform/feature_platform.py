"""Versioned, point-in-time feature definitions and deterministic computations."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import sqrt
from pathlib import Path

from .domain import OHLCVBar
from .fundamentals import SQLiteFundamentalStore
from .macro_data import SQLiteMacroReleaseStore


class FeaturePlatformError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    version: str
    owner: str
    frequency: str
    timestamp_semantics: str
    lookback: int
    sources: tuple[str, ...]
    missing_value_policy: str
    outlier_policy: str
    leakage_test: str
    expected_minimum: Decimal | None = None
    expected_maximum: Decimal | None = None

    def validate(self) -> None:
        if not all((self.name.strip(), self.version.strip(), self.owner.strip(), self.frequency.strip(), self.timestamp_semantics.strip(), self.missing_value_policy.strip(), self.outlier_policy.strip(), self.leakage_test.strip())):
            raise FeaturePlatformError("invalid_feature_definition")
        if self.lookback < 1 or not self.sources:
            raise FeaturePlatformError("feature_requires_lookback_and_sources")
        if self.expected_minimum is not None and self.expected_maximum is not None and self.expected_minimum > self.expected_maximum:
            raise FeaturePlatformError("invalid_expected_feature_range")


class FeatureRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], FeatureDefinition] = {}

    def register(self, definition: FeatureDefinition) -> None:
        definition.validate()
        key = (definition.name, definition.version)
        if key in self._definitions:
            raise FeaturePlatformError("duplicate_feature_definition")
        self._definitions[key] = definition

    def get(self, name: str, version: str) -> FeatureDefinition:
        try:
            return self._definitions[(name, version)]
        except KeyError as error:
            raise FeaturePlatformError("unknown_feature_definition") from error


@dataclass(frozen=True, slots=True)
class FeatureValue:
    instrument_id: str
    definition_name: str
    definition_version: str
    as_of: datetime
    value: Decimal
    data_version: str
    quality_score: Decimal
    input_provenance: tuple[str, ...]


class SQLiteFeatureStore:
    """Append-only feature values indexed by definition/data version and historical timestamp."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_values (
                instrument_id TEXT NOT NULL, definition_name TEXT NOT NULL, definition_version TEXT NOT NULL,
                as_of TEXT NOT NULL, value TEXT NOT NULL, data_version TEXT NOT NULL, quality_score TEXT NOT NULL,
                input_provenance_json TEXT NOT NULL,
                PRIMARY KEY (instrument_id, definition_name, definition_version, as_of, data_version)
            )
            """
        )
        self._connection.commit()

    def append(self, value: FeatureValue) -> None:
        if not value.instrument_id or not value.definition_name or not value.definition_version or value.as_of.tzinfo is None or not Decimal("0") <= value.quality_score <= Decimal("1"):
            raise FeaturePlatformError("invalid_feature_value")
        try:
            self._connection.execute(
                "INSERT INTO feature_values VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (value.instrument_id, value.definition_name, value.definition_version, value.as_of.isoformat(), str(value.value), value.data_version, str(value.quality_score), json.dumps(value.input_provenance)),
            )
        except sqlite3.IntegrityError as error:
            raise FeaturePlatformError("duplicate_feature_value") from error
        self._connection.commit()

    def available_as_of(self, instrument_id: str, name: str, version: str, decision_at: datetime) -> list[FeatureValue]:
        if decision_at.tzinfo is None:
            raise FeaturePlatformError("feature_decision_timestamp_must_be_timezone_aware")
        rows = self._connection.execute(
            "SELECT * FROM feature_values WHERE instrument_id = ? AND definition_name = ? AND definition_version = ? AND as_of <= ? ORDER BY as_of, data_version",
            (instrument_id, name, version, decision_at.isoformat()),
        ).fetchall()
        return [FeatureValue(row[0], row[1], row[2], datetime.fromisoformat(row[3]), Decimal(row[4]), row[5], Decimal(row[6]), tuple(json.loads(row[7]))) for row in rows]


class FeatureEngine:
    def __init__(self, registry: FeatureRegistry) -> None:
        self._registry = registry

    def price_return(self, definition_name: str, definition_version: str, bars: list[OHLCVBar], *, decision_at: datetime) -> FeatureValue:
        definition = self._registry.get(definition_name, definition_version)
        validated = self._validate_bars(bars, decision_at, definition.lookback + 1)
        prior, current = validated[-definition.lookback - 1], validated[-1]
        value = current.close / prior.close - Decimal("1")
        return self._value(definition, current.instrument_id, decision_at, value, validated[-definition.lookback - 1 :])

    def moving_average(self, definition_name: str, definition_version: str, bars: list[OHLCVBar], *, decision_at: datetime) -> FeatureValue:
        definition = self._registry.get(definition_name, definition_version)
        validated = self._validate_bars(bars, decision_at, definition.lookback)
        window = validated[-definition.lookback :]
        value = sum((item.close for item in window), Decimal("0")) / Decimal(definition.lookback)
        return self._value(definition, window[-1].instrument_id, decision_at, value, window)

    def realized_volatility(self, definition_name: str, definition_version: str, bars: list[OHLCVBar], *, decision_at: datetime) -> FeatureValue:
        definition = self._registry.get(definition_name, definition_version)
        validated = self._validate_bars(bars, decision_at, definition.lookback + 1)
        window = validated[-definition.lookback - 1 :]
        returns = [float(window[index].close / window[index - 1].close - Decimal("1")) for index in range(1, len(window))]
        average = sum(returns) / len(returns)
        value = Decimal(str(sqrt(sum((item - average) ** 2 for item in returns) / len(returns))))
        return self._value(definition, window[-1].instrument_id, decision_at, value, window)

    def latest_fundamental(self, definition_name: str, definition_version: str, store: SQLiteFundamentalStore, instrument_id: str, fact_name: str, *, decision_at: datetime) -> FeatureValue:
        definition = self._registry.get(definition_name, definition_version)
        facts = store.available_as_of(instrument_id, decision_at, fact_name=fact_name)
        if not facts:
            raise FeaturePlatformError("no_fundamental_fact_available")
        fact = facts[-1]
        value = fact.standardized_value if fact.standardized_value is not None else fact.as_reported_value
        provenance = f"{fact.provider}:{fact.source_identifier}:{fact.revision}"
        return FeatureValue(instrument_id, definition.name, definition.version, decision_at, value, self._data_version((provenance,)), Decimal("1"), (provenance,))

    def macro_surprise(self, definition_name: str, definition_version: str, store: SQLiteMacroReleaseStore, series_id: str, *, decision_at: datetime) -> FeatureValue:
        definition = self._registry.get(definition_name, definition_version)
        releases = store.available_as_of(series_id, decision_at)
        if not releases or releases[-1].expected is None:
            raise FeaturePlatformError("no_macro_consensus_available")
        release = releases[-1]
        provenance = f"{release.provider}:{release.source_identifier}:{release.revision}"
        return FeatureValue(series_id, definition.name, definition.version, decision_at, release.actual - release.expected, self._data_version((provenance,)), Decimal("1"), (provenance,))

    @staticmethod
    def _validate_bars(bars: list[OHLCVBar], decision_at: datetime, required: int) -> list[OHLCVBar]:
        if decision_at.tzinfo is None or len(bars) < required:
            raise FeaturePlatformError("insufficient_or_invalid_bar_history")
        if any(item.event_at.tzinfo is None or item.effective_at > decision_at or item.ingested_at > decision_at for item in bars):
            raise FeaturePlatformError("feature_input_not_available_as_of_decision")
        if any(bars[index].event_at >= bars[index + 1].event_at for index in range(len(bars) - 1)):
            raise FeaturePlatformError("feature_bars_must_be_strictly_ordered")
        if any(item.instrument_id != bars[0].instrument_id or item.close <= 0 for item in bars):
            raise FeaturePlatformError("invalid_feature_bars")
        return bars

    def _value(self, definition: FeatureDefinition, instrument_id: str, as_of: datetime, value: Decimal, bars: list[OHLCVBar]) -> FeatureValue:
        if definition.expected_minimum is not None and value < definition.expected_minimum or definition.expected_maximum is not None and value > definition.expected_maximum:
            raise FeaturePlatformError("feature_value_outside_expected_range")
        provenance = tuple(f"{item.provider}:{item.source_identifier}:{item.revision}" for item in bars)
        return FeatureValue(instrument_id, definition.name, definition.version, as_of, value, self._data_version(provenance), min(item.quality_score for item in bars), provenance)

    @staticmethod
    def _data_version(provenance: tuple[str, ...]) -> str:
        digest = hashlib.sha256("|".join(provenance).encode()).hexdigest()
        return f"feature-inputs:{digest}"
