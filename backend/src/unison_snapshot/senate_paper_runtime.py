"""Prepare existing Senate saved-grid pointers for the existing paired CLI.

No observation, identifier or qualification is manufactured here. The receipt
records dependencies only; ordinary consumers still replay source cells.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .senate import SenateEfdError

MAX_DOCUMENTS = 12
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PAGES = 100
_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _inside(root, reference):
    if (not isinstance(reference, str) or not reference or '\\' in reference or ':' in reference or
            PurePosixPath(reference).is_absolute() or
            PurePosixPath(reference).as_posix() != reference or
            any(part in {'', '.', '..'} for part in reference.split('/'))):
        raise SenateEfdError('Senate saved preparation requires canonical relative paths')
    path = (root / reference).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise SenateEfdError('Senate saved preparation source escapes its controlled root')
    return path


def prepare_senate_paper_inputs(review_root: Path, evidence_root: Path, *, document_ids: list[str]) -> dict:
    """Rebind selected existing pointers and return unchanged grid/word maps."""
    from .senate_paper import PAPER_EXTRACTION_SCHEMA, replay_legacy_grid_extraction
    from .senate_reports import PARSER_VERSION, inspect_paper_entrypoint, parse_paper_viewer_manifest
    if (not isinstance(document_ids, list) or not document_ids or len(document_ids) > MAX_DOCUMENTS or
            any(not isinstance(item, str) or not _ID.fullmatch(item) for item in document_ids) or
            len(set(document_ids)) != len(document_ids)):
        raise SenateEfdError('Senate saved preparation requires a bounded unique explicit document selection')
    review, evidence = Path(review_root).resolve(strict=True), Path(evidence_root).resolve(strict=True)
    if not review.is_dir() or not evidence.is_dir():
        raise SenateEfdError('Senate saved preparation requires controlled review and evidence directories')
    files, total = {}, 0

    def read(kind, relative, *, json_value=False):
        nonlocal total
        root = review if kind == 'review' else evidence
        path = _inside(root, relative)
        maximum = MAX_JSON_BYTES if json_value or relative.endswith('.json') else MAX_FILE_BYTES
        if path.stat().st_size > maximum:
            raise SenateEfdError('Senate saved preparation source exceeds byte budget')
        with path.open('rb') as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise SenateEfdError('Senate saved preparation source exceeds byte budget')
        item = {'root': kind, 'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        key = kind, relative
        if key in files and files[key] != item:
            raise SenateEfdError('Senate saved preparation source changed during preparation')
        if key not in files:
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise SenateEfdError('Senate saved preparation dependencies exceed total byte budget')
            files[key] = item
        if not json_value:
            return raw
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise SenateEfdError('Senate saved preparation JSON must be an object')
        return value

    status = read('review', 'status/senate_efd.json', json_value=True)
    catalog = status.get('catalog_sha256')
    if not isinstance(catalog, str) or not _SHA.fullmatch(catalog) or status.get('report_parser_version') != PARSER_VERSION:
        raise SenateEfdError('Senate saved preparation requires the current explicit report parser and catalog')
    discovery = read('review', f'senate_efd/discoveries/{catalog}.json', json_value=True)
    reports = discovery.get('reports')
    if not isinstance(reports, list):
        raise SenateEfdError('Senate saved preparation catalog reports are missing')
    grids, words, documents = {}, {}, []
    for document in sorted(document_ids):
        selected = [row for row in reports if isinstance(row, dict) and row.get('document_id') == document]
        url = f'https://efdsearch.senate.gov/search/view/paper/{document}/'
        if (len(selected) != 1 or selected[0].get('access_method') != 'paper_ptr' or
                selected[0].get('source_id') != 'senate_efd' or selected[0].get('document_url') != url):
            raise SenateEfdError('Senate saved preparation selected report is not unique canonical paper source')
        report = selected[0]
        manifest = read('evidence', f'senate_efd/paper_pages/{document}/manifest.json', json_value=True)
        digest = manifest.get('entrypoint_source_sha256')
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise SenateEfdError('Senate saved preparation manifest source hash is invalid')
        prefix = f'senate_efd/reports/{document}/{digest}'
        metadata = read('evidence', prefix + '.metadata.json', json_value=True)
        viewer = read('evidence', prefix + '.html')
        if (metadata.get('sha256') != digest or any(metadata.get(key) != report.get(key) for key in (
                'source_id', 'access_method', 'document_id', 'document_url', 'filer_name',
                'portal_listed_date', 'report_label_date', 'report_amendment_number'))):
            raise SenateEfdError('Senate saved preparation original metadata differs from the selected catalog')
        actual_manifest = parse_paper_viewer_manifest(metadata, viewer)
        if (any(manifest.get(key) != value for key, value in actual_manifest.items() if key != 'pages') or
                manifest.get('evidence_complete') is not True or type(manifest.get('page_count')) is not int or
                not 1 <= manifest['page_count'] <= MAX_PAGES or not isinstance(manifest.get('pages'), list) or
                [{key: page.get(key) for key in ('page_number', 'source_url')} for page in manifest['pages']]
                != actual_manifest['pages']):
            raise SenateEfdError('Senate saved preparation manifest differs from the original viewer')
        body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
        encoded = (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
        if hashlib.sha256(encoded).hexdigest() != manifest.get('manifest_sha256'):
            raise SenateEfdError('Senate saved preparation manifest digest differs')
        for page in manifest['pages']:
            number, sha = page['page_number'], page.get('sha256')
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise SenateEfdError('Senate saved preparation page source hash is invalid')
            relative = f'senate_efd/paper_pages/{document}/{number}/{sha}.gif'
            if page.get('archive_path') != relative or hashlib.sha256(read('evidence', relative)).hexdigest() != sha:
                raise SenateEfdError('Senate saved preparation canonical page or image hash differs')
        inspection = read('review', f'senate_efd/paper_inspections/{document}/{digest}/{PARSER_VERSION}.json', json_value=True)
        if inspection != inspect_paper_entrypoint(metadata, viewer):
            raise SenateEfdError('Senate saved preparation inspection differs from original entrypoint')
        extraction = read('review', f'senate_efd/extractions/{document}/{digest}/{PARSER_VERSION}.json', json_value=True)
        if (extraction.get('schema_version') != PAPER_EXTRACTION_SCHEMA or extraction.get('source_id') != 'senate_efd' or
                extraction.get('parser_version') != PARSER_VERSION or extraction.get('document_id') != document or
                extraction.get('source_sha256') != digest or extraction.get('source_url') != url):
            raise SenateEfdError('Senate saved preparation selected extraction binding differs')
        pointer = extraction.get('grid_replay_input', {})
        sha = pointer.get('sha256') if isinstance(pointer, dict) else None
        relative = f'senate_efd/paper_grid_inputs/{sha}.json'
        if not isinstance(sha, str) or not _SHA.fullmatch(sha) or pointer.get('path') != relative:
            raise SenateEfdError('Senate saved preparation requires an existing canonical replay pointer')
        raw = read('review', relative)
        if len(raw) > MAX_JSON_BYTES or hashlib.sha256(raw).hexdigest() != sha:
            raise SenateEfdError('Senate saved preparation replay pointer digest differs')
        saved = json.loads(raw)
        if (not isinstance(saved, dict) or saved.get('schema_version') != 'senate-paper-grid-replay/v1' or
                saved.get('report') != report or not isinstance(saved.get('observation'), dict) or
                not isinstance(saved.get('cached_word_pages'), list)):
            raise SenateEfdError('Senate saved preparation replay input differs from selected report')
        # Reuse the complete existing source/field/cell and legacy-ID contract.
        # This returns no new eligibility and does not start a recognizer.
        replay_legacy_grid_extraction(extraction, evidence, review)
        grids[document], words[document] = saved['observation'], saved['cached_word_pages']
        documents.append({'document_id': document, 'source_sha256': digest,
                          'parser_version': PARSER_VERSION, 'replay_input_sha256': sha})
    return {'grid_observations': grids, 'cached_word_pages': words,
            'receipt': {'schema_version': 'senate-paper-controlled-inputs/v1', 'documents': documents,
                'files': sorted(files.values(), key=lambda item: (item['root'], item['path'])),
                'total_bytes': total, 'recognizer_started': False,
                'qualification_status': 'deferred_to_existing_consumer',
                'scope': 'selected_saved_paper_reports_not_full_catalog'}}
