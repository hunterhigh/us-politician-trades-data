"""Auditable extraction of OGE Form 278e annual assets and Part 7 transactions.

This module produces source rows, not production ``official_matched`` facts. In
particular, the OGE receipt stamp is *not* silently converted to ``filed_at``;
Part 7 rows require cross-report de-duplication against 278-Ts before promotion.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError


SCHEMA = "oge-278e-annual-extraction/v1"
PARSER_VERSION = "oge-278e-tables/v1"
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 1200

_NUMBER = re.compile(r"[1-9]\d*(?:\.\d+)*\.?")
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
_SHA = re.compile(r"[0-9a-f]{64}")
_VALUE_RANGES = {
    (1001, 15000), (15001, 50000), (50001, 100000),
    (100001, 250000), (250001, 500000), (500001, 1000000),
    (1000001, 5000000), (5000001, 25000000), (25000001, 50000000),
}
_AMOUNTS = _VALUE_RANGES
_TYPES = {"purchase", "sale", "exchange"}
_ACCOUNT_HEADING = re.compile(r"INVESTMENT\s+ACCOUNT\s*#\s*([1-9]\d*)", re.I)
_SECTIONS = {
    "part 2: filer's employment assets": ("part2", "Self"),
    "part 5: spouse's employment assets": ("part5", "Spouse"),
    "part 6: other assets and income": ("part6", "Unknown"),
    "part 7: transactions": ("part7", "Unknown"),
}


def _compact(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _number(value: str) -> str | None:
    value = value.strip()
    return value.rstrip(".") if _NUMBER.fullmatch(value) else None


def _range(value: str) -> tuple[int, int] | None:
    """Only accept an unambiguous printed statutory band, with spacing repaired."""
    value = _compact(value).replace("–", "-").replace("—", "-")
    value = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ",", value)
    value = re.sub(r"\s+to\s+", " - ", value, flags=re.IGNORECASE)
    match = re.fullmatch(r"\$(\d{1,3}(?:,\d{3})*)\s*-\s*\$(\d{1,3}(?:,\d{3})*)", value)
    if match is None:
        return None
    pair = (int(match[1].replace(",", "")), int(match[2].replace(",", "")))
    return pair if pair in _VALUE_RANGES else None


def _date(value: str) -> str | None:
    value = _compact(value)
    if not _DATE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _section(text: str) -> tuple[str, str] | None:
    lowered = text.casefold().replace("’", "'")
    for title, section in _SECTIONS.items():
        if title in lowered:
            return section
    return None


def _header(rows: list[list[object]], section: str) -> tuple[int, dict[str, int]] | None:
    required = ("#", "description", "type", "date", "amount") if section == "part7" else (
        "#", "description", "value")
    for index, row in enumerate(rows):
        names = [_compact(cell).casefold() for cell in row]
        if all(name in names for name in required):
            return index, {name: names.index(name) for name in required}
    return None


def _quarantine(section: str, page: int, cells: list[str], reasons: list[str],
                row_number: str | None = None) -> dict:
    return {"section": section, "page_number": page, "row_number": row_number,
            "cells": cells, "reasons": sorted(set(reasons))}


def _parse_rows(section: str, default_owner: str, page_number: int,
                rows: list[list[object]], annual_year: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Return strict rows, quarantines, and explicit non-holding exclusions."""
    header = _header(rows, section)
    if header is None:
        return [], [_quarantine(section, page_number, [], ["table_header_unrecognized"])], []
    header_index, columns = header
    parsed: list[dict] = []
    quarantined: list[dict] = []
    excluded: list[dict] = []
    owner = default_owner
    for cells_raw in rows[header_index + 1:]:
        cells = [_compact(cell) for cell in cells_raw]
        if not any(cells):
            continue
        number = _number(cells[columns["#"]]) if len(cells) > columns["#"] else None
        description = cells[columns["description"]] if len(cells) > columns["description"] else ""
        if number is None and description:
            heading = description.casefold()
            if "spouse" in heading and ("account" in heading or "asset" in heading):
                owner = "Spouse"
                continue
            if "dependent" in heading and ("account" in heading or "asset" in heading):
                owner = "Dependent Child"
                continue
            if ("filer" in heading or "self" in heading) and ("account" in heading or "asset" in heading):
                owner = "Self"
                continue
            if "account" in heading or "trust" in heading:
                owner = default_owner
                continue
        if number is None:
            quarantined.append(_quarantine(section, page_number, cells, ["row_number_unreadable"]))
            continue
        evidence = {"section": section, "page_number": page_number,
                    "row_number": number, "owner": owner,
                    "asset_name": description}
        reasons: list[str] = []
        if not description or description.casefold() in {"none", "n/a", "na"}:
            reasons.append("asset_unreadable")
        if description.startswith("***"):
            reasons.append("asset_footnote_unresolved")
        if section == "part7":
            type_raw = cells[columns["type"]] if len(cells) > columns["type"] else ""
            date_raw = cells[columns["date"]] if len(cells) > columns["date"] else ""
            amount_raw = cells[columns["amount"]] if len(cells) > columns["amount"] else ""
            kind = type_raw.casefold()
            when = _date(date_raw)
            band = _range(amount_raw)
            if kind not in _TYPES:
                reasons.append("transaction_type_unreadable")
            if when is None or not when.startswith(f"{annual_year}-"):
                reasons.append("transaction_date_unreadable_or_outside_year")
            if band is None:
                reasons.append("transaction_amount_unreadable_or_open")
            if reasons:
                quarantined.append(_quarantine(section, page_number, cells, reasons, number))
                continue
            parsed.append({**evidence, "transaction_type": kind,
                           "transaction_date": when, "amount_low": band[0],
                           "amount_high": band[1], "raw_type": type_raw,
                           "raw_date": date_raw, "raw_amount": amount_raw})
        else:
            value_raw = cells[columns["value"]] if len(cells) > columns["value"] else ""
            value = _range(value_raw)
            if "value not readily ascertainable" in description.casefold():
                reasons.append("asset_value_description_conflicts_with_band")
            if re.search(r"\bsee\s+lines?\b", description, re.I):
                reasons.append("asset_references_other_line")
            if value_raw.casefold().startswith("none (or less than $1,001)"):
                excluded.append({**evidence, "reason": "no_disclosed_year_end_value",
                                 "raw_value": value_raw})
                continue
            if not value_raw:
                reasons.append("holding_value_missing")
            elif value is None:
                reasons.append("holding_value_unreadable_or_open")
            if reasons:
                quarantined.append(_quarantine(section, page_number, cells, reasons, number))
                continue
            parsed.append({**evidence, "report_period_end": f"{annual_year}-12-31",
                           "value_low": value[0], "value_high": value[1],
                           "raw_value": value_raw})
    return parsed, quarantined, excluded


