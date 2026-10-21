"""Sparse saved numbering is source replay, never sequence repair or new OCR."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import test_oge_source_quad_cells as quad_tests
from unison_snapshot.oge_278t_raster_slots import slot_quad,_observe_slot_columns
from unison_snapshot.oge_278t_field_shadow import observe_field_shapes,observe_content_rows
from unison_snapshot.oge_278t_source_quad_cells import observe_saved_source_quad_cells,canonical_sha
from unison_snapshot.whitehouse_278t import _saved_supported_fields
from unison_snapshot.oge_reports import parse_table_rows


class PrintedSourceQuadTests(unittest.TestCase):
    def fixture(self,root,*,second=False):
        case=quad_tests.SourceQuadCellTests();source,data,render,saved=case.fixture(root,'printed_number')
        page=data['pages'][0];slot=page['slots'][0]
        slot['words'][0]['text']='so'
        slot.update(_observe_slot_columns(slot['words'],page['column_edges_pdf_points'],page=page))
        if second:
            model=page['row_line_model'];model['intercepts_y_pdf_points'].append(125.)
            model['slopes_dy_per_dx'].append(model['slopes_dy_per_dx'][-1])
            line=deepcopy(model['line_observations'][-1])
            for sample in line['samples']:sample['y_pdf_points']+=10
            model['line_observations'].append(line)
            page['column_geometry_observation']['row_line_model']=deepcopy(model)
            page['column_geometry_observation']['physical_row_bands'].append([115,125])
            extra=deepcopy(slot);extra.update(slot_index=2,band_pdf_points=[115,125],row_boundary_refs=[1,2])
            for word in extra['words']:
                word['bbox_pdf_points'][1]+=15;word['bbox_pdf_points'][3]+=15
            extra['quad_pdf_points']=slot_quad(page,extra)
            extra.update(_observe_slot_columns(extra['words'],page['column_edges_pdf_points'],page=page))
            page['slots'].append(extra);page['known_physical_slot_count']=2
        saved[0]['selected_slot_indices']=[1]
        return source,data,render,saved

    def make(self,source,data,saved):
        return observe_saved_source_quad_cells(source,slots_observation=data,field='printed_number',
            saved_pages=saved,layer='source_text_objects_auxiliary',ocr_engine='frozen synthetic TSV')

    def test_sparse_selection_does_not_fabricate_reads_and_projects_preserving_raw_id(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder),second=True);before=deepcopy(data)
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                read=self.make(source,data,saved)
                out=observe_content_rows([data],document_page_count=3,secondary_observations=[read],source_pdf_path=source)
            self.assertEqual(read['summary']['observed_slots'],1)
            self.assertEqual(read['pages'][0]['slots'][1]['observation_status'],'not_selected')
            fields=out['field_observations']['pages'][0]['slots']
            self.assertEqual(len(fields[0]['field_readings']['printed_number']['observations']),2)
            self.assertEqual(len(fields[1]['field_readings']['printed_number']['observations']),1)
            cells=['so','ALPHA','purchase','01/02/2026','No','$1,001 - $15,000']
            good,bad=parse_table_rows([(2,cells)],source_sha=data['source_sha256'],default_owner='Undisclosed')
            raw=(good+bad)[0];actual=_saved_supported_fields(raw,fields[0],source_sha=data['source_sha256'])
            self.assertEqual(actual['row_number'],50);self.assertEqual(actual['cells'],cells)
            self.assertEqual(actual['extraction_id'],raw['extraction_id'])
            self.assertNotIn('reasons',actual);self.assertEqual(data,before)

    def test_valid_number_conflict_remains_unresolved(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder));p=data['pages'][0];s=p['slots'][0]
            s['words'][0]['text']='49';s.update(_observe_slot_columns(s['words'],p['column_edges_pdf_points'],page=p))
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                read=self.make(source,data,saved);out=observe_field_shapes([data],secondary_observations=[read],source_pdf_path=source)
            field=out['pages'][0]['slots'][0]['field_readings']['printed_number']
            self.assertEqual(field['consistency'],'conflicting_observations')
            cells=['49','ALPHA','purchase','01/02/2026','No','$1,001 - $15,000']
            raw=parse_table_rows([(2,cells)],source_sha=data['source_sha256'],default_owner='Undisclosed')[0][0]
            actual=_saved_supported_fields(raw,out['pages'][0]['slots'][0],source_sha=data['source_sha256'])
            self.assertIn('printed_number_reading_unresolved',actual['reasons'])

    def test_only_single_complete_positive_decimal_token_is_recognized(self):
        for token in ['5O','50.','0','-50','50 51','so']:
            with self.subTest(token=token),TemporaryDirectory() as folder:
                source,data,render,saved=self.fixture(Path(folder))
                cell=saved[0]['cells'][0];cell['tsv_utf8']=cell['tsv_utf8'].replace('50\n',token+'\n')
                cell['recipe']['tsv_sha256']=hashlib.sha256(cell['tsv_utf8'].encode()).hexdigest()
                with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                    read=self.make(source,data,saved)
                self.assertIsNone(read['pages'][0]['slots'][0]['column_shape']['value'])

    def test_selection_source_role_and_serialized_mutations_rejected(self):
        with TemporaryDirectory() as folder:
            source,data,render,saved=self.fixture(Path(folder),second=True)
            with patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                good=self.make(source,data,saved)
                for selection in [[],[True],[1,1],[2,1],[3],[2]]:
                    bad=deepcopy(saved);bad[0]['selected_slot_indices']=selection
                    with self.subTest(selection=selection),self.assertRaises(ValueError):self.make(source,data,bad)
                for kind in ['unselected','shape','role','source']:
                    bad=deepcopy(good)
                    if kind=='unselected':bad['pages'][0]['slots'][1]['column_raw_ocr_text']='51'
                    if kind=='shape':bad['pages'][0]['slots'][0]['column_shape']['value']=51
                    if kind=='role':bad['source_quad_input']['field']='type'
                    if kind=='source':bad['source_sha256']='0'*64
                    with self.subTest(kind=kind),self.assertRaises(ValueError):
                        observe_field_shapes([data],secondary_observations=[bad],source_pdf_path=source)
                no_mode=deepcopy(good);no_mode.pop('read_mode')
                with self.assertRaisesRegex(ValueError,'source quad replay'):
                    observe_field_shapes([data],secondary_observations=[no_mode],source_pdf_path=source)

    def test_saved_materializer_candidate_builder_and_repeat_keep_raw_identity(self):
        import json
        from test_whitehouse_278t_saved_ocr import SavedWhiteHouseTests
        from test_whitehouse_278t_generic import consume
        from unison_snapshot.whitehouse_278t import materialize_saved_whitehouse_278t,DUAL_AXIS_MODE
        from unison_snapshot.builder import build
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'quad').mkdir();(root/'wh').mkdir()
            source,data,render,saved=self.fixture(root/'quad')
            case=SavedWhiteHouseTests();inputs,_=case.fixture(root/'wh')
            source=Path(inputs['archived_pdf_path']);sha=hashlib.sha256(source.read_bytes()).hexdigest()
            data['source_sha256']=sha;render['receipt']['source_sha256']=sha
            saved[0]['render_receipt']=deepcopy(render['receipt']);inputs['geometry_mode']=DUAL_AXIS_MODE
            with case.context(data),patch('unison_snapshot.pdf_text_objects.render_pdf_text_objects',return_value=render):
                before=materialize_saved_whitehouse_278t(inputs)
                read=self.make(source,data,saved)
                path=root/'secondary.json';raw=json.dumps([read]).encode();path.write_bytes(raw)
                enabled={**inputs,'secondary_observations_path':str(path),'secondary_observations_sha256':hashlib.sha256(raw).hexdigest()}
                after=materialize_saved_whitehouse_278t(enabled);candidate,audit=consume(after);repeat,_=consume(after,candidate)
            old=(before['transactions']+before['quarantined'])[0]
            row=(after['transactions']+after['quarantined'])[0]
            self.assertEqual(row['row_number'],50);self.assertEqual(row['cells'][0],'so')
            self.assertEqual(row['extraction_id'],old['extraction_id'])
            self.assertEqual(len(candidate['transactions']),1);self.assertEqual(candidate,repeat)
            self.assertTrue(build(candidate,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest)

if __name__=='__main__':unittest.main()
