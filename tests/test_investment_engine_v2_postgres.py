import hashlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from tests.test_investment_engine_v2 import (
    HEALTH_ID,
    fixture_analysis,
    fixture_facts,
    fixture_macro,
    fixture_thesis,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class InvestmentEngineV2PostgresTests(unittest.TestCase):
    def test_complete_authority_is_immutable_restartable_and_non_executing(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.investment_engine_v2 import (
            InvestmentEngineV2Error,
            InvestmentPortfolioPolicyV2,
            InvestmentResearchOrchestratorV2,
            PostgresInvestmentEngineV2Store,
            build_rebalance_candidate,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.pit_fundamentals import (
            AuthorizedFilingSource,
            PostgresPitFundamentalStore,
        )
        from trade_platform.pit_macro import AuthorizedMacroSource, PostgresPitMacroStore
        from trade_platform.professional_instruments import (
            PostgresProfessionalInstrumentMaster,
            mvp_instrument_universe,
        )

        dsn = os.environ["POSTGRES_TEST_DSN"]
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        )
        command.upgrade(config, "head")
        database = PostgresDatabase(dsn)
        now = datetime(2025, 5, 1, tzinfo=UTC)
        thesis = fixture_thesis(thesis_id=uuid4())
        professional = replace(
            mvp_instrument_universe(now)[0],
            instrument_id=thesis.pit_instrument_id,
            canonical_symbol="CYCLE203",
        )
        PostgresProfessionalInstrumentMaster(database).register(professional)
        exchange_id = uuid4()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO exchanges VALUES (%s,%s,'Cycle 203','UTC',%s)",
                (exchange_id, f"C{str(exchange_id)[:6]}", now),
            )
            cursor.execute(
                "INSERT INTO instruments VALUES (%s,%s,'CYCLE203','EQUITY','USD',0.01,1,%s,NULL,%s)",
                (thesis.instrument_id, exchange_id, now, now),
            )
            cursor.execute(
                "INSERT INTO accounts VALUES ('investment-cycle203','INVESTMENT','USD',%s)",
                (now,),
            )
            cursor.execute(
                "INSERT INTO accounts VALUES ('paper-cycle203','PAPER','USD',%s)",
                (now,),
            )
            cursor.execute(
                "INSERT INTO data_health_assessments VALUES (%s,NULL,'GLOBAL','*','cycle203-fixture-v1',%s,%s,%s,'INFO',FALSE,%s,'{}'::jsonb)",
                (
                    HEALTH_ID,
                    now,
                    now,
                    now,
                    hashlib.sha256(b"cycle203-data-health").hexdigest(),
                ),
            )

        facts = fixture_facts()
        filing_source = AuthorizedFilingSource(
            "cycle203-fixture", "test-only", "fixture://cycle203/authorization", now, now,
            facts[0].filing.source_id,
        )
        fundamental_store = PostgresPitFundamentalStore(database)
        fundamental_store.register_source(filing_source)
        fundamental_store.ingest(facts[0].filing, tuple(item.fact for item in facts))
        macro_observation = fixture_macro()
        macro_source = AuthorizedMacroSource(
            "cycle203-macro-fixture", "test-only", "fixture://cycle203/macro-authorization",
            now, now, macro_observation.source_id,
        )
        macro_store = PostgresPitMacroStore(database)
        macro_store.register_source(macro_source)
        macro_store.ingest((macro_observation,))

        analysis = InvestmentResearchOrchestratorV2(
            fundamental_store, macro_store
        ).analyze(
            thesis, as_of=now, data_health_status="HEALTHY",
            data_health_assessment_ids=(HEALTH_ID,),
        )
        expected = replace(fixture_analysis(), thesis_id=thesis.thesis_id, thesis_content_hash=thesis.content_hash())
        self.assertEqual(analysis.content_hash, expected.content_hash)
        policy = InvestmentPortfolioPolicyV2(
            uuid4(), "1.0.0", "investment-cycle203", "USD", Decimal("100000"),
            Decimal("0.40"), Decimal("0.10"), Decimal("0.30"), "investment-committee", now,
        )
        candidate = build_rebalance_candidate(
            policy,
            as_of=now,
            holdings_snapshot_hash="a" * 64,
            current_weights={thesis.pit_instrument_id: Decimal("0.20")},
            candidate_weights={thesis.pit_instrument_id: Decimal("0.30")},
            analyses=(analysis,),
        )
        store = PostgresInvestmentEngineV2Store(database)
        self.assertEqual(store.publish_thesis(thesis), thesis.thesis_id)
        self.assertEqual(store.publish_thesis(thesis), thesis.thesis_id)
        self.assertEqual(store.publish_analysis(analysis), analysis.analysis_id)
        self.assertEqual(store.publish_analysis(analysis), analysis.analysis_id)
        self.assertEqual(store.publish_policy(policy), policy.policy_version_id)
        self.assertEqual(store.publish_rebalance_candidate(candidate), candidate.candidate_id)
        with self.assertRaisesRegex(
            InvestmentEngineV2Error, "investment_policy_requires_investment_account"
        ):
            store.publish_policy(replace(policy, account_id="paper-cycle203"))
        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE investment_theses SET status='RETIRED' WHERE thesis_id=%s",
                (thesis.thesis_id,),
            )
        database.close()

        reopened = PostgresDatabase(dsn)
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash FROM investment_theses WHERE thesis_id=%s",
                (thesis.thesis_id,),
            )
            self.assertEqual(str(cursor.fetchone()[0]), thesis.content_hash())
            cursor.execute(
                "SELECT status,execution_authority FROM investment_rebalance_candidates WHERE candidate_id=%s",
                (candidate.candidate_id,),
            )
            self.assertEqual(cursor.fetchone(), ("REVIEW_REQUIRED", False))
            cursor.execute("SELECT COUNT(*) FROM paper_order_intents WHERE account_id='investment-cycle203'")
            self.assertEqual(int(cursor.fetchone()[0]), 0)
            cursor.execute("SELECT COUNT(*) FROM signals WHERE instrument_id=%s", (thesis.instrument_id,))
            self.assertEqual(int(cursor.fetchone()[0]), 0)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
