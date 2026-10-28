from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from unison_snapshot.codec import digest, encode
from unison_snapshot.corrections import load_correction
from unison_snapshot.reviewed_senate_paper import qualify_reviewed_senate_paper, reviewed_record
from unison_snapshot.senate_candidate import _transaction
from unison_snapshot.senate_paper import PAPER_EXTRACTION_SCHEMA, PAPER_OCR_RULE_VERSION
from unison_snapshot.senate_reports import PARSER_VERSION


class ReviewedSenatePaperTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.document='12345678-1234-1234-1234-123456789abc'
        self.sha='a'*64
        image=Image.new('L',(20,20),255);buffer=BytesIO();image.save(buffer,format='PNG')
        raw=buffer.getvalue();(self.root/'page.png').write_bytes(raw)
        page_sha=digest(raw);crop_sha=digest(image.crop((0,0,10,10)).tobytes())
        self.manifest={'document_id':self.document,'entrypoint_source_sha256':self.sha,
            'evidence_complete':True,'page_count':1,'manifest_sha256':'b'*64,
            'pages':[{'page_number':1,'archive_path':'page.png','sha256':page_sha}]}
        slot={'document_id':self.document,'entrypoint_source_sha256':self.sha,
            'page_number':1,'page_sha256':page_sha,'grid_slot':1,'row_crop_sha256':crop_sha}
        pending={'source_slot':slot,'legacy_extraction_failed':True,'legacy_extraction_report_row_count':0,
            'asset_ocr_raw':'Example'}
        self.pending={'schema_version':'senate-paper-pending-slot-projection/v3','pending_rows':[pending]}
        self.batch={'parser_version':PARSER_VERSION,'report_count':2,'extraction_count':0,'failure_count':2,
            'transaction_count':0,'eligible_transaction_count':0,'extractions':[],
            'failures':[{'document_id':self.document,'source_sha256':self.sha},{'document_id':'other','source_sha256':'c'*64}]}
        row={'extraction_id':'senate-ptr:'+hashlib.sha256('|'.join((self.sha,'1',page_sha,'1',crop_sha)).encode()).hexdigest()[:24],
            'row_number':1,'page_number':1,'source_slot':slot,'qualification_status':'eligible','quarantine_reasons':[]}
        report={'schema_version':PAPER_EXTRACTION_SCHEMA,'paper_rule_version':PAPER_OCR_RULE_VERSION,
            'parser_version':PARSER_VERSION,'document_id':self.document,'source_sha256':self.sha,
            'source_url':f'https://efdsearch.senate.gov/search/view/paper/{self.document}/',
            'eligible_transaction_count':1,'quarantined_transaction_count':0}
        self.correction={'profile':'reviewed-senate-paper/v1','source':'senate_efd','document_id':self.document,
            'source_sha256':self.sha,'source_review':{'review':'synthetic source-bound test'},
            'assertions':{'source_review_sha256':digest(encode({'review':'synthetic source-bound test'})),
                'manifest_sha256':digest(encode(self.manifest)), 'report':report,
                'page_crops':[{'page':1,'box_px':[0,0,10,10],'grayscale_sha256':crop_sha}],
                'pixel_limits':[{'page':1,'box_px':[0,0,1,1],'dark_below':160,'max_dark_pixels':0}],
                'row_inventory':['p1#1'],'pending_row_sha256':{'p1#1':digest(encode(pending))}},
            'actions':[{'op':'set_field','field':'qualified_record','row':'p1#1','expect':None,'value':row,
                'evidence':{'source_slot':slot,'pending_sha256':digest(encode(pending))}}]}

    def qualify(self,batch=None,correction=None,pending=None):
        return qualify_reviewed_senate_paper(batch or self.batch,pending or self.pending,
            self.manifest,self.root,correction or self.correction)

    def test_source_bound_row_enters_once_and_other_reports_remain_failed(self):
        before=deepcopy(self.batch)
        result=self.qualify()
        self.assertEqual((result['transaction_count'],result['failure_count']),(1,1))
        self.assertEqual(result['failures'][0]['document_id'],'other')
        self.assertEqual(self.qualify(batch=result),result)
        self.assertEqual(self.batch,before)

    def test_source_pending_old_value_and_duplicate_action_fail_atomically(self):
        for change in (lambda c:c.update(source_sha256='d'*64),
                lambda c:c['actions'][0].update(expect='already qualified'),
                lambda c:c['actions'].append(deepcopy(c['actions'][0])),
                lambda c:c['actions'][0]['evidence'].update(pending_sha256='d'*64)):
            correction=deepcopy(self.correction);change(correction)
            with self.assertRaises(ValueError):self.qualify(correction=correction)
        pending=deepcopy(self.pending);pending['pending_rows'][0]['asset_ocr_raw']='changed'
        with self.assertRaisesRegex(ValueError,'precondition'):self.qualify(pending=pending)
        self.assertEqual(self.batch['extractions'],[])

    def test_original_pixels_manifest_paths_and_existing_flags_never_replace_source_replay(self):
        result=self.qualify();result['extractions'][0]['transactions'][0]['row_number']=9
        with self.assertRaises(ValueError):self.qualify(batch=result)
        (self.root/'page.png').write_bytes(b'changed original')
        with self.assertRaisesRegex(ValueError,'page hash'):self.qualify()
        self.manifest['pages'][0]['archive_path']='../outside.png'
        self.correction['assertions']['manifest_sha256']=digest(encode(self.manifest))
        with self.assertRaisesRegex(ValueError,'canonical'):self.qualify()

    def test_shipped_reviews_allow_exact_records_and_refuse_unreviewed_variants(self):
        directory=Path(__file__).resolve().parents[1]/'data/corrections/senate_efd'
        count=expected_count=0
        for path in directory.glob('*.json'):
            correction=load_correction(path);template=correction['assertions']['report']
            expected_count+=template['eligible_transaction_count']
            for action in correction['actions']:
                row=action['value'];identity={'person_id':correction['assertions']['person_id']}
                self.assertTrue(reviewed_record(row,template,identity['person_id']))
                value,reasons=_transaction(row,template,identity)
                self.assertEqual(reasons,[])
                self.assertIsNone(value['instrument_type'])
                changed=deepcopy(row);changed['extraction_id']='senate-ptr:'+'0'*24
                self.assertFalse(reviewed_record(changed,template,identity['person_id']))
                changed=deepcopy(row);changed['asset_name_raw']='invented'
                self.assertFalse(reviewed_record(changed,template,identity['person_id']))
                count+=1
        self.assertGreater(count,0)
        self.assertEqual(count,expected_count)
