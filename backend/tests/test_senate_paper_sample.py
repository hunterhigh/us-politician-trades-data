import json
from pathlib import Path
import unittest

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper_sample import (
    OCR_VERSION, _record_row, observe_fixed_row,
)


FIXTURE = Path(__file__).parent / "fixtures" / "senate_paper_row2.json"


class SenatePaperSampleTests(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.words = [word for key in (
            "asset_words", "date_words", "type_cell_ocr_words", "amount_cell_ocr_words"
        ) for word in self.expected[key]]

    def test_fixed_row_observations_are_exact_and_quarantined(self):
        self.assertEqual(_record_row(self.words, ocr_version=OCR_VERSION), self.expected)
        self.assertEqual(self.expected["transaction_type"], None)
        self.assertEqual(self.expected["amount_raw"], None)

    def test_ocr_marks_do_not_promote_without_cell_verification(self):
        words = self.words + [
            {"text": "X", "left": 1510, "top": 2270, "width": 30,
             "height": 30, "confidence": 98.0},
            {"text": "X", "left": 1980, "top": 2270, "width": 30,
             "height": 30, "confidence": 98.0},
        ]
        observed = _record_row(words, ocr_version=OCR_VERSION)
        self.assertEqual(observed["qualification_status"], "quarantined")
        self.assertIsNone(observed["transaction_type"])
        self.assertIsNone(observed["amount_raw"])

    def test_wrong_image_is_rejected_before_observation(self):
        with self.assertRaisesRegex(SenateEfdError, "image hash"):
            observe_fixed_row(b"wrong archived page", self.words,
                              ocr_version=OCR_VERSION)


if __name__ == "__main__":
    unittest.main()
