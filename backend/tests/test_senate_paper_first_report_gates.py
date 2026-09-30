"""Source-bound gate checks for the first Senate paper PTR sample."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO / "backend" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
HAS_PIL = importlib.util.find_spec("PIL") is not None
if HAS_PIL:
    SPEC = importlib.util.spec_from_file_location(
        "audit_senate_paper_first_report_gates",
        SCRIPT_DIR / "audit_senate_paper_first_report_gates.py")
    assert SPEC and SPEC.loader
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)
FIELDS = REPO / "docs/senate-paper-first-report-fields.json"
GATES = REPO / "docs/senate-paper-first-report-frontend-gates.json"
FRONTEND = Path(r"C:\Users\admin\Downloads\politician-disclosures (3).html")


@unittest.skipUnless(HAS_PIL, "optional Senate image audit dependency unavailable")
class FirstReportGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FRONTEND.is_file():
            raise unittest.SkipTest("latest user-provided HTML baseline is not on this host")

    def test_every_row_has_explicit_missing_fields_and_no_candidate(self):
        self.assertEqual(hashlib.sha256(FIELDS.read_bytes()).hexdigest(),
                         MODULE.FIELDS_SHA256)
        self.assertEqual(hashlib.sha256(FRONTEND.read_bytes()).hexdigest(),
                         MODULE.FRONTEND_SHA256)
        artifact = json.loads(GATES.read_text(encoding="utf-8"))
        self.assertEqual(artifact["row_count"], 36)
        self.assertEqual(artifact["report_context"]["identity"]["person_id"],
                         "senate:B001277")
        self.assertEqual(len(artifact["report_context"]["same_person_catalog_reports"]), 9)
        self.assertEqual(artifact["report_context"]["same_person_electronic_report_count"], 0)
        self.assertEqual(artifact["report_context"]["cross_report_equivalence"], "unresolved")
        self.assertEqual(artifact["frontend_context"]["current_document_transaction_count"], 0)
        self.assertEqual(artifact["schema_version"], "senate-paper-first-report-frontend-gates/v2")
        self.assertEqual(artifact["report_context"]["owner_legend_reference"]["codes"]["(S)"],
                         "Spouse")
        self.assertEqual(artifact["report_context"]["filing_timestamp_disposition"],
                         "date_only_no_time_of_day")
        expected_missing = {"id", "filed_at", "verification_status"}
        for row in artifact["rows"]:
            self.assertEqual(row["owner_code_observed"], "(S)")
            self.assertEqual(row["frontend_projection_preview"]["owner"], "Spouse")
            self.assertEqual(row["frontend_projection_preview"]["asset_name"],
                             row["asset_cell_verbatim"][4:])
            self.assertEqual(row["filed_date_observed"], "2026-02-12")
            self.assertEqual(row["filed_at_precision"], "date")
            self.assertEqual(set(row["missing_frontend_fields"]), expected_missing)
            self.assertEqual({key for key, value in row["frontend_projection_preview"].items()
                              if value is None}, expected_missing | {"instrument_type"})
            self.assertEqual(row["disposition"], "quarantined_no_candidate")
            self.assertIsNone(row["candidate_transaction_id"])

    def test_replay_matches_committed_gate_artifact(self):
        regenerated = MODULE.audit(REPO, FIELDS, FRONTEND)
        self.assertEqual(regenerated, json.loads(GATES.read_text(encoding="utf-8")))

    def test_changed_latest_html_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "frontend.html"
            changed.write_bytes(FRONTEND.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "HTML baseline changed"):
                MODULE.audit(REPO, FIELDS, changed)

    def test_changed_field_observations_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "fields.json"
            changed.write_bytes(FIELDS.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "field observations changed"):
                MODULE.audit(REPO, changed, FRONTEND)


if __name__ == "__main__":
    unittest.main()
