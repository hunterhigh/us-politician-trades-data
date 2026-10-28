from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.builder import normalize
from unison_snapshot.content_integrity import open_failures, report
from unison_snapshot.corrections import validate_correction
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_archived_pdf
from unison_snapshot.commands import publication_gate as command
import test_publication_gate as gate_fixtures


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receipts = self.root / 'receipts'
        self.environment = patch.dict(os.environ, {'UNISON_INTEGRITY_SCOPE': 'house_clerk',
            'UNISON_INTEGRITY_DIRECTORY': str(self.receipts), 'GITHUB_RUN_ID': '123', 'GITHUB_SHA': 'a' * 40})
        self.environment.start(); self.addCleanup(self.environment.stop)

    def test_real_hash_correction_and_contract_failures_record_types_without_source_values(self):
        pdf = self.root / 'report.pdf'; pdf.write_bytes(b'%PDF-1.7\nsource content')
        metadata = self.root / 'report.json'
        metadata.write_text(json.dumps({'archive_path': 'report.pdf', 'sha256': '0' * 64}))
        with self.assertRaises(HouseIndexError): parse_archived_pdf(self.root, metadata)
        with self.assertRaises(ValueError): validate_correction({'private_source': 'must-not-appear'})
        with self.assertRaises(ValueError): normalize({'private_source': 'must-not-appear'})
        raw = b''.join(path.read_bytes() for path in self.receipts.glob('*.json'))
        values = [json.loads(path.read_bytes()) for path in self.receipts.glob('*.json')]
        self.assertEqual({value['category'] for value in values}, {'official_hash', 'correction_assertion', 'snapshot_contract'})
        self.assertNotIn(b'must-not-appear', raw)
        self.assertNotIn(b'source content', raw)
        self.assertEqual(pdf.read_bytes(), b'%PDF-1.7\nsource content')

    def test_issues_are_reused_and_unrelated_or_spoofed_issues_never_block(self):
        with self.assertRaises(ValueError): validate_correction({})
        class Api:
            def __init__(self):
                self.posts = 0
                self.issues = [{'number': 10, 'user': {'login': 'someone'},
                    'body': '<!-- source-integrity:house_clerk:official_hash -->'}]
            def request(self, path, method='GET', payload=None):
                if method == 'GET': return self.issues
                if method == 'POST':
                    self.posts += 1
                    self.issues.append({'number': 11, 'user': {'login': 'github-actions[bot]'}, **payload})
                    return self.issues[-1]
                self.issues[-1].update(payload)
        api = Api()
        self.assertEqual(open_failures(api), [])
        self.assertEqual(report(api, self.receipts)[0]['issue'], 11)
        self.assertEqual(report(api, self.receipts)[0]['issue'], 11)
        self.assertEqual(api.posts, 1)
        self.assertEqual(open_failures(api), [{'scope': 'house_clerk', 'category': 'correction_assertion', 'issue': 11}])

    def test_normal_publication_command_holds_on_integrity_even_with_fresh_sources(self):
        candidates, statuses = gate_fixtures.PublicationGateTests().inputs()
        stamp = datetime.now(timezone.utc).isoformat()
        for source in candidates:
            candidates[source]['meta']['data_cutoff_at'] = stamp
            candidates[source]['source_health'][0]['data_cutoff_at'] = stamp
            statuses[source].update(run_at=stamp, catalog={'retrieved_at': stamp})
        output = self.root / 'gate.json'
        args = ['gate', '--review-commit', 'a' * 40, '--state-commit', 'b' * 40,
                '--repository', 'owner/repository', '--output', str(output), '--require-ready']
        with patch.object(sys, 'argv', args), patch.object(command, 'from_git', return_value=(candidates, statuses, {})), \
                patch.object(command, 'open_failures', return_value=[{'scope': 'house_clerk', 'category': 'official_hash', 'issue': 11}]):
            with self.assertRaises(SystemExit) as stopped: command.main()
        self.assertEqual(stopped.exception.code, 3)
        result = json.loads(output.read_bytes())
        self.assertFalse(result['ready'])
        self.assertEqual(result['blocking_sources'][0]['reason'], 'content_integrity')


if __name__ == '__main__': unittest.main()
