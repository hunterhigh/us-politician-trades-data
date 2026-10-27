"""Replay fixed official OGE catalog bytes before binding a direct PDF.

These readers bind original inputs, not owner corrections or qualification.
They neither collect sources nor choose a successful extraction version.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
import re
import subprocess

from .migration_inventory import InventoryError
from ..oge import (API_URL, MAX_CATALOG_ROWS, MAX_RESPONSE_BYTES, OgeCatalogError,
                   parse_catalog_page, build_catalog)
from ..oge_reports import (MAX_PDF_BYTES, REPORT_ARCHIVE_SCHEMA, _validate_pdf,
                           collapse_direct_catalog_records)

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PATHS = 128
MAX_PATH_BYTES = 64 * 1024
_SHA = re.compile(r'[a-f0-9]{64}')
_REF = re.compile(r'[a-f0-9]{40}')
_DOCUMENT = re.compile(r'[a-f0-9]{32}')


class OgeCatalogSourceError(InventoryError):
    pass


def _need(condition, message):
    if not condition:
        raise OgeCatalogSourceError(message)


def _fixed(ref):
    _need(isinstance(ref, str) and _REF.fullmatch(ref) is not None,
          'OGE catalog proof requires fixed evidence and review commits')


def _read(sources, ref, path, limit):
    raw = sources.read(ref, path, limit)
    _need(isinstance(raw, bytes) and len(raw) <= limit, 'OGE source object exceeds byte budget')
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, 'OGE source JSON repeats a key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON number: ' + value)


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except OgeCatalogSourceError:
        raise
    except (ValueError, UnicodeError) as error:
        raise OgeCatalogSourceError('OGE source JSON is invalid') from error
    _need(isinstance(value, dict), 'OGE source JSON must be an object')
    return value


def _object(sources, ref, path):
    return _json(_read(sources, ref, path, MAX_JSON_BYTES))


def load_fixed_oge_catalog(sources, evidence_commit, review_commit):
    """Return (complete catalog, archive metadata) selected by fixed review.

    ``sources`` provides the existing bounded ``read(commit,path,limit)``.
    Its aggregate byte budget applies across this call and later PDF reads.
    No latest-observation fallback or normalized-only identity is accepted.
    """
    _fixed(evidence_commit); _fixed(review_commit)
    status = _object(sources, review_commit, 'status/oge.json')
    digest = status.get('catalog_sha256')
    _need(isinstance(digest, str) and _SHA.fullmatch(digest) is not None,
          'OGE review catalog selection is unavailable')
    archive = _object(sources, evidence_commit, f'oge/catalog/{digest}.json')
    _need(archive.get('schema_version') == 'oge-catalog-archive/v1'
          and archive.get('source_id') == 'oge' and archive.get('source_url') == API_URL
          and archive.get('sha256') == digest
          and archive.get('archive_path') == f'oge/catalog/{digest}.json'
          and archive.get('normalized_archive_path') == f'oge/catalog/normalized/{digest}.json',
          'OGE catalog archive metadata differs from fixed selection')
    _need(type(archive.get('record_count')) is int and 0 <= archive['record_count'] <= MAX_CATALOG_ROWS,
          'OGE catalog row inventory exceeds budget or is invalid')
    pages = archive.get('pages')
    _need(isinstance(pages, list) and 1 <= len(pages) <= MAX_CATALOG_ROWS
          and type(archive.get('page_count')) is int and archive['page_count'] == len(pages),
          'OGE catalog page inventory is invalid')
    rebuilt, row_work = [], 0
    for page in pages:
        _need(isinstance(page, dict), 'OGE catalog page metadata is invalid')
        page_sha = page.get('sha256')
        _need(isinstance(page_sha, str) and _SHA.fullmatch(page_sha) is not None
              and page.get('archive_path') == f'oge/catalog/pages/{page_sha}.json'
              and type(page.get('byte_length')) is int and 0 <= page['byte_length'] <= MAX_RESPONSE_BYTES,
              'OGE catalog raw page binding is invalid')
        raw = _read(sources, evidence_commit, page['archive_path'], MAX_RESPONSE_BYTES)
        _need(len(raw) == page['byte_length'] and sha256(raw).hexdigest() == page_sha,
              'OGE catalog raw page bytes differ from archive')
        payload = _json(raw)
        _need(type(payload.get('recordsTotal')) is int and 0 <= payload['recordsTotal'] <= MAX_CATALOG_ROWS
              and isinstance(payload.get('data'), list), 'OGE catalog row inventory exceeds budget or is invalid')
        row_work += len(payload['data'])
        _need(row_work <= MAX_CATALOG_ROWS, 'OGE catalog cumulative source rows exceed budget')
        try:
            parsed = parse_catalog_page(payload, start=page.get('start'), length=page.get('length'))
        except OgeCatalogError as error:
            raise OgeCatalogSourceError('OGE catalog raw page cannot be replayed: ' + str(error)) from error
        _need(parsed.records_total <= MAX_CATALOG_ROWS, 'OGE catalog row inventory exceeds budget')
        rebuilt.append(parsed)
    try:
        catalog = build_catalog(rebuilt)
    except OgeCatalogError as error:
        raise OgeCatalogSourceError('OGE catalog page set is incomplete: ' + str(error)) from error
    counts = {'record_count': catalog['records_total'],
              'transaction_report_count': len(catalog['transactions']),
              'direct_pdf_count': sum(row['access_method'] == 'direct_pdf' for row in catalog['transactions']),
              'request_required_count': sum(row['access_method'] == 'request_required' for row in catalog['transactions'])}
    _need(all(type(archive.get(key)) is int and archive[key] == value for key, value in counts.items()),
          'OGE catalog archive counts differ from original pages')
    normalized = _read(sources, evidence_commit, archive['normalized_archive_path'], MAX_JSON_BYTES)
    expected = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    _need(sha256(normalized).hexdigest() == digest and normalized == expected,
          'OGE normalized catalog differs from complete original-page replay')
    return catalog, archive


def _git_paths(repo, ref, directory):
    process = subprocess.Popen(['git', '-C', str(repo), 'ls-tree', '-r', '--name-only', f'{ref}:{directory}'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
    try:
        chunks, length, names = [], 0, 0
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            length += len(chunk)
            _need(length <= MAX_PATH_BYTES, 'OGE direct source path bytes exceed budget')
            names += chunk.count(b'\n')
            _need(names <= MAX_SOURCE_PATHS, 'OGE direct source path inventory exceeds budget')
            chunks.append(chunk)
        _need(process.wait() == 0, 'OGE fixed direct source directory is unavailable')
        return b''.join(chunks).decode('utf-8').splitlines()
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdout.close()


def load_fixed_oge_direct_source(sources, evidence_commit, *, catalog, document_id,
                                 source_url, source_sha256=None, list_paths=None):
    """Return (record, metadata, PDF bytes) for one unambiguous fixed source.

    The catalog must come from ``load_fixed_oge_catalog``. The caller's SHA is
    a cross-check, never permission to choose among multiple evidence versions.
    Financial/owner proof and exact parser selection belong to the consumer.
    """
    _fixed(evidence_commit)
    _need(isinstance(document_id, str) and _DOCUMENT.fullmatch(document_id) is not None,
          'OGE direct document ID is invalid')
    try:
        records, _ = collapse_direct_catalog_records([row for row in catalog['transactions']
                                                     if row.get('access_method') == 'direct_pdf'])
    except (OgeCatalogError, KeyError, TypeError, AttributeError) as error:
        raise OgeCatalogSourceError('OGE direct catalog identity is invalid') from error
    selected = [row for row in records if row['source_document_id'] == document_id and row['document_url'] == source_url]
    _need(len(selected) == 1, 'OGE selected direct record is missing or conflicting')
    directory = f'oge/reports/{document_id}'
    names = (list_paths or _git_paths)(sources.repo, evidence_commit, directory)
    _need(isinstance(names, list) and 1 <= len(names) <= MAX_SOURCE_PATHS
          and sum(len(value) for value in names if isinstance(value, str)) <= MAX_PATH_BYTES
          and all(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}\.(?:json|pdf)', value) for value in names)
          and len(set(names)) == len(names), 'OGE direct source path inventory is invalid')
    versions = {name.split('.')[0] for name in names}
    _need(len(versions) == 1, 'OGE direct source has multiple evidence SHA versions')
    digest = next(iter(versions))
    _need(set(names) == {digest+'.pdf', digest+'.json'}, 'OGE direct source PDF/metadata pair is incomplete')
    _need(source_sha256 is None or source_sha256 == digest,
          'OGE selected review SHA differs from unique evidence source')
    path = f'{directory}/{digest}.pdf'
    metadata = _object(sources, evidence_commit, path[:-4]+'.json')
    _need(metadata.get('schema_version') == REPORT_ARCHIVE_SCHEMA and metadata.get('source_id') == 'oge'
          and metadata.get('document_id') == document_id and metadata.get('document_url') == source_url
          and metadata.get('sha256') == digest and metadata.get('archive_path') == path
          and type(metadata.get('byte_length')) is int and 0 <= metadata['byte_length'] <= MAX_PDF_BYTES,
          'OGE direct metadata differs from selected document/source')
    record = selected[0]
    _need(all(key in metadata and type(metadata[key]) is type(record[key]) and metadata[key] == record[key]
              for key in ('filer_name', 'agency', 'position_title',
              'catalog_added_date', 'amended_label', 'pending_final_oge_disposition')),
          'OGE direct metadata identity differs from original catalog')
    raw = _read(sources, evidence_commit, path, MAX_PDF_BYTES)
    _need(sha256(raw).hexdigest() == digest and len(raw) == metadata['byte_length'],
          'OGE direct PDF bytes differ from selected metadata')
    try:
        _validate_pdf(raw)
    except OgeCatalogError as error:
        raise OgeCatalogSourceError('OGE direct PDF envelope is invalid') from error
    return record, metadata, raw
