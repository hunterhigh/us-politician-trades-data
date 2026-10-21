"""Optional, source-replayed glyph evidence; no raw-text rewriting or OCR.

The sole interpretation in v1 is a slash-shaped prefix in a source ligature
whose Unicode is '12', with an independently two-shaped suffix. It supplies an
additional reading for a confirmed Date field, never a selected fact.
"""
from __future__ import annotations

import hashlib
import base64
import io
import json
from pathlib import Path

from .pdf_text_object_words import observe_pdf_text_objects


GLYPH_RULE = "source-glyph-slash-prefix-two-suffix/v1"
PARAMETERS = {"em_pixels": 256, "alpha_ink_at_least": 128,
              "prefix_fit_height_fraction": [.20, .80],
              "prefix_slope_min": .15, "prefix_slope_max": .45,
              "prefix_residual_over_height_max": .03,
              "whole_width_over_height_min": .90,
              "whole_width_over_height_max": 1.50,
              "suffix_x_start_fraction": .40,
              "suffix_width_over_height_min": .40,
              "suffix_bottom_width_min": .90,
              "suffix_middle_width_max": .65,
              "suffix_upper_width_min": .70}


def _parameters():
    return json.loads(json.dumps(PARAMETERS))


def glyph_shape_evidence(alpha) -> dict:
    """Measure source glyph alpha only; no word/date expectation is an input."""
    import numpy as np
    mask = np.asarray(alpha) >= PARAMETERS["alpha_ink_at_least"]
    if mask.ndim != 2:
        raise ValueError("glyph alpha must be a two-dimensional array")
    ys, xs = np.nonzero(mask)
    if not len(xs): return {"status": "unsupported", "reason": "source_glyph_has_no_ink"}
    mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = mask.shape
    low, high = PARAMETERS["prefix_fit_height_fraction"]
    pairs = [(y, int(np.flatnonzero(mask[y])[0])) for y in range(height)
             if low * (height - 1) <= y <= high * (height - 1) and mask[y].any()]
    if len(pairs) < 8: return {"status": "unsupported", "reason": "source_glyph_too_small"}
    yy, xx = np.asarray(pairs).T
    slope, intercept = np.polyfit(yy, xx, 1)
    residual = float(np.std(xx - (slope * yy + intercept)))
    right = mask[:, int(width * PARAMETERS["suffix_x_start_fraction"]):]
    ry, rx = np.nonzero(right)
    if not len(rx): return {"status": "unsupported", "reason": "suffix_has_no_ink"}
    right = right[ry.min():ry.max() + 1, rx.min():rx.max() + 1]
    rh, rw = right.shape

    def band_width(a, b):
        widths = []
        for row in right[int(a * rh):max(int(a * rh) + 1, int(b * rh))]:
            ink = np.flatnonzero(row)
            if len(ink): widths.append((int(ink[-1]) - int(ink[0]) + 1) / rw)
        return float(np.mean(widths)) if widths else 0.

    result = {"status": "measured", "prefix_slope_dx_dy": float(slope),
              "whole_width_over_height": width / height,
              "prefix_residual_over_height": residual / height,
              "suffix_width_over_height": rw / rh,
              "suffix_bottom_width": band_width(0., .08),
              "suffix_middle_width": band_width(.35, .55),
              "suffix_upper_width": band_width(.70, .90),
              "glyph_ink_size": [width, height], "suffix_ink_size": [rw, rh]}
    result["slash_prefix_supported"] = bool(
        PARAMETERS["prefix_slope_min"] <= slope <= PARAMETERS["prefix_slope_max"] and
        result["prefix_residual_over_height"] <= PARAMETERS["prefix_residual_over_height_max"])
    result["two_suffix_supported"] = bool(
        result["suffix_width_over_height"] >= PARAMETERS["suffix_width_over_height_min"] and
        result["suffix_bottom_width"] >= PARAMETERS["suffix_bottom_width_min"] and
        result["suffix_middle_width"] <= PARAMETERS["suffix_middle_width_max"] and
        result["suffix_upper_width"] >= PARAMETERS["suffix_upper_width_min"])
    return result


def interpret_glyph_unicode(raw_unicode: str, alpha) -> dict:
    evidence = glyph_shape_evidence(alpha)
    base = {"raw_unicode": raw_unicode, "rule": GLYPH_RULE,
            "parameters": _parameters(), "shape_evidence": evidence}
    if (raw_unicode == "12" and evidence.get("slash_prefix_supported") and
            evidence.get("two_suffix_supported") and
            PARAMETERS["whole_width_over_height_min"] <= evidence["whole_width_over_height"] <= PARAMETERS["whole_width_over_height_max"]):
        return {**base, "status": "interpreted", "interpreted_unicode": "/2"}
    return {**base, "status": "unsupported"}


