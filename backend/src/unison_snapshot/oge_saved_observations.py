"""Portable saved 278-T readings bound to the canonical archived original.

This prepares the existing materializer's inputs; it performs no OCR and grants
no qualification. The candidate builder still interprets the saved readings.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from .oge_reports import (
    GENERIC_PARSER_VERSION, MAX_PDF_BYTES, MAX_PDF_PAGES, PARSER_VERSION,
    _read_archived_source,
)

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_DOCUMENTS = 12
_DOCUMENT = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = {"observations", "document_page_count", "secondary_observations", "field_interpretation_mode"}
_SECONDARY = {
    f"oge-278t-{field}-column-observation/v{version}"
    for field, versions in (("printed-number", (1,)), ("type", (1,)),
                            ("description", (1,)), ("date", (1,)),
                            ("amount", (1, 2, 3)))
    for version in versions
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Saved OGE JSON contains a duplicate key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Saved OGE JSON contains a non-finite number")


def _json(path: Path) -> dict:
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Saved OGE JSON exceeds byte budget")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("Saved OGE JSON must be an object")
    return value


def _bounded(value):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("Saved OGE observations exceed byte budget")


def _source(evidence_root, metadata_path):
    root, metadata_path = Path(evidence_root).resolve(), Path(metadata_path).resolve()
    document, sha = metadata_path.parent.name, metadata_path.stem
    if (not root.is_dir() or not _DOCUMENT.fullmatch(document) or not _SHA.fullmatch(sha)
            or not metadata_path.is_relative_to(root)
            or metadata_path != (root / f"oge/reports/{document}/{sha}.json").resolve()):
        raise ValueError("Saved OGE metadata must be the canonical archived source")
    metadata = _json(metadata_path)
    relative = f"oge/reports/{document}/{sha}.pdf"
    source = (root / relative).resolve()
    if (not source.is_relative_to(root) or metadata.get("archive_path") != relative
            or metadata.get("document_id") != document or metadata.get("sha256") != sha):
        raise ValueError("Saved OGE archive identity or canonical PDF path differs")
    if source.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Saved OGE original exceeds PDF byte budget")
    archive, source, actual_sha = _read_archived_source(root, metadata_path)
    if actual_sha != sha:
        raise ValueError("Saved OGE original differs from canonical source SHA")
    try:
        import pdfplumber
    except ImportError as exc:
        raise ValueError("Saved OGE source replay requires native-source-proof dependencies") from exc
    with pdfplumber.open(source) as pdf:
        count = len(pdf.pages)
    if not 1 <= count <= MAX_PDF_PAGES:
        raise ValueError("Saved OGE original page count exceeds supported scope")
    return archive, source, count


def _quad_source_path(secondary, source_pdf_path, *, field_interpretation_mode=None):
    """Select only the existing full-source, non-date direct OGE quad reads."""
    selected = [item for item in secondary or []
                if isinstance(item, dict) and item.get("read_mode") == "source_quad_cells/v1"]
    if not selected:
        return None
    if field_interpretation_mode is not None:
        raise ValueError("Saved OGE source quad reads cannot combine with legacy field interpretation")
    fields = {"oge-278t-printed-number-column-observation/v1": "printed_number",
              "oge-278t-type-column-observation/v1": "type",
              "oge-278t-amount-column-observation/v2": "amount"}
    for reading in selected:
        inputs = reading.get("source_quad_input")
        field = fields.get(reading.get("schema_version"))
        if (field is None or not isinstance(inputs, dict) or inputs.get("field") != field
                or inputs.get("layer") != "full_source" or "content_crop_mode" in inputs):
            raise ValueError("Saved OGE source quad reads support only full-source printed number, type and amount")
        pages = inputs.get("saved_pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError("Saved OGE source quad reads require saved source render pages")
        for page in pages:
            receipt = page.get("render_receipt") if isinstance(page, dict) else None
            count = receipt.get("annotation_count_excluded_from_all_renders") if isinstance(receipt, dict) else None
            if type(count) is not int or count != 0:
                raise ValueError("Saved OGE source quad reads cannot exclude source annotations")
    if source_pdf_path is None:
        raise ValueError("Saved OGE source quad reads require the controlled original PDF path")
    return source_pdf_path


def _request(observations, secondary, page_count, sha, *, field_interpretation_mode=None,
             source_pdf_path=None):
    if (not isinstance(observations, list) or not observations
            or any(not isinstance(item, dict) or item.get("source_sha256") != sha
                   or item.get("schema_version") not in {
                       "oge-278t-raster-slot-observation/v2", "oge-278t-raster-slot-observation/v3"}
                   for item in observations)):
        raise ValueError("Saved OGE primary observations differ from source or supported schema")
    if secondary is not None and (not isinstance(secondary, list) or any(
            not isinstance(item, dict) or item.get("source_sha256") != sha
            or item.get("schema_version") not in _SECONDARY for item in secondary)):
        raise ValueError("Saved OGE secondary observations differ from source or supported schema")
    request = {"observations": deepcopy(observations), "document_page_count": page_count}
    if secondary is not None:
        request["secondary_observations"] = deepcopy(secondary)
    if field_interpretation_mode is not None:
        from .oge_278t_field_shadow import LEGACY_AMOUNT_INTERPRETATION_MODE
        if field_interpretation_mode != LEGACY_AMOUNT_INTERPRETATION_MODE:
            raise ValueError("Saved OGE field interpretation mode is unsupported")
        request["field_interpretation_mode"] = field_interpretation_mode
    _bounded(request)
    source = _quad_source_path(secondary, source_pdf_path,
                              field_interpretation_mode=field_interpretation_mode)
    # The controlled path is a replay dependency, never portable saved data.
    from .oge_278t_field_shadow import observe_content_rows
    observe_content_rows(**request, **({"source_pdf_path": source} if source is not None else {}))
    return request


def prepare_saved_oge_observations(*, evidence_root, metadata_path, observations,
                                   secondary_observations=None, document_page_count=None,
                                   field_interpretation_mode=None) -> dict:
    """Return the existing document-to-kwargs mapping without machine paths."""
    _bounded({"observations": observations, "secondary_observations": secondary_observations})
    metadata, source, count = _source(evidence_root, metadata_path)
    if document_page_count is not None and (
            type(document_page_count) is not int or document_page_count != count):
        raise ValueError("Saved OGE page count differs from archived original")
    result = {metadata["document_id"]: _request(
        observations, secondary_observations, count, metadata["sha256"],
        field_interpretation_mode=field_interpretation_mode, source_pdf_path=source)}
    _bounded(result)
    return result


def load_saved_oge_observations(path, *, evidence_root, extractions) -> dict:
    """Bind a portable selection to the chosen extraction and archived original."""
    if evidence_root is None:
        raise ValueError("Saved OGE observations require --evidence-root")
    selected = _json(Path(path))
    if not 1 <= len(selected) <= MAX_DOCUMENTS:
        raise ValueError("Saved OGE selected document count is outside budget")
    if not isinstance(extractions, list):
        raise ValueError("Saved OGE observations require selected extractions")
    indexed = {}
    for extraction in extractions:
        if not isinstance(extraction, dict):
            raise ValueError("Saved OGE extraction is invalid")
        document = extraction.get("document_id")
        if not isinstance(document, str) or document in indexed:
            raise ValueError("Saved OGE selected extraction identity is missing or duplicated")
        indexed[document] = extraction
    result = {}
    for document, request in selected.items():
        if (not _DOCUMENT.fullmatch(document) or document not in indexed
                or not isinstance(request, dict) or set(request) - _KEYS
                or not {"observations", "document_page_count"} <= request.keys()):
            raise ValueError("Saved OGE document is unselected or portable input keys are invalid")
        if "field_interpretation_mode" in request and request["field_interpretation_mode"] is None:
            raise ValueError("Saved OGE field interpretation mode must be explicit when present")
        extraction = indexed[document]
        sha = extraction.get("source_sha256")
        if (not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or extraction.get("parser_version") not in {PARSER_VERSION, GENERIC_PARSER_VERSION}):
            raise ValueError("Saved OGE observations require the selected supported original extraction")
        metadata_path = Path(evidence_root) / f"oge/reports/{document}/{sha}.json"
        metadata, source, count = _source(evidence_root, metadata_path)
        if any(extraction.get(a) != metadata.get(b) for a, b in (
                ("document_id", "document_id"), ("source_sha256", "sha256"),
                ("source_url", "document_url"), ("catalog_filer_name", "filer_name"),
                ("agency", "agency"), ("position_title", "position_title"))):
            raise ValueError("Saved OGE selected extraction differs from original archive identity")
        if type(request["document_page_count"]) is not int or request["document_page_count"] != count:
            raise ValueError("Saved OGE page count differs from archived original")
        bound = _request(request["observations"], request.get("secondary_observations"), count, sha,
                         field_interpretation_mode=request.get("field_interpretation_mode"),
                         source_pdf_path=source)
        result[document] = {**bound, "archived_pdf_path": str(source)}
    return result


def guard_saved_oge_outputs(outputs, *, inputs=(), protected_roots=()) -> None:
    """Reject aliases and writes into source trees before either output changes."""
    resolved = [Path(path).resolve() for path in outputs]
    protected = [Path(path).resolve() for path in inputs]
    roots = [Path(path).resolve() for path in protected_roots if path is not None]
    for index, path in enumerate(resolved):
        others = protected + resolved[:index]
        if (any(path == other or path.exists() and other.exists() and path.samefile(other)
                for other in others) or any(path.is_relative_to(root) for root in roots)):
            raise ValueError("Saved OGE output would overwrite an input or another output")
        if (path.exists() and not path.is_file() or
                any(parent.exists() and not parent.is_dir() for parent in path.parents) or
                any(path.is_relative_to(other) or other.is_relative_to(path)
                    for other in resolved[:index])):
            raise ValueError("Saved OGE output paths conflict with a file or directory")
