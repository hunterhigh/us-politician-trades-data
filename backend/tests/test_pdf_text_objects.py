import hashlib
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.pdf_text_objects import render_pdf_text_objects


def source_pdf(path, *, extra="", rotate=0, media="0 0 200 120", crop="10 5 190 115"):
    stream = f"BT /F1 14 Tf 25 60 Td (ALPHA 7/11/2025) Tj ET 10 20 180 2 re f {extra}".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               f"<< /Type /Page /Parent 2 0 R /MediaBox [{media}] /CropBox [{crop}] /Rotate {rotate} /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    result = b"%PDF-1.4\n"; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result)); result += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{i:010} 00000 n \n".encode() for i in offsets[1:])
    result += f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()
    path.write_bytes(result)
    return hashlib.sha256(result).hexdigest()


class PdfTextObjectsTests(unittest.TestCase):
    def test_same_source_glyphs_and_positions_with_nontext_loss_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"; sha = source_pdf(path)
            result = render_pdf_text_objects(path, expected_sha256=sha, page_number=1, resolution=144)
            r = result["receipt"]
            self.assertEqual(r["pixel_size"], [400, 240])
            self.assertEqual(r["pixel_edge_to_source_pdf_anchors"]["top_left"], [0., 120.])
            self.assertEqual(r["pixel_edge_to_source_pdf_anchors"]["bottom_right"], [200., 0.])
            self.assertTrue(r["object_manifest_restored_equal"])
            self.assertTrue(r["all_object_render_restored_equal"])
            self.assertTrue(r["original_media_view_and_imported_all_object_render_equal"])
            self.assertGreater(r["comparison"]["full_dark_pixels_absent_from_text_selection"], 0)
            self.assertEqual(r["complete_page_or_financial_text_coverage"], "not_established")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
            self.assertNotEqual(r["pixel_sha256"]["all"], r["pixel_sha256"]["text"])

    def test_rotation_and_nonzero_media_origin_use_actual_device_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, rotate=90, media="10 20 210 140", crop="15 25 205 135")
            r = render_pdf_text_objects(path, expected_sha256=sha, page_number=1, resolution=73)["receipt"]
            self.assertEqual(r["source_rotation_degrees"], 90)
            self.assertEqual(r["pixel_size"], [122, 203])
            for actual, expected in zip(r["pixel_edge_to_source_pdf_anchors"]["top_left"], [10., 20.]):
                self.assertAlmostEqual(actual, expected, delta=.0001)
            for actual, expected in zip(r["pixel_edge_to_source_pdf_anchors"]["bottom_right"], [210., 140.]):
                self.assertAlmostEqual(actual, expected, delta=.0001)

    def test_sha_page_and_dpi_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"; sha = source_pdf(path)
            for kwargs in ({"expected_sha256": "0" * 64}, {"page_number": True},
                           {"page_number": 2}, {"resolution": 0}):
                args = dict(expected_sha256=sha, page_number=1)
                args.update(kwargs)
                with self.assertRaises(ValueError): render_pdf_text_objects(path, **args)

    def test_text_clipping_not_silently_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, extra="BT /F1 12 Tf 4 Tr 25 30 Td (CLIP) Tj ET")
            with self.assertRaisesRegex(ValueError, "clipping"):
                render_pdf_text_objects(path, expected_sha256=sha, page_number=1)

    def test_nontext_financial_punctuation_is_not_claimed_complete(self):
        # The number glyphs are text objects; a comma-like mark is a path.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, extra="BT /F1 14 Tf 25 85 Td ($1 000) Tj ET 41 82 1 3 re f")
            result = render_pdf_text_objects(path, expected_sha256=sha, page_number=1, resolution=144)
            # Source x41..42/y82..85 maps to pixels82..84/top70..76.
            self.assertLess(result["all_image"].getpixel((83, 73)), 160)
            self.assertGreaterEqual(result["text_image"].getpixel((83, 73)), 160)
            self.assertEqual(result["receipt"]["complete_page_or_financial_text_coverage"], "not_established")

    def test_removed_occluder_is_reported_as_exposed_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            sha = source_pdf(path, extra="1 g 24 55 160 25 re f")
            result = render_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            self.assertGreater(result["receipt"]["comparison"]["text_dark_pixels_not_visible_in_full"], 0)
