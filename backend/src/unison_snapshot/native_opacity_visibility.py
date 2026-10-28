"""Explicit source-bound zero/one opacity and monochrome paint proof.

The PDF interpreter does not implement alpha. This separate closed program
profile computes actual referenced gs state while retaining every original
character, render mode, conservative flag and paint ordinal. It grants no field
qualification and never rewrites the source PDF or changes historical profiles.
"""
from __future__ import annotations

from copy import deepcopy
from .native_source_visibility import (PAGE_KEYS, CATALOG_KEYS, NUMERIC_OPS,
    NO_ARG_OPS, _Unsupported, _finite, _within)
from .native_bichrome_visibility import (mono, _Budget, _FootprintIndex,
    _path_box, _contains)
from .pdf_native_observer import (observe_source_characters, _graphics_state_value,
                                  _resolve_graphics_state, _clipping_path_weight)

POLICY_VERSION = 'native-opacity-source-paint/v1'
DISPLAY_DOMAIN = 'opaque_white_paper/v1'
MAX_CHARACTERS = 25_000
MAX_TOKENS = 100_000
MAX_PAINT_EVENTS = 30_000
MAX_PATH_EVENTS = 5_000
MAX_FILLS = 2_000
MAX_GRAPHICS_STATE_RESOURCES = 64
MAX_GRAPHICS_STATE_EVENTS = 5_000
MAX_CLIPPING_EVENTS = 5_000
MAX_CLIPPING_PATH_POINTS = 100_000
LEGACY_CLIP_FLAG = 'source_visibility_active_clipping_unsupported'
LEGACY_GS_FLAG = 'source_visibility_active_graphics_state_unsupported'
finite = _finite


def require(condition, reason):
    if not condition:
        raise _Unsupported('native_opacity_' + reason)


def _graphics_state(reference):
    from pdfminer.psparser import literal_name, PSLiteral
    state = _resolve_graphics_state(reference)
    require(isinstance(state, dict) and len(state) <= 4 and set(state) <= {'Type', 'ca', 'CA', 'BM'},
            'graphics_state_keys_unsupported')
    require('Type' not in state or (isinstance(_resolve_graphics_state(state['Type']), PSLiteral) and
            literal_name(_resolve_graphics_state(state['Type'])) == 'ExtGState'),
            'graphics_state_type_invalid')
    update = {}
    for key in ('ca', 'CA'):
        if key in state:
            value = _resolve_graphics_state(state[key])
            require(finite(value) and value in (0, 1), 'alpha_unsupported')
            update[key] = value
    if 'BM' in state:
        require(isinstance(_resolve_graphics_state(state['BM']), PSLiteral) and
                literal_name(_resolve_graphics_state(state['BM'])) == 'Normal', 'blend_unsupported')
        update['BM'] = 'Normal'
    # This predicts the existing observer's conservative raw flags, including
    # their sticky ca0 -> ca1 behavior. It never uses those flags as alpha.
    legacy_unsupported = (any(key in state and state[key] != 1 for key in ('ca', 'CA')) or
                          ('BM' in state and literal_name(state['BM']) != 'Normal'))
    return _graphics_state_value(state), update, legacy_unsupported


