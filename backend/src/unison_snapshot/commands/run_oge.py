"""Run the controlled OGE source job in one process."""
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
import subprocess
import sys
import time

from . import (assemble_source, build_whitehouse_278t_candidate, data_trees,
               enrich_whitehouse_annual_tickers, oge_cli, oge_review,
               oge_source_state, overlay_whitehouse_annual_candidate,
               prepare_oge_annual_runtime, prepare_oge_ptr_generic)


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
    if result not in (None, 0):
        raise SystemExit(result)


def _oge(command, argv):
    previous = sys.argv
    try:
        sys.argv = ['python -m unison_snapshot ' + command]
        return _call(oge_cli.main, argv)
    finally:
        sys.argv = previous


def _output(main, argv):
    stream = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
    with redirect_stdout(stream):
        _call(main, argv)
    stream.flush()
    return stream.buffer.getvalue().decode('utf-8').rstrip('\n')


def check_the_production_collection_gate():
    Path('.local').mkdir(exist_ok=True)
    _oge('oge-gate', ['--output', '.local/oge-gate.json'])
    _call(oge_source_state.main, ['gate', '--state-root', os.environ['STATE_ROOT'],
                           '--gate', '.local/oge-gate.json'])
    return json.loads(Path('.local/oge-gate.json').read_text(encoding='utf-8'))['status']


def publish_gate(workspace):
    data_trees.publish(workspace, ('state',), 'OGE gate ' + os.environ['GITHUB_RUN_ID'])


def reject_a_partially_authorized_collection_gate():
    if os.environ['OGE_COLLECTION_ENABLED'] == 'true' and os.environ['OGE_TERMS_ACKNOWLEDGED'] != 'true':
        print('OGE collection requires the existing terms acknowledgement', file=sys.stderr)
        raise SystemExit(2)


def discover_archive_and_parse_direct_pdfs():
    started_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    Path('.local/oge-extractions').mkdir(parents=True, exist_ok=True)
    evidence, review = os.environ['EVIDENCE_ROOT'], os.environ['REVIEW_ROOT']
    _oge('discover-oge', ['--archive', evidence, '--output', '.local/oge-catalog.json',
                          '--failure-output', '.local/oge-collection-failure.json'])
    batch_size = os.environ.get('OGE_REPORT_BATCH_SIZE') or '10'
    _oge('archive-oge-direct-pdfs', ['--catalog', '.local/oge-catalog.json', '--archive', evidence,
                                    '--limit', batch_size, '--output', '.local/oge-report-batch.json'])
    _oge('extract-oge-direct-pdfs', ['--batch', '.local/oge-report-batch.json', '--archive', evidence,
                                    '--reuse-root', review + '/oge/extractions',
                                    '--output-dir', '.local/oge-extractions',
                                    '--output', '.local/oge-extraction-batch.json'])
    return started_at


def publish_immutable_official_evidence(workspace):
    data_trees.publish(workspace, ('evidence',), 'OGE originals ' + os.environ['GITHUB_RUN_ID'])


def preserve_collection_state_independently_of_qualification(workspace, successful):
    _call(oge_source_state.main, ['collection', '--state-root', os.environ['STATE_ROOT'],
        '--gate', '.local/oge-gate.json', '--catalog', '.local/oge-catalog.json',
        '--reports', '.local/oge-report-batch.json', '--extraction', '.local/oge-extraction-batch.json',
        '--failure', '.local/oge-collection-failure.json', '--successful', str(successful).lower(),
        '--run-url', 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] +
        '/actions/runs/' + os.environ['GITHUB_RUN_ID']])
    data_trees.publish(workspace, ('state',), 'OGE collection outcome')


def stage_parsed_oge_observations():
    _call(oge_review.main, ['stage', '--review-root', os.environ['REVIEW_ROOT']])


def _prepare_generic_report(selection):
    document_id, source_sha = selection.split(':', 1)
    source_args = ['--source-sha256', source_sha] if source_sha else []
    _call(prepare_oge_ptr_generic.main, ['--document-id', document_id,
        '--evidence-root', os.environ['EVIDENCE_ROOT'], '--review-root', os.environ['REVIEW_ROOT'],
        '--catalog', '.local/oge-catalog.json',
        '--catalog-history-dir', os.environ['REVIEW_ROOT'] + '/oge/discoveries', *source_args])
    return document_id, source_sha