def _cff_index(data, position):
    count = int.from_bytes(data[position:position + 2], "big"); position += 2
    if not count: return [], position
    size = data[position]; position += 1
    if not 1 <= size <= 4: raise ValueError("invalid CFF INDEX offset size")
    offsets = [int.from_bytes(data[position + i * size:position + (i + 1) * size], "big")
               for i in range(count + 1)]
    base = position + (count + 1) * size
    if offsets[0] != 1 or offsets != sorted(offsets) or base + offsets[-1] - 1 > len(data):
        raise ValueError("invalid CFF INDEX")
    return [data[base + offsets[i] - 1:base + offsets[i + 1] - 1] for i in range(count)], base + offsets[-1] - 1


def _cff_dict(data):
    result, stack, i = {}, [], 0
    while i < len(data):
        token = data[i]; i += 1
        if token == 28: stack.append(int.from_bytes(data[i:i + 2], "big", signed=True)); i += 2
        elif token == 29: stack.append(int.from_bytes(data[i:i + 4], "big", signed=True)); i += 4
        elif token == 30:
            while True:
                value = data[i]; i += 1
                if value & 15 == 15 or value >> 4 == 15: break
            stack.append(None)  # Real-valued keys are not used for charset offsets.
        elif 32 <= token <= 246: stack.append(token - 139)
        elif 247 <= token <= 250: stack.append((token - 247) * 256 + data[i] + 108); i += 1
        elif 251 <= token <= 254: stack.append(-(token - 251) * 256 - data[i] - 108); i += 1
        elif token <= 21:
            key = (12, data[i]) if token == 12 else token
            if token == 12: i += 1
            result[key] = stack; stack = []
        else: raise ValueError("unsupported CFF DICT token")
    return result


def cff_cid_to_gid(data: bytes) -> dict[int, int]:
    """Read the actual CID charset; never assume CID == GID."""
    if not data or data[0] != 1: raise ValueError("only CFF1 CID fonts are supported")
    _, position = _cff_index(data, data[2]); tops, _ = _cff_index(data, position)
    if len(tops) != 1: raise ValueError("ambiguous CFF font set")
    top = _cff_dict(tops[0])
    if (12, 30) not in top: raise ValueError("font is not a CID-keyed CFF")
    chars, _ = _cff_index(data, top[17][0]); offset = top[15][0]
    fmt = data[offset]; offset += 1; cids = [0]
    while len(cids) < len(chars):
        first = int.from_bytes(data[offset:offset + 2], "big"); offset += 2
        if fmt == 0: cids.append(first)
        elif fmt in (1, 2):
            size = fmt; count = int.from_bytes(data[offset:offset + size], "big"); offset += size
            cids.extend(range(first, first + count + 1))
        else: raise ValueError("unsupported CFF charset")
    if len(cids) != len(chars) or len(set(cids)) != len(cids): raise ValueError("ambiguous CFF CID charset")
    return {cid: gid for gid, cid in enumerate(cids)}


