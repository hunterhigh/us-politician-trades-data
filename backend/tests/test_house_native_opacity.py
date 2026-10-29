"""Source-selected House opacity preserves raw slots and independent rows."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import pdfplumber
import pymupdf
import test_house_native_contrast as contrast
from test_house_ptr import IDENTITY
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_archived_pdf, parse_word_pages, qualify_automatic
from unison_snapshot.house_native_contrast import (OPACITY_PARSER_VERSION, PARSER_VERSION,
    plan_house_native_contrast, replay_native_contrast)

def transform(raw,kind):
    d=pymupdf.open(stream=raw,filetype='pdf');p=d[0]
    x=d.get_new_xref();d.update_object(x,'<< /Type /ExtGState /ca 0 /CA 1 >>')
    y=d.get_new_xref();d.update_object(y,'<< /Type /ExtGState /ca 1 /CA 1 >>')
    z=d.get_new_xref();d.update_object(z,'<< /Type /ExtGState /ca 1 /CA 0 >>')
    rk,rv=d.xref_get_key(p.xref,'Resources');resource=int(rv.split()[0])
    d.xref_set_key(resource,'ExtGState',f'<< /FillZero {x} 0 R /Opaque {y} 0 R /StrokeZero {z} 0 R >>')
    if kind=='cross_white':
        ref=p.get_contents()[0];stream=d.xref_stream(ref)
        d.update_stream(ref,stream.replace(b'BT',b'/Opaque gs BT',1))
    needle=b'Example'.hex().encode();hits=[]
    for ref in p.get_contents():
        stream=d.xref_stream(ref)
        if needle not in stream:continue
        hits.append(ref)
        if kind=='ca0':stream=stream.replace(b'BT',b'/FillZero gs\nBT',1)
        elif kind=='ca1':stream=stream.replace(b'BT',b'/Opaque gs\nBT',1)
        elif kind=='CA0':stream=stream.replace(b'BT',b'/StrokeZero gs\nBT',1)
        elif kind=='restore_same_q':stream=stream.replace(b'BT',b'/FillZero gs /Opaque gs\nBT',1)
        elif kind=='restore_global':stream=b'/FillZero gs\n'+stream+b'\n/Opaque gs'
        elif kind=='restore_inside_BT':stream=stream.replace(b'BT',b'BT\n/FillZero gs /Opaque gs',1)
        d.update_stream(ref,stream)
    assert len(hits)==(0 if kind=='cross_white' else 1)
    if kind=='transparent_cover':
        shape=p.new_shape();shape.draw_rect(pymupdf.Rect(104,324,142,338));shape.finish(color=None,fill=(1,1,1));shape.commit()
        ref=p.get_contents()[-1];stream=d.xref_stream(ref)
        d.update_stream(ref,stream.replace(b'q',b'q /FillZero gs',1))
    if kind=='hidden_account':
        p.insert_text((105,374),'Subholding Of: Investment account #7',fontsize=8.5)
        ref=p.get_contents()[-1];stream=d.xref_stream(ref)
        d.update_stream(ref,stream.replace(b'BT',b'/FillZero gs BT',1))
    if kind in {'stroke_zero_grid','fill_zero_grid'}:
        p.draw_line(pymupdf.Point(100,380),pymupdf.Point(550,380),color=(0,0,0),width=1)
        ref=p.get_contents()[-1];stream=d.xref_stream(ref)
        state=b'/StrokeZero gs' if kind=='stroke_zero_grid' else b'/FillZero gs'
        d.update_stream(ref,stream.replace(b'q',b'q '+state,1))
    if kind=='fractional':
        d.update_object(z,'<< /Type /ExtGState /ca .5 /CA 1 >>')
        ref=hits[0];stream=d.xref_stream(ref)
        d.update_stream(ref,stream.replace(b'BT',b'/StrokeZero gs BT',1))
    data=d.tobytes();d.close();return data


class HouseNativeOpacityTests(unittest.TestCase):
    def source(self, kind='control'):
        helper=contrast.HouseNativeContrastTests()
        self.addCleanup(helper.doCleanups)
        root, original, old=helper.source('cross_white' if kind=='cross_white' else 'control')
        original_path=root/original['source']['archive_path']
        content=original_path.read_bytes()
        if kind != 'control':
            content=transform(content,kind)
        sha=hashlib.sha256(content).hexdigest()
        path=original_path.with_name(sha+'.pdf');path.write_bytes(content)
        metadata=json.loads(original_path.with_suffix('.json').read_text())
        metadata.update(sha256=sha,byte_length=len(content),archive_path=path.relative_to(root).as_posix())
        path.with_suffix('.json').write_text(json.dumps(metadata),encoding='utf-8')
        from unison_snapshot.house_native_coverage import observe_native_geometry
        with pdfplumber.open(path) as pdf:
            pages=[{'width':p.width,'height':p.height,
                'words':p.extract_words(x_tolerance=2,y_tolerance=2,extra_attrs=['size']),
                'native_page_geometry':observe_native_geometry(p,sha,i)} for i,p in enumerate(pdf.pages,1)]
        raw=parse_word_pages(metadata,sha,pages,copy_allowed=True)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',side_effect=AssertionError('No OCR')):
            current=parse_archived_pdf(root,path.with_suffix('.json'))
        return root,raw,current

    def meta(self,root,report):
        return (root/report['source']['archive_path']).with_suffix('.json')

    def test_used_state_selects_opacity_not_unused_resources(self):
        for kind in ('control','unused_resource'):
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind)
                self.assertFalse(plan_house_native_contrast(root,self.meta(root,current))['activated'])
                self.assertNotEqual(current['parser_version'],OPACITY_PARSER_VERSION)
                self.assertEqual(len(qualify_automatic(current,IDENTITY,evidence_root=root)['transactions']),2)

    def test_visible_restored_and_invisible_rows_replay_independently(self):
        for kind,names in [('CA0',['Example Corp.','Other Inc.']),('ca1',['Example Corp.','Other Inc.']),
            ('restore_same_q',['Example Corp.','Other Inc.']),('restore_inside_BT',['Example Corp.','Other Inc.']),
            ('ca0',['Other Inc.']),('restore_global',['Other Inc.']),('transparent_cover',['Example Corp.','Other Inc.'])]:
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind)
                self.assertEqual(current['parser_version'],OPACITY_PARSER_VERSION)
                self.assertEqual(current['transactions'],raw['transactions'])
                self.assertEqual(current['source_report_ledger']['logical_rows'],raw['source_report_ledger']['logical_rows'])
                q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual(sorted(t['asset_name'] for t in q['transactions']),names)
                self.assertEqual(len(q['transactions'])+len(q['quarantined']),2)
                self.assertFalse(q['coverage_complete'])
                self.assertEqual(q['qualification']['zero_transaction_document_count'],0)
                saved=json.loads(json.dumps(current))
                self.assertEqual(replay_native_contrast(saved,root),saved)
                self.assertEqual(qualify_automatic(saved,IDENTITY,evidence_root=root),q)
                self.assertEqual([d['raw_row_id'] for d in q['native_contrast_dispositions']],
                    [r['extraction_id'] for r in raw['transactions']])

    def test_root_saved_proof_and_source_binding_are_required(self):
        root,raw,current=self.source('CA0')
        with self.assertRaises(HouseIndexError):qualify_automatic(current,IDENTITY)
        with self.assertRaises(HouseIndexError):qualify_automatic(raw,IDENTITY,evidence_root=root)
        forged=deepcopy(current);forged['native_contrast_observation']['pages'][0]['proof']['glyph_states'][0]='unknown'
        with self.assertRaises(HouseIndexError):qualify_automatic(forged,IDENTITY,evidence_root=root)
        meta=self.meta(root,current);metadata=json.loads(meta.read_text());metadata['byte_length']+=1
        meta.write_text(json.dumps(metadata))
        with self.assertRaises(HouseIndexError):qualify_automatic(current,IDENTITY,evidence_root=root)

    def test_old_bichrome_explicit_replay_is_not_silently_upgraded(self):
        root,raw,current=self.source('transparent_cover')
        import unison_snapshot.house_native_contrast as module
        metadata,path,content=module._load(root,self.meta(root,current))
        import io
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            old,_=module._build(metadata,pdf,module._inventory(pdf,metadata['sha256']))
        self.assertEqual(old['parser_version'],PARSER_VERSION)
        self.assertEqual(replay_native_contrast(old,root),old)
        with self.assertRaises(HouseIndexError):qualify_automatic(old,IDENTITY,evidence_root=root)

    def test_transparent_stroke_cannot_supply_raw_grid_and_slots_do_not_merge(self):
        for kind,count in [('stroke_zero_grid',0),('fill_zero_grid',2)]:
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind)
                q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual(len(q['transactions']),count)
                self.assertEqual(current['transactions'],raw['transactions'])
                self.assertEqual(current['source_word_pages'],raw['source_word_pages'])
                self.assertEqual(current['source_report_ledger']['logical_rows'],raw['source_report_ledger']['logical_rows'])
                self.assertEqual(current['source_report_ledger']['observed_physical_slot_count'],2)
                self.assertEqual(len(q['quarantined']),2-count)
                if not count:
                    self.assertTrue(all('native_opacity_grid_visibility_unresolved' in r['reasons'] for r in q['quarantined']))
                    self.assertTrue(all(r['slot_refs'] for r in q['quarantined']))
                self.assertFalse(q['coverage_complete'])

    def test_unsupported_fractional_alpha_stays_unknown_not_legacy(self):
        root,raw,current=self.source('fractional')
        self.assertEqual(current['parser_version'],OPACITY_PARSER_VERSION)
        q=qualify_automatic(current,IDENTITY,evidence_root=root)
        self.assertEqual(q['transactions'],[])
        self.assertEqual(len(q['quarantined']),2)
        self.assertEqual(q['qualification']['zero_transaction_document_count'],0)

    def test_invisible_account_heading_is_physical_scope_risk_not_filtered_blank(self):
        root,raw,current=self.source('hidden_account')
        q=qualify_automatic(current,IDENTITY,evidence_root=root)
        self.assertEqual(current['parser_version'],OPACITY_PARSER_VERSION)
        self.assertEqual([t['asset_name'] for t in q['transactions']],['Other Inc.'])
        self.assertEqual(len(q['quarantined']),1)
        self.assertEqual(raw['transactions'][0]['subholding_of'],'Investment account #7')
        self.assertTrue(q['native_contrast_dispositions'][0]['unavailable_word_members'])
        self.assertTrue(q['quarantined'][0]['slot_refs'])
        self.assertEqual(current['transactions'],raw['transactions'])

    def test_document_opacity_selection_does_not_ignore_other_page_white_scope(self):
        root,raw,current=self.source('cross_white')
        q=qualify_automatic(current,IDENTITY,evidence_root=root)
        self.assertEqual(current['parser_version'],OPACITY_PARSER_VERSION)
        self.assertEqual([t['asset_name'] for t in q['transactions']],['Other Inc.'])
        self.assertEqual(len(q['quarantined']),1)
        self.assertEqual(len(q['quarantined'][0]['slot_refs']),2)
        self.assertEqual(current['transactions'],raw['transactions'])

    def test_document_event_budget_is_explicit_not_mechanism_absence(self):
        root,raw,current=self.source('CA0')
        for name in ('MAX_DOCUMENT_GRAPHICS_STATE_EVENTS','MAX_DOCUMENT_PAINT_EVENTS'):
            with self.subTest(name=name),patch('unison_snapshot.house_native_contrast.'+name,0):
                with self.assertRaisesRegex(HouseIndexError,'event budget'):
                    plan_house_native_contrast(root,self.meta(root,current))
                with self.assertRaisesRegex(HouseIndexError,'event budget'):
                    parse_archived_pdf(root,self.meta(root,current))

    def test_unknown_image_body_does_not_turn_repeated_header_into_prior_row_detail(self):
        import test_house_native_visibility as visibility
        helper=visibility.NativeVisibilityTests();self.addCleanup(helper.doCleanups)
        fixture=helper.fixture('opacity')
        current,_=helper.parse(fixture)
        q=qualify_automatic(current,IDENTITY,evidence_root=fixture.root)
        historical=parse_word_pages(fixture.metadata,fixture.sha,fixture.pages,copy_allowed=True)
        old=qualify_automatic(historical,IDENTITY)
        self.assertEqual(q['transactions'],old['transactions'])
        self.assertEqual([r['asset_name'] for r in q['transactions']],['Example Corp.'])
        self.assertEqual(current['transactions'][0]['extraction_id'],historical['transactions'][0]['extraction_id'])
        self.assertEqual(len(q['quarantined']),1)
        self.assertIn('native_text_visibility_unknown',q['quarantined'][0]['reasons'])
        self.assertTrue(q['quarantined'][0]['slot_refs'])
        self.assertFalse(q['coverage_complete'])
