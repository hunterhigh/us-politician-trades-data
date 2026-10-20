"""Frozen synthetic text-order cases; source boxes and raw strings never change."""
from copy import deepcopy
import unittest
from test_oge_dual_axis_fields import dual_input
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns, slot_quad
from unison_snapshot.oge_278t_field_shadow import observe_field_shapes, observe_content_rows


def wide_description(slope=-.01, *, horizontal=False, two_lines=False, tall=False):
    data=dual_input(slope);p=data['pages'][0];slot=p['slots'][0]
    edges=[0.,10.,410.,460.,520.,570.,650.]
    p['column_edges_pdf_points']=edges
    col=p['column_line_model'];ref_y=col['reference_y_pdf_points']
    for region in col['regional_observations']:
        y=sum(region['y_span_pdf_points'])/2
        region['edges']=[x+rate*(y-ref_y) for x,rate in zip(edges,col['slopes_dx_per_dy'])]
    p['column_geometry_observation']['column_line_model']=deepcopy(col)
    spans=[[1.,3.],[324.,326.],[647.,649.]];xs=[2.,325.,648.];ref_x=325.
    row=p['row_line_model'];row.update(reference_x_pdf_points=ref_x,valid_x_span_pdf_points=[-2.,652.])
    for b,observation in zip(row['intercepts_y_pdf_points'],row['line_observations']):
        observation['samples']=[{'x_span_pdf_points':span,'y_pdf_points':b+slope*(x-ref_x)}
                                for span,x in zip(spans,xs)]
    p['column_geometry_observation']['row_line_model']=deepcopy(row)
    slot['quad_pdf_points']=slot_quad(p,slot)
    words=[]
    for i,text in enumerate(['1',None,'purchase','01/02/2026','No','$1,001 - $15,000']):
        if text is None:continue
        cx=(edges[i]+edges[i+1])/2;cy=100+slope*(cx-ref_x)+4
        words.append({'text':text,'bbox_pdf_points':[edges[i]+2,cy-1,edges[i+1]-2,cy+1]})
    specs=[('B',390.,4.,2.),('A',20.,4.5 if tall else 4.,6. if tall else 2.)]
    if two_lines:specs.extend([('D',390.,10.,2.),('C',20.,10.,2.)])
    for text,cx,relative,height in specs:
        cy=107. if horizontal else 100+slope*(cx-ref_x)+relative
        words.append({'text':text,'bbox_pdf_points':[cx-3,cy-height/2,cx+3,cy+height/2]})
    slot['words']=words;slot.update(_observe_slot_columns(words,edges,page=p))
    return data


