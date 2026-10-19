"""Synthetic geometry regressions, not official-source OCR accuracy tests."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None

from unison_snapshot.oge_278t_raster_slots import (
    _oge_2024_column_geometry, _observe_slot_columns, _rule_pixel_confirmation,
    observe_raster_slots,
)


EDGES = [103., 148., 458., 509., 566., 613., 695.]


def page_fixture(*, weak=(5,), scale=3):
    lines = [{"x0": x, "x1": x, "y0": 72., "y1": 152. if i in weak else 572.,
              "top": 72., "bottom": 152. if i in weak else 572.}
             for i, x in enumerate(EDGES)]
    image = Image.new("L", (792 * scale, 612 * scale), 255)
    pixels = np.asarray(image).copy()
    for i in weak:
        pixels[72 * scale:572 * scale, int(EDGES[i] * scale)] = 0
    page = SimpleNamespace(lines=lines, width=792, height=612, bbox=(0, 0, 792, 612),
                           rotation=90, to_image=Mock(return_value=SimpleNamespace(
                               original=Image.fromarray(pixels))))
    return page


@unittest.skipUnless(np is not None and Image is not None, "optional raster dependencies unavailable")
class OgeRasterColumnGeometryTests(unittest.TestCase):
    def test_strong_vector_path_retains_edges_without_rendering(self):
        page = page_fixture(weak=())
        result = _oge_2024_column_geometry(page)
        self.assertEqual(result["edges_pdf_points"], EDGES)
        self.assertEqual(result["basis"], "strong_vector_rules")
        page.to_image.assert_not_called()

    def test_short_same_page_vector_requires_continuous_pixel_witness(self):
        result = _oge_2024_column_geometry(page_fixture())
        self.assertEqual(result["edges_pdf_points"], EDGES)
        self.assertEqual(result["basis"], "same_page_vector_and_pixel_rules")
        witness = result["weak_rule_observations"][0]
        self.assertEqual(witness["continuous_path_scanlines"], witness["checked_scanlines"])
        self.assertEqual(witness["thin_rule_support_by_third"], [1., 1., 1.])
        self.assertGreater(witness["vector_uncovered_span_pdf_points"][1] -
                           witness["vector_uncovered_span_pdf_points"][0], 40)

    def test_display_rotation_and_actual_image_scale_share_page_coordinates(self):
        # Renderer may round or scale independently of requested DPI. It already
        # applies /Rotate; line boxes and pixels both use the displayed page.
        page = page_fixture(scale=2)
        self.assertEqual(_oge_2024_column_geometry(page)["edges_pdf_points"], EDGES)
        page.to_image.assert_called_once_with(resolution=216, force_mediabox=True)

    def test_missing_pixel_rule_remains_unknown(self):
        page = page_fixture()
        page.to_image.return_value.original = Image.new("L", (2376, 1836), 255)
        result = _oge_2024_column_geometry(page)
        self.assertIsNone(result["edges_pdf_points"])
        self.assertEqual(result["reason"], "weak_vector_not_confirmed_by_pixels")

    def test_pixels_only_on_existing_short_vector_do_not_confirm_full_column(self):
        page = page_fixture()
        pixels = np.full((1836, 2376), 255, np.uint8)
        pixels[72 * 3:152 * 3, int(EDGES[5] * 3)] = 0
        page.to_image.return_value.original = Image.fromarray(pixels)
        self.assertIsNone(_oge_2024_column_geometry(page)["edges_pdf_points"])

    def test_all_weak_rules_need_confirmation(self):
        page = page_fixture(weak=(4, 5))
        pixels = np.asarray(page.to_image.return_value.original).copy()
        pixels[:, int(EDGES[5] * 3)] = 255
        page.to_image.return_value.original = Image.fromarray(pixels)
        result = _oge_2024_column_geometry(page)
        self.assertIsNone(result["edges_pdf_points"])
        self.assertEqual([item["status"] for item in result["weak_rule_observations"]],
                         ["confirmed", "unknown"])

    def test_missing_candidate_x_is_never_invented_from_column_widths(self):
        page = page_fixture()
        del page.lines[5]
        self.assertIsNone(_oge_2024_column_geometry(page)["edges_pdf_points"])
        page.to_image.assert_not_called()

    def test_extra_rule_wrong_profile_or_too_few_strong_rules_do_not_render(self):
        for kind in ("extra", "width", "weak"):
            with self.subTest(kind=kind):
                page = page_fixture(weak=(3, 4, 5) if kind == "weak" else (5,))
                if kind == "extra":
                    line = deepcopy(page.lines[0])
                    line.update(x0=300., x1=300.)
                    page.lines.append(line)
                if kind == "width":
                    page.lines[3].update(x0=530., x1=530.)
                self.assertIsNone(_oge_2024_column_geometry(page)["edges_pdf_points"])
                page.to_image.assert_not_called()

    def test_cropped_origin_stays_unknown(self):
        page = page_fixture()
        page.bbox = (10, 10, 802, 622)
        self.assertIsNone(_oge_2024_column_geometry(page)["edges_pdf_points"])
        page.to_image.assert_not_called()

    def test_short_shared_grid_span_stays_unknown(self):
        page = page_fixture()
        # Repeated vector fragments can sum to the strong threshold while
        # occupying only a small header region. They cannot define a data grid.
        for line in page.lines:
            if line["bottom"] > 200:
                line.update(top=460., bottom=572., y0=460., y1=572.)
        page.lines = page.lines * 2
        self.assertIsNone(_oge_2024_column_geometry(page)["edges_pdf_points"])
        page.to_image.assert_not_called()


@unittest.skipUnless(np is not None, "optional numpy dependency unavailable")
class OgePixelRuleTests(unittest.TestCase):
    def confirm(self, image):
        return _rule_pixel_confirmation(image, x=10., top=0., bottom=180.,
                                         scale_x=1., scale_y=1.)

    def test_continuous_thin_rule_and_horizontal_intersections(self):
        image = np.full((180, 20), 255, np.uint8)
        image[:, 10] = 0
        image[::15, :] = 0
        self.assertEqual(self.confirm(image)["status"], "confirmed")

    def test_missing_middle_or_one_empty_third_cannot_hide_behind_average(self):
        for start, end in ((80, 81), (60, 120)):
            image = np.full((180, 20), 255, np.uint8)
            image[:, 10] = 0
            image[start:end, :] = 255
            self.assertEqual(self.confirm(image)["status"], "unknown")

    def test_shifted_text_fragments_cannot_relay_between_regions(self):
        image = np.full((180, 20), 255, np.uint8)
        image[:60, 8] = 0
        image[60:120, 12] = 0
        image[120:, 8] = 0
        self.assertEqual(self.confirm(image)["status"], "unknown")

    def test_black_block_horizontal_rules_and_parallel_lines_are_not_one_rule(self):
        for kind in ("block", "horizontal", "parallel"):
            with self.subTest(kind=kind):
                image = np.full((180, 20), 255, np.uint8)
                if kind == "block":
                    image[:, 7:13] = 0
                elif kind == "horizontal":
                    image[::15, :] = 0
                else:
                    image[:, [8, 12]] = 0
                self.assertEqual(self.confirm(image)["status"], "unknown")


class OgeSavedColumnAssignmentTests(unittest.TestCase):
    def test_words_outside_columns_are_retained_without_strict_zip_failure(self):
        words = [{"text": text, "bbox_pdf_points": box} for text, box in (
            ("outside", [40, 100, 60, 110]),
            ("asset", [200, 100, 240, 110]),
            ("crossing", [455, 100, 461, 110]),
            ("amount", [620, 100, 680, 110]),
            ("right", [710, 100, 730, 110]))]
        original = deepcopy(words)
        observed = _observe_slot_columns(words, EDGES)
        self.assertEqual(observed["unassigned_column_word_count"], 2)
        self.assertEqual(observed["boundary_crossing_word_count"], 1)
        self.assertEqual(observed["column_observation"]["description"]["word_indices"], [1])
        self.assertEqual(observed["column_observation"]["amount"]["raw_ocr_text"], "amount")
        self.assertEqual(words, original)

    def test_unknown_columns_stay_unknown_and_invalid_bounds_fail(self):
        self.assertIsNone(_observe_slot_columns([], None)["column_observation"])
        with self.assertRaises(ValueError):
            _observe_slot_columns([], EDGES[:-1])


@unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "optional PDF dependency unavailable")
class OgeRasterObservationIntegrationTests(unittest.TestCase):
    def test_unknown_rows_keep_confirmed_column_evidence_and_do_not_run_ocr(self):
        geometry = {"status": "confirmed", "basis": "same_page_vector_and_pixel_rules",
                    "edges_pdf_points": EDGES, "weak_rule_observations": []}
        census = {"results": [{"page_number": 1, "status": "coverage_unknown",
                                "physical_row_bands": [],
                                "column_geometry_observation": geometry}]}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "synthetic-input.bin"
            source.write_bytes(b"synthetic bytes; PDF reader is mocked")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch("unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback",
                       return_value=(census, {1: EDGES})), patch("pdfplumber.open"), patch(
                    "unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages") as ocr:
                observed = observe_raster_slots(source, expected_sha256=digest, page_numbers=[1])
        ocr.assert_not_called()
        self.assertEqual(observed["pages"][0]["column_geometry_observation"], geometry)
        self.assertIsNone(observed["pages"][0]["physical_slot_count"])
        self.assertFalse(observed["production_mutation_authorized"])


if __name__ == "__main__":
    unittest.main()
