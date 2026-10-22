"""Source layout/whitespace contracts; synthetic images are not official gold."""
from copy import deepcopy
import hashlib
import unittest
import tempfile
from pathlib import Path
try:
    from PIL import Image,ImageDraw,ImageFont
except ImportError:
    Image=ImageDraw=ImageFont=None
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper_accounts import RULE,interpret_account_spacing,unpunctuated_heading
from unison_snapshot.senate_slots_shadow import FORM_BOUNDS,TYPE_CELLS,AMOUNT_CELLS,classify_slot


def spacing():
    image=Image.new('RGB',(360,80),'white');draw=ImageDraw.Draw(image);font=ImageFont.load_default(size=28)
    fields=['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
    for i,(text,x) in enumerate([('(S)',12),('Acme',95),('LLC',205)]):
        draw.text((x,12),text,font=font,fill='black');bbox=draw.textbbox((x,12),text,font=font)
        # Coordinates in the original token's RGB crop plus eight-pixel border.
        left,top,right,bottom=bbox
        fields.append(f'5\t1\t1\t1\t1\t{i+1}\t{left-2}\t{top-2}\t{right-left}\t{bottom-top}\t97\t{text}')
    word={'text':'(S)AcmeLLC','left':10.,'top':10.,'width':280.,'height':45.,'confidence':97.}
    crop=image.crop((10,10,290,55));pad=Image.new('RGB',(296,61),'white');pad.paste(crop,(8,8));raw='\n'.join(fields)
    record={'schema_version':RULE,'page_sha256':'a'*64,'page_number':2,'grid_slot':1,'original_word':word,
        'source_box_px':[10,10,290,55],'input_pixels_sha256':hashlib.sha256(pad.tobytes()).hexdigest(),
        'raw_tsv':raw,'raw_tsv_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    return image,record,[word]


def read(image,record,words):
    return interpret_account_spacing(image,record,page_sha256='a'*64,page_number=2,grid_slot=1,words=words)


def heading():
    image=Image.new('L',(3400,4400),255);draw=ImageDraw.Draw(image)
    for x in FORM_BOUNDS:draw.line((x,200,x,350),fill=0,width=1)
    # Geometry uses its discovered exact source rules, not approximate seed bbox.
    date=[FORM_BOUNDS[3]+10,212,FORM_BOUNDS[4]-10,338]
    sha=lambda box:hashlib.sha256(image.crop(tuple(box)).tobytes()).hexdigest()
    source={'page_number':2,'grid_slot':1,'row_bounds_px':[200,350],
            'asset_ocr_raw':'(S) Parent LLC','date_ocr_raw':'','asset_ink_sample_count':200,
            'row_crop':{'box_px':[525,200,2920,350],'grayscale_sha256':sha([525,200,2920,350])},'asset_crop':{'box_px':[590,212,1385,338],'grayscale_sha256':sha([590,212,1385,338])},'date_crop':{'box_px':date,'grayscale_sha256':sha(date)}}
    centers=([sum(pair)//2 for pair in zip(FORM_BOUNDS[:3],FORM_BOUNDS[1:4])],
             [sum(pair)//2 for pair in zip(FORM_BOUNDS[4:-1],FORM_BOUNDS[5:])],(FORM_BOUNDS[3],FORM_BOUNDS[4]))
    slot=classify_slot(source,image,calibrated_centers=centers,calibration_required=True)
    nxt={'page_number':2,'grid_slot':2,'label':'data_observed','row_bounds_px':[350,500]}
    words=[{'text':'(S) Parent LLC','left':620,'top':240,'width':250,'height':30,'confidence':96},
           {'text':'(S) Child LLC','left':695,'top':390,'width':250,'height':30,'confidence':96}]
    return image,slot,nxt,words


@unittest.skipIf(Image is None,'optional Senate image dependency unavailable')
class SenatePaperAccountsTests(unittest.TestCase):
    def test_spacing_preserves_every_nonspace_character_and_real_gaps(self):
        image,record,words=spacing();r=read(image,record,words)
        self.assertEqual(r['text'],'(S) Acme LLC');self.assertEqual(r['original_word'],words[0])
        self.assertTrue(all(x>=7 for x in r['white_corridor_widths_px']))

    def test_spacing_does_not_correct_characters_or_accept_low_confidence(self):
        for old,new in [('Acme','Acne'),('97','89')]:
            image,record,words=spacing();record['raw_tsv']=record['raw_tsv'].replace(old,new)
            record['raw_tsv_sha256']=hashlib.sha256(record['raw_tsv'].encode()).hexdigest()
            self.assertIsNone(read(image,record,words))

    def test_spacing_source_token_crop_raw_and_pixels_are_bound(self):
        image,record,words=spacing()
        for key,value in [('page_sha256','b'*64),('grid_slot',2),('source_box_px',[9,10,290,55]),
                          ('raw_tsv_sha256','0'*64),('input_pixels_sha256','0'*64)]:
            altered=deepcopy(record);altered[key]=value
            with self.subTest(key=key),self.assertRaises(SenateEfdError):read(image,altered,words)
        with self.assertRaises(SenateEfdError):read(image,record,words*2)
        image.putpixel((30,30),(255,0,0))
        with self.assertRaises(SenateEfdError):read(image,record,words)

    def test_no_space_without_source_white_corridor_even_when_ocr_requests_it(self):
        image,record,words=spacing()
        for x in range(46,95):image.putpixel((x,12),(0,0,0))
        pad=Image.new('RGB',(296,61),'white');pad.paste(image.crop((10,10,290,55)),(8,8))
        record['input_pixels_sha256']=hashlib.sha256(pad.tobytes()).hexdigest()
        self.assertIsNone(read(image,record,words))

    def test_source_role_without_colon_and_same_page_child_indentation(self):
        image,slot,nxt,words=heading();r=unpunctuated_heading(image,slot,nxt,words,text='(S) Parent LLC',confidence=96)
        self.assertEqual(r['role'],'heading');self.assertEqual(slot['label'],'unknown')

    def test_requested_top_rule_role_keeps_isolated_marks_and_body_content_unknown(self):
        def source(extra=None):
            image,slot,nxt,words=heading();draw=ImageDraw.Draw(image)
            draw.line((FORM_BOUNDS[0],218,FORM_BOUNDS[-1],218),fill=0,width=2)
            if extra=='isolated':draw.point((1700,229),fill=0)
            if extra=='body':draw.text((1700,245),'7/13/26',fill=0,font=ImageFont.load_default(size=20))
            # Rebuild source crop hashes and source reads; this is an explicit
            # synthetic counterfactual, not edited official evidence.
            for field in ['date','direction','amount']:
                reads=[slot['key_cells'][field]] if field=='date' else slot['key_cells'][field]
                for cell in reads:cell['grayscale_sha256']=hashlib.sha256(image.crop(tuple(cell['box_px'])).tobytes()).hexdigest()
            return image,slot,nxt,words
        image,slot,nxt,words=source()
        self.assertIsNone(unpunctuated_heading(image,slot,nxt,words,text='(S) Parent LLC',confidence=96))
        self.assertIsNotNone(unpunctuated_heading(image,slot,nxt,words,text='(S) Parent LLC',confidence=96,allow_top_boundary_ink=True))
        for extra in ['isolated','body']:
            image,slot,nxt,words=source(extra)
            self.assertIsNone(unpunctuated_heading(image,slot,nxt,words,text='(S) Parent LLC',confidence=96,allow_top_boundary_ink=True))

    def test_unknown_date_mark_residual_missing_child_or_no_indent_not_heading(self):
        for mode in ['date','mark','residual','no_child','no_indent','low_confidence','nonowner','missing_rule']:
            image,slot,nxt,words=heading();text='(S) Parent LLC';confidence=96
            if mode=='date':image.putpixel((1700,250),0)
            elif mode=='mark':slot['type_marks']=['sale']
            elif mode=='residual':image.putpixel((2100,250),0)
            elif mode=='no_child':nxt=None
            elif mode=='no_indent':words[1]['left']=630
            elif mode=='low_confidence':confidence=84
            elif mode=='nonowner':text='Parent LLC'
            elif mode=='missing_rule':ImageDraw.Draw(image).rectangle((FORM_BOUNDS[1],200,FORM_BOUNDS[1],350),fill=255)
            with self.subTest(mode=mode):self.assertIsNone(unpunctuated_heading(image,slot,nxt,words,text=text,confidence=confidence))

    def test_only_proven_account_token_changes_the_old_formal_name(self):
        from unison_snapshot.senate_candidate import _source_account_whitespace_correction
        proof={'rule_version':RULE,'text':'(S) Acme LLC','original_word':{'text':'(S)AcmeLLC'}}
        current={'account_heading_raw':'(S) Acme LLC:',
                 'account_heading_source_slot':{'spacing_interpretation':proof}}
        new={'id':'senate-ptr:fixed','asset_name':'Child (Account: (S) Acme LLC:)',
             'amount_low':1001,'transaction_date':'2026-07-09','person_id':'senate:A000001'}
        old={**new,'asset_name':'Child (Account: (S)AcmeLLC:)'}
        self.assertIsNotNone(_source_account_whitespace_correction(current,new,old))
        for field,value in [('amount_low',1002),('transaction_date','2026-07-08'),
                            ('asset_name','Ch ild (Account: (S)AcmeLLC:)'),('person_id','senate:A000002')]:
            altered={**old,field:value}
            with self.subTest(field=field):self.assertIsNone(_source_account_whitespace_correction(current,new,altered))
        current['account_heading_source_slot']={}
        self.assertIsNone(_source_account_whitespace_correction(current,new,old))

    def test_normal_candidate_requires_controlled_replay_for_new_account_proofs(self):
        from unison_snapshot.senate_paper import parse_paper_grid_observations
        from unison_snapshot.senate_candidate import build_senate_candidate
        from tests.test_senate_paper_grid import inputs
        from tests.test_senate_candidate import SenateCandidateTests,identity,BASE,PARSER
        for mode in ('version','role','spacing'):
            extraction=parse_paper_grid_observations(*inputs());extraction['parser_version']=PARSER
            if mode=='version':extraction['account_layout_rule_version']=RULE
            elif mode=='role':extraction['account_heading_role_interpretations']=[]
            else:extraction['transactions'][0]['account_heading_source_slot']={'spacing_interpretation':{'text':'fake'}}
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);state=SenateCandidateTests().fixture(root,[identity(extraction['document_id'])],[extraction])
                with self.subTest(mode=mode),self.assertRaisesRegex(SenateEfdError,'archived source replay'):
                    build_senate_candidate(root,state,deepcopy(BASE))

if __name__=='__main__':unittest.main()
