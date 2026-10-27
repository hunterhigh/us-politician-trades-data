"""Normal direct classification consumption through fixed Git publication.

The originals are generated controls, not official recognition evidence.
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

from test_oge_classification_correction import classification_fixture
from test_oge_owner_correction import write_files, CUTOFF
from test_oge_owner_publication import save, encoded, sources_for


ROOT = Path(__file__).resolve().parents[2]


class ClassificationCliTests(unittest.TestCase):
    def test_default_and_generic_normal_output_reaches_one_corrected_event_then_no_change(self):
        for generic, owner in ((False, 'Self'), (True, None), (False, 'Spouse')):
            with self.subTest(generic=generic, owner=owner), tempfile.TemporaryDirectory() as directory:
                old, expected, review, evidence, previous = deepcopy(
                    classification_fixture(generic=generic, owner=owner))
                root = Path(directory); repo = root/'repo'; repo.mkdir()
                env = dict(os.environ, PYTHONIOENCODING='utf-8')
                def git(*args):
                    return subprocess.check_output(['git', '-C', str(repo), *args],
                        env=env, stderr=subprocess.STDOUT, text=True).strip()
                def run(command):
                    return subprocess.run(command, env=env, capture_output=True, text=True)
                def commit(message):
                    git('add', '.'); git('commit', '-qm', message)
                    return git('rev-parse', 'HEAD')
                def formal(board, message):
                    raw = encoded(board); sha = hashlib.sha256(raw).hexdigest()
                    save(repo/'manifest.json', {'is_demo':False, 'board':sha})
                    save(repo/f'board/{sha}.json', raw)
                    return commit(message)
                git('init', '-q'); git('config', 'user.name', 'Classification Contract')
                git('config', 'user.email', 'classification@example.invalid')
                main = formal(previous, 'fixed source-derived historical formal object')
                write_files(repo, evidence); evidence_ref = commit('fixed official original control')
                write_files(repo, review)
                catalog = next(path for path in evidence if '/catalog/normalized/' in path)
                save(root/'base.json', previous)
                output, audit_path = root/'candidate.json', root/'audit.json'
                normal = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                    '--catalog', str(repo/catalog), '--extractions-dir', str(repo/'oge/extractions'),
                    '--evidence-root', str(repo), '--base', str(root/'base.json'),
                    '--data-cutoff-at', CUTOFF, '--output', str(output), '--audit-output', str(audit_path)]
                if generic:
                    normal += ['--generic', '--legacy-extractions-dir', str(repo/'oge/extractions')]
                result = run(normal); self.assertEqual(result.returncode, 0, result.stderr)
                candidate = json.loads(output.read_bytes())
                self.assertEqual(candidate['transactions'], [expected])
                self.assertEqual(candidate['people'], previous['people'])
                first = output.read_bytes(), audit_path.read_bytes()
                result = run(normal); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((output.read_bytes(), audit_path.read_bytes()), first)
                save(repo/'oge/qualifications/current.json', audit_path.read_bytes())
                review_ref = commit('actual normal classification qualification')
                status_path = root/'status.json'
                status_command = [sys.executable, str(ROOT/'backend/scripts/build_disclosure_status.py'),
                    '--evidence-root', str(repo), '--review-root', str(repo), '--input', str(output),
                    '--code-commit', 'f'*40, '--previous-main-commit', main, '--output', str(status_path)]
                result = run(status_command); self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(status_path.read_bytes())
                record = next(row for scope in status['scopes'] for row in scope['reports']
                              if row['report_id'] == old['filing_id'])
                self.assertEqual(record['qualified_transaction_count'], 1)
                self.assertEqual(record['publication_transaction_ids'], [old['id']])
                from unison_snapshot.builder import build
                from unison_snapshot.disclosure_status import attach_disclosure_status
                from unison_snapshot.public_repo import PublicSnapshotRepository
                from test_public_repo import FakeTransport
                bundle = attach_disclosure_status(build(candidate, generated_at=CUTOFF, allow_production=True),
                    status['scopes'], transactions=candidate['transactions'], provenance=status['provenance'])
                public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
                self.assertEqual(public.fetch().snapshot['transactions'], [expected])
                self.assertEqual(public.fetch('person', expected['person_id']).snapshot['transactions'], [expected])
                for name, source in sources_for(candidate).items():
                    save(root/'sources'/f'{name}-current.json', source)
                delta_path = root/'delta.json'
                gate = [sys.executable, '-m', 'unison_snapshot.pipeline_qa.publication_delta',
                    '--previous-board', str(root/'base.json'), '--unified', str(output),
                    '--prepared', str(output), '--source-root', str(root/'sources'), '--output', str(delta_path),
                    '--repo', str(repo), '--main-commit', main,
                    '--evidence-commit', evidence_ref, '--review-commit', review_ref]
                result = run(gate); self.assertEqual(result.returncode, 0, result.stderr)
                delta = json.loads(delta_path.read_bytes()); first_delta = delta_path.read_bytes()
                self.assertEqual(delta['source_verified_correction_count'], 1)
                self.assertEqual(len(delta['changes']), 1)
                event = delta['changes'][0]
                self.assertEqual(event['reason'], 'source_classification_correction')
                self.assertEqual(event['changed_fields'], ['instrument_type', 'owner'] if generic else ['instrument_type'])
                self.assertEqual(event['old_id'], event['new_id'])
                self.assertEqual(run(gate).returncode, 0)
                self.assertEqual(delta_path.read_bytes(), first_delta)
                save(root/'transitions.json', {'schema_version':'pipeline-fact-transitions/v1',
                    'from_main_commit':main, 'to_review_commit':review_ref,
                    'evidence_commit':evidence_ref, 'records':delta['changes']})
                result = run(gate+['--transitions', str(root/'transitions.json')])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(delta_path.read_bytes(), first_delta)
                # The real output becomes the next base, including the native
                # spouse ID and nullable representation. No new correction.
                save(root/'nextbase.json', candidate)
                next_normal = normal[:]
                next_normal[next_normal.index('--base')+1] = str(root/'nextbase.json')
                result = run(next_normal); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(output.read_bytes())['transactions'], [expected])
                save(repo/'oge/qualifications/current.json', audit_path.read_bytes())
                next_main = formal(candidate, 'fixed actual Stock successor')
                next_gate = gate[:]
                next_gate[next_gate.index('--previous-board')+1] = str(root/'nextbase.json')
                next_gate[next_gate.index('--main-commit')+1] = next_main
                next_gate[next_gate.index('--review-commit')+1] = next_main
                result = run(next_gate); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(delta_path.read_bytes())['changes'], [])
                self.assertTrue(all((repo/path).read_bytes() == raw for path, raw in evidence.items()))


if __name__ == '__main__':
    unittest.main()
