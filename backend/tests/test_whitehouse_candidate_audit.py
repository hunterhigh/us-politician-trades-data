from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.whitehouse_candidate_audit import audit_wh_url_documents


FIXTURE = Path(__file__).resolve().parent / "fixtures/whitehouse_wh_url_8150_document_audit.json"


class WhiteHouseCandidateAuditTests(unittest.TestCase):
    def test_fixed_document_census_is_conservative(self) -> None:
        result = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual((result["wh_url_row_count"], result["document_count"]), (8150, 19))
        self.assertEqual((result["annual_row_count"], result["periodic_row_count"]),
                         (6759, 1391))
        self.assertEqual(sum(row["candidate_row_count"] for row in result["documents"]), 8150)
        self.assertEqual(sum(row["document_type_from_public_label"] == "278t"
                             for row in result["documents"]), 18)
        self.assertEqual(sum(row["archive_layout"] == "nine_ordered_parts"
                             for row in result["documents"]), 1)
        self.assertEqual(result["exact_tuple_matches_oge_candidate"], 0)
        self.assertEqual(result["annual_rows_with_coarse_periodic_candidate"], 361)
        self.assertEqual(result["oge_document_identity_status"],
                         "unverified_for_all_candidate_documents")
        self.assertFalse(result["promotion_or_deletion_authorized"])

    def test_document_source_binding_and_exact_tuple_scope(self) -> None:
        row = {"filing_id": "wh-url:abc", "source_url": "https://www.whitehouse.gov/a.pdf",
               "person_id": "oge:test", "source_id": "oge", "transaction_date": "2026-01-01",
               "transaction_type": "purchase", "amount_low": 1001, "amount_high": 15000,
               "asset_name": "ACME", "filed_at": "2026-01-02"}
        old = dict(row, filing_id="oge-document", source_url="https://oge.gov/b.pdf")
        candidate = json.dumps({"transactions": [row, old]}).encode()
        index = {"source_id": "whitehouse_public_disclosures", "page_sha256": "1" * 64,
                 "reports": [{"source_document_id": "wh-url:abc",
                              "document_url": row["source_url"],
                              "document_type_from_label": "278t"}]}
        prefix = "whitehouse/disclosures/reports/abc/" + "2" * 64
        evidence = [prefix + ".json", prefix + ".pdf"]
        review = ["whitehouse/extractions/abc/" + "2" * 64 + "/parser.json"]
        kwargs = {"candidate_commit": "a" * 40, "evidence_commit": "b" * 40}
        result = audit_wh_url_documents(candidate, json.dumps(index).encode(),
                                        evidence, review, **kwargs)
        self.assertEqual(result["exact_tuple_matches_oge_candidate"], 1)
        self.assertEqual(result["documents"][0]["archive_pdf_sha256_from_path"], "2" * 64)
        self.assertFalse(result["promotion_or_deletion_authorized"])
        index["reports"][0]["document_url"] = "https://www.whitehouse.gov/other.pdf"
        with self.assertRaises(ValueError):
            audit_wh_url_documents(candidate, json.dumps(index).encode(),
                                   evidence, review, **kwargs)


if __name__ == "__main__":
    unittest.main()
