"""Prepare one existing ordinary native PTR interpretation pair, offline.

This prepares immutable parser inputs, not financial qualification or a choice
for future runs. The receiver must still select the document explicitly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .codec import encode, digest
from .oge import OgeCatalogError, SCHEMA as CATALOG_SCHEMA
from .oge_reports import (
    PARSER_VERSION, GENERIC_PARSER_VERSION, MAX_PDF_BYTES,
    _read_archived_source, collapse_direct_catalog_records,
    parse_archived_pdf, selected_parser_version,
)

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_CATALOG_FILES = 128
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_CATALOG_ROWS = 100_000
MAX_ARCHIVE_VERSIONS = 32
_ID = re.compile(r'[0-9a-f]{32}')
_SHA = re.compile(r'[0-9a-f]{64}')


def _error(message):
    raise OgeCatalogError('OGE PTR preparation: ' + message)


def _inside(root: Path, path: Path) -> Path:
    # Canonical files cannot redirect through a symlink/junction, even to a
    # different location inside the root.
    absolute = path.absolute()
    resolved = path.resolve()
    if resolved != absolute or not resolved.is_relative_to(root):
        _error('noncanonical or escaping controlled path')
    return resolved


def _json(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        _error('missing or oversized JSON input')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise OgeCatalogError('OGE PTR preparation: malformed JSON input') from exc


def _catalog_record(document_id, catalog, history, review):
    paths = [catalog.resolve()]
    if history is not None:
        history = _inside(review, history)
        if history.exists() and not history.is_dir():
            _error('catalog history is not a directory')
        for path in history.glob('*.json'):
            if len(paths) >= MAX_CATALOG_FILES:
                _error('catalog file budget exceeded')
            paths.append(_inside(review, path))
    records, total_bytes, total_rows = [], 0, 0
    for path in paths:
        if not path.is_file():
            _error('catalog input is missing')
        total_bytes += path.stat().st_size
        if total_bytes > MAX_CATALOG_BYTES:
            _error('catalog byte budget exceeded')
        value = _json(path)
        if not isinstance(value, dict) or value.get('schema_version') != CATALOG_SCHEMA:
            _error('invalid normalized catalog')
        rows = value.get('transactions')
        if not isinstance(rows, list):
            _error('catalog has no transaction inventory')
        total_rows += len(rows)
        if total_rows > MAX_CATALOG_ROWS:
            _error('catalog row budget exceeded')
        for row in rows:
            if not isinstance(row, dict) or row.get('access_method') not in {'direct_pdf', 'request_required'}:
                _error('invalid catalog access method')
            if row['access_method'] == 'direct_pdf':
                records.append(row)
    # Preserve the receiver's current+historical conflict semantics; do not
    # filter to the target first and hide conflicting occurrences.
    direct, _ = collapse_direct_catalog_records(records)
    selected = [row for row in direct if row['source_document_id'] == document_id]
    if len(selected) != 1:
        _error('document is not uniquely present in the direct catalog context')
    return selected[0], paths


def prepare_oge_ptr_generic(*, document_id: str, evidence_root: Path,
                            review_root: Path, catalog: Path,
                            catalog_history_dir: Path | None = None,
                            source_sha256: str | None = None) -> dict:
    """Rebuild and immutably prepare exactly one canonical native v2/v7 pair.

    All source, interpretation and existing-output checks finish before any
    cache is created. Existing equal objects retain their original encoding.
    No collection, OCR, model or financial eligibility is invoked here.
    """
    if not isinstance(document_id, str) or not _ID.fullmatch(document_id):
        _error('document ID must be 32 lowercase hexadecimal characters')
    if source_sha256 is not None and (
            not isinstance(source_sha256, str) or not _SHA.fullmatch(source_sha256)):
        _error('explicit source SHA must be 64 lowercase hexadecimal characters')
    evidence, review = Path(evidence_root).resolve(), Path(review_root).resolve()
    if (not evidence.is_dir() or not review.is_dir() or
            evidence.is_relative_to(review) or review.is_relative_to(evidence)):
        _error('evidence and review must be separate existing roots')
    record, dependencies = _catalog_record(document_id, Path(catalog),
        Path(catalog_history_dir) if catalog_history_dir is not None else None, review)
    folder = _inside(evidence, evidence / 'oge/reports' / document_id)
    if not folder.is_dir():
        _error('canonical archived source is missing')
    entries = []
    for path in folder.iterdir():
        if source_sha256 is None:
            if len(entries) >= 2:
                _error('multiple or noncanonical archived source versions')
        elif len(entries) >= 2 * MAX_ARCHIVE_VERSIONS:
            _error('archived source version inventory exceeds budget')
        path = _inside(evidence, path)
        if source_sha256 is not None and (
                not path.is_file() or not _SHA.fullmatch(path.stem) or
                path.suffix not in {'.json', '.pdf'}):
            _error('explicit source inventory contains a noncanonical entry')
        entries.append(path)
    metadata_paths = [path for path in entries if path.suffix == '.json']
    if source_sha256 is None:
        if len(entries) != 2 or len(metadata_paths) != 1:
            _error('one canonical metadata/PDF pair is required')
        metadata_path = metadata_paths[0]
    else:
        metadata_shas = {path.stem for path in metadata_paths}
        pdf_shas = {path.stem for path in entries if path.suffix == '.pdf'}
        if not metadata_shas or metadata_shas != pdf_shas:
            _error('explicit source inventory lacks canonical metadata/PDF pairs')
        if source_sha256 not in metadata_shas:
            _error('explicit archived source SHA is unavailable')
        # This is an interpretation input, never an assertion of official
        # currency or a relationship between the retained original versions.
        metadata_path = folder / f'{source_sha256}.json'
    sha = metadata_path.stem
    if not _SHA.fullmatch(sha):
        _error('noncanonical archive metadata filename')
    pdf = _inside(evidence, folder / f'{sha}.pdf')
    if pdf not in entries or not pdf.is_file() or pdf.stat().st_size > MAX_PDF_BYTES:
        _error('missing or oversized canonical PDF')
    metadata = _json(metadata_path)
    if (not isinstance(metadata, dict) or metadata.get('document_id') != document_id or
            metadata.get('sha256') != sha or
            metadata.get('archive_path') != pdf.relative_to(evidence).as_posix() or
            metadata.get('document_url') != record['document_url']):
        _error('canonical source identity differs from catalog')
    for key in ('filer_name', 'agency', 'position_title', 'catalog_added_date',
                'amended_label', 'pending_final_oge_disposition'):
        if metadata.get(key) != record.get(key):
            _error('source metadata differs from catalog: ' + key)
    _, actual_pdf, actual_sha = _read_archived_source(evidence, metadata_path)
    if actual_pdf != pdf or actual_sha != sha:
        _error('source bytes do not match canonical identity')
    versions = (selected_parser_version(metadata, pdf, sha),
                selected_parser_version(metadata, pdf, sha, generic=True))
    if versions != (PARSER_VERSION, GENERIC_PARSER_VERSION):
        _error('only ordinary native v2/v7 sources are supported')
    values = [parse_archived_pdf(evidence, metadata_path),
              parse_archived_pdf(evidence, metadata_path, generic=True)]
    prepared = []
    dependencies += entries
    for value, version in zip(values, versions):
        if (value.get('parser_version') != version or value.get('document_id') != document_id or
                value.get('source_sha256') != sha or value.get('source_url') != record['document_url']):
            _error('source replay differs from the selected native pair')
        content = encode(value)
        if len(content) > MAX_JSON_BYTES:
            _error('extraction JSON budget exceeded')
        target = _inside(review, review / 'oge/extractions' / document_id / sha /
                         (version.replace('/', '-') + '.json'))
        if target.exists():
            if any(os.path.samefile(target, dependency) for dependency in dependencies):
                _error('cache output aliases a source dependency')
            # Full object comparison, not a few binding fields or a saved hash.
            if encode(_json(target)) != content:
                _error('immutable extraction differs from complete source replay')
        prepared.append((target, value, content))
    if prepared[0][0] == prepared[1][0] or (all(path.exists() for path, _, _ in prepared) and
            os.path.samefile(prepared[0][0], prepared[1][0])):
        _error('cache outputs alias each other')
    created = []
    try:
        for target, _, content in prepared:
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation never replaces a concurrent/existing cache.
            with target.open('xb') as stream:
                created.append(target)
                stream.write(content)
    except OSError:
        for target in created:
            target.unlink()
        raise
    return {'schema_version': 'oge-native-ptr-preparation/v1',
        'document_id': document_id, 'source_sha256': sha,
        **({'source_selection': 'explicit_archived_input'} if source_sha256 is not None else {}),
        'parser_versions': list(versions),
        'created_paths': [path.relative_to(review).as_posix() for path in created],
        'extractions': [{'path': target.relative_to(review).as_posix(),
                         'parser_version': value['parser_version'],
                         'extraction_canonical_sha256': digest(content)}
                        for target, value, content in prepared]}
