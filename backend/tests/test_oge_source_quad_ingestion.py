"""Generated direct-OGE source using existing six-column/cover primitives.

The saved words/TSV are construction-controlled observations, not an OCR
accuracy claim. No recognizer is run. Source pixels, grids and warps are real.
"""
from copy import deepcopy
import os,subprocess,sys,tempfile,unittest
import hashlib,io,json
from pathlib import Path

EDGES=[104.,150.,473.,524.,581.,628.,686.]

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')

def actual_source_quad_fixture(root:Path):
    import pdfplumber,pymupdf
    from unison_snapshot.ocr_geometry import OcrPage
    from unison_snapshot.oge_reports import parse_archived_pdf
    from unison_snapshot.oge_278t_raster_slots import observe_raster_slots,DUAL_MODE
    from tempfile import TemporaryDirectory
    from test_oge_cover_consumption import fixture as cover_fixture
    with TemporaryDirectory(prefix='source-quad-cover-') as temporary:
        seed=cover_fixture(Path(temporary))
        catalog=deepcopy(seed['catalog'])
        record=catalog['transactions'][0];document=record['source_document_id']
        metadata=deepcopy(seed['metadata']);original_bytes=seed['source'].read_bytes()
        catalog_files={p.relative_to(seed['evidence']):p.read_bytes()
                       for p in (seed['evidence']/'oge/catalog').rglob('*') if p.is_file()}
        status_bytes=(seed['review']/'status/oge.json').read_bytes()
    with pymupdf.open() as body:
        page=body.new_page(width=792,height=612)
        for x in EDGES:page.draw_line((x,85),(x,581),width=.5)
        for y in [85,*range(116,582,15)]:page.draw_line((104,y),(686,y),width=.5)
        page.insert_text((104,26),'OGE Form 278-T 2024',fontsize=8)
        for x,label in zip(EDGES[1:],['Description','Type','Date','Notification','Amount']):
            page.insert_text((x+3,111),label,fontsize=8)
        for x,text in zip(EDGES,['1','ALPHA Fund','Purchase','06/01/2026','No','$50,001 -']):
            page.insert_text((x+4,124),text,fontsize=6)
        page.insert_text((EDGES[5]+4,129),'$100,000',fontsize=6)
        vector=body.tobytes()
        image=page.get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False).tobytes('png')
    with pdfplumber.open(io.BytesIO(vector)) as pdf:
        truth=pdf.pages[0].extract_words(x_tolerance=2,y_tolerance=2)
    with pymupdf.open(stream=original_bytes,filetype='pdf') as pdf:
        while len(pdf)>1:pdf.delete_page(1)
        page=pdf.new_page(width=792,height=612)
        page.insert_image(page.rect,stream=image)
        page.insert_text((104,10),'Source quad contract fixture',fontsize=6)
        raw=pdf.tobytes()
    sha=hashlib.sha256(raw).hexdigest()
    evidence,review=root/'evidence',root/'review'
    path=evidence/f'oge/reports/{document}/{sha}.pdf';path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    metadata={**metadata,'sha256':sha,'byte_length':len(raw),
              'archive_path':path.relative_to(evidence).as_posix()}
    write(path.with_suffix('.json'),metadata)
    primary_words=[]
    for number,word in enumerate(truth,1):
        text=word['text']
        if text=='Purchase':text='?'
        if text in {'$50,001','$100,000'}:text='?'
        primary_words.append({**word,'text':text,'ocr_confidence':95.,'block_num':1,
                              'par_num':1,'line_num':number})
    observation=observe_raster_slots(path,expected_sha256=sha,page_numbers=[2],resolution=300,
        saved_ocr_pages={2:OcrPage(width=792,height=612,words=primary_words)},
        saved_ocr_engine='construction-controlled primary words; no OCR',geometry_mode=DUAL_MODE)
    for relative,raw_catalog in catalog_files.items():
        target=evidence/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw_catalog)
    (review/'status').mkdir(parents=True);(review/'status/oge.json').write_bytes(status_bytes)
    parsed=parse_archived_pdf(evidence,path.with_suffix('.json'))
    generic=parse_archived_pdf(evidence,path.with_suffix('.json'),generic=True)
    assert parsed['parser_version']=='oge-278t-pdf/v2' and generic['parser_version']=='oge-278t-pdf/v7'
    for value in [parsed,generic]:
        write(review/f'oge/extractions/{document}/{sha}/{value["parser_version"].replace("/","-")}.json',value)
    write(root/'primary.json',observation);write(root/'catalog.json',catalog)
    write(root/'construction-truth-words.json',truth)
    return {'root':root,'evidence':evidence,'review':review,'source':path,'metadata_path':path.with_suffix('.json'),
            'metadata':metadata,'record':record,'catalog':catalog,'old':parsed,'generic':generic,
            'observation':observation,'truth_words':truth,'sha':sha,'document':document}

