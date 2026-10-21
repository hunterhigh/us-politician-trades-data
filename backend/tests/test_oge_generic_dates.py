"""Generic cover roles/open amounts; synthetic cases are not source gold."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_oge_reports import record, Client
from unison_snapshot.oge import OgeCatalogError, OgeSourceConfig
from unison_snapshot.oge_reports import (
    GENERIC_PARSER_VERSION, PARSER_VERSION, archive_direct_pdf,
    _extract_pdf, parse_archived_pdf, parse_table_rows, read_278t_cover_dates,
)


SHA = "a" * 64
ROW = ["1", "Example Corp", "Purchase", "06/01/2026", "No", "$1,001 - $15,000"]


class GenericDateTests(unittest.TestCase):
    def dates(self, text):
        return read_278t_cover_dates(text, source_sha256=SHA)

    def test_filer_agency_other_and_oge_dates_have_distinct_roles(self):
        text = ("Electronic Signature - certification\n"
                "/s/ ADA [electronically signed on 06/02/2026 by ADA]\n"
                "Agency Ethics Official's Opinion\n"
                "/s/ REVIEWER [electronically signed on 06/04/2026 by REVIEWER]\n"
                "Other review conducted by\nDate: 06/03/2026\n"
                "U.S. Office of Government Ethics Certification\nDate: 06/05/2026\n"
                "Date Received: 06/06/2026\nData Revised 06/07/2026")
        out = self.dates(text)
        self.assertEqual(out["filed_at"], "2026-06-02")
        self.assertEqual([r["role"] for r in out["observations"]],
                         ["filer_signature", "agency_review", "other_review",
                          "oge_certification", "receipt", "revision_date"])
        for observed in out["observations"]:
            a, b = observed["text_span"]
            self.assertEqual(text[a:b], observed["raw_date"])

    def test_plain_paper_date_and_separate_signature_date_row(self):
        for text in ["Filer's Certification\nSignature: Ada Example Date: 6/2/2026\nAgency Review\nDate: 6/5/2026",
                     "Filer's Certification\nSignature: Ada Example Date (mm/dd/yyyy): 6/2/2026",
                     "Filer’s Certification\nSignature Date (mm/dd/yyyy)\nAda Example June 2, 2026\nAgency Review\nDate: 6/5/2026",
                     "Signature of Filer: Ada Example 2026-06-02"]:
            with self.subTest(text=text):
                self.assertEqual(self.dates(text)["filed_at"], "2026-06-02")

    def test_missing_filer_does_not_borrow_other_roles(self):
        text = "Agency Review\nDate: 6/5/2026\nOGE Certification\nDate: 6/7/2026"
        self.assertIsNone(self.dates(text)["filed_at"])

    def test_electronic_label_inside_agency_block_is_not_a_filer_reset(self):
        text = "Agency Review\nElectronic Signature\n/s/ B [electronically signed on 06/04/2026 by B]"
        out = self.dates(text)
        self.assertIsNone(out["filed_at"])
        self.assertEqual(out["observations"][0]["role"], "agency_review")

    def test_conflicting_and_invalid_filer_dates_are_not_chosen(self):
        for text, reason in [("Filer's Certification\nDate: 6/2/2026\nDate: 6/3/2026", "filer_signature_date_ambiguous"),
                             ("Filer's Certification\nDate: 2/30/2026\nDate: 6/2/2026", "filer_signature_date_invalid")]:
            out = self.dates(text)
            self.assertIsNone(out["filed_at"])
            self.assertIn(reason, out["document_reasons"])

    def test_short_year_unlabelled_narrative_and_transaction_dates_are_not_filer_dates(self):
        for text in ["Filer's Certification\nDate: 6/2/26", "Filer's Certification\nAssets acquired 6/2/2026",
                     "Filer's Certification\nTransactions\nDate 6/2/2026"]:
            self.assertIsNone(self.dates(text)["filed_at"])

    def test_amendment_signature_is_not_original_report_filing(self):
        out = self.dates("AMENDMENT TO PERIODIC TRANSACTION REPORT\nFiler's Certification\nDate: 6/2/2026")
        self.assertIsNone(out["filed_at"])
        self.assertEqual(out["observations"][0]["role"], "amendment_filer_signature")

    def parse(self, extracted, *, generic):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = archive_direct_pdf(root, record(), OgeSourceConfig(True, True),
                                          client=Client(), retrieved_at="2026-09-20T00:00:00Z")
            path = root / "oge/reports" / metadata["document_id"] / f"{metadata['sha256']}.json"
            with patch("unison_snapshot.oge_reports._extract_pdf", return_value=extracted) as mocked:
                out = parse_archived_pdf(root, path, generic=generic)
            return out, mocked.call_args

    def test_opt_in_generic_is_real_cover_and_table_path_with_undisclosed_owner(self):
        cover = {"page_number": 1, "text": "Filer's Certification\nDate: 6/2/2026", "document_page_count": 2}
        out, called = self.parse(("whole body 06/30/2026", [(2, ROW)], [(2, ROW)], cover), generic=True)
        self.assertEqual(out["parser_version"], GENERIC_PARSER_VERSION)
        self.assertEqual(out["filed_at"], "2026-06-02")
        self.assertTrue(out["evidence_complete"])
        self.assertEqual(out["transactions"][0]["owner"], "Undisclosed")
        self.assertEqual(out["document_page_count"], 2)
        self.assertTrue(called.kwargs["include_cover"])
        self.assertEqual(out["source_rows"], [{"page_number": 2, "cells": ROW}])

    def test_legacy_two_three_tuple_and_default_output_remain_legacy(self):
        for value in [("", [(2, ROW)]), ("", [(2, ROW)], [(2, ROW)])]:
            old, called = self.parse(value, generic=False)
            self.assertEqual(old["parser_version"], PARSER_VERSION)
            self.assertEqual(old["transactions"][0]["owner"], "Self")
            self.assertNotIn("cover_date_evidence", old)
            self.assertNotIn("include_cover", called.kwargs)
            current, _ = self.parse(value, generic=True)
            self.assertIsNone(current["filed_at"])
            self.assertIn("cover_text_not_separately_observed", current["document_reasons"])

    def test_cover_only_not_whole_document_signature_is_authoritative(self):
        extracted = ("Filer's Signature electronically signed on 06/02/2026", [(2, ROW)], [(2, ROW)],
                     {"page_number": 1, "text": "No readable filer date", "document_page_count": 2})
        out, _ = self.parse(extracted, generic=True)
        self.assertIsNone(out["filed_at"])

    def test_generic_retains_explicit_heading_before_first_numbered_row(self):
        heading = ["", "Spouse assets", "", "", "", ""]
        cover = {"page_number": 1, "text": "Filer's Certification\nDate: 6/2/2026", "document_page_count": 2}
        out, _ = self.parse(("", [(2, ROW)], [(2, heading), (2, ROW)], cover), generic=True)
        self.assertEqual(out["transactions"][0]["owner"], "Spouse")
        self.assertNotIn("owner_disclosure_basis", out["transactions"][0])

    def test_generic_does_not_inherit_fixed_source_facts_or_corrections(self):
        cells = [*ROW[:2], "salo", *ROW[3:]]
        cover = {"page_number": 1, "text": "Agency Review\nDate: 6/2/2026", "document_page_count": 2}
        with patch("unison_snapshot.oge_reports._is_fixed_trump_september_source", return_value=True):
            out, _ = self.parse(("", [(2, cells)], [(2, cells)], cover), generic=True)
        self.assertEqual(out["parser_version"], GENERIC_PARSER_VERSION)
        self.assertIsNone(out["filed_at"])
        self.assertNotIn("filing_date_evidence", out)
        self.assertEqual(out["transactions"], [])
        self.assertIn("transaction_type_unsupported", out["quarantined"][0]["reasons"])
        self.assertNotIn("type_ocr_correction", out["quarantined"][0])

    def test_cover_is_from_the_same_single_extraction_pass(self):
        calls = []
        class Page:
            def __init__(self, text):
                self.text = text
            def extract_text(self):
                calls.append(self.text)
                return self.text
            def extract_words(self, **kwargs):
                return []
            def extract_tables(self):
                return []
        class Document:
            pages = [Page("filer cover"), Page("body text")]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        with patch.dict(sys.modules, {"pdfplumber": SimpleNamespace(open=lambda _: Document())}):
            extracted = _extract_pdf(Path("same.pdf"), include_cover=True, include_inventory=True)
        self.assertEqual(calls, ["filer cover", "body text"])
        self.assertEqual(extracted, ("filer cover\nbody text", [], [],
                         {"page_number": 1, "text": "filer cover", "document_page_count": 2}))

    def test_open_amount_opt_in_has_exact_threshold_and_old_id_algorithm(self):
        for amount, threshold in [("Over $1,000,000", 1000000), ("Over $50,000,000", 50000000)]:
            row = [*ROW[:5], amount]
            old, old_bad = parse_table_rows([(2, row)], source_sha=SHA)
            self.assertEqual(old, [])
            self.assertIn("amount_range_unsupported", old_bad[0]["reasons"])
            good, bad = parse_table_rows([(2, row)], source_sha=SHA, allow_open_amounts=True)
            self.assertEqual(bad, [])
            self.assertEqual(good[0]["extraction_id"], old_bad[0]["extraction_id"])
            self.assertEqual((good[0]["amount_low"], good[0]["amount_high"], good[0]["amount_low_exclusive"]),
                             (threshold, None, True))

    def test_amount_owner_and_direction_qualification_remain_independent(self):
        cells = [*ROW[:2], "Transfer", ROW[3], ROW[4], "Over $1,000,000"]
        good, bad = parse_table_rows([(2, ["", "Spouse assets", "", "", "", ""]), (2, cells)],
                                    source_sha=SHA, default_owner="Undisclosed", allow_open_amounts=True)
        self.assertEqual(good, [])
        self.assertEqual(bad[0]["owner"], "Spouse")
        self.assertEqual(bad[0]["reasons"], ["transaction_type_unsupported"])
        normal, _ = parse_table_rows([(2, ROW)], source_sha=SHA, allow_open_amounts=True)
        self.assertNotIn("amount_low_exclusive", normal[0])
        for raw in ["Over $2,000,000", "$1,000,000", "Over $1,000,000 approx", "- $1,000,000"]:
            good, _ = parse_table_rows([(2, [*ROW[:5], raw])], source_sha=SHA, allow_open_amounts=True)
            self.assertEqual(good, [])

    def test_source_account_title_is_optional_stable_identity_input(self):
        original, _ = parse_table_rows([(2, ROW)], source_sha=SHA)
        explicit_none, _ = parse_table_rows([(2, ROW)], source_sha=SHA, account_title=None)
        self.assertEqual(original, explicit_none)
        first, _ = parse_table_rows([(2, ROW)], source_sha=SHA, account_title="Investment account #1")
        second, _ = parse_table_rows([(2, ROW)], source_sha=SHA, account_title="Investment account #2")
        again, _ = parse_table_rows([(2, ROW)], source_sha=SHA, account_title="Investment account #1")
        self.assertEqual(first, again)
        self.assertEqual(first[0]["asset_name"], original[0]["asset_name"])
        self.assertEqual(first[0]["account_title"], "Investment account #1")
        self.assertEqual(len({r[0]["extraction_id"] for r in [original, first, second]}), 3)
        for invalid in ["", " ", 1, False]:
            with self.assertRaises(OgeCatalogError):
                parse_table_rows([(2, ROW)], source_sha=SHA, account_title=invalid)


if __name__ == "__main__":
    unittest.main()
