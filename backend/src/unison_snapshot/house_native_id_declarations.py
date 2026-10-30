"""Observe native House ID declarations without granting source completeness.

Callers must obtain words and characters afresh from the same original PDF.
This pure observation does not authenticate a font, effective paint, the source
document, or the requirement that every transaction has an official number.
In particular, ``observed_declarations`` is never a qualification or an absence
proof. Source-row references are correspondence diagnostics, not authorities.
"""
from __future__ import annotations

import math
import re

from .house_native_coverage import (
    _closing_page, _geometry, _header_cells, _lines, _split_closing_region, _text,
)

SCHEMA = "house-native-id-declaration-observation/v1"
MAX_WORDS = 25_000
MAX_CHARACTERS = 25_000
MAX_SOURCE_ROWS = 25_000
MAX_SOURCE_SEGMENTS = 25_000
MAX_PRIMITIVES = 50_000
MAX_TEXT_CHARACTERS = 100_000
MAX_COMPARISONS = 200_000


def _box(item):
    values = [item.get(key) for key in ("x0", "top", "x1", "bottom")]
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
            or values[0] >= values[2] or values[1] >= values[3]):
        return None
    return values


def _contains(outer, inner):
    return outer[0] <= inner[0] <= inner[2] <= outer[2] and outer[1] <= inner[1] <= inner[3] <= outer[3]


