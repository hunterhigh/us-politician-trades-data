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
from .senate_members import SenateRosterError, build_roster
from .senate_reports import _TableParser, _report_parser
from .senate_annual_transactions import SCHEMA as PART4B_SCHEMA, observe_part4b, relate_part4b_to_candidate


SCHEMA = "senate-efd-annual-review/v1"
DISCOVERY_SCHEMA = "senate-efd-annual-discovery/v1"
EXTRACTION_SCHEMA = "senate-efd-annual-extraction/v1"
PARSER_VERSION = "senate-efd-annual-html-2026-10-v3"
STRUCTURED_PARSER_VERSION = "senate-efd-annual-html-2026-10-v4"
MAX_HTML_BYTES = 25 * 1024 * 1024
_URL_PREFIX = "https://efdsearch.senate.gov/search/view/annual/"
_LABEL = re.compile(r"Annual Report for CY (20\d{2})(?: \(Amendment ([1-9]\d*)\))?")
_TITLE = re.compile(r"Annual Report for Calendar (20\d{2})(?: \(Amendment ([1-9]\d*)\))?")
_VALUE = re.compile(r"\$([\d,]+)\s*-\s*\$([\d,]+)")
_OPEN_VALUE = {"Over $1,000,000": 1000000, "Over $50,000,000": 50000000}
_PLUS_VALUE = re.compile(r"\$((?:\d{1,3}(?:,\d{3})+|\d+))\+")
_NUMBER = re.compile(r"[1-9]\d*(?:\.[1-9]\d*)*")
_TICKER = re.compile(r"([A-Z0-9][A-Z0-9.\-^/]{0,31})\s+-\s+(.+)")
_ASSET_HEADERS = ("Asset", "Asset Type", "Owner", "Value", "Income Type", "Income")


class _AssetLabelParser(_TableParser):
    """Separate official primary labels from muted HTML display notes."""

    def __init__(self):
        super().__init__()
        self.asset_labels = {}
        self._asset_capture = None
        self._strong_depth = 0

    def handle_starttag(self, tag, attrs):
        if (tag == "td" and self._depth == 1 and self._section == "Part 3. Assets" and
                self._row is not None and len(self._row) == 1):
            self._asset_capture = {"number": self._row[0], "primary": [], "notes": [],
                                   "strong_count": 0, "has_muted": False}
        elif self._asset_capture is not None:
            if tag == "strong":
                if not self._strong_depth:
                    self._asset_capture["strong_count"] += 1
                self._strong_depth += 1
            if tag == "div" and "muted" in dict(attrs).get("class", "").split():
                self._asset_capture["has_muted"] = True
            if tag in {"div", "br", "p"}:
                self._asset_capture["notes"].append(" ")
        super().handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._asset_capture is not None:
            self._asset_capture["primary" if self._strong_depth else "notes"].append(data)
        super().handle_data(data)

    def handle_endtag(self, tag):
        if self._asset_capture is not None:
            if tag == "strong" and self._strong_depth:
                self._strong_depth -= 1
            if tag == "td":
                item = self._asset_capture
                clean = lambda chunks: " ".join("".join(chunks).replace("\xa0", " ").split())
                primary, notes = clean(item["primary"]), clean(item["notes"])
                if item["strong_count"] == 0 and not item["has_muted"]:
                    primary, notes = notes, ""
                if item["number"] in self.asset_labels:
                    raise SenateEfdError("Senate annual asset label row is duplicated")
                self.asset_labels[item["number"]] = {
                    "primary_label": primary, "official_notes": notes,
                    "primary_representation": "strong" if item["strong_count"] else "plain_cell",
                    "status": "observed" if primary and item["strong_count"] <= 1 else "ambiguous",
                }
                self._asset_capture = None
                self._strong_depth = 0
        super().handle_endtag(tag)


def _structured_labels(raw: bytes) -> dict:
    parsed = _AssetLabelParser()
    parsed.feed(raw.decode("utf-8")); parsed.close()
    return parsed.asset_labels


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


