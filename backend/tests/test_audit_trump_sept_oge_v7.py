from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from audit_trump_sept_oge_v7 import EXPECTED_LOCATORS, audit
from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_GEOMETRY_PASS_VERSION,
    TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
)


def _fixture() -> tuple[dict, dict]:
    v6_transactions = [
        {"extraction_id": f"oge-278t:{number:024x}", "page_number": 1,
         "row_number": number, "asset_name": f"Existing {number}"}
        for number in range(1, 384)
    ]
    target_rows = {page: set() for page in (2, 23, 31)}
    target_rows[2] = set(range(1, 34))
    target_rows[23] = set(range(694, 728))
    target_rows[31] = set(range(959, 992))
    targets = {(23, number) for number in (
        704, 706, 707, 708, 709, 710, 711, 712, 714, 715, 716, 717,
        718, 719, 720, 721, 722, 723, 724, 725, 727,
    )}
    targets |= {(31, 959), (31, 960), (31, 961)}
    v6_quarantined = []
    for page, numbers in target_rows.items():
        for number in sorted(numbers):
            row_number = None if page == 2 and number in {13, 24} else number
            raw_label = {13: ",· 13 ••", 24: "24 ."}.get(number, str(number))
            v6_quarantined.append({
                "extraction_id": f"oge-278t:q{page:02x}{number:06x}",
                "page_number": page, "row_number": row_number,
                "cells": [raw_label, "Original OCR", "", "", "No", ""],
                "reasons": (["row_number_invalid", "transaction_type_unsupported"]
                            if row_number is None else ["transaction_type_unsupported"]),
            })
    for number in range(673):
        v6_quarantined.append({
            "extraction_id": f"oge-278t:r{number:023x}",
            "page_number": 99, "row_number": number + 2000,
            "cells": [], "reasons": ["held"],
        })
    header = {
        "schema_version": EXTRACTION_SCHEMA,
        "source_id": "oge", "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_url": TRUMP_SEPT_2026_SOURCE_URL,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "filed_at": "2026-09-08", "evidence_complete": True,
        "document_reasons": [],
    }
    v6 = {**header, "parser_version": TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
          "transactions": v6_transactions, "quarantined": v6_quarantined}
    promoted = []
    for page, number in sorted(targets):
        original = next(row for row in v6_quarantined
                        if row["page_number"] == page and row["row_number"] == number)
        promoted.append({
            "extraction_id": original["extraction_id"], "page_number": page,
            "row_number": number, "cells": list(original["cells"]),
            "asset_name": f"Recovered {number}", "transaction_type": "purchase",
            "transaction_date": "2026-07-27", "amount_low": 50001, "amount_high": 100000,
            "geometry_recovery": {
                "basis": "fixed_source_pdf_word_geometry_v7",
                "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
                "page_number": page, "printed_row_number": number,
                "original_cells": list(original["cells"]),
            },
        })
    promoted_ids = {row["extraction_id"] for row in promoted}
    remaining = deepcopy([row for row in v6_quarantined
                          if row["extraction_id"] not in promoted_ids])
    for row in remaining:
        if row["page_number"] == 2 and row["row_number"] is None:
            number = int(next(iter(re.findall(r"\d+", row["cells"][0]))))
            row["row_number"] = number
            row["geometry_row_alignment"] = {
                "basis": "fixed_source_pdf_row_order_and_label_v7",
                "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
                "page_number": 2, "printed_row_number": number,
                "original_row_number": None, "original_row_label": row["cells"][0],
                "original_reasons": list(row["reasons"]),
                "original_cells": list(row["cells"]),
            }
            row["reasons"] = [reason for reason in row["reasons"]
                              if reason != "row_number_invalid"]
    v7 = {**header, "parser_version": TRUMP_SEPT_2026_GEOMETRY_PASS_VERSION,
          "transactions": deepcopy(v6_transactions) + promoted,
          "quarantined": remaining}
    return v6, v7


class TrumpSeptemberV7AuditTests(unittest.TestCase):
    def _audit_pair(self, old: dict, new: dict) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "oge" / "extractions" / TRUMP_SEPT_2026_DOCUMENT_ID / \
                TRUMP_SEPT_2026_SOURCE_SHA256
            root.mkdir(parents=True)
            (root / "oge-278t-pdf-v6.json").write_text(json.dumps(old), encoding="utf-8")
            (root / "oge-278t-pdf-v7.json").write_text(json.dumps(new), encoding="utf-8")
            return audit(Path(folder))

    def test_v7_promotions_conserve_rows_and_preserve_existing_records(self):
        old, new = _fixture()
        result = self._audit_pair(old, new)
        self.assertEqual(result["promoted_geometry_count"], 24)
        self.assertEqual({tuple(row) for row in result["promoted_locators"]}, EXPECTED_LOCATORS)
        self.assertTrue(result["legacy_v6_transactions_unchanged"])
        self.assertTrue(result["unrelated_quarantine_unchanged"])
        self.assertEqual(result["geometry_pages_disposition_count"], 100)
        self.assertEqual(result["quarantined_row_number_alignments"], 2)

    def test_v7_rejects_legacy_transaction_mutation(self):
        old, new = _fixture()
        new["transactions"][0]["asset_name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "v6 transaction"):
            self._audit_pair(old, new)

    def test_v7_rejects_unknown_or_unproven_promotion(self):
        old, new = _fixture()
        new["transactions"][383]["geometry_recovery"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "valid source-bound proof"):
            self._audit_pair(old, new)

    def test_v7_rejects_changed_unrelated_quarantine(self):
        old, new = _fixture()
        new["quarantined"][0]["reasons"] = ["changed"]
        with self.assertRaisesRegex(ValueError, "unrelated quarantine"):
            self._audit_pair(old, new)


if __name__ == "__main__":
    unittest.main()
