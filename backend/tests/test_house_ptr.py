from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import (_words_from_tesseract_tsv, make_review_template,
                                      parse_word_pages, promote_review, qualify_automatic)


def word(text, x0, top, size=9):
    return {"text": text, "x0": x0, "top": top, "size": size}


def ocr_word(text, x0, top, size=9, confidence=96):
    return {"text": text, "x0": x0, "top": top, "size": size,
            "ocr_confidence": confidence}


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


def zero_transaction_pages(statement="Nothing to report"):
    words = [
        word("Periodic", 40, 40), word("Transaction", 90, 40), word("Report", 160, 40),
        word("Filing", 480, 40), word("ID", 510, 40), word("#20000001", 530, 40),
    ]
    words.extend(word(part, 180 + index * 18, 326) for index, part in enumerate(statement.split()))
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


def final_row_continuation_pages():
    first = [
        word("Periodic", 40, 40), word("Transaction", 90, 40), word("Report", 160, 40),
        word("Filing", 480, 40), word("ID", 510, 40), word("#20000001", 530, 40),
        word("Verizon", 105, 704), word("Communications", 145, 704), word("Inc.", 215, 704),
        word("S", 262, 704), word("01/30/2026", 327, 704), word("01/30/2026", 382, 704),
        word("$15,001", 446, 704), word("-", 481, 704),
    ]
    second = [
        word("Common", 105, 116), word("Stock", 145, 116), word("(VZ)", 180, 116),
        word("[ST]", 220, 116), word("$50,000", 486, 116),
        word("Filing", 105, 134, 8.5), word("Status:", 135, 134, 8.5), word("New", 175, 134, 8.5),
    ]
    return [{"width": 612, "height": 792, "words": first},
            {"width": 612, "height": 792, "words": second}]


def legacy_checkbox_pages():
    words = [
        ocr_word("UNITED", 80, 35), ocr_word("STATES", 130, 35),
        ocr_word("HOUSE", 180, 35), ocr_word("OF", 225, 35),
        ocr_word("REPRESENTATIVES", 245, 35),
        ocr_word("Periodic", 160, 55), ocr_word("Transaction", 215, 55),
        ocr_word("Report", 285, 55), ocr_word("X", 360, 230, 14),
        ocr_word("JT", 80, 450), ocr_word("Example", 120, 450),
        ocr_word("Fund", 170, 450), ocr_word("(EXM)", 210, 450),
        ocr_word("X", 330, 450, 14), ocr_word("01/28/26", 390, 450),
        ocr_word("02/02/26", 450, 450), ocr_word("X", 490, 450, 14),
    ]
    return [{"width": 792, "height": 612, "words": words}]


def compact_legacy_checkbox_pages():
    words = [
        ocr_word("UNITED", 220, 70), ocr_word("STATES", 270, 70),
        ocr_word("HOUSE", 320, 70), ocr_word("OF", 365, 70),
        ocr_word("REPRESENTATIVES", 385, 70),
        ocr_word("Periodic", 285, 90), ocr_word("Transaction", 340, 90),
        ocr_word("Report", 410, 90), ocr_word("X", 388, 240, 14),
        # Printed example row must never become a disclosure fact.
        ocr_word("Example", 140, 405), ocr_word("Stock", 190, 405),
        ocr_word("X", 300, 405), ocr_word("02/05/20", 350, 405),
        ocr_word("03/07/20", 395, 405), ocr_word("X", 467, 405),
        ocr_word("JT", 112, 450), ocr_word("Treasury", 140, 450),
        ocr_word("ETF", 200, 450), ocr_word("X", 270, 450, 14),
        ocr_word("08/14/26", 350, 450), ocr_word("09/02/26", 395, 450),
        ocr_word("X", 467, 450, 14),
    ]
    return [{"width": 792, "height": 610.56, "words": words}]


