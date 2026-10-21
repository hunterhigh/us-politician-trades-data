"""Bounded local Tesseract OCR with PDF-coordinate word geometry.

The helpers in this module never fetch a document.  Callers must bind an
already archived PDF to its expected hash before rendering any pages.
"""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import hashlib
import json
import re
import shutil
import subprocess
import tempfile


class OcrGeometryError(ValueError):
    """The local OCR engine or its deterministic geometry output is invalid."""


class OcrPage:
    """Small pdfplumber-compatible view over one OCR word page."""

    def __init__(self, *, width: float, height: float, words: list[dict], render_frame: dict | None = None):
        self.width = width
        self.height = height
        self._words = words
        self.render_frame = deepcopy(render_frame)

    def extract_words(self, **_kwargs) -> list[dict]:
        return [dict(word) for word in self._words]

    def extract_text_lines(self) -> list[dict]:
        if any("source_object_index" in word for word in self._words):
            raise OcrGeometryError("native text objects do not establish logical OCR lines")
        grouped: dict[tuple[int, int, int], list[dict]] = {}
        for word in self._words:
            key = (word["block_num"], word["par_num"], word["line_num"])
            grouped.setdefault(key, []).append(word)
        lines = []
        for words in grouped.values():
            ordered = sorted(words, key=lambda word: (word["x0"], word["top"]))
            lines.append({
                "text": " ".join(word["text"] for word in ordered),
                "top": min(word["top"] for word in ordered),
                "words": [dict(word) for word in ordered],
                "ocr_confidence": round(
                    sum(word["ocr_confidence"] for word in ordered) / len(ordered), 2),
            })
        return sorted(lines, key=lambda line: (line["top"], line["text"]))

    def extract_text(self) -> str:
        return "\n".join(line["text"] for line in self.extract_text_lines())



def _mediabox_frame(page, image, *, source_sha256: str, page_number: int,
                    resolution: int, words: list[dict]) -> dict:
    if (not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            or type(page_number) is not int or page_number < 1
            or type(resolution) is not int or not 150 <= resolution <= 400):
        raise OcrGeometryError("Saved MediaBox render frame binding is invalid")
    # TSV coordinates begin at zero. Nonzero displayed origins need a separate
    # transform and are deliberately outside this proof's supported frame.
    if tuple(page.bbox[:2]) != (0, 0):
        raise OcrGeometryError("Saved MediaBox render origin is unsupported")
    return {"schema_version":"source-pdf-mediabox-render/v1",
        "source_sha256":source_sha256,"page_number":page_number,
        "coordinate_frame":"displayed_mediabox","force_mediabox":True,
        "media_box":list(page.mediabox),"crop_box":list(page.cropbox),
        "displayed_page_bbox":list(page.bbox),"rotation":page.rotation,
        "resolution":resolution,"antialias":True,"pixel_mode":"L",
        "pixel_size":[image.width,image.height],
        "pixel_sha256":hashlib.sha256(image.tobytes()).hexdigest(),
        "words_sha256":hashlib.sha256(json.dumps(words,sort_keys=True,separators=(",",":"),
            ensure_ascii=False,allow_nan=False).encode()).hexdigest()}


def mediabox_render_receipt(page, *, source_sha256: str, page_number: int,
                             resolution: int, words: list[dict]) -> dict:
    """Freeze the actual original-PDF frame; never performs OCR or changes words."""
    if type(resolution) is not int or not 150 <= resolution <= 400:
        raise OcrGeometryError("Saved MediaBox render DPI is invalid")
    image=page.to_image(resolution=resolution,antialias=True,force_mediabox=True).original.convert("L")
    return _mediabox_frame(page,image,source_sha256=source_sha256,page_number=page_number,
                          resolution=resolution,words=words)


def validate_mediabox_render_receipt(page, saved: OcrPage, *, source_sha256: str,
                                      page_number: int) -> None:
    receipt=saved.render_frame
    if not isinstance(receipt,dict):
        raise OcrGeometryError("Saved OCR MediaBox render proof is missing")
    expected=mediabox_render_receipt(page,source_sha256=source_sha256,page_number=page_number,
        resolution=receipt.get("resolution"),words=saved.extract_words())
    if json.dumps(receipt,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True,allow_nan=False):
        raise OcrGeometryError("Saved OCR MediaBox render proof differs from original page")


