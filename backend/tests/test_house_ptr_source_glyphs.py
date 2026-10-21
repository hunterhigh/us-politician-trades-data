"""Same-source glyph contracts; synthetic letters are not real-source gold."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.house_ptr_source_glyphs import _words, _decide, corroborate_asset_readings


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("pdfplumber"),"raster optional")
class SourceGlyphTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from PIL import Image
        self.C=np.array([[c=="1" for c in row] for row in [
            "0111110","1100011","1100000","1100000","1100000","1100000","1100000","1100011","0111110"]])
        self.G=np.array([[c=="1" for c in row] for row in [
            "0111100","1100010","1100000","1100000","1101111","1100011","1100011","1100011","0111110"]])
        self.A=np.array([[c=="1" for c in row] for row in [
            "0011100","0110110","1100011","1100011","1111111","1100011","1100011","1100011","1100011"]])
        self.L=np.zeros((9,7),bool);self.L[:,0:2]=True;self.L[-2:,:]=True
        self.exemplars={"C":[{"mask":self.C,"locator":{"n":i}} for i in range(2)],
                        "G":[{"mask":self.G,"locator":{"n":2}}]}

    def test_original_C_and_G_and_added_bar_negative(self):
        proof=_decide(self.C,"C","G",self.exemplars)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["discriminator_mismatch_count"],0)
        self.assertIsNone(_decide(self.G,"C","G",self.exemplars))
        bar=self.C.copy();bar[4,3:]=True
        self.assertIsNone(_decide(bar,"C","G",self.exemplars))
        # With independent G exemplars the actual G, rather than an asset name,
        # determines a G decision too.
        refs=deepcopy(self.exemplars);refs["G"].append({"mask":self.G,"locator":{"n":3}})
        self.assertIsNotNone(_decide(self.G,"G","C",refs))

    def test_missing_reference_or_scale_or_single_discriminator_pixel_rejects(self):
        import numpy as np
        self.assertIsNone(_decide(self.C,"C","G",{"C":self.exemplars["C"][:1],"G":self.exemplars["G"]}))
        refs=deepcopy(self.exemplars);refs["G"][0]["mask"]=np.pad(self.G,((0,1),(0,0)))
        self.assertIsNone(_decide(self.C,"C","G",refs))
        changed=self.C.copy();changed[4,5]=True
        self.assertIsNone(_decide(changed,"C","G",self.exemplars))

    def fixture(self, *, target=None):
        import numpy as np
        import pdfplumber
        from PIL import Image
        pixels=np.ones((400,600),dtype="uint8")*255
        boxes=[[20,80,220,130],[20,160,220,210]]
        strings=[("CC GG","CC GG"),("AA CL","AA GL")]
        for band,(box,(first,second)) in enumerate(zip(boxes,strings),1):
            x,y=box[0]+12,box[1]+15
            for ch in first:
                if ch==" ":x+=12;continue
                glyph=target if band==2 and ch=="C" and target is not None else getattr(self,ch)
                h,w=glyph.shape;region=pixels[y:y+h,x:x+w];region[glyph]=0;x+=w+3
        image=Image.fromarray(pixels).convert("1");stream=io.BytesIO();image.save(stream,format="PDF",resolution=200);raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
        observation={"render_dpi":200,"source_sha256":sha,"pages":[{"page":1,"rows":[]}]};reread={"rereads":[]}
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            original=pdf.pages[0].to_image(resolution=200).original
            high=pdf.pages[0].to_image(resolution=300).original.convert("L")
            for band,(box,(first,second)) in enumerate(zip(boxes,strings),1):
                crop=original.crop(tuple(box));s=io.BytesIO();crop.save(s,format="PNG");normal=[box[0]/600,box[1]/400,box[2]/600,box[3]/400]
                cell={"bbox_pixels":box,"bbox_normalized":normal,"raw_text":first,"crop_sha256":hashlib.sha256(s.getvalue()).hexdigest()}
                observation["pages"][0]["rows"].append({"physical_band_index":band,"cells":{"asset":cell}})
                highcrop=high.crop((round(normal[0]*high.width)+3,round(normal[1]*high.height)+3,round(normal[2]*high.width)-3,round(normal[3]*high.height)-3));s=io.BytesIO();highcrop.save(s,format="PNG")
                reread["rereads"].append({"page":1,"physical_band_index":band,"field":"asset","first_ocr_raw":first,"second_ocr_raw":second,"first_crop_sha256":cell["crop_sha256"],"second_crop_sha256":hashlib.sha256(s.getvalue()).hexdigest(),"second_crop_render_dpi":300})
        return raw,observation,reread

    def test_native_PDF_and_crops_recomputed_without_claimed_character_coordinates(self):
        raw,obs,read=self.fixture()
        proof=corroborate_asset_readings(raw,obs,read,{})
        self.assertEqual(set(proof),{(1,2)})
        self.assertEqual(proof[(1,2)]["first_raw"],"AA CL")
        self.assertEqual(proof[(1,2)]["second_raw"],"AA GL")
        # A source G and a source C with the distinguishing extra stroke cannot
        # be promoted merely because the first reading says C.
        for target in [self.G,self.C | self.G]:
            raw,obs,read=self.fixture(target=target)
            self.assertEqual(corroborate_asset_readings(raw,obs,read,{}),{})

    def test_hash_wrong_source_tampered_crop_and_claimed_proof_rejected(self):
        raw,obs,read=self.fixture()
        wrong=deepcopy(obs);wrong["source_sha256"]="0"*64
        with self.assertRaises(ValueError):corroborate_asset_readings(raw,wrong,read,{})
        changed=deepcopy(read);changed["rereads"][1]["second_crop_sha256"]="0"*64
        changed["source_glyph_decisions"]={"accepted":True}
        self.assertEqual(corroborate_asset_readings(raw,obs,changed,{}),{})
        changed=deepcopy(obs);changed["pages"][0]["rows"][1]["cells"]["asset"]["crop_sha256"]="0"*64
        # The first/second binding now disagrees, so no exemplar is consumed.
        self.assertEqual(corroborate_asset_readings(raw,changed,read,{}),{})

    def test_touching_joined_and_ambiguous_spacing_does_not_supply_letters(self):
        import numpy as np
        mask=np.zeros((30,100),bool)
        mask[5:14,10:17]=self.C;mask[5:14,17:24]=self.C
        mask[5:14,50:57]=self.G;mask[5:14,60:67]=self.G
        words=_words(mask,"CC GG","CC GG")
        self.assertFalse(any(v["raw"]=="CC" and v["alignable"] for v in words))
        mask[:]=False;mask[5:14,0:7]=self.C;mask[5:14,10:17]=self.C
        mask[5:14,40:47]=self.G;mask[5:14,50:57]=self.G
        words=_words(mask,"CC GG","CC GG")
        self.assertFalse(any(v["raw"]=="CC" and v["alignable"] for v in words))

    def test_multiple_lines_and_unbounded_shapes_stay_unknown(self):
        import numpy as np
        mask=np.zeros((60,100),bool)
        mask[5:14,10:17]=self.C;mask[30:39,20:27]=self.C
        mask[5:14,50:57]=self.G;mask[30:39,60:67]=self.G
        self.assertEqual(_words(mask,"CC GG","CC GG"),[])
        large=np.pad(self.C,((0,80),(0,0)))
        self.assertIsNone(_decide(large,"C","G",self.exemplars))

    def test_normal_materializer_consumes_recomputed_decision_but_keeps_other_blockers(self):
        import test_house_ptr_paper_qualification as fixtures
        from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation
        f=fixtures.HousePaperQualificationTests();f.setUp();raw,header,read,slots=f._source_bound_fixture()
        read["rereads"][0]["second_ocr_raw"]="Different"
        proof={"rule":"source_binary_glyph_exemplars/v1","first_raw":f.observation["pages"][0]["rows"][0]["cells"]["asset"]["raw_text"],"second_raw":"Different","decisions":[{"discriminator_mismatch_count":0}]}
        with patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells',return_value=slots),patch('unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound',return_value=True),patch('unison_snapshot.house_ptr_source_glyphs.corroborate_asset_readings',return_value={(1,1):proof}):
            q=materialize_paper_observation(raw,f.observation,f.metadata,f.identity,amount_header=header,field_reread=read)
        self.assertEqual(len(q['transactions']),1)
        self.assertEqual(q['source_bound_asset_decisions'][0]['first_raw'],q['transactions'][0]['asset_name'])
        self.assertEqual(len(q['quarantined']),1)  # unrelated unknown slot retained
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.builder import build
        from test_house_candidate import BASE
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            target=root/'house_clerk/qualifications/2026/9116141/source.json'
            target.parent.mkdir(parents=True);target.write_text(json.dumps(q),encoding='utf-8')
            status=root/'status/summary.json';status.parent.mkdir()
            status.write_text(json.dumps({'parser_commit':'a'*40,'pending_parse_count':0,
                'qualification_count':1,'qualified_transaction_count':1,'quarantined_row_count':1,
                'evidence_count':1,'extracted_count':1,'failure_count':0}),encoding='utf-8')
            candidate=build_house_candidate(root,{'status':'ok','run_at':'2026-10-21T00:00:00Z',
                'counts':{'archived':1,'pending':0,'failed':0}},BASE)
            self.assertEqual(candidate['transactions'],q['transactions'])
            self.assertGreater(len(build(candidate,generated_at='2026-10-21T00:00:00Z',allow_production=True).files),1)
        # A second pass cannot reclassify or migrate already formal source facts.
        with patch('unison_snapshot.house_ptr_source_glyphs.corroborate_asset_readings',side_effect=AssertionError('formal must return early')):
            again=materialize_paper_observation(raw,f.observation,f.metadata,f.identity,amount_header=header,field_reread=read,prior_qualification=q)
        self.assertEqual(again,q)


if __name__=="__main__":unittest.main()
