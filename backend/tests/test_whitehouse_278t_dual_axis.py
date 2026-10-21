"""Explicit saved-v7 integration; synthetic geometry is not official-source gold."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import patch
import test_whitehouse_278t_saved_ocr as saved_tests
from test_whitehouse_278t_generic import consume
from unison_snapshot.whitehouse_278t import (materialize_saved_whitehouse_278t,validate_generic_whitehouse_report,
    DUAL_AXIS_MODE,DUAL_AXIS_PARSER_VERSION,SAVED_OCR_PARSER_VERSION)
from unison_snapshot.whitehouse_278t import saved_ocr_source_roots
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.builder import build


class WhitehouseDualAxisTests(unittest.TestCase):
    def test_geometry_failure_cache_on_disk_replays_without_json_type_drift(self):
        from test_oge_dual_axis_geometry import dual_saved_observation
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);inputs,_=case.fixture(root);inputs['geometry_mode']=DUAL_AXIS_MODE
            meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            data,_=dual_saved_observation(meta['sha256'],kind='two_row_models')
            data['pages'][0]['page_number']=2;data['selected_page_numbers']=[2]
            with case.context(data):
                report=materialize_saved_whitehouse_278t(inputs)
                path=root/'extraction.json';path.write_text(json.dumps(report,allow_nan=False),encoding='utf-8')
                reread=json.loads(path.read_bytes());validate_generic_whitehouse_report(reread)
                candidate,audit=consume(reread)
            self.assertEqual(reread,report)
            self.assertEqual(candidate['transactions'],[])
            self.assertEqual(report['observation_receipt']['observed_physical_slot_count'],0)
            self.assertEqual(report['observation_receipt']['geometry_unknown_pages'],[2])
            self.assertTrue(report['geometry_observations']['pages'][0]['column_geometry_observation']['candidate_failures'])

    def test_relative_v7_inputs_replay_after_root_move(self):
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            inputs,_=case.fixture(source,original=False);inputs['geometry_mode']=DUAL_AXIS_MODE
            for key in ('archive_metadata_path','checkpoint_path'):inputs[key]=Path(inputs[key]).name
            with saved_ocr_source_roots(source,source):before=materialize_saved_whitehouse_278t(inputs)
            target=root/'copy';shutil.copytree(source,target);shutil.rmtree(source)
            with saved_ocr_source_roots(target,target):
                after=materialize_saved_whitehouse_278t(inputs);validate_generic_whitehouse_report(after)
            self.assertEqual(before,after)
            self.assertEqual(after['observation_receipt']['input_reference_mode'],'controlled_relative_roots')

    def test_v7_unknown_scope_and_existing_formal_identity_never_append_or_correct_by_similarity(self):
        from test_whitehouse_partial_geometry import partial_saved
        from test_oge_dual_axis_fields import dual_input
        from unison_snapshot.oge_278t_raster_slots import slot_quad
        from unison_snapshot.oge_reports import parse_table_rows
        from test_whitehouse_278t_candidate import _base,_catalog
        from unison_snapshot.whitehouse_278t_candidate import _person,_transaction
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            inputs,_=case.fixture(Path(directory));inputs['geometry_mode']=DUAL_AXIS_MODE
            meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            data=partial_saved(heading=True);data.update(schema_version='oge-278t-raster-slot-observation/v3',geometry_mode=DUAL_AXIS_MODE)
            data['source_sha256']=meta['sha256'];page=data['pages'][0]
            template=dual_input(.005,.005)['pages'][0]
            column=deepcopy(template['column_line_model']);column['valid_y_span_pdf_points']=[70.,160.]
            row=deepcopy(template['row_line_model']);row['intercepts_y_pdf_points']=[100.,115.,130.,145.]
            row['slopes_dy_per_dx']=[.005]*4
            row['line_observations']=[{'samples':[{'x_span_pdf_points':span,'y_pdf_points':b+.005*(x-75.)}
                for span,x in zip([[1.,3.],[74.,76.],[147.,149.]],[2.,75.,148.])],'maximum_fit_residual_pdf_points':0.}
                for b in row['intercepts_y_pdf_points']]
            page.update(geometry_mode=DUAL_AXIS_MODE,row_line_model=row,column_line_model=column,render_coordinate_frame='displayed_mediabox')
            page['column_geometry_observation'].update(row_line_model=deepcopy(row),column_line_model=deepcopy(column))
            for slot,refs in zip(page['slots'],([0,1],[2,3])):
                slot.update(row_boundary_refs=refs,band_coordinate_basis='row_lines_at_reference_x')
                slot['quad_pdf_points']=slot_quad(page,slot)
            with case.context(data):report=materialize_saved_whitehouse_278t(inputs)
            self.assertEqual(report['transactions'],[])
            self.assertIn('account_context_unresolved',report['quarantined'][0]['reasons'])
            oldrows,_=parse_table_rows([(r['page_number'],r['cells']) for r in report['source_rows']],source_sha=meta['sha256'])
            self.assertEqual(len(oldrows),1)
            base=_base();person=_person(_catalog()['reports'][0]);base['people']=[person]
            base['meta']['data_cutoff_at']='2026-06-20T00:00:00Z'
            fact=_transaction(report,oldrows[0],person['id']);fact['asset_name']+=' — Investment account #9'
            base['transactions']=[fact];before=deepcopy(base['transactions'])
            with case.context(data):
                candidate,audit=consume(report,base);again,second=consume(report,candidate)
            self.assertEqual(candidate['transactions'],before);self.assertEqual(again['transactions'],before)
            self.assertEqual(audit['promoted_transaction_count'],0);self.assertEqual(second['promoted_transaction_count'],0)
    def test_explicit_new_mode_preserves_v6_and_rejects_mixed_or_forged_mode(self):
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            inputs,_=case.fixture(Path(directory),original=False)
            old=materialize_saved_whitehouse_278t(inputs)
            explicit={**inputs,'geometry_mode':DUAL_AXIS_MODE}
            new=materialize_saved_whitehouse_278t(explicit)
            self.assertEqual(new['parser_version'],DUAL_AXIS_PARSER_VERSION)
            self.assertEqual(new['observation_receipt']['geometry_mode'],DUAL_AXIS_MODE)
            self.assertEqual(new['observation_receipt']['local_row_geometry_mode'],DUAL_AXIS_MODE)
            validate_generic_whitehouse_report(new)
            candidate,audit=consume(new)
            self.assertEqual(candidate['transactions'],[])
            self.assertEqual(audit['reports'][0]['geometry_mode'],DUAL_AXIS_MODE)
            self.assertEqual(old,materialize_saved_whitehouse_278t(inputs))
            self.assertEqual(old['parser_version'],SAVED_OCR_PARSER_VERSION)
            for change in ({'allow_partial_rows':True},{'geometry_mode':'unknown'}):
                with self.assertRaises(OgeCatalogError):materialize_saved_whitehouse_278t({**explicit,**change})
            changed=deepcopy(new);changed['geometry_mode']='unknown'
            with self.assertRaisesRegex(OgeCatalogError,'replay'):validate_generic_whitehouse_report(changed)

    def test_actual_synthetic_geometry_raw_to_qualification_candidate_and_builder(self):
        from test_oge_dual_axis_geometry import dual_saved_observation,np,Image
        if np is None or Image is None:self.skipTest('optional image dependencies unavailable')
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            inputs,_=case.fixture(Path(directory));inputs['geometry_mode']=DUAL_AXIS_MODE
            meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            data,words=dual_saved_observation(meta['sha256'])
            data['pages'][0]['page_number']=2;data['selected_page_numbers']=[2]
            checkpoint=json.loads(Path(inputs['checkpoint_path']).read_bytes())
            for i,w in enumerate(words):w.update(block_num=1,par_num=1,line_num=i+1)
            checkpoint['pages'][1]['words']=words
            raw=json.dumps(checkpoint).encode();Path(inputs['checkpoint_path']).write_bytes(raw)
            inputs['checkpoint_sha256']=hashlib.sha256(raw).hexdigest()
            with case.context(data):
                report=materialize_saved_whitehouse_278t(inputs)
                validate_generic_whitehouse_report(report)
                candidate,audit=consume(report)
            self.assertEqual(report['parser_version'],DUAL_AXIS_PARSER_VERSION)
            self.assertEqual(len(candidate['transactions']),1)
            self.assertEqual(candidate['transactions'][0]['asset_name'],'ALPHA')
            self.assertEqual(candidate['transactions'][0]['owner'],'Undisclosed')
            self.assertEqual(report['raw_page_observations'][1]['words'],words)
            self.assertEqual(report['observation_receipt']['observed_physical_slot_count'],31)
            self.assertIsNone(report['observation_receipt']['whole_selected_table_slot_count'])
            self.assertEqual(audit['reports'][0]['geometry_mode'],DUAL_AXIS_MODE)
            self.assertTrue(build(candidate,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest['snapshot_id'])
            with case.context(data):
                again,second=consume(report,candidate)
            self.assertEqual(again['transactions'],candidate['transactions'])
            self.assertEqual(again['people'],candidate['people'])
            self.assertEqual(second['reports'][0]['retained_count'],1)
            self.assertEqual(second['reports'][0]['quarantined_count'],0)
            self.assertEqual(second['reports'][0]['retained_ids'],[candidate['transactions'][0]['id']])
            self.assertEqual(second['reports'][0]['current_source_retained_bindings']['rows'][0]['formal_id'],candidate['transactions'][0]['id'])
            for key,value in [('owner','Self'),('asset_name','ALPHA changed'),('filed_at','2026-06-12T00:00:00Z'),
                              ('transaction_type','exchange')]:
                changed=deepcopy(candidate);changed['transactions'][0][key]=value
                with case.context(data):blocked,ba=consume(report,changed)
                self.assertEqual(blocked['transactions'],changed['transactions'])
                self.assertEqual(ba['reports'][0]['retained_count'],0)
                self.assertEqual(ba['reports'][0]['promoted_count'],0)
            changed=deepcopy(candidate);other=deepcopy(changed['people'][0])
            other.update(id='oge:1111111111111111')
            changed['people'].append(other);changed['transactions'][0]['person_id']=other['id']
            with case.context(data):blocked,ba=consume(report,changed)
            self.assertEqual(blocked['transactions'],changed['transactions'])
            self.assertEqual(ba['reports'][0]['retained_count'],0)
            self.assertEqual(ba['reports'][0]['promoted_count'],0)

    def test_supported_description_is_consumed_without_rekeying_original_raw_cells(self):
        from test_oge_dual_axis_geometry import dual_saved_observation
        from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
        from unison_snapshot.oge_reports import parse_table_rows
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            inputs,_=case.fixture(Path(directory));inputs['geometry_mode']=DUAL_AXIS_MODE
            meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            data,words=dual_saved_observation(meta['sha256']);p=data['pages'][0]
            p['page_number']=2;data['selected_page_numbers']=[2];slot=p['slots'][0]
            alpha=next(w for w in slot['words'] if w['text']=='ALPHA')
            alpha['bbox_pdf_points']=[170.,120.,200.,126.]
            beta=deepcopy(alpha);beta.update(text='BETA',bbox_pdf_points=[250.,119.,280.,127.])
            slot['words'].append(beta);slot['words'].sort(key=lambda w:(w['bbox_pdf_points'][1],w['bbox_pdf_points'][0]))
            slot.update(_observe_slot_columns(slot['words'],p['column_edges_pdf_points'],page=p))
            checkpoint=json.loads(Path(inputs['checkpoint_path']).read_bytes())
            next(w for w in words if w['text']=='ALPHA').update(x0=170.,top=120.,x1=200.,bottom=126.)
            words.append({'text':'BETA','x0':250.,'top':119.,'x1':280.,'bottom':127.,'ocr_confidence':95,
                          'block_num':1,'par_num':1,'line_num':1})
            for i,w in enumerate(words):w.update(block_num=1,par_num=1,line_num=i+1)
            checkpoint['pages'][1]['words']=words
            raw=json.dumps(checkpoint).encode();Path(inputs['checkpoint_path']).write_bytes(raw)
            inputs['checkpoint_sha256']=hashlib.sha256(raw).hexdigest()
            with case.context(data):
                report=materialize_saved_whitehouse_278t(inputs);validate_generic_whitehouse_report(report)
                candidate,audit=consume(report)
            row=report['transactions'][0]
            self.assertEqual(row['cells'][1],'BETA ALPHA')
            self.assertEqual(row['asset_name'],'ALPHA BETA')
            rawgood,_=parse_table_rows([(row['page_number'],row['cells'])],source_sha=meta['sha256'],
                default_owner='Undisclosed',allow_open_amounts=True,allow_exchange=True)
            self.assertEqual(row['extraction_id'],rawgood[0]['extraction_id'])
            self.assertEqual(row['field_interpretation_evidence']['raw_cells'],row['cells'])
            self.assertEqual(candidate['transactions'][0]['asset_name'],'ALPHA BETA')
            self.assertEqual(candidate['transactions'][0]['transaction_type'],'purchase')
            self.assertEqual(candidate['transactions'][0]['transaction_date'],'2026-06-01')
            self.assertEqual(candidate['transactions'][0]['amount_low'],1001)
            self.assertEqual(candidate['transactions'][0]['amount_high'],15000)
            self.assertTrue(build(candidate,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest['snapshot_id'])
            # Conflicting source/projected reading orders are retained as raw,
            # not picked to obtain a convenient asset name.
            from test_oge_dual_axis_reading import wide_description
            conflict=wide_description(-.01);conflict['source_sha256']=meta['sha256']
            with case.context(conflict):bad=materialize_saved_whitehouse_278t(inputs);bc,ba=consume(bad)
            self.assertEqual(bc['transactions'],[])
            self.assertTrue(any('source_text_line_orientation_conflict' in r['reasons'] for r in bad['quarantined']))

    def test_cli_cache_versions_coexist_and_default_never_selects_v7(self):
        from scripts.whitehouse_extract import extract_batch
        from scripts.whitehouse_coverage import _trade_versions
        case=saved_tests.SavedWhiteHouseTests()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);inputs,_=case.fixture(root,original=False)
            meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes())
            path=root/'input.json';path.write_text(json.dumps(inputs),encoding='utf-8')
            kwargs={'limit':1,'document_id':meta['document_id'],'source_sha256':meta['sha256'],'saved_ocr_input':path}
            extract_batch(root,root/'review',**kwargs)
            inputs['geometry_mode']=DUAL_AXIS_MODE;path.write_text(json.dumps(inputs),encoding='utf-8')
            extract_batch(root,root/'review',**kwargs)
            names={p.stem for p in (root/'review/whitehouse/extractions').glob('*/*/*.json')}
            self.assertEqual(names,{v.replace('/','-') for v in (SAVED_OCR_PARSER_VERSION,DUAL_AXIS_PARSER_VERSION)})
            self.assertEqual(_trade_versions(meta['document_url'],meta['sha256'],dual_axis_trade=True),(DUAL_AXIS_PARSER_VERSION,))
            self.assertNotIn(DUAL_AXIS_PARSER_VERSION,_trade_versions(meta['document_url'],meta['sha256']))
            with self.assertRaises(ValueError):_trade_versions(meta['document_url'],meta['sha256'],dual_axis_trade=True,saved_ocr_trade=True)


if __name__=='__main__':unittest.main()