def _exact_source_copy_groups(records: list[dict], verified_sources: dict[str, dict],
                              *, preferred_document_ids: set[str] | None = None) -> list[dict]:
    """Resolve aliases from exact source bytes, never from equal trade fields."""
    groups = defaultdict(list)
    for record in records:
        metadata = verified_sources.get(record["document_id"])
        if metadata is None:
            continue
        key = (record["person_id"], metadata["report_year"], metadata["amendment_number"],
               metadata["source_sha256"], tuple(_without_suffix(_words(metadata["filer_name"]))))
        groups[key].append(record)
    preferred, decisions = preferred_document_ids or set(), []
    for (person_id, year, rank, sha, _), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        chosen = [row for row in members if row["document_id"] in preferred]
        if len(chosen) != 1:
            chosen = sorted(members, key=lambda row: (
                verified_sources[row["document_id"]]["portal_listed_date"], row["document_id"]))[:1]
        canonical = chosen[0]["document_id"]
        decisions.append({"person_id": person_id, "report_year": year, "amendment_number": rank,
                          "source_sha256": sha, "canonical_document_id": canonical,
                          "alias_document_ids": sorted(row["document_id"] for row in members
                                                       if row["document_id"] != canonical),
                          "basis": "same_verified_archived_bytes_and_report_context"})
    return decisions


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


def extract_annual(metadata: dict, raw: bytes, *, structured_asset_labels: bool = False) -> dict:
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
    formal_name, heading_name = headings[0].rsplit("(", 1)
    heading_name = heading_name.rstrip(") ")
    if "," not in heading_name:
        raise SenateEfdError("Senate annual filer heading changed")
    surname, given = heading_name.split(",", 1)
    formal_name = re.sub(r"^(?:Mr\.|Mrs\.|Ms\.|The Honorable|Senator)\s+", "",
                         formal_name.strip())
    catalog_words = _without_suffix(_words(metadata["filer_name"]))
    formal_words = _without_suffix(_words(formal_name))
    if (len(catalog_words) < 2 or formal_words != catalog_words or
            _words(surname) != (catalog_words[-1],) or not _words(given)):
        raise SenateEfdError("Senate annual PDF filer conflicts with its catalog")
    filed = [text for text in parsed.text_segments if
             re.fullmatch(r"Filed \d{2}/\d{2}/\d{4} @ .+", text)]
    if len(filed) != 1:
        raise SenateEfdError("Senate annual has no unique filing timestamp")
    filing_match = re.fullmatch(r"Filed (\d{2}/\d{2}/\d{4}) @ .+", filed[0])
    filing_day = datetime.strptime(filing_match[1], "%m/%d/%Y").date().isoformat()
    tables = [table for table in parsed.tables if len(table.headers) == 7 and
              table.headers[1:] == _ASSET_HEADERS and table.headers[0] in {"", "#"} and
              table.section == "Part 3. Assets"]
    if (len(tables) != 1 or sum(text == "Part 3. Assets" for tag, text in parsed.headings if tag == "h3") != 1):
        raise SenateEfdError("Senate annual Part 3 asset table is absent or ambiguous")
    labels = _structured_labels(raw) if structured_asset_labels else {}
    rows, seen_numbers, parent_names, display_parents = [], set(), {}, {}
    ambiguous_display_parents = set()
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
        open_value = _OPEN_VALUE.get(value)
        plus_value = _PLUS_VALUE.fullmatch(value)
        low_exclusive = True if open_value is not None else False if plus_value else None
        excluded = value in {"--", "None (or less than $1,001)"}
        if not amount and not excluded and open_value is None and plus_value is None:
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
        label_source = None
        if structured_asset_labels:
            label = labels.get(number, {})
            display = label.get("primary_label")
            if display and label.get("official_notes"):
                display += " — " + label["official_notes"]
            display_context = display_parents.get(parent)
            if excluded and display:
                display_parents[number] = (display_context + " / " if display_context else "") + display
                if label.get("status") != "observed" or parent in ambiguous_display_parents:
                    ambiguous_display_parents.add(number)
            if not excluded and display_context and display:
                display = display_context + " / " + display
            if (label.get("status") != "observed" or not display or
                    (context and not display_context) or parent in ambiguous_display_parents):
                reasons.append("asset_primary_label_ambiguous")
            else:
                display_ticker = _TICKER.fullmatch(display)
                display_name = (display_ticker[2] if display_ticker and display_ticker[1] == ticker else display)
                label_source = {**label, "legacy_name_for_id": name,
                    "legacy_asset_name": asset_name, "display_asset_name": display_name,
                    "source_reference": {"source_sha256": metadata["source_sha256"],
                        "document_id": metadata["document_id"], "section": "Part 3. Assets",
                        "printed_row_number": number, "cell_index": 1},
                    "basis": "official_primary_label_separated_from_muted_notes"}
                asset_name = display_name
        value_low = int(amount[1].replace(",", "")) if amount else None
        value_high = int(amount[2].replace(",", "")) if amount else None
        if open_value is not None or plus_value:
            value_low = open_value if open_value is not None else int(plus_value[1].replace(",", ""))
            if value_low <= 0:
                reasons.append("value_range_invalid")
        if amount and (value_low <= 0 or value_high < value_low):
            reasons.append("value_range_invalid")
        row_id = "senate-annual:" + hashlib.sha256(
            f"{metadata['source_sha256']}:{number}:{name}:{value}".encode()).hexdigest()[:24]
        rows.append({"extraction_id": row_id, "row_number": number,
                     "asset_name": asset_name, "asset_type_raw": raw_type,
                     "owner": "Dependent Child" if owner == "Child" else owner,
                     "ticker": ticker, "value_raw": value,
                     "value_low": value_low, "value_high": value_high,
                     **({"value_low_exclusive": low_exclusive} if low_exclusive is not None else {}),
                     "income_type_raw": income_type, "income_raw": income,
                     "disposition": "excluded" if excluded else
                                    "quarantined" if reasons else "eligible",
                     "reasons": sorted(set(reasons)),
                     **({"asset_label_source": label_source} if label_source is not None else {})})
    return {"schema_version": EXTRACTION_SCHEMA,
            "parser_version": STRUCTURED_PARSER_VERSION if structured_asset_labels else PARSER_VERSION,
            "source_id": "senate_efd", "document_id": metadata["document_id"],
            "source_url": metadata["document_url"],
            "source_sha256": metadata["source_sha256"],
            "filer_name": metadata["filer_name"],
            "report_year": metadata["report_year"],
            "amendment_number": metadata["amendment_number"],
            "portal_listed_date": metadata["portal_listed_date"],
            "filed_at": filing_day, "report_period_end": f"{metadata['report_year']}-12-31",
            "row_count": len(rows), "rows": rows}