def _recover_part7_lines(page: object, page_number: int, annual_year: int) -> tuple[list[dict], list[dict]]:
    """Recover fully printed rows when drawn table cells merge across lines.

    This is deliberately confined to one-row-per-line records with every
    field printed on that line. It never distributes a merged date/type cell
    across adjacent rows by position or sequence.
    """
    if not hasattr(page, "extract_text_lines"):
        return [], [_quarantine("part7", page_number, [], ["table_header_unrecognized"])]
    parsed: list[dict] = []
    quarantined: list[dict] = []
    for line in page.extract_text_lines():
        text = _compact(line.get("text"))
        match = re.fullmatch(r"([1-9]\d*)\.\s+(.+?)\s+(purchase|sale|exchange)\s+"
                             r"(\d{1,2}/\d{1,2}/\d{4})\s+"
                             r"(\$[\d, ]+\s*-\s*\$[\d, ]+)", text, re.I)
        if match is None:
            # Printed empty template slots (e.g. 11. through 19.) are not
            # transactions. A populated numbered line that fails the strict
            # grammar must remain visible in the quarantine audit.
            if re.match(r"^[1-9]\d*\.\s*\S", text) and not re.fullmatch(r"[1-9]\d*\.", text):
                quarantined.append(_quarantine("part7", page_number, [text],
                                               ["merged_table_row_unreadable"]))
            continue
        number, asset, kind, date_raw, amount_raw = match.groups()
        when = _date(date_raw)
        band = _range(amount_raw)
        if when is None or not when.startswith(f"{annual_year}-") or band is None:
            quarantined.append(_quarantine("part7", page_number, [text],
                                           ["merged_table_row_unreadable"], number))
            continue
        parsed.append({"section": "part7", "page_number": page_number,
                       "row_number": number, "owner": "Unknown", "asset_name": asset,
                       "transaction_type": kind.casefold(), "transaction_date": when,
                       "amount_low": band[0], "amount_high": band[1],
                       "raw_type": kind, "raw_date": date_raw, "raw_amount": amount_raw})
    if not parsed and not quarantined:
        quarantined.append(_quarantine("part7", page_number, [], ["table_header_unrecognized"]))
    return parsed, quarantined


