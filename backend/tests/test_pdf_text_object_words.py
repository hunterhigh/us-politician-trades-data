import hashlib
from pathlib import Path
import tempfile
import unittest
from contextlib import closing

import pypdfium2 as pdfium

from unison_snapshot.pdf_text_object_words import observe_pdf_text_objects, TextObjectWordPage, validate_saved_native_words
from unison_snapshot.ocr_geometry import OcrPage, OcrGeometryError
from copy import deepcopy
from test_pdf_text_objects import source_pdf


class SourceTextObjectWordsTests(unittest.TestCase):
    def test_saved_native_records_require_exact_original_without_invented_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"; sha = source_pdf(path)
            words = observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)["words"]
            receipt = validate_saved_native_words(path, expected_sha256=sha, page_number=1, words=words)
            self.assertEqual(receipt["native_word_count"], 1)
            for key, value in (("text", "OTHER"), ("x0", 0), ("source_object_index", 999),
                               ("line_num", 1), ("ocr_confidence", 99)):
                changed = deepcopy(words); changed[0][key] = value
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "original text object"):
                    validate_saved_native_words(path, expected_sha256=sha, page_number=1, words=changed)
            page = OcrPage(width=200, height=120, words=words)
            self.assertEqual(page.extract_words(), words)
            with self.assertRaisesRegex(OcrGeometryError, "logical OCR lines"):
                page.extract_text_lines()

    def test_raw_text_and_tight_bounds_match_direct_source_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"; sha = source_pdf(path)
            observation = observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            with pdfium.PdfDocument(path) as pdf, closing(pdf[0]) as page, closing(page.get_textpage()) as tp:
                direct = [(i, obj.extract(), list(obj.get_bounds()))
                          for i, obj in enumerate(page.get_objects(max_depth=1, textpage=tp))
                          if isinstance(obj, pdfium.PdfTextObj)]
            self.assertEqual(len(direct), len(observation["objects"]))
            for (index, text, bounds), record in zip(direct, observation["objects"]):
                self.assertEqual(record["source_object_index"], index)
                self.assertEqual(record["raw_text"], text)
                self.assertEqual(record["bounds_pdf_bottom_left"], bounds)
                self.assertEqual(record["bbox_displayed_mediabox"],
                                 [bounds[0], 120 - bounds[3], bounds[2], 120 - bounds[1]])
            self.assertFalse(observation["is_ocr"])
            self.assertEqual(observation["words"][0]["ocr_confidence"], 0)
            self.assertEqual(observation["words"][0]["confidence_basis"], "not_applicable_not_ocr")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)

    def test_hidden_text_is_inventory_not_visible_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, extra="BT /F1 12 Tf 3 Tr 25 30 Td (INVISIBLE) Tj ET")
            x = observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            self.assertEqual(len(x["objects"]), 2)
            self.assertEqual(len(x["words"]), 1)
            self.assertEqual(x["objects"][1]["raw_text"], "INVISIBLE")
            self.assertIn("source_text_render_mode_invisible", x["objects"][1]["exclusion_reasons"])

    def test_nonzero_media_origin_does_not_use_crop_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, media="10 20 210 140", crop="15 25 205 135")
            x = observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            b = x["objects"][0]["bounds_pdf_bottom_left"]
            self.assertEqual(x["objects"][0]["bbox_displayed_mediabox"],
                             [b[0] - 10, 140 - b[3], b[2] - 10, 140 - b[1]])
            self.assertEqual([x["width"], x["height"]], [200, 120])

    def test_rotation_clipping_hash_page_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, rotate=90)
            with self.assertRaisesRegex(ValueError, "rotation"):
                observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            sha = source_pdf(path, extra="BT /F1 12 Tf 4 Tr 25 30 Td (CLIP) Tj ET")
            with self.assertRaisesRegex(ValueError, "clipping"):
                observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            for args in ({"expected_sha256": "0" * 64, "page_number": 1},
                         {"expected_sha256": sha, "page_number": 2},
                         {"expected_sha256": sha, "page_number": True}):
                with self.assertRaises(ValueError): observe_pdf_text_objects(path, **args)

    def test_source_order_not_top_sort_and_word_view_not_line_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, extra="BT /F1 12 Tf 25 85 Td (LATER OBJECT AT EARLIER Y) Tj ET")
            x = observe_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            self.assertEqual(x["words"][0]["text"], "ALPHA 7/11/2025")
            self.assertEqual(x["words"][1]["text"], "LATER OBJECT AT EARLIER Y")
            page = TextObjectWordPage(x)
            words = page.extract_words()
            words[0]["observation_issues"].append("EDIT")
            self.assertNotIn("EDIT", page.extract_words()[0]["observation_issues"])
            with self.assertRaisesRegex(ValueError, "logical line"): page.extract_text()
            with self.assertRaisesRegex(ValueError, "logical line"): page.extract_text_lines()
