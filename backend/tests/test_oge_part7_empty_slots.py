"""Source object safeguards for already located annual transaction blanks."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual_scan_sections import verify_part7_empty_slots


class Part7BlankSlotTests(unittest.TestCase):
    def setUp(self):
        self.content = b"synthetic PDF contract, not source accuracy evidence"
        text = "Part 7: Transactions\n16."
        self.slot = {"slot_id": "source-slot", "page_number": 1, "printed_number": "16",
            "top": 15, "bottom": 20, "raw_lines": ["16."],
            "source_page_text_sha256": hashlib.sha256(text.encode()).hexdigest()}
        self.sections = {"source_sha256": hashlib.sha256(self.content).hexdigest(),
            "page_count": 1, "part7_slots": [self.slot]}
        rect = lambda x0, y0, x1, y1: {"x0": x0, "top": y0, "x1": x1, "bottom": y1,
                                       "fill": True, "stroke": False}
        grid = [rect(x - .5, 9.5, x + .5, 30.5) for x in [10, 20, 30, 40, 50]]
        grid += [rect(9.5, y - .5, 50.5, y + .5) for y in [10, 30]]
        self.cells = [(0, 10, 10, 30)] + [(x, 10, x + 10, 30) for x in [10, 20, 30, 40]]
        self.rows = [SimpleNamespace(cells=self.cells), SimpleNamespace(cells=self.cells)]
        self.text_rows = [["#", "Description", "Type", "Date", "Amount"], ["16.", "", "", "", ""]]
        table = SimpleNamespace(rows=self.rows, extract=lambda: self.text_rows)
        self.page = SimpleNamespace(extract_text=lambda: text, objects={"rect": grid}, annots=[],
                                    find_tables=lambda: [table])

    def observe(self, sections=None):
        class Document:
            pages = [self.page]
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "original.pdf"
            path.write_bytes(self.content)
            with patch.dict("sys.modules", {"pdfplumber": SimpleNamespace(open=lambda _: Document())}):
                return verify_part7_empty_slots(path, sections or self.sections)

    def test_original_blank_requires_complete_cells(self):
        proofs = self.observe()
        self.assertEqual(set(proofs), {"source-slot"})
        self.assertEqual(len(proofs["source-slot"]["native_interiors"]), 4)
        self.page.objects["rect"].pop()
        self.assertFalse(self.observe())

    def test_missing_text_with_actual_marks_never_becomes_blank(self):
        for kind in ["char", "image", "curve"]:
            self.page.objects[kind] = [{"x0": 12, "top": 15, "x1": 15, "bottom": 18, "linewidth": .1}]
            self.assertFalse(self.observe(), kind)
            del self.page.objects[kind]

    def test_duplicate_number_and_shifted_locator_stay_unresolved(self):
        self.rows.append(self.rows[-1])
        self.text_rows.append(self.text_rows[-1])
        self.assertFalse(self.observe())
        self.rows.pop(); self.text_rows.pop()
        self.slot["top"] = 3
        self.assertFalse(self.observe())

    def test_wrong_table_and_nonempty_description_do_not_prove_blank(self):
        self.text_rows[0][2] = "Owner"
        self.assertFalse(self.observe())
        self.text_rows[0][2] = "Type"
        self.text_rows[1][1] = "Actual asset"
        self.assertFalse(self.observe())

    def test_pdf_and_page_hashes_are_checked(self):
        bad = deepcopy(self.sections); bad["source_sha256"] = "a" * 64
        with self.assertRaises(OgeCatalogError):
            self.observe(bad)
        self.slot["source_page_text_sha256"] = "a" * 64
        with self.assertRaises(OgeCatalogError):
            self.observe()


if __name__ == "__main__":
    unittest.main()
