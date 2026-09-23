"""Materialize White House annual asset rows with truthful filer attribution.

This is a review artifact. A source-complete asset set is not yet a canonical
holding snapshot until person identity, report versions and publication gates
are resolved.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from .oge_278e_audit import audit_public_278e
from .oge_278e_public import PARSER_VERSION, SCHEMA as EXTRACTION_SCHEMA
from .whitehouse_278t import _first_last


SCHEMA = "whitehouse-annual-filer-reported/v1"
_COVERAGE = "whitehouse-public-coverage/v1"
_ID = re.compile(r"wh-url:([0-9a-f]{24})\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_EXTRACTED = {"extracted_review_only", "extracted_with_issues"}


def build_annual_review(coverage: dict, review_root: Path, *,
                        coverage_sha256: str) -> dict:
    """Conserve every extracted Annual 278e holding without guessing owner."""
    reports = coverage.get("reports") if isinstance(coverage, dict) else None
    if (not isinstance(coverage, dict) or coverage.get("schema_version") != _COVERAGE or
            coverage.get("source_id") != "whitehouse_public_disclosures" or
            not isinstance(reports, list) or coverage.get("report_link_count") != len(reports) or
            not _SHA.fullmatch(coverage_sha256)):
        raise ValueError("White House annual coverage is invalid")
    result_reports = []
    holdings = []
    owner_counts: Counter[str] = Counter()
    seen_urls = set()
    for source in sorted(reports, key=lambda row: row.get("document_id", "")):
        if not isinstance(source, dict):
            raise ValueError("White House coverage report is invalid")
        if source.get("review_state") not in _EXTRACTED or not str(
                source.get("document_type_from_label", "")).startswith("278e_"):
            continue
        document_id = source.get("document_id")
        match = _ID.fullmatch(document_id) if isinstance(document_id, str) else None
        versions = source.get("archive_sha256_versions")
        url = source.get("document_url")
        if (match is None or not isinstance(versions, list) or len(versions) != 1 or
                not isinstance(versions[0], str) or not _SHA.fullmatch(versions[0]) or
                not isinstance(url, str) or document_id != "wh-url:" +
                hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] or url in seen_urls):
            raise ValueError("White House annual archive binding is invalid")
        seen_urls.add(url)
        relative = (Path("whitehouse/extractions") / match[1] / versions[0] /
                    f"{PARSER_VERSION.replace('/', '-')}.json")
        raw = (review_root / relative).read_bytes()
        extraction = json.loads(raw)
        filer = extraction.get("filer_name")
        page_filer = source.get("filer_name_from_label")
        if (extraction.get("schema_version") != EXTRACTION_SCHEMA or
                extraction.get("parser_version") != PARSER_VERSION or
                extraction.get("source_url") != url or
                extraction.get("source_sha256") != versions[0] or
                not isinstance(filer, str) or not isinstance(page_filer, str) or
                _first_last(filer) is None or _first_last(filer) != _first_last(page_filer)):
            raise ValueError(f"White House annual extraction is not bound to its PDF: {relative}")
        if extraction.get("report_type") != "Annual":
            continue
        audit = audit_public_278e(extraction)
        report_eligible = audit["source_holdings_eligible"]
        source_holdings = extraction["holdings"]
        if len(source_holdings) != len(audit["holding_row_audit"]):
            raise ValueError("White House annual holding audit lost rows")
        report = {
            "document_id": document_id,
            "filer_reported_name": extraction["filer_name"],
            "filer_attribution": "signed_pdf_and_official_page",
            "position_title_raw": extraction.get("position_title_raw"),
            "agency_office_raw": extraction.get("agency_office_raw"),
            "source_url": url, "source_sha256": versions[0],
            "extraction_path": relative.as_posix(),
            "extraction_sha256": hashlib.sha256(raw).hexdigest(),
            "cover_report_year": extraction.get("cover_report_year"),
            "filing_date": extraction.get("filing_date"),
            "report_period_end": extraction.get("report_period_end"),
            "source_holdings_eligible": report_eligible,
            "holding_blocking_reasons": audit["holding_blocking_reasons"],
            "parsed_holding_count": len(source_holdings),
            "quarantined_asset_count": sum(row.get("section") in {"part2", "part5", "part6"}
                                           for row in extraction["quarantined"]),
            "printed_rows_conserved": audit["printed_rows_conserved"],
        }
        result_reports.append(report)
        for index, (row, decision) in enumerate(zip(source_holdings,
                                                      audit["holding_row_audit"], strict=True)):
            owner = row["owner"]
            owner_counts[owner] += 1
            holdings.append({
                "row_id": "wh-annual:" + hashlib.sha256(
                    f"{versions[0]}|{index}|{row.get('section')}|{row.get('row_number')}".encode()
                ).hexdigest()[:24],
                "document_id": document_id,
                "filer_reported_name": extraction["filer_name"],
                "asset_owner": owner,
                "owner_is_filer": True if owner == "Self" else (
                    False if owner in {"Spouse", "Dependent Child"} else None),
                "asset_name": row["asset_name"],
                "value_low": row["value_low"], "value_high": row["value_high"],
                "report_period_end": row["report_period_end"],
                "section": row["section"], "page_number": row["page_number"],
                "row_number": row["row_number"],
                "source_holdings_eligible": report_eligible and
                decision["source_candidate_eligible"],
                "row_blocking_reasons": decision["reasons"],
                "source_url": url, "source_sha256": versions[0],
                "extraction_path": relative.as_posix(),
            })
    if len({row["row_id"] for row in holdings}) != len(holdings):
        raise ValueError("White House annual row identity collision")
    return {
        "schema_version": SCHEMA, "coverage_sha256": coverage_sha256,
        "report_count": len(result_reports), "holding_count": len(holdings),
        "owner_counts": dict(sorted(owner_counts.items())),
        "source_eligible_report_count": sum(r["source_holdings_eligible"] for r in result_reports),
        "source_eligible_holding_count": sum(r["source_holdings_eligible"] for r in holdings),
        "production_status": "review_only_pending_identity_versions_and_snapshot_gate",
        "reports": result_reports, "holdings": holdings,
    }
