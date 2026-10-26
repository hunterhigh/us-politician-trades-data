"""Native table header plus image body: generated PDFs, fixed OCR, no new recognition."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_house_ptr import fixture_pages, META, IDENTITY, word
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import (parse_archived_pdf, parse_word_pages, qualify_automatic,
    observe_native_word_pages, planned_ocr_pages)
from unison_snapshot.house_image_body_routing import METHOD, WORD_TAG, image_body_region


class ImageBodyRoutingTests(unittest.TestCase):
    def fixture(self, *, overlap=False, logo=False, missing_header=False, first_body=False, duplicate=False):
        import pymupdf
        from PIL import Image, ImageDraw
        import pdfplumber
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name); doc = pymupdf.open()
        first = doc.new_page(width=612, height=792)
        original = [w for w in fixture_pages()[0]['words'] if w['top'] < (300 if first_body else 393)]
        for w in original: first.insert_text((w['x0'], w['top'] + w['size']), w['text'], fontsize=w['size'])
        page = first if first_body else doc.new_page(width=612, height=792)
        edges = [22, 62, 101, 258, 323, 379, 443, 522, 576]
        labels = ['ID', 'Owner', 'Asset', 'Transaction Type', 'Date', 'Notification Date', 'Amount', 'Cap. Gains > $200?']
        for index, (left, right) in enumerate(zip(edges, edges[1:])):
            page.draw_rect((left, 274, right, 318))
            # PDF's re operator already closes its rectangle. Remove the
            # redundant closepath emitted by this generator so pdfminer keeps
            # the primitive as rect, matching the existing supported template.
            ref = page.get_contents()[-1]
            doc.update_stream(ref, doc.xref_stream(ref).replace(b're\nh\n', b're\n'))
            if not missing_header:
                page.insert_text((left + 2, 290), labels[index], fontsize=9 if labels[index] in {'Asset', 'Amount'} else 5)
        page.insert_text((40, 700), 'Generated footer', fontsize=9)
        image = Image.new('RGB', (600, 80), 'white')
        ImageDraw.Draw(image).text((8, 12), 'generated image-only transaction body', fill='black')
        png = io.BytesIO(); image.save(png, format='PNG')
        page.insert_image(pymupdf.Rect(22, 318, 576, 386) if not logo else pymupdf.Rect(30, 50, 70, 80),
                          stream=png.getvalue(), keep_proportion=False)
        if duplicate:
            page.insert_image(pymupdf.Rect(22, 318, 576, 386), stream=png.getvalue(), keep_proportion=False)
        if overlap: page.insert_text((105, 337), 'partial hidden text', fontsize=9)
        path = root/'source.pdf'; doc.save(path); doc.close()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = Path(f'house_clerk/documents/2026/20000001/{sha}.pdf')
        pdf_path = root/relative; pdf_path.parent.mkdir(parents=True); path.replace(pdf_path)
        metadata = dict(META, archive_path=relative.as_posix(), sha256=sha)
        metadata_path = pdf_path.with_suffix('.json'); metadata_path.write_text(json.dumps(metadata))
        with pdfplumber.open(pdf_path) as pdf:
            pages = observe_native_word_pages(pdf, sha)
        return SimpleNamespace(root=root, pdf=pdf_path, meta_path=metadata_path, metadata=metadata, sha=sha, pages=pages)

    @staticmethod
    def ocr_page(*, confidence=95, crossing=False, outside=True):
        words = [deepcopy(w) for w in fixture_pages()[0]['words'] if 300 < w['top'] < 393]
        for w in words:
            w['ocr_confidence'] = confidence
            if w['text'] == 'Example': w['text'] = 'Raster'
        if outside:
            words.append({**word('BogusNativeReplacement', 100, 180), 'ocr_confidence': 95})
        if crossing:
            words.append({**word('Straddles', 105, 312), 'bottom': 325, 'ocr_confidence': 95})
        for w in words:
            w.setdefault('x1', w['x0'] + max(3, len(w['text']) * 3))
            w.setdefault('bottom', w['top'] + w['size'])
        return {'width': 612., 'height': 792., 'words': words}

    def parse(self, fixture, **kwargs):
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=([self.ocr_page(**kwargs)], 'fixed OCR')) as ocr:
            result = parse_archived_pdf(fixture.root, fixture.meta_path)
        return result, ocr

    def test_real_pdf_image_object_and_native_header_plan_the_missing_body(self):
        f = self.fixture()
        self.assertTrue(f.pages[1]['words'])
        self.assertEqual(image_body_region(f.pages[1]), [22., 318., 576., 386.])
        self.assertEqual(planned_ocr_pages(f.pages), [2])
        previous = parse_word_pages(f.metadata, f.sha, f.pages, copy_allowed=True)
        x, ocr = self.parse(f)
        self.assertEqual(ocr.call_args.kwargs, {'page_numbers': [2], 'include_word_bounds': True})
        self.assertEqual(x['page_text_methods'], ['native_pdf_text', METHOD])
        self.assertEqual(x['transactions'][0]['extraction_id'], previous['transactions'][0]['extraction_id'])
        self.assertEqual(x['transactions'][0]['raw_cells'], previous['transactions'][0]['raw_cells'])
        self.assertEqual(x['transactions'][0]['ocr_source_pages'], [])
        self.assertEqual(qualify_automatic(x, IDENTITY)['transactions'][0],
                         qualify_automatic(previous, IDENTITY)['transactions'][0])
        self.assertEqual(x['source_word_pages'][0]['words'], f.pages[0]['words'])
        proof = x['source_word_pages'][1]['image_body_observation']
        self.assertEqual(proof['native_words'], f.pages[1]['words'])
        self.assertEqual({row['asset_name'] for row in qualify_automatic(x, IDENTITY)['transactions']},
                         {'Example Corp.', 'Raster Corp.'})
        self.assertNotIn('BogusNativeReplacement', json.dumps(x['transactions']))
        self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'], 'unknown')

    def test_same_page_native_cover_header_and_image_body_are_supported(self):
        f = self.fixture(first_body=True)
        self.assertEqual(planned_ocr_pages(f.pages), [1])
        x, _ = self.parse(f)
        self.assertEqual(x['page_text_methods'], [METHOD])
        self.assertEqual(qualify_automatic(x, IDENTITY)['transactions'][0]['asset_name'], 'Raster Corp.')

    def test_logos_missing_header_and_overlapping_native_layers_do_not_trigger_ocr(self):
        for options in ({'logo': True}, {'missing_header': True}, {'overlap': True}, {'duplicate': True}):
            with self.subTest(options=options):
                f = self.fixture(**options)
                self.assertEqual(planned_ocr_pages(f.pages), [])
                with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('not selected')):
                    x = parse_archived_pdf(f.root, f.meta_path)
                self.assertEqual(x['extraction_method'], 'native_pdf_text')
                self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'], 'unknown')

    def test_region_routing_retains_real_tsv_word_bounds_without_changing_old_ocr_contract(self):
        from unison_snapshot.house_ptr import _words_from_tesseract_tsv
        tsv = 'level\tleft\ttop\twidth\theight\tconf\ttext\n5\t10\t20\t30\t40\t95\tWord\n'
        legacy = _words_from_tesseract_tsv(tsv, points_per_pixel=.5)
        bounded = _words_from_tesseract_tsv(tsv, points_per_pixel=.5, include_word_bounds=True)
        self.assertNotIn('x1', legacy[0]); self.assertNotIn('bottom', legacy[0])
        self.assertEqual(bounded[0], {**legacy[0], 'x1': 20., 'bottom': 30.})
        with self.assertRaisesRegex(HouseIndexError, 'complete word bounds'):
            _words_from_tesseract_tsv('level\tleft\ttop\theight\tconf\ttext\n',
                                      points_per_pixel=1, include_word_bounds=True)

    def test_empty_image_ocr_keeps_the_source_region_unknown_and_native_rows(self):
        f = self.fixture()
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [{'width': 612., 'height': 792., 'words': []}], 'fixed OCR')):
            x = parse_archived_pdf(f.root, f.meta_path)
        q = qualify_automatic(x, IDENTITY)
        self.assertEqual(len(q['transactions']), 1)
        self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'], 'unknown')
        self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)

    def test_low_confidence_or_boundary_crossing_never_changes_native_facts(self):
        for options in ({'confidence': 5}, {'crossing': True}):
            with self.subTest(options=options):
                f = self.fixture()
                previous = qualify_automatic(parse_word_pages(f.metadata, f.sha, f.pages, copy_allowed=True), IDENTITY)
                x, _ = self.parse(f, **options)
                actual = qualify_automatic(x, IDENTITY)
                self.assertEqual(actual['transactions'], previous['transactions'])
                self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'], 'unknown')
                self.assertEqual(actual['qualification']['zero_transaction_document_count'], 0)
                if options.get('confidence'):
                    self.assertIn('ocr_confidence_below_threshold', actual['quarantined'][0]['reasons'])

    def test_saved_partitions_are_replayed_and_tampering_stops_consumption(self):
        f = self.fixture(); x, _ = self.parse(f)
        q = qualify_automatic(x, IDENTITY)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('saved replay must not OCR')):
            self.assertEqual(qualify_automatic(json.loads(json.dumps(x)), IDENTITY), q)
        for mutation in ('native', 'ocr', 'region', 'page', 'source', 'method', 'tag', 'merged'):
            value = deepcopy(x); page = value['source_word_pages'][1]; proof = page['image_body_observation']
            if mutation == 'native': proof['native_words'].pop()
            if mutation == 'ocr': proof['ocr_words'][0]['text'] = 'Changed'
            if mutation == 'region': proof['region'][1] += 1
            if mutation == 'page': page['native_page_geometry']['page_number'] = proof['page_number'] = 1
            if mutation == 'source': page['native_page_geometry']['source_sha256'] = proof['source_sha256'] = 'b'*64
            if mutation == 'method': value['page_text_methods'][1] = page['text_reading_method'] = 'native_pdf_text'
            if mutation == 'tag': next(w for w in page['words'] if w.get(WORD_TAG)).pop(WORD_TAG)
            if mutation == 'merged': page['words'].pop()
            # Rehashing the envelope cannot bypass partition reconstruction.
            value['source_word_pages_sha256'] = hashlib.sha256(json.dumps(value['source_word_pages'],
                ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(HouseIndexError): qualify_automatic(value, IDENTITY)

    def test_normal_cli_qualification_candidate_and_builder_reuse_the_saved_result(self):
        from test_house_candidate import BASE
        import test_house_mixed_pages
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.builder import build
        f = self.fixture(); review = f.root/'review'
        relative = f.meta_path.relative_to(f.root/'house_clerk/documents')
        extraction, qualification = review/'house_clerk/extractions'/relative, review/'house_clerk/qualifications'/relative
        cli = test_house_mixed_pages.HouseMixedPageTests().cli
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=([self.ocr_page()], 'fixed OCR')):
            cli(['parse-house-ptr', '--archive', f.root, '--metadata', f.meta_path, '--output', extraction])
        identity = f.root/'identity.json'; identity.write_text(json.dumps(IDENTITY))
        cli(['qualify-house-ptr', '--extraction', extraction, '--identity', identity, '--output', qualification])
        status = review/'status/house_clerk.json'; status.parent.mkdir(parents=True)
        status.write_text(json.dumps({'parser_commit': 'a'*40, 'pending_parse_count': 0, 'qualification_count': 1,
            'qualified_transaction_count': 2, 'quarantined_row_count': 0, 'evidence_count': 1, 'extracted_count': 1, 'failure_count': 0}))
        state = {'status': 'ok', 'run_at': '2026-09-18T12:00:00Z', 'counts': {'archived': 1, 'pending': 0, 'failed': 0}}
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('no replay OCR')):
            candidate = build_house_candidate(review, state, deepcopy(BASE))
            self.assertEqual(build_house_candidate(review, state, deepcopy(BASE)), candidate)
        self.assertEqual(len(candidate['transactions']), 2)
        self.assertTrue(build(candidate, generated_at='2026-09-18T12:00:00Z', allow_production=True).manifest['snapshot_id'])
