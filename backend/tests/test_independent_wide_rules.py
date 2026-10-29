"""Bounded wide printing is witnessed outside each target, never fitted to ink."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from unison_snapshot.ruled_cell_blank import inspect_ruled_cell_blank
from unison_snapshot.senate_paper_marks import INDEPENDENT_BOUNDARY_RULE, interpret_complete_mark_row
from unison_snapshot.senate_slot_inventory import FORM_BOUNDS, classify_slot
from unison_snapshot.senate_paper import parse_paper_grid_observations, _save_legacy_grid_input
from unison_snapshot.senate_paper_observation import _source_report_relations
from unison_snapshot.senate import SenateEfdError
from tests.test_senate_paper_grid import inputs
from tests.test_senate_paper import word, page


def scene():
    image=Image.new('L',(3400,4400),255);draw=ImageDraw.Draw(image)
    for x in FORM_BOUNDS:draw.line((x,50,x,700),fill=0,width=9 if x==FORM_BOUNDS[-1] else 1)
    for y in (200,350):draw.line((500,y,3100,y),fill=0)
    font=ImageFont.load_default(size=32)
    draw.text((1412,260),'X',font=font,fill=0);draw.text((2131,260),'X',font=font,fill=0)
    def crop(box):return dict(box_px=box,grayscale_sha256=hashlib.sha256(image.crop(tuple(box)).tobytes()).hexdigest())
    raw={'page_number':2,'grid_slot':1,'row_bounds_px':[200,350],
        'asset_ocr_raw':'(S) Acme','date_ocr_raw':'5/29/26','asset_ink_sample_count':200,
        'row_crop':crop([525,200,2920,350]),'asset_crop':crop([590,212,1385,338]),'date_crop':crop([1669,212,1923,338])}
    centers=([sum(x)//2 for x in zip(FORM_BOUNDS[:3],FORM_BOUNDS[1:4])],
        [sum(x)//2 for x in zip(FORM_BOUNDS[4:-1],FORM_BOUNDS[5:])],(FORM_BOUNDS[3],FORM_BOUNDS[4]))
    return image,classify_slot(raw,image,calibrated_centers=centers,calibration_required=True)


class WideIndependentRuleTests(unittest.TestCase):
    def observe(self,image,box=[100,300,220,450],h=[[250,350],[380,500]],v=[[100,200],[500,600]]):
        return inspect_ruled_cell_blank(image,box,horizontal_witness_spans=h,vertical_witness_spans=v,
                                       rule_profile='straight_wide/v2')

    def simple(self,width=9):
        image=Image.new('L',(650,750),255);d=ImageDraw.Draw(image)
        for x in (100,220):d.line((x,40,x,700),fill=0,width=width)
        for y in (300,450):d.line((25,y,600,y),fill=0,width=width)
        return image

    def test_wide_complete_footprint_is_explicit_and_source_not_mutated(self):
        image=self.simple();before=image.tobytes();out=self.observe(image)
        self.assertEqual(out['status'],'blank',out)
        self.assertEqual(out['black_pixel_threshold_exclusive'],160)
        self.assertFalse(out['target_ink_used_to_estimate_rules'])
        self.assertEqual(before,image.tobytes())
        old=inspect_ruled_cell_blank(image,[100,300,220,450],horizontal_witness_spans=[[250,350],[380,500]],
                                    vertical_witness_spans=[[100,200],[500,600]])
        self.assertEqual(old['status'],'unknown')

    def test_corners_attached_strokes_gray_ink_and_full_x_never_become_blank(self):
        clean=self.observe(self.simple())['independent_rule_witnesses']
        for kind in ('corner','gray','short','X'):
            image=self.simple();draw=ImageDraw.Draw(image)
            if kind=='corner':image.putpixel((105,305),0)
            elif kind=='gray':image.putpixel((105,305),150)
            elif kind=='short':draw.line((104,305,112,313),fill=0,width=2)
            else:
                draw.line((104,305,124,325),fill=0,width=2);draw.line((104,325,124,305),fill=0,width=2)
            with self.subTest(kind=kind):
                out=self.observe(image)
                self.assertEqual(out['status'],'unknown')
                self.assertGreater(out['unexplained_dark_pixel_count'],0)
                self.assertEqual(out['independent_rule_witnesses'],clean)

    def test_width_fraction_absolute_limit_ambiguity_and_incomplete_core_fail_closed(self):
        for kind in ('fraction','absolute','window','broken','ambiguous','missing_witness'):
            image=self.simple(width=17 if kind=='absolute' else 41 if kind=='window' else 9)
            d=ImageDraw.Draw(image);kwargs={}
            if kind=='fraction':
                kwargs['box']=[100,300,160,450]
                d.line((160,40,160,700),fill=0,width=9)
            elif kind=='broken':d.rectangle((145,296,155,304),fill=255)
            elif kind=='ambiguous':d.line((25,315,600,315),fill=0)
            elif kind=='missing_witness':kwargs['v']=[[100,200]]
            with self.subTest(kind=kind):
                out=self.observe(image,**kwargs)
                self.assertEqual(out['status'],'unknown')
                if kind in ('fraction','absolute'):
                    self.assertEqual(out['reason'],'source_rule_footprint_too_thick')

    def test_whole_row_requires_all_unmarked_cells_independently_and_no_conflict(self):
        image,slot=scene();before=image.tobytes();original=deepcopy(slot)
        self.assertIsNone(interpret_complete_mark_row(image,slot,resolve_boundary_traces=True))
        read=interpret_complete_mark_row(image,slot,resolve_boundary_traces=True,independent_blank_boundaries=True)
        self.assertIsNotNone(read)
        self.assertEqual(read['direction_labels'],['purchase'])
        self.assertEqual(read['amount_labels'],['50001_100000'])
        self.assertEqual(slot,original);self.assertEqual(before,image.tobytes())
        self.assertEqual(sum(c['status']=='independently_witnessed_blank' for c in read['cells']),12)
        for kind in ('corner','short','second_x','opposite'):
            altered=image.copy();s=deepcopy(slot);d=ImageDraw.Draw(altered)
            if kind=='corner':altered.putpixel((FORM_BOUNDS[2]+1,201),0)
            elif kind=='short':d.line((FORM_BOUNDS[2]+1,201,FORM_BOUNDS[2]+8,209),fill=0,width=2)
            elif kind=='second_x':d.text((1510,260),'X',font=ImageFont.load_default(size=32),fill=0)
            else:s['type_marks']=['sale']
            with self.subTest(kind=kind):
                self.assertIsNone(interpret_complete_mark_row(altered,s,resolve_boundary_traces=True,
                                                             independent_blank_boundaries=True))

    def args(self):
        args=inputs();image,slot=scene();stream=BytesIO();image.save(stream,format='GIF')
        raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
        args[1]['pages'][1]['sha256']=sha
        args[2]['slots']=[slot];args[2]['label_counts']={'unknown':1}
        args[2]['pages'][0].update(page_sha256=sha,label_counts={'unknown':1})
        args[2]['source_report_ledger']=_source_report_relations(args[2])
        args[3][0]={**page(2,[word('(S)',650,260),word('Acme',760,260),word('(Stock)',980,260),word('5/29/26',1730,260)]),
            'source_sha256':sha,'full_cell_mark_observations':[dict(rule_version=INDEPENDENT_BOUNDARY_RULE,
                page_number=2,grid_slot=1,page_sha256=sha)]}
        args[4][2]=raw
        return args

    def test_saved_source_replay_candidate_and_wrong_source_rejection(self):
        from tests.test_senate_candidate import SenateCandidateTests,identity,BASE,CATALOG_SHA,PARSER
        from unison_snapshot.senate_candidate import build_senate_candidate
        from unison_snapshot.builder import build
        args=self.args();extraction=parse_paper_grid_observations(*args)
        self.assertEqual(extraction['eligible_transaction_count'],1)
        self.assertEqual(extraction['whole_cell_mark_rule_version'],INDEPENDENT_BOUNDARY_RULE)
        without_request=deepcopy(args);without_request[3][0].pop('full_cell_mark_observations')
        self.assertEqual(parse_paper_grid_observations(*without_request)['transactions'],[])
        bad=deepcopy(args);bad[3][0]['full_cell_mark_observations'][0]['page_sha256']='0'*64
        with self.assertRaises(SenateEfdError):parse_paper_grid_observations(*bad)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);review=root/'review';evidence=root/'evidence'
            extraction['grid_replay_input']=_save_legacy_grid_input(review,args[0],args[2],args[3],None)
            state=SenateCandidateTests().fixture(review,[identity(extraction['document_id'])],[extraction])
            extraction_path=review/'senate_efd/extractions'/extraction['document_id']/extraction['source_sha256']
            (extraction_path/f'{PARSER}.json').rename(extraction_path/f"{extraction['parser_version']}.json")
            status_path=review/'status/senate_efd.json'
            status=json.loads(status_path.read_text());status['report_parser_version']=extraction['parser_version']
            status_path.write_text(json.dumps(status))
            manifest=deepcopy(args[1]);manifest['pages'][1]['archive_path']='source.gif'
            def write(path,value):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
            write(evidence/'senate_efd/paper_pages'/extraction['document_id']/'manifest.json',manifest)
            (evidence/'source.gif').write_bytes(args[4][2])
            write(review/f'senate_efd/discoveries/{CATALOG_SHA}.json',{'reports':[args[0]]})
            with patch('unison_snapshot.senate_paper.tesseract_words',side_effect=AssertionError('No OCR')):
                result,audit=build_senate_candidate(review,state,deepcopy(BASE),evidence_root=evidence)
            self.assertEqual(len(result['transactions']),1)
            self.assertEqual(result['transactions'][0]['transaction_type'],'purchase')
            self.assertEqual(result['transactions'][0]['id'],extraction['transactions'][0]['extraction_id'])
            self.assertTrue(build(result,generated_at='2026-09-20T12:00:00Z',allow_production=True).manifest['snapshot_id'])
            artifact=extraction_path/f"{extraction['parser_version']}.json"
            supplied=json.loads(artifact.read_text())
            cells=supplied['transactions'][0]['field_observations']['whole_cell_mark_read']['cells']
            proof=next(c['independent_blank_observation'] for c in cells if 'independent_blank_observation' in c)
            proof['removed_rule_pixel_count']+=1
            artifact.write_text(json.dumps(supplied))
            with self.assertRaises(SenateEfdError):
                build_senate_candidate(review,state,deepcopy(BASE),evidence_root=evidence)
            artifact.write_text(json.dumps(extraction))
            (evidence/'source.gif').write_bytes(b'wrong source')
            with self.assertRaises((SenateEfdError,ValueError)):
                build_senate_candidate(review,state,deepcopy(BASE),evidence_root=evidence)