def _program(page, fonts, graphics_states, *, rectangular_clip=False, clip_budget=None):
    """Close the source program before any default object materialization."""
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.psparser import PSKeyword, PSLiteral, PSEOF
    events = []
    nums = {**NUMERIC_OPS, 're': 4}
    noargs = {*NO_ARG_OPS, 'f', 'h'}
    if rectangular_clip:
        noargs.update({'W', 'n'})
        nums['M'] = 1
        if clip_budget is None:
            clip_budget = _Budget()
    allowed = {*nums, *noargs, 'Tf', 'Tj', 'TJ', 'gs'}

    class Parser(PDFContentParser):
        count = 0

        def nexttoken(self):
            item = super().nexttoken()
            self.count += 1
            require(self.count <= MAX_TOKENS, 'token_budget')
            return item

        def do_keyword(self, pos, item):
            require(item.name.decode('ascii') in allowed, 'unsupported_op:' + item.name.decode('ascii'))
            return super().do_keyword(pos, item)
    if not page.page_obj.contents:
        return (b'', [], 0, [], []) if rectangular_clip else (b'', [], 0, [])
    parser = Parser(page.page_obj.contents)
    args = []
    stack = []
    path = []
    text = False
    raw = bytearray()
    path_count = 0
    fill_count = 0
    gs_events = []
    clipping_events = []
    clipping_path_points = 0
    pending_clip = None
    path_rectangle = False
    rectangle_operands = None
    state = {'ca': 1, 'CA': 1, 'BM': 'Normal', 'legacy_visibility_flags': [], 'font': None, 'size': None, 'mode': 0, 'width': 1, 'width_explicit': False, 'stroke_space': 'DeviceGray', 'stroke_color': 0, 'fill_space': 'DeviceGray', 'fill_color': 0}
    if rectangular_clip:
        state['clip_box'] = [0, 0, page.width, page.height]
        state['miter_limit'] = 10

    def clipping_event(phase, operator):
        nonlocal clipping_path_points
        require(len(clipping_events) < MAX_CLIPPING_EVENTS, 'clipping_event_budget')
        weight = _clipping_path_weight(path, maximum=MAX_CLIPPING_PATH_POINTS - clipping_path_points)
        require(clipping_path_points + weight <= MAX_CLIPPING_PATH_POINTS, 'clipping_path_budget')
        clipping_path_points += weight
        clipping_events.append({'phase':phase, 'operator':operator,
            'raw_path':deepcopy(path), 'before_paint_order':len(events)+1})

    def event(kind, **data):
        nonlocal path_count, fill_count
        path_count += kind != 'text'
        fill_count += kind == 'fill'
        require(len(events) < MAX_PAINT_EVENTS, 'event_budget')
        require(path_count <= MAX_PATH_EVENTS, 'path_budget')
        require(fill_count <= MAX_FILLS, 'fill_budget')
        events.append({'order': len(events) + 1, 'kind': kind, 'state': deepcopy(state), **data})
    try:
        while True:
            _, item = parser.nextobject()
            if not isinstance(item, PSKeyword):
                args.append(item)
                continue
            op = item.name.decode('ascii')
            require(op in allowed, 'unsupported_op:' + op)
            if rectangular_clip and pending_clip is not None:
                require(op == 'n', 'clipping_commit_must_be_n')
            if op in nums:
                require(len(args) == nums[op] and all((finite(v) for v in args)), 'bad_arity:' + op)
            if op in noargs:
                require(not args, 'bad_arity:' + op)
            if op == 'gs':
                require(len(args) == 1 and isinstance(args[0], PSLiteral) and not path,
                        'graphics_state_operand_or_position')
                name = args[0].name
                require(name in graphics_states, 'graphics_state_missing')
                dictionary, update, legacy_unsupported = _graphics_state(graphics_states[name])
                require(len(gs_events) < MAX_GRAPHICS_STATE_EVENTS, 'graphics_state_event_budget')
                gs_events.append({'name': name, 'state': dictionary,
                                  'before_paint_order': len(events) + 1})
                state.update(update)
                if legacy_unsupported:
                    state['legacy_visibility_flags'] = (sorted(set(state['legacy_visibility_flags']) | {LEGACY_GS_FLAG})
                                                        if rectangular_clip else [LEGACY_GS_FLAG])
            elif op in {'q', 'Q'}:
                require(not text and (not path), 'q_path_or_text')
                if op == 'q':
                    stack.append(deepcopy(state))
                    require(len(stack) <= 32, 'q_depth')
                else:
                    require(bool(stack), 'unmatched_Q')
                    state = stack.pop()
            elif op == 'BT':
                require(not text and (not path), 'nested_BT')
                text = True
            elif op == 'ET':
                require(text and (not path), 'unmatched_ET')
                text = False
            elif op in {'Tm', 'Td', 'TD', 'T*', 'Tf', 'Tj', 'TJ'}:
                require(text, 'text_outside_BT')
                if op == 'Tf':
                    require(len(args) == 2 and isinstance(args[0], PSLiteral) and (args[0].name in fonts) and finite(args[1]) and (args[1] > 0), 'font')
                    state.update(font=args[0].name, size=args[1])
                elif op in {'Tj', 'TJ'}:
                    require(len(args) == 1 and state['font'] is not None, 'text_show')
                    seq = [args[0]] if op == 'Tj' else args[0]
                    require(isinstance(seq, list), 'text_show')
                    start = len(raw)
                    for value in seq:
                        if isinstance(value, bytes):
                            require(len(raw) + len(value) <= MAX_CHARACTERS and all((32 <= v <= 126 for v in value)), 'text_budget_or_encoding')
                            raw.extend(value)
                        else:
                            require(op == 'TJ' and finite(value), 'TJ_operand')
                    event('text', raw_span=[start, len(raw)])
            elif op == 'Tr':
                require(args[0] in (0, 3), 'render_mode_unsupported')
                state['mode'] = args[0]
            elif op in {'g', 'G', 'rg', 'RG'}:
                space = 'DeviceGray' if op in {'g', 'G'} else 'DeviceRGB'
                color = args[0] if space == 'DeviceGray' else tuple(args)
                require(mono(space, color) is not None, 'non_monochrome_color')
                state.update({('fill_' if op in {'g', 'rg'} else 'stroke_') + 'space': space, ('fill_' if op in {'g', 'rg'} else 'stroke_') + 'color': color})
            elif op == 'w':
                require(0 < args[0] <= 2, 'width')
                state.update(width=args[0], width_explicit=True)
            elif op == 'M':
                # Only the explicit clip profile accepts M. Its supported
                # strokes are one segment (no joins), rectangles only fill,
                # and Tr0/3 glyphs never stroke, so miter cannot change ink.
                require(args[0] >= 1, 'miter_limit_unsupported')
                state['miter_limit'] = args[0]
            elif op == 're':
                require(not text and (not path), 'compound_path')
                x, y, w, h = args
                require(w > 0 and h > 0, 'nonpositive_re')
                if rectangular_clip:
                    require(finite(x+w) and finite(y+h), 'derived_rectangle_nonfinite')
                    path_rectangle = True
                    rectangle_operands = tuple(args)
                path = [('m', x, y), ('l', x + w, y), ('l', x + w, y + h), ('l', x, y + h), ('h',)]
            elif op == 'm':
                require(not text and (not path), 'compound_path')
                path = [('m', *args)]
                if rectangular_clip:
                    path_rectangle = False
            elif op == 'l':
                require(not text and len(path) == 1 and (path[0][0] == 'm'), 'non_line')
                path.append(('l', *args))
            elif op == 'W':
                require(not text and path_rectangle and len(path) == 5 and path[-1] == ('h',),
                        'single_rectangle_clip_required')
                x, y, width, height = rectangle_operands
                require(all(_clip_exact_number(value) for value in
                    (*rectangle_operands, x+width, y+height)), 'clipping_numeric_domain_unsupported')
                pending_clip = _path_box(path, page.height)
                require(all(finite(value) for value in pending_clip), 'derived_rectangle_nonfinite')
                clipping_event('request', 'W')
                state['legacy_visibility_flags'] = sorted(set(state['legacy_visibility_flags']) | {LEGACY_CLIP_FLAG})
            elif op == 'n':
                require(pending_clip is not None, 'clipping_request_required')
                clipping_event('commit', 'n')
                clip_budget.charge('clipping_state_intersections')
                state['clip_box'] = _clip_intersection(state['clip_box'], pending_clip)
                pending_clip = None
                path = []
                path_rectangle = False
            elif op == 'S':
                require(not text and len(path) == 2 and (path[0][1] == path[1][1] or path[0][2] == path[1][2]), 'non_line')
                require(mono(state['stroke_space'], state['stroke_color']) == 0, 'nonblack_stroke')
                event('stroke', path=deepcopy(path))
                path = []
            elif op == 'h':
                require(not text and len(path) == 5 and (path[-1] == ('h',)), 'not_single_re_redundant_h')
                path.append(('h',))
            elif op == 'f':
                require(not text and len(path) in (5, 6) and (path[-1] == ('h',)), 'not_single_re')
                event('fill', path=deepcopy(path))
                path = []
            args = []
    except PSEOF:
        pass
    require(not args and (not text) and (not path) and (not stack), 'incomplete_program')
    result = (bytes(raw), events, parser.count, gs_events)
    return (*result, clipping_events) if rectangular_clip else result


