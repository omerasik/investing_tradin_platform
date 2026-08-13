"""Evidence gate for research promotion; this module grants no execution authority."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

from .cross_engine import CrossEngineReport, RealisticGoldenRunReport
from .domain import utc_now
from .research_validation import MultipleTestingResult, WalkForwardResult
from .strategy_validation import StrategyRunCard, StrategyValidationError
from .quant_validation import REQUIRED_EVIDENCE, SQLiteValidationEvidenceStore, StrategyValidationPackage


class PromotionStatus(str, Enum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_held_out_periods: int = 20
    minimum_total_return: Decimal = Decimal("0")
    minimum_probabilistic_sharpe: Decimal = Decimal("0.95")
    required_artifacts: tuple[str, ...] = REQUIRED_EVIDENCE
    maximum_evidence_age: timedelta = timedelta(days=90)

    def validate(self) -> None:
        if self.minimum_held_out_periods < 1 or self.maximum_evidence_age <= timedelta(0) or not Decimal("0") <= self.minimum_probabilistic_sharpe <= Decimal("1"):
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
    # Compatibility entry point.  Identifiers supplied by a caller are not
    # proof: only ``evaluate_promotion_package`` may grant review status after
    # resolving persisted, content-addressed evidence.
    if artifact_ids:
        reasons.append("unresolved_persisted_validation_package")
    else:
        reasons.append("missing_persisted_validation_package")

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


def evaluate_promotion_package(*, run_card: StrategyRunCard, package_id: UUID,
                               evidence_store: SQLiteValidationEvidenceStore,
                               walk_forward_results: tuple[WalkForwardResult, ...],
                               cross_engine_report: CrossEngineReport,
                               probabilistic_sharpe: Decimal,
                               policy: PromotionPolicy = PromotionPolicy()) -> PromotionDecision:
    """Resolve a complete persisted research package; arbitrary IDs are never evidence.

    This remains a research-only gate: a clean package is *only*
    ``REVIEW_REQUIRED`` and cannot activate paper or live trading.
    """
    policy.validate(); reasons: list[str] = []
    try:
        package = evidence_store.get(package_id)
    except KeyError:
        return PromotionDecision(uuid4(), run_card.strategy_id, run_card.strategy_version, PromotionStatus.BLOCKED,
                                 ("missing_validation_package",), 0, None, utc_now())
    if package.get("artifact_type") != "StrategyValidationPackage":
        reasons.append("invalid_validation_package")
    if package.get("strategy_version") != run_card.strategy_version:
        reasons.append("validation_package_strategy_mismatch")
    expected_dataset = run_card.required_datasets[0] if len(run_card.required_datasets) == 1 else None
    if expected_dataset is not None and package.get("dataset_version") != expected_dataset:
        reasons.append("validation_package_dataset_mismatch")
    if tuple(package.get("feature_versions", ())) != run_card.feature_versions:
        reasons.append("validation_package_feature_mismatch")
    if package.get("cost_model_version") != run_card.cost_model_version:
        reasons.append("validation_package_cost_model_mismatch")
    evidence_ids = package.get("evidence_ids", {})
    if not isinstance(evidence_ids, dict):
        reasons.append("invalid_validation_package_evidence_map"); evidence_ids = {}
    # Policy may add evidence, but may not weaken the P0 minimum package.
    required_evidence = tuple(dict.fromkeys(REQUIRED_EVIDENCE + policy.required_artifacts))
    for evidence_name in required_evidence:
        raw_id = evidence_ids.get(evidence_name)
        if not raw_id:
            reasons.append(f"missing_{evidence_name}_evidence"); continue
        try:
            artifact = evidence_store.get(UUID(str(raw_id)))
        except (KeyError, ValueError):
            reasons.append(f"missing_{evidence_name}_evidence"); continue
        if artifact.get("strategy_version") != run_card.strategy_version:
            reasons.append(f"{evidence_name}_strategy_mismatch")
        if artifact.get("dataset_version") != package.get("dataset_version"):
            reasons.append(f"{evidence_name}_dataset_mismatch")
        try:
            evaluated_at = datetime.fromisoformat(str(artifact["identity"]["evaluated_at"]))
            if utc_now() - evaluated_at > policy.maximum_evidence_age:
                reasons.append(f"{evidence_name}_evidence_stale")
        except (KeyError, TypeError, ValueError):
            reasons.append(f"invalid_{evidence_name}_timestamp")
        if evidence_name in {"data_quality", "oos_walk_forward", "golden_reconciliation", "execution_realism", "scorecard"}:
            if artifact.get("evidence_type") != evidence_name:
                reasons.append(f"invalid_{evidence_name}_evidence")
        if artifact.get("passed") is False:
            reasons.append(f"{evidence_name}_failed")
        if evidence_name == "capacity":
            levels = artifact.get("levels", [])
            if not levels or any(not bool(level.get("passed")) for level in levels):
                reasons.append("capacity_liquidity_limit_exceeded")
        elif evidence_name == "slippage":
            scenarios = artifact.get("scenarios", [])
            if not scenarios or any(not bool(item.get("meets_economic_threshold")) for item in scenarios):
                reasons.append("slippage_sensitivity_exceeds_tolerance")
        elif evidence_name == "latency":
            scenarios = artifact.get("scenarios", [])
            if not scenarios or any(not bool(item.get("passed")) for item in scenarios):
                reasons.append("latency_signal_decay_failed")
        elif evidence_name == "stress":
            scenarios = artifact.get("scenarios", [])
            if not scenarios or any(not bool(item.get("passed")) for item in scenarios):
                reasons.append("stress_evidence_weak")
        elif evidence_name == "parameter_stability" and artifact.get("narrow_performance_spike"):
            reasons.append("parameter_stability_narrow_peak")
        elif evidence_name == "multiple_testing":
            if not artifact.get("untouched_holdout_passed") or not bool(artifact.get("passed")):
                reasons.append("multiple_testing_not_passed")
    # Preserve independent/OOS checks already established in the prior gate.
    if not cross_engine_report.reconciled: reasons.append("cross_engine_reconciliation_failed")
    if not Decimal("0") <= probabilistic_sharpe <= Decimal("1"):
        reasons.append("invalid_probabilistic_sharpe")
    elif probabilistic_sharpe < policy.minimum_probabilistic_sharpe:
        reasons.append("probabilistic_sharpe_below_threshold")
    returns = tuple(period for item in walk_forward_results for period in item.result.period_returns)
    total_return: Decimal | None = None
    if len(returns) < policy.minimum_held_out_periods:
        reasons.append("insufficient_held_out_periods")
    else:
        equity = Decimal("1")
        for value in returns: equity *= Decimal("1") + value
        total_return = equity - Decimal("1")
        if total_return < policy.minimum_total_return: reasons.append("held_out_return_below_threshold")
    return PromotionDecision(uuid4(), run_card.strategy_id, run_card.strategy_version,
                             PromotionStatus.BLOCKED if reasons else PromotionStatus.REVIEW_REQUIRED,
                             tuple(dict.fromkeys(reasons)), len(returns), total_return, utc_now())


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
