"""Shared parser lifecycle: version changes must not preserve stale successes."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from unison_snapshot.parser_replay import ParserTarget, require_complete, suite_from_modules
from unison_snapshot.oge_reports import parser_target, PARSER_VERSION


class ParserReplayTests(unittest.TestCase):
    def test_each_source_reopens_both_success_and_failure_on_version_change(self):
        for source in ('house-ptr', 'house-annual', 'oge', 'whitehouse', 'senate'):
            with self.subTest(source=source):
                target = ParserTarget(source + '/v2', {'source_sha256': 'a'*64})
                old = {'parser_version': source + '/v1', 'source_sha256': 'a'*64}
                current = dict(old, parser_version=target.version)
                self.assertEqual(target.select(old), 'parse')
                self.assertEqual(target.select(None, old), 'parse')
                self.assertEqual(target.select(current), 'reuse')
                self.assertEqual(target.select(None, current), 'failure')
                self.assertEqual(target.select(current, old), 'reuse')
                self.assertEqual(target.select(current, current), 'parse')
                self.assertEqual(target.select(current, dependencies=(False,)), 'parse')
                self.assertEqual(target.select(dict(current, source_sha256='b'*64)), 'parse')

    def test_315_pre_inventory_results_cannot_stay_current_with_24_complete_results(self):
        metadata = {'document_id':'a'*32, 'sha256':'b'*64, 'document_url':'https://example.invalid/a.pdf'}
        with patch('unison_snapshot.oge_reports.source_correction', return_value=None):
            target = parser_target(metadata, PARSER_VERSION)
        complete = dict(target.binding, parser_version=PARSER_VERSION, transactions=[],
            quarantined=[], document_reasons=[], evidence_complete=True, source_rows=[])
        legacy = dict(complete, parser_version='oge-278t-pdf/v2')
        legacy.pop('source_rows')
        states = [target.select(value) for value in [legacy]*315 + [complete]*24]
        self.assertEqual((states.count('parse'), states.count('reuse')), (315, 24))
        for change in ({'source_rows': None}, {'transactions': [{}]}, {'source_sha256':'c'*64}):
            self.assertEqual(target.select(complete | change), 'parse')
        with self.assertRaisesRegex(ValueError, '315 current parser results missing'):
            require_complete(range(339), range(24), label='OGE')
        require_complete(range(339), range(339), label='OGE')

    def test_failure_suite_tracks_declared_versions_in_all_routes(self):
        before = suite_from_modules('house_ptr', 'house_native_contrast')
        from pathlib import Path
        original = Path.read_text
        def changed(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            return text.replace('house-ptr-native-bichrome/v1', 'house-ptr-native-bichrome/v2')
        with patch.object(Path, 'read_text', changed):
            self.assertNotEqual(before, suite_from_modules('house_ptr', 'house_native_contrast'))


if __name__ == '__main__':
    unittest.main()
