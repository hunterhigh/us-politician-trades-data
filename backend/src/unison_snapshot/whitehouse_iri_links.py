"""Encode official Unicode White House PDF hrefs without guessing filenames.

The disclosures page contains a small set of genuine U+2013 characters in
PDF href paths. ``urllib`` needs those exact Unicode paths encoded as UTF-8
percent escapes. This module retains the source href and page digest, accepts
only the same official host/path, and leaves PDF verification to the ordinary
archive client. It never substitutes or infers a missing character.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, urlsplit, urlunsplit

from .whitehouse_disclosures import (
    INDEX_SCHEMA, PAGE_URL, WhiteHouseDisclosureClient, WhiteHouseDisclosureError,
    _label_kind, _pdf_url,
)


SCHEMA = "whitehouse-public-iri-link-recovery/v1"


def _encode_exact_href(href: str) -> str:
    if not isinstance(href, str):
        raise WhiteHouseDisclosureError("Unicode source href is invalid")
    parsed = urlsplit(href)
    if (parsed.scheme != "https" or parsed.hostname != "www.whitehouse.gov" or
            parsed.username is not None or parsed.password is not None or
            parsed.port is not None or parsed.query or parsed.fragment or
            parsed.netloc != "www.whitehouse.gov" or parsed.path.isascii() or
            any(ord(char) < 0x20 or char == "\ufffd" for char in parsed.path)):
        raise WhiteHouseDisclosureError("source href is not an official Unicode PDF path")
    # RFC 3986 percent encoding of the *actual characters in the href*.
    encoded = urlunsplit((parsed.scheme, parsed.netloc,
                          quote(parsed.path, safe="/%-._~"), "", ""))
    _pdf_url(encoded)
    if encoded == href:
        raise WhiteHouseDisclosureError("source href needed no encoding")
    return encoded


def recover_official_iri_links(index: dict) -> tuple[dict, dict]:
    """Return an index extension plus a source-href-to-URI audit.

    A recovered row is still only a discoverable URL. The PDF archive client
    must obtain and hash the document before downstream use.
    """
    if (not isinstance(index, dict) or index.get("schema_version") != INDEX_SCHEMA or
            index.get("source_id") != "whitehouse_public_disclosures" or
            index.get("page_url") != PAGE_URL or
            not isinstance(index.get("reports"), list) or
            not isinstance(index.get("quarantine"), list) or
            not isinstance(index.get("page_sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", index["page_sha256"])):
        raise WhiteHouseDisclosureError("public index is invalid for Unicode link recovery")
    recovered = []
    remaining = []
    audit_rows = []
    known_urls = {row["document_url"] for row in index["reports"]}
    for item in index["quarantine"]:
        href = item.get("url") if isinstance(item, dict) else None
        label = item.get("label") if isinstance(item, dict) else None
        section = item.get("section") if isinstance(item, dict) else None
        if section != "Transaction Reports" or not isinstance(label, str):
            remaining.append(item)
            continue
        try:
            retrieval_url = _encode_exact_href(href)
            kind, year, name = _label_kind(section, label)
            if kind != "278t" or not name or name == "label_ambiguous":
                raise WhiteHouseDisclosureError("source label does not identify a 278-T filer")
            if retrieval_url in known_urls:
                raise WhiteHouseDisclosureError("encoded URL duplicates an indexed report")
            known_urls.add(retrieval_url)
            document_id = "wh-url:" + hashlib.sha256(
                retrieval_url.encode("utf-8")).hexdigest()[:24]
            recovered.append({
                "source_id": "whitehouse_public_disclosures",
                "source_document_id": document_id,
                "document_url": retrieval_url,
                "source_href": href,
                "source_section": section,
                "link_label": label,
                "filer_name_from_label": name,
                "document_type_from_label": kind,
                "report_year_from_label": year,
                "classification_status": "official_iri_encoded_pdf_pending",
            })
            audit_rows.append({"source_href": href, "retrieval_url": retrieval_url,
                               "source_document_id": document_id,
                               "filer_name_from_label": name,
                               "link_label": label,
                               "status": "pending_pdf_download"})
        except WhiteHouseDisclosureError as exc:
            remaining.append(item)
            audit_rows.append({"source_href": href, "link_label": label,
                               "status": "not_recovered", "reason": str(exc)})
    expanded = dict(index)
    expanded["reports"] = sorted(index["reports"] + recovered,
                                 key=lambda row: row["document_url"])
    expanded["quarantine"] = remaining
    expanded["report_link_count"] = len(expanded["reports"])
    expanded["quarantine_count"] = len(remaining)
    audit = {"schema_version": SCHEMA,
             "source_page_url": index.get("page_url"),
             "source_page_sha256": index["page_sha256"],
             "transformation": "RFC3986 UTF-8 percent encoding of exact official href path",
             "recovered_url_count": len(recovered),
             "remaining_quarantine_count": len(remaining),
             "rows": audit_rows}
    return expanded, audit


def verify_recovered_pdfs(audit: dict, *,
                          client: WhiteHouseDisclosureClient | None = None) -> dict:
    """Optional one-time read-only audit of recovered official PDF URLs."""
    if not isinstance(audit, dict) or audit.get("schema_version") != SCHEMA:
        raise WhiteHouseDisclosureError("Unicode recovery audit is invalid")
    result = dict(audit)
    rows = []
    for item in audit["rows"]:
        record = dict(item)
        if record["status"] == "pending_pdf_download":
            try:
                content, headers = (client or WhiteHouseDisclosureClient()).download_pdf(
                    record["retrieval_url"])
                record.update({"status": "official_pdf_verified",
                               "pdf_sha256": hashlib.sha256(content).hexdigest(),
                               "pdf_byte_length": len(content),
                               "content_type": headers.get("content-type")})
            except WhiteHouseDisclosureError as exc:
                record.update({"status": "download_failed", "reason": str(exc)})
        rows.append(record)
    result["rows"] = rows
    result["official_pdf_verified_count"] = sum(
        row["status"] == "official_pdf_verified" for row in rows)
    return result
