"""Portable policy tests; never starts containers or accesses private Lite state."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.qualify_lite_images import (
    GIB,
    MIB,
    Qualification,
    admission,
    admitted,
    private_path,
    scan_summary,
)


class LiteImageQualificationTests(unittest.TestCase):
    def test_successful_tool_with_non_utf8_diagnostics_keeps_exit_code_enforcement(self):
        sample = {'available_bytes': 6 * GIB, 'commit_headroom_bytes': 6 * GIB,
                  'disk_free_bytes': 16 * GIB, 'cpus': 2}
        with tempfile.TemporaryDirectory() as directory, patch('scripts.qualify_lite_images.host', return_value=sample):
            runner = Qualification.__new__(Qualification)
            runner.temp = Path(directory)
            runner.evidence = {'host_minimum': dict(sample), 'steps': []}
            runner.secret_stage = False
            command = [sys.executable, '-c', 'import os; os.write(1, bytes([255]))']
            self.assertEqual('\ufffd', runner.run('non_utf8_fixture', command))
            self.assertEqual(0, runner.evidence['steps'][-1]['exit_code'])
            with self.assertRaisesRegex(RuntimeError, 'command_failed'):
                runner.run('failed_fixture', [sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(7, runner.evidence['steps'][-1]['exit_code'])

    def test_workflow_is_manual_main_only_and_uploads_only_sanitized_summaries(self):
        import yaml

        path = Path(__file__).resolve().parents[1] / '.github/workflows/lite-image-qualification.yml'
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(['workflow_dispatch'], list(workflow['on']))
        self.assertEqual({'contents': 'read'}, workflow['permissions'])
        self.assertEqual("github.ref == 'refs/heads/main'", workflow['jobs']['qualify']['if'])
        uploads = [s for s in workflow['jobs']['qualify']['steps'] if 'actions/upload-artifact@' in s.get('uses', '')]
        self.assertEqual(1, len(uploads))
        self.assertEqual(['lite-image-evidence/admission.json', 'lite-image-evidence/qualification.json'],
                         uploads[0]['with']['path'].split())

    def test_refuses_workstation_before_reading_capacity_or_creating_artifacts(self):
        with patch.dict('os.environ', {}, clear=True), patch('scripts.qualify_lite_images.host') as sample:
            with self.assertRaisesRegex(RuntimeError, 'github_hosted'):
                admission()
            sample.assert_not_called()

    def test_capacity_policy_is_separate_and_fails_closed(self):
        good = {'available_bytes': 5 * GIB, 'commit_headroom_bytes': 5 * GIB,
                    'disk_free_bytes': 12 * GIB, 'cpus': 2}
        self.assertTrue(admitted(good, True))
        for key in good:
            self.assertFalse(admitted({**good, key: 0}, True))
        self.assertTrue(admitted({**good, 'available_bytes': 512 * MIB}, False))
        self.assertFalse(admitted({**good, 'available_bytes': 512 * MIB - 1}, False))

    def test_scan_summary_preserves_fixable_gate_without_secret_text(self):
        raw = {'Metadata': {'OS': {'EOSL': True}}, 'Results': [{
            'Secrets': [{'Match': 'must-never-be-exported'}], 'Vulnerabilities': [
                {'VulnerabilityID': 'CVE-fixture', 'PkgName': 'fixture', 'Severity': 'HIGH', 'FixedVersion': '2'},
                {'VulnerabilityID': 'CVE-unfixed', 'Severity': 'CRITICAL'}]}]}
        result = scan_summary(raw)
        self.assertEqual({'HIGH': 1, 'CRITICAL': 1}, result['severity_counts'])
        self.assertEqual(1, len(result['fixable_high_critical']))
        self.assertEqual(1, result['secret_findings'])
        self.assertTrue(result['eol'])
        self.assertNotIn('must-never', str(result))

    def test_private_files_are_rejected_in_any_app_layer(self):
        for path in ('./app/evidence/frame', 'app/settings.local.json', 'app/data/private',
                     'app/archive/day', 'app/a.parquet', 'app/a.dump', 'app/x.sqlite3'):
            self.assertTrue(private_path(path), path)
        for path in ('app/src/trade_platform/api.py', 'usr/lib/python3.12/site.py', 'app/package.json'):
            self.assertFalse(private_path(path), path)
