"""Exercise the public CLI process, including its real-data fail-closed boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def invoke(self, *args, environment=None):
        process_environment = dict(os.environ, PYTHONPATH=str(BACKEND / "src"))
        if environment:
            process_environment.update(environment)
        return subprocess.run([sys.executable, "-m", "unison_snapshot", *args],
                              env=process_environment,
                              capture_output=True, text=True)

    def test_help_documents_demo_only_publication(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("publish-demo", result.stdout)
        self.assertIn("prepare-publication", result.stdout)
        self.assertIn("fetch-public", result.stdout)
        self.assertIn("discover-house-index", result.stdout)
        self.assertIn("archive-house-ptr", result.stdout)
        self.assertIn("parse-house-ptr", result.stdout)
        self.assertIn("create-house-ptr-review", result.stdout)
        self.assertIn("promote-house-ptr-review", result.stdout)
        self.assertIn("qualify-house-ptr", result.stdout)
        self.assertIn("build-house-candidate", result.stdout)
        self.assertIn("build-disclosure-candidate", result.stdout)
        self.assertIn("parse-senate-members", result.stdout)
        self.assertIn("discover-senate-members", result.stdout)
        self.assertIn("parse-senate-search-page", result.stdout)
        self.assertIn("senate-efd-gate", result.stdout)
        self.assertIn("discover-senate-efd", result.stdout)
        self.assertIn("match-senate-catalog", result.stdout)
        self.assertIn("archive-senate-report-entrypoints", result.stdout)
        self.assertIn("extract-senate-report-entrypoints", result.stdout)
        self.assertIn("discover-house-members", result.stdout)
        self.assertIn("suggest-house-identity", result.stdout)
        self.assertIn("plan-house-ptr-sync", result.stdout)
        self.assertIn("record-house-ptr-result", result.stdout)
        self.assertNotIn("publish-live", result.stdout)

    def test_real_input_fails_before_creating_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "input.json"
            source.write_text(json.dumps({"meta": dict(is_demo=False)}), encoding="utf-8")
            result = self.invoke("publish-demo", "--input", str(source), "--store", str(folder / "repo.git"),
                                 "--generated-at", "2026-09-18T00:01:00Z")
            self.assertEqual(result.returncode, 2)
            self.assertIn("Production admission", result.stderr)
            self.assertFalse((folder / "repo.git").exists())

    def test_senate_gate_cli_is_disabled_by_default_and_requires_exact_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "gate.json"
            result = self.invoke("senate-efd-gate", "--output", str(output), environment={
                "SENATE_EFD_COLLECTION_ENABLED": "",
                "SENATE_EFD_TERMS_ACKNOWLEDGED": "",
            })
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "disabled")

            result = self.invoke("senate-efd-gate", "--output", str(output), environment={
                "SENATE_EFD_COLLECTION_ENABLED": "true",
                "SENATE_EFD_TERMS_ACKNOWLEDGED": "false",
            })
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "blocked")

            result = self.invoke("senate-efd-gate", "--output", str(output), environment={
                "SENATE_EFD_COLLECTION_ENABLED": "TRUE",
                "SENATE_EFD_TERMS_ACKNOWLEDGED": "false",
            })
            self.assertEqual(result.returncode, 2)
            self.assertIn("exactly true or false", result.stderr)
