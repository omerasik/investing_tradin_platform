"""Serve a seeded Module 1B dashboard without configured UUID references."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

import uvicorn

from trade_platform.api import build_app
from trade_platform.audit import SQLiteAuditStore
from trade_platform.config import PlatformConfig
from trade_platform.investments import (
    CompanyResearchRecord,
    DcfValuation,
    InvestmentHolding,
    InvestmentMacroSensitivity,
    InvestmentPerformanceSnapshot,
    InvestmentPortfolioPolicy,
    InvestmentRebalanceDecision,
    InvestmentRecommendation,
    InvestmentThemeExposure,
    InvestmentThesis,
    SourceBackedFact,
    SQLiteInvestmentStore,
    ThesisDriftAssessment,
    ThesisReview,
    ThesisReviewOutcome,
    ThesisStatus,
)
from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries
from trade_platform.persistence import PersistenceTarget, PostgresDatabase
from trade_platform.security import InMemoryRateLimiter, OperatorAuthenticator

# Must match scripts/seed_demo_evidence.py's stable_id("investment-thesis") so the
# SQLite-backed research ledger below and the Postgres investment_theses discovery row
# describe the same thesis. The Postgres row is the authoritative discovery identity
# (status/as-of/review state); this store supplies the rich per-thesis research content
# (statement, scenarios, valuation, catalysts, risks, invalidation conditions, review
# history, recommendations) that has never lived anywhere else.
DEMO_INVESTMENT_THESIS_ID = UUID("a53b4775-a2be-53d6-8c05-99a88534393f")
# Must match the "demo-investment" account_id seeded onto investment_rebalance_candidates
# (used as portfolio_id by the Postgres investment-portfolios discovery endpoint).
DEMO_INVESTMENT_PORTFOLIO_ID = "demo-investment"
DEMO_INVESTMENT_INSTRUMENT = "DEMO:XNAS:DEMO_EQ_A"


def _seed_demo_investment_store() -> SQLiteInvestmentStore:
    """Seed the SQLite research ledger backing `/investments/theses|portfolios/{id}`.

    Without this, those two detail routes always return 503 in every environment that
    starts this server (module2b2/module2b3/module2b4 E2E, and this fixture generally),
    because `app.state.investment_store` defaults to None. This mirrors the pattern in
    scripts/serve_dashboard_fixture.py but uses the same deterministic thesis/portfolio
    identity as the Postgres demo seed so the two stores describe one coherent scenario.
    """
    store = SQLiteInvestmentStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    thesis = InvestmentThesis(
        DEMO_INVESTMENT_THESIS_ID, DEMO_INVESTMENT_INSTRUMENT, "v1",
        "Synthetic demo issuer shows durable unit economics; long-horizon research only, not investment advice.",
        365, ("fictional product launch",), ("fictional demand slowdown",), ("demo:filing:1",),
        ThesisStatus.ACTIVE, now,
    )
    store.append_thesis(thesis)
    fact = SourceBackedFact(
        uuid5(DEMO_INVESTMENT_THESIS_ID, "demo-free-cash-flow"), DEMO_INVESTMENT_INSTRUMENT,
        "free_cash_flow", Decimal("120"), "USD_MILLIONS", now, now, now, "demo:filing:1", "demo://filing/1",
    )
    store.append_fact(fact)
    store.append_company_research(CompanyResearchRecord(
        uuid5(DEMO_INVESTMENT_THESIS_ID, "demo-company-research"), DEMO_INVESTMENT_THESIS_ID,
        DEMO_INVESTMENT_INSTRUMENT, now,
        bull_case="Synthetic adoption accelerates beyond the base case.",
        base_case="Synthetic demand grows in line with the sector.",
        bear_case="Synthetic demand slows and margins compress.",
        catalysts=("fictional product launch", "fictional margin expansion"),
        invalidation_conditions=(
            "two consecutive quarters of fictional revenue decline",
            "fictional loss of largest customer",
        ),
        position_sizing_rationale="Bounded by policy maximum single weight; no execution authority.",
        replacement_candidates=("DEMO:XNAS:DEMO_EQ_B",), evidence_ids=("demo:filing:1",),
    ))
    store.append_valuation(DcfValuation(
        uuid5(DEMO_INVESTMENT_THESIS_ID, "demo-valuation"), DEMO_INVESTMENT_THESIS_ID, DEMO_INVESTMENT_INSTRUMENT,
        now, "USD", Decimal("120"), Decimal("0.05"), Decimal("0.09"), Decimal("0.02"), 5, Decimal("1000"),
        (fact.fact_id,), "finite-dcf-v1",
    ))
    store.append_recommendation(InvestmentRecommendation(
        uuid5(DEMO_INVESTMENT_THESIS_ID, "demo-recommendation"), DEMO_INVESTMENT_THESIS_ID,
        Decimal("0.2"), Decimal("0.4"), Decimal("0.6"), Decimal("-0.1"), Decimal("0.3"), ("demo:filing:1",), now,
    ))
    store.append_review(ThesisReview(
        uuid5(DEMO_INVESTMENT_THESIS_ID, "demo-review"), DEMO_INVESTMENT_THESIS_ID,
        ThesisReviewOutcome.REAFFIRM, now, "Synthetic evidence reaffirms the base case; no drift detected.",
        ("demo:filing:1",), ThesisDriftAssessment(DEMO_INVESTMENT_THESIS_ID, now, (fact.fact_id,), ()),
        now + timedelta(days=90),
    ))
    store.set_investment_portfolio_policy(InvestmentPortfolioPolicy(
        DEMO_INVESTMENT_PORTFOLIO_ID, "USD", Decimal("100000"), Decimal("0.4"), "demo-committee", now,
    ))
    store.append_investment_holding(InvestmentHolding(
        DEMO_INVESTMENT_PORTFOLIO_ID, DEMO_INVESTMENT_INSTRUMENT, Decimal("20000"), now, "demo:custody:1",
    ))
    store.append_performance_snapshot(InvestmentPerformanceSnapshot(
        DEMO_INVESTMENT_PORTFOLIO_ID, now, Decimal("20000"), Decimal("0.05"), "demo:custody:nav",
    ))
    store.append_rebalance_decision(InvestmentRebalanceDecision(
        uuid5(DEMO_INVESTMENT_THESIS_ID, "demo-rebalance-decision"), DEMO_INVESTMENT_PORTFOLIO_ID, now,
        "Reduce concentration toward policy maximum single weight.", ("demo:filing:1",),
        {DEMO_INVESTMENT_INSTRUMENT: Decimal("0.3")}, "demo-committee",
    ))
    store.append_theme_exposure(InvestmentThemeExposure(
        DEMO_INVESTMENT_INSTRUMENT, "synthetic-growth", Decimal("0.4"), now, "demo:theme:1",
    ))
    store.append_macro_sensitivity(InvestmentMacroSensitivity(
        DEMO_INVESTMENT_INSTRUMENT, "demo-rate-series", Decimal("-0.2"), now, "demo:macro:1",
    ))
    return store


def application():
    dsn = os.environ["POSTGRES_TEST_DSN"]
    config_path = Path(os.environ["MODULE1B_DASHBOARD_CONFIG_PATH"])
    token_path = Path(os.environ["MODULE1B_DASHBOARD_TOKEN_PATH"])
    token_path.write_text("module1b-fixture-token", encoding="utf-8")
    # The deliberately minimal config proves PostgreSQL discovery, rather than
    # legacy UUID settings, supplies the dashboard workspace references.
    config_path.write_text(json.dumps({
        "api_base_url": "http://127.0.0.1:8768",
        "operator_token_file": str(token_path.resolve()),
        "dashboard_origin": "http://127.0.0.1:3001",
    }), encoding="utf-8")
    database = PostgresDatabase(dsn)
    return build_app(
        PlatformConfig(
            environment="ci", persistence_target=PersistenceTarget.POSTGRES,
            persistence_location=dsn,
        ),
        SQLiteAuditStore(), OperatorAuthenticator("module1b-fixture-token"),
        InMemoryRateLimiter(max_requests=10_000),
        operator_dashboard_queries=PostgresOperatorDashboardQueries(database),
        investment_store=_seed_demo_investment_store(),
    )


if __name__ == "__main__":
    uvicorn.run(application(), host="127.0.0.1", port=8768)
