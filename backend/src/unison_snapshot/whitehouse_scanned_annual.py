"""Source-bound corrections for exceptional scanned White House annual reports.

The public PDFs are the evidence.  This module does not create facts from a
name or URL: every correction is bound to an immutable PDF SHA-256 and keeps
the original OCR cells and failure reasons in the review artifact.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from .oge_278e_public import (TRUMP_2025_PARSER_VERSION,
                              TRUMP_2025_LEGACY_PARSER_VERSIONS,
                              TRUMP_2025_PREVIOUS_PARSER_VERSION,
                              TRUMP_2025_SOURCE_SHA256,
                              TRUMP_2025_SOURCE_URL)
from .oge_annual import _range


RECOVERY_METHOD = "whitehouse-scanned-annual-source-bound/v2"

_TRUMP_CHECKPOINT_PATH = (
    "whitehouse/ocr-checkpoints/0c14d3849ca60768024e470b/"
    "1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3/"
    "whitehouse-278e-hybrid-geometry-v5/pages-0851-0875.json")
_TRUMP_CHECKPOINT_BLOB = "bb1f45bd8f8ff54c6179cf4d5c44a44dd3cf0373"
_TRUMP_ROW_332_DESCRIPTION = (
    "Trump Marks Philippines LLC Location: Century City Makati, Philippines Licensee: "
    "Century Luxury Properties, Inc. Additional Underlying Assets: Registered Trademark(s) "
    "(values not readily ascertainable).* (See Exhibit A). Underlying Asset: U.S. bank account "
    "Location: Jupiter, FL (value represents bank account only)")

_ATTESTATIONS = {
    TRUMP_2025_SOURCE_SHA256: {
        "filer_name": "Donald Trump",
        "filing_date": "2026-06-29",
        "position_title_raw": "President of the United States of America",
        "source_url": TRUMP_2025_SOURCE_URL,
        "recover_quarantined_rows": True,
        # Fixed-PDF golden-page boundaries.  The v5 OCR extraction carried
        # the active Part 6 state across the Part 7 transaction pages because
        # the scanned heading was only read as ``Part i``.  Keep this source-
        # bound guard in the qualification layer even after parser replay so
        # an older extraction can never publish those transactions as assets.
        "holding_page_ranges": {"part6": (7, 158), "part2": (848, 870)},
        # v6 is source-bound for the whole PDF, while its new physical locator
        # and per-field confidence fields are emitted only for Part 6.  Bind the
        # one already-published Part 2 holding to the same immutable checkpoint
        # so a parser-version change cannot make that existing fact disappear.
        "qualified_holding_evidence": {
            ("part2", 864, "332"): {
                "asset_name": _TRUMP_ROW_332_DESCRIPTION,
                "owner": "Self",
                "raw_columns": {
                    "description": _TRUMP_ROW_332_DESCRIPTION,
                    "eif": "No", "value": "$1,001 to $15,000",
                },
                "value_low": 1001, "value_high": 15000,
                "source_row_locator": "p864-y2772",
                "geometry_top": 277.2,
                "critical_field_confidence": {
                    "row_number": {"mean": 96.53, "minimum": 96.53},
                    "description": {"mean": 94.89, "minimum": 85.43},
                    "value": {"mean": 95.90, "minimum": 95.41},
                },
            },
        },
        # The only Part 2 rows independently closed against the immutable OCR
        # checkpoint. Exact raw cells make this an allowlist, not a wider
        # confidence-threshold exception.
        "approved_recoveries": {
            ("part2", 856, "135"): {
                "raw_columns": {
                    "description": ("DTW Venture LLC Underlying Assets: residential real estate "
                                    "Location: Palm Beach, FL"),
                    "eif": "N/A", "value": "|$5,000,001 to $25,000,000",
                },
                "reasons": ["holding_value_unreadable_or_open"],
                "source_row_locator": "p856-y5810",
                "geometry_row_index": 17, "geometry_top": 581.04,
                "critical_field_confidence": {
                    "row_number": {"mean": 86.00, "minimum": 86.00},
                    "description": {"mean": 95.72, "minimum": 91.19},
                    "value": {"mean": 88.37, "minimum": 82.31},
                },
            },
            ("part2", 866, "374.2"): {
                "raw_columns": {
                    "description": "Receivable from Amazon MGM Studios",
                    "eif": "N/A", "value": "| $250,001 to $500,000",
                },
                "reasons": ["holding_value_unreadable_or_open"],
                "source_row_locator": "p866-y2077",
                "geometry_row_index": 5, "geometry_top": 207.72,
                "critical_field_confidence": {
                    "row_number": {"mean": 94.72, "minimum": 94.72},
                    "description": {"mean": 96.65, "minimum": 96.33},
                    "value": {"mean": 93.91, "minimum": 88.42},
                },
            },
        },
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


def source_bound_holding_section_page_valid(row: dict, extraction: dict) -> bool:
    """Validate known scanned-form section boundaries without guessing others."""

    attestation = _attestation(extraction)
    if attestation is None or "holding_page_ranges" not in attestation:
        return True
    ranges = attestation["holding_page_ranges"]
    section = row.get("section")
    page_number = row.get("page_number")
    allowed = ranges.get(section) if isinstance(section, str) else None
    if allowed is None:
        # The attestation only constrains sections whose fixed-PDF boundaries
        # have been independently established.  It must not invent a boundary
        # for an as-yet unreconciled section such as Part 5.
        return True
    return (isinstance(allowed, tuple) and len(allowed) == 2 and
            type(page_number) is int and allowed[0] <= page_number <= allowed[1])


def source_bound_parser_version_valid(extraction: dict) -> bool:
    """Trump-only parser versions must never qualify another source."""

    if extraction.get("parser_version") not in {
            *TRUMP_2025_LEGACY_PARSER_VERSIONS, TRUMP_2025_PARSER_VERSION}:
        return True
    return (extraction.get("source_sha256") == TRUMP_2025_SOURCE_SHA256 and
            _attestation(extraction) is not None)


def _qualified_holding_approval(row: dict, extraction: dict) -> dict | None:
    attestation = _attestation(extraction)
    approved = attestation.get("qualified_holding_evidence") if attestation else None
    approval = approved.get((row.get("section"), row.get("page_number"),
                             row.get("row_number"))) if isinstance(approved, dict) else None
    if (not isinstance(approval, dict) or row.get("asset_name") != approval.get("asset_name") or
            row.get("owner") != approval.get("owner") or
            row.get("raw_columns") != approval.get("raw_columns") or
            row.get("value_low") != approval.get("value_low") or
            row.get("value_high") != approval.get("value_high")):
        return None
    return approval


def source_bound_existing_holding_valid(row: dict, extraction: dict) -> bool:
    """Validate fixed evidence added to an already-qualified legacy holding."""

    approval = _qualified_holding_approval(row, extraction)
    evidence = row.get("source_bound_holding_evidence")
    return bool(
        approval is not None and isinstance(evidence, dict) and
        row.get("source_row_locator") == approval.get("source_row_locator") and
        row.get("critical_field_confidence") == approval.get("critical_field_confidence") and
        evidence.get("source_sha256") == extraction.get("source_sha256") and
        evidence.get("checkpoint_path") == _TRUMP_CHECKPOINT_PATH and
        evidence.get("checkpoint_git_blob") == _TRUMP_CHECKPOINT_BLOB and
        evidence.get("source_row_locator") == approval.get("source_row_locator") and
        evidence.get("geometry_top") == approval.get("geometry_top") and
        evidence.get("critical_field_confidence") == approval.get("critical_field_confidence") and
        _range(row.get("raw_columns", {}).get("value", "")) ==
        (row.get("value_low"), row.get("value_high"))
    )


def _recover_holding(row: dict, extraction: dict) -> dict | None:
    attestation = _attestation(extraction)
    if attestation is None:
        return None
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
    approved = attestation.get("approved_recoveries")
    approval = None
    if isinstance(approved, dict):
        approval = approved.get((section, row.get("page_number"), row_number))
        if (not isinstance(approval, dict) or raw != approval.get("raw_columns") or
                reasons != approval.get("reasons")):
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
    if approval is not None:
        evidence = {
            "checkpoint_path": _TRUMP_CHECKPOINT_PATH,
            "checkpoint_git_blob": _TRUMP_CHECKPOINT_BLOB,
            "geometry_row_index": approval["geometry_row_index"],
            "geometry_top": approval["geometry_top"],
        }
        recovered["source_row_locator"] = approval["source_row_locator"]
        recovered["critical_field_confidence"] = approval["critical_field_confidence"]
        recovered["ocr_recovery"].update({
            **evidence,
            "source_row_locator": approval["source_row_locator"],
            "critical_field_confidence": approval["critical_field_confidence"],
        })
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
    for row in corrected.get("holdings", []):
        approval = _qualified_holding_approval(row, corrected)
        if approval is None or row.get("source_row_locator") not in (
                None, approval["source_row_locator"]):
            continue
        row["source_row_locator"] = approval["source_row_locator"]
        row["critical_field_confidence"] = approval["critical_field_confidence"]
        row["source_bound_holding_evidence"] = {
            "method": RECOVERY_METHOD,
            "source_sha256": corrected["source_sha256"],
            "checkpoint_path": _TRUMP_CHECKPOINT_PATH,
            "checkpoint_git_blob": _TRUMP_CHECKPOINT_BLOB,
            "source_row_locator": approval["source_row_locator"],
            "geometry_top": approval["geometry_top"],
            "critical_field_confidence": approval["critical_field_confidence"],
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
    low, high = row.get("value_low"), row.get("value_high")
    attestation = _attestation(extraction)
    approved = attestation.get("approved_recoveries") if attestation else None
    if isinstance(approved, dict):
        approval = approved.get((row.get("section"), row.get("page_number"),
                                 row.get("row_number")))
        if (not isinstance(approval, dict) or original != approval.get("raw_columns") or
                reasons != approval.get("reasons") or
                row.get("source_row_locator") != approval.get("source_row_locator") or
                row.get("critical_field_confidence") !=
                approval.get("critical_field_confidence") or
                recovery.get("source_row_locator") != approval.get("source_row_locator") or
                recovery.get("checkpoint_path") != _TRUMP_CHECKPOINT_PATH or
                recovery.get("checkpoint_git_blob") != _TRUMP_CHECKPOINT_BLOB or
                recovery.get("geometry_row_index") != approval.get("geometry_row_index") or
                recovery.get("geometry_top") != approval.get("geometry_top")):
            return False
    return (
        recovery.get("method") == RECOVERY_METHOD and
        recovery.get("source_sha256") == extraction.get("source_sha256") and
        isinstance(original, dict) and isinstance(reasons, list) and reasons and
        set(reasons) <= _RECOVERABLE_REASONS and
        _clean_description(original.get("description")) == row.get("asset_name") and
        _clean_value(original.get("value")) == row.get("raw_columns", {}).get("value") and
        _range(row.get("raw_columns", {}).get("value", "")) == (low, high) and
        source_bound_holding_section_page_valid(row, extraction) and
        isinstance(row.get("ocr_mean_confidence"), (int, float)) and
        row["ocr_mean_confidence"] >= 85.0
    )
