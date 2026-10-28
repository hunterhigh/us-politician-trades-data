"""Apply reviewed OCR table facts with source and old-value assertions."""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import math

from .codec import encode, digest
from .corrections import apply_correction, load_correction
from .oge import OgeCatalogError

PROFILE = 'reviewed-ocr-table/v1'
ROOT = Path(__file__).resolve().parents[2] / 'data/corrections/whitehouse'
CELL_FIELDS = ('printed_row_number', 'asset_name_raw', 'transaction_type_raw',
    'transaction_date_raw', 'late_notification_raw', 'amount_raw')


@lru_cache(maxsize=32)
def _read(path, modified, size):
    value = load_correction(path)
    if value.get('profile') != PROFILE or value['source'] != 'oge':
        raise OgeCatalogError('Unsupported reviewed OCR correction profile')
    if value['source_profile']['document_id'] != value['document_id'] or any(
            value['source_profile'][key] != value[key] for key in ('source_url', 'source_sha256')):
        raise OgeCatalogError('Reviewed OCR source profile identity differs')
    if (any(value['header'].get(key) != value[key] for key in
            ('document_id', 'source_url', 'source_sha256', 'parser_version')) or
            value['header'].get('source_id') != 'oge' or
            value['header'].get('page_count') != value['source_profile']['page_count']):
        raise OgeCatalogError('Reviewed OCR header differs from its source profile')
    return value


def profiles():
    result = []
    for path in sorted(ROOT.glob('*.json')):
        stat = path.stat()
        value = _read(str(path), stat.st_mtime_ns, stat.st_size)
        if value['document_id'] != 'wh-url:' + path.stem:
            raise OgeCatalogError('Reviewed OCR profile path differs from its document')
        result.append(value)
    return result


def profile_for(document_id, source_url, source_sha256, *, mode=None):
    for value in profiles():
        if mode is not None and value['output_mode'] != mode:
            continue
        if value['source_url'] == source_url and value['source_sha256'] == source_sha256:
            if document_id is not None and document_id != value['document_id']:
                raise OgeCatalogError('Reviewed OCR document binding is invalid')
            return value
    return None


def source_profiles(*, mode=None):
    return [deepcopy(c['source_profile']) for c in profiles()
        if mode is None or c['output_mode'] == mode]


def person_for_id(person_id):
    matches = [c['person'] for c in profiles() if c['person']['id'] == person_id]
    if not matches:
        return None
    if any(value != matches[0] for value in matches):
        raise OgeCatalogError('Reviewed source person metadata conflicts')
    return deepcopy(matches[0])


