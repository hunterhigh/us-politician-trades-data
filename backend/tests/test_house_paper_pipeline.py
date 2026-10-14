import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from unison_snapshot.house_paper_pipeline import bridge_paper_batch, bridge_paper_report
from unison_snapshot.house_ptr_paper_temporal import PARSER_VERSION


class HousePaperPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.evidence = root / "evidence"
        self.review = root / "review"
        self.pdf = b"archived paper"
        self.sha = hashlib.sha256(self.pdf).hexdigest()
        self.relative = Path("2025/9116141") / f"{self.sha}.json"
        self.metadata_path = self.evidence / "house_clerk/documents" / self.relative
        self.metadata = {
            "source_id": "house_clerk", "filing_type": "P", "filing_year": 2025,
            "document_id": "9116141", "sha256": self.sha,
            "filer_name": "Example, Person", "state_district": "CA01",
            "filed_date": "2025-01-02",
            "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/9116141.pdf",
            "archive_path": "house_clerk/archive/9116141.pdf",
        }
        self._write(self.metadata_path, self.metadata)
        pdf_path = self.evidence / self.metadata["archive_path"]
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(self.pdf)
        self._write(self.review / "house_clerk/extractions" / self.relative,
                    {"extraction_method": "tesseract_legacy_checkbox"})
        self._write(self.review / "house_clerk/identities" / self.relative,
                    {"document_id": "9116141", "person_id": "house:example"})
        self.legacy = {"document_id": "9116141", "source_sha256": self.sha,
                       "transactions": [], "quarantined": [{"old": True}]}
        self.qualification_path = self.review / "house_clerk/qualifications" / self.relative
        self._write(self.qualification_path, self.legacy)
        self.observation = {"source_sha256": self.sha, "pages": []}
        self.header = {"source_sha256": self.sha, "pages": []}
        self.paper = {"parser_version": PARSER_VERSION, "transactions": [],
                      "source_sha256": self.sha, "document_id": "9116141"}

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_qualified_legacy_rows_are_preserved_without_ocr(self):
        self.legacy["transactions"] = [{"id": "house:old"}]
        self._write(self.qualification_path, self.legacy)
        with patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf") as observe:
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "legacy_qualified_preserved")
        observe.assert_not_called()
        self.assertEqual(json.loads(self.qualification_path.read_text()), self.legacy)

    def test_new_observation_replaces_only_empty_legacy_qualification(self):
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=self.paper)):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "observed_and_qualified")
        self.assertEqual(json.loads(self.qualification_path.read_text()), self.paper)
        self.assertTrue((self.review / "house_clerk/paper_observations" / self.relative).is_file())
        self.assertTrue((self.review / "house_clerk/paper_amount_headers" / self.relative).is_file())
        self.assertEqual(json.loads((self.review / "house_clerk/extractions" /
                                     self.relative).read_text())["extraction_method"],
                         "tesseract_legacy_checkbox")

    def test_failed_observation_keeps_legacy_and_records_failure(self):
        batch = Path(self.temporary.name) / "batch.txt"
        batch.write_text("", encoding="utf-8")
        with patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                   side_effect=ValueError("unsupported grid")):
            result = bridge_paper_batch(evidence_root=self.evidence,
                                        review_root=self.review, parse_batch=batch,
                                        tesseract="tesseract", document_id="9116141")
        self.assertEqual(result, {"paper_observation_failed": 1})
        self.assertEqual(json.loads(self.qualification_path.read_text()), self.legacy)
        failure = self.review / "house_clerk/paper_failures" / self.relative
        self.assertIn("unsupported grid", failure.read_text())


if __name__ == "__main__":
    unittest.main()
