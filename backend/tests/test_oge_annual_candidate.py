"""Annual v3 integration contracts; synthetic pages are not source accuracy gold."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_annual import _Page, _Document
from test_oge_candidate import base as _base
from unison_snapshot.oge_annual import (QUALIFIED_PARSER_VERSION, extract_annual_pdf,
    read_annual_band, read_annual_cover_dates, _parse_rows, materialize_saved_annual_scan)
from unison_snapshot.oge_annual_ledger import build_oge_annual_shadow_ledger
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate, overlay_whitehouse_annual_candidate
from unison_snapshot.builder import build
from unison_snapshot.oge import OgeCatalogError


DOC = "a" * 32
URL = f"https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{DOC}/$FILE/example-annual.pdf"
CONTENT = b"%PDF-1.7\nsynthetic annual contract\n%%EOF"
SHA = hashlib.sha256(CONTENT).hexdigest()
HEADER = ["#", "Description", "EIF", "Value", "Income Type", "Income Amount"]
COVER = ("OGE Form 278e\nReport Type: Annual\nYear (Annual Report only): 2025\n"
         "Last Name First Name MI Position\nExample Ada Director\n"
         "Filer's Certification\nSignature: Ada Date: 06/01/2026\nAgency Ethics Official's Opinion\nDate: 06/02/2026\n"
         "OGE Received 06/03/2026")


def base():
    result = _base()
    result["meta"]["data_cutoff_at"] = "2026-10-19T00:00:00Z"
    return result


def source(value="$1,001 - $15,000", bad=False, official_unknown=False):
    pages = [_Page(COVER, []),
             _Page("Part 2: Filer's Employment Assets", [HEADER, ["1", "ALPHA Fund", "Yes", value, "", ""],
                 *([["2", "Book rights (value not readily ascertainable)", "No", "", "royalties", "$1,001 - $2,500"]] if official_unknown else [])]),
             _Page("Part 5: Spouse's Employment Assets", [HEADER, ["", "None", "", "", "", ""]]),
             _Page("Part 6: Other Assets and Income", [HEADER,
                   ["", "Filer assets", "", "", "", ""],
                   ["1", "BETA", "N/A", "$15,001 - $50,000", "", ""],
                   *([["2", "Unreadable", "N/A", "???", "", ""]] if bad else [])]),
             _Page("Part 7: Transactions", [["#", "Description", "Type", "Date", "Amount"],
                   ["", "Filer assets", "", "", ""], ["1", "GAMMA", "exchange", "05/01/2025", "$1,001 - $15,000"]])]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"source.pdf"
        path.write_bytes(CONTENT)
        with patch.dict(sys.modules, {"pdfplumber": type("PdfPlumber", (), {"open": staticmethod(lambda _: _Document(pages))})}):
            extracted = extract_annual_pdf(path, source_url=URL, source_sha256=SHA,
                                          expected_filer="Example, Ada", parser_version=QUALIFIED_PARSER_VERSION)
    extracted.update(source_document_id=DOC, evidence_archive_path=f"oge/annual/reports/{DOC}/{SHA}.pdf")
    return extracted


def inputs(extraction):
    return {"extractions": [extraction], "code_commit": "a"*40, "started_at": "2026-10-19T00:00:00Z",
            "archive_metadata_by_document": {DOC: {"schema_version": "oge-278e-annual-archive/v1",
                "document_id": DOC, "sha256": SHA, "document_url": URL, "filer_name": "Example, Ada",
                "agency": "Example Office", "position_title": "Director", "amended_label": None,
                "pending_final_oge_disposition": False, "archive_path": f"oge/annual/reports/{DOC}/{SHA}.pdf"}}}


def ledger(extraction):
    return build_oge_annual_shadow_ledger(extraction, run_id="annual-test", code_commit="a"*40,
                                          started_at="2026-10-19T00:00:00Z", qualified=True)


class AnnualCandidateTests(unittest.TestCase):
    def test_open_labels_preserve_exclusive_and_inclusive_operators(self):
        self.assertEqual(read_annual_band("Over $50,000,000"), {"low": 50000000, "high": None, "low_exclusive": True})
        self.assertEqual(read_annual_band("$1,000,000+"), {"low": 1000000, "high": None, "low_exclusive": False})
        self.assertNotIn("low_exclusive", read_annual_band("$1,001 - $15,000"))
        for raw in ["Over $2,000,000", "$1,000,000", "$15,001 - ???", "unknown"]:
            self.assertIsNone(read_annual_band(raw))

    def test_cover_roles_never_borrow_review_receipt_or_unbounded_paper_date(self):
        dates = read_annual_cover_dates(COVER, source_sha256=SHA)
        self.assertEqual(dates["filed_at"], "2026-06-01")
        missing = COVER.replace("06/01/2026", "unreadable")
        self.assertIsNone(read_annual_cover_dates(missing, source_sha256=SHA)["filed_at"])
        # An OCR-damaged agency label still delimits the filer block.
        damaged = missing.replace("Agency Ethics Official's Opinion", "Agency Ethics Ut1t1al Opm10n")
        self.assertIsNone(read_annual_cover_dates(damaged, source_sha256=SHA)["filed_at"])
        self.assertEqual(read_annual_cover_dates("Filer's Certification\nDate: 06/01/2026", source_sha256=SHA)["filed_at"], "2026-06-01")

    def test_last_cover_filer_block_is_not_required_to_have_agency_review(self):
        cover = COVER[:COVER.index("Agency Ethics Official")]
        extracted = source()
        extracted["cover_date_evidence"] = read_annual_cover_dates(cover, source_sha256=SHA)
        extracted["filing_date"] = extracted["cover_date_evidence"]["filed_at"]
        candidate, audit = overlay_oge_annual_candidate(base(), inputs(extracted))
        self.assertEqual(len(candidate["reported_holdings"]), 2)
        self.assertEqual(audit["reports"][0]["filing_date_evidence"]["date"], "2026-06-01")
        self.assertTrue(build(candidate, generated_at="2026-10-19T00:00:00Z", allow_production=True).manifest["snapshot_id"])
        for text in ["Date: 06/01/2026", "Filer's Certification\nDate: unreadable\nAgency\nDate: 06/02/2026",
                     "Filer's Certification\nDate: unreadable\nOGE Received 06/03/2026"]:
            self.assertIsNone(read_annual_cover_dates(text, source_sha256=SHA)["filed_at"])

    def test_owner_scope_gap_is_unresolved_but_same_page_noise_does_not_reset_it(self):
        state = {}
        _parse_rows("part2", "Self", 2, [HEADER, ["", "Spouse assets", "", "", "", ""]],
                    2025, qualified=True, scope_state=state)
        row = ["1", "ALPHA", "N/A", "$1,001 - $15,000", "", ""]
        same_page, _, _ = _parse_rows("part2", "Self", 2, [HEADER, row], 2025,
                                     qualified=True, scope_state=state)
        self.assertEqual(same_page[0]["owner"], "Spouse")
        self.assertEqual(same_page[0]["owner_context"]["basis"], "explicit_owner_heading")
        following, _, _ = _parse_rows("part2", "Self", 3, [HEADER, row], 2025,
                                     qualified=True, scope_state=state)
        self.assertNotEqual(following[0]["account_context"]["status"], "unknown")
        after_gap, _, _ = _parse_rows("part2", "Self", 5, [HEADER, row], 2025,
                                     qualified=True, scope_state=state)
        self.assertEqual(after_gap[0]["owner"], "Spouse")
        self.assertEqual(after_gap[0]["account_context"]["status"], "unknown")
        extraction = source()
        extraction["holdings"][0] = after_gap[0]
        decision = ledger(extraction)
        self.assertIn("account_scope_unresolved", decision["rows"][0]["reasons"])
        restored, _, _ = _parse_rows("part2", "Self", 6,
            [HEADER, ["", "Filer assets", "", "", "", ""], row], 2025,
            qualified=True, scope_state=state)
        self.assertEqual(restored[0]["owner"], "Self")
        self.assertEqual(restored[0]["account_context"]["status"], "not_observed")

    def test_parts_2_5_6_7_feed_existing_qualification_and_candidate(self):
        extraction = source()
        actual = ledger(extraction)
        self.assertEqual(set(actual["part_counts"]), {"part2", "part5", "part6", "part7"})
        self.assertTrue(actual["holding_coverage_complete"])
        self.assertEqual(actual["part_counts"]["part2"]["qualified"], 1)
        self.assertEqual(actual["part_counts"]["part5"]["excluded"], 1)
        self.assertEqual(actual["part_counts"]["part6"]["qualified"], 1)
        tx = next(row for row in actual["rows"] if row["observations"][0]["conditions"]["section"] == "part7")
        self.assertEqual(tx["reasons"], [])
        result, audit = overlay_oge_annual_candidate(base(), inputs(extraction))
        self.assertEqual(len(result["reported_holdings"]), 2)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(audit["added_transaction_count"], 1)
        self.assertEqual(audit["added_holding_count"], 2)
        self.assertTrue(build(result, generated_at="2026-10-19T00:00:00Z", allow_production=True).manifest["snapshot_id"])

    def test_bad_row_does_not_discard_good_qualification_or_choose_partial_policy(self):
        extraction = source(bad=True)
        actual = ledger(extraction)
        self.assertEqual(actual["part_counts"]["part2"]["qualified"], 1)
        self.assertEqual(actual["part_counts"]["part6"]["qualified"], 1)
        result, audit = overlay_oge_annual_candidate(base(), inputs(extraction))
        self.assertEqual(result["reported_holdings"], [])
        self.assertIn("holding_coverage_or_field_evidence_incomplete", audit["reports"][0]["report_reasons"])
        self.assertTrue(any("holding_value_unreadable" in row["reasons"] for row in audit["reports"][0]["row_qualification"]))

    def test_open_holdings_reach_candidate_without_fake_high_or_finite_marker(self):
        for raw, marker in [("Over $50,000,000", True), ("$50,000,000+", False)]:
            extraction = source(raw)
            result, _ = overlay_oge_annual_candidate(base(), inputs(extraction))
            row = next(row for row in result["reported_holdings"] if row["asset_name"] == "ALPHA Fund")
            self.assertEqual((row["value_low"], row["value_high"], row["value_low_exclusive"]), (50000000, None, marker))
            self.assertNotIn("value_low_exclusive", next(row for row in result["reported_holdings"] if row["asset_name"] == "BETA"))
            # Approved holding semantics must survive the public builder, not
            # merely the annual candidate representation.
            published = build(result, generated_at="2026-10-19T00:00:00Z", allow_production=True)
            self.assertEqual(published.manifest["amount_policy"], "bounded-and-open-separate/v1")
            changed = deepcopy(extraction)
            changed["holdings"][0].pop("value_low_exclusive")
            self.assertEqual(ledger(changed)["part_counts"]["part2"]["qualified"], 0)

    def test_existing_formal_ids_and_values_remain_and_changed_version_cannot_duplicate(self):
        first, _ = overlay_oge_annual_candidate(base(), inputs(source()))
        second, _ = overlay_oge_annual_candidate(first, inputs(source()))
        self.assertEqual(second, first)
        changed = source()
        changed["holdings"].reverse()
        result, audit = overlay_oge_annual_candidate(first, inputs(changed))
        self.assertEqual(result, first)
        self.assertIn("legacy_annual_row_binding_unresolved", audit["reports"][0]["report_reasons"])

    def test_parent_aggregates_and_account_titles_are_preserved_without_double_counting(self):
        rows = [HEADER, ["1", "Brokerage Account", "N/A", "$100,001 - $250,000", "", ""],
                ["1.1", "ALPHA", "N/A", "$1,001 - $15,000", "", ""]]
        parsed, bad, excluded = _parse_rows("part2", "Self", 2, rows, 2025, qualified=True, preserve_valued_unreadable=True)
        from unison_snapshot.oge_annual import _reject_nested_aggregates
        kept = _reject_nested_aggregates(parsed, bad, excluded, qualified=True)
        self.assertEqual([row["asset_name"] for row in kept], ["ALPHA"])
        self.assertEqual(excluded[0]["reason"], "parent_account_aggregate_not_additive")
        self.assertEqual(kept[0]["hierarchy_evidence"]["parent_refs"][0]["asset_name"], "Brokerage Account")

    def test_existing_overlay_entry_actually_consumes_optional_oge_input(self):
        with tempfile.TemporaryDirectory() as directory:
            result, audit = overlay_whitehouse_annual_candidate(base(), Path(directory), oge_annual_input=inputs(source()))
        self.assertEqual(len(result["reported_holdings"]), 2)
        self.assertEqual(audit["oge_annual"]["added_holding_count"], 2)

    def test_saved_scan_rows_feed_same_ledger_without_new_ocr_or_visual_date_copy(self):
        from unison_snapshot.oge_annual_scan_cover import SCHEMA as COVER_SCHEMA
        from unison_snapshot.oge_annual_scan_sections import SCHEMA as SECTION_SCHEMA
        cover = {"schema_version": COVER_SCHEMA, "document_id": DOC, "source_sha256": SHA,
                 "page_count": 3, "native_text_sha256": "b"*64, "form_278e_text_observed": True,
                 "annual_type_text_observed": True, "annual_year_text_candidates": [2025]}
        pages = [{"page_number": 1, "page_text_sha256": "b"*64, "section": None},
                 {"page_number": 2, "page_text_sha256": "c"*64, "section": "part7"},
                 {"page_number": 3, "page_text_sha256": "d"*64, "section": "part2"}]
        slots = []
        for number, raw in [("1", "1. (Spouse) ALPHA Exchange 05/01/2025 Over $50,000,000"), ("2", "2.")]:
            top = float(100 + len(slots)*20)
            identifier = "oge-annual-scan-slot:" + hashlib.sha256(f"{SHA}|2|{number}|{top}".encode()).hexdigest()[:24]
            slots.append({"slot_id": identifier, "page_number": 2, "printed_number": number,
                "source_page_text_sha256": "c"*64, "raw_lines": [raw], "top": top, "bottom": top+10})
        sections = {"schema_version": SECTION_SCHEMA, "source_document_id": DOC, "source_sha256": SHA,
                    "page_count": 3, "pages": pages, "part7_slots": slots, "part7_physical_slot_count": 2,
                    "section_pages": {"part2": [3], "part5": [], "part6": [], "part7": [2]}}
        metadata = inputs(source())["archive_metadata_by_document"][DOC]
        with patch("unison_snapshot.ocr_geometry.ocr_pdf_pages", side_effect=AssertionError("new OCR forbidden")):
            result = materialize_saved_annual_scan(cover, sections, archive_metadata=metadata)
            raw_input = inputs(source())
            raw_input["extractions"] = []
            raw_input["saved_scan_observations_by_document"] = {DOC: {"cover_observation": cover, "section_observation": sections}}
            candidate, audit = overlay_oge_annual_candidate(base(), raw_input)
        self.assertEqual(candidate["transactions"], [])
        self.assertTrue(any(row["section"] == "part7" for row in audit["reports"][0]["row_qualification"]))
        self.assertEqual(len(result["transactions"]), 1)
        row = result["transactions"][0]
        self.assertEqual((row["owner"], row["asset_name"], row["transaction_type"]), ("Spouse", "ALPHA", "exchange"))
        self.assertEqual((row["amount_low"], row["amount_high"], row["amount_low_exclusive"]), (50000000, None, True))
        self.assertIsNone(result["filing_date"])
        decision = ledger(result)
        self.assertFalse(decision["holding_coverage_complete"])
        self.assertEqual(decision["rows"][0]["reasons"], ["filing_date_unverified"])
        self.assertTrue(any("saved_section_summary_has_no_field_rows" in row["reasons"] for row in decision["rows"]))
        bad = deepcopy(sections)
        bad["part7_slots"][0]["printed_number"] = "2"
        with self.assertRaises(OgeCatalogError):
            materialize_saved_annual_scan(cover, bad, archive_metadata=metadata)

    def test_fixed_visual_date_alone_cannot_authorize_filing_date(self):
        from unison_snapshot.oge_annual_ledger import annual_filing_date_evidence
        from unison_snapshot.oge_annual_scan_dates import SCHEMA as DATE_SCHEMA, FIXED_COVERS
        doc = next(iter(FIXED_COVERS))
        sha = FIXED_COVERS[doc][0]
        extraction = source()
        extraction.update(source_document_id=doc, source_sha256=sha)
        extraction.pop("cover_date_evidence")
        observation = {"schema_version": DATE_SCHEMA, "source_document_id": doc, "source_sha256": sha,
            "cover_kind": "annual_form", "page_count": extraction["page_count"],
            "cover_page_text_sha256": extraction["source_coverage_evidence"]["pages"][0]["page_text_sha256"],
            "date_blocks": {"filer_signature": {"page_number": 1, "date": "2023-05-08",
                "page_text_sha256": extraction["source_coverage_evidence"]["pages"][0]["page_text_sha256"],
                                               "embedded_text_raw": "Date: 05/08/2023"}}}
        self.assertIsNone(annual_filing_date_evidence(extraction, observation)["date"])
        observation["date_blocks"]["filer_signature"]["embedded_text_raw"] = "Filer's Certification Signature Ada Date: 05/08/2023"
        self.assertEqual(annual_filing_date_evidence(extraction, observation)["date"], "2023-05-08")
        wrong = deepcopy(observation)
        wrong["date_blocks"]["filer_signature"]["page_text_sha256"] = "f"*64
        with self.assertRaisesRegex(ValueError, "cover page binding"):
            annual_filing_date_evidence(extraction, wrong)
        # Missing native filer date may use role-bound saved raw text, not the
        # review-only normalized date value.
        extraction["cover_date_evidence"] = read_annual_cover_dates(
            COVER.replace("06/01/2026", "unreadable"), source_sha256=sha)
        extraction["filing_date"] = None
        self.assertEqual(annual_filing_date_evidence(extraction, observation)["date"], "2023-05-08")

    def test_source_page_and_report_version_constraints_do_not_pick_a_winner(self):
        extraction = source()
        changed = deepcopy(extraction)
        changed["holdings"][0]["page_number"] = 999
        self.assertEqual(ledger(changed)["unlocated_source_issues"], [changed["holdings"][0]])
        self.assertFalse(ledger(changed)["holding_coverage_complete"])
        request = inputs(extraction)
        second = deepcopy(extraction)
        second.update(source_document_id="b"*32, source_sha256="c"*64,
                      evidence_archive_path=f"oge/annual/reports/{'b'*32}/{'c'*64}.pdf")
        second["cover_date_evidence"] = read_annual_cover_dates(COVER, source_sha256="c"*64)
        second["source_coverage_evidence"]["source_sha256"] = "c"*64
        request["extractions"].append(second)
        other_meta = deepcopy(request["archive_metadata_by_document"][DOC])
        other_meta.update(document_id="b"*32, sha256="c"*64, archive_path=second["evidence_archive_path"])
        request["archive_metadata_by_document"]["b"*32] = other_meta
        candidate, audit = overlay_oge_annual_candidate(base(), request)
        self.assertEqual(candidate["reported_holdings"], [])
        self.assertTrue(all("annual_report_version_relationship_unresolved" in report["report_reasons"]
                            for report in audit["reports"]))
        cutoff = base()
        cutoff["meta"]["data_cutoff_at"] = "2026-05-01T00:00:00Z"
        candidate, audit = overlay_oge_annual_candidate(cutoff, inputs(extraction))
        self.assertEqual(candidate["reported_holdings"], [])
        self.assertIn("filing_exceeds_cutoff", audit["reports"][0]["report_reasons"])

    def test_partial_flag_and_missing_parts_cannot_claim_complete_coverage(self):
        extraction = source()
        extraction["source_coverage_evidence"]["pages"] = [row for row in extraction["source_coverage_evidence"]["pages"]
                                                        if row.get("section") != "part6"]
        extraction["holding_coverage_status"] = "complete"
        result, audit = overlay_oge_annual_candidate(base(), inputs(extraction))
        self.assertEqual(result["reported_holdings"], [])
        self.assertIn("holding_coverage_or_field_evidence_incomplete", audit["reports"][0]["report_reasons"])

    def test_existing_oge_holdings_survive_the_whitehouse_review_refresh(self):
        previous, _ = overlay_oge_annual_candidate(base(), inputs(source()))
        refreshed = deepcopy(previous)
        refreshed["reported_holdings"] = []
        refreshed["people"] = []
        with tempfile.TemporaryDirectory() as directory, patch(
                "unison_snapshot.whitehouse_annual_candidate._overlay_whitehouse_annual_candidate",
                return_value=(refreshed, {})):
            actual, _ = overlay_whitehouse_annual_candidate(previous, Path(directory))
        self.assertEqual(actual, previous)

    def test_existing_cli_uses_optional_oge_input_and_v3_extractor_is_bounded(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from oge_annual_review import extract_archived_reports
        from overlay_whitehouse_annual_candidate import main
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, request, audit = root/"candidate.json", root/"input.json", root/"audit.json"
            data.write_text(json.dumps(base()), encoding="utf-8")
            request.write_text(json.dumps(inputs(source())), encoding="utf-8")
            with redirect_stdout(io.StringIO()), patch.object(sys, "argv", ["overlay", "--candidate", str(data), "--review-root", str(root),
                                           "--audit-out", str(audit), "--oge-annual-input", str(request)]):
                main()
            self.assertEqual(len(json.loads(data.read_text(encoding="utf-8"))["reported_holdings"]), 2)
            self.assertEqual(json.loads(audit.read_text(encoding="utf-8"))["oge_annual"]["added_holding_count"], 2)
            with self.assertRaisesRegex(OgeCatalogError, "fixed document allowlist"):
                extract_archived_reports(root, root, parser_version=QUALIFIED_PARSER_VERSION)


if __name__ == "__main__":
    unittest.main()
