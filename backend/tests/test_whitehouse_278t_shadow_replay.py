from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.whitehouse_278t_shadow_replay import replay_278t_shadow


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class WhiteHouse278TShadowReplayTests(unittest.TestCase):
    def test_fixed_full_row_census_and_digest(self) -> None:
        summary = json.loads((FIXTURES / "whitehouse_278t_shadow_summary.json").read_text(
            encoding="utf-8"))
        rows_path = FIXTURES / "whitehouse_278t_shadow_rows.jsonl"
        self.assertEqual(hashlib.sha256(rows_path.read_bytes()).hexdigest(),
                         summary["rows_jsonl_sha256"])
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual((summary["document_count"], summary["source_row_count"]), (18, 8131))
        self.assertEqual(summary["dispositions"], {
            "candidate_existing": 1391,
            "parsed_absent_from_candidate": 4,
            "review_quarantined": 6736,
        })
        self.assertEqual(len(rows), 8131)
        self.assertEqual(sum(doc["source_row_count"] for doc in summary["documents"]), 8131)
        self.assertEqual(len({(row["document_id"], row["extraction_id"]) for row in rows}),
                         8131)
        self.assertFalse(summary["production_mutation_authorized"])
        self.assertEqual(summary["oge_same_document_identity"], "unknown")
        outliers = [row for row in rows
                    if row["disposition"] == "parsed_absent_from_candidate"]
        self.assertEqual({row["document_id"] for row in outliers},
                         {"wh-url:60d8d791c71b1049b30637fb"})

    def test_source_and_candidate_drift_fail_closed(self) -> None:
        pdf = b"%PDF-1.4\n%%EOF\n"
        pdf_sha = hashlib.sha256(pdf).hexdigest()
        source = {"extraction_id": "row1", "asset_name": "A", "owner": "Self",
                  "transaction_type": "purchase", "transaction_date": "2025-01-01",
                  "amount_low": 1001, "amount_high": 15000, "page_number": 2,
                  "row_number": 1}
        extraction = {"document_id": "wh-url:one", "source_sha256": pdf_sha,
                      "source_url": "https://www.whitehouse.gov/a.pdf",
                      "parser_version": "whitehouse-278t-hybrid-geometry/v2",
                      "source_row_count": 2,
                      "transactions": [source],
                      "quarantined": [{"extraction_id": "row2", "page_number": 2,
                                       "row_number": 2, "reasons": ["invalid_date"]}]}
        extraction_bytes = json.dumps(extraction).encode()
        blob = hashlib.sha1(b"blob " + str(len(extraction_bytes)).encode() +
                            b"\0" + extraction_bytes).hexdigest()
        candidate = {"transactions": [{"id": "row1", "filing_id": "wh-url:one",
                                       "source_url": extraction["source_url"],
                                       **{key: source[key] for key in
                                          ("asset_name", "owner", "transaction_type",
                                           "transaction_date", "amount_low", "amount_high")}}]}
        candidate_bytes = json.dumps(candidate).encode()
        audit = {"schema_version": "whitehouse-wh-url-document-audit/v1",
                 "candidate_commit": "a" * 40, "evidence_commit": "b" * 40,
                 "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                 "documents": [{"document_id": "wh-url:one",
                                "document_type_from_public_label": "278t",
                                "archive_pdf_sha256_from_path": pdf_sha,
                                "source_url": extraction["source_url"],
                                "candidate_row_count": 1}]}
        bundle = {"wh-url:one": (pdf, extraction_bytes, blob)}
        result = replay_278t_shadow(candidate_bytes, audit, bundle)
        self.assertEqual(result["dispositions"],
                         {"candidate_existing": 1, "review_quarantined": 1})
        with self.assertRaises(ValueError):
            replay_278t_shadow(candidate_bytes, audit,
                               {"wh-url:one": (pdf + b"x", extraction_bytes, blob)})
        with self.assertRaises(ValueError):
            replay_278t_shadow(candidate_bytes, audit,
                               {"wh-url:one": (pdf, extraction_bytes, "0" * 40)})

    def test_small_native_pdf_replay_records_all_equal_canonical_rows(self) -> None:
        fixture = json.loads((FIXTURES / "whitehouse_278t_v2_parser_replay.json").read_text(
            encoding="utf-8"))
        self.assertEqual(len(fixture["documents"]), 11)
        self.assertTrue(all(row["canonical_transaction_rows_equal"] and
                            row["quarantine_id_reasons_equal"] and
                            row["review_parser_version"] == row["current_parser_version"]
                            for row in fixture["documents"]))

    def test_bounded_ocr_audit_records_drift_and_failed_closed_replay(self) -> None:
        audit = json.loads((FIXTURES / "whitehouse_278t_ocr_bounded_audit.json").read_text(
            encoding="utf-8"))
        documents = {row["document_id"]: row for row in audit["documents"]}
        manifest = json.loads((FIXTURES / "whitehouse_278t_shadow_sources.json").read_text(
            encoding="utf-8"))
        source_blobs = {row["document_id"]: row["review_extraction_git_blob"]
                        for row in manifest["documents"]}
        document_audit = json.loads((FIXTURES / "whitehouse_wh_url_8150_document_audit.json")
                                    .read_text(encoding="utf-8"))
        pdf_hashes = {row["document_id"]: row["archive_pdf_sha256_from_path"]
                      for row in document_audit["documents"]}
        self.assertEqual(len(documents), 7)
        self.assertFalse(audit["production_mutation_authorized"])
        self.assertEqual(audit["review_commit"], manifest["candidate_commit"])
        for doc_id, row in documents.items():
            self.assertEqual(row["review_git_blob"], source_blobs[doc_id])
            if "pdf_sha256" in row:
                self.assertEqual(row["pdf_sha256"], pdf_hashes[doc_id])
        self.assertEqual({row["status"] for row in documents.values()},
                         {"completed_with_ocr_drift", "reparse_failed_closed", "not_reparsed"})
        self.assertEqual(sum(row["status"] == "not_reparsed" for row in documents.values()), 4)
        for stem, review_tx, current_tx, review_q, current_q in (
                ("1a6bbff2a684fedd76ac9f59", 465, 475, 42, 32),
                ("82a263659dcbd44a6522ecbc", 5, 3, 169, 171)):
            row = documents["wh-url:" + stem]
            self.assertEqual(row["source_row_count_review"],
                             row["source_row_count_reparse"])
            self.assertEqual(row["collections"]["transactions"]["review_count"], review_tx)
            self.assertEqual(row["collections"]["transactions"]["reparse_count"], current_tx)
            self.assertEqual(row["collections"]["quarantined"]["review_count"], review_q)
            self.assertEqual(row["collections"]["quarantined"]["reparse_count"], current_q)
            for collection in row["collections"].values():
                self.assertEqual(collection["review_count"],
                                 collection["shared_id_count"] + len(collection["review_only_ids"]))
                self.assertEqual(collection["reparse_count"],
                                 collection["shared_id_count"] + len(collection["reparse_only_ids"]))
                self.assertFalse(set(collection["review_only_ids"]) &
                                 set(collection["reparse_only_ids"]))
        failed = documents["wh-url:62340683e32b0263e8f0eb20"]
        self.assertEqual(failed["error_type"], "OgeCatalogError")
        self.assertIn("row conservation failed", failed["error_message"])

    def test_miller_absent_rows_have_fixed_qualification_reasons(self) -> None:
        qualification = json.loads((FIXTURES / "whitehouse_278t_miller_qualification_audit.json")
                                   .read_text(encoding="utf-8"))
        shadow_rows = [json.loads(line) for line in
                       (FIXTURES / "whitehouse_278t_shadow_rows.jsonl")
                       .read_text(encoding="utf-8").splitlines()]
        outliers = {(row["document_id"], row["extraction_id"], row["row_number"])
                    for row in shadow_rows
                    if row["disposition"] == "parsed_absent_from_candidate"}
        qualified = {(qualification["document_id"], row["extraction_id"], row["row_number"])
                     for row in qualification["rows"]}
        self.assertEqual(outliers, qualified)
        self.assertEqual((qualification["promoted_count"],
                          qualification["quarantined_count"]), (23, 4))
        self.assertEqual({row["row_number"] for row in qualification["rows"]},
                         {16, 19, 21, 24})
        self.assertTrue(all(row["status"] == "quarantined" and row["reasons"] ==
                            ["possible_whitehouse_same_report_transaction_duplicate"]
                            for row in qualification["rows"]))
        self.assertEqual(qualification["qualification_git_blob"],
                         "ee2529182a9aba9eb2fa310ffa448ab12374d216")
        self.assertFalse(qualification["production_mutation_authorized"])


if __name__ == "__main__":
    unittest.main()
