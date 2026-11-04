"""Execute temporary-ref cleanup against a real disposable Git remote."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PreflightCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.remote, self.checkout = root / 'remote.git', root / 'checkout'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.remote), str(self.checkout)], check=True, capture_output=True)
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                 'commit', '--allow-empty', '-m', 'original')
        self.original = self.git('rev-parse', 'HEAD').strip()
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                 'commit', '--allow-empty', '-m', 'later')
        self.later = self.git('rev-parse', 'HEAD').strip()
        self.refs = ['main', 'market', 'preflight/123/main', 'preflight/123/market',
                     'preflight/456/main', 'codex/unmerged']
        self.git('push', 'origin', *[f'{self.original}:refs/heads/{ref}' for ref in self.refs])
        git_bash = Path(shutil.which('git') or 'git').resolve().parent.parent / 'bin/bash.exe'
        self.bash = str(git_bash) if os.name == 'nt' and git_bash.exists() else shutil.which('bash')
        if not self.bash:
            self.skipTest('Bash is required to execute the publication cleanup')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.checkout, text=True, stderr=subprocess.PIPE)

    def run_cleanup(self):
        return subprocess.run([self.bash, str(ROOT / '.github/scripts/cleanup-preflight-refs.sh')],
            cwd=self.checkout, capture_output=True, text=True,
            env={**os.environ, 'PREFLIGHT_RUN_ID': '123',
                 'PREFLIGHT_MAIN_SHA': self.original, 'PREFLIGHT_MARKET_SHA': self.original})

    def remote_refs(self):
        return {line.split()[1].removeprefix('refs/heads/'): line.split()[0]
                for line in self.git('ls-remote', '--heads', 'origin').splitlines()}

    def test_cleanup_after_failure_removes_only_owned_refs_and_is_idempotent(self):
        for _ in range(2):
            result = self.run_cleanup()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.remote_refs(), {ref: self.original for ref in self.refs
                if ref not in {'preflight/123/main', 'preflight/123/market'}})

    def test_changed_temporary_ref_blocks_deletion_of_both_refs(self):
        self.git('push', 'origin', f'{self.later}:refs/heads/preflight/123/market')
        before = self.remote_refs()
        result = self.run_cleanup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ref changed', result.stderr)
        self.assertEqual(self.remote_refs(), before)
