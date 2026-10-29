"""Fixed Git/normal publication replay, not official OCR accuracy evidence.

The source factory supplies fixed complete saved words for generated print.
All source crops, blank-owner checks, legacy parsing, qualification, unique
physical mapping and publication verification below use their real consumers.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_house_candidate import BASE
from test_house_paper_owner_correction import paper_owner_fixture, prepare
from unison_snapshot.house_candidate import _person
from unison_snapshot.house_paper_owner_correction import PROFILE_KEY
from unison_snapshot.fact_policy.house_paper_owner_correction import verified_house_paper_owner_corrections
from unison_snapshot.fact_policy.source_objects import InventoryError


MAIN, REVIEW, EVIDENCE = 'a' * 40, 'b' * 40, 'c' * 40


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else encoded(value))


def publication_fixture(root):
    """Reusable real source plus canonical review bytes and complete boards."""
    data = paper_owner_fixture(root)
    proof = prepare(data)
    if proof is None:
        raise AssertionError('generated control must produce the existing source-bound owner proof')
    qualification = proof['final_qualification']
    meta = data['metadata']
    relative = f"{meta['filing_year']}/{meta['document_id']}/{meta['sha256']}.json"
    objects = {'extractions': qualification['materialized_extraction'],
        'qualifications': qualification, 'identities': data['identity'],
        'paper_observations': data['observation'], 'paper_amount_headers': data['amount_header'],
        'paper_key_field_rereads': data['field_reread']}
    review = {f'house_clerk/{folder}/{relative}': encoded(value) for folder, value in objects.items()}
    reference = qualification['legacy_extraction_reference']
    review[reference['path']] = encoded(data['legacy_extraction'])
    evidence = {meta['archive_path']: data['raw'],
                meta['archive_path'][:-4] + '.json': encoded(meta)}
    previous = deepcopy(BASE)
    previous['people'] = [_person(data['identity'])]
    previous['transactions'] = deepcopy(data['legacy_qualification']['transactions'])
    candidate = deepcopy(previous)
    candidate['transactions'] = deepcopy(qualification['transactions'])
    return {**data, 'proof': proof, 'qualification': qualification,
        'review': review, 'evidence': evidence, 'previous': previous, 'candidate': candidate,
        'qualification_path': f'house_clerk/qualifications/{relative}',
        'observation_path': f'house_clerk/paper_observations/{relative}'}


class HousePaperOwnerPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.data = publication_fixture(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def verify(self, data=None, **overrides):
        data = deepcopy(self.data if data is None else data)
        previous, candidate = data['previous'], data['candidate']
        raw = encoded(previous)
        sha = hashlib.sha256(raw).hexdigest()
        maps = {MAIN: {'manifest.json': encoded({'is_demo': False, 'board': sha}),
                       f'board/{sha}.json': raw}, REVIEW: data['review'], EVIDENCE: data['evidence']}

        def paths(repo, ref, directory):
            prefix = directory + '/'
            return sorted(path[len(prefix):] for path in maps[ref]
                          if path.startswith(prefix) and '/' not in path[len(prefix):])

        kwargs = dict(old={row['id']: row for row in previous['transactions']},
            current={row['id']: row for row in candidate['transactions']}, repo='fixed-input-contract',
            evidence_commit=EVIDENCE, review_commit=REVIEW, main_commit=MAIN,
            previous_people=previous['people'],
            read_object=lambda repo, ref, path: maps[ref][path], list_paths=paths)
        kwargs.update(overrides)
        return verified_house_paper_owner_corrections(**kwargs)

    def test_real_git_normal_publication_auto_explicit_repeat_and_fixed_ref_failure_is_atomic(self):
        data = self.data
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); repo = root / 'repo'; repo.mkdir()
            env = dict(os.environ, GIT_NO_LAZY_FETCH='1', PYTHONIOENCODING='utf-8')

            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env,
                    stderr=subprocess.STDOUT).decode().strip()

            git('init', '-q'); git('config', 'user.email', 'contract@example.invalid')
            git('config', 'user.name', 'Contract')
            raw = encoded(data['previous']); digest = hashlib.sha256(raw).hexdigest()
            save(repo / 'manifest.json', {'is_demo': False, 'board': digest})
            save(repo / f'board/{digest}.json', raw)
            git('add', '.'); git('commit', '-qm', 'fixed official predecessor')
            main = git('rev-parse', 'HEAD')
            for path, value in data['evidence'].items(): save(repo / path, value)
            git('add', '.'); git('commit', '-qm', 'fixed original generated source')
            evidence = git('rev-parse', 'HEAD')
            for path, value in data['review'].items(): save(repo / path, value)
            git('add', '.'); git('commit', '-qm', 'fixed saved paper source inputs')
            review = git('rev-parse', 'HEAD')
            save(root / 'previous.json', data['previous'])
            save(root / 'candidate.json', data['candidate'])
            for name in ('house_clerk', 'oge', 'senate_efd'):
                board = deepcopy(data['candidate'])
                if name != 'house_clerk':
                    board.update(people=[], transactions=[], reported_holdings=[])
                save(root / 'sources' / f'{name}-current.json', board)
            output = root / 'delta.json'
            command = [sys.executable, '-m', 'unison_snapshot.fact_policy.publication_delta',
                '--previous-board', str(root / 'previous.json'), '--unified', str(root / 'candidate.json'),
                '--prepared', str(root / 'candidate.json'), '--source-root', str(root / 'sources'),
                '--repo', str(repo), '--main-commit', main, '--evidence-commit', evidence,
                '--review-commit', review, '--output', str(output)]

            def run(extra=()):
                return subprocess.run(command + list(extra), env=env, capture_output=True, text=True)

            initial = run(); self.assertEqual(initial.returncode, 0, initial.stderr)
            original = output.read_bytes(); report = json.loads(original)
            self.assertEqual(report['counts']['transactions'],
                {'previous': 2, 'prepared': 2, 'unchanged': 0, 'new': 0, 'corrected': 2})
            self.assertEqual(report['source_verified_correction_count'], 2)
            self.assertIn('archived_house_paper_blank_owner', report['correction_verification_level'])
            self.assertEqual(len({item['old_id'] for item in report['changes']}), 2)
            self.assertTrue(all(item['old_id'] == item['new_id'] and item['changed_fields'] == ['owner']
                                for item in report['changes']))
            transition = {'schema_version': 'pipeline-fact-transitions/v1', 'from_main_commit': main,
                'to_review_commit': review, 'evidence_commit': evidence, 'records': report['changes']}
            save(root / 'transitions.json', transition)
            for extra in ((), ('--transitions', str(root / 'transitions.json'))):
                accepted = run(extra); self.assertEqual(accepted.returncode, 0, accepted.stderr)
                self.assertEqual(output.read_bytes(), original)
            # A later fixed bad qualification is not permission, and failure
            # cannot modify the output or any original/cache input bytes.
            bad = json.loads(data['review'][data['qualification_path']])
            bad[PROFILE_KEY]['id_mappings'][0]['physical_band_index'] += 1
            save(repo / data['qualification_path'], bad)
            git('add', '.'); git('commit', '-qm', 'bad saved mapping control')
            command[command.index('--review-commit') + 1] = git('rev-parse', 'HEAD')
            before = {path: (repo / path).read_bytes() for path in data['evidence'] | data['review']}
            output.write_bytes(b'output sentinel')
            rejected = run(); self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(output.read_bytes(), b'output sentinel')
            self.assertEqual(before, {path: (repo / path).read_bytes() for path in before})
            # Original refs keep their original meaning despite working-tree
            # pollution; there is no fallback to local source files.
            command[command.index('--review-commit') + 1] = review
            for path in data['evidence'] | data['review']: (repo / path).write_bytes(b'bad working bytes')
            accepted = run(); self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(output.read_bytes(), original)

    def test_known_null_market_enrichment_and_person_annotations_keep_the_complete_formal_shape(self):
        data = deepcopy(self.data)
        for board in (data['previous'], data['candidate']):
            board['people'][0].update(portrait_url='https://example.invalid/portrait.png',
                                     priority=True, priority_reason='preserved configured annotation')
            for row in board['transactions']:
                row.pop('position_effect_basis')
                row['ticker'] = 'EXM'; row['ticker_mapping_basis'] = 'market'
        before = deepcopy(data)
        with patch('unison_snapshot.house_ptr_cell_observation.ocr_pdf_pages',
                   side_effect=AssertionError('publication replay cannot start OCR')) as ocr:
            result = self.verify(data)
        self.assertEqual(len(result), 2)
        self.assertEqual(data, before)
        self.assertTrue(all(row['instrument_type'] == 'Unspecified'
                            for row in data['candidate']['transactions']))
        ocr.assert_not_called()

    def test_complete_fixed_predecessor_is_not_rebuilt_from_changed_formal_fields(self):
        for field, value in (('amount_low', 1002), ('asset_name', 'FORGED ASSET'),
                ('instrument_type', 'Stock'), ('id', 'house-ptr:' + '0' * 24),
                ('option_type', 'call'), ('unknown_optional', None)):
            data = deepcopy(self.data)
            for board in (data['previous'], data['candidate']): board['transactions'][0][field] = value
            before = deepcopy(data)
            with self.subTest(field=field), self.assertRaises(InventoryError): self.verify(data)
            self.assertEqual(data, before)
        for field, value in (('display_name', 'Someone Else'), ('party', 'I'),
                             ('state', 'CA'), ('chamber', 'Senate')):
            data = deepcopy(self.data)
            for board in (data['previous'], data['candidate']): board['people'][0][field] = value
            with self.subTest(person_field=field), self.assertRaisesRegex(InventoryError, 'source identity'):
                self.verify(data)
        supplied = deepcopy(self.data['previous']['people']); supplied[0]['role'] = 'Different office'
        with self.assertRaisesRegex(InventoryError, 'fixed main person'): self.verify(previous_people=supplied)

    def test_source_profile_and_ambiguous_hint_never_self_authorize(self):
        for kind in ('pdf', 'saved_words', 'mapping', 'historical_qualification', 'identity', 'duplicate_hint'):
            data = deepcopy(self.data)
            if kind == 'pdf':
                path = data['metadata']['archive_path']; data['evidence'][path] += b'\nchanged source\n'
            elif kind == 'saved_words':
                path = data['observation_path']; value = json.loads(data['review'][path])
                value['pages'][0].pop('asset_line_source'); data['review'][path] = encoded(value)
            elif kind in ('mapping', 'historical_qualification'):
                path = data['qualification_path']; value = json.loads(data['review'][path])
                if kind == 'mapping': value[PROFILE_KEY]['id_mappings'][0]['physical_id'] = 'forged'
                else: value[PROFILE_KEY]['legacy_qualification']['transactions'][0]['owner'] = 'Undisclosed'
                data['review'][path] = encoded(value)
            elif kind == 'identity':
                path = next(path for path in data['review'] if '/identities/' in path)
                value = json.loads(data['review'][path]); value['person_id'] = 'house:A000001'
                data['review'][path] = encoded(value)
            else:
                path = str(Path(data['qualification_path']).with_name('0' * 64 + '.json')).replace('\\', '/')
                data['review'][path] = data['review'][data['qualification_path']]
            before = deepcopy(data)
            with self.subTest(kind=kind), self.assertRaises(InventoryError): self.verify(data)
            self.assertEqual(data, before)

    def test_missing_refs_and_other_field_changes_do_not_gain_owner_authority(self):
        for key in ('repo', 'main_commit', 'review_commit', 'evidence_commit'):
            with self.subTest(key=key), self.assertRaisesRegex(InventoryError, 'fixed main, review and evidence'):
                self.verify(**{key: None})
        for field, value in (('amount_high', 50000), ('instrument_type', 'Stock')):
            data = deepcopy(self.data); data['candidate']['transactions'][0][field] = value
            # This verifier makes no decision for a different correction kind;
            # normal publication must account for that change independently.
            result = self.verify(data)
            self.assertEqual(len(result), 1)
            self.assertNotEqual(result[0]['old_id'], data['previous']['transactions'][0]['id'])


if __name__ == '__main__':
    unittest.main()
