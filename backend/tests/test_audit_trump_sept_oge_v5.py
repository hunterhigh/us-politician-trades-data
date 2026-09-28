from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_trump_sept_oge_v5 import audit
from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_SECOND_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
    TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
    _recover_fixed_trump_september_structural_rows,
)


def _id(number: int) -> str:
    return f"oge-278t:{number:024x}"


def _base_row(identifier: str, number: int) -> dict:
    return {
        "extraction_id": identifier,
        "page_number": 2 + number // 33,
        "row_number": number,
        "owner": "Self",
        "asset_name": f"Asset {number}",
        "ticker": None,
        "transaction_type_raw": "Purchase",
        "transaction_type": "purchase",
        "transaction_date": "2026-07-17",
        "late_notification_raw": "No",
        "amount_raw": "$1,001 - $15,000",
        "amount_low": 1001,
        "amount_high": 15000,
    }


def _fixture() -> tuple[dict, dict]:
    header = {
        "schema_version": EXTRACTION_SCHEMA,
        "source_id": "oge",
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_url": TRUMP_SEPT_2026_SOURCE_URL,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "filed_at": "2026-09-08",
        "evidence_complete": True,
        "document_reasons": [],
    }
    old = {**header, "parser_version": TRUMP_SEPT_2026_SECOND_PASS_VERSION,
           "transactions": [], "quarantined": []}
    for number in range(1, 353):
        old["transactions"].append(_base_row(_id(number), number))
    for offset in range(798):
        number = 353 + offset
        row = _base_row(_id(number), number)
        row.update(transaction_type_raw="Unknown", transaction_type=None,
                   cells=[str(number), row["asset_name"], "Unknown", "7/17/2026",
                          "No", "$1,001 - $15,000"],
                   reasons=["transaction_type_unsupported"])
        old["quarantined"].append(row)
    old["quarantined"].extend([
        {
            "extraction_id": "oge-278t:fac97da8e5025227ad0b9139",
            "page_number": 12, "row_number": 337, "owner": "Self",
            "asset_name": "", "ticker": None,
            "transaction_type_raw": "sale sale", "transaction_type": None,
            "transaction_date": "2026-07-29", "late_notification_raw": "no",
            "amount_raw": "$1 001 -$15 000", "amount_low": 1001,
            "amount_high": 15000,
            "cells": ["337", "", "sale sale", "7/29/2026", "no",
                      "$1 001 -$15 000"],
            "reasons": ["description_missing", "transaction_type_unsupported"],
        },
        {
            "extraction_id": "oge-278t:8b3bb35e34734a1102b4e41e",
            "page_number": 12, "row_number": 336, "owner": "Self",
            "asset_name": "WENDYS CO CLASS A", "ticker": None,
            "transaction_type_raw": "", "transaction_type": None,
            "transaction_date": "2026-07-29", "late_notification_raw": "no",
            "amount_raw": "$1 001 -$15 000", "amount_low": 1001,
            "amount_high": 15000,
            "cells": ["336", "WENDYS CO CLASS A", "", "7/29/2026", "no",
                      "$1 001 -$15 000"],
            "reasons": ["row_number_duplicated", "transaction_type_unsupported"],
        },
        {
            "extraction_id": "oge-278t:97438cd2efe2ba17cc80ccb4",
            "page_number": 34, "row_number": 11, "owner": "Self",
            "asset_name": "Deacrlpllon", "ticker": None,
            "transaction_type_raw": "", "transaction_type": None,
            "transaction_date": None,
            "late_notification_raw": "Notlflcatlon Re. .l vedOver 30 0.V.Aao no",
            "amount_raw": "Amount", "amount_low": None, "amount_high": None,
            "cells": ["11", "Deacrlpllon", "", "Date",
                      "Notlflcatlon Re. .l vedOver 30 0.V.Aao no", "Amount"],
            "reasons": ["amount_range_unsupported", "row_number_duplicated",
                        "transaction_date_invalid", "transaction_type_unsupported"],
        },
    ])
    transactions, quarantined = _recover_fixed_trump_september_structural_rows(
        deepcopy(old["transactions"]), deepcopy(old["quarantined"]),
        source_sha=TRUMP_SEPT_2026_SOURCE_SHA256)
    new = {**header, "parser_version": TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
           "transactions": transactions, "quarantined": quarantined}
    return old, new


def _write(root: Path, old: dict, new: dict) -> None:
    directory = (root / "oge/extractions" / TRUMP_SEPT_2026_DOCUMENT_ID /
                 TRUMP_SEPT_2026_SOURCE_SHA256)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "oge-278t-pdf-v4.json").write_text(
        json.dumps(old), encoding="utf-8")
    (directory / "oge-278t-pdf-v5.json").write_text(
        json.dumps(new), encoding="utf-8")


class TrumpSeptemberV5AuditTests(unittest.TestCase):
    def _check(self, old: dict, new: dict) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write(root, old, new)
            return audit(root)

    def test_exact_replay_preserves_352_and_conserves_1156_disposition_entries(self):
        old, new = _fixture()
        result = self._check(old, new)
        self.assertEqual(result["preserved_v4_transaction_count"], 352)
        self.assertEqual(result["recovered_missing_row_count"], 4)
        self.assertEqual(result["promoted_aligned_row_count"], 2)
        self.assertEqual(result["removed_header_artifact_count"], 1)
        self.assertEqual(result["disposition_entry_count"], 1156)
        self.assertTrue(result["disposition_entry_conservation_complete"])

    def test_existing_v4_transaction_cannot_change(self):
        old, new = _fixture()
        new["transactions"][0]["asset_name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "existing v4 transaction"):
            self._check(old, new)

    def test_unrelated_quarantine_row_cannot_change(self):
        old, new = _fixture()
        new["quarantined"][0]["asset_name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "unrelated quarantine"):
            self._check(old, new)

    def test_recovered_row_fields_are_exact(self):
        old, new = _fixture()
        new["transactions"][352]["asset_name"] = "Wrong"
        with self.assertRaisesRegex(ValueError, "recovered row evidence"):
            self._check(old, new)

    def test_aligned_row_fields_are_exact(self):
        old, new = _fixture()
        new["transactions"][-1]["row_number"] = 336
        with self.assertRaisesRegex(ValueError, "alignment correction"):
            self._check(old, new)

    def test_header_artifact_cannot_remain(self):
        old, new = _fixture()
        header = next(row for row in old["quarantined"]
                      if row["extraction_id"] == "oge-278t:97438cd2efe2ba17cc80ccb4")
        new["quarantined"].append(deepcopy(header))
        new["quarantined"].pop(0)
        with self.assertRaisesRegex(ValueError, "unexpected extraction ID"):
            self._check(old, new)

    def test_document_binding_cannot_drift(self):
        old, new = _fixture()
        new["source_url"] = "https://example.org/wrong.pdf"
        with self.assertRaisesRegex(ValueError, "fixed source"):
            self._check(old, new)


if __name__ == "__main__":
    unittest.main()
