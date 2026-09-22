"""Strict extraction of publicly linked White House OGE Form 278-T PDFs.

This module consumes an already archived PDF.  It never treats a URL, page
label, upload date, OGE receipt date, or reviewer signature as a filing date.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError
from .oge_reports import _extract_pdf, _iso_date, _validate_pdf, parse_table_rows


EXTRACTION_SCHEMA = "whitehouse-278t-extraction/v1"
PARSER_VERSION = "whitehouse-278t-pdf/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ELECTRONIC_SIGNATURE = re.compile(
    r"/s/\s*(?P<signature>[^\[\n]+?)\s*"
    r"\[electronically\s+signed\s+on\s+(?P<date>\d{2}/\d{2}/\d{4})"
    r"\s+by\s+(?P<signer>.+?)\s+in\s+Integrity\.gov\]",
    re.IGNORECASE,
)


def _name_key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", value.casefold()).split())


def _first_last(value: str) -> tuple[str, str] | None:
    if "," in value:
        surname, given = value.split(",", 1)
        surname_parts = _name_key(surname).split()
        given_parts = _name_key(given).split()
        return (given_parts[0], surname_parts[-1]) if given_parts and surname_parts else None
    parts = [part for part in _name_key(value).split()
             if part not in {"president", "vice", "mr", "mrs", "ms", "dr"}]
    return (parts[0], parts[-1]) if len(parts) >= 2 else None


def _filer_information(text: str) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
    """Read only the two printed lines in the PDF's Filer's Information box."""

    match = re.search(
        r"Filer(?:'|’)?s\s+Information\s*\n"
        r"(?P<name>[^\n]+)\n(?P<role>[^\n]+)\n"
        r"Electronic\s+Signature\s*-",
        text, re.IGNORECASE)
    if match is None:
        return None, None, None, None, ["pdf_filer_information_not_verified"]
    name = match.group("name").strip()
    role_raw = match.group("role").strip()
    if not name or not role_raw:
        return None, None, None, None, ["pdf_filer_information_not_verified"]
    role, separator, agency = role_raw.rpartition(" - ")
    position = role.strip() if separator else role_raw
    agency_label = agency.strip() if separator else None
    return name, position, agency_label, role_raw, []


def _filer_signature(text: str) -> tuple[str | None, str, str | None, str | None, list[str]]:
    """Read the filer's own electronic attestation, never an ethics signature."""

    if not text.strip() or len(re.findall(r"\(cid:\d+\)", text)) > 20:
        return None, "unreadable_pdf_text", None, None, ["filer_signature_unreadable_pdf_text"]
    start = re.search(r"Electronic\s+Signature\s*-", text, re.IGNORECASE)
    if start is None:
        method = ("handwritten_unverified" if re.search(
            r"(?:signature\s+of\s+filer|filer(?:'|’)?s\s+signature)",
            text, re.IGNORECASE) else "unknown")
        return None, method, None, None, ["filer_signature_not_electronically_verified"]
    end = re.search(r"Agency\s+Ethics\s+Official(?:'|’)?s\s+Opinion", text[start.end():],
                    re.IGNORECASE)
    if end is None:
        return None, "electronic_unverified", None, None, ["filer_signature_boundary_missing"]
    section = text[start.end():start.end() + end.start()]
    matches = list(_ELECTRONIC_SIGNATURE.finditer(section))
    if len(matches) != 1:
        return None, "electronic_unverified", None, None, ["filer_signature_date_not_unique"]
    match = matches[0]
    signed_by = match.group("signer").strip()
    if _name_key(match.group("signature")) != _name_key(signed_by):
        return None, "electronic_unverified", None, None, ["filer_signature_signer_mismatch"]
    try:
        filed = _iso_date(match.group("date"))
    except OgeCatalogError:
        return None, "electronic_unverified", None, None, ["filer_signature_date_invalid"]
    return filed, "electronic", match.group(0), signed_by, []


def _validate_source(source_url: str, source_sha256: str, document_id: str,
                     filer_name: str) -> None:
    parsed = urlsplit(source_url) if isinstance(source_url, str) else None
    if (parsed is None or parsed.scheme != "https" or
            parsed.hostname not in {"whitehouse.gov", "www.whitehouse.gov"} or
            not parsed.path.startswith("/wp-content/uploads/") or
            not parsed.path.lower().endswith(".pdf") or parsed.username or parsed.password):
        raise OgeCatalogError("White House 278-T source URL is invalid")
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise OgeCatalogError("White House 278-T SHA-256 is invalid")
    if not isinstance(document_id, str) or not document_id.strip():
        raise OgeCatalogError("White House 278-T document ID is missing")
    if not isinstance(filer_name, str) or not filer_name.strip():
        raise OgeCatalogError("White House 278-T filer name is missing")


