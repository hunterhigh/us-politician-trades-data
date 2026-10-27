"""Actual native owner facts survive the normal default reader and publication.

Generated originals prove the execution contract, not official recognition.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_oge_candidate import base as empty_base
from test_oge_owner_correction import fixture, write_files, CUTOFF
from test_oge_owner_publication import save, encoded, sources_for


ROOT = Path(__file__).resolve().parents[2]


class DefaultExplicitOwnerCliTests(unittest.TestCase):
    def test_actual_generic_output_keeps_full_facts_ids_and_current_after_default_switch(self):
        for owner, heading in (('Spouse', 'Spouse assets'),
                               ('Dependent Child', 'Dependent children assets')):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory(prefix='oge-eo-') as directory:
                _, _, review, evidence, _ = fixture(
                    asset='Example Common Stock (EXM)', heading=heading, historical=False)
                root = Path(directory); repo = root/'repo'; repo.mkdir()
                env = dict(os.environ, PYTHONIOENCODING='utf-8')

                def run(command):
                    return subprocess.run(command, env=env, capture_output=True, text=True)

                def git(*args):
                    return subprocess.check_output(['git', '-C', str(repo), *args],
                        env=env, stderr=subprocess.STDOUT, text=True).strip()

                def commit(message):
                    git('add', '.'); git('commit', '-qm', message)
                    return git('rev-parse', 'HEAD')

                git('init', '-q'); git('config', 'user.name', 'Explicit Owner Contract')
                git('config', 'user.email', 'owner@example.invalid')
                write_files(repo, {**evidence, **review})
                catalog = next(path for path in evidence if '/catalog/normalized/' in path)
                save(root/'empty.json', empty_base())
                previous_path, previous_audit = root/'generic.json', root/'generic-audit.json'
                common = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                    '--catalog', str(repo/catalog), '--extractions-dir', str(repo/'oge/extractions'),
                    '--evidence-root', str(repo), '--data-cutoff-at', CUTOFF]
                result = run(common+['--generic', '--legacy-extractions-dir', str(repo/'oge/extractions'),
                    '--base', str(root/'empty.json'), '--output', str(previous_path),
                    '--audit-output', str(previous_audit)])
                self.assertEqual(result.returncode, 0, result.stderr)
                previous = json.loads(previous_path.read_bytes())
                self.assertEqual(len(previous['transactions']), 1)
                prior = previous['transactions'][0]
                self.assertEqual((prior['owner'], prior['instrument_type']), (owner, 'Stock'))
                raw = encoded(previous); sha = hashlib.sha256(raw).hexdigest()
                save(repo/'manifest.json', {'is_demo':False, 'board':sha})
                save(repo/f'board/{sha}.json', raw)
                main = commit('actual generic formal output and source inputs')
                output, audit_path = root/'default.json', root/'default-audit.json'
                command = common+['--base', str(previous_path), '--output', str(output),
                                  '--audit-output', str(audit_path)]
                result = run(command); self.assertEqual(result.returncode, 0, result.stderr)
                candidate, audit = json.loads(output.read_bytes()), json.loads(audit_path.read_bytes())
                self.assertEqual(candidate['transactions'], previous['transactions'])
                self.assertEqual(candidate['people'], previous['people'])
                report = audit['reports'][0]
                self.assertEqual(report['source_interpretation_binding']['parser_version'], 'oge-278t-pdf/v2')
                self.assertEqual(report['default_owner_conservation']['current_ids'], [prior['id']])
                self.assertEqual(audit['current_qualified_transaction_count'], 1)
                first = output.read_bytes(), audit_path.read_bytes()
                self.assertEqual(run(command).returncode, 0)
                self.assertEqual((output.read_bytes(), audit_path.read_bytes()), first)
                # The default result itself is a real next base, not a hand
                # edited owner or a counterfactual parser cache.
                save(root/'nextbase.json', candidate)
                repeat = command[:]; repeat[repeat.index('--base')+1] = str(root/'nextbase.json')
                result = run(repeat); self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(output.read_bytes())['transactions'], previous['transactions'])
                save(repo/'oge/qualifications/current.json', audit_path.read_bytes())
                review_ref = commit('actual normal default next-base qualification')
                status_path = root/'status.json'
                result = run([sys.executable, str(ROOT/'backend/scripts/build_disclosure_status.py'),
                    '--evidence-root', str(repo), '--review-root', str(repo), '--input', str(output),
                    '--code-commit', 'f'*40, '--previous-main-commit', main, '--output', str(status_path)])
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(status_path.read_bytes())
                record = next(row for scope in status['scopes'] for row in scope['reports']
                              if row['report_id'] == prior['filing_id'])
                self.assertEqual(record['qualified_transaction_count'], 1)
                self.assertEqual(record['publication_transaction_ids'], [prior['id']])
                from unison_snapshot.builder import build
                from unison_snapshot.disclosure_status import attach_disclosure_status
                from unison_snapshot.public_repo import PublicSnapshotRepository
                from test_public_repo import FakeTransport
                bundle = attach_disclosure_status(build(candidate, generated_at=CUTOFF, allow_production=True),
                    status['scopes'], transactions=candidate['transactions'], provenance=status['provenance'])
                public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
                self.assertEqual(public.fetch().snapshot['transactions'], previous['transactions'])
                self.assertEqual(public.fetch('person', prior['person_id']).snapshot['transactions'], previous['transactions'])
                for name, source in sources_for(candidate).items():
                    save(root/'sources'/f'{name}-current.json', source)
                delta_path = root/'delta.json'
                gate = [sys.executable, '-m', 'unison_snapshot.pipeline_qa.publication_delta',
                    '--previous-board', str(previous_path), '--unified', str(output), '--prepared', str(output),
                    '--source-root', str(root/'sources'), '--output', str(delta_path), '--repo', str(repo),
                    '--main-commit', main, '--evidence-commit', main, '--review-commit', review_ref]
                result = run(gate); self.assertEqual(result.returncode, 0, result.stderr)
                first_delta = delta_path.read_bytes()
                self.assertEqual(json.loads(first_delta)['changes'], [])
                self.assertEqual(run(gate).returncode, 0)
                self.assertEqual(delta_path.read_bytes(), first_delta)
                self.assertTrue(all((repo/path).read_bytes() == raw for path, raw in evidence.items()))


if __name__ == '__main__':
    unittest.main()
