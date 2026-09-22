"""Phase 3D.8C Postgres evidence: operator surfaces classify through the canonical authority.

Reuses the Phase 3D.8A disposable-database fixture verbatim: the canonical
Bybit source comes from the real offline onboarding, two daily parents from the
real acquisition service (scripted transport, no socket) and one composite from
the real composition service. Every price is a synthetic FIXTURE; the real
research database is never referenced.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from unittest import mock
from uuid import UUID, uuid4

# The module, not its TestCase class: a class imported here would be collected
# and run a second time by unittest discovery.
from tests import test_real_market_data_provenance_v1_postgres as provenance_fixture
from tests.test_historical_dataset_composition_v1_postgres import INSTRUMENT_ID

REAL = "REAL_DATA_RESEARCH_EVIDENCE"
SYNTHETIC = "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"
UNAVAILABLE = "UNAVAILABLE"
EVALUATED_AT = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)
_SEQUENCE = count()


def _fixture() -> Any:
    """The 3D.8A TestCase, looked up lazily so discovery never collects it here."""
    return provenance_fixture.RealMarketDataProvenancePostgresTests


class _CountingAuthority:
    def __init__(self, database: Any) -> None:
        from trade_platform.real_market_data_provenance_v1 import (
            PostgresRealMarketDataProvenanceAuthorityV1,
        )

        self._authority = PostgresRealMarketDataProvenanceAuthorityV1(database)
        self.calls: list[UUID] = []

    def prove(self, dataset_version_id: UUID) -> Any:
        self.calls.append(dataset_version_id)
        return self._authority.prove(dataset_version_id)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class OperatorDashboardRealDataProvenancePostgresTests(unittest.TestCase):
    database: Any
    source_id: UUID
    parents: dict[str, Any]
    composite: Any

    @classmethod
    def setUpClass(cls) -> None:
        _fixture().setUpClass.__func__(cls)  # type: ignore[attr-defined]
        mark = _fixture()._mark_dataset
        other = _fixture()._other_source
        cls.impostor = mark(cls, other(cls), "3d8c-free-text-bybit")
        cls.fixture_dataset = mark(
            cls,
            other(cls, provider="TESTFIX_3D8C", dataset_name="3d8c-fixture",
                  authorization_reference="fixture://authorization/3d8c"),
            "3d8c-fixture-dataset",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        _fixture().tearDownClass.__func__(cls)  # type: ignore[attr-defined]

    def _queries(self, authority: Any = None) -> Any:
        from trade_platform.operator_dashboard import PostgresOperatorDashboardQueries

        return PostgresOperatorDashboardQueries(self.database, authority)

    def _datasets(self, queries: Any = None) -> dict[UUID, Any]:
        page = (queries or self._queries()).historical_datasets(limit=50, offset=0)
        return {item.dataset_version_id: item for item in page.items}

    def _health(
        self, dataset_version_id: UUID | None, *, policy_version: str = "3d8c-health-v1",
        blocking: bool = False,
    ) -> UUID:
        assessment_id = uuid4()
        evaluated_at = EVALUATED_AT + timedelta(seconds=next(_SEQUENCE))
        digest = hashlib.sha256(f"3d8c:{assessment_id}".encode()).hexdigest()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO data_health_assessments (assessment_id,dataset_version_id,scope_type,"
                "scope_value,policy_version,evaluated_at,expected_start,expected_end,max_action,"
                "blocking,content_hash,summary) VALUES (%s,%s,'INSTRUMENT',%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)",
                (assessment_id, dataset_version_id, INSTRUMENT_ID, policy_version, evaluated_at,
                 evaluated_at - timedelta(days=1), evaluated_at,
                 "BLOCK_INSTRUMENT" if blocking else "INFO", blocking, digest),
            )
        return assessment_id

    def _counts(self) -> tuple[int, ...]:
        return _fixture()._counts(self)

    def test_canonical_daily_and_composite_datasets_are_real_on_the_operator_surface(self) -> None:
        from trade_platform.real_market_data_provenance_v1 import (
            PostgresRealMarketDataProvenanceAuthorityV1,
        )

        before = self._counts()
        datasets = self._datasets()
        authority = PostgresRealMarketDataProvenanceAuthorityV1(self.database)
        for dataset_version_id in (
            self.parents["A"].dataset_version_id, self.parents["B"].dataset_version_id,
            self.composite.dataset_version_id,
        ):
            item = datasets[dataset_version_id]
            verdict = authority.prove(dataset_version_id)
            self.assertEqual(item.evidence_classification, REAL, item.provenance_reasons)
            self.assertEqual(item.provenance_reasons, [])
            self.assertEqual(item.provenance_evidence_id, verdict.evidence_id)
            self.assertEqual(item.provenance_content_hash, verdict.content_hash)
            self.assertEqual(item.source_id, self.source_id)
        self.assertEqual(
            datasets[self.composite.dataset_version_id].content_hash.strip(),
            self.composite.dataset_content_hash,
        )
        self.assertEqual(self._counts(), before)

    def test_fixture_and_free_text_bybit_datasets_are_not_real(self) -> None:
        datasets = self._datasets()
        self.assertEqual(datasets[self.fixture_dataset].evidence_classification, SYNTHETIC)
        impostor = datasets[self.impostor]
        self.assertEqual(impostor.provider, "bybit")
        self.assertEqual(impostor.evidence_classification, UNAVAILABLE)
        self.assertIn("source_id_not_canonical", impostor.provenance_reasons)

    def test_source_contract_drift_fails_closed_on_the_operator_surface(self) -> None:
        from trade_platform import real_market_data_provenance_v1 as provenance

        drifted = replace(
            provenance.canonical_bybit_source_contract_v1(), provider_terms_version="drifted"
        )
        with mock.patch.object(provenance, "canonical_bybit_source_contract_v1", return_value=drifted):
            item = self._datasets()[self.composite.dataset_version_id]
        self.assertEqual(item.evidence_classification, UNAVAILABLE)
        self.assertIn("source_contract_mismatch:provider_terms_version", item.provenance_reasons)

    def test_data_health_provenance_is_independent_of_health(self) -> None:
        blocking_real = self._health(self.composite.dataset_version_id, blocking=True)
        healthy_impostor = self._health(self.impostor)
        no_lineage = self._health(None)
        demo_on_real = self._health(self.composite.dataset_version_id, policy_version="demo-health-v1")
        fixture = self._health(self.fixture_dataset)
        queries = self._queries()
        expected = {
            blocking_real: (REAL, True),
            healthy_impostor: (UNAVAILABLE, False),
            no_lineage: (UNAVAILABLE, False),
            demo_on_real: (SYNTHETIC, False),
            fixture: (SYNTHETIC, False),
        }
        page = {item.assessment_id: item for item in queries.data_health_assessments(limit=50, offset=0).items}
        for assessment_id, (classification, blocking) in expected.items():
            detail = queries.data_health_assessment(assessment_id)
            for item in (page[assessment_id], detail):
                self.assertEqual(item.evidence_classification, classification, assessment_id)
                self.assertEqual(item.blocking, blocking)
        self.assertEqual(page[no_lineage].provenance_reasons, ["no_historical_dataset_lineage"])

    def test_one_request_proves_each_referenced_dataset_once(self) -> None:
        for _ in range(3):
            self._health(self.composite.dataset_version_id)
        self._health(self.parents["A"].dataset_version_id)
        authority = _CountingAuthority(self.database)
        page = self._queries(authority).data_health_assessments(limit=50, offset=0)
        self.assertGreaterEqual(
            sum(item.dataset_version_id == self.composite.dataset_version_id for item in page.items), 3
        )
        self.assertEqual(len(authority.calls), len(set(authority.calls)))
        self.assertIn(self.composite.dataset_version_id, authority.calls)

    def test_legacy_research_surfaces_cannot_become_real_from_bybit_text(self) -> None:
        """No authoritative bridge from legacy ``dataset_versions`` to historical datasets.

        The legacy chain says ``provider='bybit'`` and the scorecard's free-text
        ``dataset_version`` even names the real composite's id: neither is proof,
        so strategies, experiments, scorecards and signals stay UNAVAILABLE.
        """
        now = datetime(2000, 1, 1, 12, tzinfo=UTC)
        suffix = uuid4().hex[:8]
        digest = lambda name: hashlib.sha256(f"3d8c:{suffix}:{name}".encode()).hexdigest()
        ids = {name: uuid4() for name in (
            "dataset", "dataset_version", "strategy", "strategy_version", "experiment",
            "package", "scorecard", "signal",
        )}
        version_text = f"3d8c-{suffix}"
        family = f"PROVENANCE_3D8C_{suffix}"
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO datasets (dataset_id,name,provider,terms_version,created_at) "
                "VALUES (%s,%s,'bybit','terms-v1',%s)",
                (ids["dataset"], f"3d8c-dataset-{suffix}", now),
            )
            cursor.execute(
                "INSERT INTO dataset_versions (dataset_version_id,dataset_id,version,content_hash,created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (ids["dataset_version"], ids["dataset"], f"v-{suffix}", digest("dv"), now),
            )
            cursor.execute(
                "INSERT INTO strategy_definitions (strategy_id,family,hypothesis,created_at) "
                "VALUES (%s,%s,'Bybit-named provenance test.',%s)",
                (ids["strategy"], family, now),
            )
            cursor.execute(
                "INSERT INTO strategy_versions (strategy_version_id,strategy_id,version,feature_manifest,"
                "cost_model_version,capacity_model_version,contract,created_at) "
                "VALUES (%s,%s,%s,'[]'::jsonb,'cost-v1','capacity-v1','{}'::jsonb,%s)",
                (ids["strategy_version"], ids["strategy"], version_text, now),
            )
            cursor.execute(
                "INSERT INTO research_experiments (experiment_id,strategy_version_id,dataset_version_id,"
                "parameters,report,content_hash,created_at) VALUES (%s,%s,%s,'{}'::jsonb,'{}'::jsonb,%s,%s)",
                (ids["experiment"], ids["strategy_version"], ids["dataset_version"], digest("exp"), now),
            )
            cursor.execute(
                "INSERT INTO validation_packages (package_id,strategy_version_id,dataset_version_id,"
                "cost_model_version,content_hash,status,created_at,limitations,integrity_status) "
                "VALUES (%s,%s,%s,'cost-v1',%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,'[]'::jsonb,'LEGACY_UNVERIFIABLE')",
                (ids["package"], ids["strategy_version"], ids["dataset_version"], digest("pkg"), now),
            )
            cursor.execute(
                "INSERT INTO strategy_scorecards (scorecard_id,scorecard_schema_version,strategy_id,"
                "strategy_version,research_run_id,feature_versions,dataset_version,cost_model_version,"
                "evaluated_at,knowledge_cutoff,status,limitations,dataset_health_status,"
                "data_health_assessment_ids,evidence_manifest,content_hash) VALUES "
                "(%s,'scorecard-v2',%s,%s,%s,'[]'::jsonb,%s,'cost-v1',%s,%s,'REVIEW_REQUIRED',"
                "'[]'::jsonb,'HEALTHY','[]'::jsonb,'{}'::jsonb,%s)",
                (ids["scorecard"], ids["strategy"], version_text, uuid4(),
                 str(self.composite.dataset_version_id), now, now, digest("scorecard")),
            )
            cursor.execute(
                "INSERT INTO scorecard_validation_packages (scorecard_id,package_id,package_content_hash) "
                "VALUES (%s,%s,%s)",
                (ids["scorecard"], ids["package"], digest("pkg")),
            )
            cursor.execute(
                "INSERT INTO runtime_signal_proposals (signal_id,instrument_id,strategy_version,created_at,"
                "expires_at,payload) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                (ids["signal"], INSTRUMENT_ID, version_text, now, now + timedelta(hours=1),
                 json.dumps({"direction": "BUY", "explanation": "3d8c bybit-named signal"})),
            )
        queries = self._queries()
        surfaces = {
            "strategy": queries.strategies(family=family, limit=10, offset=0).items,
            "experiment": queries.experiments(strategy_id=ids["strategy"], limit=10, offset=0).items,
            "scorecards": queries.strategy_scorecards(
                strategy_id=ids["strategy"], status=None, limit=10, offset=0
            ).items,
            "signal": queries.signals(
                as_of=now, status=None, instrument=INSTRUMENT_ID, strategy_version=version_text,
                limit=10, offset=0,
            ).items,
        }
        for name, items in surfaces.items():
            self.assertEqual(len(items), 1, name)
            self.assertEqual(items[0].evidence_classification, UNAVAILABLE, name)
        self.assertEqual(
            queries.strategy_scorecard(ids["scorecard"]).evidence_classification, UNAVAILABLE
        )


if __name__ == "__main__":
    unittest.main()
