"""Portable normal-CLI inputs must fail before changing candidate outputs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.commands.overlay_whitehouse_annual_candidate import _runtime_input


class AnnualRuntimeTests(unittest.TestCase):
    def prepare(self, root):
        source = root / "archive.pdf"
        source.write_bytes(b"fixed source bytes")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        value = {"archive_metadata_by_document": {"doc": {"sha256": digest}},
            "saved_scan_observations_by_document": {"doc": {"archived_pdf_path": "archive.pdf"}}}
        path = root / "input.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path, value

    def test_input_can_move_with_its_root_and_stays_unmodified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, value = self.prepare(root)
            loaded, receipt = _runtime_input(path, root)
            self.assertEqual(json.loads(path.read_bytes()), value)
            self.assertEqual(loaded["saved_scan_observations_by_document"]["doc"]["archived_pdf_path"],
                             str((root / "archive.pdf").resolve()))
            self.assertEqual(receipt["files"], [{"path": "archive.pdf",
                "sha256": value["archive_metadata_by_document"]["doc"]["sha256"]}])
            value["saved_scan_observations_by_document"]["doc"]["archived_pdf_path"] = str((root / "archive.pdf").resolve())
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be relative"):
                _runtime_input(path, root)

    def test_missing_model_dependency_is_explicit_and_does_not_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, value = self.prepare(root)
            (root / "model").mkdir()
            value["handwritten_date_inputs_by_document"] = {"doc": {"model_directory": "model"}}
            path.write_text(json.dumps(value), encoding="utf-8")
            with patch("importlib.util.find_spec", return_value=None):
                with self.assertRaisesRegex(ValueError, "runtime dependency missing: PyMuPDF"):
                    _runtime_input(path, root)
            self.assertEqual(list((root / "model").iterdir()), [])

    def test_wrong_source_fails_before_cli_reads_or_mutates_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.prepare(root)
            (root / "archive.pdf").write_bytes(b"different PDF version")
            candidate, audit = root / "candidate.json", root / "audit.json"
            candidate.write_bytes(b"candidate sentinel")
            audit.write_bytes(b"audit sentinel")
            repo = Path(__file__).resolve().parents[2]
            result = subprocess.run([sys.executable, str(repo / "backend/src/unison_snapshot/commands/overlay_whitehouse_annual_candidate.py"),
                "--review-root", str(root / "review"), "--candidate", str(candidate), "--audit-out", str(audit),
                "--oge-annual-input", str(path), "--oge-annual-runtime-root", str(root)],
                env=dict(os.environ, PYTHONPATH=str(repo / "backend/src")), capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"Annual runtime hash differs", result.stderr)
            self.assertEqual(candidate.read_bytes(), b"candidate sentinel")
            self.assertEqual(audit.read_bytes(), b"audit sentinel")


if __name__ == "__main__":
    unittest.main()
