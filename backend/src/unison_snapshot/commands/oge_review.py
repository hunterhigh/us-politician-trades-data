"""Account for OGE source qualification and its White House overlays."""
import argparse
import os
from pathlib import Path

def build(args):
    import json, os
    from datetime import datetime, timezone
    from pathlib import Path
    review = Path(args.review_root)
    catalog = json.loads((args.run_root/'oge-catalog.json').read_text(encoding='utf-8'))
    batch = json.loads((args.run_root/'oge-report-batch.json').read_text(encoding='utf-8'))
    extraction = json.loads((args.run_root/'oge-extraction-batch.json').read_text(encoding='utf-8'))
    audit = json.loads((review / 'oge/qualifications/current.json').read_text(encoding='utf-8'))
    whitehouse = json.loads((review / 'whitehouse/qualifications/candidate-current.json').read_text(encoding='utf-8'))
    annual = json.loads((review / 'whitehouse/annual/candidate-current.json').read_text(encoding='utf-8'))
    combined = json.loads((review / 'candidates/sources/oge-current.json').read_text(encoding='utf-8'))
    ticker_audit = json.loads((review / 'whitehouse/278t/trump-2026-ticker-mapping-current.json').read_text(encoding='utf-8'))
    direct_published = audit['promoted_transaction_count']
    direct_current = audit.get('current_qualified_transaction_count', direct_published)
    # Later overlays can replace identities. Without their complete
    # current-ID intersection, a retained projection is not a qualified total.
    retained_prior = audit.get('retained_published_fact_count', 0)
    qualified_total = (len(combined['transactions'])
                       if direct_current + retained_prior == direct_published else None)
    pending_reports = audit.get('pending_reports', [])
    archived_pending = {row['document_id'] for row in pending_reports if row['archive_state'] == 'archived'}
    failure_entries = extraction.get('failures', [])
    if not isinstance(failure_entries, list) or len(failure_entries) != extraction['failure_count']:
        raise ValueError('OGE extraction failures lack their report dispositions')
    failed_pending = archived_pending & {row['document_id'] for row in failure_entries}
    archived_count = audit['extraction_count'] + len(archived_pending)
    value = {
        'schema_version': 'oge-review-run/v1', 'source_id': 'oge',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'partial',
        'catalog_sha256': catalog['metadata']['sha256'],
        'catalog_record_count': catalog['records_total'],
        'direct_pdf_count': audit['catalog_direct_count'],
        'direct_pdf_occurrence_count': batch['catalog_direct_occurrence_count'],
        'duplicate_direct_occurrence_count': batch['duplicate_catalog_occurrence_count'],
        'request_required_count': catalog['metadata']['request_required_count'],
        'retained_historical_direct_count': audit['retained_historical_direct_count'],
        'archived_report_count': archived_count,
        'pending_direct_count': audit['catalog_direct_count'] - archived_count,
        'extraction_count': audit['extraction_count'],
        'extraction_failure_count': len(failed_pending),
        'pending_extraction_count': len(archived_pending - failed_pending),
        'retained_prior_qualified_transaction_count': retained_prior,
        'qualified_transaction_count': qualified_total,
        'publication_transaction_count': len(combined['transactions']),
        'oge_direct_qualified_transaction_count': direct_current,
        'oge_direct_publication_transaction_count': direct_published,
        'oge_direct_quarantined_row_count': audit['quarantined_row_count'],
        'whitehouse_public_qualified_transaction_count': whitehouse['promoted_transaction_count'],
        'whitehouse_public_qualified_annual_transaction_count': annual.get(
            'annual_transaction_count', 0),
        'whitehouse_public_extraction_failure_count': whitehouse['extraction_failure_count'],
        'whitehouse_public_qualified_annual_report_count': annual['annual_qualified_report_count'],
        'whitehouse_public_qualified_holding_count': annual['annual_holding_count'],
        'qualified_holding_count': len(combined['reported_holdings']),
        'retained_ticker_conflict_count': ticker_audit['retained_ticker_conflict_count'],
        'quarantined_row_count': (audit['quarantined_row_count'] +
                                  whitehouse['quarantined_row_count']),
    }
    target = review / 'status/oge.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')), encoding='utf-8')


