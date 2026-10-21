"""Vertical witnesses are bounded by actual sloped-row intersections."""
import unittest
import numpy as np
from PIL import Image
from types import SimpleNamespace
from unittest.mock import Mock
from test_oge_dual_axis_geometry import image_page
from unison_snapshot.oge_278t_raster_slots import _dual_axis_image_grid

def exact_outer_page(slope=.006, gap=False):
 page=image_page(row_slope=slope)
 gray=np.array(page.to_image.return_value.original)
 yy,xx=np.indices(gray.shape)
 top=(85+slope*(xx/3-395))*3
 bottom=(581+slope*(xx/3-395))*3
 gray[(yy<np.floor(top)-1)|(yy>np.floor(bottom)+1)]=255
 if gap:
  # Cut a real interior column over four points, far from outer endpoints.
  for py in range(300*3,304*3):
   px=int((473+.004*(py/3-300))*3)
   gray[py,px-4:px+5]=255
 page.to_image=Mock(return_value=SimpleNamespace(original=Image.fromarray(gray)))
 return page

class ColumnWitnessSpanTests(unittest.TestCase):
    def test_positive_and_negative_sloped_outer_edges_are_not_internal_gaps(self):
        for slope in (.006,-.006):
            with self.subTest(slope=slope):
                result=_dual_axis_image_grid(exact_outer_page(slope))
                self.assertEqual(result['status'],'candidate')
                self.assertEqual(len(result['physical_row_bands']),31)
                self.assertTrue(all(w['status']=='confirmed' for w in result['weak_rule_observations']))

    def test_true_internal_vertical_gap_is_not_trimmed_as_outside_table(self):
        for slope in (.006,-.006):
            with self.subTest(slope=slope):
                result=_dual_axis_image_grid(exact_outer_page(slope,gap=True))
                self.assertEqual(result['status'],'unknown')
                self.assertTrue(any(f['reason']=='image_vertical_rule_not_connected' for f in result['candidate_failures']))

if __name__=='__main__':unittest.main()
