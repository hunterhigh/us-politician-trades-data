"""Source glyph/render-state observation without routing or qualification.

Mechanically shared from the House observer; projection, flags, paint order
and exception values remain the same. Consumers choose their own profile.
"""

MAX_CLIPPING_PATH_POINTS = 100_000


def observe_source_characters(page, *, max_characters=25_000, max_tokens=100_000,
                              include_path_events=False, max_paint_events=30_000,
                              include_graphics_state_events=False,
                              max_graphics_state_events=5_000,
                              include_clipping_events=False, max_clipping_events=5_000):
    from pdfplumber.page import PDFPageAggregatorWithMarkedContent
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFContentParser
    from pdfminer.layout import LTChar, LTContainer, LTFigure
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import literal_name, PSEOF

    if (include_path_events or include_graphics_state_events or include_clipping_events) and (type(max_paint_events) is not int or max_paint_events < 0):
        raise ValueError('source_visibility_paint_budget_invalid')
    if include_graphics_state_events and (type(max_graphics_state_events) is not int or
                                          max_graphics_state_events < 0):
        raise ValueError('source_visibility_graphics_state_budget_invalid')
    if include_clipping_events and (type(max_clipping_events) is not int or max_clipping_events < 0):
        raise ValueError('source_visibility_clipping_budget_invalid')

    class Interpreter(PDFPageInterpreter):
        def init_state(self, *args, **kwargs):
            super().init_state(*args, **kwargs)
            self.visibility_flags = set(getattr(self, 'inherited_visibility_flags', ()))
            self.visibility_stack = []
            self.pending_clipping_operator = None
            if include_path_events:
                self.device.path_visibility_flags = self.visibility_flags

        def subinterp(self):
            nested = super().subinterp()
            nested.inherited_visibility_flags = set(self.visibility_flags)
            return nested

        def execute(self, streams):
            # A valid PDF page may omit Contents entirely. Preserve the
            # interpreter's empty-program behavior rather than treating it
            # as an unavailable source inventory.
            if not streams:
                return super().execute(streams)
            parser = PDFContentParser(streams)
            while True:
                try:
                    parser.nextobject()
                except PSEOF:
                    break
                self.device.visibility_token_count += 1
                if self.device.visibility_token_count > max_tokens:
                    raise ValueError('source_visibility_token_budget_exceeded')
            super().execute(streams)

        def do_q(self):
            self.visibility_stack.append(set(self.visibility_flags))
            super().do_q()

        def do_Q(self):
            if self.visibility_stack:
                self.visibility_flags = self.visibility_stack.pop()
            super().do_Q()
            if include_path_events:
                self.device.path_visibility_flags = self.visibility_flags

        def do_gs(self, name):
            if include_graphics_state_events:
                resources = _resolve_graphics_state(self.resources.get('ExtGState', {}))
                if not isinstance(resources, dict):
                    raise ValueError('source_visibility_graphics_state_resources_unsupported')
                state = _resolve_graphics_state(resources.get(literal_name(name)))
            else:
                state = resolve1(resolve1(self.resources.get('ExtGState', {})).get(literal_name(name)))
            if include_graphics_state_events:
                if len(self.device.graphics_state_events) >= max_graphics_state_events:
                    raise ValueError('source_visibility_graphics_state_budget_exceeded')
                self.device.graphics_state_events.append({
                    'name': literal_name(name),
                    'state': _graphics_state_value(state),
                    'before_paint_order': self.device.paint_order + 1})
            if (not isinstance(state, dict) or any(
                    key in state and state[key] != 1 for key in ('ca', 'CA')) or
                    any(key in state for key in ('SMask', 'TR', 'TR2', 'Font')) or
                    ('BM' in state and literal_name(state['BM']) != 'Normal')):
                self.visibility_flags.add('source_visibility_active_graphics_state_unsupported')
            super().do_gs(name)

        def record_clipping(self, phase, operator):
            from copy import deepcopy
            if len(self.device.clipping_events) >= max_clipping_events:
                raise ValueError('source_visibility_clipping_event_budget_exceeded')
            size = _clipping_path_weight(self.curpath,
                maximum=MAX_CLIPPING_PATH_POINTS - self.device.clipping_path_points)
            if self.device.clipping_path_points + size > MAX_CLIPPING_PATH_POINTS:
                raise ValueError('source_visibility_clipping_path_budget_exceeded')
            self.device.clipping_path_points += size
            self.device.clipping_events.append({
                'phase': phase, 'operator': operator, 'raw_path': deepcopy(self.curpath),
                'before_paint_order': self.device.paint_order + 1})

        def commit_clipping(self, operator):
            if include_clipping_events and self.pending_clipping_operator is not None:
                self.record_clipping('commit', operator)
                self.pending_clipping_operator = None

        def do_W(self):
            if include_clipping_events:
                self.record_clipping('request', 'W')
                self.pending_clipping_operator = 'W'
            self.visibility_flags.add('source_visibility_active_clipping_unsupported')
            super().do_W()

        def do_W_a(self):
            if include_clipping_events:
                self.record_clipping('request', 'W*')
                self.pending_clipping_operator = 'W*'
            self.visibility_flags.add('source_visibility_active_clipping_unsupported')
            super().do_W_a()

        def do_n(self):
            self.commit_clipping('n')
            return super().do_n()

        def do_S(self):
            self.commit_clipping('S')
            return super().do_S()

        def do_s(self):
            self.commit_clipping('s')
            return super().do_s()

        def do_f(self):
            self.commit_clipping('f')
            return super().do_f()

        def do_F(self):
            self.commit_clipping('F')
            return super().do_F()

        def do_f_a(self):
            self.commit_clipping('f*')
            return super().do_f_a()

        def do_B(self):
            self.commit_clipping('B')
            return super().do_B()

        def do_B_a(self):
            self.commit_clipping('B*')
            return super().do_B_a()

        def do_b(self):
            self.commit_clipping('b')
            return super().do_b()

        def do_b_a(self):
            self.commit_clipping('b*')
            return super().do_b_a()

        def do_TJ(self, seq):
            self.device.visibility_flags = set(self.visibility_flags)
            super().do_TJ(seq)
            if self.textstate.render >= 4:
                self.visibility_flags.add('source_visibility_active_clipping_unsupported')

        def do_Do(self, name):
            self.device.visibility_flags = set(self.visibility_flags)
            try:
                super().do_Do(name)
            finally:
                if include_path_events:
                    self.device.path_visibility_flags = self.visibility_flags

        def do_EI(self, obj):
            self.device.visibility_flags = set(self.visibility_flags)
            super().do_EI(obj)

    class Aggregator(PDFPageAggregatorWithMarkedContent):
        current_mode = None
        count = 0
        visibility_token_count = 0
        visibility_flags = set()
        paint_order = 0
        text_paint_order = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.image_events = []
            self.path_events = []
            self.graphics_state_events = []
            self.clipping_events = []
            self.clipping_path_points = 0

        def check_paint_budget(self):
            if (include_path_events or include_graphics_state_events or include_clipping_events) and self.paint_order > max_paint_events:
                raise ValueError('source_visibility_paint_event_budget_exceeded')

        def render_string(self, textstate, *args, **kwargs):
            self.current_mode = textstate.render
            self.paint_order += 1
            self.check_paint_budget()
            self.text_paint_order = self.paint_order
            return super().render_string(textstate, *args, **kwargs)

        def render_char(self, *args, **kwargs):
            self.count += 1
            if self.count > max_characters:
                raise ValueError('source_visibility_character_budget_exceeded')
            advance = super().render_char(*args, **kwargs)
            self.cur_item._objs[-1].source_render_mode = self.current_mode
            self.cur_item._objs[-1].source_paint_order = self.text_paint_order
            flags = set(self.visibility_flags)
            if isinstance(self.cur_item, LTFigure):
                flags.add('source_visibility_form_text_unsupported')
            self.cur_item._objs[-1].source_visibility_flags = sorted(flags)
            return advance

        def render_image(self, *args, **kwargs):
            self.paint_order += 1
            self.check_paint_budget()
            super().render_image(*args, **kwargs)
            flags = set(self.visibility_flags)
            if any(isinstance(item, LTFigure) for item in self._stack):
                flags.add('source_visibility_image_form_unsupported')
            self.image_events.append((self.cur_item._objs[-1], self.paint_order, sorted(flags)))

        def paint_path(self, *args, **kwargs):
            self.paint_order += 1
            self.check_paint_budget()
            if include_path_events:
                from copy import deepcopy
                state, stroke, fill, evenodd, path = args
                if not hasattr(state, 'ncs') or not hasattr(state, 'scs'):
                    raise ValueError('source_visibility_path_colorspace_unavailable')
                self.path_events.append({'paint_order': self.paint_order,
                    'raw_path': deepcopy(path), 'stroke': stroke, 'fill': fill,
                    'evenodd': evenodd, 'fill_colorspace': state.ncs.name,
                    'fill_color': deepcopy(state.ncolor),
                    'stroke_colorspace': state.scs.name,
                    'stroke_color': deepcopy(state.scolor),
                    'source_linewidth': state.linewidth,
                    'visibility_flags': sorted(getattr(
                        self, 'path_visibility_flags', self.visibility_flags))})
            return super().paint_path(*args, **kwargs)

    def walk(items):
        for item in items:
            if isinstance(item, LTChar):
                yield item
            elif isinstance(item, LTContainer):
                yield from walk(item._objs)

    device = Aggregator(page.pdf.rsrcmgr, pageno=page.page_number, laparams=page.pdf.laparams)
    Interpreter(page.pdf.rsrcmgr, device).process_page(page.page_obj)
    chars = list(walk(device.get_result()._objs))
    records = [page.process_object(char) for char in chars]
    if records != page.chars:
        raise ValueError('source_visibility_character_projection_differs')
    flags = [char.source_visibility_flags for char in chars]
    for index, char in enumerate(records):
        if any(max(char['x0'], ann['x0']) < min(char['x1'], ann['x1']) and
               max(char['top'], ann['top']) < min(char['bottom'], ann['bottom'])
               for ann in page.annots):
            flags[index] = sorted(set(flags[index] + ['source_visibility_annotation_over_text_unsupported']))
    paint = {'character_orders': [char.source_paint_order for char in chars],
        'paint_event_count': device.paint_order,
        'images': [{'paint_order': order,
            'box': [page.process_object(image)[key] for key in ('x0', 'top', 'x1', 'bottom')],
            'visibility_flags': image_flags} for image, order, image_flags in device.image_events]}
    if include_path_events:
        paint['path_events'] = device.path_events
    if include_graphics_state_events:
        paint['graphics_state_events'] = device.graphics_state_events
    if include_clipping_events:
        paint['clipping_events'] = device.clipping_events
    return records, [char.source_render_mode for char in chars], flags, paint




