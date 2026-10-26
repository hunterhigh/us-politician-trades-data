"""Bounded native PDF objects shared by two fixed government page profiles.

An inventory is not a page role or a qualification. Only the fixed profile
consumer can account for its complete text, style, drawings and source runs.
"""
from __future__ import annotations

import hashlib
import json
import math


OPS = {"BT", "ET", "Q", "RG", "rg", "g", "G", "S", "TJ", "Td", "Tm",
       "Tf", "Tj", "cm", "l", "m", "q", "w"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def char_geometry(chars):
    return sorted([[c['text'], *[round(float(c[key]), 5)
        for key in ('x0', 'top', 'x1', 'bottom')]] for c in chars],
        key=lambda item: (item[2], item[1], item[0]))


def line_geometry(lines):
    return sorted([[*[round(float(line[key]), 5)
        for key in ('x0', 'top', 'x1', 'bottom', 'linewidth')],
        list(line['stroking_color']) if isinstance(line['stroking_color'], (list, tuple))
        else [line['stroking_color']], line['stroke'], line['fill']] for line in lines])


def inspect_strict_native_objects(page, *, allowed_sizes=(10,)):
    """Return actual source objects and runs, or a specific unsupported reason.

    Privacy's original checks and operator projection are preserved. Cover's
    larger fixed type sizes are admitted here only so its full static profile
    can check them; this function never excuses arbitrary text in those sizes.
    """
    try:
        return _inspect(page, allowed_sizes=allowed_sizes)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return {'status': 'unknown', 'reason': 'native_page_object_inventory_unavailable'}


def _inspect(page, *, allowed_sizes):
    def unknown(reason):
        return {'status': 'unknown', 'reason': reason}

    if not all(hasattr(page, key) for key in ('chars', 'objects', 'annots', 'page_obj', 'bbox')):
        return unknown('native_page_object_inventory_unavailable')
    if (tuple(float(v) for v in page.bbox) != (0., 0., 842., 595.) or page.rotation not in (0, 90) or
            any(key not in {'char', 'line'} and values for key, values in page.objects.items()) or
            page.annots):
        return unknown('unsupported_page_geometry_or_nontext_objects')
    chars = page.chars
    if not chars or len(chars) > 10_000:
        return unknown('native_character_inventory_unsupported')
    for c in chars:
        if (not isinstance(c.get('text'), str) or len(c['text']) != 1 or
                c.get('fontname') != 'Helvetica' or c.get('upright') is not True or
                not any(abs(float(c.get('size', 0)) - size) <= 1e-5 for size in allowed_sizes) or
                c.get('non_stroking_color') not in (0, (0,), [0]) or
                list(c.get('matrix', ()))[:4] != [1., 0., 0., 1.] or
                any(not math.isfinite(float(c.get(k, float('nan'))))
                    for k in ('x0', 'top', 'x1', 'bottom')) or
                not 0 <= c['x0'] < c['x1'] <= page.width or
                not 0 <= c['top'] < c['bottom'] <= page.height):
            return unknown('native_character_style_or_geometry_unsupported')
    from pdfminer.pdftypes import resolve1
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.psparser import PSEOF, PSKeyword

    fonts = resolve1(page.page_obj.resources.get('Font', {}))
    if not fonts:
        return unknown('standard_font_program_unavailable')
    for value in fonts.values():
        value = resolve1(value)
        if (set(value) != {'Subtype', 'Type', 'BaseFont', 'Encoding'} or
                getattr(value['Subtype'], 'name', None) != 'Type1' or
                getattr(value['Type'], 'name', None) != 'Font' or
                getattr(value['BaseFont'], 'name', None) != 'Helvetica' or
                getattr(value['Encoding'], 'name', None) != 'WinAnsiEncoding'):
            return unknown('standard_font_program_unavailable')
    parser = PDFContentParser(page.page_obj.contents)
    operators, operands, text_runs, run_records = [], [], [], []
    text_run = bytearray()
    show_indices = []
    tokens = depth = 0

    def flush():
        if text_run:
            raw = bytes(text_run)
            text_runs.append(raw)
            run_records.append({'raw': raw, 'show_operator_indices': list(show_indices)})
        text_run.clear()
        show_indices.clear()

    try:
        while True:
            _, obj = parser.nextobject()
            tokens += 1
            if tokens > 100_000:
                return unknown('native_content_budget_exceeded')
            if isinstance(obj, PSKeyword):
                op = obj.name.decode('ascii')
                if op not in OPS:
                    return unknown('unsupported_native_content_operator')
                operators.append(op)
                if op in {'Tj', 'TJ'}:
                    if len(operands) != 1 or op == 'Tj' and not isinstance(operands[0], bytes):
                        return unknown('unsupported_native_text_show')
                    sequence = [operands[0]] if op == 'Tj' else operands[0]
                    if (not isinstance(sequence, list) or any(not isinstance(value, bytes) and
                            (type(value) not in (int, float) or not math.isfinite(value))
                            for value in sequence)):
                        return unknown('unsupported_native_text_show')
                    show_indices.append(len(operators) - 1)
                    for value in sequence:
                        if isinstance(value, bytes):
                            text_run.extend(value)
                else:
                    flush()
                operands = []
                depth += (op == 'q') - (op == 'Q')
                if not 0 <= depth <= 32:
                    return unknown('unsupported_graphics_state')
            else:
                operands.append(obj)
    except PSEOF:
        pass
    flush()
    if depth != 0:
        return unknown('unsupported_graphics_state')
    return {'status': 'observed', 'reason': None, 'chars': chars, 'lines': page.lines,
            'operators': operators, 'text_runs': text_runs, 'text_run_records': run_records,
            'content_token_count': tokens}
