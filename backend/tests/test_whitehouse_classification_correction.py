"""Independent source replay and fixed-Git publication for Endnote migration."""
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

from tests.test_whitehouse_owner_correction import fixture, encoded
from unison_snapshot.whitehouse_native_correction import (
    NativeClassificationSources, NativeCorrectionError, prepare_native_classification_sources,
    reconcile_native_classification)
from unison_snapshot.fact_policy.whitehouse_classification_correction import verified_classification_corrections
from unison_snapshot.fact_policy.source_objects import InventoryError


class WhiteHouseClassificationCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()

    def verify(self, data=None, *, owner='Self', **kwargs):
        old, normalized, review, evidence, base = deepcopy(data or self.data)
        old['owner'] = owner
        base['transactions'] = [old]
        final = {**normalized, 'instrument_type': 'Stock'}
        board = encoded(base); digest = hashlib.sha256(board).hexdigest()
        formal = {'manifest.json': encoded({'is_demo': False, 'board': digest}), f'board/{digest}.json': board}
        options = dict(old={old['id']: old}, current={final['id']: final}, repo='unused',
            main_commit='a'*40, review_commit='b'*40, evidence_commit='e'*40, previous_people=base['people'],
            read_object=lambda repo, ref, path: (formal if ref == 'a'*40 else evidence if ref == 'e'*40 else review)[path])
        options.update(kwargs)
        return verified_classification_corrections(**options)

    def test_complete_historical_and_owner_normalized_predecessors(self):
        self.assertEqual(self.verify()[0]['changed_fields'], ['instrument_type', 'owner'])
        self.assertEqual(self.verify(owner='Undisclosed')[0]['changed_fields'], ['instrument_type'])

    def test_wrong_complete_objects_are_not_authorized_even_on_both_sides(self):
        for field, value in [('amount_low', 1002), ('ticker', 'FORGE'), ('asset_name', 'FORGED Endnote'),
                ('option_type', 'call'), ('unknown_optional', None), ('amount_low_exclusive', False),
                ('id', 'oge-278t:'+'0'*24), ('person_id', 'oge:'+'0'*16),
                ('source', 'Another source'), ('ticker_mapping_basis', 'market')]:
            data = deepcopy(self.data)
            data[0][field] = data[1][field] = value
            with self.subTest(field=field), self.assertRaises(InventoryError):
                self.verify(data)

    def test_other_classifiers_and_unsupported_owner_never_get_a_proof(self):
        for asset in ['Example Treasury Bond See Endnote (EXM)', 'Senior Notes See Endnote (EXM)',
                      'Example ETF See Endnote (EXM)', 'Example Fund See Endnote (EXM)',
                      'Example Common Stock (EXM)', 'Unspecified See Endnote']:
            data = fixture(asset=asset)
            with self.subTest(asset=asset):
                if data[0]['instrument_type'] != 'Bond':
                    self.assertEqual(self.verify(data), [])
                else:
                    with self.assertRaises(InventoryError): self.verify(data)
        self.assertEqual(self.verify(owner='Spouse'), [])

    def test_fixed_main_sources_and_previous_identity_are_required(self):
        for kwargs in [{'main_commit': None}, {'review_commit': 'invalid'}, {'repo': None},
                       {'evidence_commit': 'd'*40}, {'previous_people': []}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(InventoryError): self.verify(**kwargs)
        for role, token, mutate in [
                (2, 'native-roles', lambda x: x['transactions'][0].update(asset_name='FORGED ASSET')),
                (2, 'hybrid-geometry', lambda x: x['transactions'][0].update(amount_low=1002)),
                (2, 'whitehouse/coverage-current', lambda x: x['reports'][0].update(archive_sha256_versions=['0'*64]))]:
            data = deepcopy(self.data)
            path = next(path for path in data[role] if
                (path == token+'.json' if token.endswith('coverage-current') else token in path))
            value = json.loads(data[role][path]); mutate(value); data[role][path] = encoded(value)
            with self.subTest(token=token), self.assertRaises(InventoryError): self.verify(data)

    def test_root_dependencies_survive_repeat_and_cannot_be_replaced_by_context(self):
        old, normalized, review, evidence, base = deepcopy(self.data)
        report = json.loads(next(raw for path, raw in review.items() if 'native-roles' in path))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path, raw in {**review, **evidence}.items():
                target = root/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            sources = prepare_native_classification_sources([report], evidence_root=root, review_root=root,
                previous_transactions=[old])
            row = report['transactions'][0]
            self.assertTrue(sources.includes_row(report, row))
            result = reconcile_native_classification(report, row, old, person_id=old['person_id'],
                source_people=base['people'], sources=sources)
            final = result['fact']
            self.assertEqual(final, {**normalized, 'instrument_type': 'Stock'})
            again = prepare_native_classification_sources([report], evidence_root=root, review_root=root,
                previous_transactions=[final])
            self.assertEqual(sources.provenance(), again.provenance())
            self.assertIsNone(reconcile_native_classification(report, row, final, person_id=old['person_id'],
                source_people=base['people'], sources=again))
            with self.assertRaises(TypeError): NativeClassificationSources(reports={report['document_id']: report})
            for edit in ['inheritance', 'row', 'asset', 'raw_inventory', 'only_formal_hint']:
                altered = deepcopy(report)
                if edit == 'inheritance': del altered['transactions'][0]['legacy_inheritance']
                elif edit == 'row': altered['transactions'] = []
                elif edit == 'asset': altered['transactions'][0]['asset_name'] = 'Forged no callout'
                elif edit == 'raw_inventory': altered['source_rows'] = []
                else:
                    altered['source_rows'] = []; altered['transactions'] = []; altered['quarantined'] = []
                with self.subTest(edit=edit), self.assertRaises(NativeCorrectionError):
                    prepare_native_classification_sources([altered], evidence_root=root, review_root=root,
                        previous_transactions=[final])
            with self.assertRaises(NativeCorrectionError):
                prepare_native_classification_sources([report], previous_transactions=[old])
            altered = deepcopy(report); altered['source_rows'] = []
            with self.assertRaises((NativeCorrectionError, RuntimeError)):
                prepare_native_classification_sources([altered], previous_transactions=[final])
            altered['transactions'] = []; altered['quarantined'] = []
            altered['source_row_count'] = 0; altered.pop('legacy_source_binding', None)
            with self.assertRaises(NativeCorrectionError):
                prepare_native_classification_sources([altered], previous_transactions=[final])

    def test_fresh_native_without_legacy_keeps_first_publication_route(self):
        from unison_snapshot.whitehouse_278t import parse_generic_whitehouse_278t_pdf
        old, normalized, review, evidence, base = deepcopy(self.data)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path, raw in {**review, **evidence}.items():
                target=root/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            legacy = next(root/path for path in review if 'hybrid-geometry' in path); legacy.unlink()
            pdf = next(root/path for path in evidence if path.endswith('.pdf'))
            report = parse_generic_whitehouse_278t_pdf(pdf, source_url=old['source_url'],
                source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(), document_id=old['filing_id'],
                filer_name='Example, Ada', amended_label='Example, Ada - Periodic Transaction Report 06.13.26')
            native = next(root/path for path in review if 'native-roles' in path); native.write_bytes(encoded(report))
            sources = prepare_native_classification_sources([report], evidence_root=root, review_root=root)
            self.assertFalse(sources.includes_row(report, report['transactions'][0]))
            self.assertIsNone(sources.canonical_legacy_digest(report))
            self.assertFalse(prepare_native_classification_sources([report]).includes_row(report, report['transactions'][0]))
            with self.assertRaisesRegex(NativeCorrectionError, 'requires original legacy'):
                prepare_native_classification_sources([report], evidence_root=root, review_root=root,
                    previous_transactions=[old])
            native.write_bytes(next(raw for path, raw in review.items() if 'native-roles' in path))
            original = json.loads(native.read_bytes())
            with self.assertRaises(NativeCorrectionError):
                prepare_native_classification_sources([original], evidence_root=root, review_root=root)

    def test_unrelated_quarantine_and_budget_boundaries(self):
        self.assertEqual(self.verify(fixture(extra='unrelated'))[0]['changed_fields'], ['instrument_type', 'owner'])
        with self.assertRaises(InventoryError): self.verify(fixture(extra='scope'))
        with patch('unison_snapshot.fact_policy.whitehouse_owner_correction.MAX_TOTAL_BYTES', 10):
            with self.assertRaisesRegex(InventoryError, 'byte budget'): self.verify()
        with patch('unison_snapshot.fact_policy.whitehouse_owner_correction.MAX_PAGES', 1):
            with self.assertRaisesRegex(InventoryError, 'page count'): self.verify()

    def test_normal_candidate_and_fixed_git_delta_cli_both_states_repeat_and_explicit(self):
        old, normalized, review, evidence, base = deepcopy(self.data)
        project = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(project/'backend/src'), GIT_NO_LAZY_FETCH='1')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); repo=root/'repo'; repo.mkdir()
            def save(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value if isinstance(value, bytes) else encoded(value))
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.STDOUT, env=env).decode().strip()
            git('init', '-q'); git('config', 'user.email', 'contract@example.invalid'); git('config', 'user.name', 'Contract')
            for path, raw in {**review, **evidence}.items(): save(repo/path, raw)
            for owner in ['Self', 'Undisclosed']:
                original=deepcopy(base); original['transactions'][0]['owner']=owner
                raw=encoded(original); sha=hashlib.sha256(raw).hexdigest()
                save(repo/'manifest.json', {'is_demo': False, 'board': sha}); save(repo/f'board/{sha}.json', raw)
                git('add', '.'); git('commit', '-qm', 'source contract '+owner); commit=git('rev-parse', 'HEAD')
                save(root/'base.json', original)
                cmd=[sys.executable, str(project/'backend/scripts/build_whitehouse_278t_candidate.py'),
                    '--oge-candidate', str(root/'base.json'), '--review-root', str(repo), '--evidence-root', str(repo),
                    '--candidate-out', str(root/'candidate.json'), '--audit-out', str(root/'audit.json'), '--expected-report-count', '1']
                run=subprocess.run(cmd, capture_output=True, text=True, env=env)
                self.assertEqual(run.returncode, 0, run.stderr)
                candidate=json.loads((root/'candidate.json').read_bytes())
                self.assertEqual(candidate['transactions'], [{**normalized, 'instrument_type': 'Stock'}])
                for name in ['house_clerk', 'oge', 'senate_efd']:
                    save(root/'sources'/f'{name}-current.json', candidate if name=='oge' else
                        {**candidate, 'people': [], 'transactions': [], 'reported_holdings': []})
                output=root/'delta.json'
                gate=[sys.executable, '-m', 'unison_snapshot.fact_policy.publication_delta',
                    '--previous-board', str(root/'base.json'), '--unified', str(root/'candidate.json'),
                    '--prepared', str(root/'candidate.json'), '--source-root', str(root/'sources'), '--output', str(output),
                    '--repo', str(repo), '--main-commit', commit, '--evidence-commit', commit, '--review-commit', commit]
                run=subprocess.run(gate, capture_output=True, text=True, env=env)
                self.assertEqual(run.returncode, 0, run.stderr)
                first=output.read_bytes(); proof=json.loads(first)['changes'][0]
                self.assertEqual(proof['changed_fields'], ['instrument_type', 'owner'] if owner=='Self' else ['instrument_type'])
                run=subprocess.run(gate, capture_output=True, text=True, env=env)
                self.assertEqual(run.returncode, 0, run.stderr); self.assertEqual(output.read_bytes(), first)
                transitions={'schema_version':'pipeline-fact-transitions/v1', 'from_main_commit':commit,
                    'to_review_commit':commit, 'evidence_commit':commit, 'records':[proof]}
                save(root/'transitions.json', transitions)
                explicit=gate+['--transitions', str(root/'transitions.json')]
                run=subprocess.run(explicit, capture_output=True, text=True, env=env)
                self.assertEqual(run.returncode, 0, run.stderr); self.assertEqual(output.read_bytes(), first)
                transitions['records'][0]['changed_fields']=['owner']; save(root/'transitions.json', transitions)
                output.write_bytes(b'sentinel'); run=subprocess.run(explicit, capture_output=True, text=True, env=env)
                self.assertNotEqual(run.returncode, 0); self.assertEqual(output.read_bytes(), b'sentinel')
                # The supplied old people/facts cannot self-authorize a new ID.
                altered=deepcopy(original); altered['people'][0]['id']='oge:'+'0'*16
                altered['transactions'][0]['person_id']=altered['people'][0]['id']; save(root/'base.json', altered)
                output.write_bytes(b'sentinel'); run=subprocess.run(gate, capture_output=True, text=True, env=env)
                self.assertNotEqual(run.returncode, 0); self.assertEqual(output.read_bytes(), b'sentinel')


if __name__ == '__main__': unittest.main()
