import unittest

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper import (
    PAPER_EXTRACTION_SCHEMA, parse_paper_word_pages,
)
from unison_snapshot.senate_reports import PAPER_PAGE_MANIFEST_SCHEMA, PARSER_VERSION


DOCUMENT = "11111111-1111-4111-8111-111111111111"
REPORT = {
    "source_id": "senate_efd",
    "access_method": "paper_ptr",
    "document_id": DOCUMENT,
    "document_url": f"https://efdsearch.senate.gov/search/view/paper/{DOCUMENT}/",
    "filer_name": "Sample Senator",
    "portal_listed_date": "2026-09-17",
    "report_label_date": "2026-09-17",
    "report_amendment_number": None,
}
MANIFEST = {
    "schema_version": PAPER_PAGE_MANIFEST_SCHEMA,
    "source_id": "senate_efd",
    "document_id": DOCUMENT,
    "entrypoint_source_sha256": "a" * 64,
    "parser_version": PARSER_VERSION,
    "page_count": 2,
    "manifest_sha256": "b" * 64,
}


def word(text, x, y, confidence=98, width=35, height=35):
    return {"text": text, "left": x, "top": y, "width": width,
            "height": height, "confidence": confidence}


def page(number, words=None):
    return {"page_number": number, "width": 3400, "height": 4400,
            "words": words or []}


class SenatePaperTests(unittest.TestCase):
    def test_extracts_geometry_bound_row_and_skips_printed_example(self):
        words = [
            # The form's printed example is above the bounded data region.
            word("(S)", 650, 1930), word("Example", 760, 1930),
            word("2/1/1X", 1740, 1930), word("X", 1410, 1930),
            word("X", 1960, 1930),
            word("(S)", 650, 2280), word("Acme", 760, 2280),
            word("Inc", 890, 2280), word("(Stock)", 980, 2280),
            word("(ACME)", 1140, 2280),
            word("09/01/26", 1730, 2280),
            word("X", 1412, 2280, confidence=55),
            word("X", 1963, 2280, confidence=55),
        ]
        result = parse_paper_word_pages(REPORT, MANIFEST, [page(1), page(2, words)])

        self.assertEqual(result["schema_version"], PAPER_EXTRACTION_SCHEMA)
        self.assertEqual((result["eligible_transaction_count"],
                          result["quarantined_transaction_count"]), (1, 0))
        row = result["transactions"][0]
        self.assertEqual((row["transaction_date"], row["owner_raw"]),
                         ("2026-09-01", "Spouse"))
        self.assertEqual((row["ticker_raw"], row["asset_type_raw"]), ("ACME", "Stock"))
        self.assertEqual((row["transaction_type"], row["amount_raw"]),
                         ("purchase", "$1,001 - $15,000"))

    def test_keeps_uncertain_row_in_quarantine(self):
        words = [
            word("(S)", 650, 2280, confidence=70),
            word("Unclear", 760, 2280, confidence=70),
            word("09/01/26", 1730, 2280),
            word("X", 1510, 2280, confidence=50),
        ]
        result = parse_paper_word_pages(REPORT, MANIFEST, [page(1), page(2, words)])

        self.assertEqual((result["eligible_transaction_count"],
                          result["quarantined_transaction_count"]), (0, 1))
        reasons = result["transactions"][0]["quarantine_reasons"]
        self.assertIn("paper_asset_ocr_low_confidence", reasons)
        self.assertIn("paper_amount_mark_ambiguous", reasons)

    def test_rejects_missing_page(self):
        with self.assertRaises(SenateEfdError):
            parse_paper_word_pages(REPORT, MANIFEST, [page(1)])


if __name__ == "__main__":
    unittest.main()
