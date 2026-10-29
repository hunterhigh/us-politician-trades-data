"""Offline preparation of saved annual inputs from controlled trees.

This consumes the existing annual-review writer's layout. It never upgrades a
parser version, manufactures missing observations, downloads, or runs date
models. Cover OCR is available only through an explicit source-replay option.
"""
from __future__ import annotations

import hashlib
import json
from math import ceil, isfinite
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
MAX_COVER_RENDER_PIXELS = 16_000_000
MAX_COVER_RENDER_EDGE = 10_000
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


def _blue_model_directory(evidence: Path, reference: str) -> str:
    """Keep the two pinned leaf files under one canonical portable directory."""
    if (not isinstance(reference, str) or not reference or '\\' in reference or ':' in reference or
            PurePosixPath(reference).is_absolute() or
            PurePosixPath(reference).as_posix() != reference or
            any(not part or part in {'.', '..'} or part.rstrip(' .') != part or
                any(ord(char) < 32 for char in part) for part in reference.split('/'))):
        raise ValueError('Annual blue date model directory must be a canonical relative POSIX path')
    directory = (evidence / reference).resolve(strict=True)
    if (not directory.is_relative_to(evidence) or not directory.is_dir() or
            directory.relative_to(evidence).as_posix() != reference):
        raise ValueError('Annual blue date model directory escapes its root or is an alias')
    for name in ('model.onnx', 'config.json'):
        source = _inside(evidence, f'{reference}/{name}')
        if source.relative_to(evidence).as_posix() != f'{reference}/{name}':
            raise ValueError('Annual blue date model file is an alias')
    return reference


def _json(path: Path) -> dict:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Annual controlled runtime JSON exceeds byte budget")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Annual controlled runtime JSON must be an object")
    return value


