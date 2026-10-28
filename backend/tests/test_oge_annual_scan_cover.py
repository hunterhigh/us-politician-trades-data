"""Scanned cover observations cannot become annual qualifications."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from unison_snapshot.ocr_geometry import OcrPage
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual_scan_cover import observe_scan_cover

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unison_snapshot.commands.observe_oge_annual_scan_covers import observe_failed_covers


class OgeAnnualScanCoverTests(unittest.TestCase):
    def test_cover_ocr_observes_form_year_without_qualifying(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "report.pdf"
            data = b"%PDF-1.7\nscan\n%%EOF"
            pdf.write_bytes(data)
            sha = hashlib.sha256(data).hexdigest()
            words = []
            for line_number, content in enumerate((
                "OGE Form 278e", "Report Type: Annual",
                "Year (Annual Report only): 2022"), 1):
                words.append({"text": content, "x0": 10, "x1": 230,
                              "top": line_number * 20, "block_num": 1,
                              "par_num": 1, "line_num": line_number,
                              "ocr_confidence": 91.0})
            page = OcrPage(width=612, height=792, words=words)
            document = MagicMock()
            document.pages = [MagicMock()]
            document.pages[0].extract_text.return_value = "Annual"
            opened = MagicMock()
            opened.__enter__.return_value = document
            pdfplumber = MagicMock()
            pdfplumber.open.return_value = opened
            with patch.dict(sys.modules, {"pdfplumber": pdfplumber}), patch(
                    "unison_snapshot.oge_annual_scan_cover.ocr_pdf_pages",
                    return_value=([page], "tesseract test")) as ocr:
                result = observe_scan_cover(pdf, source_sha256=sha,
                                            document_id="a" * 32,
                                            catalog_filer="Harris, Kamala D",
                                            catalog_year=2023)
            ocr.assert_called_once()
            self.assertEqual(result["annual_year_text_candidates"], [2022])
            self.assertTrue(result["annual_type_text_observed"])
            self.assertTrue(result["form_278e_text_observed"])
            self.assertFalse(result["receipt_date_verified"])
            self.assertFalse(result["production_qualification_authorized"])
            self.assertEqual(result["source_sha256"], sha)

    def test_hash_mismatch_rejects_before_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "report.pdf"
            pdf.write_bytes(b"%PDF-1.7\nscan\n%%EOF")
            with patch("unison_snapshot.oge_annual_scan_cover.ocr_pdf_pages") as ocr:
                with self.assertRaises(OgeCatalogError):
                    observe_scan_cover(pdf, source_sha256="a" * 64,
                                       document_id="b" * 32,
                                       catalog_filer="Example", catalog_year=2023)
            ocr.assert_not_called()

    def test_batch_isolates_one_cover_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = Path(folder) / "evidence", Path(folder) / "review"
            failures = []
            for char in ("a", "b"):
                document_id = char * 32
                data = b"%PDF-1.7\nscan\n%%EOF"
                sha = hashlib.sha256(data).hexdigest()
                directory = evidence / "oge/annual/reports" / document_id
                directory.mkdir(parents=True)
                (directory / f"{sha}.pdf").write_bytes(data)
                (directory / f"{sha}.json").write_text(json.dumps({
                    "schema_version": "oge-278e-annual-archive/v1",
                    "document_id": document_id, "sha256": sha,
                    "archive_path": f"oge/annual/reports/{document_id}/{sha}.pdf",
                    "filer_name": "Example", "report_year": 2023,
                }), encoding="utf-8")
                failures.append({"document_id": document_id, "source_sha256": sha})
            target = review / "oge/annual/extraction-status.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"failures": failures}), encoding="utf-8")
            with patch("unison_snapshot.commands.observe_oge_annual_scan_covers.observe_scan_cover",
                       side_effect=[ValueError("OCR unavailable"),
                                    {"schema_version": "oge-278e-scan-cover-observation/v1",
                                     "source_sha256": sha,
                                     "production_qualification_authorized": False}]):
                status = observe_failed_covers(evidence, review)
            self.assertEqual(status["strict_parser_failure_count"], 2)
            self.assertEqual(status["cover_observation_count"], 1)
            self.assertEqual(status["cover_observation_failure_count"], 1)
            self.assertFalse(status["production_qualification_authorized"])
            self.assertTrue((review / "oge/annual/cover-observation-status.json").is_file())


if __name__ == "__main__":
    unittest.main()
