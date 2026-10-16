"""Report identity and unknown/zero boundaries for the read-only OGE adapter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.disclosure_status_oge import build_oge_status


class OgeDisclosureStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = Path(self.tmp.name) / "evidence"
        self.review = Path(self.tmp.name) / "review"
        self.doc = "1" * 32
        self.url = f"https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{self.doc}/$FILE/a.pdf"
        self.whurl = "https://www.whitehouse.gov/wp-content/uploads/2026/07/a.pdf"
        self.whid = "wh-url:" + hashlib.sha256(self.whurl.encode()).hexdigest()[:24]
        self.sha = "a" * 64

    def write(self, root, path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def catalog(self, rows, when="2026-10-16T00:00:00Z", current=True):
        catalog = {"transactions": rows}
        raw = json.dumps(catalog).encode()
        sha = hashlib.sha256(raw).hexdigest()
        path = self.evidence / f"oge/catalog/normalized/{sha}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.write(self.evidence, f"oge/catalog/{sha}.json", {
            "schema_version": "oge-catalog-archive/v1", "sha256": sha, "retrieved_at": when})
        if current:
            self.write(self.review, "status/oge.json", {"catalog_sha256": sha})
        return sha

    def archive(self, whitehouse=False, sha=None):
        sha = sha or self.sha
        report_id, url = (self.whid, self.whurl) if whitehouse else (self.doc, self.url)
        folder = "whitehouse/disclosures/reports/" + report_id.split(":")[-1] if whitehouse else "oge/reports/" + report_id
        self.write(self.evidence, f"{folder}/{sha}.json", {
            "document_id": report_id, "document_url": url, "sha256": sha})
        extraction = ("whitehouse/extractions/" + report_id.split(":")[-1] if whitehouse else
                      "oge/extractions/" + report_id)
        self.write(self.review, f"{extraction}/{sha}/parser-v1.json", {})

    def index(self):
        self.write(self.review, "whitehouse/disclosures/index-current.json", {
            "page_sha256": "f" * 64, "page_archive": {"retrieved_at": "2026-10-16T01:00:00Z"},
            "reports": [{"source_document_id": self.whid, "document_url": self.whurl,
                         "document_type_from_label": "278e_unspecified"}]})

    def build(self, transactions=None, holdings=None):
        return build_oge_status(self.evidence, self.review, transactions or [], holdings or [])

    def test_direct_qualification_publication_and_request_occurrences_are_separate(self):
        rows = [{"access_method": "direct_pdf", "source_document_id": self.doc,
                 "document_url": self.url, "catalog_index": 1,
                 "catalog_added_date": "2026-10-08"}]
        rows += [{"access_method": "request_required", "catalog_index": n,
                  "document_url": "https://extapps2.oge.gov/201/Presiden.nsf/201%20Request?OpenForm&Filer=Same"}
                 for n in (2, 3)]
        digest = self.catalog(rows)
        self.archive()
        self.write(self.review, "oge/qualifications/current.json", {"reports": [{
            "document_id": self.doc, "promoted_count": 4, "quarantined_count": 2,
            "document_reasons": [], "quarantined": [{"reasons": ["amount_invalid"]}]}]})
        tx = {"id": "published-one", "source_id": "oge", "source_url": self.url,
              "filed_at": "2026-09-17T00:00:00Z"}
        direct, request, _ = self.build([tx])
        report = direct["reports"][0]
        self.assertEqual(report["qualification_state"], "partial")
        self.assertEqual(report["qualified_transaction_count"], 4)
        self.assertEqual(report["publication_transaction_ids"], ["published-one"])
        self.assertEqual(report["source_versions"], [self.sha])
        self.assertEqual(report["extraction_state"], "extracted")
        self.assertEqual(report["catalog_added_date"], "2026-10-08")
        self.assertEqual(report["filed_date"], "2026-09-17")
        self.assertEqual(len(request["reports"]), 2)
        self.assertEqual({row["record_key"] for row in request["reports"]}, {
            f"oge-request:{digest}:2", f"oge-request:{digest}:3"})
        self.assertTrue(all(row["report_id"] is None for row in request["reports"]))

    def test_history_and_parser_versions_do_not_duplicate_report(self):
        self.catalog([{"access_method": "direct_pdf", "source_document_id": self.doc,
                       "document_url": self.url}], "2026-09-01T00:00:00Z", False)
        self.catalog([], "2026-10-16T00:00:00Z")
        self.archive()
        self.archive(sha="b" * 64)
        self.write(self.review, f"oge/extractions/{self.doc}/{self.sha}/parser-v2.json", {})
        report = self.build()[0]["reports"][0]
        self.assertEqual(report["catalog_state"], "historical")
        self.assertEqual(report["source_versions"], ["a" * 64, "b" * 64])
        self.assertEqual(len(self.build()[0]["reports"]), 1)

    def test_whole_report_blocker_with_no_extracted_rows_is_not_zero_transactions(self):
        self.catalog([{"access_method": "direct_pdf", "source_document_id": self.doc,
                       "document_url": self.url}])
        self.archive()
        self.write(self.review, "oge/qualifications/current.json", {"reports": [{
            "document_id": self.doc, "promoted_count": 0, "quarantined_count": 0,
            "document_reasons": ["document_evidence_incomplete", "filed_at_invalid"]}]})
        record = self.build()[0]["reports"][0]
        self.assertEqual(record["qualification_state"], "quarantined")
        self.assertIsNone(record["filed_date"])

    def test_direct_audit_without_archive_retains_observation_not_current_qualification(self):
        self.catalog([{"access_method": "direct_pdf", "source_document_id": self.doc,
                       "document_url": self.url}])
        self.write(self.review, "oge/qualifications/current.json", {"reports": [{
            "document_id": self.doc, "promoted_count": 4, "quarantined_count": 2}]})
        record = self.build([{"id": "published", "source_id": "oge", "source_url": self.url}])[0]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")
        self.assertIsNone(record["qualified_transaction_count"])
        self.assertEqual(record["observed_qualified_transaction_count"], 4)
        self.assertEqual(record["observed_quarantined_row_count"], 2)
        self.assertEqual(record["publication_transaction_ids"], ["published"])
        self.assertIn("qualification_source_archive_missing", record["reason_codes"])

    def test_direct_audit_requires_successful_current_extraction(self):
        self.archive()
        (self.review / f"oge/extractions/{self.doc}/{self.sha}/parser-v1.json").unlink()
        self.write(self.review, "oge/qualifications/current.json", {"reports": [{
            "document_id": self.doc, "promoted_count": 1, "quarantined_count": 0}]})
        record = self.build()[0]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")
        self.assertEqual(record["observed_qualified_transaction_count"], 1)
        self.assertIn("qualification_current_original_not_extracted", record["reason_codes"])

    def test_direct_multiple_originals_need_explicit_audit_version(self):
        self.archive()
        self.archive(sha="b" * 64)
        audit = {"document_id": self.doc, "promoted_count": 1, "quarantined_count": 0}
        self.write(self.review, "oge/qualifications/current.json", {"reports": [audit]})
        record = self.build()[0]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")
        self.assertEqual(record["observed_qualified_transaction_count"], 1)
        self.assertIn("qualification_original_version_unresolved", record["reason_codes"])
        audit["source_sha256"] = self.sha
        self.write(self.review, "oge/qualifications/current.json", {"reports": [audit]})
        self.assertEqual(self.build()[0]["reports"][0]["qualification_state"], "qualified")

    def test_whitehouse_coverage_zero_does_not_mean_zero_qualification(self):
        self.index()
        self.archive(True)
        self.write(self.review, "whitehouse/coverage-current.json", {"reports": [{
            "document_id": self.whid, "document_url": self.whurl,
            "review_state": "extracted_with_issues", "production_qualified_fact_count": 0}]})
        record = self.build()[2]["reports"][0]
        self.assertEqual(record["extraction_state"], "extracted")
        self.assertEqual(record["qualification_state"], "not_evaluated")
        self.assertIsNone(record["qualified_transaction_count"])

    def test_whitehouse_annual_holdings_bind_by_url_despite_reused_filing_id(self):
        self.index()
        self.archive(True)
        self.write(self.review, "whitehouse/annual/filer-reported-current.json", {
            "reports": [{"document_id": self.whid, "source_url": self.whurl,
                         "source_sha256": self.sha, "source_candidate_eligible": True,
                         "source_candidate_transaction_eligible": True,
                         "holding_coverage_status": "partial", "filing_date": "2026-06-10"}],
            "holdings": [{"document_id": self.whid, "source_url": self.whurl,
                          "source_sha256": self.sha, "source_holdings_eligible": True}],
            "transactions": [{"document_id": self.whid, "source_url": self.whurl,
                              "source_sha256": self.sha, "source_transaction_eligible": True}]})
        tx = {"id": "annual-tx", "source_id": "oge", "source_url": self.whurl,
              "filing_id": "legacy-shared-id"}
        holding = {"id": "holding", "source_id": "oge", "source_url": self.whurl}
        record = self.build([tx], [holding])[2]["reports"][0]
        self.assertEqual(record["report_id"], self.whid)
        self.assertEqual(record["publication_transaction_ids"], ["annual-tx"])
        self.assertEqual(record["publication_holding_ids"], ["holding"])
        self.assertEqual(record["qualified_holding_count"], 1)
        self.assertEqual(record["qualification_state"], "partial")
        self.assertEqual(record["filed_date"], "2026-06-10")
        self.assertIsNone(record["quarantined_row_count"])

    def test_official_publication_outside_catalog_is_historical_not_lost(self):
        tx = {"id": "wh-old", "source_id": "oge", "source_url": self.whurl}
        direct, request, public = self.build([tx, {
            "id": "unbound", "source_id": "oge", "source_url": "https://example.org/report.pdf"}])
        self.assertEqual(direct["unmatched_publication_transaction_ids"], ["unbound"])
        self.assertEqual(request["unmatched_publication_transaction_ids"], [])
        self.assertEqual(public["unmatched_publication_transaction_ids"], [])
        self.assertEqual(public["reports"][0]["catalog_state"], "historical")
        self.assertEqual(public["reports"][0]["qualification_state"], "unknown")
        self.assertEqual(public["reports"][0]["publication_binding"], "verified")

    def test_empty_annual_selected_arrays_do_not_attest_no_transactions(self):
        self.index()
        self.archive(True)
        self.write(self.review, "whitehouse/annual/filer-reported-current.json", {
            "reports": [{"document_id": self.whid, "source_url": self.whurl,
                         "source_sha256": self.sha, "source_candidate_eligible": True,
                         "holding_coverage_status": "partial"}], "holdings": [], "transactions": []})
        record = self.build()[2]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")

    def test_whitehouse_qualification_version_missing_from_archive_is_unknown(self):
        self.index()
        self.archive(True)
        audit = {"document_id": self.whid, "source_url": self.whurl,
                 "source_sha256": "b" * 64, "promoted_count": 1, "quarantined_count": 0}
        self.write(self.review, "whitehouse/qualifications/candidate-current.json", {"reports": [audit]})
        record = self.build()[2]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")
        self.assertIsNone(record["qualified_transaction_count"])
        self.assertEqual(record["observed_qualified_transaction_count"], 1)

    def test_whitehouse_qualification_without_archive_keeps_publication_independent(self):
        self.index()
        self.write(self.review, "whitehouse/qualifications/candidate-current.json", {"reports": [{
            "document_id": self.whid, "source_url": self.whurl, "filed_at": "2026-06-10",
            "source_sha256": self.sha, "promoted_count": 1, "quarantined_count": 0}]})
        record = self.build([{"id": "wh-published", "source_id": "oge", "source_url": self.whurl}])[2]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")
        self.assertEqual(record["observed_qualification_state"], "qualified")
        self.assertEqual(record["observed_filed_date"], "2026-06-10")
        self.assertIsNone(record["filed_date"])
        self.assertEqual(record["publication_transaction_ids"], ["wh-published"])

    def test_annual_qualification_without_archive_preserves_observed_holding_count(self):
        self.index()
        self.write(self.review, "whitehouse/annual/filer-reported-current.json", {
            "reports": [{"document_id": self.whid, "source_url": self.whurl,
                         "source_sha256": self.sha, "source_candidate_eligible": True,
                         "holding_coverage_status": "complete"}],
            "holdings": [{"document_id": self.whid, "source_url": self.whurl,
                          "source_sha256": self.sha, "source_holdings_eligible": True}],
            "transactions": []})
        record = self.build()[2]["reports"][0]
        self.assertEqual(record["qualification_state"], "unknown")
        self.assertIsNone(record["qualified_holding_count"])
        self.assertEqual(record["observed_qualified_holding_count"], 1)

    def test_corrupted_catalog_is_not_silently_used(self):
        digest = self.catalog([])
        (self.evidence / f"oge/catalog/normalized/{digest}.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash differs"):
            self.build()


if __name__ == "__main__":
    unittest.main()
