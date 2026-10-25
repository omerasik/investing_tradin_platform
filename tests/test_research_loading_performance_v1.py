"""Admission and interrupted-progress evidence; no performance workloads run here."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def harness():
    from scripts import research_performance

    path = Path(__file__).resolve().parents[1] / "scripts/research_loading_performance.py"
    spec = importlib.util.spec_from_file_location("bounded_loading_harness_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"research_performance": research_performance}):
        spec.loader.exec_module(module)
    return module


class LoadingHarnessEvidenceTests(unittest.TestCase):
    def test_disk_or_ram_or_commit_admission_failure_never_starts_a_child(self):
        module = harness()
        budget = 512 * module.MIB
        baseline = {"available_physical_bytes": budget + module.HOST_RESERVE,
                    "commit_headroom_bytes": budget + module.HOST_RESERVE,
                    "disk_free_bytes": module.DISK_FLOOR}
        for field, value in baseline.items():
            with self.subTest(field=field), patch.object(module, "host_memory", return_value={**baseline, field: value - 1}), \
                    patch.object(module.subprocess, "Popen") as spawn:
                result = module.run_child(SimpleNamespace(working_set_mib=512), budget)
                self.assertEqual(result["status"], "RESOURCE_BLOCKED")
                spawn.assert_not_called()

    def test_interrupted_json_never_upgrades_an_incomplete_gate_to_success(self):
        module = harness()
        progress = '{"progress":true,"completed":71,"failed":0}\n'
        for output in (progress, progress.encode(), progress + '{"progress":true,"completed":1000',
                       progress + '{"progress":"yes","completed":1000}\n', progress + 'not JSON\n'):
            with self.subTest(output=output):
                result = module.last_evaluation_progress(output)
                self.assertEqual(result["completed"], 71)
        self.assertIsNone(module.last_evaluation_progress('{"completed":1000}\n'))
        self.assertIsNone(module.last_evaluation_progress(b"\xff partial"))
