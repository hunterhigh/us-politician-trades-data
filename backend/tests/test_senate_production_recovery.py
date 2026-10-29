"""Exercise HTTP failures and the real workflow shell against local Git remotes."""
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from unison_snapshot import __main__ as cli
from unison_snapshot.senate import (
    HOME_URL, SEARCH_URL, SenateEfdClient, SenateHttpError,
)
from tests.test_disclosure_candidate import candidate


ROOT = Path(__file__).resolve().parents[2]


def step_script(workflow, name):
    text = (ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8')
    step = text.split('- name: ' + name + '\n', 1)[1]
    lines = step.split('        run: |\n', 1)[1].splitlines()
    script = []
    for line in lines:
        if line and not line.startswith('          '):
            break
        script.append(line[10:])
    return '\n'.join(script).replace('${{ github.run_id }}', 'test-run').replace(
        '${{ github.sha }}', 'test-code') + '\n'


def bash_path():
    if os.name == 'nt':
        git = Path(shutil.which('git'))
        bundled = git.parent.parent / 'bin/bash.exe'
        if bundled.is_file():
            return str(bundled)
    return shutil.which('bash')


class SenateHttpFailureTests(unittest.TestCase):
    def test_http_failure_identifies_stage_without_leaking_body_or_url(self):
        for url, method, stage in (
                (HOME_URL, 'GET', 'agreement_page'),
                (HOME_URL, 'POST', 'agreement_submit'),
                (SEARCH_URL, 'POST', 'catalog_search')):
            with self.subTest(stage=stage):
                body = BytesIO(b'private body, session cookie, reference id')
                error = HTTPError(url, 403, 'private reason',
                                  {'Server': 'AkamaiGHost', 'Set-Cookie': 'private'}, body)
                opener = unittest.mock.Mock()
                opener.open.side_effect = error
                client = SenateEfdClient(opener=opener)
                with self.assertRaises(SenateHttpError) as raised:
                    client._open(Request(url, method=method), expected_urls={url})
                self.assertEqual(raised.exception.diagnostic, {
                    'http_status': 403, 'request_stage': stage,
                    'error_code': 'edge_access_denied'})
                self.assertNotIn('private', str(raised.exception))
                self.assertNotIn('https://', str(raised.exception))
                self.assertTrue(body.closed)
                self.assertEqual(opener.open.call_count, 1)

    def test_non_edge_http_error_is_not_reported_as_edge_denial(self):
        error = HTTPError(SEARCH_URL, 403, 'Forbidden', None, BytesIO(b'CSRF failed'))
        opener = unittest.mock.Mock()
        opener.open.side_effect = error
        with self.assertRaises(SenateHttpError) as raised:
            SenateEfdClient(opener=opener)._open(Request(SEARCH_URL, method='POST'),
                                               expected_urls={SEARCH_URL})
        self.assertEqual(raised.exception.diagnostic['error_code'], 'http_error')

    def test_cli_failure_writes_diagnostic_but_preserves_prior_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            discovery, failure = root / 'discovery.json', root / 'failure.json'
            discovery.write_bytes(b'prior complete discovery')
            args = ['snapshot', 'discover-senate-efd', '--archive', str(root / 'archive'),
                    '--output', str(discovery), '--failure-output', str(failure),
                    '--submitted-start-date', '2026-01-01']
            error = SenateHttpError(403, Request(HOME_URL), edge_denied=True)
            with patch.object(sys, 'argv', args), patch.dict(os.environ, {
                    'SENATE_EFD_COLLECTION_ENABLED': 'true',
                    'SENATE_EFD_TERMS_ACKNOWLEDGED': 'true'}), patch.object(
                        cli, 'discover_ptrs', side_effect=error), self.assertRaises(SystemExit) as result:
                cli.main()
            self.assertEqual(result.exception.code, 2)
            self.assertEqual(discovery.read_bytes(), b'prior complete discovery')
            self.assertFalse((root / 'archive').exists())
            diagnostic = json.loads(failure.read_text(encoding='utf-8'))
            self.assertEqual(diagnostic['error_code'], 'edge_access_denied')
            self.assertEqual(diagnostic['http_status'], 403)


@unittest.skipUnless(bash_path(), 'Git Bash or bash is required')
class SourceWorkflowRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote, self.review = self.root / 'remote.git', self.root / 'review'
        self.git('init', '--bare', str(self.remote))
        self.git('init', str(self.review))
        self.git('-C', str(self.review), 'config', 'user.name', 'Recovery test')
        self.git('-C', str(self.review), 'config', 'user.email', 'test@example.invalid')
        self.git('-C', str(self.review), 'remote', 'add', 'origin', str(self.remote))
        self.write('README.md', {})
        self.git('-C', str(self.review), 'add', '.')
        self.git('-C', str(self.review), 'commit', '-m', 'initial')
        self.git('-C', str(self.review), 'push', 'origin', 'HEAD:refs/heads/review')
        self.env = {**os.environ, 'REVIEW_ROOT': self.review.as_posix(),
                    'STATE_ROOT': self.review.as_posix(),
                    'PYTHONPATH': str(ROOT / 'backend/src'),
                    'WORKFLOW_RUN_URL': 'https://github.com/example/repo/actions/runs/123',
                    'COLLECT_OUTCOME': 'failure'}

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True,
                              text=True, encoding='utf-8').stdout.strip()

    def write(self, path, value):
        target = self.review / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding='utf-8')

    def run_shell(self, script):
        path = self.root / 'step.sh'
        # Match the workflow's code checkout while retaining each test's local roots.
        target=self.root/'backend/src'
        if not target.exists():
            shutil.copytree(ROOT/'backend/src',target,ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
        path.write_text(script, encoding='utf-8', newline='\n')
        return subprocess.run([bash_path(), '-e', path.as_posix()], cwd=self.root,
                              env=self.env, text=True, encoding='utf-8',
                              capture_output=True, timeout=60)

    def test_failure_writer_retains_last_complete_source_for_shared_publication(self):
        prior = {'schema_version': 'senate-source-run/v1',
                 'status': 'catalog_ready_for_review',
                 'catalog': {'status': 'ok', 'retrieved_at': '2026-10-23T07:42:50Z',
                             'sha256': 'a' * 64},
                 'reports': {'evidence_count': 129},
                 'roster': {'sha256': 'b' * 64},
                 'historical_roster': {'sha256': 'c' * 64}}
        self.write('status/senate_efd.json', prior)
        self.git('-C', str(self.review), 'add', '.')
        self.git('-C', str(self.review), 'commit', '-m', 'prior source')
        self.git('-C', str(self.review), 'push', 'origin', 'HEAD:refs/heads/state')
        local = self.root / '.local'
        local.mkdir()
        (local / 'senate-efd-failure.json').write_text(json.dumps({
            'http_status': 403, 'request_stage': 'agreement_page',
            'error_code': 'edge_access_denied', 'unexpected': 'do not publish'}), encoding='utf-8')
        script = step_script('senate.yml',
                             'Record failed catalog collection without advancing source data')
        result = self.run_shell(script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads((self.review / 'status/senate_efd.json').read_bytes())
        self.assertEqual(saved['status'], 'failed')
        for key in ('catalog', 'reports', 'roster', 'historical_roster'):
            self.assertEqual(saved[key], prior[key])
        self.assertEqual(saved['last_collection_attempt']['http_status'], 403)
        self.assertNotIn('unexpected', saved['last_collection_attempt'])



if __name__ == '__main__':
    unittest.main()
