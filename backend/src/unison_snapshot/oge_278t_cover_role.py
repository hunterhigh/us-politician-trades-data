"""One complete native 2019 administrative cover, with typed source runs.

The static equivalence fingerprints describe government wording and layout,
not a filing or person. Variable fields must be explained in full from the
original PDF. This page role grants no person or financial qualification.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
from pathlib import Path
import re

from .oge_278t_native_objects import (
    digest, char_geometry, line_geometry, inspect_strict_native_objects)


COVER_RULE = 'oge-278t-native-administrative-cover-2019/v1'
CONTEXT_SCHEMA = 'oge-278t-cover-identity-context/v1'
# Every fixed government glyph, including whitespace, style and matrix, and
# all seven gray dividing lines. No dynamic name, date or source SHA selects it.
STATIC_LAYOUT_SHA256 = '0f7f61b03d958fdbf025c381b17ae3f1ab9812593e9483b00c1fef0eab9bb16a'
LINE_LAYOUT_SHA256 = 'c597d45dec85e589f41d689c6c419b3a65f446d931d311d2ec6c825291314e6f'
_BANDS = {
    'filer_name': (159.07, 169.07),
    'position_agency': (184.07, 194.07),
    'filer_signature': (244.07, 254.07),
    'agency_review': (319.07, 329.07),
    'other_review': (379.07, 389.07),
    'oge_certification': (439.07, 449.07),
    'revision_date': (484.07, 494.07),
    'footer': (551.07, 561.07),
}
_NAME = r"[A-Za-z][A-Za-z .'-]{0,60}, [A-Za-z][A-Za-z .'-]{0,60}"
_SIGNATURE = re.compile(r'/s/ (?P<name>' + _NAME +
    r')(?P<office>, (?:Certifying Official|Ethics Official))?'
    r' \[electronically signed on (?P<date>\d{2}/\d{2}/\d{4}) by (?P<signer>' +
    _NAME + r') in Integrity\.gov\]')
_SIGNED_ROLES = ('filer_signature', 'agency_review', 'other_review', 'oge_certification')


def _color(value):
    return [float(v) for v in value] if isinstance(value, (list, tuple)) else [float(value)]


def _styled_chars(chars):
    return sorted([[c['text'], *[round(float(c[key]), 5)
        for key in ('x0', 'top', 'x1', 'bottom')], c['fontname'], round(float(c['size']), 5),
        _color(c['non_stroking_color']), _color(c['stroking_color']), c['upright'],
        [round(float(v), 5) for v in c['matrix']]] for c in chars],
        key=lambda row: (row[2], row[1], row[0]))


def _valid_context(context, sha):
    fields = {'schema_version', 'source_sha256', 'filer_name', 'agency', 'position_title'}
    return (isinstance(context, dict) and fields <= context.keys() and
            not context.keys() - fields - {'document_id', 'source_url'} and
            context['schema_version'] == CONTEXT_SCHEMA and context['source_sha256'] == sha and
            all(isinstance(context[key], str) and 0 < len(context[key]) <= 512 and
                context[key] == context[key].strip()
                for key in context if key not in {'schema_version', 'source_sha256'}))


def _context_binding(context):
    # Optional locators are checked by the consumer's independent archive
    # pairing. They must not change the meaning of identical expected labels.
    return {key: context[key] for key in
            ('schema_version', 'source_sha256', 'filer_name', 'agency', 'position_title')}


def observe_native_cover_role(page, page_number: int, *, source_sha256: str | None,
                              cover_identity_context: dict | None = None) -> dict:
    base = {'rule_version': COVER_RULE, 'source_sha256': source_sha256,
            'page_number': page_number, 'status': 'unknown', 'role': None,
            'financial_fact_qualification': False}

    def unknown(reason):
        return {**base, 'reason': reason}

    if not isinstance(source_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        return unknown('page_role_source_binding_unavailable')
    if type(page_number) is not int or page_number != 1:
        return unknown('administrative_cover_not_first_page')
    if not _valid_context(cover_identity_context, source_sha256):
        return unknown('administrative_cover_identity_context_unavailable_or_invalid')
    inventory = inspect_strict_native_objects(page, allowed_sizes=(10, 12, 14, 16))
    if inventory['status'] != 'observed':
        return unknown(inventory['reason'])
    if tuple(page.cropbox) != tuple(page.bbox):
        return unknown('administrative_cover_cropped_page_unsupported')

    # These existing label readers first observe the complete, unmodified
    # native page. The new bands do not supply their own expected readings.
    from .whitehouse_278t import _generic_cover_identity, _filer_signature, _first_last
    from .oge_reports import read_278t_cover_dates
    text = page.extract_text() or ''
    identity = _generic_cover_identity(text)
    own_signature = _filer_signature(text)
    dates = read_278t_cover_dates(text, source_sha256=source_sha256)
    if identity[4] or own_signature[4] or dates['document_reasons'] or dates['amendment_cover']:
        return unknown('administrative_cover_native_role_readings_unresolved')
    if (_first_last(identity[0] or '') is None or
            _first_last(identity[0]) != _first_last(cover_identity_context['filer_name']) or
            identity[3] != cover_identity_context['position_title'] + ', ' + cover_identity_context['agency']):
        return unknown('administrative_cover_source_identity_context_conflict')

    chars = inventory['chars']
    groups = {key: [] for key in _BANDS}
    static = []
    top_roles = {top: role for role, (top, _) in _BANDS.items()}
    for index, char in enumerate(chars):
        role = top_roles.get(round(char['top'], 5))
        if role is None:
            static.append(char)
        else:
            groups[role].append((index, char))
    static_hash = digest(_styled_chars(static))
    lines_hash = digest(line_geometry(inventory['lines']))
    if static_hash != STATIC_LAYOUT_SHA256 or lines_hash != LINE_LAYOUT_SHA256:
        return unknown('administrative_cover_static_text_or_drawings_unaccounted')

    typed = {}
    for role, entries in groups.items():
        ordered = sorted(entries, key=lambda entry: entry[1]['x0'])
        if not ordered:
            return unknown('administrative_cover_dynamic_role_missing')
        members = [char for _, char in ordered]
        raw = ''.join(char['text'] for char in members)
        _, bottom = _BANDS[role]
        if (any(round(char['bottom'], 5) != bottom or round(float(char['size']), 5) != 10
                for char in members) or
                any(abs(left['x1'] - right['x0']) > 1e-5 for left, right in zip(members, members[1:])) or
                ''.join(char['text'] for _, char in entries) != raw or
                (round(members[-1]['x1'], 5) != 806 if role == 'footer'
                 else round(members[0]['x0'], 5) != 36 or members[-1]['x1'] > 806)):
            return unknown('administrative_cover_dynamic_role_geometry_unsupported')
        # Only the government's observed single trailing filer-signature space
        # is excluded from its typed grammar; no arbitrary strip/free-text area.
        if role == 'filer_signature':
            if not raw.endswith(' ') or raw.endswith('  '):
                return unknown('administrative_cover_dynamic_role_whitespace_unsupported')
            value = raw[:-1]
        else:
            value = raw
        try:
            encoded = raw.encode('ascii')
        except UnicodeEncodeError:
            return unknown('administrative_cover_dynamic_role_text_unsupported')
        matches = [(index, record) for index, record in enumerate(inventory['text_run_records'])
                   if record['raw'] == encoded]
        if len(matches) != 1:
            return unknown('administrative_cover_dynamic_role_not_one_source_run')
        run_index, record = matches[0]
        typed[role] = {'text': value, 'raw_text': raw,
            'source_character_indices': [index for index, _ in ordered],
            'character_geometry': char_geometry(members), 'source_text_run_index': run_index,
            'show_operator_indices': record['show_operator_indices']}
    if len({value['source_text_run_index'] for value in typed.values()}) != 8:
        return unknown('administrative_cover_dynamic_source_runs_not_unique')

    values = {role: value['text'] for role, value in typed.items()}
    if (not re.fullmatch(_NAME, values['filer_name']) or values['filer_name'] != identity[0] or
            values['position_agency'] != identity[3] or
            values['footer'] != values['filer_name'] + ' - Page 1'):
        return unknown('administrative_cover_source_identity_roles_conflict')
    signed_dates = []
    for role in _SIGNED_ROLES:
        match = _SIGNATURE.fullmatch(values[role])
        if not match or match['name'] != match['signer']:
            return unknown('administrative_cover_dynamic_role_not_typed_signature')
        expected_office = None if role == 'filer_signature' else (
            ', Ethics Official' if role == 'other_review' else ', Certifying Official')
        if match['office'] != expected_office or (
                role == 'filer_signature' and match['name'] != values['filer_name']):
            return unknown('administrative_cover_signature_role_or_signer_conflict')
        try:
            normalized = datetime.strptime(match['date'], '%m/%d/%Y').date().isoformat()
        except ValueError:
            return unknown('administrative_cover_native_date_invalid')
        signed_dates.append((role, normalized))
    revision = re.fullmatch(r'Data Revised (\d{2}/\d{2}/\d{4})', values['revision_date'])
    if not revision:
        return unknown('administrative_cover_revision_role_unresolved')
    try:
        revised = datetime.strptime(revision[1], '%m/%d/%Y').date().isoformat()
    except ValueError:
        return unknown('administrative_cover_native_date_invalid')
    signed_dates.append(('revision_date', revised))
    if ([(value['role'], value['date']) for value in dates['observations']] != signed_dates or
            own_signature[0] != signed_dates[0][1] or own_signature[3] != values['filer_name']):
        return unknown('administrative_cover_complete_native_date_roles_conflict')

    return {**base, 'status': 'non_table', 'role': 'fixed_native_administrative_cover_2019',
            'reason': None, 'static_layout_sha256': static_hash, 'line_layout_sha256': lines_hash,
            'native_character_count': len(chars), 'static_character_count': len(static),
            'source_character_layout_sha256': digest(_styled_chars(chars)),
            'typed_roles': typed, 'cover_date_evidence': dates,
            'native_identity_reading': {'filer_name': identity[0], 'position_title': identity[1],
                'agency_label': identity[2], 'position_agency_raw': identity[3],
                'filer_signature': own_signature[2], 'signed_by': own_signature[3]},
            'identity_context_sha256': digest(_context_binding(cover_identity_context)),
            'content_operator_sha256': digest(inventory['operators']),
            'native_objects_fully_accounted': True, 'recognizer_started': False,
            'trust_boundary': 'original_native_objects_and_existing_source_identity_context_not_financial_qualification'}


def replay_native_cover_role(path: Path, proof: dict, *, source_sha256: str,
                             page_number: int, cover_identity_context=None) -> bool:
    import pdfplumber
    from .oge_reports import MAX_PDF_BYTES
    source = Path(path)
    if source.stat().st_size > MAX_PDF_BYTES:
        raise ValueError('administrative cover PDF exceeds source byte budget')
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError('non-table page proof belongs to a different source PDF')
    with pdfplumber.open(io.BytesIO(raw)) as document:
        if type(page_number) is not int or not 1 <= page_number <= len(document.pages):
            raise ValueError('non-table proof page is outside source PDF')
        actual = observe_native_cover_role(document.pages[page_number-1], page_number,
            source_sha256=source_sha256, cover_identity_context=cover_identity_context)
    return actual['status'] == 'non_table' and actual == proof
