from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_trump_sept_oge_v6 import PAGE7_IDS, audit
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
    TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
    _recover_fixed_trump_september_page7, parse_table_rows,
)


PAGE7_OCR_CELLS = [
    ["168", "Amazon Com Inc", "sale", "7/17/2026", "No", "$250.001 • $500,000"],
    ["171", "STATE STREET SPDR BLOOMBERG INTERNATIONAL TREASURY BONO ETF",
     "purchase", "7/8/2026 Yes", "", "$1,000,001·$5,000000"],
    ["172", "FIDELITY MSCI COMMUNICATION SERVICES INDEX ETF I", "P urchase",
     "7/8/2026", "Yes", "$1000001-$5000000"],
    ["173", "VANGUARD SHORT-TERM BONO INDEX FUND ETF SHARES l", "ourchaso",
     "7/8/2026", "Yes", "$1 000 001 -$5 000 000"],
    ["1n", "VANGUARD DIVIDEND APPRECIATION INDEX FUND ETF SHARES",
     "Durchaso", "7/8/2026", "-", "$1 000.001 • $5,000,000"],
    ["178", "HOME DEPOT INC", "ourchaso", "7/31/2026", "no", "$250 001 • $500 000"],
    ["179", "TEXASINSTRSINC", "DUrchaSO", "7/31/2026", "no", "$250 001 -$500 000"],
    ["180", "BROADCOM INC", "Durehase", "7/31/2026", "no", "$250 001 • $500 000"],
    ["181", "S&P GLOBAL INC", "ourchaso", "7/16/2026", "no", "$250,001 • $500 000"],
    ["182", "CME GROUP INC CLASS A", "lourchaso", "7/16/2026", "no", "$250,001 • $500,000"],
    ["183", "PROCTER & GAMBLE CO", "lourchaso", "7/31/2026", "no", "$250 001 -$500 000"],
    ["184", "EXXONMOBIL HLDGS CORP", "lourchase", "7/31/2026", "no", "$250 001 • S500 000"],
    ["185", "FIDELITY NATL INFORMATIO", "lr,urchaso", "7/31/2026", "no", "$250,001 • $500,000"],
    ["186", "NIKE INC CLASS CLASS B", "ourchaso", "7/22/2026", "no", "$250 001 • $500 000"],
    ["187", "PHILIP MORRIS INTL INC", "DurchaSO", "7/31/2026", "no", "$250,001 • $500 000"],
    ["188", "CISCO SYS INC", "ourchaso", "7/31/2026", "no", "$250 001 • $500,000"],
    ["189", "VERIZON COMMUNICATIONS I", "ourchaso", "7/31/2026", "no", "S250 001 -$500 000"],
    ["190", "ABBOTT LABS", "IDUrchaSO", "7/31/2026", "no", "$100 001-$250000"],
    ["191 WATSCO INC CLASS A", "", "lourchaso", "7/22/2026", "no", "$100,001 -$250 000"],
    ["192", "BECTON DICKINSON & CO", "lourchaso", "7/31/2026", "no", "$100,001 -$250,000"],
    ["193", "DUKE ENERGY CORP NEW", "lourchaso", "7/31/2026", "no", "$100 001-$250000"],
    ["194", "FASTENAL CO", "lourchaso", "7/31/2026", "no", "$100,001 -$250,000"],
    ["195", "PFIZER INC", "ourchase", "7/31/2026", "no", "$100,001 -$250 000"],
    ["196", "AVALONBAY CMNTYS INC REIT", "ourchase", "7/31/2026", "no", "$100 001-$250000"],
    ["197", "ALTRIA GROUP INC", "ourchase", "7/31/2026", "no", "$100 001 -S250 000"],
]