def actual_source_quad_secondary(data,field,rendered):
    import numpy as np,math
    from unison_snapshot.oge_278t_source_quad_cells import source_quad_cell,observe_saved_source_quad_cells
    page=data['observation']['pages'][0]
    slot=next(s for s in page['slots'] if s['words'])
    recipe,pixels=source_quad_cell(rendered,page,slot,field,layer='full_source')
    # Known construction word boxes are mapped through the *actual* source
    # affine and warp. No fixed pixel recipe or synthetic render is accepted.
    affine=rendered['receipt']['pixel_edge_to_source_pdf_affine']
    media=rendered['receipt']['source_mediabox_pdf_bottom_left']
    inverse=np.linalg.inv(np.array([[affine[0],affine[2],affine[4]],
                                    [affine[1],affine[3],affine[5]],[0.,0.,1.]]))
    warp=np.array(recipe['source_pixel_to_cell_matrix'])
    words=[w for w in data['truth_words'] if w['top']>116 and
           ((field=='type' and EDGES[2]<w['x0']<EDGES[3]) or
            (field=='amount' and EDGES[5]<w['x0']<EDGES[6]))]
    tsv=['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
    for index,w in enumerate(words,1):
        points=[]
        for x,y in [(w['x0'],w['top']),(w['x1'],w['top']),(w['x1'],w['bottom']),(w['x0'],w['bottom'])]:
            p=inverse@np.array([x+media[0],media[3]-y,1.]);p=warp@np.array([p[0]/p[2],p[1]/p[2],1.])
            points.append((p[0]/p[2]+recipe['padding'],p[1]/p[2]+recipe['padding']))
        left,top=math.floor(min(p[0] for p in points)),math.floor(min(p[1] for p in points))
        right,bottom=math.ceil(max(p[0] for p in points)),math.ceil(max(p[1] for p in points))
        line=1 if w['top']<122 else 2
        tsv.append(f'5\t1\t1\t1\t{line}\t{index}\t{left}\t{top}\t{right-left}\t{bottom-top}\t95\t{w["text"]}')
    text='\n'.join(tsv)+'\n'
    recipe.update(physical_index=slot['slot_index']-1,column=2 if field=='type' else 4,
                  tsv_sha256=hashlib.sha256(text.encode()).hexdigest())
    saved=[{'page_number':2,'render_receipt':rendered['receipt'],'selected_slot_indices':[slot['slot_index']],
            'cells':[{'slot_index':slot['slot_index'],'recipe':recipe,'tsv_utf8':text}]}]
    return observe_saved_source_quad_cells(data['source'],slots_observation=data['observation'],field=field,
        saved_pages=saved,layer='full_source',ocr_engine='construction-controlled saved secondary TSV; no OCR')


ROOT=Path(__file__).resolve().parents[2]
CUTOFF='2026-06-20T00:00:00Z'


class SourceQuadIngestionTests(unittest.TestCase):
    """Real source/renderer/warp replay, construction TSV, never OCR accuracy."""

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='oge-quad-ingestion-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root=Path(cls.temporary.name)
        cls.data=actual_source_quad_fixture(cls.root)
        from unison_snapshot.pdf_text_objects import render_pdf_text_objects
        render=render_pdf_text_objects(cls.data['source'],expected_sha256=cls.data['sha'],
                                       page_number=2,resolution=300)
        cls.secondary=[]
        for field in ('type','amount'):
            reading=actual_source_quad_secondary(cls.data,field,render)
            cls.secondary.append(reading);write(cls.root/f'{field}.json',reading)
        from test_oge_candidate import base
        write(cls.root/'ingest/current.json',base())
        write(cls.root/'.local/oge-catalog.json',cls.data['catalog'])
        cls.ledger=cls.root/'field-ledger.json'
        cls.package=(cls.data['review']/f'oge/saved_observations/{cls.data["document"]}/'
                     f'{cls.data["sha"]}/saved-observations.json')

    def command(self,*arguments):
        return subprocess.run([sys.executable,*map(str,arguments)],cwd=ROOT,
            env={**os.environ,'PYTHONPATH':str(ROOT/'backend/src'),'PYTHONIOENCODING':'utf-8'},
            capture_output=True,text=True)

    def writer(self):
        return self.command(ROOT/'backend/scripts/observe_oge_278t_fields.py',
            '--observations',self.root/'primary.json','--secondary-observations',
            self.root/'type.json',self.root/'amount.json','--review-root',self.data['review'],
            '--evidence-root',self.data['evidence'],'--metadata',self.data['metadata_path'],
            '--output',self.ledger)

    def workflow(self):
        from test_oge_annual_workflow import bash_binary
        from test_oge_saved_observation_workflow import source_branch
        shell=bash_binary()
        if shell is None:self.skipTest('Bash is required for the real saved workflow branch')
        script=('set -euo pipefail\n'
            'python() { PYTHONPATH="$TEST_SOURCE_ROOT" "$REAL_PYTHON" "$@"; }\n'
            'cutoff="$TEST_CUTOFF"\n'+source_branch())
        return subprocess.run([shell,'-c',script],cwd=self.root,
            env={**os.environ,'REAL_PYTHON':sys.executable,'TEST_SOURCE_ROOT':str(ROOT/'backend/src'),
                'TEST_CUTOFF':CUTOFF,'INCLUDE_SAVED_OGE_OBSERVATIONS':'true',
                'OGE_SAVED_OBSERVATIONS_PATH':self.package.relative_to(self.data['review']).as_posix(),
                'OGE_GENERIC_DOCUMENT_ID':'','OGE_GENERIC_DOCUMENT_IDS':'','OGE_DOCUMENT_SOURCES':'',
                'REVIEW_ROOT':str(self.data['review']),'EVIDENCE_ROOT':str(self.data['evidence']),
                'MSYS_NO_PATHCONV':'1','PYTHONIOENCODING':'utf-8'},capture_output=True,text=True)

    def paths(self):
        return (self.data['review']/'candidates/sources/oge-current.json',
                self.data['review']/'oge/qualifications/current.json')

    def test_actual_writer_workflow_candidate_status_public_reader_and_next_base_repeat(self):
        from unison_snapshot.disclosure_status_oge import build_oge_status
        from unison_snapshot.builder import build
        from unison_snapshot.disclosure_status import attach_disclosure_status
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        before=self.data['source'].read_bytes()
        result=self.writer();self.assertEqual(result.returncode,0,result.stderr)
        portable=json.loads(self.package.read_bytes())
        self.assertNotIn('archived_pdf_path',portable[self.data['document']])
        self.assertEqual([r['read_mode'] for r in portable[self.data['document']]['secondary_observations']],
                         ['source_quad_cells/v1']*2)
        result=self.workflow();self.assertEqual(result.returncode,0,result.stderr)
        candidate_path,audit_path=self.paths()
        candidate=json.loads(candidate_path.read_bytes());audit=json.loads(audit_path.read_bytes())
        self.assertEqual(len(candidate['transactions']),1)
        fact=candidate['transactions'][0]
        self.assertEqual((fact['asset_name'],fact['transaction_type'],fact['amount_low'],fact['amount_high'],
                          fact['owner'],fact['transaction_date']),
                         ('ALPHA Fund','purchase',50001,100000,'Undisclosed','2026-06-01'))
        receipt=audit['reports'][0]['observation_receipt']
        self.assertFalse(receipt['coverage_complete'])
        self.assertEqual(sum(r['disposition']=='materialized' for r in receipt['rows']),1)
        self.assertGreater(receipt['unresolved_slot_count'],0)
        scopes=build_oge_status(self.data['evidence'],self.data['review'],candidate['transactions'],[])
        record=next(r for s in scopes for r in s['reports'] if r['report_id']==self.data['document'])
        self.assertEqual(record['qualification_state'],'partial')
        self.assertEqual(record['qualified_transaction_count'],1)
        self.assertEqual(record['publication_transaction_ids'],[fact['id']])
        bundle=build(candidate,generated_at=CUTOFF,allow_production=True)
        bundle=attach_disclosure_status(bundle,scopes,transactions=candidate['transactions'],
            provenance={k:v*40 for k,v in [('code_commit','a'),('evidence_commit','b'),
                ('review_commit','c'),('previous_main_commit','d')]})
        public=PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'],candidate['transactions'])
        self.assertTrue(public.disclosure_status(public.resolve(),include_records=True))
        write(self.root/'ingest/current.json',candidate)
        result=self.workflow();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(candidate_path.read_bytes())['transactions'],[fact])
        repeated=(candidate_path.read_bytes(),audit_path.read_bytes())
        result=self.workflow();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(repeated,(candidate_path.read_bytes(),audit_path.read_bytes()))
        self.assertEqual(self.data['source'].read_bytes(),before)

    def test_changed_source_or_tsv_rejects_before_existing_writer_outputs_change(self):
        # All variants use the same real source/recipes; no extra render/OCR.
        originals={p:p.read_bytes() for p in [self.root/'type.json',self.root/'amount.json',self.root/'primary.json']}
        self.package.parent.mkdir(parents=True,exist_ok=True)
        self.package.write_bytes(b'prior saved input');self.ledger.write_bytes(b'prior ledger')
        for kind in ('source','tsv','annotation_receipt'):
            with self.subTest(kind=kind):
                reading=json.loads(originals[self.root/'type.json'])
                if kind=='source':reading['source_sha256']='0'*64
                elif kind=='tsv':reading['source_quad_input']['saved_pages'][0]['cells'][0]['tsv_utf8']+='tamper'
                else:reading['source_quad_input']['saved_pages'][0]['render_receipt']['annotation_count_excluded_from_all_renders']=1
                write(self.root/'type.json',reading)
                result=self.writer();self.assertNotEqual(result.returncode,0)
                self.assertEqual(self.package.read_bytes(),b'prior saved input')
                self.assertEqual(self.ledger.read_bytes(),b'prior ledger')
                (self.root/'type.json').write_bytes(originals[self.root/'type.json'])


if __name__=='__main__':unittest.main()