def multipage_legacy_checkbox_pages():
    cover = [
        ocr_word("UNITED", 220, 70), ocr_word("STATES", 270, 70),
        ocr_word("HOUSE", 320, 70), ocr_word("OF", 365, 70),
        ocr_word("REPRESENTATIVES", 385, 70),
        ocr_word("Periodic", 285, 90), ocr_word("Transaction", 340, 90),
        ocr_word("Report", 410, 90), ocr_word("X", 360, 235, 14),
    ]
    table = [
        ocr_word("Capital", 280, 150), ocr_word("Gain", 320, 150),
        ocr_word("Partial", 350, 150), ocr_word("Transaction", 390, 150),
        ocr_word("DC", 60, 400), ocr_word("Example", 80, 400),
        ocr_word("Security", 140, 400), ocr_word("X", 215, 400, 14),
        ocr_word("02/19/26", 330, 400), ocr_word("03/02/26", 370, 400),
        ocr_word("X", 455, 400, 14),
    ]
    return [{"width": 792, "height": 610.56, "words": cover},
            {"width": 792, "height": 610.56, "words": table}]


META = {"source_id": "house_clerk", "document_id": "20000001", "filing_type": "P",
        "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000001.pdf",
        "filer_name": "Hon. Example", "state_district": "CA01", "filing_year": 2026,
        "filed_date": "2026-09-15", "archive_path": "house_clerk/documents/2026/20000001/a.pdf"}

IDENTITY = {"status": "matched_automatically", "document_id": "20000001",
            "person_id": "house:B000001", "official_name": "Example Person",
            "state": "CA", "state_district": "CA01", "party": "D",
            "evidence_url": "https://bioguide.congress.gov/search/bio/B000001",
            "roster_sha256": "f" * 64,
            "match_basis": "official_roster_exact_district_first_last_name"}


