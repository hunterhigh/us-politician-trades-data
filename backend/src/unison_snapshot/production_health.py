"""Independent production health and idempotent GitHub Issue alerts.

Health stays independent of the formal snapshot. Only a verified content failure
can request the existing expected-head-bound rollback workflow; transport errors
never grant that authority.
"""
import argparse
import base64
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .source_freshness import (maximum_age, senate_grace_allowed, senate_max_stale_days,
                               SENATE_WARNING_AGE)

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


def evaluate(manifest, source_status, previous=None, *, now=None, publication_success_at=None, assembly=None, smoke=None, integrity_failures=None, senate_days=None):
    now = now or datetime.now(UTC)
    previous = previous or {}
    prior_sources = previous.get('sources', {})
    sources, alerts, blockers = {}, [], []
    for failure in integrity_failures or []:
        blockers.append({'source': failure['scope'], 'reason': 'content_integrity',
                         'category': failure['category'], 'issue': failure['issue']})
    for source, hours in SOURCES.items():
        run = source_status.get(source, {})
        prior = prior_sources.get(source, {})
        attempted = run.get('run_at')
        catalog = run.get('catalog') or {}
        last_success = run.get('last_successful_collection_at') or (catalog.get('retrieved_at') if source in {'senate_efd', 'oge'} else
            (attempted if run.get('status') in {'ok', 'attention'} else None))
        last_success = last_success or run.get('last_successful_collection_at') or prior.get('last_successful_collection_at')
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
        entry['stale'] = bool(stale or blocked)
        grace = (source == 'senate_efd' and senate_grace_allowed(run) and last_success is not None
                 and now - timestamp(last_success) <= maximum_age(source, senate_days))
        if source == 'senate_efd':
            entry.update(grace_active=bool(grace and reason), max_stale_days=senate_max_stale_days(senate_days))
        sources[source] = entry
        if reason:
            if not grace:
                blockers.append({'source': source, 'reason': reason})
            if now - timestamp(since) >= timedelta(hours=48):
                alerts.append({'key': 'source:' + source, 'title': '[Health] ' + source + ' collection blocked or stale',
                    'detail': json.dumps(entry, ensure_ascii=False, sort_keys=True)})
    if assembly is not None:
        if assembly.get('schema_version') != 'disclosure-assembly-status/v1' or assembly.get('status') not in {'ready', 'deferred'}:
            raise ValueError('Unsupported assembly status')
        cutoffs = assembly.get('source_cutoffs', {})
        if assembly['status'] == 'deferred' or cutoffs:
            if set(cutoffs) != set(SOURCES):
                raise ValueError('Deferred assembly lacks the source waterlines')
            for source, value in cutoffs.items():
                sources[source]['qualified_cutoff_at'] = value
                if now - timestamp(value) > maximum_age(source, senate_days):
                    blockers.append({'source': source, 'reason': 'candidate_stale', 'candidate_cutoff_at': value})
    senate = sources['senate_efd']
    qualified = senate.get('qualified_cutoff_at') or next((row.get('data_cutoff_at')
        for row in manifest.get('source_health', []) if row.get('source_id') == 'senate_efd'), None)
    if qualified:
        senate['qualified_cutoff_at'] = qualified
        senate['stale'] |= now - timestamp(qualified) > timedelta(hours=48)
        if assembly is None and now - timestamp(qualified) > maximum_age('senate_efd', senate_days):
            blockers.append({'source':'senate_efd','reason':'candidate_stale','candidate_cutoff_at':qualified})
    if senate['stale'] and not any(a['key'] == 'source:senate_efd' for a in alerts):
        alerts.append({'key':'source:senate_efd', 'title':'[Health] senate_efd collection blocked or stale',
                       'detail':json.dumps(senate, ensure_ascii=False, sort_keys=True)})
    waterlines = [value for value in (qualified, senate.get('last_successful_collection_at')) if value]
    if waterlines:
        since = min(waterlines, key=timestamp)
        senate['grace_active'] = bool(senate['stale'] and
            senate_grace_allowed(source_status.get('senate_efd', {})) and
            now - timestamp(since) <= maximum_age('senate_efd', senate_days) and
            not any(row['source'] == 'senate_efd' for row in blockers))
        if now - timestamp(since) >= SENATE_WARNING_AGE:
            alerts.append({'key':'senate-stale-20d', 'title':'[Health] Senate data has reached 20 days stale',
                'detail':json.dumps(senate, ensure_ascii=False, sort_keys=True), 'one_shot':True,
                'condition':{'source':'senate_efd','waterline_at':since,'threshold_days':20}})
    published = manifest.get('generated_at')
    candidates = [v for v in (publication_success_at, previous.get('last_successful_publication_check_at'), published) if v]
    last_live = max(candidates, key=timestamp) if candidates else None
    if not last_live or timestamp(last_live) < latest_publication_deadline(now):
        alerts.append({'key': 'publication', 'title': '[Health] Production publication missed its expected window',
            'detail': 'Latest snapshot: ' + str(published) + '\nBlocking sources: ' +
                json.dumps(blockers, ensure_ascii=False)})
    if smoke and smoke.get('status') not in {'passed', 'superseded'}:
        alerts.append({'key':'smoke', 'title':'[Health] Published client read or report smoke failed',
            'detail':json.dumps(smoke, ensure_ascii=False, sort_keys=True),
            'condition':{'commit':smoke['commit'], 'status':smoke['status'], 'recovery':smoke.get('recovery')}})
    return {'schema_version': 'production-health/v1', 'checked_at': now.isoformat(),
        'snapshot_id': manifest.get('snapshot_id'), 'published_at': published,
        'last_successful_publication_check_at': last_live,
        'publication': 'held' if blockers else 'sources_recovered', 'assembly': assembly,
        'blocking_sources': blockers, 'sources': sources, 'alerts': alerts,
        'integrity_failures': integrity_failures or [],
        'snapshot_changed': manifest.get('snapshot_id') != previous.get('snapshot_id'), 'smoke':smoke}


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
        condition = (health['blocking_sources'] if key == 'publication' else
            {k: health['sources'][key.split(':', 1)[1]].get(k) for k in
             (('status','failed_step','http_status','stale','grace_active','max_stale_days')
              if key == 'source:senate_efd' else ('status','failed_step','http_status'))}
                if key.startswith('source:') else alert['condition'])
        fingerprint = hashlib.sha256(json.dumps(condition, sort_keys=True).encode()).hexdigest()
        condition_marker = '<!-- condition:' + fingerprint + ' -->'
        body = marker + '\n' + condition_marker + '\n\n' + alert['detail'] + '\n\nIndependent health status: `health/status/production_health.json`.'
        if not matches:
            issue = api.request('/issues', 'POST', {'title': alert['title'], 'body': body})
            results.append({'key': key, 'number': issue['number'], 'action': 'created'})
        else:
            issue = next((i for i in matches if i.get('state', 'open') == 'open'), matches[0])
            if alert.get('one_shot') and condition_marker in (issue.get('body') or ''):
                results.append({'key':key,'number':issue['number'],'action':'already_notified'})
            elif issue.get('state', 'open') == 'closed':
                api.request('/issues/' + str(issue['number']), 'PATCH', {'state': 'open', 'body': body})
                results.append({'key': key, 'number': issue['number'], 'action': 'reopened'})
            else:
                if condition_marker not in (issue.get('body') or ''):
                    api.request('/issues/' + str(issue['number']), 'PATCH', {'body': body})
                    results.append({'key': key, 'number': issue['number'], 'action': 'updated_condition'})
                else:
                    results.append({'key': key, 'number': issue['number'], 'action': 'already_open'})
    # Close only alerts owned by this observer, and only after a complete read.
    for issue in issues:
        body = issue.get('body') or ''
        prefix = '<!-- production-health:'
        if body.startswith(prefix) and issue.get('state', 'open') == 'open':
            key = body[len(prefix):].split(' -->', 1)[0]
            closable = (key in {'publication', 'smoke', 'senate-stale-20d', *('source:' + s for s in SOURCES)} or
                        key in health.get('quality_observed_scopes', []) or
                        key.startswith('quality-summary:') and any(k.startswith('quality-summary:') for k in current))
            if key not in current and closable:
                api.request('/issues/' + str(issue['number']), 'PATCH', {'state': 'closed', 'state_reason': 'completed'})
                results.append({'key': key, 'number': issue['number'], 'action': 'closed'})
    return results


