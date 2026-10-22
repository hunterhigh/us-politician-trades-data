"""Auditable extraction of OGE Form 278e annual assets and Part 7 transactions.

This module produces source rows, not production ``official_matched`` facts. In
particular, the OGE receipt stamp is *not* silently converted to ``filed_at``;
Part 7 rows require cross-report de-duplication against 278-Ts before promotion.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
from math import isfinite
import re
from urllib.parse import urlsplit

from .oge import OgeCatalogError


SCHEMA = "oge-278e-annual-extraction/v1"
PARSER_VERSION = "oge-278e-tables/v1"
SHADOW_PARSER_VERSION = "oge-278e-tables/v2"
QUALIFIED_PARSER_VERSION = "oge-278e-tables/v3"
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


def read_annual_band(value: str) -> dict | None:
    """Keep the official lower-bound operator; an absent ceiling is not zero."""
    bounded = _range(value)
    if bounded is not None:
        return {"low": bounded[0], "high": bounded[1]}
    text = _compact(value)
    match = re.fullmatch(r"Over\s+\$\s*(1,000,000|50,000,000)", text, re.I)
    if match:
        return {"low": int(match[1].replace(",", "")), "high": None, "low_exclusive": True}
    match = re.fullmatch(r"\$\s*(1,000,000|50,000,000)\s*\+", text)
    if match:
        return {"low": int(match[1].replace(",", "")), "high": None, "low_exclusive": False}
    return None


def read_annual_cover_dates(text: str, *, source_sha256: str) -> dict:
    """Observe filer certification separately from annual receipt/review dates."""
    from .oge_reports import read_278t_cover_dates
    evidence = read_278t_cover_dates(text, source_sha256=source_sha256)
    start = re.search(r"Filer(?:['’]s)?\s+Certification|Electronic\s+Signature|Signature\s+of\s+(?:Filer|Reporting\s+Individual)", text, re.I)
    boundary = (re.search(r"(?im)^\s*(?:Agency\b|Other\s+Review\b|U\.?S\.?\s+Office\b|OGE\s+(?:Certification|Received)\b)",
                          text[start.end():]) if start else None)
    # A reviewing block is optional.  End of the same cover text is also a
    # boundary; requiring an agency block discarded explicit filer dates.
    stop = start.end() + boundary.start() if start and boundary else len(text) if start else None
    for observed in evidence["observations"]:
        if observed["role"] == "filer_signature" and (stop is None or observed["text_span"][0] >= stop):
            observed["role"] = "unresolved_cover_role"
    filer = [item for item in evidence["observations"] if item["role"] == "filer_signature"]
    dates = {item["date"] for item in filer if item["date"] is not None}
    reasons = (["filer_signature_date_invalid"] if any(item["date"] is None for item in filer) else
               ["filer_signature_date_ambiguous"] if len(dates) > 1 else
               ["filer_signature_date_missing"] if not dates else [])
    evidence.update(schema_version="oge-278e-cover-date-roles/v1", document_reasons=reasons,
                    filed_at=next(iter(dates)) if not reasons else None)
    return evidence


def materialize_saved_annual_scan(cover: dict, sections: dict, *, archive_metadata: dict,
                                 holding_page_observations: dict | None = None,
                                 native_cover_observation: dict | None = None,
                                 archived_pdf_path: Path | None = None,
                                 verify_transaction_blank_slots: bool = False) -> dict:
    """Reuse saved scan text, preserving missing sections instead of new OCR."""
    import json
    from types import SimpleNamespace
    from .oge_annual_scan_cover import SCHEMA as COVER_SCHEMA
    from .oge_annual_scan_sections import SCHEMA as SECTION_SCHEMA
    sha, document = archive_metadata.get("sha256"), archive_metadata.get("document_id")
    if (not isinstance(sha, str) or not _SHA.fullmatch(sha) or
            cover.get("schema_version") != COVER_SCHEMA or sections.get("schema_version") != SECTION_SCHEMA or
            cover.get("document_id") != document or sections.get("source_document_id") != document or
            cover.get("source_sha256") != sha or sections.get("source_sha256") != sha or
            cover.get("page_count") != sections.get("page_count") or
            cover.get("form_278e_text_observed") is not True or cover.get("annual_type_text_observed") is not True):
        raise OgeCatalogError("saved annual scan observations differ from source/form")
    years = cover.get("annual_year_text_candidates")
    if not isinstance(years, list) or len(years) != 1 or type(years[0]) is not int:
        raise OgeCatalogError("saved annual scan report period is unverified")
    year = years[0]
    pages = {page["page_number"]: page for page in sections.get("pages", [])}
    if (type(sections.get("page_count")) is not int or not 1 <= sections["page_count"] <= MAX_PDF_PAGES or sorted(pages) != list(range(1, sections["page_count"] + 1)) or
            pages[1].get("page_text_sha256") != cover.get("native_text_sha256") or
            len(pages) != len(sections["pages"])):
        raise OgeCatalogError("saved annual scan page inventory is not closed")
    if type(verify_transaction_blank_slots) is not bool:
        raise OgeCatalogError("annual blank-slot verification flag must be boolean")
    blank_proofs = {}
    if verify_transaction_blank_slots:
        if archived_pdf_path is None:
            raise OgeCatalogError("annual blank-slot verification requires original source replay")
        from .oge_annual_scan_sections import verify_part7_empty_slots
        blank_proofs = verify_part7_empty_slots(Path(archived_pdf_path), sections)
    transactions, quarantined, transaction_excluded = [], [], []
    slots = sections.get("part7_slots")
    if not isinstance(slots, list) or len(slots) != sections.get("part7_physical_slot_count"):
        raise OgeCatalogError("saved annual scan slot counts differ")
    seen = set()
    for slot in slots:
        page = pages.get(slot.get("page_number"), {})
        identity = slot.get("slot_id")
        if (not isinstance(identity, str) or identity in seen or page.get("section") != "part7" or
                slot.get("source_page_text_sha256") != page.get("page_text_sha256") or
                not isinstance(slot.get("raw_lines"), list) or not all(isinstance(line, str) for line in slot["raw_lines"])):
            raise OgeCatalogError("saved annual scan row binding is invalid")
        if (any(type(slot.get(name)) not in (int, float) or not isfinite(slot[name]) for name in ("top", "bottom"))
                or slot["top"] < 0 or slot["top"] >= slot["bottom"]):
            raise OgeCatalogError("saved annual scan row bounds are invalid")
        seen.add(identity)
        raw = " ".join(slot["raw_lines"])
        expected_id = "oge-annual-scan-slot:" + hashlib.sha256(
            f"{sha}|{slot['page_number']}|{slot.get('printed_number')}|{slot.get('top')}".encode()).hexdigest()[:24]
        number = re.match(r"^\s*([1-9]\d*(?:\.\d+)*)\.?\s", raw + " ")
        if identity != expected_id or number is None or number[1] != slot.get("printed_number"):
            raise OgeCatalogError("saved annual scan locator differs from raw source row")
        if re.fullmatch(r"\s*[1-9]\d*\.?\s*", raw):
            if identity in blank_proofs:
                transaction_excluded.append({"section": "part7", "page_number": slot["page_number"],
                    "row_number": slot["printed_number"], "cells": slot["raw_lines"],
                    "reason": "source_objects_verified_empty_template",
                    "native_empty_cell_evidence": blank_proofs[identity]})
                continue
            parsed, rejected = [], [_quarantine("part7", slot["page_number"], slot["raw_lines"],
                ["empty_text_not_verified_blank"], slot.get("printed_number"))]
        else:
            parsed, rejected = _recover_part7_lines(SimpleNamespace(extract_text_lines=lambda: [{"text": raw}]),
                                                    slot["page_number"], year, qualified=True)
        if not parsed and not rejected:
            rejected = [_quarantine("part7", slot["page_number"], slot["raw_lines"],
                                    ["empty_text_not_verified_blank"], slot.get("printed_number"))]
        for row in parsed + rejected:
            row["saved_scan_reference"] = {"slot_id": identity, "raw_lines": slot["raw_lines"],
                "source_page_text_sha256": page["page_text_sha256"], "top": slot.get("top"), "bottom": slot.get("bottom")}
        transactions.extend(parsed)
        quarantined.extend(rejected)
    cover_dates = None
    if native_cover_observation is not None:
        from .oge_annual_scan_cover import observe_native_cover_text
        if archived_pdf_path is None or not isinstance(native_cover_observation, dict):
            raise OgeCatalogError("annual native cover requires original source replay")
        replay_cover = observe_native_cover_text(Path(archived_pdf_path), source_sha256=sha, document_id=document)
        if (replay_cover != native_cover_observation or replay_cover["document_page_count"] != sections["page_count"] or
                replay_cover["cover_text_sha256"] != cover["native_text_sha256"]):
            raise OgeCatalogError("annual native cover differs from original or saved cover inventory")
        cover_dates = read_annual_cover_dates(replay_cover["cover_text"], source_sha256=sha)
    holdings, excluded, observed_pages = [], transaction_excluded, set()
    if holding_page_observations is not None:
        from .oge_annual_scan_sections import observe_holding_pages
        if archived_pdf_path is None or not isinstance(holding_page_observations, dict):
            raise OgeCatalogError("annual holding observations require original source replay")
        selected = holding_page_observations.get("pages")
        if not isinstance(selected, list) or any(not isinstance(page, dict) for page in selected):
            raise OgeCatalogError("annual holding page observations are invalid")
        replay = observe_holding_pages(Path(archived_pdf_path), source_sha256=sha, document_id=document,
                                      page_numbers=[page.get("page_number") for page in selected])
        comparison = replay
        if (holding_page_observations.get("schema_version") == "oge-278e-native-holding-pages/v1" and
                replay.get("schema_version") == "oge-278e-native-holding-pages/v2"):
            comparison = {**replay, "schema_version": "oge-278e-native-holding-pages/v1",
                "pages": [{key: value for key, value in page.items() if key != "number_only_cell_evidence"}
                          for page in replay["pages"]]}
        if comparison != holding_page_observations or replay["document_page_count"] != sections["page_count"]:
            raise OgeCatalogError("annual holding observations differ from original source replay")
        scopes = {}
        for page in replay["pages"]:
            number, part = page["page_number"], page["section"]
            if (pages.get(number, {}).get("section") != part or
                    pages[number].get("page_text_sha256") != page["page_text_sha256"]):
                raise OgeCatalogError("annual holding observations differ from existing section inventory")
            observed_pages.add(number)
            if len(page["tables"]) != 1:
                quarantined.append(_quarantine(part, number, [], ["table_count_unrecognized"]))
                continue
            good, bad, omitted = _parse_rows(part, "Self" if part == "part2" else "Spouse" if part == "part5" else "Unknown",
                number, page["tables"][0], year, preserve_valued_unreadable=True,
                qualified=True, scope_state=scopes.setdefault(part, {}), native_table_context=True,
                native_empty_rows={item["table_row_index"]: item for item in page.get("number_only_cell_evidence", [])
                    if item["table_index"] == 0}
                    if holding_page_observations.get("schema_version") == "oge-278e-native-holding-pages/v2" else None)
            for row in good + bad + omitted:
                row["saved_native_reference"] = {"page_number": number, "section": part,
                    "page_text_sha256": page["page_text_sha256"], "source_sha256": sha,
                    "method": replay["method"]}
            holdings.extend(good)
            quarantined.extend(bad)
            excluded.extend(omitted)
        holdings = _reject_nested_aggregates(holdings, quarantined, excluded, qualified=True)
        holdings = _reject_duplicate_page_rows(holdings, quarantined, qualified=True)
    for page in pages.values():
        if page.get("section") in {"part2", "part5", "part6"} and page["page_number"] not in observed_pages:
            quarantined.append(_quarantine(page["section"], page["page_number"], [],
                                           ["saved_section_summary_has_no_field_rows"]))
    return {"schema_version": SCHEMA, "parser_version": QUALIFIED_PARSER_VERSION,
        "source_id": "oge", "source_document_id": document, "source_sha256": sha,
        "source_url": archive_metadata["document_url"], "filer_name": archive_metadata["filer_name"],
        "evidence_archive_path": archive_metadata["archive_path"], "form_type": "278e", "report_type": "Annual",
        "annual_year": year, "report_period_end": f"{year}-12-31", "page_count": sections["page_count"],
        "filing_date": cover_dates["filed_at"] if cover_dates else None,
        **({"cover_date_evidence": cover_dates, "native_cover_observation": native_cover_observation} if cover_dates else {}),
        "oge_received_on": None, "holdings": holdings, "transactions": transactions,
        "excluded": excluded, "quarantined": quarantined, "section_pages": sections["section_pages"],
        "extraction_method": "saved_scan_observations_no_new_ocr",
        "source_cover_text_sha256": cover["native_text_sha256"],
        "saved_observation_hashes": {"cover": hashlib.sha256(json.dumps(cover, sort_keys=True).encode()).hexdigest(),
                                    "sections": hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest()},
        **({"saved_native_holding_observations": holding_page_observations} if holding_page_observations is not None else {}),
        "requires_cross_report_dedup": True, "holding_coverage_status": "partial_unverified"}


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
                rows: list[list[object]], annual_year: int, *,
                preserve_valued_unreadable: bool = False, qualified: bool = False,
                scope_state: dict | None = None,
                native_table_context: bool = False,
                native_empty_rows: dict | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Return strict rows, quarantines, and explicit non-holding exclusions."""
    header = _header(rows, section)
    if header is None:
        if preserve_valued_unreadable and section != "part7":
            # A scanned page can merge the first asset into its header cell.
            # Preserve every populated body row as evidence, without guessing
            # column positions or promoting any value from that page.
            weak_header = next((index for index, row in enumerate(rows)
                                if len(row) >= 4 and _compact(row[0]) == "#" and
                                _compact(row[1]).casefold().startswith("description")), None)
            if weak_header is not None:
                pending = [[_compact(cell) for cell in row]
                           for row in rows[weak_header + 1:]]
                return [], [_quarantine(section, page_number, cells,
                                        ["table_header_unrecognized"])
                            for cells in pending if any(cells)], []
        return [], [_quarantine(section, page_number, [], ["table_header_unrecognized"])], []
    header_index, columns = header
    parsed: list[dict] = []
    quarantined: list[dict] = []
    excluded: list[dict] = []
    state = scope_state if scope_state is not None else {}
    owner = state.get("owner", default_owner) if qualified else default_owner
    if qualified and state.get("previous_page") not in {None, page_number, page_number - 1}:
        if state.get("account_title") or state.get("owner_heading_ref"):
            state["account_scope_lost"] = True
        # Keep the last observed scope as evidence, but do not qualify it
        # across an unobserved intervening page.  In particular an explicit
        # spouse heading must not silently become the section's default Self.
    state["previous_page"] = page_number
    for source_row_index, cells_raw in enumerate(rows[header_index + 1:], start=header_index + 1):
        cells = [_compact(cell) for cell in cells_raw]
        if not any(cells):
            continue
        number = _number(cells[columns["#"]]) if len(cells) > columns["#"] else None
        description = cells[columns["description"]] if len(cells) > columns["description"] else ""
        # A table cell can merge several printed row numbers (for example
        # "11.\n12.\n13.") while leaving each asset/value on a separate
        # physical row.  A valued account must be quarantined, not mistaken
        # for a section heading and silently dropped.
        first_business_column = columns["type"] if section == "part7" else columns["value"]
        has_business_values = any(cells[first_business_column:])
        raw_number = cells[columns["#"]] if len(cells) > columns["#"] else ""
        proof = (native_empty_rows or {}).get(source_row_index)
        if (qualified and native_table_context and number is not None and not any(cells[1:]) and
                isinstance(proof, dict) and proof.get("status") == "source_objects_verified_empty" and
                _compact(proof.get("printed_number")) == raw_number):
            excluded.append({"section": section, "page_number": page_number, "row_number": number,
                "cells": cells, "reason": "source_objects_verified_empty_template", "native_empty_cell_evidence": proof})
            continue
        if (qualified and native_table_context and number is None and
                re.match(r"Endnotes?\s+for\s+Lines?\b", raw_number, re.I) and not any(cells[1:])):
            excluded.append({"section": section, "page_number": page_number, "row_number": None,
                             "cells": cells, "reason": "source_endnote_not_holding"})
            continue
        if (number is None and description and
                (not preserve_valued_unreadable or
                 not has_business_values and not re.search(r"\d", raw_number))):
            heading = description.casefold()
            if qualified and not re.fullmatch(
                    r"(?:(?:filer|self|spouse|dependent child(?:ren)?)(?:'s)? (?:assets|accounts?)|investment account\s*#[0-9]+)", heading):
                heading = ""
            if "spouse" in heading and ("account" in heading or "asset" in heading):
                owner = "Spouse"
                if qualified:
                    state.update(owner=owner, account_title=None, account_heading_ref=None, account_scope_lost=False,
                                 owner_heading_ref={"page_number": page_number, "cells": cells})
                continue
            if "dependent" in heading and ("account" in heading or "asset" in heading):
                owner = "Dependent Child"
                if qualified:
                    state.update(owner=owner, account_title=None, account_heading_ref=None, account_scope_lost=False,
                                 owner_heading_ref={"page_number": page_number, "cells": cells})
                continue
            if ("filer" in heading or "self" in heading) and ("account" in heading or "asset" in heading):
                owner = "Self"
                if qualified:
                    state.update(owner=owner, account_title=None, account_heading_ref=None, account_scope_lost=False,
                                 owner_heading_ref={"page_number": page_number, "cells": cells})
                continue
            if (not qualified and ("account" in heading or "trust" in heading) or
                    qualified and _ACCOUNT_HEADING.fullmatch(description)):
                owner = owner if qualified else default_owner
                if qualified:
                    state.update(account_title=description, account_scope_lost=False,
                                 owner=owner, account_heading_ref={"page_number": page_number, "cells": cells})
                continue
        if qualified and number is None and description.casefold() == "none" and not has_business_values:
            excluded.append({"section": section, "page_number": page_number, "row_number": None,
                             "cells": cells, "reason": "explicit_none_section"})
            continue
        if number is None:
            quarantined.append(_quarantine(section, page_number, cells, ["row_number_unreadable"]))
            continue
        evidence = {"section": section, "page_number": page_number,
                    "row_number": number, "owner": owner,
                    "asset_name": description}
        if qualified and isinstance(proof, dict):
            evidence["native_empty_cell_evidence"] = proof
        if qualified:
            evidence["owner_context"] = {"basis": "explicit_owner_heading" if state.get("owner_heading_ref") else
                "section_role" if default_owner != "Unknown" else "not_disclosed",
                "heading_ref": state.get("owner_heading_ref"), "section": section}
            prefix = re.match(r"^\((Self|Spouse|Dependent Child|Joint)\)\s+(.+)$", description, re.I)
            if prefix:
                evidence.update(owner={"self": "Self", "spouse": "Spouse", "dependent child": "Dependent Child", "joint": "Joint"}[prefix[1].casefold()],
                                asset_name=prefix[2], asset_description_raw=description,
                                owner_basis="explicit_parenthesized_source_prefix")
            evidence.update(cells=cells, account_context={
                "status": "unknown" if state.get("account_scope_lost") else
                          "observed" if state.get("account_title") else "not_observed",
                "title": state.get("account_title"),
                "heading_ref": state.get("account_heading_ref")})
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
            interval = read_annual_band(amount_raw) if qualified else None
            if kind not in _TYPES:
                reasons.append("transaction_type_unreadable")
            if when is None or not when.startswith(f"{annual_year}-"):
                reasons.append("transaction_date_unreadable_or_outside_year")
            if band is None and interval is None:
                reasons.append("transaction_amount_unreadable" if qualified else "transaction_amount_unreadable_or_open")
            if reasons:
                rejected = _quarantine(section, page_number, cells, reasons, number)
                if qualified:
                    rejected.update(evidence, raw_type=type_raw, raw_date=date_raw, raw_amount=amount_raw)
                quarantined.append(rejected)
                continue
            parsed.append({**evidence, "transaction_type": kind,
                           "transaction_date": when, "amount_low": interval["low"] if interval else band[0],
                           "amount_high": interval["high"] if interval else band[1],
                           **({"amount_low_exclusive": interval["low_exclusive"]} if interval is not None and "low_exclusive" in interval else {}),
                           "raw_type": type_raw,
                           "raw_date": date_raw, "raw_amount": amount_raw})
        else:
            value_raw = cells[columns["value"]] if len(cells) > columns["value"] else ""
            value = _range(value_raw)
            interval = read_annual_band(value_raw) if qualified else None
            # Part 5 also contains employment income sources, not just assets.
            # OGE's Salary example explicitly has EIF=N/A, no Value and no
            # Income Amount. Preserve that source row without inventing an
            # employer holding or treating its lawful blanks as unread value.
            # Require the complete, unambiguous native column roles; OCR-only
            # or incomplete tables cannot claim this classification.
            names = [_compact(cell).casefold() for cell in rows[header_index]]
            roles = ("#", "description", "eif", "value", "income type", "income amount")
            if (qualified and native_table_context and section == "part5" and not reasons
                    and evidence["owner"] == "Spouse" and evidence["account_context"]["status"] == "not_observed"
                    and all(names.count(role) == 1 for role in roles)):
                indices = {role: names.index(role) for role in roles}
                if (max(indices.values()) < len(cells) and not value_raw
                        and cells[indices["eif"]].casefold() == "n/a"
                        and cells[indices["income type"]].casefold() == "salary"
                        and not cells[indices["income amount"]]
                        and not any(cell for i, cell in enumerate(cells) if i not in indices.values())):
                    excluded.append({**evidence, "reason": "spouse_salary_income_source_not_holding",
                        "source_row_role": "spouse_salary_income_source", "raw_value": value_raw,
                        "classification_basis": "oge_part5_salary_example_explicit_native_columns",
                        "format_guidance_url": "https://www.oge.gov/web/278eGuide.nsf/Part_5"})
                    continue
            if qualified:
                evidence["value_disclosure_status"] = (
                    "explicitly_not_readily_ascertainable" if "value not readily ascertainable" in description.casefold() and not value_raw else
                    "native_cell_content_unresolved" if isinstance(proof, dict) and proof.get("status") == "unknown" else
                    "value_cell_text_empty" if not value_raw else
                    "bounded_band_observed" if value else "open_band_observed" if interval else "value_reading_unsupported")
            if "value not readily ascertainable" in description.casefold() and (not qualified or value is not None or interval is not None):
                reasons.append("asset_value_description_conflicts_with_band")
            if re.search(r"\bsee\s+lines?\b", description, re.I):
                reasons.append("asset_references_other_line")
            if value_raw.casefold().startswith("none (or less than $1,001)"):
                excluded.append({**evidence, "reason": "no_disclosed_year_end_value",
                                 "raw_value": value_raw})
                continue
            if not value_raw:
                reasons.append("holding_value_missing")
            elif value is None and interval is None:
                reasons.append("holding_value_unreadable" if qualified else "holding_value_unreadable_or_open")
            if reasons:
                rejected = _quarantine(section, page_number, cells, reasons, number)
                if qualified:
                    rejected.update(evidence, raw_value=value_raw)
                quarantined.append(rejected)
                continue
            parsed.append({**evidence, "report_period_end": f"{annual_year}-12-31",
                           "value_low": interval["low"] if interval else value[0],
                           "value_high": interval["high"] if interval else value[1],
                           **({"value_low_exclusive": interval["low_exclusive"]} if interval is not None and "low_exclusive" in interval else {}),
                           "raw_value": value_raw})
    return parsed, quarantined, excluded


