"""Generated PDFs and fixed OCR: visibility routing, never recognition accuracy."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pdfplumber
import pymupdf

from test_house_ptr import fixture_pages, cross_page_fixture_pages, META, IDENTITY
import test_house_image_body_routing as image_tests
import test_house_mixed_pages as mixed_tests
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import (parse_archived_pdf, parse_word_pages, qualify_automatic,
    observe_native_word_pages, planned_ocr_pages)
from unison_snapshot.house_native_coverage import observe_native_geometry
from unison_snapshot.house_native_visibility import KEY, MODE, BLOCKER


class NativeVisibilityTests(unittest.TestCase):
    def simple_native(self, pages, render_mode, *, footer=False, form_body=False, clip_body=False):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        root = Path(folder.name); document = pymupdf.open()
        for number, data in enumerate(pages, 1):
            page = document.new_page(width=data['width'], height=data['height'])
            body = pymupdf.open(); body.new_page(width=data['width'], height=data['height'])
            for word in data['words']:
                target = body[0] if form_body and word['top'] >= 300 else page
                target.insert_text((word['x0'], word['top'] + word['size']), word['text'],
                    fontsize=word['size'], render_mode=render_mode(number, word))
            if form_body:
                page.show_pdf_page(page.rect, body, 0)
            body.close()
            if clip_body:
                content = b'\n'.join(document.xref_stream(ref) for ref in page.get_contents())
                ref = document.get_new_xref(); document.update_object(ref, '<<>>')
                document.update_stream(ref, b'q 0 0 612 792 re W n\n' + content + b'\nQ')
                page.set_contents(ref)
        if footer:
            document[-1].insert_text((40, 740), 'Unrelated invisible footer', fontsize=9, render_mode=3)
        raw = document.tobytes(); document.close(); sha = hashlib.sha256(raw).hexdigest()
        path = root/'source.pdf'; path.write_bytes(raw)
        metadata = {**META, 'sha256': sha, 'archive_path': 'source.pdf'}
        meta_path = root/'source.json'; meta_path.write_text(json.dumps(metadata))
        with pdfplumber.open(path) as pdf:
            old = [{'width': p.width, 'height': p.height,
                'words': p.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=['size']),
                'native_page_geometry': observe_native_geometry(p, sha, n)} for n, p in enumerate(pdf.pages, 1)]
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('must not OCR')):
            current = parse_archived_pdf(root, meta_path)
        return parse_word_pages(metadata, sha, old, copy_allowed=True), current

    def fixture(self, mode='hidden', *, zero=False):
        helper = image_tests.ImageBodyRoutingTests()
        self.addCleanup(helper.doCleanups)
        f = helper.fixture(logo=mode in {'no_image', 'same_page', 'same_page_banner', 'zero_statement'}, first_body=mode == 'zero_statement')
        document = pymupdf.open(f.pdf); page = document[-1]
        before = list(page.get_contents())
        rows = [w for w in fixture_pages()[0]['words'] if 300 < w['top'] < 393] if mode != 'zero_statement' else []
        if mode == 'form':
            body = pymupdf.open(); body.new_page(width=612, height=792)
            target = body[0]
        else:
            target = page
        for item in rows:
            text = 'Stale' if item['text'] == 'Example' else item['text']
            target.insert_text((item['x0'], item['top'] + item['size']), text,
                fontsize=item['size'], render_mode=0 if mode in {'visible', 'opacity', 'irrelevant'} or
                    (mode == 'mixed' and item['text'] == 'P') else 3,
                fill_opacity=0 if mode == 'opacity' else 1)
        if mode == 'form':
            page.show_pdf_page(page.rect, body, 0); body.close()
        if mode == 'cross':
            page.insert_text((15, 338), 'Crosses', fontsize=9, render_mode=3)
        if mode == 'zero_statement':
            page.insert_text((180, 337), 'Nothing to report', fontsize=10, render_mode=3)
        if mode.startswith('same_page'):
            for item in rows:
                page.insert_text((item['x0'], item['top'] + item['size'] + 130),
                    'Visible' if item['text'] == 'Example' else item['text'], fontsize=item['size'])
        if mode == 'same_page_banner':
            page.insert_text((30, 240), 'Unrelated hidden banner', fontsize=8, render_mode=3)
        if mode == 'clip':
            streams = page.get_contents()
            original = b'\n'.join(document.xref_stream(ref) for ref in before)
            new = b'\n'.join(document.xref_stream(ref) for ref in streams if ref not in before)
            ref = document.get_new_xref(); document.update_object(ref, '<<>>')
            document.update_stream(ref, original + b'\nq 0 0 612 792 re W n\n' + new + b'\nQ')
            page.set_contents(ref)
        if mode == 'irrelevant':
            kind, value = document.xref_get_key(page.xref, 'Resources')
            resource = int(value.split()[0])
            document.xref_set_key(resource, 'ExtGState', '<< /Unused << /Type /ExtGState /ca 0 >> >>')
            annotation = page.add_rect_annot(pymupdf.Rect(30, 730, 60, 750)); annotation.update()
        if zero:
            page.insert_text((180, 410), 'Nothing to report', fontsize=10)
        raw = document.tobytes(); document.close()
        sha = hashlib.sha256(raw).hexdigest()
        relative = Path(f'house_clerk/documents/2026/20000001/{sha}.pdf')
        path = f.root / relative; path.write_bytes(raw)
        metadata = {**f.metadata, 'sha256': sha, 'archive_path': relative.as_posix()}
        metadata_path = path.with_suffix('.json'); metadata_path.write_text(json.dumps(metadata))
        f.meta_path.unlink()  # The generated intermediate is not a second archived source version.
        with pdfplumber.open(path) as pdf:
            native = observe_native_word_pages(pdf, sha)
            old = [{'width': p.width, 'height': p.height,
                'words': p.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=['size']),
                'native_page_geometry': observe_native_geometry(p, sha, n)} for n, p in enumerate(pdf.pages, 1)]
        return SimpleNamespace(root=f.root, pdf=path, meta_path=metadata_path,
            metadata=metadata, sha=sha, pages=native, old_pages=old)

    def parse(self, f, **ocr_options):
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [image_tests.ImageBodyRoutingTests.ocr_page(**ocr_options)], 'fixed OCR')) as ocr:
            extraction = parse_archived_pdf(f.root, f.meta_path)
        return extraction, ocr

    def test_hidden_image_layer_routes_and_preserves_original_native_facts(self):
        f = self.fixture(); old = parse_word_pages(f.metadata, f.sha, f.old_pages, copy_allowed=True)
        x, ocr = self.parse(f)
        self.assertEqual(planned_ocr_pages(f.pages), [2])
        self.assertEqual(ocr.call_args.kwargs, {'page_numbers': [2], 'include_word_bounds': True})
        self.assertEqual(x['native_visibility_mode'], MODE)
        proof = x['source_word_pages'][1][KEY]
        self.assertEqual(proof['original_words'], f.old_pages[1]['words'])
        self.assertIn('Stale', json.dumps(proof['original_words']))
        self.assertEqual(proof['status'], 'image_body_hidden_layer_partitioned')
        self.assertIn(3, proof['source_render_modes'])
        self.assertEqual(x['transactions'][0]['raw_cells'], parse_word_pages(f.metadata, f.sha, [f.old_pages[0]], copy_allowed=True)['transactions'][0]['raw_cells'])
        q = qualify_automatic(x, IDENTITY)
        self.assertEqual({r['asset_name'] for r in q['transactions']}, {'Example Corp.', 'Raster Corp.'})
        self.assertNotIn('Stale', json.dumps(q['transactions']))
        self.assertEqual(q['transactions'][0], qualify_automatic(parse_word_pages(f.metadata, f.sha, [f.old_pages[0]], copy_allowed=True), IDENTITY)['transactions'][0])
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('saved replay cannot OCR')):
            self.assertEqual(qualify_automatic(json.loads(json.dumps(x)), IDENTITY), q)

    def test_no_image_cross_boundary_and_mixed_mode_never_qualify_stale_facts(self):
        from unison_snapshot.house_native_contrast import PARSER_VERSIONS
        for mode in ('no_image', 'cross', 'mixed', 'opacity', 'clip', 'form'):
            with self.subTest(mode=mode):
                f = self.fixture(mode); x, ocr = self.parse(f)
                q = qualify_automatic(x, IDENTITY, **({'evidence_root':f.root}
                    if x.get('parser_version') in PARSER_VERSIONS else {}))
                self.assertFalse(ocr.called)
                self.assertEqual(planned_ocr_pages(f.pages), [])
                self.assertEqual([r['asset_name'] for r in q['transactions']], ['Example Corp.'])
                self.assertTrue(q['quarantined'])
                self.assertTrue(any(BLOCKER in row['reasons'] for row in q['quarantined']))
                self.assertEqual(x['source_report_ledger']['pages'][1]['coverage'], 'unknown')
                self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)

    def test_unrelated_resources_annotations_and_visible_layers_keep_native_words_and_fields(self):
        for mode in ('visible', 'irrelevant'):
            with self.subTest(mode=mode):
                f = self.fixture(mode)
                self.assertNotIn(KEY, f.pages[1])
                self.assertEqual(f.pages, f.old_pages)
                expected = parse_word_pages(f.metadata, f.sha, f.old_pages, copy_allowed=True)
                x, ocr = self.parse(f)
                self.assertFalse(ocr.called)
                self.assertEqual(x, expected)
                self.assertEqual(qualify_automatic(x, IDENTITY), qualify_automatic(expected, IDENTITY))

    def test_located_hidden_risk_does_not_reject_an_unrelated_visible_row_on_the_same_page(self):
        for mode in ('same_page', 'same_page_banner'):
            with self.subTest(mode=mode):
                f = self.fixture(mode); x, ocr = self.parse(f); q = qualify_automatic(x, IDENTITY)
                self.assertFalse(ocr.called)
                self.assertEqual({row['asset_name'] for row in q['transactions']}, {'Example Corp.', 'Visible Corp.'})
                old = qualify_automatic(parse_word_pages(f.metadata, f.sha, f.old_pages, copy_allowed=True), IDENTITY)
                self.assertEqual(next(r for r in q['transactions'] if r['asset_name'] == 'Visible Corp.'),
                                 next(r for r in old['transactions'] if r['asset_name'] == 'Visible Corp.'))
                self.assertTrue(any(BLOCKER in row['reasons'] for row in q['quarantined']))
                # Existing saved inputs do not gain invented source-mode provenance.
                self.assertNotIn('native_visibility_mode', parse_word_pages(f.metadata, f.sha, f.old_pages, copy_allowed=True))

    def test_unrelated_hidden_footer_preserves_cross_page_continuation_but_hidden_leading_detail_blocks(self):
        for leading in (False, True):
            with self.subTest(leading=leading):
                previous, current = self.simple_native(cross_page_fixture_pages(),
                    lambda n, w: 3 if leading and n == 2 and w['top'] == 120 else 0, footer=not leading)
                old, new = qualify_automatic(previous, IDENTITY), qualify_automatic(current, IDENTITY)
                self.assertEqual([row['raw_cells'] for row in current['transactions']],
                                 [row['raw_cells'] for row in previous['transactions']])
                if leading:
                    self.assertEqual(len(new['transactions']), 1)
                    self.assertEqual(new['transactions'][0], old['transactions'][1])
                    self.assertTrue(any(BLOCKER in row['reasons'] for row in new['quarantined']))
                    self.assertEqual(current['transactions'][0]['filing_status'], 'New')
                else:
                    self.assertEqual(new['transactions'], old['transactions'])
                    self.assertEqual(new['quarantined'], old['quarantined'])

    def test_hidden_modern_title_or_filing_id_cannot_authenticate_visible_body_without_table_boxes(self):
        source = fixture_pages(); source[0]['words'] = [w for w in source[0]['words'] if w['top'] < 393]
        for hidden in ('title', 'id', 'footer'):
            with self.subTest(hidden=hidden):
                old, current = self.simple_native(source, lambda n, w: 3 if w['top'] == 40 and (
                    hidden == 'title' and w['x0'] < 480 or hidden == 'id' and w['x0'] >= 480) else 0,
                    footer=hidden == 'footer')
                previous, actual = qualify_automatic(old, IDENTITY), qualify_automatic(current, IDENTITY)
                if hidden == 'footer':
                    self.assertEqual(actual['transactions'], previous['transactions'])
                else:
                    self.assertTrue(previous['transactions'])
                    self.assertEqual(actual['transactions'], [])
                    self.assertIn('native_format_anchor_visibility_unknown', actual['quarantined'][0]['reasons'])

    def test_unrelated_hidden_footer_does_not_upgrade_normal_form_or_clip_tags_into_field_blockers(self):
        source = fixture_pages(); source[0]['words'] = [w for w in source[0]['words'] if w['top'] < 393]
        for options in ({'form_body': True}, {'clip_body': True}):
            with self.subTest(options=options):
                old, current = self.simple_native(source, lambda n, w: 0, footer=True, **options)
                previous, actual = qualify_automatic(old, IDENTITY), qualify_automatic(current, IDENTITY)
                self.assertTrue(previous['transactions'])
                self.assertEqual(actual['transactions'], previous['transactions'])
                self.assertEqual(actual['quarantined'], previous['quarantined'])

    def test_hidden_fields_with_zero_text_never_become_a_zero_document(self):
        f = self.fixture('no_image', zero=True); x, _ = self.parse(f)
        q = qualify_automatic(x, IDENTITY)
        self.assertIsNone(x.get('document_disposition'))
        self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)
        self.assertTrue(any(BLOCKER in row['reasons'] for row in q['quarantined']))
        f = self.fixture('zero_statement'); x, _ = self.parse(f); q = qualify_automatic(x, IDENTITY)
        self.assertEqual(q['transactions'], [])
        self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)
        self.assertFalse(q['qualification']['production_eligible'])
        self.assertEqual(x['source_report_ledger']['pages'][0]['coverage_reason'], BLOCKER)

    def test_low_or_empty_ocr_keeps_native_fact_and_hidden_layer_out_of_qualification(self):
        f = self.fixture()
        x, _ = self.parse(f, confidence=5)
        q = qualify_automatic(x, IDENTITY)
        self.assertEqual([r['asset_name'] for r in q['transactions']], ['Example Corp.'])
        self.assertIn('ocr_confidence_below_threshold', q['quarantined'][0]['reasons'])
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [{'width': 612., 'height': 792., 'words': []}], 'fixed OCR')):
            q = qualify_automatic(parse_archived_pdf(f.root, f.meta_path), IDENTITY)
        self.assertEqual([r['asset_name'] for r in q['transactions']], ['Example Corp.'])
        self.assertEqual(q['qualification']['zero_transaction_document_count'], 0)

    def test_saved_visibility_binding_and_projection_changes_are_rejected(self):
        f = self.fixture(); x, _ = self.parse(f)
        for mutation in ('source', 'page', 'size', 'mode', 'character', 'count', 'raw', 'region', 'status', 'method', 'missing_proof'):
            value = deepcopy(x); proof = value['source_word_pages'][1][KEY]
            if mutation == 'source': proof['source_sha256'] = 'b' * 64
            if mutation == 'page': proof['page_number'] = 1
            if mutation == 'size': proof['page_size'][1] += 1
            if mutation == 'mode': proof['source_render_modes'][0] = 3
            if mutation == 'character': proof['source_characters'][0]['text'] = 'Changed'
            if mutation == 'count': proof['source_render_modes'].pop()
            if mutation == 'raw': proof['original_words'].pop()
            if mutation == 'region': proof['region'][1] += 1
            if mutation == 'status': proof['status'] = 'unknown'
            if mutation == 'method': value['extraction_method'] = 'tesseract_ocr'
            if mutation == 'missing_proof': value['source_word_pages'][1].pop(KEY)
            proof['source_characters_sha256'] = hashlib.sha256(json.dumps([
                proof['source_characters'], proof['source_render_modes'], proof['source_character_visibility_flags'], proof['source_paint']],
                ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            value['source_word_pages_sha256'] = hashlib.sha256(json.dumps(value['source_word_pages'],
                ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(HouseIndexError):
                qualify_automatic(value, IDENTITY)

    def test_budget_or_failed_source_projection_blocks_instead_of_native_fallback(self):
        for failure in ('budget', 'projection'):
            f = self.fixture()
            target = 'unison_snapshot.house_native_visibility.MAX_CHARACTERS' if failure == 'budget' else \
                     'unison_snapshot.house_native_visibility._source_characters'
            options = {'new': 1} if failure == 'budget' else {'side_effect': ValueError('source_visibility_character_projection_differs')}
            with self.subTest(failure=failure), patch(target, **options):
                x, ocr = self.parse(f)
            q = qualify_automatic(x, IDENTITY)
            self.assertFalse(ocr.called)
            self.assertEqual(q['transactions'], [])
            self.assertTrue(all(BLOCKER in row['reasons'] for row in q['quarantined']))

    def test_normal_cli_candidate_status_builder_and_workflow_selector(self):
        from test_house_candidate import BASE
        from unison_snapshot.house_candidate import build_house_candidate
        from unison_snapshot.disclosure_status_house import build_house_status
        from unison_snapshot.builder import build
        f = self.fixture(); helper = mixed_tests.HouseMixedPageTests(); cli = helper.cli
        review = f.root / 'review'; relative = f.meta_path.relative_to(f.root / 'house_clerk/documents')
        xp = review / 'house_clerk/extractions' / relative; qp = review / 'house_clerk/qualifications' / relative
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', return_value=(
                [image_tests.ImageBodyRoutingTests.ocr_page()], 'fixed OCR')):
            cli(['parse-house-ptr', '--archive', f.root, '--metadata', f.meta_path, '--output', xp])
        identity = f.root / 'identity.json'; identity.write_text(json.dumps(IDENTITY))
        cli(['qualify-house-ptr', '--extraction', xp, '--identity', identity, '--output', qp])
        state = {'status': 'ok', 'run_at': '2026-09-18T12:00:00Z', 'counts': {'archived': 1, 'pending': 0, 'failed': 0}}
        status = review / 'status/house_clerk.json'; status.parent.mkdir(parents=True)
        status.write_text(json.dumps({'parser_commit': 'a'*40, 'pending_parse_count': 0, 'qualification_count': 1,
            'qualified_transaction_count': 2, 'quarantined_row_count': 0, 'evidence_count': 1,
            'extracted_count': 1, 'failure_count': 0}))
        with patch('unison_snapshot.house_ptr._ocr_pdf_pages', side_effect=AssertionError('no saved OCR')):
            candidate = build_house_candidate(review, state, deepcopy(BASE))
            self.assertEqual(build_house_candidate(review, state, deepcopy(BASE)), candidate)
        self.assertEqual(len(candidate['transactions']), 2)
        reports = build_house_status(f.root, review, candidate['transactions'], [])[0]['reports']
        self.assertTrue(any(report['qualified_transaction_count'] == 2 for report in reports))
        self.assertTrue(build(candidate, generated_at='2026-09-18T12:00:00Z', allow_production=True).manifest['snapshot_id'])
        script, env = helper.workflow_script('Detect pages requiring OCR in this batch')
        local = f.root / '.local'; local.mkdir()
        (local/'parse-batch.txt').write_text(str(f.meta_path) + '\n')
        output = f.root / 'github-output.txt'
        done = subprocess.run([sys.executable, '-c', script], cwd=f.root, env={**env,
            'EVIDENCE_ROOT': str(f.root), 'PAPER_DOCUMENT_ID': '', 'GITHUB_OUTPUT': str(output)},
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(output.read_text().strip(), 'needed=true')
        helper.test_old_failures_reenter_default_selection_and_current_failures_remain_bounded()
        helper.doCleanups()

    def test_current_workflow_retries_previous_visibility_failure_and_keeps_success_cache(self):
        from unison_snapshot.house_ptr import PARSER_RETRY_VERSION, LEGACY_PARSER_VERSION
        f = self.fixture(); helper = mixed_tests.HouseMixedPageTests()
        script, env = helper.workflow_script('Select unprocessed evidence')
        review = f.root / 'review'; relative = f.meta_path.relative_to(f.root/'house_clerk/documents')
        failure = review/'house_clerk/failures'/relative; failure.parent.mkdir(parents=True)
        extraction = review/'house_clerk/extractions'/relative
        env.update(EVIDENCE_ROOT=str(f.root), REVIEW_ROOT=str(review), PARSER_COMMIT='a'*40,
            LEGACY_PARSER_VERSION=LEGACY_PARSER_VERSION, PARSER_RETRY_VERSION=PARSER_RETRY_VERSION, BATCH_SIZE='25')
        for version, expected in (('house-parser-suite-2026-10-image-body', True),
                                  ('house-parser-suite-2026-11-native-visibility', True), (PARSER_RETRY_VERSION, False)):
            failure.write_text(json.dumps({'parser_version': version}))
            done = subprocess.run([sys.executable, '-c', script], cwd=f.root, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(bool((f.root/'.local/parse-batch.txt').read_text().strip()), expected)
        failure.unlink(); extraction.parent.mkdir(parents=True)
        extraction.write_text(json.dumps({'extraction_method': 'native_pdf_text', 'parser_version': 'old'}))
        done = subprocess.run([sys.executable, '-c', script], cwd=f.root, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((f.root/'.local/parse-batch.txt').read_text().strip())
