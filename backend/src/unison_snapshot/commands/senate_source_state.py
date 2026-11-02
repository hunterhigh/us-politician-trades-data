"""Persist Senate collection and review metadata without advancing facts on failure."""
import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+".next")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def collection_start(prior, configured='', *, now=None, qualified_at=None, earliest_known_date=None):
    """Never skip a recovery interval because a configured window moved forward."""
    now = now or datetime.now(timezone.utc)
    try: fallback = now.date().replace(year=now.year - 6).isoformat()
    except ValueError: fallback = date(now.year - 6, 2, 28).isoformat()
    selected = configured or fallback
    if date.fromisoformat(selected).isoformat() != selected:
        raise ValueError('Senate collection start requires an ISO date')
    waterlines = [prior.get('last_successful_collection_at'),
                  (prior.get('catalog') or {}).get('retrieved_at'), qualified_at]
    from unison_snapshot.source_freshness import timestamp
    for value in waterlines:
        if value:
            selected = min(selected, timestamp(value).date().isoformat())
    if earliest_known_date:
        if date.fromisoformat(earliest_known_date).isoformat() != earliest_known_date:
            raise ValueError('Prior Senate catalog date is invalid')
        selected = min(selected, earliest_known_date)
    if selected > now.date().isoformat():
        raise ValueError('Senate collection start cannot be in the future')
    return selected

