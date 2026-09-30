import importlib
from pathlib import Path
import sys
import unittest

try:
    from PIL import Image, ImageDraw
except ImportError:  # Optional dependency for this offline evidence audit.
    Image = ImageDraw = None


SCRIPTS = Path(__file__).parents[1] / "scripts"


@unittest.skipIf(Image is None, "Pillow not installed for offline image audit")
class SenatePaperFirstReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPTS))
        cls.audit = importlib.import_module("audit_senate_paper_first_report")

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(SCRIPTS))

    def test_mark_crop_rejects_adjacent_grid_rule(self):
        image = Image.new("L", (3400, 4400), 255)
        draw = ImageDraw.Draw(image)
        draw.line((1450, 2170, 1450, 2220), fill=0, width=3)
        self.assertFalse(self.audit._mark_cell(image, 2195, "purchase", 1429)["mark_signal"])
        draw.line((1419, 2185, 1439, 2205), fill=0, width=5)
        draw.line((1419, 2205, 1439, 2185), fill=0, width=5)
        self.assertTrue(self.audit._mark_cell(image, 2195, "purchase", 1429)["mark_signal"])

    def test_fixed_report_classification_keeps_trades_quarantined(self):
        marked = [{"mark_signal": True}]
        unmarked = [{"mark_signal": False}]
        classify = self.audit._classify_row
        self.assertEqual(classify(2, 2, 200, marked, marked, True),
                         "transaction_observed_quarantined")
        self.assertEqual(classify(2, 1, 200, unmarked, unmarked, True),
                         "section_heading_excluded")
        self.assertEqual(classify(5, 9, 0, unmarked, unmarked, True),
                         "ocr_grid_noise_excluded")
        self.assertEqual(classify(2, 2, 200, marked, unmarked, True),
                         "unresolved_quarantined")


if __name__ == "__main__":
    unittest.main()
