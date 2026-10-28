"""Normal empty-seed workflow keeps formal owners and defers proofless caches."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_oge_default_explicit_owner_status import explicit_owner_inputs
from test_oge_owner_correction import CUTOFF, DOCUMENT
from unison_snapshot.builder import build
from unison_snapshot.codec import encode
from unison_snapshot.materialize import materialize

ROOT = Path(__file__).resolve().parents[2]


class FormalInputCliTests(unittest.TestCase):
    def consume(self, *, historical_cache):
        with tempfile.TemporaryDirectory(prefix='oge-formal-cli-') as directory:
            root = Path(directory)
            data = explicit_owner_inputs(root)
            formal = data['generic']['transactions']
            self.assertEqual(formal[0]['owner'], 'Spouse')
            bundle = build(data['generic'], generated_at=CUTOFF, allow_production=True)
            materialize(root, bundle)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.STDOUT)
            git('init', '-q')
            git('config', 'user.name', 'Formal input control')
            git('config', 'user.email', 'test@example.invalid')
            git('add', '--', *[name for name in ('manifest.json', 'board', 'people', 'tickers')
                               if (root/name).exists()])
            git('commit', '-qm', 'Source-generated formal predecessor')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            manifest_before = (root/'manifest.json').read_bytes()
            if historical_cache:
                raw = data['legacy']
                raw.pop('source_rows')
                for row in raw['transactions']: row.pop('cells')
                cache = (data['review_root']/'oge/extractions'/DOCUMENT/raw['source_sha256']/'oge-278t-pdf-v2.json')
                cache.write_bytes(encode(raw))
            (root/'catalog.json').write_bytes(encode(data['catalog']))
            (root/'base.json').write_bytes((ROOT/'backend/data/empty-disclosure.json').read_bytes())
            command = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                '--catalog', str(root/'catalog.json'), '--extractions-dir', str(data['review_root']/'oge/extractions'),
                '--evidence-root', str(data['evidence_root']), '--base', str(root/'base.json'),
                '--data-cutoff-at', CUTOFF, '--allow-incomplete-reports',
                '--output', str(root/'candidate.json'), '--audit-output', str(root/'audit.json')]
            env = {**os.environ, 'PYTHONPATH': str(ROOT/'backend/src')}
            for _ in range(2):
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                candidate = json.loads((root/'candidate.json').read_bytes())
                audit = json.loads((root/'audit.json').read_bytes())
                self.assertEqual(candidate['transactions'], formal)
                self.assertEqual(audit['prior_publication']['board_sha256'], bundle.manifest['board'])
                self.assertEqual((root/'manifest.json').read_bytes(), manifest_before)
                if historical_cache:
                    self.assertEqual(audit['current_qualified_transaction_count'], 0)
                    self.assertEqual(audit['retained_published_fact_count'], 1)
                    self.assertEqual(audit['pending_reports'][0]['document_id'], DOCUMENT)
                else:
                    self.assertEqual(audit['current_qualified_transaction_count'], 1)
                    self.assertEqual(audit.get('pending_reports', []), [])

    def test_current_cache_uses_formal_owner_and_id_instead_of_an_empty_predecessor(self):
        self.consume(historical_cache=False)

    def test_pre_inventory_cache_retains_formal_facts_without_current_qualification(self):
        self.consume(historical_cache=True)
