"""Source paint-order partition: generated PDFs, fixed composite OCR only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

import test_house_image_body_routing as image_tests
import test_house_mixed_pages as mixed_tests
from test_house_ptr import fixture_pages, IDENTITY
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_archived_pdf, parse_word_pages, qualify_automatic, observe_native_word_pages, planned_ocr_pages
from unison_snapshot.house_native_coverage import observe_native_geometry
from unison_snapshot.house_native_visibility import KEY, BLOCKER


class ImagePaintOrderTests(unittest.TestCase):
    def fixture(self, mode='earlier_native'):
        helper = image_tests.ImageBodyRoutingTests(); self.addCleanup(helper.doCleanups)
        f = helper.fixture(); document = pymupdf.open(f.pdf); page = document[1]
        image_refs = [ref for ref in page.get_contents() if re.search(rb'/\S+\s+Do\b', document.xref_stream(ref))]
        self.assertEqual(len(image_refs), 1)
        image_content = document.xref_stream(image_refs[0])
        png = document.extract_image(page.get_images(full=True)[0][0])['image']
        for word in fixture_pages()[0]['words']:
            if 300 < word['top'] < 393:
                page.insert_text((word['x0'], word['top'] + word['size']),
                    'Stale' if word['text'] == 'Example' else word['text'], fontsize=word['size'],
                    render_mode=3 if mode.startswith('hidden_') else 0)
        if mode == 'cross':
            page.insert_text((15, 337), 'Crossing', fontsize=9)
        ordinary = b'\n'.join(document.xref_stream(ref) for ref in page.get_contents() if ref not in image_refs)
        image_mode = mode.removeprefix('hidden_')
        if image_mode in ('image_opacity', 'image_clip'):
            if image_mode == 'image_opacity':
                _, resources = document.xref_get_key(page.xref, 'Resources')
                document.xref_set_key(int(resources.split()[0]), 'ExtGState', '<< /Risk << /Type /ExtGState /ca 0.5 >> >>')
                image_content = b'q /Risk gs\n' + image_content + b'\nQ'
            else:
                image_content = b'q 0 0 612 792 re W n\n' + image_content + b'\nQ'
        if mode == 'later_native':
            content = image_content + b'\n' + ordinary
        elif mode == 'image_form':
            content = ordinary
        else:
            content = ordinary + b'\n' + image_content
        ref = document.get_new_xref(); document.update_object(ref, '<<>>')
        document.update_stream(ref, content); page.set_contents(ref)
        if mode == 'mixed_order':
            page.insert_text((105, 340), 'Later', fontsize=9)
        if mode == 'duplicate':
            page.insert_image(pymupdf.Rect(22, 318, 576, 386), stream=png, keep_proportion=False)
        if mode == 'image_form':
            body = pymupdf.open(); body.new_page(width=612, height=792)
            body[0].insert_image(pymupdf.Rect(22, 318, 576, 386), stream=png, keep_proportion=False)
            page.show_pdf_page(page.rect, body, 0); body.close()
        raw = document.tobytes(); document.close(); sha = hashlib.sha256(raw).hexdigest()
        relative = Path(f'house_clerk/documents/2026/20000001/{sha}.pdf')
        target = f.root/relative; target.write_bytes(raw)
        metadata = {**f.metadata, 'sha256': sha, 'archive_path': relative.as_posix()}
        mp = target.with_suffix('.json'); mp.write_text(json.dumps(metadata)); f.meta_path.unlink()
        with pdfplumber.open(target) as pdf:
            pages = observe_native_word_pages(pdf, sha)
            old = [{'width': p.width, 'height': p.height,
                'words': p.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=['size']),
                'native_page_geometry': observe_native_geometry(p, sha, n)} for n, p in enumerate(pdf.pages, 1)]
        return SimpleNamespace(root=f.root, pdf=target, meta_path=mp, metadata=metadata,
            sha=sha, pages=pages, old_pages=old, original_bytes=raw)

    def parse(self, f, **options):
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [image_tests.ImageBodyRoutingTests.ocr_page(**options)], 'fixed composite OCR')) as ocr:
            result = parse_archived_pdf(f.root, f.meta_path)
        return result, ocr

    def test_earlier_native_body_routes_composite_ocr_and_keeps_raw_native_and_order_proof(self):
        f = self.fixture(); x, ocr = self.parse(f); q = qualify_automatic(x, IDENTITY)
        self.assertEqual(planned_ocr_pages(f.pages), [2])
        self.assertEqual(ocr.call_args.kwargs, {'page_numbers': [2], 'include_word_bounds': True})
        proof = x['source_word_pages'][1][KEY]
        self.assertEqual(proof['status'], 'image_body_earlier_native_partitioned')
        self.assertEqual(proof['original_words'], f.old_pages[1]['words'])
        self.assertTrue(all(mode == 0 for mode in proof['source_render_modes']))
        self.assertEqual({r['asset_name'] for r in q['transactions']}, {'Example Corp.', 'Raster Corp.'})
        previous = qualify_automatic(parse_word_pages(f.metadata, f.sha, [f.old_pages[0]], copy_allowed=True), IDENTITY)
        self.assertEqual(q['transactions'][0], previous['transactions'][0])
        self.assertNotIn('Stale', json.dumps(q['transactions']))
        self.assertIn('Stale', json.dumps(proof['original_words']))
        self.assertEqual(f.pdf.read_bytes(), f.original_bytes)
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('saved replay cannot OCR')):
            self.assertEqual(qualify_automatic(json.loads(json.dumps(x)), IDENTITY), q)

    def test_image_before_visible_native_preserves_entire_original_native_result(self):
        f = self.fixture('later_native')
        previous = parse_word_pages(f.metadata, f.sha, f.old_pages, copy_allowed=True)
        x, ocr = self.parse(f)
        self.assertFalse(ocr.called)
        self.assertEqual(planned_ocr_pages(f.pages), [])
        self.assertNotIn(KEY, f.pages[1])
        self.assertEqual(f.pages, f.old_pages)
        self.assertEqual(x, previous)
        self.assertEqual(qualify_automatic(x, IDENTITY), qualify_automatic(previous, IDENTITY))
        self.assertEqual(f.pdf.read_bytes(), f.original_bytes)

    def test_mixed_order_cross_boundary_duplicate_and_image_own_graphics_states_block_native_fallback(self):
        from unison_snapshot.house_native_contrast import PARSER_VERSIONS
        for mode in ('mixed_order', 'cross', 'duplicate', 'image_opacity', 'image_clip', 'image_form',
                     'hidden_image_opacity', 'hidden_image_clip'):
            with self.subTest(mode=mode):
                f = self.fixture(mode); x, ocr = self.parse(f)
                q = qualify_automatic(x, IDENTITY, **({'evidence_root':f.root}
                    if x.get('parser_version') in PARSER_VERSIONS else {}))
                self.assertFalse(ocr.called)
                self.assertEqual(planned_ocr_pages(f.pages), [])
                self.assertEqual(f.pages[1][KEY]['status'], 'unknown')
                self.assertEqual([r['asset_name'] for r in q['transactions']], ['Example Corp.'])
                self.assertTrue(any(BLOCKER in row['reasons'] for row in q['quarantined']))
                self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)
                self.assertEqual(f.pdf.read_bytes(), f.original_bytes)
                if mode.removeprefix('hidden_').startswith('image_'):
                    self.assertTrue(any(image['visibility_flags'] for image in f.pages[1][KEY]['source_paint']['images']))

    def test_paint_sequence_image_state_and_membership_tampering_fails_saved_replay(self):
        f = self.fixture(); x, _ = self.parse(f)
        for mutation in ('char_order', 'image_order', 'image_box', 'image_flags', 'count', 'missing_event'):
            value = deepcopy(x); proof = value['source_word_pages'][1][KEY]; paint = proof['source_paint']
            if mutation == 'char_order':
                paint['paint_event_count'] += 1
                paint['character_orders'][-1] = paint['paint_event_count']
            if mutation == 'image_order': paint['images'][0]['paint_order'] = 1
            if mutation == 'image_box': paint['images'][0]['box'][0] += 1
            if mutation == 'image_flags': paint['images'][0]['visibility_flags'] = ['source_visibility_active_clipping_unsupported']
            if mutation == 'count': paint['character_orders'].pop()
            if mutation == 'missing_event': paint['images'] = []
            proof['source_characters_sha256'] = hashlib.sha256(json.dumps([
                proof['source_characters'], proof['source_render_modes'], proof['source_character_visibility_flags'], paint],
                ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            value['source_word_pages_sha256'] = hashlib.sha256(json.dumps(value['source_word_pages'],
                ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(HouseIndexError):
                qualify_automatic(value, IDENTITY)

    def test_low_empty_and_failed_ocr_never_restore_earlier_native_strings_or_overwrite_prior_output(self):
        f = self.fixture(); x, _ = self.parse(f, confidence=5); q = qualify_automatic(x, IDENTITY)
        self.assertEqual([row['asset_name'] for row in q['transactions']], ['Example Corp.'])
        self.assertIn('ocr_confidence_below_threshold', q['quarantined'][0]['reasons'])
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [{'width': 612., 'height': 792., 'words': []}], 'fixed composite OCR')):
            q = qualify_automatic(parse_archived_pdf(f.root, f.meta_path), IDENTITY)
        self.assertEqual([row['asset_name'] for row in q['transactions']], ['Example Corp.'])
        output = f.root/'prior.json'; output.write_bytes(b'previous interpretation')
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=HouseIndexError('fixed failed OCR')):
            with self.assertRaises(SystemExit):
                mixed_tests.HouseMixedPageTests().cli(['parse-house-ptr', '--archive', f.root,
                    '--metadata', f.meta_path, '--output', output])
        self.assertEqual(output.read_bytes(), b'previous interpretation')
        self.assertEqual(f.pdf.read_bytes(), f.original_bytes)

    def test_normal_cli_candidate_status_builder_and_workflow_ocr_detection(self):
        from test_house_candidate import BASE
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.disclosure_status_house import build_house_status
        from unison_snapshot.builder import build
        f = self.fixture(); helper = mixed_tests.HouseMixedPageTests(); review = f.root/'review'
        relative = f.meta_path.relative_to(f.root/'house_clerk/documents')
        xp, qp = review/'house_clerk/extractions'/relative, review/'house_clerk/qualifications'/relative
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [image_tests.ImageBodyRoutingTests.ocr_page()], 'fixed composite OCR')):
            helper.cli(['parse-house-ptr', '--archive', f.root, '--metadata', f.meta_path, '--output', xp])
        identity = f.root/'identity.json'; identity.write_text(json.dumps(IDENTITY))
        helper.cli(['qualify-house-ptr', '--extraction', xp, '--identity', identity, '--output', qp])
        status = review/'status/house_clerk.json'; status.parent.mkdir(parents=True)
        status.write_text(json.dumps({'parser_commit': 'a'*40, 'pending_parse_count': 0, 'qualification_count': 1,
            'qualified_transaction_count': 2, 'quarantined_row_count': 0, 'evidence_count': 1,
            'extracted_count': 1, 'failure_count': 0}))
        state = {'status': 'ok', 'run_at': '2026-09-18T12:00:00Z', 'counts': {'archived': 1, 'pending': 0, 'failed': 0}}
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('no saved OCR')):
            candidate = build_house_candidate(review, state, deepcopy(BASE))
            self.assertEqual(build_house_candidate(review, state, deepcopy(BASE)), candidate)
        self.assertEqual(len(candidate['transactions']), 2)
        reports = build_house_status(f.root, review, candidate['transactions'], [])[0]['reports']
        self.assertEqual(reports[0]['qualified_transaction_count'], 2)
        self.assertTrue(build(candidate, generated_at='2026-09-18T12:00:00Z', allow_production=True).manifest['snapshot_id'])
        script, env = helper.workflow_script('Detect pages requiring OCR in this batch')
        (f.root/'.local').mkdir(); (f.root/'.local/parse-batch.txt').write_text(str(f.meta_path)+'\n')
        output = f.root/'github-output.txt'
        done = subprocess.run([sys.executable, '-c', script], cwd=f.root, env={**env,
            'EVIDENCE_ROOT': str(f.root), 'PAPER_DOCUMENT_ID': '', 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(output.read_text().strip(), 'needed=true')
        self.assertEqual(f.pdf.read_bytes(), f.original_bytes)