def apply_row_corrections(records, correction):
    guards = correction['assertions']
    normalized = []
    previous = None
    if not isinstance(records, list):
        raise OgeCatalogError('Reviewed OCR original rows must be an array')
    for record in records:
        if not isinstance(record, dict):
            raise OgeCatalogError('Reviewed OCR original row must be an object')
        page, top, cells = (record.get(k) for k in ('page_number', 'geometry_top', 'cells'))
        confidence = record.get('ocr_confidence')
        if (type(page) is not int or not 1 <= page <= correction['source_profile']['page_count'] or
                type(top) not in (int, float) or not math.isfinite(top) or
                type(confidence) not in (int, float) or not math.isfinite(confidence) or
                not isinstance(cells, list) or len(cells) != 6 or
                any(not isinstance(cell, str) for cell in cells) or
                record.get('raw_cells', cells) != cells or
                previous is not None and (page, float(top)) <= previous):
            raise OgeCatalogError('Reviewed OCR original geometry or cells changed')
        previous = page, float(top)
        normalized.append({k: record[k] for k in ('page_number', 'cells', 'geometry_top', 'ocr_confidence')})
    if (digest(encode(normalized)) != guards['input_rows_sha256'] or
            dict(Counter(str(r['page_number']) for r in normalized)) != guards['page_row_counts'] or
            [str(i) for i in range(1, len(records)+1)] != guards['row_inventory']):
        raise OgeCatalogError('Reviewed OCR original row precondition changed')
    model = {key: correction[key] for key in ('source', 'document_id', 'source_sha256')}
    model.update(report={}, rows={str(i): {
        **dict(zip(CELL_FIELDS, row['cells'], strict=True)),
        **({'source_bound_corrections': deepcopy(row['source_bound_corrections'])}
            if 'source_bound_corrections' in row else {})} for i, row in enumerate(records, 1)})
    for action in correction['actions']:
        if action['op'] != 'set_field' or action.get('field') not in {*CELL_FIELDS, 'source_bound_corrections'}:
            raise OgeCatalogError('Reviewed OCR correction operation is unsupported')
    try:
        corrected = apply_correction(model, correction)
    except (KeyError, ValueError, TypeError) as error:
        raise OgeCatalogError('Reviewed OCR correction precondition failed: ' + str(error)) from error
    result = []
    for key, original in zip(guards['row_inventory'], records, strict=True):
        fields = corrected['rows'][key]
        row = deepcopy(original)
        row['cells'] = [fields[field] for field in CELL_FIELDS]
        if 'source_bound_corrections' in fields:
            row['source_bound_corrections'] = fields['source_bound_corrections']
        row['raw_cells'] = list(original['cells'])
        row['printed_row_number_raw'] = original['cells'][0]
        row['printed_row_number_resolution'] = 'source_bound_geometry_order'
        result.append(row)
    if digest(encode(result)) != guards['output_rows_sha256']:
        raise OgeCatalogError('Reviewed OCR correction differs from its approved output')
    return result


def apply_cover_corrections(report, correction):
    model = {key: correction[key] for key in ('source', 'document_id', 'source_sha256')}
    model.update(report=deepcopy(report), rows={})
    actions = correction.get('cover_actions', [])
    allowed = {'filed_at', 'signature_method', 'filer_signature_name',
        'filing_date_evidence', 'filer_identity_evidence'}
    if any(a['op'] != 'set_report' or a.get('field') not in allowed for a in actions):
        raise OgeCatalogError('Reviewed OCR cover operation is unsupported')
    try:
        return apply_correction(model, {**correction, 'actions': actions})['report']
    except (KeyError, ValueError, TypeError) as error:
        raise OgeCatalogError('Reviewed OCR cover precondition failed: ' + str(error)) from error


def artifact_fingerprint(report):
    value = deepcopy(report)
    for key in ('extraction_method', 'ocr_engine', 'ocr_checkpoint'):
        value.pop(key, None)
    return digest(encode(value))


def validate_reusable_report(report, correction):
    """Recheck current correction data while preserving the original cache bytes."""
    if (report.get('document_id') != correction['document_id'] or
            report.get('source_sha256') != correction['source_sha256'] or
            report.get('source_url') != correction['source_url'] or
            report.get('parser_version') != correction['parser_version'] or
            any(report.get(k) != v for k, v in correction['header'].items()) or
            artifact_fingerprint(report) not in correction['assertions']['approved_artifact_sha256']):
        raise OgeCatalogError('Reviewed OCR cache differs from its approved artifact')
    rows = sorted([*report.get('transactions', []), *report.get('quarantined', [])],
        key=lambda row: row.get('geometry_row_index', -1))
    if [r.get('geometry_row_index') for r in rows] != list(range(1, len(rows)+1)):
        raise OgeCatalogError('Reviewed OCR cache row inventory is ambiguous')
    raw = [{'page_number': r.get('page_number'), 'cells': r.get('ocr_raw_cells'),
        'geometry_top': r.get('geometry_top'), 'ocr_confidence': r.get('ocr_confidence')} for r in rows]
    corrected = apply_row_corrections(raw, correction)
    for row, expected in zip(rows, corrected, strict=True):
        if (row.get('source_bound_normalized_cells', row.get('ocr_raw_cells')) != expected['cells'] or
                any(row.get(k) != expected.get(k) for k in ('printed_row_number_raw',
                    'printed_row_number_resolution', 'source_bound_corrections'))):
            raise OgeCatalogError('Reviewed OCR cache does not implement the current corrections')
    return report
