"""Preparation/consumption contracts; fixtures do not claim OCR accuracy."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_house_ptr_key_fields_shadow import report
from unison_snapshot.house_ptr_key_field_reread import (
    DOUBTFUL_SELECTION, MATERIALIZATION_SELECTION, REREAD_RULE_VERSION,
    main, missing_key_field_rereads, plan_key_field_rereads, reread_doubtful_key_cells)


def observation(raw=b'synthetic source', count=1):
    value=report();value['source_sha256']=hashlib.sha256(raw).hexdigest()
    page=value['pages'][0];page.update(coordinate_frame='original_page',orientation_degrees=0)
    row=page['rows'][0];page['rows']=[]
    for index in range(count):
        item=deepcopy(row);item['physical_band_index']=index+1
        for offset,name in enumerate(('asset','event_date')):
            cell=item['cells'][name]
            cell.update(crop_sha256=hashlib.sha256(f'{index}:{name}'.encode()).hexdigest(),
                bbox_normalized=[.1+offset*.4,.1+index*.02,.4+offset*.4,.12+index*.02],
                raw_ocr_confidence=99.)
        page['rows'].append(item)
    return value


def cache_for(plan, requests=None):
    rows=[]
    for r in plan['requests'] if requests is None else requests:
        rows.append({**{k:r[k] for k in ('page','physical_band_index','field','first_ocr_raw','first_crop_sha256')},
            'second_ocr_raw':r['first_ocr_raw'],'second_crop_sha256':'b'*64,'second_crop_render_dpi':300})
    return {'schema_version':'house-paper-key-field-reread/v1','rule_version':REREAD_RULE_VERSION,
        'source_sha256':plan['source_sha256'],'qualification_status':'unverified_shadow_only',
        'selected_cell_count':len(rows),'rereads':rows}


class SecondaryPreparationTests(unittest.TestCase):
    def test_high_confidence_assets_are_consumer_requirements_and_diagnostics_stay_opt_in(self):
        x=observation(count=2);before=deepcopy(x)
        self.assertEqual(plan_key_field_rereads(x,selection_mode=DOUBTFUL_SELECTION)['requests'],[])
        plan=plan_key_field_rereads(x)
        self.assertEqual([r['field'] for r in plan['requests']],['asset','asset'])
        self.assertTrue(all(r['selection_reasons']==['asset_corroboration_required'] for r in plan['requests']))
        self.assertEqual(x,before)
        # Existing flagged event diagnostics join the plan once, not a new recognizer.
        x['pages'][0]['rows'][0]['cells']['event_date']['raw_ocr_confidence']=40.
        self.assertEqual(plan_key_field_rereads(x)['required_cell_count'],3)
        x['pages'][0]['rows'].append({'physical_band_index':3,'disposition':'data_candidate','cells':None})
        self.assertEqual(plan_key_field_rereads(x)['required_cell_count'],3)

    def test_bad_inputs_and_total_budget_fail_before_any_render_or_ocr(self):
        cases=[]
        x=observation(count=33);cases.append((x,{},'exceeds cap'))
        x=observation();x['pages'][0]['rows'][0]['cells']['asset']['bbox_normalized'][0]=float('nan')
        cases.append((x,{},'geometry'))
        x=observation();x['pages'][0]['orientation_degrees']=90;cases.append((x,{},'rotated'))
        x=observation();x['pages'][0]['rows'].append(deepcopy(x['pages'][0]['rows'][0]));cases.append((x,{},'duplicated'))
        for x,kwargs,message in cases:
            with self.subTest(message=message),patch('pdfplumber.open') as opened,patch(
                    'unison_snapshot.house_ptr_key_field_reread._second_ocr') as ocr:
                with self.assertRaisesRegex(ValueError,message):
                    reread_doubtful_key_cells(b'synthetic source',x,executable=Path('unused'),
                        selection_mode=MATERIALIZATION_SELECTION,**kwargs)
                opened.assert_not_called();ocr.assert_not_called()

    def test_cache_coverage_checks_first_reading_and_reuses_complete_legacy_evidence(self):
        x=observation(count=2);plan=plan_key_field_rereads(x);saved=cache_for(plan)
        with patch('pdfplumber.open',side_effect=AssertionError('cache must not render')):
            actual=reread_doubtful_key_cells(b'synthetic source',x,executable=Path('unused'),
                selection_mode=MATERIALIZATION_SELECTION,existing_reread=saved)
        self.assertEqual(actual,saved);self.assertIsNot(actual,saved)
        for name in ('first_ocr_raw','first_crop_sha256','second_crop_sha256'):
            bad=deepcopy(saved);bad['rereads'][0][name]='changed'
            self.assertEqual(missing_key_field_rereads(plan,bad),[plan['requests'][0]])
        for change in ('source','count','duplicate','version'):
            bad=deepcopy(saved)
            if change=='source':bad['source_sha256']='c'*64
            if change=='count':bad['selected_cell_count']=True
            if change=='duplicate':bad['rereads'].append(deepcopy(bad['rereads'][0]));bad['selected_cell_count']+=1
            if change=='version':bad['rule_version']='obsolete'
            with self.subTest(change=change),self.assertRaises(ValueError):missing_key_field_rereads(plan,bad)
        for bad in ([],dict(saved,rereads=[None],selected_cell_count=1)):
            with self.assertRaises(ValueError):missing_key_field_rereads(plan,bad)

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'optional image dependency unavailable')
    def test_partial_cache_prepares_only_missing_cells_and_preserves_other_evidence(self):
        from PIL import Image
        x=observation(count=2);plan=plan_key_field_rereads(x);saved=cache_for(plan,plan['requests'][:1])
        saved['rereads'].append({'page':1,'physical_band_index':1,'field':'owner','reading_mode':'saved owner proof'})
        saved['selected_cell_count']=2;saved['other_receipt']={'keep':True};before=deepcopy(saved)
        image=Image.new('L',(1000,1000),'white')
        page=SimpleNamespace(to_image=lambda **kw:SimpleNamespace(original=image))
        context=SimpleNamespace(pages=[page])
        with patch('pdfplumber.open') as opened,patch(
                'unison_snapshot.house_ptr_key_field_reread._second_ocr',return_value=('CARLISLE INC','b'*64)) as ocr:
            opened.return_value.__enter__.return_value=context
            actual=reread_doubtful_key_cells(b'synthetic source',x,executable=Path('unused'),
                selection_mode=MATERIALIZATION_SELECTION,existing_reread=saved)
        ocr.assert_called_once();self.assertEqual(saved,before)
        self.assertIn(saved['rereads'][0],actual['rereads']);self.assertIn(saved['rereads'][1],actual['rereads'])
        self.assertEqual(actual['other_receipt'],saved['other_receipt'])
        self.assertEqual(missing_key_field_rereads(plan,actual),[])
        # Changing why a crop was selected cannot invalidate the same bound reading.
        actual['rereads'][-1]['source_request']['selection_reasons']=['diagnostic']
        self.assertEqual(missing_key_field_rereads(plan,actual),[])

    def test_plan_only_cli_needs_no_pdf_renderer_and_budget_failure_leaves_no_output(self):
        x=observation()
        with TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';pdf.write_bytes(b'synthetic source')
            source=root/'observation.json';source.write_text(json.dumps(x),encoding='utf-8')
            target=root/'plan.json'
            args=['reread','--pdf',str(pdf),'--observation',str(source),'--output',str(target),'--plan-only']
            with patch.object(sys,'argv',args),redirect_stdout(io.StringIO()),patch(
                    'pdfplumber.open',side_effect=AssertionError('plan must not render')):
                main()
            self.assertEqual(json.loads(target.read_bytes()),plan_key_field_rereads(x))
            target.unlink();cached=root/'cache.json';cached.write_text(json.dumps(cache_for(plan_key_field_rereads(x),[])))
            with patch.object(sys,'argv',args+['--existing-rereads',str(cached)]),redirect_stdout(io.StringIO()):main()
            self.assertEqual(json.loads(target.read_bytes())['cache_coverage']['missing_cell_count'],1)
            x=observation(count=2);source.write_text(json.dumps(x),encoding='utf-8');target.unlink()
            with patch.object(sys,'argv',args+['--max-cells','1']),self.assertRaisesRegex(ValueError,'exceeds cap'):
                main()
            self.assertFalse(target.exists())

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'optional image dependency unavailable')
    def test_failed_preparation_leaves_input_cache_and_output_untouched(self):
        from PIL import Image
        x=observation(count=2);saved=cache_for(plan_key_field_rereads(x),[])
        page=SimpleNamespace(to_image=lambda **kw:SimpleNamespace(original=Image.new('L',(1000,1000),'white')))
        with TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';pdf.write_bytes(b'synthetic source')
            source=root/'observation.json';source.write_text(json.dumps(x),encoding='utf-8')
            cached=root/'existing.json';cached.write_text(json.dumps(saved),encoding='utf-8');before=cached.read_bytes()
            target=root/'new.json'
            args=['reread','--pdf',str(pdf),'--observation',str(source),'--existing-rereads',str(cached),
                  '--output',str(target),'--tesseract','fixture']
            with patch.object(sys,'argv',args),patch('pdfplumber.open') as opened,patch(
                    'unison_snapshot.house_ptr_key_field_reread._second_ocr',
                    side_effect=[('CARLISLE INC','b'*64),ValueError('engine failed')]):
                opened.return_value.__enter__.return_value=SimpleNamespace(pages=[page])
                with self.assertRaisesRegex(ValueError,'engine failed'):main()
            self.assertFalse(target.exists());self.assertEqual(cached.read_bytes(),before)

    @unittest.skipUnless(importlib.util.find_spec('pymupdf') and importlib.util.find_spec('pdfplumber'),
                         'optional PDF dependencies unavailable')
    def test_real_pdf_preparation_cli_and_consumer_use_the_same_unchanged_crop(self):
        import pymupdf
        import pdfplumber
        from unison_snapshot.house_ptr_paper_qualification import _asset_reading_is_bound
        document=pymupdf.open();page=document.new_page(width=216,height=288)
        page.insert_text((25,32),'CARLISLE INC',fontsize=8)
        raw=document.tobytes();document.close();x=observation(raw);before=deepcopy(x)
        def recognized(crop,_):
            encoded=io.BytesIO();crop.save(encoded,format='PNG')
            return 'CARLISLE INC',hashlib.sha256(encoded.getvalue()).hexdigest()
        with TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';pdf.write_bytes(raw)
            source=root/'observation.json';source.write_text(json.dumps(x),encoding='utf-8')
            target=root/'readings.json'
            args=['reread','--pdf',str(pdf),'--observation',str(source),'--output',str(target),'--tesseract','fixture']
            with patch.object(sys,'argv',args),redirect_stdout(io.StringIO()),patch(
                    'unison_snapshot.house_ptr_key_field_reread._second_ocr',side_effect=recognized):
                main()
            prepared=json.loads(target.read_bytes());item=prepared['rereads'][0]
            self.assertEqual(prepared['selection_plan']['selection_mode'],MATERIALIZATION_SELECTION)
            cell=x['pages'][0]['rows'][0]['cells']['asset']
            self.assertTrue(_asset_reading_is_bound(raw,item,cell,1,{}))
            replay=root/'replay.json'
            with patch.object(sys,'argv',[*args[:args.index('--output')],'--output',str(replay),
                    '--tesseract','must not run','--existing-rereads',str(target)]),redirect_stdout(io.StringIO()),patch(
                    'unison_snapshot.house_ptr_key_field_reread._second_ocr',side_effect=AssertionError('replay must not OCR')):
                main()
            self.assertEqual(replay.read_bytes(),target.read_bytes())
            self.assertEqual(x,before)
            item['second_crop_sha256']='a'*64
            self.assertFalse(_asset_reading_is_bound(raw,item,cell,1,{}))


if __name__=='__main__':unittest.main()
