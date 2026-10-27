"""Fresh source CID/font bindings whose LTChar projection equals pdfplumber.

This observes provenance, not glyph semantics, visible paint, an ID scope or a
financial fact. A consumer must compare these actual CID/font pointers with its
independent source program; equal text/count/ordinal alone is insufficient.
"""
from __future__ import annotations

import hashlib
import math


MAX_CHARACTERS = 25_000
MAX_TOKENS = 100_000
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_FONTS = 64
MAX_FORMS = 128
MAX_FORM_DEPTH = 16
MAX_PATH_POINTS = 100_000
MAX_IMAGES = 2_000
MAX_RESOURCE_NODES = 20_000


class SourceCharacterError(ValueError):
    pass


def observe_house_id_source_characters(page, *, max_characters=MAX_CHARACTERS,
                                       max_tokens=MAX_TOKENS) -> dict:
    """Bind each projected character to the source font object and raw CID.

    ``matrix`` is the unmodified pdfminer render_char matrix, not a MuPDF
    binary32 placement. Generation is that of the effective source xref object;
    callers must also compare it with the independently parsed resource pointer.
    Direct/aliased font dictionaries and reordered layout projections remain
    unknown rather than being claimed from a font name or text match.
    """
    base = {"status": "unknown", "reason": None, "chars": [], "bindings": [],
            "scope_proven": False, "paint_proven": False}
    try:
        from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager, PDFContentParser
        from pdfminer.layout import LTChar, LTContainer
        from pdfminer.pdftypes import PDFObjRef, PDFStream
        from pdfminer.psparser import PSEOF
        from pdfplumber.page import PDFPageAggregatorWithMarkedContent
        from .house_id_source_fonts import _resolve, _stream, _unicode_map, _name

        if (type(max_characters) is not int or not 0 <= max_characters <= MAX_CHARACTERS or
                type(max_tokens) is not int or not 0 <= max_tokens <= MAX_TOKENS):
            raise SourceCharacterError("source_character_budget_invalid")
        doc = page.pdf.doc
        if len(doc.xrefs) > 32:
            raise SourceCharacterError("source_xref_inventory_over_budget")
        counts = {"characters": 0, "tokens": 0, "fonts": 0, "forms": 0,
                  "content_bytes": 0, "path_points": 0, "images": 0,
                  "resource_nodes": 0}

        def bounded_tree(value, active=None, depth=0):
            """Close resource references before pdfminer's unbounded resolve1."""
            counts["resource_nodes"] += 1
            if counts["resource_nodes"] > MAX_RESOURCE_NODES or depth > 16:
                raise SourceCharacterError("source_resource_tree_over_budget")
            value = _resolve(value)
            if isinstance(value, PDFStream):
                return  # Stream bytes and invoked Form resources have separate budgets.
            if not isinstance(value, (dict, list, tuple)):
                return
            active = set() if active is None else active
            if id(value) in active:
                raise SourceCharacterError("source_resource_tree_cycle")
            active.add(id(value))
            values = value.values() if isinstance(value, dict) else value
            for child in values:
                bounded_tree(child, active, depth + 1)
            active.remove(id(value))

        def bounded_widths(spec):
            # get_widths expands range declarations before any character is drawn.
            widths = _resolve(spec.get("W", []))
            if not isinstance(widths, list) or len(widths) > 65536:
                raise SourceCharacterError("source_width_inventory_over_budget")
            index, expanded = 0, 0
            while index < len(widths):
                first = _resolve(widths[index]); index += 1
                if type(first) is not int or not 0 <= first <= 65535 or index >= len(widths):
                    raise SourceCharacterError("source_width_declaration_invalid")
                following = _resolve(widths[index]); index += 1
                if isinstance(following, list):
                    expanded += len(following)
                    if first + len(following) > 65536:
                        raise SourceCharacterError("source_width_inventory_over_budget")
                else:
                    if (type(following) is not int or not first <= following <= 65535
                            or index >= len(widths)):
                        raise SourceCharacterError("source_width_declaration_invalid")
                    expanded += following - first + 1
                    index += 1
                if expanded > 65536:
                    raise SourceCharacterError("source_width_inventory_over_budget")

        def generation(objid):
            if type(objid) is not int or objid <= 0:
                raise SourceCharacterError("indirect_font_source_required")
            for xref in doc.xrefs:
                try:
                    _, _, gen = xref.get_pos(objid)
                except KeyError:
                    continue
                if type(gen) is not int or not 0 <= gen <= 65535:
                    raise SourceCharacterError("source_font_generation_unproved")
                return gen
            raise SourceCharacterError("source_font_xref_missing")

        def materialize(stream, limit):
            data = _stream(stream, limit)
            if stream.data is None:
                # Normal decoded-stream memoization only, no PDF/source write.
                stream.data = data
            return data

        class Resources(PDFResourceManager):
            def __init__(self):
                super().__init__(caching=True)
                self.sources = {}
                self.loading = 0

            def get_font(self, objid, spec):
                self.loading += 1
                try:
                    if self.loading > 4:
                        raise SourceCharacterError("source_font_recursion_over_budget")
                    if not isinstance(spec, dict):
                        raise SourceCharacterError("source_font_dictionary_invalid")
                    bounded_tree(spec)
                    subtype = _name(spec.get("Subtype"))
                    if subtype not in {"Type0", "CIDFontType2", "Type1", "TrueType"}:
                        raise SourceCharacterError("source_font_subtype_unsupported")
                    if subtype in {"Type0", "CIDFontType2"} and _name(spec.get("Encoding")) != "Identity-H":
                        raise SourceCharacterError("source_font_encoding_unsupported")
                    if subtype == "CIDFontType2":
                        bounded_widths(spec)
                    if "ToUnicode" in spec:
                        stream = _resolve(spec["ToUnicode"])
                        data = materialize(stream, 64 * 1024)
                        _unicode_map(data)
                    encoding = _resolve(spec.get("Encoding"))
                    if isinstance(encoding, dict):
                        difference = _resolve(encoding.get("Differences", []))
                        if not isinstance(difference, list) or len(difference) > 512:
                            raise SourceCharacterError("source_encoding_inventory_over_budget")
                    if subtype == "Type0":
                        children = _resolve(spec.get("DescendantFonts"))
                        if not isinstance(children, list) or len(children) != 1:
                            raise SourceCharacterError("source_descendant_font_unproved")
                        _resolve(children[0])  # Bounded before pdfminer's Type0 recursion.
                    binding = None
                    if objid is not None:
                        counts["fonts"] += 1
                        if counts["fonts"] > MAX_FONTS:
                            raise SourceCharacterError("source_font_inventory_over_budget")
                        if doc.getobj(objid) is not spec:
                            raise SourceCharacterError("source_font_dictionary_alias_unproved")
                        binding = {"source_object_id": objid, "source_generation": generation(objid)}
                        source = spec
                        if subtype == "Type0":
                            source = _resolve(_resolve(spec["DescendantFonts"])[0])
                        descriptor = _resolve(source.get("FontDescriptor", {}))
                        if not isinstance(descriptor, dict):
                            raise SourceCharacterError("source_font_descriptor_invalid")
                        if "FontFile2" in descriptor:
                            program = materialize(_resolve(descriptor["FontFile2"]), MAX_CONTENT_BYTES)
                            binding["font_file_sha256"] = hashlib.sha256(program).hexdigest()
                        if "ToUnicode" in spec:
                            binding["ToUnicode_sha256"] = hashlib.sha256(data).hexdigest()
                    font = super().get_font(objid, spec)
                    if binding is not None:
                        previous = self.sources.setdefault(id(font), [])
                        if binding not in previous:
                            previous.append(binding)
                    return font
                finally:
                    self.loading -= 1

        manager = Resources()

        class BoundedParser(PDFContentParser):
            def nexttoken(self):
                counts["tokens"] += 1
                if counts["tokens"] > max_tokens:
                    raise SourceCharacterError("source_character_token_budget_exceeded")
                return super().nexttoken()

        class Interpreter(PDFPageInterpreter):
            depth = 0

            def init_resources(self, resources):
                resources = _resolve(resources)
                if not isinstance(resources, dict) or len(resources) > 32:
                    raise SourceCharacterError("source_resource_inventory_invalid")
                for key in ("Font", "XObject", "ColorSpace", "ExtGState"):
                    mapping = _resolve(resources.get(key, {}))
                    if not isinstance(mapping, dict) or len(mapping) > 64:
                        raise SourceCharacterError("source_resource_inventory_over_budget")
                    for value in mapping.values():
                        bounded_tree(value)
                return super().init_resources(resources)

            def subinterp(self):
                counts["forms"] += 1
                if self.depth >= MAX_FORM_DEPTH or counts["forms"] > MAX_FORMS:
                    raise SourceCharacterError("source_Form_budget_exceeded")
                child = super().subinterp()
                child.depth = self.depth + 1
                return child

            def execute(self, streams):
                if not isinstance(streams, (list, tuple)) or len(streams) > 128:
                    raise SourceCharacterError("source_content_inventory_over_budget")
                checked = []
                for reference in streams:
                    stream = _resolve(reference)
                    if not isinstance(stream, PDFStream) or stream.objid is None:
                        raise SourceCharacterError("indirect_source_content_required")
                    if stream.objid in self.parent_stream_ids:
                        raise SourceCharacterError("source_content_cycle")
                    data = materialize(stream, MAX_CONTENT_BYTES)
                    counts["content_bytes"] += len(data)
                    if counts["content_bytes"] > MAX_CONTENT_BYTES:
                        raise SourceCharacterError("source_content_byte_budget_exceeded")
                    checked.append(stream)
                if checked:
                    parser = BoundedParser(checked)
                    while True:
                        try:
                            parser.nextobject()
                        except PSEOF:
                            break
                return super().execute(checked)

        class Aggregator(PDFPageAggregatorWithMarkedContent):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.by_object = {}

            def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):
                counts["characters"] += 1
                if counts["characters"] > max_characters:
                    raise SourceCharacterError("source_character_count_budget_exceeded")
                sources = manager.sources.get(id(font), [])
                if len(sources) != 1:
                    raise SourceCharacterError("source_character_font_pointer_unproved")
                values = (*matrix, fontsize, scaling, rise)
                if (len(matrix) != 6 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                        or type(cid) is not int or not 0 <= cid <= 65535):
                    raise SourceCharacterError("source_character_numeric_domain_unsupported")
                before = len(self.cur_item._objs)
                advance = super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate)
                if len(self.cur_item._objs) != before + 1 or not isinstance(self.cur_item._objs[-1], LTChar):
                    raise SourceCharacterError("source_LTChar_creation_unproved")
                char = self.cur_item._objs[-1]
                if id(char) in self.by_object:
                    raise SourceCharacterError("source_LTChar_reused")
                self.by_object[id(char)] = {**sources[0], "render_ordinal": counts["characters"] - 1,
                    "cid": cid, "matrix": list(matrix), "fontsize": fontsize, "scaling": scaling,
                    "rise": rise, "source_text": char.get_text()}
                return advance

            def render_image(self, *args, **kwargs):
                counts["images"] += 1
                if counts["images"] > MAX_IMAGES:
                    raise SourceCharacterError("source_image_inventory_over_budget")
                return super().render_image(*args, **kwargs)

            def paint_path(self, graphicstate, stroke, fill, evenodd, path):
                for item in path:
                    counts["path_points"] += max(1, (len(item) - 1) // 2)
                    if counts["path_points"] > MAX_PATH_POINTS:
                        raise SourceCharacterError("source_path_point_budget_exceeded")
                return super().paint_path(graphicstate, stroke, fill, evenodd, path)

        device = Aggregator(manager, pageno=page.page_number, laparams=page.pdf.laparams)
        Interpreter(manager, device).process_page(page.page_obj)

        def walk(items, depth=0):
            if depth > 64:
                raise SourceCharacterError("source_layout_depth_over_budget")
            for item in items:
                if isinstance(item, LTChar):
                    yield item
                elif isinstance(item, LTContainer):
                    yield from walk(item._objs, depth + 1)

        layout_chars = list(walk(device.get_result()._objs))
        if len(layout_chars) != counts["characters"]:
            raise SourceCharacterError("source_character_inventory_not_closed")
        chars, bindings = [], []
        for index, char in enumerate(layout_chars):
            source = device.by_object.get(id(char))
            if source is None or source["render_ordinal"] != index:
                raise SourceCharacterError("source_projection_reorders_glyphs")
            projected = page.process_object(char)
            chars.append(projected)
            bindings.append({**source, "char_index": index, "text": projected["text"]})
        if chars != page.chars:
            raise SourceCharacterError("source_character_projection_differs")
        return {**base, "status": "observed", "chars": chars, "bindings": bindings,
                "character_count": len(chars), "budgets": counts}
    except (SourceCharacterError, ValueError) as error:
        return {**base, "reason": str(error)}
    except Exception:
        return {**base, "reason": "source_character_observation_invalid"}
