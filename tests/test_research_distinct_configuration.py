"""Canonical enumeration and acceptance metadata; no large evaluation workload."""

import copy
import dataclasses
import hashlib
import json
import sys
import tempfile
import unittest
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from tests import test_research_hosted_acceptance as hosted_tests
from tests.test_research_loading_performance_v1 import harness
from trade_platform.strategy_lab_study_v1 import (
    DatasetBindingV1,
    SearchModeV1,
    SearchPlanV1,
    StudySpecV1,
)
from trade_platform.strategy_sdk_v1 import FAMILIES_V1


def study(family):
    from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1

    space = family.parameter_space()
    return StudySpecV1(family.spec(), space, (DatasetBindingV1("bars", UUID(int=741), "a" * 64,
        EvidenceTierV1.T2_EVENT_TIME, datetime(2026, 6, 1, tzinfo=UTC)),),
        SearchPlanV1(SearchModeV1.EXHAUSTIVE, space.cardinality), datetime(2026, 6, 2, tzinfo=UTC))


class DistinctConfigurationTests(unittest.TestCase):
    def test_exact_canonical_counts_reasons_decimal_domains_and_unmodified_trial_identities(self):
        module = harness()
        expected = {"trend_ma_cross": (480, 380, {"fast_not_shorter_than_slow": 100}),
                    "mean_reversion_z": (240, 240, {}),
                    "breakout_channel": (84, 64, {"exit_channel_not_shorter": 20})}
        identities = set()
        for name, family in FAMILIES_V1.items():
            spec = study(family)
            valid, catalog = module.distinct_trials(spec)
            raw, accepted, reasons = expected[name]
            self.assertEqual((spec.planned_trial_count, len(valid)), (raw, accepted))
            self.assertEqual(catalog["rejection_reasons"], reasons)
            self.assertEqual(catalog["raw_points"], raw)
            self.assertEqual(catalog["rejected_points"], raw - accepted)
            self.assertEqual(catalog["points"], module.distinct_trials(spec)[1]["points"])
            for trial in valid:
                self.assertEqual(trial, spec.trial_for(trial.parameters, ordinal=trial.ordinal))
                self.assertEqual(trial.ordinal, trial.space_index)
                self.assertIsNone(family.inadmissible(spec.parameter_space.typed_point(trial.parameters)))
                self.assertNotIn(trial.content_hash, identities)
                identities.add(trial.content_hash)
                for domain in spec.parameter_space.domains:
                    if domain.kind.value == "DECIMAL":
                        self.assertIsInstance(spec.parameter_space.typed_point(trial.parameters)[domain.name], Decimal)
            representatives = module.representative_trials(valid)
            self.assertEqual(len({t.content_hash for t in representatives}), 4)
            self.assertEqual((representatives[0], representatives[-1]), (valid[0], valid[-1]))
            self.assertEqual([t.space_index for t in representatives], sorted(t.space_index for t in representatives))
        self.assertEqual(len(identities), 684)

    def test_duplicates_incomplete_enumeration_and_altered_domains_fail_instead_of_truncating(self):
        module = harness()
        spec = study(FAMILIES_V1["trend_ma_cross"])
        points = list(spec.trials())
        for bad in (points + [points[0]], points[:-1]):
            with self.subTest(length=len(bad)), patch.object(StudySpecV1, "trials", return_value=iter(bad)), \
                    self.assertRaises(ValueError):
                module.distinct_trials(spec)
        domains = spec.parameter_space.domains
        altered = dataclasses.replace(spec.parameter_space, domains=(dataclasses.replace(domains[0], values=domains[0].values[:2]), *domains[1:]))
        with self.assertRaises(ValueError):
            module.distinct_trials(dataclasses.replace(spec, parameter_space=altered))

    def report(self, tasks=684, implementation="optimized"):
        module, controller = harness(), hosted_tests.driver()
        report = hosted_tests.HostedAcceptanceTests().report(controller)
        result = report["runs"][0]["result"]
        catalogs = [module.distinct_trials(study(f))[1] for f in FAMILIES_V1.values()]
        values = {}
        for c in catalogs:
            points = [p for p in c["points"] if p["rejected_reason"] is None]
            for p in module.representative_trials(points) if tasks == 12 else points:
                values[p["trial_hash"]] = {"family": c["family"], "study_id": c["study_id"],
                    "study_hash": c["study_hash"], **{k: p[k] for k in (
                        "parameters", "ordinal", "space_index", "trial_id", "trial_hash")}, "outcome": "EVALUATED"}
        result.update(completed=tasks, unique_configurations_evaluated=tasks, declared_unique_configurations=684,
            unique_results=values, planned_parameter_points=804, rejected_parameter_points=120,
            configuration_catalog=catalogs, configuration_catalog_hash=hashlib.sha256(
                json.dumps(catalogs, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            reference_comparisons=12, stages={"verification": {"count": tasks if implementation == "reference" else 3}})
        report.update(evaluation_tasks=tasks, implementation=implementation, distinct_configurations=True,
            runs=[{"status": "COMPLETE", "wall_seconds": 11, "result": copy.deepcopy(result)} for _ in range(3)])
        return report

    def test_all_684_are_required_and_reference_subset_plus_full_repetitions_must_match(self):
        controller = hosted_tests.driver()
        reference = self.report(12, "reference")
        oracle, *_ = controller.validate_report(reference, 12, "reference", distinct=True)
        report = self.report()
        controller.validate_report(report, 684, "optimized", oracle, distinct=True)
        mutations = ("missing", "duplicate_parameters", "identity", "inconsistent_repeat", "reference_count", "rejection_count")
        for mutation in mutations:
            bad = copy.deepcopy(report)
            r = bad["runs"][1]["result"]
            key = next(iter(r["unique_results"]))
            if mutation == "missing":
                del r["unique_results"][key]
            elif mutation == "duplicate_parameters":
                r["configuration_catalog"][0]["points"][1]["parameters"] = r["configuration_catalog"][0]["points"][0]["parameters"]
            elif mutation == "identity":
                r["unique_results"][key]["trial_id"] = "invented"
            elif mutation == "inconsistent_repeat":
                r["unique_results"][key]["unexpected"] = "changed"
            elif mutation == "reference_count":
                r["reference_comparisons"] = 11
            else:
                r["rejected_parameter_points"] = 119
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                controller.validate_report(bad, 684, "optimized", oracle, distinct=True)
        self.assertEqual(Counter(v["family"] for v in report["runs"][0]["result"]["unique_results"].values()),
            {"trend_ma_cross": 380, "mean_reversion_z": 240, "breakout_channel": 64})

    def test_existing_workflow_adds_only_narrow_distinct_opt_in_and_retains_limits(self):
        import yaml

        m = harness()
        text = (m.ROOT / ".github/workflows/research-p2b1-acceptance.yml").read_text()
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        job, = workflow["jobs"].values()
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(job["runs-on"], "windows-latest")
        self.assertIn("codex/distinct-configuration-baseline", job["if"])
        self.assertIn("research-p2b21-acceptance", job["if"])
        self.assertNotIn("secrets.", text)
        self.assertNotIn("services", job)
        self.assertEqual(set(workflow["on"]), {"pull_request"})
        self.assertEqual(job["env"]["OMP_NUM_THREADS"], "1")

    def test_inconsistent_complete_repeats_are_written_as_negative_evidence(self):
        module = harness()
        result = {"result_chain": "one", "unique_results": {}, "completed": 684,
            "dataset_content_hash": "hash", "elapsed_seconds": 1, "tasks_per_second": 684}
        runs = [{"status": "COMPLETE", "result": copy.deepcopy(result)} for _ in range(3)]
        runs[1]["result"]["result_chain"] = "changed"
        with tempfile.TemporaryDirectory(prefix="distinct-negative-receipt-") as directory:
            output = Path(directory) / "negative.json"
            args = ["harness", "--distinct-configurations", "--evaluation-tasks", "684",
                "--root", directory, "--dataset", "test", "--expected", "hash", "--output", str(output)]
            with patch.object(sys, "argv", args), patch.object(module, "run_child", side_effect=runs):
                self.assertEqual(module.main(), 2)
            receipt = json.loads(output.read_bytes())
            self.assertEqual(receipt["validation_error"], "non_deterministic_evaluation_identity")
            self.assertEqual(len(receipt["runs"]), 3)
