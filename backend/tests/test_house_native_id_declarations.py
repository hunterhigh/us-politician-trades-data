"""Declaration membership tests, not font/paint or source-scope qualification."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pdfplumber
import pymupdf

from unison_snapshot.house_native_id_declarations import observe_native_house_id_declarations
from unison_snapshot.house_native_visibility import _source_characters
from unison_snapshot.house_ptr import observe_native_word_pages, parse_word_pages
from unison_snapshot.house import HouseIndexError
from test_house_ptr import META


def declaration_pdf(path, kind="control"):
    """Generated real PDF with the already supported eight-column header."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    edges = [22, 80, 119, 263, 327, 383, 447, 524, 576]
    labels = ["ID", "Owner", "Asset", "Transaction Type", "Date", "Notification Date", "Amount", "Cap. Gains > $200?"]
    # Use the actual PDF rectangle primitive, rather than a four-line path;
    # this is the existing header observer's source grammar.
    header_stream = b"0.95 g\n" + b"".join(
        f"{left} 474 {right-left} 44 re f\n".encode() for left, right in zip(edges, edges[1:]))
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, header_stream + b"0 g\n")
    page.set_contents(xref)
    for left, right, label in zip(edges, edges[1:], labels):
        if label == "Cap. Gains > $200?":
            page.insert_text((left + 2, 284), "Cap. Gains >", fontsize=6)
            page.insert_text((left + 2, 295), "$200?", fontsize=6)
        else:
            page.insert_text((left + 2, 284), label, fontsize=6)
    page.insert_text((22, 80), "Periodic Transaction Report", fontsize=10)
    page.insert_text((430, 100), "Filing ID #20000001", fontsize=8)
    page.insert_text((22, 140), "Clerk of the House of Representatives - Legislative Resource Center - Washington, DC 20515", fontsize=8)
    page.insert_text((22, 190), "Name: Example Person", fontsize=9)
    page.insert_text((22, 210), "Status: Member", fontsize=9)
    page.insert_text((22, 230), "State/District: NJ07", fontsize=9)
    for left in (22, 576):
        page.draw_rect(pymupdf.Rect(left, 318, left + .75, 410), width=0, fill=(0,))
    if kind not in {"unnumbered", "unnumbered_new"}:
        page.insert_text((25, 334), "2000140446", fontsize=8)
    page.insert_text((123, 334), "Example Fund [MF]", fontsize=9)
    if kind != "no_financial_fields":
        for x, text in [(270, "S"), (330, "06/03/2025"), (386, "06/04/2025"), (450, "$1,001-$15,000")]:
            page.insert_text((x, 334), text, fontsize=8)
    page.insert_text((123, 365), "Filing Status: " + ("New" if kind == "unnumbered_new" else "Amended"), fontsize=7.5)
    if kind == "extra":
        page.insert_text((25, 350), "2000999999", fontsize=8)
    if kind == "same_id_extra":
        page.insert_text((25, 350), "2000140446", fontsize=8)
    if kind == "split":
        page.insert_text((25, 350), "20009", fontsize=8)
        page.insert_text((55, 350), "99999", fontsize=8)
    if kind == "bad":
        page.insert_text((25, 350), "2000X99999", fontsize=8)
    if kind == "column_overlap":
        page.insert_text((76, 350), "1234567890", fontsize=8)
    if kind == "footer":
        page.insert_text((25, 720), "2000999999", fontsize=8)
    if kind == "closing":
        page.insert_text((25, 535), "2000999999", fontsize=8)
    doc.save(path)
    doc.close()


def phrase_members(source, page_number=1):
    """Exact synthetic token members for the existing pure role fixture."""
    source = deepcopy(source)
    words, chars = [], []
    for item in source["words"]:
        left = item["x0"]
        for token in item["text"].split():
            members = [{"text": value, "x0": left + n * .25, "x1": left + (n + 1) * .25,
                "top": item["top"], "bottom": item["top"] + item["size"], "page_number": page_number}
                for n, value in enumerate(token)]
            words.append({**item, "text": token, "x0": members[0]["x0"], "x1": members[-1]["x1"],
                "bottom": members[-1]["bottom"]})
            chars.extend(members)
            left = members[-1]["x1"] + .25
    source["words"] = words
    return source, chars


class NativeIdDeclarationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def observe(self, kind="control", mutation=None):
        path = Path(self.temp.name) / f"{kind}.pdf"
        declaration_pdf(path, kind)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with pdfplumber.open(path) as doc:
            page = observe_native_word_pages(doc, sha)[0]
            chars = _source_characters(doc.pages[0])[0]
        if kind == "no_financial_fields":
            with self.assertRaisesRegex(HouseIndexError, "no recognized transaction rows"):
                parse_word_pages(META, sha, [page], copy_allowed=True)
            parsed = {"transactions": []}
        else:
            parsed = parse_word_pages(META, sha, [page], copy_allowed=True)
        before = deepcopy((page, chars, parsed["transactions"]))
        if mutation:
            mutation(page, chars, parsed)
        result = observe_native_house_id_declarations(page, chars, source_sha256=sha,
            page_number=1, source_rows=parsed["transactions"])
        if not mutation:
            self.assertEqual((page, chars, parsed["transactions"]), before)
        return result, page, chars, parsed

    def test_real_pdf_declaration_exact_original_members_and_repeat(self):
        result, page, chars, parsed = self.observe()
        self.assertEqual(result["status"], "observed_declarations")
        self.assertFalse(result["scope_complete"])
        self.assertFalse(result["font_paint_proven"])
        declaration, = result["declarations"]
        self.assertEqual(declaration["reported_transaction_id"], "2000140446")
        self.assertEqual("".join(chars[i]["text"] for i in declaration["source_character_indices"]), "2000140446")
        self.assertEqual([page["words"][i]["text"] for i in declaration["word_indices"]], ["2000140446"])
        self.assertEqual(declaration["source_row_indices"], [0])
        self.assertEqual([r["role"] for r in result["known_numeric_roles"]], ["clerk_contact_postcode"])
        self.assertEqual(observe_native_house_id_declarations(page, chars,
            source_sha256=result["source_sha256"], page_number=1, source_rows=parsed["transactions"]), result)

    def test_unanchored_extra_id_and_duplicate_id_cannot_disappear(self):
        for kind, extra in (("extra", "2000999999"), ("same_id_extra", "2000140446")):
            with self.subTest(kind=kind):
                result, _, _, parsed = self.observe(kind)
                self.assertEqual(len(parsed["transactions"]), 1)
                self.assertEqual([d["raw_text"] for d in result["declarations"]], ["2000140446", extra])
                self.assertEqual(result["status"], "unknown")
                self.assertTrue(result["unbound_declarations"])

    def test_no_financial_fields_does_not_select_declaration_inventory(self):
        result, _, _, parsed = self.observe("no_financial_fields")
        self.assertEqual([d["reported_transaction_id"] for d in result["declarations"]], ["2000140446"])
        self.assertEqual(parsed["transactions"], [])
        self.assertTrue(result["unbound_declarations"])

    def test_split_bad_and_cross_column_members_stay_unknown(self):
        for kind in ("split", "bad", "column_overlap"):
            with self.subTest(kind=kind):
                result, _, _, _ = self.observe(kind)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(len(result["declarations"]), 2)
                self.assertTrue(result["unknown_declarations"])
                self.assertTrue(result["declarations"][1]["source_character_indices"])
        split, _, _, _ = self.observe("split")
        self.assertIsNone(split["declarations"][1]["reported_transaction_id"])
        self.assertEqual(split["declarations"][1]["raw_text"], "20009 99999")

    def test_footer_closing_and_unnumbered_new_are_not_absence(self):
        for kind in ("footer", "closing"):
            result, _, _, _ = self.observe(kind)
            self.assertEqual(result["status"], "unknown")
            # Without an independently recognized closing boundary these are
            # still raw ID-strip declarations, rather than erased footer text.
            retained = [w["raw_text"] for w in result["outside_numeric_words"]] + [
                d["raw_text"] for d in result["declarations"]]
            self.assertIn("2000999999", retained)
        result, _, _, parsed = self.observe("unnumbered_new")
        self.assertEqual(parsed["transactions"][0]["filing_status"], "New")
        self.assertIsNone(parsed["transactions"][0]["reported_transaction_id"])
        self.assertEqual(result["declarations"], [])
        self.assertTrue(result["unnumbered_source_rows"])
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["scope_complete"])

    def test_exact_membership_rejects_tiny_geometry_and_text_changes(self):
        def shift(_, chars, __):
            char = next(c for c in chars if c["text"] == "2" and 320 < c["top"] < 340)
            char["x0"] -= .0000001
        def text(_, chars, __):
            char = next(c for c in chars if c["text"] == "2" and 320 < c["top"] < 340)
            char["text"] = "9"
        for mutation in (shift, text):
            result, _, _, _ = self.observe(mutation=mutation)
            self.assertEqual(result["status"], "unknown")
            self.assertIn("word_character_membership_not_exact", result["reasons"])

    def test_source_binding_unsupported_family_and_member_budget(self):
        from unittest.mock import patch
        result, page, chars, parsed = self.observe()
        wrong = observe_native_house_id_declarations(page, chars, source_sha256="b" * 64,
            page_number=1, source_rows=parsed["transactions"])
        self.assertIn("bound_native_geometry_unavailable", wrong["reasons"])
        self.assertEqual(wrong["status"], "unknown")
        no_header = deepcopy(page)
        no_header["native_page_geometry"]["rects"] = []
        unknown = observe_native_house_id_declarations(no_header, chars,
            source_sha256=result["source_sha256"], page_number=1, source_rows=parsed["transactions"])
        self.assertEqual(unknown["status"], "unknown")
        self.assertTrue(unknown["outside_numeric_words"])
        with patch("unison_snapshot.house_native_id_declarations.MAX_COMPARISONS", 1):
            limited = observe_native_house_id_declarations(page, chars,
                source_sha256=result["source_sha256"], page_number=1, source_rows=parsed["transactions"])
        self.assertIn("source_role_comparison_budget_exceeded", limited["reasons"])
        self.assertEqual(limited["status"], "unknown")

    def test_existing_closing_role_is_reused_but_extra_numeric_text_is_not_swallowed(self):
        from test_house_native_coverage import mixed_template
        source = mixed_template()[0]
        source["words"].append({"text": "2000140446", "x0": 25, "top": 326, "size": 9})
        # Turn the existing grammar fixture's phrases into exact token/character
        # members. This checks pure role composition, not actual font semantics.
        source, chars = phrase_members(source)
        sha = source["native_page_geometry"]["source_sha256"]
        original = observe_native_house_id_declarations(source, chars,
            source_sha256=sha, page_number=1, source_rows=[])
        self.assertTrue(original["role_evidence"]["closing_role_observed"])
        added = {"text": "2000999999", "x0": 25, "x1": 35, "top": 760, "bottom": 769, "size": 9}
        source["words"].append(added)
        chars.extend({"text": c, "x0": 25 + i, "x1": 26 + i, "top": 760, "bottom": 769, "page_number": 1}
            for i, c in enumerate(added["text"]))
        changed = observe_native_house_id_declarations(source, chars,
            source_sha256=sha, page_number=1, source_rows=[])
        self.assertFalse(changed["role_evidence"]["closing_role_observed"])
        self.assertIn("closing_role_unknown", changed["reasons"])
        self.assertEqual([w["raw_text"] for w in changed["outside_numeric_words"]], ["2000999999"])

    def test_supported_closing_only_role_observation_never_grants_scope(self):
        from test_house_native_coverage import template
        source, chars = phrase_members(template()[1], page_number=2)
        sha = source["native_page_geometry"]["source_sha256"]
        result = observe_native_house_id_declarations(source, chars,
            source_sha256=sha, page_number=2, source_rows=[])
        self.assertEqual(result["status"], "observed_role")
        self.assertEqual(result["role_evidence"]["page_kind"], "closing_only")
        self.assertFalse(result["scope_complete"])
        self.assertFalse(result["font_paint_proven"])
        self.assertEqual(result["declarations"], [])
        same_page_row = [{"evidence": {"segments": [{"page": 2, "bbox_points": [22, 120, 576, 180]}]}}]
        conflicted = observe_native_house_id_declarations(source, chars,
            source_sha256=sha, page_number=2, source_rows=same_page_row)
        self.assertEqual(conflicted["status"], "unknown")
        self.assertIn("closing_role_conflicts_with_source_row", conflicted["reasons"])
        # Existing closing grammar permits prose comments; an unexplained naked
        # official-looking number there is still retained as unknown.
        source["words"].append({"text": "2000999999", "x0": 25, "x1": 35, "top": 235,
            "bottom": 244, "size": 9})
        chars.extend({"text": c, "x0": 25+i, "x1": 26+i, "top": 235, "bottom": 244, "page_number": 2}
            for i, c in enumerate("2000999999"))
        extra = observe_native_house_id_declarations(source, chars,
            source_sha256=sha, page_number=2, source_rows=[])
        self.assertEqual(extra["status"], "unknown")
        self.assertEqual([w["raw_text"] for w in extra["outside_numeric_words"]], ["2000999999"])

    def test_missing_character_member_and_wrong_page_cannot_grant_clean_observation(self):
        def missing(_, chars, __):
            index = next(i for i, c in enumerate(chars) if c["text"] == "2" and 320 < c["top"] < 340)
            chars.pop(index)
        result, _, _, _ = self.observe(mutation=missing)
        self.assertIn("word_character_membership_not_exact", result["reasons"])
        def wrong_page(_, chars, __):
            chars[0]["page_number"] = 2
        result, _, _, _ = self.observe(mutation=wrong_page)
        self.assertIn("source_character_page_mismatch", result["reasons"])


if __name__ == "__main__":
    unittest.main()
