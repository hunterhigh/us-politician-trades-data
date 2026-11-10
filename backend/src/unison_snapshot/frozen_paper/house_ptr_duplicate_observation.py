"""Flag repeated House paper trade signatures without deleting physical rows.

Equal printed fields can describe distinct trades. This audit preserves a
comparison hint without blocking or collapsing either physical source row.
"""

from __future__ import annotations

from collections import defaultdict


def observe_repeated_trade_signatures(reading: dict, section_context: dict) -> dict:
    source_sha = reading.get("source_sha256")
    if (reading.get("schema_version") != "house-ptr-key-fields-shadow/v1" or
            section_context.get("schema_version") != "house-ptr-section-context/v1" or
            section_context.get("source_sha256") != source_sha):
        raise ValueError("House paper trade signature inputs are incompatible")
    sections = {(row["page"], row["physical_band_index"]): row
                for row in section_context["data_rows"]}
    if len(sections) != reading.get("data_candidate_count"):
        raise ValueError("House paper trade signature row census differs")
    grouped = defaultdict(list)
    for row in reading["rows"]:
        slot = (row["page"], row["physical_band_index"])
        context = sections.get(slot)
        if context is None:
            raise ValueError("House paper trade signature section row is missing")
        if row["status"] != "raw_fields_present_unverified" or not context["section_key"]:
            continue
        fields = row["fields"]
        signature = (
            context["section_key"],
            " ".join(fields["asset_raw"].upper().split()),
            fields["owner_code"], fields["event_date"],
            fields["notification_date"], fields["direction_mark"],
            fields["amount_letter"], fields["spouse_dc_over_1000000_mark"],
        )
        if any(value is None or value == "" for value in signature[:-1]):
            continue
        grouped[signature].append(slot)
    groups = []
    for signature, slots in sorted(grouped.items(), key=lambda item: item[1][0]):
        if len(slots) < 2:
            continue
        groups.append({"status": "possible_same_signature_distinct_physical_rows",
                       "section_key": signature[0],
                       "asset_raw_normalized": signature[1],
                       "owner_code": signature[2],
                       "event_date": signature[3],
                       "notification_date": signature[4],
                       "direction_mark": signature[5],
                       "amount_letter": signature[6],
                       "spouse_dc_over_1000000_mark": signature[7],
                       "slots": [{"page": page, "physical_band_index": index}
                                 for page, index in slots]})
    return {"schema_version": "house-ptr-duplicate-observation/v1",
            "source_sha256": source_sha,
            "qualification_status": "unverified_shadow_only",
            "repeated_group_count": len(groups),
            "repeated_physical_row_count": sum(len(group["slots"]) for group in groups),
            "groups": groups}
