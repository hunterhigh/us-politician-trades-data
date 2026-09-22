"""Prepare auditable White House OGE Form 201 requests from archived catalog pages.

This module only prepares a request inventory. It never submits a government
form, infers a filing date from the catalog date, or equates duplicate catalog
rows with duplicate reports: request-only rows have no report identifier.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlsplit


SCHEMA = "oge-whitehouse-request-plan/v1"
SCOPE_AGENCIES = frozenset({"White House Office", "Office of The Vice President",
                            "Office of the Vice President"})
_LINK = re.compile(r"<a href='([^']+)'>Request this Document</a>")
_ANNUAL = re.compile(r"Annual \((\d{4})\)")
_FORM_201_PATH = "/201/Presiden.nsf/201 Request"


class OgeWhiteHouseRequestError(ValueError):
    """Archived catalog cannot support a reliable request plan."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _request_type(markup: str) -> tuple[str, int | None, str] | None:
    """Classify only the in-scope report types; retain original markup separately."""
    if not isinstance(markup, str):
        raise OgeWhiteHouseRequestError("catalog row type is invalid")
    if markup.startswith("278 Transaction "):
        report_type, year = "278_transaction", None
    elif markup.startswith("Annual Term "):
        report_type, year = "annual_termination", None
    else:
        match = _ANNUAL.match(markup)
        if match is None:
            return None
        report_type, year = "annual_278e", int(match.group(1))
    links = _LINK.findall(markup)
    if not links:
        # A public direct PDF is handled by the report archive, not Form 201.
        if "Request this Document" in markup:
            raise OgeWhiteHouseRequestError("OGE request link markup changed")
        return None
    if len(links) != 1:
        raise OgeWhiteHouseRequestError("OGE request row has multiple links")
    url = links[0]
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.hostname != "extapps2.oge.gov" or
            parsed.username is not None or parsed.password is not None or
            parsed.port is not None or parsed.fragment or
            unquote(parsed.path).lower() != _FORM_201_PATH.lower() or
            set(query) != {"OpenForm", "Filer"} or query["OpenForm"] != [""] or
            len(query["Filer"]) != 1 or not query["Filer"][0].strip()):
        raise OgeWhiteHouseRequestError("OGE request link is not a valid official Form 201 URL")
    return report_type, year, url


def _catalog_date(value: object) -> str:
    if not isinstance(value, str):
        raise OgeWhiteHouseRequestError("catalog date is invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").date().isoformat()
    except ValueError:
        raise OgeWhiteHouseRequestError("catalog date is invalid") from None


def build_request_plan(archive_root: Path, catalog_metadata: dict) -> dict:
    """Group request *intents* while retaining each original catalog occurrence.

    ``catalog_metadata`` is the existing oge-catalog-archive/v1 record. The
    content-addressed raw pages are read and SHA-256 checked before planning.
    Grouping never asserts how many distinct underlying reports exist.
    """
    if (not isinstance(catalog_metadata, dict) or
            catalog_metadata.get("schema_version") != "oge-catalog-archive/v1" or
            catalog_metadata.get("source_id") != "oge" or
            not isinstance(catalog_metadata.get("pages"), list) or
            not catalog_metadata["pages"]):
        raise OgeWhiteHouseRequestError("OGE catalog archive metadata is invalid")
    total = catalog_metadata.get("record_count")
    if type(total) is not int or total < 0:
        raise OgeWhiteHouseRequestError("OGE catalog row count is invalid")
    groups: dict[tuple[str, str, str, int | None, str], list[dict]] = defaultdict(list)
    scope_rows = request_rows = 0
    expected_start = 0
    root = Path(archive_root).resolve()
    for page in sorted(catalog_metadata["pages"], key=lambda item: item["start"]):
        if page.get("start") != expected_start:
            raise OgeWhiteHouseRequestError("OGE catalog pages have a gap or overlap")
        digest = page.get("sha256")
        relative = page.get("archive_path")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
                relative != f"oge/catalog/pages/{digest}.json"):
            raise OgeWhiteHouseRequestError("OGE catalog page reference is invalid")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise OgeWhiteHouseRequestError("OGE catalog page leaves archive root")
        raw = path.read_bytes()
        if _sha(raw) != digest or len(raw) != page.get("byte_length"):
            raise OgeWhiteHouseRequestError("OGE catalog page content hash differs")
        payload = json.loads(raw)
        rows = payload.get("data")
        requested_length = page.get("length")
        if type(requested_length) is not int or requested_length <= 0:
            raise OgeWhiteHouseRequestError("OGE catalog page length is invalid")
        expected_count = min(requested_length, total - expected_start)
        if (payload.get("recordsTotal") != total or
                payload.get("recordsFiltered") != total or
                not isinstance(rows, list) or len(rows) != expected_count):
            raise OgeWhiteHouseRequestError("OGE catalog page coverage differs")
        for offset, row in enumerate(rows):
            if not isinstance(row, dict):
                raise OgeWhiteHouseRequestError("OGE catalog row is invalid")
            agency = row.get("agency")
            if agency not in SCOPE_AGENCIES:
                continue
            scope_rows += 1
            classified = _request_type(row.get("type"))
            if classified is None:
                continue
            report_type, year, url = classified
            name = row.get("name")
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise OgeWhiteHouseRequestError("OGE filer name is invalid")
            request_rows += 1
            key = (name, agency, report_type, year, url)
            groups[key].append({
                "catalog_index": expected_start + offset,
                "catalog_added_date": _catalog_date(row.get("docDate")),
                "source_page_sha256": digest,
                "source_row_sha256": _sha(_canonical_bytes(row)),
                "source_row": row,
            })
        expected_start += len(rows)
    if expected_start != total:
        raise OgeWhiteHouseRequestError("OGE catalog is incomplete")
    requests = []
    for key in sorted(groups, key=lambda value: (value[0], value[1], value[2],
                                                  value[3] or 0, value[4])):
        name, agency, report_type, year, url = key
        occurrences = sorted(groups[key], key=lambda item: item["catalog_index"])
        requests.append({
            "request_id": _sha(_canonical_bytes(key))[:24],
            "filer_name": name,
            "agency": agency,
            "document_type": report_type,
            "filing_year_from_catalog_label": year,
            "official_form_201_url": url,
            "status": "not_submitted",
            "request_route": "requires_current_or_prior_administration_review",
            "catalog_occurrence_count": len(occurrences),
            "distinct_report_count": None,
            "catalog_occurrences": occurrences,
        })
    return {
        "schema_version": SCHEMA,
        "source_id": "oge",
        "source_catalog_sha256": catalog_metadata.get("sha256"),
        "catalog_records_total": total,
        "scope_agencies": sorted(SCOPE_AGENCIES),
        "scope_catalog_rows": scope_rows,
        "request_catalog_rows": request_rows,
        "request_intent_count": len(requests),
        "requests": requests,
    }
