"""Record explicit content-boundary failures without logging source values or secrets."""
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import uuid

CATEGORIES = {'correction_assertion', 'snapshot_contract', 'official_hash', 'old_fact_delta'}
SCOPES = {'house_clerk', 'senate_efd', 'oge', 'publication', 'market'}
MARKER = '<!-- source-integrity:'


def record_failure(category):
    directory = os.environ.get('UNISON_INTEGRITY_DIRECTORY')
    scope = os.environ.get('UNISON_INTEGRITY_SCOPE')
    if not directory or not scope:
        return
    if scope not in SCOPES or category not in CATEGORIES:
        raise ValueError('Unknown content failure scope or category')
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    value = {'schema_version': 'source-integrity-failure/v1', 'scope': scope,
             'category': category, 'recorded_at': datetime.now(timezone.utc).isoformat(),
             'run_id': os.environ.get('GITHUB_RUN_ID'), 'code_commit': os.environ.get('GITHUB_SHA')}
    target = root / (uuid.uuid4().hex + '.json')
    target.write_bytes((json.dumps(value, sort_keys=True) + '\n').encode())


def content_boundary(category):
    def decorate(function):
        @wraps(function)
        def checked(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (ValueError, RuntimeError, KeyError, TypeError):
                record_failure(category)
                raise
        return checked
    return decorate


def integrity_error(error_type, message):
    record_failure('official_hash')
    return error_type(message)


def open_failures(api):
    found = []
    page = 1
    while True:
        batch = api.request(f'/issues?state=open&per_page=100&page={page}')
        for issue in batch:
            if 'pull_request' in issue or issue.get('user', {}).get('login') != 'github-actions[bot]':
                continue
            match = re.match(re.escape(MARKER) + r'([a-z_]+):([a-z_]+) -->', issue.get('body') or '')
            if match and match[1] in SCOPES and match[2] in CATEGORIES:
                found.append({'scope': match[1], 'category': match[2], 'issue': issue['number']})
        if len(batch) < 100:
            return found
        page += 1


def report(api, directory):
    paths = sorted(Path(directory).glob('*.json'))
    failures = {}
    for path in paths:
        value = json.loads(path.read_bytes())
        if (value.get('schema_version') != 'source-integrity-failure/v1' or
                value.get('scope') not in SCOPES or value.get('category') not in CATEGORIES):
            raise ValueError('Invalid content failure receipt')
        if not re.fullmatch('[0-9]+', str(value.get('run_id'))) or not re.fullmatch('[0-9a-f]{40}', str(value.get('code_commit'))):
            raise ValueError('Content failure requires a fixed Actions run and code commit')
        failures[value['scope'], value['category']] = value
    if not failures:
        return []
    existing = {(row['scope'], row['category']): row for row in open_failures(api)}
    actions = []
    for key, value in failures.items():
        marker = MARKER + ':'.join(key) + ' -->'
        body = (marker + '\n\nContent validation failed; related candidates remain blocked.\n'
                'Category: `' + value['category'] + '`; source: `' + value['scope'] + '`.\n'
                'Actions run: `' + value['run_id'] + '`; code: `' + value['code_commit'] + '`.\n'
                'Repair the cause, verify the normal affected path, then close this issue. '
                'A retry or an unrelated successful run cannot clear this condition.')
        if key in existing:
            number = existing[key]['issue']
            api.request('/issues/' + str(number), 'PATCH', {'body': body})
        else:
            number = api.request('/issues', 'POST', {'title': '[Integrity] ' + ' / '.join(key), 'body': body})['number']
        actions.append({'scope': key[0], 'category': key[1], 'issue': number})
    return actions