def _fixture() -> tuple[dict, dict]:
    parsed_transactions, page7_quarantine = parse_table_rows(
        [(7, cells) for cells in PAGE7_OCR_CELLS],
        source_sha=TRUMP_SEPT_2026_SOURCE_SHA256)
    if parsed_transactions or len(page7_quarantine) != 25:
        raise AssertionError("page-7 OCR fixture no longer matches the fixed parser")
    if {row["extraction_id"] for row in page7_quarantine} != set(PAGE7_IDS.values()):
        raise AssertionError("page-7 OCR fixture extraction IDs changed")
    old_transactions = [
        {"extraction_id": f"oge-278t:{number:024x}", "page_number": 99,
         "row_number": 2000 + number, "asset_name": f"Other {number}"}
        for number in range(350)
    ] + [
        {"extraction_id": f"oge-278t:{5000 + number:024x}", "page_number": 7,
         "row_number": number, "asset_name": f"Existing {number}"}
        for number in (166, 167, 169, 170, 174, 175, 176, 198)
    ]
    old_quarantine = page7_quarantine + [
        {"extraction_id": f"oge-278t:{number:024x}", "page_number": 99,
         "row_number": 3000 + number, "reasons": ["held"]}
        for number in range(350, 1123)
    ]
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
    old = {**header, "parser_version": TRUMP_SEPT_2026_STRUCTURAL_PASS_VERSION,
           "transactions": old_transactions, "quarantined": old_quarantine}
    transactions, quarantined = _recover_fixed_trump_september_page7(
        deepcopy(old_transactions), deepcopy(old_quarantine),
        source_sha=TRUMP_SEPT_2026_SOURCE_SHA256)
    new = {**header, "parser_version": TRUMP_SEPT_2026_PAGE7_PASS_VERSION,
           "transactions": transactions, "quarantined": quarantined}
    return old, new


def _audit(old: dict, new: dict) -> dict:
    with tempfile.TemporaryDirectory() as folder:
        directory = (Path(folder) / "oge" / "extractions" /
                     TRUMP_SEPT_2026_DOCUMENT_ID / TRUMP_SEPT_2026_SOURCE_SHA256)
        directory.mkdir(parents=True)
        (directory / "oge-278t-pdf-v5.json").write_text(json.dumps(old), encoding="utf-8")
        (directory / "oge-278t-pdf-v6.json").write_text(json.dumps(new), encoding="utf-8")
        return audit(Path(folder))


class TrumpSeptemberV6AuditTests(unittest.TestCase):
    def test_source_bound_page7_replay_and_disposition_conservation(self):
        old, new = _fixture()
        result = _audit(old, new)
        self.assertEqual(result["promoted_page7_count"], 25)
        self.assertEqual(result["preserved_v5_transaction_count"], 358)
        self.assertEqual(result["preserved_other_quarantine_count"], 773)
        self.assertEqual(result["page7_printed_position_count"], 33)
        self.assertEqual(result["disposition_entry_count"], 1156)
        self.assertEqual([row["row_number"] for row in new["transactions"][358:]],
                         sorted(PAGE7_IDS))

    def test_old_transaction_change_is_rejected(self):
        old, new = _fixture()
        new["transactions"][0]["asset_name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "v5 transaction"):
            _audit(old, new)

    def test_unrelated_quarantine_change_is_rejected(self):
        old, new = _fixture()
        new["quarantined"][0]["reasons"] = ["Changed"]
        with self.assertRaisesRegex(ValueError, "unrelated quarantine"):
            _audit(old, new)

    def test_original_ocr_cells_remain_immutable(self):
        old, _ = _fixture()
        old["quarantined"][0]["cells"][5] = "$250,001 - $500,000"
        with self.assertRaisesRegex(OgeCatalogError, "original OCR cells changed"):
            _recover_fixed_trump_september_page7(
                old["transactions"], old["quarantined"],
                source_sha=TRUMP_SEPT_2026_SOURCE_SHA256)

    def test_row_177_and_191_keep_original_cells_and_resolution(self):
        old, new = _fixture()
        by_number = {row["row_number"]: row for row in new["transactions"][358:]}
        self.assertEqual(by_number[177]["cells"][0], "1n")
        self.assertEqual(by_number[191]["cells"][0], "191 WATSCO INC CLASS A")
        self.assertEqual(by_number[177]["amount_low"], 1000001)
        self.assertEqual(by_number[191]["asset_name"], "WATSCO INC CLASS A")
        self.assertTrue(_audit(old, new)["page7_disposition_conservation_complete"])

    def test_other_page_cannot_reuse_page7_number(self):
        old, new = _fixture()
        new["quarantined"][0]["row_number"] = 191
        with self.assertRaisesRegex(ValueError, "unrelated quarantine"):
            _audit(old, new)
        old["quarantined"][25]["row_number"] = 191
        with self.assertRaisesRegex(OgeCatalogError, "row labels are not unique"):
            _recover_fixed_trump_september_page7(
                old["transactions"], old["quarantined"],
                source_sha=TRUMP_SEPT_2026_SOURCE_SHA256)

    def test_different_source_is_rejected(self):
        old, _ = _fixture()
        with self.assertRaisesRegex(OgeCatalogError, "fixed v5 source"):
            _recover_fixed_trump_september_page7(
                old["transactions"], old["quarantined"], source_sha="0" * 64)


if __name__ == "__main__":
    unittest.main()
