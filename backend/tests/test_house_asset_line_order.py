"""Generated source PDF and fixed OCR words isolate asset word-order loss."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_house_ptr_paper_qualification as paper_tests
from unison_snapshot.ocr_geometry import OcrPage
from unison_snapshot.house_ptr_cell_observation import observe_house_ptr_pdf
from unison_snapshot.house_asset_line_order import MODE, project_asset_line_reading
from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation


class HouseAssetLineOrderTests(unittest.TestCase):
    def fixture(self):
        import pymupdf
        import pdfplumber
        helper = paper_tests.HousePaperQualificationTests(); helper.setUp()
        doc = pymupdf.open(); page = doc.new_page(width=100, height=100)
        words = []
        for text, x0, top, x1, bottom, line in (
            ('SP', 1, 12, 8, 15, 1), ('Alpha', 12, 12, 24, 15, 1), ('Beta', 42, 12, 54, 15, 1),
            ('Holdings', 12, 16, 34, 19, 2), ('Class', 42, 16, 54, 19, 2),
            ('05/01/2026', 61, 12, 69, 15, 1), ('05/03/2026', 71, 12, 79, 15, 1)):
            page.insert_text((x0, bottom), text, fontsize=1 if '/' in text else 3)
            words.append(dict(text=text, x0=x0, top=top, x1=x1, bottom=bottom,
                ocr_confidence=95, block_num=1, par_num=1, line_num=line))
        raw = doc.tobytes(); doc.close(); sha = hashlib.sha256(raw).hexdigest()
        columns = {'status': 'located_shadow', 'owner': [0, .1], 'asset': [.1, .6], 'event': [.6, .7],
            'notification': [.7, .8], 'action': [.8, .85, .9], 'action_labels': ['purchase', 'sale'],
            'amount': [.9 + i * .1/11 for i in range(12)]}
        region = {'status': 'located_shadow', 'coordinate_frame': 'original_page', 'orientation_degrees': 0,
            'physical_row_bands': [{'band': [.1, .3], 'disposition': 'data_candidate'}]}
        def observe(mode):
            # Mark decisions and cover eligibility are established contracts;
            # only original asset word/crop geometry is the subject here.
            with patch('unison_snapshot.house_ptr_table_region.locate_transaction_grid', return_value=region), \
                 patch('unison_snapshot.house_ptr_cell_observation._column_boxes', return_value=columns), \
                 patch('unison_snapshot.house_ptr_cell_observation.ocr_pdf_pages',
                       return_value=([OcrPage(width=100, height=100, words=words)], 'fixed words')), \
                 patch('unison_snapshot.house_ptr._legacy_filing_status', return_value='New'):
                result = observe_house_ptr_pdf(raw, source_sha256=sha, asset_line_mode=mode)
            cells = result['pages'][0]['rows'][0]['cells']
            for index, cell in enumerate(cells['direction']):
                cell.pop('printed_checkbox', None); cell['interior_dark_fraction'] = .12 if index == 0 else .001
            for index, cell in enumerate(cells['amount']):
                cell.pop('printed_checkbox', None); cell['interior_dark_fraction'] = .13 if index == 0 else .001
            return result
        old, current = observe(None), observe(MODE)
        helper._source_bound_fixture()
        helper.sha = sha; helper.metadata['sha256'] = sha
        helper.metadata['archive_path'] = 'house_clerk/archive/source.pdf'
        helper.observation = current
        from unison_snapshot.house_ptr_amount_header import LABELS, evaluate_amount_header_pages
        header = evaluate_amount_header_pages(sha, [{'page': 1, 'status': 'header_ocr_observed',
            'raw_header_text': '\n'.join(['$1,000-$15,000', *LABELS.values()])}])
        cell = current['pages'][0]['rows'][0]['cells']['asset']
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            image = pdf.pages[0].to_image(resolution=300).original.convert('L')
        box = cell['bbox_normalized']; stream = io.BytesIO()
        image.crop((round(box[0]*image.width)+3, round(box[1]*image.height)+3,
                    round(box[2]*image.width)-3, round(box[3]*image.height)-3)).save(stream, format='PNG')
        reread = {'schema_version': 'house-paper-key-field-reread/v1', 'source_sha256': sha,
            'rule_version': 'house-paper-key-field-conflict-probe-v2', 'selected_cell_count': 1,
            'qualification_status': 'unverified_shadow_only', 'rereads': [{'page': 1,
                'physical_band_index': 1, 'field': 'asset', 'first_ocr_raw': cell['raw_text'],
                'first_crop_sha256': cell['crop_sha256'], 'second_ocr_raw': 'Alpha Beta Holdings Class',
                'second_crop_sha256': hashlib.sha256(stream.getvalue()).hexdigest(), 'second_crop_render_dpi': 300}]}
        return raw, helper, old, current, header, reread

    def materialize(self, raw, helper, observation, header, reread):
        return materialize_paper_observation(raw, observation, helper.metadata, helper.identity,
                                             amount_header=header, field_reread=reread)

    def test_same_words_in_disjoint_lines_reach_a_real_source_bound_fact(self):
        raw, helper, old, current, header, reread = self.fixture()
        old_cell = old['pages'][0]['rows'][0]['cells']['asset']
        new_cell = current['pages'][0]['rows'][0]['cells']['asset']
        self.assertEqual(old_cell['raw_text'], 'Alpha Holdings Beta Class')
        self.assertEqual(new_cell['raw_text'], old_cell['raw_text'])
        self.assertEqual(new_cell['crop_sha256'], old_cell['crop_sha256'])
        rejected = self.materialize(raw, helper, old, header, reread)
        self.assertFalse(rejected['transactions'])
        self.assertIn('asset_reading_conflict', rejected['quarantined'][0]['reasons'])
        actual = self.materialize(raw, helper, current, header, reread)
        self.assertEqual(actual['transactions'][0]['asset_name'], 'Alpha Beta Holdings Class')
        self.assertEqual(actual['transactions'][0]['id'], rejected['quarantined'][0]['extraction_id'])
        proof = actual['source_bound_asset_decisions'][0]
        self.assertEqual(proof['first_raw'], 'Alpha Holdings Beta Class')
        self.assertEqual(proof['second_raw'], 'Alpha Beta Holdings Class')
        self.assertEqual(len(proof['source_line_references']), 2)
        self.assertEqual(proof['source_page_word_indices'], new_cell['asset_line_word_indices'])

    def test_character_disagreement_or_secondary_old_order_does_not_use_the_order_proof(self):
        raw, helper, _, current, header, reread = self.fixture()
        for text in ('Alpba Beta Holdings Class', 'Alpha Holdings Beta Class'):
            changed = deepcopy(reread); changed['rereads'][0]['second_ocr_raw'] = text
            actual = self.materialize(raw, helper, current, header, changed)
            self.assertFalse(actual['transactions'])
            self.assertIn('asset_reading_conflict', actual['quarantined'][0]['reasons'])

    def test_source_membership_text_labels_page_and_mode_changes_cannot_self_certify(self):
        raw, helper, _, current, header, reread = self.fixture()
        for mutation in ('refs', 'text', 'labels', 'page', 'size', 'mode', 'digest', 'cell_frame', 'band'):
            changed = deepcopy(current); page = changed['pages'][0]
            source = page['asset_line_source']['source']
            if mutation == 'refs': page['rows'][0]['cells']['asset']['asset_line_word_indices'].pop()
            if mutation == 'text': source['words'][1]['text'] = 'Different'
            if mutation == 'labels': source['words'][1]['line_num'] = 8
            if mutation == 'page': source['page_number'] = 2
            if mutation == 'size': page['asset_line_source']['page_size_pdf_points'][0] = 101
            if mutation == 'mode': changed.pop('asset_line_mode')
            if mutation == 'digest': source['words_sha256'] = '0'*64
            if mutation == 'cell_frame': page['rows'][0]['cells']['asset']['bbox_points'][0] += 1
            if mutation == 'band': page['rows'][0]['band'][0] += .001
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.materialize(raw, helper, changed, header, reread)

    def test_ambiguous_word_boxes_and_crossing_producer_line_keep_conflict(self):
        from unison_snapshot.oge_ocr_line_order import prepare_line_source
        raw, helper, _, current, header, reread = self.fixture()
        for mutation in ('overlap', 'merged_lines', 'crosses_row', 'excluded_edge_word'):
            changed = deepcopy(current); page = changed['pages'][0]
            source = page['asset_line_source']['source']; words = source['words']
            if mutation == 'overlap':
                words[1]['x1'] = 46  # overlaps Beta on the same source line
            if mutation == 'merged_lines':
                for word in words: word['line_num'] = 1
            if mutation == 'crosses_row':
                words.append({**words[1], 'top': 31, 'bottom': 34})
            if mutation == 'excluded_edge_word':
                words.append({**words[1], 'x0': 59, 'x1': 65, 'line_num': 3})
            page['asset_line_source']['source'] = prepare_line_source(words,
                source_sha256=current['source_sha256'], page_number=1)
            actual = self.materialize(raw, helper, changed, header, reread)
            self.assertFalse(actual['transactions'])
            self.assertIn('asset_reading_conflict', actual['quarantined'][0]['reasons'])

    def test_normal_saved_batch_cli_materializes_and_candidate_keeps_existing_row_id(self):
        from unison_snapshot.__main__ import main
        from test_house_candidate import BASE
        from unison_snapshot.house_candidate import build_house_candidate
        raw, helper, _, current, header, reread = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); evidence, review = root/'evidence', root/'review'
            relative = Path('2026/9116141') / f'{helper.sha}.json'
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value), encoding='utf-8')
            meta_path = evidence/'house_clerk/documents'/relative; write(meta_path, helper.metadata)
            pdf = evidence/helper.metadata['archive_path']; pdf.parent.mkdir(parents=True); pdf.write_bytes(raw)
            for group, value in (('identities', helper.identity), ('paper_observations', current),
                    ('paper_amount_headers', header), ('paper_key_field_rereads', reread),
                    ('failures', {'schema_version': 'house-ptr-parse-failure/v1', 'source_sha256': helper.sha, 'document_id': '9116141'})):
                write(review/'house_clerk'/group/relative, value)
            batch = root/'batch.txt'; batch.write_text('')
            argv = ['snapshot', 'bridge-house-paper-batch', '--evidence-root', str(evidence), '--review-root', str(review),
                    '--parse-batch', str(batch), '--materialize-saved', '--tesseract', 'absent']
            with patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()) as output, \
                 patch('unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf', side_effect=AssertionError('saved must not OCR')):
                main()
            self.assertEqual(json.loads(output.getvalue()), {'materialized_existing_observation': 1})
            q = json.loads((review/'house_clerk/qualifications'/relative).read_bytes())
            self.assertEqual(q['transactions'][0]['asset_name'], 'Alpha Beta Holdings Class')
            write(review/'status/house_clerk.json', {'parser_commit': 'a'*40, 'pending_parse_count': 0,
                'qualification_count': 1, 'qualified_transaction_count': 1, 'quarantined_row_count': 0,
                'evidence_count': 1, 'extracted_count': 1, 'failure_count': 0})
            state = {'status': 'ok', 'run_at': '2026-06-11T00:00:00Z', 'counts': {'archived': 1, 'pending': 0, 'failed': 0}}
            candidate = build_house_candidate(review, state, deepcopy(BASE))
            self.assertEqual(candidate['transactions'][0]['id'], q['transactions'][0]['id'])
            self.assertEqual(candidate['transactions'][0]['asset_name'], 'Alpha Beta Holdings Class')
            self.assertEqual(build_house_candidate(review, state, deepcopy(BASE)), candidate)