def _recover_part7_lines(page: object, page_number: int, annual_year: int, *, qualified: bool = False) -> tuple[list[dict], list[dict]]:
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
        amount_pattern = (r"(?:\$[\d, ]+\s*-\s*\$[\d, ]+|Over\s+\$[\d,]+|\$[\d,]+\s*\+)"
                          if qualified else r"\$[\d, ]+\s*-\s*\$[\d, ]+")
        match = re.fullmatch(r"([1-9]\d*)\.\s+(.+?)\s+(purchase|sale|exchange)\s+"
                             r"(\d{1,2}/\d{1,2}/\d{4})\s+"
                             "(" + amount_pattern + ")", text, re.I)
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
        interval = read_annual_band(amount_raw) if qualified else None
        if when is None or not when.startswith(f"{annual_year}-") or band is None and interval is None:
            quarantined.append(_quarantine("part7", page_number, [text],
                                           ["merged_table_row_unreadable"], number))
            continue
        parsed.append({"section": "part7", "page_number": page_number,
                       "row_number": number, "owner": "Unknown", "asset_name": asset,
                       "transaction_type": kind.casefold(), "transaction_date": when,
                       "amount_low": interval["low"] if interval else band[0],
                       "amount_high": interval["high"] if interval else band[1],
                       **({"amount_low_exclusive": interval["low_exclusive"]} if interval is not None and "low_exclusive" in interval else {}),
                       "raw_type": kind, "raw_date": date_raw, "raw_amount": amount_raw})
        if qualified:
            prefix = re.match(r"^\((Self|Spouse|Dependent Child|Joint)\)\s+(.+)$", asset, re.I)
            if prefix:
                parsed[-1].update(owner={"self": "Self", "spouse": "Spouse", "dependent child": "Dependent Child", "joint": "Joint"}[prefix[1].casefold()],
                                  asset_name=prefix[2], asset_description_raw=asset,
                                  owner_basis="explicit_parenthesized_source_prefix")
    if not parsed and not quarantined:
        quarantined.append(_quarantine("part7", page_number, [], ["table_header_unrecognized"]))
    return parsed, quarantined


