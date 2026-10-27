"""Candidate/status/publication owner-scope contract through normal CLIs.

Generated source pages exercise integration; no official recognition claim.
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

import pymupdf

from test_oge_owner_correction import fixture, write_files, CUTOFF, DOCUMENT
from test_oge_owner_publication import save, encoded, sources_for
from unison_snapshot.codec import encode


def unknown_context(raw):
    document = pymupdf.open(stream=raw, filetype='pdf')
    document[1].insert_text((30, 80), 'Additional information')
    result = document.tobytes(); document.close()
    return result


class OwnerScopeCliTests(unittest.TestCase):
    def test_existing_v2_manual_overlay_keeps_raw_selection_separate(self):
        from unittest.mock import patch
        from unison_snapshot import __main__ as cli
        from unison_snapshot.oge_candidate import build_oge_candidate
        from test_oge_candidate import extraction, catalog, base
        raw = extraction()
        raw.update(filed_at=None, evidence_complete=False,
                   document_reasons=['filer_signature_date_not_unique'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); extracts = root / 'extracts'; extracts.mkdir()
            path = extracts / 'raw.json'; save(path, raw)
            save(root / 'catalog.json', catalog()); save(root / 'base.json', base())
            observation = {'schema_version': 'oge-manual-cover-date/v1',
                'status': 'observation_only', 'qualification_effect': 'none',
                'document_id': raw['document_id'], 'source_sha256': raw['source_sha256'],
                'source_url': raw['source_url'], 'extraction_parser_version': 'oge-278t-pdf/v2',
                'page_number': 1, 'recognition_method': 'human_visual_reading_of_pdf_cover',
                'field_role': 'filer_signature_date', 'raw_reading': '6/3/25',
                'observed_date': '2025-06-03', 'filed_at': '2025-06-03'}
            save(root / 'dates' / raw['document_id'] / (raw['source_sha256'] + '.json'), observation)
            args = ['snapshot', 'build-oge-candidate', '--catalog', str(root / 'catalog.json'),
                '--base', str(root / 'base.json'), '--extractions-dir', str(extracts),
                '--manual-cover-dates-dir', str(root / 'dates'),
                '--data-cutoff-at', '2025-06-04T00:00:00Z', '--output', str(root / 'candidate.json'),
                '--audit-output', str(root / 'audit.json')]
            before = path.read_bytes()
            with patch.object(sys, 'argv', args), patch.object(cli, 'build_oge_candidate',
                    wraps=build_oge_candidate) as receiver:
                cli.main()
            self.assertEqual(receiver.call_args.args[1][0]['filed_at'], '2025-06-03')
            self.assertEqual(receiver.call_args.kwargs['source_extractions_by_document'][raw['document_id']], raw)
            candidate = json.loads((root / 'candidate.json').read_bytes())
            audit = json.loads((root / 'audit.json').read_bytes())
            self.assertEqual(candidate['transactions'][0]['filed_at'], '2025-06-03T00:00:00Z')
            report = audit['reports'][0]
            self.assertIn('manual_filing_date_evidence', report)
            self.assertEqual(report['source_interpretation_binding']['extraction_canonical_sha256'],
                             hashlib.sha256(encode(raw)).hexdigest())
            self.assertNotEqual(hashlib.sha256(encode(receiver.call_args.args[1][0])).hexdigest(),
                                hashlib.sha256(encode(raw)).hexdigest())
            self.assertEqual(path.read_bytes(), before)

    def test_closed_and_unknown_context_keep_each_layer_consistent(self):
        for case, mutation in (('closed', None), ('unknown', unknown_context)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                old, expected, review, evidence, previous = fixture(
                    asset='Example Common Stock See Endnote (EXM)', pdf_mutator=mutation,
                    extra_rows=[['', 'Filer assets', '', '', '', ''],
                                ['2', 'INDEPENDENT Fund', 'Purchase', '06/02/2026', 'No',
                                 '$15,001 - $50,000']])
                root = Path(directory); repo = root / 'repo'; repo.mkdir()
                env = dict(os.environ, PYTHONIOENCODING='utf-8')

                def git(*args):
                    return subprocess.check_output(['git', '-C', str(repo), *args], env=env,
                        stderr=subprocess.STDOUT).decode().strip()

                def run(command):
                    return subprocess.run(command, env=env, text=True, capture_output=True)

                def formal_commit(board, message):
                    raw = encoded(board); sha = hashlib.sha256(raw).hexdigest()
                    save(repo / 'manifest.json', {'is_demo': False, 'board': sha})
                    save(repo / f'board/{sha}.json', raw)
                    git('add', '.'); git('commit', '-qm', message)
                    return git('rev-parse', 'HEAD')

                git('init', '-q'); git('config', 'user.name', 'Scope Contract')
                git('config', 'user.email', 'scope@example.invalid')
                main = formal_commit(previous, 'fixed old formal control')
                write_files(repo, evidence); git('add', '.'); git('commit', '-qm', 'fixed source control')
                evidence_ref = git('rev-parse', 'HEAD')
                write_files(repo, review); save(root / 'base.json', previous)
                catalog = next(path for path in evidence if '/catalog/normalized/' in path)
                candidate_path, audit_path = root / 'candidate.json', root / 'audit.json'
                normal = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                    '--catalog', str(repo / catalog), '--extractions-dir', str(repo / 'oge/extractions'),
                    '--legacy-extractions-dir', str(repo / 'oge/extractions'), '--generic',
                    '--evidence-root', str(repo), '--base', str(root / 'base.json'),
                    '--data-cutoff-at', CUTOFF, '--output', str(candidate_path), '--audit-output', str(audit_path)]
                result = run(normal); self.assertEqual(result.returncode, 0, result.stderr)
                candidate = json.loads(candidate_path.read_bytes()); audit = json.loads(audit_path.read_bytes())
                facts = candidate['transactions']
                target = next(row for row in facts if row['id'] == old['id'])
                independent = next(row for row in facts if row['id'] != old['id'])
                self.assertEqual(target, expected if case == 'closed' else old)
                self.assertEqual(independent['owner'], 'Self')
                self.assertEqual(independent['asset_name'], 'INDEPENDENT Fund')
                self.assertEqual(len(facts), 2)
                raw_native = json.loads(next(raw for path, raw in review.items() if path.endswith('v7.json')))
                binding = audit['reports'][0]['source_interpretation_binding']
                self.assertEqual(binding['document_id'], DOCUMENT)
                self.assertEqual(binding['source_sha256'], raw_native['source_sha256'])
                self.assertEqual(binding['parser_version'], 'oge-278t-pdf/v7')
                self.assertEqual(binding['extraction_canonical_sha256'], hashlib.sha256(encode(raw_native)).hexdigest())
                first_bytes = candidate_path.read_bytes(), audit_path.read_bytes()
                result = run(normal); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((candidate_path.read_bytes(), audit_path.read_bytes()), first_bytes)
                save(repo / 'oge/qualifications/current.json', audit)
                git('add', '.'); git('commit', '-qm', 'normal scope qualification')
                review_ref = git('rev-parse', 'HEAD')
                status_path = root / 'status.json'
                status_command = [sys.executable,
                    str(Path(__file__).resolve().parents[1] / 'scripts/build_disclosure_status.py'),
                    '--evidence-root', str(repo), '--review-root', str(repo), '--input', str(candidate_path),
                    '--code-commit', 'f' * 40, '--previous-main-commit', main, '--output', str(status_path)]
                result = run(status_command); self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(status_path.read_bytes())
                record = next(row for scope in status['scopes'] for row in scope['reports']
                              if row['report_id'] == DOCUMENT)
                self.assertEqual(record['qualified_transaction_count'], 2 if case == 'closed' else 1)
                self.assertEqual(set(record['publication_transaction_ids']), {row['id'] for row in facts})
                if case == 'unknown':
                    self.assertEqual(record['qualification_state'], 'partial')
                from unison_snapshot.builder import build
                from unison_snapshot.disclosure_status import attach_disclosure_status
                from unison_snapshot.public_repo import PublicSnapshotRepository
                from test_public_repo import FakeTransport
                bundle = attach_disclosure_status(build(candidate, generated_at=CUTOFF, allow_production=True),
                    status['scopes'], transactions=facts, provenance=status['provenance'])
                public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
                self.assertEqual(public.fetch().snapshot['transactions'], facts)
                self.assertEqual(public.fetch('person', old['person_id']).snapshot['transactions'], facts)
                self.assertTrue(public.disclosure_status(public.resolve(), include_records=True))
                for name, source in sources_for(candidate).items():
                    save(root / 'sources' / f'{name}-current.json', source)
                delta_path = root / 'delta.json'
                gate = [sys.executable, '-m', 'unison_snapshot.pipeline_qa.publication_delta',
                    '--previous-board', str(root / 'base.json'), '--unified', str(candidate_path),
                    '--prepared', str(candidate_path), '--source-root', str(root / 'sources'),
                    '--output', str(delta_path), '--repo', str(repo), '--main-commit', main,
                    '--evidence-commit', evidence_ref, '--review-commit', review_ref]
                result = run(gate); self.assertEqual(result.returncode, 0, result.stderr)
                delta = json.loads(delta_path.read_bytes()); first_delta = delta_path.read_bytes()
                self.assertEqual(delta['counts']['transactions']['new'], 1)
                self.assertEqual(delta['source_verified_correction_count'], 1 if case == 'closed' else 0)
                self.assertEqual(delta['counts']['transactions']['unchanged'], 0 if case == 'closed' else 1)
                self.assertEqual(run(gate).returncode, 0)
                self.assertEqual(delta_path.read_bytes(), first_delta)
                transitions = {'schema_version': 'pipeline-fact-transitions/v1', 'from_main_commit': main,
                    'to_review_commit': review_ref, 'evidence_commit': evidence_ref,
                    'records': [row for row in delta['changes'] if row['kind'] == 'corrected']}
                save(root / 'transitions.json', transitions)
                result = run(gate + ['--transitions', str(root / 'transitions.json')])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(delta_path.read_bytes(), first_delta)
                # Actual previous output becomes the next base; a migration
                # alias may not duplicate or silently lose its qualification.
                save(root / 'nextbase.json', candidate)
                next_normal = normal[:]; next_normal[next_normal.index('--base') + 1] = str(root / 'nextbase.json')
                result = run(next_normal); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(candidate_path.read_bytes())['transactions'], facts)
                save(repo / 'oge/qualifications/current.json', audit_path.read_bytes())
                next_main = formal_commit(candidate, 'fixed normal prior output for next cycle')
                next_status = status_command[:]
                next_status[next_status.index('--previous-main-commit') + 1] = next_main
                result = run(next_status); self.assertEqual(result.returncode, 0, result.stderr)
                next_record = next(row for scope in json.loads(status_path.read_bytes())['scopes']
                                   for row in scope['reports'] if row['report_id'] == DOCUMENT)
                self.assertEqual(next_record['qualified_transaction_count'], record['qualified_transaction_count'])
                for name, source in sources_for(json.loads(candidate_path.read_bytes())).items():
                    save(root / 'sources' / f'{name}-current.json', source)
                next_gate = gate[:]
                next_gate[next_gate.index('--previous-board') + 1] = str(root / 'nextbase.json')
                next_gate[next_gate.index('--main-commit') + 1] = next_main
                next_gate[next_gate.index('--review-commit') + 1] = next_main
                result = run(next_gate); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(delta_path.read_bytes())['changes'], [])

    def test_wrong_cache_authentication_preserves_both_existing_outputs(self):
        _, _, review, evidence, previous = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); write_files(root, {**evidence, **review})
            catalog = next(path for path in evidence if '/catalog/normalized/' in path)
            native_path = next(path for path in review if path.endswith('v7.json'))
            native = json.loads(review[native_path]); native['owner_scope_complete'] = True
            save(root / native_path, native); save(root / 'base.json', previous)
            output, audit = root / 'output.json', root / 'audit.json'
            output.write_bytes(b'candidate sentinel'); audit.write_bytes(b'audit sentinel')
            result = subprocess.run([sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                '--catalog', str(root / catalog), '--extractions-dir', str(root / 'oge/extractions'),
                '--legacy-extractions-dir', str(root / 'oge/extractions'), '--generic',
                '--evidence-root', str(root), '--base', str(root / 'base.json'), '--data-cutoff-at', CUTOFF,
                '--output', str(output), '--audit-output', str(audit)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), b'candidate sentinel')
            self.assertEqual(audit.read_bytes(), b'audit sentinel')


if __name__ == '__main__':
    unittest.main()
