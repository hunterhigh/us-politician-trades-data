"""Run the controlled House source and annual-holdings jobs in one process."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from . import (assemble_source, data_trees, drain_house_ptr, house_cli,
               house_review, run_house_annual_sections)


def _step(function, *args):
    print('::group::' + function.__name__, flush=True)
    started = time.monotonic()
    try:
        return function(*args)
    finally:
        print(f'{function.__name__}: {time.monotonic() - started:.3f} seconds', flush=True)
        print('::endgroup::', flush=True)


def _call(main, argv):
    result = main(argv)
    if result:
        raise SystemExit(result)


def _house(command, argv):
    previous = sys.argv[0]
    try:
        sys.argv[0] = 'python -m unison_snapshot ' + command
        return _call(house_cli.main, argv)
    finally:
        sys.argv[0] = previous


def _output(function, *args):
    stream = io.StringIO()
    with redirect_stdout(stream):
        function(*args)
    value = stream.getvalue()
    print(value, end='', flush=True)
    return value


def _summary(value):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
        output.write(value + '\n')


def set_run_coordinates(args):
    current_year = datetime.now(timezone.utc).strftime('%Y')
    if args.mode == 'amount-policy' and not args.materialize_saved_paper:
        raise SystemExit('Amount policy refresh requires saved observations only')
    year = args.filing_year or current_year
    if year not in ('2025', current_year):
        raise SystemExit('House v1 only permits the current year or fixed 2025 PTR scope')
    if args.mode == 'amount-policy' and year != current_year:
        raise SystemExit('Amount policy refresh uses the complete current source projection')
    return year, datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def load_previous_public_checkpoint(state, year):
    Path('.local').mkdir(exist_ok=True)
    previous = state / 'checkpoints/house' / (year + '.json')
    if previous.is_file():
        shutil.copyfile(previous, '.local/previous.json')


def discover_official_house_index(evidence, year):
    _house('discover-house-index', ['--year', year, '--archive', str(evidence),
                                 '--output', '.local/discovery.json'])


def discover_official_house_member_roster(evidence):
    _house('discover-house-members', ['--archive', str(evidence), '--output', '.local/house-members.json'])
    roster_sha = json.load(open('.local/house-members.json'))['metadata']['sha256']
    shutil.copyfile('.local/house-members.json', evidence / 'house_clerk/members' / (roster_sha + '.roster.json'))


def build_recoverable_checkpoint(evidence, run_at):
    previous = Path('.local/previous.json')
    arguments = ['--checkpoint', str(previous)] if previous.is_file() and previous.stat().st_size else []
    _house('plan-house-ptr-sync', ['--discovery', '.local/discovery.json', '--archive', str(evidence),
        '--planned-at', run_at, '--output', '.local/checkpoint.json', *arguments])


def drain_house_ptr_archive_queue_within_the_time_budget(evidence, budget):
    _call(drain_house_ptr.main, ['--checkpoint', '.local/checkpoint.json', '--archive', str(evidence),
        '--budget-seconds', budget, '--output', '.local/archive-budget.json'])


def publish_immutable_evidence_first(workspace):
    print(json.dumps(data_trees.publish(workspace, ('evidence',), 'House originals ' + os.environ['GITHUB_RUN_ID'])))


def build_public_run_status():
    checkpoint = json.loads(Path('.local/checkpoint.json').read_text(encoding='utf-8'))
    status = {
        'schema_version': 'house-source-run/v1',
        'source_id': 'house_clerk',
        'filing_year': checkpoint['filing_year'],
        'index_sha256': checkpoint['index_sha256'],
        'run_at': checkpoint['planned_at'],
        'counts': checkpoint['counts'],
        'queue_count': len(checkpoint['queue']),
        'anomaly_count': len(checkpoint['anomalies']),
        'status': 'attention' if checkpoint['anomalies'] else 'ok',
    }
    Path('.local/last-run.json').write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')


def save_archive_checkpoint_and_collection_state(state, year):
    (state / 'checkpoints/house').mkdir(parents=True, exist_ok=True)
    (state / 'status').mkdir(parents=True, exist_ok=True)
    shutil.copyfile('.local/checkpoint.json', state / 'checkpoints/house' / (year + '.json'))
    if year == datetime.now(timezone.utc).strftime('%Y'):
        shutil.copyfile('.local/last-run.json', state / 'status/house_clerk.json')
        shutil.copyfile('.local/last-run.json', state / 'status/last-run.json')
    else:
        shutil.copyfile('.local/last-run.json', state / 'status' / ('house_backfill_' + year + '.json'))


def persist_collection_state_after_evidence(workspace):
    print(json.dumps(data_trees.publish(workspace, ('state',), 'House collection ' + os.environ['GITHUB_RUN_ID'])))


def consume_the_fixed_2025_archived_ptr_inventory():
    _call(house_review.main, ['backfill', '--year', '2025', '--batch-size', '25'])


def select_unprocessed_evidence(args, year):
    if args.materialize_saved_paper:
        Path('.local').mkdir(exist_ok=True)
        Path('.local/parse-batch.txt').write_text('', encoding='utf-8')
        return {}
    batch_size = args.batch_size or os.environ['BATCH_SIZE']
    if not re.fullmatch('[0-9]+', batch_size):
        raise SystemExit('batch_size must be an integer')
    if not 1 <= int(batch_size) <= 25:
        raise SystemExit('batch_size must be between 1 and 25')
    return house_review.main(['select', '--year', year, '--batch-size', batch_size])


def detect_pages_requiring_ocr_in_this_batch():
    return house_review.main(['ocr'])


def _parse_ptr_report(task):
    metadata_path, evidence, review, parser_commit = task
    data = json.load(open(metadata_path, encoding='utf-8'))
    year, document_id, sha = data['filing_year'], data['document_id'], data['sha256']
    relative = Path(str(year)) / document_id / (sha + '.json')
    extraction = review / 'house_clerk/extractions' / relative
    identity = review / 'house_clerk/identities' / relative
    failure = review / 'house_clerk/failures' / relative
    qualification = review / 'house_clerk/qualifications' / relative
    for path in (extraction, identity, failure):
        path.parent.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        _house('parse-house-ptr', ['--archive', str(evidence), '--metadata', metadata_path, '--output', str(extraction)])
    except Exception as exc:
        error = str(exc).replace('\n', ' ')[-480:]
    except SystemExit as exc:
        if exc.code not in (None, 0):
            error = str(exc).replace('\n', ' ')[-480:]
    if error is None:
        failure.unlink(missing_ok=True)
        return 1
    for path in (extraction, identity, qualification):
        path.unlink(missing_ok=True)
    previous = {}
    if failure.is_file():
        try:
            previous = json.loads(failure.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    from unison_snapshot.house_ptr import PARSER_RETRY_VERSION
    value = {
        'schema_version': 'house-ptr-parse-failure/v1',
        'filing_year': int(year),
        'document_id': document_id,
        'source_sha256': sha,
        'parser_commit': parser_commit,
        'parser_version': PARSER_RETRY_VERSION,
        'attempts': int(previous.get('attempts', 0)) + 1,
        'last_attempt_at': datetime.now(timezone.utc).isoformat(),
        'error': error,
    }
    failure.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    return 0


def parse_and_prepare_machine_extraction_artifacts(evidence, review):
    metadata = [line for line in Path('.local/parse-batch.txt').read_text(encoding='utf-8').splitlines() if line]
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        parsed = sum(executor.map(_parse_ptr_report,
            ((line, evidence, review, os.environ['PARSER_COMMIT']) for line in metadata)))
    failed = len(metadata) - parsed
    _summary(f'Parsed {parsed} archived PTRs; {failed} require parser attention.')


def _refresh_identity(task):
    extraction, review, roster = task
    relative = extraction.relative_to(review / 'house_clerk/extractions')
    identity = review / 'house_clerk/identities' / relative
    identity.parent.mkdir(parents=True, exist_ok=True)
    _house('suggest-house-identity', ['--extraction', str(extraction), '--members', str(roster), '--output', str(identity)])


def refresh_deterministic_house_identities(evidence, review, year):
    roster = sorted((evidence / 'house_clerk/members').glob('*.roster.json'))[-1]
    extractions = sorted((review / 'house_clerk/extractions' / year).rglob('*.json'))
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        list(executor.map(_refresh_identity, ((path, review, roster) for path in extractions)))


def _qualify_ptr_report(task):
    extraction, evidence, review, mode, cache_ready = task
    relative = extraction.relative_to(review / 'house_clerk/extractions')
    identity = review / 'house_clerk/identities' / relative
    qualification = review / 'house_clerk/qualifications' / relative
    if mode == 'amount-policy' and qualification.is_file():
        return
    method = json.load(open(extraction, encoding='utf-8')).get('extraction_method', '')
    if method == 'source_bound_paper_cells':
        return
    if mode != 'amount-policy' and not cache_ready and method != 'tesseract_legacy_checkbox':
        return
    if not identity.is_file():
        return
    qualification.parent.mkdir(parents=True, exist_ok=True)
    _house('qualify-house-ptr', ['--extraction', str(extraction), '--identity', str(identity),
        '--evidence-root', str(evidence), '--output', str(qualification)])


def build_automatic_qualification_artifacts(args, evidence, review, year, selection):
    extractions = sorted((review / 'house_clerk/extractions' / year).rglob('*.json'))
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        list(executor.map(_qualify_ptr_report,
            ((path, evidence, review, args.mode, selection.get('cache_ready', False)) for path in extractions)))


def bridge_bounded_house_paper_observations_into_qualification(args, evidence, review):
    arguments = ['--document-id', args.paper_document_id] if args.paper_document_id else []
    if args.materialize_saved_paper:
        arguments += ['--materialize-saved']
    output = _output(_house, 'bridge-house-paper-batch', ['--evidence-root', str(evidence),
        '--review-root', str(review), '--parse-batch', '.local/parse-batch.txt', '--tesseract', 'tesseract', *arguments])
    Path('.local/house-paper-bridge.json').write_text(output, encoding='utf-8')
    _summary('House paper bridge: ' + output.rstrip('\n'))


def qualify_registered_house_paper_corrections(evidence, review):
    for correction in sorted(Path(os.environ['CORRECTION_DIR']).glob('*.json')):
        _house('correct-reviewed-house-paper', ['--evidence-root', str(evidence), '--review-root', str(review),
                                              '--correction', str(correction)])


def build_qualification_queue_status(year):
    _call(house_review.main, ['status', '--year', year])


def build_and_validate_frontend_candidate(args, evidence, review, state, workspace):
    (review / 'candidates/sources').mkdir(parents=True, exist_ok=True)
    path = review / 'candidates/sources/house_clerk-current.json'
    candidate_base = path if path.is_file() else Path('backend/data/empty-disclosure.json')
    if args.mode == 'amount-policy':
        from unison_snapshot.codec import encode
        from unison_snapshot.house_open_amounts import refresh_candidate
        candidate = refresh_candidate(json.loads(path.read_bytes()), str(review), str(evidence))
        path.write_bytes(encode(candidate))
    else:
        _house('build-house-candidate', ['--review-root', str(review), '--evidence-root', str(evidence),
            '--state-status', str(state / 'status/house_clerk.json'), '--base', str(candidate_base), '--output', str(path)])
    shutil.copyfile(path, review / 'candidates/house-current.json')
    _call(assemble_source.main, ['--workspace', str(workspace), '--source', 'house_clerk',
        '--base', 'backend/data/empty-disclosure.json', '--builder-commit', os.environ['GITHUB_SHA']])


def report_remaining_parser_work(selection):
    _summary('Saved bounded parse progress; ' + str(selection['remaining_after_batch']) +
             ' reports remain queued. Qualified candidate and waterline unchanged.')


def discover_and_archive_a_bounded_annual_report_batch(args, evidence, run_dir):
    year = args.filing_year or '2025'
    if re.search('[^0-9:]', year + ':' + args.batch_size):
        raise SystemExit('filing_year and batch_size must be integers')
    if not 2008 <= int(year) <= 2100 or not 1 <= int(args.batch_size) <= 25:
        raise SystemExit(1)
    discovery_path = run_dir / ('house-financial-' + year + '.json')
    _house('discover-house-index', ['--year', year, '--archive', str(evidence), '--output', str(discovery_path)])
    from unison_snapshot.house_members import suggest_identity
    discovery = json.loads(discovery_path.read_text(encoding='utf-8'))
    roster_path = sorted((evidence / 'house_clerk/members').glob('*.roster.json'))[-1]
    roster = json.loads(roster_path.read_text(encoding='utf-8'))
    annual = [row for row in discovery['filings'] if row['filing_type'] == 'O'
              and suggest_identity({'source': row}, roster).get('status') == 'matched_automatically']
    pending = [row for row in annual if not any(
        (evidence / 'house_clerk/financial-reports' / str(row['filing_year']) / row['document_id']).glob('*.json'))]
    pending.sort(key=lambda row: (row.get('filed_date') or '', row['document_id']), reverse=True)
    selected = pending[:int(args.batch_size)]
    batch = {'index_sha': discovery['metadata']['sha256'], 'current_member_annual_count': len(annual),
             'unarchived_before_count': len(pending), 'selected_count': len(selected), 'rows': selected}
    (run_dir / 'house-holding-batch.json').write_text(
        json.dumps(batch, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    for row in batch['rows']:
        _house('archive-house-holding-report', ['--year', str(row['filing_year']), '--index-sha', batch['index_sha'],
            '--document-id', row['document_id'], '--archive', str(evidence),
            '--output', str(run_dir / 'last-house-holding.json')])


def publish_immutable_annual_evidence_first(workspace):
    print(json.dumps(data_trees.publish(workspace, ('evidence',), 'House annual evidence ' + os.environ['GITHUB_RUN_ID'])))


def _parse_annual_report(task):
    metadata_path, evidence, review, roster, parser_commit = task
    metadata = json.load(open(metadata_path, encoding='utf-8'))
    relative = Path(str(metadata['filing_year'])) / metadata['document_id'] / (metadata['sha256'] + '.json')
    extraction = review / 'house_clerk/holding_extractions' / relative
    identity = review / 'house_clerk/holding_identities' / relative
    qualification = review / 'house_clerk/holding_qualifications' / relative
    failure = review / 'house_clerk/holding_failures' / relative
    for path in (extraction, identity, qualification, failure):
        path.parent.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        _house('parse-house-holding-report', ['--archive', str(evidence), '--metadata', metadata_path, '--output', str(extraction)])
    except Exception as exc:
        error = str(exc).replace('\n', ' ')[-480:]
    except SystemExit as exc:
        if exc.code not in (None, 0):
            error = str(exc).replace('\n', ' ')[-480:]
    if error is None:
        failure.unlink(missing_ok=True)
        _house('suggest-house-identity', ['--extraction', str(extraction), '--members', str(roster), '--output', str(identity)])
        _house('qualify-house-holding-report', ['--extraction', str(extraction), '--identity', str(identity), '--output', str(qualification)])
        return 1
    for path in (extraction, identity, qualification):
        path.unlink(missing_ok=True)
    value = {'schema_version': 'house-holding-parse-failure/v1',
             'document_id': metadata['document_id'], 'filing_year': metadata['filing_year'],
             'source_sha256': metadata['sha256'], 'parser_commit': parser_commit,
             'parser_version': house_review.HOLDING_PARSER_VERSION, 'error': error,
             'last_attempt_at': datetime.now(timezone.utc).isoformat()}
    failure.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    return 0


def parse_match_and_qualify_annual_reports(evidence, review, run_dir):
    roster = sorted((evidence / 'house_clerk/members').glob('*.roster.json'))[-1]
    _call(house_review.main, ['select-holdings'])
    metadata = [line for line in (run_dir / 'holding-metadata.txt').read_text(encoding='utf-8').splitlines() if line]
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        parsed = sum(executor.map(_parse_annual_report,
            ((line, evidence, review, roster, os.environ['PARSER_COMMIT']) for line in metadata)))
    print(f'Parsed {parsed} annual reports; {len(metadata) - parsed} require parser attention.', flush=True)


def observe_annual_sections_without_changing_holding_qualification(evidence, review, run_dir):
    (review / 'house_clerk/holding_section_observations').mkdir(parents=True, exist_ok=True)
    (review / 'house_clerk/holding_section_failures').mkdir(parents=True, exist_ok=True)
    _call(run_house_annual_sections.main, ['--evidence-root', str(evidence), '--review-root', str(review),
        '--new-metadata-list', str(run_dir / 'holding-metadata.txt'), '--old-limit', '2'])


def build_holding_status_and_candidates(evidence, review, state, workspace):
    _call(house_review.main, ['holding-status'])
    (review / 'candidates/sources').mkdir(parents=True, exist_ok=True)
    path = review / 'candidates/sources/house_clerk-current.json'
    candidate_base = path if path.is_file() else Path('backend/data/empty-disclosure.json')
    _house('build-house-candidate', ['--review-root', str(review), '--evidence-root', str(evidence),
        '--state-status', str(state / 'status/house_clerk.json'), '--base', str(candidate_base), '--output', str(path)])
    shutil.copyfile(path, review / 'candidates/house-current.json')
    _call(assemble_source.main, ['--workspace', str(workspace), '--source', 'house_clerk',
        '--base', 'backend/data/empty-disclosure.json', '--builder-commit', os.environ['GITHUB_SHA']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, choices=('source', 'annual-holdings'))
    parser.add_argument('--mode', default='ptr', choices=('ptr', 'annual', 'amount-policy'))
    parser.add_argument('--filing_year', default='')
    parser.add_argument('--archive_budget_seconds', default='900')
    parser.add_argument('--batch_size', default='25')
    parser.add_argument('--paper_document_id', default='')
    parser.add_argument('--materialize_saved_paper', default='false')
    args = parser.parse_args(argv)
    args.materialize_saved_paper = args.materialize_saved_paper == 'true'
    evidence, review, state = (Path(os.environ[name]) for name in ('EVIDENCE_ROOT', 'REVIEW_ROOT', 'STATE_ROOT'))
    run_dir = Path(os.environ['RUNNER_TEMP'])
    workspace = run_dir / ('house-source' if args.job == 'source' else 'house-annual')
    if args.job == 'annual-holdings':
        _step(discover_and_archive_a_bounded_annual_report_batch, args, evidence, run_dir)
        _step(publish_immutable_annual_evidence_first, workspace)
        _step(parse_match_and_qualify_annual_reports, evidence, review, run_dir)
        _step(observe_annual_sections_without_changing_holding_qualification, evidence, review, run_dir)
        _step(build_holding_status_and_candidates, evidence, review, state, workspace)
        return
    year, run_at = _step(set_run_coordinates, args)
    if not args.materialize_saved_paper:
        _step(load_previous_public_checkpoint, state, year)
        _step(discover_official_house_index, evidence, year)
        _step(discover_official_house_member_roster, evidence)
        _step(build_recoverable_checkpoint, evidence, run_at)
        _step(drain_house_ptr_archive_queue_within_the_time_budget, evidence,
              args.archive_budget_seconds or os.environ['BUDGET_SECONDS'])
        _step(publish_immutable_evidence_first, workspace)
        _step(build_public_run_status)
        _step(save_archive_checkpoint_and_collection_state, state, year)
        _step(persist_collection_state_after_evidence, workspace)
    if args.filing_year == '2025':
        _step(consume_the_fixed_2025_archived_ptr_inventory)
        return
    selection = _step(select_unprocessed_evidence, args, year)
    if not args.materialize_saved_paper:
        _step(detect_pages_requiring_ocr_in_this_batch)
        _step(parse_and_prepare_machine_extraction_artifacts, evidence, review)
        _step(refresh_deterministic_house_identities, evidence, review, year)
    if not args.materialize_saved_paper or args.mode == 'amount-policy':
        _step(build_automatic_qualification_artifacts, args, evidence, review, year, selection)
    if args.mode != 'amount-policy':
        _step(bridge_bounded_house_paper_observations_into_qualification, args, evidence, review)
    if not args.materialize_saved_paper and selection['cache_ready']:
        _step(qualify_registered_house_paper_corrections, evidence, review)
    if args.materialize_saved_paper or selection['cache_ready']:
        _step(build_qualification_queue_status, year)
        _step(build_and_validate_frontend_candidate, args, evidence, review, state, workspace)
    if not args.materialize_saved_paper and not selection['cache_ready']:
        _step(report_remaining_parser_work, selection)


if __name__ == '__main__':
    main()