def _holding_from_annual_row(row: dict, report: dict, extraction: dict, person_id: str) -> dict:
    raw_type = row["asset_type_raw"]
    instrument = ("Stock" if "Stock" in raw_type else
                  "Bond" if "Bond" in raw_type else
                  "Fund" if "Fund" in raw_type else "Other")
    return {"id": row["extraction_id"],
                     "filing_id": report["document_id"], "person_id": person_id,
                     "owner": row["owner"], "asset_name": row["asset_name"],
                     "ticker": row["ticker"],
                     "ticker_mapping_basis": "filing_explicit" if row["ticker"] else None,
                     "instrument_type": instrument,
                     "report_period_end": extraction["report_period_end"],
                     "filed_at": extraction["filed_at"] + "T00:00:00Z",
                     "value_low": row["value_low"], "value_high": row["value_high"],
                     **({"value_low_exclusive": row["value_low_exclusive"]}
                        if "value_low_exclusive" in row else {}),
                     "change_from_prior": "unknown", "source_id": "senate_efd",
                     "source": "U.S. Senate eFD", "source_url": extraction["source_url"],
                     "verification_status": "official_matched"}


def build_annual_review(evidence_root: Path, review_root: Path, roster: dict, *,
                        structured_asset_labels: bool = False) -> dict:
    catalog_path = evidence_root / "senate_efd/annual/catalog/current.json"
    catalog_raw = catalog_path.read_bytes()
    discovery = json.loads(catalog_raw)
    selected, selection = select_current_annual_versions(discovery, roster)
    by_person = defaultdict(list)
    report_records, people, holdings, verified_sources = [], {}, [], {}
    previous_path = review_root / "senate_efd/annual/current.json"
    previous = json.loads(previous_path.read_bytes()) if previous_path.is_file() else {}
    preferred = {row["document_id"] for row in previous.get("reports", [])
                 if row.get("publication_state") == "qualified"}
    for report in selected:
        person_id = report["identity"]["person_id"]
        folder = evidence_root / "senate_efd/annual/reports" / report["document_id"]
        metadata_files = sorted(folder.glob("*.metadata.json"))
        if len(metadata_files) != 1:
            report_records.append({"document_id": report["document_id"],
                                   "person_id": person_id, "status": "not_uniquely_archived"})
            continue
        metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
        transaction_observation = transaction_relative = None
        transaction_error = "annual_source_not_parsed"
        try:
            if (metadata.get("document_id") != report["document_id"] or
                    metadata.get("document_url") != report["document_url"] or
                    metadata.get("portal_listed_date") != report["portal_listed_date"] or
                    metadata.get("report_year") != report["report_year"] or
                    metadata.get("amendment_number") != report["amendment_number"]):
                raise SenateEfdError("Senate annual archive conflicts with catalog")
            raw = (folder / f"{metadata['source_sha256']}.html").read_bytes()
            if (metadata.get("schema_version") != "senate-efd-annual-archive/v1" or
                    hashlib.sha256(raw).hexdigest() != metadata["source_sha256"] or
                    len(raw) != metadata.get("byte_length")):
                raise SenateEfdError("Senate annual source bytes do not match archive")
            verified_sources[report["document_id"]] = metadata
            try:
                transaction_observation = observe_part4b(metadata, raw)
                transaction_relative = (Path("senate_efd/annual/part4b") /
                                        report["document_id"] / metadata["source_sha256"] /
                                        "observation-v2.json")
                _write_once(review_root / transaction_relative,
                            _json(transaction_observation))
                transaction_error = None
            except (SenateEfdError, KeyError, ValueError) as exc:
                # A new transaction-section issue cannot remove qualified holdings.
                transaction_observation = None
                transaction_relative = None
                transaction_error = str(exc)[:200]
            # The annual asset section and transaction section are independent
            # disclosures. A malformed Part 3 row must not erase readable 4b.
            extraction = extract_annual(metadata, raw, structured_asset_labels=structured_asset_labels)
            relative = (Path("senate_efd/annual/extractions") / report["document_id"] /
                        metadata["source_sha256"] / f"{extraction['parser_version']}.json")
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
        record["part4b_observation_status"] = (
            "observed" if transaction_observation is not None else "unresolved")
        record["part4b_observation_path"] = (
            transaction_relative.as_posix() if transaction_relative else None)
        record["part4b_printed_row_count"] = (
            transaction_observation["part4b_printed_row_count"]
            if transaction_observation is not None else None)
        if transaction_error is not None:
            record["part4b_observation_error"] = transaction_error
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
    source_copies = _exact_source_copy_groups(report_records, verified_sources, preferred_document_ids=preferred)
    aliases = {alias: decision["canonical_document_id"] for decision in source_copies
               for alias in decision["alias_document_ids"]}
    for alias, canonical in aliases.items():
        report_by_id[alias].update(publication_state="exact_source_copy", exact_source_copy_of=canonical)
    for person_id in sorted({row["identity"]["person_id"] for row in selected}):
        versions = [row for row in selected if row["identity"]["person_id"] == person_id and
                    row["document_id"] not in aliases]
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
            holdings.append(_holding_from_annual_row(row, report, extraction, person_id))
        record["publication_state"] = "qualified"
        for row in versions:
            if row["document_id"] != chosen["document_id"]:
                report_by_id[row["document_id"]]["publication_state"] = "superseded"
    result = {"schema_version": SCHEMA, "source_id": "senate_efd",
              "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
              "roster_sha256": selection["roster_sha256"],
              "selection": selection, "selected_report_count": len(selected),
              "exact_source_copies": source_copies,
              "reports": report_records, "people": sorted(people.values(), key=lambda x: x["id"]),
              "reported_holdings": sorted(holdings, key=lambda x: x["id"]),
              "qualified_report_count": sum(row.get("publication_state") == "qualified"
                                            for row in report_records),
              "holding_count": len(holdings)}
    if structured_asset_labels:
        extraction_rows = {row["extraction_id"]: row for versions in by_person.values()
                           for _, extraction, _ in versions for row in extraction["rows"]}
        result["asset_label_mode"] = "structured_v4"
        result["asset_label_differences"] = [{"holding_id": row["id"], "filing_id": row["filing_id"],
            **extraction_rows[row["id"]]["asset_label_source"]} for row in result["reported_holdings"]
            if extraction_rows[row["id"]].get("asset_label_source", {}).get("legacy_asset_name") != row["asset_name"]]
    target = review_root / "senate_efd/annual/current.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_json(result))
    return result


