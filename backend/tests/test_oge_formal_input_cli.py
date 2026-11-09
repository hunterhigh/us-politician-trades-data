"""Normal empty-seed workflow keeps formal owners and rejects proofless caches."""
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


def seed_formal_main(root, payload):
    bundle = build(payload, generated_at=CUTOFF, allow_production=True, allow_empty_production=True)
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
    return bundle


class FormalInputCliTests(unittest.TestCase):
    def consume(self, *, historical_cache):
        with tempfile.TemporaryDirectory(prefix='oge-formal-cli-') as directory:
            root = Path(directory)
            data = explicit_owner_inputs(root)
            formal = data['generic']['transactions']
            self.assertEqual(formal[0]['owner'], 'Spouse')
            bundle = seed_formal_main(root, data['generic'])
            manifest_before = (root/'manifest.json').read_bytes()
            if historical_cache:
                raw = data['legacy']
                raw.pop('source_rows')
                for row in raw['transactions']: row.pop('cells')
                cache = (data['review_root']/'oge/extractions'/DOCUMENT/raw['source_sha256']/'oge-278t-pdf-v8.json')
                cache.write_bytes(encode(raw))
            (root/'catalog.json').write_bytes(encode(data['catalog']))
            (root/'base.json').write_bytes((ROOT/'backend/data/empty-disclosure.json').read_bytes())
            command = [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                '--catalog', str(root/'catalog.json'), '--extractions-dir', str(data['review_root']/'oge/extractions'),
                '--evidence-root', str(data['evidence_root']), '--base', str(root/'base.json'),
                '--data-cutoff-at', CUTOFF, '--prior-publication',
                '--output', str(root/'candidate.json'), '--audit-output', str(root/'audit.json')]
            env = {**os.environ, 'PYTHONPATH': str(ROOT/'backend/src')}
            for _ in range(2):
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                if historical_cache:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((root/'candidate.json').exists())
                    self.assertEqual((root/'manifest.json').read_bytes(), manifest_before)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                candidate = json.loads((root/'candidate.json').read_bytes())
                audit = json.loads((root/'audit.json').read_bytes())
                self.assertEqual(candidate['transactions'], formal)
                self.assertEqual(audit['prior_publication']['board_sha256'], bundle.manifest['board'])
                self.assertEqual((root/'manifest.json').read_bytes(), manifest_before)
                self.assertEqual(audit['current_qualified_transaction_count'], 1)
                self.assertNotIn('retained_published_fact_count', audit)
                self.assertEqual(audit.get('pending_reports', []), [])

    def test_current_cache_uses_formal_owner_and_id_instead_of_an_empty_predecessor(self):
        self.consume(historical_cache=False)

    def test_pre_inventory_cache_blocks_assembly_without_replacing_formal_facts(self):
        self.consume(historical_cache=True)
