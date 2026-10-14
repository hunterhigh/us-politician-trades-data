import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from PIL import Image
except ImportError:
    Image = None

from unison_snapshot.house_ptr_key_field_reread import (
    _conflict_probe, reread_doubtful_key_cells)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_house_ptr_key_fields_shadow import report


class HousePtrKeyFieldRereadTests(unittest.TestCase):
    def setUp(self):
        self.pdf = b"fixed pdf"
        self.observation = report()
        self.observation["source_sha256"] = hashlib.sha256(self.pdf).hexdigest()
        page = self.observation["pages"][0]
        page.update({"coordinate_frame": "original_page", "orientation_degrees": 0})
        asset = page["rows"][0]["cells"]["asset"]
        asset["raw_ocr_confidence"] = 42.0
        asset["crop_sha256"] = "a" * 64
        asset["bbox_normalized"] = [.1, .1, .9, .3]

    @unittest.skipIf(Image is None, "Pillow is required for image-crop replay")
    def test_second_read_preserves_conflict_without_changing_first(self):
        image = Image.new("L", (100, 100), color=255)
        page = SimpleNamespace(to_image=lambda resolution: SimpleNamespace(original=image))
        document = SimpleNamespace(pages=[page], __enter__=lambda self: self,
                                   __exit__=lambda self, *args: None)

        class Pdf:
            def __enter__(self):
                return document

            def __exit__(self, *args):
                pass

        with patch("pdfplumber.open", return_value=Pdf()), patch(
                "unison_snapshot.house_ptr_key_field_reread._second_ocr",
                return_value=("CARLISLE CORP", "b" * 64)), patch(
                "unison_snapshot.house_ptr_key_field_reread._conflict_probe",
                return_value={"raw": "CARLISLE INC", "status":
                              "alternate_ocr_unverified"}):
            result = reread_doubtful_key_cells(
                self.pdf, self.observation, executable=Path("tesseract"))
        self.assertEqual(result["selected_cell_count"], 1)
        row = result["rereads"][0]
        self.assertEqual(row["first_ocr_raw"], "CARLISLE INC")
        self.assertEqual(row["second_ocr_raw"], "CARLISLE CORP")
        self.assertEqual(row["comparison"], "second_ocr_shape_differs")
        self.assertEqual(row["conflict_probe"]["shape_comparison_to_first"],
                         "agrees")
        self.assertEqual(result["qualification_status"], "unverified_shadow_only")
        self.assertEqual(self.observation["pages"][0]["rows"][0]["cells"]
                         ["asset"]["raw_text"], "CARLISLE INC")

        with patch("pdfplumber.open", return_value=Pdf()), patch(
                "unison_snapshot.house_ptr_key_field_reread._second_ocr",
                return_value=("CARLISLE CORP", "b" * 64)), patch(
                "unison_snapshot.house_ptr_key_field_reread._conflict_probe",
                side_effect=ValueError("probe unavailable")):
            fallback = reread_doubtful_key_cells(
                self.pdf, self.observation, executable=Path("tesseract"))
        self.assertEqual(fallback["rereads"][0]["comparison"],
                         "second_ocr_shape_differs")
        self.assertEqual(fallback["rereads"][0]["conflict_probe"]["status"],
                         "alternate_ocr_unavailable")

    @unittest.skipIf(Image is None, "Pillow is required for image-crop replay")
    def test_conflict_probe_records_image_variant_without_a_fact(self):
        image = Image.new("L", (100, 30), color=255)
        completed = SimpleNamespace(returncode=0, stdout=b"05/18/2026\n")
        with patch("unison_snapshot.house_ptr_key_field_reread.subprocess.run",
                   return_value=completed) as run:
            date = _conflict_probe(image, Path("tesseract"), "event_date")
        self.assertEqual(run.call_args.args[0][-1], "7")
        self.assertEqual(date["image_border_pixels"], 20)
        self.assertEqual(date["status"], "alternate_ocr_unverified")
        self.assertEqual(date["raw"], "05/18/2026")
        self.assertNotIn("value", date)
        with patch("unison_snapshot.house_ptr_key_field_reread.subprocess.run",
                   return_value=completed) as run:
            asset = _conflict_probe(image, Path("tesseract"), "asset")
        self.assertEqual(run.call_args.args[0][-1], "11")
        self.assertEqual(asset["image_border_pixels"], 0)

    def test_source_mismatch_and_selection_cap_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            reread_doubtful_key_cells(b"different", self.observation,
                                     executable=Path("tesseract"))
        with self.assertRaisesRegex(ValueError, "cap is invalid"):
            reread_doubtful_key_cells(self.pdf, self.observation,
                                     executable=Path("tesseract"), max_cells=0)


if __name__ == "__main__":
    unittest.main()
