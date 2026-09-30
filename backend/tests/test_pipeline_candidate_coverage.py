import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from unison_snapshot.pipeline_qa.candidate_coverage import audit_candidate_coverage
from unison_snapshot.pipeline_qa.canonical_compare import git_blob_sha1
from unison_snapshot.pipeline_qa.projection_audit import ProjectionAuditError


class CandidateCoverageTests(unittest.TestCase):
    def test_reports_fixed_transactions_outside_shadow(self):
        transactions = [{"id": "matched", "source_id": "oge", "filing_id": "a"},
                        {"id": "whitehouse", "source_id": "oge", "filing_id": "wh-url:x"},
                        {"id": "other", "source_id": "oge", "filing_id": "b"}]
        payload = {"meta": {"is_demo": False}, "transactions": transactions}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.json"
            raw = json.dumps(payload).encode()
            path.write_bytes(raw)
            binding = {"schema_version": "pipeline-fixed-canonical-binding/v1",
                       "projection_ready": False, "bundle_run_id": "fixed",
                       "candidate_inputs": {"oge": {"review_commit": "a" * 40,
                                                    "candidate_blob_sha1": git_blob_sha1(raw)}},
                       "matched_rows": [{"scoped_row": ["oge", "a", "b", "c"],
                                         "canonical_transaction_id": "matched",
                                         "review_commit": "a" * 40}]}
            result = audit_candidate_coverage(binding, {"oge": path})
            self.assertEqual(result["counts"], {"existing": 3, "bound": 1, "outside": 2})
            self.assertEqual(result["sources"]["oge"]["outside_by_channel"],
                             {"other_fixed_candidate": 1, "whitehouse_url": 1})
            self.assertFalse(result["candidate_coverage_complete"])
            self.assertFalse(result["projection_ready"])
            path.write_bytes(raw + b" ")
            with self.assertRaisesRegex(ProjectionAuditError, "blob mismatch"):
                audit_candidate_coverage(binding, {"oge": path})


if __name__ == "__main__":
    unittest.main()
