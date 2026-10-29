"""Bounded source paint evidence for the pinned horizontal native-CID profile.

This is an observation, not an ID reader or a publication authorization. The
font callback receives the actual embedded bytes; it supplies unhinted source
controls, never a saved glyph box. Digit semantics belong to a separate reader.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction as F
import hashlib
import io
import math
import re
import zlib

from .house_id_source_numbers import (
    Float32Interval, GlyphPosition, HorizontalTextState, SourceNumberError, SourceNumbers,
)


PROFILE = "house-native-cid-paint-mupdf-1.28.2/v1"


@dataclass(frozen=True)
class GlyphOutline:
    units_per_em: int
    points: tuple[tuple[int, int], ...]
    simple: bool = True


@dataclass(frozen=True)
class FontSource:
    """Runtime-only pointers/bytes for an independent same-source reader.

    Obtain the original opaque font specification from a fresh PDF document
    using object_id/generation. Never serialize this object into an audit.
    """
    font_ref: str
    source_object_id: int
    source_generation: int
    program_bytes: bytes
    to_unicode_bytes: bytes | None
    used_cids: tuple[int, ...]


class SourcePaintError(ValueError):
    pass


class _Unknown(SourcePaintError):
    pass


class _Name(str):
    pass


class _Number(str):
    pass


@dataclass(frozen=True)
class _Ref:
    object_id: int
    generation: int


_NUM = re.compile(rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_SPACE = b"\x00\t\n\f\r "
_ARITY = {"q": 0, "Q": 0, "cm": 6, "BT": 0, "ET": 0, "Tf": 2,
          "Tm": 6, "Td": 2, "Tj": 1, "m": 2, "l": 2, "c": 6,
          "re": 4, "h": 0, "W": 0, "W*": 0, "n": 0, "f": 0,
          "S": 0, "rg": 3, "RG": 3, "gs": 1, "Do": 1}


def _need(condition, reason):
    if not condition:
        raise _Unknown(reason)


class _Reader:
    """Closed PDF lexical subset; retains real versus integer literals."""

    def __init__(self, raw, charge):
        self.raw, self.index, self.charge = raw, 0, charge

    def space(self):
        while self.index < len(self.raw):
            c = self.raw[self.index:self.index + 1]
            if c in _SPACE:
                self.index += 1
            elif c == b"%":
                end = re.search(rb"[\r\n]", self.raw[self.index:])
                self.index = len(self.raw) if end is None else self.index + end.start() + 1
            else:
                break

    def token(self):
        self.space()
        if self.index == len(self.raw):
            return None
        self.charge("tokens")
        a = self.index
        raw = self.raw
        if raw[a:a + 2] in (b"<<", b">>"):
            self.index += 2
            return raw[a:a + 2]
        c = raw[a:a + 1]
        if c in (b"[", b"]"):
            self.index += 1
            return c
        if c == b"/":
            match = re.match(rb"/[^\s\x00\[\]<>()/%]+", raw[a:])
            _need(match is not None, "source_name_unsupported")
            self.index += len(match[0])
            name = match[0][1:]
            _need(re.search(rb"#(?![0-9a-fA-F]{2})", name) is None, "source_name_escape_invalid")
            name = re.sub(rb"#([0-9a-fA-F]{2})", lambda m: bytes([int(m[1], 16)]), name)
            _need(0 < len(name) <= 128 and all(32 < c < 127 for c in name), "source_name_unsupported")
            return _Name(name.decode("ascii"))
        if c == b"<":
            match = re.match(rb"<([0-9A-Fa-f\s]*)>", raw[a:])
            _need(match is not None, "source_hex_string_unsupported")
            text = re.sub(rb"\s", b"", match[1])
            _need(len(text) % 2 == 0, "source_hex_nibble_unsupported")
            self.index += len(match[0])
            return bytes.fromhex(text.decode("ascii"))
        if c == b"(":
            out = bytearray()
            self.index += 1
            while self.index < len(raw):
                c = raw[self.index:self.index + 1]
                self.index += 1
                if c == b")":
                    return bytes(out)
                _need(c != b"(", "nested_literal_string_unsupported")
                if c == b"\\":
                    _need(self.index < len(raw), "literal_string_unclosed")
                    c = raw[self.index:self.index + 1]
                    self.index += 1
                    _need(c in (b"(", b")", b"\\"), "literal_escape_unsupported")
                out.extend(c)
                _need(len(out) <= 65536, "literal_string_budget_exceeded")
            raise _Unknown("literal_string_unclosed")
        match = _NUM.match(raw[a:])
        if match:
            _need(len(match[0]) <= 128, "numeric_literal_budget_exceeded")
            self.index += len(match[0])
            _need(self.index == len(raw) or raw[self.index:self.index + 1] in _SPACE + b"/[]<>()%",
                  "numeric_token_unsupported")
            return _Number(match[0].decode("ascii"))
        match = re.match(rb"[A-Za-z][A-Za-z0-9*]*", raw[a:])
        _need(match is not None, "source_token_unsupported")
        self.index += len(match[0])
        return match[0].decode("ascii")

    def value(self, depth=0):
        _need(depth <= 12, "source_dictionary_depth_exceeded")
        token = self.token()
        if token == b"<<":
            result = {}
            while True:
                key = self.token()
                if key == b">>":
                    return result
                _need(isinstance(key, _Name) and key not in result, "source_dictionary_key_invalid")
                result[str(key)] = self.value(depth + 1)
                _need(len(result) <= 128, "source_dictionary_budget_exceeded")
        if token == b"[":
            result = []
            while True:
                saved = self.index
                if self.token() == b"]":
                    return result
                self.index = saved
                result.append(self.value(depth + 1))
                _need(len(result) <= 65536, "source_array_budget_exceeded")
        if isinstance(token, _Number):
            saved = self.index
            other = self.token()
            if (isinstance(other, _Number) and re.fullmatch(r"[0-9]+", token) and
                    re.fullmatch(r"[0-9]+", other) and self.token() == "R"):
                return _Ref(int(token), int(other))
            self.index = saved
            return token
        if isinstance(token, (_Name, bytes)):
            return token
        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        raise _Unknown("source_object_value_unsupported")


class _Objects:
    def __init__(self, raw, limits):
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser

        self.raw, self.limits, self.counts = raw, limits, {}
        self.cache, self.stream_cache = {}, {}
        _need(re.match(rb"%PDF-1\.[0-7](?:\r|\n)", raw) is not None, "PDF_header_unsupported")
        end = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*\Z", raw)
        _need(end is not None and raw[int(end[1]):].startswith(b"xref"),
              "classic_xref_required")
        self.document = PDFDocument(PDFParser(io.BytesIO(raw)))
        _need(len(self.document.xrefs) == 1, "multiple_xrefs_unsupported")
        for _ in self.document.xrefs[0].get_objids():
            self.charge("objects")
        trailer = self.document.xrefs[0].trailer
        _need(not set(trailer) - {"Root", "Size", "Info", "ID", "Encrypt"}, "trailer_state_unsupported")
        self.encryption = None
        if "Encrypt" in trailer:
            from pdfminer.pdftypes import resolve1
            security = resolve1(trailer["Encrypt"])
            _need(isinstance(security, dict) and not set(security) - {"Filter", "V", "R", "Length", "P", "O", "U", "EncryptMetadata"} and
                  {"Filter", "V", "R", "Length", "P", "O", "U"} <= set(security) and
                  security.get("EncryptMetadata", True) is True and
                  getattr(security["Filter"], "name", None) == "Standard" and security["V"] == 2 and
                  security["R"] == 3 and security["Length"] == 128 and
                  isinstance(security["O"], bytes) and len(security["O"]) == 32 and
                  isinstance(security["U"], bytes) and len(security["U"]) == 32 and self.document.decipher is not None,
                  "source_encryption_unsupported")
            self.encryption = {"handler": "Standard", "V": 2, "R": 3, "bits": 128, "password": "empty-user"}
        root = trailer.get("Root")
        _need(hasattr(root, "objid"), "catalog_reference_invalid")
        self.root = _Ref(root.objid, 0)

    def charge(self, kind, amount=1):
        self.counts[kind] = self.counts.get(kind, 0) + amount
        _need(self.counts[kind] <= self.limits.get(kind, 100000), kind + "_budget_exceeded")

    def object(self, ref):
        _need(isinstance(ref, _Ref), "indirect_source_reference_required")
        if ref in self.cache:
            return self.cache[ref][0]
        try:
            kind, offset, generation = self.document.xrefs[0].get_pos(ref.object_id)
        except KeyError as exc:
            raise _Unknown("source_reference_missing") from exc
        _need(kind is None and generation == ref.generation, "source_object_version_unsupported")
        head = re.match(rb"(\d+)\s+(\d+)\s+obj\b", self.raw[offset:offset + 80])
        _need(head is not None and (int(head[1]), int(head[2])) == (ref.object_id, ref.generation),
              "source_object_header_mismatch")
        reader = _Reader(self.raw[offset + head.end():offset + head.end() + self.limits["object_bytes"]], self.charge)
        value = reader.value()
        reader.space()
        tail = reader.raw[reader.index:]
        _need(tail.startswith(b"stream") or tail.startswith(b"endobj"), "source_object_tail_unsupported")
        if self.encryption is not None:
            def decrypt(item):
                if isinstance(item, bytes):
                    return self.document.decipher(ref.object_id, ref.generation, item)
                if isinstance(item, list):
                    return [decrypt(v) for v in item]
                if isinstance(item, dict):
                    return {k: decrypt(v) for k, v in item.items()}
                return item
            value = decrypt(value)
        self.cache[ref] = (value, offset + head.end() + reader.index)
        return value

    def resolve(self, value):
        return self.object(value) if isinstance(value, _Ref) else value

    def stream(self, ref):
        if ref in self.stream_cache:
            return self.stream_cache[ref]
        metadata = self.object(ref)
        _need(isinstance(metadata, dict), "source_stream_dictionary_required")
        start = self.cache[ref][1]
        marker = re.match(rb"stream(?:\r\n|\n|\r)", self.raw[start:start + 10])
        _need(marker is not None, "source_stream_marker_invalid")
        length = _int(self.resolve(metadata.get("Length")))
        _need(0 <= length <= self.limits["stream_bytes"], "source_stream_size_exceeded")
        a = start + marker.end()
        encoded = self.raw[a:a + length]
        _need(len(encoded) == length and re.match(rb"\s*endstream\s+endobj\b", self.raw[a + length:a + length + 80]),
              "source_stream_length_mismatch")
        if self.encryption is not None:
            encoded = self.document.decipher(ref.object_id, ref.generation, encoded)
        _need(metadata.get("DecodeParms") is None, "stream_predictor_unsupported")
        filter_name = metadata.get("Filter")
        if filter_name is None:
            decoded = encoded
        elif filter_name == _Name("FlateDecode"):
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(encoded, self.limits["stream_bytes"] + 1)
            _need(decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data,
                  "decoded_stream_budget_or_tail_unsupported")
        else:
            raise _Unknown("source_stream_filter_unsupported")
        _need(len(decoded) <= self.limits["stream_bytes"], "decoded_stream_budget_exceeded")
        self.charge("decoded_bytes", len(decoded))
        self.stream_cache[ref] = decoded
        return decoded


def _int(value):
    _need(isinstance(value, _Number) and re.fullmatch(r"[+-]?[0-9]+", value), "source_integer_required")
    result = int(value)
    _need(result.bit_length() <= 64, "source_integer_size_exceeded")
    return result


def _keys(value, allowed, required=()):
    _need(isinstance(value, dict) and not set(value) - set(allowed) and set(required) <= set(value),
          "source_dictionary_fields_unsupported")


def _operations(raw, objects):
    reader, operands = _Reader(raw, objects.charge), []
    while True:
        reader.space()
        start = reader.index
        token = reader.token()
        if token is None:
            break
        if type(token) is str:
            _need(token in _ARITY, "unsupported_source_operator:" + token)
            _need(len(operands) == _ARITY[token], "source_operator_arity_invalid")
            objects.charge("operators")
            yield token, operands, start
            operands = []
        else:
            _need(isinstance(token, (_Number, _Name, bytes)), "source_operand_unsupported")
            operands.append(token)
            _need(len(operands) <= 6, "source_operand_budget_exceeded")
    _need(not operands, "source_trailing_operands")


def _box(points):
    return [min(p[0].lower for p in points), min(p[1].lower for p in points),
            max(p[0].upper for p in points), max(p[1].upper for p in points)]


def _corners(b):
    x, y, X, Y = b
    return ((x, y), (X, y), (X, Y), (x, Y))


def _cells(b):
    return [math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3])]


def _contains(a, b):
    return a[0] <= b[0] <= b[2] <= a[2] and a[1] <= b[1] <= b[3] <= a[3]


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class _Enclosures:
    def __init__(self, numbers):
        self.n = numbers

    def span(self, lower, upper):
        # Enclose a derived range through the shared arithmetic, including
        # its subnormal/size policy; no separate floating point model.
        lo, hi = self.n.number(lower), self.n.number(upper)
        _need(lo.lower <= hi.upper, "derived_interval_invalid")
        return self.n._interval(lo.point, lo.lower, hi.upper)

    @staticmethod
    def magnitude(v):
        return max(abs(v.lower), abs(v.upper))

    @staticmethod
    def minimum(v):
        return F(0) if v.lower <= 0 <= v.upper else min(abs(v.lower), abs(v.upper))

    def axis(self, matrix):
        _need(all(v.lower == v.upper == 0 for v in (matrix[1], matrix[2])),
              "non_axis_transform_unsupported")
        _need(all(self.minimum(v) > 0 for v in (matrix[0], matrix[3])), "singular_transform_unsupported")

    def curve_range(self, values):
        # Pinned draw-path.c MAX_DEPTH8: all children are positive convex
        # 2/4/8-input sums followed by exact power-of-two scaling.
        n = self.n
        value = self.span(min(v.lower for v in values), max(v.upper for v in values))
        for _ in range(8):
            pair = n.add(value, value)
            four = n.add(pair, pair)
            eight = n.add(four, four)
            candidates = [value, n.multiply(pair, n.number("0.5")),
                          n.multiply(four, n.number("0.25")), n.multiply(eight, n.number("0.125"))]
            value = self.span(min(v.lower for v in candidates), max(v.upper for v in candidates))
        return value

    def raster(self, b):
        q = [self.n.multiply(self.n.number(v), self.n.number(s)) for v, s in zip(b, (17, 15, 17, 15))]
        return [math.floor(F(math.floor(q[0].lower), 17)), math.floor(F(math.floor(q[1].lower), 15)),
                math.ceil(F(math.floor(q[2].upper), 17)), math.ceil(F(math.floor(q[3].upper), 15))]

    def clip_inner(self, points):
        result = []
        for axis, scale in ((0, 17), (1, 15)):
            values = [p[axis] for p in points]
            low, high = min(v.point for v in values), max(v.point for v in values)
            _need(low < high, "rectangle_clip_degenerate")
            a = self.span(min(v.lower for v in values if v.point == low), max(v.upper for v in values if v.point == low))
            b = self.span(min(v.lower for v in values if v.point == high), max(v.upper for v in values if v.point == high))
            _need(a.upper < b.lower, "rectangle_clip_order_unknown")
            a, b = self.n.multiply(a, self.n.number(scale)), self.n.multiply(b, self.n.number(scale))
            result.append((math.ceil(F(math.floor(a.upper), scale)), math.floor(F(math.floor(b.lower), scale))))
        return [result[0][0], result[1][0], result[0][1], result[1][1]]

    def _phase(self, origin, mask, shift):
        n = self.n
        shifted = n.add(origin, n.number(shift))
        _need(shifted.upper - shifted.lower < 2, "subpixel_origin_range_unsupported")
        candidates = []
        for pixel in range(math.floor(shifted.lower), math.floor(shifted.upper) + 1):
            lo, hi = max(shifted.lower, F(pixel)), min(shifted.upper, F(pixel + 1))
            if hi < lo:
                continue
            lower = n.add(n.number(lo), n.number(-pixel)).lower
            upper = n.add(n.number(hi), n.number(-pixel)).upper
            low, high = max(F(0), lower), min(F(16777215, 16777216), upper)
            if high < low:
                continue
            a, b = max(0, math.trunc(low * 256)), min(255, math.trunc(high * 256))
            if a <= b:
                candidates.extend((F(pixel) + F(a & mask, 256), F(pixel) + F(b & mask, 256)))
        _need(bool(candidates), "subpixel_phase_unproved")
        return min(candidates), max(candidates)

    def glyph(self, matrix, outline):
        self.axis(matrix)
        _need(all(self.magnitude(v) <= 512 for v in matrix[:4]), "glyph_transform_size_unsupported")
        n = self.n
        determinant = n.multiply(matrix[0], matrix[3])
        _need(self.magnitude(determinant) <= 256**2, "glyph_render_fallback_unsupported")
        def grids(first, second):
            # sqrtf can round across a comparison. One adjacent binary32 ULP
            # on each side of its exact integer threshold encloses that
            # decision; this is derived from binary32 spacing, not epsilon.
            def margin(k):
                return F(2) ** (k.bit_length() - 1 - 23)
            low, high = self.minimum(determinant), self.magnitude(determinant)
            choices = []
            if low <= (first + margin(first))**2:
                choices.append((192, F(1, 8)))
            if high >= (first - margin(first))**2 and low <= (second + margin(second))**2:
                choices.append((128, F(1, 4)))
            if high >= (second - margin(second))**2:
                choices.append((0, F(1, 2)))
            return choices
        phases = []
        for axis, choices in ((4, grids(24, 48)), (5, grids(4, 8))):
            choices = [self._phase(matrix[axis], mask, shift)
                       for mask, shift in choices]
            phases.append((min(x[0] for x in choices), max(x[1] for x in choices)))
        if not outline.points:
            return None, None
        bounds = [min(p[0] for p in outline.points), min(p[1] for p in outline.points),
                  max(p[0] for p in outline.points), max(p[1] for p in outline.points)]

        def product(unit, coefficient):
            candidates = []
            for c in (math.trunc(64 * coefficient.lower), math.trunc(64 * coefficient.upper)):
                n._charge()
                value = 32 * unit * c
                _need(abs(value) < 1 << 62, "glyph_fixed_point_overflow")
                candidates.append((1 if value >= 0 else -1) * ((abs(value) + 32768) // 65536))
            return min(candidates), max(candidates)

        points = []
        for x, y in _corners(bounds):
            axes = []
            for a, b, phase in ((matrix[0], matrix[2], phases[0]), (matrix[1], matrix[3], phases[1])):
                u, v = product(x, a), product(y, b)
                axes.append((F(u[0] + v[0], 64) + phase[0], F(u[1] + v[1], 64) + phase[1]))
            points.append(axes)
        b = [min(p[0][0] for p in points), min(p[1][0] for p in points),
             max(p[0][1] for p in points), max(p[1][1] for p in points)]
        return _cells(b), [str(v) for v in b]

    def image(self, matrix):
        self.axis(matrix)
        axes, n = [], self.n
        for scale, origin in ((matrix[0], matrix[4]), (matrix[3], matrix[5])):
            low, high = math.floor(origin.lower), math.ceil(origin.upper)
            _need(high - low <= 4, "image_gridfit_origin_unknown")
            candidates = []
            for pixel in range(low, high + 1):
                edge = n.number(pixel)
                width = n.add(scale, n.add(origin, n.number(-pixel)))
                a, b = math.floor(width.lower), math.ceil(width.upper)
                _need(b - a <= 4, "image_gridfit_extent_unknown")
                for extent in range(a, b + 1):
                    far = n.add(edge, n.number(extent))
                    candidates.extend((edge.lower, edge.upper, far.lower, far.upper))
            axes.append((min(candidates), max(candidates)))
        return _cells([axes[0][0], axes[1][0], axes[0][1], axes[1][1]])


class _PagePaint:
    def __init__(self, objects, page, page_number, loader, max_numeric_operations):
        self.o, self.page, self.page_number, self.loader = objects, page, page_number, loader
        self.n = SourceNumbers(max_operations=max_numeric_operations)
        self.bounds = _Enclosures(self.n)
        self.events, self.occurrences, self.clips, self.fonts = [], [], [], {}
        self.font_programs, self.unicode_programs, self.outlines, self.form_stack = {}, {}, {}, []
        self.resource_stack = []
        self.fills, self.palette = [], {}
        self.scope = "page:" + str(page_number)

    def font(self, reference, scope, resource):
        font = self.o.object(reference)
        _keys(font, {"Type", "Subtype", "BaseFont", "Encoding", "DescendantFonts", "ToUnicode"},
              {"Type", "Subtype", "BaseFont", "Encoding", "DescendantFonts"})
        _need(font["Type"] == _Name("Font") and font["Subtype"] == _Name("Type0") and
              font["Encoding"] == _Name("Identity-H"), "font_encoding_unsupported")
        children = self.o.resolve(font["DescendantFonts"])
        _need(isinstance(children, list) and len(children) == 1, "CID_font_inventory_unsupported")
        child = self.o.object(children[0])
        _keys(child, {"Type", "Subtype", "BaseFont", "FontDescriptor", "CIDToGIDMap", "CIDSystemInfo", "W", "DW"},
              {"Type", "Subtype", "FontDescriptor", "CIDToGIDMap", "CIDSystemInfo"})
        _need(child["Type"] == _Name("Font") and child["Subtype"] == _Name("CIDFontType2") and
              child["CIDToGIDMap"] == _Name("Identity"), "CID_to_GID_mapping_unsupported")
        info = self.o.resolve(child["CIDSystemInfo"])
        _keys(info, {"Registry", "Ordering", "Supplement"}, {"Registry", "Ordering", "Supplement"})
        _need(info["Registry"] == b"Adobe" and info["Ordering"] == b"Identity" and _int(info["Supplement"]) == 0,
              "CID_system_info_unsupported")
        descriptor = self.o.object(child["FontDescriptor"])
        _keys(descriptor, {"Type", "FontName", "Flags", "Ascent", "Descent", "StemV", "CapHeight",
                           "ItalicAngle", "FontBBox", "FontFile2", "MissingWidth"},
              {"Type", "FontName", "Flags", "ItalicAngle", "FontBBox", "FontFile2"})
        _need(descriptor["Type"] == _Name("FontDescriptor") and
              descriptor["FontName"] == font["BaseFont"] and F(descriptor["ItalicAngle"]) == 0,
              "font_descriptor_unsupported")
        for key in ("Flags", "Ascent", "Descent", "StemV", "CapHeight", "ItalicAngle", "MissingWidth"):
            if key in descriptor:
                self.n.number(descriptor[key])
        _need(isinstance(descriptor["FontBBox"], list) and len(descriptor["FontBBox"]) == 4,
              "font_descriptor_bbox_unsupported")
        for value in descriptor["FontBBox"]:
            self.n.number(value)
        program_meta = self.o.object(descriptor["FontFile2"])
        _keys(program_meta, {"Length", "Length1", "Filter"}, {"Length"})
        program = self.o.stream(descriptor["FontFile2"])
        _need(len(program) <= 2 * 1024 * 1024, "font_program_budget_exceeded")
        if "Length1" in program_meta:
            _need(_int(program_meta["Length1"]) == len(program), "font_program_length_mismatch")
        digest = hashlib.sha256(program).hexdigest()
        widths, array = {}, self.o.resolve(child.get("W", []))
        _need(isinstance(array, list), "CID_width_array_unsupported")
        index = 0
        while index < len(array):
            cid = _int(array[index]); index += 1
            _need(0 <= cid <= 65535 and index < len(array), "CID_width_range_invalid")
            if isinstance(array[index], list):
                values = array[index]; index += 1
            else:
                last = _int(array[index]); index += 1
                _need(cid <= last <= 65535 and index < len(array), "CID_width_range_invalid")
                values = [array[index]] * (last - cid + 1); index += 1
            for value in values:
                self.o.charge("widths")
                _need(cid <= 65535 and cid not in widths and isinstance(value, _Number), "CID_width_inventory_invalid")
                self.n.engine_width(value)
                widths[cid] = value
                cid += 1
        default = child.get("DW", _Number("1000"))
        _need(isinstance(default, _Number), "CID_default_width_invalid")
        self.n.engine_width(default)
        unicode_hash, unicode_bytes = None, None
        if "ToUnicode" in font:
            _keys(self.o.object(font["ToUnicode"]), {"Length", "Filter"}, {"Length"})
            unicode_bytes = self.o.stream(font["ToUnicode"])
            unicode_hash = hashlib.sha256(unicode_bytes).hexdigest()
        key = f"{scope}/{resource}@{reference.object_id}:{reference.generation}"
        record = {"font_ref": key, "source_object_id": reference.object_id,
                  "source_generation": reference.generation, "resource": resource,
                  "scope": scope, "program_sha256": digest, "encoding": "Identity-H",
                  "cid_to_gid": "Identity", "to_unicode_sha256": unicode_hash}
        self.fonts[key] = record
        self.font_programs[key] = program
        self.unicode_programs[key] = unicode_bytes
        return {"key": key, "widths": widths, "default_width": default}

    def resources(self, reference, scope):
        resources = self.o.resolve(reference)
        _keys(resources, {"ProcSet", "ExtGState", "XObject", "Font"})
        if "ProcSet" in resources:
            values = self.o.resolve(resources["ProcSet"])
            _need(isinstance(values, list) and all(v in tuple(map(_Name, ("PDF", "Text", "ImageB", "ImageC", "ImageI"))) for v in values),
                  "procedure_set_unsupported")
        result = {"fonts": {}, "states": {}, "objects": {}}
        for field in ("ExtGState", "XObject", "Font"):
            values = self.o.resolve(resources.get(field, {}))
            _need(isinstance(values, dict) and len(values) <= 32, "resource_inventory_unsupported")
            for name, value in values.items():
                _need(isinstance(value, _Ref), "indirect_resource_required")
                if field == "Font":
                    result["fonts"][name] = self.font(value, scope, name)
                elif field == "ExtGState":
                    state = self.o.object(value)
                    _keys(state, {"Type", "ca", "CA", "LC", "LJ", "LW", "ML", "SA", "BM"})
                    _need(state.get("Type", _Name("ExtGState")) == _Name("ExtGState") and
                          state.get("BM", _Name("Normal")) == _Name("Normal") and
                          ("SA" not in state or type(state["SA"]) is bool), "graphics_state_unsupported")
                    numeric = {}
                    for key in ("ca", "CA", "LC", "LJ", "LW", "ML"):
                        if key in state:
                            numeric[key] = self.n.number(state[key])
                    for key in ("ca", "CA"):
                        if key in numeric:
                            _need(0 <= numeric[key].lower <= numeric[key].upper <= 1, "alpha_state_invalid")
                    for key in ("LC", "LJ"):
                        if key in numeric:
                            _need(numeric[key].lower == numeric[key].upper == 0, "stroke_style_unsupported")
                    for key in ("LW", "ML"):
                        if key in numeric:
                            _need(0 < numeric[key].lower <= numeric[key].upper <= 100, "stroke_extent_unsupported")
                    result["states"][name] = numeric
                else:
                    obj = self.o.object(value)
                    _need(isinstance(obj, dict) and obj.get("Type") == _Name("XObject"), "xobject_type_unsupported")
                    if obj.get("Subtype") == _Name("Form"):
                        _keys(obj, {"Type", "Subtype", "FormType", "BBox", "Matrix", "Resources", "Length", "Filter"},
                              {"Type", "Subtype", "BBox", "Resources", "Length"})
                        _need(_int(obj.get("FormType", _Number("1"))) == 1, "form_type_unsupported")
                        self.o.stream(value)
                        _need(value not in self.resource_stack and len(self.resource_stack) < 1,
                              "form_resource_nesting_unsupported")
                        self.resource_stack.append(value)
                        self.resources(obj["Resources"], scope + "/" + name)
                        self.resource_stack.pop()
                    elif obj.get("Subtype") == _Name("Image"):
                        _keys(obj, {"Type", "Subtype", "Width", "Height", "ColorSpace", "BitsPerComponent",
                                   "Filter", "Length", "ColorTransform"},
                              {"Type", "Subtype", "Width", "Height", "ColorSpace", "BitsPerComponent", "Length"})
                        w, h = _int(obj["Width"]), _int(obj["Height"])
                        _need(0 < w <= 16384 and 0 < h <= 16384 and w * h <= 16_000_000 and
                              obj["ColorSpace"] in (_Name("DeviceRGB"), _Name("DeviceGray")) and
                              _int(obj["BitsPerComponent"]) == 8 and
                              obj.get("Filter") in (None, _Name("DCTDecode"), _Name("FlateDecode")) and
                              _int(obj.get("ColorTransform", _Number("0"))) == 0 and
                              0 <= _int(obj["Length"]) <= self.o.limits["stream_bytes"], "image_profile_unsupported")
                    else:
                        raise _Unknown("xobject_subtype_unsupported")
                    result["objects"][name] = value
        return result

    def outline(self, font_key, gid):
        key = (self.fonts[font_key]["program_sha256"], gid)
        if key not in self.outlines:
            self.o.charge("outline_calls")
            outline = self.loader(self.font_programs[font_key], gid)
            _need(isinstance(outline, GlyphOutline) and outline.simple is True and
                  outline.units_per_em == 2048 and isinstance(outline.points, tuple) and
                  len(outline.points) <= 4096, "source_glyph_outline_unsupported")
            for point in outline.points:
                self.o.charge("control_points")
                _need(isinstance(point, tuple) and len(point) == 2 and
                      all(type(v) is int and -32768 <= v <= 32767 for v in point), "glyph_control_point_invalid")
            self.outlines[key] = outline
        return self.outlines[key]

    def emit(self, kind, outer, **extra):
        self.o.charge("events")
        event = {"event": len(self.events), "kind": kind, "outer_device_pixels": outer, **extra}
        self.events.append(event)
        return event

    def color(self, values, alpha):
        """Pinned DeviceRGB8 byte enclosure; no perceptual color threshold.

        color-fast.c copies DeviceRGB channels; the sole supported default
        DeviceGray is PDF's initial black, replicated to RGB. draw-device.c
        resolve_color multiplies by255 in float32 then truncates into uchar.
        """
        space = "DeviceRGB" if len(values) == 3 else "DeviceGray"
        _need(len(values) == 3 or len(values) == 1 and values[0].lower == values[0].upper == 0,
              "source_color_space_unsupported")
        key = (space, tuple((v.point, v.lower, v.upper) for v in values),
               (alpha.point, alpha.lower, alpha.upper))
        if key not in self.palette:
            _need(len(self.palette) < 32, "source_palette_budget_exceeded")
            channels = values if len(values) == 3 else values * 3
            ranges = []
            for value in channels:
                quantized = self.n.multiply(value, self.n.number(255))
                low, high = math.trunc(quantized.lower), math.trunc(quantized.upper)
                _need(0 <= low <= high <= 255, "display_byte_range_unproved")
                ranges.append([low, high])
            self.palette[key] = {
                "source_color_space": space,
                "source_color_intervals": [v.as_dict() for v in values],
                "source_alpha_interval": alpha.as_dict(),
                "display_profile": "MuPDF1.28.2-DeviceRGB8-identity-no-separations",
                "display_byte_ranges": ranges,
                "display_bytes": [a for a, b in ranges] if all(a == b for a, b in ranges) else None,
                "opaque": alpha.lower == alpha.upper == 1,
            }
        return dict(self.palette[key])

    def run(self, content_ref, resource_ref, state, scope, depth=0):
        _need(depth <= 1 and content_ref not in self.form_stack, "form_nesting_unsupported")
        self.form_stack.append(content_ref)
        resources = self.resources(resource_ref, scope)
        n, b = self.n, self.bounds
        metrics = {name: {k: value[k] for k in ("widths", "default_width")} for name, value in resources["fonts"].items()}
        # The outer page often only invokes a Form and has no font resource.
        text = HorizontalTextState(n, ctm=state["ctm"], font_metrics=metrics or {"_unused": {"widths": {}}})
        state = {**state, "gs": dict(state["gs"]), "clips": list(state["clips"])}
        stack, points, path_ops, pending = [], [], [], None
        for operator, args, byte_offset in _operations(self.o.stream(content_ref), self.o):
            if operator in ("q", "Q", "cm", "BT", "ET", "Tf", "Tm", "Td", "Tj"):
                _need(not points and pending is None, "graphics_or_text_change_during_path_unsupported")
                operands = list(args)
                if operator in ("Tf",):
                    _need(isinstance(args[0], _Name) and args[0] in resources["fonts"], "font_resource_missing")
                    operands[0] = str(args[0])
                if operator == "cm":
                    _need(not points and pending is None, "matrix_change_during_path_unsupported")
                if operator == "Tj":
                    _need(isinstance(args[0], bytes) and len(args[0]) % 2 == 0, "CID_string_unsupported")
                    operands = [[int.from_bytes(args[0][i:i + 2], "big") for i in range(0, len(args[0]), 2)]]
                positions = text.apply(operator, operands)
                if operator == "q":
                    _need(len(stack) < 32, "graphics_stack_budget_exceeded")
                    stack.append({**state, "gs": dict(state["gs"]), "clips": list(state["clips"])})
                elif operator == "Q":
                    _need(bool(stack), "graphics_stack_underflow")
                    before = {c["clip_id"] for c in state["clips"] if "clip_id" in c}
                    state = stack.pop()
                    after = {c["clip_id"] for c in state["clips"] if "clip_id" in c}
                    for index in before - after:
                        self.clips[index]["retired_before_event"] = len(self.events)
                state["ctm"] = text.ctm
                for position in positions:
                    self.o.charge("glyphs")
                    font_key = resources["fonts"][position.font_resource]["key"]
                    outline = self.outline(font_key, position.cid)
                    matrix = n.compose(position.ctm, position.render_matrix)
                    outer, control = b.glyph(matrix, outline)
                    index = len(self.occurrences)
                    occurrence = {"occurrence": index, "page_number": self.page_number,
                        "scope": scope, "source_stream_object": content_ref.object_id,
                        "source_stream_generation": content_ref.generation, "operator_byte_offset": byte_offset,
                        "show_index": position.show_index, "source_glyph_index": position.glyph_index,
                        "cid": position.cid, "gid": position.cid, "font_ref": font_key,
                        "font_program_sha256": self.fonts[font_key]["program_sha256"],
                        "origin": [v.as_dict() for v in position.origin],
                        "render_matrix": [v.as_dict() for v in matrix],
                        "quantized_control_domain": control, "outer_device_pixels": outer,
                        "clip_ids": [c.get("clip_id", c.get("origin")) for c in state["clips"]],
                        "effective_clips": [dict(c) for c in state["clips"]],
                        "unresolved": []}
                    occurrence.update(self.color(state["fill"], state["gs"]["ca"]))
                    if state["gs"]["ca"].lower != 1 or state["gs"]["ca"].upper != 1:
                        occurrence["unresolved"].append("target_alpha_not_opaque")
                    if not all(v.lower == v.upper == 0 for v in state["fill"]):
                        occurrence["unresolved"].append("target_fill_not_black")
                    if outer is None:
                        occurrence["unresolved"].append("source_glyph_has_no_control_points")
                    else:
                        for clip in state["clips"]:
                            if clip["kind"] != "rect" or not _contains(clip["inner_device_pixels"], outer):
                                occurrence["unresolved"].append("target_clip_not_proved_interior")
                    event = self.emit("glyph", outer, occurrence=index)
                    occurrence["paint_event"] = event["event"]
                    self.occurrences.append(occurrence)
                continue
            _need(not text.inside_text, "nontext_operator_inside_text_unsupported")
            if operator in ("rg", "RG"):
                values = [n.number(v) for v in args]
                _need(all(0 <= v.lower <= v.upper <= 1 for v in values), "color_value_unsupported")
                if operator == "rg":
                    state["fill"] = values
            elif operator == "gs":
                _need(isinstance(args[0], _Name) and args[0] in resources["states"], "graphics_state_resource_missing")
                state["gs"] = {**state["gs"], **resources["states"][args[0]]}
            elif operator in ("m", "l", "c"):
                _need(pending is None, "path_changed_after_clip_operator")
                points.extend((n.number(args[i]), n.number(args[i + 1])) for i in range(0, len(args), 2))
                path_ops.append(operator)
                self.o.charge("path_points", len(args) // 2)
            elif operator == "re":
                _need(pending is None, "path_changed_after_clip_operator")
                x, y, w, h = [n.number(v) for v in args]
                X, Y = n.add(x, w), n.add(y, h)
                points.extend(((x, y), (X, y), (X, Y), (x, Y)))
                path_ops.append(operator)
                self.o.charge("path_points", 4)
            elif operator == "h":
                _need(points and pending is None, "path_close_without_points")
                path_ops.append(operator)
            elif operator in ("W", "W*"):
                _need(points and pending is None, "clip_path_missing_or_pending")
                b.axis(state["ctm"])
                transformed = [n.transform_point(state["ctm"], *point) for point in points]
                pending = {"kind": "rect" if path_ops == ["re"] else "unknown_path",
                           "source_ops": list(path_ops), "scope": scope}
                if pending["kind"] == "rect":
                    pending["inner_device_pixels"] = b.clip_inner(transformed)
            elif operator in ("n", "f", "S"):
                _need(points, "path_end_without_points")
                if operator != "n":
                    b.axis(state["ctm"])
                    if "c" in path_ops:
                        X, Y = b.curve_range([p[0] for p in points]), b.curve_range([p[1] for p in points])
                        source_points = [(n.number(x), n.number(y)) for x, y in _corners([X.lower, Y.lower, X.upper, Y.upper])]
                    else:
                        source_points = points
                    transformed = [n.transform_point(state["ctm"], *point) for point in source_points]
                    outer = _box(transformed)
                    if operator == "S":
                        minimum = min(b.minimum(state["ctm"][0]), b.minimum(state["ctm"][3]))
                        _need(state["gs"]["LW"].lower * minimum > F(1, 5), "stroke_min_width_increase_unsupported")
                        scale = max(b.magnitude(state["ctm"][0]), b.magnitude(state["ctm"][3]))
                        expand = n.multiply(n.multiply(state["gs"]["LW"], state["gs"]["ML"]), n.number(scale))
                        negative = n.multiply(expand, n.number(-1))
                        outer = [n.add(n.number(outer[0]), negative).lower, n.add(n.number(outer[1]), negative).lower,
                                 n.add(n.number(outer[2]), expand).upper, n.add(n.number(outer[3]), expand).upper]
                    event = self.emit("path", b.raster(outer), source_ops=list(path_ops), stroke=operator == "S")
                    if operator == "f":
                        fill = {"event": event, "ctm": state["ctm"], "color": tuple(state["fill"]),
                                "alpha": state["gs"]["ca"], "source_rect": None}
                        self.fills.append(fill)
                        if (path_ops == ["re"] and len(state["fill"]) == 3 and
                                state["gs"]["ca"].lower == state["gs"]["ca"].upper == 1 and
                                all(c["kind"] == "rect" for c in state["clips"])):
                            inner = b.clip_inner(transformed)
                            for clip in state["clips"]:
                                cb = clip["inner_device_pixels"]
                                inner = [max(inner[0], cb[0]), max(inner[1], cb[1]), min(inner[2], cb[2]), min(inner[3], cb[3])]
                            if inner[0] < inner[2] and inner[1] < inner[3]:
                                metadata = self.color(state["fill"], state["gs"]["ca"])
                                metadata.update(schema_version="house-native-opaque-rgb-rectangle/v1",
                                    status="pending_engine_source_check", inner_device_pixels=inner,
                                    effective_clips=[dict(c) for c in state["clips"]],
                                    engine_fill_index=len(self.fills) - 1,
                                    source_stream_object=content_ref.object_id,
                                    source_stream_generation=content_ref.generation,
                                    source_operator_byte_offset=byte_offset,
                                    source_rect_coordinates=[v.as_dict() for v in
                                        (points[0][0], points[0][1], points[2][0], points[2][1])])
                                event["uniform_background"] = metadata
                                fill["source_rect"] = (points[0][0], points[0][1], points[2][0], points[2][1])
                if pending is not None:
                    self.o.charge("clips")
                    pending.update(clip_id=len(self.clips), activated_before_event=len(self.events))
                    self.clips.append(pending)
                    state["clips"] = state["clips"] + [pending]
                    pending = None
                points, path_ops = [], []
            elif operator == "Do":
                _need(not points and pending is None and isinstance(args[0], _Name) and args[0] in resources["objects"],
                      "xobject_invocation_unsupported")
                reference = resources["objects"][args[0]]
                obj = self.o.object(reference)
                if obj["Subtype"] == _Name("Image"):
                    self.emit("image", b.image(state["ctm"]))
                else:
                    values = obj.get("Matrix", list(map(_Number, ("1", "0", "0", "1", "0", "0"))))
                    _need(isinstance(values, list) and len(values) == 6 and tuple(F(v) for v in values) == (1, 0, 0, 1, 0, 0),
                          "form_matrix_unsupported")
                    bbox = obj["BBox"]
                    _need(isinstance(bbox, list) and len(bbox) == 4, "form_bbox_unsupported")
                    b.axis(state["ctm"])
                    transformed = [n.transform_point(state["ctm"], n.number(x), n.number(y))
                                   for x, y in _corners([F(v) for v in bbox])]
                    inner = b.clip_inner(transformed)
                    child = {**state, "ctm": n.compose(state["ctm"], n.identity()),
                             "clips": state["clips"] + [{"kind": "rect", "inner_device_pixels": inner,
                                                         "origin": scope + "/" + args[0] + ":BBox"}]}
                    self.run(reference, obj["Resources"], child, scope + "/" + args[0], depth + 1)
            else:
                raise _Unknown("source_operator_not_consumed")
        text.finish()
        _need(not stack and not points and pending is None, "unclosed_paint_state")
        self.form_stack.pop()

    def engine_fonts(self, raw):
        """Reopen source in the pinned engine; never render a pixel oracle."""
        import pymupdf

        _need(pymupdf.version[:2] == ("1.28.2", "1.28.2"), "pinned_engine_unavailable")
        _need(pymupdf.TOOLS.show_aa_level() == {"graphics": 8, "text": 8, "graphics_min_line_width": 0.0},
              "render_settings_unsupported")
        observed, fill_observed, failure = [], [], []
        expected = self.occurrences
        fills = self.fills

        def color_matches(metadata, colorspace, color, alpha):
            _need(pymupdf.mupdf.ll_fz_colorspace_name(colorspace) == metadata["source_color_space"],
                  "engine_source_colorspace_mismatch")
            for i, interval in enumerate(metadata["source_color_intervals"]):
                actual = F.from_float(pymupdf.mupdf.floats_getitem(color, i))
                _need(actual == F(interval["point"]) and F(interval["lower"]) <= actual <= F(interval["upper"]),
                      "engine_source_color_mismatch")
            _need(F.from_float(float(alpha)) == F(metadata["source_alpha_interval"]["point"]),
                  "engine_source_alpha_mismatch")

        class Rectangle(pymupdf.mupdf.FzPathWalker2):
            def __init__(walker):
                super().__init__()
                walker.records = []
                walker.use_virtual_moveto(); walker.use_virtual_lineto()
                walker.use_virtual_curveto(); walker.use_virtual_closepath(); walker.use_virtual_rectto()

            def moveto(walker, ctx, x, y):
                walker.records.append(("m", (x, y)))

            def lineto(walker, ctx, x, y):
                walker.records.append(("l", (x, y)))

            def curveto(walker, ctx, *values):
                walker.records.append(("c", values))

            def closepath(walker, ctx):
                walker.records.append(("h", ()))

            def rectto(walker, ctx, x, y, X, Y):
                walker.records.append(("re", (x, y, X, Y)))

        class Device(pymupdf.mupdf.FzDevice2):
            def __init__(device):
                super().__init__()
                device.use_virtual_fill_text()
                device.use_virtual_fill_path()

            def fill_text(device, ctx, text, ctm, colorspace, color, alpha, params):
                if failure:
                    return
                try:
                    span = text.head
                    while span:
                        _need(0 <= span.len <= len(expected) - len(observed), "engine_glyph_inventory_mismatch")
                        flags = pymupdf.mupdf.ll_fz_font_flags(span.font)
                        _need(not flags.fake_bold and not flags.fake_italic and not flags.ft_stretch,
                              "engine_font_geometry_modified")
                        program = pymupdf.mupdf.ll_fz_buffer_storage_memoryview(span.font.buffer, False)
                        _need(len(program) <= 2 * 1024 * 1024, "engine_font_program_size_unsupported")
                        digest = hashlib.sha256(program).hexdigest()
                        matrix = pymupdf.mupdf.fz_concat(pymupdf.mupdf.FzMatrix(span.trm), pymupdf.mupdf.FzMatrix(ctm))
                        for _ in range(span.len):
                            record = expected[len(observed)]
                            _need(digest == record["font_program_sha256"], "engine_source_font_mismatch")
                            color_matches(record, colorspace, color, alpha)
                            for key, interval in zip("abcd", record["render_matrix"][:4]):
                                value = F.from_float(getattr(matrix, key))
                                _need(F(interval["lower"]) <= value <= F(interval["upper"]),
                                      "engine_linear_transform_mismatch")
                            observed.append(digest)
                            record["engine_color_source_verified"] = True
                        span = span.next
                except Exception as exc:
                    failure.append(str(exc))

            def fill_path(device, ctx, path, even_odd, ctm, colorspace, color, alpha, params):
                if failure:
                    return
                try:
                    _need(len(fill_observed) < len(fills) and not even_odd, "engine_fill_inventory_mismatch")
                    fill = fills[len(fill_observed)]
                    for key, interval in zip("abcdef", fill["ctm"]):
                        value = F.from_float(getattr(ctm, key))
                        _need(interval.lower <= value <= interval.upper, "engine_fill_transform_mismatch")
                    metadata = self.color(fill["color"], fill["alpha"])
                    color_matches(metadata, colorspace, color, alpha)
                    if fill["source_rect"] is not None:
                        walker = Rectangle()
                        # The pinned wrapper's opaque arg MUST be the original
                        # fz_path_walker*, per classes.h FzPathWalker2 contract.
                        pymupdf.mupdf.ll_fz_walk_path(path, walker.m_internal, walker.m_internal)
                        _need(len(walker.records) == 1 and walker.records[0][0] == "re",
                              "engine_background_rectangle_shape_mismatch")
                        for value, interval in zip(walker.records[0][1], fill["source_rect"]):
                            actual = F.from_float(value)
                            _need(interval.lower <= actual <= interval.upper,
                                  "engine_background_rectangle_coordinates_mismatch")
                        background = fill["event"]["uniform_background"]
                        background.update(status="observed", engine_source_color_verified=True,
                                          engine_source_shape_verified=True)
                    fill_observed.append(fill["event"]["event"])
                except Exception as exc:
                    failure.append(str(exc))

        with pymupdf.open(stream=raw, filetype="pdf") as document:
            device = Device()
            pymupdf.mupdf.fz_run_page_contents(pymupdf._as_fz_page(document[self.page_number - 1]), device,
                                             pymupdf.mupdf.FzMatrix(), pymupdf.mupdf.FzCookie())
        _need(not failure, "engine_source_profile_unknown:" + (failure[0] if failure else ""))
        _need(len(observed) == len(expected), "engine_glyph_inventory_mismatch")
        _need(len(fill_observed) == len(fills), "engine_fill_inventory_mismatch")
        return {"engine": "MuPDF1.28.2", "graphics_aa": 8, "text_aa": 8,
                "min_line_width": 0, "actual_font_program_hashes": sorted(set(observed)),
                "actual_glyph_count": len(observed), "actual_fill_count": len(fill_observed),
                "source_palette_count": len(self.palette), "synthetic_font_flags": False}

    def observations(self):
        unresolved = []
        for occurrence in self.occurrences:
            outer = occurrence["outer_device_pixels"]
            blockers, nonglyph = [], []
            if outer is not None:
                for event in self.events:
                    self.o.charge("comparisons")
                    other = event["outer_device_pixels"]
                    if event["event"] != occurrence["paint_event"] and other is not None and _overlaps(outer, other):
                        blockers.append(event["event"])
                        if event["kind"] != "glyph":
                            nonglyph.append(event["event"])
                if nonglyph:
                    occurrence["unresolved"].append("nonglyph_paint_domain_intersects")
            occurrence["intersecting_paint_events"] = blockers
            occurrence["occlusion_status"] = "requires_independent_target_group_check"
            occurrence["paint_status"] = "unknown" if occurrence["unresolved"] else "observed"
            unresolved.extend({"occurrence": occurrence["occurrence"], "reason": reason}
                              for reason in occurrence["unresolved"])
        return unresolved


def _pages(objects, max_pages):
    catalog = objects.object(objects.root)
    _keys(catalog, {"Type", "Pages"}, {"Type", "Pages"})
    _need(catalog["Type"] == _Name("Catalog"), "catalog_type_unsupported")
    result, seen = [], set()

    def visit(reference, inherited=None, depth=0):
        _need(isinstance(reference, _Ref) and reference not in seen and depth <= 8, "page_tree_cycle_or_depth")
        seen.add(reference)
        value = objects.object(reference)
        if value.get("Type") == _Name("Pages"):
            _keys(value, {"Type", "Parent", "Kids", "Count", "MediaBox", "CropBox", "Resources", "Rotate"},
                  {"Type", "Kids", "Count"})
            _need(0 < _int(value["Count"]) <= max_pages, "document_page_budget_exceeded")
            children = objects.resolve(value["Kids"])
            _need(isinstance(children, list) and len(children) <= max_pages, "page_tree_children_unsupported")
            before = len(result)
            inherited = {**(inherited or {}), **{k: value[k] for k in ("MediaBox", "CropBox", "Resources", "Rotate") if k in value}}
            for child in children:
                visit(child, inherited, depth + 1)
            _need(len(result) - before == _int(value["Count"]), "page_tree_count_mismatch")
        else:
            _keys(value, {"Type", "Parent", "Contents", "MediaBox", "CropBox", "Resources", "Rotate", "Group", "Annots"},
                  {"Type", "Contents", "Parent"})
            _need(value["Type"] == _Name("Page"), "page_type_unsupported")
            result.append((reference, {**(inherited or {}), **value}))
            _need(len(result) <= max_pages, "document_page_budget_exceeded")

    visit(catalog["Pages"])
    _need(bool(result), "source_has_no_pages")
    return result


def _page_metadata(objects, page):
    media = objects.resolve(page.get("MediaBox"))
    crop = objects.resolve(page.get("CropBox", media))
    _need(isinstance(media, list) and len(media) == 4 and crop == media and
          tuple(F(v) for v in media[:2]) == (0, 0) and
          all(isinstance(v, _Number) for v in media) and
          0 < F(media[2]) <= 1440 and 0 < F(media[3]) <= 1440 and
          _int(page.get("Rotate", _Number("0"))) == 0, "page_geometry_unsupported")
    _need(F(media[2]).denominator == F(media[3]).denominator == 1, "integer_page_domain_required")
    if "Group" in page:
        group = objects.resolve(page["Group"])
        _keys(group, {"S", "CS", "I", "K"}, {"S", "CS"})
        _need(group["S"] == _Name("Transparency") and group["CS"] == _Name("DeviceRGB") and
              group.get("I", False) is False and group.get("K", False) is False, "page_group_unsupported")
    annotations = objects.resolve(page.get("Annots", []))
    _need(isinstance(annotations, list) and len(annotations) <= 32, "annotation_inventory_unsupported")
    for reference in annotations:
        item = objects.object(reference)
        _keys(item, {"Type", "Subtype", "Rect", "F", "P", "Border", "A"},
              {"Type", "Subtype", "Rect", "Border", "A"})
        _need(item["Type"] == _Name("Annot") and item["Subtype"] == _Name("Link") and
              isinstance(item["Border"], list) and len(item["Border"]) == 3 and
              all(F(v) == 0 for v in item["Border"]), "annotation_appearance_unsupported")
        rect = objects.resolve(item["Rect"])
        _need(isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, _Number) and abs(F(v)) <= 1440 for v in rect),
              "annotation_rectangle_unsupported")
        _int(item.get("F", _Number("0")))
        action = objects.resolve(item["A"])
        _keys(action, {"S", "URI"}, {"S", "URI"})
        _need(action["S"] == _Name("URI") and isinstance(action["URI"], bytes) and len(action["URI"]) <= 4096,
              "annotation_action_unsupported")
    return int(media[2]), int(media[3])


def inspect_house_id_source_paint(source_bytes: bytes, *, source_sha256: str,
                                  font_program_loader, page_number: int = 1,
                                  font_source_observer=None,
                                  max_pages: int = 32, max_bytes: int = 50 * 1024 * 1024,
                                  max_operators: int = 20000, max_glyphs: int = 25000,
                                  max_events: int = 30000, max_numeric_operations: int = 200000) -> dict:
    """Inspect one explicitly selected page while enumerating the whole PDF.

    ``font_program_loader(bytes, gid) -> GlyphOutline`` is trusted program code
    reading source TTF data, not an observation receipt. This function passes
    actual FontFile2 bytes and source Identity-H/Identity-mapped GIDs. It never
    grants digit meaning, ID relations, complete-report or publish eligibility.
    Optional ``font_source_observer(FontSource)`` receives runtime source bytes
    and object pointers after the source/engine checks, for an independent
    font semantic reader. Its return value is ignored and never saved.
    A nonempty ``unprocessed_pages`` must be consumed by a caller claiming a
    document-wide result. Fatal unsupported source state invalidates all
    selected-page paint claims; local clipping/overlap marks occurrences unknown.
    """
    if (not isinstance(source_bytes, bytes) or not callable(font_program_loader) or
            (font_source_observer is not None and not callable(font_source_observer)) or
            not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)):
        raise SourcePaintError("source_bytes_hash_and_font_loader_required")
    for value, maximum in ((max_pages, 32), (max_bytes, 50 * 1024 * 1024), (max_operators, 100000),
                           (max_glyphs, 25000), (max_events, 30000), (max_numeric_operations, 1000000)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise SourcePaintError("source_paint_budget_invalid")
    if type(page_number) is not int or not 1 <= page_number <= max_pages:
        raise SourcePaintError("page_selection_invalid")
    if len(source_bytes) > max_bytes:
        raise SourcePaintError("source_pdf_byte_budget_exceeded")
    if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
        raise SourcePaintError("source_pdf_sha256_mismatch")
    result = {"schema_version": PROFILE, "source_sha256": source_sha256,
              "selected_page": page_number, "page_count": None, "pages": [],
              "processed_pages": [], "unprocessed_pages": [], "status": "unknown",
              "fonts": [], "occurrences": [], "paint_inventory": [], "clips": [], "unresolved": []}
    objects, painter = None, None
    try:
        limits = {"objects": 4096, "object_bytes": 1024 * 1024, "tokens": 200000,
                  "stream_bytes": 4 * 1024 * 1024, "decoded_bytes": 16 * 1024 * 1024,
                  "operators": max_operators, "glyphs": max_glyphs, "events": max_events,
                  "outline_calls": 4096, "control_points": 100000, "path_points": 50000,
                  "widths": 131072, "clips": 5000, "comparisons": 3000000}
        objects = _Objects(source_bytes, limits)
        pages = _pages(objects, max_pages)
        _need(page_number <= len(pages), "selected_page_unavailable")
        result.update(page_count=len(pages), pages=list(range(1, len(pages) + 1)),
                      unprocessed_pages=[i for i in range(1, len(pages) + 1) if i != page_number])
        _, page = pages[page_number - 1]
        width, height = _page_metadata(objects, page)
        contents = objects.resolve(page["Contents"])
        # Source stream sequence is preserved; page-level q/Q may span streams.
        references = contents if isinstance(contents, list) else [page["Contents"]]
        _need(1 <= len(references) <= 32 and all(isinstance(v, _Ref) for v in references), "page_contents_unsupported")
        for reference in references:
            _keys(objects.object(reference), {"Length", "Filter"}, {"Length"})
        painter = _PagePaint(objects, page, page_number, font_program_loader, max_numeric_operations)
        n = painter.n
        state = {"ctm": n.matrix([1, 0, 0, -1, 0, height]), "fill": [n.zero],
                 "gs": {"ca": n.one, "CA": n.one, "LW": n.one, "ML": n.number(10),
                        "LC": n.zero, "LJ": n.zero},
                 "clips": [{"kind": "rect", "inner_device_pixels": [0, 0, width, height], "origin": "page-domain"}]}
        if len(references) == 1:
            painter.run(references[0], page["Resources"], state, painter.scope)
        else:
            # A synthetic in-memory stream retains exact original segments
            # and offsets below; it never writes or supplies source authority.
            combined = b"\n".join(objects.stream(ref) for ref in references)
            _need(len(combined) <= limits["stream_bytes"], "page_contents_budget_exceeded")
            synthetic = _Ref(-1, 0)
            objects.stream_cache[synthetic] = combined
            painter.run(synthetic, page["Resources"], state, painter.scope)
            # Map each occurrence back to its original stream and byte offset.
            starts, cursor = [], 0
            for reference in references:
                starts.append((cursor, cursor + len(objects.stream(reference)), reference))
                cursor += len(objects.stream(reference)) + 1
            for occurrence in painter.occurrences:
                if occurrence["source_stream_object"] == -1:
                    offset = occurrence["operator_byte_offset"]
                    matches = [(a, ref) for a, b, ref in starts if a <= offset < b]
                    _need(len(matches) == 1, "source_stream_operator_binding_unknown")
                    a, ref = matches[0]
                    occurrence.update(source_stream_object=ref.object_id, source_stream_generation=ref.generation,
                                      operator_byte_offset=offset - a)
        engine = painter.engine_fonts(source_bytes)
        unresolved = painter.observations()
        if font_source_observer is not None:
            for key, record in painter.fonts.items():
                used = tuple(sorted({v["cid"] for v in painter.occurrences if v["font_ref"] == key}))
                if used:
                    font_source_observer(FontSource(key, record["source_object_id"], record["source_generation"],
                                                    painter.font_programs[key], painter.unicode_programs[key], used))
        result.update(status="observed", processed_pages=[page_number],
                      fonts=list(painter.fonts.values()), occurrences=painter.occurrences,
                      paint_inventory=painter.events, clips=painter.clips, unresolved=unresolved,
                      render_profile=engine, numerical_operations=n.operations,
                      numerical_rounding_brackets=n.rounding_brackets,
                      page_context={"media_box": [0, 0, width, height], "crop_box": [0, 0, width, height],
                                    "rotation": 0, "group_profile": "nonisolated_nonknockout_DeviceRGB" if "Group" in page else None,
                                    "annotation_profile": "zero_border_no_appearance_Link_URI"},
                      inventory_summary={"events": len(painter.events), "glyph_occurrences": len(painter.occurrences),
                                         "paths": sum(e["kind"] == "path" for e in painter.events),
                                         "images": sum(e["kind"] == "image" for e in painter.events),
                                         "path_clips": len(painter.clips)})
    except Exception as exc:
        result["unresolved"] = [{"page_number": page_number, "reason": str(exc) or type(exc).__name__}]
        if painter is not None:
            result.update(numerical_operations=painter.n.operations,
                          numerical_rounding_brackets=painter.n.rounding_brackets)
    if objects is not None:
        result["budgets_used"] = dict(objects.counts)
        result["source_encryption"] = objects.encryption
    return result
