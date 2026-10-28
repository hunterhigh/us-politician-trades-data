"""Complete source X contracts, separate from fixed-original accuracy."""
from copy import deepcopy
from io import BytesIO
import hashlib
import unittest
import tempfile
from pathlib import Path
try:
    from PIL import Image,ImageDraw,ImageFont
except ImportError:
    Image=ImageDraw=ImageFont=None
from unison_snapshot.senate_paper_marks import complete_x,interpret_complete_mark_row,RULE
from unison_snapshot.senate_slot_inventory import FORM_BOUNDS,TYPE_CELLS,AMOUNT_CELLS,classify_slot
from unison_snapshot.senate_paper import parse_paper_grid_observations
from unison_snapshot.senate_paper_observation import _source_report_relations
from unison_snapshot.senate import SenateEfdError
from tests.test_senate_paper_grid import inputs
from tests.test_senate_paper import word,page


def fixture():
    image=Image.new('L',(3400,4400),255);draw=ImageDraw.Draw(image)
    for x in FORM_BOUNDS:draw.line((x,200,x,350),fill=0,width=1)
    for y in [200,350]:draw.line((1300,y,2950,y),fill=0,width=3)
    font=ImageFont.load_default(size=32)
    draw.text((1412,260),'X',font=font,fill=0)
    draw.text((2131,260),'X',font=font,fill=0)
    def crop(box):return {'box_px':box,'grayscale_sha256':hashlib.sha256(image.crop(tuple(box)).tobytes()).hexdigest()}
    source={'page_number':2,'grid_slot':1,'row_bounds_px':[200,350],
        'asset_ocr_raw':'(S) Acme','date_ocr_raw':'5/29/26','asset_ink_sample_count':200,
        'row_crop':crop([525,200,2920,350]),'asset_crop':crop([590,212,1385,338]),'date_crop':crop([1669,212,1923,338])}
    centers=([sum(x)//2 for x in zip(FORM_BOUNDS[:3],FORM_BOUNDS[1:4])],
             [sum(x)//2 for x in zip(FORM_BOUNDS[4:-1],FORM_BOUNDS[5:])],(FORM_BOUNDS[3],FORM_BOUNDS[4]))
    slot=classify_slot(source,image,calibrated_centers=centers,calibration_required=True)
    return image,slot


@unittest.skipIf(Image is None,'optional image dependency unavailable')
class SenateCompleteMarksTests(unittest.TestCase):
    def test_closed_source_face_keeps_all_islands_and_rejects_attached_ink_or_open_rule(self):
        from unison_snapshot.senate_paper_cell_faces import enclosed_cell_ink
        image=Image.new('L',(160,180),255);draw=ImageDraw.Draw(image)
        draw.rectangle((45,45,115,135),outline=0,width=2)
        draw.line((65,70,85,90),fill=0,width=2);draw.line((65,90,85,70),fill=0,width=2)
        image.putpixel((100,110),0)
        ink,proof=enclosed_cell_ink(image,45,115,45,135)
        self.assertEqual(sum(v<160 for v in ink.getdata()),sum(image.getpixel((x,y))<160 for y in range(47,134) for x in range(47,114)))
        self.assertTrue(proof['all_enclosed_dark_pixels_retained'])
        self.assertIsNone(complete_x(ink,allow_fragments=True))
        attached=image.copy();ImageDraw.Draw(attached).line((46,80,66,80),fill=0,width=2)
        self.assertIsNone(enclosed_cell_ink(attached,45,115,45,135))
        opened=image.copy();ImageDraw.Draw(opened).line((45,100,46,100),fill=255)
        self.assertIsNone(enclosed_cell_ink(opened,45,115,45,135))

    def test_fragment_mode_preserves_four_arm_geometry_and_never_joins_pixels(self):
        image=Image.new('L',(80,80),255);draw=ImageDraw.Draw(image)
        draw.line((20,20,40,40),fill=0,width=2);draw.line((20,40,40,20),fill=0,width=2)
        draw.line((19,22,42,22),fill=255)
        self.assertIsNone(complete_x(image))
        read=complete_x(image,allow_fragments=True)
        self.assertEqual(read['status'],'fragmented_x')
        self.assertEqual(read['dark_pixel_count'],sum(v<160 for v in image.getdata()))
        for text in ['O','V','H','I','+','-','/','8']:
            other=Image.new('L',(80,80),255);ImageDraw.Draw(other).text((20,20),text,font=ImageFont.load_default(size=32),fill=0)
            self.assertIsNone(complete_x(other,allow_fragments=True))

    def test_only_small_components_connected_to_proved_frame_core_are_reclassified(self):
        from unison_snapshot.senate_paper_marks import _source_rule_trace_result
        image=Image.new('L',(100,100),255);draw=ImageDraw.Draw(image)
        draw.rectangle((8,8,91,91),outline=0,width=2)
        draw.line((30,30,50,50),fill=0,width=2);draw.line((30,50,50,30),fill=0,width=2)
        draw.line((10,25,10,28),fill=0,width=1)
        box=[10,10,90,90];bands={'left':[8,9],'right':[90,91],'top':[8,9],'bottom':[90,91]}
        self.assertIsNone(complete_x(image.crop(box)))
        read=_source_rule_trace_result(image,box,bands)
        self.assertEqual(read['status'],'complete_x');self.assertTrue(read['source_rule_trace_components'])
        dot=image.copy();dot.putpixel((20,20),0)
        self.assertIsNone(_source_rule_trace_result(dot,box,bands))
        attached=image.copy();d=ImageDraw.Draw(attached);d.line((10,45,30,65),fill=0,width=2);d.line((10,65,30,45),fill=0,width=2)
        self.assertIsNone(_source_rule_trace_result(attached,box,bands))
        inward=image.copy();ImageDraw.Draw(inward).line((10,26,18,26),fill=0)
        self.assertIsNone(_source_rule_trace_result(inward,box,bands))

    def test_off_center_x_recovered_without_changing_center_samples(self):
        image,slot=fixture();old=deepcopy(slot);read=interpret_complete_mark_row(image,slot)
        self.assertEqual(slot,old);self.assertEqual(slot['label'],'unknown')
        self.assertEqual(slot['type_marks'],['purchase']);self.assertEqual(slot['amount_marks'],[])
        self.assertEqual(read['amount_labels'],['50001_100000'])
        self.assertEqual(read['direction_labels'],['purchase'])
        self.assertEqual(sum(x['status']=='complete_x' for x in read['cells']),2)
        self.assertEqual(sum(x['status']=='no_dark_ink' for x in read['cells']),12)

    def test_extra_dot_second_mark_missing_border_and_opposite_old_read_stay_unknown(self):
        for mode in ['dot','second_x','missing_border','opposite']:
            image,slot=fixture();draw=ImageDraw.Draw(image)
            if mode=='dot':image.putpixel((1500,240),0)
            elif mode=='second_x':draw.text((1510,260),'X',font=ImageFont.load_default(size=32),fill=0)
            elif mode=='missing_border':draw.rectangle((2024,210,2028,340),fill=255)
            else:slot['type_marks']=['sale']
            with self.subTest(mode=mode):self.assertIsNone(interpret_complete_mark_row(image,slot))

    def test_non_x_noise_clipping_and_fragments_are_not_marks(self):
        for text in ['O','V','H','I','+','-','/','8']:
            image=Image.new('L',(80,80),255);ImageDraw.Draw(image).text((20,20),text,font=ImageFont.load_default(size=32),fill=0)
            with self.subTest(text=text):self.assertIsNone(complete_x(image))
        image=Image.new('L',(80,80),255);draw=ImageDraw.Draw(image);draw.line((20,20,40,40),fill=0,width=2);draw.line((20,40,40,20),fill=0,width=2)
        self.assertEqual(complete_x(image)['status'],'complete_x')
        self.assertIsNone(complete_x(image.crop((20,20,41,41))))
        image.putpixel((60,60),0);self.assertIsNone(complete_x(image))

    def test_normal_parser_preserves_source_id_raw_reads_and_ledger_conservation(self):
        args=inputs();image,slot=fixture();stream=BytesIO();image.save(stream,format='GIF');raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
        args[1]['pages'][1]['sha256']=sha;args[2]['slots']=[slot];args[2]['label_counts']={'unknown':1};args[2]['pages'][0].update(page_sha256=sha,label_counts={'unknown':1});args[2]['source_report_ledger']=_source_report_relations(args[2])
        args[3][0]= {**page(2,[word('(S)',650,260),word('Acme',760,260),word('5/29/26',1730,260)]),'source_sha256':sha};args[4][2]=raw
        old_default=parse_paper_grid_observations(*args)
        self.assertEqual(old_default['transactions'],[])
        self.assertNotIn('whole_cell_mark_rule_version',old_default)
        args[3][0]['full_cell_mark_observations']=[{'rule_version':RULE,'page_number':2,'grid_slot':1,'page_sha256':sha,'amount_labels':['over_50000000']}]
        result=parse_paper_grid_observations(*args);self.assertEqual(result['whole_cell_mark_rule_version'],RULE)
        self.assertEqual(result['source_report_ledger']['observed_physical_slot_count'],1)
        self.assertEqual(result['source_report_ledger']['disposition_counts'],{'data_candidate':1})
        self.assertEqual(result['eligible_transaction_count'],1)
        row=result['transactions'][0];self.assertEqual(row['transaction_date'],'2026-05-29');self.assertEqual(row['amount_raw'],'$50,001 - $100,000')
        self.assertEqual(row['source_slot']['grid_slot'],1)
        self.assertEqual(row['field_observations']['whole_cell_mark_read']['original_amount_marks'],[])

    def test_enclosed_rule_is_explicit_and_does_not_invent_owner_or_override_direction(self):
        from unison_snapshot.senate_paper_marks import ENCLOSED_RULE,interpret_enclosed_amount_row
        args=inputs();image,slot=fixture();stream=BytesIO();image.save(stream,format='GIF');raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
        args[1]['pages'][1]['sha256']=sha;args[2]['slots']=[slot];args[2]['label_counts']={'unknown':1};args[2]['pages'][0].update(page_sha256=sha,label_counts={'unknown':1});args[2]['source_report_ledger']=_source_report_relations(args[2])
        args[3][0]={**page(2,[word('[5)',650,260),word('Acme',760,260),word('5/29/26',1730,260)]),'source_sha256':sha};args[4][2]=raw
        self.assertEqual(parse_paper_grid_observations(*args)['transactions'],[])
        request={'rule_version':ENCLOSED_RULE,'page_number':2,'grid_slot':1,'page_sha256':sha}
        args[3][0]['full_cell_mark_observations']=[request]
        result=parse_paper_grid_observations(*args);row=result['transactions'][0]
        self.assertEqual(result['whole_cell_mark_rule_version'],ENCLOSED_RULE)
        self.assertEqual(row['amount_raw'],'$50,001 - $100,000')
        self.assertEqual(row['quarantine_reasons'],['paper_owner_ocr_ambiguous'])
        self.assertIsNone(row['owner_raw'])
        self.assertEqual(row['field_observations']['asset_raw'],'[5) Acme')
        self.assertEqual(row['field_observations']['whole_cell_mark_read']['cells'][0]['status'],'original_unique_center_mark_retained')
        bad=deepcopy(slot);bad['type_marks']=['sale']
        self.assertIsNone(interpret_enclosed_amount_row(image,bad))
        request['page_sha256']='0'*64
        with self.assertRaises(SenateEfdError):parse_paper_grid_observations(*args)

    def test_exact_owner_code_bracket_punctuation_does_not_change_code_or_raw_text(self):
        from unison_snapshot.senate_paper import _paper_row
        for prefix,expected in [('[S)','Spouse'),('[J)','Joint'),('[DC)','Child'),('[5)',None),('[$)',None),('[Z)',None),('[S',None)]:
            image,slot=fixture();slot['amount_marks']=['50001_100000']
            row=_paper_row('a'*64,2,1,[word(prefix,650,260),word('Acme',760,260),word('5/29/26',1730,260)],275,grid_slot=slot)
            with self.subTest(prefix=prefix):
                self.assertEqual(row['owner_raw'],expected)
                if expected:
                    self.assertEqual(row['owner_prefix_interpretation']['raw_prefix'],prefix)
                    self.assertEqual(row['asset_name_raw'],'Acme')
                else:self.assertIn('paper_owner_ocr_ambiguous',row['quarantine_reasons'])

    def test_short_stroke_or_dot_at_rule_inner_face_is_not_erased(self):
        for mode in ('dot','short_stroke'):
            image,slot=fixture()
            if mode=='dot':image.putpixel((1386,240),0)
            else:ImageDraw.Draw(image).line((1386,225,1386,255),fill=0)
            with self.subTest(mode=mode):self.assertIsNone(interpret_complete_mark_row(image,slot))

    def test_long_dithered_source_rule_flank_is_not_a_second_mark(self):
        image,slot=fixture()
        for y in range(203,348,2):image.putpixel((1386,y),0)
        read=interpret_complete_mark_row(image,slot)
        self.assertEqual(read['direction_labels'],['purchase'])
        cell=read['cells'][0]
        self.assertEqual(cell['boundary_bands_px']['left'],[1385,1386])

    def test_normal_candidate_requires_replay_even_when_top_version_is_removed(self):
        from unison_snapshot.senate_candidate import build_senate_candidate
        from unison_snapshot.senate import SenateEfdError
        from tests.test_senate_candidate import SenateCandidateTests,identity,BASE,PARSER
        for mode in ('version','field_proof'):
            extraction=parse_paper_grid_observations(*inputs());extraction['parser_version']=PARSER
            if mode=='version':extraction['whole_cell_mark_rule_version']=RULE
            else:extraction['transactions'][0]['field_observations']['whole_cell_mark_read']={'rule_version':RULE}
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);state=SenateCandidateTests().fixture(root,[identity(extraction['document_id'])],[extraction])
                with self.subTest(mode=mode),self.assertRaisesRegex(SenateEfdError,'archived source replay'):
                    build_senate_candidate(root,state,deepcopy(BASE))

if __name__=='__main__':unittest.main()
