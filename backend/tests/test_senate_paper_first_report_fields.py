"""Validate the fixed 36-row paper field evidence without creating candidates."""
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
HAS_IMAGE_DEPS = all(importlib.util.find_spec(name) is not None
                     for name in ("PIL", "numpy", "rapidocr_onnxruntime"))
if HAS_IMAGE_DEPS:
    SPEC = importlib.util.spec_from_file_location(
        "audit_senate_paper_first_report_fields",
        SCRIPT_DIR / "audit_senate_paper_first_report_fields.py")
    assert SPEC and SPEC.loader
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)
ROWS = REPO / "docs/senate-paper-first-report-rows.json"
REVIEW = REPO / "backend/tests/fixtures/senate_paper_first_report_visual_review.json"
FIELDS = REPO / "docs/senate-paper-first-report-fields.json"


@unittest.skipUnless(HAS_IMAGE_DEPS, "optional Senate OCR audit dependencies unavailable")
class FirstReportFieldTests(unittest.TestCase):
    def test_fixed_visual_review_and_field_observations_close_36_rows(self):
        self.assertEqual(hashlib.sha256(ROWS.read_bytes()).hexdigest(), MODULE.ROWS_SHA256)
        self.assertEqual(hashlib.sha256(REVIEW.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                         MODULE.VISUAL_REVIEW_SHA256)
        artifact = json.loads(FIELDS.read_text(encoding="utf-8"))
        rows = artifact["rows"]
        self.assertEqual(len(rows), 36)
        self.assertEqual(len({(r["page_number"], r["grid_slot"]) for r in rows}), 36)
        for row in rows:
            self.assertEqual(row["row_field_status"],
                             "four_fields_confirmed_candidate_quarantined")
            self.assertEqual(row["projection_status"], "quarantined_no_candidate")
            self.assertIsNone(row["candidate_transaction_id"])
            self.assertTrue(all(row[field]["status"] == "confirmed_fixed_image"
                                for field in ("asset", "date", "direction", "amount")))
            self.assertTrue(all(row["visual_review"]["field_matches"].values()))
            self.assertIsNotNone(row["date"]["normalized_date_if_legible"])

    def test_date_parser_keeps_nonmatching_ocr_ambiguous(self):
        self.assertEqual(MODULE._date_status({"observations": []}),
                         ("ambiguous_ocr", None))
        self.assertEqual(MODULE._date_status({"observations": [
            {"text": "1/99/26", "confidence": 0.99}]}),
            ("ambiguous_ocr", None))

    def test_changed_visual_review_is_rejected_before_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "review.json"
            changed.write_bytes(REVIEW.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "visual review changed"):
                MODULE.audit(REPO, ROWS, changed)


if __name__ == "__main__":
    unittest.main()