def parse_whitehouse_278t_pdf(pdf_path: Path, *, source_url: str,
                              source_sha256: str, document_id: str,
                              filer_name: str, amended_label: str | None = None) -> dict:
    """Extract row evidence; uncertain filings and rows remain quarantined.

    ``source_sha256`` is the archive's expected byte hash, not a web header.
    ``amended_label`` is an official index label, if present.  It cannot by
    itself identify a predecessor report, so amended documents stay blocked.
    """

    _validate_source(source_url, source_sha256, document_id, filer_name)
    if amended_label is not None and not isinstance(amended_label, str):
        raise OgeCatalogError("White House 278-T amendment label is invalid")
    content = Path(pdf_path).read_bytes()
    _validate_pdf(content)
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("White House 278-T PDF does not match archive hash")
    text, rows = _extract_pdf(Path(pdf_path))
    first_page = text.split("Transactions", 1)[0] if text else ""
    filed_at, signature_method, signature_raw, signature_name, reasons = _filer_signature(first_page)
    pdf_filer_name, pdf_position_title, pdf_agency_label, pdf_role_raw, identity_reasons = (
        _filer_information(first_page))
    reasons.extend(identity_reasons)
    if signature_name is not None and _first_last(signature_name) != _first_last(filer_name):
        reasons.append("filer_signature_name_mismatch")
    if pdf_filer_name is not None and _first_last(pdf_filer_name) != _first_last(filer_name):
        reasons.append("pdf_filer_name_mismatch")
    if signature_name is not None and pdf_filer_name is not None and (
            _first_last(signature_name) != _first_last(pdf_filer_name)):
        reasons.append("pdf_filer_signature_name_mismatch")

    if "Periodic Transaction Report (OGE Form 278-T)" not in first_page:
        reasons.append("278t_form_title_not_verified")
    if amended_label and "amend" in amended_label.casefold():
        reasons.append("amendment_relationship_unresolved")
    if re.search(r"\bData\s+Revised\b", first_page, re.IGNORECASE):
        reasons.append("data_revision_relationship_unresolved")
    if not rows:
        reasons.append("transaction_table_not_found")

    transactions, quarantined = parse_table_rows(rows, source_sha=source_sha256)
    if not transactions and not quarantined:
        reasons.append("no_transaction_rows_found")
    numbered = [row["row_number"] for row in [*transactions, *quarantined]
                if isinstance(row, dict) and type(row.get("row_number")) is int]
    if numbered and sorted(numbered) != list(range(1, max(numbered) + 1)):
        reasons.append("transaction_row_sequence_incomplete")
    if len(numbered) != len(set(numbered)):
        reasons.append("transaction_row_number_duplicated")
    for row in transactions:
        if filed_at is not None and row["transaction_date"] > filed_at:
            quarantined.append({**row, "reasons": ["transaction_after_filer_signature"]})
    transactions = [row for row in transactions
                    if not (filed_at is not None and row["transaction_date"] > filed_at)]

    return {
        "schema_version": EXTRACTION_SCHEMA,
        "parser_version": PARSER_VERSION,
        "source_id": "oge",
        "document_id": document_id,
        "source_url": source_url,
        "source_sha256": source_sha256,
        "filer_name": filer_name,
        "pdf_filer_name": pdf_filer_name,
        "pdf_position_title": pdf_position_title,
        "pdf_agency_label": pdf_agency_label,
        "pdf_position_agency_raw": pdf_role_raw,
        "amended_label": amended_label,
        "filed_at": filed_at,
        "signature_method": signature_method,
        "filer_signature_evidence": signature_raw,
        "filer_signature_name": signature_name,
        "document_reasons": sorted(set(reasons)),
        "evidence_complete": not reasons,
        "transactions": transactions,
        "quarantined": quarantined,
    }


def quarantine_duplicate_report_groups(extractions: list[dict]) -> list[dict]:
    """Block exact-byte or identical-row duplicate reports pending source review.

    Distinct filings on the same date with distinct transactions are retained.
    This does not infer amendment predecessor/successor relationships.
    """

    results = deepcopy(extractions)
    by_sha: dict[str, list[int]] = {}
    by_content: dict[tuple, list[int]] = {}
    for index, report in enumerate(results):
        if report.get("schema_version") != EXTRACTION_SCHEMA:
            raise OgeCatalogError("White House 278-T duplicate screen input is invalid")
        by_sha.setdefault(report["source_sha256"], []).append(index)
        rows = report.get("transactions", [])
        if rows:
            fingerprint = tuple(sorted((row.get("owner"), row.get("asset_name"),
                                        row.get("transaction_type"), row.get("transaction_date"),
                                        row.get("amount_low"), row.get("amount_high"))
                                       for row in rows))
            key = (_name_key(report["filer_name"]), report.get("filed_at"), fingerprint)
            by_content.setdefault(key, []).append(index)
    for group in by_sha.values():
        if len(group) > 1:
            for index in group:
                results[index]["document_reasons"] = sorted(set(
                    results[index]["document_reasons"] + ["duplicate_pdf_unresolved"]))
                results[index]["evidence_complete"] = False
    for group in by_content.values():
        if len(group) > 1:
            for index in group:
                results[index]["document_reasons"] = sorted(set(
                    results[index]["document_reasons"] + ["duplicate_report_content_unresolved"]))
                results[index]["evidence_complete"] = False
    return results
