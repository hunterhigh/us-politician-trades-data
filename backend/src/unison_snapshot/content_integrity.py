"""Match failed and verified content checks without exposing source values or secrets."""
import base64
from datetime import datetime, timezone
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import uuid

CATEGORIES = {'correction_assertion', 'snapshot_contract', 'official_hash', 'old_fact_delta'}
SCOPES = {'house_clerk', 'senate_efd', 'oge', 'publication', 'market'}
MARKER = '<!-- source-integrity:'
META = '<!-- integrity-conditions:'


def subject(category, values):
    metadata = values.get('metadata')
    document_hint = metadata.get('document_id') if isinstance(metadata, dict) else None
    if not isinstance(document_hint, str) or not re.fullmatch('[A-Za-z0-9:._-]{1,128}', document_hint):
        document_hint = None
    if category in {'snapshot_contract', 'old_fact_delta'}:
        if document_hint: return 'document:' + document_hint
        path = values.get('metadata_path', values.get('path'))
        if isinstance(path, (str, Path)):
            return 'input-path:' + hashlib.sha256('/'.join(Path(path).parts[-3:]).encode()).hexdigest()
        year=values.get('year')
        return 'year:' + str(year) if isinstance(year,(str,int)) and re.fullmatch('[0-9]{4}',str(year)) else 'scope'
    document = sha = url = None
    for key, value in values.items():
        if isinstance(value, dict):
            nested = value.get('source') if isinstance(value.get('source'), dict) else {}
            document = document or value.get('document_id') or nested.get('document_id')
            sha = sha or value.get('source_sha256') or value.get('sha256')
        elif isinstance(value, Path) and key == 'metadata_path':
            document = document or value.parent.name
            sha = sha or (value.stem if re.fullmatch('[0-9a-f]{64}', value.stem) else None)
        elif isinstance(value, str):
            if key in {'document_id', 'document'}: document = document or value
            elif key == 'source_url': url = value
            elif key in {'source_sha', 'source_sha256'}: sha = sha or value
    if not isinstance(document, str) or not re.fullmatch('[A-Za-z0-9:._-]{1,128}', document): document = None
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha): sha = None
    if category == 'official_hash':
        return json.dumps({'document': document, 'sha256': sha}, sort_keys=True)
    if document: return 'document:' + str(document)
    if url: return 'source-url:' + hashlib.sha256(url.encode()).hexdigest()
    if sha: return 'source-sha256:' + str(sha)
    return 'scope'


def event(category, passed, *, boundary='manual', identity='scope'):
    directory = os.environ.get('UNISON_INTEGRITY_DIRECTORY')
    scope = os.environ.get('UNISON_INTEGRITY_SCOPE')
    if not directory or not scope: return
    if scope not in SCOPES or category not in CATEGORIES:
        raise ValueError('Unknown content failure scope or category')
    pipeline = os.environ.get('GITHUB_WORKFLOW_REF', 'local') + ':' + os.environ.get('GITHUB_JOB', 'local')
    key = hashlib.sha256(json.dumps([pipeline, category, identity]).encode()).hexdigest()
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    value = {'schema_version': 'source-integrity-success/v1' if passed else 'source-integrity-failure/v1',
        'scope': scope, 'category': category, 'condition': key, 'boundary': boundary, 'subject': identity,
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'run_id': os.environ.get('GITHUB_RUN_ID'),
        'code_commit': os.environ.get('GITHUB_SHA')}
    target = root / (key + '.success.json' if passed else uuid.uuid4().hex + '.failure.json')
    temporary = target.with_name(uuid.uuid4().hex + '.tmp')
    temporary.write_bytes((json.dumps(value, sort_keys=True) + '\n').encode()); temporary.replace(target)


def record_failure(category, *, boundary='manual', identity='scope'):
    event(category, False, boundary=boundary, identity=identity)


def content_boundary(category):
    def decorate(function):
        @wraps(function)
        def checked(*args, **kwargs):
            enabled = bool(os.environ.get('UNISON_INTEGRITY_DIRECTORY') and os.environ.get('UNISON_INTEGRITY_SCOPE'))
            values = inspect.signature(function).bind_partial(*args, **kwargs).arguments if enabled else {}
            identity = subject(category, values) if enabled else 'scope'
            if function.__name__ == 'validate_correction': identity = 'scope'
            boundary = function.__module__ + '.' + function.__name__
            effective_category = category
            if enabled and category == 'official_hash' and json.loads(identity)['sha256'] is None:
                effective_category = 'snapshot_contract'
                identity = subject(effective_category, values)
            try:
                result = function(*args, **kwargs)
            except (ValueError, RuntimeError, KeyError, TypeError) as error:
                current=error;seen=set();recorded=False
                while current is not None and id(current) not in seen:
                    seen.add(id(current));recorded=recorded or getattr(current,'_unison_integrity_recorded',False)
                    current=current.__cause__ or current.__context__
                if not recorded:
                    record_failure(effective_category, boundary=boundary, identity=identity)
                    if enabled:error._unison_integrity_recorded=True
                raise
            event(effective_category, True, boundary=boundary, identity=identity)
            if category == 'official_hash':
                event('snapshot_contract', True, boundary=boundary, identity=subject('snapshot_contract', values))
            return result
        return checked
    return decorate


