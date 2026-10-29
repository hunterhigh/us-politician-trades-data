"""Mixed-file native revision contracts, not independent OCR accuracy evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import test_house_automatic_revisions as revisions
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_word_pages, qualify_automatic, resolve_automatic_revisions
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.builder import build


def mixed(pair, *, ocr_body=False):
    qualification, extraction = pair
    pages = deepcopy(extraction['source_word_pages'])
    second = {'width': pages[0]['width'], 'height': pages[0]['height'], 'words': []}
    if ocr_body:
        second['words'] = [{**word, 'ocr_confidence': 95} for word in pages[0]['words'] if word['top'] >= 300]
        pages[0]['words'] = [word for word in pages[0]['words'] if word['top'] < 300]
    pages.append(second)
    result = parse_word_pages({**extraction['source'], 'filing_type': 'P'},
        extraction['source_sha256'], pages, copy_allowed=True, ocr_engine='fixed provenance contract',
        page_text_methods=['native_pdf_text', 'tesseract_ocr'])
    return qualify_automatic(result, qualification['identity']), result


def pure_ocr(pair):
    qualification, extraction = pair
    pages = deepcopy(extraction['source_word_pages'])
    for page in pages:
        for word in page['words']:
            word['ocr_confidence'] = 95
    result = parse_word_pages({**extraction['source'], 'filing_type': 'P'}, extraction['source_sha256'],
                             pages, copy_allowed=True, ocr_engine='fixed provenance contract')
    return qualify_automatic(result, qualification['identity']), result


class MixedRevisionTests(unittest.TestCase):
    def sources(self):
        return [revisions.source(),
                revisions.source('20000002', 'b'*64, 'Amended', '2026-09-15', high='$2,000,000')]

    def test_unrelated_ocr_page_does_not_disqualify_replayed_native_rows(self):
        original, amendment = self.sources()
        baseline = resolve_automatic_revisions([original, amendment], original[0]['transactions'])
        for change in ('original', 'amendment', 'both'):
            inputs = [mixed(original) if change != 'amendment' else original,
                      mixed(amendment) if change != 'original' else amendment]
            with self.subTest(change=change):
                for (_, updated), (_, previous) in zip(inputs, [original, amendment]):
                    self.assertEqual(updated['transactions'][0]['raw_cells'], previous['transactions'][0]['raw_cells'])
                    self.assertEqual(updated['transactions'][0]['extraction_id'], previous['transactions'][0]['extraction_id'])
                result = resolve_automatic_revisions(inputs, inputs[0][0]['transactions'])
                self.assertEqual(result['transactions'], baseline['transactions'])
                self.assertEqual(result['replacements'], baseline['replacements'])

    def test_any_ocr_derived_relevant_row_still_blocks_unique_native_binding(self):
        original, amendment = self.sources()
        for inputs in ([mixed(original, ocr_body=True), amendment],
                       [original, mixed(amendment, ocr_body=True)],
                       [original, amendment, mixed(revisions.source('20000003', 'c'*64), ocr_body=True)],
                       [original, amendment, pure_ocr(revisions.source('20000003', 'c'*64))]):
            facts = [row for q, _ in inputs for row in q['transactions']]
            self.assertTrue(any(x['extraction_method'] == 'tesseract_ocr' or
                                any(row.get('ocr_source_pages') for row in x['transactions']) for _, x in inputs))
            result = resolve_automatic_revisions(inputs, facts)
            self.assertEqual(result['replacements'], [])
            self.assertEqual(result['transactions'], sorted(facts, key=lambda row: row['id']))

    def test_native_row_provenance_is_replayed_before_binding(self):
        original, amendment = self.sources()
        inputs = [original, mixed(amendment, ocr_body=True)]
        tampered = deepcopy(inputs)
        tampered[1][1]['transactions'][0]['ocr_source_pages'] = []
        with self.assertRaises(HouseIndexError):
            resolve_automatic_revisions(tampered, original[0]['transactions'])
        third = pure_ocr(revisions.source('20000003', 'c'*64))
        third[1]['transactions'][0]['asset_name'] = 'tampered saved OCR'
        with self.assertRaises(HouseIndexError):
            resolve_automatic_revisions([original, amendment, third], original[0]['transactions'])
        tampered = deepcopy([original, mixed(amendment)])
        tampered[1][1]['source_word_pages'][0]['words'][0]['text'] = 'changed source'
        with self.assertRaises(HouseIndexError):
            resolve_automatic_revisions(tampered, original[0]['transactions'])

    def test_normal_candidate_status_builder_do_not_replace_from_unproved_mixed_caches(self):
        inputs = [mixed(pair) for pair in self.sources()]
        old = inputs[0][0]['transactions'][0]
        with tempfile.TemporaryDirectory() as folder:
            review, evidence, state = revisions.fixture(Path(folder), inputs)
            base = deepcopy(revisions.BASE); base['transactions'] = [old]
            candidate = build_house_candidate(review, state, base)
            self.assertEqual(len(candidate['transactions']), 1)
            self.assertEqual(candidate['transactions'], [old])
            self.assertEqual(build_house_candidate(review, state, candidate), candidate)
            reports = {row['document_id']: row for row in
                       build_house_status(evidence, review, candidate['transactions'], [])[0]['reports']}
            self.assertNotEqual(reports['20000001']['qualification_state'], 'superseded')
            self.assertEqual(reports['20000002']['qualified_transaction_count'], 0)
            self.assertEqual(reports['20000002']['quarantined_row_count'], 1)
            self.assertTrue(build(candidate, generated_at=candidate['meta']['data_cutoff_at'],
                                  allow_production=True).manifest['snapshot_id'])

    def test_existing_formal_difference_remains_blocking(self):
        inputs = [mixed(pair) for pair in self.sources()]
        old = deepcopy(inputs[0][0]['transactions'][0]); old['amount_high'] = 1_234_567
        result = resolve_automatic_revisions(inputs, inputs[0][0]['transactions'], prior_transactions=[old])
        self.assertFalse(result['replacements'])
        self.assertEqual(result['transactions'], [old])
