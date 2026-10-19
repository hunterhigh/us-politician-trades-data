"""Read-only House annual Schedule A hierarchy and Schedule B row observations.

This sidecar never changes the existing Schedule A qualification or publishes
Schedule B transactions. The B ledger preserves date and independent financial row anchors; pages
with other shapes remain unresolved rather than being counted as empty.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re

from .house import HouseIndexError
from .house_holdings import ASSET_RE, ASSET_TYPES, OWNER_CODES, _clean, _lines, _value


SCHEMA = "house-annual-sections-observation/v3"
DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def observe_annual_sections(pages: list[dict], extraction: dict) -> dict:
    """Describe existing A rows and B date anchors from the same source PDF."""
    sha = extraction.get("source_sha256")
    if (extraction.get("schema_version") != "house-holding-extraction/v1" or
            not re.fullmatch(r"[0-9a-f]{64}", str(sha))):
        raise HouseIndexError("House annual section observation needs source-bound extraction")
    schedule_a = []
    for row in extraction.get("rows", []):
        name = row.get("asset_name", "")
        parts = [part.strip() for part in name.split("⇒")]
        if len(parts) > 1 and all(parts):
            parent, leaf = " ⇒ ".join(parts[:-1]), parts[-1]
            hierarchy_status = "parent_and_leaf_observed" if len(parts) == 2 else "parent_path_and_leaf_observed"
        elif len(parts) > 1:
            parent, leaf, hierarchy_status = None, name, "incomplete_marker_unresolved"
        else:
            parent, leaf, hierarchy_status = None, name, "direct_asset_observed"
        schedule_a.append({
            "extraction_id": row["extraction_id"], "evidence": row.get("evidence"),
            "parent_account_raw": parent, "leaf_asset_raw": leaf,
            "account_path_raw": parts[:-1] if parent is not None else [],
            "hierarchy_status": hierarchy_status,
            "holding_review_reasons": row.get("review_reasons", []),
            "disposition": "observation_only",
        })
    schedule_b = []
    pages_with_header = []
    for page_number, page in enumerate(pages, start=1):
        words = page.get("words")
        if not isinstance(words, list):
            raise HouseIndexError("House annual section page words are invalid")
        lines = _lines(words)
        header = next(((top, tokens) for top, tokens in lines
                       if {"Asset", "Owner", "Date", "Tx.", "Amount"}.issubset(
                           {_clean(word.get("text")) for word in tokens})), None)
        if header is None:
            continue
        header_top, header_words = header
        x = {token: min(float(word["x0"]) for word in header_words
                         if _clean(word.get("text")) == token)
             for token in ("Asset", "Owner", "Date", "Tx.", "Amount", "Cap.")
             if any(_clean(word.get("text")) == token for word in header_words)}
        if not {"Asset", "Owner", "Date", "Tx.", "Amount"}.issubset(x):
            continue
        x.setdefault("Cap.", float(page["width"]))
        if not x["Asset"] < x["Owner"] < x["Date"] < x["Tx."] < x["Amount"] < x["Cap."]:
            continue
        pages_with_header.append(page_number)
        section_end = min((top for top, tokens in lines if top > header_top + 10 and
                           re.match(r"^Schedule\s+[AC-Z]\b", _clean(" ".join(
                               str(word.get("text", "")) for word in tokens)), re.I)),
                          default=float(page["height"]))
        section_end = min(section_end, min((float(word["top"]) for word in words
            if float(word["top"]) > header_top + 10 and
            "asset-type-codes.aspx" in _clean(word.get("text"))), default=float(page["height"])))
        anchors = []
        for top, tokens in lines:
            if not header_top + 10 < top < section_end:
                continue
            dates = [word for word in tokens if x["Date"] - 4 <= float(word["x0"]) < x["Tx."] - 2
                     and DATE.fullmatch(_clean(word.get("text")))]
            kinds = [word for word in tokens if x["Tx."] - 4 <= float(word["x0"]) < x["Amount"] - 2
                     and re.fullmatch(r"[PSE](?:\s*\(partial\))?", _clean(word.get("text")))]
            asset = [word for word in tokens if x["Asset"] - 4 <= float(word["x0"]) < x["Owner"] - 2]
            amount_cells = [word for word in tokens if x["Amount"] - 4 <= float(word["x0"]) < x["Cap."] - 2
                            and "$" in _clean(word.get("text"))]
            asset_codes = [word for word in asset if re.fullmatch(r"\[[A-Z0-9]{2}\]", _clean(word.get("text")))]
            if dates or (asset and (amount_cells or kinds and asset_codes)):
                raw_date = _clean(" ".join(_clean(word.get("text")) for word in tokens
                    if x["Date"] - 4 <= float(word["x0"]) < x["Tx."] - 2))
                anchors.append((top, {"text": raw_date}))
        for ordinal, (top, date_word) in enumerate(anchors, start=1):
            end = anchors[ordinal][0] - 1 if ordinal < len(anchors) else section_end
            section_words = sorted((word for word in words
                                    if top - 1 <= float(word["top"]) < end),
                                   key=lambda word: (round(float(word["top"]), 1),
                                                     float(word["x0"])))
            asset_tokens = [_clean(word.get("text")) for word in section_words
                            if x["Asset"] - 4 <= float(word["x0"]) < x["Owner"] - 2]
            # The first printed asset-type code closes the asset cell; later
            # Description/Location notes are evidence but not asset names.
            code_end = next((i + 1 for i, token in enumerate(asset_tokens)
                             if re.fullmatch(r"\[[A-Z0-9]{2}\]", token) and
                             (i + 1 == len(asset_tokens) or asset_tokens[i + 1] != "⇒")), None)
            asset_raw = _clean(" ".join(asset_tokens[:code_end])) if code_end else _clean(
                " ".join(asset_tokens))
            asset_match = ASSET_RE.fullmatch(asset_raw)
            owner_raw = _clean(" ".join(_clean(word.get("text")) for word in section_words
                                        if abs(float(word["top"]) - top) < 2.5 and
                                        x["Owner"] - 4 <= float(word["x0"]) < x["Date"] - 2))
            kind_raw = _clean(" ".join(_clean(word.get("text")) for word in section_words
                                       if x["Tx."] - 4 <= float(word["x0"]) < x["Amount"] - 2))
            amount_raw = _clean(" ".join(_clean(word.get("text")) for word in section_words
                                         if x["Amount"] - 4 <= float(word["x0"]) < x["Cap."] - 2
                                         and float(word["top"]) < top + 18))
            amount = _value(amount_raw)
            problems = []
            if asset_match is None:
                problems.append("asset_cell_unresolved")
            if owner_raw not in OWNER_CODES:
                problems.append("owner_code_unresolved")
            if not kind_raw or kind_raw[0] not in {"P", "S", "E"}:
                problems.append("transaction_type_unresolved")
            if amount is None:
                problems.append("amount_range_unresolved")
            try:
                event_date = datetime.strptime(_clean(date_word["text"]), "%m/%d/%Y").date().isoformat()
            except ValueError:
                event_date = None
                problems.append("transaction_date_unresolved")
            schedule_b.append({
                "observation_id": "house-annual-b:" + hashlib.sha256(
                    f"{sha}:{page_number}:{top:.2f}".encode()).hexdigest()[:24],
                "page_number": page_number, "top_points": round(top, 2),
                "asset_raw": asset_raw, "owner_code_raw": owner_raw,
                "asset_name": asset_match.group(1).strip() if asset_match else None,
                "account_path_raw": [part.strip() for part in asset_match.group(1).split("⇒")[:-1]]
                                    if asset_match else [],
                "leaf_asset_raw": asset_match.group(1).split("⇒")[-1].strip() if asset_match else None,
                "ticker": asset_match.group(2) if asset_match else None,
                "asset_type_code": asset_match.group(3) if asset_match else None,
                "instrument_type": ASSET_TYPES.get(asset_match.group(3)) if asset_match else None,
                "owner": OWNER_CODES.get(owner_raw),
                "transaction_type": {"P": "purchase", "S": "sale", "E": "exchange"}.get(kind_raw[:1]),
                "evidence": {"page": page_number, "section": "Schedule B",
                             "bbox_points": [x["Asset"] - 4, top - 1, x["Cap."], end]},
                "transaction_type_raw": kind_raw, "transaction_date": event_date,
                "transaction_date_raw": _clean(date_word["text"]),
                "amount_raw": amount_raw,
                "amount_low": amount[0] if amount else None,
                "amount_high": amount[1] if amount else None,
                **({"amount_low_exclusive": True} if amount and amount[1] is None else {}),
                "problems": problems, "ptr_overlap_status": "unassessed",
                "disposition": "pending_relationship_and_qualification",
            })
    schedule_b = _continue_schedule_b_rows(schedule_b)
    return {
        "schema_version": SCHEMA, "source_sha256": sha,
        "source": extraction.get("source"),
        "schedule_a_extraction_row_count": len(schedule_a),
        "schedule_a_parent_leaf_row_count": sum(
            row["hierarchy_status"] in {"parent_and_leaf_observed", "parent_path_and_leaf_observed"}
            for row in schedule_a),
        "schedule_a_rows": schedule_a,
        "schedule_b_header_pages": pages_with_header,
        "schedule_b_date_anchored_row_count": sum(bool(DATE.fullmatch(row["transaction_date_raw"])) for row in schedule_b),
        "schedule_b_observed_row_count": len(schedule_b),
        "schedule_a_coverage": {"status": "extraction_rows_only",
                                "unresolved_row_ids": [row["extraction_id"] for row in schedule_a
                                 if row["holding_review_reasons"] or "unresolved" in row["hierarchy_status"]]},
        "schedule_b_rows": schedule_b,
        "schedule_b_coverage_status": "row_anchors_only_not_complete_census",
        "production_eligible_transaction_count": 0,
    }


def _continue_schedule_b_rows(rows: list[dict]) -> list[dict]:
    """Join only explicit unfinished rows across adjacent repeated B headers."""
    result = []
    seen_pages = set()
    for row in rows:
        page = row["page_number"]
        first = page not in seen_pages
        seen_pages.add(page)
        previous = result[-1] if result else None
        if (previous and first and previous["page_number"] == page - 1 and
                previous["transaction_date"] is not None and previous["transaction_type"] is not None and
                row["transaction_date_raw"] == "" and row["transaction_type_raw"] == "" and
                row["owner_code_raw"] == "" and "asset_cell_unresolved" in previous["problems"] and
                (not row["amount_raw"] or previous["amount_raw"].endswith("-") and
                 row["amount_low"] is not None and row["amount_low"] == row["amount_high"])):
            asset_raw = _clean(previous["asset_raw"] + " " + row["asset_raw"])
            asset = ASSET_RE.fullmatch(asset_raw)
            if asset is not None:
                previous.update(asset_raw=asset_raw, asset_name=asset.group(1).strip(), ticker=asset.group(2),
                    asset_type_code=asset.group(3), instrument_type=ASSET_TYPES.get(asset.group(3)),
                    account_path_raw=[part.strip() for part in asset.group(1).split("⇒")[:-1]],
                    leaf_asset_raw=asset.group(1).split("⇒")[-1].strip())
                raw_amount = _clean(previous["amount_raw"] + " " + row["amount_raw"])
                amount = _value(raw_amount)
                previous["amount_raw"] = raw_amount
                previous["amount_low"] = amount[0] if amount else None
                previous["amount_high"] = amount[1] if amount else None
                previous["problems"] = [problem for problem in previous["problems"]
                                         if problem not in {"asset_cell_unresolved", "amount_range_unresolved"}]
                if amount is None:
                    previous["problems"].append("amount_range_unresolved")
                elif amount[1] is None:
                    previous["amount_low_exclusive"] = True
                previous["evidence"].setdefault("continuation_segments", []).append(row["evidence"])
                continue
        result.append(row)
    return result


def observe_archived_annual_pdf(pdf_path: Path, extraction: dict) -> dict:
    raw = pdf_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != extraction.get("source_sha256"):
        raise HouseIndexError("House annual section PDF does not match extraction")
    try:
        import pdfplumber
    except ImportError:
        raise HouseIndexError("House annual section observer requires pdfplumber") from None
    with pdfplumber.open(pdf_path) as document:
        pages = [{"width": page.width, "height": page.height,
                  "words": page.extract_words(keep_blank_chars=False, extra_attrs=["size"])}
                 for page in document.pages]
    return observe_annual_sections(pages, extraction)
