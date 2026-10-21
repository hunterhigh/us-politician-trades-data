"""Source-bound amount TSV uses existing complete-group semantics, never dot repair."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import test_oge_source_quad_cells as quad_tests
from unison_snapshot.oge_278t_field_shadow import observe_field_shapes
from unison_snapshot.oge_278t_source_quad_cells import observe_saved_source_quad_cells
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.whitehouse_278t import _saved_supported_fields

class SourceQuadAmountTests(unittest.TestCase):
    def read(self,source,data,saved):
        return observe_saved_source_quad_cells(source,slots_observation=data,field='amount',saved_pages=saved,
            layer='source_text_objects_auxiliary',ocr_engine='saved synthetic amount TSV')

    def test_complete_source_amount_projects_without_mutating_raw_or_id(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=quad_tests.SourceQuadCellTests().fixture(Path(folder),'amount')
            p=data['pages'][0];s=p['slots'][0];s['words'][-1]['text']='$50.001 - $100,000'
            s.update(_observe_slot_columns(s['words'],p['column_edges_pdf_points'],page=p));before=deepcopy(data)
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                reading=self.read(source,data,saved);out=observe_field_shapes([data],secondary_observations=[reading],source_pdf_path=source)
            cells=['1','ALPHA','purchase','01/02/2026','No','$50.001 - $100,000']
            good,bad=parse_table_rows([(2,cells)],source_sha=data['source_sha256'],default_owner='Undisclosed')
            raw=(good+bad)[0];actual=_saved_supported_fields(raw,out['pages'][0]['slots'][0],source_sha=data['source_sha256'])
            self.assertEqual((actual['amount_low'],actual['amount_high']),(50001,100000))
            self.assertEqual(actual['cells'],cells);self.assertEqual(actual['extraction_id'],raw['extraction_id'])
            self.assertEqual(before,data)

    def test_source_amount_conflict_is_not_outvoted(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=quad_tests.SourceQuadCellTests().fixture(Path(folder),'amount')
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                reading=self.read(source,data,saved);out=observe_field_shapes([data],secondary_observations=[reading],source_pdf_path=source)
            slot=out['pages'][0]['slots'][0]
            self.assertEqual(slot['field_readings']['amount']['consistency'],'conflicting_observations')
            raw=parse_table_rows([(2,['1','ALPHA','purchase','01/02/2026','No','$1,001 - $15,000'])],source_sha=data['source_sha256'],default_owner='Undisclosed')[0][0]
            self.assertIn('amount_reading_unresolved',_saved_supported_fields(raw,slot,source_sha=data['source_sha256'])['reasons'])

    def test_decimal_dot_missing_endpoint_extra_digits_remain_unknown(self):
        for token in ('$50.001 - $100,000','$50.01 - $100,000','$50,001','$50,001 - $100,000 99'):
            with self.subTest(token=token),TemporaryDirectory() as folder:
                source,data,render,saved=quad_tests.SourceQuadCellTests().fixture(Path(folder),'amount')
                cell=saved[0]['cells'][0];cell['tsv_utf8']=cell['tsv_utf8'].replace('$50,001 - $100,000',token)
                cell['recipe']['tsv_sha256']=hashlib.sha256(cell['tsv_utf8'].encode()).hexdigest()
                with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                    reading=self.read(source,data,saved)
                self.assertIsNone(reading['pages'][0]['slots'][0]['column_shape']['values'])

    def test_notification_box_is_diagnostic_but_financial_overhang_still_blocks(self):
        for token in ('Yes','Yes1','$9','Over'):
            with self.subTest(token=token),TemporaryDirectory() as folder:
                source,data,render,saved=quad_tests.SourceQuadCellTests().fixture(Path(folder),'amount')
                cell=saved[0]['cells'][0];head,body=cell['tsv_utf8'].split('\n',1)
                cell['tsv_utf8']=head+'\n'+f'5\t1\t1\t1\t1\t1\t25\t15\t10\t15\t95\t{token}\n'+body
                cell['recipe']['tsv_sha256']=hashlib.sha256(cell['tsv_utf8'].encode()).hexdigest()
                with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                    reading=self.read(source,data,saved);out=observe_field_shapes([data],secondary_observations=[reading],source_pdf_path=source)
                observation=out['pages'][0]['slots'][0]['field_readings']['amount']['observations'][1]
                if token=='Yes':
                    self.assertEqual(observation['geometry_status'],'source_bound')
                    self.assertEqual(len(observation['nonfinancial_notification_geometry_issues']),1)
                    from unison_snapshot.oge_278t_field_shadow import _complete_secondary_geometry
                    self.assertTrue(_complete_secondary_geometry(observation,field='amount',slot=data['pages'][0]['slots'][0],page=data['pages'][0]))
                else:
                    self.assertEqual(observation['geometry_status'],'ambiguous')
                    self.assertEqual(observation['nonfinancial_notification_geometry_issues'],[])

if __name__=='__main__':unittest.main()
