"""Real raster PDFs with synthetic rule populations, not recognition gold."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.oge_278t_page_shadow import inspect_pdf, probe_raster_row_bands
from unison_snapshot.oge_278t_raster_inventory import audit_raster_source_inventory


def raster_pdf(path, rules, *, second_page=False, page_count=None, occlude=False, extra_table=False):
    from PIL import Image, ImageDraw
    import pymupdf
    image=Image.new('RGB',(1584,1584),'white');draw=ImageDraw.Draw(image)
    for y in rules:draw.line((180,y*2,1400,y*2),fill='black',width=2)
    if rules:
        for x in (90,135,445,496,553,600,700):
            draw.line((x*2,min(rules)*2,x*2,max(rules)*2),fill='black',width=2)
    if occlude:draw.rectangle((216,282,274,298),fill='white')
    if extra_table:
        for y in range(650,741,15):draw.line((180,y*2,1400,y*2),fill='black',width=2)
    raw=BytesIO();image.save(raw,format='PNG')
    document=pymupdf.open()
    for _ in range(page_count or (2 if second_page else 1)):
        page=document.new_page(width=792,height=792);page.insert_image(page.rect,stream=raw.getvalue())
    document.save(path);document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IndependentRasterInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'source.pdf'

    def observe(self, rules, **kwargs):
        sha=raster_pdf(self.path,rules,**kwargs)
        census=probe_raster_row_bands(self.path,expected_sha256=sha,page_numbers=[1])
        return sha,census

    def test_reproduces_old_search_window_blind_spot_without_any_field_anchors(self):
        sha,census=self.observe(list(range(100,731,15)))
        old=census['results'][0]
        self.assertEqual(old['status'],'physical_slots_located')
        self.assertEqual(len(old['rule_positions']),33)  # Source actually has 43 rules.
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        page=result['pages'][0]
        self.assertEqual(len(page['source_boundary_positions']),43)
        self.assertEqual(len(page['unaccounted_source_boundaries']),10)
        self.assertEqual(result['mismatched_page_numbers'],[1])
        self.assertFalse(result['all_selected_boundaries_accounted'])
        self.assertFalse(result['report_completeness_attested'])
        self.assertIsNone(result['qualified_transaction_count'])

    def test_aligned_rules_are_geometry_evidence_not_blank_or_transaction_qualification(self):
        sha,census=self.observe(list(range(100,221,15)))
        original=deepcopy(census)
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        self.assertEqual(census,original)
        self.assertTrue(result['all_selected_boundaries_accounted'])
        self.assertEqual(result['pages'][0]['status'],'source_boundaries_aligned')
        self.assertEqual(result['pages'][0]['content_status'],'unverified')
        self.assertFalse(result['report_completeness_attested'])
        self.assertIsNone(result['qualified_transaction_count'])
        self.assertFalse(result['recognizer_started'])

    def test_inserted_source_boundary_inside_claimed_slot_is_found(self):
        sha,census=self.observe(list(range(100,221,15)))
        bands=census['results'][0]['physical_row_bands']
        bands[2:4]=[[bands[2][0],bands[3][1]]]
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        page=result['pages'][0]
        self.assertEqual(page['status'],'source_inventory_mismatch')
        self.assertEqual(page['claimed_slots_crossing_source_boundaries'][0]['slot_index'],3)
        self.assertEqual(len(page['unaccounted_source_boundaries']),1)

    def test_splitting_one_source_boundary_within_tolerance_is_not_a_bijection(self):
        sha,census=self.observe(list(range(100,221,15)))
        bands=census['results'][0]['physical_row_bands']
        left,right=bands[0]
        bands[:1]=[[left,left+.5],[left+.5,right]]
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        page=result['pages'][0]
        self.assertEqual(page['status'],'source_inventory_mismatch')
        self.assertTrue(page['ambiguous_source_boundaries'])
        self.assertFalse(result['all_selected_boundaries_accounted'])

    def test_extra_table_and_image_page_remain_unaccounted(self):
        sha,census=self.observe(list(range(100,221,15)),second_page=True,extra_table=True)
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census,document_page_count=2)
        self.assertEqual(result['mismatched_page_numbers'],[1])
        self.assertEqual(result['unknown_page_numbers'],[2])
        self.assertEqual(result['pages'][1]['reason'],'raster_page_not_in_saved_inventory')
        self.assertEqual(result['document_page_count'],2)
        self.assertFalse(result['report_completeness_attested'])

    def test_structural_header_rules_do_not_become_missing_transaction_claims(self):
        sha,census=self.observe([40,55,*range(100,221,15)])
        self.assertEqual(len(census['results'][0]['physical_row_bands']),8)
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        self.assertEqual(len(result['pages'][0]['unaccounted_source_boundaries']),2)
        self.assertEqual(result['pages'][0]['status'],'source_inventory_mismatch')
        self.assertEqual(result['pages'][0]['content_status'],'unverified')
        self.assertIn('not necessarily missed disclosures',result['limitation'])
        self.assertFalse(result['report_completeness_attested'])
        self.assertIsNone(result['qualified_transaction_count'])

    def test_more_than_eight_saved_pages_preserves_unknown_instead_of_failing(self):
        sha,_=self.observe(list(range(100,221,15)),page_count=9)
        census=probe_raster_row_bands(self.path,expected_sha256=sha,page_numbers=[1])
        template=census['results'][0]
        census['results']=[dict(deepcopy(template),page_number=number) for number in range(1,10)]
        result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        self.assertEqual(result['limited_page_numbers'],[9])
        self.assertEqual(result['pages'][8]['reason'],'source_inventory_render_budget_exceeded')
        self.assertEqual(result['pages'][8]['status'],'unknown')
        self.assertEqual(result['unknown_page_numbers'],[9])
        self.assertFalse(result['all_selected_boundaries_accounted'])

    def test_large_mediabox_is_unknown_before_any_raster_render(self):
        import pymupdf
        from PIL import Image
        from unittest.mock import patch
        raw=BytesIO();Image.new('RGB',(10,10),'white').save(raw,format='PNG')
        document=pymupdf.open();page=document.new_page(width=3000,height=3000)
        page.insert_image(page.rect,stream=raw.getvalue());document.save(self.path);document.close()
        sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        census={'source_sha256':sha,'results':[{'page_number':1,'status':'physical_slots_located',
            'physical_row_bands':[[100,115]]}]}
        with patch('pdfplumber.page.Page.to_image',side_effect=AssertionError('oversized page rendered')):
            result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        self.assertEqual(result['pages'][0]['reason'],'source_inventory_render_size_exceeded')
        self.assertEqual(result['limited_page_numbers'],[1])
        self.assertFalse(result['all_selected_boundaries_accounted'])

    def test_saved_materializer_and_adapter_retain_facts_but_reject_false_complete_inventory(self):
        from tests.test_oge_observed_candidate import original
        from tests.test_oge_content_relations import saved
        from tests.test_oge_candidate import direct
        from unison_snapshot.oge_transaction_adapter import materialize_oge_278t_observations, adapt_oge_278t_extraction
        for rules,bottom in ((list(range(100,731,15)),115), (list(range(100,221,15)),130)):
            with self.subTest(bottom=bottom):
                sha=raster_pdf(self.path,rules)
                old=original();old['source_sha256']=sha
                observations=saved([old['transactions'][0]['cells']])
                observations['source_sha256']=sha
                observations['pages'][0]['page_number']=1
                observations['pages'][0]['slots'][0]['band_pdf_points']=[100,bottom]
                before=deepcopy(old)
                materialized=materialize_oge_278t_observations(old,direct(),observations=[observations],
                    document_page_count=1,archived_pdf_path=str(self.path))
                self.assertEqual(old,before)
                self.assertEqual(materialized['transactions'],old['transactions'])
                receipt=materialized['observation_receipt']
                self.assertFalse(receipt['coverage_complete'])
                self.assertEqual(receipt['raster_source_inventory']['mismatched_page_numbers'],[1])
                if bottom==130:
                    self.assertEqual(receipt['raster_source_inventory']['pages'][0]
                        ['claimed_slots_crossing_source_boundaries'][0]['slot_index'],1)
                adapted=adapt_oge_278t_extraction(old,direct(),raster_observations=[observations],
                    document_page_count=1,archived_pdf_path=self.path)
                self.assertEqual(adapted['source_report_ledger']['pages'][0]['coverage'],'unknown')
                self.assertEqual(adapted['source_report_ledger']['relation_coverage'],'partial_unknown')
                self.assertEqual(adapted['raster_source_inventory']['mismatched_page_numbers'],[1])
                without=adapt_oge_278t_extraction(old,direct(),raster_observations=[observations],document_page_count=1)
                self.assertEqual(without['raster_source_inventory']['status'],'archived_source_unavailable')
                self.assertFalse(without['raster_source_inventory']['all_selected_boundaries_accounted'])
                self.assertEqual(without['source_report_ledger']['pages'][0]['coverage'],'unknown')
                self.assertEqual(without['source_report_ledger']['relation_coverage'],'partial_unknown')

    def test_empty_image_and_strip_disagreement_do_not_prove_zero_or_coverage(self):
        for rules,occlude in (([],False),(list(range(100,221,15)),True)):
            with self.subTest(occlude=occlude):
                sha,census=self.observe(rules,occlude=occlude)
                result=audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
                self.assertEqual(result['pages'][0]['status'],'unknown')
                self.assertFalse(result['all_selected_boundaries_accounted'])
                self.assertIsNone(result['qualified_transaction_count'])

    def test_wrong_source_page_count_and_outside_pages_are_rejected(self):
        sha,census=self.observe(list(range(100,221,15)))
        with self.assertRaisesRegex(ValueError,'source PDF hash'):
            self.path.write_bytes(self.path.read_bytes()+b'\n')
            audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)
        sha,census=self.observe(list(range(100,221,15)))
        with self.assertRaisesRegex(ValueError,'page count'):
            audit_raster_source_inventory(self.path,expected_sha256=sha,census=census,document_page_count=2)
        census['results'][0]['page_number']=2
        with self.assertRaisesRegex(ValueError,'outside PDF'):
            audit_raster_source_inventory(self.path,expected_sha256=sha,census=census)

    def test_normal_pdf_inspector_exposes_independent_source_mismatch(self):
        sha,_=self.observe(list(range(100,731,15)))
        result=inspect_pdf(self.path,raster_ocr_records=[],raster_ocr_source_sha256=sha)
        self.assertEqual(result['raster_ocr_coverage']['physical_slot_count'],32)
        self.assertTrue(all(slot['content_status']=='unverified' for slot in result['raster_ocr_coverage']['slots']))
        self.assertEqual(result['raster_source_inventory']['mismatched_page_numbers'],[1])


if __name__=='__main__':unittest.main()
