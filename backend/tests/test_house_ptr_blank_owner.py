"""New paper-only Owner policy; synthetic pixels never stand in for source gold."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import test_house_ptr_paper_qualification as fixtures
from unison_snapshot.house_ptr_paper_qualification import _blank_paper_owner, materialize_paper_observation
from unison_snapshot.house_ptr import qualify_automatic, OWNER_CODES
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.builder import build


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy'), 'raster optional')
class HouseBlankPaperOwnerTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        f = fixtures.HousePaperQualificationTests()
        f.setUp()
        self.raw, self.header, self.reread, self.slots = f._source_bound_fixture()
        self.f = f
        self.image = Image.new('RGB', (1000,1000), 'white')
        self.row = f.observation['pages'][0]['rows'][0]
        self.owner = self.row['cells']['owner']
        self.owner.update(raw_text='', bbox_pixels=[100,400,200,450],
                          bbox_normalized=[.1,.4,.2,.45])
        f.observation['render_dpi'] = 200
        f.observation['pages'][0].update(cell_status='raw_observed_unverified', coordinate_frame='original_page')
        self.grid = {'status':'located_shadow','physical_row_bands':[
            {'band':r['band']} for r in f.observation['pages'][0]['rows']]}
        self.refresh_crop()

    def refresh_crop(self):
        encoded = io.BytesIO()
        self.image.crop(tuple(self.owner['bbox_pixels'])).save(encoded, format='PNG')
        self.owner['crop_sha256'] = hashlib.sha256(encoded.getvalue()).hexdigest()

    def contexts(self):
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [SimpleNamespace(to_image=lambda **kw:SimpleNamespace(original=self.image))]
        return [patch('pdfplumber.open',return_value=pdf),
                patch('unison_snapshot.house_ptr_region_shadow.locate_transaction_grid',return_value=self.grid),
                patch('unison_snapshot.house_ptr_cell_observation_shadow._column_boxes',return_value={'status':'located_shadow','owner':[.1,.2]})]

    def proof(self):
        a,b,c=self.contexts()
        with a,b,c:
            return _blank_paper_owner(self.raw,self.f.observation,self.row,1,{})

    def materialize(self, prior=None):
        a,b,c=self.contexts()
        with a,b,c, patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells',return_value=self.slots), patch(
                'unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound',return_value=True):
            return materialize_paper_observation(self.raw,self.f.observation,self.f.metadata,self.f.identity,
                amount_header=self.header,field_reread=self.reread,prior_qualification=prior)

    def test_complete_synthetic_owner_reaches_actual_candidate_and_builder(self):
        from test_house_candidate import BASE
        q=self.materialize()
        self.assertEqual(q['transactions'][0]['owner'],'Undisclosed')
        self.assertEqual(len(q['source_bound_owner_decisions']),1)
        decision=q['source_bound_owner_decisions'][0]
        self.assertEqual((decision['page'],decision['physical_band_index']),(1,1))
        self.assertEqual(decision['extraction_id'],q['transactions'][0]['id'])
        self.assertEqual(decision['source_sha256'],self.f.metadata['sha256'])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            target=root/'house_clerk/qualifications/2026/9116141/source.json'
            target.parent.mkdir(parents=True);target.write_text(json.dumps(q))
            target=root/'status/summary.json';target.parent.mkdir()
            target.write_text(json.dumps({'parser_commit':'a'*40,'pending_parse_count':0,'qualification_count':1,
                'qualified_transaction_count':1,'quarantined_row_count':1,'evidence_count':1,'extracted_count':1,'failure_count':0}))
            candidate=build_house_candidate(root,{'status':'ok','run_at':'2026-10-20T00:00:00Z',
                'counts':{'archived':1,'pending':0,'failed':0}},BASE)
            self.assertEqual(candidate['transactions'][0]['owner'],'Undisclosed')
            bundle=build(candidate,generated_at='2026-10-20T00:00:00Z',allow_production=True)
            self.assertGreater(len(bundle.files),1)
        # A label/receipt alone never enables the default qualifier.
        default=qualify_automatic(q['materialized_extraction'],self.f.identity)
        self.assertEqual(default['transactions'],[])
        self.assertIn('owner_not_normalized',default['quarantined'][0]['reasons'])

    def test_empty_ocr_and_forged_zero_hint_do_not_hide_edge_handwriting(self):
        from PIL import ImageDraw
        for rectangle in [(102,416,108,431),(192,416,198,431),(136,402,158,407),(136,442,158,448)]:
            with self.subTest(edge=rectangle):
                self.image.paste('white',(0,0,1000,1000))
                self.draw_rules()
                ImageDraw.Draw(self.image).rectangle(rectangle,fill='black')
                self.owner['interior_dark_fraction']=0
                self.refresh_crop()
                self.assertIsNone(self.proof())
                self.assertEqual(self.materialize()['transactions'],[])

    def draw_rules(self):
        from PIL import ImageDraw
        pen=ImageDraw.Draw(self.image)
        for x in (100,199): pen.line((x,380,x,470),fill='black',width=2)
        for y in (400,449): pen.line((80,y,220,y),fill='black',width=2)

    def test_only_source_continuous_printed_rules_can_be_removed(self):
        self.draw_rules()
        self.refresh_crop()
        self.assertIsNotNone(self.proof())

    def test_blank_owner_does_not_need_ocr_but_event_date_still_does(self):
        self.owner['raw_ocr_confidence']=20
        q=self.materialize()
        self.assertEqual(q['transactions'][0]['owner'],'Undisclosed')
        self.row['cells']['event_date']['raw_ocr_confidence']=20
        q=self.materialize()
        self.assertEqual(q['transactions'],[])
        self.assertIn('key_source_reading_uncertain',q['quarantined'][0]['reasons'])

    def test_explicit_sp_dc_jt_are_never_overwritten(self):
        for code,owner in [('SP','Spouse'),('DC','Dependent Child'),('JT','Joint')]:
            with self.subTest(code=code):
                self.owner['raw_text']=code
                self.assertIsNone(self.proof())
                q=self.materialize()
                self.assertEqual(q['transactions'][0]['owner'],owner)
                self.assertNotIn('source_bound_owner_decisions',q)
        self.owner['raw_text']='?SP? conflicting'
        self.assertIsNone(self.proof())
        self.assertEqual(self.materialize()['transactions'],[])

    def test_hash_crop_and_geometry_tampering_are_not_blank_proof(self):
        self.owner['crop_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'blank owner crop differs'):
            self.proof()
        self.refresh_crop()
        self.owner['bbox_pixels'][0]+=1
        self.assertIsNone(self.proof())
        with self.assertRaisesRegex(ValueError,'source mismatch'):
            materialize_paper_observation(b'wrong',self.f.observation,self.f.metadata,self.f.identity)

    def test_existing_formal_self_returns_before_new_policy_or_pixel_work(self):
        prior=self.materialize()
        prior['transactions'][0]['owner']='Self'
        prior.pop('source_bound_owner_decisions',None)
        with patch('unison_snapshot.house_ptr_paper_qualification._blank_paper_owner',side_effect=AssertionError('old facts not rescanned')):
            retained=self.materialize(prior)
        self.assertEqual(retained,prior)
        self.assertEqual(OWNER_CODES[''],'Self')

    def test_source_cell_missing_or_unobserved_is_not_blank(self):
        self.owner['raw_text']=''
        self.f.observation['pages'][0]['cell_status']='unobserved_not_selected'
        self.assertIsNone(self.proof())


if __name__ == '__main__': unittest.main()