def integrity_error(error_type, message, *, metadata=None):
    values = {'metadata': metadata or {}}
    identity = subject('official_hash', values)
    category = 'official_hash' if json.loads(identity)['sha256'] is not None else 'snapshot_contract'
    record_failure(category, boundary='official_archive_hash', identity=identity if category == 'official_hash' else subject(category, values))
    return error_type(message)


def verified_original_hash(metadata):
    event('official_hash', True, boundary='official_archive_hash',
          identity=subject('official_hash', {'metadata': metadata}))
    event('snapshot_contract', True, boundary='official_archive_hash', identity=subject('snapshot_contract', {'metadata': metadata}))


def open_failures(api):
    found = []; page = 1
    while True:
        batch = api.request(f'/issues?state=open&per_page=100&page={page}')
        for issue in batch:
            if ('pull_request' in issue or issue.get('user', {}).get('login') != 'github-actions[bot]'
                    or issue.get('state', 'open') != 'open'): continue
            body = issue.get('body') or ''
            match = re.match(re.escape(MARKER) + r'([a-z_]+):([a-z_]+) -->', body)
            if not match or match[1] not in SCOPES or match[2] not in CATEGORIES: continue
            metadata = None
            block = re.search(re.escape(META) + r'([A-Za-z0-9+/=]+) -->', body)
            if block:
                try:
                    parsed = json.loads(base64.b64decode(block[1], validate=True))
                    if (parsed.get('schema_version') == 'integrity-conditions/v1' and
                            isinstance(parsed.get('conditions'), list) and len(parsed['conditions']) <= 400 and
                            all(isinstance(key, str) and re.fullmatch('[0-9a-f]{64}', key) for key in parsed['conditions'])):
                        metadata = parsed
                except (ValueError, TypeError): pass
            found.append({'scope': match[1], 'category': match[2], 'issue': issue['number'], 'metadata': metadata})
        if len(batch) < 100: return found
        page += 1


def report(api, directory, *, successful=False):
    failures = {}; verified = {}
    for path in sorted(Path(directory).glob('*.json')):
        value = json.loads(path.read_bytes())
        if (value.get('schema_version') not in {'source-integrity-failure/v1', 'source-integrity-success/v1'} or
                value.get('scope') not in SCOPES or value.get('category') not in CATEGORIES or
                not re.fullmatch('[0-9a-f]{64}', str(value.get('condition')))):
            raise ValueError('Invalid content check receipt')
        if not re.fullmatch('[0-9]+', str(value.get('run_id'))) or not re.fullmatch('[0-9a-f]{40}', str(value.get('code_commit'))):
            raise ValueError('Content check requires a fixed Actions run and code commit')
        key = value['scope'], value['category']
        target = verified if value['schema_version'] == 'source-integrity-success/v1' else failures
        group = target.setdefault(key, {})
        prior = group.get(value['condition'])
        if prior is None or value['recorded_at'] > prior['recorded_at']: group[value['condition']] = value
    if not failures and not successful: return []
    existing = {(row['scope'], row['category']): row for row in open_failures(api)}
    actions = []
    for key in set(failures) | (set(existing) & set(verified) if successful else set()):
        previous = existing.get(key); metadata = previous.get('metadata') if previous else None
        active = set(metadata['conditions']) if metadata else set()
        overflow = bool(metadata.get('overflow')) if metadata else previous is not None
        current = failures.get(key, {})
        active.update(current)
        if successful:
            for condition, check in verified.get(key, {}).items():
                if condition not in current or check['recorded_at'] > current[condition]['recorded_at']:
                    active.discard(condition)
        marker = MARKER + ':'.join(key) + ' -->'
        if not active and not overflow:
            if previous:
                api.request('/issues/' + str(previous['issue']), 'PATCH', {'state': 'closed', 'state_reason': 'completed'})
                actions.append({'scope': key[0], 'category': key[1], 'issue': previous['issue'], 'action': 'verified_recovery'})
            continue
        latest = max(current.values(), key=lambda check: check['recorded_at']) if current else (metadata or {})
        overflow = overflow or len(active) > 400
        payload = {'schema_version': 'integrity-conditions/v1', 'conditions': sorted(active)[:400], 'overflow': overflow,
            'run_id': latest.get('run_id'), 'code_commit': latest.get('code_commit')}
        encoded = base64.b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()
        subjects = sorted({check['subject'] for check in current.values()})[:5]
        body = (marker + '\n' + META + encoded + ' -->\n\nContent validation failed; formal publication remains blocked.\n'
            'Category: `' + key[1] + '`; source: `' + key[0] + '`.\n'
            'Unresolved checks: ' + str(len(active)) + '.\n'
            'Actions run: `' + str(payload['run_id']) + '`; code: `' + str(payload['code_commit']) + '`.\n'
            'Observed subjects: ' + json.dumps(subjects) + '\n'
            'Only matching verified checks in a completed pipeline clear these conditions; unrelated success does not.\n')
        if previous:
            number = previous['issue']; api.request('/issues/' + str(number), 'PATCH', {'body': body})
        else:
            number = api.request('/issues', 'POST', {'title': '[Integrity] ' + ' / '.join(key), 'body': body})['number']
        actions.append({'scope': key[0], 'category': key[1], 'issue': number, 'action': 'held'})
    return actions
