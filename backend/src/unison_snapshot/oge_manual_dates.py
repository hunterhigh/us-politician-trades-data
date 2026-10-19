"""Apply source-bound human cover-date observations to OGE candidate input."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path

from .oge import OgeCatalogError


SCHEMA = "oge-manual-cover-date/v1"
DATE_REASON = "filer_signature_date_not_unique"


def apply_manual_cover_dates(extractions: list[dict], root: Path) -> list[dict]:
    """Resolve only the filing-date reason; leave all other evidence and rows intact."""
    selected: list[dict] = []
    for extraction in extractions:
        document_id = extraction.get("document_id")
        source_sha = extraction.get("source_sha256")
        if not isinstance(document_id, str) or not isinstance(source_sha, str):
            raise OgeCatalogError("OGE extraction lacks manual-date source identity")
        path = root / document_id / f"{source_sha}.json"
        if not path.is_file():
            selected.append(extraction)
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("schema_version") != SCHEMA or
                value.get("status") != "observation_only" or
                value.get("qualification_effect") != "none" or
                value.get("document_id") != document_id or
                value.get("source_sha256") != source_sha or
                value.get("source_url") != extraction.get("source_url") or
                value.get("extraction_parser_version") != "oge-278t-pdf/v2" or
                value.get("page_number") != 1 or
                value.get("recognition_method") != "human_visual_reading_of_pdf_cover" or
                not isinstance(value.get("raw_reading"), str) or
                not value["raw_reading"].strip()):
            raise OgeCatalogError("OGE manual cover date is not bound to the extraction")
        role = value.get("field_role")
        observed = value.get("observed_date")
        try:
            if not isinstance(observed, str) or date.fromisoformat(observed).isoformat() != observed:
                raise ValueError
        except ValueError:
            raise OgeCatalogError("OGE manual cover date is invalid") from None
        if role == "amendment_signature_date":
            if value.get("filed_at") is not None:
                raise OgeCatalogError("OGE amendment signature is not a filing date")
            selected.append(extraction)
            continue
        if role != "filer_signature_date" or value.get("filed_at") != observed:
            raise OgeCatalogError("OGE manual filing date cannot resolve this extraction")
        if (extraction.get("filed_at") == observed and
                DATE_REASON not in extraction.get("document_reasons", [])):
            selected.append(extraction)
            continue
        if (extraction.get("filed_at") is not None or
                DATE_REASON not in extraction.get("document_reasons", [])):
            raise OgeCatalogError("OGE manual filing date conflicts with extraction")
        resolved = deepcopy(extraction)
        resolved["filed_at"] = observed
        resolved["document_reasons"] = [reason for reason in resolved["document_reasons"]
                                        if reason != DATE_REASON]
        resolved["evidence_complete"] = not resolved["document_reasons"]
        resolved["manual_filing_date_evidence"] = {
            "review_path": f"oge/manual_cover_dates/{document_id}/{source_sha}.json",
            "page_number": 1,
            "source_sha256": source_sha,
            "raw_reading": value["raw_reading"],
            "observed_date": observed,
        }
        selected.append(resolved)
    return selected
