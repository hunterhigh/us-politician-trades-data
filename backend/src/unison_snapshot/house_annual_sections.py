"""House annual hierarchy, source observations and independent B disclosures.

Observing alone never qualifies a transaction. The normal candidate consumer
reproduces saved words before qualifying independent Schedule B disclosure rows.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re

from .house import HouseIndexError
from .house_holdings import ASSET_RE, ASSET_TYPES, OWNER_CODES, _clean, _lines, _value


SCHEMA = "house-annual-sections-observation/v4"
DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def annual_text_coverage(pages: list[dict], extraction: dict, schedule_b: list[dict]) -> dict:
    """Census observed section text lines, never a count of physical disclosures.

    A line linked as context is not declared parsed or non-data. Independent
    financial text not represented by a parsed row stays explicitly unresolved.
    """
    from collections import Counter
    result = []
    for number, page in enumerate(pages, 1):
        lines = _lines(page["words"])
        headers = []
        for top, words in lines:
            tokens = {_clean(word.get("text")) for word in words}
            section = "A" if {"Asset", "Owner", "Value"} <= tokens else "B" if {
                "Asset", "Owner", "Date", "Tx.", "Amount"} <= tokens else None
            if section:
                headers.append((top, words, section))
        if not headers:
            result.append({"page_number": number, "status": "target_section_not_located",
                           "sections": []})
            continue
        sections = []
        for top, words, section in headers:
            x = {token: min(float(word["x0"]) for word in words if _clean(word.get("text")) == token)
                 for token in ("Asset", "Owner")}
            limit = min((other for other, tokens in lines if other > top+10 and
                         (re.match(r"^Schedule\s+[A-Z]\b", _clean(" ".join(str(w.get("text", "")) for w in tokens)), re.I)
                          or any("asset-type-codes.aspx" in str(w.get("text", "")) for w in tokens))),
                        default=float(page["height"]))
            source_rows = extraction.get("rows", []) if section == "A" else schedule_b
            anchors = []
            for row in source_rows:
                evidence = row.get("evidence") or {}
                segments = [evidence, *evidence.get("continuation_segments", [])]
                if isinstance(evidence.get("continuation"), dict):
                    segments.append(evidence["continuation"])
                for segment in segments:
                    row_page = segment.get("page", row.get("page_number"))
                    row_top = segment.get("top_points")
                    if row_top is None and isinstance(segment.get("bbox_points"), list):
                        row_top = segment["bbox_points"][1] + 1
                    if row_top is None:
                        row_top = row.get("top_points")
                    if row_page == number and isinstance(row_top, (int, float)) and top+10 < row_top < limit:
                        anchors.append((row_top, row))
            anchors.sort(key=lambda pair: pair[0])
            observed = []
            for line_top, tokens in lines:
                if not top+10 < line_top < limit:
                    continue
                text = _clean(" ".join(str(word.get("text", "")) for word in tokens))
                previous = next((row for row_top, row in reversed(anchors) if row_top <= line_top+1), None)
                exact = next((row for row_top, row in anchors if abs(row_top-line_top) <= 1), None)
                asset_text = _clean(" ".join(str(word.get("text", "")) for word in tokens
                    if x["Asset"]-4 <= float(word["x0"]) < x["Owner"]-2))
                financial = any("$" in str(word.get("text", "")) or DATE.fullmatch(_clean(word.get("text")))
                                for word in tokens if float(word["x0"]) >= x["Owner"]-4)
                financial = financial or bool(re.search(r"\[[A-Z0-9]{2}\]", asset_text))
                represented = previous is not None and (not asset_text or
                    asset_text in _clean(previous.get("asset_raw", "")))
                if (not previous and line_top < top+40 and
                        text in ({"$1,000?"} if section == "A" else {"$200?", "Type Gains >"})):
                    disposition = "printed_header_extension"
                elif exact:
                    disposition = "parsed_row_anchor"
                elif re.match(r"^(?:D|L|Description|Location|Comments?):", text, re.I) or text == "None disclosed.":
                    disposition = "explicit_note_or_none"
                elif financial and not represented:
                    disposition = "unresolved_financial_text"
                elif previous is not None:
                    disposition = "row_context_not_independent_disclosure"
                else:
                    disposition = "unassigned_context"
                row = exact or previous
                observed.append({"top_points": round(line_top, 2), "text": text,
                    "disposition": disposition,
                    "linked_row_reference": (row.get("extraction_id") or row.get("observation_id")) if row else None})
            sections.append({"section": section, "header_top_points": top, "end_top_points": limit,
                "observed_text_line_count": len(observed),
                "disposition_counts": dict(sorted(Counter(row["disposition"] for row in observed).items())),
                "lines": observed})
        result.append({"page_number": number, "status": "target_sections_observed", "sections": sections})
    return {"schema_version": "house-annual-native-text-coverage/v1",
            "source_sha256": extraction["source_sha256"], "count_unit": "observed_native_text_lines",
            "whole_source_unique_disclosure_row_count": None, "pages": result,
            "unresolved_financial_text_line_count": sum(
                section["disposition_counts"].get("unresolved_financial_text", 0)
                for page in result for section in page["sections"]),
            "unassigned_context_line_count": sum(section["disposition_counts"].get("unassigned_context", 0)
                for page in result for section in page["sections"])}


def observe_annual_sections(pages: list[dict], extraction: dict) -> dict:
    """Describe existing A rows and B date anchors from the same source PDF."""
    import json
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
        # A ticker/type-only tail at the first asset position has no date or
        # amount anchor. Only an adjacent unfinished explicit account path may
        # consume it; all other text remains visible in the native-line census.
        first_asset = next(((line_top, tokens) for line_top, tokens in lines
            if header_top+10 < line_top < section_end and any(
                x["Asset"]-4 <= float(word["x0"]) < x["Owner"]-2 for word in tokens)), None)
        previous = schedule_b[-1] if schedule_b else None
        if first_asset and previous and previous["page_number"] == page_number-1:
            tail_top, tokens = first_asset
            tail = _clean(" ".join(str(word.get("text", "")) for word in tokens))
            combined = _clean(previous["asset_raw"] + " " + tail)
            match = ASSET_RE.fullmatch(combined)
            if (all(x["Asset"]-4 <= float(word["x0"]) < x["Owner"]-2 for word in tokens) and
                    re.fullmatch(r"\([A-Z][A-Z0-9.\-^/]{0,31}\)\s*\[[A-Z0-9]{2}\]", tail) and
                    "⇒" in previous["asset_raw"] and "asset_cell_unresolved" in previous["problems"] and
                    previous["transaction_date"] and previous["transaction_type"] and match):
                previous.update(asset_raw=combined, asset_name=match.group(1).strip(), ticker=match.group(2),
                    asset_type_code=match.group(3), instrument_type=ASSET_TYPES.get(match.group(3)),
                    account_path_raw=[part.strip() for part in match.group(1).split("⇒")[:-1]],
                    leaf_asset_raw=match.group(1).split("⇒")[-1].strip())
                previous["problems"] = [problem for problem in previous["problems"] if problem != "asset_cell_unresolved"]
                previous["evidence"].setdefault("continuation_segments", []).append({"page": page_number,
                    "section": "Schedule B", "bbox_points": [x["Asset"]-4, tail_top-1, x["Owner"]-2, tail_top+12]})
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
        "source_text_coverage": annual_text_coverage(pages, extraction, schedule_b),
        "source_word_pages": pages,
        "source_word_pages_sha256": hashlib.sha256(json.dumps(pages, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
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


RELATION_RULE = "house-annual-b-candidate-relations/v1"


def _materialize_schedule_b_reports(reports: list[dict], payloads: dict,
                                    existing: list[dict], relations: list[dict], ptrs: list[dict]) -> tuple[list, list, list]:
    """Consume qualified independent annual disclosure rows, preserving old facts on uncertainty."""
    from copy import deepcopy
    from .disclosure_amounts import validate_amount
    facts = {r["id"]: deepcopy(r) for r in existing}
    public = {r["transaction_id"]: deepcopy(r) for r in relations
              if r.get("transaction_id") in facts and r.get("source_id") == "house_clerk"}
    for identifier, relation in public.items():
        valid = {r["id"] for r in ptrs if r.get("source_id") == "house_clerk" and
                 r.get("person_id") == facts[identifier].get("person_id") and str(r.get("id","")).startswith("house-ptr:")}
        relation["possible_ptr_transaction_ids"] = sorted(set(relation["possible_ptr_transaction_ids"]) & valid)
        relation["status"] = "possible_ptr_overlap" if relation["possible_ptr_transaction_ids"] else "cross_report_relationship_unconfirmed"
    lifecycle = []
    # Without verified original-byte copy proof, an ID collision is not a
    # licence to choose a different filing context for the same physical row.
    owners = {}
    for report in reports:
        for decision in report["rows"]:
            if decision["candidate_disposition"] != "excluded_exact_source_copy":
                owners.setdefault(decision["observation_id"], []).append(report)
    for report in reports:
        payload = payloads[id(report)]
        source = report["source"]
        old = {i:r for i,r in facts.items() if r.get("filing_id") == report["document_id"]}
        if old and any(public.get(i,{}).get("source_sha256") != report["source_sha256"] for i in old):
            report["report_blocking_reasons"] = sorted(set(report["report_blocking_reasons"] +
                ["existing_annual_source_replacement_unresolved"]))
            for decision in report["rows"]:
                decision["candidate_blocking_reasons"] = sorted(set(decision["candidate_blocking_reasons"] +
                    ["existing_annual_source_replacement_unresolved"]))
        if report["report_blocking_reasons"]:
            lifecycle.append({"document_id":report["document_id"],"action":"retain_existing_block_new",
                              "retained_ids":sorted(old),"reasons":report["report_blocking_reasons"]})
            continue
        observation = payload["observation"]
        sections = [s for p in report.get("source_text_coverage", {}).get("pages", [])
                    for s in p["sections"] if s["section"] == "B"]
        explicit_none = (not observation["schedule_b_rows"] and bool(sections) and
                         any(line["text"] == "None disclosed." and line["disposition"] == "explicit_note_or_none"
                             for s in sections for line in s["lines"]) and
                         all(line["disposition"] in {"explicit_note_or_none","printed_header_extension"}
                             for s in sections for line in s["lines"]))
        if explicit_none:
            removable = [i for i in old if public.get(i,{}).get("source_sha256") == report["source_sha256"]]
            for identifier in removable:facts.pop(identifier);public.pop(identifier,None)
            lifecycle.append({"document_id":report["document_id"],"action":"remove_explicit_none",
                              "removed_ids":sorted(removable)})
            continue
        if not observation["schedule_b_rows"]:
            lifecycle.append({"document_id":report["document_id"],"action":"retain_existing_coverage_unknown",
                              "retained_ids":sorted(old)})
            continue
        for row, decision in zip(observation["schedule_b_rows"],report["rows"],strict=True):
            identifier = row["observation_id"]
            if decision["candidate_disposition"] == "excluded_exact_source_copy":
                continue
            reasons = list(decision["candidate_blocking_reasons"])
            if len(owners.get(identifier, [])) > 1:
                reasons.append("annual_id_collision_without_exact_copy_proof")
            try:
                event = datetime.strptime(row["transaction_date"],"%Y-%m-%d").date()
                filed = datetime.strptime(source["filed_date"],"%Y-%m-%d").date()
                if event > filed or event.year != int(source["filing_year"]):
                    reasons.append("annual_transaction_outside_report_period")
                validate_amount(row)
            except (ValueError,TypeError,KeyError):
                reasons.append("annual_date_or_amount_unqualified")
            if row.get("owner") not in set(OWNER_CODES.values()):reasons.append("owner_unresolved")
            if not isinstance(row.get("asset_name"),str) or not row["asset_name"].strip():reasons.append("asset_unresolved")
            if row.get("transaction_type") not in {"purchase","sale","exchange"}:reasons.append("transaction_type_unresolved")
            if row.get("asset_type_code") not in ASSET_TYPES:reasons.append("asset_type_unresolved")
            if row.get("asset_type_code") == "OP":reasons.append("annual_option_details_unresolved")
            ticker = row.get("ticker")
            if ticker is not None and not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^/]{0,31}",str(ticker)):reasons.append("ticker_unresolved")
            fact = {"id":identifier,"filing_id":report["document_id"],"person_id":report["person_id"],
                "owner":row.get("owner"),"asset_name":row.get("asset_name"),"ticker":ticker,
                "ticker_mapping_basis":"filing_explicit" if ticker else None,
                "instrument_type":row.get("instrument_type"),"transaction_type":row.get("transaction_type"),
                "option_type":None,"strike_price":None,"expiration_date":None,
                "transaction_date":row.get("transaction_date"),"filed_at":source["filed_date"]+"T00:00:00Z",
                "amount_low":row.get("amount_low"),"amount_high":row.get("amount_high"),
                **({"amount_low_exclusive":row["amount_low_exclusive"]} if "amount_low_exclusive" in row else {}),
                "position_effect":"unknown","position_effect_basis":None,"source_id":"house_clerk",
                "source":"U.S. House Clerk","source_url":report["source_url"],"verification_status":"official_matched"}
            prior = facts.get(identifier)
            if prior is not None and prior != fact:reasons.append("existing_annual_formal_projection_differs")
            if reasons:
                decision.update(candidate_disposition="quarantined",candidate_blocking_reasons=sorted(set(reasons)))
                continue
            facts[identifier] = deepcopy(prior or fact)
            public[identifier] = {"transaction_id":identifier,"source_id":"house_clerk",
                "filing_id":fact["filing_id"],"source_url":fact["source_url"],
                "source_sha256":report["source_sha256"],"section":"Schedule B",
                "page_number":row["page_number"],"top_points":row["top_points"],
                "status":decision["relation_status"],
                "possible_ptr_transaction_ids":decision["possible_ptr_transaction_ids"]}
            decision.update(candidate_disposition="retained_existing" if prior else "promoted",
                            candidate_blocking_reasons=[],formal_transaction_id=identifier)
    return ([facts[k] for k in sorted(facts)],[public[k] for k in sorted(public)],lifecycle)


def consume_schedule_b_inputs(inputs: list[dict], *, transactions: list[dict],
                              reported_holdings: list[dict],
                              existing_annual_transactions: list[dict] | None = None,
                              existing_relations: list[dict] | None = None,
                              data_cutoff_at: str | None = None) -> dict:
    """Decide existing annual observations inside the real candidate consumer.

    Annual rows are independent disclosures, never synthetic PTRs. A verified exact report copy can be
    excluded once; equal fields merely expose possible overlaps. Optional raw
    source bytes prove copies, and cached pages reproduce parsed observations.
    Missing proof blocks the new observations, never replaces old formal rows.
    """
    from collections import Counter
    import json
    from urllib.parse import urlsplit
    reports, verified, source_inputs = [], {}, {}
    seen = set()
    for payload in inputs:
        observation, extraction, identity = (payload.get(name) for name in
                                             ("observation", "extraction", "identity"))
        extraction = extraction if isinstance(extraction, dict) else {}
        identity = identity if isinstance(identity, dict) else {}
        source = observation.get("source", {}) if isinstance(observation, dict) else {}
        document, sha = source.get("document_id"), observation.get("source_sha256") if isinstance(observation, dict) else None
        source_url = source.get("source_url")
        reasons = []
        if (not isinstance(observation, dict) or observation.get("schema_version") not in {
                SCHEMA, "house-annual-sections-observation/v3"} or
                not isinstance(extraction, dict) or not isinstance(identity, dict) or
                extraction.get("source_sha256") != sha or extraction.get("source") != source or
                not re.fullmatch(r"[0-9a-f]{64}", str(sha)) or
                not re.fullmatch(r"house:[A-Z][0-9]{6}", str(identity.get("person_id"))) or
                identity.get("document_id") != document or source.get("source_id") != "house_clerk" or
                not re.fullmatch(r"20[0-9]{2}", str(source.get("filing_year"))) or
                not isinstance(source_url, str) or urlsplit(source_url).scheme != "https" or
                urlsplit(source_url).hostname != "disclosures-clerk.house.gov"):
            reasons.append("annual_source_or_identity_binding_invalid")
        key = (document, sha)
        if key in seen:
            reasons.append("annual_observation_input_repeated")
        seen.add(key)
        rows = observation.get("schedule_b_rows") if isinstance(observation, dict) else None
        if not isinstance(rows, list):
            rows = []
            reasons.append("schedule_b_rows_unknown")
        elif observation.get("schedule_b_observed_row_count") != len(rows):
            reasons.append("schedule_b_row_count_inconsistent")
        if payload.get("pages") is not None and not reasons:
            try:
                reproduced = observe_annual_sections(payload["pages"], extraction)
                # Existing v3 caches predate the additive text-line census.
                comparison = dict(reproduced)
                comparison["schema_version"] = observation.get("schema_version")
                if "source_text_coverage" not in observation:
                    comparison.pop("source_text_coverage", None)
                for name in ("source_word_pages", "source_word_pages_sha256"):
                    if name not in observation:
                        comparison.pop(name, None)
                if comparison != observation:
                    reasons.append("annual_observation_differs_from_saved_pages")
            except (ValueError, KeyError, TypeError):
                reasons.append("annual_saved_page_observation_invalid")
        elif payload.get("pages") is None:
            reasons.append("annual_saved_page_reproduction_unavailable")
        from .house_ptr import DETERMINISTIC_IDENTITY_BASES
        if (identity.get("status") not in {"matched_automatically", "suggested_requires_review"} or
                identity.get("match_basis") not in DETERMINISTIC_IDENTITY_BASES or
                not re.fullmatch(r"[0-9a-f]{64}", str(identity.get("roster_sha256"))) or
                not str(identity.get("evidence_url", "")).startswith("https://bioguide.congress.gov/")):
            reasons.append("annual_identity_not_deterministic")
        try:
            filed = datetime.strptime(source.get("filed_date"), "%Y-%m-%d").date()
            period = datetime.strptime(extraction.get("report_period_end"), "%Y-%m-%d").date()
            if (period.isoformat() != f"{source['filing_year']}-12-31" or filed < period or
                    extraction.get("report_period_basis") != "annual_member_pdf_filing_year_end"):
                raise ValueError("unsupported annual period")
            if data_cutoff_at is not None and filed > datetime.fromisoformat(data_cutoff_at.replace("Z","+00:00")).date():
                reasons.append("annual_filing_exceeds_cutoff")
        except (TypeError, ValueError, KeyError):
            reasons.append("annual_filing_or_period_date_invalid")
        if source.get("filing_type") != "O" or source.get("report_type") != "Annual Report":
            reasons.append("annual_revision_selection_unresolved")
        raw = payload.get("source_bytes")
        bytes_verified = isinstance(raw, bytes) and hashlib.sha256(raw).hexdigest() == sha
        if raw is not None and not bytes_verified:
            reasons.append("annual_source_bytes_hash_mismatch")
        report = {"document_id": document, "source_sha256": sha,
                  "person_id": identity.get("person_id") if isinstance(identity, dict) else None,
                  "identity": identity,
                  "source_url": source_url, "source": source,
                  "input_observation_sha256": hashlib.sha256(json.dumps(observation,
                      ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  "observed_row_count": len(rows) if "schedule_b_rows_unknown" not in reasons else None,
                  "coverage_status": observation.get("schedule_b_coverage_status") if isinstance(observation, dict) else "unknown",
                  "report_blocking_reasons": sorted(set(reasons)), "rows": [],
                  "source_text_coverage": reproduced.get("source_text_coverage")
                     if payload.get("pages") is not None and not reasons else None}
        for row in rows:
            from math import isfinite
            page, top = row.get("page_number"), row.get("top_points")
            position_valid = (type(page) is int and page>0 and type(top) in (int,float) and
                              isfinite(top) and top>=0 and round(top,2)==top)
            expected = "house-annual-b:" + hashlib.sha256(
                f"{sha}:{page}:{top:.2f}".encode()).hexdigest()[:24] if position_valid else None
            blockers = [*reasons, *row.get("problems", [])]
            if not position_valid or row.get("observation_id") != expected:
                blockers.append("annual_row_source_position_invalid")
            matches = []
            if not blockers:
                for tx in transactions:
                    if (str(tx.get("id", "")).startswith("house-ptr:") and tx.get("source_id") == "house_clerk" and tx.get("person_id") == report["person_id"] and
                            all(tx.get(name) == row.get(name) for name in (
                                "asset_name", "owner", "ticker", "transaction_type", "transaction_date",
                                "amount_low", "amount_high", "amount_low_exclusive"))):
                        matches.append(tx["id"])
            relation_status = "possible_ptr_overlap" if matches else "cross_report_relationship_unconfirmed"
            report["rows"].append({"observation_id": row.get("observation_id"),
                "source_reference": {"document_id": document, "source_sha256": sha,
                    "source_url": source_url, "section": "Schedule B", "evidence": row.get("evidence")},
                "relation_status": relation_status, "possible_ptr_transaction_ids": sorted(matches),
                "candidate_disposition": "blocked_pending_relationship",
                "candidate_blocking_reasons": sorted(set(blockers)),
                "formal_transaction_id": None})
        reports.append(report)
        source_inputs[id(report)] = payload
        if bytes_verified and not reasons:
            verified[key] = (payload, report)
    # Only exact original bytes plus the same reliable report context establish
    # report aliases. Never group financial rows by matching fields.
    groups = {}
    for key,(payload,report) in verified.items():
        source=report["source"]
        context=(report["person_id"],source.get("filing_year"),source.get("filing_type"),
                 source.get("filed_date"),report["source_sha256"])
        groups.setdefault(context,[]).append((payload,report))
    selected = {r.get("filing_id") for r in reported_holdings}
    existing_selected = {r.get("filing_id") for r in existing_annual_transactions or []}
    for group in groups.values():
        if len(group)<2:
            continue
        preferred=[r for _,r in group if r["document_id"] in existing_selected]
        if not preferred:
            preferred=[r for _,r in group if r["document_id"] in selected]
        if len(preferred)>1:
            for _,report in group:
                report["report_blocking_reasons"].append("multiple_existing_formal_reports_share_original")
            continue
        canonical = preferred[0] if preferred else min((r for _,r in group),key=lambda r:r["document_id"])
        canonical_payload=next(p for p,r in group if r is canonical)
        canonical_rows=canonical_payload["observation"]["schedule_b_rows"]
        for payload,report in group:
            if report is canonical:
                continue
            if payload["observation"]["schedule_b_rows"] != canonical_rows:
                report["report_blocking_reasons"].append("same_source_bytes_but_row_interpretation_differs")
                continue
            for row in report["rows"]:
                row.update(relation_status="resolved_exact_source_copy",
                    candidate_disposition="excluded_exact_source_copy", candidate_blocking_reasons=[],
                    canonical_document_id=canonical["document_id"],
                    canonical_observation_id=row["observation_id"],
                    relation_basis="same_verified_original_bytes_and_report_context")
    for report in reports:
        for row in report["rows"]:
            if row["candidate_disposition"] == "blocked_pending_relationship":
                row["candidate_blocking_reasons"] = sorted(set([
                    *row["candidate_blocking_reasons"], *report["report_blocking_reasons"]]))
    facts, public, lifecycle = _materialize_schedule_b_reports(reports, source_inputs,
        existing_annual_transactions or [], existing_relations or [], transactions)
    return {"schema_version": "house-annual-b-candidate-audit/v1", "rule_version": RELATION_RULE,
            "reports": reports, "observed_row_count": sum(r["observed_row_count"] or 0 for r in reports),
            "candidate_disposition_counts": dict(sorted(Counter(row["candidate_disposition"]
                for report in reports for row in report["rows"]).items())),
            "new_candidate_transaction_count": sum(r["candidate_disposition"] == "promoted"
                for report in reports for r in report["rows"]),
            "formal_transactions_unchanged": (not any(r["candidate_disposition"] == "promoted"
                for report in reports for r in report["rows"]) and
                {r["id"] for r in facts} == {r["id"] for r in existing_annual_transactions or []}),
            "prior_ptr_transactions_unchanged": True,
            "transactions": facts, "transaction_disclosure_relations": public,
            "existing_annual_lifecycle": lifecycle}