class HousePtrTests(unittest.TestCase):
    def test_tesseract_tsv_is_converted_to_pdf_word_geometry(self):
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t100\t200\t40\t20\t96.5\tPeriodic\n"
               "5\t1\t1\t1\t1\t2\t145\t200\t30\t20\t-1\tignored\n")
        words = _words_from_tesseract_tsv(tsv, points_per_pixel=0.36)
        self.assertEqual(len(words), 1)
        self.assertEqual((words[0]["text"], words[0]["x0"], words[0]["top"]),
                         ("Periodic", 36.0, 72.0))
        self.assertAlmostEqual(words[0]["size"], 7.2)
        self.assertEqual(words[0]["ocr_confidence"], 96.5)

    def test_rows_wait_for_automatic_qualification_with_page_evidence(self):
        result = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=False)
        self.assertEqual(len(result["transactions"]), 2)
        first = result["transactions"][0]
        self.assertEqual((first["owner"], first["ticker"], first["instrument_type"]),
                         ("Spouse", "EXM", "Stock"))
        self.assertEqual((first["amount_low"], first["amount_high"]), (1001, 15000))
        self.assertEqual(first["evidence"]["page"], 1)
        self.assertEqual(first["verification_status"], "awaiting_automatic_qualification")
        self.assertFalse(result["review"]["production_eligible"])
        self.assertIn("source_pdf_copy_permission_disabled", result["review"]["reasons"])

    def test_explicit_zero_transaction_statement_is_a_qualified_document(self):
        extraction = parse_word_pages(
            META, "0" * 64, zero_transaction_pages("N o T h I n g to Re port"), copy_allowed=True)
        self.assertEqual(extraction["transactions"], [])
        self.assertEqual(extraction["document_disposition"]["status"],
                         "explicit_no_transactions")
        self.assertEqual(extraction["document_disposition"]["statement"], "Nothing to report")
        self.assertEqual(extraction["review"]["status"], "awaiting_automatic_qualification")
        self.assertIn("explicit_no_transactions_declaration", extraction["review"]["reasons"])

        qualified = qualify_automatic(extraction, IDENTITY)
        self.assertEqual(qualified["transactions"], [])
        self.assertEqual(qualified["quarantined"], [])
        self.assertEqual(qualified["qualification"]["status"], "qualified_no_transactions")
        self.assertEqual(qualified["qualification"]["zero_transaction_document_count"], 1)
        self.assertTrue(qualified["qualification"]["production_eligible"])

    def test_no_transactions_variant_is_recognized_but_blank_is_not(self):
        result = parse_word_pages(
            META, "1" * 64, zero_transaction_pages("NO TRANSACTIONS TO REPORT"), copy_allowed=True)
        self.assertEqual(result["document_disposition"]["statement"],
                         "No transactions to report")
        with self.assertRaisesRegex(HouseIndexError, "no recognized transaction rows"):
            parse_word_pages(META, "2" * 64, zero_transaction_pages(""), copy_allowed=True)

    def test_zero_statement_cannot_hide_an_unrecognized_transaction_row(self):
        pages = fixture_pages()
        pages[0]["words"].extend([
            word("Nothing", 180, 250), word("to", 235, 250), word("report", 250, 250),
        ])
        next(item for item in pages[0]["words"] if item["text"] == "$1,001")["text"] = "unknown"
        with self.assertRaisesRegex(HouseIndexError, "unsupported amount layout"):
            parse_word_pages(META, "3" * 64, pages, copy_allowed=True)

    def test_bad_identity_and_unknown_layout_fail_closed(self):
        bad_header = fixture_pages()
        bad_header[0]["words"] = [word for word in bad_header[0]["words"] if word["text"] != "#20000001"]
        with self.assertRaises(HouseIndexError):
            parse_word_pages(META, "a" * 64, bad_header, copy_allowed=True)
        bad_amount = fixture_pages()
        next(word for word in bad_amount[0]["words"] if word["text"] == "$1,001")["text"] = "unknown"
        with self.assertRaises(HouseIndexError):
            parse_word_pages(META, "a" * 64, bad_amount, copy_allowed=True)
        with self.assertRaisesRegex(HouseIndexError, "requires OCR"):
            parse_word_pages(META, "a" * 64, [{"width": 612, "height": 792, "words": []}],
                             copy_allowed=True)

    def test_exact_and_open_ended_amounts_are_preserved(self):
        exact_pages = deepcopy(fixture_pages())
        exact_pages[0]["words"] = [item for item in exact_pages[0]["words"]
                                    if not (item["top"] == 326 and item["x0"] >= 446)]
        exact_pages[0]["words"].append(word("$15.00", 446, 326))
        exact = parse_word_pages(META, "d" * 64, exact_pages, copy_allowed=True)["transactions"][0]
        self.assertEqual((exact["amount_low"], exact["amount_high"], exact["amount_kind"]),
                         (15, 15, "exact"))

        open_pages = deepcopy(fixture_pages())
        open_pages[0]["words"] = [item for item in open_pages[0]["words"]
                                   if not (item["top"] == 326 and item["x0"] >= 446)]
        open_pages[0]["words"].extend([word("Spouse/DC", 446, 326), word("Over", 492, 326),
                                        word("$1,000,000", 520, 326)])
        opened = parse_word_pages(META, "e" * 64, open_pages, copy_allowed=True)["transactions"][0]
        self.assertEqual((opened["amount_low"], opened["amount_high"], opened["amount_kind"]),
                         (1000001, None, "open_ended"))

    def test_exact_cent_amount_uses_enclosing_integer_bounds_without_losing_source(self):
        pages = deepcopy(fixture_pages())
        pages[0]["words"] = [item for item in pages[0]["words"]
                              if not (item["top"] == 326 and item["x0"] >= 446)]
        pages[0]["words"].append(word("$318.74", 446, 326))
        row = parse_word_pages(META, "7" * 64, pages, copy_allowed=True)["transactions"][0]
        self.assertEqual((row["amount_low"], row["amount_high"], row["amount_kind"]),
                         (318, 319, "exact"))
        self.assertEqual(row["amount_raw"], "$318.74")

        integer_pages = deepcopy(pages)
        next(item for item in integer_pages[0]["words"]
             if item["text"] == "$318.74")["text"] = "$318.00"
        integer_row = parse_word_pages(
            META, "8" * 64, integer_pages, copy_allowed=True)["transactions"][0]
        self.assertEqual((integer_row["amount_low"], integer_row["amount_high"]),
                         (318, 318))

    def test_transaction_date_accepts_small_left_shift_but_not_notification_only(self):
        shifted = deepcopy(fixture_pages())
        transaction = next(item for item in shifted[0]["words"]
                           if item["text"] == "08/12/2026")
        transaction["x0"] = 0.53 * shifted[0]["width"] - 0.66
        parsed = parse_word_pages(META, "9" * 64, shifted, copy_allowed=True)
        self.assertEqual(parsed["transactions"][0]["transaction_date"], "2026-08-12")

        notification_only = deepcopy(fixture_pages())
        notification_only[0]["words"] = [
            item for item in notification_only[0]["words"]
            if item["top"] < 326 or item["top"] >= 393 or item["text"] != "08/12/2026"]
        next(item for item in notification_only[0]["words"]
             if item["text"] == "09/01/2026")["x0"] = 0.62 * notification_only[0]["width"] - 0.66
        notification_only[0]["words"] = [
            item for item in notification_only[0]["words"] if item["top"] < 393]
        with self.assertRaisesRegex(HouseIndexError, "no recognized transaction rows"):
            parse_word_pages(META, "0" * 64, notification_only, copy_allowed=True)

    def test_filing_status_ignores_path_note_and_final_row_details_can_cross_page(self):
        polluted = deepcopy(fixture_pages())
        polluted[0]["words"].extend([
            word("C:/exports/report.pdf", 105, 367, 8.5),
        ])
        first = parse_word_pages(
            META, "1" * 64, polluted, copy_allowed=True)["transactions"][0]
        self.assertEqual(first["filing_status"], "New")
        self.assertNotIn("non_new_filing_requires_revision_resolution", first["review_reasons"])

        continued = final_row_continuation_pages()
        for item in continued[0]["words"]:
            if item["top"] == 704:
                item["top"] = 620
        row = parse_word_pages(
            META, "2" * 64, continued, copy_allowed=True)["transactions"][0]
        self.assertEqual(row["filing_status"], "New")
        self.assertEqual(row["evidence"]["pages"], [1, 2])

        no_detail = deepcopy(continued)
        no_detail[1]["words"] = [item for item in no_detail[1]["words"]
                                  if item["text"] not in {"Filing", "Status:", "New"}]
        with self.assertRaisesRegex(HouseIndexError, "unsupported .*amount.* layout"):
            parse_word_pages(META, "3" * 64, no_detail, copy_allowed=True)

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

    def test_final_row_can_continue_without_another_date_anchor(self):
        result = parse_word_pages(META, "b" * 64, final_row_continuation_pages(), copy_allowed=True)
        row = result["transactions"][0]
        self.assertEqual((row["asset_name"], row["ticker"]),
                         ("Verizon Communications Inc. Common Stock", "VZ"))
        self.assertEqual((row["amount_low"], row["amount_high"]), (15001, 50000))
        self.assertEqual(row["evidence"]["pages"], [1, 2])

    def test_automatic_qualification_promotes_standard_rows_and_isolates_ambiguity(self):
        extraction = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=False)
        result = qualify_automatic(extraction, IDENTITY)
        self.assertEqual(result["qualification"]["qualified_count"], 1)
        self.assertEqual(result["qualification"]["quarantined_count"], 1)
        self.assertEqual(result["transactions"][0]["verification_status"], "official_matched")
        self.assertNotIn("reviewed_by", result)

        for basis in ("official_roster_exact_district_unique_surname",
                      "official_roster_same_state_unique_first_last_name_redistricted"):
            with self.subTest(match_basis=basis):
                recovered_identity = dict(IDENTITY, match_basis=basis)
                recovered = qualify_automatic(extraction, recovered_identity)
                self.assertEqual(recovered["qualification"]["qualified_count"], 1)

        unresolved = {"status": "unresolved", "document_id": "20000001"}
        isolated = qualify_automatic(extraction, unresolved)
        self.assertEqual(isolated["transactions"], [])
        self.assertTrue(all("identity_not_deterministic" in row["reasons"]
                            for row in isolated["quarantined"]))

        open_pages = deepcopy(fixture_pages())
        open_pages[0]["words"] = [item for item in open_pages[0]["words"]
                                   if not (item["top"] == 326 and item["x0"] >= 446)]
        open_pages[0]["words"].extend([word("Over", 446, 326), word("$1,000,000", 486, 326)])
        open_extraction = parse_word_pages(META, "e" * 64, open_pages, copy_allowed=True)
        open_result = qualify_automatic(open_extraction, IDENTITY)
        first_quarantine = next(item for item in open_result["quarantined"]
                                if item["extraction_id"] == open_extraction["transactions"][0]["extraction_id"])
        self.assertIn("open_ended_amount_not_representable", first_quarantine["reasons"])

        ocr_extraction = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=False,
                                          ocr_engine="tesseract 5.3.0")
        for row in ocr_extraction["transactions"]:
            row["ocr_confidence"] = 84.9
        ocr_result = qualify_automatic(ocr_extraction, IDENTITY)
        self.assertTrue(all("ocr_confidence_below_threshold" in row["reasons"]
                            for row in ocr_result["quarantined"]))

        impossible = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=True)
        impossible["transactions"][0]["transaction_date"] = "2026-12-26"
        impossible["transactions"][0]["notification_date"] = "2026-01-21"
        impossible_result = qualify_automatic(impossible, IDENTITY)
        impossible_row = next(row for row in impossible_result["quarantined"]
                              if row["extraction_id"] == impossible["transactions"][0]["extraction_id"])
        self.assertIn("date_sequence_invalid", impossible_row["reasons"])

        option = parse_word_pages(META, "a" * 64, fixture_pages(), copy_allowed=True)
        option_row = option["transactions"][0]
        option_row["instrument_type"] = "Option"
        option_row["description"] = (
            "Purchased 20 call options with a strike price of $150 and an expiration date of 1/15/27.")
        qualified_option = qualify_automatic(option, IDENTITY)["transactions"][0]
        self.assertEqual((qualified_option["option_type"], qualified_option["strike_price"],
                          qualified_option["expiration_date"]), ("Call", 150, "2027-01-15"))

        option_row["description"] = "Put Option"
        incomplete_option = qualify_automatic(option, IDENTITY)
        isolated_option = next(row for row in incomplete_option["quarantined"]
                               if row["extraction_id"] == option_row["extraction_id"])
        self.assertIn("option_details_incomplete", isolated_option["reasons"])

    def test_legacy_checkbox_form_uses_mark_columns_without_inference(self):
        extraction = parse_word_pages(META, "f" * 64, legacy_checkbox_pages(), copy_allowed=True,
                                      ocr_engine="tesseract 5.3.0")
        row = extraction["transactions"][0]
        self.assertEqual(extraction["extraction_method"], "tesseract_legacy_checkbox")
        self.assertEqual((row["owner"], row["asset_name"], row["ticker"]),
                         ("Joint", "Example Fund", "EXM"))
        self.assertEqual((row["transaction_type"], row["amount_low"], row["amount_high"]),
                         ("purchase", 1001, 15000))
        self.assertEqual((row["transaction_date"], row["notification_date"]),
                         ("2026-01-28", "2026-02-02"))
        qualified = qualify_automatic(extraction, IDENTITY)
        self.assertEqual(qualified["qualification"]["qualified_count"], 1)

        ambiguous_pages = deepcopy(legacy_checkbox_pages())
        ambiguous_pages[0]["words"].append(ocr_word("X", 520, 450, 14))
        ambiguous = parse_word_pages(META, "e" * 64, ambiguous_pages, copy_allowed=True,
                                     ocr_engine="tesseract 5.3.0")
        isolated = qualify_automatic(ambiguous, IDENTITY)
        self.assertIn("amount_invalid", isolated["quarantined"][0]["reasons"])

    def test_compact_legacy_form_excludes_its_printed_example_row(self):
        extraction = parse_word_pages(META, "d" * 64, compact_legacy_checkbox_pages(),
                                      copy_allowed=True, ocr_engine="tesseract 5.3.0")
        self.assertEqual(len(extraction["transactions"]), 1)
        row = extraction["transactions"][0]
        self.assertEqual((row["asset_name"], row["transaction_type"]),
                         ("Treasury ETF", "purchase"))
        self.assertEqual((row["amount_low"], row["amount_high"]), (15001, 50000))

    def test_multipage_legacy_form_reuses_cover_status_and_table_geometry(self):
        extraction = parse_word_pages(META, "c" * 64, multipage_legacy_checkbox_pages(),
                                      copy_allowed=True, ocr_engine="tesseract 5.3.0")
        self.assertEqual(len(extraction["transactions"]), 1)
        row = extraction["transactions"][0]
        self.assertEqual((row["filing_status"], row["owner"], row["asset_name"]),
                         ("New", "Dependent Child", "Example Security"))
        self.assertEqual((row["transaction_type"], row["amount_low"], row["amount_high"]),
                         ("purchase", 15001, 50000))

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
