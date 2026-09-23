import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.codec import digest, encode
from unison_snapshot.release_readiness import ReleaseReadinessError, validate_first_launch


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "status").mkdir()
        (self.root / "candidates" / "sources").mkdir(parents=True)
        self._write("status/house_clerk.json", {
            "schema_version": "house-review-run/v1", "evidence_count": 3,
            "extracted_count": 2, "failure_count": 1, "pending_parse_count": 0,
            "qualification_count": 2, "status_conflict_count": 0,
        })
        self._write("status/house_holdings.json", {
            "schema_version": "house-holding-run/v1", "evidence_count": 4,
            "current_member_annual_count": 4, "remaining_archive_count": 0,
            "extraction_count": 3, "failure_count": 1, "pending_parse_count": 0,
            "qualification_count": 3,
        })
        self._write("status/oge.json", {
            "schema_version": "oge-review-run/v1", "direct_pdf_count": 2,
            "archived_report_count": 2, "extraction_count": 2,
            "pending_direct_count": 0, "extraction_failure_count": 0,
            "request_required_count": 99, "qualified_transaction_count": 1,
            "qualified_holding_count": 0,
        })
        self._write("status/senate_efd.json", {
            "schema_version": "senate-review-run/v1", "catalog_record_count": 3,
            "report_entrypoint_count": 3, "report_entrypoint_pending_count": 0,
            "report_entrypoint_failure_count": 0, "report_evidence_count": 2,
            "report_extraction_failure_count": 1, "paper_report_pending_count": 0,
        })
        self.sources = {}
        for index, source_id in enumerate(("house_clerk", "oge", "senate_efd"), 1):
            source = {"meta": {"is_demo": False}, "people": [{"id": f"p{index}"}],
                      "transactions": [{"id": f"t{index}"}],
                      "reported_holdings": ([{"id": "h1"}] if source_id == "house_clerk" else []),
                      "security_market_data": [], "source_health": [{"source_id": source_id}]}
            self.sources[source_id] = source
            self._write(f"candidates/sources/{source_id}-current.json", source)
        self.candidate = {
            "meta": {"is_demo": False, "data_cutoff_at": "2026-09-20T23:59:59Z"},
            "people": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
            "transactions": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],
            "reported_holdings": [{"id": "h1"}], "security_market_data": [],
            "source_health": [{"source_id": "house_clerk"}, {"source_id": "oge"},
                              {"source_id": "senate_efd"}],
        }
        self.candidate_path = self.root / "candidates" / "disclosure-current.json"
        self._write("candidates/disclosure-current.json", self.candidate)
        self._write_cutoff()

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, relative: str, value: dict):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode(value))

    def _write_cutoff(self):
        self._write("status/disclosure_cutoff.json", {
            "schema_version": "disclosure-cutoff-audit/v1",
            "candidate_sha256": digest(encode(self.candidate)),
            "sources": {
                source_id: {
                    "candidate_sha256": digest(encode(source)),
                    "retained_counts": {
                        "transactions": len(source["transactions"]),
                        "reported_holdings": len(source["reported_holdings"]),
                    },
                }
                for source_id, source in self.sources.items()
            },
        })

    def test_accepts_accounted_scope_and_excludes_form_201(self):
        result = validate_first_launch(self.root, self.candidate_path)
        self.assertEqual(result["transaction_count"], 3)
        self.assertEqual(result["reported_holding_count"], 1)
        self.assertEqual(result["oge_form_201_excluded_count"], 99)

    def test_rejects_unarchived_house_holdings(self):
        value = json.loads((self.root / "status/house_holdings.json").read_text())
        value["remaining_archive_count"] = 1
        self._write("status/house_holdings.json", value)
        with self.assertRaisesRegex(ReleaseReadinessError, "pending first-launch work"):
            validate_first_launch(self.root, self.candidate_path)

    def test_rejects_unaccounted_senate_paper_report(self):
        value = json.loads((self.root / "status/senate_efd.json").read_text())
        value["report_evidence_count"] = 1
        self._write("status/senate_efd.json", value)
        with self.assertRaisesRegex(ReleaseReadinessError, "not fully accounted"):
            validate_first_launch(self.root, self.candidate_path)

    def test_rejects_stale_unified_candidate(self):
        self.candidate["transactions"].append({"id": "tampered"})
        self._write("candidates/disclosure-current.json", self.candidate)
        with self.assertRaisesRegex(ReleaseReadinessError, "does not match its cutoff audit"):
            validate_first_launch(self.root, self.candidate_path)

    def test_rejects_oge_candidate_that_omits_reported_holdings(self):
        status = json.loads((self.root / "status/oge.json").read_text())
        status["qualified_holding_count"] = 1
        self._write("status/oge.json", status)
        with self.assertRaisesRegex(ReleaseReadinessError,
                                    "holding count does not match its source candidate"):
            validate_first_launch(self.root, self.candidate_path)


if __name__ == "__main__":
    unittest.main()
