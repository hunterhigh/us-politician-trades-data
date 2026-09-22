"""Review publication cannot promote OGE annual rows into the frontend candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from oge_annual_review import extract_archived_reports
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual import PARSER_VERSION, SCHEMA


class OgeAnnualReviewTests(unittest.TestCase):
    def test_review_only_and_content_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            evidence, review = base / "evidence", base / "review"
            document_id = "a" * 32
            pdf_content = b"%PDF-1.7\nfixture\n%%EOF"
            sha = hashlib.sha256(pdf_content).hexdigest()
            target = evidence / "oge" / "annual" / "reports" / document_id
            target.mkdir(parents=True)
            (target / f"{sha}.pdf").write_bytes(pdf_content)
            (target / f"{sha}.json").write_text(json.dumps({
                "schema_version": "oge-278e-annual-archive/v1",
                "document_id": document_id, "sha256": sha,
                "archive_path": f"oge/annual/reports/{document_id}/{sha}.pdf",
                "document_url": "https://extapps2.oge.gov/example.pdf",
                "filer_name": "Example, Ada",
            }), encoding="utf-8")
            extraction = {
                "schema_version": SCHEMA, "parser_version": PARSER_VERSION,
                "page_count": 4, "transactions": [{"row_number": "1"}],
                "holdings": [{"row_number": "1"}], "quarantined": [],
                "part7_numbering": {"printed_row_count": 1,
                                    "numbering_complete": True,
                                    "row_reconciliation_complete": True},
                "production_qualification": "pending_filing_date_cross_report_dedup_and_row_quarantine",
            }
            with patch("oge_annual_review.extract_annual_pdf", return_value=extraction) as parser:
                status = extract_archived_reports(evidence, review)
            parser.assert_called_once()
            self.assertEqual(status["promoted_to_candidate"], 0)
            self.assertEqual(status["reports"][0]["strict_extracted_transactions"], 1)
            self.assertTrue((review / status["reports"][0]["extraction_path"]).is_file())
            self.assertFalse((review / "candidates").exists())

    def test_metadata_path_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            target = base / "evidence/oge/annual/reports" / ("a" * 32)
            target.mkdir(parents=True)
            (target / ("b" * 64 + ".json")).write_text(json.dumps({
                "schema_version": "oge-278e-annual-archive/v1",
                "document_id": "a" * 32, "sha256": "c" * 64,
            }), encoding="utf-8")
            with self.assertRaises(OgeCatalogError):
                extract_archived_reports(base / "evidence", base / "review")


if __name__ == "__main__":
    unittest.main()
