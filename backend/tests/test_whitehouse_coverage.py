import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "whitehouse_coverage_script", ROOT / "scripts/whitehouse_coverage.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class WhiteHouseCoverageReportTests(unittest.TestCase):
    def test_each_public_link_has_an_explicit_review_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = "wh-url:" + "a" * 24, "wh-url:" + "b" * 24
            url = "https://www.whitehouse.gov/wp-content/uploads/2026/09/a.pdf"
            index = {"schema_version": script.INDEX_SCHEMA,
                     "page_sha256": "c" * 64, "report_link_count": 2,
                     "reports": [
                         {"source_document_id": first, "link_label": "Ada",
                          "filer_name_from_label": "Ada", "document_type_from_label": "278t",
                          "document_url": url},
                         {"source_document_id": second, "link_label": "Bee",
                          "filer_name_from_label": "Bee", "document_type_from_label": "278e_annual",
                          "document_url": url.replace("a.pdf", "b.pdf")},
                     ], "quarantine": [{"label": "bad href"}]}
            batch = {"schema_version": "whitehouse-public-disclosures-batch/v1",
                     "indexed_count": 2, "failures": [],
                     "reports": [{"document_id": first, "document_url": url,
                                  "sha256": "d" * 64}]}
            target = (root / "whitehouse/extractions" / first[7:] / ("d" * 64) /
                      f"{script.TRADE_PARSER_VERSION.replace('/', '-')}.json")
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"source_url": url, "source_sha256": "d" * 64,
                                          "parser_version": script.TRADE_PARSER_VERSION,
                                          "document_reasons": [], "quarantined": []}),
                              encoding="utf-8")
            result = script.build_coverage(index, batch, root)
            self.assertEqual(result["counts_by_review_state"], {
                "extracted_review_only": 1, "not_archived": 1})
            self.assertEqual(result["index_quarantine_count"], 1)
            self.assertEqual(result["production_qualified_fact_count"], 0)
            batch["reports"][0]["document_url"] = "https://evil.example/a.pdf"
            with self.assertRaisesRegex(ValueError, "not index-bound"):
                script.build_coverage(index, batch, root)

    def test_legacy_annual_stays_visible_until_current_parser_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "wh-url:" + "a" * 24
            url = "https://www.whitehouse.gov/wp-content/uploads/2026/09/annual.pdf"
            sha = "d" * 64
            index = {"schema_version": script.INDEX_SCHEMA, "page_sha256": "c" * 64,
                     "report_link_count": 1, "quarantine": [], "reports": [{
                         "source_document_id": document_id, "link_label": "Annual",
                         "filer_name_from_label": "Example, Ada",
                         "document_type_from_label": "278e_annual", "document_url": url}]}
            batch = {"schema_version": "whitehouse-public-disclosures-batch/v1",
                     "indexed_count": 1, "failures": [], "reports": [{
                         "document_id": document_id, "document_url": url, "sha256": sha}]}
            legacy = script.ANNUAL_PARSER_VERSIONS[1]
            directory = root / "whitehouse/extractions" / document_id[7:] / sha
            directory.mkdir(parents=True)
            legacy_path = directory / f"{legacy.replace('/', '-')}.json"
            legacy_path.write_text(json.dumps({"source_url": url, "source_sha256": sha,
                                               "parser_version": legacy,
                                               "document_reasons": [], "quarantined": []}),
                                   encoding="utf-8")
            result = script.build_coverage(index, batch, root)
            report = result["reports"][0]
            self.assertEqual(report["review_state"], "extracted_review_only")
            self.assertEqual(report["extraction_parser_version"], legacy)
            self.assertEqual(report["extraction_path"], legacy_path.relative_to(root).as_posix())

            current = script.ANNUAL_PARSER_VERSION
            failure = directory / f"{current.replace('/', '-')}.failure.json"
            failure.write_text(json.dumps({"source_url": url, "source_sha256": sha,
                                           "parser_version": current}), encoding="utf-8")
            blocked = script.build_coverage(index, batch, root)["reports"][0]
            self.assertEqual(blocked["review_state"], "extraction_quarantined")
            self.assertEqual(blocked["extraction_parser_version"], current)

    def test_legacy_trade_stays_visible_until_geometry_parser_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document_id = "wh-url:" + "a" * 24
            url = "https://www.whitehouse.gov/wp-content/uploads/2026/09/trade.pdf"
            sha = "d" * 64
            index = {"schema_version": script.INDEX_SCHEMA, "page_sha256": "c" * 64,
                     "report_link_count": 1, "quarantine": [], "reports": [{
                         "source_document_id": document_id, "link_label": "Trade",
                         "filer_name_from_label": "Example, Ada",
                         "document_type_from_label": "278t", "document_url": url}]}
            batch = {"schema_version": "whitehouse-public-disclosures-batch/v1",
                     "indexed_count": 1, "failures": [], "reports": [{
                         "document_id": document_id, "document_url": url, "sha256": sha}]}
            legacy = script.TRADE_PARSER_VERSIONS[1]
            directory = root / "whitehouse/extractions" / document_id[7:] / sha
            directory.mkdir(parents=True)
            legacy_path = directory / f"{legacy.replace('/', '-')}.json"
            legacy_path.write_text(json.dumps({"source_url": url, "source_sha256": sha,
                                               "parser_version": legacy,
                                               "document_reasons": [], "quarantined": []}),
                                   encoding="utf-8")
            visible = script.build_coverage(index, batch, root)["reports"][0]
            self.assertEqual(visible["extraction_parser_version"], legacy)
            current = script.TRADE_PARSER_VERSION
            failure = directory / f"{current.replace('/', '-')}.failure.json"
            failure.write_text(json.dumps({"source_url": url, "source_sha256": sha,
                                           "parser_version": current}), encoding="utf-8")
            blocked = script.build_coverage(index, batch, root)["reports"][0]
            self.assertEqual(blocked["review_state"], "extraction_quarantined")
            self.assertEqual(blocked["extraction_parser_version"], current)


if __name__ == "__main__":
    unittest.main()
