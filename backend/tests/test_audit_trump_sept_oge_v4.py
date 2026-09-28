from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_trump_sept_oge_v4 import audit
from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_PARSER_VERSION, TRUMP_SEPT_2026_SOURCE_SHA256,
    TRUMP_SEPT_2026_SOURCE_URL,
)


def _id(number: int) -> str:
    return f"oge-278t:{number:024x}"


def _fixture() -> tuple[dict, dict]:
    header = {
        "schema_version": EXTRACTION_SCHEMA,
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_url": TRUMP_SEPT_2026_SOURCE_URL,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "filed_at": "2026-09-08",
        "evidence_complete": True,
        "document_reasons": [],
    }
    old = {**header, "parser_version": TRUMP_SEPT_2026_PARSER_VERSION,
           "transactions": [], "quarantined": []}
    for number in range(228):
        old["transactions"].append({
            "extraction_id": _id(number), "page_number": 2,
            "row_number": number + 1, "asset_name": f"Asset {number}",
            "transaction_type_raw": "Purchase", "transaction_type": "purchase",
        })
    for offset in range(925):
        number = 228 + offset
        sale_ocr = offset < 259
        reasons = (["transaction_type_unsupported"] if offset < 124 else
                   ["amount_range_unsupported", "transaction_type_unsupported"]
                   if sale_ocr else ["transaction_date_invalid"])
        old["quarantined"].append({
            "extraction_id": _id(number), "page_number": 2 + offset // 32,
            "row_number": number + 1, "asset_name": f"Asset {number}",
            "transaction_type_raw": "salo" if sale_ocr else "Unknown",
            "transaction_type": None,
            "cells": [str(number + 1), f"Asset {number}",
                      "salo" if sale_ocr else "Unknown", "7/17/2026", "No",
                      "$1,001 - $15,000"],
            "reasons": reasons,
        })
    new = deepcopy(old)
    new["parser_version"] = "oge-278t-pdf/v4"
    new["quarantined"] = []
    for offset, row in enumerate(old["quarantined"]):
        result = deepcopy(row)
        if offset < 259:
            result["transaction_type"] = "sale"
            result["type_ocr_correction"] = {
                "basis": "fixed_source_salo_ocr_normalization",
                "raw_type": "salo",
                "resolved_type": "sale",
                "original_reasons": list(row["reasons"]),
            }
            result["reasons"].remove("transaction_type_unsupported")
            if offset < 124:
                del result["reasons"]
                new["transactions"].append(result)
                continue
        new["quarantined"].append(result)
    return old, new


def _write(root: Path, old: dict, new: dict) -> None:
    directory = (root / "oge/extractions" / TRUMP_SEPT_2026_DOCUMENT_ID /
                 TRUMP_SEPT_2026_SOURCE_SHA256)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "oge-278t-pdf-v3.json").write_text(
        json.dumps(old), encoding="utf-8")
    (directory / "oge-278t-pdf-v4.json").write_text(
        json.dumps(new), encoding="utf-8")


class TrumpSeptemberV4AuditTests(unittest.TestCase):
    def _check(self, old: dict, new: dict) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, old, new)
            return audit(root)

    def test_exact_replay_passes_and_counts_all_rows(self):
        old, new = _fixture()
        result = self._check(old, new)
        self.assertEqual(result["corrected_salo_count"], 259)
        self.assertEqual(result["newly_promoted_count"], 124)
        self.assertEqual(result["v4_transaction_count"], 352)
        self.assertEqual(result["v4_quarantined_count"], 801)
        self.assertTrue(result["row_conservation_complete"])

    def test_original_qualified_row_must_remain_byte_equivalent(self):
        old, new = _fixture()
        new["transactions"][0]["asset_name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "original qualified row"):
            self._check(old, new)

    def test_promoted_row_must_keep_original_cells_and_raw_type(self):
        old, new = _fixture()
        new["transactions"][228]["cells"][1] = "Different asset"
        with self.assertRaisesRegex(ValueError, "beyond type and reasons"):
            self._check(old, new)
        new["transactions"][228]["cells"][1] = old["quarantined"][0]["cells"][1]
        new["transactions"][228]["transaction_type_raw"] = "sale"
        with self.assertRaisesRegex(ValueError, "beyond type and reasons"):
            self._check(old, new)

    def test_other_quarantine_reason_cannot_be_discarded(self):
        old, new = _fixture()
        new["quarantined"][0]["reasons"] = []
        with self.assertRaisesRegex(ValueError, "beyond type and reasons"):
            self._check(old, new)

    def test_non_salo_row_cannot_change(self):
        old, new = _fixture()
        new["quarantined"][135]["transaction_type"] = "sale"
        with self.assertRaisesRegex(ValueError, "non-salo quarantined row"):
            self._check(old, new)

    def test_ids_must_form_the_same_unique_1153_row_set(self):
        old, new = _fixture()
        new["quarantined"][0]["extraction_id"] = new["transactions"][0]["extraction_id"]
        with self.assertRaisesRegex(ValueError, "repeated extraction ID"):
            self._check(old, new)

    def test_document_binding_and_header_must_not_drift(self):
        old, new = _fixture()
        new["source_url"] = "https://example.org/wrong.pdf"
        with self.assertRaisesRegex(ValueError, "fixed source"):
            self._check(old, new)
        new["source_url"] = TRUMP_SEPT_2026_SOURCE_URL
        new["catalog_added_date"] = "2026-09-23"
        with self.assertRaisesRegex(ValueError, "document field"):
            self._check(old, new)

    def test_source_bound_correction_evidence_must_be_exact(self):
        old, new = _fixture()
        self.assertEqual(self._check(old, new)["newly_promoted_count"], 124)
        new["transactions"][228]["type_ocr_correction"]["raw_type"] = "sale"
        with self.assertRaisesRegex(ValueError, "beyond type and reasons"):
            self._check(old, new)


if __name__ == "__main__":
    unittest.main()
