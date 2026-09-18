from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import make_review_template, parse_word_pages, promote_review


def word(text, x0, top, size=9):
    return {"text": text, "x0": x0, "top": top, "size": size}


def fixture_pages():
    words = [
        word("Periodic", 40, 40), word("Transaction", 90, 40), word("Report", 160, 40),
        word("Filing", 480, 40), word("ID", 510, 40), word("#20000001", 530, 40),
        word("SP", 66, 326), word("Example", 105, 326), word("Corp.", 150, 326),
        word("(EXM)", 190, 326), word("[ST]", 225, 326), word("P", 262, 326),
        word("08/12/2026", 327, 326), word("09/01/2026", 382, 326),
        word("$1,001", 446, 326), word("-", 475, 326), word("$15,000", 481, 326),
        word("Filing", 105, 354, 8.5), word("Status:", 135, 354, 8.5), word("New", 175, 354, 8.5),
        word("Other", 105, 393), word("Inc.", 145, 393), word("[ST]", 180, 393),
        word("S", 262, 393), word("(partial)", 270, 393),
        word("08/13/2026", 327, 393), word("09/02/2026", 382, 393),
        word("$15,001", 446, 393), word("-", 480, 393), word("$50,000", 486, 393),
    ]
    return [{"width": 612, "height": 792, "words": words}]


def amended_fixture_pages():
    words = [
        word("Periodic", 40, 40), word("Transaction", 90, 40), word("Report", 160, 40),
        word("Filing", 480, 40), word("ID", 510, 40), word("#20000001", 530, 40),
        word("2000140446", 25, 326), word("Example", 105, 326), word("Fund", 145, 326),
        word("[MF}", 180, 326), word("[OT]", 220, 326), word("S", 262, 326),
        word("06/03/2025", 327, 326), word("06/04/2025", 382, 326),
        word("$500,001", 446, 326), word("-", 490, 326), word("$1,000,000", 446, 337),
        word("Filing", 105, 354, 8.5), word("Status:", 135, 354, 8.5), word("Amended", 175, 354, 8.5),
        word("Description:", 105, 379, 8.5), word("Corrected", 165, 379, 8.5),
        word("transaction", 210, 379, 8.5),
        word("*", 25, 429, 8.2), word("For", 31, 429, 8.2), word("the", 46, 429, 8.2),
        word("complete", 60, 429, 8.2), word("list", 95, 429, 8.2), word("of", 108, 429, 8.2),
        word("asset", 117, 429, 8.2), word("type", 138, 429, 8.2),
        word("abbreviations,", 155, 429, 8.2),
        word("https://fd.house.gov/reference/asset-type-codes.aspx.", 251, 429, 8.2),
    ]
    return [{"width": 612, "height": 792, "words": words}]


def cross_page_fixture_pages():
    first = [
        word("Periodic", 40, 40), word("Transaction", 90, 40), word("Report", 160, 40),
        word("Filing", 480, 40), word("ID", 510, 40), word("#20000001", 530, 40),
        word("SP", 66, 700), word("Across", 105, 700), word("Pages", 150, 700),
        word("(ACR)", 195, 700), word("[ST]", 230, 700), word("P", 262, 700),
        word("08/12/2026", 327, 700), word("09/01/2026", 382, 700),
        word("$1,001", 446, 700), word("-", 475, 700), word("$15,000", 481, 700),
    ]
    second = [
        word("Filing", 105, 120, 8.5), word("Status:", 135, 120, 8.5), word("New", 175, 120, 8.5),
        word("Description:", 105, 135, 8.5), word("continued", 165, 135, 8.5),
        word("Other", 105, 326), word("Inc.", 145, 326), word("(OTH)", 180, 326),
        word("[ST]", 220, 326), word("S", 262, 326),
        word("08/13/2026", 327, 326), word("09/02/2026", 382, 326),
        word("$15,001", 446, 326), word("-", 480, 326), word("$50,000", 486, 326),
        word("Filing", 105, 354, 8.5), word("Status:", 135, 354, 8.5), word("New", 175, 354, 8.5),
    ]
    return [{"width": 612, "height": 792, "words": first},
            {"width": 612, "height": 792, "words": second}]


META = {"source_id": "house_clerk", "document_id": "20000001", "filing_type": "P",
        "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000001.pdf",
        "filer_name": "Hon. Example", "state_district": "CA01", "filing_year": 2026,
        "filed_date": "2026-09-15", "archive_path": "house_clerk/documents/2026/20000001/a.pdf"}