def _source_domain(page):
    from pdfminer.pdftypes import resolve1
    width, height = page.width, page.height
    require(finite(width) and finite(height) and width > 0 and height > 0 and
            tuple(page.mediabox) == tuple(page.bbox) == (0, 0, width, height) and
            page.cropbox == page.mediabox and page.rotation == 0 and
            resolve1(page.page_obj.attrs.get('UserUnit', 1)) == 1,
            'page_domain_unsupported')
    require(set(page.page_obj.attrs) <= PAGE_KEYS and
            set(page.pdf.doc.catalog) <= CATALOG_KEYS, 'page_or_document_state_unsupported')
    resources = resolve1(page.page_obj.resources)
    require(isinstance(resources, dict) and not any(
        key not in {'Font', 'ProcSet', 'ExtGState'} and resolve1(value) for key, value in resources.items()),
        'resources_unsupported')
    fonts = resolve1(resources.get('Font', {}))
    require(isinstance(fonts, dict), 'standard_font_unavailable')
    expected = {'Type':'Font', 'Subtype':'Type1', 'BaseFont':'Helvetica', 'Encoding':'WinAnsiEncoding'}
    for reference in fonts.values():
        font = resolve1(reference)
        require(isinstance(font, dict) and set(font) == set(expected) and all(
            getattr(font[key], 'name', None) == name for key, name in expected.items()),
            'standard_font_unavailable')
    graphics_states = _resolve_graphics_state(resources.get('ExtGState', {}))
    require(isinstance(graphics_states, dict) and len(graphics_states) <= MAX_GRAPHICS_STATE_RESOURCES and
            all(type(key) is str and len(key) <= 128 for key in graphics_states),
            'graphics_state_resources_unsupported')
    return fonts, graphics_states