def _reject_nested_aggregates(holdings: list[dict], quarantined: list[dict],
                              excluded: list[dict]) -> list[dict]:
    """A valued parent and its valued children must not both enter an aggregate."""
    by_section: dict[tuple[str, str], set[str]] = {}
    for row in holdings + quarantined + excluded:
        number = row.get("row_number")
        owner = row.get("owner")
        if number and owner:
            by_section.setdefault((row["section"], owner), set()).add(number)
    retained = []
    for row in holdings:
        numbers = by_section[(row["section"], row["owner"])]
        if any(other.startswith(row["row_number"] + ".") for other in numbers):
            quarantined.append(_quarantine(row["section"], row["page_number"],
                                           [row["asset_name"], row["raw_value"]],
                                           ["nested_aggregate_may_double_count"], row["row_number"]))
        else:
            retained.append(row)
    return retained


def _reject_duplicate_page_rows(rows: list[dict], quarantined: list[dict]) -> list[dict]:
    counts: dict[tuple[str, int, str], int] = {}
    for row in rows:
        key = (row["section"], row["page_number"], row["row_number"])
        counts[key] = counts.get(key, 0) + 1
    retained = []
    for row in rows:
        if counts[(row["section"], row["page_number"], row["row_number"])] > 1:
            quarantined.append(_quarantine(row["section"], row["page_number"],
                                           [row["asset_name"]], ["duplicate_page_row_number"],
                                           row["row_number"]))
        else:
            retained.append(row)
    return retained


def _audit_part7_numbering(state: dict, rows: list[list[object]], page_number: int) -> None:
    """Check printed row numbers in order, across pages and account resets."""
    header = _header(rows, "part7")
    if header is None:
        state["errors"].append({"page_number": page_number, "reason": "table_header_unrecognized"})
        return
    index, columns = header
    for raw in rows[index + 1:]:
        if len(raw) <= max(columns.values()):
            state["errors"].append({"page_number": page_number, "reason": "column_count_unrecognized"})
            continue
        number = _number(_compact(raw[columns["#"]]))
        description = _compact(raw[columns["description"]])
        if number is None:
            heading = _ACCOUNT_HEADING.fullmatch(description)
            if heading:
                account = int(heading[1])
                previous = state["account"]
                if ((previous is None and account != 1) or
                        (previous is not None and account not in {previous, previous + 1})):
                    state["errors"].append({"page_number": page_number,
                                            "reason": "account_sequence_violation",
                                            "account": account})
                state["account"] = account
                state["next"].setdefault(account, 1)
            continue
        account = state["account"]
        if account is None:
            state["errors"].append({"page_number": page_number,
                                    "reason": "numbered_row_without_account", "row_number": number})
            continue
        if not number.isdecimal():
            state["errors"].append({"page_number": page_number,
                                    "reason": "account_row_number_not_integer",
                                    "account": account, "row_number": number})
            continue
        current = int(number)
        expected = state["next"][account]
        if current != expected:
            state["errors"].append({"page_number": page_number,
                                    "reason": "account_row_number_nonconsecutive",
                                    "account": account, "expected": expected, "actual": current})
        state["next"][account] = current + 1
        state["counts"][account] = state["counts"].get(account, 0) + 1


