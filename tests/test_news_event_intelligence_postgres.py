import hashlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from tests.test_news_event_intelligence import (
    INSTRUMENT,
    document,
    extraction,
    link,
    source,
)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class NewsEventIntelligencePostgresTests(unittest.TestCase):
    def test_pit_correction_retraction_restart_immutability_and_no_authority(self) -> None:
        from alembic import command
        from alembic.config import Config

        from trade_platform.news_event_intelligence import (
            ConfidenceAction,
            EvidenceStatus,
            NewsDocumentRevisionV2,
            Officiality,
            PostgresNewsEventIntelligenceStore,
            RevisionKind,
            assess_news_event,
            build_research_candidate,
            cluster_documents,
        )
        from trade_platform.persistence import PersistenceError, PostgresDatabase
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
        now = datetime(2025, 2, 3, 15, tzinfo=UTC)
        instrument = replace(
            mvp_instrument_universe(datetime(2024, 1, 1, tzinfo=UTC))[0],
            instrument_id=INSTRUMENT,
            canonical_symbol="CYCLE206",
        )
        PostgresProfessionalInstrumentMaster(database).register(instrument)
        health_id = uuid4()
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO data_health_assessments (assessment_id,dataset_version_id,scope_type,scope_value,policy_version,evaluated_at,expected_start,expected_end,max_action,blocking,content_hash,summary) VALUES (%s,NULL,'INSTRUMENT',%s,'cycle206-health-v1',%s,%s,%s,'INFO',FALSE,%s,'{}'::jsonb)",
                (
                    health_id,
                    INSTRUMENT,
                    now,
                    now - timedelta(days=1),
                    now,
                    hashlib.sha256(b"cycle206-health").hexdigest(),
                ),
            )

        policy = source()
        initial = document(policy, published_at=now, ingested_at=now + timedelta(minutes=1))
        correction = document(
            policy,
            item="item-1-correction",
            root="item-1",
            headline="Fixture issuer corrects guidance",
            published_at=now,
            ingested_at=now + timedelta(hours=1),
            revision=1,
            kind=RevisionKind.CORRECTION,
            supersedes=initial.document_revision_id,
        )
        retraction = document(
            policy,
            item="item-1-retraction",
            root="item-1",
            headline="Fixture issuer retracts guidance",
            published_at=now,
            ingested_at=now + timedelta(hours=2),
            officiality=Officiality.OFFICIAL,
            revision=2,
            kind=RevisionKind.RETRACTION,
            supersedes=correction.document_revision_id,
        )
        documents: tuple[NewsDocumentRevisionV2, ...] = (initial, correction, retraction)
        links = tuple(link(item) for item in documents)
        extractions = tuple(extraction(item) for item in documents)
        assessments = tuple(
            assess_news_event(
                policy,
                item,
                extracted,
                (linked,),
                assessed_at=extracted.extracted_at + timedelta(minutes=1),
                data_health_status="HEALTHY",
                data_health_assessment_ids=(health_id,),
            )
            for item, linked, extracted in zip(documents, links, extractions, strict=True)
        )
        candidates = tuple(
            build_research_candidate(
                assessment,
                instrument_id=INSTRUMENT,
                created_at=assessment.assessed_at,
            )
            for assessment in assessments
        )
        clusters = cluster_documents(documents, now + timedelta(hours=3))
        store = PostgresNewsEventIntelligenceStore(database)
        self.assertEqual(store.publish_source(policy), policy.source_policy_version_id)
        self.assertEqual(store.publish_source(policy), policy.source_policy_version_id)
        ids = store.publish_evidence(
            policy, documents, links, extractions, clusters, assessments, candidates
        )
        self.assertEqual(ids, tuple(item.candidate_id for item in candidates))
        self.assertEqual(
            store.publish_evidence(
                policy, documents, links, extractions, clusters, assessments, candidates
            ),
            ids,
        )

        before_correction = store.candidates_for_instrument_as_of(
            INSTRUMENT, initial.ingested_at
        )
        self.assertEqual(before_correction[0][0:2], ("REVIEW_REQUIRED", "MAINTAIN"))
        after_correction = store.candidates_for_instrument_as_of(
            INSTRUMENT, correction.ingested_at
        )
        self.assertEqual(len(after_correction), 1)
        self.assertEqual(after_correction[0][3], correction.ingested_at)
        after_retraction = store.candidates_for_instrument_as_of(
            INSTRUMENT, retraction.ingested_at
        )
        self.assertEqual(after_retraction[0][0:3], ("WITHDRAWN", "WITHDRAW", Decimal("0")))
        self.assertEqual(assessments[-1].status, EvidenceStatus.WITHDRAWN)
        self.assertEqual(candidates[-1].confidence_action, ConfidenceAction.WITHDRAW)

        with (
            self.assertRaises(PersistenceError),
            database.transaction() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE news_event_research_candidates SET automatic_authority=TRUE WHERE candidate_id=%s",
                (candidates[0].candidate_id,),
            )
        database.close()

        reopened = PostgresDatabase(dsn)
        restarted = PostgresNewsEventIntelligenceStore(reopened)
        self.assertEqual(
            restarted.candidates_for_instrument_as_of(INSTRUMENT, retraction.ingested_at),
            after_retraction,
        )
        with reopened.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM news_event_lineage WHERE successor_document_revision_id IN (%s,%s)",
                (correction.document_revision_id, retraction.document_revision_id),
            )
            self.assertEqual(int(cursor.fetchone()[0]), 2)
            cursor.execute(
                "SELECT COUNT(*) FROM paper_order_intents WHERE instrument_id=%s", (INSTRUMENT,)
            )
            self.assertEqual(int(cursor.fetchone()[0]), 0)
            cursor.execute(
                "SELECT COUNT(*) FROM strategy_activation_events WHERE reason LIKE '%%cycle206%%'"
            )
            self.assertEqual(int(cursor.fetchone()[0]), 0)
        reopened.close()


if __name__ == "__main__":
    unittest.main()
