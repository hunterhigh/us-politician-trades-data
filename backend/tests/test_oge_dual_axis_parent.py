"""Independent geometry checks; these synthetic cases do not prove OCR accuracy."""
from copy import deepcopy
import unittest

from test_oge_dual_axis_geometry import dual_page
from unison_snapshot.oge_278t_raster_slots import (
    box_in_slot, box_overlaps_slot, column_edges_at_y, point_in_slot,
    row_edges_at_x, slot_crop_bounds, slot_quad,
)


def both_axes(row_slope, column_slope):
    page, slot = dual_page(row_slope)
    page['row_line_model']['valid_x_span_pdf_points'] = [8., 102.]
    spans = [[0., 10.], [30., 40.], [60., 70.]]
    reference = 35.
    page['column_line_model'] = {
        'schema_version': 'oge-278t-column-lines/v1',
        'coordinate_frame': 'displayed_mediabox',
        'reference_y_pdf_points': reference,
        'valid_y_span_pdf_points': [0., 70.],
        'slopes_dx_per_dy': [column_slope] * 7,
        'regional_observations': [
            {'y_span_pdf_points': span, 'edges': [
                edge + column_slope * (sum(span) / 2 - reference)
                for edge in page['column_edges_pdf_points']
            ]} for span in spans
        ],
    }
    return page, slot


class ParentDualAxisGeometryTests(unittest.TestCase):
    def test_simultaneously_sloped_axes_intersect_in_original_coordinates(self):
        for row_slope in [-.01, 0., .01]:
            for column_slope in [-.01, 0., .01]:
                with self.subTest(row=row_slope, column=column_slope):
                    page, slot = both_axes(row_slope, column_slope)
                    quad = slot_quad(page, slot)
                    for (x, y), col, row in zip(quad, [0, 6, 6, 0], [0, 0, 1, 1]):
                        self.assertAlmostEqual(x, column_edges_at_y(page, y)[col])
                        self.assertAlmostEqual(y, row_edges_at_x(page, x)[row])
                    crop = slot_crop_bounds(page, slot, left_pad=0., right_pad=0.)
                    self.assertEqual(crop, (min(p[0] for p in quad), min(p[1] for p in quad),
                                            max(p[0] for p in quad), max(p[1] for p in quad)))
                    self.assertTrue(box_in_slot(page, slot, [75., 25., 80., 35.]))

    def test_crossing_word_is_not_owned_as_a_complete_field(self):
        page, slot = both_axes(.01, -.01)
        x = 80.
        boundary = row_edges_at_x(page, x)[1]
        crossing = [x - 1., boundary - 1., x + 1., boundary + 1.]
        self.assertTrue(box_overlaps_slot(page, slot, crossing))
        self.assertFalse(box_in_slot(page, slot, crossing))
        self.assertTrue(point_in_slot(page, slot, x, boundary - .001))
        self.assertFalse(point_in_slot(page, slot, x, boundary + .001))

    def test_intersections_cannot_extrapolate_beyond_observed_row_domain(self):
        page, slot = both_axes(.01, .01)
        page['row_line_model']['valid_x_span_pdf_points'] = [10., 100.]
        with self.assertRaisesRegex(ValueError, 'row line model'):
            slot_quad(page, slot)
        changed = deepcopy(page)
        changed['column_line_model']['valid_y_span_pdf_points'] = [25., 70.]
        with self.assertRaises(ValueError):
            slot_quad(changed, slot)


if __name__ == '__main__':
    unittest.main()