def publish_normalized_review_data_and_build_source_candidate():
    evidence, review = os.environ['EVIDENCE_ROOT'], os.environ['REVIEW_ROOT']
    subprocess.run(['git', 'fetch', '--no-tags', '--depth=1', '--filter=blob:none',
                    'origin', '+main:refs/remotes/origin/main'], check=True)
    c = json.load(open('.local/oge-catalog.json'))
    cutoff = c.get('observed_at', c['metadata']['retrieved_at'])
    saved_observation_args = []
    include_saved = os.environ.get('INCLUDE_SAVED_OGE_OBSERVATIONS') or 'false'
    if include_saved not in ('true', 'false'):
        print('Invalid saved PTR observation switch.', file=sys.stderr)
        raise SystemExit(2)
    if include_saved == 'true':
        saved_observation_path = _output(oge_review.main, ['saved-input', '--review-root', review])
        saved_observation_args = ['--saved-observations', saved_observation_path]
    elif os.environ.get('OGE_SAVED_OBSERVATIONS_PATH'):
        print('A saved PTR input requires its explicit switch.', file=sys.stderr)
        raise SystemExit(2)
    generic_document_args, document_source_args = [], []
    generic_document_selection = _output(oge_review.main, ['selection', '--review-root', review])
    if generic_document_selection:
        with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
            for document_id, source_sha in executor.map(_prepare_generic_report, generic_document_selection.splitlines()):
                generic_document_args += ['--generic-document-id', document_id]
                if source_sha:
                    document_source_args += ['--document-source', document_id + ':' + source_sha]
    Path(review + '/candidates/sources').mkdir(parents=True, exist_ok=True)
    Path(review + '/oge/qualifications').mkdir(parents=True, exist_ok=True)
    _oge('build-oge-candidate', ['--prior-publication', '--catalog', '.local/oge-catalog.json',
        '--catalog-history-dir', review + '/oge/discoveries',
        '--extractions-dir', review + '/oge/extractions', '--evidence-root', evidence,
        '--manual-cover-dates-dir', review + '/oge/manual_cover_dates',
        '--base', 'backend/data/empty-disclosure.json', '--data-cutoff-at', cutoff,
        '--output', review + '/candidates/sources/oge-current.json',
        '--audit-output', review + '/oge/qualifications/current.json',
        *saved_observation_args, *generic_document_args, *document_source_args])
    _call(build_whitehouse_278t_candidate.main, [
        '--oge-candidate', review + '/candidates/sources/oge-current.json',
        '--review-root', review, '--evidence-root', evidence,
        '--candidate-out', review + '/candidates/sources/oge-current.json',
        '--audit-out', review + '/whitehouse/qualifications/candidate-current.json'])
    annual_args = []
    switches = (
        ('INCLUDE_SAVED_OGE_ANNUAL', 'Invalid saved annual preparation switch.'),
        ('INCLUDE_SAVED_OGE_ANNUAL_SCANS', 'Invalid saved annual scan preparation switch.'),
        ('VERIFY_OGE_ANNUAL_BLANK_SLOTS', 'Invalid saved annual blank-slot switch.'),
        ('REPLAY_OGE_ANNUAL_COVER_OCR', 'Invalid saved annual cover replay switch.'))
    for name, error in switches:
        if (os.environ.get(name) or 'false') not in ('true', 'false'):
            print(error, file=sys.stderr)
            raise SystemExit(2)
    annual = os.environ.get('INCLUDE_SAVED_OGE_ANNUAL') == 'true'
    scans = os.environ.get('INCLUDE_SAVED_OGE_ANNUAL_SCANS') == 'true'
    blank_slots = os.environ.get('VERIFY_OGE_ANNUAL_BLANK_SLOTS') == 'true'
    cover_ocr = os.environ.get('REPLAY_OGE_ANNUAL_COVER_OCR') == 'true'
    blue_document = os.environ.get('OGE_ANNUAL_BLUE_DATE_DOCUMENT_ID', '')
    blue_model = os.environ.get('OGE_ANNUAL_BLUE_DATE_MODEL_DIRECTORY', '')
    if blue_document + blue_model or blank_slots or cover_ocr:
        if not annual or not scans:
            print('Blue dates, blank slots and cover replay require saved annual scan preparation.', file=sys.stderr)
            raise SystemExit(2)
    if blue_document + blue_model:
        if not re.fullmatch('[0-9a-f]{32}', blue_document):
            raise SystemExit(2)
        raw = os.environ.get('OGE_ANNUAL_BLUE_DATE_MODEL_DIRECTORY', '')
        if (not raw or len(raw) > 512 or raw != raw.strip() or raw.startswith('/') or
                chr(92) in raw or ':' in raw or any(ord(c) < 32 or ord(c) == 127 for c in raw) or
                any(part in ('', '.', '..') for part in raw.split('/'))):
            raise SystemExit('Blue-date model directory must be a bounded canonical evidence-relative path')
    if scans and not annual:
        print('Saved annual scans require the explicit annual preparation switch.', file=sys.stderr)
        raise SystemExit(2)
    annual_document = os.environ.get('OGE_ANNUAL_DOCUMENT_ID', '')
    annual_parser = os.environ.get('OGE_ANNUAL_PARSER_VERSION', '')
    if annual:
        selection_args = []
        if annual_parser:
            selection_args += ['--parser-version', annual_parser]
        if scans:
            selection_args += ['--include-saved-scans']
        if annual_document:
            if not re.fullmatch('[0-9a-f]{32}', annual_document):
                raise SystemExit(2)
            selection_args += ['--document-id', annual_document]
        if blue_document:
            selection_args += ['--blue-date-document-id', blue_document, '--blue-date-model-directory', blue_model]
        if blank_slots:
            selection_args += ['--verify-transaction-blank-slots']
        if cover_ocr:
            if shutil.which('tesseract') is None:
                raise SystemExit(1)
            selection_args += ['--replay-cover-ocr']
        annual_input = os.environ['RUNNER_TEMP'] + '/oge-annual-input.json'
        _call(prepare_oge_annual_runtime.main, ['--review-root', review, '--evidence-root', evidence,
            '--output', annual_input, '--code-commit', subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], text=True).strip(),
            '--started-at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), *selection_args])
        annual_args += ['--oge-annual-input', annual_input, '--oge-annual-runtime-root', evidence]
    elif annual_document + annual_parser:
        print('Saved annual document/parser selection requires the explicit preparation switch.', file=sys.stderr)
        raise SystemExit(2)
    _call(overlay_whitehouse_annual_candidate.main, ['--review-root', review, '--evidence-root', evidence,
        '--candidate', review + '/candidates/sources/oge-current.json',
        '--audit-out', review + '/whitehouse/annual/candidate-current.json', *annual_args])
    if os.environ['MARKET_DISTRIBUTION_AUTHORIZED'] != 'true':
        raise SystemExit(1)
    assets_url = os.environ.get('ALPACA_ASSETS_URL') or 'https://paper-api.alpaca.markets/v2/assets'
    _call(enrich_whitehouse_annual_tickers.main, ['--candidate', review + '/candidates/sources/oge-current.json',
        '--mapping-output', review + '/whitehouse/annual/ticker-mapping-current.json',
        '--trump-2026-mapping-output', review + '/whitehouse/278t/trump-2026-ticker-mapping-current.json',
        '--checked-at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        '--assets-url', assets_url, '--distribution-authorized'])
    _call(oge_review.main, ['build', '--review-root', review])


def save_the_valid_source_and_assemble_current_peer_projections(workspace):
    _call(assemble_source.main, ['--workspace', str(workspace), '--source', 'oge',
        '--base', 'backend/data/empty-disclosure.json', '--builder-commit', os.environ['GITHUB_SHA']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('oge_generic_document_id', 'oge_ptr_document_source', 'oge_saved_observations_path',
                 'oge_annual_document_id', 'oge_annual_parser_version', 'oge_annual_blue_date_document_id',
                 'oge_annual_blue_date_model_directory'):
        parser.add_argument('--' + name, default='')
    for name in ('include_saved_oge_observations', 'include_saved_oge_annual', 'include_saved_oge_annual_scans',
                 'verify_oge_annual_blank_slots', 'replay_oge_annual_cover_ocr'):
        parser.add_argument('--' + name, default='false')
    args = parser.parse_args(argv)
    for name, value in vars(args).items():
        os.environ[name.upper()] = value
    workspace = Path(os.environ['RUNNER_TEMP']) / 'oge-source'
    status = _step(check_the_production_collection_gate)
    _step(publish_gate, workspace)
    _step(reject_a_partially_authorized_collection_gate)
    if status == 'enabled':
        collected = published = False
        try:
            _step(discover_archive_and_parse_direct_pdfs)
            collected = True
        finally:
            try:
                _step(publish_immutable_official_evidence, workspace)
                published = True
            finally:
                _step(preserve_collection_state_independently_of_qualification, workspace, collected and published)
        _step(stage_parsed_oge_observations)
        _step(publish_normalized_review_data_and_build_source_candidate)
        _step(save_the_valid_source_and_assemble_current_peer_projections, workspace)


if __name__ == '__main__':
    main()