def _archived_roster_members(evidence_root: Path, sha: str) -> list[dict]:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SenateEfdError("Senate annual roster evidence hash is invalid")
    path = evidence_root / "senate_efd" / "members" / f"{sha}.xml"
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise SenateEfdError("Senate annual roster evidence hash does not match")
        return build_roster(raw)["members"]
    except (OSError, SenateRosterError):
        raise SenateEfdError("Senate annual roster evidence is unavailable or invalid") from None


def _verify_annual_source_copies(annual: dict, evidence_root: Path | None) -> list[dict]:
    """Reproduce new source-copy decisions inside the actual consumer."""
    if "exact_source_copies" not in annual:
        return []  # Frozen legacy review artifacts retain their existing path.
    declared = annual["exact_source_copies"]
    if not isinstance(declared, list):
        raise SenateEfdError("Senate annual exact source-copy decisions are invalid")
    groups = defaultdict(list)
    for record in annual.get("reports", []):
        sha = record.get("source_sha256")
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha):
            groups[(record.get("person_id"), record.get("report_year"),
                    record.get("amendment_number"), sha)].append(record)
    repeated = [record for group in groups.values() if len(group) > 1 for record in group]
    if not repeated:
        if declared:
            raise SenateEfdError("Senate annual source-copy decision has no repeated original")
        return []
    if evidence_root is None:
        raise SenateEfdError("Senate annual exact source copies require archived original bytes")
    verified = {}
    for record in repeated:
        document, sha = record["document_id"], record["source_sha256"]
        folder = evidence_root / "senate_efd/annual/reports" / document
        metadata = json.loads((folder / f"{sha}.metadata.json").read_bytes())
        raw = (folder / f"{sha}.html").read_bytes()
        if (metadata.get("schema_version") != "senate-efd-annual-archive/v1" or
                metadata.get("document_id") != document or metadata.get("source_sha256") != sha or
                metadata.get("document_url") != f"{_URL_PREFIX}{document}/" or
                hashlib.sha256(raw).hexdigest() != sha or metadata.get("byte_length") != len(raw) or
                metadata.get("report_year") != record.get("report_year") or
                metadata.get("amendment_number") != record.get("amendment_number")):
            raise SenateEfdError("Senate annual source-copy decision differs from archived source")
        verified[document] = metadata
    preferred = {decision.get("canonical_document_id") for decision in declared}
    expected = _exact_source_copy_groups(repeated, verified, preferred_document_ids=preferred)
    if declared != expected:
        raise SenateEfdError("Senate annual source-copy decisions do not reproduce from archived bytes")
    alias_to_canonical = {alias: decision["canonical_document_id"] for decision in expected
                          for alias in decision["alias_document_ids"]}
    by_id = {record["document_id"]: record for record in annual["reports"]}
    for alias, canonical in alias_to_canonical.items():
        if (by_id[alias].get("publication_state") != "exact_source_copy" or
                by_id[alias].get("exact_source_copy_of") != canonical or
                by_id[canonical].get("publication_state") == "exact_source_copy"):
            raise SenateEfdError("Senate annual source-copy report dispositions are inconsistent")
    if any(row.get("filing_id") in alias_to_canonical for row in annual["reported_holdings"]):
        raise SenateEfdError("Senate annual source-copy alias was promoted as an additional holding")
    return expected


