"""Allocation guards for native source rendering and non-date quad replay.

Large outputs are rejected with render/warp/pad mocked to be unreachable. Quad
inputs stay within16M grayscale pixels; the small control uses real PDFium.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from test_pdf_text_objects import source_pdf
from unison_snapshot.pdf_text_objects import render_pdf_text_objects
from unison_snapshot.oge_278t_source_quad_cells import source_quad_cell


def canvas_receipt(width=8000, height=2000):
    # Exact 300dpi, with an actual-canvas affine rather than nominal page DPI.
    image = Image.new('L', (width, height), 255)
    media_width, media_height = width * .24, height * .24
    receipt = {'source_sha256': 'a' * 64, 'page_number': 1, 'resolution': 300,
        'source_mediabox_pdf_bottom_left': [0, 0, media_width, media_height],
        'source_cropbox_pdf_bottom_left': [0, 0, media_width, media_height],
        'source_rotation_degrees': 0, 'pixel_size': [width, height],
        'pixel_edge_to_source_pdf_affine': [.24, 0, 0, -.24, 0, media_height]}
    return {'receipt': receipt, 'all_image': image, 'text_image': image}


def quad_at_pixels(left, top, right, bottom):
    return [[left * .24, top * .24], [right * .24, top * .24],
            [right * .24, bottom * .24], [left * .24, bottom * .24]]


class SourceQuadRenderBudgetTests(unittest.TestCase):
    def test_real_small_rotated_nonzero_media_control_keeps_actual_canvas_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            sha = source_pdf(path, rotate=90, media='10 20 210 140', crop='15 25 205 135')
            result = render_pdf_text_objects(path, expected_sha256=sha, page_number=1, resolution=73)
            self.assertEqual(result['receipt']['pixel_size'], [122, 203])
            self.assertEqual(result['receipt']['source_rotation_degrees'], 90)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
            sha = source_pdf(path)
            result = render_pdf_text_objects(path, expected_sha256=sha, page_number=1, resolution=144)
            with patch('unison_snapshot.oge_278t_raster_slots.slot_quad',
                       return_value=[[30, 30], [60, 30], [60, 45], [30, 45]]):
                recipe, pixels = source_quad_cell(result, {}, {}, 'type', layer='full_source')
            self.assertEqual(recipe['crop_pixel_size'], [100, 70])
            self.assertEqual(list(pixels.shape), [70, 100])

    def test_media_expansion_pixel_area_and_edge_budget_reject_before_page_render(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            cases = [
                # A small original CropBox must not conceal the MediaBox canvas.
                ('0 0 100000 100000', '0 0 100 100', 300),
                # Area exceeds16M, although each edge is below10000.
                ('0 0 4001 4000', '0 0 4001 4000', 72),
                # Only the edge budget is exceeded; area remains small.
                ('0 0 10001 10', '0 0 10001 10', 72),
            ]
            for media, crop, resolution in cases:
                with self.subTest(media=media, resolution=resolution):
                    sha = source_pdf(path, media=media, crop=crop)
                    with patch.object(pdfium.PdfPage, 'render', side_effect=AssertionError('Render must be guarded')) as render:
                        with self.assertRaises(ValueError):
                            render_pdf_text_objects(path, expected_sha256=sha, page_number=1, resolution=resolution)
                    render.assert_not_called()

    def test_source_byte_budget_is_checked_before_source_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.pdf'
            sha = source_pdf(path)
            # Sparse enlargement exercises stat preflight without allocating a
            # 50MiB Python byte string or opening it in PDFium.
            with path.open('r+b') as handle:
                handle.truncate(50 * 1024 * 1024 + 1)
            original = Path.read_bytes
            def guarded_read(target, *args, **kwargs):
                if target == path:
                    raise AssertionError('Source byte budget must precede read_bytes')
                return original(target, *args, **kwargs)
            with patch.object(Path, 'read_bytes', guarded_read), patch.object(pdfium.PdfPage, 'render') as render:
                with self.assertRaises(ValueError):
                    render_pdf_text_objects(path, expected_sha256=sha, page_number=1)
            render.assert_not_called()

    def assert_prewarp_rejection(self, rendered, quad):
        with patch('unison_snapshot.oge_278t_raster_slots.slot_quad', return_value=quad), \
                patch.object(cv2, 'warpPerspective', side_effect=AssertionError('Warp must be guarded')) as warp, \
                patch.object(np, 'pad', side_effect=AssertionError('Padding must be guarded')) as pad:
            with self.assertRaises(ValueError):
                source_quad_cell(rendered, {}, {}, 'type', layer='full_source')
        warp.assert_not_called()
        pad.assert_not_called()

    def test_quad_padding_and_canvas_budgets_are_known_before_warp_or_pad(self):
        # The unpadded crop fits16M, but its40px border would exceed16M.
        self.assert_prewarp_rejection(canvas_receipt(), quad_at_pixels(1, 1, 7991, 1991))
        # A narrow crop fits the area bound, but padded width would exceed10000.
        self.assert_prewarp_rejection(canvas_receipt(10000, 100), quad_at_pixels(1, 1, 9981, 11))
        # The source canvas itself may not exceed either allocation bound.
        self.assert_prewarp_rejection(canvas_receipt(10001, 10), quad_at_pixels(1, 1, 20, 5))

    def test_nonfinite_or_singular_geometry_rejects_before_allocations(self):
        rendered = canvas_receipt(100, 100)
        valid = quad_at_pixels(1, 1, 20, 20)
        for quad in ([[float('nan'), 1], *valid[1:]],
                     [[float('inf'), 1], *valid[1:]]):
            with self.subTest(quad=quad):
                self.assert_prewarp_rejection(rendered, quad)
        for affine in ([0, 0, 0, 0, 0, 0], [float('nan'), 0, 0, -.24, 0, 24],
                       [float('inf'), 0, 0, -.24, 0, 24]):
            changed = deepcopy(rendered)
            changed['receipt']['pixel_edge_to_source_pdf_affine'] = affine
            with self.subTest(affine=affine):
                self.assert_prewarp_rejection(changed, valid)


if __name__ == '__main__':
    unittest.main()