def successful_publication_at(api):
    # Held assessments, legacy bootstrap and market-only runs grant no freshness.
    completed = []
    for workflow in ('publish.yml', 'publish-complete.yml'):
        try:
            runs = api.request('/actions/workflows/' + workflow + '/runs?branch=code&status=completed&per_page=30')
        except HTTPError as error:
            if error.code != 404:
                raise
            continue
        for run in runs['workflow_runs']:
            if run['conclusion'] != 'success' or run.get('name') != 'Publish complete production snapshot':
                continue
            jobs = api.request('/actions/runs/' + str(run['id']) + '/jobs?per_page=100')
            live = [job['completed_at'] for job in jobs['jobs']
                    if job['name'] == 'publish' and job['conclusion'] == 'success']
            if live:
                completed.extend(live)
                break
    return max(completed, key=timestamp) if completed else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-root', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--health-root', type=Path)
    parser.add_argument('--review-root', type=Path)
    parser.add_argument('--observe-assembly', action='store_true')
    parser.add_argument('--apply-alerts', action='store_true')
    parser.add_argument('--publication-success-at')
    parser.add_argument('--observe-publication-runs', action='store_true')
    parser.add_argument('--observe-source-quality', action='store_true')
    parser.add_argument('--smoke-client-root', type=Path)
    parser.add_argument('--apply-content-rollback', action='store_true')
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    manifest = read(args.main_root / 'manifest.json')
    statuses = {s: read(args.state_root / 'status' / (s + '.json')) for s in SOURCES}
    target = (args.health_root or args.state_root) / 'status/production_health.json'
    prior = read(target) if target.exists() else {}
    api = GitHub(args.repository, os.environ.get('GH_TOKEN'))
    live_at = successful_publication_at(api) if args.observe_publication_runs else args.publication_success_at
    assembly = read(args.review_root / 'status/disclosure_assembly.json') if args.review_root else None
    if args.observe_assembly:
        saved = api.request('/contents/status/disclosure_assembly.json?ref=review')
        assembly = json.loads(base64.b64decode(saved['content']))
    smoke = None
    if args.apply_content_rollback and not args.smoke_client_root:
        parser.error('Content recovery requires a real client smoke')
    if args.smoke_client_root:
        import subprocess
        from .publication_smoke import observe_smoke
        commit = subprocess.check_output(['git','-C',str(args.main_root),'rev-parse','HEAD'], text=True).strip()
        smoke = observe_smoke(api, repository=args.repository, commit=commit, manifest=manifest,
            client_root=args.smoke_client_root, health_root=args.health_root or args.state_root,
            apply=args.apply_content_rollback)
    from .content_integrity import open_failures
    health = evaluate(manifest, statuses, prior, publication_success_at=live_at, assembly=assembly, smoke=smoke,
                      integrity_failures=open_failures(api))
    if args.observe_source_quality:
        from .source_quality import weekly
        current = {}
        review_commit = api.request('/git/ref/heads/review')['object']['sha']
        for source in SOURCES:
            try:
                saved = api.request('/contents/status/quality/' + source + '.json?ref=' + review_commit)
            except HTTPError as error:
                if error.code != 404:
                    raise
                continue
            current[source] = json.loads(base64.b64decode(saved['content']))
        history_path = (args.health_root or args.state_root) / 'status/quality_history.json'
        history, alerts = weekly(current, read(history_path) if history_path.exists() else None)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = history_path.with_suffix('.next.json')
        temporary.write_text(json.dumps(history, sort_keys=True) + '\n', encoding='utf-8'); temporary.replace(history_path)
        health['quality_review_commit'] = review_commit
        health['quality_complete'] = (set(current) == set(SOURCES) and all(
            datetime.now(UTC) - timestamp(value['observed_at']) <= timedelta(hours=48)
            and timestamp(value['observed_at']).isocalendar()[:2] == datetime.now(UTC).isocalendar()[:2]
            for value in current.values()))
        health['quality_observed_scopes'] = ['quality:' + source + ':' + scope['scope_id']
            for source, value in current.items()
            if datetime.now(UTC) - timestamp(value['observed_at']) <= timedelta(hours=48)
            and timestamp(value['observed_at']).isocalendar()[:2] == datetime.now(UTC).isocalendar()[:2]
            for scope in value['scopes']]
        health['alerts'].extend(alerts)
    if args.apply_alerts:
        health['issue_actions'] = reconcile_issues(api, health)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(health, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
    print(json.dumps({'publication': health['publication'], 'alerts': len(health['alerts']), 'output': str(target)}))


if __name__ == '__main__':
    main()