def words_from_tesseract_tsv(value: bytes, *, points_per_pixel: float) -> list[dict]:
    """Convert Tesseract TSV bytes to stable PDF-coordinate word records."""

    text = value.decode("utf-8", errors="replace")
    words: list[dict] = []
    try:
        lines = text.splitlines()
        headers = lines[0].split("\t") if lines else []
        if headers != ["level", "page_num", "block_num", "par_num", "line_num",
                       "word_num", "left", "top", "width", "height", "conf", "text"]:
            raise OcrGeometryError("Tesseract returned invalid TSV word geometry")
        for line in lines[1:]:
            fields = line.split("\t", len(headers) - 1)
            if len(fields) != len(headers):
                raise OcrGeometryError("Tesseract returned invalid TSV word geometry")
            row = dict(zip(headers, fields, strict=True))
            token = " ".join((row.get("text") or "").replace("\ufffd", " ").split())
            if row.get("level") != "5" or not token:
                continue
            confidence = float(row["conf"])
            if confidence < 0:
                continue
            left = float(row["left"])
            top = float(row["top"])
            width = float(row["width"])
            height = float(row["height"])
            words.append({
                "text": token,
                "x0": left * points_per_pixel,
                "x1": (left + width) * points_per_pixel,
                "top": top * points_per_pixel,
                "bottom": (top + height) * points_per_pixel,
                "size": max(height * points_per_pixel, 1.0),
                "ocr_confidence": confidence,
                "block_num": int(row["block_num"]),
                "par_num": int(row["par_num"]),
                "line_num": int(row["line_num"]),
            })
    except OcrGeometryError:
        raise
    except (KeyError, TypeError, ValueError):
        raise OcrGeometryError("Tesseract returned invalid TSV word geometry") from None
    return words


def _engine(executable: str | None) -> tuple[str, str]:
    resolved = shutil.which(executable or "tesseract")
    if not resolved:
        raise OcrGeometryError("Tesseract OCR engine is unavailable")
    try:
        completed = subprocess.run(
            [resolved, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        raise OcrGeometryError("Tesseract OCR engine version is unavailable") from None
    version = completed.stdout.decode("utf-8", errors="replace").splitlines()
    label = version[0].strip() if version else ""
    if completed.returncode or not label.casefold().startswith("tesseract "):
        raise OcrGeometryError("Tesseract OCR engine version is unavailable")
    return resolved, label


def ocr_pdf_pages(document, *, executable: str | None = None, resolution: int = 200,
                  page_numbers: list[int] | None = None,
                  max_pages: int = 100, force_mediabox: bool = False,
                  source_sha256: str | None = None) -> tuple[list[OcrPage], str]:
    """Render and OCR a bounded, one-based page selection from an open PDF."""

    if type(resolution) is not int or not 150 <= resolution <= 400:
        raise OcrGeometryError("OCR resolution is outside bounds")
    total = len(document.pages)
    selected = list(range(1, total + 1)) if page_numbers is None else list(page_numbers)
    if (not selected or len(selected) > max_pages or len(set(selected)) != len(selected) or
            any(type(number) is not int or not 1 <= number <= total for number in selected)):
        raise OcrGeometryError("OCR page selection is outside bounds")
    resolved, engine = _engine(executable)
    pages: list[OcrPage] = []
    with tempfile.TemporaryDirectory(prefix="whitehouse-ocr-") as folder:
        for page_number in selected:
            page = document.pages[page_number - 1]
            image_path = Path(folder) / f"page-{page_number:04d}.png"
            render_options = {"force_mediabox": True} if force_mediabox else {}
            image = page.to_image(resolution=resolution, antialias=True, **render_options).original.convert("L")
            image.save(image_path, format="PNG")
            try:
                completed = subprocess.run(
                    [resolved, str(image_path), "stdout", "--dpi", str(resolution),
                     "-l", "eng", "tsv"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError):
                raise OcrGeometryError(f"Tesseract OCR failed on page {page_number}") from None
            if completed.returncode:
                raise OcrGeometryError(f"Tesseract OCR failed on page {page_number}")
            words = words_from_tesseract_tsv(
                completed.stdout, points_per_pixel=72.0 / resolution)
            frame = (_mediabox_frame(page,image,source_sha256=source_sha256,page_number=page_number,
                resolution=resolution,words=words) if force_mediabox and source_sha256 is not None else None)
            pages.append(OcrPage(width=page.width, height=page.height, words=words, render_frame=frame))
    return pages, engine


def ocr_rendered_image(image, *, executable: str | None = None,
                       resolution: int = 200,
                       page_segmentation: int | None = None) -> tuple[OcrPage, str]:
    """OCR one already rendered image in its own (possibly rotated) frame."""
    if type(resolution) is not int or not 150 <= resolution <= 400:
        raise OcrGeometryError("OCR resolution is outside bounds")
    if page_segmentation not in (None, 6):
        raise OcrGeometryError("unsupported OCR page segmentation mode")
    resolved, engine = _engine(executable)
    with tempfile.TemporaryDirectory(prefix="whitehouse-ocr-image-") as folder:
        image_path = Path(folder) / "page.png"
        image.convert("L").save(image_path, format="PNG")
        try:
            command = [resolved, str(image_path), "stdout", "--dpi", str(resolution),
                       "-l", "eng"]
            if page_segmentation is not None:
                command.extend(["--psm", str(page_segmentation)])
            completed = subprocess.run(
                [*command, "tsv"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            raise OcrGeometryError("Tesseract OCR failed on rendered image") from None
        if completed.returncode:
            raise OcrGeometryError("Tesseract OCR failed on rendered image")
        words = words_from_tesseract_tsv(
            completed.stdout, points_per_pixel=72.0 / resolution)
    return OcrPage(width=image.width * 72.0 / resolution,
                   height=image.height * 72.0 / resolution,
                   words=words), engine
