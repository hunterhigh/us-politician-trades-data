from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_annual_receipt_overlap import audit_receipt_overlap


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OgeAnnualReceiptOverlapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = _fixture("oge_annual_six_receipt_metadata.json")
        self.dates = _fixture("oge_annual_scanned_cover_dates_summary.json")
        self.part7 = _fixture("oge_annual_harris_2023_part7_physical_rows.json")
        reports = [{**row, "access_method": "direct_pdf"}
                   for row in self.metadata["reports"]]
        self.catalog = {"schema_version": "oge-whitehouse-coverage/v1",
                        "records_total": 16746, "catalog_rows_covered": 16746,
                        "catalog_sha256": self.metadata["official_catalog_sha256"],
                        "reports": reports}

    def _audit(self, *, catalog: dict | None = None, dates: list | None = None,
               part7: dict | None = None, metadata: dict | None = None) -> dict:
        catalog = self.catalog if catalog is None else catalog
        metadata = deepcopy(self.metadata) if metadata is None else metadata
        payload = json.dumps(catalog, sort_keys=True).encode()
        metadata["catalog_snapshot_sha256"] = hashlib.sha256(payload).hexdigest()
        return audit_receipt_overlap(payload, self.dates if dates is None else dates,
                                     self.part7 if part7 is None else part7, metadata)

    def test_catalog_dates_and_pdf_timestamps_remain_distinct_from_receipt(self) -> None:
        result = self._audit()
        summary = _fixture("oge_annual_receipt_overlap_summary.json")
        self.assertEqual(result["harris_part7_filled_row_count"], 95)
        self.assertEqual(result["harris_same_filer_278t_catalog_count"], 0)
        self.assertEqual(result["verified_agency_receipt_count"], 0)
        self.assertEqual(result["production_eligible_transaction_count"], 0)
        self.assertEqual(len(result["harris_pending_rows"]), 95)
        self.assertEqual(len({(row["page_number"], row["printed_number"])
                              for row in result["harris_pending_rows"]}), 95)
        self.assertTrue(all(row["overlap_status"] == "unresolved_no_source_pair" and
                            not row["deduplicated"] and not row["production_eligible"]
                            for row in result["harris_pending_rows"]))
        for row in result["receipt_matrix"]:
            self.assertIsNone(row["filed_at"])
            self.assertEqual(row["agency_receipt_status"], "unverified")
            self.assertIn("not_agency_receipt", row["catalog_date_semantics"])
            self.assertIn("not_agency_receipt", row["pdf_date_semantics"])
        self.assertEqual([row["source_document_id"] for row in result["receipt_matrix"]],
                         [row["source_document_id"] for row in summary["receipt_matrix"]])

    def test_same_filer_278t_candidate_is_a_link_not_deduplication(self) -> None:
        catalog = deepcopy(self.catalog)
        catalog["reports"].append({"document_type": "278t",
                                   "filer_name": "Kamala D. Harris",
                                   "source_document_id": "f" * 32})
        result = self._audit(catalog=catalog)
        self.assertEqual(result["harris_same_filer_278t_catalog_count"], 1)
        self.assertTrue(all(row["overlap_status"] == "unresolved_candidates_present" and
                            not row["deduplicated"] for row in result["harris_pending_rows"]))

    def test_receipt_or_catalog_drift_fails_closed(self) -> None:
        changed = deepcopy(self.dates)
        changed[0]["dates"]["agency_receipt"] = "2023-05-08"
        with self.assertRaises(ValueError):
            self._audit(dates=changed)
        changed = deepcopy(self.catalog)
        changed["reports"][0]["catalog_added_date"] = "2020-01-01"
        with self.assertRaises(ValueError):
            self._audit(catalog=changed)
        changed = deepcopy(self.part7)
        changed["part7_pages"][0]["rows"].pop()
        with self.assertRaises(ValueError):
            self._audit(part7=changed)


if __name__ == "__main__":
    unittest.main()
