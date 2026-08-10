import unittest
from decimal import Decimal

from trade_platform.portfolio_risk import PortfolioExposure, PortfolioRiskPolicy, StressScenario, concentration_breaches, evaluate_portfolio, factor_exposures, gross_and_net, grouped_exposures, historical_var_cvar, maximum_drawdown_loss, stress, weighted_exposure


class PortfolioRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exposures = [PortfolioExposure("US:NYSE:SPY", Decimal("600"), "EQUITY", "INDEX"), PortfolioExposure("XAU:USD", Decimal("-100"), "COMMODITY", "METALS")]

    def test_gross_net_and_concentration(self) -> None:
        self.assertEqual(gross_and_net(self.exposures), (Decimal("700"), Decimal("500")))
        self.assertEqual(concentration_breaches(self.exposures, Decimal("1000"), Decimal("0.5")), ("US:NYSE:SPY",))

    def test_generalized_stress_records_contributions(self) -> None:
        result = stress(self.exposures, StressScenario("risk_off", {"EQUITY": Decimal("-0.1"), "COMMODITY": Decimal("0.05")}))
        self.assertEqual(result.loss, Decimal("-65"))
        self.assertEqual(result.contributions["US:NYSE:SPY"], Decimal("-60"))

    def test_policy_produces_explainable_risk_block(self) -> None:
        decision = evaluate_portfolio(self.exposures, Decimal("1000"), StressScenario("risk_off", {"EQUITY": Decimal("-0.1")}), PortfolioRiskPolicy(Decimal("650"), Decimal("0.5"), Decimal("50")))
        self.assertFalse(decision.approved)
        self.assertIn("gross_exposure_limit", decision.reasons)
        self.assertIn("concentration:US:NYSE:SPY", decision.reasons)
        self.assertIn("scenario_loss_limit:risk_off", decision.reasons)

    def test_every_stress_scenario_is_evaluated_and_worst_loss_is_retained(self) -> None:
        policy = PortfolioRiskPolicy(Decimal("1000"), Decimal("1"), Decimal("50"))
        decision = evaluate_portfolio(self.exposures, Decimal("1000"), (
            StressScenario("mild", {"EQUITY": Decimal("-0.01")}),
            StressScenario("severe", {"EQUITY": Decimal("-0.1"), "COMMODITY": Decimal("0.05")}),
        ), policy)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.stress_loss, Decimal("-65"))
        self.assertEqual(tuple(result.scenario for result in decision.stress_results), ("mild", "severe"))
        self.assertEqual(decision.reasons, ("scenario_loss_limit:severe",))

    def test_group_limits_and_historical_var_cvar_are_explicit(self) -> None:
        exposures = [PortfolioExposure("US:NYSE:SPY", Decimal("600"), "EQUITY", "INDEX", country="US", currency="USD"), PortfolioExposure("XAU:USD", Decimal("-100"), "COMMODITY", "METALS", country="GLOBAL", currency="USD")]
        self.assertEqual(grouped_exposures(exposures, "currency"), {"USD": Decimal("500")})
        metrics = historical_var_cvar([Decimal("-0.05"), Decimal("-0.03"), Decimal("0.01"), Decimal("0.02"), Decimal("0.04")], confidence=Decimal("0.4"))
        self.assertEqual((metrics.var_loss, metrics.cvar_loss), (Decimal("0.03"), Decimal("0.04")))
        policy = PortfolioRiskPolicy(Decimal("1000"), Decimal("1"), Decimal("1000"), maximum_net_notional=Decimal("400"), maximum_var_loss=Decimal("0.02"), maximum_cvar_loss=Decimal("0.03"), maximum_group_notionals={"currency:USD": Decimal("400")})
        decision = evaluate_portfolio(exposures, Decimal("1000"), StressScenario("none", {}), policy, historical_returns=[Decimal("-0.05"), Decimal("-0.03"), Decimal("0.01"), Decimal("0.02"), Decimal("0.04")], confidence=Decimal("0.4"))
        self.assertFalse(decision.approved)
        self.assertEqual(set(decision.reasons), {"net_exposure_limit", "group_exposure_limit:currency:USD", "var_limit", "cvar_limit"})

    def test_configured_var_limit_rejects_missing_history(self) -> None:
        decision = evaluate_portfolio(self.exposures, Decimal("1000"), StressScenario("none", {}), PortfolioRiskPolicy(Decimal("1000"), Decimal("1"), Decimal("1000"), maximum_var_loss=Decimal("0.1")))
        self.assertIn("missing_var_history", decision.reasons)

    def test_minimum_historical_observations_blocks_short_history(self) -> None:
        policy = PortfolioRiskPolicy(Decimal("1000"), Decimal("1"), Decimal("1000"), maximum_var_loss=Decimal("1"), minimum_historical_observations=3)
        decision = evaluate_portfolio(self.exposures, Decimal("1000"), StressScenario("none", {}), policy, historical_returns=[Decimal("-0.01")])
        self.assertIn("insufficient_historical_return_observations", decision.reasons)

    def test_factor_beta_duration_cluster_and_drawdown_limits_are_explicit(self) -> None:
        exposures = [PortfolioExposure("US:NYSE:SPY", Decimal("600"), "EQUITY", "INDEX", correlation_cluster="risk", beta=Decimal("1"), duration=Decimal("0"), factors={"market": Decimal("1")}), PortfolioExposure("US:NASDAQ:QQQ", Decimal("300"), "EQUITY", "TECH", correlation_cluster="risk", beta=Decimal("1.2"), duration=Decimal("0"), factors={"market": Decimal("1.2")})]
        self.assertEqual(factor_exposures(exposures), {"market": Decimal("960")})
        self.assertEqual(weighted_exposure(exposures, "beta"), Decimal("960"))
        self.assertEqual(maximum_drawdown_loss([Decimal("0.1"), Decimal("-0.2"), Decimal("0.05")]), Decimal("0.2"))
        policy = PortfolioRiskPolicy(Decimal("2000"), Decimal("1"), Decimal("2000"), maximum_group_notionals={"correlation_cluster:risk": Decimal("800")}, maximum_factor_notionals={"market": Decimal("900")}, maximum_beta_notional=Decimal("900"), maximum_drawdown_loss=Decimal("0.1"))
        decision = evaluate_portfolio(exposures, Decimal("1000"), StressScenario("none", {}), policy, historical_returns=[Decimal("0.1"), Decimal("-0.2"), Decimal("0.05")])
        self.assertFalse(decision.approved)
        self.assertEqual(set(decision.reasons), {"group_exposure_limit:correlation_cluster:risk", "factor_exposure_limit:market", "beta_exposure_limit", "drawdown_limit"})


if __name__ == "__main__":
    unittest.main()