def _verify_structured_holdings(annual: dict, review_root: Path, evidence_root: Path | None) -> None:
    """Reproduce opt-in display corrections, including their unchanged old IDs."""
    mode = annual.get("asset_label_mode")
    structured_paths = any(str(report.get("extraction_path", "")).endswith(
        f"/{STRUCTURED_PARSER_VERSION}.json") for report in annual.get("reports", []))
    if mode is None and not structured_paths and "asset_label_differences" not in annual:
        return
    if mode != "structured_v4" or evidence_root is None:
        raise SenateEfdError("Senate structured annual labels require archived source evidence")
    replayed = {}
    for report in annual["reports"]:
        if report.get("status") != "extracted":
            continue
        document, sha = report["document_id"], report["source_sha256"]
        folder = evidence_root / "senate_efd/annual/reports" / document
        metadata = json.loads((folder / f"{sha}.metadata.json").read_bytes())
        if (metadata.get("document_id") != document or metadata.get("source_sha256") != sha or
                metadata.get("document_url") != f"{_URL_PREFIX}{document}/"):
            raise SenateEfdError("Senate structured annual labels crossed source identity")
        extraction = extract_annual(metadata, (folder / f"{sha}.html").read_bytes(),
                                    structured_asset_labels=True)
        relative = (Path("senate_efd/annual/extractions") / document / sha /
                    f"{STRUCTURED_PARSER_VERSION}.json")
        if (report.get("extraction_path") != relative.as_posix() or
                json.loads((review_root / relative).read_bytes()) != extraction):
            raise SenateEfdError("Senate structured annual extraction differs from archived HTML")
        replayed[document] = (report, extraction,
                              {row["extraction_id"]: row for row in extraction["rows"]})
    differences = []
    for holding in annual["reported_holdings"]:
        value = replayed.get(holding.get("filing_id"))
        if value is None:
            raise SenateEfdError("Senate structured holding has no source extraction")
        report, extraction, rows = value
        row = rows.get(holding.get("id"))
        if (row is None or row["disposition"] != "eligible" or
                report.get("publication_state") != "qualified" or
                holding != _holding_from_annual_row(row, report, extraction, report["person_id"])):
            raise SenateEfdError("Senate structured holding differs from its source projection")
        if row["asset_label_source"]["legacy_asset_name"] != holding["asset_name"]:
            differences.append({"holding_id": holding["id"], "filing_id": holding["filing_id"],
                                **row["asset_label_source"]})
    if annual.get("asset_label_differences") != differences:
        raise SenateEfdError("Senate structured annual label differences do not reproduce")


