"""Actual generic-to-default loop through workflow, status and publication.

Generated native originals prove the contract, not official recognition.
"""
import json
import subprocess
import sys
import unittest

import test_oge_scoped_generic_workflow as workflow_fixture
from test_oge_owner_publication import encoded, save, sources_for


class DefaultOwnerCliTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing real Bash checkout fixture without inheriting
        # its tests or duplicating its source-generation implementation.
        self.pipeline = workflow_fixture.ScopedGenericWorkflowTests()
        self.pipeline.setUp()
        self.addCleanup(self.pipeline.doCleanups)

    def corrected_base(self):
        p = self.pipeline
        result = p.execute(dispatched=p.data['a'])
        self.assertEqual(result.returncode, 0, result.stderr)
        previous, _ = p.result()
        p.write(p.root/'ingest/current.json', previous)
        return previous

    def test_actual_corrected_output_survives_default_reader_status_and_fixed_git_publication(self):
        p = self.pipeline
        previous = self.corrected_base()
        originals = {path: path.read_bytes() for path in p.data['evidence_root'].rglob('*')
                     if path.is_file()}
        result = p.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate, audit = p.result()
        self.assertEqual(candidate['transactions'], previous['transactions'])
        self.assertEqual(candidate['people'], previous['people'])
        self.assertEqual(len(candidate['transactions']), 2)
        reports = {row['document_id']: row for row in audit['reports']}
        self.assertEqual({row['source_interpretation_binding']['parser_version']
                          for row in reports.values()}, {'oge-278t-pdf/v2'})
        conserved = reports[p.data['a']]['default_owner_conservation']
        identifier = p.data['old_a']['id']
        self.assertEqual(conserved['retained_ids'], [identifier])
        self.assertEqual(conserved['current_ids'], [identifier])
        self.assertEqual(conserved['deferred_ids'], [])
        self.assertNotIn('default_owner_conservation', reports[p.data['b']])
        p.public_status(candidate)
        first = p.output.read_bytes(), p.audit.read_bytes()
        result = p.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((p.output.read_bytes(), p.audit.read_bytes()), first)
        self.assertTrue(all(path.read_bytes() == content for path, content in originals.items()))

        repo = p.root/'publication'; repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args],
                env=p.env, stderr=subprocess.STDOUT, text=True).strip()
        def commit(message):
            git('add', '.'); git('commit', '-qm', message)
            return git('rev-parse', 'HEAD')
        def copy_inputs(root):
            for path in root.rglob('*'):
                relative = path.relative_to(root)
                if path.is_file() and '.git' not in relative.parts:
                    target = repo/relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
        git('init', '-q'); git('config', 'user.name', 'Default Owner Contract')
        git('config', 'user.email', 'default-owner@example.invalid')
        import hashlib
        raw = encoded(previous); sha = hashlib.sha256(raw).hexdigest()
        save(repo/'manifest.json', {'is_demo': False, 'board': sha})
        save(repo/f'board/{sha}.json', raw)
        main_ref = commit('fixed actual generic formal output')
        copy_inputs(p.data['evidence_root']); evidence_ref = commit('fixed original sources')
        copy_inputs(p.review); review_ref = commit('fixed normal default qualification')
        for name, source in sources_for(candidate).items():
            save(p.root/'sources'/f'{name}-current.json', source)
        delta = p.root/'delta.json'
        command = [sys.executable, '-m', 'unison_snapshot.pipeline_qa.publication_delta',
            '--previous-board', str(p.root/'ingest/current.json'),
            '--unified', str(p.output), '--prepared', str(p.output),
            '--source-root', str(p.root/'sources'), '--output', str(delta),
            '--repo', str(repo), '--main-commit', main_ref,
            '--evidence-commit', evidence_ref, '--review-commit', review_ref]
        result = subprocess.run(command, env=p.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        first_delta = delta.read_bytes()
        self.assertEqual(json.loads(first_delta)['changes'], [])
        result = subprocess.run(command, env=p.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(delta.read_bytes(), first_delta)

    def test_wrong_default_cache_preserves_candidate_audit_and_original_bytes(self):
        p = self.pipeline
        self.corrected_base()
        originals = {path: path.read_bytes() for path in p.data['evidence_root'].rglob('*')
                     if path.is_file()}
        cache = p.data['extractions_root']/p.data['a']/p.data['sha_a']/'oge-278t-pdf-v2.json'
        wrong = json.loads(cache.read_bytes()); wrong['self_signed_conservation'] = True
        p.write(cache, wrong)
        p.output.write_bytes(b'candidate sentinel'); p.audit.write_bytes(b'audit sentinel')
        result = p.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.output.read_bytes(), b'candidate sentinel')
        self.assertEqual(p.audit.read_bytes(), b'audit sentinel')
        self.assertEqual(json.loads(cache.read_bytes()), wrong)
        self.assertTrue(all(path.read_bytes() == content for path, content in originals.items()))


if __name__ == '__main__':
    unittest.main()
