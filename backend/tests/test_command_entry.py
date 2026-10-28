"""The installed public entry loads commands and preserves argument boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unison_snapshot.command_registry import COMMANDS


class CommandEntryTests(unittest.TestCase):
    def run_cli(self, *arguments, **extra_env):
        return subprocess.run([sys.executable,'-m','unison_snapshot',*map(str,arguments)],
            env={**os.environ,**extra_env},capture_output=True,text=True,encoding='utf-8')

    def test_all_registered_commands_expose_their_real_parameters(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                result = self.run_cli(command,'--help')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('usage:',result.stdout)

    def test_real_status_command_and_missing_argument_keep_separate_paths(self):
        with tempfile.TemporaryDirectory(prefix='command paths with spaces ') as folder:
            root=Path(folder);status=root/'source status.json';summary=root/'summary.md';output=root/'output.txt'
            status.write_text(json.dumps({'schema_version':'disclosure-assembly-status/v1',
                'status':'deferred','attempted_at':'2026-10-28T00:00:00Z',
                'reason':'source_cutoffs_too_stale','source_cutoffs':{'oge':'2026-10-23T00:00:00Z'}}),encoding='utf-8')
            result=self.run_cli('source-assembly-status','--status',status,
                GITHUB_STEP_SUMMARY=str(summary),GITHUB_OUTPUT=str(output))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'deferred')
            self.assertIn('not a successful data update',summary.read_text())
            self.assertIn('assembly_status=deferred',output.read_text())
            before=summary.read_bytes(),output.read_bytes()
            failed=self.run_cli('source-assembly-status','--status',
                GITHUB_STEP_SUMMARY=str(summary),GITHUB_OUTPUT=str(output))
            self.assertEqual(failed.returncode,2)
            self.assertEqual((summary.read_bytes(),output.read_bytes()),before)


if __name__=='__main__':
    unittest.main()
