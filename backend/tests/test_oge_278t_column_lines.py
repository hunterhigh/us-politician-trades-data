"""Synthetic source-coordinate models and cache boundaries, not OCR accuracy."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
try:
 import numpy as np
 from PIL import Image
except ImportError:
 np=Image=None
from test_oge_content_relations import saved,column_read,EDGES
from unison_snapshot.oge_278t_raster_slots import (column_edges_at_y,column_crop_bounds,column_geometry_sha256,
 _observe_slot_columns,_oge_2024_image_grid)
from unison_snapshot.oge_278t_fields import (_word_evidence,_complete_secondary_geometry,observe_field_shapes,
 compare_field_observations,observe_content_rows)


def modeled(slope=.01):
 base=saved();p=base['pages'][0]
 sections=[{'y_span_pdf_points':[a,a+5],'edges':[x+slope*(a+2.5-102.5) for x in EDGES]} for a in [80,100,120]]
 model={'schema_version':'oge-278t-column-lines/v1','reference_y_pdf_points':102.5,
        'slopes_dx_per_dy':[slope]*7,'regional_observations':sections,
        'maximum_fit_residual_pdf_points':0.,'coordinate_frame':'displayed_mediabox',
        'valid_y_span_pdf_points':[70.,130.]}
 p.update(column_line_model=model,column_geometry_observation={'column_line_model':deepcopy(model)},
          render_coordinate_frame='displayed_mediabox')
 return base


class ColumnLineContractTests(unittest.TestCase):
 def test_legacy_main_observation_with_in_band_word_and_null_model(self):
  from unison_snapshot.oge_278t_raster_slots import observe_raster_slots
  geometry={'status':'confirmed','basis':'same_page_vector_and_pixel_rules','edges_pdf_points':EDGES}
  census={'results':[{'page_number':1,'status':'physical_slots_located',
           'physical_row_bands':[[100,115]],'column_geometry_observation':geometry}]}
  word={'text':'ALPHA','x0':20,'x1':40,'top':102,'bottom':108,'ocr_confidence':95}
  with tempfile.TemporaryDirectory() as folder:
   source=Path(folder)/'mock.bin';source.write_bytes(b'mocked PDF reader')
   with patch('unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback',return_value=(census,{1:EDGES})),patch(
     'pdfplumber.open'),patch('unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages',
       return_value=([SimpleNamespace(extract_words=lambda:[word])],'mock')):
    result=observe_raster_slots(source,expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),page_numbers=[1])
  page=result['pages'][0]
  self.assertNotIn('column_line_model',page)
  self.assertEqual(page['slots'][0]['column_observation']['description']['raw_ocr_text'],'ALPHA')
  observe_field_shapes([result])
  null_page={'column_edges_pdf_points':EDGES,'column_line_model':None}
  observations=[{'text':'ALPHA','bbox_pdf_points':[20,102,40,108]}]
  self.assertEqual(_observe_slot_columns(observations,EDGES,page=null_page),_observe_slot_columns(observations,EDGES))
  self.assertEqual(column_crop_bounds(null_page,1,2,100,115),(10.6,68.5))

 def test_source_coordinates_and_union_crop_are_distinct_from_full_box_proof(self):
  p=modeled()['pages'][0]
  self.assertAlmostEqual(column_edges_at_y(p,110)[1],10.075)
  left,right=column_crop_bounds(p,1,2,100,117)
  self.assertAlmostEqual(left,10.575);self.assertAlmostEqual(right,68.645)
  box=[10.65,100,40,110]
  self.assertGreater(box[0],left)
  read={'method':'column_ocr','geometry_status':'source_bound','shape':{'status':'ocr_text_only','value':'ALPHA'},
        'detections':[{'bbox_pdf_points':box}]}
  self.assertFalse(_complete_secondary_geometry(read,field='description',slot={'band_pdf_points':[100,115]},page=p))
  read['detections'][0]['bbox_pdf_points'][0]=10.7
  self.assertTrue(_complete_secondary_geometry(read,field='description',slot={'band_pdf_points':[100,115]},page=p))

 def test_word_corner_crossing_is_detected_even_when_center_does_not_cross(self):
  p=modeled()['pages'][0];words=[{'text':'EDGE','bbox_pdf_points':[69.99,100,70.01,110]}]
  slot={'words':words,**_observe_slot_columns(words,EDGES,page=p)}
  evidence=_word_evidence(slot,p)
  self.assertEqual(len(evidence['crossing_words']),1)
  self.assertEqual(slot['boundary_crossing_word_count'],1)

 def test_model_and_evidence_or_frame_or_domain_cannot_drift_silently(self):
  for kind in ['slope','frame','domain','missing_region','regional_position','nested','page_frame']:
   p=modeled()['pages'][0]
   if kind=='slope':p['column_line_model']['slopes_dx_per_dy'][0]=.02
   if kind=='frame':p['column_line_model']['coordinate_frame']='pixels'
   if kind=='page_frame':p['render_coordinate_frame']='pixels'
   if kind=='domain':p['column_line_model']['valid_y_span_pdf_points']=[101,105]
   if kind=='missing_region':p['column_line_model']['regional_observations'].pop()
   if kind=='regional_position':p['column_line_model']['regional_observations'][0]['edges'][1]+=2
   if kind=='nested':p['column_geometry_observation']['column_line_model']['reference_y_pdf_points']=103
   with self.subTest(kind=kind),self.assertRaises(ValueError):column_edges_at_y(p,102.5)
  with self.assertRaises(ValueError):column_edges_at_y(modeled()['pages'][0],69)

 def test_old_or_mismatched_auxiliary_model_is_rejected_even_with_same_reference_edges(self):
  base=modeled();reading=column_read(base,'type','purchase')
  with self.assertRaises(ValueError):observe_field_shapes([base],secondary_observations=[reading])
  reading['input_column_geometry_sha256']={'2':column_geometry_sha256(base['pages'][0])}
  out=observe_field_shapes([base],secondary_observations=[reading])
  self.assertEqual(out['pages'][0]['slots'][0]['field_readings']['type']['consistency'],'matching_observations')
  with self.assertRaises(ValueError):observe_field_shapes([modeled(.005)],secondary_observations=[reading])
  with self.assertRaises(ValueError):observe_field_shapes([saved()],secondary_observations=[reading])

 def test_neighbor_outside_actual_column_is_ambiguous_and_cannot_supply_geometry(self):
  base=modeled();reading=column_read(base,'type','purchase')
  reading['input_column_geometry_sha256']={'2':column_geometry_sha256(base['pages'][0])}
  # A saved token whose center is outside the actual field cannot supply evidence.
  reading['pages'][0]['slots'][0]['column_words'][0]['bbox_pdf_points']=[70.07,112,70.09,114]
  out=observe_field_shapes([base],secondary_observations=[reading])
  obs=out['pages'][0]['slots'][0]['field_readings']['type']['observations'][1]
  self.assertEqual(obs['geometry_status'],'ambiguous')
  self.assertIn('detection_outside_sloped_field',obs['geometry_issues'])

 def test_box_beyond_witnessed_domain_cannot_supply_geometry_or_relation(self):
  base=modeled();page=base['pages'][0];slot=page['slots'][0]
  read={'method':'column_ocr','geometry_status':'source_bound','shape':{'status':'ocr_text_only','value':'ALPHA'},
        'detections':[{'bbox_pdf_points':[11,129,30,131]}]}
  self.assertFalse(_complete_secondary_geometry(read,field='description',slot={'band_pdf_points':[125,140]},page=page))
  slot['words'][0]['bbox_pdf_points']=[1,97,9,131]
  slot.update(_observe_slot_columns(slot['words'],EDGES,page=page))
  self.assertEqual(slot['column_model_outside_word_indices'],[0])
  out=observe_content_rows([base],document_page_count=2)
  self.assertEqual(out['source_report_ledger']['logical_row_count'],0)
  self.assertIn('source_words_outside_column_model_domain',out['slot_content'][0]['relation_evidence']['blocking_reasons'])

 def test_comparison_rejects_changed_slope_or_coordinate_frame(self):
  a=modeled();b=modeled(.005);b['ocr_resolution_dpi']=300
  with self.assertRaises(ValueError):compare_field_observations(a,b)

 def test_zero_slope_preserves_field_shapes_and_legacy_has_no_new_model_keys(self):
  old=observe_field_shapes([saved()]);new=observe_field_shapes([modeled(0)])
  self.assertEqual(old['summary'],new['summary'])
  self.assertEqual(old['pages'],new['pages'])
  self.assertNotIn('column_line_model',saved()['pages'][0])




@unittest.skipUnless(np is not None and Image is not None,'optional raster dependencies unavailable')
class ColumnLineImageTests(unittest.TestCase):
 def page(self,slope=.005,broken=False,curved=False,short=False):
  height,width=612,792;scale=3
  pixels=np.full((height*scale,width*scale),255,np.uint8)
  edges=[81,127,469,520,577,624,713]
  for row in range(80*scale,583*scale):
   y=row/scale
   for i,x in enumerate(edges):
    if broken and i==3 and 275<y<400:continue
    extra=5 if curved and i==3 and 275<y<400 else 0
    px=int((x+slope*(y-330)+extra)*scale);pixels[row,px]=0
  for y in range(116,431 if short else 582,15):
   left,right=[int((x+slope*(y-330))*scale) for x in [edges[0],edges[-1]]]
   pixels[y*scale,left:right+1]=0
  return SimpleNamespace(width=width,height=height,bbox=(0,0,width,height),cropbox=(2,3,790,609),rotation=0,
    to_image=lambda **kwargs:SimpleNamespace(original=Image.fromarray(pixels)))

 def test_positive_and_negative_small_drift_are_measured_from_same_page(self):
  for slope in [.005,-.005,0.]:
   with self.subTest(slope=slope):
    out=_oge_2024_image_grid(self.page(slope))
    self.assertEqual(out['status'],'candidate')
    self.assertEqual(len(out['physical_row_bands']),31)
    if slope == 0 and 'column_line_model' not in out:
     continue  # A visible flat layout no longer needs the drift fallback.
    self.assertIn('column_line_model',out)
    for witness in out['weak_rule_observations']:
     self.assertEqual(witness['span_pdf_points'],out['column_line_model']['valid_y_span_pdf_points'])
    self.assertLess(max(abs(x-slope) for x in out['column_line_model']['slopes_dx_per_dy']),.002)

 def test_missing_regional_line_cannot_be_averaged_or_copied(self):
  self.assertEqual(_oge_2024_image_grid(self.page(broken=True))['status'],'unknown')

 def test_long_vertical_rules_with_early_table_end_return_unknown(self):
  self.assertEqual(_oge_2024_image_grid(self.page(short=True))['status'],'unknown')

 def test_curved_line_outside_linear_model_stays_unknown(self):
  self.assertEqual(_oge_2024_image_grid(self.page(curved=True))['status'],'unknown')


if __name__=='__main__':unittest.main()
