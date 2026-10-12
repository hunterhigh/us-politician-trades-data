import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from unison_snapshot.house_ptr_cell_observation_shadow import (_cell, _column_boxes,
                                                              observe_house_ptr_pdf)


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
    def test_asset_word_order_uses_position_within_one_line(self):
        from PIL import Image

        image = Image.new("RGB", (300, 100), "white")
        page = SimpleNamespace(width=300, height=100)
        words = [
            {"x0": 100, "x1": 160, "top": 20, "bottom": 28,
             "text": "COMPANIES", "ocr_confidence": 90.0},
            {"x0": 20, "x1": 90, "top": 23.6, "bottom": 31,
             "text": "CARLISLE", "ocr_confidence": 90.0},
            {"x0": 170, "x1": 190, "top": 23.6, "bottom": 31,
             "text": "INC", "ocr_confidence": 90.0},
        ]
        observed = _cell(image, words, page, label="asset",
                         x0=0, x1=.8, y0=.1, y1=.4)
        self.assertEqual(observed["raw_text"], "CARLISLE COMPANIES INC")

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

    def test_short_grid_locates_asset_and_both_date_columns(self):
        region = {"bands": [{"upper": .1, "vertical_x": [.12, .35, .46, .53, .60]},
                            {"upper": .2}], "header_band": [.2, .25],
                  "column_family": "short_grid"}
        rules = [.06, .12, .35, .375, .405, .43, .46] + [round(.60 + .03 * n, 3)
                                                for n in range(12)]
        with patch("unison_snapshot.house_ptr_cell_observation_shadow._vertical_rules",
                   return_value=rules):
            columns = _column_boxes(region, None)
        self.assertEqual(columns["status"], "located_shadow")
        self.assertEqual(columns["owner"], [.06, .12])
        self.assertEqual(columns["asset"], [.12, .35])
        self.assertEqual(columns["event"], [.46, .53])
        self.assertEqual(columns["notification"], [.53, .60])

    @unittest.skipUnless(HAS_RASTER, "raster libraries are optional in contract CI")
    def test_notification_shape_is_only_raw_reading(self):
        from PIL import Image

        image = Image.new("RGB", (100, 100), "white")
        page = SimpleNamespace(width=100, height=100)
        words = [{"x0": 54, "x1": 59, "top": 10, "bottom": 20,
                  "text": "07/25/26", "ocr_confidence": 90.0}]
        observed = _cell(image, words, page, label="notification_date",
                         x0=.53, x1=.60, y0=.1, y1=.3)
        self.assertEqual(observed["reading"], "date_shape_match")
        self.assertIsNone(observed["value"])