def reconcile(args):
    import json, os
    from pathlib import Path
    root = Path(args.review_root)
    candidate = json.loads((root / 'candidates/sources/oge-current.json').read_text(encoding='utf-8'))
    audit = json.loads((root / 'whitehouse/qualifications/candidate-current.json').read_text(encoding='utf-8'))
    annual = json.loads((root / 'whitehouse/annual/candidate-current.json').read_text(encoding='utf-8'))
    path = root / 'status/oge.json'
    status = json.loads(path.read_text(encoding='utf-8'))
    direct_count = status.get('oge_direct_qualified_transaction_count', status['qualified_transaction_count'])
    direct_quarantined = status.get('oge_direct_quarantined_row_count', status['quarantined_row_count'])
    annual_transaction_count = annual.get('annual_transaction_count', 0)
    direct_published = status.get('oge_direct_publication_transaction_count', direct_count)
    if len(candidate['transactions']) != (direct_published +
                                           audit['promoted_transaction_count'] +
                                           annual_transaction_count):
        raise SystemExit('OGE and White House transaction counts do not reconcile')
    status['oge_direct_qualified_transaction_count'] = direct_count
    status['oge_direct_quarantined_row_count'] = direct_quarantined
    status['whitehouse_public_qualified_transaction_count'] = audit['promoted_transaction_count']
    status['whitehouse_public_qualified_annual_transaction_count'] = annual_transaction_count
    status['whitehouse_public_qualified_annual_report_count'] = annual['annual_qualified_report_count']
    status['whitehouse_public_qualified_holding_count'] = annual['annual_holding_count']
    status['publication_transaction_count'] = len(candidate['transactions'])
    if status.get('qualified_transaction_count') is not None:
        status['qualified_transaction_count'] = len(candidate['transactions'])
    status['qualified_holding_count'] = len(candidate['reported_holdings'])
    status['quarantined_row_count'] = direct_quarantined + audit['quarantined_row_count']
    path.write_text(json.dumps(status, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':')), encoding='utf-8')

def stage(args):
    import hashlib, json, os
    from unison_snapshot.codec import encode
    from pathlib import Path
    review = Path(args.review_root)
    catalog_path = args.run_root/'oge-catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    sha = catalog['metadata']['sha256']
    target = review / 'oge' / 'discoveries' / f'{sha}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    content = encode({key: value for key, value in catalog.items() if key != 'observed_at'})
    if target.is_file() and target.read_bytes() != content:
        raise SystemExit('Immutable OGE catalog collision')
    target.write_bytes(content)
    for source in (args.run_root/'oge-extractions').glob('*.json'):
        value = json.loads(source.read_text(encoding='utf-8'))
        parser_file = ('failure-' + hashlib.sha256(source.read_bytes()).hexdigest()[:16] + '.failure.json' if source.name.endswith('.failure.json') else
                       value['parser_version'].replace('/', '-') + '.json')
        target = (review / 'oge' / 'extractions' / value['document_id'] /
                  value['source_sha256'] / parser_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_bytes() != source.read_bytes():
            raise SystemExit('Immutable OGE extraction collision')
        target.write_bytes(source.read_bytes())


def saved_input(args):
    import os
    from pathlib import Path
    raw = args.saved_input
    if (not raw or len(raw) > 512 or raw != raw.strip() or
            raw.startswith('/') or chr(92) in raw or ':' in raw or
            any(ord(c) < 32 for c in raw) or
            any(part in ('', '.', '..') for part in raw.split('/'))):
        raise SystemExit('A bounded review-relative saved PTR input must be selected explicitly')
    root = Path(args.review_root).resolve()
    path = (root / raw).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise SystemExit('Selected saved PTR input is missing or outside the controlled review root')
    print(path)


def selection(args):
    import os, re, sys
    from unison_snapshot.oge_source_selection import parse_document_sources
    configured = args.document_ids
    dispatched = args.document_id
    if len(configured) > 32 * 33 or len(dispatched) > 32:
        raise SystemExit('Generic PTR document selection exceeds its budget')
    ids = configured.split(',') if configured else []
    if dispatched:
        ids.append(dispatched)
    if any(not re.fullmatch('[0-9a-f]{32}', item) for item in ids):
        raise SystemExit('Generic PTR document IDs must be lowercase 32-character hex values')
    ids = sorted(set(ids))
    if len(ids) > 32:
        raise SystemExit('At most 32 generic PTR document IDs may be selected')
    configured_sources = args.document_sources
    dispatched_source = args.document_source
    if len(configured_sources) > 32 * 98 or len(dispatched_source) > 97:
        raise SystemExit('PTR archived input selection exceeds its budget')
    values = configured_sources.split(',') if configured_sources else []
    if dispatched_source:
        values.append(dispatched_source)
    sources = parse_document_sources(sorted(set(values)))
    if sources.keys() - set(ids):
        raise SystemExit('An archived PTR input requires its explicit generic document selection')
    sys.stdout.buffer.write('\n'.join(doc + ':' + sources.get(doc, '') for doc in ids).encode('ascii'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build', 'reconcile', 'stage', 'saved-input', 'selection'))
    parser.add_argument('--review-root', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, default=Path('.local'))
    parser.add_argument('--saved-input', default=os.environ.get('OGE_SAVED_OBSERVATIONS_PATH', ''))
    parser.add_argument('--document-ids', default=os.environ.get('OGE_GENERIC_DOCUMENT_IDS', ''))
    parser.add_argument('--document-id', default=os.environ.get('OGE_GENERIC_DOCUMENT_ID', ''))
    parser.add_argument('--document-sources', default=os.environ.get('OGE_PTR_DOCUMENT_SOURCES', ''))
    parser.add_argument('--document-source', default=os.environ.get('OGE_PTR_DOCUMENT_SOURCE', ''))
    args = parser.parse_args()
    if not args.review_root.is_dir(): parser.error('review-root must be an existing directory')
    {'build': build, 'reconcile': reconcile, 'stage': stage, 'saved-input': saved_input, 'selection': selection}[args.mode](args)
    return 0
