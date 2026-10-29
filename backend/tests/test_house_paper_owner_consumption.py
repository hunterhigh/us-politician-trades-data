"""Saved paper owner correction through real source and normal consumers.

The printed PDF and saved words are generated mechanism controls, not historical
production predecessors or OCR accuracy gold. Source grids, blank cells, crops,
legacy qualification and the normal bridge are exercised without a recognizer.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_house_candidate import BASE
from test_house_paper_owner_correction import paper_owner_fixture
from test_public_repo import FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.codec import encode
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_candidate import _person, build_house_candidate
from unison_snapshot.house_paper_pipeline import bridge_paper_report
from unison_snapshot.public_repo import PublicSnapshotRepository


CUTOFF = '2026-10-28T00:00:00Z'
PROFILE = 'source_bound_paper_owner_correction'
ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


class HousePaperOwnerConsumptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate only one source/observation set. Each test gets an independent
        # controlled tree containing the same complete, original-bound objects.
        cls.seed = tempfile.TemporaryDirectory(prefix='paper-owner-source-')
        cls.addClassCleanup(cls.seed.cleanup)
        data = paper_owner_fixture(Path(cls.seed.name))
        cls.original = {key: deepcopy(data[key]) for key in (
            'raw', 'metadata', 'identity', 'observation', 'amount_header',
            'field_reread', 'legacy_extraction', 'legacy_qualification')}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='paper-owner-consumer-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evidence, self.review = self.root / 'evidence', self.root / 'review'
        self.data = deepcopy(self.original)
        self.metadata = self.data['metadata']
        self.relative = Path(str(self.metadata['filing_year'])) / self.metadata['document_id'] / (
            self.metadata['sha256'] + '.json')
        self.source = self.evidence / self.metadata['archive_path']
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(self.data['raw'])
        self.metadata_path = self.source.with_suffix('.json')
        write(self.metadata_path, self.metadata)
        for folder, key in (
                ('identities', 'identity'), ('extractions', 'legacy_extraction'),
                ('qualifications', 'legacy_qualification'), ('paper_observations', 'observation'),
                ('paper_amount_headers', 'amount_header'), ('paper_key_field_rereads', 'field_reread')):
            write(self.artifact(folder), self.data[key])
        self.base = deepcopy(BASE)
        self.base['meta']['data_cutoff_at'] = CUTOFF
        self.base['people'] = [_person(self.data['identity'])]
        self.base['transactions'] = deepcopy(self.data['legacy_qualification']['transactions'])
        self.summary()

    def artifact(self, folder):
        return self.review / 'house_clerk' / folder / self.relative

    def read(self, folder):
        return json.loads(self.artifact(folder).read_bytes())

    def summary(self):
        qualification = self.read('qualifications')
        write(self.review / 'status/summary.json', {
            'parser_commit': 'a' * 40, 'pending_parse_count': 0, 'qualification_count': 1,
            'qualified_transaction_count': len(qualification['transactions']),
            'quarantined_row_count': len(qualification['quarantined']),
            'evidence_count': 1, 'extracted_count': 1, 'failure_count': 0,
        })
        self.state = {'status': 'ok', 'run_at': CUTOFF,
                      'filing_year': self.metadata['filing_year'],
                      'counts': {'archived': 1, 'pending': 0, 'failed': 0}}

    def bridge(self):
        # Any accidental acquisition or OCR is a test failure, not a fallback.
        with (patch('unison_snapshot.house_ptr_cell_observation_shadow.ocr_pdf_pages',
                    side_effect=AssertionError('No OCR in saved owner consumption')),
              patch('unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells',
                    side_effect=AssertionError('No new secondary read in saved consumption'))):
            result = bridge_paper_report(evidence_root=self.evidence, review_root=self.review,
                metadata_path=self.metadata_path, tesseract='NO_OCR_ALLOWED',
                observe_new=False, materialize_saved=True)
        self.summary()
        return result

    def candidate(self, base=None):
        return build_house_candidate(self.review, self.state,
            deepcopy(self.base if base is None else base), evidence_root=self.evidence)

    def cli(self, *arguments):
        result = subprocess.run([sys.executable, '-m', 'unison_snapshot', *map(str, arguments)],
            cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT/'backend/src'),
                           'PYTHONIOENCODING': 'utf-8'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def status(self, candidate):
        return build_house_status(self.evidence, self.review, candidate['transactions'], [])

    def test_real_saved_bridge_preserves_complete_formal_representation_and_person_then_repeats(self):
        # These are existing supported shard/enrichment representations, not
        # additional source fields or new evidence for an owner correction.
        first, second = self.base['transactions']
        for key in ('ticker', 'ticker_mapping_basis', 'position_effect_basis'):
            self.assertIsNone(first.pop(key))
        second.update(ticker='EXM', ticker_mapping_basis='issuer_name_mapping')
        person = self.base['people'][0]
        person.update(portrait_url='https://example.invalid/retained-portrait.png',
                      short_name='Retained short name')
        prior = deepcopy(self.base)
        original_pdf = self.source.read_bytes()
        original_inputs = {folder: self.artifact(folder).read_bytes() for folder in (
            'paper_observations', 'paper_amount_headers', 'paper_key_field_rereads')}
        batch = self.root/'parse-batch.txt'; batch.write_text('', encoding='utf-8')
        result = self.cli('bridge-house-paper-batch', '--evidence-root', self.evidence,
            '--review-root', self.review, '--parse-batch', batch,
            '--tesseract', 'NO_OCR_ALLOWED', '--materialize-saved')
        self.assertEqual(json.loads(result.stdout), {'materialized_existing_observation': 1})
        self.summary()
        qualification = self.read('qualifications')
        self.assertIn(PROFILE, qualification)
        self.assertEqual(len(qualification['transactions']), 2)
        write(self.root/'base.json', self.base); write(self.root/'state.json', self.state)
        self.cli('build-house-candidate', '--evidence-root', self.evidence,
            '--review-root', self.review, '--state-status', self.root/'state.json',
            '--base', self.root/'base.json', '--output', self.root/'candidate.json')
        candidate = json.loads((self.root/'candidate.json').read_bytes())
        expected = sorted([{**row, 'owner': 'Undisclosed'} for row in prior['transactions']],
                          key=lambda row: row['id'])
        self.assertEqual(candidate['transactions'], expected)
        self.assertEqual(candidate['people'], prior['people'])
        physical_ids = {row['extraction_id'] for row in self.read('extractions')['transactions']}
        self.assertTrue(physical_ids.isdisjoint(row['id'] for row in expected))
        scopes = self.status(candidate)
        report = next(row for scope in scopes for row in scope['reports']
                      if row['document_id'] == self.metadata['document_id'])
        self.assertEqual(report['qualified_transaction_count'], 2)
        self.assertEqual(set(report['publication_transaction_ids']), {row['id'] for row in expected})
        self.assertNotEqual(report['qualification_state'], 'zero_transactions')
        bundle = build(candidate, generated_at=CUTOFF, allow_production=True)
        bundle = attach_disclosure_status(bundle, scopes, transactions=candidate['transactions'],
            provenance={key: value * 40 for key, value in (
                ('code_commit', 'a'), ('evidence_commit', 'b'),
                ('review_commit', 'c'), ('previous_main_commit', 'd'))})
        public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'], candidate['transactions'])
        self.assertTrue(public.disclosure_status(public.resolve(), include_records=True))
        self.bridge()
        self.assertEqual(self.read('qualifications'), qualification)
        self.assertEqual(self.candidate(candidate)['transactions'], expected)
        self.assertEqual(self.source.read_bytes(), original_pdf)
        self.assertTrue(all(self.artifact(folder).read_bytes() == raw
                            for folder, raw in original_inputs.items()))
        self.assertEqual(self.base, prior)

    def test_wrong_original_or_changed_profile_refuses_consumption_without_rewriting_saved_outputs(self):
        self.bridge()
        qualification = self.read('qualifications')
        saved = {folder: self.artifact(folder).read_bytes() for folder in ('extractions', 'qualifications')}
        source = self.source.read_bytes()
        self.source.write_bytes(source + b'\n% changed controlled original\n')
        try:
            with self.assertRaises((HouseIndexError, ValueError)):
                self.candidate()
            with self.assertRaises((HouseIndexError, ValueError)):
                self.status({'transactions': qualification['transactions']})
            self.assertTrue(all(self.artifact(folder).read_bytes() == raw for folder, raw in saved.items()))
        finally:
            self.source.write_bytes(source)
        changed = deepcopy(qualification)
        changed[PROFILE]['id_mappings'][0]['physical_band_index'] = 999
        write(self.artifact('qualifications'), changed)
        with self.assertRaises((HouseIndexError, ValueError)):
            self.candidate()
        with self.assertRaises((HouseIndexError, ValueError)):
            self.status({'transactions': qualification['transactions']})

    def test_actual_nonnull_formal_difference_is_not_hidden_by_owner_correction(self):
        self.bridge()
        for field, value in (('amount_low', 1002), ('asset_name', 'Different source asset'),
                             ('instrument_type', 'Bond')):
            with self.subTest(field=field):
                prior = deepcopy(self.base)
                prior['transactions'][0][field] = value
                before = deepcopy(prior)
                with self.assertRaises(HouseIndexError):
                    self.candidate(prior)
                self.assertEqual(prior, before)

    def test_erasing_profile_cannot_make_legacy_ids_current_under_a_physical_extraction(self):
        self.bridge()
        qualification = self.read('qualifications')
        changed = deepcopy(qualification)
        changed.pop(PROFILE)
        write(self.artifact('qualifications'), changed)
        # The physical extraction and old legacy IDs require the original-bound
        # mapping. Removing it must fail rather than grant generic saved trust.
        with self.assertRaises((HouseIndexError, ValueError)):
            self.candidate()
        with self.assertRaises((HouseIndexError, ValueError)):
            self.status({'transactions': qualification['transactions']})


if __name__ == '__main__':
    unittest.main()