def _reject_nested_aggregates(holdings: list[dict], quarantined: list[dict],
                              excluded: list[dict], *, qualified: bool = False) -> list[dict]:
    """A valued parent and its valued children must not both enter an aggregate."""
    if qualified:
        all_rows = holdings + quarantined + excluded
        def source_scope(row):
            # A leaf's explicit owner prefix does not change its printed
            # parent account.  Separate owner headings and account titles do.
            return (row["section"], row.get("account_context", {}).get("title"),
                    row.get("owner_context", {}).get("heading_ref"))

        def related_rows(row):
            return [other for other in all_rows if source_scope(other) == source_scope(row)
                    and isinstance(other.get("row_number"), str)]

        for row in list(quarantined):
            if row.get("reasons") != ["holding_value_missing"] or not isinstance(row.get("row_number"), str):
                continue
            children = [other for other in related_rows(row)
                        if other["row_number"].startswith(row["row_number"] + ".")]
            if children:
                excluded.append({**row, "reason": "parent_account_without_additive_value",
                    "hierarchy_evidence": {"status": "parent_account", "basis": "explicit_hierarchical_printed_number_same_source_scope",
                        "child_refs": [{"page_number": child["page_number"], "row_number": child["row_number"]} for child in children]}})
                quarantined.remove(row)
        retained = []
        for row in holdings:
            related = related_rows(row)
            children = [other for other in related if other["row_number"].startswith(row["row_number"] + ".")]
            parents = [other for other in related if row["row_number"].startswith(other["row_number"] + ".")]
            row["hierarchy_evidence"] = {
                "status": "parent_aggregate" if children else "leaf",
                "parent_refs": [{"page_number": other["page_number"], "row_number": other["row_number"],
                                  "asset_name": other.get("asset_name")} for other in parents],
                "child_refs": [{"page_number": other["page_number"], "row_number": other["row_number"]} for other in children],
                "basis": "explicit_hierarchical_printed_number_same_section_owner_and_account"}
            if children:
                excluded.append({**row, "reason": "parent_account_aggregate_not_additive"})
            else:
                retained.append(row)
        return retained
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


