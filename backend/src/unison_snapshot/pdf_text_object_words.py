"""Source PDF text-object observations; neither OCR nor repaired Unicode."""
from __future__ import annotations

from contextlib import closing
import ctypes
import hashlib
import json
import math
from pathlib import Path


def observe_pdf_text_objects(source: Path, *, expected_sha256: str, page_number: int) -> dict:
    """Keep raw object strings and tight PDFium bounds in source object order.

    This first contract supports unrotated pages and top-level text only. Mode
    3 and inactive objects remain in the inventory with explicit exclusions;
    clipping and Form XObjects reject the observation, rather than omit text.
    No whitespace, Unicode, punctuation or object order is normalized.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    source = Path(source)
    data = source.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != expected_sha256:
        raise ValueError("source PDF SHA differs")
    objects, words, nontext = [], [], []
    with pdfium.PdfDocument(data) as document:
        if page_number > len(document):
            raise ValueError("page_number exceeds source page count")
        page_count = len(document)
        with closing(document[page_number - 1]) as page:
            rotation = page.get_rotation()
            if rotation != 0:
                raise ValueError("nonzero page rotation is unsupported by text-object words/v1")
            media, crop = list(page.get_mediabox()), list(page.get_cropbox())
            left, bottom, right, top = media
            width, height = right - left, top - bottom
            if any(not math.isfinite(v) for v in media + crop) or min(width, height) <= 0:
                raise ValueError("source page boxes are invalid")
            with closing(page.get_textpage()) as textpage:
                for index, obj in enumerate(page.get_objects(max_depth=1, textpage=textpage)):
                    if obj.type == raw.FPDF_PAGEOBJ_FORM:
                        raise ValueError("nested Form text requires a separate transform/selection contract")
                    bounds = list(obj.get_bounds())
                    if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                        nontext.append({"source_object_index": index, "kind": type(obj).__name__,
                                        "bounds_pdf_bottom_left": bounds})
                        continue
                    active = ctypes.c_int()
                    if not raw.FPDFPageObj_GetIsActive(obj, active):
                        raise ValueError("source text object activity is unavailable")
                    mode = raw.FPDFTextObj_GetTextRenderMode(obj)
                    if mode not in (0, 1, 2, 3):
                        raise ValueError("text clipping/unknown render mode is unsupported")
                    matrix = obj.get_matrix()
                    matrix_values = [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f]
                    if any(not math.isfinite(v) for v in bounds + matrix_values):
                        raise ValueError("source text object geometry is nonfinite")
                    text = obj.extract()
                    font = obj.get_font()
                    box = [bounds[0] - left, top - bounds[3], bounds[2] - left, top - bounds[1]]
                    reasons = []
                    if not active.value: reasons.append("source_object_inactive")
                    if mode == 3: reasons.append("source_text_render_mode_invisible")
                    issues = []
                    if not text: issues.append("source_object_decoded_text_empty")
                    if box[0] >= box[2] or box[1] >= box[3]: issues.append("source_object_bounds_degenerate")
                    if not (0 <= box[0] <= box[2] <= width and 0 <= box[1] <= box[3] <= height):
                        issues.append("source_object_bounds_outside_mediabox")
                    record = {"source_object_index": index, "raw_text": text,
                              "bounds_pdf_bottom_left": bounds, "bbox_displayed_mediabox": box,
                              "matrix_pdf_bottom_left": matrix_values,
                              "font_base_name": font.get_base_name(), "font_family_name": font.get_family_name(),
                              "font_embedded": font.is_embedded, "font_size": obj.get_font_size(),
                              "text_render_mode": mode, "source_object_active": bool(active.value),
                              "exclusion_reasons": reasons, "observation_issues": issues,
                              "source_text_index": len(objects)}
                    objects.append(record)
                    if not reasons:
                        # Includes empty/degenerate/outside boxes with issues:
                        # downstream callers must retain their unknown status,
                        # never silently drop objects to make a clean page.
                        words.append({"text": text, "x0": box[0], "top": box[1],
                                      "x1": box[2], "bottom": box[3], "size": obj.get_font_size(),
                                      "source_object_index": index, "source_text_index": len(objects) - 1,
                                      "source_raw_text": text, "observation_issues": list(issues),
                                      "ocr_confidence": 0, "confidence_basis": "not_applicable_not_ocr"})
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
        raise ValueError("source PDF changed during observation")
    manifest_sha = hashlib.sha256(json.dumps(objects, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": "pdf-source-text-object-words/v1", "source_sha256": sha,
            "page_number": page_number, "document_page_count": page_count,
            "coordinate_frame": "displayed_mediabox", "width": width, "height": height,
            "source_mediabox_pdf_bottom_left": media, "source_cropbox_pdf_bottom_left": crop,
            "source_rotation_degrees": rotation,
            "source_pdf_to_displayed_mediabox_affine": [1, 0, 0, -1, -left, top],
            "bbox_basis": "FPDFPageObj_GetBounds_original_text_object",
            "text_basis": "FPDFTextObj_GetText_original_object_no_normalization",
            "ordering": "source_object_index_no_logical_line_or_reading_order_inferred",
            "objects": objects, "words": words, "nontext_objects": nontext,
            "text_object_manifest_sha256": manifest_sha, "source_pdf_unchanged": True,
            "confidence_basis": "not_applicable_not_ocr", "is_ocr": False,
            "complete_financial_text_coverage": "not_established",
            "limitations": ["source Unicode/CID mapping can be incorrect despite readable glyphs",
                            "render-enabled objects can be covered by images, paths or other objects",
                            "non-text objects can contain financial glyphs and punctuation",
                            "raw object order is not logical line/field order; no top-bucket sorting",
                            "no slot assignment, financial qualification or source completeness is claimed"]}


class TextObjectWordPage:
    """Only the extract_words portion of the OcrPage/pdfplumber interface.

    Source object runs are not split into words and do not have inferred line
    IDs. In particular, a short dash must not be top-bucketed into another row.
    """

    def __init__(self, observation: dict):
        if observation.get("schema_version") != "pdf-source-text-object-words/v1":
            raise ValueError("unsupported source text-object observation")
        self.width, self.height = observation["width"], observation["height"]
        self._words = observation["words"]

    def extract_words(self, **_kwargs):
        # Serialization roundtrip prevents consumers mutating nested issues.
        return json.loads(json.dumps(self._words, ensure_ascii=False))

    def extract_text_lines(self):
        raise ValueError("source text objects do not establish logical line order")

    def extract_text(self):
        raise ValueError("source text objects do not establish logical line order")


def validate_saved_native_words(source: Path, *, expected_sha256: str,
                                page_number: int, words: list[dict]) -> dict:
    """Validate exact native records in a mixed saved page, without line IDs.

    OCR header words retain their own OCR records. Native body records must be
    byte-for-byte-equivalent JSON values from the source producer, rather than
    self-reported text/object labels or fabricated OCR line references.
    """
    observed = observe_pdf_text_objects(source, expected_sha256=expected_sha256,
                                        page_number=page_number)
    expected = {w["source_object_index"]: w for w in observed["words"]}
    count = 0
    for word in words:
        if "source_object_index" not in word:
            continue
        if (type(word["source_object_index"]) is not int or
                word != expected.get(word["source_object_index"])):
            raise ValueError("saved native word differs from the original text object")
        count += 1
    return {"source_sha256":expected_sha256,"page_number":page_number,
            "native_word_count":count,"source_text_manifest_sha256":observed["text_object_manifest_sha256"],
            "line_order":"not_inferred","ocr_confidence":"not_applicable_not_ocr"}
