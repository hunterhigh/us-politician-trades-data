from copy import deepcopy
from datetime import date
import unittest
from urllib.error import HTTPError

from test_twelve_data_market import Response, candidate
from unison_snapshot.market_store import PublishedTwelveDataCache
from unison_snapshot.twelve_data_market import (TwelveDataClient, _fingerprint, _history,
                                                _schedule, supplement)


class Clock:
    now = 0.0
    def time(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class QuotaTests(unittest.TestCase):
    def client(self, limit, used=0):
        clock, calls = Clock(), []
        def opener(request, timeout):
            calls.append((clock.now, request.full_url.split('?')[0].rsplit('/', 1)[-1]))
            if '/api_usage?' in request.full_url:
                return Response({'plan_limit': limit, 'current_usage': used})
            return Response({'status': 'ok', 'data': []})
        return TwelveDataClient('test', opener=opener, sleeper=clock.sleep,
                                monotonic=clock.time), clock, calls

    def test_verified_plan_controls_pacing_and_probe_happens_once(self):
        for limit in (8, 610):
            client, clock, calls = self.client(limit)
            for _ in range(100):
                client.search('ABC')
            self.assertEqual(client.request_count, 101)
            self.assertEqual(client.plan_limit, limit)
            self.assertEqual(sum(endpoint == 'api_usage' for _, endpoint in calls), 1)
            self.assertAlmostEqual(clock.now, 100 * 68 / limit)
            self.assertEqual(client.metrics()['quota_status'], 'verified')

    def test_exhausted_minute_waits_before_data_request(self):
        client, clock, calls = self.client(8, 8)
        client.search('ABC')
        self.assertGreaterEqual(calls[1][0], 60)

    def test_unknown_quota_keeps_existing_pace_without_poisoning_data(self):
        client, clock, calls = self.client('unknown')
        client.search('ABC')
        client.search('ABC')
        self.assertEqual(client.metrics()['quota_status'], 'unavailable_default_pace')
        self.assertEqual(clock.now, 17)

    def test_http_and_json_rate_limits_wait_a_minute_then_retry(self):
        for json_error in (False, True):
            clock, calls = Clock(), []
            def opener(request, timeout):
                calls.append(clock.now)
                if len(calls) == 1:
                    if json_error:
                        return Response({'status': 'error', 'code': 429})
                    raise HTTPError(request.full_url, 429, 'limited', {}, None)
                return Response({'status': 'ok', 'data': []})
            client = TwelveDataClient('private-test-key', opener=opener, sleeper=clock.sleep,
                                      monotonic=clock.time, pace_seconds=0)
            self.assertEqual(client.search('ABC'), [])
            self.assertEqual(calls, [0, 60])
            self.assertNotIn('private-test-key', str(client.metrics()))


class HistoryTests(unittest.TestCase):
    def history(self, *, split=False, old_metadata=False, checked='2026-09-19T00:00:00Z'):
        cached = {'price_history': [{'date': '2025-01-01', 'close': 10.0},
                                    {'date': '2026-08-01', 'close': 11.0},
                                    {'date': '2026-09-10', 'close': 12.0}]}
        full = deepcopy(cached['price_history']) + [{'date': '2026-09-17', 'close': 13.0}]
        if split:
            for point in full:
                point['close'] /= 2
        prior = {'history_start_date': '2024-01-01', 'history_end_date': '2026-09-10',
                 'full_refreshed_at': '2026-09-01T00:00:00Z'}
        calls = []
        class Client:
            def daily(self, ticker, *, start, end):
                calls.append(start.isoformat())
                return {'meta': {'symbol': ticker, 'interval': '1day'}, 'values': [
                    {'datetime': p['date'], 'close': p['close']} for p in full
                    if start.isoformat() <= p['date'] <= end.isoformat()]}
        points, duplicates, audit = _history(Client(), 'ABC', start=date(2024, 1, 1),
                                             end=date(2026, 9, 18), prior={} if old_metadata else prior,
                                             cached=cached, checked_at=checked)
        return points, full, calls, audit

    def test_tail_merges_to_same_full_history(self):
        points, full, calls, audit = self.history()
        self.assertEqual(points, full)
        self.assertEqual(calls, ['2026-07-27'])
        self.assertEqual(audit['history_mode'], 'incremental')
        self.assertEqual(audit['received_point_count'], 3)

    def test_split_overlap_reloads_all_history(self):
        points, full, calls, audit = self.history(split=True)
        self.assertEqual(points, full)
        self.assertEqual(calls, ['2026-07-27', '2024-01-01'])
        self.assertEqual(audit['history_mode'], 'overlap_repair')

    def test_legacy_state_and_monthly_refresh_use_full_read(self):
        for kwargs in ({'old_metadata': True}, {'checked': '2026-10-02T00:00:00Z'}):
            points, full, calls, audit = self.history(**kwargs)
            self.assertEqual(points, full)
            self.assertEqual(calls, ['2024-01-01'])
            self.assertEqual(audit['history_mode'], 'full')


class ScheduleTests(unittest.TestCase):
    def test_both_queues_progress_even_with_a_single_slot_across_runs(self):
        new = [({'ticker': f'N{i}'}, '', None) for i in range(5)]
        refresh = [({'ticker': f'R{i}'}, '', {'checked_at': '2026-09-01T00:00:00Z'}) for i in range(5)]
        selected, cursor = _schedule(new, refresh, 1, 'refresh')
        self.assertEqual(selected[0][0]['ticker'], 'R0')
        selected, cursor = _schedule(new, refresh, 1, cursor)
        self.assertEqual(selected[0][0]['ticker'], 'N0')
        selected, _ = _schedule(new, refresh, 4, cursor)
        self.assertEqual([row[0]['ticker'] for row in selected], ['R0', 'N0', 'R1', 'N1'])
        self.assertEqual(len(_schedule([], refresh, 4, 'new')[0]), 4)
        self.assertEqual(_schedule(new, refresh, 0, 'new'), ([], 'new'))

    def test_failed_refresh_keeps_price_and_success_date_but_does_not_block_queue(self):
        source = candidate()
        entry = {'status': 'accepted', 'checked_at': '2026-09-01T00:00:00Z',
                 'fingerprint': _fingerprint({'Example Fund'})}
        row = {'ticker': 'FUNDX', 'company_name': 'Example Fund Class A',
               'source_id': 'twelve_data_split_adjusted_eod',
               'price_history': [{'date': '2026-09-01', 'close': 12.0}]}
        previous = PublishedTwelveDataCache({'FUNDX': row}, {'entries': {'FUNDX': entry}})
        client = TwelveDataClient('test', pace_seconds=0, opener=lambda *a, **k: Response({
            'status': 'ok', 'meta': {'symbol': 'FUNDX', 'interval': '1day'}, 'values': []}))
        result, audit = supplement(source, client=client, checked_at='2026-09-19T00:00:00Z',
                                   previous_market=previous, limit=1)
        kept = audit['state']['entries']['FUNDX']
        self.assertEqual(kept['checked_at'], entry['checked_at'])
        self.assertEqual(kept['last_attempt_at'], '2026-09-19T00:00:00Z')
        self.assertIn(row, result['security_market_data'])
        queue = [({'ticker': 'FUNDX'}, '', kept), ({'ticker': 'Z'}, '', entry)]
        self.assertEqual(_schedule([], queue, 1, 'refresh')[0][0][0]['ticker'], 'Z')
        self.assertEqual(previous.state['entries']['FUNDX'], entry)
        health = next(r for r in result['source_health'] if r['source_id'] == 'twelve_data_split_adjusted_eod')
        self.assertEqual(health['last_successful_sync_at'], entry['checked_at'])
