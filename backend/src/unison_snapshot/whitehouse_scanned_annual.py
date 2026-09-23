"""Source-bound corrections for exceptional scanned White House annual reports.

The public PDFs are the evidence.  This module does not create facts from a
name or URL: every correction is bound to an immutable PDF SHA-256 and keeps
the original OCR cells and failure reasons in the review artifact.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from .oge_annual import _range


RECOVERY_METHOD = "whitehouse-scanned-annual-source-bound/v1"

_ATTESTATIONS = {
    "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3": {
        "filer_name": "Donald Trump",
        "filing_date": "2026-06-29",
        "position_title_raw": "President of the United States of America",
        "source_url": ("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                       "President-Donald-J.-Trump-2025-Annual-Report.pdf"),
        "recover_quarantined_rows": False,
    },
    "d43f25659a26474faae4df8218ff352e3c01a2eaf17b6ae35ae07649bbe90c3d": {
        "filer_name": "JD Vance",
        "filing_date": "2026-06-23",
        "position_title_raw": "Vice President of the United States",
        "source_url": ("https://www.whitehouse.gov/wp-content/uploads/2026/06/"
                       "Vice-President-JD-Vance-2025-Annual-Report.pdf"),
        "recover_quarantined_rows": True,
    },
}

_RECOVERABLE_REASONS = {
    "holding_ocr_confidence_below_threshold",
    "holding_value_unreadable_or_open",
}
_ROW_NUMBER = re.compile(r"[1-9]\d*(?:\.[1-9]\d*)*")
_LEADING_DESCRIPTION_NOISE = re.compile(r"^[\s|_{}\[\]]+")
_LEADING_VALUE_NOISE = re.compile(r"^[\s|_{}\[\]1]+(?=\$)")


def _attestation(extraction: dict) -> dict | None:
    source_sha256 = extraction.get("source_sha256")
    attestation = _ATTESTATIONS.get(source_sha256)
    if (attestation is None or extraction.get("source_url") != attestation["source_url"] or
            extraction.get("filer_name") != attestation["filer_name"] or
            extraction.get("report_type") != "Annual" or
            extraction.get("report_period_end") != "2025-12-31" or
            extraction.get("holding_valuation_date") != "2025-12-31"):
        return None
    return attestation


def _clean_description(value: object) -> str | None:
    if not isinstance(value, str) or "\ufffd" in value:
        return None
    cleaned = " ".join(_LEADING_DESCRIPTION_NOISE.sub("", value).split())
    # An internal vertical bar is ambiguous (for example, Roman I versus a
    # table border) and is deliberately not repaired.
    return cleaned if cleaned and "|" not in cleaned else None


def _clean_value(value: object) -> str | None:
    if not isinstance(value, str) or "\ufffd" in value:
        return None
    cleaned = " ".join(_LEADING_VALUE_NOISE.sub("", value).split())
    return cleaned if _range(cleaned) is not None else None


def _recover_holding(row: dict, extraction: dict) -> dict | None:
    reasons = row.get("reasons")
    raw = row.get("raw_columns")
    mean = row.get("ocr_mean_confidence")
    row_number = row.get("row_number")
    section = row.get("section")
    if (not isinstance(reasons, list) or not reasons or
            not set(reasons) <= _RECOVERABLE_REASONS or
            section not in {"part2", "part5", "part6"} or
            not isinstance(row_number, str) or not _ROW_NUMBER.fullmatch(row_number) or
            not isinstance(raw, dict) or not isinstance(mean, (int, float)) or mean < 85.0):
        return None
    description = _clean_description(raw.get("description"))
    value = _clean_value(raw.get("value"))
    if description is None or value is None:
        return None
    band = _range(value)
    if band is None:
        return None
    owner = row.get("owner")
    expected_owner = {"part2": "Self", "part5": "Spouse"}.get(section)
    if (expected_owner is not None and owner != expected_owner) or (
            section == "part6" and owner != "Unknown"):
        return None
    recovered = {
        "section": section,
        "page_number": row.get("page_number"),
        "row_number": row_number,
        "asset_name": description,
        "owner": owner,
        "raw_columns": {**raw, "description": description, "value": value},
        "value_low": band[0],
        "value_high": band[1],
        "report_period_end": extraction["report_period_end"],
        "holding_valuation_date": extraction["holding_valuation_date"],
        "holding_valuation_status": "exact_period_end",
        "ocr_mean_confidence": mean,
        "ocr_min_confidence": row.get("ocr_min_confidence"),
        "ocr_recovery": {
            "method": RECOVERY_METHOD,
            "source_sha256": extraction["source_sha256"],
            "original_raw_columns": raw,
            "original_reasons": reasons,
        },
    }
    return recovered


def apply_scanned_annual_corrections(extraction: dict) -> dict:
    """Return a corrected copy only for the two immutable attested PDFs."""

    attestation = _attestation(extraction)
    if attestation is None:
        return extraction
    corrected = deepcopy(extraction)
    corrected["filing_date"] = attestation["filing_date"]
    corrected["source_bound_original_position_title_raw"] = corrected.get(
        "position_title_raw")
    corrected["position_line_raw"] = attestation["position_title_raw"]
    corrected["position_title_raw"] = attestation["position_title_raw"]
    document_reasons = corrected.get("document_reasons", [])
    if "filer_handwritten_signature_or_date_unverified" in document_reasons:
        corrected["source_bound_resolved_document_reasons"] = [
            "filer_handwritten_signature_or_date_unverified"]
        corrected["document_reasons"] = [
            reason for reason in document_reasons
            if reason != "filer_handwritten_signature_or_date_unverified"]
    corrected["signature_evidence"] = {
        "type": "handwritten_scan",
        "page_number": 1,
        "signed_on": attestation["filing_date"],
        "filer_name": attestation["filer_name"],
        "source_sha256": corrected["source_sha256"],
        "method": RECOVERY_METHOD,
    }
    recovered = []
    remaining = []
    existing = {(row.get("section"), row.get("row_number"))
                for row in corrected.get("holdings", [])}
    for row in corrected.get("quarantined", []):
        candidate = (_recover_holding(row, corrected)
                     if attestation["recover_quarantined_rows"] else None)
        key = (row.get("section"), row.get("row_number"))
        if candidate is None or key in existing:
            remaining.append(row)
            continue
        existing.add(key)
        recovered.append(candidate)
    corrected["holdings"] = [*corrected.get("holdings", []), *recovered]
    corrected["quarantined"] = remaining
    corrected["source_bound_recovered_holding_count"] = len(recovered)
    corrected["source_bound_correction_method"] = RECOVERY_METHOD
    return corrected


def scanned_signature_evidence_valid(extraction: dict) -> bool:
    attestation = _attestation(extraction)
    evidence = extraction.get("signature_evidence")
    if attestation is None or not isinstance(evidence, dict):
        return False
    try:
        date.fromisoformat(attestation["filing_date"])
    except ValueError:
        return False
    return evidence == {
        "type": "handwritten_scan",
        "page_number": 1,
        "signed_on": attestation["filing_date"],
        "filer_name": attestation["filer_name"],
        "source_sha256": extraction["source_sha256"],
        "method": RECOVERY_METHOD,
    }


def recovered_ocr_holding_valid(row: dict, extraction: dict) -> bool:
    recovery = row.get("ocr_recovery")
    if _attestation(extraction) is None or not isinstance(recovery, dict):
        return False
    original = recovery.get("original_raw_columns")
    reasons = recovery.get("original_reasons")
    return (
        recovery.get("method") == RECOVERY_METHOD and
        recovery.get("source_sha256") == extraction.get("source_sha256") and
        isinstance(original, dict) and isinstance(reasons, list) and reasons and
        set(reasons) <= _RECOVERABLE_REASONS and
        _clean_description(original.get("description")) == row.get("asset_name") and
        _clean_value(original.get("value")) == row.get("raw_columns", {}).get("value") and
        isinstance(row.get("ocr_mean_confidence"), (int, float)) and
        row["ocr_mean_confidence"] >= 85.0
    )
