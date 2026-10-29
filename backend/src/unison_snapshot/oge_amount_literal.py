"""Interpret complete statutory interval literals without rewriting source text."""
from __future__ import annotations
import re


def interpret_grouped_statutory_amount(raw: str, *, field_role: str) -> dict | None:
    """A three-digit grouping mark is not an arbitrary decimal repair.

    Both currency endpoints and their explicit separator must be present. The
    entire pair must uniquely equal an existing OGE statutory whole-dollar
    category; no prefix, extra character, open interval or missing digit enters.
    This states a category meaning, not what punctuation was printed.
    """
    if field_role != "amount" or not isinstance(raw, str):
        return None
    number = r"(?:[0-9]{1,3}(?:[,.\s][0-9]{3})+|[0-9]+)"
    match = re.fullmatch(r"\s*\$\s*("+number+r")\s*[-–—]\s*\$\s*("+number+r")\s*", raw)
    if match is None or not any(re.search(r"\.[0-9]{3}(?:$|[^0-9])", x) for x in match.groups()):
        return None
    values = []
    for token in match.groups():
        separators = set(re.findall(r"[^0-9]", token))
        # A single endpoint with comma and decimal-like punctuation is not
        # unambiguous grouping. Whitespace grouping is already handled elsewhere.
        if len(separators) > 1:
            return None
        digits = re.sub(r"[^0-9]", "", token)
        if len(digits) > 1 and digits.startswith("0"):
            return None
        values.append(int(digits))
    from .oge_reports import _AMOUNTS
    if tuple(values) not in set(_AMOUNTS.values()):
        return None
    return {"values": values, "raw_literal": raw,
            "interpretation_basis": "complete_oge_statutory_interval_grouping_semantics/v1",
            "punctuation_glyph_identity": "not_asserted",
            "source_text_rewritten": False}