def _clip_exact_number(value):
    """A source rectangle shared exactly by double and binary32 interpreters.

    Merely finite double values can overflow the renderer's binary32 geometry
    and produce a different visible clip. This is an explicit, new-only source
    numeric domain, not a coordinate-magnitude threshold or a pixel heuristic.
    """
    import struct
    if not finite(value):
        return False
    try:
        single = struct.unpack('!f', struct.pack('!f', value))[0]
    except (OverflowError, struct.error):
        return False
    return finite(single) and single == value


def _clip_intersection(outer, rectangle):
    if outer is None:
        return None
    box = [max(outer[0], rectangle[0]), max(outer[1], rectangle[1]),
           min(outer[2], rectangle[2]), min(outer[3], rectangle[3])]
    return box if box[0] < box[2] and box[1] < box[3] else None


def _clip_relation(clip, box, budget):
    budget.charge('clipping_relations')
    if clip is None:
        return 'outside'
    if clip[0] <= box[0] and clip[1] <= box[1] and box[2] <= clip[2] and box[3] <= clip[3]:
        return 'inside'
    budget.charge('clipping_intersections')
    return ('partial' if max(clip[0],box[0]) < min(clip[2],box[2]) and
            max(clip[1],box[1]) < min(clip[3],box[3]) else 'outside')

