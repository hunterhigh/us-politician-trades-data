"""Record OGE gate and collection outcomes without replacing successful source waterlines."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from unison_snapshot.codec import encode


def record(root, gate, *, catalog=None, reports=None, extraction=None, successful=None, run_url=None, failure=None):
    target = Path(root) / 'status/oge.json'
    prior = json.loads(target.read_bytes()) if target.is_file() else {}
    value = {**prior, 'schema_version': 'oge-source-run/v1', 'source_id': 'oge',
             'run_at': datetime.now(timezone.utc).isoformat(), 'gate': gate,
             'collection_enabled': gate['collection_enabled'], 'terms_acknowledged': gate['terms_acknowledged'],
             'workflow_run_url': run_url}
    if successful is None:
        value['status'] = 'collection_pending' if gate['status'] == 'enabled' else gate['status']
        if gate['status']=='enabled' and (prior.get('status')=='blocked' or
                prior.get('last_collection_attempt',{}).get('http_status')==403):
            value['status']='blocked'
        value['detail'] = 'OGE collection pending.' if gate['status'] == 'enabled' else 'OGE collection gate is closed.'
    elif successful:
        if not all(isinstance(part, dict) for part in (catalog, reports, extraction)):
            raise ValueError('Successful OGE collection lacks its complete batch')
        value.update(status='partial', catalog=catalog['metadata'],
            last_successful_collection_at=catalog.get('observed_at', catalog['metadata']['retrieved_at']),
            reports={'direct_count': reports['catalog_direct_count'], 'archived_count': len(reports['reports']),
                'pending_count': reports['pending_count'], 'failure_count': reports['failure_count'],
                'request_required_count': reports['request_required_count'],
                **{key: extraction[key] for key in ('extraction_count', 'reused_extraction_count',
                    'parsed_extraction_count', 'transaction_count', 'quarantined_row_count')},
                'extraction_failure_count': extraction['failure_count']},
            detail='Only catalog-listed direct PDFs were collected; Form 201 requests remain excluded.')
        value.pop('last_collection_attempt', None)
    else:
        value.update(status='failed', last_collection_attempt={'failed_step': 'collect_archive_parse',
            'workflow_run_url': run_url, **(failure or {})}, detail='Collection did not complete; prior successful waterlines retained.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.next.json'); temporary.write_bytes(encode(value)); temporary.replace(target)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['gate', 'collection'])
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--gate', type=Path, required=True)
    for name in ('catalog', 'reports', 'extraction', 'failure'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--successful', choices=['true', 'false'], default='false')
    parser.add_argument('--run-url')
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_bytes()) if path and path.is_file() else None
    gate = read(args.gate)
    result = record(args.state_root, gate, catalog=read(args.catalog), reports=read(args.reports),
                    extraction=read(args.extraction), run_url=args.run_url,
                    failure=read(args.failure),
                    successful=None if args.phase == 'gate' else args.successful == 'true')
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as stream: stream.write('status=' + gate['status'] + '\n')
    print(json.dumps({'status': result['status']}))


if __name__ == '__main__': main()
