"""Positive visibility profile for bounded standard-font native PDF sources.

This inventory grants no identity or financial qualification. It deliberately
excludes composition that needs a renderer: only complete nonoverlapping black
Helvetica glyphs and disjoint bounded straight grid strokes are supported.
"""
from __future__ import annotations

import heapq
import math

from .pdf_native_observer import observe_source_characters

MAX_CHARACTERS = 25_000
MAX_TOKENS = 100_000
PAGE_KEYS = {'Type', 'Parent', 'Contents', 'Resources', 'MediaBox', 'CropBox',
             'Rotate', 'UserUnit', 'Metadata', 'LastModified'}
CATALOG_KEYS = {'Type', 'Pages', 'Info', 'Metadata', 'Version', 'Lang', 'MarkInfo',
                'PageMode', 'PageLayout'}
NUMERIC_OPS = {'Tm': 6, 'Td': 2, 'TD': 2, 'Tr': 1, 'g': 1, 'G': 1,
               'rg': 3, 'RG': 3, 'm': 2, 'l': 2, 'w': 1}
NO_ARG_OPS = {'q', 'Q', 'BT', 'ET', 'T*', 'S'}


class _Unsupported(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Unsupported(reason)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _black(space, color):
    if space == 'DeviceGray':
        if isinstance(color, (tuple, list)) and len(color) == 1:
            color = color[0]
        return _finite(color) and color == 0
    return (space == 'DeviceRGB' and isinstance(color, (tuple, list)) and
            len(color) == 3 and all(_finite(value) and value == 0 for value in color))


def _within(box, width, height):
    return (len(box) == 4 and all(_finite(value) for value in box) and
            0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def _program(page, fonts):
    """Close the actual operators, operands and single-segment stroke states."""
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.psparser import PSEOF, PSKeyword, PSLiteral
    allowed = {*NUMERIC_OPS, *NO_ARG_OPS, 'Tf', 'Tj', 'TJ'}
    class BoundedContentParser(PDFContentParser):
        token_count = 0

        def nexttoken(self):
            token = super().nexttoken()
            self.token_count += 1
            _require(self.token_count <= MAX_TOKENS, 'native_content_budget_exceeded')
            return token

        def do_keyword(self, position, token):
            # Refuse inline-image entry before the parser collects its data.
            _require(token.name.decode('ascii') in allowed, 'unsupported_native_content_operator')
            return super().do_keyword(position, token)

    parser = BoundedContentParser(page.page_obj.contents)
    operands, stack, strokes, raw = [], [], [], bytearray()
    text = False
    path = []
    state = {'width': 1, 'width_explicit': False, 'stroke_space': 'DeviceGray',
             'stroke_color': 0, 'font': None}
    try:
        while True:
            _, item = parser.nextobject()
            if not isinstance(item, PSKeyword):
                operands.append(item)
                continue
            op = item.name.decode('ascii')
            _require(op in allowed,
                     'unsupported_native_content_operator')
            if op in NUMERIC_OPS:
                _require(len(operands) == NUMERIC_OPS[op] and all(_finite(v) for v in operands),
                         'native_operator_operands_invalid')
            elif op in NO_ARG_OPS:
                _require(not operands, 'native_operator_operands_invalid')
            if op in {'q', 'Q'}:
                _require(not text and not path, 'native_graphics_state_invalid')
                if op == 'q':
                    stack.append(dict(state))
                    _require(len(stack) <= 32, 'native_graphics_state_invalid')
                else:
                    _require(bool(stack), 'native_graphics_state_invalid')
                    state = stack.pop()
            elif op == 'BT':
                _require(not text and not path, 'native_text_state_invalid')
                text = True
            elif op == 'ET':
                _require(text, 'native_text_state_invalid')
                text = False
            elif op in {'Tm', 'Td', 'TD', 'T*', 'Tf', 'Tj', 'TJ'}:
                _require(text, 'native_text_state_invalid')
                if op == 'Tf':
                    _require(len(operands) == 2 and isinstance(operands[0], PSLiteral) and
                             operands[0].name in fonts and _finite(operands[1]) and operands[1] > 0,
                             'native_font_selection_invalid')
                    state['font'] = operands[0].name
                elif op in {'Tj', 'TJ'}:
                    _require(state['font'] is not None and len(operands) == 1,
                             'native_text_show_invalid')
                    sequence = [operands[0]] if op == 'Tj' else operands[0]
                    _require(isinstance(sequence, list) and (op != 'Tj' or isinstance(operands[0], bytes)),
                             'native_text_show_invalid')
                    for value in sequence:
                        if isinstance(value, bytes):
                            _require(len(raw) + len(value) <= MAX_CHARACTERS,
                                     'source_visibility_character_budget_exceeded')
                            _require(all(32 <= code <= 126 for code in value), 'native_character_encoding_unsupported')
                            raw.extend(value)
                        else:
                            _require(_finite(value), 'native_text_show_invalid')
            elif op == 'Tr':
                _require(operands == [0], 'native_render_mode_unsupported')
            elif op in {'g', 'G', 'rg', 'RG'}:
                _require(all(0 <= value <= 1 for value in operands), 'native_color_operands_invalid')
                if op in {'G', 'RG'}:
                    state['stroke_space'] = 'DeviceGray' if op == 'G' else 'DeviceRGB'
                    state['stroke_color'] = operands[0] if op == 'G' else tuple(operands)
            elif op == 'w':
                _require(0 < operands[0] <= 2, 'native_stroke_width_unsupported')
                state.update(width=operands[0], width_explicit=True)
            elif op in {'m', 'l', 'S'}:
                _require(not text, 'native_path_state_invalid')
                if op == 'm':
                    _require(not path, 'native_path_state_invalid')
                    path.append(tuple(operands))
                elif op == 'l':
                    _require(len(path) == 1, 'native_path_state_invalid')
                    path.append(tuple(operands))
                else:
                    _require(len(path) == 2 and (path[0][0] == path[1][0] or path[0][1] == path[1][1]) and
                             path[0] != path[1] and _black(state['stroke_space'], state['stroke_color']),
                             'native_stroke_style_unsupported')
                    strokes.append((tuple(path), dict(state)))
                    path = []
            operands = []
    except PSEOF:
        pass
    _require(not operands and not stack and not text and not path, 'native_content_state_incomplete')
    return bytes(raw), strokes, parser.token_count


def _disjoint(rectangles, max_comparisons):
    """Sweep x intervals, bounding every active-pair visit including grid pairs."""
    active, endings = {}, []
    comparisons = 0
    for index, (box, kind) in enumerate(sorted(rectangles, key=lambda item: item[0][0])):
        while endings and endings[0][0] <= box[0]:
            _, expired = heapq.heappop(endings)
            active.pop(expired, None)
        for other, other_kind in active.values():
            comparisons += 1
            _require(comparisons <= max_comparisons, 'native_intersection_budget_exceeded')
            if kind == other_kind == 'line':
                continue
            overlap = max(box[1], other[1]) < min(box[3], other[3])
            _require(not overlap, 'native_glyph_or_stroke_overlap')
        active[index] = box, kind
        heapq.heappush(endings, (box[2], index))
    return comparisons


def _inspect(page, max_comparisons):
    from pdfminer.pdftypes import resolve1
    _require(type(max_comparisons) is int and 0 <= max_comparisons <= 200_000,
             'native_comparison_budget_invalid')
    width, height = page.width, page.height
    _require(_finite(width) and _finite(height) and width > 0 and height > 0 and
             tuple(page.mediabox) == tuple(page.bbox) == (0, 0, width, height) and
             page.cropbox == page.mediabox and page.rotation == 0 and
             resolve1(page.page_obj.attrs.get('UserUnit', 1)) == 1,
             'native_visible_page_domain_unsupported')
    _require(set(page.page_obj.attrs) <= PAGE_KEYS, 'native_page_state_unsupported')
    resources = resolve1(page.page_obj.resources)
    _require(isinstance(resources, dict) and not any(key not in {'Font', 'ProcSet'} and resolve1(value)
             for key, value in resources.items()), 'native_resources_unsupported')
    fonts = resolve1(resources.get('Font', {}))
    _require(isinstance(fonts, dict), 'native_standard_font_unavailable')
    for value in fonts.values():
        font = resolve1(value)
        expected = {'Type':'Font', 'Subtype':'Type1', 'BaseFont':'Helvetica', 'Encoding':'WinAnsiEncoding'}
        _require(isinstance(font, dict) and set(font) == set(expected) and all(
            getattr(font[key], 'name', None) == name for key, name in expected.items()),
            'native_standard_font_unavailable')
    raw, strokes, tokens = _program(page, fonts)
    chars, modes, flags, paint = observe_source_characters(page,
        max_characters=MAX_CHARACTERS, max_tokens=MAX_TOKENS)
    # The controlled observer stops before accessing page.chars if its limit
    # fails. Do not trigger pdfplumber's default object materialization first.
    _require(not page.annots and not any(key not in {'char', 'line'} and values
             for key, values in page.objects.items()), 'native_painted_objects_unsupported')
    _require(raw.decode('ascii') == ''.join(char['text'] for char in chars) and
             len(chars) == len(modes) == len(flags) == len(paint['character_orders']),
             'native_glyph_projection_differs')
    _require(not any(mode != 0 for mode in modes) and not any(flags), 'native_render_state_unsupported')
    import pymupdf
    font = pymupdf.Font('helv')
    rectangles = []
    for char in chars:
        text, matrix, size = char['text'], char.get('matrix'), char.get('size')
        box = [char[key] for key in ('x0', 'top', 'x1', 'bottom')]
        _require(len(text) == 1 and 32 <= ord(text) <= 126 and char.get('fontname') == 'Helvetica' and
                 char.get('upright') is True and isinstance(matrix, (list, tuple)) and len(matrix) == 6 and
                 all(_finite(value) for value in matrix) and list(matrix[:4]) == [1, 0, 0, 1] and
                 _finite(size) and size > 0 and _finite(char.get('adv')) and
                 abs(char['adv'] - font.glyph_advance(ord(text)) * size) <= 1e-4 and
                 _within(box, width, height) and _black(char.get('ncs'), char.get('non_stroking_color')),
                 'native_glyph_style_or_geometry_unsupported')
        if text != ' ':
            glyph = font.glyph_bbox(ord(text))
            x, y = matrix[-2:]
            ink = [x + glyph.x0 * size, height - y - glyph.y1 * size,
                   x + glyph.x1 * size, height - y - glyph.y0 * size]
            _require(_within(ink, width, height), 'native_glyph_ink_outside_visible_page')
            rectangles.append((ink, 'glyph'))
    _require(len(strokes) == len(page.lines), 'native_stroke_projection_differs')
    for line, (path, state) in zip(page.lines, strokes):
        box = [line[key] for key in ('x0', 'top', 'x1', 'bottom')]
        expected_box = [min(p[0] for p in path), height - max(p[1] for p in path),
                        max(p[0] for p in path), height - min(p[1] for p in path)]
        width_matches = line['linewidth'] == state['width'] or (
            not state['width_explicit'] and line['linewidth'] == 0)
        _require(all(_finite(value) for value in box) and _finite(line['linewidth']) and width_matches and
                 line['stroke'] is True and line['fill'] is False and len(line.get('path', [])) == 2 and
                 all(abs(a-b) <= 1e-5 for a, b in zip(box, expected_box)) and
                 line['stroking_color'] == state['stroke_color'], 'native_stroke_projection_differs')
        footprint = [box[0]-1, box[1]-1, box[2]+1, box[3]+1]
        _require(_within(footprint, width, height), 'native_stroke_visible_domain_unsupported')
        rectangles.append((footprint, 'line'))
    comparisons = _disjoint(rectangles, max_comparisons)
    return {'status':'observed', 'reason':None, 'character_count':len(chars),
            'stroke_count':len(strokes), 'content_token_count':tokens, 'comparison_count':comparisons}


def inspect_standard_visible_page(page, *, max_comparisons=200_000):
    try:
        return _inspect(page, max_comparisons)
    except _Unsupported as error:
        return {'status':'unknown', 'reason':str(error)}
    except Exception as error:
        reason = str(error)
        return {'status':'unknown', 'reason':reason if reason.startswith('source_visibility_') else
                'native_visibility_inventory_unavailable'}


def inspect_standard_visible_document(pdf, *, max_pages=32, max_comparisons=200_000):
    """Verify every original page; no saved role/visibility label is consulted."""
    try:
        _require(type(max_pages) is int and 0 < max_pages <= 32 and
                 type(max_comparisons) is int and 0 <= max_comparisons <= 200_000,
                 'native_document_budget_invalid')
        _require(set(pdf.doc.catalog) <= CATALOG_KEYS, 'native_document_paint_state_unsupported')
        _require(0 < len(pdf.pages) <= max_pages, 'native_page_budget_exceeded')
        pages = []
        for number, page in enumerate(pdf.pages, 1):
            result = inspect_standard_visible_page(page, max_comparisons=max_comparisons)
            pages.append({'page_number':number, **result})
            if result['status'] != 'observed':
                return {'status':'unknown', 'reason':result['reason'], 'pages':pages}
        return {'status':'observed', 'reason':None, 'pages':pages}
    except _Unsupported as error:
        return {'status':'unknown', 'reason':str(error)}
    except Exception:
        return {'status':'unknown', 'reason':'native_document_inventory_unavailable'}
