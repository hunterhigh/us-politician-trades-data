"""Source glyph/render-state observation without routing or qualification.

Mechanically shared from the House observer; projection, flags, paint order
and exception values remain the same. Consumers choose their own profile.
"""

def observe_source_characters(page, *, max_characters=25_000, max_tokens=100_000):
    from pdfplumber.page import PDFPageAggregatorWithMarkedContent
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFContentParser
    from pdfminer.layout import LTChar, LTContainer, LTFigure
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import literal_name, PSEOF

    class Interpreter(PDFPageInterpreter):
        def init_state(self, *args, **kwargs):
            super().init_state(*args, **kwargs)
            self.visibility_flags = set(getattr(self, 'inherited_visibility_flags', ()))
            self.visibility_stack = []

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

        def do_gs(self, name):
            state = resolve1(resolve1(self.resources.get('ExtGState', {})).get(literal_name(name)))
            if (not isinstance(state, dict) or any(
                    key in state and state[key] != 1 for key in ('ca', 'CA')) or
                    any(key in state for key in ('SMask', 'TR', 'TR2', 'Font')) or
                    ('BM' in state and literal_name(state['BM']) != 'Normal')):
                self.visibility_flags.add('source_visibility_active_graphics_state_unsupported')
            super().do_gs(name)

        def do_W(self):
            self.visibility_flags.add('source_visibility_active_clipping_unsupported')
            super().do_W()

        def do_W_a(self):
            self.visibility_flags.add('source_visibility_active_clipping_unsupported')
            super().do_W_a()

        def do_TJ(self, seq):
            self.device.visibility_flags = set(self.visibility_flags)
            super().do_TJ(seq)
            if self.textstate.render >= 4:
                self.visibility_flags.add('source_visibility_active_clipping_unsupported')

        def do_Do(self, name):
            self.device.visibility_flags = set(self.visibility_flags)
            super().do_Do(name)

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

        def render_string(self, textstate, *args, **kwargs):
            self.current_mode = textstate.render
            self.paint_order += 1
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
            super().render_image(*args, **kwargs)
            flags = set(self.visibility_flags)
            if any(isinstance(item, LTFigure) for item in self._stack):
                flags.add('source_visibility_image_form_unsupported')
            self.image_events.append((self.cur_item._objs[-1], self.paint_order, sorted(flags)))

        def paint_path(self, *args, **kwargs):
            self.paint_order += 1
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
    return records, [char.source_render_mode for char in chars], flags, paint

