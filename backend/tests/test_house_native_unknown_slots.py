from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import (parse_word_pages, qualify_automatic,
    _modern_report_ledger)
from test_house_ptr import fixture_pages, word, META, IDENTITY


class NativeUnknownSlotsTests(unittest.TestCase):
    def parse(self, pages):
        pages = deepcopy(pages)
        pages[0]["words"].extend([word("Filing", 105, 418, 8.5),
            word("Status:", 135, 418, 8.5), word("New", 175, 418, 8.5)])
        return parse_word_pages(META, "a" * 64, pages, copy_allowed=True)

    def broken(self):
        pages = fixture_pages()
        for item in pages[0]["words"]:
            if item["top"] == 326 and item["text"] in {"P", "08/12/2026"}:
                item["text"] = "unreadable"
        return pages

    def test_double_damage_retains_source_slot_not_a_formal_transaction(self):
        baseline = self.parse(fixture_pages())
        damaged = self.parse(self.broken())
        ledger = damaged["source_report_ledger"]
        self.assertEqual(len(damaged["transactions"]), 1)
        self.assertEqual(damaged["transactions"][0], baseline["transactions"][1])
        self.assertEqual(ledger["disposition_counts"], {"data_candidate": 1, "unknown": 1})
        self.assertEqual(ledger["logical_row_count"], 1)
        self.assertIsNone(ledger["physical_slot_count"])
        self.assertEqual(ledger["relation_coverage"], "partial_unknown")
        slot = next(s for s in ledger["pages"][0]["slots"] if s["disposition"] == "unknown")
        self.assertEqual(slot["bounds"], [326., 335.])
        self.assertTrue(slot["observation_ref"].startswith("native_financial_line_without_row_anchor:"))
        q = qualify_automatic(damaged, IDENTITY)
        self.assertEqual((len(q["transactions"]), len(q["quarantined"])), (1, 0))
        self.assertEqual(q["source_report_ledger"], ledger)

    def test_single_bad_field_uses_existing_row_qualification(self):
        for bad in ("P", "08/12/2026"):
            with self.subTest(field=bad):
                pages = fixture_pages()
                next(w for w in pages[0]["words"] if w["text"] == bad)["text"] = "unreadable"
                x = self.parse(pages)
                self.assertEqual(len(x["transactions"]), 2)
                self.assertNotIn("unknown", x["source_report_ledger"]["disposition_counts"])
                q = qualify_automatic(x, IDENTITY)
                self.assertEqual((len(q["transactions"]), len(q["quarantined"])), (1, 1))

    def test_notes_status_mentions_and_wrapped_amount_do_not_create_slots(self):
        pages = fixture_pages()
        pages[0]["words"].extend([
            word("Wrapped", 105, 340), word("[ST]", 180, 340), word("$50,000", 446, 340),
            word("Filing Status: New", 105, 500),
            word("Comment", 105, 480), word("[ST]", 180, 480), word("$1,000", 446, 480),
        ])
        x = self.parse(pages)
        self.assertNotIn("unknown", x["source_report_ledger"]["disposition_counts"])
        for change in ("status", "asset", "amount"):
            bad = self.broken()
            bad[0]["words"] = [w for w in bad[0]["words"] if not (
                (change == "status" and w["top"] == 354) or
                (change == "asset" and w["top"] == 326 and w["text"] == "[ST]") or
                (change == "amount" and w["top"] == 326 and "$" in w["text"]))]
            self.assertNotIn("unknown", self.parse(bad)["source_report_ledger"]["disposition_counts"])

    def test_wrapped_asset_code_smallcaps_status_and_second_page_unknown(self):
        pages = self.broken()
        code = next(w for w in pages[0]["words"] if w["top"] == 326 and w["text"] == "[ST]")
        code["top"] = 337
        for item in pages[0]["words"]:
            if item["top"] == 354 and item["text"] == "Filing":
                item["text"] = "F\x00\x00"
            if item["top"] == 354 and item["text"] == "Status:":
                item["text"] = "S\x00:"
        first = self.parse(pages)
        self.assertEqual(first["source_report_ledger"]["disposition_counts"]["unknown"], 1)
        pages.append({"width": 612, "height": 792, "words": [
            word("Unclear", 105, 400), word("[ST]", 180, 410),
            word("unknown", 262, 400), word("$1,001", 446, 400),
            word("Filing Status: New", 105, 425, 8.5)]})
        second = self.parse(pages)
        self.assertEqual(second["transactions"], first["transactions"])
        self.assertEqual(second["source_report_ledger"]["disposition_counts"]["unknown"], 2)
        self.assertEqual(second["source_report_ledger"]["coverage_unknown_pages"], [1, 2])

    def test_unknown_slot_reaches_normal_candidate_and_builder(self):
        import tempfile
        from test_house_native_account_context import fixture
        from test_house_candidate import BASE
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.builder import build
        x = self.parse(self.broken());q = qualify_automatic(x, IDENTITY)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);state = fixture(root, x, q)
            c = build_house_candidate(root, state, deepcopy(BASE))
            self.assertEqual(c["transactions"], q["transactions"])
            self.assertEqual(len(c["transactions"]), 1)
            self.assertTrue(build(c, generated_at="2026-09-18T12:00:00Z", allow_production=True))

    def test_legacy_ledger_only_accepted_when_exactly_recomputed(self):
        pages = self.broken()
        # Add an official account to exercise the source replay consumer.
        pages[0]["words"].extend([word("Subholding Of:", 105, 435, 8.5), word("Account A", 180, 435, 8.5)])
        x = self.parse(pages)
        self.assertTrue(x["transactions"][0].get("subholding_of"))
        old = deepcopy(x)
        old["source_report_ledger"] = _modern_report_ledger("a" * 64, x["source_word_pages"],
            old["transactions"], include_unanchored=False)
        self.assertEqual(qualify_automatic(old, IDENTITY)["transactions"],
                         qualify_automatic(x, IDENTITY)["transactions"])
        tampered = deepcopy(old)
        tampered["source_report_ledger"]["logical_row_count"] = 9
        with self.assertRaises(HouseIndexError):
            qualify_automatic(tampered, IDENTITY)
        tampered = deepcopy(old)
        tampered["source_word_pages_sha256"] = "0" * 64
        with self.assertRaises(HouseIndexError):
            qualify_automatic(tampered, IDENTITY)


if __name__ == "__main__":
    unittest.main()
