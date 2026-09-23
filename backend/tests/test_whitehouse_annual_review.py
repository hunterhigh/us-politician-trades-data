"""Annual review materialization preserves filer attribution and row fates."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_oge_278e_audit import _annual
from unison_snapshot.oge_278e_public import PARSER_VERSION
from unison_snapshot.whitehouse_annual_review import build_annual_review


class WhiteHouseAnnualReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def fixture(self, extraction: dict) -> tuple[dict, Path]:
        url = extraction["source_url"]
        document_id = "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24]
        relative = (Path("whitehouse/extractions") / document_id[7:] /
                    extraction["source_sha256"] /
                    f"{PARSER_VERSION.replace('/', '-')}.json")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(extraction), encoding="utf-8")
        coverage = {
            "schema_version": "whitehouse-public-coverage/v1",
            "source_id": "whitehouse_public_disclosures", "report_link_count": 1,
            "reports": [{"document_id": document_id, "document_url": url,
                         "filer_name_from_label": "Example, Ada",
                         "document_type_from_label": "278e_annual",
                         "archive_sha256_versions": [extraction["source_sha256"]],
                         "review_state": "extracted_review_only"}],
        }
        return coverage, path

    def test_unknown_part6_owner_is_attributed_to_filer_without_claiming_ownership(self) -> None:
        extraction = _annual()
        extraction["explicit_empty_sections"] = ["part2", "part5", "part7"]
        extraction["holdings"][0].update(section="part6", owner="Unknown")
        coverage, _ = self.fixture(extraction)
        result = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        self.assertEqual((result["report_count"], result["holding_count"]), (1, 1))
        self.assertEqual(result["source_eligible_holding_count"], 1)
        row = result["holdings"][0]
        self.assertEqual(row["filer_reported_name"], "Example, Ada")
        self.assertEqual(row["asset_owner"], "Unknown")
        self.assertIsNone(row["owner_is_filer"])
        self.assertTrue(row["source_holdings_eligible"])
        self.assertEqual(result["production_status"],
                         "review_only_pending_identity_versions_and_snapshot_gate")

    def test_quarantined_asset_blocks_entire_report_without_losing_parsed_row(self) -> None:
        extraction = _annual()
        extraction["printed_row_count"] = 2
        extraction["quarantined"] = [{"section": "part6", "page_number": 4,
                                     "row_number": "1", "reasons": ["amount_unreadable"]}]
        coverage, _ = self.fixture(extraction)
        result = build_annual_review(coverage, self.root, coverage_sha256="b" * 64)
        self.assertEqual(result["holding_count"], 1)
        self.assertEqual(result["source_eligible_holding_count"], 0)
        self.assertEqual(result["reports"][0]["quarantined_asset_count"], 1)
        self.assertIn("asset_rows_quarantined",
                      result["reports"][0]["holding_blocking_reasons"])

    def test_modified_archive_binding_is_rejected(self) -> None:
        extraction = _annual()
        coverage, path = self.fixture(extraction)
        changed = deepcopy(extraction)
        changed["source_sha256"] = "c" * 64
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not bound"):
            build_annual_review(coverage, self.root, coverage_sha256="b" * 64)


if __name__ == "__main__":
    unittest.main()