class HousePtrTests(unittest.TestCase):
    def test_rows_are_review_only_with_page_evidence(self):
        result = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=False)
        self.assertEqual(len(result["transactions"]), 2)
        first = result["transactions"][0]
        self.assertEqual((first["owner"], first["ticker"], first["instrument_type"]),
                         ("Spouse", "EXM", "Stock"))
        self.assertEqual((first["amount_low"], first["amount_high"]), (1001, 15000))
        self.assertEqual(first["evidence"]["page"], 1)
        self.assertEqual(first["verification_status"], "awaiting_manual_review")
        self.assertFalse(result["review"]["production_eligible"])
        self.assertIn("source_pdf_copy_permission_disabled", result["review"]["reasons"])

    def test_bad_identity_and_unknown_layout_fail_closed(self):
        bad_header = fixture_pages()
        bad_header[0]["words"] = [word for word in bad_header[0]["words"] if word["text"] != "#20000001"]
        with self.assertRaises(HouseIndexError):
            parse_word_pages(META, "a" * 64, bad_header, copy_allowed=True)
        bad_amount = fixture_pages()
        next(word for word in bad_amount[0]["words"] if word["text"] == "$1,001")["text"] = "unknown"
        with self.assertRaises(HouseIndexError):
            parse_word_pages(META, "a" * 64, bad_amount, copy_allowed=True)

    def test_pending_review_cannot_be_promoted(self):
        extraction = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=False)
        review = make_review_template(extraction)
        with self.assertRaises(HouseIndexError):
            promote_review(extraction, review)

    def test_cross_page_row_preserves_evidence_and_continuation(self):
        result = parse_word_pages(META, "b" * 64, cross_page_fixture_pages(), copy_allowed=True)
        first = result["transactions"][0]
        self.assertEqual(first["evidence"]["pages"], [1, 2])
        self.assertEqual(first["description"], "continued")
        self.assertEqual(result["transactions"][1]["evidence"]["pages"], [2])

    def test_amended_row_requires_explicit_revision_resolution(self):
        extraction = parse_word_pages(META, "c" * 64, amended_fixture_pages(), copy_allowed=True)
        amended = extraction["transactions"][0]
        self.assertEqual(amended["reported_transaction_id"], "2000140446")
        self.assertEqual(amended["filing_status"], "Amended")
        self.assertEqual((amended["amount_low"], amended["amount_high"]), (500001, 1000000))
        self.assertEqual(amended["description"], "Corrected transaction")
        review = make_review_template(extraction)
        review["identity"] = {"person_id": "house:BIO123", "evidence_url": "https://bioguide.congress.gov/test"}
        review["filing"]["filed_at"] = "2026-09-15T00:00:00Z"
        review["source_use_clearance"] = {"status": "approved", "reference": "legal-review-001"}
        review["review"] = {"decision": "approved", "reviewed_by": "Test Reviewer",
                            "reviewed_at": "2026-09-18T10:00:00Z", "note": "Fixture review"}
        review["rows"][0]["decision"] = "accepted"
        with self.assertRaises(HouseIndexError):
            promote_review(extraction, review)
        review["rows"][0]["revision"] = {
            "action": "replace_prior", "prior_record_id": "house-ptr:prior-record-001"}
        result = promote_review(extraction, review)
        self.assertEqual(result["audit"]["revision_count"], 1)
        self.assertEqual(result["revisions"][0]["prior_record_id"], "house-ptr:prior-record-001")

    def test_complete_review_promotes_only_accepted_rows(self):
        extraction = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=False)
        review = make_review_template(extraction)
        review["identity"] = {"person_id": "house:BIO123", "evidence_url": "https://bioguide.congress.gov/test"}
        review["filing"]["filed_at"] = "2026-09-15T00:00:00Z"
        review["source_use_clearance"] = {"status": "approved", "reference": "legal-review-001"}
        review["review"] = {"decision": "approved", "reviewed_by": "Test Reviewer",
                            "reviewed_at": "2026-09-18T10:00:00Z", "note": "Fixture review"}
        review["rows"][0].update(decision="accepted")
        review["rows"][1].update(decision="rejected", note="Fixture rejection")
        result = promote_review(extraction, review)
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["revisions"], [])
        self.assertEqual(result["transactions"][0]["verification_status"], "official_matched")
        self.assertEqual(result["audit"]["rejected_count"], 1)


if __name__ == "__main__":
    unittest.main()
