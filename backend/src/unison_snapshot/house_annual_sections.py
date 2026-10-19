"""Read-only House annual Schedule A hierarchy and Schedule B row observations.

This sidecar never changes the existing Schedule A qualification or publishes
Schedule B transactions. The B ledger covers date-anchored rows only; pages
with other shapes remain unresolved rather than being counted as empty.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re

from .house import HouseIndexError
from .house_holdings import ASSET_RE, ASSET_TYPES, OWNER_CODES, _clean, _lines, _value


SCHEMA = "house-annual-sections-observation/v2"
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
        parts = name.split("⇒")
        if len(parts) > 2:
            parent, leaf, hierarchy_status = None, name, "multiple_markers_unresolved"
        elif len(parts) == 2 and all(part.strip() for part in parts):
            parent, leaf, hierarchy_status = (parts[0].strip(), parts[1].strip(),
                                              "parent_and_leaf_observed")
        elif len(parts) == 2:
            parent, leaf, hierarchy_status = None, name, "incomplete_marker_unresolved"
        else:
            parent, leaf, hierarchy_status = None, name, "direct_asset_observed"
        schedule_a.append({
            "extraction_id": row["extraction_id"], "evidence": row.get("evidence"),
            "parent_account_raw": parent, "leaf_asset_raw": leaf,
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
        anchors = []
        for top, tokens in lines:
            if not header_top + 10 < top < section_end:
                continue
            dates = [word for word in tokens if x["Date"] - 4 <= float(word["x0"]) < x["Tx."] - 2
                     and DATE.fullmatch(_clean(word.get("text")))]
            kinds = [word for word in tokens if x["Tx."] - 4 <= float(word["x0"]) < x["Amount"] - 2
                     and re.fullmatch(r"[PSE](?:\s*\(partial\))?", _clean(word.get("text")))]
            asset = [word for word in tokens if x["Asset"] - 4 <= float(word["x0"]) < x["Owner"] - 2]
            if dates or (kinds and asset):
                anchors.append((top, dates[0] if len(dates) == 1 else {"text": ""}))
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
                             if re.fullmatch(r"\[[A-Z0-9]{2}\]", token)), None)
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
    return {
        "schema_version": SCHEMA, "source_sha256": sha,
        "source": extraction.get("source"),
        "schedule_a_extraction_row_count": len(schedule_a),
        "schedule_a_parent_leaf_row_count": sum(
            row["hierarchy_status"] == "parent_and_leaf_observed" for row in schedule_a),
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
