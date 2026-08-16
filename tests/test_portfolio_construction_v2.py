import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from trade_platform.portfolio_construction_v2 import (
    ConstraintState,
    ConstructionStatus,
    CovarianceEstimateV2,
    PortfolioConstructionPolicyV2,
    PortfolioConstructionV2Error,
    StrategySleeveInputV2,
    construct_multi_strategy_portfolio,
)

NOW = datetime(2025, 2, 1, tzinfo=UTC)
REGIME_RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
REGIME_HASH = "a" * 64
HEALTH_ID = UUID("20000000-0000-0000-0000-000000000001")
COVARIANCE_DATASET_ID = UUID("20000000-0000-0000-0000-000000000002")
COVARIANCE_DATASET_HASH = "b" * 64


def policy() -> PortfolioConstructionPolicyV2:
    return PortfolioConstructionPolicyV2(
        policy_id=UUID("30000000-0000-0000-0000-000000000001"),
        version="cycle205-v1",
        implementation_version="portfolio-construction-v2.0.0",
        equity=Decimal("1000000"),
        target_volatility=Decimal("0.12"),
        maximum_gross_leverage=Decimal("1"),
        maximum_net_leverage=Decimal("1"),
        maximum_sleeve_weight=Decimal("0.7"),
        maximum_stress_loss_weight=Decimal("0.30"),
        minimum_liquidity_score=Decimal("0.5"),
        maximum_drawdown=Decimal("0.20"),
        minimum_decay_score=Decimal("0.4"),
        covariance_uncertainty=Decimal("0.25"),
        correlation_stress=Decimal("0.50"),
        stress_shock=Decimal("-0.20"),
        group_weight_limits=(("asset_class:EQUITY", Decimal("1")),),
        factor_exposure_limits=(("market", Decimal("1")),),
        created_at=NOW,
    )


def sleeve(key: str, *, volatility: str, regime: str = "1") -> StrategySleeveInputV2:
    return StrategySleeveInputV2(
        strategy_key=key,
        strategy_version_id=uuid4(),
        scorecard_id=uuid4(),
        validation_package_id=uuid4(),
        regime_candidate_id=uuid4(),
        normalized_signal=Decimal("0.8"),
        standalone_volatility=Decimal(volatility),
        risk_budget=Decimal("0.5"),
        capacity_weight=Decimal("0.9"),
        liquidity_score=Decimal("0.9"),
        drawdown=Decimal("0.05"),
        decay_score=Decimal("0.8"),
        regime_current_multiplier=Decimal("1"),
        regime_proposed_multiplier=Decimal(regime),
        delta_adjusted_multiplier=Decimal("1"),
        pending_order_weight=Decimal("0"),
        fx_weight=Decimal("0"),
        groups=(
            ("asset_class", "EQUITY"),
            ("sector", "DIVERSIFIED"),
            ("country", "US"),
            ("currency", "USD"),
            ("broker", "PAPER"),
            ("exchange", "MULTI"),
            ("correlation_cluster", "RISK"),
        ),
        factor_loadings=(("market", Decimal("1")),),
    )


def covariance(keys: tuple[str, ...] = ("breakout", "trend")) -> CovarianceEstimateV2:
    return CovarianceEstimateV2(
        strategy_keys=keys,
        matrix=(
            (Decimal("0.04"), Decimal("0.018")),
            (Decimal("0.018"), Decimal("0.09")),
        ),
        dataset_version_id=COVARIANCE_DATASET_ID,
        dataset_version="cycle205-covariance-v1",
        dataset_content_hash=COVARIANCE_DATASET_HASH,
        estimation_version="synthetic-oos-covariance-v1",
        observations=120,
        as_of=NOW - timedelta(days=1),
    )


def run(
    sleeves: tuple[StrategySleeveInputV2, ...],
    *,
    health: str = "HEALTHY",
    estimate: CovarianceEstimateV2 | None = None,
):
    return construct_multi_strategy_portfolio(
        policy(),
        regime_run_id=REGIME_RUN_ID,
        regime_content_hash=REGIME_HASH,
        covariance=covariance() if estimate is None else estimate,
        sleeves=sleeves,
        data_health_status=health,
        data_health_assessment_ids=(HEALTH_ID,),
        constructed_at=NOW,
    )


