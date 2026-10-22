from copy import deepcopy
from unittest.mock import patch
import unittest
from unison_snapshot.house_ptr_owner_reading import verified_owner_readings, MODE


class OwnerReadingTests(unittest.TestCase):
    def test_original_cell_components_blank_extra_ink_and_broken_frame(self):
        from PIL import Image, ImageDraw
        from unison_snapshot.house_ptr_owner_reading import _geometry
        image=Image.new('L',(600,400),255);draw=ImageDraw.Draw(image)
        for y in [50,120,190,260]:draw.line((20,y,550,y),fill=0,width=3)
        for x in [20,200,280,400,550]:draw.line((x,50,x,260),fill=0,width=3)
        page={'rows':[]}
        for band,y in enumerate([50,120,190],1):
            cells={name:{'bbox_pixels':[left,y,right,y+70]} for name,left,right in
                   [('asset',20,200),('owner',200,280),('event_date',280,400),('notification_date',400,550)]}
            page['rows'].append({'physical_band_index':band,'cells':cells})
            if band<3:
                draw.line((226,y+20,226,y+40,220,y+40,218,y+36),fill=0,width=3)
                draw.line((238,y+20,250,y+20),fill=0,width=3);draw.line((244,y+20,244,y+40),fill=0,width=3)
        self.assertIsNotNone(_geometry(image,page,page['rows'][0]))
        self.assertIsNotNone(_geometry(image,page,page['rows'][1]))
        self.assertIsNone(_geometry(image,page,page['rows'][2]))
        dot=image.copy();ImageDraw.Draw(dot).rectangle((260,90,262,92),fill=0)
        self.assertIsNone(_geometry(dot,page,page['rows'][0]))
        broken=image.copy();ImageDraw.Draw(broken).rectangle((198,78,202,86),fill=255)
        self.assertIsNone(_geometry(broken,page,page['rows'][0]))
        with patch('PIL.ImageDraw.floodfill',return_value=None):
            self.assertIsNone(_geometry(image,page,page['rows'][0]))

    def fixture(self):
        proof={"reading_mode":MODE,"first_ocr_raw":"JT|","primary_ocr_engine":"tesseract 5.3.4",
               "source_sha256":"a"*64,"component_crop_sha256":"b"*64}
        saved={**proof,"page":2,"physical_band_index":2,"field":"owner",
               "second_ocr_engine":"rapidocr-onnxruntime","second_engine_version":"1.4.4",
               "second_ocr_raw":"JT","second_recognition_score":.997}
        return proof,{"rereads":[saved]}

    def test_independent_exact_code_and_source_proof_required(self):
        proof,read=self.fixture()
        with patch('unison_snapshot.house_ptr_owner_reading._inputs',return_value=[((2,2),proof,None)]):
            self.assertEqual(verified_owner_readings(b'',{},read)[(2,2)]['owner_code'],'JT')
            for key,value in [('second_ocr_raw','SP'),('second_ocr_raw',''),('second_recognition_score',.89),
                              ('component_crop_sha256','0'*64),('primary_ocr_engine','rapidocr-onnxruntime')]:
                bad=deepcopy(read);bad['rereads'][0][key]=value
                with self.subTest(key=key,value=value):self.assertEqual(verified_owner_readings(b'',{},bad),{})
        with self.assertRaises(ValueError):verified_owner_readings(b'',{}, {'rereads':read['rereads']*2})
        self.assertEqual(verified_owner_readings(b'',{},None),{})

    def test_normal_consumer_only_resolves_owner_uncertainty(self):
        import test_house_ptr_paper_qualification as fixtures
        from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation
        f=fixtures.HousePaperQualificationTests();f.setUp();raw,header,read,slots=f._source_bound_fixture()
        f.observation['pages'][0]['rows'][0]['cells']['owner']['raw_ocr_confidence']=65
        proof={'owner_code':'SP','decision':'independent_owner_reading_matches_source_components'}
        def run(ownerproof):
            with patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells',return_value=slots), \
                 patch('unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound',return_value=True), \
                 patch('unison_snapshot.house_ptr_owner_reading.verified_owner_readings',return_value=ownerproof):
                return materialize_paper_observation(raw,f.observation,f.metadata,f.identity,amount_header=header,field_reread=read)
        before=run({});after=run({(1,1):proof})
        self.assertEqual(len(before['transactions']),0)
        self.assertEqual(len(after['transactions']),1)
        self.assertEqual(len(after['quarantined']),1)  # unrelated unknown source slot
        self.assertEqual(after['transactions'][0]['owner'],'Spouse')
        # An uncertain event date remains blocking even with a proved owner.
        f.observation['pages'][0]['rows'][0]['cells']['event_date']['raw_ocr_confidence']=65
        blocked=run({(1,1):proof})
        self.assertEqual(len(blocked['transactions']),0)
