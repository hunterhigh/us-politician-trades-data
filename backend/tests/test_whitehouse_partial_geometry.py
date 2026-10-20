"""Partial geometry contracts; synthetic examples are not source OCR gold."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image
from test_oge_278t_image_grid import grid_page, EDGES
from test_oge_content_relations import saved
from test_whitehouse_278t_saved_ocr import SavedWhiteHouseTests
from test_whitehouse_278t_generic import consume
from unison_snapshot.oge_278t_raster_slots import _oge_2024_image_grid, _joint_horizontal_partition, _partial_header_geometry
from unison_snapshot.oge_278t_field_shadow import observe_content_rows
from unison_snapshot.whitehouse_278t import (_generic_cover_dates, _saved_source_cover_resolution,
    generic_whitehouse_filing_date, SAVED_OCR_PARSER_VERSION, TRUMP_2026_PROFILES,
    validate_generic_whitehouse_report, materialize_saved_whitehouse_278t)
from unison_snapshot.whitehouse_278t_audit import source_bound_filer_identity, source_bound_filing_date
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.builder import build


def altered_page(kind):
    page=grid_page();pixels=np.asarray(page.to_image().original).copy()
    if kind=='stroke':pixels[183*3,215*3:230*3]=0
    if kind=='right_only':pixels[183*3,628*3:686*3]=0
    if kind=='broken':
        pixels[191*3,:]=255
        for x in EDGES:pixels[191*3,int(x*3)]=0
        pixels[191*3,118*3:133*3]=0
    page.to_image=Mock(return_value=SimpleNamespace(original=Image.fromarray(pixels)))
    return page


def partial_saved(*, heading=False):
    first=["", "Investment account #1", "", "", "", ""] if heading else ["1","ALPHA","purchase","06/01/2026","No","$1,001 - $15,000"]
    data=saved([first,["", "unknown original text", "", "", "", ""],
        ["3","BETA","purchase","06/02/2026","No","$1,001 - $15,000"]])
    page=data['pages'][0];removed=page['slots'].pop(1);page['slots'][1]['slot_index']=2
    unbound=[{'word_index':0,'text':w['text'],'x0':w['bbox_pdf_points'][0],'top':w['bbox_pdf_points'][1],
        'x1':w['bbox_pdf_points'][2],'bottom':w['bbox_pdf_points'][3],'ocr_confidence':90,'block_num':1,'par_num':1,'line_num':2}
        for w in removed['words']]
    geometry={'partial_row_geometry':True,'physical_row_bands':[s['band_pdf_points'] for s in page['slots']],
        'unresolved_regions':[[115,130]]}
    page.update(partial_row_geometry=True,physical_slot_count=None,known_physical_slot_count=2,
        whole_page_physical_slot_count=None,unresolved_regions=[[115,130]],column_geometry_observation=geometry,
        unbound_page_ocr_words=unbound)
    return data


class PartialGeometryTests(unittest.TestCase):
    def test_extra_local_stroke_is_preserved_as_barrier_not_a_deleted_boundary(self):
        page=altered_page('stroke')
        self.assertEqual(_oge_2024_image_grid(page)['status'],'unknown')
        out=_oge_2024_image_grid(page,allow_partial_rows=True)
        self.assertEqual(out['status'],'candidate')
        self.assertTrue(out['partial_row_geometry'])
        self.assertEqual(len(out['physical_row_bands']),30)
        self.assertIn([176.,191.],out['unresolved_regions'])
        self.assertNotIn([176.,191.],out['physical_row_bands'])
        self.assertTrue(out['joint_horizontal_evidence']['boundary_barriers'])

    def test_right_column_only_separator_is_not_invisible_to_three_seed_strips(self):
        out=_oge_2024_image_grid(altered_page('right_only'),allow_partial_rows=True)
        self.assertEqual(out['status'],'candidate')
        self.assertIn([176.,191.],out['unresolved_regions'])
        page=altered_page('right_only');gray=np.asarray(page.to_image().original)
        rules=[list(map(float,range(116,582,15))) for _ in range(3)]
        partition=_joint_horizontal_partition(gray,rules,EDGES,sx=3,sy=3)
        self.assertIn([176.,191.],partition['unresolved_regions'])
        self.assertNotIn([176.,191.],partition['bands'])
        self.assertTrue(any(b.get('column_index')==5 for b in partition['evidence']['boundary_barriers']))

    def test_actual_broken_divider_cannot_be_swallowed_as_a_double_height_row(self):
        out=_oge_2024_image_grid(altered_page('broken'),allow_partial_rows=True)
        self.assertEqual(out['status'],'candidate')
        self.assertNotIn([176.,206.],out['physical_row_bands'])
        self.assertTrue(any(a<=191<=b for a,b in out['unresolved_regions']))

    def test_conflicting_nearby_candidates_are_not_greedily_matched(self):
        gray=np.asarray(grid_page().to_image().original)
        rules=[list(map(float,range(116,582,15))) for _ in range(3)]
        rules[2].insert(6,192.5)
        out=_joint_horizontal_partition(gray,rules,EDGES,sx=3,sy=3)
        self.assertTrue(any(b['kind']=='ambiguous_strip_candidate' for b in out['evidence']['boundary_barriers']))
        self.assertFalse(any(a < 191 < b for a,b in out['bands']))

    def test_existing_negative_geometries_stay_unknown_with_opt_in(self):
        for kind in ('missing','extra','gap','block','left_only','header_only'):
            with self.subTest(kind=kind):self.assertEqual(_oge_2024_image_grid(grid_page(kind=kind),allow_partial_rows=True)['status'],'unknown')

    def test_unique_existing_header_roles_required_before_any_local_row_consumption(self):
        from test_oge_278t_image_grid import header_words
        geometry=_oge_2024_image_grid(altered_page('stroke'),allow_partial_rows=True)
        bound,header=_partial_header_geometry(header_words(),geometry)
        self.assertEqual(header['status'],'confirmed')
        self.assertEqual(bound['header_band_pdf_points'][1],116)
        for kind in ('missing','annual','body_duplicate'):
            words=header_words()
            if kind=='missing':words.pop(0)
            if kind=='annual':words[7]['text']='278e'
            if kind=='body_duplicate':
                duplicate=deepcopy(words[:5])
                for w in duplicate:w['top']+=30;w['bottom']+=30
                words.extend(duplicate)
            with self.subTest(kind=kind):self.assertEqual(_partial_header_geometry(words,geometry)[1]['status'],'unknown')

    def test_partial_content_retains_unknown_regions_words_and_disables_number_inference(self):
        data=partial_saved();before=deepcopy(data)
        result=observe_content_rows([data],document_page_count=3)
        self.assertEqual(data,before)
        ledger=result['source_report_ledger']
        self.assertIsNone(ledger['physical_slot_count'])
        self.assertEqual(ledger['observed_physical_slot_count'],2)
        page=next(p for p in ledger['pages'] if p['page_number']==2)
        self.assertEqual(page['coverage'],'unknown');self.assertEqual(page['unresolved_regions'],[[115,130]])
        self.assertEqual(result['partial_geometry_pages'][0]['unbound_page_ocr_words'],data['pages'][0]['unbound_page_ocr_words'])
        self.assertTrue(result['slot_content'][1]['unresolved_region_before'])
        self.assertEqual(result['printed_number_observation']['pages'][0]['status'],'partial_grid_numbers_observed')
        changed=deepcopy(data);changed['pages'][0]['unresolved_regions']=[[110,130]]
        changed['pages'][0]['column_geometry_observation']['unresolved_regions']=[[110,130]]
        with self.assertRaisesRegex(ValueError,'overlaps'):observe_content_rows([changed],document_page_count=3)
        changed=deepcopy(data);changed['pages'][0]['slots'][0]['unresolved_region_crossing_word_indices']=[0]
        relation=observe_content_rows([changed],document_page_count=3)['slot_content'][0]['relation_evidence']
        self.assertIn('word_box_crosses_unresolved_region',relation['blocking_reasons'])

    def test_partial_materializer_candidate_builder_and_scope_loss(self):
        case=SavedWhiteHouseTests()
        for heading in (False,True):
            with self.subTest(heading=heading),tempfile.TemporaryDirectory() as folder:
                inputs,_=case.fixture(Path(folder));inputs['allow_partial_rows']=True
                data=partial_saved(heading=heading)
                meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes());data['source_sha256']=meta['sha256']
                with case.context(data):
                    report=materialize_saved_whitehouse_278t(inputs)
                    validate_generic_whitehouse_report(report)
                    candidate,audit=consume(report)
                self.assertEqual(report['observation_receipt']['observed_physical_slot_count'],2)
                self.assertIsNone(report['observation_receipt']['whole_selected_table_slot_count'])
                self.assertEqual(report['observation_receipt']['geometry_partial_pages'],[2])
                self.assertEqual(audit['reports'][0]['partial_geometry_pages'],report['content_observations']['partial_geometry_pages'])
                if heading:
                    self.assertEqual(candidate['transactions'],[])
                    self.assertIn('account_context_unresolved',report['quarantined'][0]['reasons'])
                else:
                    self.assertEqual(len(candidate['transactions']),2)
                    self.assertTrue(build(candidate,generated_at='2026-06-20T00:00:00Z',allow_production=True).manifest['snapshot_id'])

    def test_existing_profile_dates_and_identity_resolve_missing_ocr_but_not_conflicts(self):
        profile=next(p for p in TRUMP_2026_PROFILES if p['page_count']==113)
        missing=_generic_cover_dates('OGE Form 278-T\nFiler Certification\nDate: unknown\n',profile['source_sha256'],True)
        resolved=_saved_source_cover_resolution(profile['document_id'],profile['source_url'],profile['source_sha256'],113,missing)
        self.assertEqual(resolved['filed_at'],'2026-05-08')
        self.assertNotIn('filer_signature_date_missing',resolved['document_reasons'])
        report={**profile,'parser_version':SAVED_OCR_PARSER_VERSION,'cover_date_evidence':missing,
            'filed_at':resolved['filed_at'],'source_cover_resolution':resolved,
            'signature_method':'official_disclosure_date_source_bound','filing_date_evidence':resolved['filing_date_evidence'],
            'filer_identity_evidence':resolved['filer_identity_evidence'],'pdf_filer_name':'Donald J Trump',
            'pdf_position_title':'President of the United States of America','pdf_agency_label':None}
        self.assertEqual(generic_whitehouse_filing_date(report),'2026-05-08')
        self.assertEqual(source_bound_filing_date(report),'2026-05-08');self.assertTrue(source_bound_filer_identity(report))
        changed=deepcopy(report);changed['filing_date_evidence']['normalized']='2026-05-09'
        with self.assertRaises(OgeCatalogError):generic_whitehouse_filing_date(changed)
        self.assertIsNone(source_bound_filing_date(changed))
        with self.assertRaisesRegex(OgeCatalogError,'page count'):
            _saved_source_cover_resolution(profile['document_id'],profile['source_url'],profile['source_sha256'],112,missing)
        conflict=_generic_cover_dates("OGE Form 278-T\nFiler's Information\nBiden, Joseph\nVice President\nFiler Certification\nDate: 05/09/2026\n",profile['source_sha256'],True)
        value=_saved_source_cover_resolution(profile['document_id'],profile['source_url'],profile['source_sha256'],113,conflict)
        self.assertIn('ocr_source_filer_identity_conflict',value['document_reasons'])
        self.assertIn('ocr_source_filing_date_conflict',value['document_reasons'])
        self.assertIn('ocr_source_filer_role_conflict',value['document_reasons'])

    def test_same_report_formal_rows_are_preserved_without_append_or_unproved_correction(self):
        from test_whitehouse_278t_candidate import _base,_catalog
        from unison_snapshot.whitehouse_278t_candidate import _person,_transaction
        from unison_snapshot.oge_reports import parse_table_rows
        case=SavedWhiteHouseTests()
        for bind_prior in (False,True):
            with self.subTest(bind_prior=bind_prior),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);inputs,_=case.fixture(root);inputs['allow_partial_rows']=True
                data=partial_saved();meta=json.loads(Path(inputs['archive_metadata_path']).read_bytes());data['source_sha256']=meta['sha256']
                with case.context(data):report=materialize_saved_whitehouse_278t(inputs)
                physical=report['source_rows']
                old_good,_=parse_table_rows([(r['page_number'],r['cells']) for r in physical],source_sha=meta['sha256'],default_owner='Self')
                prior={**deepcopy(report),'parser_version':'whitehouse-278t-hybrid-geometry/v2',
                       'transactions':old_good,'quarantined':[]}
                if bind_prior:
                    path=root/'legacy.json';raw=json.dumps(prior).encode();path.write_bytes(raw)
                    inputs.update(legacy_extraction_path=str(path),legacy_extraction_sha256=hashlib.sha256(raw).hexdigest())
                person=_person(_catalog()['reports'][0]);base=_base();base['people']=[person]
                base['meta']['data_cutoff_at']='2026-06-20T00:00:00Z'
                fact=_transaction(prior,old_good[-1],person['id'])
                # A preexisting display suffix must not invite a new ID based
                # on the newly compacted local slot array or Undisclosed owner.
                fact['asset_name']+=' — Investment account #9'
                base['transactions']=[fact];before=deepcopy(base['transactions'])
                with case.context(data):
                    report=materialize_saved_whitehouse_278t(inputs)
                    candidate,audit=consume(report,base)
                    again,second=consume(report,candidate)
                self.assertEqual(candidate['transactions'],before)
                self.assertEqual(again['transactions'],before)
                self.assertEqual(audit['promoted_transaction_count'],0)
                self.assertEqual(second['promoted_transaction_count'],0)
                self.assertTrue(all('legacy_report_new_row_binding_pending' in row['reasons'] for row in audit['reports'][0]['rows']))
                if bind_prior:self.assertEqual({r['extraction_id'] for r in report['transactions']},{r['extraction_id'] for r in old_good})


if __name__=='__main__':unittest.main()