def _cover_source_projection(value: dict) -> dict:
    """Only source fields consumed as form/period evidence can authorize them."""
    from .oge_annual_scan_cover import SCHEMA
    years = value.get('annual_year_text_candidates')
    if (value.get('schema_version') != SCHEMA or
            not isinstance(value.get('document_id'), str) or not _ID.fullmatch(value['document_id']) or
            not isinstance(value.get('source_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', value['source_sha256']) or
            type(value.get('page_count')) is not int or not 1 <= value['page_count'] <= MAX_SCAN_PAGES or
            type(value.get('cover_page_number')) is not int or value['cover_page_number'] != 1 or
            not isinstance(value.get('native_text_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', value['native_text_sha256']) or
            value.get('form_278e_text_observed') is not True or
            value.get('annual_type_text_observed') is not True or
            not isinstance(years, list) or len(years) != 1 or type(years[0]) is not int):
        raise ValueError('Annual controlled cover replay lacks a unique typed source form and period')
    keys = ('schema_version', 'document_id', 'source_sha256', 'page_count',
            'cover_page_number', 'native_text_sha256', 'form_278e_text_observed',
            'annual_type_text_observed')
    return {**{key: value[key] for key in keys}, 'annual_year_text_candidates': list(years)}


def _replay_scan_sources(pdf: Path, *, roles: dict, document: str, sha: str,
                         archive: dict, replay_cover_ocr: bool = False) -> dict | None:
    """Bind saved text; explicit cover replay does not verify signature dates."""
    from .oge_annual_scan_sections import observe_scan_sections
    if observe_scan_sections(pdf, source_sha256=sha, document_id=document) != roles['section_observation']:
        raise ValueError('Annual controlled scan sections differ from original source replay')
    native = roles.get('native_cover_observation')
    if native is None and not replay_cover_ocr:
        # Saved OCR remains an observation boundary. Without the explicit native
        # cover role the existing consumer leaves filing_date unresolved.
        return
    from .oge_annual_scan_cover import observe_native_cover_text, _TYPE, _YEAR
    replay = observe_native_cover_text(pdf, source_sha256=sha, document_id=document)
    if native is not None and replay != native:
        raise ValueError('Annual controlled native cover differs from original source replay')
    text, cover = replay['cover_text'], roles['cover_observation']
    years = sorted(set(int(year) for year in _YEAR.findall(text)))
    if not re.search(r'\b278e\b', text, re.I):
        raise ValueError('Annual controlled native cover form or year is missing or contradicts saved OCR')
    proof = None
    if replay_cover_ocr:
        from .oge_annual_scan_cover import observe_scan_cover
        reading = observe_scan_cover(pdf, source_sha256=sha, document_id=document,
            catalog_filer=archive['filer_name'], catalog_year=archive.get('report_year'))
        projection = _cover_source_projection(reading)
        if _cover_source_projection(cover) != projection:
            raise ValueError('Annual controlled cover fields differ from original source OCR replay')
        first_pages = [page for page in roles['section_observation'].get('pages', [])
                       if page.get('page_number') == 1]
        if (len(first_pages) != 1 or replay.get('cover_text_sha256') != projection['native_text_sha256'] or
                first_pages[0].get('page_text_sha256') != projection['native_text_sha256']):
            raise ValueError('Annual controlled cover replay differs from the original first-page inventory')
        declared = re.findall(r'Report\s+Type\s*:\s*([A-Za-z]+)', text, re.I)
        if (any(value.casefold() != 'annual' for value in declared) or
                years and years != projection['annual_year_text_candidates']):
            raise ValueError('Annual controlled native cover explicitly contradicts source OCR form or period')
        raw = json.dumps(reading, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
        proof = {'method': 'source_ocr_cover_fields/v1', 'document_id': document,
                 'source_sha256': sha, 'projection': projection,
                 'observation_sha256': hashlib.sha256(raw).hexdigest(),
                 'ocr_engine': reading.get('ocr_engine')}
    elif (not _TYPE.search(text) or len(years) != 1 or
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
    return proof


def prepare_controlled_annual_input(review_root: Path, evidence_root: Path, *,
                                    code_commit: str, started_at: str,
                                    document_ids: list[str] | None = None,
                                    include_saved_scans: bool = False,
                                    parser_version: str | None = None,
                                    blue_date_document_ids: list[str] | None = None,
                                    blue_date_model_directory: str | None = None,
                                    verify_transaction_blank_slots: bool = False,
                                    replay_cover_ocr: bool = False) -> dict:
    """Discover saved source caches, verify all dependencies, then return input."""
    review, evidence = review_root.resolve(strict=True), evidence_root.resolve(strict=True)
    if not review.is_dir() or not evidence.is_dir():
        raise ValueError("Annual controlled runtime requires two controlled directories")
    if not isinstance(code_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("Annual controlled runtime code commit is invalid")
    timestamp(started_at)
    if type(include_saved_scans) is not bool:
        raise ValueError('Annual controlled scan preparation flag must be boolean')
    if type(replay_cover_ocr) is not bool or (replay_cover_ocr and not include_saved_scans):
        raise ValueError('Annual cover OCR replay requires saved scans and a boolean flag')
    if type(verify_transaction_blank_slots) is not bool or (
            verify_transaction_blank_slots and not include_saved_scans):
        raise ValueError('Annual blank slot verification requires saved scans and a boolean flag')
    if (blue_date_document_ids is None) != (blue_date_model_directory is None):
        raise ValueError('Annual blue date document selection and model directory must be paired')
    if blue_date_document_ids is not None and (
            not include_saved_scans or not isinstance(blue_date_document_ids, list) or
            not blue_date_document_ids or len(blue_date_document_ids) > MAX_DOCUMENTS or
            any(not isinstance(item, str) or not _ID.fullmatch(item) for item in blue_date_document_ids) or
            len(set(blue_date_document_ids)) != len(blue_date_document_ids)):
        raise ValueError('Annual blue date document selection requires unique saved scan documents')
    if parser_version is not None and (not isinstance(parser_version, str) or
                                       parser_version not in _VERSIONS):
        raise ValueError('Annual controlled runtime parser selection is unsupported')
    if document_ids is not None and (not isinstance(document_ids, list) or not document_ids or
            len(document_ids) > MAX_DOCUMENTS or len(set(document_ids)) != len(document_ids) or
            any(not isinstance(item, str) or not _ID.fullmatch(item) for item in document_ids)):
        raise ValueError("Annual controlled runtime document selection is invalid")
    base = review / 'oge/annual/extractions'
    paths = []
    available_documents = set()
    for path in base.glob('*/*/*.json'):
        if document_ids is None or path.parent.parent.name in document_ids:
            available_documents.add(path.parent.parent.name)
            if len(available_documents) > MAX_DOCUMENTS:
                raise ValueError('Annual controlled runtime document selection exceeds budget')
            # Select an existing cache, never relabel or regenerate it. The
            # selected file's payload/path/source are still checked below.
            if parser_version is not None and path.name != parser_version.replace('/', '-') + '.json':
                continue
            paths.append(path)
            if len(paths) > MAX_DOCUMENTS:
                raise ValueError("Annual controlled runtime document/version selection exceeds budget")
    paths.sort()
    if parser_version is not None and available_documents != {path.parent.parent.name for path in paths}:
        raise ValueError('Annual controlled runtime selected parser is missing for a selected document')
    scans = _saved_scans(review, document_ids) if include_saved_scans else {}
    if replay_cover_ocr and not scans:
        raise ValueError('Annual cover OCR replay requires selected saved scan observations')
    if blue_date_document_ids is not None and not set(blue_date_document_ids) <= scans.keys():
        raise ValueError('Annual blue date documents must be selected saved scan inputs')
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
        if parser_version is not None and parser != parser_version:
            raise ValueError('Annual controlled runtime selected parser differs from saved extraction')
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
    blue_selection = {}
    if blue_date_document_ids is not None:
        directory = _blue_model_directory(evidence, blue_date_model_directory)
        # Importing these constants does not load the optional inference runtime.
        from .oge_handwritten_date import MODEL_SHA256, CONFIG_SHA256
        for name, expected in (('model.onnx', MODEL_SHA256), ('config.json', CONFIG_SHA256)):
            if bind_file('evidence', f'{directory}/{name}') != expected:
                raise ValueError('Annual blue date model or configuration hash differs from pinned source')
        blue_selection = {'blue_date_document_ids': sorted(blue_date_document_ids),
                          'blue_date_model_directory': directory}
    saved, handwritten_dates, cover_replays = {}, {}, []
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
            if replay_cover_ocr:
                # The existing cover reader renders page 1 at 200 DPI. Bound
                # that allocation before invoking either renderer or engine.
                if not 1 <= page_count <= MAX_SCAN_PAGES:
                    raise ValueError('Annual cover source page inventory exceeds its reader scope')
                first = source_pdf.pages[0]
                boxes = [first.mediabox, first.cropbox or first.mediabox]
                widths = [abs(float(box[2])-float(box[0])) for box in boxes]
                heights = [abs(float(box[3])-float(box[1])) for box in boxes]
                if (first.page_obj.attrs.get('UserUnit', 1) != 1 or
                        any(not isfinite(value) or value <= 0 for value in widths+heights)):
                    raise ValueError('Annual cover source render frame is unsupported')
                width, height = ceil(max(widths)*200/72), ceil(max(heights)*200/72)
                if (max(width, height) > MAX_COVER_RENDER_EDGE or
                        width*height > MAX_COVER_RENDER_PIXELS):
                    raise ValueError('Annual cover source render exceeds pixel budget')
        if (page_count != roles['cover_observation'].get('page_count') or
                page_count != roles['section_observation'].get('page_count')):
            raise ValueError('Annual controlled scan saved page count differs from source PDF')
        cover_proof = _replay_scan_sources(_inside(evidence, pdf_relative), roles=roles,
            document=document, sha=sha, archive=archive, replay_cover_ocr=replay_cover_ocr)
        if cover_proof is not None:
            cover_replays.append(cover_proof)
        # Use the existing source replay as a contract preflight. Its result is
        # discarded: preparation grants no row or report qualification.
        from .oge_annual import materialize_saved_annual_scan
        value = materialize_saved_annual_scan(roles['cover_observation'], roles['section_observation'],
            archive_metadata=archive, holding_page_observations=roles.get('holding_page_observations'),
            native_cover_observation=roles.get('native_cover_observation'),
            archived_pdf_path=_inside(evidence,pdf_relative),
            **({'verify_transaction_blank_slots': True} if verify_transaction_blank_slots else {}))
        _validate_source(value)
        seen.add(document)
        metadata[document] = archive
        saved[document] = {**roles, 'archived_pdf_path': pdf_relative,
            **({'verify_transaction_blank_slots': True} if verify_transaction_blank_slots else {})}
        if blue_date_document_ids is not None and document in blue_date_document_ids:
            handwritten_dates[document] = {'archived_pdf_path': pdf_relative,
                                          'model_directory': blue_date_model_directory}
        documents.append({'document_id':document,'source_sha256':sha,
            'parser_version':value['parser_version'],'input_status':'saved_scan_observation_input',
            'qualification_status':'deferred_to_existing_consumer',
            'holding_coverage_parser_supported':'holding_page_observations' in roles})
    if document_ids is not None and seen != set(document_ids):
        raise ValueError("Annual controlled runtime selected document lacks a saved extraction")
    return {'extractions': extractions, 'archive_metadata_by_document': metadata,
            **({'saved_scan_observations_by_document':saved} if include_saved_scans else {}),
            **({'handwritten_date_inputs_by_document': handwritten_dates} if handwritten_dates else {}),
            'code_commit': code_commit, 'started_at': started_at,
            'controlled_annual_runtime': {'schema_version': RECEIPT_SCHEMA,
                'mode': 'saved_native_and_scan_inputs' if include_saved_scans else 'saved_native_extractions',
                **({'selected_parser_version': parser_version} if parser_version is not None else {}),
                **blue_selection,
                **({'verify_transaction_blank_slots': True} if verify_transaction_blank_slots else {}),
                **({'replay_cover_ocr': True, 'cover_source_replays': cover_replays} if replay_cover_ocr else {}),
                'documents': documents,
                'files': sorted(files, key=lambda item: (item['root'], item['path'])),
                'total_bytes': total, 'python_minimum': '3.11', 'recognizer_started': replay_cover_ocr}}


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
    if 'selected_parser_version' in receipt and receipt['selected_parser_version'] is None:
        raise ValueError('Annual controlled runtime parser selection must not be null')
    expected = prepare_controlled_annual_input(review_root, evidence_root,
        code_commit=value.get('code_commit'), started_at=value.get('started_at'), document_ids=docs,
        include_saved_scans=receipt['mode']=='saved_native_and_scan_inputs',
        parser_version=receipt.get('selected_parser_version'),
        blue_date_document_ids=receipt.get('blue_date_document_ids'),
        blue_date_model_directory=receipt.get('blue_date_model_directory'),
        verify_transaction_blank_slots=receipt.get('verify_transaction_blank_slots', False),
        replay_cover_ocr=receipt.get('replay_cover_ocr', False))
    if expected != value:
        raise ValueError("Annual controlled runtime saved input or dependency hash differs")
    return receipt
