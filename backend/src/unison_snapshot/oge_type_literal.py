"""Narrow semantics of a complete literal in an established official Type field.

This function does not establish field geometry, source identity or eligibility.
Its caller must bind both the field and the official enum to the original PDF.
It never changes source text, text-object observations, or identifier inputs.
"""
from __future__ import annotations


def interpret_official_type_literal(raw_text: str, *, field_role: str,
                                    official_values: list[str] | tuple[str, ...]) -> dict:
    """Interpret only the observed full-token ``purchaso`` source variant.

    There is no edit distance, substring match, suffix replacement, or asset /
    comment interpretation. Another recognized observation remains independent
    evidence: this result must not overrule a Sale/Exchange conflict.
    """
    basis = {"schema_version": "oge-278t-type-literal-interpretation/v1",
             "raw_text": raw_text, "field_role": field_role,
             "official_values": list(official_values) if isinstance(official_values, (list, tuple)) else None,
             "requires_source_field_binding": True,
             "is_glyph_or_unicode_repair": False}
    if (not isinstance(raw_text, str) or field_role != "type" or
            not isinstance(official_values, (list, tuple)) or
            len(official_values) != 3 or any(not isinstance(v, str) for v in official_values) or
            {v.casefold() for v in official_values} != {"purchase", "sale", "exchange"}):
        return {**basis, "status": "unsupported", "reason": "official_type_role_not_established"}
    if raw_text.strip().casefold() != "purchaso":
        return {**basis, "status": "unsupported", "reason": "not_the_complete_supported_source_literal"}
    return {**basis, "status": "interpreted", "value": "purchase",
            "rule": "oge-278t-official-type-exact-purchaso/v1",
            "basis": "complete_source_literal_in_official_purchase_sale_exchange_field"}
