import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.disclosure_status_senate import build_senate_status


SHA = "a" * 64
CATALOG = "b" * 64
PARSER = "ptr-parser-v2"


def write(root: Path, name: str, value: dict):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class SenateStatusTests(unittest.TestCase):
    def test_no_known_rows_with_unknown_coverage_has_unknown_status_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            self.extract(review, "report", 0)
            self.audit(review,
                quarantined_reports=[{"document_id": "report", "source_sha256": SHA,
                                      "reasons": ["source_row_coverage_incomplete"]}],
                paper_pending_projection={"source_report_coverage": [
                    {"document_id": "report", "source_sha256": SHA,
                     "relation_coverage": "partial_unknown", "logical_row_count": 0,
                     "unknown_slot_count": 8, "coverage_unknown_pages": [1]}]})
            record = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(record["qualification_state"], "unknown")
            self.assertIsNone(record["qualified_transaction_count"])
            self.assertIsNone(record["quarantined_row_count"])

    def test_bound_unknown_pages_keep_known_transactions_but_prevent_full_report_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            self.extract(review, "report")
            coverage = {"document_id": "report", "source_sha256": SHA,
                        "relation_coverage": "partial_unknown", "logical_row_count": 1,
                        "unknown_slot_count": 8, "coverage_unknown_pages": [1], "unobserved_pages": [1]}
            self.audit(review, qualified_rows=[{"document_id": "report", "source_sha256": SHA,
                                              "transaction_id": "q1"}],
                       paper_pending_projection={"source_report_coverage": [coverage]})
            row = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(row["qualification_state"], "partial")
            self.assertEqual(row["qualified_transaction_count"], 1)
            self.assertEqual(row["quarantined_row_count"], 0)
            self.assertEqual(row["source_row_coverage"]["unknown_slot_count"], 8)
            coverage["source_sha256"] = "f" * 64
            self.audit(review, qualified_rows=[{"document_id": "report", "source_sha256": SHA,
                                              "transaction_id": "q1"}],
                       paper_pending_projection={"source_report_coverage": [coverage]})
            row = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(row["qualification_state"], "qualified")
            self.assertNotIn("source_row_coverage", row)

    def fixture(self, root: Path, current: list[str]):
        evidence, review = root / "evidence", root / "review"
        write(review, "status/senate_efd.json", {
            "catalog_sha256": CATALOG, "report_parser_version": PARSER,
            "generated_at": "2026-10-16T01:00:00Z",
        })
        write(review, f"senate_efd/discoveries/{CATALOG}.json", {
            "reports": [{"document_id": doc, "access_method": "electronic_ptr",
                         "document_url": f"https://efdsearch.senate.gov/search/view/ptr/{doc}/"}
                        for doc in current]})
        return evidence, review

    def archive(self, evidence: Path, doc: str, sha=SHA):
        write(evidence, f"senate_efd/reports/{doc}/{sha}.metadata.json", {
            "document_id": doc, "sha256": sha, "access_method": "electronic_ptr",
            "document_url": f"https://efdsearch.senate.gov/search/view/ptr/{doc}/"})
        (evidence / "senate_efd" / "reports" / doc / f"{sha}.html").write_text("report")

    def extract(self, review: Path, doc: str, count=1, sha=SHA):
        write(review, f"senate_efd/extractions/{doc}/{sha}/{PARSER}.json", {
            "document_id": doc, "source_sha256": sha, "transactions": [{}] * count})

    def audit(self, review: Path, **updates):
        value = {"catalog_sha256": CATALOG, "parser_version": PARSER,
                 "data_cutoff_at": "2026-10-16T02:00:00Z"}
        value.update(updates)
        write(review, "senate_efd/qualifications/current.json", value)

    def test_report_dispositions_publication_binding_and_old_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["qualified", "partial", "isolated", "row-only", "old", "failed"])
            for doc in ["qualified", "partial", "isolated", "row-only", "old", "failed", "historical", "superseded-history"]:
                self.archive(evidence, doc)
            for doc, count in [("qualified", 1), ("partial", 2), ("isolated", 3), ("row-only", 1), ("old", 1)]:
                self.extract(review, doc, count)
            for doc, directory in [("qualified", "report_failures"), ("failed", "paper_report_failures")]:
                write(review, f"senate_efd/{directory}/{doc}/{SHA}/{PARSER}.json", {
                    "document_id": doc, "source_sha256": SHA, "reason": "old_or_current_failure"})
            self.audit(review,
                qualified_rows=[{"document_id": doc, "source_sha256": SHA, "transaction_id": tx}
                                for doc, tx in [("qualified", "q1"), ("partial", "p1")]],
                quarantined_rows=[{"document_id": doc, "source_sha256": SHA, "reasons": ["amount_unknown"]}
                                  for doc in ["partial", "row-only"]],
                quarantined_reports=[{"document_id": "isolated", "source_sha256": SHA, "reasons": ["identity_unresolved"]}],
                resolved_amendment_chains=[{"superseded_document_ids": ["old", "superseded-history"]}],
                paper_pending_projection={"physical_slot_count": 483, "unknown_slot_count": 60, "unobserved_page_count": 9})
            txs = [{"source_id": "senate_efd", "id": tx, "filing_id": doc}
                   for doc, tx in [("qualified", "q1"), ("partial", "p1"), ("annual-outside-scope", "a1")]]
            result = build_senate_status(evidence, review, txs, [])[0]
            reports = {r["report_id"]: r for r in result["reports"]}
            self.assertEqual(reports["qualified"]["qualification_state"], "qualified")
            self.assertEqual(reports["qualified"]["extraction_state"], "extracted")
            self.assertNotIn("old_or_current_failure", reports["qualified"]["reason_codes"])
            self.assertEqual(reports["partial"]["qualification_state"], "partial")
            self.assertEqual(reports["partial"]["quarantined_row_count"], 1)
            self.assertEqual(reports["isolated"]["quarantined_row_count"], 3)
            self.assertEqual(reports["row-only"]["qualification_state"], "quarantined")
            self.assertEqual(reports["failed"]["extraction_state"], "failed")
            self.assertIsNone(reports["failed"]["qualified_transaction_count"])
            self.assertEqual(reports["old"]["qualification_state"], "superseded")
            self.assertEqual(reports["superseded-history"]["qualification_state"], "superseded")
            self.assertEqual(reports["historical"]["qualification_state"], "unknown")
            self.assertEqual(reports["qualified"]["publication_transaction_ids"], ["q1"])
            self.assertEqual(reports["qualified"]["publication_binding"], "verified")
            self.assertEqual(result["unmatched_publication_transaction_ids"], ["a1"])
            self.assertEqual(result["paper_coverage_observations"]["physical_slot_count"], 483)
            self.assertEqual(result, build_senate_status(evidence, review, list(reversed(txs)), [])[0])

    def test_source_relation_observations_do_not_change_qualification_or_public_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            self.extract(review, "report", 1)
            pending = {"physical_slot_count": 2, "unknown_slot_count": 1,
                       "unobserved_page_count": 1, "qualified_transaction_count": 0}
            self.audit(review, paper_pending_projection=pending)
            before = build_senate_status(evidence, review, [], [])
            pending["source_report_relations"] = {
                "report_count": 1, "linked_observation_row_count": 2,
                "coverage_unknown_report_count": 1, "coverage_unknown_page_count": 1,
                "qualified_transaction_count": None}
            self.audit(review, paper_pending_projection=pending)
            after = build_senate_status(evidence, review, [], [])
            self.assertEqual(after, before)
            self.assertEqual(after[0]["reports"][0]["qualification_state"], "not_evaluated")
            self.assertIsNone(after[0]["reports"][0]["quarantined_row_count"])

    def test_missing_inputs_are_unknown_and_formal_ids_do_not_create_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = build_senate_status(root / "missing-evidence", root / "missing-review", [
                {"source_id": "senate_efd", "id": "unmatched", "filing_id": "missing"},
                {"source_id": "house_clerk", "id": "other"}], [])[0]
            self.assertEqual(result["reports"], [])
            self.assertEqual(result["unmatched_publication_transaction_ids"], ["unmatched"])
            self.assertIsNone(result["observed_at"])

    def test_mismatched_audit_is_not_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            self.extract(review, "report")
            self.audit(review, catalog_sha256="c" * 64, qualified_rows=[{
                "document_id": "report", "source_sha256": SHA, "transaction_id": "q1"}])
            record = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(record["qualification_state"], "not_evaluated")
            self.assertIsNone(record["qualified_transaction_count"])

    def test_conflicting_publication_references_are_unmatched(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["one", "two"])
            for doc in ["one", "two"]:
                self.archive(evidence, doc)
                self.extract(review, doc)
            self.audit(review, qualified_rows=[{
                "document_id": "one", "source_sha256": SHA, "transaction_id": "q1"}])
            result = build_senate_status(evidence, review, [{
                "source_id": "senate_efd", "id": "q1", "filing_id": "two"}], [])[0]
            self.assertEqual(result["unmatched_publication_transaction_ids"], ["q1"])
            self.assertTrue(all(not r["publication_transaction_ids"] for r in result["reports"]))

    def test_archive_metadata_without_original_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            (evidence / "senate_efd" / "reports" / "report" / f"{SHA}.html").unlink()
            with self.assertRaisesRegex(ValueError, "no original"):
                build_senate_status(evidence, review, [], [])

    def test_multiple_archive_versions_without_selected_version_stay_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            self.archive(evidence, "report", "c" * 64)
            self.extract(review, "report")
            result = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(result["extraction_state"], "unknown")
            self.assertEqual(result["source_versions"], [SHA, "c" * 64])
            self.assertIn("active_archive_version_unknown", result["reason_codes"])
            self.audit(review, qualified_rows=[
                {"document_id": "report", "source_sha256": SHA, "transaction_id": "q1"},
                {"document_id": "report", "source_sha256": "c" * 64, "transaction_id": "q2"}])
            result = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(result["qualification_state"], "not_evaluated")
            self.assertIsNone(result["qualified_transaction_count"])

    def test_empty_transaction_list_requires_evaluated_zero_report(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["zero"])
            self.archive(evidence, "zero")
            self.extract(review, "zero", 0)
            self.audit(review)
            record = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(record["qualification_state"], "not_evaluated")
            self.audit(review, empty_eligible_report_count=1)
            record = build_senate_status(evidence, review, [], [])[0]["reports"][0]
            self.assertEqual(record["qualification_state"], "zero_transactions")
            self.assertEqual(record["qualified_transaction_count"], 0)

    def test_filed_date_uses_report_field_and_id_only_publication_can_bind(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self.fixture(Path(folder), ["report"])
            self.archive(evidence, "report")
            self.extract(review, "report")
            self.audit(review, qualified_rows=[{
                "document_id": "report", "source_sha256": SHA, "transaction_id": "q1",
                "filed_at_raw": "Filed 10/07/2026 @ 4:36 PM"}])
            result = build_senate_status(evidence, review, [{
                "source_id": "senate_efd", "id": "q1"}], [])[0]
            record = result["reports"][0]
            self.assertEqual(record["filed_date"], "2026-10-07")
            self.assertEqual(record["filed_date_basis"], "report_filed_at_raw")
            self.assertEqual(record["publication_transaction_ids"], ["q1"])
            self.assertEqual(result["unmatched_publication_transaction_ids"], [])


if __name__ == "__main__":
    unittest.main()
