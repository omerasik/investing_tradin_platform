"""Pure acceptance-policy tests; never generate/evaluate the research workload."""

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_research_loading_performance_v1 import harness


def driver():
    path = Path(__file__).resolve().parents[1] / "scripts/research_hosted_acceptance.py"
    spec = importlib.util.spec_from_file_location("hosted_acceptance_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"research_loading_performance": harness()}):
        spec.loader.exec_module(module)
    return module


class HostedAcceptanceTests(unittest.TestCase):
    def report(self, m):
        result = {"completed": 40, "failed": 0, "excluded": 0, "registered_studies": 0,
            "unique_configurations_evaluated": 40, "declared_unique_configurations": 40,
            "dataset_content_hash": m.CONTENT, "manifest_hash": m.MANIFEST, "elapsed_seconds": 10,
            "after": {"peak_commit_bytes": 100, "peak_rss_bytes": 100},
            "input_fingerprints": {str(i): m.INPUT for i in range(3)},
            "unique_results": {str(i): "result" for i in range(40)},
            "result_chain": m.REFERENCE_CHAIN, "stages": {"verification": {"count": 40}}}
        return {"implementation": "reference", "evaluation_tasks": 40, "real_development_loader_only": False,
            "limits": {"admission_policy": "github-hosted-windows-p2b1-v1", "process_memory_bytes": 512 * 1024 ** 2,
                "working_set_bytes": 512 * 1024 ** 2, "wall_seconds_per_run": 120, "workers": 1, "numeric_threads": 1,
                "host_reserve_bytes": 2 * 1024 ** 3, "disk_floor_bytes": 4 * 1024 ** 3,
                "runtime_commit_floor_bytes": 2 * 1024 ** 3},
            "runs": [{"status": "COMPLETE", "wall_seconds": 11, "result": copy.deepcopy(result)} for _ in range(3)]}

    def test_repeated_reports_require_all_tasks_no_failures_and_exact_reference(self):
        m = driver()
        report = self.report(m)
        oracle, *_ = m.validate_report(report, 40, "reference")
        for field, value in [("completed", 39), ("failed", 1), ("excluded", 1),
                             ("result_chain", "changed"), ("unique_results", {"trial": "different"})]:
            bad = copy.deepcopy(report)
            bad["runs"][1]["result"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.validate_report(bad, 40, "reference", oracle)

    def test_limits_stops_timeouts_and_memory_are_not_promoted_to_acceptance(self):
        m = driver()
        report = self.report(m)
        for kind in ("RESOURCE_BLOCKED", "RESOURCE_STOPPED", "FAILED"):
            bad = copy.deepcopy(report)
            bad["runs"][1] = {"status": kind}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                m.validate_report(bad, 40, "reference")
        for where, key, value in [("run", "wall_seconds", 120.001),
                                  ("peak", "peak_commit_bytes", 512 * 1024 ** 2 + 1),
                                  ("limits", "numeric_threads", 2)]:
            bad = copy.deepcopy(report)
            target = (bad["runs"][1] if where == "run" else
                      bad["runs"][1]["result"]["after"] if where == "peak" else bad["limits"])
            target[key] = value
            with self.subTest(where=where), self.assertRaises(ValueError):
                m.validate_report(bad, 40, "reference")

    def test_fixture_portability_failure_preserves_actual_identities(self):
        m = driver()
        with tempfile.TemporaryDirectory(prefix="hosted-identity-unit-") as directory:
            root = Path(directory)
            catalog = root / "v1/datasets/public-archive-bars" / f"{m.DATASET}.json"
            manifest = root / "v1/manifests" / f"{m.MANIFEST}.json"
            obj = root / "v1/objects" / m.OBJECT[:2] / f"{m.OBJECT}.parquet"
            for path in (catalog, manifest, obj):
                path.parent.mkdir(parents=True, exist_ok=True)
            catalog.write_text(json.dumps({"dataset_version_id": m.DATASET, "content_hash": m.CONTENT,
                "bar_frame_manifest_hash": m.MANIFEST, "identity": {"bar_frame": {"logical_content_hash": m.LOGICAL}}}))
            manifest.write_text(json.dumps({"logical_content_hash": "changed", "row_count": 86400,
                "objects": [{"sha256": m.OBJECT, "bytes": 2048325, "rows": 86400}], "writer": {"pyarrow": "25.0.1"}}))
            obj.write_bytes(b"not a replacement dataset")
            evidence = m.fixture_evidence(root)
            self.assertFalse(evidence["matches"])
            self.assertIn("logical_hash", evidence["mismatches"])
            self.assertIn("object_sha256", evidence["mismatches"])
            self.assertIn("object_bytes", evidence["mismatches"])
            self.assertEqual(evidence["actual"]["logical_hash"], "changed")

    def test_workflow_is_opt_in_read_only_standard_windows_without_secrets_or_services(self):
        import yaml

        path = Path(__file__).resolve().parents[1] / ".github/workflows/research-p2b1-acceptance.yml"
        text = path.read_text()
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"pull_request"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["acceptance"]
        self.assertEqual(job["runs-on"], "windows-latest")
        self.assertIn("research-p2b1-acceptance", job["if"])
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        self.assertNotIn("services", job)
        self.assertNotIn("secrets.", text)
        self.assertEqual(job["steps"][0]["with"]["persist-credentials"], "false")
