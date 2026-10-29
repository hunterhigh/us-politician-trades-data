"""Bounded native PDF objects shared by fixed government page/region profiles.

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


def inspect_strict_native_objects(page, *, allowed_sizes=(10,), allow_square_linecaps=False):
    """Return actual source objects and runs, or a specific unsupported reason.

    Privacy's original checks and operator projection are preserved. Cover's
    larger fixed type sizes are admitted here only so its full static profile
    can check them; this function never excuses arbitrary text in those sizes.
    """
    try:
        return _inspect(page, allowed_sizes=allowed_sizes, allow_square_linecaps=allow_square_linecaps)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return {'status': 'unknown', 'reason': 'native_page_object_inventory_unavailable'}


def _inspect(page, *, allowed_sizes, allow_square_linecaps=False):
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
    if type(allow_square_linecaps) is not bool:
        return unknown('native_linecap_profile_invalid')
    parser = PDFContentParser(page.page_obj.contents)
    operators, operands, text_runs, run_records = [], [], [], []
    text_run = bytearray()
    show_indices = []
    tokens = depth = 0
    # This opt-in is solely for the mixed-page regional consumer. The default
    # Privacy/Cover operator profile and returned fields remain unchanged.
    caps, strokes, state_stack, path = [], [], [], []
    linecap, linewidth, in_text = 0, 1., False
    from pdfminer.utils import mult_matrix, apply_matrix_pt
    ctm = None
    if allow_square_linecaps:
        x0, y0, x1, y1 = page.page_obj.mediabox
        ctm = ((0., -1., 1., 0., -y0, x1) if page.page_obj.rotate == 90
               else (1., 0., 0., 1., -x0, -y0))

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
                if op not in OPS and not (allow_square_linecaps and op == 'J'):
                    return unknown('unsupported_native_content_operator')
                operators.append(op)
                if allow_square_linecaps:
                    numeric = lambda n: (len(operands) == n and all(
                        type(v) in (int, float) and math.isfinite(v) for v in operands))
                    if op == 'J':
                        if in_text or len(operands) != 1 or type(operands[0]) is not int or operands[0] != 2:
                            return unknown('unsupported_native_linecap')
                        linecap = 2
                        caps.append({'operator_index': len(operators)-1,
                                     'graphics_depth': depth, 'line_cap': 2})
                    elif op == 'q':
                        if path or in_text:
                            return unknown('unsupported_native_regional_graphics_state')
                        state_stack.append((ctm, linecap, linewidth))
                    elif op == 'Q':
                        if path or in_text or not state_stack:
                            return unknown('unsupported_native_regional_graphics_state')
                        ctm, linecap, linewidth = state_stack.pop()
                    elif op == 'cm':
                        if not numeric(6):
                            return unknown('unsupported_native_regional_matrix')
                        ctm = mult_matrix(tuple(operands), ctm)
                    elif op == 'w':
                        if not numeric(1) or not 0 < operands[0] <= 2:
                            return unknown('unsupported_native_regional_stroke_width')
                        linewidth = operands[0]
                    elif op == 'BT':
                        if in_text or path:
                            return unknown('unsupported_native_regional_text_state')
                        in_text = True
                    elif op == 'ET':
                        if not in_text:
                            return unknown('unsupported_native_regional_text_state')
                        in_text = False
                    elif op in {'m', 'l'}:
                        if in_text or not numeric(2) or len(path) != (0 if op == 'm' else 1):
                            return unknown('unsupported_native_regional_path')
                        path.append(apply_matrix_pt(ctm, tuple(operands)))
                    elif op == 'S':
                        if in_text or len(path) != 2 or tuple(ctm[:4]) != (1., 0., 0., 1.):
                            return unknown('unsupported_native_regional_path')
                        box = [min(p[0] for p in path), page.height-max(p[1] for p in path),
                               max(p[0] for p in path), page.height-min(p[1] for p in path)]
                        strokes.append({'operator_index': len(operators)-1, 'line_cap': linecap,
                            'linewidth': linewidth, 'bbox_pdf_points': box,
                            'outer_bbox_pdf_points': [box[0]-linewidth, box[1]-linewidth,
                                                      box[2]+linewidth, box[3]+linewidth]})
                        path = []
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
    result = {'status': 'observed', 'reason': None, 'chars': chars, 'lines': page.lines,
            'operators': operators, 'text_runs': text_runs, 'text_run_records': run_records,
            'content_token_count': tokens}
    if allow_square_linecaps:
        if path or in_text or state_stack or len(strokes) != len(page.lines):
            return unknown('native_regional_path_projection_differs')
        for stroke, line in zip(strokes, page.lines):
            if (round(stroke['linewidth'], 5) != round(line['linewidth'], 5) or
                    any(round(a, 5) != round(line[key], 5) for a, key in zip(
                        stroke['bbox_pdf_points'], ('x0', 'top', 'x1', 'bottom')))):
                return unknown('native_regional_path_projection_differs')
        result.update(line_cap_operations=caps, source_stroke_records=strokes)
    return result