class PortfolioConstructionV2Tests(unittest.TestCase):
    def test_deterministic_constrained_candidate_binds_all_authorities(self) -> None:
        sleeves = (sleeve("trend", volatility="0.30"), sleeve("breakout", volatility="0.20"))
        first = run(sleeves)
        second = run(sleeves)

        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.status, ConstructionStatus.REVIEW_REQUIRED)
        self.assertEqual(first.target.status, ConstructionStatus.REVIEW_REQUIRED)
        self.assertTrue(first.risk_gate.approved)
        self.assertFalse(first.target.automatic_authority)
        self.assertFalse(first.risk_gate.automatic_authority)
        self.assertLessEqual(first.target.gross_weight, first.policy.maximum_gross_leverage)
        self.assertLessEqual(first.target.stressed_volatility, first.policy.target_volatility)
        self.assertGreater(first.target.cash_weight, Decimal("0"))
        self.assertEqual(tuple(item.strategy_key for item in first.sleeves), ("breakout", "trend"))
        self.assertTrue(all(item.scorecard_id for item in first.sleeves))
        self.assertTrue(all(item.validation_package_id for item in first.sleeves))
        self.assertTrue(all(item.regime_candidate_id for item in first.sleeves))
        self.assertIn("SYNTHETIC_ENGINEERING_EVIDENCE_ONLY", first.limitations)
        self.assertIn("NO_SIGNAL_ORDER_OMS_BROKER_OR_EXECUTION_AUTHORITY", first.limitations)

    def test_regime_uncertainty_capacity_liquidity_drawdown_and_decay_only_reduce(self) -> None:
        baseline = sleeve("breakout", volatility="0.20")
        reduced = sleeve("trend", volatility="0.30", regime="0.5")
        result = run((reduced, baseline))
        weights = {item.strategy_key: item for item in result.target.sleeves}
        self.assertIn("regime_reduction", weights["trend"].adjustment_reasons)
        self.assertIn("capacity_reduction", weights["trend"].adjustment_reasons)

        disabled = replace(
            reduced,
            liquidity_score=Decimal("0.1"),
            drawdown=Decimal("0.3"),
            decay_score=Decimal("0.1"),
        )
        disabled_result = run((disabled, baseline))
        disabled_weight = next(
            item for item in disabled_result.target.sleeves if item.strategy_key == "trend"
        )
        self.assertEqual(disabled_weight.target_weight, Decimal("0"))
        self.assertEqual(
            set(disabled_weight.adjustment_reasons),
            {
                "regime_reduction",
                "capacity_reduction",
                "liquidity_disabled",
                "drawdown_disabled",
                "signal_decay_disabled",
            },
        )
        raw_off_diagonal = covariance().matrix[0][1]
        self.assertGreater(result.stressed_covariance[0][1], raw_off_diagonal)

    def test_ineligible_or_blocked_data_health_fails_closed_with_zero_candidate(self) -> None:
        invalid = replace(
            sleeve("trend", volatility="0.30"),
            validation_integrity="LEGACY_UNVERIFIABLE",
        )
        other = sleeve("breakout", volatility="0.20")
        ineligible = run((invalid, other))
        self.assertEqual(ineligible.status, ConstructionStatus.BLOCKED)
        self.assertEqual(ineligible.target.gross_weight, Decimal("0"))
        self.assertIn("trend:validation_package_not_verified", ineligible.reasons)

        blocked_health = run((invalid, other), health="BLOCKED")
        self.assertEqual(blocked_health.status, ConstructionStatus.BLOCKED)
        self.assertEqual(blocked_health.target.gross_weight, Decimal("0"))
        self.assertIn("portfolio_data_health_blocked", blocked_health.reasons)

    def test_binding_future_covariance_risk_increase_and_hidden_leverage_are_rejected(self) -> None:
        trend = sleeve("trend", volatility="0.30")
        breakout = sleeve("breakout", volatility="0.20")
        with self.assertRaisesRegex(PortfolioConstructionV2Error, "covariance_strategy_binding_mismatch"):
            run((trend, breakout), estimate=covariance(("breakout", "missing")))
        with self.assertRaisesRegex(PortfolioConstructionV2Error, "future_covariance_rejected"):
            run(
                (trend, breakout),
                estimate=replace(covariance(), as_of=NOW + timedelta(seconds=1)),
            )

        increased = replace(trend, regime_proposed_multiplier=Decimal("1.1"))
        increase_run = run((increased, breakout))
        self.assertEqual(increase_run.status, ConstructionStatus.BLOCKED)
        self.assertIn("trend:regime_risk_increase_prohibited", increase_run.reasons)
        self.assertEqual(increase_run.target.gross_weight, Decimal("0"))

        hidden = replace(trend, pending_order_weight=Decimal("1.2"))
        hidden_run = run((hidden, breakout))
        self.assertEqual(hidden_run.status, ConstructionStatus.BLOCKED)
        self.assertIn("pending_or_fx_hidden_leverage_exceeds_policy", hidden_run.reasons)
        self.assertFalse(hidden_run.risk_gate.approved)
        state = next(
            item.state for item in hidden_run.constraints if item.name == "aggregate_exposure_limits"
        )
        self.assertEqual(state, ConstraintState.BLOCKED)


if __name__ == "__main__":
    unittest.main()