class DualAxisReadingTests(unittest.TestCase):
    def reading(self,data):
        return observe_field_shapes([data])['pages'][0]['slots'][0]['description_reading']

    def test_disagreeing_sloped_line_orders_remain_unknown_for_both_signs(self):
        for slope in [-.01,.01]:
            data=wide_description(slope);before=deepcopy(data)
            reading=self.reading(data)
            if slope<0:
                self.assertIsNone(reading['ordered_ocr_text'])
                self.assertEqual(reading['reason'],'source_text_line_orientation_conflict')
            else:
                self.assertEqual(reading['ordered_ocr_text'],'A B')
                self.assertEqual(len(reading['line_word_indices']),1)
            self.assertEqual(data,before)

    def test_two_sloped_lines_and_tall_glyph_do_not_mix_or_reverse(self):
        for slope in [-.01,.01]:
            data=wide_description(slope,two_lines=True,tall=True);before=deepcopy(data)
            reading=self.reading(data)
            if slope<0:
                self.assertIsNone(reading['ordered_ocr_text'])
                self.assertEqual(reading['reason'],'source_text_line_orientation_conflict')
            else:
                self.assertEqual(reading['ordered_ocr_text'],'A B C D')
                self.assertEqual(len(reading['line_word_indices']),2)
            self.assertEqual(data,before)

    def test_horizontal_handwriting_is_not_assumed_parallel_to_sloped_table(self):
        data=wide_description(.01,horizontal=True);before=deepcopy(data)
        reading=self.reading(data)
        self.assertIsNone(reading['ordered_ocr_text'])
        self.assertEqual(reading['reason'],'source_text_line_orientation_conflict')
        self.assertEqual(data,before)

    def test_independently_compatible_grouping_orders_can_keep_both_slope_signs(self):
        for slope in [-.01,.01]:
            data=wide_description(slope);p=data['pages'][0];slot=p['slots'][0]
            for word in slot['words']:
                if word['text'] not in {'A','B'}:continue
                x0,_,x1,_=word['bbox_pdf_points'];cy=100+slope*((x0+x1)/2-325)+6
                word['bbox_pdf_points']=[x0,cy-4,x1,cy+4]
            slot.update(_observe_slot_columns(slot['words'],p['column_edges_pdf_points'],page=p))
            before=deepcopy(data);reading=self.reading(data)
            self.assertEqual(reading['ordered_ocr_text'],'A B')
            self.assertEqual(reading['source_coordinate_grouping']['ordered_ocr_text'],'A B')
            self.assertEqual(reading['row_relative_grouping']['ordered_ocr_text'],'A B')
            self.assertEqual(data,before)

    def test_orientation_conflict_is_only_its_row_in_actual_candidate(self):
        from test_oge_observed_candidate import candidate, original
        from test_oge_candidate import base, catalog
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.builder import build
        from test_oge_dual_axis_fields import dual_input
        bad=wide_description(-.01)
        good=dual_input();good['pages'][0]['page_number']=3
        for data in [bad,good]:
            p=data['pages'][0];s=p['slots'][0]
            for word in s['words']:
                if word['text']=='01/02/2026':word['text']='05/02/2025'
            s.update(_observe_slot_columns(s['words'],p['column_edges_pdf_points'],page=p))
        inputs={'observations':[bad,good],'document_page_count':4}
        before=deepcopy(inputs);old=original()
        baseline,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at='2025-06-04T00:00:00Z')
        result,audit=candidate(inputs,old)
        self.assertEqual(inputs,before)
        prior={v['id']:v for v in baseline['transactions']}
        self.assertEqual({v['id']:v for v in result['transactions'] if v['id'] in prior},prior)
        self.assertEqual(len(result['transactions']),2)
        self.assertEqual([v['asset_name'] for v in result['transactions'] if v['id'] not in prior],['ALPHA'])
        rows=audit['reports'][0]['observation_receipt']['rows']
        bad_receipt=next(v for v in rows if v['page_number']==2)
        good_receipt=next(v for v in rows if v['page_number']==3)
        self.assertIn('source_text_line_orientation_conflict',bad_receipt['reasons'])
        self.assertEqual(good_receipt['disposition'],'materialized')
        self.assertTrue(build(result,generated_at='2025-06-04T00:00:00Z',allow_production=True).manifest['snapshot_id'])

    def test_secondary_producer_and_replay_keep_the_same_virtual_and_source_groups(self):
        from test_oge_dual_axis_fields import DualAxisColumnTests
        for slope in [-.01,.01]:
            fixture=DualAxisColumnTests();fixture.setUp();self.addCleanup(fixture.temp.cleanup)
            data=wide_description(slope);data['source_sha256']=fixture.data['source_sha256'];fixture.data=data
            source=data['pages'][0]['slots'][0]
            boxes=[(w['text'],w['bbox_pdf_points']) for w in source['words'] if w['text'] in {'A','B'}]
            before=deepcopy(data)
            aux=fixture.produce_word('description',boxes)
            replay=observe_content_rows([data],document_page_count=3,secondary_observations=[aux])
            primary=self.reading(data)
            secondary=aux['pages'][0]['slots'][0]['column_reading']
            self.assertEqual(primary['ordered_ocr_text'],secondary['ordered_ocr_text'])
            self.assertIn('source_coordinate_grouping',secondary)
            self.assertIn('row_relative_grouping',secondary)
            self.assertEqual(replay['source_report_ledger']['logical_row_count'],0 if slope<0 else 1)
            self.assertEqual(data,before)


if __name__=='__main__':unittest.main()
