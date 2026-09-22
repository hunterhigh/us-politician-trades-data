"""Official Senate eFD annual holdings, kept separate from PTR transactions.

The portal's type-7 search also contains candidate and new-filer reports.
Only annual labels for members in the archived Senate roster are selected.
Every selected version is archived before extraction; only a latest, complete,
unambiguous annual report contributes frontend holdings.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from urllib.request import Request
import uuid

from .senate import (HOME_URL, SEARCH_PAGE_URL, SenateEfdClient, SenateEfdError,
                     SenateSourceConfig, _AnchorParser, _portal_date,
                     require_collection_enabled)
from .senate_candidate import _person
from .senate_identity import _member_rows, _without_suffix, _words, match_report_identity
from .senate_reports import _report_parser


SCHEMA = "senate-efd-annual-review/v1"
DISCOVERY_SCHEMA = "senate-efd-annual-discovery/v1"
EXTRACTION_SCHEMA = "senate-efd-annual-extraction/v1"
PARSER_VERSION = "senate-efd-annual-html-2026-09-v1"
MAX_HTML_BYTES = 25 * 1024 * 1024
_URL_PREFIX = "https://efdsearch.senate.gov/search/view/annual/"
_LABEL = re.compile(r"Annual Report for CY (20\d{2})(?: \(Amendment ([1-9]\d*)\))?")
_TITLE = re.compile(r"Annual Report for Calendar (20\d{2})(?: \(Amendment ([1-9]\d*)\))?")
_VALUE = re.compile(r"\$([\d,]+)\s*-\s*\$([\d,]+)")
_NUMBER = re.compile(r"[1-9]\d*(?:\.[1-9]\d*)*")
_TICKER = re.compile(r"([A-Z0-9][A-Z0-9.\-^/]{0,31})\s+-\s+(.+)")
_ASSET_HEADERS = ("Asset", "Asset Type", "Owner", "Value", "Income Type", "Income")


def _write_once(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise SenateEfdError(f"Immutable Senate annual evidence changed: {path}")
        return
    path.write_bytes(raw)


def _json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) +
            "\n").encode("utf-8")


def parse_annual_search_page(payload: object, *, start: int, length: int) -> tuple[int, list[dict], int]:
    if (not isinstance(payload, dict) or set(payload) !=
            {"draw", "recordsTotal", "recordsFiltered", "data", "result"} or
            payload.get("result") != "ok" or type(payload.get("recordsTotal")) is not int or
            payload["recordsTotal"] < 0 or
            payload.get("recordsFiltered") != payload["recordsTotal"] or
            not isinstance(payload.get("data"), list) or len(payload["data"]) > length or
            start + len(payload["data"]) > payload["recordsTotal"]):
        raise SenateEfdError("Senate annual catalog page changed")
    reports = []
    ignored = 0
    for offset, row in enumerate(payload["data"]):
        if not isinstance(row, list) or len(row) != 5 or any(
                not isinstance(cell, str) or not cell.strip() for cell in row):
            raise SenateEfdError("Senate annual catalog row changed")
        parser = _AnchorParser()
        parser.feed(row[3]); parser.close()
        if len(parser.links) != 1 or "".join(parser.outside_text).strip():
            raise SenateEfdError("Senate annual catalog report link changed")
        href, label = parser.links[0]
        match = _LABEL.fullmatch(label)
        if match is None:
            ignored += 1  # The type-7 endpoint also returns candidate/new-filer reports.
            continue
        if href.startswith("//") or not (href.startswith("/") or href.startswith(_URL_PREFIX)):
            raise SenateEfdError("Senate annual report link is not official")
        url = urljoin(HOME_URL, href)
        parsed = urlsplit(url)
        raw_id = parsed.path.removeprefix("/search/view/annual/").removesuffix("/")
        try:
            document_id = str(uuid.UUID(raw_id))
        except ValueError:
            raise SenateEfdError("Senate annual report ID is invalid") from None
        if (url != f"{_URL_PREFIX}{document_id}/" or parsed.query or parsed.fragment):
            raise SenateEfdError("Senate annual report URL changed")
        reports.append({
            "catalog_index": start + offset, "document_id": document_id,
            "document_url": url, "filer_name": " ".join((row[0], row[1])),
            "office": row[2], "portal_listed_date": _portal_date(row[4]),
            "report_year": int(match[1]), "amendment_number": int(match[2] or 0),
        })
    return payload["recordsTotal"], reports, ignored


def discover_annuals(client: SenateEfdClient, config: SenateSourceConfig, *, submitted_start_date: str,
                     page_size: int = 100) -> tuple[dict, list[tuple[bytes, int]]]:
    require_collection_enabled(config)
    try:
        start_day = date.fromisoformat(submitted_start_date)
    except (TypeError, ValueError):
        raise SenateEfdError("Senate annual search start date is invalid") from None
    if not date(2012, 1, 1) <= start_day <= datetime.now(timezone.utc).date():
        raise SenateEfdError("Senate annual search start date is outside its supported range")
    if not 1 <= page_size <= 100:
        raise SenateEfdError("Senate annual search page size is invalid")
    if client.csrf_token is None:
        client.begin_authorized_session()
    start, draw, total = 0, 1, None
    reports, pages, ignored = [], [], 0
    while total is None or start < total:
        raw, _ = client.download_search_page(
            start=start, length=page_size, draw=draw,
            submitted_start_date=start_day.strftime("%m/%d/%Y") + " 00:00:00",
            report_types="[7]")
        try:
            observed, rows, skipped = parse_annual_search_page(
                json.loads(raw), start=start, length=page_size)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SenateEfdError("Senate annual search returned invalid JSON") from None
        if observed > 3000 or (total is not None and observed != total):
            raise SenateEfdError("Senate annual search total changed or exceeded bound")
        total = observed
        count = len(json.loads(raw)["data"])
        if count != min(page_size, total - start):
            raise SenateEfdError("Senate annual search pagination is incomplete")
        reports.extend(rows); ignored += skipped; pages.append((raw, start))
        start += count; draw += 1
    ids = [row["document_id"] for row in reports]
    if len(ids) != len(set(ids)) or len(reports) + ignored != total:
        raise SenateEfdError("Senate annual search rows are not conserved")
    return {"schema_version": DISCOVERY_SCHEMA, "source_id": "senate_efd",
            "submitted_start_date": submitted_start_date, "catalog_record_count": total,
            "annual_report_count": len(reports), "nonannual_count": ignored,
            "reports": reports}, pages


def select_current_annual_versions(discovery: dict, roster: dict) -> tuple[list[dict], dict]:
    members, roster_sha = _member_rows(roster)
    if discovery.get("schema_version") != DISCOVERY_SCHEMA:
        raise SenateEfdError("Senate annual discovery is invalid")
    current = {row["person_id"] for row in members}
    by_person = defaultdict(list)
    identity_counts = Counter()
    for report in discovery["reports"]:
        identity = match_report_identity(report, roster)
        identity_counts[identity["status"]] += 1
        person_id = identity.get("person_id")
        if identity.get("status") == "matched_automatically" and person_id in current:
            by_person[person_id].append({**report, "identity": identity})
    selected = []
    for person_id, rows in by_person.items():
        latest_year = max(row["report_year"] for row in rows)
        selected.extend(row for row in rows if row["report_year"] == latest_year)
    selected.sort(key=lambda row: (row["identity"]["person_id"], row["report_year"],
                                   row["amendment_number"], row["document_id"]))
    return selected, {"roster_sha256": roster_sha,
                      "current_member_count": len(current),
                      "matched_member_count": len(by_person),
                      "selected_report_count": len(selected),
                      "identity_counts": dict(sorted(identity_counts.items()))}


def archive_selected_annuals(client: SenateEfdClient, evidence_root: Path,
                             selected: list[dict], *, limit: int = 200) -> dict:
    if not 1 <= limit <= 300:
        raise SenateEfdError("Senate annual archive limit is invalid")
    root = evidence_root / "senate_efd/annual/reports"
    archived, failures, pending = [], [], []
    attempted = 0
    for report in selected:
        folder = root / report["document_id"]
        existing = sorted(folder.glob("*.metadata.json"))
        if len(existing) > 1:
            failures.append({"document_id": report["document_id"],
                             "reason": "multiple_archived_versions"})
            continue
        if existing:
            archived.append(json.loads(existing[0].read_text(encoding="utf-8")))
            continue
        if attempted >= limit:
            pending.append(report["document_id"])
            continue
        attempted += 1
        url = report["document_url"]
        try:
            raw, headers = client._open(Request(url, headers={
                "Accept": "text/html,application/xhtml+xml", "Referer": SEARCH_PAGE_URL,
            }, method="GET"), expected_urls={url}, maximum=MAX_HTML_BYTES)
            if (not raw.lstrip().lower().startswith((b"<!doctype html", b"<html")) or
                    "html" not in headers.get("content-type", "").lower()):
                raise SenateEfdError("Senate annual response is not HTML")
            sha = hashlib.sha256(raw).hexdigest()
            path = folder / f"{sha}.html"
            metadata = {"schema_version": "senate-efd-annual-archive/v1",
                        "document_id": report["document_id"], "document_url": url,
                        "source_sha256": sha, "byte_length": len(raw),
                        "filer_name": report["filer_name"], "office": report["office"],
                        "portal_listed_date": report["portal_listed_date"],
                        "report_year": report["report_year"],
                        "amendment_number": report["amendment_number"]}
            _write_once(path, raw)
            _write_once(folder / f"{sha}.metadata.json", _json(metadata))
            archived.append(metadata)
        except (SenateEfdError, OSError) as exc:
            failures.append({"document_id": report["document_id"], "reason": str(exc)[:200]})
    return {"selected_count": len(selected), "archived_count": len(archived),
            "failure_count": len(failures), "pending_count": len(pending),
            "failures": failures, "pending_document_ids": pending}


def _first_last(value: str) -> tuple[str, str] | None:
    words = _without_suffix(_words(value))
    return (words[0], words[-1]) if len(words) >= 2 else None


def extract_annual(metadata: dict, raw: bytes) -> dict:
    if (metadata.get("schema_version") != "senate-efd-annual-archive/v1" or
            hashlib.sha256(raw).hexdigest() != metadata.get("source_sha256") or
            len(raw) != metadata.get("byte_length")):
        raise SenateEfdError("Senate annual extraction is not bound to archived bytes")
    parsed = _report_parser(raw)
    titles = [text for tag, text in parsed.headings if tag == "h1"]
    match = _TITLE.fullmatch(titles[0]) if len(titles) == 1 else None
    if (match is None or int(match[1]) != metadata["report_year"] or
            int(match[2] or 0) != metadata["amendment_number"]):
        raise SenateEfdError("Senate annual title does not match its catalog")
    headings = [text for tag, text in parsed.headings if tag == "h2"]
    if len(headings) != 1 or "(" not in headings[0] or ")" not in headings[0]:
        raise SenateEfdError("Senate annual filer heading is missing")
    heading_name = headings[0].rsplit("(", 1)[1].rstrip(") ")
    if "," not in heading_name:
        raise SenateEfdError("Senate annual filer heading changed")
    surname, given = heading_name.split(",", 1)
    if _first_last(given.strip() + " " + surname.strip()) != _first_last(metadata["filer_name"]):
        raise SenateEfdError("Senate annual PDF filer conflicts with its catalog")
    filed = [text for text in parsed.text_segments if
             re.fullmatch(r"Filed \d{2}/\d{2}/\d{4} @ .+", text)]
    if len(filed) != 1:
        raise SenateEfdError("Senate annual has no unique filing timestamp")
    filing_match = re.fullmatch(r"Filed (\d{2}/\d{2}/\d{4}) @ .+", filed[0])
    filing_day = datetime.strptime(filing_match[1], "%m/%d/%Y").date().isoformat()
    tables = [table for table in parsed.tables if len(table.headers) == 7 and
              table.headers[1:] == _ASSET_HEADERS and table.headers[0] in {"", "#"}]
    if len(tables) != 1:
        raise SenateEfdError("Senate annual Part 3 asset table is absent or ambiguous")
    rows, seen_numbers, parent_names = [], set(), {}
    for cells in tables[0].rows:
        if len(cells) != 7 or not _NUMBER.fullmatch(cells[0]) or cells[0] in seen_numbers:
            raise SenateEfdError("Senate annual asset rows are not uniquely numbered")
        number, name, raw_type, owner, value, income_type, income = cells
        seen_numbers.add(number)
        reasons = []
        if not name or not raw_type:
            reasons.append("asset_or_type_missing")
        if owner not in {"Self", "Spouse", "Joint", "Child"}:
            reasons.append("owner_unresolved")
        amount = _VALUE.fullmatch(value)
        excluded = value in {"--", "None (or less than $1,001)"}
        if not amount and not excluded:
            reasons.append("value_range_open_or_unreadable")
        parent = ".".join(number.split(".")[:-1])
        context = parent_names.get(parent)
        if excluded and name:
            parent_names[number] = (context + " / " if context else "") + name
        if not excluded and context and name:
            name = context + " / " + name
        ticker_match = _TICKER.fullmatch(name) if name else None
        ticker = ticker_match[1] if ticker_match else None
        asset_name = ticker_match[2] if ticker_match else name
        value_low = int(amount[1].replace(",", "")) if amount else None
        value_high = int(amount[2].replace(",", "")) if amount else None
        if amount and (value_low <= 0 or value_high < value_low):
            reasons.append("value_range_invalid")
        row_id = "senate-annual:" + hashlib.sha256(
            f"{metadata['source_sha256']}:{number}:{name}:{value}".encode()).hexdigest()[:24]
        rows.append({"extraction_id": row_id, "row_number": number,
                     "asset_name": asset_name, "asset_type_raw": raw_type,
                     "owner": "Dependent Child" if owner == "Child" else owner,
                     "ticker": ticker, "value_raw": value,
                     "value_low": value_low, "value_high": value_high,
                     "income_type_raw": income_type, "income_raw": income,
                     "disposition": "excluded" if excluded else
                                    "quarantined" if reasons else "eligible",
                     "reasons": sorted(set(reasons))})
    return {"schema_version": EXTRACTION_SCHEMA, "parser_version": PARSER_VERSION,
            "source_id": "senate_efd", "document_id": metadata["document_id"],
            "source_url": metadata["document_url"],
            "source_sha256": metadata["source_sha256"],
            "filer_name": metadata["filer_name"],
            "report_year": metadata["report_year"],
            "amendment_number": metadata["amendment_number"],
            "portal_listed_date": metadata["portal_listed_date"],
            "filed_at": filing_day, "report_period_end": f"{metadata['report_year']}-12-31",
            "row_count": len(rows), "rows": rows}


def build_annual_review(evidence_root: Path, review_root: Path, roster: dict) -> dict:
    catalog_path = evidence_root / "senate_efd/annual/catalog/current.json"
    catalog_raw = catalog_path.read_bytes()
    discovery = json.loads(catalog_raw)
    selected, selection = select_current_annual_versions(discovery, roster)
    by_person = defaultdict(list)
    report_records, people, holdings = [], {}, []
    for report in selected:
        person_id = report["identity"]["person_id"]
        folder = evidence_root / "senate_efd/annual/reports" / report["document_id"]
        metadata_files = sorted(folder.glob("*.metadata.json"))
        if len(metadata_files) != 1:
            report_records.append({"document_id": report["document_id"],
                                   "person_id": person_id, "status": "not_uniquely_archived"})
            continue
        metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
        try:
            if (metadata.get("document_id") != report["document_id"] or
                    metadata.get("document_url") != report["document_url"] or
                    metadata.get("report_year") != report["report_year"] or
                    metadata.get("amendment_number") != report["amendment_number"]):
                raise SenateEfdError("Senate annual archive conflicts with catalog")
            raw = (folder / f"{metadata['source_sha256']}.html").read_bytes()
            extraction = extract_annual(metadata, raw)
            relative = (Path("senate_efd/annual/extractions") / report["document_id"] /
                        metadata["source_sha256"] / f"{PARSER_VERSION}.json")
            _write_once(review_root / relative, _json(extraction))
            status = "extracted"
        except (SenateEfdError, OSError, KeyError, ValueError) as exc:
            extraction = None
            relative = None
            status = "extraction_failed"
            error = str(exc)[:200]
        record = {"document_id": report["document_id"], "person_id": person_id,
                  "report_year": report["report_year"],
                  "amendment_number": report["amendment_number"],
                  "source_sha256": metadata.get("source_sha256"), "status": status,
                  "extraction_path": relative.as_posix() if relative else None}
        if extraction is not None:
            record["row_count"] = extraction["row_count"]
            record["quarantined_row_count"] = sum(
                row["disposition"] == "quarantined" for row in extraction["rows"])
            record["filed_at"] = extraction["filed_at"]
            by_person[person_id].append((report, extraction, record))
        else:
            record["error"] = error
        report_records.append(record)
    report_by_id = {record["document_id"]: record for record in report_records}
    for person_id in sorted({row["identity"]["person_id"] for row in selected}):
        versions = [row for row in selected if row["identity"]["person_id"] == person_id]
        ranks = [row["amendment_number"] for row in versions]
        if len(ranks) != len(set(ranks)) or sorted(ranks) != list(range(max(ranks) + 1)):
            for row in versions:
                report_by_id[row["document_id"]]["publication_state"] = "amendment_chain_unresolved"
            continue
        ordered = sorted(versions, key=lambda row: row["amendment_number"])
        filed_days = [report_by_id[row["document_id"]].get("filed_at") for row in ordered]
        if any(day is None for day in filed_days) or filed_days != sorted(filed_days):
            for row in versions:
                report_by_id[row["document_id"]]["publication_state"] = "amendment_filing_order_unresolved"
            continue
        chosen = max(versions, key=lambda row: row["amendment_number"])
        current = next(((row, extraction, record) for row, extraction, record in by_person[person_id]
                        if row["document_id"] == chosen["document_id"]), None)
        if current is None:
            report_by_id[chosen["document_id"]]["publication_state"] = "latest_extraction_missing"
            continue
        report, extraction, record = current
        if extraction["filed_at"] != report["portal_listed_date"]:
            record["publication_state"] = "filing_date_conflicts_catalog"
            continue
        if extraction["filed_at"] < extraction["report_period_end"]:
            record["publication_state"] = "filing_precedes_period"
            continue
        if any(row["disposition"] == "quarantined" for row in extraction["rows"]):
            record["publication_state"] = "report_has_unresolved_asset_rows"
            continue
        identity = report["identity"]
        person = _person(identity)
        people[person_id] = person
        for row in extraction["rows"]:
            if row["disposition"] != "eligible":
                continue
            raw_type = row["asset_type_raw"]
            instrument = ("Stock" if "Stock" in raw_type else
                          "Bond" if "Bond" in raw_type else
                          "Fund" if "Fund" in raw_type else "Other")
            holdings.append({"id": row["extraction_id"],
                             "filing_id": report["document_id"], "person_id": person_id,
                             "owner": row["owner"], "asset_name": row["asset_name"],
                             "ticker": row["ticker"],
                             "ticker_mapping_basis": "filing_explicit" if row["ticker"] else None,
                             "instrument_type": instrument,
                             "report_period_end": extraction["report_period_end"],
                             "filed_at": extraction["filed_at"] + "T00:00:00Z",
                             "value_low": row["value_low"], "value_high": row["value_high"],
                             "change_from_prior": "unknown", "source_id": "senate_efd",
                             "source": "U.S. Senate eFD", "source_url": extraction["source_url"],
                             "verification_status": "official_matched"})
        record["publication_state"] = "qualified"
        for row in versions:
            if row["document_id"] != chosen["document_id"]:
                report_by_id[row["document_id"]]["publication_state"] = "superseded"
    result = {"schema_version": SCHEMA, "source_id": "senate_efd",
              "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
              "roster_sha256": selection["roster_sha256"],
              "selection": selection, "selected_report_count": len(selected),
              "reports": report_records, "people": sorted(people.values(), key=lambda x: x["id"]),
              "reported_holdings": sorted(holdings, key=lambda x: x["id"]),
              "qualified_report_count": sum(row.get("publication_state") == "qualified"
                                            for row in report_records),
              "holding_count": len(holdings)}
    target = review_root / "senate_efd/annual/current.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_json(result))
    return result


def overlay_annual_candidate(candidate: dict, review_root: Path, *,
                             expected_roster_sha256: str) -> tuple[dict, dict]:
    path = review_root / "senate_efd/annual/current.json"
    if not path.is_file():
        return candidate, {"annual_status": "not_available", "annual_holding_count": 0}
    raw = path.read_bytes()
    annual = json.loads(raw)
    if (annual.get("schema_version") != SCHEMA or annual.get("source_id") != "senate_efd" or
            annual.get("roster_sha256") != expected_roster_sha256 or
            not isinstance(annual.get("people"), list) or
            not isinstance(annual.get("reported_holdings"), list) or
            annual.get("holding_count") != len(annual["reported_holdings"])):
        raise SenateEfdError("Senate annual review artifact is invalid")
    existing = {person["id"]: person for person in candidate["people"]}
    for person in annual["people"]:
        if person["id"] in existing:
            if any(existing[person["id"]].get(field) != person.get(field)
                   for field in ("role", "office_type", "chamber", "state", "party",
                                 "disclosure_authority")):
                raise SenateEfdError("Senate annual identity conflicts with PTR candidate")
        else:
            existing[person["id"]] = person
    ids = {row["id"] for row in candidate["reported_holdings"]}
    for row in annual["reported_holdings"]:
        if (row.get("id") in ids or row.get("person_id") not in existing or
                row.get("source_id") != "senate_efd" or
                row.get("verification_status") != "official_matched"):
            raise SenateEfdError("Senate annual holding conflicts with candidate")
        ids.add(row["id"])
    candidate["people"] = sorted(existing.values(), key=lambda row: row["id"])
    candidate["reported_holdings"] = sorted(
        [*candidate["reported_holdings"], *annual["reported_holdings"]],
        key=lambda row: row["id"])
    for row in candidate["source_health"]:
        if row.get("source_id") == "senate_efd":
            row["detail"] += (f"; annual eFD: {annual['qualified_report_count']} latest reports, "
                              f"{annual['holding_count']} holdings qualified")
    return candidate, {"annual_status": "included", "annual_review_sha256":
                       hashlib.sha256(raw).hexdigest(),
                       "annual_qualified_report_count": annual["qualified_report_count"],
                       "annual_holding_count": annual["holding_count"]}