def _inspect(page, maximum, display_domain, *, rectangular_clip=False):
    require(type(maximum) is int and 0 <= maximum <= 200_000, 'comparison_budget_invalid')
    require(display_domain == DISPLAY_DOMAIN, 'display_domain_unsupported')
    budget = _Budget(maximum) if rectangular_clip else None
    fonts, graphics_states = _source_domain(page)
    # Grammar, lexical/character/event limits precede ordinary object layout.
    if rectangular_clip:
        raw, events, tokens, gs_events, clipping_events = _program(page, fonts, graphics_states, rectangular_clip=True, clip_budget=budget)
        clip_arguments = {'include_clipping_events':True, 'max_clipping_events':MAX_CLIPPING_EVENTS}
    else:
        raw, events, tokens, gs_events = _program(page, fonts, graphics_states)
        clip_arguments = {}
    chars, modes, flags, paint = observe_source_characters(page,
        max_characters=MAX_CHARACTERS, max_tokens=MAX_TOKENS,
        include_path_events=True, include_graphics_state_events=True,
        max_paint_events=MAX_PAINT_EVENTS, max_graphics_state_events=MAX_GRAPHICS_STATE_EVENTS, **clip_arguments)
    require(not page.annots and not paint['images'] and not any(
        key not in {'char', 'line', 'rect', 'curve'} and values
        for key, values in page.objects.items()), 'painted_objects_unsupported')
    require(raw.decode('ascii') == ''.join(char['text'] for char in chars) and
            len(chars) == len(modes) == len(flags) == len(paint['character_orders']), 'glyph_inventory_differs')
    require(len(events) == paint['paint_event_count'], 'paint_order_differs')
    require(gs_events == paint['graphics_state_events'], 'graphics_state_inventory_differs')
    if rectangular_clip:
        require(clipping_events == paint['clipping_events'], 'clipping_inventory_differs')
    expected_orders = []
    for event in events:
        if event['kind'] == 'text':
            expected_orders.extend([event['order']] * (event['raw_span'][1]-event['raw_span'][0]))
    require(expected_orders == paint['character_orders'], 'show_order_differs')
    expected_flags = [events[order - 1]['state']['legacy_visibility_flags'] for order in expected_orders]
    require(flags == expected_flags, 'glyph_visibility_flags_differ')
    paths = [event for event in events if event['kind'] != 'text']
    require(len(paths) == len(paint['path_events']), 'path_inventory_differs')
    for event, actual in zip(paths, paint['path_events']):
        state = event['state']
        is_fill = event['kind'] == 'fill'
        require(event['order'] == actual['paint_order'] and
                event['path'] == actual['raw_path'] and actual['fill'] is is_fill and
                actual['stroke'] is (not is_fill) and actual['evenodd'] is False and
                actual['visibility_flags'] == state['legacy_visibility_flags'], 'path_program_differs')
        require(all(state[prefix+'_space'] == actual[prefix+'_colorspace'] and
                    state[prefix+'_color'] == actual[prefix+'_color']
                    for prefix in ('fill', 'stroke')), 'path_color_differs')
        if event['kind'] == 'stroke':
            require(actual['source_linewidth'] == state['width'] or
                    (not state['width_explicit'] and actual['source_linewidth'] == 0),
                    'stroke_width_differs')
    strokes = [event for event in paths if event['kind'] == 'stroke']
    require(len(strokes) == len(page.lines), 'stroke_inventory_differs')
    displayed_stroke_indices = [i for i, event in enumerate(strokes) if event['state']['CA'] == 1]
    for event, line in zip(strokes, page.lines):
        require(line.get('stroke') is True and line.get('fill') is False and
                len(line.get('path', [])) == 2 and
                all(abs(a-b) <= 1e-5 for a, b in zip(_path_box(event['path'], page.height),
                    [line[key] for key in ('x0', 'top', 'x1', 'bottom')])), 'stroke_projection_differs')
    import pymupdf
    font = pymupdf.Font('helv')
    ink_boxes = []
    for ordinal, char in enumerate(chars):
        text, matrix, size = char['text'], char.get('matrix'), char.get('size')
        state = events[expected_orders[ordinal]-1]['state']
        require(len(text) == 1 and 32 <= ord(text) <= 126 and
                char.get('fontname') == 'Helvetica' and char.get('upright') is True and
                isinstance(matrix, (list, tuple)) and len(matrix) == 6 and
                all(finite(value) for value in matrix) and list(matrix[:4]) == [1,0,0,1] and
                finite(size) and size > 0 and abs(size-state['size']) <= 1e-5 and
                finite(char.get('adv')) and abs(char['adv']-font.glyph_advance(ord(text))*size) <= 1e-4 and
                modes[ordinal] == state['mode'] and modes[ordinal] in (0,3) and
                _within([char[key] for key in ('x0','top','x1','bottom')], page.width, page.height) and
                mono(char.get('ncs'), char.get('non_stroking_color')) is not None and
                mono(char['ncs'], char['non_stroking_color']) == mono(state['fill_space'], state['fill_color']),
                'glyph_style_or_geometry_unsupported')
        if text == ' ':
            ink_boxes.append(None)
            continue
        ink = font.glyph_bbox(ord(text))
        x, y = matrix[-2:]
        box = [x+ink.x0*size, page.height-y-ink.y1*size,
               x+ink.x1*size, page.height-y-ink.y0*size]
        require(_within(box, page.width, page.height), 'glyph_ink_outside_page')
        ink_boxes.append(box)
    clip_states = []
    if rectangular_clip:
        for char, box, order in zip(chars, ink_boxes, expected_orders):
            clip_states.append(_clip_relation(events[order-1]['state']['clip_box'],
                box if box is not None else [char[key] for key in ('x0','top','x1','bottom')], budget))
    items = []
    for ordinal, box in enumerate(ink_boxes):
        if (box is not None and modes[ordinal] == 0 and events[expected_orders[ordinal]-1]['state']['ca'] == 1 and
                (not rectangular_clip or clip_states[ordinal] != 'outside')):
            items.append({'kind':'glyph', 'ordinal':ordinal, 'order':expected_orders[ordinal], 'box':box})
    for event in paths:
        box = _path_box(event['path'], page.height)
        if event['kind'] == 'stroke':
            box = [box[0]-1, box[1]-1, box[2]+1, box[3]+1]
        require(_within(box, page.width, page.height), 'path_domain_unsupported')
        if event['state']['CA' if event['kind'] == 'stroke' else 'ca'] == 0:
            continue
        if rectangular_clip:
            require(_clip_relation(event['state']['clip_box'], box, budget) == 'inside',
                    'contributing_path_clip_unproved')
        items.append({'kind':event['kind'], 'order':event['order'], 'box':box,
                      'color':mono(event['state']['fill_space'], event['state']['fill_color'])})
    if not rectangular_clip:
        budget = _Budget(maximum)
    index = _FootprintIndex([item['box'] for item in items], budget)
    # Fill edges cannot certify table rules, and no fill may cover a real grid
    # footprint, even if a text extractor still returns that invisible line.
    for item in items:
        if item['kind'] == 'fill':
            for hit in index.touching(item['box']):
                require(items[hit]['kind'] != 'stroke', 'fill_grid_footprint_intersection')
    states, reasons, backgrounds = [], [], []
    for ordinal, (char, box, order, mode) in enumerate(zip(chars, ink_boxes, expected_orders, modes)):
        if mode == 3:
            states.append('no_effect'); reasons.append('nonpainting_render_mode_three'); backgrounds.append(None)
            continue
        if events[order-1]['state']['ca'] == 0:
            states.append('no_effect'); reasons.append('zero_nonstroking_alpha'); backgrounds.append(None)
            continue
        if rectangular_clip and clip_states[ordinal] != 'inside':
            outside = clip_states[ordinal] == 'outside'
            states.append('no_effect' if outside else 'unknown')
            reasons.append('full_ink_outside_clip' if outside else 'partial_ink_clip')
            backgrounds.append(None)
            continue
        if box is None:
            states.append('spacing'); reasons.append(None); backgrounds.append(None)
            continue
        # Complete each bounded query before any positive result can escape.
        nearby = [items[hit] for hit in index.touching(box)]
        earlier_fills = [item for item in nearby if item['kind'] == 'fill' and item['order'] < order]
        later = [item for item in nearby if item['order'] > order or
                 (item['kind'] == 'glyph' and item['ordinal'] > ordinal)]
        covers = [item for item in later if item['kind'] == 'fill' and _contains(item['box'], box)]
        if covers:
            cover = max(covers, key=lambda item:item['order'])
            states.append('occluded'); reasons.append('later_full_opaque_rectangle')
            backgrounds.append({'cover_order':cover['order'], 'cover_box':cover['box']})
            continue
        background = max(earlier_fills, key=lambda item:item['order']) if earlier_fills else None
        start = background['order'] if background else 0
        color = background['color'] if background else 1
        evidence = {'source':'source_rectangle' if background else display_domain,
                    'paint_order':start, 'color':color,
                    'box':background['box'] if background else None}
        backgrounds.append(evidence)
        if background and not _contains(background['box'], box):
            states.append('unknown'); reasons.append('partial_background_rectangle'); continue
        earlier = [item for item in nearby if item['order'] > start and
                   (item['order'] < order or (item['kind'] == 'glyph' and item['ordinal'] < ordinal))]
        interfering = [item for item in earlier if item['kind'] == 'stroke' or
                       (item['kind'] == 'glyph' and states[item['ordinal']] != 'no_effect')]
        if interfering:
            states.append('unknown'); reasons.append('intervening_background_paint'); continue
        if mono(char['ncs'], char['non_stroking_color']) == color:
            states.append('no_effect'); reasons.append('same_color_uniform_background'); continue
        if later:
            states.append('unknown'); reasons.append('later_intersecting_paint'); continue
        states.append('displayed'); reasons.append(None)
    result = {'status':'observed', 'reason':None, 'policy_version':POLICY_VERSION,
        'display_domain':display_domain, 'chars':chars, 'render_modes':modes,
        'visibility_flags':flags, 'paint':paint, 'glyph_states':states,
        'glyph_reasons':reasons, 'glyph_ink_boxes':ink_boxes, 'glyph_backgrounds':backgrounds,
        'displayed_indices':[i for i,state in enumerate(states) if state in {'displayed','spacing'} and modes[i] != 3],
        'no_effect_indices':[i for i,state in enumerate(states) if state == 'no_effect'],
        'occluded_indices':[i for i,state in enumerate(states) if state == 'occluded'],
        'unknown_indices':[i for i,state in enumerate(states) if state == 'unknown'],
        'displayed_stroke_indices':displayed_stroke_indices,
        'source_program':{'gs_events':gs_events, 'events':events, 'content_token_count':tokens,
            'raw_ascii_text':raw.decode('ascii'), 'grid_basis':'source_bound_opaque_stroke_objects_only'},
        'budgets':{'maximum_comparisons':maximum, 'comparisons_used':budget.used,
            'comparison_counts':dict(budget.counts), 'character_count':len(chars),
            'paint_event_count':len(events), 'path_event_count':len(paths),
            'fill_count':sum(event['kind']=='fill' for event in paths),
            'graphics_state_event_count':len(gs_events), 'graphics_state_resource_count':len(graphics_states)}}
    if rectangular_clip:
        result['glyph_clip_states'] = clip_states
        result['source_program']['clipping_events'] = clipping_events
        result['source_program']['clipping_numeric_domain'] = 'float32_exact_source_rectangle/v1'
        result['budgets'].update(clipping_event_count=len(clipping_events),
            clipping_path_point_count=sum(_clipping_path_weight(event['raw_path']) for event in clipping_events))
    return result


def inspect_opacity_native_page(page, *, max_comparisons=200_000,
                                display_domain=DISPLAY_DOMAIN):
    """Complete per-original-ordinal proof, or unknown without partial facts."""
    try:
        return _inspect(page, max_comparisons, display_domain)
    except _Unsupported as error:
        return {'status': 'unknown', 'reason': str(error)}
    except Exception as error:
        reason = str(error)
        return {'status': 'unknown', 'reason': reason if reason.startswith('source_visibility_')
                else 'native_opacity_inventory_unavailable'}
