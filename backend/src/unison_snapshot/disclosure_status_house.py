"""Read-only report accounting for House PTR archive and review artifacts.

Archive receipts are evidence of acquisition; PDF bytes are not re-read here.
No clock, collection, extraction, or qualification is invoked by this adapter.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .house import document_url, parse_index

SHA = re.compile(r"[0-9a-f]{64}")


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid House status input: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"House status input must be an object: {path}")
    return value


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _version_path(path: Path, base: Path) -> tuple[int, str, str]:
    parts = path.relative_to(base).parts
    if (len(parts) != 3 or not re.fullmatch(r"\d{4}", parts[0]) or
            not re.fullmatch(r"\d{1,20}", parts[1]) or not SHA.fullmatch(path.stem)):
        raise ValueError(f"Invalid House report artifact path: {path}")
    return int(parts[0]), parts[1], path.stem


def _reasons(value: dict) -> set[str]:
    result: set[str] = set()
    for key in ("reasons", "reason_codes", "document_reasons"):
        items = value.get(key, [])
        if isinstance(items, list):
            result.update(item for item in items if isinstance(item, str))
    return result


def build_house_status(evidence_root: Path, review_root: Path,
                       transactions: list[dict], holdings: list[dict]) -> list[dict]:
    """Account for every discovered PTR and bind supplied publication rows.

    Each year's most recently retrieved index defines its current catalog.
    Earlier index-only reports remain historical. Latest archive retrieval wins
    across source versions; a missing time or tied latest time leaves version
    selection unknown, rather than borrowing an older successful qualification.
    """
    evidence_root, review_root = Path(evidence_root), Path(review_root)
    current: set[tuple[int, str]] = set()
    catalogued: set[tuple[int, str]] = set()
    discovered: set[tuple[int, str]] = set()
    versions: dict[tuple[int, str], dict[str, dict]] = defaultdict(dict)
    catalog_dates: dict[tuple[int, str], tuple[datetime, str | None]] = {}
    artifacts: dict[str, dict[tuple[int, str, str], dict]] = {}
    observed: list[datetime] = []
    years: list[int] = []
    limitations = ["House annual holdings are outside this PTR scope.",
                   "Archived means a validated controlled archive receipt; PDF bytes are not re-read."]
    index_base = evidence_root / "house_clerk/index"
    for year_dir in sorted(index_base.glob("*")):
        if not year_dir.is_dir() or not re.fullmatch(r"\d{4}", year_dir.name):
            continue
        year = int(year_dir.name)
        indexes = []
        for path in sorted(year_dir.glob("*.json")):
            meta = _read(path)
            timestamp = _time(meta.get("retrieved_at"))
            if (not SHA.fullmatch(path.stem) or meta.get("sha256") != path.stem or
                    meta.get("filing_year") != year or timestamp is None):
                raise ValueError(f"House index receipt cannot be ranked: {path}")
            content = path.with_suffix(".zip").read_bytes()
            if hashlib.sha256(content).hexdigest() != path.stem:
                raise ValueError(f"House index hash mismatch: {path}")
            rows = [row for row in parse_index(content, year) if row.filing_type == "P"]
            keys = {(year, row.document_id) for row in rows}
            for row in rows:
                key = (year, row.document_id)
                if key not in catalog_dates or timestamp > catalog_dates[key][0]:
                    catalog_dates[key] = (timestamp, row.filed_date)
            indexes.append((timestamp, path.stem, keys))
            discovered.update(keys)
            catalogued.update(keys)
            observed.append(timestamp)
        if indexes:
            # Equal-time, different content is not a trustworthy current view.
            indexes.sort(key=lambda item: (item[0], item[1]))
            latest = [item for item in indexes if item[0] == indexes[-1][0]]
            if len(latest) != 1:
                raise ValueError(f"House latest index retrieval is ambiguous for {year}")
            current.update(latest[0][2])
            years.append(year)

    document_base = evidence_root / "house_clerk/documents"
    for path in sorted(document_base.glob("*/*/*.json")):
        year, docid, sha = _version_path(path, document_base)
        meta = _read(path)
        if meta.get("filing_type") != "P":
            continue
        if (meta.get("filing_year") != year or str(meta.get("document_id")) != docid or
                meta.get("sha256") != sha or meta.get("source_id") != "house_clerk" or
                meta.get("source_url") != document_url(year, "P", docid) or
                meta.get("archive_path") != f"house_clerk/documents/{year}/{docid}/{sha}.pdf"):
            raise ValueError(f"House archive receipt binding mismatch: {path}")
        versions[(year, docid)][sha] = meta
        timestamp = _time(meta.get("retrieved_at"))
        if timestamp:
            observed.append(timestamp)

    for kind in ("extractions", "failures", "qualifications"):
        base = review_root / "house_clerk" / kind
        artifacts[kind] = {}
        for path in sorted(base.glob("*/*/*.json")):
            key = _version_path(path, base)
            value = _read(path)
            # Some failure receipts contain only the path-bound identity.
            if (value.get("source_sha256", key[2]) != key[2] or
                    str(value.get("document_id", key[1])) != key[1]):
                raise ValueError(f"House review artifact binding mismatch: {path}")
            source = value.get("source")
            if isinstance(source, dict) and (
                    str(source.get("document_id", key[1])) != key[1] or
                    source.get("source_url", document_url(key[0], "P", key[1])) !=
                    document_url(key[0], "P", key[1])):
                raise ValueError(f"House review source binding mismatch: {path}")
            artifacts[kind][key] = value
            discovered.add(key[:2])
    keys = discovered | set(versions)
    records = []
    for year, docid in sorted(keys):
        key = (year, docid)
        archived = versions.get(key, {})
        reasons: set[str] = set()
        selected = None
        if len(archived) == 1:
            selected = next(iter(archived))
        elif archived:
            reasons.add("multiple_source_versions")
            ranked = [( _time(meta.get("retrieved_at")), sha) for sha, meta in archived.items()]
            if all(item[0] is not None for item in ranked):
                ranked.sort()
                if len([item for item in ranked if item[0] == ranked[-1][0]]) == 1:
                    selected = ranked[-1][1]
            if selected is None:
                reasons.add("source_version_order_unknown")
        filed_date = catalog_dates.get(key, (None, None))[1]
        filed_date_basis = "official_catalog_filing_date" if filed_date else None
        if filed_date is None and selected:
            candidate_date = archived[selected].get("filed_date")
            if candidate_date is not None:
                try:
                    if (not isinstance(candidate_date, str) or
                            datetime.strptime(candidate_date, "%Y-%m-%d").date().isoformat() != candidate_date):
                        raise ValueError
                except ValueError:
                    raise ValueError(f"Invalid House archive filing date: {year}/{docid}") from None
                filed_date = candidate_date
                filed_date_basis = "archive_receipt_filing_date"
        record = {
            "record_key": f"house_ptr:{year}:{docid}", "report_id": f"house_ptr:{year}:{docid}",
            "document_id": docid,
            "document_type": "PTR", "source_url": document_url(year, "P", docid),
            "catalog_state": "current" if key in current else "historical" if key in catalogued else "unknown",
            "acquisition_state": "archived" if archived else "not_archived" if key in catalogued else "unknown",
            "extraction_state": "unknown", "qualification_state": "not_evaluated",
            "qualified_transaction_count": None, "quarantined_row_count": None,
            "reason_codes": [], "source_versions": sorted(archived),
            "publication_transaction_ids": [], "publication_binding": "verified",
            "selected_source_version": selected,
            "filed_date": filed_date, "filed_date_basis": filed_date_basis,
        }
        if selected:
            version = (year, docid, selected)
            extraction = artifacts["extractions"].get(version)
            failure = artifacts["failures"].get(version)
            qualification = artifacts["qualifications"].get(version)
            if extraction is not None:
                record["extraction_state"] = "extracted"
                if failure is not None:
                    reasons.add("historical_failure_receipt_present")
            elif failure is not None:
                record["extraction_state"] = "failed"
                reasons.add("extraction_failed")
            else:
                record["extraction_state"] = "not_attempted"
            if qualification is not None:
                rows, quarantined = qualification.get("transactions"), qualification.get("quarantined")
                summary = qualification.get("qualification")
                if (not isinstance(rows, list) or not isinstance(quarantined, list) or
                        not all(isinstance(row, dict) for row in rows + quarantined) or
                        not isinstance(summary, dict)):
                    raise ValueError(f"House qualification row arrays missing: {version}")
                if extraction is None:
                    reasons.add("qualification_without_extraction")
                    record["qualification_state"] = "unknown"
                else:
                    record["qualified_transaction_count"] = len(rows)
                    record["quarantined_row_count"] = len(quarantined)
                    from .house_ptr_paper_qualification import paper_materialization_counts
                    paper_counts = paper_materialization_counts(qualification, extraction)
                    if paper_counts is not None:
                        record.update(paper_counts)
                        record["quarantined_row_count"] = paper_counts["whole_source_unique_quarantined_row_count"]
                    reasons.update(_reasons(summary))
                    for row in quarantined:
                        if isinstance(row, dict):
                            reasons.update(_reasons(row))
                    state = ("partial" if rows and quarantined else "qualified" if rows else
                             "quarantined" if quarantined else "zero_transactions"
                             if summary.get("zero_transaction_document_count") == 1 else "unknown")
                    ledger = qualification.get("source_report_ledger")
                    if ledger is not None:
                        if (not isinstance(ledger, dict) or
                                ledger.get("document_sha256") != selected or
                                ledger.get("source_id") != "house_clerk"):
                            raise ValueError("House source ledger provenance mismatch")
                        if ledger.get("relation_coverage") != "accounted":
                            reasons.add("source_row_coverage_incomplete")
                            state = "partial" if rows else "quarantined" if quarantined else "unknown"
                    record["qualification_state"] = state
                    from .house_ptr import revision_reference_dispositions
                    revisions = revision_reference_dispositions(qualification, extraction, transactions)
                    if revisions:
                        record["revision_reference_dispositions"] = revisions
                        reasons.update(item["disposition"] for item in revisions)
                    if state == "unknown":
                        reasons.add("empty_qualification_without_zero_declaration")
            elif extraction is not None:
                reasons.add("qualification_not_available")
        elif not archived:
            record["extraction_state"] = "not_attempted"
            reasons.add("archive_not_available")
            if any(version[:2] == key for values in artifacts.values() for version in values):
                record["extraction_state"] = "unknown"
                record["qualification_state"] = "unknown"
                reasons.add("review_without_archive_receipt")
        else:
            record["qualification_state"] = "unknown"
        record["reason_codes"] = sorted(reasons)
        records.append(record)

    by_url = {record["source_url"]: record for record in records}
    by_id: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_id[record["document_id"]].append(record)
    unmatched: list[str] = []
    seen_ids: set[str] = set()
    for row in transactions:
        if row.get("source_id") != "house_clerk":
            continue
        txid = row.get("id")
        if not isinstance(txid, str) or not txid or txid in seen_ids:
            raise ValueError("House publication transaction ID missing or duplicated")
        seen_ids.add(txid)
        filing_id, url = row.get("filing_id"), row.get("source_url")
        record = by_url.get(url) if isinstance(url, str) else None
        if record is not None and filing_id is not None and str(filing_id) != record["document_id"]:
            record = None
        if not url and len(by_id.get(str(filing_id), [])) == 1:
            record = by_id[str(filing_id)][0]
        if record is None:
            unmatched.append(txid)
        else:
            record["publication_transaction_ids"].append(txid)
    for record in records:
        record["publication_transaction_ids"].sort()
    if not years:
        limitations.append("No archived annual PTR index is available; catalog coverage is unknown.")
    return [{"scope_id": "house_ptr", "source_id": "house_clerk",
             "scope": {"document_type": "PTR", "filing_years": sorted(years),
                       "catalog_selection": "latest_retrieved_at_per_year",
                       "coverage_description": "All PTR keys in archived indexes and archive/review receipts; no full-history coverage claim.",
                       "source_version_selection": "latest_archive_retrieved_at; ambiguous chronology remains unknown",
                       "filing_date_precision": "day; official FilingDate, never transaction or catalog-added date"},
             "observed_at": _stamp(max(observed) if observed else None),
             "limitations": limitations, "reports": records,
             "unmatched_publication_transaction_ids": sorted(unmatched)}]