def extract_annual_pdf(pdf_path: Path, *, source_url: str, source_sha256: str,
                       expected_filer: str) -> dict:
    """Extract a content-verified annual report; never mark rows production-ready.

    Every encountered table body row is parsed, explicitly excluded, or
    quarantined. ``source_sha256`` must be the independently archived hash.
    """
    if not _SHA.fullmatch(source_sha256):
        raise OgeCatalogError("OGE annual evidence hash is invalid")
    url = urlsplit(source_url)
    if (url.scheme != "https" or url.hostname not in {"extapps2.oge.gov", "www2.oge.gov", "oge.gov", "www.oge.gov"}
            or not url.path.casefold().endswith(".pdf")):
        raise OgeCatalogError("OGE annual source URL is not an official PDF")
    if not isinstance(expected_filer, str) or not expected_filer.strip():
        raise OgeCatalogError("OGE annual expected filer is required")
    content = pdf_path.read_bytes()
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
        raise OgeCatalogError("OGE annual PDF envelope is invalid or too large")
    if hashlib.sha256(content).hexdigest() != source_sha256:
        raise OgeCatalogError("OGE annual PDF does not match archived SHA-256")
    try:
        import pdfplumber
    except ImportError:
        raise OgeCatalogError("OGE annual extraction requires pdfplumber") from None
    with pdfplumber.open(pdf_path) as document:
        if not 1 <= len(document.pages) <= MAX_PDF_PAGES:
            raise OgeCatalogError("OGE annual PDF page count is outside bounds")
        cover = document.pages[0].extract_text() or ""
        if "278e" not in cover.casefold() or not re.search(r"Report\s+Type:\s*Annual", cover, re.I):
            raise OgeCatalogError("OGE PDF is not an annual 278e")
        year_match = re.search(r"Year\s*\(Annual\s+Report\s+only\):\s*(20\d{2})", cover, re.I)
        if year_match is None:
            raise OgeCatalogError("OGE annual report year is unverified")
        annual_year = int(year_match[1])
        name_lines = cover.splitlines()
        name_line = next((name_lines[index + 1] for index, line in enumerate(name_lines[:-1])
                          if "Last Name" in line and "First Name" in line), "")
        expected_tokens = re.findall(r"[a-z]+", expected_filer.casefold())
        actual_tokens = re.findall(r"[a-z]+", name_line.casefold())
        if not expected_tokens or not all(token in actual_tokens for token in expected_tokens):
            raise OgeCatalogError("OGE annual filer does not match the cover")
        received_match = re.search(r"OGE\s+Received\s+(\d{1,2}/\d{1,2}/\d{4})", cover, re.I)
        received_on = _date(received_match[1]) if received_match else None
        if received_on is None or received_on < f"{annual_year}-12-31":
            raise OgeCatalogError("OGE annual receipt date is unverified")
        holdings: list[dict] = []
        transactions: list[dict] = []
        quarantined: list[dict] = []
        excluded: list[dict] = []
        section_pages: dict[str, int] = {part: 0 for part in ("part2", "part5", "part6", "part7")}
        part7_numbering = {"account": None, "next": {}, "counts": {}, "errors": []}
        for page_number, page in enumerate(document.pages, 1):
            section = _section(page.extract_text() or "")
            if section is None:
                continue
            part, owner = section
            section_pages[part] += 1
            tables = page.extract_tables()
            if len(tables) != 1:
                quarantined.append(_quarantine(part, page_number, [], ["table_count_unrecognized"]))
                if part == "part7":
                    part7_numbering["errors"].append({"page_number": page_number,
                                                      "reason": "table_count_unrecognized"})
                continue
            if part == "part7" and _header(tables[0], part) is None:
                recovered, rejected = _recover_part7_lines(page, page_number, annual_year)
                transactions.extend(recovered)
                quarantined.extend(rejected)
                part7_numbering["errors"].append({"page_number": page_number,
                                                  "reason": "table_header_unrecognized"})
                continue
            if part == "part7":
                _audit_part7_numbering(part7_numbering, tables[0], page_number)
            parsed, rejected, omitted = _parse_rows(part, owner, page_number, tables[0], annual_year)
            (transactions if part == "part7" else holdings).extend(parsed)
            quarantined.extend(rejected)
            excluded.extend(omitted)
        if not section_pages["part7"]:
            quarantined.append(_quarantine("part7", 0, [], ["part7_missing"]))
        holdings = _reject_nested_aggregates(holdings, quarantined, excluded)
        holdings = _reject_duplicate_page_rows(holdings, quarantined)
        transactions = _reject_duplicate_page_rows(transactions, quarantined)
        numbered_quarantines = sum(row["section"] == "part7" and row["row_number"] is not None
                                   for row in quarantined)
        unnumbered_quarantines = sum(row["section"] == "part7" and row["row_number"] is None
                                     for row in quarantined)
        printed_count = sum(part7_numbering["counts"].values())
        numbering_complete = bool(printed_count and not part7_numbering["errors"])
        row_reconciliation_complete = (
            numbering_complete and not unnumbered_quarantines and
            printed_count == len(transactions) + numbered_quarantines)
        qualification = (
            "blocked_by_part7_numbering_or_reconciliation"
            if not row_reconciliation_complete else
            "pending_filing_date_cross_report_dedup_and_row_quarantine")
        return {
            "schema_version": SCHEMA, "parser_version": PARSER_VERSION,
            "source_id": "oge", "form_type": "278e", "report_type": "Annual",
            "source_url": source_url, "source_sha256": source_sha256,
            "filer_name": expected_filer, "annual_year": annual_year,
            "report_period_end": f"{annual_year}-12-31",
            "oge_received_on": received_on, "filing_date": None,
            "page_count": len(document.pages), "section_pages": section_pages,
            "holdings": holdings, "transactions": transactions,
            "excluded": excluded, "quarantined": quarantined,
            "part7_numbering": {
                "scope": "investment_account" if part7_numbering["counts"] else "unverified",
                "account_printed_row_counts": {str(key): value for key, value in sorted(part7_numbering["counts"].items())},
                "printed_row_count": printed_count,
                "strictly_parsed_row_count": len(transactions),
                "numbered_quarantine_count": numbered_quarantines,
                "unnumbered_quarantine_count": unnumbered_quarantines,
                "numbering_complete": numbering_complete,
                "row_reconciliation_complete": row_reconciliation_complete,
                "errors": part7_numbering["errors"],
            },
            "requires_cross_report_dedup": True,
            "production_qualification": qualification,
        }
