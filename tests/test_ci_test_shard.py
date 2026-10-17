"""The CI shard gate fails closed on any test that ran nowhere, twice, failed or skipped unexpectedly."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ci_test_shard

SUITE = ["test_a.A.test_1", "test_a.A.test_2", "test_dry_activation_e2e_postgres.D.test_1"]


def _report(shard: str, assigned: list[str], **overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "shard": shard, "discovered": sorted(SUITE), "assigned": sorted(assigned), "tests_run": len(assigned),
        "failures": 0, "errors": 0, "unexpected_successes": 0, "skipped": [], "successful": True,
    }
    report.update(overrides)
    return report


class ShardGateTests(unittest.TestCase):
    def _verify(self, *reports: dict[str, object]) -> list[str]:
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for index, report in enumerate(reports):
                path = Path(root) / f"{index}.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                paths.append(path)
            return ci_test_shard.verify(paths)

    def test_an_exact_partition_passes(self) -> None:
        self.assertEqual([], self._verify(_report("core", SUITE[:2]), _report("heavy", SUITE[2:])))

    def test_assignment_is_total_and_unknown_modules_run_in_core(self) -> None:
        self.assertEqual("heavy", ci_test_shard.shard_of(SUITE[2]))
        self.assertEqual("core", ci_test_shard.shard_of("test_brand_new_module.X.test_y"))
        self.assertEqual("core", ci_test_shard.shard_of("unittest.loader._FailedTest.test_broken_import"))

    def test_a_test_that_ran_in_no_shard_fails(self) -> None:
        problems = self._verify(_report("core", SUITE[:1]), _report("heavy", SUITE[2:]))
        self.assertTrue(any("ran in no shard" in p for p in problems), problems)

    def test_a_test_in_two_shards_fails(self) -> None:
        problems = self._verify(_report("core", SUITE), _report("heavy", SUITE[2:]))
        self.assertTrue(any("assigned to both" in p for p in problems), problems)

    def test_a_missing_shard_report_fails(self) -> None:
        self.assertTrue(self._verify(_report("core", SUITE)))

    def test_a_postgres_skip_fails_and_a_permitted_skip_passes(self) -> None:
        skipped = [[SUITE[2], "POSTGRES_TEST_DSN not configured"]]
        problems = self._verify(_report("core", SUITE[:2]), _report("heavy", SUITE[2:], skipped=skipped))
        self.assertTrue(any("skipped in CI" in p for p in problems), problems)
        permitted = [[SUITE[0], "TARDIS_FREE_SAMPLE_DIR is not set; never retained"]]
        self.assertEqual([], self._verify(_report("core", SUITE[:2], skipped=permitted), _report("heavy", SUITE[2:])))

    def test_a_failure_or_a_short_run_fails(self) -> None:
        self.assertTrue(self._verify(_report("core", SUITE[:2], successful=False, failures=1),
                                     _report("heavy", SUITE[2:])))
        self.assertTrue(self._verify(_report("core", SUITE[:2], tests_run=1), _report("heavy", SUITE[2:])))

    def test_shards_that_discovered_different_suites_fail(self) -> None:
        problems = self._verify(_report("core", SUITE[:2]), _report("heavy", SUITE[2:], discovered=SUITE[2:]))
        self.assertTrue(any("different suite" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
