import importlib.util
from pathlib import Path
import unittest

try:
    from PIL import Image, ImageDraw
except ImportError:  # Pillow is only needed for the offline audit.
    Image = ImageDraw = None


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_senate_paper_pages.py"


@unittest.skipIf(Image is None, "Pillow not installed for offline image audit")
class SenatePaperPageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("senate_paper_page_audit", SCRIPT)
        cls.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.audit)

    def _page(self, *, faint=False):
        image = Image.new("L", (3400, 4400), 255)
        draw = ImageDraw.Draw(image)
        for index in range(16):
            y = 1830 + index * 132
            draw.line((700 if faint else 525, y,
                       1080 if faint else 2920, y),
                      fill=150 if faint else 0, width=4)
        draw.rectangle((700, 2120, 790, 2155), fill=0)
        draw.rectangle((700, 2250, 800, 2285), fill=0)
        return image

    def test_first_form_examples_are_excluded_and_ink_only_row_retained(self):
        words = [
            {"text": "Asset", "left": 740, "top": 2130, "width": 45, "height": 20},
            {"text": "1/9/26", "left": 1730, "top": 2130, "width": 75, "height": 20},
        ]
        result = self.audit._page_inventory(self._page(), words)
        self.assertTrue(result["table_grid_detected"])
        self.assertTrue(result["printed_examples_detected"])
        self.assertEqual(result["filled_grid_band_signal_count"], 2)
        self.assertEqual(result["ocr_content_band_count"], 1)
        self.assertEqual([band["grid_slot"] for band in result["filled_grid_bands"]], [1, 2])
        self.assertTrue(all(band["disposition"] == "quarantined_unverified_paper_row"
                            for band in result["filled_grid_bands"]))

    def test_faint_asset_cell_grid_uses_explicit_fallback(self):
        result = self.audit._page_inventory(self._page(faint=True), [])
        self.assertTrue(result["table_grid_detected"])
        self.assertEqual(result["grid_detection_method"], "faint_asset_cell_projection")
        self.assertEqual(result["filled_grid_band_signal_count"], 2)


if __name__ == "__main__":
    unittest.main()
