"""Filer attribution must not silently become personal asset ownership."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.oge_278e_public import PARSER_VERSION as ANNUAL_PARSER, SCHEMA as ANNUAL_SCHEMA
from unison_snapshot.whitehouse_278t import PARSER_VERSION as TRADE_PARSER, EXTRACTION_SCHEMA as TRADE_SCHEMA
from unison_snapshot.whitehouse_reported import build_filer_reported_index


class FilerReportedIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def report(self, filename: str, kind: str, *, state: str = "extracted_with_issues") -> dict:
        url = f"https://www.whitehouse.gov/wp-content/uploads/2026/09/{filename}.pdf"
        return {"document_id": "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24],
                "document_url": url, "filer_name_from_label": "Example, Ada",
                "document_type_from_label": kind, "archive_sha256_versions": ["a" * 64],
                "review_state": state}

    def write_extraction(self, report: dict, parser: str, value: dict) -> str:
        relative = (Path("whitehouse/extractions") / report["document_id"][7:] /
                    report["archive_sha256_versions"][0] /
                    f"{parser.replace('/', '-')}.json")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"parser_version": parser,
                                    "source_url": report["document_url"],
                                    "source_sha256": report["archive_sha256_versions"][0],
                                    "filer_name": "Example, Ada", **value}), encoding="utf-8")
        return relative.as_posix()

    def coverage(self, *reports: dict) -> dict:
        return {"schema_version": "whitehouse-public-coverage/v1",
                "source_id": "whitehouse_public_disclosures",
                "report_link_count": len(reports), "reports": list(reports)}

    def test_reported_trade_and_unknown_owned_asset_are_stored_without_self_inference(self) -> None:
        trade = self.report("trade", "278t")
        annual = self.report("annual", "278e_annual")
        trade_path = self.write_extraction(trade, TRADE_PARSER, {
            "schema_version": TRADE_SCHEMA, "pdf_filer_name": "Ada Example",
            "transactions": [{"owner": "Spouse", "row_number": 1, "asset_name": "ABC"}],
            "quarantined": [{"owner": "Unknown", "row_number": 2,
                             "asset_name": "Unclear transaction"}]})
        annual_path = self.write_extraction(annual, ANNUAL_PARSER, {
            "schema_version": ANNUAL_SCHEMA,
            "holdings": [{"section": "part6", "owner": "Unknown", "page_number": 3,
                          "row_number": "1", "asset_name": "XYZ"}],
            "transactions": [], "quarantined": [], "excluded": []})
        result = build_filer_reported_index(self.coverage(trade, annual), self.root,
                                            coverage_sha256="b" * 64)
        self.assertEqual(result["meaning"], "filer_reported_not_filer_owned")
        self.assertEqual(result["record_count"], 3)
        by_type = {item["form_type_from_page"]: item for item in result["reports"]}
        self.assertEqual(by_type["278t"]["filer_attribution"], "pdf_and_official_page")
        self.assertEqual(by_type["278t"]["records"][0]["owner_is_filer"], False)
        self.assertEqual(by_type["278t"]["records"][1]["owner_is_filer"], None)
        self.assertEqual(by_type["278e_annual"]["records"][0]["owner"], "Unknown")
        self.assertEqual(by_type["278e_annual"]["records"][0]["owner_is_filer"], None)
        self.assertEqual(by_type["278t"]["records"][0]["row_pointer"],
                         f"{trade_path}#/transactions/0")
        self.assertEqual(by_type["278e_annual"]["records"][0]["row_pointer"],
                         f"{annual_path}#/holdings/0")

    def test_pdf_mismatch_is_recorded_without_reassigning_reporter(self) -> None:
        report = self.report("conflict", "278t")
        self.write_extraction(report, TRADE_PARSER, {
            "schema_version": TRADE_SCHEMA, "pdf_filer_name": "Different, Bob",
            "transactions": [], "quarantined": []})
        result = build_filer_reported_index(self.coverage(report), self.root,
                                            coverage_sha256="b" * 64)
        self.assertEqual(result["reports"][0]["filer_reported_name"], "Example, Ada")
        self.assertEqual(result["reports"][0]["filer_attribution"], "pdf_page_name_conflict")

    def test_modified_extraction_binding_is_rejected(self) -> None:
        report = self.report("changed", "278t")
        path = self.write_extraction(report, TRADE_PARSER, {
            "schema_version": TRADE_SCHEMA, "transactions": [], "quarantined": []})
        value = json.loads((self.root / path).read_text(encoding="utf-8"))
        value["source_sha256"] = "c" * 64
        (self.root / path).write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not bound"):
            build_filer_reported_index(self.coverage(report), self.root,
                                       coverage_sha256="b" * 64)


if __name__ == "__main__":
    unittest.main()
