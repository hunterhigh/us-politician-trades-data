"""The fixed visual review may qualify only its original source slots."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

from qualify_senate_paper_first_report import (DOCUMENT_ID, SOURCE_SHA256, qualify)
from unison_snapshot.senate_candidate import _transaction
from unison_snapshot.senate_reports import PARSER_VERSION


class FixedPaperPromotionTest(unittest.TestCase):
    def setUp(self):
        self.audit = json.loads((ROOT / "docs/senate-paper-first-report-frontend-gates.json")
                                .read_text(encoding="utf-8"))
        pages = {item["page_number"]: item["page_sha256"]
                 for item in self.audit["rows"]}
        self.manifest = {"document_id": DOCUMENT_ID,
                         "entrypoint_source_sha256": SOURCE_SHA256,
                         "evidence_complete": True,
                         "page_count": max(pages),
                         "manifest_sha256": "a" * 64,
                         "pages": [{"page_number": n, "sha256": pages.get(n, "b" * 64)}
                                   for n in range(1, max(pages) + 1)]}
        self.pending = {"schema_version": "senate-paper-pending-slot-projection/v3",
                        "pending_rows": [
                            {"source_slot": {"document_id": DOCUMENT_ID,
                                             "entrypoint_source_sha256": SOURCE_SHA256,
                                             "page_number": item["page_number"],
                                             "page_sha256": item["page_sha256"],
                                             "grid_slot": item["grid_slot"],
                                             "row_crop_sha256": item["row_crop_sha256"]},
                             "account_heading_visual_raw": item["account_heading_visual_raw"],
                             "legacy_extraction_failed": True,
                             "legacy_extraction_report_row_count": 0}
                            for item in self.audit["rows"]]}
        self.batch = {"parser_version": PARSER_VERSION, "report_count": 9,
                      "extraction_count": 0, "failure_count": 9,
                      "transaction_count": 0, "eligible_transaction_count": 0,
                      "quarantined_transaction_count": 0, "extractions": [],
                      "failures": [{"document_id": DOCUMENT_ID,
                                    "source_sha256": SOURCE_SHA256},
                                   *({"document_id": str(n), "source_sha256": "c" * 64}
                                     for n in range(8))]}

    def test_qualifies_36_distinct_disclosure_slots_and_leaves_other_failures(self):
        result = qualify(self.batch, self.pending, self.audit, self.manifest)
        self.assertEqual((result["extraction_count"], result["failure_count"]), (1, 8))
        extraction = result["extractions"][0]
        self.assertEqual(len(extraction["transactions"]), 36)
        self.assertEqual(len({row["extraction_id"] for row in extraction["transactions"]}), 36)
        self.assertTrue(all(" (Account: (S) " in row["asset_name_raw"]
                            for row in extraction["transactions"]))
        identity = {"person_id": "senate:B001277"}
        for row in extraction["transactions"]:
            candidate, reasons = _transaction(row, extraction, identity)
            self.assertEqual(reasons, [])
            self.assertIsNone(candidate["instrument_type"])
            self.assertEqual(candidate["filed_at"], "2026-02-12T00:00:00Z")

    def test_changed_row_crop_or_heading_blocks_promotion(self):
        pending = deepcopy(self.pending)
        pending["pending_rows"][0]["source_slot"]["row_crop_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "differs from verified source slot"):
            qualify(self.batch, pending, self.audit, self.manifest)
        pending = deepcopy(self.pending)
        pending["pending_rows"][0]["account_heading_visual_raw"] = "wrong account"
        with self.assertRaisesRegex(ValueError, "differs from verified source slot"):
            qualify(self.batch, pending, self.audit, self.manifest)

    def test_unknown_instrument_is_not_generic_permission(self):
        extraction = qualify(self.batch, self.pending, self.audit, self.manifest)["extractions"][0]
        row = deepcopy(extraction["transactions"][0])
        row.pop("qualification_basis")
        candidate, reasons = _transaction(row, extraction, {"person_id": "senate:B001277"})
        self.assertIsNone(candidate)
        self.assertIn("instrument_type_not_supported", reasons)


if __name__ == "__main__":
    unittest.main()
