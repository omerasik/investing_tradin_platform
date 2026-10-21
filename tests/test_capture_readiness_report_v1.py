"""Bounded archive fixtures exercise the read-only OR-1 report, not a recorder."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from trade_platform.capture_readiness_report_v1 import (
    HOST_COMPONENTS,
    REQUIRED_SYMBOLS,
    HostObservationV1,
    HostStateV1,
    capture_readiness_report_v1,
)
from trade_platform.first_party_capture_archive_v1 import (
    END_PROOF_CONNECTION_LOST,
    END_PROOF_OPERATOR_BOUNDED_STOP,
    CaptureClockReadingV1,
    CaptureCoverageIntervalV1,
    CapturePartitionWriterV1,
    build_capture_record_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_universe_contract_v1,
)
from trade_platform.first_party_capture_hourly_acceptance_v1 import HOUR_NANOS

DAY = 1_791_504_000_000_000_000  # 2026-10-09 UTC
SECOND = 1_000_000_000


class CaptureReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def partition(self, symbol, start=DAY, end=DAY + HOUR_NANOS,
                  end_proof=END_PROOF_OPERATOR_BOUNDED_STOP):
        contract = first_party_bybit_universe_contract_v1(symbol)
        session = uuid4()
        writer = CapturePartitionWriterV1(
            root=self.root, contract=contract, session_id=session,
            day=datetime.fromtimestamp(start // SECOND, UTC).date(), clock_resolution_nanos=1,
        )
        payload = json.dumps({"topic": f"tickers.{symbol}", "type": "snapshot",
                              "ts": start // 1_000_000, "data": {"symbol": symbol,
                              "markPrice": "100", "indexPrice": "100", "lastPrice": "100"}})
        for index, instant in enumerate((start, end - 1)):
            writer.append_record(build_capture_record_v1(
                contract=contract, session_id=session, sequence=index,
                clock=CaptureClockReadingV1(instant, instant - DAY + 1), payload_text=payload,
            ))
        writer.declare_coverage(CaptureCoverageIntervalV1(start, end - 1, end_proof, 2))
        return symbol, writer.finalize().parent

    def report(self, partitions=(), **kwargs):
        options = {"start_utc_nanos": DAY, "end_utc_nanos": DAY + 2 * HOUR_NANOS,
                   "observed_at_utc_nanos": DAY + HOUR_NANOS + SECOND,
                   "diagnostic_max_unproven_nanos": 0, "max_partitions": 100,
                   "max_report_hours": 48}
        options.update(kwargs)
        return capture_readiness_report_v1(partitions, **options)

    def observation(self, component="recorder", state=HostStateV1.OK, **kwargs):
        return replace(HostObservationV1(component, state, "fixture:independent-probe",
                                        DAY + HOUR_NANOS, 5 * SECOND, "probe result"), **kwargs)

    def test_complete_verified_hours_formal_s_stays_unresolved(self):
        selected = [self.partition(symbol) for symbol in REQUIRED_SYMBOLS]
        before = {str(path): path.read_bytes() for _, directory in selected
                  for path in directory.iterdir()}
        result = self.report(selected, host_observations=[self.observation()])
        self.assertEqual(result.diagnostic_status.acceptance.longest_run.hours, 1)
        self.assertEqual(result.current_run_hours, 1)
        self.assertEqual(result.formal_acceptance, "UNAVAILABLE")
        self.assertEqual(result.formal_bound_s, "UNRESOLVED_OWNER_APPROVAL_REQUIRED")
        self.assertEqual(result.readiness, "NO_GO")
        self.assertEqual(result.paging_threshold_nanos, 60 * SECOND)
        self.assertTrue(all(item.verified_partitions == 1 for item in result.symbols))
        self.assertTrue(all(item.latest_diagnostic_complete_hour_start_utc_nanos == DAY
                            for item in result.symbols))
        after = {str(path): path.read_bytes() for _, directory in selected
                 for path in directory.iterdir()}
        self.assertEqual(before, after)
        json.dumps(asdict(result), default=str)

    def test_missing_symbol_and_elapsed_trailing_hours_are_explicit(self):
        result = self.report([self.partition("BTCUSDT"), self.partition("ETHUSDT")],
                             observed_at_utc_nanos=DAY + 4 * HOUR_NANOS,
                             end_utc_nanos=DAY + 4 * HOUR_NANOS)
        sol = result.symbols[2]
        self.assertEqual(sol.missing_hours_utc_nanos,
                         tuple(DAY + i * HOUR_NANOS for i in range(4)))
        self.assertIsNone(sol.latest_diagnostic_complete_hour_start_utc_nanos)
        self.assertEqual(result.symbols[0].missing_hours_utc_nanos,
                         tuple(DAY + i * HOUR_NANOS for i in range(1, 4)))
        self.assertIsNone(result.diagnostic_status.acceptance.longest_run)
        self.assertEqual(result.current_run_hours, 0)

    def test_historical_24h_run_does_not_become_current_after_stop(self):
        selected = [self.partition(symbol, DAY, DAY + 24 * HOUR_NANOS)
                    for symbol in REQUIRED_SYMBOLS]
        result = self.report(selected, end_utc_nanos=DAY + 28 * HOUR_NANOS,
                             observed_at_utc_nanos=DAY + 28 * HOUR_NANOS)
        self.assertTrue(result.diagnostic_status.acceptance.met)
        self.assertEqual(result.diagnostic_status.acceptance.longest_run.hours, 24)
        self.assertEqual(result.diagnostic_status.state.value, "STALE")
        self.assertEqual(result.current_run_hours, 0)
        self.assertEqual(result.readiness, "NO_GO")

    def test_mid_hour_stop_and_independent_recorder_stop_withdraw_current(self):
        selected = [self.partition(symbol, DAY, DAY + HOUR_NANOS + 120 * SECOND,
                                   END_PROOF_CONNECTION_LOST) for symbol in REQUIRED_SYMBOLS]
        result = self.report(selected, observed_at_utc_nanos=DAY + HOUR_NANOS + 121 * SECOND,
                             host_observations=[self.observation(state=HostStateV1.STOPPED)])
        self.assertEqual(result.diagnostic_status.state.value, "INTERRUPTED")
        self.assertEqual(result.current_run_hours, 0)
        self.assertEqual(len(result.diagnostic_status.acceptance.pending_hour_interruptions), 3)

    def test_gap_splits_run_and_pages_without_relaxing_acceptance(self):
        selected = [self.partition(symbol, DAY + offset * HOUR_NANOS,
                                   DAY + (offset + 1) * HOUR_NANOS)
                    for symbol in REQUIRED_SYMBOLS for offset in (0, 2)]
        result = self.report(selected, end_utc_nanos=DAY + 4 * HOUR_NANOS,
                             observed_at_utc_nanos=DAY + 3 * HOUR_NANOS + SECOND)
        self.assertEqual(result.diagnostic_status.acceptance.longest_run.hours, 1)
        self.assertEqual(result.symbols[0].missing_hours_utc_nanos, (DAY + HOUR_NANOS,))
        self.assertEqual(result.symbols[0].paging_breaches[0].uncovered_nanos, HOUR_NANOS)
        self.assertEqual(result.symbols[0].incomplete_hours[0].verdict.value,
                         "NO_PROVEN_COVERAGE")

    def test_alive_process_and_api_ping_never_prove_capture(self):
        observations = [self.observation(component=component) for component in HOST_COMPONENTS]
        result = self.report(host_observations=observations)
        self.assertEqual(result.current_run_hours, 0)
        self.assertIsNone(result.diagnostic_status.acceptance.longest_run)
        self.assertEqual(result.diagnostic_status.state.value, "NO_EVIDENCE")
        self.assertEqual(result.readiness, "NO_GO")

    def test_paging_threshold_never_becomes_acceptance_bound(self):
        selected = [self.partition(symbol, DAY + 30 * SECOND) for symbol in REQUIRED_SYMBOLS]
        strict = self.report(selected)
        self.assertTrue(all(not item.paging_breaches for item in strict.symbols))
        self.assertTrue(all(len(item.incomplete_hours) == 1 for item in strict.symbols))
        diagnostic = self.report(selected, diagnostic_max_unproven_nanos=30 * SECOND)
        self.assertEqual(diagnostic.longest_diagnostic_joint_run_hours, 1)
        self.assertEqual(diagnostic.formal_acceptance, "UNAVAILABLE")

    def test_fresh_stopped_recorder_withdraws_apparently_current_history(self):
        selected = [self.partition(symbol) for symbol in REQUIRED_SYMBOLS]
        result = self.report(selected, host_observations=[self.observation(state=HostStateV1.STOPPED)])
        self.assertEqual(result.diagnostic_status.state.value, "CURRENT")
        self.assertEqual(result.longest_diagnostic_joint_run_hours, 1)
        self.assertEqual(result.current_run_hours, 0)

    def test_absent_stale_future_and_unattributed_host_evidence_is_unknown(self):
        observations = [self.observation(), self.observation("disk", max_age_nanos=0),
                        self.observation("clock", observed_at_utc_nanos=DAY + 3 * HOUR_NANOS),
                        self.observation("deadman", evidence_reference="")]
        result = self.report(host_observations=observations)
        self.assertEqual(result.host_health[0].state, HostStateV1.OK)
        self.assertTrue(all(item.state is HostStateV1.UNKNOWN for item in result.host_health[1:]))
        self.assertIn("OBSERVATION_STALE", [item.reason for item in result.host_health])
        self.assertTrue(any("fresh independent oci evidence" in item for item in result.owner_actions))

    def test_tampered_wrong_source_missing_and_future_partitions_refused(self):
        btc = self.partition("BTCUSDT")
        eth = self.partition("ETHUSDT")
        (btc[1] / "records.ndjson").write_text("{}\n", encoding="utf-8")
        future = self.partition("SOLUSDT", DAY + 2 * HOUR_NANOS, DAY + 3 * HOUR_NANOS)
        result = self.report([btc, ("SOLUSDT", eth[1]), ("ETHUSDT", self.root / "absent"), future])
        self.assertEqual(len(result.refusals), 4)
        self.assertTrue(any("EVIDENCE_AFTER_OBSERVATION" in item.reasons for item in result.refusals))
        self.assertTrue(all(item.verified_partitions == 0 for item in result.symbols))

    def test_budget_range_and_duplicate_validation(self):
        for kwargs in ({"max_report_hours": 1}, {"max_partitions": 0},
                       {"start_utc_nanos": DAY + 1}, {"diagnostic_max_unproven_nanos": HOUR_NANOS},
                       {"end_utc_nanos": DAY + 3 * HOUR_NANOS}, {"max_report_hours": True},
                       {"diagnostic_max_unproven_nanos": -1}, {"max_partitions": 1.0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.report(**kwargs)
        with self.assertRaises(ValueError):
            self.report([("BTCUSDT", self.root), ("ETHUSDT", self.root)])
        with self.assertRaises(ValueError):
            self.report(host_observations=[self.observation(), self.observation()])

    def test_cli_has_no_acquisition_or_host_side_effects(self):
        from scripts.capture_readiness_report import main

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--start", "2026-10-09T00:00:00Z", "--end", "2026-10-09T01:00:00Z",
                         "--observed-at", "2026-10-09T02:00:00Z",
                         "--diagnostic-max-unproven-nanos", "0", "--max-partitions", "3",
                         "--max-report-hours", "24"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["readiness"], "NO_GO")


if __name__ == "__main__":
    unittest.main()
