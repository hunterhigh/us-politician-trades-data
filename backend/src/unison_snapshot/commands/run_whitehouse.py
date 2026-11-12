"""Run a controlled White House source job in one process."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys

from . import (assemble_source, build_whitehouse_278t_candidate,
               build_whitehouse_annual_review, build_whitehouse_reported_index,
               data_trees, enrich_whitehouse_annual_tickers,
               observe_oge_annual_scan_covers, observe_oge_annual_scan_dates,
               oge_annual_review, oge_review, oge_whitehouse,
               overlay_whitehouse_annual_candidate, whitehouse_coverage,
               whitehouse_disclosures, whitehouse_extract)
from .run_oge import _call, _step


def account_for_reports_and_archive_a_bounded_annual_batch():
    Path('.local').mkdir(exist_ok=True)
    evidence = os.environ['EVIDENCE_ROOT']
    _call(oge_whitehouse.main, ['coverage', '--evidence-root', evidence, '--output', '.local/oge-whitehouse-coverage.json'])
    _call(oge_whitehouse.main, ['request-plan', '--evidence-root', evidence, '--output', '.local/oge-whitehouse-request-plan.json'])
    batch_size = os.environ.get('OGE_ANNUAL_REPORT_BATCH_SIZE') or '2'
    if not re.fullmatch('([1-9]|1[0-2])', batch_size):
        print('OGE annual batch size must be between 1 and 12', file=sys.stderr)
        raise SystemExit(2)
    path = Path(os.environ['REVIEW_ROOT']) / 'oge/whitehouse/archive-status.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    cursor = value.get('last_attempted_id') or ''
    _call(oge_whitehouse.main, ['archive-annual', '--evidence-root', evidence,
        '--coverage', '.local/oge-whitehouse-coverage.json', '--start-after-id', cursor,
        '--limit', batch_size, '--output', '.local/oge-annual-batch.json'])


def publish_official_annual_originals(workspace):
    print(json.dumps(data_trees.publish(workspace, ('evidence',),
        'White House Publish official annual originals ' + os.environ['GITHUB_RUN_ID'])))


def publish_complete_white_house_coverage_audit():
    import json, os
    from pathlib import Path
    root = Path(os.environ['REVIEW_ROOT'])
    coverage = json.loads(Path('.local/oge-whitehouse-coverage.json').read_text(encoding='utf-8'))
    batch = json.loads(Path('.local/oge-annual-batch.json').read_text(encoding='utf-8'))
    target = root / 'oge/whitehouse/coverage-current.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(coverage, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')), encoding='utf-8')
    status = root / 'oge/whitehouse/archive-status.json'
    status.write_text(json.dumps({
        'schema_version': 'oge-whitehouse-archive-status/v1',
        'catalog_sha256': coverage['catalog_sha256'],
        'catalog_retrieved_at': coverage['catalog_retrieved_at'],
        'counts': coverage['counts'],
        'direct_annual_archived': batch['archived_count'],
        'direct_annual_pending': batch['pending_count'],
        'direct_annual_failures': batch['failures'],
        'last_attempted_id': batch['last_attempted_id'],
        'request_required_not_obtained': True,
        'candidate_integration_status': 'not_started',
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        encoding='utf-8')
    request_plan = json.loads(Path('.local/oge-whitehouse-request-plan.json').read_text(encoding='utf-8'))
    request_target = root / 'oge/whitehouse/request-plan-current.json'
    request_target.write_text(json.dumps(request_plan, ensure_ascii=False,
                                         sort_keys=True, separators=(',', ':')),
                              encoding='utf-8')


def persist_annual_coverage(workspace):
    print(json.dumps(data_trees.publish(workspace, ('review',),
        'White House Persist annual coverage ' + os.environ['GITHUB_RUN_ID'])))


def extract_content_verified_annual_reports_into_review_only():
    _call(oge_annual_review.main, ['--evidence-root', os.environ['EVIDENCE_ROOT'], '--review-root', os.environ['REVIEW_ROOT']])


def observe_scanned_annual_covers_without_promotion():
    _call(observe_oge_annual_scan_covers.main, ['--evidence-root', os.environ['EVIDENCE_ROOT'], '--review-root', os.environ['REVIEW_ROOT']])


def record_scanned_cover_date_roles_without_filing_date_promotion():
    _call(observe_oge_annual_scan_dates.main, ['--evidence-root', os.environ['EVIDENCE_ROOT'],
        '--output', os.environ['REVIEW_ROOT'] + '/oge/annual/scan_date_blocks/current.json'])


def publish_annual_extraction_audits(workspace):
    print(json.dumps(data_trees.publish(workspace, ('review',),
        'White House Publish annual extraction audits ' + os.environ['GITHUB_RUN_ID'])))


def archive_public_index_and_bounded_original_pdfs():
    import json, os
    from pathlib import Path
    path = Path(os.environ['REVIEW_ROOT']) / 'whitehouse/disclosures/archive-status.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    cursor = value.get('last_attempted_id') or ''
    review = os.environ['REVIEW_ROOT']
    _call(whitehouse_disclosures.main, ['sync', '--evidence-root', os.environ['EVIDENCE_ROOT'],
        '--index-out', review + '/whitehouse/disclosures/index-current.json',
        '--iri-audit-out', review + '/whitehouse/disclosures/iri-link-audit-current.json',
        '--batch-out', review + '/whitehouse/disclosures/batch-current.json',
        '--limit', os.environ['PDF_BATCH_SIZE'], '--start-after-id', cursor])
    import json, os
    from pathlib import Path
    root = Path(os.environ['REVIEW_ROOT']) / 'whitehouse/disclosures'
    batch = json.loads((root / 'batch-current.json').read_text(encoding='utf-8'))
    status = {key: batch[key] for key in (
        'schema_version', 'source_id', 'indexed_count', 'attempted_count',
        'last_attempted_id', 'archived_version_count', 'pending_url_count',
        'failures', 'index_quarantine')}
    (root / 'archive-status.json').write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        encoding='utf-8')


def publish_official_originals_to_evidence(workspace):
    print(json.dumps(data_trees.publish(workspace, ('evidence',),
        'White House Publish official originals to evidence ' + os.environ['GITHUB_RUN_ID'])))


def build_conservative_oge_catalog_crosswalk():
    root = Path(os.environ['EVIDENCE_ROOT']) / 'oge/catalog'
    manifests = []
    for path in root.glob('*.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema_version') == 'oge-catalog-archive/v1':
            manifests.append((value['retrieved_at'], str(path)))
    if not manifests:
        raise SystemExit('No archived OGE catalog manifest for crosswalk')
    manifest = max(manifests)[1]
    _call(whitehouse_disclosures.main, ['crosswalk', '--evidence-root', os.environ['EVIDENCE_ROOT'],
        '--oge-manifest', manifest, '--index', os.environ['REVIEW_ROOT'] + '/whitehouse/disclosures/index-current.json',
        '--out', os.environ['REVIEW_ROOT'] + '/whitehouse/disclosures/crosswalk-current.json'])


def extract_hash_verified_pdfs_into_review_only():
    path = Path(os.environ['REVIEW_ROOT']) / 'whitehouse/extraction-status.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    cursor = value.get('last_attempted_id') or ''
    _call(whitehouse_extract.main, ['--evidence-root', os.environ['EVIDENCE_ROOT'],
        '--review-root', os.environ['REVIEW_ROOT'], '--limit', os.environ['EXTRACTION_BATCH_SIZE'],
        '--start-after-id', cursor, '--ocr-page-limit', os.environ['OCR_PAGE_LIMIT']])


def account_for_every_public_report_link():
    _call(whitehouse_coverage.main, ['--review-root', os.environ['REVIEW_ROOT'], '--evidence-root', os.environ['EVIDENCE_ROOT']])


def index_filer_reported_rows_independently_of_frontend_eligibility():
    _call(build_whitehouse_reported_index.main, ['--review-root', os.environ['REVIEW_ROOT']])


def materialize_white_house_annual_filer_reported_holdings():
    _call(build_whitehouse_annual_review.main, ['--review-root', os.environ['REVIEW_ROOT']])


def publish_bounded_review_observations(workspace):
    print(json.dumps(data_trees.publish(workspace, ('review',),
        'White House Publish bounded review observations ' + os.environ['GITHUB_RUN_ID'])))


def rebuild_complete_oge_and_unified_review_candidates(workspace):
    review, evidence = os.environ['REVIEW_ROOT'], os.environ['EVIDENCE_ROOT']
    shutil.copyfile(review + '/candidates/sources/oge-current.json', os.environ['RUNNER_TEMP'] + '/oge-candidate-before.json')
    _call(build_whitehouse_278t_candidate.main, ['--oge-candidate', review + '/candidates/sources/oge-current.json',
        '--review-root', review, '--evidence-root', evidence,
        '--candidate-out', review + '/candidates/sources/oge-current.json',
        '--audit-out', review + '/whitehouse/qualifications/candidate-current.json'])
    _call(overlay_whitehouse_annual_candidate.main, ['--review-root', review, '--evidence-root', evidence,
        '--candidate', review + '/candidates/sources/oge-current.json',
        '--audit-out', review + '/whitehouse/annual/candidate-current.json'])
    if os.environ['MARKET_DISTRIBUTION_AUTHORIZED'] != 'true':
        raise SystemExit(1)
    assets_url = os.environ.get('ALPACA_ASSETS_URL') or 'https://paper-api.alpaca.markets/v2/assets'
    _call(enrich_whitehouse_annual_tickers.main, ['--candidate', review + '/candidates/sources/oge-current.json',
        '--mapping-output', review + '/whitehouse/annual/ticker-mapping-current.json',
        '--trump-2026-mapping-output', review + '/whitehouse/278t/trump-2026-ticker-mapping-current.json',
        '--checked-at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        '--assets-url', assets_url, '--distribution-authorized'])
    _call(oge_review.main, ['reconcile', '--review-root', review])
    _call(assemble_source.main, ['--workspace', str(workspace), '--source', 'oge',
        '--base', 'backend/data/empty-disclosure.json', '--builder-commit', os.environ['GITHUB_SHA']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, choices=('annual-catalog', 'annual-review', 'public', 'rebuild'))
    parser.add_argument('--mode', default='all')
    parser.add_argument('--pdf_batch_size', default='20')
    parser.add_argument('--extraction_batch_size', default='100')
    parser.add_argument('--ocr_page_limit', default='50')
    args = parser.parse_args(argv)
    os.environ.update(PDF_BATCH_SIZE=args.pdf_batch_size, EXTRACTION_BATCH_SIZE=args.extraction_batch_size,
                      OCR_PAGE_LIMIT=args.ocr_page_limit)
    if args.job == 'annual-catalog':
        workspace = Path(os.environ['RUNNER_TEMP']) / 'whitehouse-annual-catalog'
        _step(account_for_reports_and_archive_a_bounded_annual_batch)
        _step(publish_official_annual_originals, workspace)
        _step(publish_complete_white_house_coverage_audit)
        _step(persist_annual_coverage, workspace)
    elif args.job == 'annual-review':
        workspace = Path(os.environ['RUNNER_TEMP']) / 'whitehouse-annual-review'
        _step(extract_content_verified_annual_reports_into_review_only)
        _step(observe_scanned_annual_covers_without_promotion)
        _step(record_scanned_cover_date_roles_without_filing_date_promotion)
        _step(publish_annual_extraction_audits, workspace)
    elif args.job == 'public':
        workspace = Path(os.environ['RUNNER_TEMP']) / 'whitehouse-public'
        _step(archive_public_index_and_bounded_original_pdfs)
        _step(publish_official_originals_to_evidence, workspace)
        _step(build_conservative_oge_catalog_crosswalk)
        _step(extract_hash_verified_pdfs_into_review_only)
        _step(account_for_every_public_report_link)
        _step(index_filer_reported_rows_independently_of_frontend_eligibility)
        _step(materialize_white_house_annual_filer_reported_holdings)
        _step(publish_bounded_review_observations, workspace)
    else:
        _step(rebuild_complete_oge_and_unified_review_candidates, Path(os.environ['RUNNER_TEMP']) / 'whitehouse-candidate')


if __name__ == '__main__':
    main()
