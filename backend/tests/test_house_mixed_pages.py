"""Generated mixed PDFs and fixed OCR outputs: routing, not OCR accuracy."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_house_ptr import fixture_pages, word, META, IDENTITY
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_archived_pdf, parse_word_pages, qualify_automatic, _ocr_pdf_pages


class HouseMixedPageTests(unittest.TestCase):
    def fixture(self, *, native_row=False, blank_middle=False, scanned=True, native=True, zero=False):
        import pymupdf
        import pdfplumber
        from PIL import Image, ImageDraw
        from unison_snapshot.house_native_coverage import observe_native_geometry
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); document = pymupdf.open()
        page = document.new_page(width=612, height=792)
        if native:
            source = [w for w in fixture_pages()[0]['words'] if w['top'] < (390 if native_row else 300)]
            if native_row:
                source += [word('Subholding',105,371,8.5), word('Of:',155,371,8.5), word('NativeAccount',180,371,8.5)]
            for w in source: page.insert_text((w['x0'], w['top']+w['size']), w['text'], fontsize=w['size'])
            if zero: page.insert_text((180,300), 'Nothing to report', fontsize=10)
        if blank_middle: document.new_page(width=612, height=792)
        page = document.new_page(width=612, height=792)
        if scanned:
            image = Image.new('RGB',(400,400),'white')
            ImageDraw.Draw(image).text((20,20),'generated scanned body',fill='black')
            png = io.BytesIO(); image.save(png,format='PNG'); page.insert_image(page.rect,stream=png.getvalue())
        path = root/'source.pdf'; document.save(path); document.close()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = Path(f'house_clerk/documents/2026/20000001/{sha}.pdf')
        target = root/relative; target.parent.mkdir(parents=True); path.replace(target)
        metadata = {**META, 'sha256':sha, 'archive_path':relative.as_posix(), 'retrieved_at':'2026-09-16T00:00:00Z'}
        meta_path = target.with_suffix('.json'); meta_path.write_text(json.dumps(metadata),encoding='utf-8')
        with pdfplumber.open(target) as pdf:
            pages = [{'width':p.width,'height':p.height,
                      'words':p.extract_words(x_tolerance=2,y_tolerance=2,extra_attrs=['size']),
                      'native_page_geometry':observe_native_geometry(p,sha,n)} for n,p in enumerate(pdf.pages,1)]
        return SimpleNamespace(root=root,pdf=target,metadata=metadata,meta_path=meta_path,native_pages=pages,sha=sha)

    def ocr_page(self, confidence=95):
        words = [deepcopy(w) for w in fixture_pages()[0]['words'] if 300 < w['top'] < 390]
        for w in words:
            w['ocr_confidence'] = confidence
            if w['text'] == 'Example': w['text'] = 'Scanned'
        words += [{**word(text,x,371,8.5),'ocr_confidence':confidence} for text,x in (
            ('Subholding',105),('Of:',155),('ScanAccount',180))]
        from PIL import Image
        return {'width':612.,'height':792.,'words':words,'_image':Image.new('L',(20,20),'white')}

    def parse(self, fixture, confidence=95):
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',return_value=([self.ocr_page(confidence)],'tesseract fixture')) as ocr:
            result = parse_archived_pdf(fixture.root,fixture.meta_path)
        return result, ocr

    def cli(self, arguments):
        from unison_snapshot.__main__ import main
        with patch.object(sys,'argv',['unison_snapshot',*map(str,arguments)]),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            main()

    def test_native_cover_no_longer_suppresses_scanned_body_and_blank_page_stays_unknown(self):
        f = self.fixture(blank_middle=True); before = deepcopy(f.native_pages)
        x, ocr = self.parse(f)
        self.assertEqual(ocr.call_args.kwargs,{'page_numbers':[3]})
        self.assertEqual(x['extraction_method'],'mixed_native_ocr')
        self.assertEqual(x['page_text_methods'],['native_pdf_text','native_pdf_text','tesseract_ocr'])
        self.assertEqual(x['transactions'][0]['evidence']['page'],3)
        self.assertEqual(x['source_word_pages'][0]['words'],before[0]['words'])
        self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'],'unknown')
        self.assertNotIn('_image',x['source_word_pages'][2])
        json.dumps(x,allow_nan=False)
        q = qualify_automatic(x,IDENTITY)
        self.assertEqual(q['transactions'][0]['asset_name'],'Scanned Corp. — ScanAccount')
        self.assertEqual(qualify_automatic(json.loads(json.dumps(x)),IDENTITY),q)

    def test_native_rows_keep_their_ids_and_values_while_only_ocr_rows_need_confidence(self):
        f = self.fixture(native_row=True)
        old = parse_word_pages(f.metadata,f.sha,f.native_pages,copy_allowed=True)
        prior = qualify_automatic(old,IDENTITY)['transactions']
        x, _ = self.parse(f,confidence=5)
        q = qualify_automatic(x,IDENTITY)
        self.assertEqual(q['transactions'],prior)
        self.assertEqual(len(q['quarantined']),1)
        self.assertIn('ocr_confidence_below_threshold',q['quarantined'][0]['reasons'])
        self.assertIsNone(x['transactions'][0]['ocr_confidence'])
        self.assertEqual(x['transactions'][1]['ocr_confidence'],5.)

    def test_native_row_with_an_ocr_continuation_keeps_the_ocr_confidence_requirement(self):
        from test_house_ptr import cross_page_fixture_pages
        for confidence in (5,95):
            pages = cross_page_fixture_pages()
            for w in pages[1]['words']: w['ocr_confidence'] = confidence
            before = deepcopy(pages)
            x = parse_word_pages(META,'a'*64,pages,copy_allowed=True,ocr_engine='tesseract fixture',
                                 page_text_methods=['native_pdf_text','tesseract_ocr'])
            self.assertEqual(x['transactions'][0]['ocr_source_pages'],[2])
            q = qualify_automatic(x,IDENTITY)
            if confidence < 80:
                self.assertEqual(q['transactions'],[])
                self.assertTrue(all('ocr_confidence_below_threshold' in r['reasons'] for r in q['quarantined']))
            else:
                self.assertEqual(len(q['transactions']),2)
            self.assertEqual(pages,before)

    def test_saved_mixed_qualification_and_candidate_replay_reject_source_or_method_changes(self):
        for change in ('method','ocr_method','missing_methods','methods','page_tag','words',
                       'row_confidence','ocr_pages','missing_pages'):
            with self.subTest(change=change):
                f = self.fixture(native_row=True); x, _ = self.parse(f)
                if change == 'method': x['extraction_method'] = 'native_pdf_text'
                if change == 'ocr_method': x['extraction_method'] = 'tesseract_ocr'
                if change == 'missing_methods': x.pop('page_text_methods')
                if change == 'methods': x['page_text_methods'].reverse()
                if change == 'page_tag': x['source_word_pages'][1]['text_reading_method'] = 'native_pdf_text'
                if change == 'words': x['source_word_pages'][1]['words'][0]['text'] = 'edited'
                if change == 'row_confidence': x['transactions'][1]['ocr_confidence'] = 100.
                if change == 'ocr_pages': x['transactions'][1]['ocr_source_pages'] = []
                if change == 'missing_pages': x.pop('source_word_pages')
                with self.assertRaises(HouseIndexError): qualify_automatic(x,IDENTITY)

    def test_normal_parse_qualification_candidate_status_and_builder_consume_the_mixed_result(self):
        from test_house_candidate import BASE
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.disclosure_status_house import build_house_status
        from unison_snapshot.builder import build
        f = self.fixture(native_row=True); review = f.root/'review'; relative = f.meta_path.relative_to(f.root/'house_clerk/documents')
        x_path = review/'house_clerk/extractions'/relative
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',return_value=([self.ocr_page()],'tesseract fixture')):
            self.cli(['parse-house-ptr','--archive',f.root,'--metadata',f.meta_path,'--output',x_path])
        identity = f.root/'identity.json'; identity.write_text(json.dumps(IDENTITY))
        q_path = review/'house_clerk/qualifications'/relative
        self.cli(['qualify-house-ptr','--extraction',x_path,'--identity',identity,'--output',q_path])
        q = json.loads(q_path.read_bytes())
        status = review/'status/house_clerk.json'; status.parent.mkdir(parents=True)
        status.write_text(json.dumps({'parser_commit':'a'*40,'pending_parse_count':0,'qualification_count':1,
            'qualified_transaction_count':len(q['transactions']),'quarantined_row_count':len(q['quarantined']),
            'evidence_count':1,'extracted_count':1,'failure_count':0}))
        state = {'status':'ok','run_at':'2026-09-18T12:00:00Z','counts':{'archived':1,'pending':0,'failed':0}}
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',side_effect=AssertionError('saved consumer must not OCR')):
            candidate = build_house_candidate(review,state,deepcopy(BASE))
            self.assertEqual(build_house_candidate(review,state,deepcopy(BASE)),candidate)
        self.assertEqual({r['asset_name'] for r in candidate['transactions']},
                         {'Example Corp. — NativeAccount','Scanned Corp. — ScanAccount'})
        reports = build_house_status(f.root,review,candidate['transactions'],[])[0]['reports']
        self.assertEqual(reports[0]['qualified_transaction_count'],2)
        self.assertTrue(build(candidate,generated_at='2026-09-18T12:00:00Z',allow_production=True).manifest['snapshot_id'])
        changed = json.loads(q_path.read_bytes()); changed['transactions'][0]['amount_high'] = 999999
        q_path.write_text(json.dumps(changed))
        with self.assertRaises(HouseIndexError): build_house_candidate(review,state,deepcopy(BASE))

    def test_ocr_failure_or_mismatched_output_does_not_replace_the_prior_cli_artifact(self):
        for failure in ('engine','count','dimensions'):
            with self.subTest(failure=failure):
                f = self.fixture(); output = f.root/'prior.json'; output.write_bytes(b'prior result')
                page = self.ocr_page()
                if failure == 'dimensions': page['width'] = 999
                options = {'side_effect':HouseIndexError('injected OCR failure')} if failure == 'engine' else {
                    'return_value':([] if failure == 'count' else [page],'tesseract fixture')}
                with patch('unison_snapshot.house_ptr._ocr_pdf_pages',**options),self.assertRaises(SystemExit):
                    self.cli(['parse-house-ptr','--archive',f.root,'--metadata',f.meta_path,'--output',output])
                self.assertEqual(output.read_bytes(),b'prior result')
                self.assertEqual(hashlib.sha256(f.pdf.read_bytes()).hexdigest(),f.sha)

    def test_empty_ocr_is_not_a_zero_transaction_declaration_or_complete_page(self):
        f = self.fixture(native_row=True)
        empty = {'width':612.,'height':792.,'words':[]}
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',return_value=([empty],'tesseract fixture')):
            x = parse_archived_pdf(f.root,f.meta_path)
        q = qualify_automatic(x,IDENTITY)
        self.assertEqual(q['qualification']['zero_transaction_document_count'],0)
        self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'],'unknown')
        f = self.fixture(zero=True)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',return_value=([empty],'tesseract fixture')):
            with self.assertRaisesRegex(HouseIndexError,'zero-transaction'):
                parse_archived_pdf(f.root,f.meta_path)

    def test_pure_native_blank_tail_and_pure_ocr_keep_the_existing_paths(self):
        f = self.fixture(native_row=True,scanned=False)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',side_effect=AssertionError('native must not OCR')):
            x = parse_archived_pdf(f.root,f.meta_path)
        self.assertEqual(x['extraction_method'],'native_pdf_text')
        self.assertNotIn('page_text_methods',x)
        f = self.fixture(native=False)
        first = fixture_pages()[0]
        for w in first['words']: w['ocr_confidence'] = 95.
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',return_value=([first,{'width':612.,'height':792.,'words':[]}],'tesseract fixture')) as ocr:
            x = parse_archived_pdf(f.root,f.meta_path)
        self.assertEqual(ocr.call_args.kwargs,{})
        self.assertEqual(x['extraction_method'],'tesseract_ocr')
        self.assertNotIn('page_text_methods',x)

    def test_mixed_legacy_header_remains_explicitly_unsupported(self):
        f = self.fixture(native=False)
        import pymupdf
        document = pymupdf.open(f.pdf); document[0].insert_text((40,40),'UNITED STATES HOUSE Periodic Transaction Report',fontsize=9)
        path = f.root/'legacy.pdf'; document.save(path); document.close()
        metadata = {**f.metadata,'archive_path':'legacy.pdf','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        f.meta_path.write_text(json.dumps(metadata))
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',side_effect=AssertionError('unsupported mixed header must not OCR')):
            with self.assertRaisesRegex(HouseIndexError,'native modern PTR header'):
                parse_archived_pdf(f.root,f.meta_path)

    def test_ocr_subset_renders_only_selected_pages_in_the_original_order_and_mediabox(self):
        from PIL import Image
        pages = [SimpleNamespace(width=612.,height=792.,bbox=(0,0,612,792),
            to_image=Mock(return_value=SimpleNamespace(original=Image.new('L',(30,40),'white')))) for _ in range(3)]
        def run(arguments,**kwargs):
            if '--version' in arguments: return SimpleNamespace(returncode=0,stdout='tesseract fixture')
            label = Path(arguments[1]).stem
            return SimpleNamespace(returncode=0,stdout='level\ttext\tconf\tleft\ttop\theight\n5\t'+label+'\t95\t10\t20\t9\n')
        with patch('unison_snapshot.house_ptr.shutil.which',return_value='fixture-engine'),patch(
                'unison_snapshot.house_ptr.subprocess.run',side_effect=run):
            result, _ = _ocr_pdf_pages(SimpleNamespace(pages=pages),page_numbers=[3,1])
        self.assertEqual([p['words'][0]['text'] for p in result],['page-3','page-1'])
        pages[1].to_image.assert_not_called()
        for page in (pages[0],pages[2]):
            page.to_image.assert_called_once_with(resolution=200,antialias=True,force_mediabox=True)

    def test_bad_ocr_subset_fails_before_starting_the_recognizer(self):
        document = SimpleNamespace(pages=[None,None])
        for selected in ([],[0],[True],[3],[1,1],(1,)):
            with self.subTest(selected=selected),patch('unison_snapshot.house_ptr.shutil.which') as engine:
                with self.assertRaises(HouseIndexError): _ocr_pdf_pages(document,page_numbers=selected)
                engine.assert_not_called()

    def test_selected_ocr_uses_the_full_mediabox_on_an_actual_cropped_pdf(self):
        import pymupdf
        import pdfplumber
        from PIL import Image
        f = self.fixture(); document = pymupdf.open(f.pdf)
        document[1].set_cropbox(pymupdf.Rect(10,20,602,782))
        cropped = f.root/'cropped.pdf'; document.save(cropped); document.close()
        dimensions = []
        def run(arguments,**kwargs):
            if '--version' in arguments: return SimpleNamespace(returncode=0,stdout='tesseract fixture')
            with Image.open(arguments[1]) as image: dimensions.append(image.size)
            return SimpleNamespace(returncode=0,stdout='level\ttext\tconf\tleft\ttop\theight\n5\tTOKEN\t95\t100\t200\t10\n')
        with pdfplumber.open(cropped) as pdf,patch('unison_snapshot.house_ptr.shutil.which',return_value='fixture-engine'),patch(
                'unison_snapshot.house_ptr.subprocess.run',side_effect=run):
            result, _ = _ocr_pdf_pages(pdf,page_numbers=[2])
        self.assertEqual(dimensions,[(1700,2200)])
        self.assertEqual((result[0]['width'],result[0]['height']),(612,792))
        self.assertAlmostEqual(result[0]['words'][0]['x0'],36.)

    def test_nonzero_page_origin_is_not_silently_reinterpreted_as_a_mixed_ocr_frame(self):
        page = SimpleNamespace(width=612.,height=792.,bbox=(10,20,622,812),to_image=Mock())
        with patch('unison_snapshot.house_ptr.shutil.which',return_value='fixture-engine'),patch(
                'unison_snapshot.house_ptr.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='tesseract fixture')):
            with self.assertRaisesRegex(HouseIndexError,'origin'):
                _ocr_pdf_pages(SimpleNamespace(pages=[page]),page_numbers=[1])
        page.to_image.assert_not_called()

    def workflow_script(self, name):
        repo = Path(__file__).resolve().parents[2]
        text = (repo/'.github/workflows/house-review.yml').read_text(encoding='utf-8')
        step = text.split('- name: '+name,1)[1].split('- name:',1)[0]
        script = textwrap.dedent(step.split("python - <<'PY'",1)[1].split('\n          PY',1)[0])
        return script, {**os.environ,'PYTHONPATH':str(repo/'backend/src')}

    def test_normal_workflow_prepares_ocr_for_mixed_pages_and_not_for_native_blank_tails(self):
        script, env = self.workflow_script('Detect pages requiring OCR in this batch')
        for scanned, expected in ((True,'true'),(False,'false')):
            f = self.fixture(native_row=True,scanned=scanned)
            local = f.root/'.local'; local.mkdir()
            (local/'parse-batch.txt').write_text(str(f.meta_path)+'\n',encoding='utf-8')
            output = f.root/'github-output.txt'
            done = subprocess.run([sys.executable,'-c',script],cwd=f.root,env={**env,
                'EVIDENCE_ROOT':str(f.root),'PAPER_DOCUMENT_ID':'','GITHUB_OUTPUT':str(output)},
                capture_output=True,text=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(output.read_text().strip(),'needed='+expected)

    def test_old_failures_reenter_default_selection_and_current_failures_remain_bounded(self):
        from unison_snapshot.house_ptr import PARSER_RETRY_VERSION, LEGACY_PARSER_VERSION
        script, env = self.workflow_script('Select unprocessed evidence')
        f = self.fixture(); review = f.root/'review'
        relative = f.meta_path.relative_to(f.root/'house_clerk/documents')
        failure = review/'house_clerk/failures'/relative; failure.parent.mkdir(parents=True)
        env.update(EVIDENCE_ROOT=str(f.root),REVIEW_ROOT=str(review),PARSER_COMMIT='a'*40,
                   LEGACY_PARSER_VERSION=LEGACY_PARSER_VERSION,PARSER_RETRY_VERSION=PARSER_RETRY_VERSION,BATCH_SIZE='25')
        self.assertNotEqual(PARSER_RETRY_VERSION,'house-parser-suite-2026-08')
        for version, selected in (('house-parser-suite-2026-08',True),(PARSER_RETRY_VERSION,False)):
            failure.write_text(json.dumps({'parser_version':version}),encoding='utf-8')
            done = subprocess.run([sys.executable,'-c',script],cwd=f.root,env=env,capture_output=True,text=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(bool((f.root/'.local/parse-batch.txt').read_text().strip()),selected)


if __name__ == '__main__': unittest.main()
