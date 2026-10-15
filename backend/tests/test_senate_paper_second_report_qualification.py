"""Check the bounded second paper report source correction contract."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

from qualify_senate_paper_second_report import (
    CORRECTIONS_SHA256, DOCUMENT_ID, SOURCE_SHA256, qualify,
)
from unison_snapshot.senate_candidate import _transaction


class SecondPaperReportTest(unittest.TestCase):
    def setUp(self):
        raw = (ROOT / "docs/senate-paper-second-report-corrections.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), CORRECTIONS_SHA256)
        self.corrections = json.loads(raw)
        ledger = json.loads((ROOT / "docs/senate-paper-viewer-remainders.json")
                            .read_text(encoding="utf-8"))
        self.ledger = next(item for item in ledger["reports"]
                           if item["document_id"] == DOCUMENT_ID)

    def test_visual_corrections_are_source_bound_and_nonoverlapping(self):
        slots = {(item["page_number"], item["grid_slot"]): item
                 for item in self.ledger["slots"]}
        self.assertEqual(len(slots), len(self.ledger["slots"]))
        self.assertEqual(len(self.corrections["account_headings"]), 5)
        self.assertEqual(len(self.corrections["asset_corrections"]), 6)
        seen = set()
        for item in [*self.corrections["account_headings"],
                     *self.corrections["asset_corrections"]]:
            key = (item["page_number"], item["grid_slot"])
            self.assertNotIn(key, seen)
            seen.add(key)
            self.assertEqual(item["page_sha256"], slots[key]["page_sha256"])
            self.assertEqual(item["asset_crop_sha256"],
                             slots[key]["asset_crop"]["grayscale_sha256"])

    def test_changed_source_binding_is_rejected_before_evidence_read(self):
        batch = {"parser_version": "senate-efd-report-parser-2026-09-v2",
                 "failures": [], "extractions": []}
        pending = {"schema_version": "senate-paper-pending-slot-projection/v3"}
        manifest = {"document_id": DOCUMENT_ID,
                    "entrypoint_source_sha256": SOURCE_SHA256,
                    "manifest_sha256": self.corrections["manifest_sha256"],
                    "evidence_complete": True}
        bad = deepcopy(self.corrections)
        bad["received_date"] = "2026-07-18"
        with self.assertRaisesRegex(ValueError, "source binding changed"):
            qualify(batch, pending, bad, self.ledger, manifest, ROOT)

    def test_second_report_unknown_type_is_narrowly_supported(self):
        extraction = {"schema_version": "senate-efd-paper-ptr-extraction/v1",
                      "qualification_basis": "fixed_source_visual_review_v1",
                      "document_id": DOCUMENT_ID,
                      "source_sha256": SOURCE_SHA256,
                      "visual_corrections_sha256": CORRECTIONS_SHA256,
                      "filed_at_raw": "Filed 07/17/2026",
                      "source_url": f"https://efdsearch.senate.gov/search/view/paper/{DOCUMENT_ID}/"}
        row = {"qualification_status": "eligible", "quarantine_reasons": [],
               "qualification_basis": "fixed_source_visual_review_v1",
               "extraction_id": "senate-ptr:" + "a" * 24,
               "amount_raw": "$1,001 - $15,000", "transaction_type": "purchase",
               "owner_raw": "Spouse",
               "asset_name_raw": "MH Four Winds LLC (Account: (S) Summa I LLC)",
               "asset_type_raw": None, "ticker_raw": None,
               "transaction_date": "2026-06-23"}
        value, reasons = _transaction(row, extraction, {"person_id": "senate:B001277"})
        self.assertEqual(reasons, [])
        self.assertIsNone(value["instrument_type"])
        self.assertEqual(value["filed_at"], "2026-07-17T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
