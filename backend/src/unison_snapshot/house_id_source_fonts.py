"""Exact, source-read Georgia glyph foundations for native ID accounting.

No name, GID, ToUnicode label or saved digest alone proves a glyph. Reference
data contains trusted design/hint fingerprints, never font binaries. Returned
points are ephemeral source observations, not visible-ink or financial proof.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import zlib


class SourceFontError(ValueError):
    pass


MAX_FONT_BYTES = 2 * 1024 * 1024
MAX_USED_CIDS = 2048
MAX_FONT_GLYPHS = 4096
MAX_GLYPH_POINTS = 2048
MAX_TOTAL_POINTS = 100_000
_HINT_TABLES = ("fpgm", "prep", "cvt ", "gasp")
_FORBIDDEN_TABLES = {"fvar", "gvar", "cvar", "avar", "HVAR", "VVAR", "MVAR", "CFF ",
                     "CFF2", "COLR", "CPAL", "SVG ", "CBDT", "CBLC", "EBDT", "EBLC", "sbix"}


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _resolve(value):
    from pdfminer.pdftypes import PDFObjRef
    seen = set()
    for _ in range(16):
        if not isinstance(value, PDFObjRef):
            return value
        key = (id(value.doc), value.objid)
        if key in seen:
            break
        seen.add(key)
        value = value.resolve()
    raise SourceFontError("font_reference_chain_unproved")


def _name(value):
    from pdfminer.psparser import PSLiteral
    value = _resolve(value)
    return value.name if isinstance(value, PSLiteral) else None


def _stream(value, limit):
    from pdfminer.pdftypes import PDFStream
    value = _resolve(value)
    if not isinstance(value, PDFStream):
        raise SourceFontError("source_font_stream_required")
    data = value.data
    if data is None:
        raw = value.rawdata
        if not isinstance(raw, bytes) or len(raw) > limit:
            raise SourceFontError("font_stream_byte_budget_exceeded")
        if value.decipher:
            raw = value.decipher(value.objid, value.genno, raw, value.attrs)
            if not isinstance(raw, bytes) or len(raw) > limit:
                raise SourceFontError("font_stream_byte_budget_exceeded")
        filters = _resolve(value.attrs.get("Filter"))
        if isinstance(filters, list) and len(filters) == 1:
            filters = filters[0]
        parameters = _resolve(value.attrs.get("DecodeParms"))
        if filters is None:
            data = raw
        elif _name(filters) == "FlateDecode" and parameters in (None, {}):
            decoder = zlib.decompressobj()
            data = decoder.decompress(raw, limit + 1)
            if len(data) > limit or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
                raise SourceFontError("font_stream_byte_budget_exceeded_or_invalid")
        else:
            raise SourceFontError("font_stream_filter_unsupported")
    if not isinstance(data, bytes) or len(data) > limit:
        raise SourceFontError("font_stream_byte_budget_exceeded")
    return data


def _directory(data):
    if len(data) < 12 or len(data) > MAX_FONT_BYTES or data[:4] != b"\x00\x01\x00\x00":
        raise SourceFontError("simple_true_type_font_required")
    count = struct.unpack_from(">H", data, 4)[0]
    if not 1 <= count <= 32 or 12 + count * 16 > len(data):
        raise SourceFontError("font_table_inventory_invalid")
    result, spans = {}, []
    for index in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        tag = tag.decode("ascii")
        if (tag in result or offset < 12 + count * 16 or offset + length > len(data)
                or any(max(a, offset) < min(b, offset + length) for a, b in spans)):
            raise SourceFontError("font_table_bounds_unproved")
        result[tag] = data[offset:offset + length]
        spans.append((offset, offset + length))
    if _FORBIDDEN_TABLES & result.keys() or not {"head", "maxp", "hhea", "hmtx", "loca", "glyf"} <= result.keys():
        raise SourceFontError("complex_font_program_unsupported")
    return result


def _global_profile(font, tables):
    maximum = vars(font["maxp"])
    fields = {key: value for key, value in maximum.items() if not key.startswith("_") and key != "numGlyphs"}
    head = font["head"]
    return {"units_per_em": head.unitsPerEm, "head_flags": head.flags,
            "mac_style": head.macStyle, "glyph_data_format": head.glyphDataFormat,
            "maxp": fields, "hint_tables": {tag: {
                "bytes": len(tables[tag]), "sha256": _sha(tables[tag])} if tag in tables else None
                for tag in _HINT_TABLES}}


def _glyph_observation(font, gid, tables, remaining):
    locations = font["loca"].locations
    start, end = locations[gid], locations[gid + 1]
    if not 0 <= start <= end <= len(tables["glyf"]):
        raise SourceFontError("glyph_source_bounds_invalid")
    record = tables["glyf"][start:end]
    expected_points = 0
    if record:
        if len(record) < 10:
            raise SourceFontError("glyph_header_invalid")
        contours = struct.unpack_from(">h", record)[0]
        if not 0 <= contours <= 256:
            raise SourceFontError("composite_or_complex_glyph_unsupported")
        cursor = 10 + 2 * contours
        if cursor + 2 > len(record):
            raise SourceFontError("glyph_contours_invalid")
        endpoints = struct.unpack_from(">" + "H" * contours, record, 10)
        if any(a >= b for a, b in zip(endpoints, endpoints[1:])):
            raise SourceFontError("glyph_contour_order_invalid")
        expected_points = endpoints[-1] + 1 if endpoints else 0
        instructions = struct.unpack_from(">H", record, cursor)[0]
        if (expected_points > min(MAX_GLYPH_POINTS, remaining) or instructions > 4096
                or cursor + 2 + instructions > len(record)):
            raise SourceFontError("glyph_point_or_instruction_budget_exceeded")
    name = font.getGlyphName(gid)
    glyph = font["glyf"][name]
    if glyph.isComposite():
        raise SourceFontError("composite_or_complex_glyph_unsupported")
    coordinates, ends, flags = glyph.getCoordinates(font["glyf"])
    if len(coordinates) != expected_points:
        raise SourceFontError("glyph_point_inventory_invalid")
    points = [list(point) for point in coordinates]
    if any(type(v) is not int or abs(v) > (1 << 20) for point in points for v in point):
        raise SourceFontError("glyph_coordinate_domain_unsupported")
    program = getattr(glyph, "program", None)
    instructions = bytes(program.getBytecode()) if program else b""
    outline = {"coordinates": points, "ends": list(ends), "flags": list(flags),
               "bbox": [getattr(glyph, key, None) for key in ("xMin", "yMin", "xMax", "yMax")]}
    design = {"outline_sha256": _sha(_canonical(outline)), "hmtx": list(font["hmtx"][name]),
              "instructions_sha256": _sha(instructions), "instructions_bytes": len(instructions)}
    return {**design, "fingerprint": _sha(_canonical(design)), "points": points,
            "end_points": list(ends), "point_flags": list(flags), "bbox": outline["bbox"]}


def _references():
    from .house_id_georgia_reference import REFERENCE, REFERENCE_SHA256
    if _sha(_canonical(REFERENCE)) != REFERENCE_SHA256:
        raise SourceFontError("trusted_reference_corrupted")
    return REFERENCE


def _unicode_map(data):
    from pdfminer.cmapdb import FileUnicodeMap, CMapParser

    class BoundedMap(FileUnicodeMap):
        def add_cid2unichr(self, cid, code):
            if type(cid) is not int or not 0 <= cid <= 65535 or cid in self.cid2unichr:
                raise SourceFontError("source_ToUnicode_duplicate_or_invalid_CID")
            if len(self.cid2unichr) >= 4096:
                raise SourceFontError("source_ToUnicode_mapping_budget_exceeded")
            super().add_cid2unichr(cid, code)

    class Parser(CMapParser):
        count = 0

        def nexttoken(self):
            self.count += 1
            if self.count > 20_000:
                raise SourceFontError("source_ToUnicode_token_budget_exceeded")
            return super().nexttoken()

        def do_keyword(self, pos, token):
            if token.name == b"usecmap":
                raise SourceFontError("source_ToUnicode_inheritance_unsupported")
            return super().do_keyword(pos, token)

    result = BoundedMap()
    Parser(result, io.BytesIO(data)).run()
    return result.cid2unichr


def observe_house_id_font(font_spec, font_file_bytes: bytes, used_cids) -> dict:
    """Read an actual source font/CID set, returning in-memory matched points.

    The caller must obtain both arguments from its reopened canonical PDF and
    completely enumerate used CIDs. This is not a saved-proof verifier. Missing
    Unicode is allowed only for reference-proven nonnumeric SmallCaps; it never
    invents a display character. Unknown/contradictory glyphs fail the font.
    """
    base = {"status": "unknown", "reason": None, "glyphs": {}, "visibility_proven": False,
            "ID_scope_proven": False}
    if isinstance(font_file_bytes, bytes) and len(font_file_bytes) <= MAX_FONT_BYTES:
        base["source_font_sha256"] = _sha(font_file_bytes)
    try:
        import fontTools
        from fontTools.ttLib import TTFont
        if fontTools.__version__ != "4.62.1":
            raise SourceFontError("source_font_parser_version_unsupported")
        if not isinstance(font_file_bytes, bytes) or len(font_file_bytes) > MAX_FONT_BYTES:
            raise SourceFontError("font_stream_byte_budget_exceeded")
        if (not isinstance(used_cids, (list, tuple, set, frozenset)) or not used_cids
                or len(used_cids) > MAX_USED_CIDS or any(type(v) is not int or not 0 <= v <= 65535 for v in used_cids)):
            raise SourceFontError("used_CID_inventory_invalid_or_over_budget")
        used_cids = sorted(set(used_cids))
        spec = _resolve(font_spec)
        if (not isinstance(spec, dict) or set(spec) - {"Type", "Subtype", "BaseFont", "Encoding", "DescendantFonts", "ToUnicode"}
                or _name(spec.get("Type")) != "Font" or _name(spec.get("Subtype")) != "Type0"
                or _name(spec.get("Encoding")) != "Identity-H"):
            raise SourceFontError("source_horizontal_Type0_font_required")
        children = _resolve(spec.get("DescendantFonts"))
        if not isinstance(children, list) or len(children) != 1:
            raise SourceFontError("source_descendant_font_inventory_invalid")
        child = _resolve(children[0])
        if not isinstance(child, dict) or _name(child.get("Subtype")) != "CIDFontType2":
            raise SourceFontError("source_CIDFontType2_required")
        descriptor = _resolve(child.get("FontDescriptor"))
        if not isinstance(descriptor, dict) or _stream(descriptor.get("FontFile2"), MAX_FONT_BYTES) != font_file_bytes:
            raise SourceFontError("font_program_differs_from_source_FontFile2")
        if any(key in descriptor for key in ("FontFile", "FontFile3")):
            raise SourceFontError("alternate_font_program_unsupported")
        unicode_data = _stream(spec.get("ToUnicode"), 64 * 1024)
        unicode_map = _unicode_map(unicode_data)
        gid_map = _resolve(child.get("CIDToGIDMap"))
        if _name(gid_map) == "Identity":
            gids = {cid: cid for cid in used_cids}
            map_binding = "Identity"
        else:
            mapping = _stream(gid_map, 2 * 65536)
            if len(mapping) % 2 or any(2 * cid + 2 > len(mapping) for cid in used_cids):
                raise SourceFontError("source_CIDToGIDMap_incomplete")
            gids = {cid: int.from_bytes(mapping[2 * cid:2 * cid + 2], "big") for cid in used_cids}
            map_binding = _sha(mapping)
        tables = _directory(font_file_bytes)
        reference = _references()
        with TTFont(io.BytesIO(font_file_bytes), lazy=True) as font:
            glyph_count = font["maxp"].numGlyphs
            if not 1 <= glyph_count <= MAX_FONT_GLYPHS or any(gid >= glyph_count for gid in gids.values()):
                raise SourceFontError("source_glyph_inventory_invalid_or_over_budget")
            if font["head"].indexToLocFormat not in (0, 1) or font["head"].glyphDataFormat != 0:
                raise SourceFontError("source_glyph_format_unsupported")
            # Names and source cmap are not glyph-selection or semantic
            # authority. Supply ordinal-only names before loading glyf/hmtx.
            font.setGlyphOrder([f"gid{gid:05}" for gid in range(glyph_count)])
            profile = _global_profile(font, tables)
            families = [item for item in reference["families"] if item["global_profile"] == profile]
            if len(families) != 1:
                raise SourceFontError("trusted_font_global_program_unmatched")
            family = families[0]
            if len(family["glyphs"]) > 256:
                raise SourceFontError("trusted_reference_glyph_budget_exceeded")
            indexed = {entry["fingerprint"]: entry for entry in family["glyphs"]}
            if len(indexed) != len(family["glyphs"]):
                raise SourceFontError("trusted_reference_glyph_ambiguous")
            observed, remaining, by_gid = {}, MAX_TOTAL_POINTS, {}
            for cid in used_cids:
                gid = gids[cid]
                if gid not in by_gid:
                    by_gid[gid] = _glyph_observation(font, gid, tables, remaining)
                    remaining -= len(by_gid[gid]["points"])
                glyph = by_gid[gid]
                entry = indexed.get(glyph["fingerprint"])
                if entry is None:
                    raise SourceFontError("trusted_used_glyph_unmatched_or_ambiguous")
                source_unicode = unicode_map.get(cid)
                declared_numeric = isinstance(source_unicode, str) and any(c.isnumeric() for c in source_unicode)
                actual_numeric = any(chr(code).isnumeric() for code in entry["cmap_unicode"])
                missing = source_unicode in (None, "\x00")
                smallcap_proven = (entry["smallcaps_nonnumeric"] and bool(entry["smallcaps_origin_unicode"])
                    and bool(entry["trusted_GSUB_features"])
                    and set(entry["trusted_GSUB_features"]) <= {"smcp", "c2sc"}
                    and all(32 <= c < 127 and chr(c).isalpha() for c in entry["smallcaps_origin_unicode"]))
                if missing:
                    if actual_numeric or not smallcap_proven:
                        raise SourceFontError("source_numeric_or_unexplained_Unicode_missing")
                    kind = "known_nonnumeric_missing_unicode"
                elif (len(source_unicode) != 1 or ord(source_unicode) not in entry["cmap_unicode"]):
                    # A valid trusted small-cap shape does not turn a missing
                    # label into invented external data, including a number.
                    raise SourceFontError("source_numeric_Unicode_conflict" if declared_numeric or actual_numeric
                                          else "source_glyph_Unicode_conflict")
                else:
                    kind = "numeric" if actual_numeric else "nonnumeric"
                observed[cid] = {"cid": cid, "gid": gid, **glyph, "source_unicode": source_unicode,
                    "unicode_status": "absent" if source_unicode is None else "literal_NUL" if source_unicode == "\x00" else "mapped",
                    "semantic_kind": kind, "numeric": actual_numeric, "units_per_em": profile["units_per_em"],
                    "reference_glyph_fingerprint": entry["fingerprint"]}
        return {**base, "status": "observed", "reason": None, "glyphs": observed,
                "source_ToUnicode_sha256": _sha(unicode_data), "source_CIDToGIDMap": map_binding,
                "reference_profile": family["profile_id"], "reference_sha256": _sha(_canonical(reference)),
                "source_global_profile_sha256": _sha(_canonical(profile)),
                "matched_used_CID_count": len(observed), "point_count": MAX_TOTAL_POINTS - remaining}
    except SourceFontError as error:
        return {**base, "reason": str(error)}
    except ImportError:
        return {**base, "reason": "source_font_dependencies_unavailable"}
    except Exception:
        return {**base, "reason": "source_font_program_invalid"}