def observe_source_glyph_interpretations(source: Path, *, expected_sha256: str, page_number: int) -> dict:
    """Replay original content CIDs, font program and object origin before interpreting.

    Interpretations require Date-role binding by the consuming reader. All raw
    strings, glyph IDs and text-object boxes are retained unchanged. This does
    not select a value or arbitrate another recognized date observation.
    """
    import numpy as np
    import pymupdf
    from pdfminer.pdfdevice import PDFTextDevice
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdftypes import resolve1

    source = Path(source); data = source.read_bytes()
    observation = observe_pdf_text_objects(source, expected_sha256=expected_sha256, page_number=page_number)

    class Device(PDFTextDevice):
        def __init__(self, manager): super().__init__(manager); self.rows = []
        def render_string(self, state, sequence, ncs=None, graphicstate=None):
            glyphs = []
            for token in sequence:
                if isinstance(token, bytes):
                    for cid in state.font.decode(token):
                        try: text = state.font.to_unichr(cid)
                        except Exception: text = None
                        glyphs.append({"cid": cid, "raw_unicode": text})
            self.rows.append({"font": str(state.font.fontname), "matrix": list(state.matrix),
                              "linematrix": list(state.linematrix), "ctm": list(self.ctm),
                              "font_size": state.fontsize, "rise": state.rise,
                              "raw": "".join(g["raw_unicode"] if g["raw_unicode"] is not None else "\ufffd" for g in glyphs),
                              "glyphs": glyphs})

    selected = next(PDFPage.get_pages(io.BytesIO(data), pagenos={page_number - 1}))
    fonts = {}
    for resource, reference in resolve1(selected.resources.get("Font", {})).items():
        dictionary = resolve1(reference)
        if str(dictionary.get("Encoding")) != "/'Identity-H'": continue
        descendants = resolve1(dictionary.get("DescendantFonts", []))
        if len(descendants) != 1: continue
        descendant = resolve1(descendants[0]); descriptor = resolve1(descendant.get("FontDescriptor", {}))
        program = resolve1(descriptor.get("FontFile3"))
        cmap = resolve1(dictionary.get("ToUnicode"))
        if program is None or cmap is None: continue
        font_data, cmap_data = program.get_data(), cmap.get_data()
        name = dictionary["BaseFont"].name.removesuffix("-Identity-H")
        fonts.setdefault(name, []).append({"resource": str(resource), "data": font_data,
            "font_program_sha256": hashlib.sha256(font_data).hexdigest(),
            "to_unicode_sha256": hashlib.sha256(cmap_data).hexdigest()})
    manager = PDFResourceManager(); device = Device(manager)
    PDFPageInterpreter(manager, device).process_page(selected)
    records = []
    for obj in observation["objects"]:
        if obj["exclusion_reasons"]: continue
        matches = [r for r in device.rows if r["font"] == obj["font_base_name"] and r["raw"] == obj["raw_text"] and
                   abs(r["font_size"] - obj["font_size"]) < .0001 and
                   all(abs(a - b) < .0001 for a, b in zip(r["matrix"], obj["matrix_pdf_bottom_left"]))]
        if not any(g["raw_unicode"] == "12" for r in matches for g in r["glyphs"]): continue
        base = {"source_object_index": obj["source_object_index"], "raw_text": obj["raw_text"],
                "font_base_name": obj["font_base_name"],
                "bbox_displayed_mediabox": obj["bbox_displayed_mediabox"],
                "matrix_pdf_bottom_left": obj["matrix_pdf_bottom_left"], "allowed_field_roles": ["transaction_date"]}
        if len(matches) != 1 or len(fonts.get(obj["font_base_name"], [])) != 1:
            records.append({**base, "status": "unsupported", "reason": "source_content_or_font_not_unique"}); continue
        content = matches[0]; font = fonts[obj["font_base_name"]][0]
        if any(g["raw_unicode"] is None or "\ufffd" in g["raw_unicode"] for g in content["glyphs"]):
            records.append({**base, "status": "unsupported", "reason": "source_unicode_incomplete"}); continue
        if content["ctm"] != [1, 0, 0, 1, 0, 0] or content["linematrix"] != [0, 0] or content["rise"] != 0:
            records.append({**base, "status": "unsupported", "reason": "source_content_transform_not_supported"}); continue
        try: mapping = cff_cid_to_gid(font["data"])
        except (ValueError, IndexError, KeyError):
            records.append({**base, "status": "unsupported", "reason": "source_CID_to_GID_not_supported"}); continue
        embedded = pymupdf.Font(fontbuffer=font["data"]); evidence = []; derived = []; changed = False
        for item in content["glyphs"]:
            text = item["raw_unicode"]; current = {**item, "interpreted_unicode": text}
            if text == "12" and item["cid"] in mapping:
                gid = mapping[item["cid"]]
                raw_pix = pymupdf.mupdf.fz_render_glyph_pixmap(embedded.this, gid,
                    pymupdf.mupdf.FzMatrix(256, 0, 0, 256, 0, 0),
                    pymupdf.mupdf.FzIrect(-2000, -2000, 2000, 2000), 8)
                pix = pymupdf.Pixmap(raw_pix)
                alpha = np.frombuffer(pix.samples, dtype="uint8").reshape(pix.height, pix.width)
                clipped = pix.x <= -2000 or pix.y <= -2000 or pix.x + pix.width >= 2000 or pix.y + pix.height >= 2000
                interpretation = ({"raw_unicode": text, "status": "unsupported", "reason": "source_glyph_raster_clipped"}
                                  if clipped else interpret_glyph_unicode(text, alpha))
                from PIL import Image
                png = io.BytesIO(); Image.fromarray(alpha).save(png, format="PNG")
                current.update(gid=gid, glyph_alpha_sha256=hashlib.sha256(alpha.tobytes()).hexdigest(),
                               glyph_pixel_bbox=[pix.x, pix.y, pix.width, pix.height], interpretation=interpretation)
                current.update(glyph_alpha_png_base64=base64.b64encode(png.getvalue()).decode("ascii"),
                               glyph_pixel_basis="original_font_glyph_at_256px_per_em_font_positive_y",
                               glyph_alpha_polarity="255_ink_coverage_0_transparent")
                if interpretation["status"] == "interpreted":
                    current["interpreted_unicode"] = interpretation["interpreted_unicode"]; changed = True
            evidence.append(current); derived.append(current["interpreted_unicode"] or "")
        records.append({**base, "status": "interpreted" if changed else "unsupported",
                        "interpreted_text": "".join(derived) if changed else None, "glyphs": evidence,
                        "font_program_sha256": font["font_program_sha256"],
                        "to_unicode_sha256": font["to_unicode_sha256"], "font_resource": font["resource"],
                        "source_content_matrix": content["matrix"]})
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("source changed during glyph observation")
    return {"schema_version": "pdf-source-glyph-interpretations/v1", "source_sha256": expected_sha256,
            "page_number": page_number, "coordinate_frame": "displayed_mediabox", "rule": GLYPH_RULE,
            "parameters": _parameters(), "source_text_manifest_sha256": observation["text_object_manifest_sha256"],
            "observations": records, "raw_text_modified": False, "is_ocr": False,
            "requires_confirmed_source_field_role": True, "conflicts_must_remain_unresolved": True,
            "requires_source_visibility_evidence": True,
            "glyph_renderer": {"library": "PyMuPDF", "binding_version": pymupdf.VersionBind,
                               "mupdf_version": pymupdf.VersionFitz, "aa": 8, "em_pixels": 256},
            "complete_financial_text_coverage": "not_established"}
