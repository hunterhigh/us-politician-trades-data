"""Bounded OCR geometry must preserve lines and survive non-UTF8 noise."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unison_snapshot.ocr_geometry import OcrPage, words_from_tesseract_tsv


class OcrGeometryTests(unittest.TestCase):
    def test_tsv_bytes_are_decoded_and_grouped_without_locale_dependence(self):
        raw = (b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               b"5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t96.5\tHello\n"
               b"5\t1\t1\t1\t1\t2\t45\t20\t20\t10\t91\tW\x94rld\n")
        words = words_from_tesseract_tsv(raw, points_per_pixel=0.5)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0]["x0"], 5.0)
        self.assertNotIn("\ufffd", words[1]["text"])
        page = OcrPage(width=100, height=200, words=words)
        lines = page.extract_text_lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["text"].startswith("Hello"))
        self.assertEqual(len(lines[0]["words"]), 2)

    def test_quote_token_cannot_consume_following_tsv_rows(self):
        raw = (
            b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\r\n"
            b"5\t1\t1\t1\t1\t1\t10\t20\t5\t8\t95\t\"\r\n"
            b"5\t1\t1\t1\t1\t2\t20\t20\t20\t8\t96\tNEXT\r\n")
        words = words_from_tesseract_tsv(raw, points_per_pixel=1.0)
        self.assertEqual([word["text"] for word in words], ['"', "NEXT"])


if __name__ == "__main__":
    unittest.main()
