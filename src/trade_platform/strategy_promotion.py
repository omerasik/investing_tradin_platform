"""Evidence gate for research promotion; this module grants no execution authority."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from .cross_engine import CrossEngineReport, RealisticGoldenRunReport
from .domain import utc_now
from .research_validation import MultipleTestingResult, WalkForwardResult
from .strategy_validation import StrategyRunCard, StrategyValidationError


class PromotionStatus(str, Enum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_held_out_periods: int = 20
    minimum_total_return: Decimal = Decimal("0")
    minimum_probabilistic_sharpe: Decimal = Decimal("0.95")
    required_artifacts: tuple[str, ...] = ("capacity", "data_quality", "stress")

    def validate(self) -> None:
        if self.minimum_held_out_periods < 1 or not Decimal("0") <= self.minimum_probabilistic_sharpe <= Decimal("1"):
            raise ValueError("invalid_promotion_policy")
        if not self.required_artifacts or any(not value.strip() for value in self.required_artifacts):
            raise ValueError("promotion_policy_requires_artifacts")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    decision_id: UUID
    strategy_id: UUID
    strategy_version: str
    status: PromotionStatus
    reasons: tuple[str, ...]
    held_out_periods: int
    held_out_total_return: Decimal | None
    decided_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class StrategyActivation:
    activation_id: UUID
    strategy_version: str
    active: bool
    actor: str
    effective_at: datetime
    ingested_at: datetime
    promotion_decision_id: UUID | None = None

    def validate(self) -> None:
        if not self.strategy_version.strip() or not self.actor.strip() or self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None or self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None or self.ingested_at < self.effective_at:
            raise ValueError("invalid_strategy_activation")
        if self.active and self.promotion_decision_id is None:
            raise ValueError("strategy_activation_requires_promotion_decision")


def evaluate_promotion(
    run_card: StrategyRunCard,
    walk_forward_results: tuple[WalkForwardResult, ...],
    cross_engine_report: CrossEngineReport,
    multiple_testing: MultipleTestingResult,
    probabilistic_sharpe: Decimal,
    artifact_ids: dict[str, str],
    policy: PromotionPolicy = PromotionPolicy(),
    *,
    golden_run: RealisticGoldenRunReport | None = None,
) -> PromotionDecision:
    """Return an evidence decision only; even a passing result always needs human review."""
    policy.validate()
    reasons: list[str] = []
    try:
        run_card.validate()
    except StrategyValidationError:
        reasons.append("invalid_run_card")
    if not cross_engine_report.reconciled:
        reasons.append("cross_engine_reconciliation_failed")
    if golden_run is None:
        reasons.append("missing_golden_execution_evidence")
    elif golden_run.strategy_version != run_card.strategy_version:
        reasons.append("golden_execution_strategy_mismatch")
    elif not golden_run.reconciled:
        reasons.append("golden_execution_unexplained_divergence")
    if run_card.strategy_version not in multiple_testing.discoveries:
        reasons.append("multiple_testing_not_passed")
    if not Decimal("0") <= probabilistic_sharpe <= Decimal("1"):
        reasons.append("invalid_probabilistic_sharpe")
    elif probabilistic_sharpe < policy.minimum_probabilistic_sharpe:
        reasons.append("probabilistic_sharpe_below_threshold")
    for artifact in policy.required_artifacts:
        if not artifact_ids.get(artifact, "").strip():
            reasons.append(f"missing_{artifact}_evidence")

    returns = tuple(period for item in walk_forward_results for period in item.result.period_returns)
    total_return: Decimal | None = None
    if len(returns) < policy.minimum_held_out_periods:
        reasons.append("insufficient_held_out_periods")
    else:
        equity = Decimal("1")
        for period_return in returns:
            equity *= Decimal("1") + period_return
        total_return = equity - Decimal("1")
        if total_return < policy.minimum_total_return:
            reasons.append("held_out_return_below_threshold")

    return PromotionDecision(
        uuid4(), run_card.strategy_id, run_card.strategy_version,
        PromotionStatus.BLOCKED if reasons else PromotionStatus.REVIEW_REQUIRED,
        tuple(reasons), len(returns), total_return, utc_now(),
    )


class SQLitePromotionLedger:
    """Append-only research evidence and explicit human strategy-activation ledger."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS promotion_decisions (
                decision_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
                status TEXT NOT NULL, reasons_json TEXT NOT NULL, held_out_periods INTEGER NOT NULL,
                held_out_total_return TEXT, decided_at TEXT NOT NULL)"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS strategy_activations (
                activation_id TEXT PRIMARY KEY, strategy_version TEXT NOT NULL, active INTEGER NOT NULL,
                actor TEXT NOT NULL, effective_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
                promotion_decision_id TEXT)"""
        )
        self._connection.commit()

    def append(self, decision: PromotionDecision) -> None:
        try:
            self._connection.execute(
                "INSERT INTO promotion_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(decision.decision_id), str(decision.strategy_id), decision.strategy_version, decision.status.value,
                 json.dumps(decision.reasons), decision.held_out_periods,
                 None if decision.held_out_total_return is None else str(decision.held_out_total_return), decision.decided_at.isoformat()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_promotion_decision") from error
        self._connection.commit()

    def get(self, decision_id: UUID) -> PromotionDecision:
        row = self._connection.execute("SELECT * FROM promotion_decisions WHERE decision_id = ?", (str(decision_id),)).fetchone()
        if row is None:
            raise KeyError(str(decision_id))
        return PromotionDecision(UUID(row[0]), UUID(row[1]), row[2], PromotionStatus(row[3]), tuple(json.loads(row[4])), row[5], None if row[6] is None else Decimal(row[6]), datetime.fromisoformat(row[7]))

    def append_activation(self, activation: StrategyActivation) -> None:
        activation.validate()
        if activation.active:
            try:
                decision = self.get(activation.promotion_decision_id)  # type: ignore[arg-type]
            except KeyError as error:
                raise ValueError("unknown_promotion_decision") from error
            if decision.strategy_version != activation.strategy_version or decision.status is not PromotionStatus.REVIEW_REQUIRED:
                raise ValueError("strategy_activation_requires_reviewable_matching_promotion")
        try:
            self._connection.execute(
                "INSERT INTO strategy_activations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(activation.activation_id), activation.strategy_version, int(activation.active), activation.actor,
                 activation.effective_at.isoformat(), activation.ingested_at.isoformat(),
                 None if activation.promotion_decision_id is None else str(activation.promotion_decision_id)),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("duplicate_strategy_activation") from error
        self._connection.commit()

    def strategy_enabled_as_of(self, strategy_version: str, decision_at: datetime) -> bool:
        if not strategy_version.strip() or decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("invalid_strategy_enablement_query")
        row = self._connection.execute(
            "SELECT active FROM strategy_activations WHERE strategy_version = ? AND effective_at <= ? AND ingested_at <= ? ORDER BY effective_at DESC, ingested_at DESC, rowid DESC LIMIT 1",
            (strategy_version, decision_at.isoformat(), decision_at.isoformat()),
        ).fetchone()
        return row is not None and bool(row[0])

    def close(self) -> None:
        self._connection.close()
