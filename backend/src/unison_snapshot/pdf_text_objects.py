"""Source-bound PDF object rendering for reading experiments, never completeness.

The original text objects, fonts and transforms are not rewritten. Selection is
an in-memory PDFium activity flag on an imported page, not text extraction and
reconstruction. Non-text evidence is returned alongside the selected rendering.
"""
from __future__ import annotations

import ctypes
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path


def _canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def render_pdf_text_objects(source: Path, *, expected_sha256: str,
                            page_number: int, resolution: int = 300) -> dict:
    """Return same-renderer all/text/non-text PIL images and replay evidence.

    PDF source coordinates remain bottom-left; pixel mapping is obtained from
    PDFium's actual integer render canvas. This helper does not run OCR, save a
    PDF, interpret fields, or certify that the text selection is complete.
    Form XObjects and text clipping are explicitly unsupported for selection.
    Annotations are excluded from every comparison and counted in the receipt.
    """
    import numpy as np
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    if type(resolution) is not int or not 72 <= resolution <= 600:
        raise ValueError("resolution must be an integer within 72..600 DPI")
    source = Path(source)
    original_bytes = source.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    if original_sha != expected_sha256:
        raise ValueError("source PDF SHA differs")

    def manifest(objects):
        records = []
        for index, obj in enumerate(objects):
            active = ctypes.c_int()
            if not raw.FPDFPageObj_GetIsActive(obj, active):
                raise ValueError("PDFium cannot report original object activity")
            matrix = obj.get_matrix()
            item = {"object_index": index, "type": obj.type,
                    "kind": type(obj).__name__, "active": bool(active.value),
                    "bounds_pdf_bottom_left": list(obj.get_bounds()),
                    "matrix": [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f]}
            if obj.type == raw.FPDF_PAGEOBJ_TEXT:
                mode = raw.FPDFTextObj_GetTextRenderMode(obj)
                font = obj.get_font()
                item.update(font_size=obj.get_font_size(), text_render_mode=mode,
                            font_base_name=font.get_base_name(), font_family_name=font.get_family_name(),
                            font_embedded=font.is_embedded)
                # Modes 4..7 change the clipping region; selection would alter
                # more than whether an object contributes visible glyphs.
                if mode not in (0, 1, 2, 3):
                    raise ValueError("text clipping/unknown rendering mode is unsupported")
            if obj.type == raw.FPDF_PAGEOBJ_FORM:
                raise ValueError("nested Form XObjects require a separate selection contract")
            if any(not math.isfinite(v) for v in [*item["matrix"], *item["bounds_pdf_bottom_left"]]):
                raise ValueError("nonfinite source object geometry")
            records.append(item)
        return records

    with pdfium.PdfDocument(original_bytes) as original:
        if page_number > len(original):
            raise ValueError("page_number exceeds original page count")
        with pdfium.PdfDocument.new() as memory:
            memory.import_pages(original, pages=[page_number - 1])
            with closing(original[page_number - 1]) as original_page, closing(memory[0]) as page:
                media = list(original_page.get_mediabox())
                crop = list(original_page.get_cropbox())
                rotation = original_page.get_rotation()
                # Preserve original coordinates; media canvas is a documented
                # view change on the in-memory copy only.
                page.set_cropbox(*media)
                objects = list(page.get_objects(max_depth=1))
                before = manifest(objects)
                if not any(o["type"] == raw.FPDF_PAGEOBJ_TEXT for o in before):
                    raise ValueError("page has no top-level text objects")
                original_objects = manifest(list(original_page.get_objects(max_depth=1)))
                if before != original_objects:
                    raise ValueError("import changed source object geometry or rendering mode")

                def render(target=page):
                    bitmap = target.render(scale=resolution / 72, grayscale=True,
                                         draw_annots=False, may_draw_forms=False)
                    try:
                        image = bitmap.to_pil().copy().convert("L")
                        conv = bitmap.get_posconv(target)
                        width, height = image.size
                        anchors = {"top_left": list(conv.to_page(0, 0)),
                                   "top_right": list(conv.to_page(width, 0)),
                                   "bottom_left": list(conv.to_page(0, height)),
                                   "bottom_right": list(conv.to_page(width, height))}
                    finally:
                        bitmap.close()
                    return image, anchors

                def select(kind):
                    for obj, info in zip(objects, before, strict=True):
                        keep = kind == "all" or ((info["type"] == raw.FPDF_PAGEOBJ_TEXT) == (kind == "text"))
                        if not raw.FPDFPageObj_SetIsActive(obj, info["active"] and keep):
                            raise ValueError("PDFium object selection failed")

                all_image, anchors = render()
                original_page.set_cropbox(*media)
                try:
                    original_image, original_anchors = render(original_page)
                finally:
                    original_page.set_cropbox(*crop)
                if anchors != original_anchors or all_image.tobytes() != original_image.tobytes():
                    raise ValueError("import changed original media-view rendering")
                select("text")
                text_image, text_anchors = render()
                select("nontext")
                nontext_image, nontext_anchors = render()
                select("all")
                restored_image, restored_anchors = render()
                if anchors != text_anchors or anchors != nontext_anchors or anchors != restored_anchors:
                    raise ValueError("selected rendering changed the pixel mapping")
                after = manifest(objects)
                if before != after or all_image.tobytes() != restored_image.tobytes():
                    raise ValueError("selection failed to preserve objects or restore original rendering")
                annotation_count = raw.FPDFPage_GetAnnotCount(original_page)
                if annotation_count < 0:
                    raise ValueError("PDFium cannot count original annotations")
                page_count = len(original)

    if hashlib.sha256(source.read_bytes()).hexdigest() != original_sha:
        raise ValueError("source PDF changed while rendering")
    full, text, nontext = [np.asarray(image) for image in (all_image, text_image, nontext_image)]
    # Diagnostic masks only. No threshold here grants field or page coverage.
    removed_ink = (full < 160) & (text >= 160)
    selected_only_ink = (text < 160) & (full >= 160)
    from PIL import Image
    lost_image = Image.fromarray(np.where(removed_ink, 0, 255).astype("uint8"))
    receipt = {
        "schema_version": "pdf-source-text-object-render/v1",
        "source_sha256": original_sha, "page_number": page_number,
        "document_page_count": page_count, "resolution": resolution,
        "source_pdf_unchanged": True, "selection": "original_top_level_PDFium_text_objects",
        "selection_operation": "in_memory_activity_flags_no_text_rewrite_no_gen_content",
        "source_mediabox_pdf_bottom_left": media, "source_cropbox_pdf_bottom_left": crop,
        "source_rotation_degrees": rotation, "render_cropbox_pdf_bottom_left": media,
        "pixel_size": list(all_image.size), "pixel_edge_to_source_pdf_anchors": anchors,
        "pixel_edge_to_source_pdf_affine": [
            (anchors["top_right"][0] - anchors["top_left"][0]) / all_image.width,
            (anchors["top_right"][1] - anchors["top_left"][1]) / all_image.width,
            (anchors["bottom_left"][0] - anchors["top_left"][0]) / all_image.height,
            (anchors["bottom_left"][1] - anchors["top_left"][1]) / all_image.height,
            *anchors["top_left"]],
        "mapping_basis": "PDFium_DeviceToPage_actual_integer_render_canvas",
        "annotation_count_excluded_from_all_renders": annotation_count,
        "source_object_manifest_sha256": _canonical_sha(before), "objects": before,
        "object_manifest_restored_equal": True, "all_object_render_restored_equal": True,
        "original_media_view_and_imported_all_object_render_equal": True,
        "pixel_sha256": {name: hashlib.sha256(img.tobytes()).hexdigest() for name, img in
                         (("all", all_image), ("text", text_image), ("nontext", nontext_image))},
        "comparison": {"threshold_diagnostic_only": 160,
                       "nontext_dark_pixel_count": int((nontext < 160).sum()),
                       "full_dark_pixels_absent_from_text_selection": int(removed_ink.sum()),
                       "text_dark_pixels_not_visible_in_full": int(selected_only_ink.sum()),
                       "all_vs_text_changed_pixel_count": int((full != text).sum())},
        "complete_page_or_financial_text_coverage": "not_established",
        "limitations": ["non-text images/paths may contain financial glyphs or annotations",
                        "text selection can expose glyphs originally covered by non-text objects",
                        "object preservation does not prove Unicode correctness or OCR accuracy",
                        "no field classification or completeness decision is made"],
    }
    return {"receipt": receipt, "all_image": all_image, "text_image": text_image,
            "nontext_image": nontext_image, "removed_visible_ink_image": lost_image}
