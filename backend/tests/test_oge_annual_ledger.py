"""Fixed official-extraction replay for the review-only OGE 278e ledger."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_annual_ledger import build_oge_annual_shadow_ledger
from unison_snapshot.pipeline_ledger import validate_candidate_row, validate_run_manifest


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE = FIXTURES / "oge_annual_vance_2026.json"
EXPECTED = FIXTURES / "oge_annual_vance_2026_dispositions.json"


def _ledger(extraction: dict) -> dict:
    return build_oge_annual_shadow_ledger(
        extraction, run_id="oge-annual-vance-2026", code_commit="a" * 40,
        started_at="2026-09-30T00:00:00Z")


class OgeAnnualLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_bytes = SOURCE.read_bytes()
        self.extraction = json.loads(self.source_bytes)

    def test_real_vance_rows_match_pinned_dispositions(self) -> None:
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(self.source_bytes).hexdigest(),
                         "52c843f25dd21af193f4fdcdf4faa6c7907dff0780531550e34bf983cc1abbf7")
        ledger = _ledger(self.extraction)
        self.assertEqual(ledger["part_counts"], expected["part_counts"])
        self.assertEqual(ledger["report_completeness"], expected["report_completeness"])
        self.assertEqual(ledger["part6_relationships"], expected["part6_relationships"])
        self.assertEqual(ledger["part7_opt_in"], expected["part7_opt_in"])
        self.assertEqual(ledger["revision_reconciliation"], expected["revision_reconciliation"])
        actual_rows = [{
            "candidate_id": row["candidate_id"],
            "part": row["observations"][0]["conditions"]["section"],
            "page": row["evidence_locations"][0]["page"],
            "row_number": row["evidence_locations"][0].get("row_locator"),
            "disposition": row["disposition"], "reasons": row["reasons"],
        } for row in ledger["rows"]]
        self.assertEqual(actual_rows, expected["rows"])
        self.assertEqual(len(actual_rows), 30)
        self.assertEqual(len({row["candidate_id"] for row in actual_rows}), 30)
        self.assertEqual(ledger["manifest"]["accounted_rows"], 30)
        validate_run_manifest(ledger["manifest"])
        for row in ledger["rows"]:
            validate_candidate_row(row)

    def test_untrusted_opt_in_field_cannot_enable_part7(self) -> None:
        extraction = deepcopy(self.extraction)
        extraction["part7_opt_in_verified"] = True
        result = _ledger(extraction)
        self.assertEqual(result["part7_opt_in"], "not_enabled")
        part7 = [row for row in result["rows"] if
                 row["observations"][0]["conditions"]["section"] == "part7"]
        self.assertEqual(len(part7), 10)
        self.assertTrue(all("part7_opt_in_not_enabled" in row["reasons"]
                            for row in part7))

    def test_report_cannot_be_complete_from_only_detected_rows(self) -> None:
        extraction = deepcopy(self.extraction)
        extraction["quarantined"] = []
        result = _ledger(extraction)
        self.assertEqual(result["report_completeness"]["status"],
                         "not_verified_complete")
        self.assertEqual(result["publication_status"], "shadow_only_not_promoted")

    def test_malformed_source_or_unlocated_row_fails_closed(self) -> None:
        extraction = deepcopy(self.extraction)
        extraction["form_type"] = "278-T"
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            _ledger(extraction)
        extraction = deepcopy(self.extraction)
        extraction["source_url"] = "https://not-oge.gov/example.pdf"
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            _ledger(extraction)
        extraction = deepcopy(self.extraction)
        extraction["evidence_archive_path"] = "oge/annual/reports/other.pdf"
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            _ledger(extraction)
        extraction = deepcopy(self.extraction)
        extraction["transactions"][0]["page_number"] = 0
        with self.assertRaisesRegex(ValueError, "positive PDF page"):
            _ledger(extraction)

    def test_parent_row_and_raw_value_provenance_are_retained(self) -> None:
        rows = _ledger(self.extraction)["rows"]
        child = next(row for row in rows if row["evidence_locations"][0].get("row_locator") == "4.2")
        self.assertIn("parent_account_row_unresolved", child["reasons"])
        value = next(item for item in child["observations"] if item["field"] == "value_low")
        self.assertEqual(value["raw_value"], "$100,001 -$250,000")
        self.assertEqual(value["normalized_value"], 100001)
        transaction = next(row for row in rows if row["observations"][0]["conditions"]["section"] == "part7")
        date = next(item for item in transaction["observations"] if item["field"] == "transaction_date")
        self.assertEqual(date["raw_value"], "03/20/2025")
        self.assertEqual(date["normalized_value"], "2025-03-20")

    def test_duplicate_printed_row_is_detected_without_colliding_ids(self) -> None:
        extraction = deepcopy(self.extraction)
        extraction["holdings"].append(deepcopy(extraction["holdings"][-1]))
        result = _ledger(extraction)
        self.assertEqual(result["manifest"]["accounted_rows"], 31)
        self.assertEqual(len({row["candidate_id"] for row in result["rows"]}), 31)
        duplicates = [row for row in result["rows"] if
                      "duplicate_printed_row_number" in row["reasons"]]
        self.assertEqual(len(duplicates), 2)

    def test_distinct_row_ids_do_not_depend_on_input_order(self) -> None:
        original = _ledger(self.extraction)
        shuffled = deepcopy(self.extraction)
        shuffled["holdings"].reverse()
        shuffled["transactions"].reverse()
        self.assertEqual({row["candidate_id"] for row in original["rows"]},
                         {row["candidate_id"] for row in _ledger(shuffled)["rows"]})

    def test_wrong_part_collection_is_rejected(self) -> None:
        extraction = deepcopy(self.extraction)
        extraction["holdings"].append(deepcopy(extraction["transactions"][0]))
        with self.assertRaisesRegex(ValueError, "wrong part"):
            _ledger(extraction)


if __name__ == "__main__":
    unittest.main()