def _clipping_path_weight(path, *, maximum=None):
    """Count original control points, stopping before an oversized copy.

    A close command has weight one; a cubic command carries three points.
    This also bounds unsupported source paths observed before profile rejection.
    """
    weight = 0
    for component in path:
        weight += max(1, (len(component) - 1) // 2)
        if maximum is not None and weight > maximum:
            break
    return weight


def _graphics_state_value(value):
    """Bounded typed source values, without traversing referenced object graphs.

    References inside a state remain references. The closed profile separately
    resolves only its supported numeric/name operands; this is observation only.
    """
    from pdfminer.pdftypes import PDFObjRef
    from pdfminer.psparser import PSLiteral
    nodes = 0

    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 128 or depth > 4:
            raise ValueError('source_visibility_graphics_state_value_budget_exceeded')
        if isinstance(item, PDFObjRef):
            return {'pdf_object_reference': item.objid}
        if isinstance(item, PSLiteral):
            if type(item.name) is not str or len(item.name) > 1024:
                raise ValueError('source_visibility_graphics_state_value_budget_exceeded')
            return {'pdf_literal': item.name}
        if type(item) in (int, float, bool, str) or item is None:
            if isinstance(item, str) and len(item) > 1024:
                raise ValueError('source_visibility_graphics_state_value_budget_exceeded')
            return item
        if isinstance(item, bytes):
            if len(item) > 1024:
                raise ValueError('source_visibility_graphics_state_value_budget_exceeded')
            return {'pdf_bytes_hex': item.hex()}
        if isinstance(item, (tuple, list)):
            if len(item) > 128:
                raise ValueError('source_visibility_graphics_state_value_budget_exceeded')
            return [visit(part, depth + 1) for part in item]
        if isinstance(item, dict):
            if len(item) > 64 or any(type(key) is not str or len(key) > 128 for key in item):
                raise ValueError('source_visibility_graphics_state_value_budget_exceeded')
            return {key: visit(part, depth + 1) for key, part in item.items()}
        raise ValueError('source_visibility_graphics_state_value_unsupported')

    return visit(value)


def _resolve_graphics_state(value, *, max_hops=16):
    """Resolve one used gs value with a bounded, cycle-checked reference chain.

    Legacy observations intentionally keep pdfminer's original resolve1 path.
    This bound belongs only to the explicit graphics-state inventory/profile.
    """
    from pdfminer.pdftypes import PDFObjRef
    visited = set()
    while isinstance(value, PDFObjRef):
        identity = (id(value.doc), value.objid)
        if identity in visited or len(visited) >= max_hops:
            raise ValueError('source_visibility_graphics_state_reference_budget_exceeded')
        visited.add(identity)
        value = value.resolve()
    return value
