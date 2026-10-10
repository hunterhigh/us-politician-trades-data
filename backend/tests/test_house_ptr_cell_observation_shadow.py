import importlib.util
import unittest
from types import SimpleNamespace

from unison_snapshot.house_ptr_cell_observation_shadow import _cell, observe_house_ptr_pdf


HAS_RASTER = importlib.util.find_spec("PIL") is not None and importlib.util.find_spec("numpy") is not None


class HousePtrCellObservationShadowTests(unittest.TestCase):
    @unittest.skipUnless(HAS_RASTER, "raster libraries are optional in contract CI")
    def test_cell_preserves_raw_and_crop_without_qualifying_date(self):
        from PIL import Image

        image = Image.new("RGB", (200, 100), "white")
        page = SimpleNamespace(width=200, height=100)
        words = [{"x0": 20, "x1": 80, "top": 20, "bottom": 30,
                  "text": "07/24/26", "ocr_confidence": 93.0}]
        observed = _cell(image, words, page, label="event_date",
                         x0=.1, x1=.5, y0=.1, y1=.4)
        self.assertEqual(observed["reading"], "date_shape_match")
        self.assertEqual(observed["raw_text"], "07/24/26")
        self.assertEqual(observed["bbox_points"], [20, 10, 100, 40])
        self.assertEqual(observed["bbox_pixels"], [20, 10, 100, 40])
        self.assertEqual(len(observed["crop_sha256"]), 64)
        self.assertEqual(observed["status"], "raw_unverified")
        self.assertIsNone(observed["value"])

    @unittest.skipUnless(HAS_RASTER, "raster libraries are optional in contract CI")
    def test_checkbox_ocr_never_becomes_a_direction(self):
        from PIL import Image

        image = Image.new("RGB", (100, 100), "white")
        page = SimpleNamespace(width=100, height=100)
        observed = _cell(image, [], page, label="sale",
                         x0=.1, x1=.2, y0=.1, y1=.2)
        self.assertEqual(observed["reading"], "mark_unknown")
        self.assertIsNone(observed["value"])

    @unittest.skipUnless(HAS_RASTER, "raster libraries are optional in contract CI")
    def test_border_noise_is_preserved_around_date_shape(self):
        from PIL import Image

        image = Image.new("RGB", (100, 100), "white")
        page = SimpleNamespace(width=100, height=100)
        words = [{"x0": 10, "x1": 50, "top": 10, "bottom": 20,
                  "text": "| 07/28/26", "ocr_confidence": 60.0}]
        observed = _cell(image, words, page, label="event_date",
                         x0=.1, x1=.5, y0=.1, y1=.3)
        self.assertEqual(observed["reading"], "date_shape_with_noise")
        self.assertEqual(observed["raw_text"], "| 07/28/26")
        self.assertIsNone(observed["value"])

    def test_document_hash_guard(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            observe_house_ptr_pdf(b"not-a-pdf", source_sha256="0" * 64)
