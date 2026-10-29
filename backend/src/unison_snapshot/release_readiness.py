"""Validate candidate bindings and explicit dispositions for the v1 publication scope."""
from __future__ import annotations

import json
from pathlib import Path

from .codec import digest, encode


class ReleaseReadinessError(ValueError):
    """The review candidate has an inconsistent source or disposition binding."""


def _read(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseReadinessError(f"Cannot read {label}: {exc}") from None
    if not isinstance(value, dict):
        raise ReleaseReadinessError(f"{label} must be an object")
    return value


def _count(value: dict, key: str, label: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise ReleaseReadinessError(f"{label}.{key} must be a non-negative integer")
    return result


def _zero(value: dict, keys: tuple[str, ...], label: str) -> None:
    nonzero = {key: _count(value, key, label) for key in keys if _count(value, key, label)}
    if nonzero:
        raise ReleaseReadinessError(f"{label} still has inconsistent source state: {nonzero}")


def validate_publication(review_root: Path, candidate_path: Path) -> dict:
    """Validate source accounting without requiring quarantines and declared queues to vanish."""
    review_root = review_root.resolve()
    candidate_path = candidate_path.resolve()
    candidate = _read(candidate_path, "unified disclosure candidate")
    if not isinstance(candidate.get("meta"), dict) or candidate["meta"].get("is_demo") is not False:
        raise ReleaseReadinessError("Unified disclosure candidate must be is_demo=false")
    for key in ("people", "transactions", "reported_holdings", "source_health"):
        if not isinstance(candidate.get(key), list) or not candidate[key]:
            raise ReleaseReadinessError(f"Unified disclosure candidate requires non-empty {key}")
    if candidate.get("security_market_data") != []:
        raise ReleaseReadinessError("Disclosure candidate must not contain preloaded market data")

    house = _read(review_root / "status" / "house_clerk.json", "House PTR status")
    if house.get("schema_version") != "house-review-run/v1":
        raise ReleaseReadinessError("House PTR status schema is unsupported")
    _zero(house, ("status_conflict_count",), "House PTR status")
    if _count(house, "evidence_count", "House PTR status") != (
            _count(house, "extracted_count", "House PTR status")
            + _count(house, "failure_count", "House PTR status")
            + _count(house, "pending_parse_count", "House PTR status")):
        raise ReleaseReadinessError("House PTR evidence is not fully accounted for")
    if _count(house, "qualification_count", "House PTR status") != \
            _count(house, "extracted_count", "House PTR status"):
        raise ReleaseReadinessError("House PTR extractions are not fully qualified")

    holdings = _read(review_root / "status" / "house_holdings.json", "House holdings status")
    if holdings.get("schema_version") != "house-holding-run/v1":
        raise ReleaseReadinessError("House holdings status schema is unsupported")
    if _count(holdings, "evidence_count", "House holdings status") + \
            _count(holdings, "remaining_archive_count", "House holdings status") < \
            _count(holdings, "current_member_annual_count", "House holdings status"):
        raise ReleaseReadinessError("House annual-report archive queue is not accounted for")
    if _count(holdings, "evidence_count", "House holdings status") != (
            _count(holdings, "extraction_count", "House holdings status")
            + _count(holdings, "failure_count", "House holdings status")
            + _count(holdings, "pending_parse_count", "House holdings status")):
        raise ReleaseReadinessError("House annual reports are not fully accounted for")
    if _count(holdings, "qualification_count", "House holdings status") != \
            _count(holdings, "extraction_count", "House holdings status"):
        raise ReleaseReadinessError("House annual-report extractions are not fully qualified")

    oge = _read(review_root / "status" / "oge.json", "OGE status")
    if oge.get("schema_version") != "oge-review-run/v1":
        raise ReleaseReadinessError("OGE status schema is unsupported")
    direct = _count(oge, "direct_pdf_count", "OGE status")
    archived = _count(oge, "archived_report_count", "OGE status")
    if direct != archived + _count(oge, "pending_direct_count", "OGE status"):
        raise ReleaseReadinessError("OGE direct-PDF archive queue is not accounted for")
    if archived != (_count(oge, "extraction_count", "OGE status") +
                    _count(oge, "extraction_failure_count", "OGE status") +
                    (_count(oge, 'pending_extraction_count', 'OGE status') if 'pending_extraction_count' in oge else 0)):
        raise ReleaseReadinessError("OGE direct-PDF extraction dispositions are not fully accounted for")

    senate = _read(review_root / "status" / "senate_efd.json", "Senate status")
    if senate.get("schema_version") != "senate-review-run/v1":
        raise ReleaseReadinessError("Senate status schema is unsupported")
    _count(senate, "report_entrypoint_failure_count", "Senate status")
    catalog = _count(senate, "catalog_record_count", "Senate status")
    entrypoints = _count(senate, "report_entrypoint_count", "Senate status")
    if catalog != entrypoints + _count(senate, "report_entrypoint_pending_count", "Senate status"):
        raise ReleaseReadinessError("Senate PTR entrypoint archive queue is not accounted for")
    report_failures = _count(senate, "report_extraction_failure_count", "Senate status")
    paper_pending = _count(senate, "paper_report_pending_count", "Senate status") if 'paper_report_pending_count' in senate else 0
    if entrypoints != _count(senate, "report_evidence_count", "Senate status") + report_failures + paper_pending:
        raise ReleaseReadinessError("Senate electronic and paper PTRs are not fully accounted for")
    for key in ("paper_report_pending_count", "paper_page_pending_count"):
        if key in senate:
            _count(senate, key, "Senate status")

    cutoff = _read(review_root / "status" / "disclosure_cutoff.json",
                   "unified cutoff audit")
    if cutoff.get("schema_version") != "disclosure-cutoff-audit/v1" \
            or set((cutoff.get("sources") or {})) != {"house_clerk", "oge", "senate_efd"}:
        raise ReleaseReadinessError("Unified cutoff audit does not cover all publication sources")
    if cutoff.get("candidate_sha256") != digest(encode(candidate)):
        raise ReleaseReadinessError("Unified candidate does not match its cutoff audit")
    retained_transactions = retained_holdings = 0
    for source_id, source_audit in cutoff["sources"].items():
        source_path = review_root / "candidates" / "sources" / f"{source_id}-current.json"
        source_candidate = _read(source_path, f"{source_id} source candidate")
        if source_id == "oge":
            source_transactions = source_candidate.get("transactions")
            source_holdings = source_candidate.get("reported_holdings")
            if not isinstance(source_transactions, list) or not isinstance(source_holdings, list):
                raise ReleaseReadinessError("OGE source candidate row arrays are invalid")
            if len(source_transactions) != _count(
                    oge, "qualified_transaction_count", "OGE status"):
                raise ReleaseReadinessError(
                    "OGE qualified transaction count does not match its source candidate")
            if len(source_holdings) != _count(
                    oge, "qualified_holding_count", "OGE status"):
                raise ReleaseReadinessError(
                    "OGE qualified holding count does not match its source candidate")
        if source_audit.get("candidate_sha256") != digest(encode(source_candidate)):
            raise ReleaseReadinessError(f"{source_id} source candidate does not match cutoff audit")
        retained = source_audit.get("retained_counts")
        if not isinstance(retained, dict):
            raise ReleaseReadinessError(f"{source_id} cutoff audit has no retained counts")
        retained_transactions += _count(retained, "transactions", f"{source_id} retained counts")
        retained_holdings += _count(retained, "reported_holdings", f"{source_id} retained counts")
    if retained_transactions != len(candidate["transactions"]) \
            or retained_holdings != len(candidate["reported_holdings"]):
        raise ReleaseReadinessError("Unified candidate row counts do not match cutoff audit")

    return {
        "readiness_policy": "v1-accounted-dispositions",
        "pending": {
            "house_ptr": _count(house, "pending_parse_count", "House PTR status"),
            "house_annual_archive": _count(holdings, "remaining_archive_count", "House holdings status"),
            "house_annual_parse": _count(holdings, "pending_parse_count", "House holdings status"),
            "oge_direct_archive": _count(oge, "pending_direct_count", "OGE status"),
            "senate_entrypoints": _count(senate, "report_entrypoint_pending_count", "Senate status"),
            "senate_paper": paper_pending,
        },
        "oge_extraction_failure_count": _count(oge, "extraction_failure_count", "OGE status"),
        "people_count": len(candidate["people"]),
        "transaction_count": len(candidate["transactions"]),
        "reported_holding_count": len(candidate["reported_holdings"]),
        "house_ptr_failure_count": _count(house, "failure_count", "House PTR status"),
        "house_holding_failure_count": _count(holdings, "failure_count", "House holdings status"),
        "senate_report_failure_count": report_failures,
        "oge_form_201_excluded_count": _count(oge, "request_required_count", "OGE status"),
        "data_cutoff_at": candidate["meta"].get("data_cutoff_at"),
    }
