"""Producer adapters preserve provenance, warm-up boundaries and explicit refusals."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from tests import test_paper_evidence_production_v1 as fixtures
from trade_platform import paper_evidence_production_v1 as production
from trade_platform.runner_liveness_v1 import RunnerLivenessError


class ProducerAdapterTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProductionTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ProductionTests.tearDownClass.__func__)
    sources = fixtures.ProductionTests.sources
    run_production = fixtures.ProductionTests.run_production

    def test_warmup_only_heartbeat_retains_consumed_history_without_forward_claim(self):
        start = fixtures.AS_OF - timedelta(hours=1)
        rows = fixtures.progress_rows(self.candidate, self.gate, self.bars, start=start)
        source = self.sources(liveness=rows, live_bars=self.bars)
        processed = production._processed_bars(source, self.candidate, fixtures.AS_OF)
        bound = production._bound_live_bars(self.bars, processed, symbol="BTCUSDT", as_of=fixtures.AS_OF)
        self.assertEqual(len(self.bars), len(bound))
        self.assertTrue(all(row["identity"]["kind"] != "BARS_PROCESSED" for row in rows))
        self.assertTrue(all(not row["identity"]["bars"] for row in rows))

    def test_live_repository_error_becomes_explicit_producer_refusal(self):
        source = production.PostgresPaperEvidenceSourcesV1(None, frame_store=None, capture_root=None, funding_root=None)
        with (patch("trade_platform.live_bar_evidence_v1.PostgresLiveBarStoreV1.for_candidate",
                    side_effect=RunnerLivenessError("fixture_corruption")),
              self.assertRaisesRegex(production.EvidenceInputRefusedV1, "RUNNER_LIVENESS_DOES_NOT_REDERIVE")):
            source.verified_live_bars(candidate=self.candidate, as_of=fixtures.AS_OF)

    def test_source_scan_uses_each_registered_selected_contract_and_io_refuses(self):
        source = production.PostgresPaperEvidenceSourcesV1(None, frame_store=object(), capture_root=Path("fixture"), funding_root=None)
        seal = SimpleNamespace(source_id="fixture-sol-source")
        contract = object()
        with (patch.object(source, "_seals", return_value=[seal]),
              patch("trade_platform.first_party_capture_authority_v1.resolve_t4_registered_first_party_capture_contract_v1", return_value=contract),
              patch("trade_platform.first_party_t4_seal_v1.discover_t4_segments_v1", side_effect=OSError("fixture_io")) as discover):
            with self.assertRaisesRegex(production.EvidenceInputRefusedV1, "SEAL_DOES_NOT_REDERIVE"):
                source.verified_replay_bars(symbol="SOLUSDT", start_micros=0, end_micros=1, as_of=fixtures.AS_OF, live_bars=[])
            discover.assert_called_once_with(Path("fixture"), contract=contract)

    def test_no_price_declaration_reports_unavailable_without_invented_mark(self):
        run = self.run_production(self.sources(live_bars=self.bars))
        self.assertTrue(run.economics[0]["read_only"])
        self.assertEqual("UNAVAILABLE", run.economics[0]["price_resolution"]["status"])
        self.assertEqual(["PAPER_PRICE_DECLARATION_NOT_SUPPLIED"], run.economics[0]["price_resolution"]["reasons"])
        self.assertEqual("NOT_COSTABLE", run.economics[0]["economics"]["candidates"][0]["net"]["status"])

    def test_restarted_runner_with_overlapping_consumption_is_explicitly_ambiguous(self):
        rows = fixtures.progress_rows(self.candidate, self.gate, self.bars, session=UUID(int=7))
        rows += fixtures.progress_rows(self.candidate, self.gate, self.bars, session=UUID(int=8))
        run = self.run_production(self.sources(liveness=rows, live_bars=self.bars))
        self.assertIn(production.AMBIGUOUS_RUNNER_PROGRESS, run.outcomes[0].reasons)

    def test_live_sample_and_source_cannot_be_self_asserted(self):
        bar = self.bars[0]
        source = production.PostgresPaperEvidenceSourcesV1(None, frame_store=object(), capture_root=Path("fixture"), funding_root=None)
        seal = SimpleNamespace(source_id=bar.capture_source_id, identity={"session_id": str(bar.session_id),
            "clock_evidence": {"sample_hashes": [bar.latest_clock_sample_hash]}})
        sample = SimpleNamespace(sample_hash=bar.latest_clock_sample_hash,
            venue_minus_host_upper_nanos=bar.clock_bound_nanos + 1, host_receive_utc_nanos=0)
        plan = SimpleNamespace(partition=SimpleNamespace(session_id=bar.session_id), clock=SimpleNamespace(samples=[sample]))
        with (patch.object(source, "_seals", return_value=[seal]),
              patch("trade_platform.first_party_capture_authority_v1.resolve_t4_registered_first_party_capture_contract_v1", return_value=object()),
              patch("trade_platform.first_party_t4_seal_v1.discover_t4_segments_v1", return_value=SimpleNamespace(segments=[plan]))):
            for changed in (bar, replace(bar, capture_source_id=UUID(int=123))):
                with self.assertRaisesRegex(production.EvidenceInputRefusedV1, "LIVE_BAR_NOT_RECORDED_BY_RUNNER"):
                    source.verified_replay_bars(symbol="BTCUSDT", start_micros=0, end_micros=1,
                                               as_of=fixtures.AS_OF, live_bars=[changed])

    def test_catalogue_source_excludes_registrations_after_the_reading(self):
        class Cursor:
            def execute(inner, query, parameters):
                self.assertIn("sealed_at <= %s AND registered_at <= %s", query)
                self.assertEqual((fixtures.AS_OF, fixtures.AS_OF), parameters[-2:])

            def fetchall(inner):
                return []

        source = production.PostgresPaperEvidenceSourcesV1(Cursor(), frame_store=object(),
            capture_root=Path("fixture"), funding_root=None)
        self.assertEqual([], source._seals(symbol="BTCUSDT", start_micros=0, end_micros=1, as_of=fixtures.AS_OF))
