"""Admission and interrupted-progress evidence; no performance workloads run here."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


def harness():
    from scripts import research_performance

    path = Path(__file__).resolve().parents[1] / "scripts/research_loading_performance.py"
    spec = importlib.util.spec_from_file_location("bounded_loading_harness_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"research_performance": research_performance}):
        spec.loader.exec_module(module)
    return module


class LoadingHarnessEvidenceTests(unittest.TestCase):
    def test_hosted_policy_is_explicit_context_bound_and_leaves_local_limits_unchanged(self):
        module = harness()
        args = SimpleNamespace(working_set_mib=512)
        self.assertEqual(module.resource_policy(args), {"name": "local-research-host-v1",
            "reserve": 512 * module.MIB, "disk_floor": 24 * 1024 ** 3, "commit_floor": 1024 ** 3})
        args.hosted_windows_acceptance = True
        with patch.dict(module.os.environ, {"GITHUB_ACTIONS": "false"}), self.assertRaises(ValueError):
            module.resource_policy(args)
        context = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Windows"}
        with patch.object(module.os, "name", "nt"), patch.dict(module.os.environ, context):
            policy = module.resource_policy(args)
            self.assertEqual(policy["reserve"], 2 * 1024 ** 3)
            self.assertEqual(policy["commit_floor"], 2 * 1024 ** 3)
            self.assertEqual(policy["disk_floor"], 4 * 1024 ** 3)
            args.real = True
            with self.assertRaises(ValueError):
                module.resource_policy(args)
            args.real, args.working_set_mib = False, 1024
            with self.assertRaises(ValueError):
                module.resource_policy(args)

    def test_hosted_sampling_protects_every_relevant_volume(self):
        module = harness()
        host = {"available_physical_bytes": 10, "commit_headroom_bytes": 20, "disk_free_bytes": 99}
        with patch.object(module, "host_memory", return_value=host), \
                patch.dict(module.os.environ, {"SystemDrive": "C:"}), \
                patch.object(module.shutil, "disk_usage", side_effect=[SimpleNamespace(free=x) for x in (9, 3, 7)]):
            self.assertEqual(module.sampled_host({"name": "github-hosted-windows-p2b1-v1"})["disk_free_bytes"], 3)

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

    def test_resource_stop_drains_windows_pipe_after_kill_without_continuing_work(self):
        module = harness()
        budget = 512 * module.MIB
        admitted = {"available_physical_bytes": budget + module.HOST_RESERVE,
                    "commit_headroom_bytes": budget + module.HOST_RESERVE,
                    "disk_free_bytes": module.DISK_FLOOR}
        child, kernel = Mock(), Mock()
        child._handle = 1
        child.poll.side_effect = [None, 1]
        child.communicate.return_value = ('{"progress":true,"completed":71,"failed":0}\n', '')
        kernel.SetProcessWorkingSetSizeEx.return_value = 1
        with patch.object(module, "host_memory", side_effect=[admitted,
                {**admitted, "available_physical_bytes": module.HOST_RESERVE - 1}]), \
                patch.object(module.subprocess, "Popen", return_value=child), \
                patch.object(module.bounds, "_windows_job", return_value=(kernel, 1)):
            result = module.run_child(SimpleNamespace(working_set_mib=512), budget)
        self.assertEqual(result["status"], "RESOURCE_STOPPED")
        self.assertEqual(result["stop_reason"], "host_resource_guard")
        self.assertEqual(result["last_progress"]["completed"], 71)
        child.kill.assert_called_once_with()
        child.communicate.assert_called_once_with(timeout=5)
