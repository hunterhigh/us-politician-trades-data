"""Complete fixed native Summary region; upper financial content stays partial.

Every original glyph/run in this region and every potentially intersecting
source stroke is checked. A title alone cannot select or excuse a region.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .oge_278t_native_objects import (
    char_geometry, digest, inspect_strict_native_objects, line_geometry,
)
from .oge_278t_cover_role import _styled_chars
from .oge_278t_page_roles import _bounded_footer

REGION_RULE = 'oge-278t-native-summary-region-2019/v1'
REGION_BBOX = [0., 398.898, 842., 595.]
# Complete official title and four paragraph lines on the native 2019 form.
# Neither report identity nor a heading substring chooses these fingerprints.
BODY_LAYOUT_SHA256 = '4d261a2b1faa8f558c39d5e64faf7d74d9b9a9222fd8529175dd26795fa63539'
BODY_STYLE_SHA256 = '1b8ca4e14bad7bc9a01d8f9bc9f6b05b35bb4ac48ad30979016f034e59205605'


def observe_native_summary_region(page, page_number, *, source_sha256):
    base = {'rule_version': REGION_RULE, 'source_sha256': source_sha256,
            'page_number': page_number, 'status': 'unknown', 'role': None,
            'financial_fact_qualification': False, 'page_coverage': 'partial',
            'coordinate_frame': 'pdf_points'}

    def unknown(reason):
        return {**base, 'reason': reason}

    if (not isinstance(source_sha256, str) or re.fullmatch(r'[0-9a-f]{64}', source_sha256) is None
            or type(page_number) is not int or page_number < 1):
        return unknown('summary_region_source_binding_unavailable')
    inventory = inspect_strict_native_objects(page, allowed_sizes=(10, 14), allow_square_linecaps=True)
    if inventory['status'] != 'observed':
        return unknown(inventory['reason'])
    chars = inventory['chars']
    body = [i for i, c in enumerate(chars) if round(c['top'], 5) >= REGION_BBOX[1]
            and c['bottom'] <= 495]
    footer = [i for i, c in enumerate(chars) if c['top'] >= page.height-60
              and c['bottom'] <= page.height-25]
    members = set(body) | set(footer)
    intersecting = {i for i, c in enumerate(chars) if c['bottom'] > REGION_BBOX[1]}
    if (not body or not footer or set(body) & set(footer) or members != intersecting or
            any(chars[i]['top'] < REGION_BBOX[1]-1e-5 for i in members)):
        return unknown('summary_region_native_characters_unaccounted')
    body_chars, footer_chars = [chars[i] for i in body], [chars[i] for i in footer]
    body_hash, style_hash = digest(char_geometry(body_chars)), digest(_styled_chars(body_chars))
    if body_hash != BODY_LAYOUT_SHA256 or style_hash != BODY_STYLE_SHA256:
        return unknown('summary_region_complete_body_differs')
    footer_text = _bounded_footer(footer_chars, page_number, inventory['text_runs'])
    if footer_text is None:
        return unknown('summary_region_footer_not_bounded')
    # Square caps and ordinary butt caps are bounded by one full source width
    # on each axis. The opt-in inspector has already rejected nonunit path
    # transforms, curves, joins, unknown caps and nonpositive/hairline widths.
    strokes = inventory['source_stroke_records']
    if any(stroke['outer_bbox_pdf_points'][3] > REGION_BBOX[1] for stroke in strokes):
        return unknown('source_stroke_intersects_summary_region')
    try:
        raw = b''.join(record['raw'] for record in inventory['text_run_records'])
        if raw.decode('ascii') != ''.join(c['text'] for c in chars):
            return unknown('summary_region_source_run_projection_differs')
    except UnicodeDecodeError:
        return unknown('summary_region_source_encoding_unsupported')
    refs, cursor = [], 0
    for index, record in enumerate(inventory['text_run_records']):
        indices = set(range(cursor, cursor+len(record['raw'])))
        cursor += len(record['raw'])
        if not indices & members:
            continue
        role = 'body' if indices <= set(body) else 'footer' if indices <= set(footer) else None
        if role is None:
            return unknown('summary_region_source_run_crosses_role')
        refs.append({'role': role, 'run_index': index,
                     'show_operator_indices': record['show_operator_indices'],
                     'character_indices': sorted(indices),
                     'raw_ascii_sha256': hashlib.sha256(record['raw']).hexdigest()})
    if set(i for ref in refs for i in ref['character_indices']) != members:
        return unknown('summary_region_source_run_membership_incomplete')
    object_digest = digest({'chars': _styled_chars(chars), 'lines': line_geometry(inventory['lines']),
        'operators': inventory['operators'], 'line_cap_operations': inventory['line_cap_operations'],
        'source_stroke_records': strokes,
        'source_runs': [{'raw_hex': record['raw'].hex(),
                         'show_operator_indices': record['show_operator_indices']}
                        for record in inventory['text_run_records']]})
    return {**base, 'status': 'observed', 'role': 'fixed_native_summary_of_contents_2019',
            'reason': None, 'region_bbox_pdf_points': list(REGION_BBOX),
            'body_layout_sha256': body_hash, 'body_style_sha256': style_hash,
            'footer_text': footer_text, 'footer_layout_sha256': digest(char_geometry(footer_chars)),
            'body_character_indices': body, 'footer_character_indices': footer,
            'source_text_run_refs': refs, 'region_character_count': len(members),
            'content_operator_sha256': digest(inventory['operators']),
            'source_object_inventory_sha256': object_digest,
            'native_objects_fully_accounted': True, 'recognizer_started': False}


def replay_native_summary_region(path, proof, *, source_sha256, page_number):
    from .oge_reports import MAX_PDF_BYTES, MAX_PDF_PAGES
    import pdfplumber
    if not isinstance(proof, dict) or Path(path).stat().st_size > MAX_PDF_BYTES:
        return False
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_PDF_BYTES+1)
    if len(raw) > MAX_PDF_BYTES or hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError('Summary region proof belongs to a different or oversized source PDF')
    import io
    with pdfplumber.open(io.BytesIO(raw)) as document:
        if (type(page_number) is not int or not 1 <= page_number <= len(document.pages)
                or len(document.pages) > MAX_PDF_PAGES):
            raise ValueError('Summary region page is outside the bounded source PDF')
        actual = observe_native_summary_region(document.pages[page_number-1], page_number,
                                               source_sha256=source_sha256)
    return actual['status'] == 'observed' and actual == proof
