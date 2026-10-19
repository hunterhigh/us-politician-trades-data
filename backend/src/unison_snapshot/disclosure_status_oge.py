"""Read-only report status for OGE direct, request occurrences and White House PDFs.

Qualification comes from the existing qualification artifacts.  Publication comes
only from the actual transaction/holding arguments, never from candidate totals.
No PDF parsing or name-based crosswalk is performed here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DIRECT_ID = re.compile(r"[0-9a-f]{32}\Z")


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Report status requires a JSON object: {path}")
    return value


def _rows(value: dict, key: str) -> list[dict]:
    rows = value.get(key, [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Report status requires an object array: {key}")
    return rows


def _count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Report status count must be a nonnegative integer")
    return value


def _time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _official_id(url: object, whitehouse: bool) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.netloc != parsed.hostname or parsed.query or parsed.fragment):
        return None
    path = unquote(parsed.path)
    if whitehouse:
        if (parsed.hostname != "www.whitehouse.gov" or
                not path.startswith("/wp-content/uploads/") or
                not path.lower().endswith(".pdf")):
            return None
        return "wh-url:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    if parsed.hostname != "extapps2.oge.gov" or not path.lower().endswith(".pdf"):
        return None
    match = re.search(r"/([0-9a-f]{32})/\$file/", path, re.I)
    return match[1].lower() if match else None


def _record(report_id: str | None, url: str | None, kind: str,
            catalog_state: str = "unknown") -> dict:
    return {
        "record_key": report_id, "report_id": report_id, "document_type": kind,
        "source_url": url, "catalog_state": catalog_state,
        "acquisition_state": "unknown", "extraction_state": "unknown",
        "qualification_state": "not_evaluated",
        "qualified_transaction_count": None, "quarantined_row_count": None,
        "reason_codes": [], "source_versions": [],
        "publication_transaction_ids": [], "publication_binding": "unknown",
        "filed_date": None, "filed_date_basis": None,
    }


def _reasons(row: dict) -> list[str]:
    reasons = set()
    for key in ("document_reasons", "holding_blocking_reasons", "reasons",
                "row_blocking_reasons"):
        values = row.get(key, [])
        if isinstance(values, list):
            reasons.update(item for item in values if isinstance(item, str))
    for key in ("rows", "quarantined"):
        for item in _rows(row, key):
            reasons.update(_reasons(item))
    return sorted(reasons)


def _date(value: object) -> str | None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _observed_reference_counts(record: dict, audit: dict, review: Path | None) -> dict:
    """Keep a verified observation count separate from an unknown source total."""
    if audit.get("quarantine_count_unit") != "observed_slots_not_whole_source_unique_rows" or review is None:
        raise ValueError("OGE observation count unit requires its bound legacy reference")
    receipt, sha = audit.get("observation_receipt"), audit.get("source_sha256")
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != "oge-278t-observed-facts/v1" or
            not isinstance(sha, str) or sha not in record["source_versions"] or
            audit.get("document_id") != record["report_id"] or receipt.get("source_sha256") != sha or
            receipt.get("legacy_inventory_basis") != "normalized_disposition_reference_without_physical_inventory" or
            receipt.get("legacy_normalized_not_sent_to_qualification") is not True or
            receipt.get("coverage_complete") is not False or
            "unique_source_quarantined_row_count" not in audit or audit["unique_source_quarantined_row_count"] is not None or
            "unique_source_row_count" not in receipt or receipt["unique_source_row_count"] is not None):
        raise ValueError("OGE observed/reference counts lack consistent source evidence")
    normalized = _count(audit.get("legacy_normalized_reference_count"))
    old_quarantine = _count(audit.get("legacy_quarantined_reference_count"))
    if (normalized is None or old_quarantine is None or normalized + old_quarantine == 0 or
            receipt.get("legacy_normalized_transaction_count") != normalized or
            receipt.get("legacy_quarantined_reference_count") != old_quarantine):
        raise ValueError("OGE legacy reference counts disagree")
    matched = []
    for path in sorted((review / "oge/extractions" / record["report_id"] / sha).glob("*.json")):
        if path.name.endswith(".failure.json"):
            continue
        old = _read(path)
        if hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest() == receipt.get("legacy_reference_sha256"):
            matched.append(old)
    if len(matched) != 1:
        raise ValueError("OGE observed counts have no unique saved legacy reference")
    old = matched[0]
    if (old.get("schema_version") != "oge-278t-extraction/v1" or old.get("parser_version") != "oge-278t-pdf/v2" or
            old.get("source_id") != "oge" or old.get("document_id") != record["report_id"] or
            old.get("source_sha256") != sha or old.get("source_url") != record["source_url"] or "source_rows" in old or
            len(_rows(old, "transactions")) != normalized or len(_rows(old, "quarantined")) != old_quarantine):
        raise ValueError("OGE legacy reference differs from archived report identity")
    rows = _rows(receipt, "rows")
    locations, by_id = {}, defaultdict(list)
    for row in rows:
        page, slot = row.get("page_number"), row.get("slot_key")
        key = (page, slot)
        if (type(page) is not int or page < 1 or not isinstance(slot, str) or not re.fullmatch(r"[1-9]\d*", slot) or
                key in locations or row.get("source_sha256") != sha or row.get("disposition") not in {
                    "materialized", "quarantined", "unresolved", "retained_legacy", "excluded"}):
            raise ValueError("OGE observed receipt repeats or changes a source slot")
        locations[key] = row
        if isinstance(row.get("extraction_id"), str):
            by_id[row["extraction_id"]].append(key)
    observed = _count(receipt.get("observed_slot_count"))
    unknown = _rows(receipt, "unknown_regions")
    if observed is None or len(rows) + len(unknown) != observed:
        raise ValueError("OGE observed receipt slot counts do not close")
    quarantined_slots = set()
    for row in _rows(audit, "quarantined"):
        ref = row.get("observation_reference")
        keys = ([(ref.get("page_number"), ref.get("slot_key"))] if isinstance(ref, dict) and
                ref.get("source_sha256") == sha else by_id.get(row.get("extraction_id"), []))
        if len(keys) != 1 or keys[0] not in locations or keys[0] in quarantined_slots:
            raise ValueError("OGE quarantine does not bind uniquely to observed slots")
        quarantined_slots.add(keys[0])
    if len(quarantined_slots) != _count(audit.get("quarantined_count")):
        raise ValueError("OGE observed quarantine count differs from its receipt")
    unobserved = receipt.get("unobserved_pages")
    if (not isinstance(unobserved, list) or any(type(page) is not int or page < 1 for page in unobserved) or
            unobserved != sorted(set(unobserved)) or set(unobserved) & {page for page, _ in locations}):
        raise ValueError("OGE observed/unobserved page coverage is inconsistent")
    return {"quarantined_row_count": None, "observed_quarantined_slot_count": len(quarantined_slots),
            "observed_slot_count": observed, "legacy_normalized_reference_count": normalized,
            "legacy_quarantined_reference_count": old_quarantine,
            "quarantine_count_unit": audit["quarantine_count_unit"], "unique_source_quarantined_row_count": None,
            "legacy_reference_sha256": receipt["legacy_reference_sha256"],
            "source_observation_coverage": {"coverage_complete": False, "unobserved_pages": unobserved,
                "geometry_unknown_pages": receipt.get("geometry_unknown_pages", []),
                "page_count_basis": receipt.get("page_count_basis")}}


def _whitehouse_retained_ids(record: dict, audit: dict, review: Path | None) -> list[str]:
    """Reproduce retained source bindings before adding them to qualification."""
    from .whitehouse_278t import (GENERIC_PARSER_VERSION, validate_generic_whitehouse_report,
        _generic_table_rows, _inherit_whitehouse_generic_rows)
    if audit.get("parser_version") != GENERIC_PARSER_VERSION:
        return []
    ids, count = audit.get("retained_ids"), _count(audit.get("retained_count"))
    if (not isinstance(ids, list) or any(not isinstance(value, str) for value in ids) or
            count is None or len(ids) != count or len(set(ids)) != count or
            sorted(ids) != sorted(row.get("extraction_id") for row in _rows(audit, "rows")
                                  if row.get("status") == "retained_existing")):
        raise ValueError("White House retained identities do not match candidate decisions")
    if not ids:
        return []
    sha, binding = audit.get("source_sha256"), audit.get("retained_source_bindings")
    if (review is None or sha not in record["source_versions"] or not isinstance(binding, dict) or
            binding.get("schema_version") != "whitehouse-generic-retained-source/v1" or
            binding.get("source_sha256") != sha or
            binding.get("legacy_cache_hash_is_reference_only") is not True):
        raise ValueError("White House retained identities lack archived source binding")
    folder = review / "whitehouse/extractions" / record["report_id"].split(":")[-1] / sha
    cached = [_read(path) for path in sorted(folder.glob("*.json"))
              if not path.name.endswith(".failure.json")]
    reports = [item for item in cached if item.get("parser_version") == GENERIC_PARSER_VERSION]
    def canonical(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    legacy = [item for item in cached if canonical(item) == binding.get("legacy_extraction_canonical_sha256")]
    if len(reports) != 1 or len(legacy) != 1:
        raise ValueError("White House retained identities lack unique current and legacy caches")
    report = reports[0]
    if any(report.get(key) != value for key, value in (("document_id", record["report_id"]),
            ("source_sha256", sha), ("source_url", record["source_url"]))):
        raise ValueError("White House retained cache crossed report sources")
    validate_generic_whitehouse_report(report)
    good, bad, _ = _generic_table_rows([(row["page_number"], row["cells"])
        for row in report["source_rows"]], source_sha256=sha)
    reproduced = _inherit_whitehouse_generic_rows({**report, "transactions": good, "quarantined": bad}, legacy[0])
    source_rows = {row["extraction_id"]: row for row in reproduced["transactions"]
                   if row.get("legacy_inheritance") and not row.get("reasons") and
                   not row["legacy_inheritance"].get("new_observation_reasons")}
    if not set(ids) <= set(source_rows):
        raise ValueError("White House retained identities were not reproduced from raw source cells")
    for identifier in ids:
        stored = [row for row in report["transactions"] if row.get("extraction_id") == identifier]
        if len(stored) != 1 or stored[0] != source_rows[identifier]:
            raise ValueError("White House retained cache differs from its source replay")
    expected = [{"formal_id": identifier, "page_number": source_rows[identifier]["page_number"],
        "source_rows_index": source_rows[identifier]["source_rows_index"],
        "raw_cells_canonical_sha256": canonical(source_rows[identifier]["cells"]),
        "basis": "same_source_raw_cells_stable_id_replayed_and_formal_projection_bound"} for identifier in ids]
    if binding.get("rows") != expected:
        raise ValueError("White House retained cell bindings disagree with source replay")
    return ids


def _apply_audit(record: dict, audit: dict, *, review: Path | None = None) -> None:
    receipt = audit.get("observation_receipt")
    if (isinstance(receipt, dict) and receipt.get("legacy_inventory_basis") ==
            "normalized_disposition_reference_without_physical_inventory" and "quarantine_count_unit" not in audit):
        raise ValueError("OGE legacy reference observation counts require their explicit unit")
    separate_counts = (_observed_reference_counts(record, audit, review)
                       if "quarantine_count_unit" in audit else {})
    promoted = _count(audit.get("promoted_count"))
    quarantined = _count(audit.get("quarantined_count"))
    retained = _whitehouse_retained_ids(record, audit, review)
    if retained:
        if promoted is None:
            raise ValueError("White House retained qualification lacks a promoted count")
        promoted += len(retained)
        record["qualified_retained_transaction_ids"] = retained
    record["qualified_transaction_count"] = promoted
    record["quarantined_row_count"] = quarantined
    record["reason_codes"].extend(_reasons(audit))
    incomplete_observations = isinstance(receipt, dict) and receipt.get("coverage_complete") is not True
    if incomplete_observations:
        record["reason_codes"].append("observed_source_coverage_incomplete")
    if promoted is None or quarantined is None:
        record["qualification_state"] = "unknown"
    elif promoted:
        record["qualification_state"] = (
            "partial" if quarantined or audit.get("document_reasons") or incomplete_observations else "qualified")
    elif quarantined or audit.get("document_reasons"):
        record["qualification_state"] = "quarantined"
    elif incomplete_observations:
        record["qualification_state"] = "unknown"
    else:
        record["qualification_state"] = "zero_transactions"
    record.update(separate_counts)


def _unbound_qualification(record: dict, reason: str) -> None:
    """Retain a prior audit as an observation without claiming current binding."""
    record["observed_qualification_state"] = record["qualification_state"]
    for key in ("qualified_transaction_count", "qualified_holding_count",
                "quarantined_row_count", "qualification_row_rejected_count"):
        if key in record:
            record[f"observed_{key}"] = record[key]
            record[key] = None
    record["qualification_state"] = "unknown"
    record["reason_codes"].append(reason)


def _bind_direct_audit(record: dict, audit: dict, review: Path) -> None:
    versions = record["source_versions"]
    sha = audit.get("source_sha256")
    if sha is not None:
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError("OGE qualification source version is invalid")
        if sha not in versions:
            _unbound_qualification(record, "qualification_source_version_not_in_archive_inventory")
            return
        paths = (review / "oge/extractions" / record["report_id"] / sha).glob("*.json")
        if not any(not path.name.endswith(".failure.json") for path in paths):
            _unbound_qualification(record, "qualification_current_original_not_extracted")
        return
    # The legacy direct audit does not identify its PDF hash. It can only close
    # over one archived original with a successful extraction. Counts from an
    # older audit remain visible separately when the current identity is open.
    if not versions:
        _unbound_qualification(record, "qualification_source_archive_missing")
    elif len(versions) != 1:
        _unbound_qualification(record, "qualification_original_version_unresolved")
    elif record["extraction_state"] != "extracted":
        _unbound_qualification(record, "qualification_current_original_not_extracted")


def _apply_manual_direct_date(record: dict, audit: dict, review: Path) -> None:
    evidence = audit.get("manual_filing_date_evidence")
    if evidence is None:
        return
    sha = audit.get("source_sha256")
    report_id = record["report_id"]
    if not isinstance(evidence, dict) or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("OGE manual filing date audit is invalid")
    relative = f"oge/manual_cover_dates/{report_id}/{sha}.json"
    if (evidence.get("review_path") != relative or
            evidence.get("source_sha256") != sha or
            evidence.get("page_number") != 1):
        raise ValueError("OGE manual filing date path is invalid")
    sidecar = _read(review / relative)
    filed = evidence.get("observed_date")
    if (sidecar.get("document_id") != report_id or
            sidecar.get("schema_version") != "oge-manual-cover-date/v1" or
            sidecar.get("status") != "observation_only" or
            sidecar.get("source_sha256") != sha or
            sidecar.get("source_url") != record["source_url"] or
            sidecar.get("field_role") != "filer_signature_date" or
            sidecar.get("recognition_method") != "human_visual_reading_of_pdf_cover" or
            sidecar.get("page_number") != evidence.get("page_number") or
            sidecar.get("filed_at") != filed or
            sidecar.get("observed_date") != filed or
            sidecar.get("raw_reading") != evidence.get("raw_reading") or
            _date(filed) is None):
        raise ValueError("OGE manual filing date is not source-bound")
    bound = sha in record["source_versions"] and record["qualification_state"] != "unknown"
    field = "filed_date" if bound else "observed_filed_date"
    record[field] = filed
    record[field + "_basis"] = "human_pdf_cover_signature"


def _archive_records(root: Path, relative: str, whitehouse: bool) -> dict[str, list[dict]]:
    records = defaultdict(list)
    for path in sorted((root / relative).glob("*/*.json")):
        row = _read(path)
        report_id, sha, url = row.get("document_id"), row.get("sha256"), row.get("document_url")
        if (not isinstance(report_id, str) or not isinstance(sha, str) or not _SHA.fullmatch(sha) or
                path.stem != sha or _official_id(url, whitehouse) != report_id):
            raise ValueError(f"Report archive metadata identity is invalid: {path}")
        records[report_id].append(row)
    return dict(records)


def _catalog(evidence: Path, review: Path) -> tuple[dict, dict, list[dict], list[str]]:
    """Use the source status pointer; only fall back when no pointer exists."""
    metadata = [_read(path) for path in sorted((evidence / "oge/catalog").glob("*.json"))]
    metadata = [row for row in metadata if row.get("schema_version") == "oge-catalog-archive/v1"]
    status = _read(review / "status/oge.json")
    sha = status.get("catalog_sha256")
    limitations = []
    if sha:
        current = next((row for row in metadata if row.get("sha256") == sha), {})
        if not current:
            limitations.append("current_catalog_pointer_missing_from_evidence")
    elif metadata:
        current = max(metadata, key=lambda row: (row.get("retrieved_at", ""), row.get("sha256", "")))
        limitations.append("current_catalog_selected_by_latest_archived_observation")
    else:
        current = {}
        limitations.append("oge_catalog_unavailable")
    catalogs = []
    current_catalog = {}
    for item in metadata:
        digest = item.get("sha256")
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError("OGE catalog archive hash is invalid")
        path = evidence / "oge/catalog/normalized" / f"{digest}.json"
        if not path.is_file():
            if item == current:
                limitations.append("current_normalized_catalog_unavailable")
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("OGE normalized catalog hash differs from archive metadata")
        value = _read(path)
        catalogs.append(value)
        if item == current:
            current_catalog = value
    return current, current_catalog, catalogs, limitations


def _scope(scope_id: str, scope: dict, observed_at: str | None,
           limitations: list[str]) -> dict:
    return {"scope_id": scope_id, "source_id": "oge", "scope": scope,
            "observed_at": observed_at, "limitations": limitations, "reports": [],
            "unmatched_publication_transaction_ids": []}


def _add_archive_state(record: dict, archives: list[dict], review: Path,
                       relative: str, whitehouse: bool) -> None:
    versions = sorted({row["sha256"] for row in archives})
    record["source_versions"] = versions
    if archives:
        record["acquisition_state"] = "archived"
    folder_id = record["report_id"].split(":", 1)[-1] if whitehouse else record["report_id"]
    extraction_root = review / relative / folder_id
    success, failure = set(), set()
    for path in sorted(extraction_root.glob("*/*.json")):
        sha = path.parent.name
        if sha not in versions:
            continue
        (failure if path.name.endswith(".failure.json") else success).add(sha)
    # A prior parser failure cannot overrule a successful extraction of the same
    # PDF. Multiple original versions remain one report and are explicitly shown.
    if versions and set(versions) <= success:
        record["extraction_state"] = "extracted"
    elif success:
        record["extraction_state"] = "unknown"
        record["reason_codes"].append("archive_versions_not_all_extracted")
    elif failure:
        record["extraction_state"] = "failed"
    elif versions:
        record["extraction_state"] = "not_attempted"
    if len(versions) > 1:
        record["reason_codes"].append("multiple_archive_versions")


def _bind_publication(scopes: list[dict], transactions: list[dict], holdings: list[dict]) -> None:
    direct, _, whitehouse = scopes
    indexes = {scope["scope_id"]: {row["report_id"]: row for row in scope["reports"]}
               for scope in (direct, whitehouse)}
    for key, facts in (("publication_transaction_ids", transactions),
                       ("publication_holding_ids", holdings)):
        for fact in facts:
            if fact.get("source_id") != "oge":
                continue
            url = fact.get("source_url")
            try:
                host = urlsplit(url).hostname if isinstance(url, str) else None
            except ValueError:
                host = None
            is_whitehouse = host == "www.whitehouse.gov"
            scope = whitehouse if is_whitehouse else direct
            report_id = _official_id(url, is_whitehouse)
            if report_id is None:
                unmatched_key = ("unmatched_publication_transaction_ids" if key ==
                                 "publication_transaction_ids" else "unmatched_publication_holding_ids")
                scope.setdefault(unmatched_key, []).append(fact["id"])
                continue
            index = indexes[scope["scope_id"]]
            record = index.get(report_id)
            if record is None:
                record = _record(report_id, url, "unknown" if is_whitehouse else "278t", "historical")
                record["qualification_state"] = "unknown"
                record["reason_codes"].append("publication_report_outside_current_inventory")
                scope["reports"].append(record)
                index[report_id] = record
            # The official URL encodes the report identity. A legacy filing_id
            # can differ; it does not merge two URLs or two annual/278-T reports.
            if record["source_url"] and _official_id(record["source_url"], is_whitehouse) != report_id:
                raise ValueError("Publication report URL conflicts with status identity")
            record.setdefault(key, []).append(fact["id"])
            record["publication_binding"] = "verified"
            filed_at = fact.get("filed_at")
            filed = _date(filed_at[:10]) if isinstance(filed_at, str) else None
            if filed:
                record.setdefault("_published_filed_dates", []).append(filed)
    for scope in scopes:
        scope["reports"].sort(key=lambda row: row["record_key"])
        for record in scope["reports"]:
            published_dates = set(record.pop("_published_filed_dates", []))
            if not record["filed_date"] and len(published_dates) == 1:
                record["filed_date"] = next(iter(published_dates))
                record["filed_date_basis"] = "published_official_fact"
            elif len(published_dates) > 1:
                record["reason_codes"].append("publication_report_filed_dates_conflict")
            for key in ("reason_codes", "source_versions", "publication_transaction_ids",
                        "publication_holding_ids"):
                if key in record:
                    record[key] = sorted(set(record[key]))
            record["publication_holding_count"] = len(record.get("publication_holding_ids", []))
        for key in ("limitations", "unmatched_publication_transaction_ids",
                    "unmatched_publication_holding_ids"):
            if key in scope:
                scope[key] = sorted(set(scope[key]))


def build_oge_status(evidence_root: Path, review_root: Path,
                     transactions: list[dict], holdings: list[dict]) -> list[dict]:
    """Build deterministic status scopes from fixed input trees and actual facts."""
    evidence, review = Path(evidence_root), Path(review_root)
    current_meta, current, history, limitations = _catalog(evidence, review)
    observed = _time(current_meta.get("retrieved_at"))
    sha = current_meta.get("sha256")
    coverage = {"document_types": ["278t"], "catalog_sha256": sha,
                "universe_complete": False, "catalog_unit": "occurrence"}
    direct = _scope("oge_direct_278t", dict(coverage), observed, list(limitations))
    request = _scope("oge_request_278t", dict(coverage), observed, [
        *limitations, "request_occurrences_have_no_verified_unique_report_identity",
        "whitehouse_name_type_year_crosswalk_is_not_report_identity"])
    catalog_direct = {}
    current_ids = set()
    for catalog in history:
        for row in _rows(catalog, "transactions"):
            if row.get("access_method") == "direct_pdf":
                report_id, url = row.get("source_document_id"), row.get("document_url")
                if _official_id(url, False) != report_id or not isinstance(report_id, str):
                    raise ValueError("OGE direct catalog identity is invalid")
                catalog_direct[report_id] = row
    occurrence_indexes = set()
    for row in _rows(current, "transactions"):
        if row.get("access_method") == "direct_pdf":
            current_ids.add(row["source_document_id"])
            catalog_direct[row["source_document_id"]] = row
        elif row.get("access_method") == "request_required":
            number = row.get("catalog_index")
            if type(number) is not int or number < 0 or number in occurrence_indexes or not sha:
                raise ValueError("OGE request occurrence lacks unique catalog position")
            occurrence_indexes.add(number)
            record = _record(None, row.get("document_url"), "278t", "current")
            record.update(record_key=f"oge-request:{sha}:{number}", acquisition_state="request_required",
                          extraction_state="not_attempted", catalog_index=number,
                          catalog_sha256=sha, reason_codes=["request_required_no_verified_report_identity"])
            if _date(row.get("catalog_added_date")):
                record["catalog_added_date"] = row["catalog_added_date"]
            request["reports"].append(record)
    archives = _archive_records(evidence, "oge/reports", False)
    audits = _read(review / "oge/qualifications/current.json")
    audit_by_id = {row["document_id"]: row for row in _rows(audits, "reports")}
    if len(audit_by_id) != len(_rows(audits, "reports")):
        raise ValueError("OGE report qualification IDs are duplicated")
    for report_id in sorted(set(catalog_direct) | set(archives) | set(audit_by_id)):
        if not isinstance(report_id, str) or not _DIRECT_ID.fullmatch(report_id):
            raise ValueError("OGE qualification report identity is invalid")
        source = catalog_direct.get(report_id) or (archives.get(report_id) or [{}])[0]
        url = source.get("document_url")
        record = _record(report_id, url, "278t", "current" if report_id in current_ids else "historical")
        if _date(source.get("catalog_added_date")):
            record["catalog_added_date"] = source["catalog_added_date"]
        if report_id in catalog_direct:
            record["acquisition_state"] = "not_archived"
        _add_archive_state(record, archives.get(report_id, []), review, "oge/extractions", False)
        if report_id in audit_by_id:
            _apply_audit(record, audit_by_id[report_id], review=review)
            _bind_direct_audit(record, audit_by_id[report_id], review)
            _apply_manual_direct_date(record, audit_by_id[report_id], review)
        else:
            record["reason_codes"].append("qualification_artifact_missing_for_report")
        direct["reports"].append(record)
    if not audits:
        direct["limitations"].append("direct_qualification_artifact_unavailable")

    public = _build_whitehouse(evidence, review)
    trade = _read(review / "whitehouse/qualifications/candidate-current.json")
    if trade.get("schema_version") == "whitehouse-278t-exact-source-candidate/v1":
        direct_by_id = {row["report_id"]: row for row in direct["reports"]}
        public_by_id = {row["report_id"]: row for row in public["reports"]}
        for relation in _rows(trade, "cross_source_relations"):
            record = direct_by_id.get(relation.get("oge_document_id"))
            canonical = public_by_id.get(relation.get("canonical_document_id"))
            sha = relation.get("source_sha256")
            if (record is None or canonical is None or sha not in record["source_versions"] or
                    sha not in canonical["source_versions"] or record["source_url"] != relation.get("oge_source_url")):
                direct["limitations"].append("cross_source_exact_alias_archive_binding_unavailable")
                continue
            record["exact_source_related_report_id"] = canonical["report_id"]
            record["exact_source_sha256"] = sha
            if relation.get("oge_formal_transaction_count") == 0:
                record["qualification_state"] = "superseded"
                record["qualified_transaction_count"] = 0
                record["quarantined_row_count"] = 0
                record["reason_codes"].append("exact_source_copy_alias_to_whitehouse")
    result = [direct, request, public]
    _bind_publication(result, transactions, holdings)
    return result


def _build_whitehouse(evidence: Path, review: Path) -> dict:
    index = _read(review / "whitehouse/disclosures/index-current.json")
    coverage = _read(review / "whitehouse/coverage-current.json")
    annual = _read(review / "whitehouse/annual/filer-reported-current.json")
    trade = _read(review / "whitehouse/qualifications/candidate-current.json")
    archives = _archive_records(evidence, "whitehouse/disclosures/reports", True)
    scope = _scope("whitehouse_public", {
        "document_types": ["278t", "278e_annual", "278e_unspecified", "278e_termination",
                           "278e_new_entrant", "278e_annual_term"],
        "page_sha256": index.get("page_sha256"), "universe_complete": False,
        "catalog_unit": "official_pdf_url"},
        _time(index.get("page_archive", {}).get("retrieved_at")), [])
    for name, value in (("public_index", index), ("coverage", coverage),
                        ("annual_qualification", annual), ("278t_qualification", trade)):
        if not value:
            scope["limitations"].append(f"whitehouse_{name}_unavailable")
    by_id = {}
    for item in _rows(index, "reports"):
        report_id, url = item.get("source_document_id"), item.get("document_url")
        if _official_id(url, True) != report_id or report_id in by_id:
            raise ValueError("White House catalog identity is invalid or duplicated")
        by_id[report_id] = _record(report_id, url, item.get("document_type_from_label") or "unknown", "current")
        by_id[report_id]["acquisition_state"] = "not_archived"
    for report_id, versions in archives.items():
        by_id.setdefault(report_id, _record(report_id, versions[0]["document_url"], "unknown", "historical"))
    for report_id, record in by_id.items():
        _add_archive_state(record, archives.get(report_id, []), review, "whitehouse/extractions", True)
    # Coverage is useful for selected extraction versions, never for the count
    # of production-qualified facts (its legacy value of zero is unbound).
    for item in _rows(coverage, "reports"):
        record = by_id.get(item.get("document_id"))
        if record is None:
            continue
        if item.get("document_url") != record["source_url"]:
            raise ValueError("White House coverage URL conflicts with catalog")
        state = item.get("review_state")
        if state in {"extracted_review_only", "extracted_with_issues"}:
            record["extraction_state"] = "extracted"
        elif state == "extraction_quarantined":
            record["extraction_state"] = "failed"
        elif state == "multiple_archive_versions_unresolved":
            record["extraction_state"] = "unknown"
            record["reason_codes"].append(state)
    qualified_ids = set()
    for audit in _rows(trade, "reports"):
        report_id, url = audit.get("document_id"), audit.get("source_url")
        if _official_id(url, True) != report_id or report_id in qualified_ids:
            raise ValueError("White House 278-T qualification identity is invalid")
        qualified_ids.add(report_id)
        record = by_id.setdefault(report_id, _record(report_id, url, "278t", "historical"))
        if record["source_url"] != url:
            raise ValueError("White House 278-T qualification URL conflicts with catalog")
        record["document_type"] = "278t"
        _apply_audit(record, audit, review=review)
        version_bound = _check_version(record, audit)
        if not version_bound:
            _unbound_qualification(record, "qualification_source_version_not_in_archive_inventory")
        if _date(audit.get("filed_at")):
            record["filed_date" if version_bound else "observed_filed_date"] = audit["filed_at"]
            record["filed_date_basis" if version_bound else "observed_filed_date_basis"] = "qualified_report"
    holding_rows, transaction_rows = defaultdict(list), defaultdict(list)
    for row in _rows(annual, "holdings"):
        holding_rows[row.get("document_id")].append(row)
    for row in _rows(annual, "transactions"):
        transaction_rows[row.get("document_id")].append(row)
    for audit in _rows(annual, "reports"):
        report_id, url = audit.get("document_id"), audit.get("source_url")
        if _official_id(url, True) != report_id or report_id in qualified_ids:
            raise ValueError("White House annual qualification identity is invalid")
        qualified_ids.add(report_id)
        record = by_id.setdefault(report_id, _record(report_id, url, "278e_annual", "historical"))
        if record["source_url"] != url or record["document_type"] == "278t":
            raise ValueError("White House annual qualification conflicts with catalog identity")
        record["document_type"] = "278e_annual"
        version_bound = _check_version(record, audit)
        hs, ts = holding_rows[report_id], transaction_rows[report_id]
        for row in [*hs, *ts]:
            if row.get("source_url") != url or row.get("source_sha256") != audit.get("source_sha256"):
                raise ValueError("White House annual row is not bound to its report")
        qh = (sum(row.get("source_holdings_eligible") is True for row in hs)
              if audit.get("source_candidate_eligible") is True else 0)
        qt = (sum(row.get("source_transaction_eligible") is True for row in ts)
              if audit.get("source_candidate_transaction_eligible") is True else 0)
        record["qualified_holding_count"] = qh
        record["qualified_transaction_count"] = qt
        # These explicit qualification row arrays are auditable; OCR quarantine
        # arrays can overlap them, so a total quarantine count stays unknown.
        record["quarantined_row_count"] = None
        record["reason_codes"].append("annual_quarantine_total_not_proven_disjoint")
        record["qualification_row_rejected_count"] = sum(
            row.get("source_holdings_eligible") is not True for row in hs) + sum(
            row.get("source_transaction_eligible") is not True for row in ts)
        record["reason_codes"].extend(_reasons(audit))
        for row in [*hs, *ts]:
            record["reason_codes"].extend(_reasons(row))
        eligible = audit.get("source_candidate_eligible") is True or audit.get("source_candidate_transaction_eligible") is True
        if qh or qt:
            record["qualification_state"] = (
                "partial" if audit.get("holding_coverage_status") != "complete" or
                record["qualification_row_rejected_count"] else "qualified")
        elif not eligible and (_reasons(audit) or hs or ts):
            record["qualification_state"] = "quarantined"
        else:
            # Eligible report/empty selected arrays do not prove the PDF has no
            # transactions, especially for annual sections outside this scope.
            record["qualification_state"] = "unknown"
            record["reason_codes"].append("annual_empty_qualified_rows_not_zero_transaction_attestation")
        if not version_bound:
            _unbound_qualification(record, "qualification_source_version_not_in_archive_inventory")
        if _date(audit.get("filing_date")):
            record["filed_date" if version_bound else "observed_filed_date"] = audit["filing_date"]
            record["filed_date_basis" if version_bound else "observed_filed_date_basis"] = "qualified_annual_report"
    if trade.get("schema_version") == "whitehouse-278t-exact-source-candidate/v1":
        for group in _rows(trade, "exact_source_aliases"):
            canonical_id, sha = group.get("canonical_document_id"), group.get("source_sha256")
            sources = _rows(group, "aliases")
            canonical_source = next((source for source in sources
                                     if source.get("document_id") == canonical_id), None)
            if canonical_source is None:
                raise ValueError("White House exact-source relation has no canonical source")
            canonical_versions = {item["sha256"] for item in archives.get(canonical_id, [])}
            for source in sources:
                report_id, url = source.get("document_id"), source.get("source_url")
                if (_official_id(url, True) != report_id or source.get("source_sha256") != sha):
                    raise ValueError("White House exact-source alias identity is invalid")
                record = by_id.setdefault(report_id, _record(report_id, url, "278t", "historical"))
                if sha not in record["source_versions"] or sha not in canonical_versions:
                    record["reason_codes"].append("exact_source_alias_archive_binding_missing")
                    continue
                record["exact_source_canonical_report_id"] = canonical_id
                record["exact_source_sha256"] = sha
                record["document_type"] = "278t"
                if report_id != canonical_id:
                    record["qualification_state"] = "superseded"
                    record["qualified_transaction_count"] = 0
                    record["quarantined_row_count"] = 0
                    record["reason_codes"].append("exact_source_copy_alias")
                else:
                    retained_rows = [row for row in _rows(trade, "retained_rows") if row.get("document_id") == report_id]
                    counted = set(record.get("qualified_retained_transaction_ids", []))
                    retained = sum(row.get("formal_source_url", url) == url and
                                   row.get("formal_transaction_id") not in counted for row in retained_rows)
                    if retained:
                        record["qualified_transaction_count"] = (record["qualified_transaction_count"] or 0) + retained
                        record["qualification_state"] = ("partial" if record["quarantined_row_count"] else "qualified")
                    foreign = [row for row in retained_rows if row.get("formal_source_url", url) != url]
                    if foreign and len(foreign) == group.get("canonical_row_count"):
                        record["qualification_state"] = "superseded"
                        record["qualified_transaction_count"] = 0
                        record["quarantined_row_count"] = 0
                        record["reason_codes"].append("exact_source_rows_retained_at_official_oge_url")
                        record["exact_source_formal_url"] = foreign[0]["formal_source_url"]
    scope["reports"] = list(by_id.values())
    return scope


def _check_version(record: dict, audit: dict) -> bool:
    sha = audit.get("source_sha256")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("White House qualification source version is invalid")
    return sha in record["source_versions"]
