from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unison_snapshot.source_quality import snapshot, weekly


class QualityTests(unittest.TestCase):
    def scopes(self, good=90, bad=10):
        return [{'source_id': 'oge', 'scope_id': 'oge_ptr', 'scope': {'document_type': '278t'},
            'reports': [{'record_key': 'report-1', 'source_versions': ['a' * 64],
                'qualified_transaction_count': good, 'quarantined_row_count': bad,
                'qualification_state': 'partial', 'reason_codes': []},
                {'record_key': 'legacy-observed-slots', 'qualification_state': 'partial',
                 'qualified_transaction_count': 100, 'quarantined_row_count': None,
                 'reason_codes': [], 'source_versions': ['b' * 64]}]}]

    def observed(self, scopes, stamp):
        return snapshot('oge', scopes, {'unchanged_facts': []}, observed_at=stamp)

    def test_unknown_whole_source_rows_are_excluded_and_explicitly_counted(self):
        scopes = self.scopes(); before = deepcopy(scopes)
        value = self.observed(scopes, '2026-10-20T00:00:00Z')['scopes'][0]
        self.assertEqual(value['row_quarantine'], {'numerator': 10, 'denominator': 100})
        self.assertEqual(value['unknown_row_report_count'], 1)
        self.assertEqual(scopes, before)

    def test_strict_five_point_boundary_and_new_layout_use_last_week(self):
        old = self.observed(self.scopes(), '2026-10-20T00:00:00Z')
        history, _ = weekly({'oge': old}, now=datetime(2026, 10, 20, tzinfo=timezone.utc))
        current = self.observed(self.scopes(85, 15), '2026-10-27T00:00:00Z')
        _, alerts = weekly({'oge': current}, history, now=datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual([alert['key'] for alert in alerts], ['quality-summary:2026-W44'])
        current = self.observed(self.scopes(84, 16), '2026-10-27T00:00:00Z')
        _, alerts = weekly({'oge': current}, history, now=datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(alerts[0]['condition']['increased_metrics'], ['row_quarantine'])
        scopes = self.scopes(); scopes[0]['reports'][0]['reason_codes'] = ['unknown_layout:unrecognized_table']
        current = self.observed(scopes, '2026-10-27T00:00:00Z')
        _, alerts = weekly({'oge': current}, history, now=datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(len(alerts[0]['condition']['new_unknown_layouts']), 1)

    def test_old_saved_observation_never_becomes_this_weeks_fresh_sample(self):
        old = self.observed(self.scopes(), '2026-10-20T00:00:00Z')
        history, _ = weekly({'oge': old}, now=datetime(2026, 10, 20, tzinfo=timezone.utc))
        result, alerts = weekly({'oge': old}, history, now=datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(result['weeks']['2026-W44'], {})
        self.assertEqual(alerts, [])
        bad = deepcopy(old); bad['scopes'][0]['row_quarantine']['denominator'] = 1
        with self.assertRaisesRegex(ValueError, 'denominator'):
            weekly({'oge': bad}, history, now=datetime(2026, 10, 27, tzinfo=timezone.utc))


if __name__ == '__main__': unittest.main()
