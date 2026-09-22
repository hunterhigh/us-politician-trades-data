"""Local CLI gates for a complete, archive-bound White House 278-T batch."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from backend.scripts.build_whitehouse_278t_candidate import main, run
from backend.tests.test_whitehouse_278t_candidate import _base, _catalog, _wiles
from unison_snapshot.builder import SOURCE_HOSTS


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


class WhiteHouse278TCandidateCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.review = self.root / "review"
        self.oge = self.root / "oge-current.json"
        self.audit_out = self.root / "conservation.json"
        self.report = _wiles()
        url = self.report["source_url"]
        self.report["document_id"] = "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24]
        self.coverage = {
            "schema_version": "whitehouse-public-coverage/v1",
            "source_id": "whitehouse_public_disclosures",
            "report_link_count": 1,
            "counts_by_review_state": {"extracted_review_only": 1},
            "reports": [{
                "document_id": self.report["document_id"],
                "document_url": url, "document_type_from_label": "278t",
                "archive_sha256_versions": [self.report["source_sha256"]],
                "review_state": "extracted_review_only",
            }],
        }
        self.status = {
            "schema_version": "whitehouse-public-extraction-status/v1",
            "source_id": "whitehouse_public_disclosures",
            "archived_version_count": 1, "pending_count": 0,
        }
        self.extraction = (self.review / "whitehouse/extractions" /
                           self.report["document_id"].split(":", 1)[1] /
                           self.report["source_sha256"] /
                           "whitehouse-278t-pdf-v1.json")
        base = _base()
        base["meta"]["data_cutoff_at"] = "2025-06-14T00:00:00Z"
        _write(self.oge, base)
        self._write_inputs()

    def _write_inputs(self):
        _write(self.review / "whitehouse/coverage-current.json", self.coverage)
        _write(self.review / "whitehouse/extraction-status.json", self.status)
        _write(self.review / "oge/whitehouse/coverage-current.json", _catalog())
        _write(self.extraction, self.report)

    def _run(self, expected_report_count=None):
        # The integration branch already admits official White House OGE URLs.
        # This isolated worktree predates that producer allowlist commit.
        with patch.dict(SOURCE_HOSTS, {"oge": {"www.whitehouse.gov", "whitehouse.gov"}}):
            return run(oge_candidate=self.oge, review_root=self.review,
                       candidate_out=self.oge, audit_out=self.audit_out,
                       expected_report_count=expected_report_count)

    def test_complete_local_batch_publishes_pair_and_is_idempotent(self):
        before_coverage = (self.review / "whitehouse/coverage-current.json").read_bytes()
        with patch.dict(SOURCE_HOSTS, {"oge": {"www.whitehouse.gov", "whitehouse.gov"}}):
            with redirect_stdout(io.StringIO()) as output:
                exit_code = main([
                    "--oge-candidate", str(self.oge), "--review-root", str(self.review),
                    "--candidate-out", str(self.oge), "--audit-out", str(self.audit_out),
                    "--expected-report-count", "1",
                ])
        self.assertEqual(exit_code, 0)
        self.assertIn('"promoted_transaction_count": 3', output.getvalue())
        candidate_raw = self.oge.read_bytes()
        candidate = json.loads(candidate_raw)
        audit = json.loads(self.audit_out.read_bytes())
        self.assertEqual(len(candidate["transactions"]), 3)
        self.assertEqual(audit["report_count"], 1)
        self.assertEqual(audit["source_row_count"], 3)
        self.assertEqual(audit["promoted_transaction_count"], 3)
        self.assertEqual(audit["candidate_sha256"], hashlib.sha256(candidate_raw).hexdigest())
        self.assertIn("3 total OGE candidate transactions", candidate["source_health"][0]["detail"])
        self.assertEqual((self.review / "whitehouse/coverage-current.json").read_bytes(),
                         before_coverage)
        self.assertTrue(self._run()["idempotent"])
        self.assertEqual(self.oge.read_bytes(), candidate_raw)

    def test_wrong_archive_hash_pending_or_count_blocks_all_outputs(self):
        original = self.oge.read_bytes()
        self.coverage["reports"][0]["archive_sha256_versions"] = ["c" * 64]
        self._write_inputs()
        with self.assertRaises(ValueError):
            self._run()
        self.assertEqual(self.oge.read_bytes(), original)
        self.assertFalse(self.audit_out.exists())
        self.coverage["reports"][0]["archive_sha256_versions"] = [self.report["source_sha256"]]
        self.status["pending_count"] = 1
        self._write_inputs()
        with self.assertRaises(ValueError):
            self._run()
        self.status["pending_count"] = 0
        self._write_inputs()
        with self.assertRaises(ValueError):
            self._run(expected_report_count=2)
        self.assertEqual(self.oge.read_bytes(), original)
        self.assertFalse(self.audit_out.exists())

    def test_unaccounted_extraction_and_wrong_url_fail_closed(self):
        original = self.oge.read_bytes()
        extra = (self.extraction.parent.parent.parent / ("e" * 24) / ("d" * 64) /
                 "whitehouse-278t-pdf-v1.json")
        _write(extra, deepcopy(self.report))
        with self.assertRaises(ValueError):
            self._run()
        extra.unlink()
        self.report["source_url"] = self.report["source_url"].replace("Wiles", "Other")
        self._write_inputs()
        with self.assertRaises(ValueError):
            self._run()
        self.assertEqual(self.oge.read_bytes(), original)
        self.assertFalse(self.audit_out.exists())


if __name__ == "__main__":
    unittest.main()
