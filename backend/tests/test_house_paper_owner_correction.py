"""Real generated print/source crops; fixed saved words are not OCR accuracy gold."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

import test_house_ptr_paper_qualification as paper_tests
from unison_snapshot.house_asset_line_order import MODE
from unison_snapshot.house_paper_owner_correction import PROFILE_KEY, prepare_paper_owner_correction
from unison_snapshot.house_ptr import parse_word_pages, qualify_automatic
from unison_snapshot.house_ptr_amount_header import evaluate_amount_header_pages, LABELS
from unison_snapshot.house_ptr_cell_observation_shadow import observe_house_ptr_pdf
from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation, paper_materialization_counts
from unison_snapshot.ocr_geometry import OcrPage


def paper_owner_fixture(root, *, owner_text='', row_tops=(600,700)):
    """Two identical financial rows, actual legacy IDs and source-bound grid.

    The sole patched producer returns complete fixed saved OCR words for this
    generated printed PDF. Grid/column detection, every crop, blank source
    proof, historical parsing and financial qualification remain real calls.
    """
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    document=pymupdf.open();page=document.new_page(width=1000,height=1000);words=[]
    def word(text,x,top,size=12,line=1):
        page.insert_text((x,top+size),text,fontsize=size)
        words.append({'text':text,'x0':x,'top':top,
            'x1':x+pymupdf.get_text_length(text,fontsize=size),'bottom':top+size,
            'size':size,'ocr_confidence':96,'block_num':1,'par_num':1,'line_num':line})
    for x,text in ((100,'UNITED'),(160,'STATES'),(220,'HOUSE'),(280,'OF'),(310,'REPRESENTATIVES')):
        word(text,x,40,line=1)
    for x,text in ((100,'Periodic'),(180,'Transaction'),(285,'Report')):
        word(text,x,65,line=2)
    word('X',460,400,20,line=3)
    major=[140,405,477,550,607];action=[405,429,453,477]
    amount=[607+(950-607)*i/10 for i in range(11)]
    for y in (200,300,550,650,750,850):page.draw_line((70,y),(980,y),width=1)
    for x in major:page.draw_line((x,200),(x,850),width=1)
    for x in sorted(set([90,140,*action,550,*amount])):page.draw_line((x,300),(x,850),width=1)
    for row,top in enumerate(row_tops,1):
        if owner_text:word(owner_text,95,top,10,line=10+row)
        word('EXAMPLE INC',200,top,16,line=10+row)
        word('05/01/2026',482,top,9.5,line=10+row)
        word('05/03/2026',555,top,9.5,line=10+row)
        word('X',415,top,12,line=10+row);word('X',618,top,12,line=10+row)
        for center in (417,624):
            page.draw_line((center-6,top-15),(center+6,top+35),width=4)
            page.draw_line((center+6,top-15),(center-6,top+35),width=4)
    for index,text in enumerate(['$1,001-$15,000',*LABELS.values()]):
        word(text,100,875+index*9,6,line=30+index)
    raw=document.tobytes();document.close();sha=hashlib.sha256(raw).hexdigest()
    f=paper_tests.HousePaperQualificationTests();f.setUp();f._source_bound_fixture()
    metadata=f.metadata;relative=f"house_clerk/documents/2026/{metadata['document_id']}/{sha}.pdf"
    metadata.update(sha256=sha,archive_path=relative,byte_length=len(raw),retrieved_at='2026-06-11T00:00:00Z')
    identity=f.identity
    legacy_extraction=parse_word_pages(metadata,sha,
        [{'width':1000,'height':1000,'words':deepcopy(words)}],
        copy_allowed=True,ocr_engine='fixed saved control words')
    legacy_qualification=qualify_automatic(legacy_extraction,identity)
    with patch('unison_snapshot.house_ptr_cell_observation_shadow.ocr_pdf_pages',
               return_value=([OcrPage(width=1000,height=1000,words=deepcopy(words))],
                             'fixed saved control words')):
        observation=observe_house_ptr_pdf(raw,source_sha256=sha,asset_line_mode=MODE)
    amount_header=evaluate_amount_header_pages(sha,[{'page':1,'status':'header_ocr_observed',
        'raw_header_text':'\n'.join(['$1,001-$15,000',*LABELS.values()])}])
    rereads=[]
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        image=pdf.pages[0].to_image(resolution=300).original.convert('L')
        for physical in observation['pages'][0]['rows']:
            if physical['disposition']!='data_candidate':continue
            cell=physical['cells']['asset'];box=cell['bbox_normalized'];stream=io.BytesIO()
            image.crop((round(box[0]*image.width)+3,round(box[1]*image.height)+3,
                        round(box[2]*image.width)-3,round(box[3]*image.height)-3)).save(stream,format='PNG')
            rereads.append({'page':1,'physical_band_index':physical['physical_band_index'],
                'field':'asset','first_ocr_raw':cell['raw_text'],'first_crop_sha256':cell['crop_sha256'],
                'second_ocr_raw':'EXAMPLE INC','second_crop_sha256':hashlib.sha256(stream.getvalue()).hexdigest(),
                'second_crop_render_dpi':300})
    field_reread={'schema_version':'house-paper-key-field-reread/v1','source_sha256':sha,
        'rule_version':'house-paper-key-field-conflict-probe-v2','selected_cell_count':len(rereads),
        'qualification_status':'unverified_shadow_only','rereads':rereads}
    evidence_root=root/'evidence';review_root=root/'review';review_root.mkdir(exist_ok=True)
    source_path=evidence_root/relative;source_path.parent.mkdir(parents=True,exist_ok=True);source_path.write_bytes(raw)
    metadata_path=source_path.with_suffix('.json');metadata_path.write_text(json.dumps(metadata),encoding='utf-8')
    return dict(raw=raw,metadata=metadata,identity=identity,observation=observation,
        amount_header=amount_header,field_reread=field_reread,legacy_extraction=legacy_extraction,
        legacy_qualification=legacy_qualification,evidence_root=evidence_root,review_root=review_root,
        source_path=source_path,metadata_path=metadata_path)


def prepare(data, **overrides):
    values=dict(amount_header=data['amount_header'],field_reread=data['field_reread'],
        prior_extraction=data['legacy_extraction'],prior_qualification=data['legacy_qualification'])
    values.update(overrides)
    return prepare_paper_owner_correction(data['raw'],data['observation'],data['metadata'],data['identity'],**values)


class HousePaperOwnerCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        cls.data=paper_owner_fixture(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_actual_legacy_ids_and_defaults_are_preserved_with_only_owner_corrected(self):
        data=self.data;before=deepcopy(data);result=prepare(data)
        self.assertIsNotNone(result)
        self.assertEqual(result['legacy_extraction'],data['legacy_extraction'])
        self.assertEqual(result['legacy_qualification'],data['legacy_qualification'])
        old={row['id']:row for row in data['legacy_qualification']['transactions']}
        final=result['final_qualification']
        self.assertEqual({row['id'] for row in final['transactions']},set(old))
        for row in final['transactions']:
            self.assertEqual(row,{**old[row['id']],'owner':'Undisclosed'})
            self.assertEqual(row['instrument_type'],'Unspecified')
        self.assertEqual(len({m['physical_id'] for m in result['id_mappings']}),2)
        self.assertTrue(set(old).isdisjoint(m['physical_id'] for m in result['id_mappings']))
        self.assertEqual(paper_materialization_counts(final)['observed_transaction_row_count'],2)
        self.assertNotIn(PROFILE_KEY,final['materialized_extraction'])
        self.assertEqual(before,data)

    def test_exact_repeat_rebuilds_history_and_rejects_a_changed_profile(self):
        data=self.data;first=prepare(data);final=first['final_qualification']
        repeated=prepare(data,prior_extraction=final['materialized_extraction'],prior_qualification=final)
        self.assertEqual(repeated,first)
        bad=deepcopy(final);bad[PROFILE_KEY]['id_mappings'][0]['physical_band_index']=3
        self.assertIsNone(prepare(data,prior_extraction=final['materialized_extraction'],prior_qualification=bad))

    def test_full_old_fact_raw_or_missing_saved_words_never_grant_correction(self):
        data=self.data
        bad_q=deepcopy(data['legacy_qualification']);bad_q['transactions'][0]['amount_low']+=1
        bad_raw=deepcopy(data['legacy_extraction']);bad_raw['transactions'][0]['owner']='Spouse'
        nullable=deepcopy(data['legacy_qualification']);nullable['transactions'][0].pop('ticker')
        for overrides in ({'prior_qualification':bad_q},{'prior_extraction':bad_raw},{'prior_qualification':nullable}):
            with self.subTest(overrides=overrides):self.assertIsNone(prepare(data,**overrides))
        missing=deepcopy(data['observation']);missing['pages'][0].pop('asset_line_source')
        self.assertIsNone(prepare({**data,'observation':missing}))
        with patch('unison_snapshot.house_paper_owner_correction.prepare_paper_owner_correction',
                   side_effect=AssertionError('no old raw means no correction attempt')):
            retained=materialize_paper_observation(data['raw'],data['observation'],data['metadata'],data['identity'],
                prior_qualification=data['legacy_qualification'])
        self.assertEqual(retained,data['legacy_qualification'])

    def test_actual_nonblank_owner_and_crossing_physical_geometry_keep_complete_prior(self):
        for kwargs in ({'owner_text':'INK'},{'row_tops':(642,700)}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as directory:
                data=paper_owner_fixture(Path(directory),**kwargs)
                self.assertTrue(data['legacy_qualification']['transactions'])
                self.assertTrue(all(row['owner']=='Self' for row in data['legacy_qualification']['transactions']))
                retained=materialize_paper_observation(data['raw'],data['observation'],data['metadata'],data['identity'],
                    amount_header=data['amount_header'],field_reread=data['field_reread'],
                    prior_extraction=data['legacy_extraction'],prior_qualification=data['legacy_qualification'])
                self.assertEqual(retained,data['legacy_qualification'])
                self.assertNotIn(PROFILE_KEY,retained)


if __name__=='__main__':
    unittest.main()
