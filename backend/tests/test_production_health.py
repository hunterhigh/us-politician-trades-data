from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unison_snapshot.production_health import evaluate, reconcile_issues, latest_publication_deadline


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)
        self.manifest = {'snapshot_id': 'old', 'generated_at': '2026-10-24T01:16:50Z'}
        self.sources = {
            'house_clerk': {'status': 'ok', 'run_at': '2026-10-28T06:00:00Z'},
            'oge': {'status': 'partial', 'run_at': '2026-10-28T07:00:00Z',
                'catalog': {'retrieved_at': '2026-10-28T07:00:00Z'}},
            'senate_efd': {'status': 'blocked', 'run_at': '2026-10-28T04:00:00Z',
                'catalog': {'retrieved_at': '2026-10-23T07:42:50Z'},
                'last_collection_attempt': {'http_status': 403, 'failed_step': 'collect_catalog'}}}

    def test_blocked_source_visible_while_formal_snapshot_unchanged(self):
        h = evaluate(self.manifest, self.sources, now=self.now)
        self.assertEqual(h['publication'], 'held')
        self.assertEqual(h['blocking_sources'], [{'source': 'senate_efd', 'reason': 'source_blocked'}])
        self.assertEqual({a['key'] for a in h['alerts']}, {'publication', 'source:senate_efd'})
        self.assertEqual(h['sources']['senate_efd']['http_status'], 403)
        self.assertNotIn('unhealthy_since', self.sources['senate_efd'])

    def test_recovery_and_successful_no_change_run_clear_alerts(self):
        old = evaluate(self.manifest, self.sources, now=self.now)
        self.sources['senate_efd'].update(status='catalog_ready_for_review',
            catalog={'retrieved_at': '2026-10-28T07:00:00Z'})
        h = evaluate(self.manifest, self.sources, old, now=self.now,
            publication_success_at='2026-10-28T01:00:00Z')
        self.assertEqual(h['alerts'], [])
        self.assertFalse(h['snapshot_changed'])
        self.assertIsNone(h['sources']['senate_efd']['unhealthy_since'])

    def test_weekend_does_not_expect_unscheduled_publication(self):
        now = datetime(2026, 11, 1, 20, tzinfo=timezone.utc)
        self.assertEqual(latest_publication_deadline(now).isoformat(), '2026-10-30T22:31:00+00:00')

    def test_transient_network_failure_with_fresh_source_does_not_hold_publication(self):
        self.sources['senate_efd'].update(status='unavailable', catalog={'retrieved_at': '2026-10-28T07:00:00Z'},
            last_collection_attempt={'http_status': 503, 'failed_step': 'collect_catalog'})
        h = evaluate(self.manifest, self.sources, now=self.now, publication_success_at='2026-10-28T01:00:00Z')
        self.assertEqual(h['alerts'], [])
        self.assertEqual(h['blocking_sources'], [])
        self.assertEqual(h['sources']['senate_efd']['status'], 'retry_pending')

    def test_issue_reconciliation_does_not_repeat_open_alerts_or_close_unrelated_issues(self):
        class Api:
            def __init__(self):
                self.calls = []
            def request(self, path, method='GET', payload=None):
                self.calls.append((path, method, payload))
                if method == 'GET':
                    return [{'number': 1, 'body': '<!-- production-health:source:senate_efd -->'},
                        {'number': 2, 'body': '<!-- production-health:publication -->'},
                        {'number': 3, 'body': 'Unrelated user issue'}]
                return {'number': 4}
        api = Api()
        h = {'alerts': [{'key': 'source:senate_efd', 'title': 'Blocked', 'detail': '403'}]}
        result = reconcile_issues(api, h)
        self.assertEqual([x['action'] for x in result], ['already_open', 'closed'])
        self.assertEqual([x[0] for x in api.calls if x[1] == 'PATCH'], ['/issues/2'])
        self.assertFalse(any(x[1] == 'POST' for x in api.calls))

    def test_missing_or_unzoned_source_time_is_not_a_success(self):
        self.sources['house_clerk']['run_at'] = '2026-10-28T06:00:00'
        with self.assertRaises(ValueError):
            evaluate(self.manifest, self.sources, now=self.now)