def overlay_annual_candidate(candidate: dict, review_root: Path, *,
                             expected_roster_sha256: str,
                             evidence_root: Path | None = None) -> tuple[dict, dict]:
    path = review_root / "senate_efd/annual/current.json"
    if not path.is_file():
        return candidate, {"annual_status": "not_available", "annual_holding_count": 0}
    annual_raw = path.read_bytes()
    annual = json.loads(annual_raw)
    annual_roster_sha = annual.get("roster_sha256")
    if (annual.get("schema_version") != SCHEMA or annual.get("source_id") != "senate_efd" or
            not isinstance(annual_roster_sha, str) or
            not re.fullmatch(r"[0-9a-f]{64}", annual_roster_sha) or
            not isinstance(annual.get("people"), list) or
            not isinstance(annual.get("reported_holdings"), list) or
            annual.get("holding_count") != len(annual["reported_holdings"])):
        raise SenateEfdError("Senate annual review artifact is invalid")
    if annual_roster_sha != expected_roster_sha256:
        if evidence_root is None:
            raise SenateEfdError("Senate annual roster evidence is required for compatibility")
        annual_members = _archived_roster_members(evidence_root, annual_roster_sha)
        ptr_members = _archived_roster_members(evidence_root, expected_roster_sha256)
        if annual_members != ptr_members:
            raise SenateEfdError("Senate annual roster members differ from PTR roster")
    exact_source_copies = _verify_annual_source_copies(annual, evidence_root)
    _verify_structured_holdings(annual, review_root, evidence_root)
    # Rebuild the annual projection from its current review artifact. A later
    # annual run must replace, not accumulate, prior-year or amended rows.
    candidate["reported_holdings"] = [
        row for row in candidate["reported_holdings"]
        if not str(row.get("id", "")).startswith("senate-annual:")]
    retained_people = {row["person_id"] for row in candidate["transactions"]}
    retained_people.update(row["person_id"] for row in candidate["reported_holdings"])
    existing = {person["id"]: person for person in candidate["people"]
                if person["id"] in retained_people}
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
            row["detail"] = row["detail"].split("; annual eFD:", 1)[0]
            row["detail"] += (f"; annual eFD: {annual['qualified_report_count']} latest reports, "
                              f"{annual['holding_count']} holdings qualified")
    compatibility = ("same_raw_source" if annual_roster_sha == expected_roster_sha256
                     else "same_members")
    transaction_relations = []
    for report in annual.get("reports", []):
        if report.get("publication_state") == "exact_source_copy":
            transaction_relations.append({"document_id": report["document_id"],
                                          "source_sha256": report["source_sha256"],
                                          "canonical_document_id": report["exact_source_copy_of"],
                                          "status": "resolved_exact_source_copy",
                                          "observed_row_count": report.get("part4b_printed_row_count"),
                                          "new_candidate_transaction_count": 0})
            continue
        if report.get("publication_state") == "superseded":
            continue
        observation_path = report.get("part4b_observation_path")
        if not isinstance(observation_path, str) or not observation_path.endswith("/observation-v2.json"):
            transaction_relations.append({"document_id": report["document_id"],
                                          "status": "part4b_observation_unavailable",
                                          "observed_row_count": None})
            continue
        source_sha = report.get("source_sha256")
        expected_path = (Path("senate_efd/annual/part4b") / report["document_id"] /
                         str(source_sha) / "observation-v2.json")
        if observation_path != expected_path.as_posix():
            raise SenateEfdError("Senate annual Part 4b observation path crosses source identity")
        observation = json.loads((review_root / expected_path).read_bytes())
        if (observation.get("schema_version") != PART4B_SCHEMA or
                observation.get("document_id") != report["document_id"] or
                observation.get("source_sha256") != source_sha):
            raise SenateEfdError("Senate annual Part 4b observation differs from selected report")
        if evidence_root is None:
            transaction_relations.append({"document_id": report["document_id"],
                                          "status": "archived_bytes_verification_required",
                                          "observed_row_count": observation.get("part4b_printed_row_count")})
            continue
        source_folder = evidence_root / "senate_efd/annual/reports" / report["document_id"]
        metadata = json.loads((source_folder / f"{source_sha}.metadata.json").read_bytes())
        source_raw = (source_folder / f"{source_sha}.html").read_bytes()
        if observe_part4b(metadata, source_raw) != observation:
            raise SenateEfdError("Senate annual Part 4b observation differs from archived bytes")
        relation = relate_part4b_to_candidate(
            observation, person_id=report["person_id"], transactions=candidate["transactions"])
        relation["annual_report_publication_state"] = report.get("publication_state")
        transaction_relations.append(relation)
    return candidate, {"annual_status": "included",
                       "annual_roster_compatibility": compatibility,
                       "annual_roster_sha256": annual_roster_sha,
                       "ptr_roster_sha256": expected_roster_sha256,
                       "annual_review_sha256": hashlib.sha256(annual_raw).hexdigest(),
                       "annual_qualified_report_count": annual["qualified_report_count"],
                       "annual_holding_count": annual["holding_count"],
                       "annual_exact_source_copies": exact_source_copies,
                       "annual_part4b_relationships": transaction_relations}
