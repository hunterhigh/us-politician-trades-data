"""Generated original PDFs exercise House clip source replay and physical rows."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import pdfplumber
import pymupdf
import test_house_native_contrast as contrast
import test_house_native_visibility as visibility
from test_house_ptr import IDENTITY
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_archived_pdf, parse_word_pages, qualify_automatic
from unison_snapshot.house_native_contrast import (CLIP_PARSER_VERSION, CLIP_MODE,
    OPACITY_PARSER_VERSION, PARSER_VERSION, plan_house_native_contrast, replay_native_contrast)


def transform(raw, kind):
    document=pymupdf.open(stream=raw,filetype='pdf')
    page=document[1] if kind=='image_header_clip' else document[0]
    codes={'inside':b'100 447 70 25 re W n','outside':b'400 447 30 25 re W n',
        'partial':b'120 447 50 25 re W n','inside_gs':b'100 447 70 25 re W n',
        'outside_gs':b'400 447 30 25 re W n','scoped_empty':b'q 400 447 30 25 re W n Q',
        'after_text':b'','evenodd':b'100 447 70 25 re W* n',
        'negative':b'170 447 -70 25 re W n','curve':b'100 447 m 170 447 l 170 472 l 100 472 l h W n',
        'uncommitted':b'400 447 30 25 re W','transform':b'1 0 0 1 0 0 cm 100 447 70 25 re W n',
        'nested':b'100 447 70 25 re W n 110 447 60 25 re W n',
        'image_header_clip':b'400 447 30 25 re W n',
        'white_outside':b'400 447 30 25 re W n','outside_same':b'400 447 30 25 re W n','outside_different':b'400 447 30 25 re W n',
        'partial_same':b'120 447 50 25 re W n'}
    if kind in {'cross_white','cross_tr3'}:
        ref=page.get_contents()[0];stream=document.xref_stream(ref)
        document.update_stream(ref,stream.replace(b'BT',b'0 0 612 792 re W n BT',1))
        if kind=='cross_tr3':
            for ref in document[1].get_contents():
                stream=document.xref_stream(ref)
                if b'Subholding Of: Investment account #7'.hex().encode() in stream:
                    document.update_stream(ref,stream.replace(b'BT',b'BT 3 Tr',1))
    elif kind in {'account_outside','anchor_outside','second_anchor_outside','glyph_clip','all_glyph_clip','stroke_outside'}:
        if kind=='account_outside':
            page.insert_text((105,374),'Subholding Of: Investment account #7',fontsize=8.5)
            ref=page.get_contents()[-1];stream=document.xref_stream(ref)
            document.update_stream(ref,stream.replace(b'BT',b'400 390 30 25 re W n BT',1))
        elif kind=='stroke_outside':
            page.draw_line(pymupdf.Point(100,380),pymupdf.Point(550,380),color=(0,0,0),width=1)
            ref=page.get_contents()[-1];stream=document.xref_stream(ref)
            document.update_stream(ref,stream.replace(b'q',b'q 400 447 30 25 re W n',1))
        else:
            for ref in page.get_contents():
                stream=document.xref_stream(ref)
                needle=(b'08/13/2026' if kind=='second_anchor_outside' else b'08/12/2026').hex().encode()
                if kind in {'glyph_clip','all_glyph_clip'}:
                    if (kind=='glyph_clip' and b'Example'.hex().encode() in stream) or (kind=='all_glyph_clip' and b'BT' in stream):
                        document.update_stream(ref,stream.replace(b'BT',b'BT 4 Tr',1))
                elif needle in stream:
                    document.update_stream(ref,stream.replace(b'BT',b'400 447 30 25 re W n BT',1))
    elif kind!='control':
        needle=(b'Stale' if kind=='image_header_clip' else b'Example').hex().encode();hits=[]
        for ref in page.get_contents():
            stream=document.xref_stream(ref)
            if needle not in stream:continue
            hits.append(ref);stream=stream.replace(b'BT',codes[kind]+b'\nBT',1)
            if kind=='after_text':stream=stream.replace(b'ET',b'ET 400 447 30 25 re W n',1)
            if kind.endswith('_gs'):
                resource=int(document.xref_get_key(page.xref,'Resources')[1].split()[0])
                document.xref_set_key(resource,'ExtGState','<< /Opaque << /Type /ExtGState /ca 1 /CA 1 >> >>')
                stream=stream.replace(b'BT',b'/Opaque gs BT',1)
            document.update_stream(ref,stream)
        assert len(hits)==1
        if kind in {'outside_same','outside_different','partial_same'}:
            page.insert_text((105,335),'Other' if kind=='outside_different' else 'Example',fontsize=9)
    result=document.tobytes();document.close();return result


class HouseNativeClipTests(unittest.TestCase):
    def source(self,kind='control'):
        if kind=='image_header_clip':
            helper=visibility.NativeVisibilityTests();self.addCleanup(helper.doCleanups)
            fixture=helper.fixture('visible');root=fixture.root;original=fixture.pdf
        else:
            helper=contrast.HouseNativeContrastTests();self.addCleanup(helper.doCleanups)
            root,old,_=helper.source('cross_white' if kind=='cross_white' else 'cross_visible' if kind=='cross_tr3' else 'white' if kind=='white_outside' else 'control')
            original=root/old['source']['archive_path']
        content=transform(original.read_bytes(),kind)
        sha=hashlib.sha256(content).hexdigest();path=original.with_name(sha+'.pdf');path.write_bytes(content)
        meta=json.loads(original.with_suffix('.json').read_text(encoding='utf-8'))
        meta.update(sha256=sha,byte_length=len(content),archive_path=path.relative_to(root).as_posix())
        path.with_suffix('.json').write_text(json.dumps(meta),encoding='utf-8')
        if original.with_suffix('.json')!=path.with_suffix('.json'):original.with_suffix('.json').unlink()
        from unison_snapshot.house_native_coverage import observe_native_geometry
        with pdfplumber.open(path) as pdf:
            pages=[{'width':p.width,'height':p.height,
                'words':p.extract_words(x_tolerance=2,y_tolerance=2,extra_attrs=['size']),
                'native_page_geometry':observe_native_geometry(p,sha,i)} for i,p in enumerate(pdf.pages,1)]
        raw=parse_word_pages(meta,sha,pages,copy_allowed=True,_source_repeated_header_boundaries=True)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages',side_effect=AssertionError('No new OCR')):
            current=parse_archived_pdf(root,path.with_suffix('.json'))
        return root,raw,current

    def meta(self,root,report):return (root/report['source']['archive_path']).with_suffix('.json')

    def test_clip_visibility_and_qQ_recovery_preserve_original_physical_rows(self):
        for kind,names in [('inside',['Example Corp.','Other Inc.']),('outside',['Other Inc.']),
            ('partial',['Other Inc.']),('inside_gs',['Example Corp.','Other Inc.']),
            ('outside_gs',['Other Inc.']),('scoped_empty',['Example Corp.','Other Inc.']),
            ('after_text',['Example Corp.','Other Inc.']),('nested',['Other Inc.'])]:
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind)
                self.assertEqual(current['parser_version'],CLIP_PARSER_VERSION)
                self.assertEqual(current['native_contrast_mode'],CLIP_MODE)
                self.assertEqual(current['transactions'],raw['transactions'])
                self.assertEqual(current['source_report_ledger']['logical_rows'],raw['source_report_ledger']['logical_rows'])
                q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual(sorted(t['asset_name'] for t in q['transactions']),names)
                self.assertEqual(len(q['transactions'])+len(q['quarantined']),2)
                self.assertFalse(q['coverage_complete']);self.assertEqual(q['qualification']['zero_transaction_document_count'],0)
                self.assertEqual(replay_native_contrast(json.loads(json.dumps(current)),root),current)
                self.assertEqual(qualify_automatic(json.loads(json.dumps(current)),IDENTITY,evidence_root=root),q)
                if q['quarantined']:self.assertTrue(q['quarantined'][0]['slot_refs'])

    def test_unsupported_clip_does_not_fall_back_to_old_native_facts(self):
        for kind in ('evenodd','negative','curve','uncommitted','transform','glyph_clip','all_glyph_clip'):
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind)
                self.assertTrue(plan_house_native_contrast(root,self.meta(root,current))['activated'])
                self.assertEqual(current['parser_version'],CLIP_PARSER_VERSION)
                q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual(q['transactions'],[]);self.assertEqual(len(q['quarantined']),2)
                self.assertEqual(current['source_report_ledger']['observed_physical_slot_count'],2)
                self.assertEqual(q['qualification']['zero_transaction_document_count'],0)

    def test_cross_page_white_or_hidden_account_members_are_not_an_unchanged_bypass(self):
        for kind in ('cross_white','cross_tr3'):
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind);q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual(current['parser_version'],CLIP_PARSER_VERSION)
                self.assertEqual([r['asset_name'] for r in q['transactions']],['Other Inc.'])
                self.assertEqual(len(q['quarantined']),1);self.assertEqual(len(q['quarantined'][0]['slot_refs']),2)
                self.assertEqual(current['transactions'],raw['transactions'])

    def test_account_and_missing_date_anchor_stay_original_risk_not_blank(self):
        for kind in ('account_outside','anchor_outside','second_anchor_outside'):
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind);q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual([r['asset_name'] for r in q['transactions']],
                    ['Example Corp.'] if kind=='second_anchor_outside' else ['Other Inc.'])
                self.assertEqual(len(current['transactions']),2);self.assertEqual(len(q['quarantined']),1)
                self.assertEqual(q['quarantined'][0]['extraction_id'],
                    raw['transactions'][1 if kind=='second_anchor_outside' else 0]['extraction_id'])
                self.assertTrue(q['quarantined'][0]['slot_refs']);self.assertTrue(q['native_contrast_dispositions'][1 if kind=='second_anchor_outside' else 0]['unavailable_word_members'])

    def test_clip_path_and_clipped_stroke_do_not_merge_the_raw_grid(self):
        root,raw,current=self.source('stroke_outside');q=qualify_automatic(current,IDENTITY,evidence_root=root)
        self.assertEqual(q['transactions'],[]);self.assertEqual(len(q['quarantined']),2)
        self.assertEqual(current['transactions'],raw['transactions'])
        self.assertEqual(current['source_word_pages'],raw['source_word_pages'])
        self.assertEqual(current['source_report_ledger']['observed_physical_slot_count'],2)
        self.assertEqual(current['source_report_ledger']['logical_rows'],raw['source_report_ledger']['logical_rows'])

    def test_clip_only_unknown_image_later_page_cannot_pollute_previous_row_with_header(self):
        root,raw,current=self.source('image_header_clip');q=qualify_automatic(current,IDENTITY,evidence_root=root)
        self.assertEqual([r['asset_name'] for r in q['transactions']],['Example Corp.'])
        self.assertEqual(current['transactions'][0]['extraction_id'],raw['transactions'][0]['extraction_id'])
        self.assertNotIn('Asset',current['transactions'][0]['raw_cells']['asset'])
        self.assertEqual(len(q['quarantined']),1);self.assertTrue(q['quarantined'][0]['slot_refs'])
        self.assertEqual(replay_native_contrast(current,root),current)

    def test_source_proof_roots_and_budget_cannot_be_signed_away(self):
        root,raw,current=self.source('outside')
        with self.assertRaises(HouseIndexError):qualify_automatic(current,IDENTITY)
        with self.assertRaises(HouseIndexError):qualify_automatic(raw,IDENTITY,evidence_root=root)
        bad=deepcopy(current);bad['native_contrast_observation']['pages'][0]['proof']['glyph_states'][0]='unknown'
        with self.assertRaises(HouseIndexError):qualify_automatic(bad,IDENTITY,evidence_root=root)
        for budget in ('MAX_DOCUMENT_CLIPPING_EVENTS','MAX_DOCUMENT_CLIPPING_PATH_POINTS'):
            with self.subTest(budget=budget),patch('unison_snapshot.house_native_contrast.'+budget,0):
                with self.assertRaises(HouseIndexError):plan_house_native_contrast(root,self.meta(root,current))
        pdf=root/current['source']['archive_path'];pdf.write_bytes(pdf.read_bytes()+b'\n% changed')
        with self.assertRaises(HouseIndexError):qualify_automatic(current,IDENTITY,evidence_root=root)

    def test_no_clip_output_and_explicit_old_versions_keep_their_meaning(self):
        root,raw,current=self.source('control')
        self.assertEqual(raw,current);self.assertFalse(plan_house_native_contrast(root,self.meta(root,current))['activated'])
        root,raw,current=self.source('inside_gs')
        import unison_snapshot.house_native_contrast as module
        metadata,path,content=module._load(root,self.meta(root,current))
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            old,_=module._build(metadata,pdf,module._inventory(pdf,metadata['sha256'],include_opacity=True),parser_version=OPACITY_PARSER_VERSION)
        self.assertEqual(replay_native_contrast(old,root),old)
        with self.assertRaises(HouseIndexError):qualify_automatic(old,IDENTITY,evidence_root=root)
        root,raw,current=self.source('white_outside')
        metadata,path,content=module._load(root,self.meta(root,current))
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            old_bichrome,_=module._build(metadata,pdf,module._inventory(pdf,metadata['sha256']))
        self.assertEqual(old_bichrome['parser_version'],PARSER_VERSION)
        self.assertEqual(replay_native_contrast(old_bichrome,root),old_bichrome)
        with self.assertRaises(HouseIndexError):qualify_automatic(old_bichrome,IDENTITY,evidence_root=root)
        root,raw,current=self.source('outside')
        # Old ordinary native observation is retained explicitly, but cannot
        # grant current facts once the actual source selects the clip family.
        self.assertNotEqual(raw['parser_version'],CLIP_PARSER_VERSION)
        with self.assertRaises(HouseIndexError):qualify_automatic(raw,IDENTITY,evidence_root=root)

    def test_only_unique_full_same_field_clipped_counterpart_aliases_once(self):
        for kind,names in [('outside_same',['Example Corp.','Other Inc.']),
                           ('outside_different',['Other Inc.']),('partial_same',['Other Inc.'])]:
            with self.subTest(kind=kind):
                root,raw,current=self.source(kind);q=qualify_automatic(current,IDENTITY,evidence_root=root)
                self.assertEqual(sorted(r['asset_name'] for r in q['transactions']),names)
                self.assertEqual(current['transactions'],raw['transactions'])
                self.assertEqual(len(q['transactions'])+len(q['quarantined']),2)
                if kind=='outside_same':
                    self.assertTrue(current['native_contrast_observation']['pages'][0]['word_aliases'])
                    self.assertEqual(next(r['id'] for r in q['transactions'] if r['asset_name']=='Example Corp.'),raw['transactions'][0]['extraction_id'])
                else:
                    self.assertTrue(q['quarantined'][0]['slot_refs'])
