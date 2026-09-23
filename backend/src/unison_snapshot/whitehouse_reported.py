"""Index White House source rows by the person who filed the disclosure.

This is a review-layer inventory, not a publication qualification.  A filer
reports a row even when its beneficial owner is unknown or the row is not yet
fully parsed.  Each row pointer resolves to an immutable, hash-bound extraction.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .oge_278e_public import (PARSER_VERSION as ANNUAL_PARSER,
                               SCHEMA as ANNUAL_SCHEMA,
                               SUPPORTED_PARSER_VERSIONS as ANNUAL_PARSERS)
from .whitehouse_278t import (PARSER_VERSION as TRADE_PARSER,
                              EXTRACTION_SCHEMA as TRADE_SCHEMA,
                              SUPPORTED_PARSER_VERSIONS as TRADE_PARSERS,
                              _first_last)


SCHEMA = "whitehouse-filer-reported-index/v1"
_COVERAGE = "whitehouse-public-coverage/v1"
_FAILURE = "whitehouse-public-extraction-failure/v1"
_ID = re.compile(r"wh-url:([0-9a-f]{24})\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_EXTRACTED = {"extracted_review_only", "extracted_with_issues"}


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"White House review object is invalid: {path}")
    return value


def _kind(section: object, source_field: str, form_type: str) -> str:
    if source_field == "transactions" or section == "part7" or form_type == "278t":
        return "transaction"
    if source_field in {"holdings", "excluded"} or section in {"part2", "part5", "part6"}:
        return "holding"
    return "unresolved"


def build_filer_reported_index(coverage: dict, review_root: Path,
                               *, coverage_sha256: str) -> dict:
    """Return deterministic pointers to every source row, including uncertain rows."""
    if not isinstance(coverage, dict):
        raise ValueError("White House coverage is invalid for filer-reported indexing")
    reports = coverage.get("reports")
    if (coverage.get("schema_version") != _COVERAGE or
            coverage.get("source_id") != "whitehouse_public_disclosures" or
            not isinstance(reports, list) or coverage.get("report_link_count") != len(reports) or
            not _SHA.fullmatch(coverage_sha256)):
        raise ValueError("White House coverage is invalid for filer-reported indexing")
    indexed = []
    seen = set()
    counts: Counter[str] = Counter()
    for report in sorted(reports, key=lambda row: row.get("document_id", "")):
        document_id = report.get("document_id")
        match = _ID.fullmatch(document_id) if isinstance(document_id, str) else None
        source_url = report.get("document_url")
        parsed = urlsplit(source_url) if isinstance(source_url, str) else urlsplit("")
        name = report.get("filer_name_from_label")
        form_type = report.get("document_type_from_label")
        versions = report.get("archive_sha256_versions")
        state = report.get("review_state")
        if (match is None or document_id in seen or not isinstance(name, str) or not name.strip() or
                form_type not in {"278t", "278e_annual", "278e_unspecified", "278e_new_entrant",
                                  "278e_termination", "278e_annual_term"} or
                parsed.scheme != "https" or parsed.hostname != "www.whitehouse.gov" or
                not parsed.path.startswith("/wp-content/uploads/") or
                not parsed.path.lower().endswith(".pdf") or
                document_id != "wh-url:" + hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24] or
                not isinstance(versions, list) or len(versions) > 1 or
                any(not isinstance(sha, str) or not _SHA.fullmatch(sha) for sha in versions)):
            raise ValueError("White House coverage report identity is invalid")
        seen.add(document_id)
        item = {"document_id": document_id, "source_url": source_url,
                "source_sha256": versions[0] if versions else None,
                "form_type_from_page": form_type, "review_state": state,
                "filer_reported_name": name, "filer_attribution": "official_page_label",
                "asset_owner_is_filer": None, "extraction_path": None, "failure_path": None,
                "records": []}
        if state in _EXTRACTED:
            if len(versions) != 1:
                raise ValueError("Extracted White House report has no unique archived PDF")
            parser = report.get("extraction_parser_version") or (
                TRADE_PARSER if form_type == "278t" else ANNUAL_PARSER)
            schema = TRADE_SCHEMA if form_type == "278t" else ANNUAL_SCHEMA
            supported = TRADE_PARSERS if form_type == "278t" else ANNUAL_PARSERS
            if parser not in supported:
                raise ValueError("White House extraction parser is unsupported")
            relative_value = report.get("extraction_path")
            relative = (Path(relative_value) if isinstance(relative_value, str) else
                        Path("whitehouse/extractions") / match[1] / versions[0] /
                        f"{parser.replace('/', '-')}.json")
            expected_relative = (Path("whitehouse/extractions") / match[1] / versions[0] /
                                 f"{parser.replace('/', '-')}.json")
            if relative != expected_relative:
                raise ValueError("White House extraction path is invalid")
            extraction = _read(review_root / relative)
            if (extraction.get("schema_version") != schema or
                    extraction.get("parser_version") != parser or
                    extraction.get("source_url") != source_url or
                    extraction.get("source_sha256") != versions[0]):
                raise ValueError("White House extraction is not bound to its official PDF")
            filer = extraction.get("filer_name")
            if not isinstance(filer, str) or _first_last(filer) != _first_last(name):
                raise ValueError("White House extracted filer conflicts with official page")
            pdf_filer = extraction.get("pdf_filer_name") if form_type == "278t" else filer
            if isinstance(pdf_filer, str) and pdf_filer.strip():
                item["filer_attribution"] = ("pdf_and_official_page" if
                    _first_last(pdf_filer) == _first_last(name) else "pdf_page_name_conflict")
            item["extraction_path"] = relative.as_posix()
            for source_field in (("transactions", "quarantined") if form_type == "278t" else
                                 ("holdings", "transactions", "quarantined", "excluded")):
                source_rows = extraction.get(source_field)
                if not isinstance(source_rows, list) or any(not isinstance(row, dict)
                                                            for row in source_rows):
                    raise ValueError("White House extraction row array is invalid")
                for index, row in enumerate(source_rows):
                    owner = row.get("owner")
                    record = {"row_pointer": f"{relative.as_posix()}#/{source_field}/{index}",
                              "source_field": source_field,
                              "record_kind": _kind(row.get("section"), source_field, form_type),
                              "owner": owner if isinstance(owner, str) and owner.strip() else "Unknown",
                              "owner_is_filer": (True if owner == "Self" else
                                                 False if owner in {"Spouse", "Dependent Child"}
                                                 else None),
                              "page_number": row.get("page_number"),
                              "row_number": row.get("row_number"),
                              "source_row_id": row.get("extraction_id"),
                              "disposition": source_field}
                    item["records"].append(record)
                    counts[source_field] += 1
        elif state == "extraction_quarantined":
            if len(versions) != 1:
                raise ValueError("Failed White House extraction has no unique archived PDF")
            parser = report.get("extraction_parser_version") or (
                TRADE_PARSER if form_type == "278t" else ANNUAL_PARSER)
            supported = TRADE_PARSERS if form_type == "278t" else ANNUAL_PARSERS
            if parser not in supported:
                raise ValueError("White House extraction failure parser is unsupported")
            relative_value = report.get("failure_path")
            relative = (Path(relative_value) if isinstance(relative_value, str) else
                        Path("whitehouse/extractions") / match[1] / versions[0] /
                        f"{parser.replace('/', '-')}.failure.json")
            expected_relative = (Path("whitehouse/extractions") / match[1] / versions[0] /
                                 f"{parser.replace('/', '-')}.failure.json")
            if relative != expected_relative:
                raise ValueError("White House failure path is invalid")
            failure = _read(review_root / relative)
            if (failure.get("schema_version") != _FAILURE or
                    failure.get("document_id") != document_id or
                    failure.get("source_url") != source_url or
                    failure.get("source_sha256") != versions[0] or
                    failure.get("parser_version") != parser):
                raise ValueError("White House extraction failure is not bound to its PDF")
            item["failure_path"] = relative.as_posix()
        elif state not in {"pending_extraction", "not_archived", "download_failed",
                           "multiple_archive_versions_unresolved"}:
            raise ValueError("White House coverage review state is unknown")
        indexed.append(item)
    return {"schema_version": SCHEMA, "source_id": "whitehouse_public_disclosures",
            "meaning": "filer_reported_not_filer_owned",
            "coverage_sha256": coverage_sha256, "report_count": len(indexed),
            "record_count": sum(len(item["records"]) for item in indexed),
            "record_counts_by_source_field": dict(sorted(counts.items())),
            "reports": indexed}
