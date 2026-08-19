"""Runs in CI or locally only when POSTGRES_TEST_DSN names an ephemeral database."""

import hashlib
import os
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        self.config = Config("alembic.ini")
        self.config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(self.config, "head")
        import psycopg

        self.connection = psycopg.connect(os.environ["POSTGRES_TEST_DSN"])
        self.now = datetime.now(UTC)
        self.exchange_id, self.instrument_id = uuid4(), uuid4()
        self.dataset_id, self.dataset_version_id = uuid4(), uuid4()
        self.strategy_id, self.strategy_version_id = uuid4(), uuid4()
        self.signal_id, self.policy_id, self.policy_version_id = uuid4(), uuid4(), uuid4()
        suffix = str(self.exchange_id)[:8]
        dataset_hash = hashlib.sha256(f"dataset:{self.dataset_version_id}".encode()).hexdigest()
        policy_hash = hashlib.sha256(f"policy:{self.policy_version_id}".encode()).hexdigest()
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO exchanges VALUES (%s, %s, 'Integration', 'UTC', %s)",
                (self.exchange_id, f"XI{suffix[:6]}", self.now),
            )
            cursor.execute(
                "INSERT INTO instruments VALUES (%s, %s, 'TEST', 'EQUITY', 'USD', 0.01, 1, %s, NULL, %s)",
                (self.instrument_id, self.exchange_id, self.now, self.now),
            )
            cursor.execute(
                "INSERT INTO datasets VALUES (%s, %s, 'fixture', 'terms-v1', %s)",
                (self.dataset_id, f"integration-data-{suffix}", self.now),
            )
            cursor.execute(
                "INSERT INTO dataset_versions VALUES (%s, %s, 'v1', %s, NULL, NULL, %s)",
                (self.dataset_version_id, self.dataset_id, dataset_hash, self.now),
            )
            cursor.execute(
                "INSERT INTO strategy_definitions VALUES (%s, 'test', 'test', %s)",
                (self.strategy_id, self.now),
            )
            cursor.execute(
                "INSERT INTO strategy_versions VALUES (%s, %s, 'v1', '{}'::jsonb, 'cost-v1', 'capacity-v1', '{}'::jsonb, %s)",
                (self.strategy_version_id, self.strategy_id, self.now),
            )
            cursor.execute(
                "INSERT INTO accounts VALUES (%s, 'PAPER', 'USD', %s)",
                (f"integration-paper-{suffix}", self.now),
            )
            cursor.execute(
                "INSERT INTO signals VALUES (%s, %s, %s, 'VALIDATED', %s, %s + interval '1 day', '{}'::jsonb)",
                (self.signal_id, self.strategy_version_id, self.instrument_id, self.now, self.now),
            )
            cursor.execute(
                "INSERT INTO risk_policies VALUES (%s, %s, %s)",
                (self.policy_id, f"integration-policy-{suffix}", self.now),
            )
            cursor.execute(
                "INSERT INTO risk_policy_versions VALUES (%s, %s, 'v1', '{}'::jsonb, %s, %s)",
                (self.policy_version_id, self.policy_id, policy_hash, self.now),
            )

    def tearDown(self) -> None:
        self.connection.close()

    def test_migration_creates_schema_and_immutable_constraint(self) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.quant_validation_artifacts')")
            self.assertEqual(cursor.fetchone()[0], "quant_validation_artifacts")
            cursor.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'quant_validation_artifacts'::regclass"
            )
            self.assertIn(
                "quant_validation_artifacts_immutable", {row[0] for row in cursor.fetchall()}
            )

    def test_policy_and_keyed_assessment_survive_restart_and_reject_tamper(self) -> None:
        from trade_platform.domain import RiskDecision, RiskDecisionType
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.policy_registry import PolicyDocument
        from trade_platform.postgres_pretrade import (
            PostgresPolicyRegistry,
            PostgresPreTradeAssessmentStore,
        )
        from trade_platform.pretrade_assessment import PreTradeAssessment

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        policies = PostgresPolicyRegistry(database)
        version = f"risk:integration:{str(self.exchange_id)[:8]}"
        document = PolicyDocument(
            "risk",
            version,
            {"maximum_order_notional": "1000"},
            "risk-reviewer",
            self.now,
        )
        policies.append(document)
        policies.append(document)
        self.assertEqual(
            policies.resolve_risk_policy(version).maximum_order_notional, Decimal(1000)
        )
        intent_id = uuid4()
        assessment = PreTradeAssessment(
            uuid4(),
            RiskDecision(
                uuid4(),
                intent_id,
                RiskDecisionType.REJECT,
                ("fixture_rejection",),
                self.now,
            ),
            None,
            "fixture_rejection",
            self.now,
            version,
            "portfolio:fixture",
            document.digest,
            "portfolio-digest",
        )
        store = PostgresPreTradeAssessmentStore(database, integrity_key=b"integration-key")
        store.append(assessment)
        store.append(assessment)
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresPreTradeAssessmentStore(
            restarted, integrity_key=b"integration-key"
        ).get_for_intent(intent_id)
        self.assertEqual(recovered, assessment)
        with self.assertRaisesRegex(ValueError, "mac_mismatch"):
            PostgresPreTradeAssessmentStore(
                restarted, integrity_key=b"wrong-key"
            ).get_for_intent(intent_id)
        with (
            self.assertRaises(PersistenceError),
            restarted.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE runtime_pretrade_assessments SET assessment_mac = %s "
                "WHERE intent_id = %s",
                ("0" * 64, intent_id),
            )
        restarted.close()

    def test_point_in_time_market_context_survives_restart(self) -> None:
        from trade_platform.domain import OrderSide
        from trade_platform.execution_evidence import (
            EventRiskObservation,
            HaltObservation,
            SlippageEstimate,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_market_context import (
            PostgresExecutionEvidenceStore,
            PostgresPortfolioReturnStore,
            PostgresQuoteStore,
        )
        from trade_platform.quotes import QuoteObservation
        from trade_platform.return_history import PortfolioReturnObservation

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        instrument_id = str(self.instrument_id)
        quotes = PostgresQuoteStore(database)
        quote = QuoteObservation(
            uuid4(),
            instrument_id,
            Decimal("99.9"),
            Decimal(100),
            Decimal(100),
            Decimal(1),
            "integration-feed",
            f"quote-{self.exchange_id}",
            self.now,
            self.now,
        )
        quotes.append(quote)
        execution = PostgresExecutionEvidenceStore(database)
        execution.append_halt(
            HaltObservation(
                uuid4(), instrument_id, False, "venue", f"halt-{self.exchange_id}", self.now, self.now
            )
        )
        execution.append_event_risk(
            EventRiskObservation(
                uuid4(),
                instrument_id,
                Decimal("0.1"),
                "events",
                f"event-{self.exchange_id}",
                self.now,
                self.now,
            )
        )
        execution.append_slippage(
            SlippageEstimate(
                uuid4(),
                instrument_id,
                OrderSide.BUY,
                Decimal("0.001"),
                "model",
                f"slippage-{self.exchange_id}",
                self.now,
                self.now,
            )
        )
        returns = PostgresPortfolioReturnStore(database)
        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        observation = PortfolioReturnObservation(
            uuid4(),
            account_id,
            self.now - timedelta(days=2),
            self.now - timedelta(days=1),
            Decimal("0.01"),
            "paper-oms",
            f"return-{self.exchange_id}",
            self.now - timedelta(days=1),
        )
        returns.append(observation)
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        self.assertEqual(PostgresQuoteStore(restarted).latest_as_of(instrument_id, self.now), quote)
        snapshot = PostgresExecutionEvidenceStore(restarted).snapshot_as_of(
            instrument_id, OrderSide.BUY, self.now
        )
        self.assertEqual(snapshot.event.risk, Decimal("0.1"))
        self.assertFalse(snapshot.halt.halted)
        self.assertEqual(snapshot.slippage.expected_fraction, Decimal("0.001"))
        evidence = PostgresPortfolioReturnStore(restarted).evidence_for_policy(
            account_id,
            self.now,
            minimum_observations=1,
            window_observations=1,
            maximum_age_seconds=86400,
        )
        self.assertEqual(evidence.observations, (observation,))
        restarted.close()

    def test_instrument_signal_and_model_authorities_survive_restart(self) -> None:
        from trade_platform.domain import AssetClass, Instrument, OrderSide
        from trade_platform.instruments import InstrumentRiskProfile, TradingSession
        from trade_platform.model_registry import ModelValidation, ModelVersion
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_decision_authorities import (
            PostgresInstrumentStore,
            PostgresModelRegistry,
            PostgresSignalStore,
        )
        from trade_platform.signal_engine import (
            DetailedSignalStatus,
            SignalEngine,
            SignalEngineError,
            SignalProposal,
            ValidationStage,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        instrument_id = str(self.instrument_id)
        runtime_venue = f"RUNTIME-{str(self.exchange_id)[:8]}"
        instruments = PostgresInstrumentStore(database)
        instrument = Instrument(
            instrument_id,
            "RUNTIME",
            runtime_venue,
            AssetClass.ETF,
            "USD",
            Decimal("0.01"),
            Decimal(1),
        )
        instruments.register(instrument)
        profile = InstrumentRiskProfile(
            instrument_id,
            "INDEX",
            "US",
            "US_INDEX",
            Decimal(1),
            Decimal(0),
            {"market": Decimal(1)},
            self.now,
            self.now,
        )
        instruments.append_risk_profile(profile)
        instruments.add_trading_session(
            TradingSession(
                runtime_venue, self.now.weekday(), time(0), time(23, 59), "UTC"
            )
        )
        proposal = SignalProposal.create(
            instrument_id=instrument_id,
            asset_class=AssetClass.ETF,
            strategy_version=f"runtime:{self.strategy_version_id}",
            direction=OrderSide.BUY,
            entry_low=Decimal(99),
            entry_high=Decimal(101),
            invalidation_level=Decimal(98),
            stop_level=Decimal(98),
            target_logic="target",
            expected_holding_period=timedelta(days=1),
            strength=Decimal("0.8"),
            confidence=Decimal("0.8"),
            expected_return=Decimal("0.02"),
            expected_volatility=Decimal("0.1"),
            expected_downside=Decimal("-0.01"),
            risk_reward_estimate=Decimal(2),
            regime="BULL",
            liquidity_score=Decimal(1),
            data_quality_score=Decimal(1),
            news_risk=Decimal(0),
            event_risk=Decimal("0.1"),
            correlation_impact=Decimal(0),
            recommended_quantity=Decimal(1),
            expires_at=self.now + timedelta(days=1),
            explanation="integration evidence",
            contradicting_evidence=(),
        )
        proposal = replace(proposal, signal_id=self.signal_id, created_at=self.now)
        signals = PostgresSignalStore(database)
        signals.append(proposal)
        validation = replace(
            SignalEngine().validate(
                proposal, {stage: True for stage in ValidationStage}
            ),
            assessed_at=self.now,
        )
        signals.append_validation(validation)
        models = PostgresModelRegistry(database)
        model = replace(
            ModelVersion.create(
                name=f"runtime-{self.exchange_id}",
                version="v1",
                task="direction",
                feature_version="features:v1",
                dataset_version="dataset:v1",
                artifact_reference="local://integration-model",
            ),
            created_at=self.now,
        )
        models.register(model)
        model_validation = replace(
            ModelValidation.create(
                model_id=model.model_id,
                metrics={"brier": Decimal("0.1")},
                economic_value_after_cost=Decimal("0.01"),
                evidence_reference="integration://model",
            ),
            validated_at=self.now,
        )
        models.append_validation(model_validation)
        models.approve(
            model.model_id,
            validation_id=model_validation.validation_id,
            actor="reviewer",
            reason="integration review",
        )
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered_instruments = PostgresInstrumentStore(restarted)
        self.assertEqual(recovered_instruments.get(instrument_id), instrument)
        self.assertEqual(recovered_instruments.risk_profile_as_of(instrument_id, self.now), profile)
        # Probe the middle of the configured session on the same weekday,
        # rather than depending on the wall-clock minute when CI runs.
        session_probe = self.now.replace(hour=12, minute=0, second=0, microsecond=0)
        self.assertTrue(recovered_instruments.is_trading_session(runtime_venue, session_probe))
        self.assertEqual(
            PostgresSignalStore(restarted).risk_signal(proposal.signal_id, as_of=self.now).signal_id,
            proposal.signal_id,
        )
        recovered_signals = PostgresSignalStore(restarted)
        self.assertEqual(recovered_signals.status(proposal.signal_id, as_of=self.now), DetailedSignalStatus.VALIDATED)
        validation_events = recovered_signals.lifecycle_events(proposal.signal_id, as_of=self.now)
        self.assertEqual((len(validation_events), validation_events[0].reason), (1, "all_validation_stages_passed"))
        expiry_events = recovered_signals.expire_due(as_of=proposal.expires_at)
        self.assertEqual((len(expiry_events), expiry_events[0].to_status), (1, DetailedSignalStatus.EXPIRED))
        self.assertEqual(recovered_signals.expire_due(as_of=proposal.expires_at), ())
        with self.assertRaisesRegex(SignalEngineError, "current_validated"):
            recovered_signals.risk_signal(proposal.signal_id, as_of=proposal.expires_at)
        self.assertTrue(PostgresModelRegistry(restarted).is_approved(model.model_id))
        restarted.close()

    def test_configured_postgres_assessment_reserves_before_paper_oms(self) -> None:
        from unittest.mock import patch

        from trade_platform.broker_adapter import (
            BrokerAccountSnapshot,
            BrokerConfiguration,
            BrokerMode,
            SandboxPaperBrokerAdapter,
        )
        from trade_platform.config import PlatformConfig
        from trade_platform.domain import OrderIntent, OrderSide
        from trade_platform.execution_evidence import (
            EventRiskObservation,
            HaltObservation,
            SlippageEstimate,
        )
        from trade_platform.paper_execution import CashBalance, ReconciliationResult
        from trade_platform.paper_runtime import PaperPolicySelection
        from trade_platform.persistence import PersistenceTarget, PostgresDatabase
        from trade_platform.policy_registry import PolicyDocument
        from trade_platform.postgres_market_context import PostgresPortfolioReturnStore
        from trade_platform.postgres_pretrade import PostgresPolicyRegistry
        from trade_platform.postgres_runtime import (
            PostgresRuntimeIdentityMap,
            build_postgres_paper_runtime,
        )
        from trade_platform.quotes import QuoteObservation
        from trade_platform.return_history import PortfolioReturnObservation

        # Reuse the independently asserted authority seeding path in this fresh
        # per-test database fixture, then compose the actual configured runtime.
        self.test_instrument_signal_and_model_authorities_survive_restart()
        suffix = str(self.exchange_id)[:8]
        strategy_version = f"runtime:{self.strategy_version_id}"
        risk_version = f"risk:runtime:{suffix}"
        portfolio_version = f"portfolio:runtime:{suffix}"
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        policies = PostgresPolicyRegistry(database)
        policies.append(
            PolicyDocument(
                "risk",
                risk_version,
                {
                    "maximum_order_notional": "10000",
                    "maximum_daily_order_notional": "10000",
                },
                "risk-reviewer",
                self.now,
            )
        )
        policies.append(
            PolicyDocument(
                "portfolio",
                portfolio_version,
                {
                    "maximum_gross_notional": "10000",
                    "maximum_single_weight": "1",
                    "maximum_scenario_loss": "10000",
                    "maximum_var_loss": "1",
                    "minimum_historical_observations": 1,
                    "return_history_window_observations": 1,
                    "maximum_return_history_age_seconds": 86400,
                    "stress_scenarios": {"risk_off": {"ETF": "-0.1"}},
                },
                "risk-reviewer",
                self.now,
            )
        )
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT model_id FROM runtime_models LIMIT 1")
            model_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO strategy_activation_events "
                "(activation_id, strategy_version_id, active, actor, effective_at, ingested_at, promotion_decision_id) "
                "VALUES (%s,%s,true,'integration-reviewer',%s,%s,NULL)",
                (uuid4(), self.strategy_version_id, self.now, self.now),
            )
        database.close()

        account_id = f"integration-paper-{suffix}"
        adapter = SandboxPaperBrokerAdapter(
            BrokerConfiguration("deterministic", BrokerMode.SIMULATED_PAPER, account_id),
            cash=CashBalance("USD", Decimal(10000)),
        )
        config = PlatformConfig(
            environment="paper",
            persistence_target=PersistenceTarget.POSTGRES,
            persistence_location=os.environ["POSTGRES_TEST_DSN"],
            assessment_integrity_key_reference="env:PG_ASSESSMENT_KEY",
        )
        identities = PostgresRuntimeIdentityMap(
            {risk_version: self.policy_version_id},
            {strategy_version: self.strategy_version_id},
            {"dataset:v1": self.dataset_version_id},
        )
        selection = PaperPolicySelection(risk_version, portfolio_version, model_id)
        with patch.dict(os.environ, {"PG_ASSESSMENT_KEY": "integration-assessment-key"}):
            runtime = build_postgres_paper_runtime(
                config=config,
                adapter=adapter,
                identities=identities,
                policy_selection=selection,
            )
            instrument_id = str(self.instrument_id)
            runtime.core.quotes.append(
                QuoteObservation(
                    uuid4(), instrument_id, Decimal("99.9"), Decimal(100), Decimal(100),
                    Decimal(1), "paper-feed", f"runtime-quote-{suffix}", self.now, self.now,
                )
            )
            runtime.core.execution_evidence.append_halt(
                HaltObservation(uuid4(), instrument_id, False, "venue", f"runtime-halt-{suffix}", self.now, self.now)
            )
            runtime.core.execution_evidence.append_event_risk(
                EventRiskObservation(uuid4(), instrument_id, Decimal("0.1"), "events", f"runtime-event-{suffix}", self.now, self.now)
            )
            runtime.core.execution_evidence.append_slippage(
                SlippageEstimate(uuid4(), instrument_id, OrderSide.BUY, Decimal("0.001"), "model", f"runtime-slip-{suffix}", self.now, self.now)
            )
            PostgresPortfolioReturnStore(runtime.core.database).append(
                PortfolioReturnObservation(
                    uuid4(), account_id, self.now - timedelta(days=2), self.now - timedelta(days=1),
                    Decimal("0.01"), "paper-oms", f"runtime-return-{suffix}", self.now - timedelta(days=1),
                )
            )
            runtime.core.oms.record_reconciliation_with_account(
                BrokerAccountSnapshot(
                    account_id, "deterministic", self.now, CashBalance("USD", Decimal(10000)),
                    Decimal(10000), {}, (), (), Decimal(0), Decimal(0),
                ),
                ReconciliationResult(True, ()),
                healthy=True,
                ingested_at=self.now,
            )
            intent = OrderIntent(
                uuid4(), self.signal_id, instrument_id, account_id, OrderSide.BUY,
                Decimal(1), Decimal(100), self.now,
            )
            result = runtime.assess_and_submit(intent, observed_at=self.now)
            self.assertTrue(result.assessment.approved, result.assessment.risk_decision.reasons)
            self.assertEqual(result.paper_order.status.value, "ACKNOWLEDGED")
            with runtime.core.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM risk_reservations WHERE intent_id=%s", (intent.intent_id,))
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SELECT count(*) FROM runtime_pretrade_assessments WHERE intent_id=%s", (intent.intent_id,))
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SELECT count(*) FROM paper_order_intents WHERE intent_id=%s", (intent.intent_id,))
                self.assertEqual(cursor.fetchone()[0], 1)
            runtime.core.kill_switches.activate("GLOBAL", actor="integration-risk")
            blocked_intent = replace(intent, intent_id=uuid4())
            blocked = runtime.assess_and_submit(blocked_intent, observed_at=self.now)
            self.assertFalse(blocked.assessment.approved)
            self.assertIn("kill_switch_active", blocked.assessment.risk_decision.reasons)
            self.assertIsNone(blocked.paper_order)
            with runtime.core.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM paper_order_intents WHERE intent_id=%s",
                    (blocked_intent.intent_id,),
                )
                self.assertEqual(cursor.fetchone()[0], 0)
            adapter.simulate_fill(
                f"partial-{suffix}", intent.intent_id, Decimal("0.4"), Decimal(100)
            )
            runtime.core.broker.sync_events()
            self.assertEqual(
                runtime.core.oms.get(intent.intent_id).status.value, "PARTIALLY_FILLED"
            )
            adapter.simulate_fill(
                f"final-{suffix}", intent.intent_id, Decimal("0.6"), Decimal(100)
            )
            runtime.core.broker.sync_events()
            self.assertEqual(runtime.core.oms.get(intent.intent_id).status.value, "FILLED")
            self.assertEqual(len(runtime.core.oms.fills(intent.intent_id)), 2)
            final_snapshot = adapter.sync_account()
            runtime.core.oms.record_reconciliation_with_account(
                final_snapshot,
                ReconciliationResult(True, ()),
                healthy=True,
                ingested_at=final_snapshot.as_of,
            )
            runtime.close()

            restarted = build_postgres_paper_runtime(
                config=config,
                adapter=adapter,
                identities=identities,
                policy_selection=selection,
            )
            self.assertEqual(restarted.core.oms.get(intent.intent_id).status.value, "FILLED")
            self.assertEqual(len(restarted.core.oms.fills(intent.intent_id)), 2)
            self.assertEqual(
                restarted.core.events.last_sequence("deterministic"),
                adapter.order_events()[-1].sequence,
            )
            recovered_account = restarted.core.reconciled_accounts.latest_as_of(
                account_id, datetime.now(UTC)
            ).snapshot
            self.assertEqual(recovered_account.cash.amount, Decimal(9900))
            self.assertEqual(recovered_account.positions[instrument_id].quantity, Decimal(1))
            self.assertIn("GLOBAL", restarted.core.kill_switches.active_scopes())
            with restarted.core.database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM risk_reservations WHERE intent_id=%s",
                    (intent.intent_id,),
                )
                self.assertEqual(cursor.fetchone()[0], 1)
            restarted.close()

    def test_atomic_oms_fill_and_risk_idempotency(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        intent_id = uuid4()
        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        repository.create_order_with_event(
            intent_id=intent_id,
            signal_id=self.signal_id,
            account_id=account_id,
            instrument_id=self.instrument_id,
            side="BUY",
            quantity=Decimal(2),
            limit_price=Decimal(100),
            status="PROPOSED",
            created_at=self.now,
        )
        first_fill = repository.ingest_fill(
            external_fill_id="fill-1",
            intent_id=intent_id,
            quantity=Decimal(1),
            price=Decimal(100),
            occurred_at=self.now,
        )
        self.assertEqual(
            first_fill,
            repository.ingest_fill(
                external_fill_id="fill-1",
                intent_id=intent_id,
                quantity=Decimal(1),
                price=Decimal(100),
                occurred_at=self.now,
            ),
        )
        with self.assertRaises(PostgresRepositoryError):
            repository.ingest_fill(
                external_fill_id="fill-1",
                intent_id=intent_id,
                quantity=Decimal(2),
                price=Decimal(100),
                occurred_at=self.now,
            )
        first = repository.reserve_and_record_decision(
            account_id=account_id,
            intent_id=intent_id,
            policy_version_id=self.policy_version_id,
            business_date=self.now.date(),
            notional=Decimal(70),
            daily_limit=Decimal(100),
            approved=True,
            reasons=(),
            decided_at=self.now,
        )
        self.assertTrue(
            repository.reserve_and_record_decision(
                account_id=account_id,
                intent_id=intent_id,
                policy_version_id=self.policy_version_id,
                business_date=self.now.date(),
                notional=Decimal(70),
                daily_limit=Decimal(100),
                approved=True,
                reasons=(),
                decided_at=self.now,
            ).idempotent
        )
        with self.assertRaisesRegex(PostgresRepositoryError, "daily_notional"):
            repository.reserve_and_record_decision(
                account_id=account_id,
                intent_id=uuid4(),
                policy_version_id=self.policy_version_id,
                business_date=self.now.date(),
                notional=Decimal(31),
                daily_limit=Decimal(100),
                approved=True,
                reasons=(),
                decided_at=self.now,
            )
        self.assertIsNotNone(first.reservation_id)
        repository._database.close()

    def test_validation_package_rolls_back_on_unknown_artifact(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        package_id = uuid4()
        with self.assertRaises(PostgresRepositoryError):
            repository.create_validation_package(
                package_id=package_id,
                strategy_version_id=self.strategy_version_id,
                dataset_version_id=self.dataset_version_id,
                cost_model_version="cost-v1",
                content_hash="c" * 64,
                status="BLOCKED",
                created_at=self.now,
                limitations=(),
                artifacts={"capacity": uuid4()},
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM validation_packages WHERE package_id = %s", (package_id,)
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        repository._database.close()

    def test_concurrent_reservations_cannot_exceed_daily_limit(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        start = threading.Barrier(2)
        outcomes: list[str] = []

        def reserve() -> None:
            repository = PostgresCriticalRepository(
                PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
            )
            try:
                start.wait(timeout=5)
                repository.reserve_and_record_decision(
                    account_id=account_id,
                    intent_id=uuid4(),
                    policy_version_id=self.policy_version_id,
                    business_date=self.now.date(),
                    notional=Decimal(60),
                    daily_limit=Decimal(100),
                    approved=True,
                    reasons=(),
                    decided_at=self.now,
                )
                outcomes.append("reserved")
            except PostgresRepositoryError:
                outcomes.append("blocked")
            finally:
                repository._database.close()

        workers = (threading.Thread(target=reserve), threading.Thread(target=reserve))
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertEqual(sorted(outcomes), ["blocked", "reserved"])

    def test_runtime_oms_cursor_reconciliation_and_kill_switch_survive_restart(self) -> None:
        """Real PostgreSQL objects recover only from durable, immutable evidence."""
        from trade_platform.broker_adapter import BrokerAccountSnapshot, BrokerOrderEvent
        from trade_platform.domain import OrderIntent, OrderSide, OrderStatus
        from trade_platform.paper_execution import (
            CashBalance,
            PaperOrder,
            Position,
            ReconciliationResult,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_paper_oms import PostgresBrokerEventStore, PostgresPaperOms
        from trade_platform.risk import PostgresKillSwitchRegistry

        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        intent = OrderIntent(
            uuid4(),
            self.signal_id,
            str(self.instrument_id),
            account_id,
            OrderSide.BUY,
            Decimal(2),
            Decimal(100),
            self.now,
        )
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        oms = PostgresPaperOms(database)
        self.assertEqual(oms.create(PaperOrder(intent)).status, OrderStatus.PROPOSED)
        for status in (
            OrderStatus.RISK_PENDING,
            OrderStatus.APPROVED,
            OrderStatus.SUBMISSION_PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
        ):
            oms.transition(intent.intent_id, status)
        event = BrokerOrderEvent(
            1,
            uuid4(),
            intent.intent_id,
            OrderStatus.PARTIALLY_FILLED,
            "FILL",
            self.now,
            "restart-fill-1",
            {"external_fill_id": "restart-fill-1", "quantity": "1", "price": "100"},
        )
        self.assertTrue(oms.apply_broker_event(account_id, "fixture-broker", event))
        self.assertFalse(oms.apply_broker_event(account_id, "fixture-broker", event))
        switch = PostgresKillSwitchRegistry(database)
        switch.activate("ACCOUNT:" + account_id, actor="integration")
        snapshot = BrokerAccountSnapshot(
            account_id,
            "fixture-broker",
            self.now,
            CashBalance("USD", Decimal(1000)),
            Decimal(800),
            {str(self.instrument_id): Position(str(self.instrument_id), Decimal(1), Decimal(100))},
            (),
            ("restart-fill-1",),
            Decimal(0),
            Decimal(0),
        )
        recorded, evidence = oms.record_reconciliation_with_account(
            snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=self.now
        )
        self.assertTrue(recorded.complete)
        self.assertIsNotNone(evidence)
        database.close()

        reopened_database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        reopened = PostgresPaperOms(reopened_database)
        self.assertEqual(reopened.get(intent.intent_id).status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(reopened.get(intent.intent_id).filled_quantity, Decimal(1))
        self.assertEqual(
            PostgresBrokerEventStore(reopened_database, account_id).last_sequence("fixture-broker"),
            1,
        )
        self.assertTrue(
            PostgresKillSwitchRegistry(reopened_database).blocks(
                str(self.instrument_id), "fixture", account_id=account_id
            )
        )
        self.assertEqual(
            reopened.latest_reconciled_account(account_id, self.now)
            .snapshot.positions[str(self.instrument_id)]
            .quantity,
            Decimal(1),
        )  # type: ignore[union-attr]
        reopened_database.close()

    def test_quant_package_and_promotion_history_survive_restart(self) -> None:
        import psycopg

        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_quant_validation import (
            PostgresPromotionLedger,
            PostgresQuantValidationStore,
        )
        from trade_platform.quant_validation import (
            REQUIRED_EVIDENCE,
            QuantValidationError,
            build_validation_package,
            record_external_evidence,
        )
        from trade_platform.strategy_promotion import (
            PromotionDecision,
            PromotionStatus,
            StrategyActivation,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        mappings = {"fixture-v1": self.strategy_version_id}
        datasets = {"fixture-data-v1": self.dataset_version_id}
        store = PostgresQuantValidationStore(
            database, strategy_versions=mappings, dataset_versions=datasets
        )
        artifacts = {}
        artifact_hashes = {}
        for evidence_type in REQUIRED_EVIDENCE:
            artifact = record_external_evidence(
                evidence_type=evidence_type,
                strategy_version="fixture-v1",
                dataset_version="fixture-data-v1",
                passed=True,
            )
            artifacts[evidence_type] = store.append(artifact)
            artifact_hashes[evidence_type] = artifact.identity.content_hash
        package = build_validation_package(
            strategy_id=self.strategy_id,
            strategy_version_id=self.strategy_version_id,
            strategy_version="fixture-v1",
            dataset_id=self.dataset_id,
            dataset_version_id=self.dataset_version_id,
            dataset_version="fixture-data-v1",
            feature_versions=("features-v1",),
            cost_model_version="cost-v1",
            evidence_ids=artifacts,
            evidence_hashes=artifact_hashes,
            evaluated_at=self.now,
            validation_metadata={"protocol": "integration-v1"},
        )
        package_id = store.append(package)
        self.assertEqual(store.append(package), package_id)
        with self.assertRaisesRegex(QuantValidationError, "identity_mismatch"):
            store.append(replace(package, feature_versions=("tampered-features",)))
        with self.assertRaisesRegex(QuantValidationError, "identity_mismatch"):
            store.append(replace(package, dataset_version="tampered-dataset"))
        with self.assertRaisesRegex(QuantValidationError, "identity_mismatch"):
            store.append(replace(package, strategy_version="tampered-strategy"))
        with self.assertRaisesRegex(QuantValidationError, "content_hash_mismatch"):
            store.append(
                replace(
                    package,
                    identity=replace(package.identity, content_hash="0" * 64),
                )
            )
        changed_membership = dict(package.evidence_ids)
        changed_membership["capacity"] = uuid4()
        with self.assertRaisesRegex(QuantValidationError, "identity_mismatch"):
            store.append(replace(package, evidence_ids=changed_membership))
        bad_hashes = dict(artifact_hashes)
        bad_hashes["capacity"] = "0" * 64
        bad_evidence_package = build_validation_package(
            strategy_id=self.strategy_id,
            strategy_version_id=self.strategy_version_id,
            strategy_version="fixture-v1",
            dataset_id=self.dataset_id,
            dataset_version_id=self.dataset_version_id,
            dataset_version="fixture-data-v1",
            feature_versions=("features-v1",),
            cost_model_version="cost-v1",
            evidence_ids=artifacts,
            evidence_hashes=bad_hashes,
            limitations=("bad-evidence-hash",),
            evaluated_at=self.now,
        )
        with self.assertRaisesRegex(QuantValidationError, "evidence_hash_mismatch"):
            store.append(bad_evidence_package)
        with (
            self.assertRaises(psycopg.errors.RaiseException),
            self.connection.transaction(),
            self.connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE validation_packages SET canonical_manifest = '{}' WHERE package_id = %s",
                (package_id,),
            )
        with (
            self.assertRaises(psycopg.errors.RaiseException),
            self.connection.transaction(),
            self.connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM validation_package_artifacts WHERE package_id = %s",
                (package_id,),
            )
        decision = PromotionDecision(
            uuid4(),
            self.strategy_id,
            "fixture-v1",
            PromotionStatus.REVIEW_REQUIRED,
            (),
            20,
            Decimal("0.1"),
            self.now,
        )
        promotions = PostgresPromotionLedger(database, strategy_versions=mappings)
        promotions.append(decision, package_id=package_id)
        promotions.append_activation(
            StrategyActivation(
                uuid4(), "fixture-v1", True, "reviewer", self.now, self.now, decision.decision_id
            )
        )
        database.close()

        reopened = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        package_record = PostgresQuantValidationStore(
            reopened, strategy_versions=mappings, dataset_versions=datasets
        ).get(package_id)
        self.assertEqual(package_record["artifact_type"], "StrategyValidationPackage")
        self.assertEqual(
            package_record["evidence_ids"], {name: str(value) for name, value in artifacts.items()}
        )
        self.assertEqual(package_record["evidence_hashes"], artifact_hashes)
        self.assertEqual(
            package_record["identity"]["content_hash"],
            hashlib.sha256(package_record["canonical_manifest"].encode("utf-8")).hexdigest(),
        )
        self.assertTrue(
            PostgresPromotionLedger(reopened, strategy_versions=mappings).strategy_enabled_as_of(
                "fixture-v1", self.now
            )
        )
        reopened.close()

    def _install_failure_trigger(self, table: str) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                "CREATE OR REPLACE FUNCTION cycle7_injected_failure() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'cycle7_injected_failure'; END; $$"
            )
            cursor.execute(
                f"CREATE TRIGGER cycle7_fail BEFORE INSERT ON {table} "  # nosec B608: test allow-list
                "FOR EACH ROW EXECUTE FUNCTION cycle7_injected_failure()"
            )

    def _remove_failure_trigger(self, table: str) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER IF EXISTS cycle7_fail ON {table}")  # nosec B608: test allow-list
            cursor.execute("DROP FUNCTION IF EXISTS cycle7_injected_failure()")

    def test_database_unavailable_and_terminated_connections_fail_closed(self) -> None:
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        with self.assertRaises(PersistenceError):
            PostgresDatabase(
                "postgresql://postgres:postgres@127.0.0.1:1/unavailable?connect_timeout=1"
            )

        interrupted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        with interrupted.connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            backend_pid = cursor.fetchone()[0]
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(%s)", (backend_pid,))
            self.assertTrue(cursor.fetchone()[0])
        repository = PostgresCriticalRepository(interrupted)
        with self.assertRaises(PostgresRepositoryError):
            repository.reserve_and_record_decision(
                account_id=f"integration-paper-{str(self.exchange_id)[:8]}",
                intent_id=uuid4(),
                policy_version_id=self.policy_version_id,
                business_date=self.now.date(),
                notional=Decimal(1),
                daily_limit=Decimal(100),
                approved=True,
                reasons=(),
                decided_at=self.now,
            )
        interrupted.close()

    def test_risk_reservation_rolls_back_when_decision_commit_fails(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        intent_id = uuid4()
        with self.assertRaises(PostgresRepositoryError):
            repository.reserve_and_record_decision(
                account_id=f"integration-paper-{str(self.exchange_id)[:8]}",
                intent_id=intent_id,
                policy_version_id=uuid4(),
                business_date=self.now.date(),
                notional=Decimal(10),
                daily_limit=Decimal(100),
                approved=True,
                reasons=(),
                decided_at=self.now,
            )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM risk_reservations WHERE intent_id = %s", (intent_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT COUNT(*) FROM risk_decisions WHERE intent_id = %s", (intent_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
        repository._database.close()

    def test_order_intent_rolls_back_when_initial_oms_event_fails(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_repositories import (
            PostgresCriticalRepository,
            PostgresRepositoryError,
        )

        intent_id = uuid4()
        repository = PostgresCriticalRepository(PostgresDatabase(os.environ["POSTGRES_TEST_DSN"]))
        self._install_failure_trigger("oms_events")
        try:
            with self.assertRaises(PostgresRepositoryError):
                repository.create_order_with_event(
                    intent_id=intent_id,
                    signal_id=self.signal_id,
                    account_id=f"integration-paper-{str(self.exchange_id)[:8]}",
                    instrument_id=self.instrument_id,
                    side="BUY",
                    quantity=Decimal(1),
                    limit_price=Decimal(100),
                    status="PROPOSED",
                    created_at=self.now,
                )
        finally:
            self._remove_failure_trigger("oms_events")
            repository._database.close()
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM paper_order_intents WHERE intent_id = %s", (intent_id,))
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_broker_cursor_and_event_roll_back_when_fill_write_fails(self) -> None:
        from trade_platform.broker_adapter import BrokerOrderEvent
        from trade_platform.domain import OrderIntent, OrderSide, OrderStatus
        from trade_platform.paper_execution import PaperOrder, progress_to_acknowledged
        from trade_platform.paper_oms import PaperOmsError
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_paper_oms import PostgresPaperOms

        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        intent = OrderIntent(
            uuid4(), self.signal_id, str(self.instrument_id), account_id,
            OrderSide.BUY, Decimal(1), Decimal(100), self.now,
        )
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        oms = PostgresPaperOms(database)
        oms.create(PaperOrder(intent))
        for status in (
            OrderStatus.RISK_PENDING,
            OrderStatus.APPROVED,
            OrderStatus.SUBMISSION_PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
        ):
            oms.transition(intent.intent_id, status)
        event = BrokerOrderEvent(
            1, uuid4(), intent.intent_id, OrderStatus.FILLED, "FILL", self.now,
            f"cycle7-fill-{intent.intent_id}",
            {"external_fill_id": f"cycle7-fill-{intent.intent_id}", "quantity": "1", "price": "100"},
        )
        self._install_failure_trigger("fills")
        try:
            with self.assertRaises(PaperOmsError):
                oms.apply_broker_event(account_id, "cycle7-broker", event)
        finally:
            self._remove_failure_trigger("fills")
        self.assertEqual(oms.get(intent.intent_id), progress_to_acknowledged(PaperOrder(intent)))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM broker_sync_events WHERE event_id = %s", (event.event_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute(
                "SELECT COUNT(*) FROM broker_event_cursors WHERE account_id = %s AND source = 'cycle7-broker'",
                (account_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        database.close()

    def test_reconciliation_rolls_back_when_account_evidence_write_fails(self) -> None:
        from trade_platform.broker_adapter import BrokerAccountSnapshot
        from trade_platform.paper_execution import CashBalance, ReconciliationResult
        from trade_platform.paper_oms import PaperOmsError
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_paper_oms import PostgresPaperOms

        account_id = f"integration-paper-{str(self.exchange_id)[:8]}"
        snapshot = BrokerAccountSnapshot(
            account_id, "cycle7-broker", self.now, CashBalance("USD", Decimal(1000)),
            Decimal(1000), {}, (), (), Decimal(0), Decimal(0),
        )
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        oms = PostgresPaperOms(database)
        self._install_failure_trigger("reconciled_account_evidence")
        try:
            with self.assertRaises(PaperOmsError):
                oms.record_reconciliation_with_account(
                    snapshot, ReconciliationResult(True, ()), healthy=True, ingested_at=self.now
                )
        finally:
            self._remove_failure_trigger("reconciled_account_evidence")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM reconciliations WHERE account_id = %s AND source = 'cycle7-broker'",
                (account_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        database.close()

    def test_recovery_gate_is_append_only_and_survives_restart(self) -> None:
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.postgres_recovery import PostgresRecoveryGate

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        gate = PostgresRecoveryGate(database)
        gate.require_reconciliation("integration_restore")
        self.assertTrue(gate.blocks())
        database.close()

        restarted = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        recovered = PostgresRecoveryGate(restarted)
        self.assertTrue(recovered.blocks())
        with (
            self.assertRaises(PersistenceError),
            restarted.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM runtime_recovery_events")
        recovered.mark_reconciled("integration_reconciliation_complete")
        self.assertFalse(recovered.blocks())
        restarted.close()

    def test_unknown_package_cannot_create_promotion_decision(self) -> None:
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.postgres_quant_validation import PostgresPromotionLedger
        from trade_platform.strategy_promotion import PromotionDecision, PromotionStatus

        decision = PromotionDecision(
            uuid4(), self.strategy_id, "cycle7-strategy", PromotionStatus.REVIEW_REQUIRED,
            (), 20, Decimal("0.1"), self.now,
        )
        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        ledger = PostgresPromotionLedger(
            database, strategy_versions={"cycle7-strategy": self.strategy_version_id}
        )
        with self.assertRaisesRegex(ValueError, "postgres_promotion_persistence_failed"):
            ledger.append(decision, package_id=uuid4())
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM strategy_promotion_decisions WHERE decision_id = %s",
                (decision.decision_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        database.close()


if __name__ == "__main__":
    unittest.main()
