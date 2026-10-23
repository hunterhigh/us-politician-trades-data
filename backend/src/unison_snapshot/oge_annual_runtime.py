"""Offline preparation of saved native annual inputs from controlled trees.

This consumes the existing annual-review writer's layout. It never upgrades a
parser version, assembles incomplete scan observations, downloads, or runs OCR.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from .builder import timestamp
from .oge_annual import PARSER_VERSION, SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION
from .oge_annual_ledger import _validate_source

RECEIPT_SCHEMA = "oge-annual-controlled-runtime/v1"
MAX_DOCUMENTS = 12
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}\Z")
_VERSIONS = {PARSER_VERSION, SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION}


def _inside(root: Path, reference: str) -> Path:
    if (not isinstance(reference, str) or not reference or '\\' in reference or ':' in reference or
            PurePosixPath(reference).is_absolute() or
            any(part in {'.', '..'} for part in reference.split('/'))):
        raise ValueError("Annual controlled runtime requires relative source paths")
    target = (root / reference).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("Annual controlled runtime source escapes its root or is not a file")
    return target


def _digest(path: Path, *, limit: int) -> tuple[str, int]:
    if path.stat().st_size > limit:
        raise ValueError("Annual controlled runtime source exceeds byte budget")
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        while block := handle.read(1024 * 1024):
            size += len(block)
            if size > limit:
                raise ValueError("Annual controlled runtime source exceeds byte budget")
            digest.update(block)
    return digest.hexdigest(), size


def _json(path: Path) -> dict:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Annual controlled runtime JSON exceeds byte budget")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Annual controlled runtime JSON must be an object")
    return value


def prepare_controlled_annual_input(review_root: Path, evidence_root: Path, *,
                                    code_commit: str, started_at: str,
                                    document_ids: list[str] | None = None) -> dict:
    """Discover existing native caches, verify all dependencies, then return input."""
    review, evidence = review_root.resolve(strict=True), evidence_root.resolve(strict=True)
    if not review.is_dir() or not evidence.is_dir():
        raise ValueError("Annual controlled runtime requires two controlled directories")
    if not isinstance(code_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("Annual controlled runtime code commit is invalid")
    timestamp(started_at)
    if document_ids is not None and (not isinstance(document_ids, list) or not document_ids or
            len(document_ids) > MAX_DOCUMENTS or len(set(document_ids)) != len(document_ids) or
            any(not isinstance(item, str) or not _ID.fullmatch(item) for item in document_ids)):
        raise ValueError("Annual controlled runtime document selection is invalid")
    base = review / 'oge/annual/extractions'
    paths = []
    for path in sorted(base.glob('*/*/*.json')):
        if document_ids is None or path.parent.parent.name in document_ids:
            paths.append(path)
            if len(paths) > MAX_DOCUMENTS:
                raise ValueError("Annual controlled runtime document/version selection exceeds budget")
    if not paths:
        raise ValueError("Annual controlled runtime has no selected saved extractions")
    extractions, metadata, files, documents, seen, total = [], {}, [], [], set(), 0
    for path in paths:
        relative = path.relative_to(review).as_posix()
        checked_path = _inside(review, relative)
        value = _json(checked_path)
        _validate_source(value)
        document, sha = value['source_document_id'], value['source_sha256']
        parser = value['parser_version']
        if parser not in _VERSIONS or relative != (
                f"oge/annual/extractions/{document}/{sha}/{parser.replace('/', '-')}.json"):
            raise ValueError("Annual controlled runtime extraction path or parser version differs")
        if document in seen:
            raise ValueError("Annual controlled runtime document has ambiguous source/parser versions")
        seen.add(document)
        meta_relative = f'oge/annual/reports/{document}/{sha}.json'
        meta_path = _inside(evidence, meta_relative)
        archive = _json(meta_path)
        if (archive.get('schema_version') != 'oge-278e-annual-archive/v1' or
                any(archive.get(left) != value.get(right) for left, right in (
                    ('document_id', 'source_document_id'), ('sha256', 'source_sha256'),
                    ('document_url', 'source_url'), ('filer_name', 'filer_name'),
                    ('archive_path', 'evidence_archive_path'))) or
                any(not isinstance(archive.get(key), str) or not archive[key].strip()
                    for key in ('filer_name', 'agency', 'position_title'))):
            raise ValueError("Annual controlled runtime metadata differs from saved extraction")
        pdf_relative = archive['archive_path']
        pdf = _inside(evidence, pdf_relative)
        for kind, source, reference in (("review", checked_path, relative),
                                        ("evidence", meta_path, meta_relative),
                                        ("evidence", pdf, pdf_relative)):
            digest, size = _digest(source, limit=MAX_TOTAL_BYTES - total)
            total += size
            if source == pdf and digest != sha:
                raise ValueError("Annual controlled runtime PDF hash differs from saved source")
            files.append({'root': kind, 'path': reference, 'sha256': digest, 'bytes': size})
        extractions.append(value)
        metadata[document] = archive
        documents.append({'document_id': document, 'source_sha256': sha, 'parser_version': parser,
            'input_status': 'saved_qualified_parser_input' if parser == QUALIFIED_PARSER_VERSION
                            else 'saved_legacy_parser_input',
            'qualification_status': 'deferred_to_existing_consumer',
            'holding_coverage_parser_supported': parser == QUALIFIED_PARSER_VERSION})
    if document_ids is not None and seen != set(document_ids):
        raise ValueError("Annual controlled runtime selected document lacks a saved extraction")
    return {'extractions': extractions, 'archive_metadata_by_document': metadata,
            'code_commit': code_commit, 'started_at': started_at,
            'controlled_annual_runtime': {'schema_version': RECEIPT_SCHEMA,
                'mode': 'saved_native_extractions', 'documents': documents,
                'files': sorted(files, key=lambda item: (item['root'], item['path'])),
                'total_bytes': total, 'python_minimum': '3.11', 'recognizer_started': False}}


def verify_controlled_annual_input(value: dict, *, review_root: Path, evidence_root: Path) -> dict:
    """Rebuild from exact saved paths and compare before normal candidate writes."""
    receipt = value.get('controlled_annual_runtime')
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != RECEIPT_SCHEMA or
            receipt.get('mode') != 'saved_native_extractions' or
            not isinstance(receipt.get('documents'), list)):
        raise ValueError("Annual controlled runtime receipt version is invalid")
    if sys.version_info < (3, 11):
        raise ValueError("Annual controlled runtime requires Python 3.11")
    docs = [item.get('document_id') if isinstance(item, dict) else None for item in receipt['documents']]
    expected = prepare_controlled_annual_input(review_root, evidence_root,
        code_commit=value.get('code_commit'), started_at=value.get('started_at'), document_ids=docs)
    if expected != value:
        raise ValueError("Annual controlled runtime saved input or dependency hash differs")
    return receipt
