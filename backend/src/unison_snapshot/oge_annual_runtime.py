"""Offline preparation of saved annual inputs from controlled trees.

This consumes the existing annual-review writer's layout. It never upgrades a
parser version, manufactures missing observations, downloads, or runs OCR.
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
MAX_SCAN_FILES = MAX_DOCUMENTS * 4
MAX_SCAN_PAGES = 100  # Same scope as the saved section-observation writer.
SCAN_LAYOUT = {
    'cover_observation': ('cover_observations', 'cover-ocr-v1.json',
                          {'cover-ocr-v1.json': 'oge-278e-scan-cover-observation/v1'}),
    'section_observation': ('section_observations', 'sections-v1.json',
                            {'sections-v1.json': 'oge-278e-scan-sections/v1'}),
    'holding_page_observations': ('holding_page_observations', 'native-holdings-v2.json',
        {'native-holdings-v1.json': 'oge-278e-native-holding-pages/v1',
         'native-holdings-v2.json': 'oge-278e-native-holding-pages/v2'}),
    'native_cover_observation': ('native_cover_observations', 'native-cover-v1.json',
                                {'native-cover-v1.json': 'oge-278e-native-cover-text/v1'}),
}


def controlled_scan_output_path(review_root: Path, role: str, document: str, sha: str) -> Path:
    """The observation writers and runtime discovery share exact versioned names."""
    if (role not in SCAN_LAYOUT or not isinstance(document, str) or not _ID.fullmatch(document) or
            not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
        raise ValueError('Annual controlled observation output identity or role is invalid')
    root = review_root.resolve()
    directory, name, _ = SCAN_LAYOUT[role]
    output = (root / 'oge/annual' / directory / document / sha / name).resolve()
    if not output.is_relative_to(root):
        raise ValueError('Annual controlled observation output escapes review root')
    return output


def guard_scan_observation_output(output: Path, source_pdf: Path) -> None:
    if (output.resolve() == source_pdf.resolve() or
            output.exists() and source_pdf.exists() and output.samefile(source_pdf)):
        raise ValueError('Annual scan observation output must not overwrite its source PDF')


def _saved_scans(review: Path, document_ids: list[str] | None) -> dict:
    result, count, total = {}, 0, 0
    for role, (directory, _, versions) in SCAN_LAYOUT.items():
        selected_paths = []
        for path in (review / 'oge/annual' / directory).glob('*/*/*.json'):
            if document_ids is not None and path.parent.parent.name not in document_ids:
                continue
            count += 1
            if count > MAX_SCAN_FILES:
                raise ValueError('Annual controlled scan observation file count exceeds budget')
            selected_paths.append(path)
        for path in sorted(selected_paths):
            document, sha = path.parent.parent.name, path.parent.name
            relative = path.relative_to(review).as_posix()
            checked = _inside(review, relative)
            total += checked.stat().st_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError('Annual controlled scan observation bytes exceed budget')
            value = _json(checked)
            key = 'document_id' if role == 'cover_observation' else 'source_document_id'
            if (not _ID.fullmatch(document) or not re.fullmatch(r'[0-9a-f]{64}', sha) or
                    path.name not in versions or value.get('schema_version') != versions[path.name] or
                    value.get(key) != document or value.get('source_sha256') != sha):
                raise ValueError('Annual controlled scan observation source/path/version differs')
            group = result.setdefault(document, {'source_sha256': sha, 'roles': {}, 'paths': []})
            if group['source_sha256'] != sha or role in group['roles']:
                raise ValueError('Annual controlled scan source or role has ambiguous versions')
            group['roles'][role] = value
            group['paths'].append(relative)
    return result


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


def _replay_scan_sources(pdf: Path, *, roles: dict, document: str, sha: str,
                         archive: dict) -> None:
    """Bind field-bearing saved text to the original; never run cover OCR."""
    from .oge_annual_scan_sections import observe_scan_sections
    if observe_scan_sections(pdf, source_sha256=sha, document_id=document) != roles['section_observation']:
        raise ValueError('Annual controlled scan sections differ from original source replay')
    native = roles.get('native_cover_observation')
    if native is None:
        # Saved OCR remains an observation boundary. Without the explicit native
        # cover role the existing consumer leaves filing_date unresolved.
        return
    from .oge_annual_scan_cover import observe_native_cover_text, _TYPE, _YEAR
    replay = observe_native_cover_text(pdf, source_sha256=sha, document_id=document)
    if replay != native:
        raise ValueError('Annual controlled native cover differs from original source replay')
    text, cover = replay['cover_text'], roles['cover_observation']
    years = sorted(set(int(year) for year in _YEAR.findall(text)))
    if (not re.search(r'\b278e\b', text, re.I) or not _TYPE.search(text) or len(years) != 1 or
            cover.get('form_278e_text_observed') is not True or
            cover.get('annual_type_text_observed') is not True or
            cover.get('annual_year_text_candidates') != years):
        raise ValueError('Annual controlled native cover form or year is missing or contradicts saved OCR')
    # Match the existing native annual parser's supported identity block. A
    # directory identity is not a substitute for absent original cover text.
    lines = text.splitlines()
    names = [lines[index + 1] for index, line in enumerate(lines[:-1])
             if 'Last Name' in line and 'First Name' in line]
    expected = re.findall(r'[a-z]+', archive['filer_name'].casefold())
    actual = re.findall(r'[a-z]+', names[0].casefold()) if len(names) == 1 else []
    if not expected or not all(token in actual for token in expected):
        raise ValueError('Annual controlled native cover filer is missing or contradicts archive identity')


def prepare_controlled_annual_input(review_root: Path, evidence_root: Path, *,
                                    code_commit: str, started_at: str,
                                    document_ids: list[str] | None = None,
                                    include_saved_scans: bool = False) -> dict:
    """Discover saved source caches, verify all dependencies, then return input."""
    review, evidence = review_root.resolve(strict=True), evidence_root.resolve(strict=True)
    if not review.is_dir() or not evidence.is_dir():
        raise ValueError("Annual controlled runtime requires two controlled directories")
    if not isinstance(code_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("Annual controlled runtime code commit is invalid")
    timestamp(started_at)
    if type(include_saved_scans) is not bool:
        raise ValueError('Annual controlled scan preparation flag must be boolean')
    if document_ids is not None and (not isinstance(document_ids, list) or not document_ids or
            len(document_ids) > MAX_DOCUMENTS or len(set(document_ids)) != len(document_ids) or
            any(not isinstance(item, str) or not _ID.fullmatch(item) for item in document_ids)):
        raise ValueError("Annual controlled runtime document selection is invalid")
    base = review / 'oge/annual/extractions'
    paths = []
    for path in base.glob('*/*/*.json'):
        if document_ids is None or path.parent.parent.name in document_ids:
            paths.append(path)
            if len(paths) > MAX_DOCUMENTS:
                raise ValueError("Annual controlled runtime document/version selection exceeds budget")
    paths.sort()
    scans = _saved_scans(review, document_ids) if include_saved_scans else {}
    if not paths and not scans:
        raise ValueError("Annual controlled runtime has no selected saved extractions")
    extractions, metadata, files, documents, seen, total = [], {}, [], [], set(), 0
    def bind_file(kind, reference):
        nonlocal total
        root = review if kind == 'review' else evidence
        source = _inside(root, reference)
        digest, size = _digest(source, limit=MAX_TOTAL_BYTES-total)
        total += size
        files.append({'root': kind, 'path': reference, 'sha256': digest, 'bytes': size})
        return digest
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
    saved = {}
    for document, group in sorted(scans.items()):
        if document in seen:
            raise ValueError('Annual controlled runtime native and scan inputs duplicate a document')
        if len(seen) >= MAX_DOCUMENTS:
            raise ValueError('Annual controlled runtime combined document count exceeds budget')
        roles, sha = group['roles'], group['source_sha256']
        if not {'cover_observation', 'section_observation'} <= roles.keys():
            raise ValueError('Annual controlled scan input lacks its required cover or section inventory')
        meta_relative = f'oge/annual/reports/{document}/{sha}.json'
        archive = _json(_inside(evidence, meta_relative))
        pdf_relative = f'oge/annual/reports/{document}/{sha}.pdf'
        if (archive.get('schema_version') != 'oge-278e-annual-archive/v1' or
                archive.get('document_id') != document or archive.get('sha256') != sha or
                archive.get('archive_path') != pdf_relative or
                any(not isinstance(archive.get(key), str) or not archive[key].strip()
                    for key in ('filer_name','agency','position_title','document_url'))):
            raise ValueError('Annual controlled scan archive metadata binding differs')
        for relative in group['paths']:
            bind_file('review', relative)
        bind_file('evidence', meta_relative)
        if bind_file('evidence', pdf_relative) != sha:
            raise ValueError('Annual controlled runtime PDF hash differs from saved source')
        declared_count = roles['section_observation'].get('page_count')
        if (type(declared_count) is not int or not 1 <= declared_count <= MAX_SCAN_PAGES or
                type(roles['cover_observation'].get('page_count')) is not int):
            raise ValueError('Annual controlled scan saved page count exceeds its writer scope')
        import pdfplumber
        with pdfplumber.open(_inside(evidence,pdf_relative)) as source_pdf:
            page_count = len(source_pdf.pages)
        if (page_count != roles['cover_observation'].get('page_count') or
                page_count != roles['section_observation'].get('page_count')):
            raise ValueError('Annual controlled scan saved page count differs from source PDF')
        _replay_scan_sources(_inside(evidence, pdf_relative), roles=roles, document=document,
                             sha=sha, archive=archive)
        # Use the existing source replay as a contract preflight. Its result is
        # discarded: preparation grants no row or report qualification.
        from .oge_annual import materialize_saved_annual_scan
        value = materialize_saved_annual_scan(roles['cover_observation'], roles['section_observation'],
            archive_metadata=archive, holding_page_observations=roles.get('holding_page_observations'),
            native_cover_observation=roles.get('native_cover_observation'),
            archived_pdf_path=_inside(evidence,pdf_relative))
        _validate_source(value)
        seen.add(document)
        metadata[document] = archive
        saved[document] = {**roles, 'archived_pdf_path': pdf_relative}
        documents.append({'document_id':document,'source_sha256':sha,
            'parser_version':value['parser_version'],'input_status':'saved_scan_observation_input',
            'qualification_status':'deferred_to_existing_consumer',
            'holding_coverage_parser_supported':'holding_page_observations' in roles})
    if document_ids is not None and seen != set(document_ids):
        raise ValueError("Annual controlled runtime selected document lacks a saved extraction")
    return {'extractions': extractions, 'archive_metadata_by_document': metadata,
            **({'saved_scan_observations_by_document':saved} if include_saved_scans else {}),
            'code_commit': code_commit, 'started_at': started_at,
            'controlled_annual_runtime': {'schema_version': RECEIPT_SCHEMA,
                'mode': 'saved_native_and_scan_inputs' if include_saved_scans else 'saved_native_extractions',
                'documents': documents,
                'files': sorted(files, key=lambda item: (item['root'], item['path'])),
                'total_bytes': total, 'python_minimum': '3.11', 'recognizer_started': False}}


def verify_controlled_annual_input(value: dict, *, review_root: Path, evidence_root: Path) -> dict:
    """Rebuild from exact saved paths and compare before normal candidate writes."""
    receipt = value.get('controlled_annual_runtime')
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != RECEIPT_SCHEMA or
            receipt.get('mode') not in {'saved_native_extractions','saved_native_and_scan_inputs'} or
            not isinstance(receipt.get('documents'), list)):
        raise ValueError("Annual controlled runtime receipt version is invalid")
    if sys.version_info < (3, 11):
        raise ValueError("Annual controlled runtime requires Python 3.11")
    docs = [item.get('document_id') if isinstance(item, dict) else None for item in receipt['documents']]
    expected = prepare_controlled_annual_input(review_root, evidence_root,
        code_commit=value.get('code_commit'), started_at=value.get('started_at'), document_ids=docs,
        include_saved_scans=receipt['mode']=='saved_native_and_scan_inputs')
    if expected != value:
        raise ValueError("Annual controlled runtime saved input or dependency hash differs")
    return receipt
