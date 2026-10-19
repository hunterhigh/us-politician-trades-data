"""Read Senate PTR processing observations without changing qualifications.

Report UUIDs and archived versions are independent of publication transaction
IDs. In particular, paper slots and old failure attempts are not transactions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import re


_SHA = re.compile(r"[0-9a-f]{64}")


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Senate status artifact is not an object: {path}")
    return value


def _rows(value: dict, key: str) -> list[dict]:
    rows = value.get(key, [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Senate status {key} must be an object array")
    return rows


def _time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
    except ValueError:
        return None
    return value


def _filed_date(value: object) -> str | None:
    match = re.fullmatch(
        r"Filed (\d{2}/\d{2}/\d{4})(?: @ (?:0?[1-9]|1[0-2])(?::[0-5][0-9])? (?:AM|PM))?",
        value) if isinstance(value, str) else None
    if match:
        try:
            return datetime.strptime(match.group(1), "%m/%d/%Y").date().isoformat()
        except ValueError:
            pass
    return None


def build_senate_status(evidence_root: Path, review_root: Path,
                        transactions: list[dict], holdings: list[dict]) -> list[dict]:
    """Return one deterministic PTR report scope from the frozen build inputs."""
    evidence = evidence_root / "senate_efd"
    review = review_root / "senate_efd"
    status = _read(review_root / "status" / "senate_efd.json")
    audit = _read(review / "qualifications" / "current.json")
    limitations = [
        "Report inventory covers Senate PTR only; annual holdings are outside this scope.",
        "Unmatched Senate publication IDs may belong to other report types; they are not a pending PTR count.",
        "Paper physical slots, unknown slots and unobserved pages are coverage observations, not transaction counts.",
    ]
    catalog_sha = status.get("catalog_sha256") or audit.get("catalog_sha256")
    discovery = {}
    metadata = {}
    if isinstance(catalog_sha, str) and _SHA.fullmatch(catalog_sha):
        discovery = _read(review / "discoveries" / f"{catalog_sha}.json")
        metadata = _read(evidence / "catalog" / f"{catalog_sha}.json")
    if not discovery:
        limitations.append("Current PTR catalog is unavailable; archived reports are not assumed to be current.")
    catalog_rows = _rows(discovery, "reports")
    current = {}
    for row in catalog_rows:
        document_id = row.get("document_id")
        if not isinstance(document_id, str) or not document_id or document_id in current:
            raise ValueError("Senate current catalog has missing or duplicate document IDs")
        current[document_id] = row

    archived = defaultdict(list)
    for path in sorted((evidence / "reports").glob("*/*.metadata.json")):
        item = _read(path)
        document_id, sha = item.get("document_id"), item.get("sha256")
        if (document_id != path.parent.name or not isinstance(sha, str) or
                not _SHA.fullmatch(sha) or path.name != f"{sha}.metadata.json"):
            raise ValueError("Senate archive metadata does not match its path")
        # Metadata alone cannot prove that archived original bytes still exist.
        if not any((path.parent / f"{sha}{suffix}").is_file() for suffix in (".html", ".pdf")):
            raise ValueError("Senate archive metadata has no original file")
        archived[document_id].append(item)

    parser = status.get("report_parser_version") or audit.get("parser_version")
    extractions = defaultdict(list)
    failures = defaultdict(list)
    for folder, target in (("extractions", extractions),
                           ("paper_report_failures", failures), ("report_failures", failures)):
        for path in sorted((review / folder).glob("*/*/*.json")):
            if parser and path.stem != parser:
                continue
            item = _read(path)
            document_id, sha = item.get("document_id"), item.get("source_sha256")
            if (document_id != path.parent.parent.name or sha != path.parent.name or
                    not isinstance(sha, str) or not _SHA.fullmatch(sha)):
                raise ValueError("Senate extraction/failure artifact does not match its path")
            target[document_id].append(item)

    supplement_pointer = review / "amendment_supplements" / "current.json"
    if supplement_pointer.is_file() and isinstance(parser, str):
        # Reuse the existing content-addressed selection check. Historical
        # extraction files not selected by this pointer do not become current.
        from .senate_candidate import _load_active_amendment_supplement

        supplement = _load_active_amendment_supplement(review_root, parser)
        for item in supplement["extractions"]:
            extractions[item["document_id"]].append(item)

    # An audit is applicable only to the selected catalog/parser, never a newer
    # arbitrary qualification file found while walking historical artifacts.
    audit_active = (bool(audit) and isinstance(catalog_sha, str) and
                    bool(_SHA.fullmatch(catalog_sha))) and audit.get("catalog_sha256") == catalog_sha and (
        not parser or audit.get("parser_version") == parser)
    if audit and not audit_active:
        limitations.append("Qualification audit does not match the selected catalog/parser; its decisions are not applied.")
    qualified, row_quarantine, report_quarantine = defaultdict(list), defaultdict(list), defaultdict(list)
    superseded = set()
    superseded_versions = defaultdict(set)
    if audit_active:
        for key, target in (("qualified_rows", qualified), ("quarantined_rows", row_quarantine),
                            ("quarantined_reports", report_quarantine)):
            for row in _rows(audit, key):
                document_id = row.get("document_id")
                if not isinstance(document_id, str) or not document_id:
                    raise ValueError("Senate qualification record has no document ID")
                target[document_id].append(row)
        for chain in _rows(audit, "resolved_amendment_chains"):
            ids = chain.get("superseded_document_ids", [])
            if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
                raise ValueError("Senate superseded IDs are invalid")
            superseded.update(ids)
            for link in _rows(chain, "links"):
                if link.get("previous_document_id") in ids and isinstance(link.get("previous_source_sha256"), str):
                    superseded_versions[link["previous_document_id"]].add(link["previous_source_sha256"])

    ids = set(current) | set(archived) | set(extractions) | set(failures)
    ids |= set(qualified) | set(row_quarantine) | set(report_quarantine) | superseded
    records = {}
    for document_id in sorted(ids):
        versions = sorted({item["sha256"] for item in archived[document_id]})
        qualified_rows = qualified[document_id]
        isolated_rows = row_quarantine[document_id]
        isolated_reports = report_quarantine[document_id]
        audited = qualified_rows + isolated_rows + isolated_reports
        selected_versions = {row.get("source_sha256") for row in audited}
        selected_versions.update(superseded_versions[document_id])
        if not selected_versions and len(versions) == 1:
            selected_versions = set(versions)
        bound_extractions = [item for item in extractions[document_id]
                             if item["source_sha256"] in versions and
                             item["source_sha256"] in selected_versions]
        reasons = set()
        if len(selected_versions) != 1 and len(versions) > 1:
            reasons.add("active_archive_version_unknown")
        if extractions[document_id] and not bound_extractions:
            reasons.add("extraction_archive_binding_unknown")
        if len(bound_extractions) > 1:
            # A per-report view must not merge transaction counts across versions.
            reasons.add("multiple_extraction_versions")
        extraction = bound_extractions[0] if len(bound_extractions) == 1 else None
        audit_bound = (audit_active and len(selected_versions) <= 1 and
                       all(row.get("source_sha256") in versions for row in audited))
        if superseded_versions[document_id] and not superseded_versions[document_id].issubset(versions):
            audit_bound = False
        if audited and not audit_bound:
            reasons.add("qualification_archive_binding_unknown")
        for row in audited if audit_bound else []:
            values = row.get("reasons", [])
            if isinstance(values, list):
                reasons.update(reason for reason in values if isinstance(reason, str))
        qualification = "unknown" if document_id not in current else "not_evaluated"
        qualified_count = quarantined_count = None
        if audit_bound and document_id in superseded:
            qualification, qualified_count, quarantined_count = "superseded", 0, 0
            reasons.add("superseded_by_resolved_amendment")
        elif audit_bound and audited:
            qualified_count = len(qualified_rows)
            if isolated_reports:
                quarantined_count = (len(extraction["transactions"])
                                     if extraction and isinstance(extraction.get("transactions"), list) else None)
            else:
                quarantined_count = len(isolated_rows)
            qualification = ("partial" if qualified_rows and (isolated_rows or isolated_reports)
                             else "qualified" if qualified_rows else "quarantined")
        elif (audit_bound and audit.get("empty_eligible_report_count", 0) > 0 and
              document_id in current and extraction is not None and
              extraction.get("transactions") == []):
            qualification, qualified_count, quarantined_count = "zero_transactions", 0, 0
        if document_id not in current and document_id not in superseded:
            # Archived historical reports absent from this audit remain unknown.
            reasons.add("historical_catalog_relationship_unknown")
        valid_failures = [item for item in failures[document_id]
                          if item["source_sha256"] in versions and
                          item["source_sha256"] in selected_versions]
        extraction_state = ("extracted" if extraction is not None else
                            "unknown" if bound_extractions or "active_archive_version_unknown" in reasons else
                            "failed" if valid_failures else "not_attempted" if versions else "unknown")
        if extraction_state == "failed":
            reasons.update(item["reason"] for item in valid_failures
                           if isinstance(item.get("reason"), str))
        descriptor = current.get(document_id) or (archived[document_id][0] if versions else {})
        filed_dates = {_filed_date(item.get("filed_at_raw")) for item in
                       ([extraction] if extraction else []) + (audited if audit_bound else [])}
        filed_dates.discard(None)
        filed_date = next(iter(filed_dates)) if len(filed_dates) == 1 else None
        if len(filed_dates) > 1:
            reasons.add("report_filed_dates_conflict")
        records[document_id] = {
            "record_key": f"senate_ptr:{document_id}", "report_id": document_id,
            "document_type": descriptor.get("access_method") or "periodic_transaction_report",
            "source_url": descriptor.get("document_url") or (extraction or {}).get("source_url"),
            "filed_date": filed_date,
            "filed_date_basis": "report_filed_at_raw" if filed_date else None,
            "catalog_listed_date": descriptor.get("portal_listed_date"),
            "catalog_state": "current" if document_id in current else "historical" if versions else "unknown",
            "acquisition_state": "archived" if versions else "not_archived" if document_id in current else "unknown",
            "extraction_state": extraction_state, "qualification_state": qualification,
            "qualified_transaction_count": qualified_count, "quarantined_row_count": quarantined_count,
            "reason_codes": sorted(reasons), "source_versions": versions,
            "publication_transaction_ids": [], "publication_binding": "unknown",
        }

    # Apply only source-bound coverage produced by the candidate's verified
    # pending projection. Physical slots never become quarantined row counts.
    pending = audit.get("paper_pending_projection") if audit_active else None
    coverage_rows = pending.get("source_report_coverage", []) if isinstance(pending, dict) else []
    seen_coverage = set()
    for coverage in coverage_rows:
        document_id = coverage.get("document_id")
        sha = coverage.get("source_sha256")
        if document_id in seen_coverage:
            raise ValueError("Senate paper coverage repeats a report")
        seen_coverage.add(document_id)
        record = records.get(document_id)
        if record is None or sha not in record["source_versions"]:
            continue
        active_shas = {item["source_sha256"] for item in extractions[document_id]}
        if active_shas and active_shas != {sha}:
            continue
        record["source_row_coverage"] = coverage
        if coverage.get("relation_coverage") != "accounted":
            record["reason_codes"] = sorted({*record["reason_codes"], "source_row_coverage_incomplete"})
            if record["qualification_state"] == "qualified":
                record["qualification_state"] = "partial"
            elif record["qualification_state"] == "zero_transactions":
                record["qualification_state"] = "unknown"
                record["qualified_transaction_count"] = None

    qualified_id_to_report = {}
    for document_id, rows in qualified.items():
        for row in rows:
            transaction_id = row.get("transaction_id")
            if isinstance(transaction_id, str):
                if transaction_id in qualified_id_to_report:
                    raise ValueError("Senate qualification audit duplicates a transaction ID")
                qualified_id_to_report[transaction_id] = document_id
    unmatched = []
    for row in transactions:
        if row.get("source_id") != "senate_efd":
            continue
        transaction_id, filing_id = row.get("id"), row.get("filing_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ValueError("Senate publication transaction has no ID")
        audited_id = qualified_id_to_report.get(transaction_id)
        document_id = filing_id if filing_id in records else audited_id
        # Inconsistent ID and filing references must never be silently reconciled.
        if (document_id is None or (audited_id and filing_id and audited_id != filing_id) or
                (filing_id and filing_id not in records)):
            unmatched.append(transaction_id)
            continue
        record = records[document_id]
        record["publication_transaction_ids"].append(transaction_id)
        record["publication_binding"] = "verified"
    for record in records.values():
        record["publication_transaction_ids"].sort()

    result = {
        "scope_id": "senate_ptr", "source_id": "senate_efd",
        "scope": {"document_type": "periodic_transaction_report",
                  "coverage": "Selected current PTR catalog plus preserved archived report UUIDs; not all historical Senate reports.",
                  "catalog_sha256": catalog_sha if isinstance(catalog_sha, str) else None},
        "observed_at": _time(audit.get("data_cutoff_at")) if audit_active else None,
        "limitations": sorted(limitations), "reports": list(records.values()),
        "unmatched_publication_transaction_ids": sorted(unmatched),
    }
    if result["observed_at"] is None:
        result["observed_at"] = _time(status.get("generated_at")) or _time(metadata.get("retrieved_at"))
    pending = audit.get("paper_pending_projection") if audit_active else None
    if isinstance(pending, dict):
        result["paper_coverage_observations"] = {
            key: pending[key] for key in ("physical_slot_count", "unknown_slot_count",
                                          "unobserved_page_count")
            if type(pending.get(key)) is int and pending[key] >= 0
        }
    return [result]
