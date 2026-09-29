import hashlib
import unittest

from unison_snapshot.house_shadow import HouseShadowError, build_document
from unison_snapshot.pipeline_ledger import validate_candidate_row


class HouseShadowTests(unittest.TestCase):
    def setUp(self):
        self.pdf = b"%PDF-1.4\nfixed sample bytes"
        self.sha = hashlib.sha256(self.pdf).hexdigest()
        self.source_url = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/12345678.pdf"
        self.archive = f"house_clerk/documents/2026/12345678/{self.sha}.pdf"
        self.metadata = {"source_id": "house_clerk", "source_url": self.source_url,
                         "document_id": "12345678", "sha256": self.sha,
                         "archive_path": self.archive}
        self.row = {"extraction_id": "house-ptr:row-one", "asset_name": "Example Co",
                    "transaction_date": "2026-01-02", "transaction_type": "sale",
                    "transaction_type_raw": "S", "amount_low": 1001,
                    "amount_high": 15000, "amount_raw": "$1,001 - $15,000",
                    "evidence": {"page": 2, "bbox_points": [10, 20, 30, 40]}}
        self.extraction = {"schema_version": "house-ptr-extraction/v1",
                           "parser_version": "house-ptr-2026-04", "source_sha256": self.sha,
                           "source": {"source_id": "house_clerk", "document_id": "12345678",
                                      "source_url": self.source_url,
                                      "archive_path": self.archive},
                           "extraction_method": "native_pdf_text", "transactions": [self.row]}
        self.qualification = {"schema_version": "house-ptr-qualification/v1",
                              "source_sha256": self.sha, "document_id": "12345678",
                              "parser_version": "house-ptr-2026-04",
                              "identity": {"person_id": "house:A123456"},
                              "transactions": [{"id": "house-ptr:row-one", "filing_id": "12345678",
                                                "source_id": "house_clerk", "person_id": "house:A123456",
                                                "verification_status": "official_matched",
                                                "source_url": self.source_url,
                                                **{key: self.row[key] for key in
                                                   ("asset_name", "transaction_date", "transaction_type",
                                                    "amount_low", "amount_high")}}],
                              "quarantined": [],
                              "qualification": {"qualified_count": 1, "quarantined_count": 0,
                                                "production_eligible": True}}

    def _build(self):
        return build_document(self.metadata, self.pdf, self.extraction,
                              self.qualification, None, run_id="fixed-run")

    def test_qualified_row_has_source_provenance_and_observations(self):
        document, rows = self._build()
        self.assertEqual(document["disposition"], "parsed")
        self.assertEqual(len(rows), 1)
        self.assertEqual(validate_candidate_row(rows[0])["disposition"], "qualified")
        self.assertEqual(rows[0]["source"]["source_sha256"], self.sha)
        self.assertEqual(rows[0]["evidence_locations"][0]["page"], 2)
        self.assertEqual({obs["field"] for obs in rows[0]["observations"]},
                         {"asset_name", "transaction_date", "transaction_type",
                          "amount_low", "amount_high"})

    def test_quarantine_preserves_ledger_row(self):
        self.qualification["transactions"] = []
        self.qualification["quarantined"] = [{"extraction_id": "house-ptr:row-one",
                                               "source_sha256": self.sha,
                                               "reasons": ["identity_not_deterministic"]}]
        self.qualification["qualification"] = {"qualified_count": 0, "quarantined_count": 1}
        document, rows = self._build()
        self.assertEqual(document["disposition"], "parsed")
        self.assertEqual(rows[0]["disposition"], "quarantined")
        self.assertEqual(rows[0]["reasons"], ["identity_not_deterministic"])

    def test_missing_qualification_row_fails_closed(self):
        self.qualification["transactions"] = []
        self.qualification["qualification"]["qualified_count"] = 0
        with self.assertRaisesRegex(HouseShadowError, "conserve"):
            self._build()

    def test_pdf_hash_mismatch_fails_closed(self):
        with self.assertRaisesRegex(HouseShadowError, "PDF hash"):
            build_document(self.metadata, self.pdf + b"x", self.extraction,
                           self.qualification, None, run_id="fixed-run")

    def test_parse_failure_has_unknown_row_count(self):
        failure = {"schema_version": "house-ptr-parse-failure/v1",
                   "document_id": "12345678", "source_sha256": self.sha,
                   "parser_version": "house-parser-suite-2026-08",
                   "error": "No recognized rows"}
        document, rows = build_document(self.metadata, self.pdf, None, None, failure,
                                        run_id="fixed-run")
        self.assertEqual(document["disposition"], "failed")
        self.assertEqual(document["row_count_status"], "unknown_parse_failed")
        self.assertEqual(rows, [])

    def test_empty_extraction_needs_explicit_zero_declaration(self):
        self.extraction["transactions"] = []
        self.qualification["transactions"] = []
        self.qualification["qualification"]["qualified_count"] = 0
        with self.assertRaisesRegex(HouseShadowError, "explicit zero"):
            self._build()
        self.extraction["document_disposition"] = {"status": "explicit_no_transactions"}
        self.qualification["qualification"].update(
            status="qualified_no_transactions", production_eligible=True)
        document, rows = self._build()
        self.assertEqual(document["disposition"], "no_rows")
        self.assertEqual(rows, [])
        self.qualification["qualification"]["production_eligible"] = False
        document, rows = self._build()
        self.assertEqual(document["disposition"], "failed")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
