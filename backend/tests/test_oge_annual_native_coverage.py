"""Exercise coverage against real generated PDF geometry, including omissions."""
from copy import deepcopy
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual import QUALIFIED_PARSER_VERSION
from unison_snapshot.oge_annual_ledger import annual_holdings_coverage_complete
from unison_snapshot.oge_annual_native_coverage import observe_native_holding_coverage


@unittest.skipUnless(all(importlib.util.find_spec(name) is not None
                         for name in ("pymupdf", "pdfplumber", "PIL")),
                     "native PDF geometry fixture requires optional rendering dependencies")
class NativeAnnualCoverageTests(unittest.TestCase):
    def fixture(self, folder, *, outside=False, image=False, unknown=False):
        import pymupdf
        from PIL import Image

        document = pymupdf.open()
        document.new_page().insert_text((30, 40), "OGE Form 278e Annual")
        holdings = []
        for part, title in [(2, "Filer's Employment Assets"),
                            (5, "Spouse's Employment Assets"), (6, "Other Assets and Income")]:
            page = document.new_page(width=600, height=500)
            page.insert_text((30, 40), f"Part {part}: {title}")
            for x in [30, 60, 300, 470]:
                page.draw_line((x, 65), (x, 120))
            for y in [65, 95, 120]:
                page.draw_line((30, y), (470, y))
            for x, text in [(35, "#"), (65, "Description"), (305, "Value")]:
                page.insert_text((x, 85), text)
            cells = ["1", f"Asset {part}", "$1,001 - $15,000"]
            for x, text in zip([35, 65, 305], cells):
                page.insert_text((x, 112), text)
            if part == 2 and outside:
                page.insert_text((30, 155), "2 Omitted asset $15,001 - $50,000")
            if part == 2 and image:
                stream = io.BytesIO()
                Image.new("RGB", (20, 10), "black").save(stream, format="PNG")
                page.insert_image(pymupdf.Rect(100, 140, 140, 160), stream=stream.getvalue())
            holdings.append({"section": f"part{part}", "page_number": len(document),
                             "row_number": "1", "cells": cells})
        if unknown:
            document.new_page().insert_text((30, 40), "Unclassified additional disclosure")
        path = Path(folder) / "source.pdf"
        document.save(path)
        count = len(document)
        document.close()
        extraction = {"parser_version": QUALIFIED_PARSER_VERSION,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "page_count": count, "holdings": holdings, "excluded": [], "quarantined": []}
        return path, extraction

    def covered(self, path, extraction):
        extraction["source_coverage_evidence"] = observe_native_holding_coverage(path, extraction)
        return annual_holdings_coverage_complete(extraction)

    def test_complete_native_tables_are_reconciled_without_changing_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            path, extraction = self.fixture(folder)
            rows = deepcopy(extraction["holdings"])
            self.assertTrue(self.covered(path, extraction))
            self.assertEqual(extraction["holdings"], rows)

    def test_a_dropped_row_is_found_from_the_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            path, extraction = self.fixture(folder)
            extraction["holdings"].pop(0)
            self.assertFalse(self.covered(path, extraction))

    def test_unparsed_text_and_raster_marks_block_coverage(self):
        for options in [{"outside": True}, {"image": True}, {"unknown": True}]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                path, extraction = self.fixture(folder, **options)
                self.assertFalse(self.covered(path, extraction))

    def test_changing_a_cell_or_dropping_a_page_after_inventory_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, extraction = self.fixture(folder)
            self.assertTrue(self.covered(path, extraction))
            changed = deepcopy(extraction)
            changed["holdings"][0]["cells"][1] = "Different asset"
            self.assertFalse(annual_holdings_coverage_complete(changed))
            extraction["source_coverage_evidence"]["pages"].pop()
            self.assertFalse(annual_holdings_coverage_complete(extraction))

    def test_wrong_original_and_wrong_page_count_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, extraction = self.fixture(folder)
            changed = deepcopy(extraction)
            changed["source_sha256"] = "0" * 64
            with self.assertRaises(OgeCatalogError):
                self.covered(path, changed)
            extraction["page_count"] += 1
            with self.assertRaises(OgeCatalogError):
                self.covered(path, extraction)


if __name__ == "__main__":
    unittest.main()
