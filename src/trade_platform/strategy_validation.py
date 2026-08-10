"""Strategy run cards and leakage-resistant chronological validation plans."""

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now


class StrategyValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StrategyRunCard:
    strategy_id: UUID
    strategy_version: str
    family: str
    hypothesis: str
    required_datasets: tuple[str, ...]
    feature_versions: tuple[str, ...]
    universe_rules: str
    entry_logic: str
    exit_logic: str
    sizing_policy: str
    risk_policy: str
    cost_model_version: str
    capacity_model: str
    expected_regimes: tuple[str, ...]
    parameter_schema: dict[str, str]
    failure_conditions: tuple[str, ...]
    limitations: tuple[str, ...]
    created_at: datetime

    @classmethod
    def create(cls, *, strategy_version: str, family: str, hypothesis: str, required_datasets: tuple[str, ...], feature_versions: tuple[str, ...], universe_rules: str, entry_logic: str, exit_logic: str, sizing_policy: str, risk_policy: str, cost_model_version: str, capacity_model: str, expected_regimes: tuple[str, ...], parameter_schema: dict[str, str], failure_conditions: tuple[str, ...], limitations: tuple[str, ...]) -> "StrategyRunCard":
        return cls(uuid4(), strategy_version, family, hypothesis, required_datasets, feature_versions, universe_rules, entry_logic, exit_logic, sizing_policy, risk_policy, cost_model_version, capacity_model, expected_regimes, parameter_schema, failure_conditions, limitations, utc_now())

    def validate(self) -> None:
        scalar_fields = (self.strategy_version, self.family, self.hypothesis, self.universe_rules, self.entry_logic, self.exit_logic, self.sizing_policy, self.risk_policy, self.cost_model_version, self.capacity_model)
        if not all(item.strip() for item in scalar_fields):
            raise StrategyValidationError("incomplete_strategy_run_card")
        if not self.required_datasets or not self.feature_versions or not self.expected_regimes or not self.parameter_schema or not self.failure_conditions or not self.limitations:
            raise StrategyValidationError("strategy_run_card_requires_evidence_fields")
        if self.created_at.tzinfo is None:
            raise StrategyValidationError("strategy_run_card_timestamp_must_be_timezone_aware")


class SQLiteStrategyRegistry:
    """Append-only registry for research contracts; it is not an execution registry."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_run_cards (
                strategy_id TEXT PRIMARY KEY, strategy_version TEXT NOT NULL, family TEXT NOT NULL,
                hypothesis TEXT NOT NULL, required_datasets_json TEXT NOT NULL, feature_versions_json TEXT NOT NULL,
                universe_rules TEXT NOT NULL, entry_logic TEXT NOT NULL, exit_logic TEXT NOT NULL,
                sizing_policy TEXT NOT NULL, risk_policy TEXT NOT NULL, cost_model_version TEXT NOT NULL,
                capacity_model TEXT NOT NULL, expected_regimes_json TEXT NOT NULL, parameter_schema_json TEXT NOT NULL,
                failure_conditions_json TEXT NOT NULL, limitations_json TEXT NOT NULL, created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute("CREATE TABLE IF NOT EXISTS strategy_run_card_commands (idempotency_key TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, strategy_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL, requested_at TEXT NOT NULL)")
        self._connection.commit()

    def append(self, card: StrategyRunCard) -> None:
        card.validate()
        try:
            self._connection.execute(
                "INSERT INTO strategy_run_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(card.strategy_id), card.strategy_version, card.family, card.hypothesis, json.dumps(card.required_datasets), json.dumps(card.feature_versions), card.universe_rules, card.entry_logic, card.exit_logic, card.sizing_policy, card.risk_policy, card.cost_model_version, card.capacity_model, json.dumps(card.expected_regimes), json.dumps(card.parameter_schema, sort_keys=True), json.dumps(card.failure_conditions), json.dumps(card.limitations), card.created_at.isoformat()),
            )
        except sqlite3.IntegrityError as error:
            raise StrategyValidationError("duplicate_strategy_run_card") from error
        self._connection.commit()

    def get(self, strategy_id: UUID) -> StrategyRunCard:
        row = self._connection.execute("SELECT * FROM strategy_run_cards WHERE strategy_id = ?", (str(strategy_id),)).fetchone()
        if row is None:
            raise StrategyValidationError("unknown_strategy_run_card")
        return StrategyRunCard(UUID(row[0]), row[1], row[2], row[3], tuple(json.loads(row[4])), tuple(json.loads(row[5])), row[6], row[7], row[8], row[9], row[10], row[11], row[12], tuple(json.loads(row[13])), json.loads(row[14]), tuple(json.loads(row[15])), tuple(json.loads(row[16])), datetime.fromisoformat(row[17]))

    def create(self, card: StrategyRunCard, *, idempotency_key: str, actor: str) -> StrategyRunCard:
        if not idempotency_key.strip() or not actor.strip(): raise StrategyValidationError("invalid_strategy_create_command")
        payload = {"strategy_version": card.strategy_version, "family": card.family, "hypothesis": card.hypothesis, "required_datasets": card.required_datasets, "feature_versions": card.feature_versions, "universe_rules": card.universe_rules, "entry_logic": card.entry_logic, "exit_logic": card.exit_logic, "sizing_policy": card.sizing_policy, "risk_policy": card.risk_policy, "cost_model_version": card.cost_model_version, "capacity_model": card.capacity_model, "expected_regimes": card.expected_regimes, "parameter_schema": card.parameter_schema, "failure_conditions": card.failure_conditions, "limitations": card.limitations}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        existing = self._connection.execute("SELECT payload_digest, strategy_id FROM strategy_run_card_commands WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing is not None:
            if existing[0] != digest: raise StrategyValidationError("strategy_create_idempotency_conflict")
            return self.get(UUID(existing[1]))
        self.append(card)
        self._connection.execute("INSERT INTO strategy_run_card_commands VALUES (?, ?, ?, ?, ?)", (idempotency_key, digest, str(card.strategy_id), actor, utc_now().isoformat())); self._connection.commit()
        return card

    def close(self) -> None:
        self._connection.close()


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardSplit:
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int

    def validate(self) -> None:
        if not 0 <= self.train_start < self.train_end <= self.validation_start < self.validation_end <= self.test_start < self.test_end:
            raise StrategyValidationError("invalid_purged_walk_forward_split")

    @property
    def train_indices(self) -> range:
        return range(self.train_start, self.train_end)

    @property
    def validation_indices(self) -> range:
        return range(self.validation_start, self.validation_end)

    @property
    def test_indices(self) -> range:
        return range(self.test_start, self.test_end)


def purged_walk_forward_splits(length: int, *, train_size: int, validation_size: int, test_size: int, step: int, purge: int, embargo: int) -> list[PurgedWalkForwardSplit]:
    """Chronological splits with explicit gaps between train/validation and validation/test."""
    if min(length, train_size, validation_size, test_size, step) < 1 or purge < 0 or embargo < 0:
        raise StrategyValidationError("invalid_purged_walk_forward_parameters")
    splits: list[PurgedWalkForwardSplit] = []
    train_start = 0
    while True:
        train_end = train_start + train_size
        validation_start = train_end + purge
        validation_end = validation_start + validation_size
        test_start = validation_end + embargo
        test_end = test_start + test_size
        if test_end > length:
            break
        split = PurgedWalkForwardSplit(train_start, train_end, validation_start, validation_end, test_start, test_end)
        split.validate()
        splits.append(split)
        train_start += step
    if not splits:
        raise StrategyValidationError("insufficient_history_for_purged_walk_forward")
    return splits
