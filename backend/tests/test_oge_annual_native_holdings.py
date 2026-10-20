"""Known archived page fields and integration safeguards, not heldout OCR gold."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_annual import _Document, _Page
from test_oge_annual_candidate import base, source, inputs
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual import materialize_saved_annual_scan, _parse_rows, _reject_nested_aggregates
from unison_snapshot.oge_annual_scan_sections import observe_holding_pages
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate
from unison_snapshot.builder import build


FIXTURE = Path(__file__).parent / "fixtures/oge_annual_harris_2023_native_holding_pages.json"


class NativeAnnualHoldingTests(unittest.TestCase):
    def setup_inputs(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        doc, sha = raw["source_document_id"], raw["source_sha256"]
        observation = {"schema_version": "oge-278e-native-holding-pages/v1", "source_document_id": doc,
            "source_sha256": sha, "document_page_count": 15,
            "method": "selected_archived_native_tables_no_new_ocr", "pages": raw["pages"]}
        from unison_snapshot.oge_annual_scan_cover import SCHEMA as COVER_SCHEMA
        from unison_snapshot.oge_annual_scan_sections import SCHEMA as SECTION_SCHEMA
        cover = {"schema_version": COVER_SCHEMA, "document_id": doc, "source_sha256": sha,
            "page_count": 15, "native_text_sha256": "a"*64, "form_278e_text_observed": True,
            "annual_type_text_observed": True, "annual_year_text_candidates": [2022]}
        mapped = {row["page_number"]: row for row in raw["pages"]}
        sections = {"schema_version": SECTION_SCHEMA, "source_document_id": doc, "source_sha256": sha,
            "page_count": 15, "part7_slots": [], "part7_physical_slot_count": 0,
            "pages": [{"page_number": n, "section": mapped.get(n, {}).get("section"),
                       "page_text_sha256": mapped.get(n, {}).get("page_text_sha256", "a"*64)} for n in range(1, 16)],
            "section_pages": {part: [row["page_number"] for row in raw["pages"] if row["section"] == part]
                              for part in ("part2", "part5", "part6", "part7")}}
        meta = inputs(source())["archive_metadata_by_document"].popitem()[1]
        meta.update(document_id=doc, sha256=sha, filer_name="Harris, Kamala D",
                    archive_path=f"oge/annual/reports/{doc}/{sha}.pdf")
        return cover, sections, observation, meta

    def materialize(self, cover, sections, observation, meta):
        return materialize_saved_annual_scan(cover, sections, archive_metadata=meta,
            holding_page_observations=observation, archived_pdf_path=Path("original.pdf"))

    def test_fixed_known_pages_preserve_fields_parent_accounts_and_source_missing_values(self):
        cover, sections, obs, meta = self.setup_inputs()
        with patch("unison_snapshot.oge_annual_scan_sections.observe_holding_pages", return_value=obs):
            result = self.materialize(cover, sections, obs, meta)
        self.assertEqual(len(result["holdings"]), 45)
        counts = {page: sum(row["page_number"] == page for row in result["holdings"]) for page in (3, 6, 7, 8)}
        self.assertEqual(counts, {3: 11, 6: 18, 7: 12, 8: 4})
        known = {(row["page_number"], row["row_number"]): row for row in result["holdings"]}
        expected = {(3, "1.1"): ("International Fund", "Self", 15001, 50000),
                    (3, "2.5"): ("SFDCP Large Cap Eq S&P 500 Ind", "Self", 100001, 250000),
                    (6, "2.2"): ("Invesco Preferred ETF (PGX)", "Spouse", 1001, 15000),
                    (7, "3.12"): ("U.S. brokerage cash sweep account (cash)", "Spouse", 1001, 15000),
                    (8, "4.1"): ("U.S. bank #4 (cash)", "Joint", 50001, 100000)}
        for key, value in expected.items():
            self.assertEqual(tuple(known[key][field] for field in ("asset_name", "owner", "value_low", "value_high")), value)
            self.assertEqual(known[key]["saved_native_reference"]["source_sha256"], meta["sha256"])
        self.assertEqual(sum(row["reason"] == "parent_account_without_additive_value" for row in result["excluded"]), 5)
        # A blank extraction is not positive proof of an empty rendered cell.
        # These source-number-only slots remain unknown even though the fixed
        # visual reference shows unfilled template lines.
        self.assertEqual(sum(row.get("page_number") == 8 and row.get("asset_name") == ""
                             for row in result["quarantined"]), 15)
        self.assertEqual(sum(row["reason"] == "source_endnote_not_holding" for row in result["excluded"]), 1)
        missing = [row for row in result["quarantined"] if row.get("value_disclosure_status") == "explicitly_not_readily_ascertainable"]
        self.assertEqual(len(missing), 6)
        self.assertTrue(all("asset_value_description_conflicts_with_band" not in row["reasons"] for row in missing))

    def test_actual_overlay_consumes_saved_fields_and_does_not_invent_dates_or_partial_policy(self):
        cover, sections, obs, meta = self.setup_inputs()
        request = {"extractions": [], "archive_metadata_by_document": {meta["document_id"]: meta},
            "code_commit": "a"*40, "started_at": "2026-10-20T00:00:00Z",
            "saved_scan_observations_by_document": {meta["document_id"]: {"cover_observation": cover,
                "section_observation": sections, "holding_page_observations": obs, "archived_pdf_path": "original.pdf"}}}
        with patch("unison_snapshot.oge_annual_scan_sections.observe_holding_pages", return_value=obs):
            candidate, audit = overlay_oge_annual_candidate(base(), request)
        self.assertEqual(candidate["reported_holdings"], [])
        report = audit["reports"][0]
        self.assertEqual(report["report_reasons"], ["partial_report_policy_pending"])
        self.assertIsNone(report["filing_date_evidence"]["date"])
        observed = [row for row in report["row_qualification"] if "saved_native_reference" in row]
        self.assertTrue(observed)
        self.assertTrue(any("filing_date_unverified" in row["reasons"] for row in observed))
        self.assertTrue(build(candidate, generated_at="2026-10-20T00:00:00Z", allow_production=True,
                              allow_empty_production=True).manifest["snapshot_id"])

    def test_tampering_saved_values_source_page_and_section_cannot_bypass_original_replay(self):
        cover, sections, obs, meta = self.setup_inputs()
        for mutate in (lambda v: v["pages"][0]["tables"][0][5].__setitem__(3, "$1,001 - $15,000"),
                       lambda v: v.__setitem__("source_sha256", "f"*64),
                       lambda v: v["pages"][0].__setitem__("page_text_sha256", "f"*64)):
            changed = deepcopy(obs); mutate(changed)
            with patch("unison_snapshot.oge_annual_scan_sections.observe_holding_pages", return_value=obs):
                with self.assertRaisesRegex(OgeCatalogError, "original source replay"):
                    self.materialize(cover, sections, changed, meta)
        with self.assertRaisesRegex(OgeCatalogError, "require original"):
            materialize_saved_annual_scan(cover, sections, archive_metadata=meta, holding_page_observations=obs)
        changed = deepcopy(sections); changed["pages"][2]["section"] = "part5"
        with patch("unison_snapshot.oge_annual_scan_sections.observe_holding_pages", return_value=obs):
            with self.assertRaisesRegex(OgeCatalogError, "existing section inventory"):
                self.materialize(cover, changed, obs, meta)

    def test_observer_reads_only_selected_pages_and_rejects_unknown_pages(self):
        content = b"%PDF-1.7\nsynthetic selection contract\n%%EOF"
        sha = hashlib.sha256(content).hexdigest()
        page = _Page("Part 2: Filer's Employment Assets", [["#", "Description", "Value"]])
        class Unread:
            def extract_text(self): raise AssertionError("unselected page read")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"source.pdf"; path.write_bytes(content)
            with patch("pdfplumber.open", return_value=_Document([Unread(), page, Unread()])):
                out = observe_holding_pages(path, source_sha256=sha, document_id="a"*32, page_numbers=[2])
            self.assertEqual([row["page_number"] for row in out["pages"]], [2])
            for numbers in ([], [2, 2], [True], [0]):
                with self.assertRaises(OgeCatalogError):
                    observe_holding_pages(path, source_sha256=sha, document_id="a"*32, page_numbers=numbers)

    def test_ocr_number_only_is_still_unknown_and_parent_scopes_do_not_cross(self):
        header = ["#", "Description", "EIF", "Value", "Income Type", "Income Amount"]
        good, bad, excluded = _parse_rows("part2", "Self", 2, [header, ["1", "", "", "", "", ""]],
                                         2025, qualified=True)
        self.assertTrue(bad); self.assertEqual(excluded, [])
        rows = [header, ["", "Filer assets", "", "", "", ""], ["1", "Parent", "No", "", "", ""],
                ["", "Spouse assets", "", "", "", "", ""], ["1.1", "Child", "Yes", "$1,001 - $15,000", "", ""]]
        good, bad, excluded = _parse_rows("part2", "Self", 2, rows, 2025, qualified=True)
        _reject_nested_aggregates(good, bad, excluded, qualified=True)
        self.assertEqual(len(bad), 1)
        self.assertEqual(excluded, [])


if __name__ == "__main__":
    unittest.main()