def _intersects(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def observe_native_house_id_declarations(page, source_chars, *, source_sha256,
                                         page_number, source_rows, allow_unnumbered=False,
                                         source_pages=None):
    """Retain all declarations in an independently observed eight-column ID strip.

    Inventory selection uses original words/geometry, never successful financial
    fields or cached reported IDs. Split/bad declarations, unnumbered source
    rows, and numeric words outside explained roles remain unknown. Exact fresh
    word/character boxes and ordinal order are required: no membership padding.
    Existing template-role helpers retain their own historical tolerances.
    """
    result = {"schema_version": SCHEMA, "source_sha256": source_sha256,
        "page_number": page_number, "status": "unknown", "scope_complete": False,
        "font_paint_proven": False, "declarations": [], "unknown_declarations": [],
        "unbound_declarations": [], "unnumbered_source_rows": [],
        "outside_numeric_words": [], "known_numeric_roles": [],
        "unrepresented_id_character_indices": [], "role_evidence": {}, "reasons": [],
        "budgets": {"member_comparisons": 0, "limit": MAX_COMPARISONS}}
    reasons = set()

    def finish():
        result["reasons"] = sorted(reasons)
        result["status"] = ("unknown" if reasons else "observed_role"
            if result["role_evidence"].get("page_kind") == "closing_only" else "observed_declarations")
        return result

    if (not isinstance(source_sha256, str) or not re.fullmatch("[a-f0-9]{64}", source_sha256)
            or type(page_number) is not int or page_number < 1 or not isinstance(page, dict)):
        reasons.add("invalid_source_binding")
        return finish()
    words = page.get("words")
    if (not isinstance(words, list) or not isinstance(source_chars, list)
            or not isinstance(source_rows, list)):
        reasons.add("source_inventory_unavailable")
        return finish()
    if len(words) > MAX_WORDS or len(source_chars) > MAX_CHARACTERS or len(source_rows) > MAX_SOURCE_ROWS:
        reasons.add("source_inventory_budget_exceeded")
        return finish()
    if len({id(word) for word in words}) != len(words):
        reasons.add("source_word_membership_duplicated")
        return finish()
    if any(not isinstance(w, dict) or not isinstance(w.get("text"), str) or _box(w) is None for w in words):
        reasons.add("source_word_geometry_unavailable")
        return finish()
    if any("size" in word and (type(word["size"]) not in (int, float)
            or not math.isfinite(word["size"]) or word["size"] <= 0) for word in words):
        reasons.add("source_word_size_invalid")
        return finish()
    if any(not isinstance(c, dict) or not isinstance(c.get("text"), str) or _box(c) is None for c in source_chars):
        reasons.add("source_character_geometry_unavailable")
        return finish()
    if any("page_number" in char and char["page_number"] != page_number for char in source_chars):
        reasons.add("source_character_page_mismatch")
        return finish()
    if any(not isinstance(row, dict) for row in source_rows):
        reasons.add("source_row_inventory_invalid")
        return finish()
    if (sum(len(w["text"]) for w in words) > MAX_TEXT_CHARACTERS
            or sum(len(c["text"]) for c in source_chars) > MAX_TEXT_CHARACTERS):
        reasons.add("source_text_budget_exceeded")
        return finish()
    raw_geometry = page.get("native_page_geometry")
    if isinstance(raw_geometry, dict) and sum(len(raw_geometry.get(kind, []))
            if isinstance(raw_geometry.get(kind), list) else MAX_PRIMITIVES + 1
            for kind in ("lines", "rects", "curves", "images")) > MAX_PRIMITIVES:
        reasons.add("source_geometry_budget_exceeded")
        return finish()
    if isinstance(raw_geometry, dict):
        role_work = sum(len(raw_geometry.get(kind, [])) for kind in ("lines", "rects", "curves", "images")) * len(words)
        result["budgets"]["role_comparison_upper_bound"] = role_work
        if role_work > MAX_COMPARISONS:
            reasons.add("source_role_comparison_budget_exceeded")
            return finish()
    row_segments = []
    segment_count = 0
    for row in source_rows:
        evidence = row.get("evidence")
        segments = evidence.get("segments") if isinstance(evidence, dict) else None
        if not isinstance(segments, list) or not segments:
            reasons.add("source_row_geometry_invalid")
            segments = []
        if any(not isinstance(s, dict) or type(s.get("page")) is not int or s["page"] < 1 for s in segments):
            reasons.add("source_row_geometry_invalid")
        segment_count += len(segments)
        if segment_count > MAX_SOURCE_SEGMENTS:
            reasons.add("source_row_segment_budget_exceeded")
            return finish()
        row_segments.append(segments)
    char_boxes = [_box(c) for c in source_chars]
    budget_exceeded = False

    def members(word):
        nonlocal budget_exceeded
        box = _box(word)
        indices = []
        exact = True
        for index, char in enumerate(source_chars):
            result["budgets"]["member_comparisons"] += 1
            if result["budgets"]["member_comparisons"] > MAX_COMPARISONS:
                budget_exceeded = True
                return indices, False
            if not char["text"].strip() or not _intersects(box, char_boxes[index]):
                continue
            indices.append(index)
            if not _contains(box, char_boxes[index]):
                exact = False
        if "".join(source_chars[i]["text"] for i in indices) != word["text"]:
            exact = False
        if indices and [min(char_boxes[i][0] for i in indices), min(char_boxes[i][1] for i in indices),
                        max(char_boxes[i][2] for i in indices), max(char_boxes[i][3] for i in indices)] != box:
            exact = False
        return indices, exact

    try:
        geometry = _geometry(page, source_sha256, page_number)
        header = _header_cells(page, geometry) if geometry is not None else None
    except (KeyError, TypeError, ValueError):
        geometry, header = None, None
    if geometry is None:
        reasons.add("bound_native_geometry_unavailable")
    from .house_native_coverage import closing_role_pages
    closing_proven = (page_number in closing_role_pages(source_sha256, source_pages)
                      if source_pages is not None else False)
    if header is None:
        if geometry is not None and (closing_proven or _closing_page(page, geometry)):
            result["role_evidence"] = {"page_kind": "closing_only", "closing_role_observed": True,
                "geometry_schema": geometry["schema_version"], "closing_word_indices": list(range(len(words)))}
            represented = set()
            for index, word in enumerate(words):
                indices, exact = members(word)
                if not exact or represented.intersection(indices):
                    reasons.add("closing_word_character_membership_not_exact")
                represented.update(indices)
                if re.fullmatch(r"[0-9]+", word["text"]):
                    result["outside_numeric_words"].append({"word_index": index, "raw_text": word["text"],
                        "box": _box(word), "source_character_indices": indices, "membership_exact": exact})
                    reasons.add("numeric_word_outside_explained_id_role")
            if any(char["text"].strip() and index not in represented for index, char in enumerate(source_chars)):
                reasons.add("unrepresented_closing_characters")
            if any(s.get("page") == page_number for segments in row_segments for s in segments if isinstance(s, dict)):
                reasons.add("closing_role_conflicts_with_source_row")
            if budget_exceeded:
                reasons.add("word_character_comparison_budget_exceeded")
            return finish()
        reasons.add("unique_eight_column_header_unavailable")
        # Retain possible declarations even when their column cannot be proved.
        for index, word in enumerate(words):
            if re.fullmatch(r"[0-9]+", word["text"]):
                indices, exact = members(word)
                result["outside_numeric_words"].append({"word_index": index, "raw_text": word["text"],
                    "box": _box(word), "source_character_indices": indices, "membership_exact": exact})
        if budget_exceeded:
            reasons.add("word_character_comparison_budget_exceeded")
        return finish()
    result["role_evidence"]["header_cells"] = [list(b) for b in header]
    result["role_evidence"]["geometry_schema"] = geometry["schema_version"]
    result["role_evidence"]["header_word_indices"] = [i for i, word in enumerate(words)
        if any(_contains(box, _box(word)) for box in header)]
    split = _split_closing_region(page, geometry)
    if split is None:
        reasons.add("source_role_partition_unknown")
        table, closing, closing_geometry = page, None, None
    else:
        table, _, closing, closing_geometry = split
    closing_closed = closing is not None and (closing_proven or _closing_page(closing, closing_geometry))
    result["role_evidence"]["closing_role_observed"] = closing_closed
    closing_word_ids = {id(word) for word in closing["words"]} if closing is not None else set()
    result["role_evidence"]["closing_word_indices"] = [i for i, word in enumerate(words)
        if id(word) in closing_word_ids]
    if closing is not None and not closing_closed:
        reasons.add("closing_role_unknown")
    word_indices = {id(word): i for i, word in enumerate(words)}
    left, right = header[0][0], header[0][2]
    header_bottom = header[0][3]
    id_words = [w for w in table["words"] if w["top"] >= header_bottom
        and w["x0"] < right and w["x1"] > left]
    id_word_indices = {word_indices[id(w)] for w in id_words}
    used_chars = set()

    for line_index, line in enumerate(_lines(id_words)):
        indices, exact = [], True
        for word in line:
            original_indices, word_exact = members(word)
            if any(index in used_chars for index in original_indices):
                word_exact = False
            used_chars.update(original_indices)
            indices.extend(original_indices)
            exact &= word_exact and left <= word["x0"] < word["x1"] <= right
        raw = _text(line)
        digits = raw if len(line) == 1 and re.fullmatch(r"[0-9]{1,30}", raw) else None
        local_reasons = []
        if digits is None:
            local_reasons.append("split_or_invalid_official_id")
        if not exact:
            local_reasons.append("word_character_membership_not_exact")
        declaration = {"local_key": f"id-declaration-{line_index + 1}", "raw_text": raw,
            "reported_transaction_id": digits, "word_indices": [word_indices[id(w)] for w in line],
            "source_character_indices": indices,
            "box": [min(w["x0"] for w in line), min(w["top"] for w in line),
                    max(w["x1"] for w in line), max(w["bottom"] for w in line)],
            "source_row_indices": [], "reasons": local_reasons}
        result["declarations"].append(declaration)
        if local_reasons:
            result["unknown_declarations"].append(declaration["local_key"])
            reasons.update(local_reasons)
    if budget_exceeded:
        reasons.add("word_character_comparison_budget_exceeded")

    # A glyph in the column with no original word must not disappear through
    # extraction/tokenization. Keep original ordinals rather than inventing text.
    closing_top = min((w["top"] for w in closing["words"]), default=page["height"]) if closing is not None else page["height"]
    strip = [left, header_bottom, right, closing_top]
    for index, char in enumerate(source_chars):
        if char["text"].strip() and _intersects(strip, char_boxes[index]) and index not in used_chars:
            result["unrepresented_id_character_indices"].append(index)
    if result["unrepresented_id_character_indices"]:
        reasons.add("unrepresented_id_column_characters")

    # Correspondence is checked *after* the complete declaration inventory.
    # Rows cannot select which IDs are observed or make an extra ID unrelated.
    for row_index, row in enumerate(source_rows):
        segments = row_segments[row_index]
        local_boxes = [s.get("bbox_points") for s in segments if isinstance(s, dict) and s.get("page") == page_number]
        if not local_boxes:
            continue
        valid_boxes = [box for box in local_boxes if isinstance(box, list) and len(box) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and box[0] < box[2] and box[1] < box[3]]
        if len(valid_boxes) != len(local_boxes):
            reasons.add("source_row_geometry_invalid")
            continue
        number = row.get("reported_transaction_id")
        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,30}", number):
            result["unnumbered_source_rows"].append({"source_row_index": row_index,
                "extraction_id": row.get("extraction_id"), "boxes": valid_boxes,
                "filing_status": row.get("filing_status")})
            if not (allow_unnumbered and number in (None, '') and not any(
                    _intersects(box, declaration['box']) for box in valid_boxes
                    for declaration in result['declarations'])):
                reasons.add("native_source_row_id_unavailable")
            continue
        matches = []
        for declaration in result["declarations"]:
            result["budgets"]["member_comparisons"] += 1
            if result["budgets"]["member_comparisons"] > MAX_COMPARISONS:
                reasons.add("source_row_correspondence_budget_exceeded")
                break
            if declaration["reported_transaction_id"] == number and not declaration["reasons"]:
                for box in valid_boxes:
                    result["budgets"]["member_comparisons"] += 1
                    if result["budgets"]["member_comparisons"] > MAX_COMPARISONS:
                        reasons.add("source_row_correspondence_budget_exceeded")
                        break
                    if _contains(box, declaration["box"]):
                        matches.append(declaration)
                        break
        if len(matches) == 1:
            matches[0]["source_row_indices"].append(row_index)
        else:
            reasons.add("source_row_declaration_not_unique")
    for declaration in result["declarations"]:
        if len(declaration["source_row_indices"]) != 1:
            result["unbound_declarations"].append(declaration["local_key"])
            reasons.add("native_declaration_not_uniquely_bound_to_source_row")
    if not allow_unnumbered and not id_words and any(w["top"] >= header_bottom for w in table["words"]):
        reasons.add("native_body_without_accounted_id_declaration")

    lines = _lines(words)
    line_by_word = {id(word): line for line in lines for word in line}
    for index, word in enumerate(words):
        if index in id_word_indices or not re.fullmatch(r"[0-9]+", word["text"]):
            continue
        numeric_members, exact = members(word)
        if not exact:
            reasons.add("numeric_role_character_membership_not_exact")
        numeric_ref = {"word_index": index, "raw_text": word["text"], "box": _box(word),
            "source_character_indices": numeric_members}
        line_text = _text(line_by_word[id(word)])
        # A contextual contact postcode is not an official declaration. Other
        # naked numeric text in comments/closing cannot inherit that exemption.
        postcode = (word["top"] < header[0][1] and re.fullmatch(r"[0-9]{5}", word["text"])
            and line_text.startswith("Clerk of the House of Representatives")
            and "Legislative Resource Center" in line_text
            and re.search(r"Washington, DC " + re.escape(word["text"]) + r"$", line_text))
        if postcode:
            result["known_numeric_roles"].append({**numeric_ref,
                "role": "clerk_contact_postcode", "source_line": line_text})
        elif any(_contains(box, _box(word)) for box in header):
            result["known_numeric_roles"].append({**numeric_ref, "role": "table_header"})
        elif allow_unnumbered and ((closing_closed and id(word) in closing_word_ids)
                or (word['x0'] >= header[1][0] and sum(any(
                    s.get('page') == page_number and _contains(s['bbox_points'], _box(word))
                    for s in segments) for segments in row_segments) == 1)):
            result['known_numeric_roles'].append({**numeric_ref,
                'role': 'financial_or_closed_closing_text'})
        else:
            result["outside_numeric_words"].append(numeric_ref)
            reasons.add("numeric_word_outside_explained_id_role")
    if budget_exceeded:
        reasons.add("word_character_comparison_budget_exceeded")
    return finish()
