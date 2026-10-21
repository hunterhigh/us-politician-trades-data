"""Geometry amount reads preserve raw identity; synthetic contracts, not OCR gold."""
from copy import deepcopy
import unittest
from test_oge_dual_axis_fields import dual_input, bind_secondary
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes, _amount_shape
from unison_snapshot.whitehouse_278t import _saved_supported_fields
from unison_snapshot.oge_reports import parse_table_rows


def reordered():
    data=dual_input();page=data['pages'][0];slot=page['slots'][0]
    slot['words']=slot['words'][:-1]+[
        {'text':'$1,000,000','bbox_pdf_points':[131,102,148,108]},
        {'text':'$500,001','bbox_pdf_points':[112,104,126,110]},
        {'text':'-','bbox_pdf_points':[128,104,129,110]}]
    slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points'],page=page))
    slot['raw_ocr_text']=' '.join(w['text'] for w in slot['words'])
    return data


class AmountReadingGeometryTests(unittest.TestCase):
    def test_geometry_order_reaches_relation_and_saved_supported_projection_without_new_id(self):
        data=reordered();before=deepcopy(data)
        out=observe_content_rows([data],document_page_count=3)
        row=out['field_observations']['pages'][0]['slots'][0]
        self.assertEqual(row['field_shapes']['amount']['values'],[500001,1000000])
        self.assertEqual(row['amount_reading']['ordered_ocr_text'],'$500,001 - $1,000,000')
        self.assertEqual(out['slot_content'][0]['relation_evidence']['status'],'supported_observation_relation')
        source=data['pages'][0]['slots'][0]
        cells=[source['column_observation'][name]['raw_ocr_text'] for name in ('printed_number','description','type','transaction_date','notification','amount')]
        good,bad=parse_table_rows([(2,cells)],source_sha=data['source_sha256'],default_owner='Undisclosed',allow_open_amounts=True,allow_exchange=True)
        self.assertFalse(good);self.assertEqual(len(bad),1)
        fixed=_saved_supported_fields(bad[0],row,source_sha=data['source_sha256'])
        self.assertEqual((fixed['amount_low'],fixed['amount_high']),(500001,1000000))
        self.assertEqual(fixed['extraction_id'],bad[0]['extraction_id'])
        self.assertEqual(fixed['cells'],bad[0]['cells'])
        self.assertEqual(data,before)

    def test_split_thousands_never_become_small_dollar_amounts(self):
        for raw in ('$100 001 - $250 000','$500 001 - $1,000,000','$15 001 -$50,000','$50,001 - $100 000'):
            with self.subTest(raw=raw):self.assertIsNone(_amount_shape(raw)['values'])

    def test_complete_spaced_groups_use_existing_statutory_policy_and_saved_projection(self):
        data=reordered();page=data['pages'][0];slot=page['slots'][0]
        slot['words'][-3]['text']='$250 000'
        slot['words'][-2]['text']='$100 001'
        slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points'],page=page))
        before=deepcopy(data)
        out=observe_content_rows([data],document_page_count=3)
        row=out['field_observations']['pages'][0]['slots'][0]
        self.assertEqual(row['field_shapes']['amount']['values'],[100001,250000])
        self.assertEqual(row['field_shapes']['amount']['interpretation_basis'],'existing_statutory_range_complete_digit_groups/v1')
        self.assertEqual(row['field_readings']['amount']['observations'][0]['template_category']['status'],'bounded_template_numeric_shape')
        self.assertEqual(out['slot_content'][0]['relation_evidence']['status'],'supported_observation_relation')
        cells=[slot['column_observation'][name]['raw_ocr_text'] for name in ('printed_number','description','type','transaction_date','notification','amount')]
        good,bad=parse_table_rows([(2,cells)],source_sha=data['source_sha256'],default_owner='Undisclosed',allow_open_amounts=True,allow_exchange=True)
        self.assertFalse(good)
        fixed=_saved_supported_fields(bad[0],row,source_sha=data['source_sha256'])
        self.assertEqual((fixed['amount_low'],fixed['amount_high']),(100001,250000))
        self.assertEqual(fixed['extraction_id'],bad[0]['extraction_id'])
        self.assertEqual(fixed['cells'],bad[0]['cells']);self.assertEqual(data,before)
        conflicting=observe_content_rows([data],document_page_count=3,secondary_observations=[bind_secondary(data,'amount','$250,001 - $500,000')])
        self.assertIn('amount_observation_conflict',conflicting['slot_content'][0]['relation_evidence']['blocking_reasons'])

    def test_incomplete_nonstatutory_or_unproved_groups_are_not_interpreted(self):
        from unison_snapshot.oge_278t_field_shadow import _ordered_amount_shape
        texts=['$10 01 - $15 000','$100 001 - 250 000','100 001 - $250 000',
            '$100 001 $250 000','$100 001 - $300 000','$100 001 - $250 000 additional 2',
            '$100 001 - $250 000 note','$100,,001 - $250 000','$100 001 - $250 00',
            '$100 001 - $250 000 - $500 000']
        for text in texts:
            with self.subTest(text=text):
                reading={'status':'geometry_ordered_unverified','ordered_ocr_text':text,
                         'reading_coordinate_basis':'source_and_row_top_relative_consistency/v1'}
                self.assertIsNone(_ordered_amount_shape(reading)['values'])
        for basis in (None,'source_coordinates_only'):
            reading={'status':'geometry_ordered_unverified','ordered_ocr_text':'$100 001 - $250 000','reading_coordinate_basis':basis}
            self.assertIsNone(_ordered_amount_shape(reading)['values'])

    def test_conflicting_source_read_remains_blocked_after_geometric_order(self):
        data=reordered();secondary=bind_secondary(data,'amount','$250,001 - $500,000')
        out=observe_content_rows([data],document_page_count=3,secondary_observations=[secondary])
        self.assertNotEqual(out['slot_content'][0]['relation_evidence']['status'],'supported_observation_relation')
        self.assertIn('amount_observation_conflict',out['slot_content'][0]['relation_evidence']['blocking_reasons'])

    def test_source_and_row_relative_orientation_disagreement_is_amount_unknown(self):
        data=reordered();page=data['pages'][0];slot=page['slots'][0]
        for word in slot['words'][-3:]:
            x=(word['bbox_pdf_points'][0]+word['bbox_pdf_points'][2])/2
            center=103+.004*(x-112)
            word['bbox_pdf_points'][1]=center-.05
            word['bbox_pdf_points'][3]=center+.05
        slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points'],page=page))
        out=observe_content_rows([data],document_page_count=3)
        row=out['field_observations']['pages'][0]['slots'][0]
        self.assertEqual(row['amount_reading']['reason'],'source_text_line_orientation_conflict')
        self.assertIsNone(row['field_shapes']['amount']['values'])
        self.assertNotEqual(out['slot_content'][0]['relation_evidence']['status'],'supported_observation_relation')

    def test_unknown_notification_label_does_not_gate_amount_or_change_raw(self):
        data=reordered();slot=data['pages'][0]['slots'][0];page=data['pages'][0]
        slot['words'][4]['text']='Yos'
        slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points'],page=page))
        row=observe_field_shapes([data])['pages'][0]['slots'][0]
        self.assertEqual(row['field_shapes']['amount']['values'],[500001,1000000])
        self.assertEqual(row['raw_columns']['notification'],'Yos')
        self.assertEqual(row['amount_reading']['excluded_notification_word_indices'],[4])

if __name__=='__main__':unittest.main()
