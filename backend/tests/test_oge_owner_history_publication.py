"""Normal historical-catalog owner correction through fixed Git publication.

All sources are generated native controls, not official recognition samples.
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

from test_oge_owner_correction import fixture, write_files, encoded, CUTOFF
from test_oge_owner_publication import save, sources_for
from unison_snapshot.oge import parse_catalog_page, build_catalog, archive_catalog


class OgeOwnerHistoryPublicationTests(unittest.TestCase):
    def test_normal_history_selection_publication_repeat_and_missing_history_source(self):
        old, expected, review, evidence, previous = fixture()
        original = deepcopy(evidence)
        history_sha = json.loads(review['status/oge.json'])['catalog_sha256']
        history = {**json.loads(evidence[f'oge/catalog/normalized/{history_sha}.json']),
                   'metadata': json.loads(evidence[f'oge/catalog/{history_sha}.json'])}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / 'repo'; repo.mkdir()
            env = dict(os.environ, PYTHONIOENCODING='utf-8')

            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], env=env,
                    stderr=subprocess.STDOUT).decode().strip()

            def run(command):
                return subprocess.run(command, env=env, text=True, capture_output=True)

            git('init', '-q'); git('config', 'user.name', 'History Contract')
            git('config', 'user.email', 'history@example.invalid')
            board = encoded(previous); digest = hashlib.sha256(board).hexdigest()
            save(repo / 'manifest.json', {'is_demo': False, 'board': digest})
            save(repo / f'board/{digest}.json', board)
            git('add', '.'); git('commit', '-qm', 'fixed old official snapshot contract')
            main = git('rev-parse', 'HEAD')
            write_files(repo, evidence)
            raw_page = encoded({'draw': 1, 'recordsTotal': 0, 'recordsFiltered': 0, 'data': []})
            current = build_catalog([parse_catalog_page(json.loads(raw_page), start=0, length=1)])
            metadata = archive_catalog(repo, [(0, 1, raw_page, {'content-type': 'application/json'})],
                                       current, retrieved_at=CUTOFF)
            git('add', '.'); git('commit', '-qm', 'fixed current and historical original catalogs')
            evidence_commit = git('rev-parse', 'HEAD')
            write_files(repo, review)
            save(repo / f'oge/discoveries/{history_sha}.json', history)
            current_wrapper = {**current, 'metadata': metadata}
            save(repo / f'oge/discoveries/{metadata["sha256"]}.json', current_wrapper)
            save(repo / 'status/oge.json', {'schema_version': 'oge-review-run/v1',
                'source_id': 'oge', 'catalog_sha256': metadata['sha256']})
            save(root / 'previous.json', previous); save(root / 'catalog.json', current_wrapper)
            candidate_path, audit_path = root / 'candidate.json', repo / 'oge/qualifications/current.json'
            normal = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                '--catalog', str(root / 'catalog.json'), '--catalog-history-dir', str(repo / 'oge/discoveries'),
                '--extractions-dir', str(repo / 'oge/extractions'),
                '--legacy-extractions-dir', str(repo / 'oge/extractions'), '--generic',
                '--evidence-root', str(repo), '--base', str(root / 'previous.json'),
                '--data-cutoff-at', CUTOFF, '--output', str(candidate_path), '--audit-output', str(audit_path)]
            result = run(normal); self.assertEqual(result.returncode, 0, result.stderr)
            candidate = json.loads(candidate_path.read_bytes()); audit = json.loads(audit_path.read_bytes())
            self.assertEqual(candidate['transactions'], [expected])
            self.assertEqual(audit['current_catalog_direct_count'], 0)
            self.assertEqual(audit['retained_historical_direct_count'], 1)
            first = candidate_path.read_bytes(), audit_path.read_bytes()
            result = run(normal); self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((candidate_path.read_bytes(), audit_path.read_bytes()), first)
            git('add', '.'); git('commit', '-qm', 'normal historical generic review')
            review_commit = git('rev-parse', 'HEAD')
            source_root, output = root / 'sources', root / 'delta.json'
            for name, value in sources_for(candidate).items():
                save(source_root / f'{name}-current.json', value)
            gate = [sys.executable, '-m', 'unison_snapshot.pipeline_qa.publication_delta',
                '--previous-board', str(root / 'previous.json'), '--unified', str(candidate_path),
                '--prepared', str(candidate_path), '--source-root', str(source_root), '--output', str(output),
                '--repo', str(repo), '--main-commit', main, '--evidence-commit', evidence_commit,
                '--review-commit', review_commit]
            result = run(gate); self.assertEqual(result.returncode, 0, result.stderr)
            first_delta = output.read_bytes(); delta = json.loads(first_delta)
            self.assertEqual(delta['source_verified_correction_count'], 1)
            self.assertEqual(delta['counts']['transactions']['new'], 0)
            self.assertEqual(delta['changes'][0]['changed_fields'], ['owner'])
            self.assertEqual(delta['changes'][0]['old_id'], old['id'])
            self.assertEqual(run(gate).returncode, 0); self.assertEqual(output.read_bytes(), first_delta)
            decision = {'schema_version': 'pipeline-fact-transitions/v1', 'from_main_commit': main,
                'to_review_commit': review_commit, 'evidence_commit': evidence_commit,
                'records': delta['changes']}
            save(root / 'transitions.json', decision)
            result = run(gate + ['--transitions', str(root / 'transitions.json')])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), first_delta)
            # Declaring history cannot replace the original catalog pages.
            page = history['metadata']['pages'][0]['archive_path']
            (repo / page).unlink()
            git('add', '-u'); git('commit', '-qm', 'missing historical original page')
            wrong = gate[:]; wrong[wrong.index('--evidence-commit') + 1] = git('rev-parse', 'HEAD')
            output.write_bytes(b'sentinel')
            result = run(wrong); self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), b'sentinel')
            result = run(gate); self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), first_delta)
            for path, raw in original.items():
                if path.endswith('.pdf'):
                    self.assertEqual((repo / path).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