def _reject_duplicate_page_rows(rows: list[dict], quarantined: list[dict], *, qualified: bool = False) -> list[dict]:
    counts: dict[tuple[str, int, str], int] = {}
    for row in rows:
        key = (row["section"], row["page_number"], row["row_number"],
               row.get("account_context", {}).get("title") if qualified else None)
        counts[key] = counts.get(key, 0) + 1
    retained = []
    for row in rows:
        if counts[(row["section"], row["page_number"], row["row_number"],
                   row.get("account_context", {}).get("title") if qualified else None)] > 1:
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
                       expected_filer: str,
                       parser_version: str = PARSER_VERSION) -> dict:
    """Extract a content-verified annual report; never mark rows production-ready.

    Every encountered table body row is parsed, explicitly excluded, or
    quarantined. ``source_sha256`` must be the independently archived hash.
    """
    if not _SHA.fullmatch(source_sha256):
        raise OgeCatalogError("OGE annual evidence hash is invalid")
    if parser_version not in {PARSER_VERSION, SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION}:
        raise OgeCatalogError("OGE annual parser version is unsupported")
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
            raise OgeCatalogError("OGE annual 278e cover is not machine-verifiable")
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
        qualified = parser_version == QUALIFIED_PARSER_VERSION
        if not qualified and (received_on is None or received_on < f"{annual_year}-12-31"):
            raise OgeCatalogError("OGE annual receipt date is unverified")
        if qualified:
            cover_dates = read_annual_cover_dates(cover, source_sha256=source_sha256)
        holdings: list[dict] = []
        transactions: list[dict] = []
        quarantined: list[dict] = []
        excluded: list[dict] = []
        section_pages: dict[str, int] = {part: 0 for part in ("part2", "part5", "part6", "part7")}
        part7_numbering = {"account": None, "next": {}, "counts": {}, "errors": []}
        scopes = {part: {} for part in section_pages}
        page_roles = []
        for page_number, page in enumerate(document.pages, 1):
            page_text = cover if page_number == 1 else page.extract_text() or ""
            section = _section(page_text)
            role = {"page_number": page_number,
                    "page_text_sha256": hashlib.sha256(page_text.encode()).hexdigest()}
            if section is None:
                if qualified:
                    role["role"] = ("cover" if page_number == 1 else
                        "other_disclosure_section" if re.search(r"\bPart\s+[13489]\s*:", page_text, re.I) else
                        "instructions" if re.search(r"\bInstructions\s+(?:for\s+)?Part\b", page_text, re.I) else "unknown")
                    page_roles.append(role)
                continue
            part, owner = section
            if qualified:
                role.update(role="target_section", section=part, status="unresolved")
                page_roles.append(role)
            section_pages[part] += 1
            tables = page.extract_tables()
            if len(tables) != 1:
                quarantined.append(_quarantine(part, page_number, [], ["table_count_unrecognized"]))
                if part == "part7":
                    part7_numbering["errors"].append({"page_number": page_number,
                                                      "reason": "table_count_unrecognized"})
                continue
            if part == "part7" and _header(tables[0], part) is None:
                recovered, rejected = _recover_part7_lines(page, page_number, annual_year, qualified=qualified)
                transactions.extend(recovered)
                quarantined.extend(rejected)
                part7_numbering["errors"].append({"page_number": page_number,
                                                  "reason": "table_header_unrecognized"})
                continue
            if part == "part7":
                _audit_part7_numbering(part7_numbering, tables[0], page_number)
            parsed, rejected, omitted = _parse_rows(
                part, owner, page_number, tables[0], annual_year,
                preserve_valued_unreadable=parser_version in {SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION},
                qualified=qualified, scope_state=scopes[part])
            if qualified:
                role["status"] = ("rows_accounted" if not rejected and (parsed or omitted) else
                                  "rows_with_quarantine" if rejected else "empty_text_not_verified_blank")
            (transactions if part == "part7" else holdings).extend(parsed)
            quarantined.extend(rejected)
            excluded.extend(omitted)
        if not section_pages["part7"]:
            quarantined.append(_quarantine("part7", 0, [], ["part7_missing"]))
        holdings = _reject_nested_aggregates(holdings, quarantined, excluded, qualified=qualified)
        holdings = _reject_duplicate_page_rows(holdings, quarantined, qualified=qualified)
        transactions = _reject_duplicate_page_rows(transactions, quarantined, qualified=qualified)
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
        complete_holdings = (qualified and all(section_pages[part] for part in ("part2", "part5", "part6"))
                             and not any(row["section"] in {"part2", "part5", "part6"} for row in quarantined)
                             and all(role["role"] != "unknown" and
                                     not (role.get("section") in {"part2", "part5", "part6"}
                                          and role.get("status") != "rows_accounted") for role in page_roles))
        return {
            "schema_version": SCHEMA, "parser_version": parser_version,
            "source_id": "oge", "form_type": "278e", "report_type": "Annual",
            "source_url": source_url, "source_sha256": source_sha256,
            "filer_name": expected_filer, "annual_year": annual_year,
            "report_period_end": f"{annual_year}-12-31",
            "oge_received_on": received_on, "filing_date": cover_dates["filed_at"] if qualified else None,
            **({"cover_date_evidence": cover_dates,
                "holding_coverage_status": "complete" if complete_holdings else "partial_unverified",
                "source_coverage_evidence": {"schema_version": "oge-278e-native-page-roles/v1",
                    "source_sha256": source_sha256, "page_count": len(document.pages), "pages": page_roles,
                    "method": "same_pass_full_archived_pdf_native_text_and_tables"},
                "date_role_status": "filer_signature_observed" if cover_dates["filed_at"] else "filer_signature_unresolved"}
               if qualified else {}),
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
            "production_qualification": "source_rows_require_date_and_candidate_qualification" if qualified else qualification,
        }
