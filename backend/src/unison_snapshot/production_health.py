"""Independent production health and idempotent GitHub Issue alerts.

This observer cannot publish or roll back a snapshot. Transport failures preserve
the previous health record and never count as proof of corrupt snapshot content.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UTC = timezone.utc
SOURCES = {'house_clerk': 48, 'senate_efd': 48, 'oge': 48}


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Health timestamps require a timezone')
    return result.astimezone(UTC)


def latest_publication_deadline(now):
    """Existing 22:31 UTC weekday schedule, with a two-hour completion allowance."""
    for days in range(8):
        day = (now - timedelta(days=days)).replace(hour=22, minute=31, second=0, microsecond=0)
        deadline = day + timedelta(hours=2)
        if day.weekday() < 5 and deadline <= now:
            return day
    raise ValueError('No publication window')


def evaluate(manifest, source_status, previous=None, *, now=None, publication_success_at=None):
    now = now or datetime.now(UTC)
    previous = previous or {}
    prior_sources = previous.get('sources', {})
    sources, alerts, blockers = {}, [], []
    for source, hours in SOURCES.items():
        run = source_status.get(source, {})
        prior = prior_sources.get(source, {})
        attempted = run.get('run_at')
        catalog = run.get('catalog', {})
        last_success = (catalog.get('retrieved_at') if source in {'senate_efd', 'oge'} else
            (attempted if run.get('status') == 'ok' else None))
        last_success = last_success or prior.get('last_successful_collection_at')
        last_attempt = run.get('last_collection_attempt', {})
        if not last_success:
            published_health = next((h for h in manifest.get('source_health', []) if h.get('source_id') == source), {})
            last_success = published_health.get('last_successful_sync_at')
        blocked = (run.get('status') in {'blocked', 'disabled', 'authorization_required'} or
            (run.get('status') == 'failed' and last_attempt.get('http_status') == 403))
        stale = last_success is None or now - timestamp(last_success) > timedelta(hours=hours)
        reason = ('source_blocked' if blocked else 'source_stale' if stale else None)
        since = (prior.get('unhealthy_since') or last_success or attempted or now.isoformat()) if reason else None
        transient = run.get('status') in {'failed', 'unavailable'}
        entry = {'status': 'blocked' if blocked else 'stale' if stale else 'retry_pending' if transient else 'ok',
            'last_attempt_at': attempted, 'last_successful_collection_at': last_success,
            'unhealthy_since': since, 'detail': run.get('detail'),
            'failed_step': last_attempt.get('failed_step'),
            'http_status': last_attempt.get('http_status'),
            'workflow_run_url': last_attempt.get('workflow_run_url') or run.get('workflow_run_url')}
        sources[source] = entry
        if reason:
            blockers.append({'source': source, 'reason': reason})
            if now - timestamp(since) >= timedelta(hours=48):
                alerts.append({'key': 'source:' + source, 'title': '[Health] ' + source + ' collection blocked or stale',
                    'detail': json.dumps(entry, ensure_ascii=False, sort_keys=True)})
    published = manifest.get('generated_at')
    candidates = [v for v in (publication_success_at, previous.get('last_successful_publication_check_at'), published) if v]
    last_live = max(candidates, key=timestamp) if candidates else None
    if not last_live or timestamp(last_live) < latest_publication_deadline(now):
        alerts.append({'key': 'publication', 'title': '[Health] Production publication missed its expected window',
            'detail': 'Latest snapshot: ' + str(published) + '\nBlocking sources: ' +
                json.dumps(blockers, ensure_ascii=False)})
    return {'schema_version': 'production-health/v1', 'checked_at': now.isoformat(),
        'snapshot_id': manifest.get('snapshot_id'), 'published_at': published,
        'last_successful_publication_check_at': last_live,
        'publication': 'held' if blockers else 'ready_for_normal_publication',
        'blocking_sources': blockers, 'sources': sources, 'alerts': alerts,
        'snapshot_changed': manifest.get('snapshot_id') != previous.get('snapshot_id')}


class GitHub:
    def __init__(self, repository, token=None):
        if repository.count('/') != 1 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-' for c in repository):
            raise ValueError('Invalid repository')
        self.base = 'https://api.github.com/repos/' + repository
        self.token = token

    def request(self, path, method='GET', payload=None):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'politician-production-health'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        data = None if payload is None else json.dumps(payload).encode()
        for attempt in range(3):
            try:
                with urlopen(Request(self.base + path, data=data, headers=headers, method=method), timeout=25) as response:
                    body = response.read()
                    return json.loads(body) if body else None
            except (HTTPError, URLError, TimeoutError) as exc:
                # Mutations are reconciled on the next run rather than retried after
                # an ambiguous response (which could otherwise create duplicates).
                retryable = not isinstance(exc, HTTPError) or exc.code in {429, 500, 502, 503, 504}
                if method != 'GET' or not retryable or attempt == 2:
                    raise
                time.sleep(2 ** attempt)


def reconcile_issues(api, health):
    issues = []
    page = 1
    while True:
        batch = api.request(f'/issues?state=all&per_page=100&page={page}')
        issues.extend(issue for issue in batch if 'pull_request' not in issue)
        if len(batch) < 100:
            break
        page += 1
    current = {alert['key']: alert for alert in health['alerts']}
    results = []
    for key, alert in current.items():
        marker = '<!-- production-health:' + key + ' -->'
        matches = [issue for issue in issues if marker in (issue.get('body') or '')]
        body = marker + '\n\n' + alert['detail'] + '\n\nIndependent health status: `health/status/production_health.json`.'
        if not matches:
            issue = api.request('/issues', 'POST', {'title': alert['title'], 'body': body})
            results.append({'key': key, 'number': issue['number'], 'action': 'created'})
        else:
            issue = next((i for i in matches if i.get('state', 'open') == 'open'), matches[0])
            if issue.get('state', 'open') == 'closed':
                api.request('/issues/' + str(issue['number']), 'PATCH', {'state': 'open', 'body': body})
                results.append({'key': key, 'number': issue['number'], 'action': 'reopened'})
            else:
                results.append({'key': key, 'number': issue['number'], 'action': 'already_open'})
    # Close only alerts owned by this observer, and only after a complete read.
    for issue in issues:
        body = issue.get('body') or ''
        prefix = '<!-- production-health:'
        if body.startswith(prefix) and issue.get('state', 'open') == 'open':
            key = body[len(prefix):].split(' -->', 1)[0]
            if key not in current and key in {'publication', *('source:' + s for s in SOURCES)}:
                api.request('/issues/' + str(issue['number']), 'PATCH', {'state': 'closed', 'state_reason': 'completed'})
                results.append({'key': key, 'number': issue['number'], 'action': 'closed'})
    return results


def successful_publication_at(api):
    # A successful market-only job or canceled run is not a publication check.
    runs = api.request('/actions/workflows/publish-complete.yml/runs?branch=code&status=completed&per_page=30')
    for run in runs['workflow_runs']:
        if run['conclusion'] != 'success':
            continue
        jobs = api.request('/actions/runs/' + str(run['id']) + '/jobs?per_page=100')
        for job in jobs['jobs']:
            if job['name'] == 'publish' and job['conclusion'] == 'success':
                return job['completed_at']
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-root', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--health-root', type=Path)
    parser.add_argument('--apply-alerts', action='store_true')
    parser.add_argument('--publication-success-at')
    parser.add_argument('--observe-publication-runs', action='store_true')
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    manifest = read(args.main_root / 'manifest.json')
    statuses = {s: read(args.state_root / 'status' / (s + '.json')) for s in SOURCES}
    target = (args.health_root or args.state_root) / 'status/production_health.json'
    prior = read(target) if target.exists() else {}
    api = GitHub(args.repository, os.environ.get('GH_TOKEN'))
    live_at = successful_publication_at(api) if args.observe_publication_runs else args.publication_success_at
    health = evaluate(manifest, statuses, prior, publication_success_at=live_at)
    if args.apply_alerts:
        health['issue_actions'] = reconcile_issues(api, health)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(health, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
    print(json.dumps({'publication': health['publication'], 'alerts': len(health['alerts']), 'output': str(target)}))


if __name__ == '__main__':
    main()
