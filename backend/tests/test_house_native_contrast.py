"""Generated original PDFs exercise source replay and bounded House consumers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from test_house_ptr import fixture_pages, cross_page_fixture_pages, zero_transaction_pages, META, IDENTITY
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_archived_pdf, parse_word_pages, qualify_automatic
from unison_snapshot.house_native_contrast import (PARSER_VERSION, plan_house_native_contrast,
    replay_native_contrast)


class HouseNativeContrastTests(unittest.TestCase):
    def source(self, kind='control'):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        doc = pymupdf.open()
        cross = kind.startswith('cross_')
        data = zero_transaction_pages() if kind == 'white_zero' else cross_page_fixture_pages() if cross else fixture_pages()
        for number, entry in enumerate(data):
            p = doc.new_page(width=entry['width'], height=entry['height'])
            if not cross and kind in {'white_dark', 'black_dark'}:
                p.draw_rect(pymupdf.Rect(104, 324, 142, 338), color=None, fill=(0, 0, 0))
            for w in entry['words']:
                if kind=='white_zero' and w['top']==326:
                    continue
                white = (w['text'] == 'Example' and kind in {'white', 'white_dark', 'white_black'}) or (kind == 'white_zero' and w['top'] == 326)
                p.insert_text((w['x0'], w['top']+w['size']), w['text'], fontsize=w['size'],
                              color=(1, 1, 1) if white else (0, 0, 0))
            if kind=='white_zero':
                p.insert_text((180,335),'Nothing to report',fontsize=9,color=(1,1,1))
            if not cross:
                p.insert_text((105, 428), 'Filing Status: New', fontsize=8.5)
            if kind in {'erase', 'erase_black', 'erase_different', 'erase_shifted'}:
                p.draw_rect(pymupdf.Rect(104, 324, 142, 338), color=None, fill=(1, 1, 1))
            if kind in {'erase_black', 'erase_different', 'erase_shifted'}:
                p.insert_text((106 if kind=='erase_shifted' else 105,335),
                              'Other' if kind=='erase_different' else 'Example',fontsize=9)
            if kind in {'white_black', 'black_white'}:
                p.insert_text((105, 335), 'Example', fontsize=9,
                              color=(0, 0, 0) if kind == 'white_black' else (1, 1, 1))
            if kind in {'white_account', 'visible_account'}:
                p.insert_text((105, 374), 'Subholding Of: Investment account #7', fontsize=8.5,
                              color=(1, 1, 1) if kind == 'white_account' else (0, 0, 0))
            if kind == 'date_erased':
                p.draw_rect(pymupdf.Rect(325, 325, 380, 339), color=None, fill=(1, 1, 1))
            if kind in {'first_anchor_erased', 'second_anchor_erased'}:
                y = 325 if kind == 'first_anchor_erased' else 392
                p.draw_rect(pymupdf.Rect(258, y, 380, y+14), color=None, fill=(1, 1, 1))
            if cross and number == 1:
                p.insert_text((105, 158.5), 'Subholding Of: Investment account #7', fontsize=8.5,
                              color=(1, 1, 1) if kind == 'cross_white' else (0, 0, 0))
            if kind == 'unrelated_white_footer':
                p.insert_text((5, 740), 'UNRELATED', fontsize=8, color=(1, 1, 1))
        raw = doc.tobytes(); doc.close()
        sha = hashlib.sha256(raw).hexdigest()
        path = root/f'house_clerk/documents/2026/20000001/{sha}.pdf'
        path.parent.mkdir(parents=True); path.write_bytes(raw)
        meta = {**META, 'sha256': sha, 'archive_path': path.relative_to(root).as_posix(), 'byte_length':len(raw)}
        path.with_suffix('.json').write_text(json.dumps(meta), encoding='utf-8')
        from unison_snapshot.house_native_coverage import observe_native_geometry
        with pdfplumber.open(path) as pdf:
            pages = [{'width': p.width, 'height': p.height,
                'words': p.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=['size']),
                'native_page_geometry': observe_native_geometry(p, sha, i)} for i, p in enumerate(pdf.pages, 1)]
        raw_report = parse_word_pages(meta, sha, pages, copy_allowed=True)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('No new OCR')):
            report = parse_archived_pdf(root, path.with_suffix('.json'))
        return root, raw_report, report

    def meta(self, root, report):
        return (root/report['source']['archive_path']).with_suffix('.json')

    def test_plain_output_and_historical_parser_remain_identical(self):
        root, raw, report = self.source()
        self.assertEqual(report, raw)
        self.assertFalse(plan_house_native_contrast(root, self.meta(root, report))['activated'])
        self.assertNotEqual(report['parser_version'], PARSER_VERSION)
        self.assertEqual(qualify_automatic(report, IDENTITY), qualify_automatic(raw, IDENTITY))

    def test_monochrome_contrast_and_paint_order_preserve_independent_row(self):
        for kind, names in [('white', ['Other Inc.']), ('erase', ['Other Inc.']),
                            ('black_dark', ['Other Inc.']), ('white_dark', ['Example Corp.', 'Other Inc.']),
                            ('white_black', ['Example Corp.', 'Other Inc.']), ('black_white', ['Other Inc.']),
                            ('erase_black',['Example Corp.','Other Inc.']),
                            ('erase_different',['Other Inc.']),('erase_shifted',['Other Inc.'])]:
            with self.subTest(kind=kind):
                root, raw, report = self.source(kind)
                self.assertTrue(plan_house_native_contrast(root, self.meta(root, report))['activated'])
                self.assertEqual(report['parser_version'], PARSER_VERSION)
                self.assertEqual(report['transactions'], raw['transactions'])
                self.assertEqual(report['source_word_pages'], raw['source_word_pages'])
                self.assertEqual(report['source_report_ledger']['logical_rows'], raw['source_report_ledger']['logical_rows'])
                q = qualify_automatic(report, IDENTITY, evidence_root=root)
                self.assertEqual(sorted(t['asset_name'] for t in q['transactions']), names)
                other = next(t for t in q['transactions'] if t['asset_name'] == 'Other Inc.')
                self.assertEqual(other, next(t for t in qualify_automatic(raw, IDENTITY)['transactions'] if t['asset_name'] == 'Other Inc.'))
                self.assertFalse(q['coverage_complete'])
                self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)
                if kind in {'white_black','erase_black'}:
                    source_row = raw['transactions'][0]
                    self.assertEqual(source_row['asset_name'], 'Example Example Corp.')
                    self.assertEqual(next(t for t in q['transactions'] if t['asset_name'] == 'Example Corp.')['id'], source_row['extraction_id'])
                    self.assertTrue(any(p['word_aliases'] for p in report['native_contrast_observation']['pages']))
                saved = json.loads(json.dumps(report))
                self.assertEqual(replay_native_contrast(saved, root), saved)
                self.assertEqual(qualify_automatic(saved, IDENTITY, evidence_root=root), q)

    def test_missing_anchor_keeps_raw_slots_and_does_not_merge_good_row(self):
        for kind in ('date_erased', 'first_anchor_erased', 'second_anchor_erased'):
            with self.subTest(kind=kind):
                root, raw, report = self.source(kind)
                q = qualify_automatic(report, IDENTITY, evidence_root=root)
                self.assertEqual(len(report['transactions']), 2)
                self.assertEqual(len(q['transactions']), 1)
                self.assertEqual(len(q['quarantined']), 1)
                self.assertEqual(report['source_report_ledger']['observed_physical_slot_count'], 2)
                self.assertEqual(report['source_report_ledger']['logical_rows'], raw['source_report_ledger']['logical_rows'])
                good_name = 'Example Corp.' if kind == 'second_anchor_erased' else 'Other Inc.'
                self.assertEqual(q['transactions'][0], next(t for t in qualify_automatic(raw, IDENTITY)['transactions'] if t['asset_name'] == good_name))
                self.assertTrue(q['quarantined'][0]['slot_refs'])
                self.assertTrue(q['quarantined'][0]['raw_cells'])

    def test_account_notes_and_cross_page_members_are_not_dropped(self):
        for kind in ('white_account', 'cross_white'):
            with self.subTest(kind=kind):
                root, raw, report = self.source(kind)
                q = qualify_automatic(report, IDENTITY, evidence_root=root)
                self.assertEqual([t['asset_name'] for t in q['transactions']], ['Other Inc.'])
                self.assertEqual(len(q['quarantined']), 1)
                self.assertEqual(q['quarantined'][0]['extraction_id'], raw['transactions'][0]['extraction_id'])
                self.assertEqual(len(q['quarantined'][0]['slot_refs']), 2 if kind == 'cross_white' else 1)
                self.assertEqual(report['transactions'][0]['subholding_of'], 'Investment account #7')
        root, _, report = self.source('cross_visible')
        # No bichrome mechanism: ordinary visible note keeps the old route.
        self.assertEqual(len(qualify_automatic(report, IDENTITY)['transactions']), 2)

    def test_unrelated_white_footer_does_not_block_financial_rows(self):
        root, raw, report = self.source('unrelated_white_footer')
        q = qualify_automatic(report, IDENTITY, evidence_root=root)
        self.assertEqual(q['transactions'], qualify_automatic(raw, IDENTITY)['transactions'])
        self.assertEqual(len(q['transactions']), 2)
        self.assertFalse(q['coverage_complete'])

    def test_new_saved_source_requires_root_and_rejects_resigned_json(self):
        root, _, report = self.source('white')
        with self.assertRaisesRegex(HouseIndexError, 'evidence root'):
            qualify_automatic(report, IDENTITY)
        variants = []
        forged = deepcopy(report); forged['native_contrast_dispositions'][0]['status'] = 'current_projected'; variants.append(forged)
        forged = deepcopy(report); forged['transactions'][0]['asset_name'] = 'Forged'; variants.append(forged)
        forged = deepcopy(report)
        forged['native_contrast_observation']['pages'][0]['word_aliases'] = {'7': 8}
        from unison_snapshot.house_native_contrast import _digest
        forged['native_contrast_binding']['observation_sha256'] = _digest(forged['native_contrast_observation']); variants.append(forged)
        for forged in variants:
            with self.subTest(forged=variants.index(forged)), self.assertRaises(HouseIndexError):
                qualify_automatic(forged, IDENTITY, evidence_root=root)
        pdf = root/report['source']['archive_path']
        original = pdf.read_bytes()
        pdf.write_bytes(original+b'changed')
        with self.assertRaises(HouseIndexError):
            replay_native_contrast(report, root)
        pdf.write_bytes(original)
        path = self.meta(root, report)
        meta = json.loads(path.read_text(encoding='utf-8')); meta['filer_name'] = 'Different'
        path.write_text(json.dumps(meta), encoding='utf-8')
        with self.assertRaises(HouseIndexError):
            replay_native_contrast(report, root)

    def test_budgets_stop_before_ordinary_word_geometry_materialization(self):
        root, _, report = self.source('white')
        import unison_snapshot.house_native_contrast as module
        with patch('unison_snapshot.house_native_visibility._source_characters', side_effect=ValueError('source_visibility_token_budget_exceeded')):
            with patch('pdfplumber.page.Page.extract_words', side_effect=AssertionError('unbounded words')):
                with self.assertRaises(HouseIndexError):
                    module.plan_house_native_contrast(root, self.meta(root, report))
        with patch.object(module, 'MAX_TOTAL_CHARACTERS', 1), self.assertRaises(HouseIndexError):
            module.plan_house_native_contrast(root, self.meta(root, report))
        with patch.object(module, 'MAX_ROW_WORK', 1), self.assertRaises(HouseIndexError):
            module.parse_selected_native_contrast(root, self.meta(root, report), required=True)
        with patch.object(module, 'MAX_PROOF_BYTES', 16), self.assertRaises(HouseIndexError):
            module.parse_selected_native_contrast(root, self.meta(root, report), required=True)
        with patch.object(module, 'MAX_PROOF_BYTES', 5), self.assertRaises(HouseIndexError):
            module._json({'x':'中'})

    def test_new_archive_identity_paths_and_length_cannot_be_resigned(self):
        root, _, report = self.source('white')
        path = self.meta(root, report); original = path.read_bytes(); metadata = json.loads(original)
        from unison_snapshot.house_native_contrast import _digest
        for key, value in [('source_url','https://example.invalid/20000001.pdf'),
                           ('filing_year',2025),('byte_length',metadata['byte_length']+1)]:
            with self.subTest(key=key):
                changed={**metadata,key:value};path.write_text(json.dumps(changed),encoding='utf-8')
                forged=deepcopy(report);forged['native_contrast_binding']['metadata_sha256']=_digest(changed)
                if key in forged['source']:forged['source'][key]=value
                with self.assertRaises(HouseIndexError):plan_house_native_contrast(root,path)
                with self.assertRaises(HouseIndexError):qualify_automatic(forged,IDENTITY,evidence_root=root)
        path.write_bytes(original)
        alias=root/'alias.json';alias.write_bytes(original)
        with self.assertRaises(HouseIndexError):plan_house_native_contrast(root,alias)

    def test_unavailable_legacy_observer_retains_only_explicit_unknown_not_facts(self):
        root, _, report = self.source('white')
        with patch('unison_snapshot.house_native_visibility._source_characters', side_effect=ValueError('source_visibility_character_projection_differs')):
            with self.assertRaises(HouseIndexError):plan_house_native_contrast(root,self.meta(root,report))
            pending=parse_archived_pdf(root,self.meta(root,report))
        q=qualify_automatic(pending,IDENTITY)
        self.assertEqual(q['transactions'],[])
        self.assertEqual(q['qualification']['zero_transaction_document_count'],0)
        self.assertEqual(len(pending['transactions']),2)
        self.assertTrue(all(p['native_visibility_observation']['status']=='unknown' for p in pending['source_word_pages']))

    def test_invisible_zero_statement_does_not_authorize_no_transactions(self):
        root, raw, report = self.source('white_zero')
        self.assertEqual(raw['document_disposition']['status'],'explicit_no_transactions')
        self.assertEqual(report['parser_version'],PARSER_VERSION)
        q=qualify_automatic(report,IDENTITY,evidence_root=root)
        self.assertEqual(q['transactions'],[])
        self.assertEqual(q['qualification']['zero_transaction_document_count'],0)
        self.assertFalse(q['qualification']['production_eligible'])
        self.assertFalse(q['coverage_complete'])

    def test_rooted_qualification_does_not_use_old_cache_on_selected_source(self):
        root, raw, _ = self.source('white')
        # Explicit historical pure calls retain their old cache semantics.
        self.assertEqual(len(qualify_automatic(raw,IDENTITY)['transactions']),2)
        with self.assertRaisesRegex(HouseIndexError,'selected source version'):
            qualify_automatic(raw,IDENTITY,evidence_root=root)


if __name__ == '__main__':
    unittest.main()
