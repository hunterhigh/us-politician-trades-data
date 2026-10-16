import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.house import document_url


class HouseDisclosureStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.evidence = Path(self.temporary.name) / "evidence"
        self.review = Path(self.temporary.name) / "review"

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def index(self, year, docids, timestamp):
        content = io.BytesIO()
        xml = "<FinancialDisclosure>" + "".join(
            f"<Member><DocID>{docid}</DocID><FilingType>P</FilingType><Year>{year}</Year>"
            f"<FilingDate>10/01/{year}</FilingDate>"
            "<First>Example</First><Last>Person</Last></Member>" for docid in docids
        ) + "</FinancialDisclosure>"
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr(f"{year}FD.xml", xml)
            archive.writestr(f"{year}FD.txt", "")
        data = content.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        path = self.evidence / f"house_clerk/index/{year}/{sha}.json"
        self.write(path, {"filing_year": year, "sha256": sha, "retrieved_at": timestamp})
        path.with_suffix(".zip").write_bytes(data)
        return path

    def archive(self, docid, sha="a" * 64, timestamp="2026-10-15T00:00:00Z", year=2026):
        self.write(self.evidence / f"house_clerk/documents/{year}/{docid}/{sha}.json", {
            "filing_year": year, "document_id": docid, "sha256": sha,
            "source_id": "house_clerk", "source_url": document_url(year, "P", docid),
            "filing_type": "P", "retrieved_at": timestamp,
            "archive_path": f"house_clerk/documents/{year}/{docid}/{sha}.pdf"})

    def artifact(self, kind, docid, sha="a" * 64, **fields):
        self.write(self.review / f"house_clerk/{kind}/2026/{docid}/{sha}.json", {
            "document_id": docid, "source_sha256": sha, **fields})

    def result(self, transactions=()):
        return build_house_status(self.evidence, self.review, list(transactions), [])[0]

    def test_year_scope_and_latest_index_keep_discovered_history(self):
        self.index(2025, ["11"], "2026-10-14T00:00:00Z")
        self.index(2026, ["21", "22"], "2026-10-14T00:00:00Z")
        self.index(2026, ["22"], "2026-10-15T00:00:00Z")
        self.archive("22")
        status = self.result()
        reports = {row["document_id"]: row for row in status["reports"]}
        self.assertEqual(status["scope"]["filing_years"], [2025, 2026])
        self.assertEqual(reports["11"]["acquisition_state"], "not_archived")
        self.assertEqual(reports["11"]["catalog_state"], "current")
        self.assertEqual(reports["21"]["catalog_state"], "historical")
        self.assertIsNone(reports["11"]["qualified_transaction_count"])
        self.assertEqual(reports["22"]["extraction_state"], "not_attempted")
        self.assertEqual(reports["11"]["filed_date"], "2025-10-01")
        self.assertEqual(reports["11"]["filed_date_basis"], "official_catalog_filing_date")
        self.assertEqual(self.result(), status)

    def test_mutually_exclusive_qualification_and_stale_failure(self):
        self.index(2026, ["1", "2", "3", "4", "5"], "2026-10-15T00:00:00Z")
        inputs = [("1", [{"id": "a"}], [], {}),
                  ("2", [{"id": "b"}], [{"reasons": ["amount_unknown"]}], {}),
                  ("3", [], [{"reasons": ["date_unknown"]}], {}),
                  ("4", [], [], {"zero_transaction_document_count": 1})]
        for docid, rows, quarantined, summary in inputs:
            self.archive(docid)
            self.artifact("extractions", docid)
            self.artifact("qualifications", docid, transactions=rows,
                          quarantined=quarantined, qualification=summary)
        self.artifact("failures", "1")
        self.archive("5")
        self.artifact("failures", "5")
        reports = {row["document_id"]: row for row in self.result()["reports"]}
        self.assertEqual([reports[str(i)]["qualification_state"] for i in range(1, 5)],
                         ["qualified", "partial", "quarantined", "zero_transactions"])
        self.assertEqual(reports["1"]["extraction_state"], "extracted")
        self.assertIn("historical_failure_receipt_present", reports["1"]["reason_codes"])
        self.assertEqual(reports["5"]["extraction_state"], "failed")
        self.assertIsNone(reports["5"]["qualified_transaction_count"])
        self.assertEqual(reports["2"]["reason_codes"], ["amount_unknown"])

    def test_latest_source_version_does_not_reuse_old_qualification(self):
        self.archive("1", timestamp="2026-10-14T00:00:00Z")
        self.artifact("extractions", "1")
        self.artifact("qualifications", "1", transactions=[{"id": "old"}],
                      quarantined=[], qualification={})
        self.archive("1", sha="b" * 64)
        report = self.result()["reports"][0]
        self.assertEqual(report["selected_source_version"], "b" * 64)
        self.assertEqual(report["qualification_state"], "not_evaluated")
        self.assertIsNone(report["qualified_transaction_count"])
        self.assertEqual(report["source_versions"], ["a" * 64, "b" * 64])

    def test_ambiguous_source_chronology_remains_unknown(self):
        self.archive("1")
        self.archive("1", sha="b" * 64)
        report = self.result()["reports"][0]
        self.assertIsNone(report["selected_source_version"])
        self.assertEqual(report["extraction_state"], "unknown")
        self.assertIn("source_version_order_unknown", report["reason_codes"])

    def test_publication_uses_input_ids_not_qualification_counts(self):
        self.index(2026, ["1", "2"], "2026-10-15T00:00:00Z")
        self.archive("1")
        transactions = [
            {"id": "actual", "source_id": "house_clerk", "filing_id": "1",
             "source_url": document_url(2026, "P", "1")},
            {"id": "conflict", "source_id": "house_clerk", "filing_id": "2",
             "source_url": document_url(2026, "P", "1")},
            {"id": "wrong_url", "source_id": "house_clerk", "filing_id": "1",
             "source_url": "https://example.com/1.pdf"},
            {"id": "key_only", "source_id": "house_clerk", "filing_id": "2"},
            {"id": "other", "source_id": "senate_efd"}]
        status = self.result(transactions)
        self.assertEqual(status["reports"][0]["publication_transaction_ids"], ["actual"])
        self.assertEqual(status["reports"][1]["publication_transaction_ids"], ["key_only"])
        self.assertEqual(status["unmatched_publication_transaction_ids"], ["conflict", "wrong_url"])

    def test_orphan_review_cannot_claim_archive_or_catalog(self):
        self.artifact("extractions", "1")
        report = self.result()["reports"][0]
        self.assertEqual(report["catalog_state"], "unknown")
        self.assertEqual(report["acquisition_state"], "unknown")
        self.assertEqual(report["extraction_state"], "unknown")
        self.assertEqual(report["qualification_state"], "unknown")
        self.assertIn("review_without_archive_receipt", report["reason_codes"])

    def test_same_official_document_id_in_two_years_has_unique_report_identity(self):
        self.index(2025, ["1"], "2026-10-14T00:00:00Z")
        self.index(2026, ["1"], "2026-10-15T00:00:00Z")
        status = self.result([
            {"id": "old", "source_id": "house_clerk", "filing_id": "1",
             "source_url": document_url(2025, "P", "1")},
            {"id": "new", "source_id": "house_clerk", "filing_id": "1",
             "source_url": document_url(2026, "P", "1")},
            {"id": "ambiguous", "source_id": "house_clerk", "filing_id": "1"}])
        reports = status["reports"]
        self.assertEqual([row["report_id"] for row in reports],
                         ["house_ptr:2025:1", "house_ptr:2026:1"])
        self.assertEqual([row["document_id"] for row in reports], ["1", "1"])
        self.assertEqual([row["publication_transaction_ids"] for row in reports], [["old"], ["new"]])
        self.assertEqual(status["unmatched_publication_transaction_ids"], ["ambiguous"])

    def test_bad_receipts_and_duplicate_publication_ids_fail_closed(self):
        path = self.index(2026, ["1"], "2026-10-15T00:00:00Z")
        path.with_suffix(".zip").write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "index hash mismatch"):
            self.result()
        path.unlink()
        tx = {"id": "duplicate", "source_id": "house_clerk"}
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.result([tx, tx])


if __name__ == "__main__":
    unittest.main()
