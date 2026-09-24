"""Phase R2A.2 -- three-clock feature materializations against real PostgreSQL.

Fixture instruments use the ``TESTFIXTURE:R2A2:`` prefix; every price and
timestamp is a FIXTURE (all in the past, so shared-database invariants hold).
Feature definitions are registered under the canonical basis name with a
tagged semantic version, which the basis tests tolerate.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from tests.test_knowledge_time_doctrine_v1 import _facts, _provenance


def _verdict_for(dataset_version_id: UUID, content_hash: str, authority: Any, **lag: Any) -> Any:
    """A real verdict bound to a real sealed dataset, over a locally issued contract."""
    from trade_platform import evidence_tier_authority_v1 as tier_module
    from trade_platform.evidence_tier_authority_v1 import (
        TimingAuthorityV1,
        evaluate_evidence_tier_v1,
    )

    provenance = _provenance(dataset_version_id=dataset_version_id, content_hash=content_hash)
    facts = _facts(
        authority, dataset_version_id=dataset_version_id, dataset_content_hash=content_hash, **lag
    )
    if authority is TimingAuthorityV1.NONE:
        return evaluate_evidence_tier_v1(facts, provenance)
    contract = tier_module._issue_contract(
        source_id=provenance.source_id, timing_authority=authority, tier_ceiling_reason=None,
        authorization_reference="test-only",
    )
    original = tier_module.authorized_timing_contracts_v1
    tier_module.authorized_timing_contracts_v1 = lambda: (contract,)  # type: ignore[assignment]
    try:
        return evaluate_evidence_tier_v1(facts, provenance)
    finally:
        tier_module.authorized_timing_contracts_v1 = original  # type: ignore[assignment]


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class FeatureThreeClockV3PostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            os.environ["POSTGRES_TEST_DSN"].replace("postgresql://", "postgresql+psycopg://", 1),
        )
        command.upgrade(config, "head")

    def test_three_clock_basis_end_to_end(self) -> None:
        from trade_platform.crypto_derivatives_features import (
            PostgresCryptoDerivativesFeatureCalculator,
            crypto_mark_index_basis_three_clock_definition,
        )
        from trade_platform.crypto_instruments import (
            CryptoInstrumentKind,
            CryptoInstrumentSpecification,
            CryptoSettlementType,
            PostgresCryptoInstrumentAuthority,
            ReferencePriceRequirement,
            SettlementStyle,
        )
        from trade_platform.domain import AssetClass
        from trade_platform.evidence_tier_authority_v1 import TimingAuthorityV1
        from trade_platform.feature_authority import (
            FeatureAuthorityError,
            FeatureSubjectType,
            PostgresFeatureAuthority,
            PostgresSealedObservationClockResolverV1,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            AssetScope,
            AuthorizedHistoricalSource,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )
        from trade_platform.knowledge_time_doctrine_v1 import (
            ClaimCeilingV1,
            DeclaredComputeLatencyV1,
        )
        from trade_platform.open_to_open_validation_orchestration_v1 import (
            canonical_feature_decision_at,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
        from trade_platform.professional_instruments import (
            IdentifierMapping,
            IdentifierSourceKind,
            InstrumentType,
            LifecycleStatus,
            PostgresProfessionalInstrumentMaster,
            ProfessionalInstrument,
            RepresentationKind,
            SessionType,
        )

        database = PostgresDatabase(os.environ["POSTGRES_TEST_DSN"])
        master = PostgresProfessionalInstrumentMaster(database)
        crypto = PostgresCryptoInstrumentAuthority(database)
        pipeline = PostgresHistoricalMarketDataPipeline(database)
        authority = PostgresFeatureAuthority(database)
        calculator = PostgresCryptoDerivativesFeatureCalculator(database)
        resolver = PostgresSealedObservationClockResolverV1(database)

        registered_at = datetime(2025, 1, 2, tzinfo=UTC)
        tag = uuid4().hex[:10].upper()
        namespace = f"TESTFIX_R2A2_{tag}"
        venue = f"TESTFIXCEXR2A2{tag}"
        symbol = f"TESTFIXR2A2{tag}"
        instrument_id = f"TESTFIXTURE:R2A2:{tag}"
        master.register(
            ProfessionalInstrument(
                instrument_id=instrument_id, asset_class=AssetClass.CRYPTO,
                instrument_type=InstrumentType.CRYPTO_PERPETUAL, exchange_name=venue,
                venue=venue, mic=None, canonical_symbol=symbol, listing_date=date(2024, 1, 2),
                base_currency="BTC", quote_currency="USDT", settlement_currency="USDT",
                contract_multiplier=Decimal(1), contract_size=Decimal(1),
                tick_size=Decimal("0.01"), lot_size=Decimal("0.00001"),
                price_precision=2, quantity_precision=5, trading_timezone="UTC",
                market_session_type=SessionType.CRYPTO_24X7,
                representation_kind=RepresentationKind.PERPETUAL, registered_at=registered_at,
                lifecycle_status=LifecycleStatus.ACTIVE,
            )
        )
        crypto.specify_instrument(
            CryptoInstrumentSpecification(
                instrument_id=instrument_id, venue=venue, kind=CryptoInstrumentKind.PERPETUAL,
                base_asset="BTC", quote_asset="USDT", settlement_asset="USDT",
                settlement_style=SettlementStyle.LINEAR,
                settlement_type=CryptoSettlementType.CASH_SETTLED,
                contract_multiplier=Decimal(1), contract_size=Decimal(1),
                reference_price_requirement=ReferencePriceRequirement.MARK_AND_INDEX,
                index_reference=f"TESTFIX_R2A2_{tag}_INDEX", registered_at=registered_at,
                source_reference="fixture:crypto-specification",
            )
        )
        master.add_identifier_mapping(
            IdentifierMapping(
                instrument_id=instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
                namespace=namespace, value=tag, valid_from=registered_at, valid_until=None,
                ingested_at=registered_at, source_reference="fixture:provider-identifier",
            )
        )
        source = AuthorizedHistoricalSource(
            provider=f"TESTFIX_R2A2_{tag}", dataset_name="r2a2-fixture",
            provider_identifier_namespace=namespace, provider_terms_version="v1",
            authorization_reference="fixture://authorization/r2a2", authorized_at=registered_at,
            created_at=registered_at, asset_scope=AssetScope.CRYPTO.value,
            authorized_observation_kinds=frozenset(
                {ObservationKind.MARK_PRICE, ObservationKind.INDEX_PRICE}
            ),
        )
        pipeline.register_source(source)

        events = [datetime(2025, 3, 1, 8, tzinfo=UTC) + timedelta(minutes=i) for i in range(3)]
        normalized_at = datetime(2025, 3, 2, tzinfo=UTC)

        def capture(kind: Any, price: str, event_at: datetime) -> UUID:
            (raw_id,) = pipeline.capture_raw(
                [
                    RawHistoricalObservation(
                        source_id=source.source_id, observation_kind=kind,
                        provider_identifier=tag, provider_symbol=symbol, exchange=venue,
                        event_at=event_at, effective_at=event_at, ingested_at=event_at,
                        adjustment_status=AdjustmentStatus.AS_REPORTED, revision=0,
                        provenance_uri=f"fixture://r2a2/{kind.value}/{event_at.isoformat()}",
                        raw_payload={
                            "price": price, "price_asset": "USDT",
                            "observed_at": event_at.isoformat(),
                        },
                    )
                ]
            )
            return pipeline.normalize(raw_id, "r2a2-v1", normalized_at).normalized_observation_id

        members: list[UUID] = []
        for offset, event_at in enumerate(events):
            members.append(capture(ObservationKind.MARK_PRICE, f"{100 + offset}.5", event_at))
            members.append(capture(ObservationKind.INDEX_PRICE, f"{100 + offset}", event_at))
        sealed_at = datetime(2025, 3, 3, tzinfo=UTC)
        dataset = pipeline.seal_dataset(
            source.source_id, f"r2a2-dataset-{tag}", "r2a2-v1", tuple(members), sealed_at
        )
        platform_as_of = sealed_at + timedelta(hours=1)

        def definition(label: str) -> Any:
            base = crypto_mark_index_basis_three_clock_definition(registered_at)
            registered = replace(base, semantic_version=f"2.0.0-r2a2-{label}-{tag}", feature_id=uuid4())
            authority.register(registered)
            return registered

        # ---- T1: real values, undefined market knowledge, descriptive claim ----
        t1 = _verdict_for(
            dataset.dataset_version_id, dataset.content_hash, TimingAuthorityV1.NONE
        )
        t1_definition = definition("t1")
        written = calculator.materialize_crypto_mark_index_basis_v3_batch(
            feature_id=t1_definition.feature_id, instrument_id=instrument_id,
            dataset_version_id=dataset.dataset_version_id, evidence_tier=t1, event_ats=events,
            platform_as_of=platform_as_of,
        )
        self.assertEqual(3, written)
        rows = authority.v3_rows_for_dataset(
            t1_definition.feature_id, FeatureSubjectType.INSTRUMENT, instrument_id,
            str(dataset.dataset_version_id),
        )
        self.assertEqual(3, len(rows))
        for row in rows:
            self.assertIsNone(row.market_knowledge_at)
            self.assertIs(ClaimCeilingV1.DESCRIPTIVE, row.claim_ceiling)
            # Platform instant kept under its honest name, equal to the old V2 knowledge_at.
            self.assertEqual(max(normalized_at, sealed_at), row.platform_recorded_at)
            self.assertEqual(row.platform_recorded_at, row.knowledge_at)
        self.assertEqual(Decimal("0.005000000000"), rows[0].value)
        self.assertEqual(
            (),
            authority.historical_as_of_subject_v3(
                t1_definition.feature_id, FeatureSubjectType.INSTRUMENT, instrument_id,
                str(dataset.dataset_version_id), datetime(2026, 1, 1, tzinfo=UTC),
                minimum_claim=ClaimCeilingV1.CONDITIONAL, evidence_tiers={t1.evidence_id: t1},
                clock_resolver=resolver,
            ),
        )
        # The legacy read never sees a V3 row.
        self.assertEqual(
            (),
            authority.latest_as_of_subject(
                t1_definition.feature_id, FeatureSubjectType.INSTRUMENT, instrument_id,
                str(dataset.dataset_version_id), datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )

        # ---- recomputing a week later reconciles as the identical rows ----------
        again = calculator.materialize_crypto_mark_index_basis_v3_batch(
            feature_id=t1_definition.feature_id, instrument_id=instrument_id,
            dataset_version_id=dataset.dataset_version_id, evidence_tier=t1, event_ats=events,
            platform_as_of=platform_as_of + timedelta(days=7),
            computed_at=platform_as_of + timedelta(days=7),
        )
        self.assertEqual(3, again)
        replayed = authority.v3_rows_for_dataset(
            t1_definition.feature_id, FeatureSubjectType.INSTRUMENT, instrument_id,
            str(dataset.dataset_version_id),
        )
        self.assertEqual([row.content_hash for row in rows], [row.content_hash for row in replayed])
        self.assertEqual([row.computed_at for row in rows], [row.computed_at for row in replayed])

        # ---- T2: distinct, causal market knowledge; historical reads gate on it -
        lag_nanos = 2_000_000_000
        t2 = _verdict_for(
            dataset.dataset_version_id, dataset.content_hash,
            TimingAuthorityV1.VENUE_EVENT_TIMESTAMP, declared_publication_lag_nanos=lag_nanos,
            publication_lag_assumption_reference="test-only assumption, not an owner decision",
        )
        # Same feature, same market key, different evidence verdict: fail closed.
        with self.assertRaises(FeatureAuthorityError):
            calculator.materialize_crypto_mark_index_basis_v3_batch(
                feature_id=t1_definition.feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset.dataset_version_id, evidence_tier=t2,
                event_ats=events, platform_as_of=platform_as_of,
            )
        t2_definition = definition("t2")
        calculator.materialize_crypto_mark_index_basis_v3_batch(
            feature_id=t2_definition.feature_id, instrument_id=instrument_id,
            dataset_version_id=dataset.dataset_version_id, evidence_tier=t2, event_ats=events,
            platform_as_of=platform_as_of,
        )
        t2_rows = authority.v3_rows_for_dataset(
            t2_definition.feature_id, FeatureSubjectType.INSTRUMENT, instrument_id,
            str(dataset.dataset_version_id),
        )
        lag = timedelta(seconds=2)
        self.assertEqual([event + lag for event in events], [row.market_knowledge_at for row in t2_rows])
        self.assertTrue(all(row.claim_ceiling is ClaimCeilingV1.CONDITIONAL for row in t2_rows))
        # Rows read back re-derive from their genuine verdict -- and only from it.
        latency = DeclaredComputeLatencyV1(500_000_000, "test-only declared compute latency")
        self.assertEqual(
            [event + lag + timedelta(milliseconds=500) for event in events],
            [
                canonical_feature_decision_at(
                    row, compute_latency=latency, evidence_tiers={t2.evidence_id: t2},
                    clock_resolver=resolver,
                )
                for row in t2_rows
            ],
        )
        with self.assertRaises(FeatureAuthorityError):
            t2_rows[0].verified_feature_knowledge_v1({t1.evidence_id: t1}, resolver)
        self.assertIsNone(
            rows[0].verified_feature_knowledge_v1({t1.evidence_id: t1}, resolver).market_knowledge_at
        )

        def visible(as_of: datetime, minimum: Any = ClaimCeilingV1.CONDITIONAL) -> int:
            return len(
                authority.historical_as_of_subject_v3(
                    t2_definition.feature_id, FeatureSubjectType.INSTRUMENT, instrument_id,
                    str(dataset.dataset_version_id), as_of, minimum_claim=minimum,
                    evidence_tiers={t2.evidence_id: t2}, clock_resolver=resolver,
                )
            )

        # Visible by market knowledge, long before the platform ever stored it.
        self.assertEqual(0, visible(events[0] + lag - timedelta(microseconds=1)))
        self.assertEqual(1, visible(events[0] + lag))
        self.assertEqual(3, visible(events[2] + lag))
        self.assertLess(events[2] + lag, normalized_at)
        self.assertEqual(0, visible(events[2] + lag, ClaimCeilingV1.PROFESSIONAL))
        with self.assertRaises(FeatureAuthorityError):
            visible(events[2], ClaimCeilingV1.DESCRIPTIVE)

        # ---- the database refuses incoherent three-clock rows ------------------
        row = t2_rows[0]

        class _Rollback(Exception):
            pass

        def raw_insert(*, rollback: bool = False, **overrides: Any) -> None:
            values: dict[str, Any] = {
                "materialization_id": uuid4(), "feature_id": row.feature_id,
                "instrument_id": instrument_id, "dataset_version": f"r2a2-raw-{uuid4()}",
                "event_at": row.event_at, "effective_at": row.effective_at,
                "knowledge_at": row.platform_recorded_at, "computed_at": row.computed_at,
                "source_observation_manifest": '["fixture"]', "value": row.value,
                "quality_status": "VALIDATED", "content_hash": "e" * 64,
                "subject_type": "INSTRUMENT", "subject_id": instrument_id, "hash_version": "V3",
                "market_knowledge_at": row.market_knowledge_at,
                "platform_recorded_at": row.platform_recorded_at, "claim_ceiling": "CONDITIONAL",
                "feature_knowledge": "{}", "feature_knowledge_hash": "f" * 64,
                "knowledge_inputs": "[{}]",
            }
            values.update(overrides)
            columns = ",".join(values)
            placeholders = ",".join("%s" for _ in values)
            with database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO feature_materializations ({columns}) VALUES ({placeholders})",  # nosec B608 - fixed test columns
                    tuple(values.values()),
                )
                if rollback:
                    raise _Rollback

        # Positive control: the coherent row itself is accepted (then rolled back),
        # so each refusal below is the CHECK's doing, not a malformed statement.
        with self.assertRaises(_Rollback):
            raw_insert(rollback=True)
        for overrides in (
            {"claim_ceiling": "DESCRIPTIVE"},  # defined knowledge with a descriptive claim
            {"market_knowledge_at": None},  # undefined knowledge with a conditional claim
            {"market_knowledge_at": row.event_at - timedelta(seconds=1)},  # known before it happened
            {"knowledge_at": row.platform_recorded_at + timedelta(seconds=1)},  # legacy column drift
            {"claim_ceiling": None},
            {"market_knowledge_at": row.effective_at - timedelta(microseconds=1)},  # before completion
            {"feature_knowledge_hash": "F" * 64},  # not a lowercase hex digest
            {"knowledge_inputs": "[]"},  # no input provenance
            {"knowledge_inputs": None},
            {"hash_version": "V2"},  # a legacy row may not carry three-clock columns
        ):
            with self.subTest(overrides=overrides), self.assertRaises(PersistenceError):
                raw_insert(**overrides)


if __name__ == "__main__":
    unittest.main()