def record_gate(state, run, now):
    gate = json.loads((run / 'senate-efd-gate.json').read_text(encoding='utf-8'))
    target = state / 'status' / 'senate_efd.json'
    prior = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
    # A gate check is not a new source snapshot. Preserve the last
    # complete catalog/reports if this run is canceled before collection.
    value = {
        **prior,
        'schema_version': 'senate-source-run/v1',
        'source_id': 'senate_efd',
        'run_at': now,
        'status': (prior['status'] if gate['status'] == 'enabled' and
                   prior.get('status') in {'catalog_ready_for_review', 'failed', 'blocked', 'unavailable'} else
                   gate['status']),
        'collection_enabled': gate['collection_enabled'],
        'terms_acknowledged': gate['terms_acknowledged'],
        'gate': gate,
        'roster': prior.get('roster', {'status': 'unknown'}),
        'detail': (
            'eFD authorization gate is enabled; catalog collection is pending.'
            if gate['status'] == 'enabled' else
            'eFD catalog collection did not run because both explicit production '
            'authorization variables were not true.'
        ),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def record_roster(state, run, now):
    roster = json.loads((run / 'senate-members.json').read_text(encoding='utf-8'))
    congress_path = run / 'congress-senate-members.json'
    congress = json.loads(congress_path.read_text(encoding='utf-8')) if congress_path.is_file() else None
    target = state / 'status/senate_efd.json'
    prior = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
    gate = prior.get('gate', {'schema_version': 'senate-efd-source-gate/v1',
        'source_id': 'senate_efd', 'status': 'disabled', 'collection_enabled': False,
        'terms_acknowledged': False, 'reasons': ['collection_not_enabled']})
    value = {**prior, 'schema_version': 'senate-source-run/v1', 'source_id': 'senate_efd',
        'run_at': now, 'status': prior.get('status', gate['status']),
        'collection_enabled': gate['collection_enabled'],
        'terms_acknowledged': gate['terms_acknowledged'], 'gate': gate,
        'roster': {'status': 'ok', 'record_count': len(roster['members']),
                   'sha256': roster['metadata']['sha256'], 'retrieved_at': roster['metadata']['retrieved_at']},
        'detail': prior.get('detail', 'Official Senate identity roster archived. eFD report collection remains disabled.')}
    if congress is not None:
        value['historical_roster'] = {'status': 'ok', 'source_id': 'congress_gov_members',
            'congress': congress['metadata']['congress'], 'record_count': len(congress['members']),
            'sha256': congress['metadata']['sha256'], 'retrieved_at': congress['metadata']['retrieved_at']}
    write_text(target, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

def record_failure(state, run, now, source_status='', collect_outcome='failure', run_url='', summary=None):
    target = state / 'status/senate_efd.json'
    prior = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
    failed_step = ('collect_catalog' if collect_outcome == 'failure'
                   or source_status in {'blocked', 'unavailable'}
                   else 'publish_evidence')
    diagnostic_path = (run / 'senate-efd-failure.json')
    diagnostic = (json.loads(diagnostic_path.read_text(encoding='utf-8'))
                  if diagnostic_path.is_file() else {})
    attempted_at = now
    recorded_status = source_status if source_status in {'blocked', 'unavailable'} else 'failed'
    value = {
        **prior, 'schema_version': 'senate-source-run/v1',
        'source_id': 'senate_efd', 'status': recorded_status, 'run_at': attempted_at,
        'detail': 'Senate catalog collection failed; prior source data is retained.',
        'last_collection_attempt': {
            'status': recorded_status, 'attempted_at': attempted_at,
            'workflow_run_url': run_url,
            'failed_step': failed_step,
            **{key: diagnostic[key] for key in
               ('http_status', 'request_stage', 'error_code') if key in diagnostic},
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    if summary:
        with Path(summary).open('a', encoding='utf-8') as handle:
            handle.write(f"\nSenate source status: **{recorded_status}**. Prior catalog/reports retained; no new Senate data was produced.\n")
            handle.write(f"Last successful catalog: `{prior.get('catalog', {}).get('retrieved_at', 'unknown')}`.\n")
            handle.write(f"Run: {run_url}\n")

def record_collected(state, run, now):
    gate = json.loads((run / 'senate-efd-gate.json').read_text(encoding='utf-8'))
    discovery = json.loads((run / 'senate-efd-discovery.json').read_text(encoding='utf-8'))
    report_batch = json.loads((run / 'senate-efd-report-batch.json').read_text(encoding='utf-8'))
    extraction_batch = json.loads((run / 'senate-efd-extraction-batch.json').read_text(encoding='utf-8'))
    target = state / 'status' / 'senate_efd.json'
    prior = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
    value = {
        'schema_version': 'senate-source-run/v1',
        'source_id': 'senate_efd',
        'run_at': now,
        'status': 'catalog_ready_for_review',
        'last_successful_collection_at': now,
        'collection_enabled': True,
        'terms_acknowledged': True,
        'gate': gate,
        'roster': prior.get('roster', {'status': 'unknown'}),
        'catalog': {
            'status': 'ok',
            'record_count': discovery['records_total'],
            'page_count': discovery['metadata']['page_count'],
            'sha256': discovery['metadata']['sha256'],
            'retrieved_at': discovery['metadata']['retrieved_at'],
        },
        'reports': {
            'status': ('entrypoints_archived' if report_batch['archived_total'] else
                       'entrypoint_collection_failed' if report_batch['failure_count'] else
                       'not_collected'),
            'entrypoint_count': report_batch['archived_total'],
            'pending_count': report_batch['pending_count'],
            'last_batch_archived': report_batch['archived_count'],
            'last_batch_failures': report_batch['failure_count'],
            'evidence_count': extraction_batch['extraction_count'],
            'last_batch_extractions': extraction_batch['extraction_count'],
            'last_batch_extraction_failures': extraction_batch['failure_count'],
            'last_batch_transactions': extraction_batch['transaction_count'],
        },
        'detail': ('Authorized eFD PTR catalog and a bounded batch of report entrypoint '
                   'responses were archived. Transaction facts remain unpublished.'),
    }
    if 'historical_roster' in prior:
        value['historical_roster'] = prior['historical_roster']
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    write_text(target, content)
    write_text(run / 'senate-efd-state.json', content)

def record_review(review, run, now):
    source = (run / 'senate-efd-discovery.json')
    discovery = json.loads(source.read_text(encoding='utf-8'))
    identities = json.loads((run / 'senate-efd-identities.json').read_text(encoding='utf-8'))
    report_batch = json.loads((run / 'senate-efd-report-batch.json').read_text(encoding='utf-8'))
    extraction_batch = json.loads((run / 'senate-efd-extraction-batch.json').read_text(encoding='utf-8'))
    review = review
    sha = discovery['metadata']['sha256']
    if (identities.get('schema_version') != 'senate-efd-identities/v1' or
            identities.get('source_id') != 'senate_efd' or
            identities.get('catalog_sha256') != sha or
            identities.get('report_count') != discovery['records_total'] or
            sum(identities.get('identity_counts', {}).values()) != discovery['records_total'] or
            {item.get('document_id') for item in identities.get('identities', [])} !=
            {item.get('document_id') for item in discovery['reports']}):
        raise SystemExit('Senate eFD identity batch does not match its discovery catalog')
    roster_sha = identities.get('roster_sha256')
    if not isinstance(roster_sha, str) or len(roster_sha) != 64:
        raise SystemExit('Senate eFD identity batch has no valid roster hash')
    congress_roster_sha = identities.get('congress_roster_sha256')
    if congress_roster_sha is not None and (
            not isinstance(congress_roster_sha, str) or len(congress_roster_sha) != 64):
        raise SystemExit('Senate eFD identity batch has no valid Congress.gov roster hash')
    target = review / 'senate_efd' / 'discoveries' / f'{sha}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(discovery, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if target.is_file() and target.read_text(encoding='utf-8') != content:
        raise SystemExit('Senate eFD discovery conflicts with its content hash')
    write_text(target, content)
    identity_binding = hashlib.sha256(
        f"{roster_sha}:{congress_roster_sha or ''}".encode('ascii')).hexdigest()
    identity_target = review / 'senate_efd' / 'identities' / sha / f'{identity_binding}.json'
    identity_target.parent.mkdir(parents=True, exist_ok=True)
    identity_content = json.dumps(
        identities, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if identity_target.is_file() and identity_target.read_text(encoding='utf-8') != identity_content:
        raise SystemExit('Senate eFD identities conflict with their catalog hash')
    write_text(identity_target, identity_content)
    for collection, folder in (
            (extraction_batch['extractions'], 'extractions'),
            (extraction_batch['inspections'], 'paper_inspections')):
        for item in collection:
            item_target = (review / 'senate_efd' / folder / item['document_id'] /
                           item['source_sha256'] / f"{item['parser_version']}.json")
            item_target.parent.mkdir(parents=True, exist_ok=True)
            item_content = json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if item_target.is_file() and item_target.read_text(encoding='utf-8') != item_content:
                raise SystemExit('Senate eFD report result conflicts with its source hash')
            write_text(item_target, item_content)
    for item in extraction_batch['failures']:
        item_target = (review / 'senate_efd' / 'report_failures' / item['document_id'] /
                       item['source_sha256'] / f"{item['parser_version']}.json")
        item_target.parent.mkdir(parents=True, exist_ok=True)
        item_content = json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if item_target.is_file() and item_target.read_text(encoding='utf-8') != item_content:
            raise SystemExit('Senate eFD report failure conflicts with its source hash')
        write_text(item_target, item_content)
    parser_version = extraction_batch['parser_version']
    extraction_paths = list(review.glob(
        f'senate_efd/extractions/*/*/{parser_version}.json'))
    extraction_values = [json.loads(path.read_text(encoding='utf-8'))
                         for path in extraction_paths]
    evidence_count = sum(bool(item.get('evidence_complete')) for item in extraction_values)
    transaction_count = sum(len(item.get('transactions', [])) for item in extraction_values)
    report_failure_count = sum(len(list(review.glob(pattern))) for pattern in (
        f'senate_efd/report_failures/*/*/{parser_version}.json',
        f'senate_efd/paper_report_failures/*/*/{parser_version}.json',
    ))
    status = {
        'schema_version': 'senate-review-run/v1',
        'source_id': 'senate_efd',
        'generated_at': now,
        'status': 'catalog_ready_for_review',
        'catalog_sha256': sha,
        'catalog_record_count': discovery['records_total'],
        'identity_counts': identities['identity_counts'],
        'identity_roster_sha256': roster_sha,
        'identity_binding_sha256': identity_binding,
        'report_parser_version': parser_version,
        'report_entrypoint_count': report_batch['archived_total'],
        'report_entrypoint_pending_count': report_batch['pending_count'],
        'report_entrypoint_failure_count': report_batch['failure_count'],
        'report_evidence_count': evidence_count,
        'report_extraction_failure_count': report_failure_count,
        'extracted_transaction_count': transaction_count,
        'detail': (f"{report_batch['archived_total']} PTR entrypoints archived; "
                   f"{evidence_count} electronic reports parsed into {transaction_count} transactions; "
                   f"{report_failure_count} current-parser failures remain."),
    }
    if congress_roster_sha:
        status['identity_congress_roster_sha256'] = congress_roster_sha
    status_path = review / 'status' / 'senate_efd.json'
    status_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(status_path, json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('gate','failure','collected','review','roster','start-date'))
    parser.add_argument('--configured-start', default='')
    parser.add_argument('--run-root', type=Path, default=Path('.local'))
    parser.add_argument('--state-root', type=Path)
    parser.add_argument('--review-root', type=Path)
    parser.add_argument('--source-status', default='')
    parser.add_argument('--collect-outcome', default='failure')
    parser.add_argument('--run-url', default='')
    parser.add_argument('--summary', type=lambda value: Path(value) if value else None)
    args = parser.parse_args()
    now = datetime.now(timezone.utc).isoformat()
    try:
        if args.operation == 'review':
            if not args.review_root:parser.error('review requires --review-root')
            record_review(args.review_root, args.run_root, now)
        else:
            if not args.state_root:parser.error('state operations require --state-root')
            if args.operation == 'start-date':
                path = args.state_root / 'status/senate_efd.json'
                prior = json.loads(path.read_bytes()) if path.is_file() else {}
                qualified, earliest = None, None
                if args.review_root:
                    candidate = args.review_root / 'candidates/sources/senate_efd-current.json'
                    if candidate.is_file():
                        qualified = json.loads(candidate.read_bytes())['meta']['data_cutoff_at']
                    status = args.review_root / 'status/senate_efd.json'
                    if status.is_file():
                        status_value = json.loads(status.read_bytes())
                        sha = status_value.get('catalog_sha256')
                        import re
                        if sha is None and status_value.get('schema_version') != 'senate-review-run/v1':
                            sha = None  # Bootstrap state has no normalized review catalog yet.
                        elif not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):
                            raise ValueError('Prior Senate catalog lacks its content binding')
                        if sha is not None:
                            discovery = json.loads((args.review_root / 'senate_efd/discoveries' / (sha+'.json')).read_bytes())
                            content = {key:value for key,value in discovery.items() if key != 'metadata'}
                            if hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest() != sha:
                                raise ValueError('Prior Senate catalog content differs from its binding')
                            dates = [r['portal_listed_date'] for r in discovery['reports']]
                            earliest = min(dates) if dates else None
                print(collection_start(prior, args.configured_start,
                    qualified_at=qualified, earliest_known_date=earliest))
            elif args.operation == 'failure':
                record_failure(args.state_root, args.run_root, now, args.source_status,
                    args.collect_outcome, args.run_url, args.summary)
            else:
                {'gate':record_gate, 'collected':record_collected, 'roster':record_roster}[args.operation](
                    args.state_root, args.run_root, now)
    except (OSError, ValueError) as error:parser.exit(2,str(error)+'\n')


if __name__ == '__main__':main()
