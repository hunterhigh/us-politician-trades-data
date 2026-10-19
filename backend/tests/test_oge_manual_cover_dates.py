import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_oge_manual_cover_dates import build, SCHEMA


class OgeManualCoverDateTests(unittest.TestCase):
    def test_source_bound_date_is_written_without_promoting_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence, review = root / "evidence", root / "review"
            document_id = "a" * 32
            pdf = b"%PDF-1.7\nsource\n%%EOF\n"
            sha = hashlib.sha256(pdf).hexdigest()
            url = "https://extapps2.oge.gov/example.pdf"
            source = evidence / "oge" / "reports" / document_id
            source.mkdir(parents=True)
            (source / f"{sha}.pdf").write_bytes(pdf)
            (source / f"{sha}.json").write_text(json.dumps({
                "schema_version": "oge-278t-archive/v1", "source_id": "oge",
                "document_id": document_id, "document_url": url,
                "archive_path": f"oge/reports/{document_id}/{sha}.pdf",
                "sha256": sha, "byte_length": len(pdf),
            }), encoding="utf-8")
            extraction = review / "oge" / "extractions" / document_id / sha
            extraction.mkdir(parents=True)
            (extraction / "oge-278t-pdf-v2.json").write_text(json.dumps({
                "document_id": document_id, "source_sha256": sha,
                "source_url": url, "filed_at": None,
                "document_reasons": ["filer_signature_date_not_unique"],
                "transactions": [{"extraction_id": "kept"}],
            }), encoding="utf-8")
            manual = root / "manual.json"
            row = {"document_id": document_id, "source_sha256": sha,
                   "page_number": 1, "field_role": "filer_signature_date",
                   "raw_reading": "9/17/26", "observed_date": "2026-09-17"}
            manual.write_text(json.dumps({"schema_version": SCHEMA,
                                          "status": "observation_only",
                                          "observations": [row]}), encoding="utf-8")
            before = (extraction / "oge-278t-pdf-v2.json").read_bytes()
            result = build(observations_path=manual, evidence_root=evidence,
                           review_root=review)
            self.assertEqual(result["qualification_effect"], "none")
            output = review / "oge" / "manual_cover_dates" / document_id / f"{sha}.json"
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["observed_date"], "2026-09-17")
            self.assertEqual(saved["filed_at"], "2026-09-17")
            self.assertEqual(saved["recognition_method"],
                             "human_visual_reading_of_pdf_cover")
            self.assertEqual((extraction / "oge-278t-pdf-v2.json").read_bytes(), before)
            self.assertEqual(build(observations_path=manual, evidence_root=evidence,
                                   review_root=review)["observations"], 1)
            row["observed_date"] = "2026-09-18"
            manual.write_text(json.dumps({"schema_version": SCHEMA,
                                          "status": "observation_only",
                                          "observations": [row]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "collision"):
                build(observations_path=manual, evidence_root=evidence,
                      review_root=review)
            output.unlink()
            row.update(field_role="amendment_signature_date",
                       raw_reading="1/14/26", observed_date="2026-01-14")
            manual.write_text(json.dumps({"schema_version": SCHEMA,
                                          "status": "observation_only",
                                          "observations": [row]}), encoding="utf-8")
            build(observations_path=manual, evidence_root=evidence,
                  review_root=review)
            self.assertIsNone(json.loads(output.read_text(encoding="utf-8"))["filed_at"])


if __name__ == "__main__":
    unittest.main()
