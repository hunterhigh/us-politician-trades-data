"""Read-only filing chronology for House paper PTR relationship review.

An OCR date shape is not a verified transaction date. This module only states
which report pairs would be impossible if the target date is later verified.
It cannot qualify a row, settle an amendment, or deduplicate transactions.
"""

from __future__ import annotations

from datetime import date
import re

from .house_ptr import QUALIFICATION_SCHEMA

PARSER_VERSION = "house-paper-observation-qualification-v1"

def _day(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        raise ValueError("House paper filing date is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("House paper filing date is invalid") from None


def _source(metadata: dict) -> tuple[str, str, str, str]:
    if not isinstance(metadata, dict):
        raise ValueError("House paper metadata is invalid")
    document_id = metadata.get("document_id")
    source_sha = metadata.get("sha256")
    filer = metadata.get("filer_name")
    district = metadata.get("state_district")
    url = metadata.get("source_url")
    if (not isinstance(document_id, str) or
            re.fullmatch(r"\d{1,12}", document_id) is None or
            not isinstance(source_sha, str) or
            re.fullmatch(r"[0-9a-f]{64}", source_sha) is None or
            not isinstance(filer, str) or not filer.strip() or
            not isinstance(district, str) or not district.strip() or
            metadata.get("source_id") != "house_clerk" or
            metadata.get("filing_type") != "P" or
            url != f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/"
                   f"{metadata.get('filing_year')}/{document_id}.pdf"):
        raise ValueError("House paper source metadata is not bound to an official PTR")
    _day(metadata.get("filed_date"))
    return document_id, source_sha, filer, district


def audit_paper_temporal_relations(qualification: dict, metadata: dict,
                                   peers: list[dict]) -> dict:
    """Compare one paper report's observed rows to official peer filing dates."""
    document_id, source_sha, filer, district = _source(metadata)
    if (not isinstance(qualification, dict) or
            qualification.get("schema_version") != QUALIFICATION_SCHEMA or
            qualification.get("parser_version") != PARSER_VERSION or
            qualification.get("document_id") != document_id or
            qualification.get("source_sha256") != source_sha or
            not isinstance(qualification.get("quarantined"), list) or
            qualification.get("transactions") != [] or
            not isinstance(qualification.get("qualification"), dict) or
            qualification.get("qualification", {}).get("production_eligible") is not False or
            not isinstance(peers, list)):
        raise ValueError("House paper temporal audit inputs are invalid")
    rows = [row for row in qualification["quarantined"]
            if isinstance(row, dict) and isinstance(row.get("raw_fields"), dict)]
    if len({row.get("observation_key") for row in rows}) != len(rows) or not rows:
        raise ValueError("House paper temporal audit has missing or duplicate data slots")
    dates = {}
    for row in rows:
        key = row.get("observation_key")
        if not isinstance(key, str) or not key.startswith(f"paper-slot:{source_sha}:"):
            raise ValueError("House paper data slot is not bound to the source")
        raw = row["raw_fields"]
        observed = raw.get("event_date")
        status = raw.get("event_date_status")
        if observed is not None and (
                not isinstance(status, str) or not status.endswith("_unverified")):
            raise ValueError("House paper event date is not an unverified observation")
        dates[key] = _day(observed) if observed is not None else None
    observed_days = [day for day in dates.values() if day is not None]
    if not observed_days:
        raise ValueError("House paper target has no observed event dates")
    results = []
    seen = {document_id}
    for peer in peers:
        peer_id, peer_sha, peer_filer, peer_district = _source(peer)
        if peer_id in seen or peer_filer != filer or peer_district != district:
            raise ValueError("House paper peer is duplicated or belongs to another filer")
        seen.add(peer_id)
        filed = _day(peer["filed_date"])
        conditional = sorted(key for key, day in dates.items()
                             if day is not None and filed < day)
        unresolved = sorted(set(dates) - set(conditional))
        results.append({
            "peer_document_id": peer_id, "peer_source_sha256": peer_sha,
            "peer_filed_date": filed.isoformat(),
            "conditional_chronology_exclusion_count": len(conditional),
            "conditional_chronology_exclusion_slots": conditional,
            "relationship_unresolved_count": len(unresolved),
            "relationship_unresolved_slots": unresolved,
        })
    return {
        "schema_version": "house-paper-temporal-relationship-observation/v1",
        "qualification_status": "read_only_unverified_dates",
        "document_id": document_id, "source_sha256": source_sha,
        "observed_data_slot_count": len(rows),
        "observed_event_date_count": len(observed_days),
        "observed_event_date_min": min(observed_days).isoformat(),
        "observed_event_date_max": max(observed_days).isoformat(),
        "peer_count": len(results),
        "peers": sorted(results, key=lambda row: row["peer_document_id"]),
    }
