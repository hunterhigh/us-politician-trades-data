"""Read the live publication through the shipped client, with content-only recovery."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError, URLError

from . import legacy

SHA = re.compile(r'[0-9a-f]{40}\Z')
SCHEMA = 'publication-smoke/v1'


def client_identity(root):
    """Reuse a passing smoke only while both publication and consumer stay fixed."""
    root = Path(root)
    paths = set()
    for pattern in ('backend/src/unison_snapshot/**/*.py', 'backend/src/unison_snapshot/**/*.js', 'client/*.py',
            'frontend-contract-v*/scripts/*.py', 'review-input/us-politician-trades-watch/scripts/*.py'):
        paths.update(root.glob(pattern))
    paths.add(root/'backend/data/publication_smoke.json')
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


def check_client(*, repository, commit, client_root, output_dir, people, runner=subprocess.run):
    """Execute the same entry point used by the report Skill, without live writes."""
    if not SHA.fullmatch(commit) or repository.count('/') != 1 or len(people) != 3 or len(set(people)) != 3:
        raise ValueError('Smoke requires one frozen publication and three fixed distinct people')
    owner, repo = repository.split('/')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, (mode, person) in enumerate([('dashboard', None), *(('person', p) for p in people)]):
        output, status_path = (output_dir/f'{index}.json', output_dir/f'{index}-status.json')
        # A previous attempt's receipt must never mask a crashed new invocation.
        status_path.unlink(missing_ok=True)
        command = [sys.executable, str(Path(client_root)/'client/fetch_snapshot.py'), mode,
            '--owner', owner, '--repo', repo, '--expected-commit', commit,
            '--output', str(output), '--status-output', str(status_path)]
        if person is not None:
            command.extend(['--value', person])
        try:
            process = runner(command, capture_output=True, text=True, encoding='utf-8', timeout=240)
            result = read_json(status_path) if status_path.is_file() else {'status':'operational_failure',
                'detail':'Client exited without a typed receipt'}
            if process.returncode != 0 and result.get('status') == 'passed':
                result = {'status':'operational_failure', 'detail':'Client exit contradicts its receipt'}
            if result.get('status') == 'passed' and (result.get('commit') != commit or not output.is_file()):
                result = {'status':'operational_failure', 'detail':'Client receipt is missing its bound output'}
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            result = {'status':'operational_failure', 'detail':str(error)}
        results.append({'mode':mode, 'person_id':person, **result})
        if result.get('status') == 'superseded':
            break
    if results[0]['status'] == 'passed':
        try:
            dashboard = read_json(output_dir/'0.json')
            # The deployed producer uses the pinned v2 renderer; the v1
            # candidate adds a format selector. Keep each release on its own
            # shipped consumer rather than importing the new parser to deploy
            # this independent operational observer.
            version = legacy.processor_version_for(dashboard) if hasattr(legacy, 'processor_version_for') else 'v2'
            renderer = legacy.load('render_dashboard', version=version)
            html = renderer.render_html(renderer.load_dashboard_data(output_dir/'0.json'))
            if not isinstance(html, str) or '<html' not in html.lower():
                raise ValueError('Report renderer produced no HTML document')
            (output_dir/'dashboard.html').write_text(html, encoding='utf-8')
            results.append({'mode':'render', 'status':'passed'})
        except ValueError as error:
            results.append({'mode':'render', 'status':'content_failure', 'detail':str(error)})
        except Exception as error:
            # A missing local renderer or programming exception is not evidence
            # for restoring older published facts.
            results.append({'mode':'render', 'status':'operational_failure', 'detail':str(error)})
    statuses = {r.get('status') for r in results}
    status = next((s for s in ('superseded','content_failure','network_failure','operational_failure') if s in statuses), 'passed')
    if any(s not in {'passed','superseded','content_failure','network_failure','operational_failure'} for s in statuses):
        status = 'operational_failure'
    return {'status':status, 'checks':results}


def recovery_decision(api, receipt, previous, *, apply=False):
    """Recheck head and the known-good published tag before dispatching rollback.

    The existing rollback workflow repeats the expected-head and published-tag
    checks while holding the publication lock. An ambiguous dispatch can thus
    never restore over a newer head, even if delivery is retried.
    """
    if receipt['status'] != 'content_failure':
        return {'action':'none', 'reason':receipt['status']}
    failed, target = receipt['commit'], previous.get('last_good_commit')
    if not target or not SHA.fullmatch(target) or target == failed:
        return {'action':'held', 'reason':'no_distinct_verified_previous_publication'}
    if previous.get('rollback_requested_for') == failed:
        return {'action':'already_requested', 'target':target}
    current = api.request('/git/ref/heads/main')['object']
    if current.get('type') != 'commit' or current.get('sha') != failed:
        return {'action':'superseded', 'reason':'main_advanced'}
    tag = api.request('/git/ref/tags/published/'+target)['object']
    if tag.get('type') != 'commit' or tag.get('sha') != target:
        return {'action':'held', 'reason':'previous_publication_tag_mismatch'}
    comparison = api.request('/compare/'+target+'...'+failed)
    if comparison.get('status') != 'ahead':
        return {'action':'held', 'reason':'previous_publication_not_an_ancestor'}
    if not apply:
        return {'action':'would_request', 'target':target}
    api.request('/actions/workflows/rollback.yml/dispatches', 'POST', {
        'ref':'code', 'inputs':{'target_commit':target, 'expected_head':failed, 'dry_run':False}})
    return {'action':'requested', 'target':target}


def observe_smoke(api, *, repository, commit, manifest, client_root, health_root, apply=False, checker=check_client):
    path = Path(health_root)/'status/publication_smoke.json'
    previous = read_json(path) if path.is_file() else {}
    if previous and previous.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported saved publication smoke receipt')
    identity = client_identity(client_root)
    if previous.get('commit') == commit and previous.get('status') == 'passed' and previous.get('client_sha256') == identity:
        return previous
    selection = read_json(Path(client_root)/'backend/data/publication_smoke.json')
    if selection.get('schema_version') != 'publication-smoke-selection/v1':
        raise ValueError('Unsupported smoke selection')
    checked = checker(repository=repository, commit=commit, client_root=client_root,
        output_dir=Path(client_root)/'.local/publication-smoke'/commit, people=selection['people'])
    receipt = {**checked, 'schema_version':SCHEMA, 'commit':commit, 'client_sha256':identity,
        'snapshot_id':manifest['snapshot_id'], 'checked_at':datetime.now(timezone.utc).isoformat(),
        'last_good_commit':previous.get('last_good_commit'),
        'failed_snapshots':previous.get('failed_snapshots', {}),
        'rollback_requested_for':previous.get('rollback_requested_for')}
    if receipt['status'] == 'passed':
        receipt['last_good_commit'] = commit
    if receipt['status'] == 'content_failure':
        receipt['failed_snapshots'][receipt['snapshot_id']] = {'commit':commit, 'checked_at':receipt['checked_at']}
    try:
        receipt['recovery'] = recovery_decision(api, receipt, previous, apply=apply)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        receipt['recovery'] = {'action':'held', 'reason':'recovery_check_or_dispatch_unavailable', 'detail':str(error)}
    if receipt['recovery']['action'] == 'requested':
        receipt['rollback_requested_for'] = commit
    save_json(path, receipt)
    return receipt


def check_circuit(manifest, receipt):
    if receipt.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported publication recovery receipt')
    if manifest['snapshot_id'] in receipt.get('failed_snapshots', {}):
        raise ValueError('This snapshot previously failed published content validation; rebuild it before publishing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--repository')
    args = parser.parse_args()
    if bool(args.receipt) == bool(args.repository):
        parser.error('Choose a saved receipt or the live health repository')
    if args.receipt:
        saved = read_json(args.receipt)
    else:
        import base64
        from .production_health import GitHub
        item = GitHub(args.repository, os.environ.get('GH_TOKEN')).request('/contents/status/publication_smoke.json?ref=health')
        saved = json.loads(base64.b64decode(item['content']))
    check_circuit(read_json(args.manifest), saved)
    print(json.dumps({'content_circuit':'clear'}))


if __name__ == '__main__':
    main()
