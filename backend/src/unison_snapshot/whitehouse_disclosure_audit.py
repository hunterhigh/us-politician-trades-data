"""Conservative catalog crosswalk for official White House public links.

Matching a catalog name and report family yields a *candidate* only. OGE
request rows contain no public report identifier, so this audit never claims
that a White House PDF fulfills an OGE request without PDF-level evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlsplit

from .whitehouse_disclosures import INDEX_SCHEMA, WhiteHouseDisclosureError


SCHEMA = "whitehouse-oge-public-crosswalk/v1"
_AGENCIES = {"white house office", "office of the vice president"}
_REQUEST = re.compile(r"<a href='([^']+)'>Request this Document</a>")
_ANNUAL = re.compile(r"^Annual \((20\d{2})\)")


def _kind(markup: str) -> tuple[str, int | None] | None:
    if markup.startswith("278 Transaction "):
        return "278t", None
    if markup.startswith("Annual Term "):
        return "278e_annual_termination", None
    match = _ANNUAL.match(markup)
    if match:
        return "278e_annual", int(match.group(1))
    if markup.startswith("New Entrant "):
        return "278e_new_entrant", None
    if markup.startswith("Termination "):
        return "278e_termination", None
    return None


def _name(value: str) -> str:
    # Exact display-name matching only; no alias, maiden-name, or nickname guess.
    return " ".join(value.casefold().replace(",", " ").split())


def build_oge_public_crosswalk(archive_root: Path, oge_catalog_metadata: dict,
                               public_index: dict, *,
                               since_catalog_date: str = "2025-01-20") -> dict:
    if (not isinstance(oge_catalog_metadata, dict) or
            oge_catalog_metadata.get("schema_version") != "oge-catalog-archive/v1" or
            oge_catalog_metadata.get("source_id") != "oge" or
            not isinstance(public_index, dict) or public_index.get("schema_version") != INDEX_SCHEMA):
        raise WhiteHouseDisclosureError("crosswalk inputs are invalid")
    try:
        date.fromisoformat(since_catalog_date)
    except ValueError:
        raise WhiteHouseDisclosureError("crosswalk cutoff is invalid") from None
    reports = public_index.get("reports")
    if not isinstance(reports, list):
        raise WhiteHouseDisclosureError("public report index is invalid")
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in reports:
        if not isinstance(row, dict):
            raise WhiteHouseDisclosureError("public report row is invalid")
        name = row.get("filer_name_from_label")
        if isinstance(name, str) and name.strip():
            by_name[_name(name)].append(row)
    base = Path(archive_root).resolve()
    total = oge_catalog_metadata.get("record_count")
    pages = oge_catalog_metadata.get("pages")
    if type(total) is not int or total < 0 or not isinstance(pages, list) or not pages:
        raise WhiteHouseDisclosureError("OGE catalog manifest is invalid")
    candidates = []
    expected_start = 0
    for page in sorted(pages, key=lambda item: item["start"]):
        sha = page.get("sha256")
        relative = page.get("archive_path")
        if (page.get("start") != expected_start or not isinstance(sha, str) or
                not re.fullmatch(r"[0-9a-f]{64}", sha) or
                relative != f"oge/catalog/pages/{sha}.json"):
            raise WhiteHouseDisclosureError("OGE catalog page reference is invalid")
        path = (base / relative).resolve()
        if not path.is_relative_to(base):
            raise WhiteHouseDisclosureError("OGE catalog page escapes archive")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) != page.get("byte_length"):
            raise WhiteHouseDisclosureError("OGE catalog page hash differs")
        payload = json.loads(raw)
        rows = payload.get("data")
        requested = page.get("length")
        if (type(requested) is not int or requested <= 0 or
                payload.get("recordsTotal") != total or
                payload.get("recordsFiltered") != total or
                not isinstance(rows, list) or len(rows) != min(requested, total - expected_start)):
            raise WhiteHouseDisclosureError("OGE catalog page is incomplete")
        for offset, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("agency"), str) or \
                    row["agency"].strip().casefold() not in _AGENCIES:
                continue
            markup = row.get("type")
            if not isinstance(markup, str) or "Request this Document" not in markup:
                continue
            classified = _kind(markup)
            if classified is None:
                continue
            links = _REQUEST.findall(markup)
            if len(links) != 1:
                raise WhiteHouseDisclosureError("OGE request link markup changed")
            url = urlsplit(links[0])
            query = parse_qs(url.query, keep_blank_values=True)
            if (url.scheme != "https" or url.hostname != "extapps2.oge.gov" or
                    url.username is not None or url.password is not None or
                    url.port is not None or url.fragment or
                    unquote(url.path).lower() != "/201/presiden.nsf/201 request" or
                    set(query) != {"OpenForm", "Filer"} or
                    query["OpenForm"] != [""] or len(query["Filer"]) != 1 or
                    not query["Filer"][0].strip()):
                raise WhiteHouseDisclosureError("OGE request link is not official")
            try:
                added = datetime.strptime(row["docDate"], "%Y-%m-%dT%H:%M:%S").date().isoformat()
            except (KeyError, TypeError, ValueError):
                raise WhiteHouseDisclosureError("OGE catalog row date is invalid") from None
            if added < since_catalog_date:
                continue
            name = row.get("name")
            if not isinstance(name, str) or not name.strip():
                raise WhiteHouseDisclosureError("OGE catalog filer name is invalid")
            kind, year = classified
            same_name = by_name.get(_name(name), [])
            strong = [report for report in same_name
                      if report["document_type_from_label"] == kind and
                      (year is None or report["report_year_from_label"] == year)]
            # Bare financial link labels cannot prove that the PDF is a New
            # Entrant or Termination report; keep them as weak possibilities.
            weak = [report for report in same_name
                    if report["document_type_from_label"] == "278e_unspecified"
                    and kind != "278t"]
            candidates.append({
                "catalog_index": expected_start + offset,
                "catalog_row_sha256": hashlib.sha256(json.dumps(
                    row, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")).encode("utf-8")).hexdigest(),
                "catalog_added_date": added,
                "filer_name_from_oge": name,
                "agency_from_oge": row["agency"],
                "document_type_from_oge": kind,
                "annual_year_from_oge_label": year,
                "candidate_link_count": len(strong),
                "same_name_unspecified_link_count": len(weak),
                "candidate_document_ids": sorted(report["source_document_id"] for report in strong),
                "same_name_unspecified_document_ids": sorted(
                    report["source_document_id"] for report in weak),
                "match_status": "unverified_candidate" if strong else
                                "unverified_type_unknown" if weak else "no_public_candidate",
                "same_document_verified": False,
            })
        expected_start += len(rows)
    if expected_start != total:
        raise WhiteHouseDisclosureError("OGE catalog is incomplete")
    counts = Counter(item["match_status"] for item in candidates)
    return {"schema_version": SCHEMA, "public_page_sha256": public_index["page_sha256"],
            "oge_catalog_sha256": oge_catalog_metadata.get("sha256"),
            "since_catalog_added_date": since_catalog_date,
            "scope_note": "Catalog date is not a filing, transaction, or appointment date; includes predecessor terminations.",
            "matching_note": "Name/type/year matches are candidates, never verified report identity or fulfilled Form 201 requests.",
            "request_catalog_occurrence_count": len(candidates),
            "counts_by_status": {key: counts[key] for key in
                                 ("unverified_candidate", "unverified_type_unknown",
                                  "no_public_candidate")},
            "rows": candidates}
