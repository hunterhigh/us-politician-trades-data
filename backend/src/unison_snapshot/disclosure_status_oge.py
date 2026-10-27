"""Read-only report status for OGE direct, request occurrences and White House PDFs.

Qualification comes from the existing qualification artifacts.  Publication comes
only from the actual transaction/holding arguments, never from candidate totals.
Saved administrative page proofs are replayed from original bytes; they never
grant transaction qualification or a name-based crosswalk.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DIRECT_ID = re.compile(r"[0-9a-f]{32}\Z")
_SAVED_WHITEHOUSE_PARSERS = {
    "whitehouse-278t-saved-ocr-roles/v6": None,
    "whitehouse-278t-saved-ocr-dual-axis/v7": "source_pdf_dual_axis/v1",
}


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


def _verified_administrative_pages(record, receipt, old, evidence, catalog_record):
    """Recheck saved roles with independent expected identity and source bytes."""
    proofs = _rows(receipt, 'non_table_page_proofs')
    conflicts = _rows(receipt, 'page_role_conflicts')
    if not proofs and not conflicts:
        return [], [], None
    if evidence is None or not _DIRECT_ID.fullmatch(record.get('report_id') or ''):
        raise ValueError('OGE administrative page proof requires controlled original source')
    sha, document = receipt['source_sha256'], record['report_id']
    relative = f'oge/reports/{document}/{sha}.pdf'
    path = evidence / relative
    raw = path.read_bytes()
    metadata = _read(path.with_suffix('.json'))
    if (metadata.get('schema_version') != 'oge-278t-archive/v1' or metadata.get('source_id') != 'oge' or
            metadata.get('document_id') != document or metadata.get('document_url') != record['source_url'] or
            metadata.get('sha256') != sha or metadata.get('archive_path') != relative or
            metadata.get('byte_length') != len(raw) or hashlib.sha256(raw).hexdigest() != sha):
        raise ValueError('OGE administrative page source metadata or bytes differ')
    import pdfplumber
    with pdfplumber.open(path) as document_pages:
        page_count = len(document_pages.pages)
    if not 1 <= page_count <= 1200 or receipt.get('page_count_basis') != 'source_sha256_verified_pdf':
        raise ValueError('OGE administrative page proof lacks its verified source page inventory')
    from .oge_transaction_adapter import _cover_identity_context, _parser_claimed_pages
    from .oge_278t_page_roles import replay_native_page_role
    context = _cover_identity_context(old, catalog_record)
    if context is not None and any(metadata.get(key) != context[key]
                                   for key in ('filer_name', 'agency', 'position_title')):
        context = None
    observed, conflicting = [], []
    for collection, destination in ((proofs, observed), (conflicts, conflicting)):
        for proof in collection:
            number = proof.get('page_number')
            if (type(number) is not int or number < 1 or number > 1200 or
                    number in observed or number in conflicting or proof.get('source_sha256') != sha or
                    not replay_native_page_role(path, proof, source_sha256=sha, page_number=number,
                                                cover_identity_context=context)):
                raise ValueError('OGE administrative page proof differs from original source replay')
            if destination is observed and number in _parser_claimed_pages(old):
                raise ValueError('OGE administrative page proof conflicts with original parser rows')
            destination.append(number)
    return sorted(observed), sorted(conflicting), page_count


def _observed_reference_counts(record: dict, audit: dict, review: Path | None,
                               evidence: Path | None = None, catalog_record: dict | None = None) -> dict:
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
    geometry = receipt.get("geometry_unknown_pages")
    if (not isinstance(geometry, list) or any(type(page) is not int or page < 1 for page in geometry) or
            geometry != sorted(set(geometry)) or set(geometry) & {page for page, _ in locations}):
        raise ValueError("OGE geometry-unknown pages disagree with observed source slots")
    unknown_locations = set()
    for row in unknown:
        page, slot = row.get("page_number"), row.get("slot_key")
        key = (page, slot)
        if (type(page) is not int or page not in geometry or not isinstance(slot, str) or
                not re.fullmatch(r"[1-9]\d*", slot) or key in locations or key in unknown_locations or
                row.get("source_sha256", sha) != sha or row.get("disposition") != "unknown" or
                row.get("reason") != "column_geometry_unknown" or row.get("field_observation_ref") is not None):
            raise ValueError("OGE unknown regions repeat or change source geometry slots")
        unknown_locations.add(key)
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
            unobserved != sorted(set(unobserved)) or
            set(unobserved) & ({page for page, _ in locations} | set(geometry))):
        raise ValueError("OGE observed/unobserved page coverage is inconsistent")
    administrative, conflicts, page_count = _verified_administrative_pages(record, receipt, old, evidence, catalog_record)
    claimed_pages = set(unobserved) | {page for page, _ in locations} | set(geometry)
    if set(administrative) & claimed_pages:
        raise ValueError('OGE administrative page proof overlaps observed or unknown source claims')
    all_pages = claimed_pages | set(administrative) | set(conflicts)
    if not all_pages or max(all_pages) > 1200 or all_pages != set(range(1, max(all_pages) + 1)):
        raise ValueError("OGE source page coverage has unexplained gaps")
    if page_count is not None and all_pages != set(range(1, page_count+1)):
        raise ValueError('OGE administrative page partition differs from actual source page count')
    return {"quarantined_row_count": None, "observed_quarantined_slot_count": len(quarantined_slots),
            "observed_slot_count": observed, "legacy_normalized_reference_count": normalized,
            "legacy_quarantined_reference_count": old_quarantine,
            "quarantine_count_unit": audit["quarantine_count_unit"], "unique_source_quarantined_row_count": None,
            "legacy_reference_sha256": receipt["legacy_reference_sha256"],
            "source_observation_coverage": {"coverage_complete": False, "unobserved_pages": unobserved,
                "geometry_unknown_pages": receipt.get("geometry_unknown_pages", []),
                "page_count_basis": receipt.get("page_count_basis"),
                **({'non_table_pages':administrative, 'page_role_conflict_pages':conflicts}
                   if administrative or conflicts else {})}}


def _native_exhibit_counts(record: dict, audit: dict) -> dict:
    """A replacement block count is never a whole-document transaction total."""
    receipt = audit["observation_receipt"]
    sha = audit.get("source_sha256")
    if (receipt.get("schema_version") != "oge-278t-observed-facts/v1" or
            sha not in record["source_versions"] or receipt.get("source_sha256") != sha or
            audit.get("document_id") != record["report_id"] or receipt.get("coverage_complete") is not False or
            receipt.get("observation_unit") != "native_replacement_table_block" or
            receipt.get("unique_source_row_count", "missing") is not None):
        raise ValueError("OGE native replacement coverage lacks consistent source evidence")
    rows = _rows(receipt, "rows")
    slots, quarantined = {}, {}
    for row in rows:
        page, table = row.get("page_number"), row.get("table_index")
        if (type(page) is not int or page < 1 or type(table) is not int or table < 0 or
                (page, table) in slots or row.get("source_sha256") != sha or
                row.get("method") != "embedded_native_pdf_words" or
                not _SHA.fullmatch(row.get("input_observation_canonical_sha256") or "") or
                row.get("disposition") not in ({"quarantined", "unresolved", "qualified", "superseded"}
                    if audit.get("native_exhibit_application_context") else {"quarantined", "unresolved"})):
            raise ValueError("OGE native replacement receipt repeats or changes a source block")
        slots[page, table] = row
        if row["disposition"] == "quarantined":
            identifier = row.get("extraction_id")
            if not isinstance(identifier, str) or identifier in quarantined:
                raise ValueError("OGE native replacement block identity is not unique")
            quarantined[identifier] = row
    if _count(receipt.get("observed_slot_count")) != len(rows):
        raise ValueError("OGE native replacement block counts do not close")
    bound = set()
    for row in _rows(audit, "quarantined"):
        ref = row.get("native_exhibit_reference")
        if ref is None:
            continue  # Existing inventory is not a new replacement block.
        identifier = row.get("extraction_id")
        expected = quarantined.get(identifier)
        if (expected is None or identifier in bound or ref != {
                key: value for key, value in expected.items()
                if key not in {"extraction_id", "disposition", "reasons"}}):
            raise ValueError("OGE quarantine differs from its native replacement source block")
        bound.add(identifier)
    if bound != set(quarantined):
        raise ValueError("OGE native replacement quarantine is incomplete")
    unobserved = receipt.get("unobserved_pages")
    if (not isinstance(unobserved, list) or any(type(p) is not int or p < 1 for p in unobserved) or
            unobserved != sorted(set(unobserved)) or set(unobserved) & {p for p, _ in slots}):
        raise ValueError("OGE native replacement page coverage overlaps")
    known_pages = {p for p, _ in slots}
    for region in _rows(receipt, "unknown_regions"):
        page = region.get("page_number")
        if (type(page) is not int or page < 1 or page in unobserved or
                not _SHA.fullmatch(region.get("input_observation_canonical_sha256") or "")):
            raise ValueError("OGE native replacement unknown region is unbound")
        known_pages.add(page)
    all_pages = set(unobserved) | known_pages
    if not all_pages or max(all_pages) > 1200 or all_pages != set(range(1, max(all_pages) + 1)):
        raise ValueError("OGE native replacement page coverage has unexplained gaps")
    return {"quarantined_row_count": None, "unique_source_quarantined_row_count": None,
        "quarantine_count_unit": "native_replacement_blocks_not_whole_source_unique_rows",
        "observed_replacement_block_count": len(rows),
        "observed_quarantined_replacement_block_count": len(quarantined),
        **({"observed_applied_replacement_block_count":sum(r.get("disposition")=="qualified" for r in rows),
            "observed_superseded_replacement_block_count":sum(r.get("disposition")=="superseded" for r in rows)}
           if audit.get("native_exhibit_application_context") else {}),
        "source_observation_coverage": {"coverage_complete": False, "unobserved_pages": unobserved,
            "unknown_regions": receipt.get("unknown_regions", []),
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


def _saved_whitehouse_counts(record: dict, audit: dict, review: Path | None,
                            evidence: Path | None = None) -> dict:
    """Replay the saved-word consumer; OCR pages are not a source-row census."""
    from .whitehouse_278t import validate_generic_whitehouse_report
    parser = audit.get("parser_version")
    sha = audit.get("source_sha256")
    receipt = audit.get("observation_receipt")
    if (review is None or not isinstance(parser, str) or parser not in _SAVED_WHITEHOUSE_PARSERS or
            sha not in record["source_versions"] or not isinstance(receipt, dict) or
            audit.get("document_id") != record["report_id"] or
            audit.get("source_url") != record["source_url"]):
        raise ValueError("White House saved-word status lacks its archived source binding")
    folder = review / "whitehouse/extractions" / record["report_id"].split(":")[-1] / sha
    reports = [_read(path) for path in sorted(folder.glob("*.json"))
               if not path.name.endswith(".failure.json")]
    reports = [row for row in reports if row.get("parser_version") == parser]
    if len(reports) != 1:
        raise ValueError("White House saved-word status requires a unique current cache")
    report = reports[0]
    if any(report.get(key) != audit.get(key) for key in
           ("document_id", "source_sha256", "source_url", "filed_at", "saved_ocr_input", "observation_receipt",
            "source_identity_resolution")):
        raise ValueError("White House saved-word audit differs from its source cache")
    geometry_mode = _SAVED_WHITEHOUSE_PARSERS[parser]
    saved_input = report.get("saved_ocr_input")
    if (not isinstance(saved_input, dict) or
            any(value != geometry_mode for value in
                (report.get("geometry_mode"), audit.get("geometry_mode"),
                 receipt.get("geometry_mode"), saved_input.get("geometry_mode"))) or
            geometry_mode is not None and receipt.get("local_row_geometry_mode") != geometry_mode):
        raise ValueError("White House saved-word geometry mode differs from its parser and source")
    # This validator materializes again from the referenced checkpoint and PDF
    # when available. A matching receipt/digest alone is not source validation.
    if evidence is None:
        validate_generic_whitehouse_report(report)
    else:
        from .whitehouse_278t import saved_ocr_source_roots
        with saved_ocr_source_roots(evidence, review):
            validate_generic_whitehouse_report(report)
    source_rows = [*_rows(report, "transactions"), *_rows(report, "quarantined")]
    decisions = _rows(audit, "rows")
    source_ids = [row.get("extraction_id") for row in source_rows]
    retained_ids = audit.get("retained_ids", [])
    retained_count = _count(audit.get("retained_count", 0))
    allowed_statuses = {"promoted", "quarantined"} | ({"retained_existing"} if geometry_mode else set())
    if (None in source_ids or len(set(source_ids)) != len(source_ids) or
            [row.get("extraction_id") for row in decisions] != source_ids or
            _count(audit.get("source_row_count")) != len(source_rows) or
            any(row.get("status") not in allowed_statuses for row in decisions) or
            _count(audit.get("promoted_count")) != sum(row["status"] == "promoted" for row in decisions) or
            _count(audit.get("quarantined_count")) != sum(row["status"] == "quarantined" for row in decisions) or
            retained_count != sum(row["status"] == "retained_existing" for row in decisions) or
            retained_ids != [row["extraction_id"] for row in decisions if row["status"] == "retained_existing"]):
        raise ValueError("White House saved-word decisions do not conserve their source rows")
    if retained_ids or audit.get("current_source_retained_bindings") is not None:
        def canonical(value):
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        expected_binding = {"schema_version": "whitehouse-saved-current-retained/v1",
            "source_sha256": sha, "document_id": report["document_id"], "parser_version": parser,
            "geometry_mode": geometry_mode, "rows": [
                {"formal_id": row["extraction_id"], "source_rows_index": row.get("source_rows_index"),
                 "page_number": row.get("page_number"), "raw_cells_canonical_sha256": canonical(row.get("cells")),
                 "basis": "current_source_replayed_exact_formal_projection"}
                for row in source_rows if row["extraction_id"] in retained_ids]}
        if geometry_mode is None or audit.get("current_source_retained_bindings") != expected_binding:
            raise ValueError("White House saved-word retained facts lack current source bindings")
    qualified_source_ids = {row["extraction_id"] for row in _rows(report, "transactions")}
    for source, decision in zip(source_rows, decisions, strict=True):
        source_reasons, reasons = source.get("reasons", []), decision.get("reasons", [])
        if (not isinstance(reasons, list) or not isinstance(source_reasons, list) or
                not set(source_reasons) <= set(reasons) or
                decision.get("status") in {"promoted", "retained_existing"} and
                (reasons or source_reasons or source["extraction_id"] not in qualified_source_ids)):
            raise ValueError("White House saved-word decision drops a source blocker")
    source_document_reasons = report.get("document_reasons", [])
    audit_document_reasons = audit.get("document_reasons", [])
    if (not isinstance(source_document_reasons, list) or
            not isinstance(audit_document_reasons, list) or
            not set(source_document_reasons) <= set(audit_document_reasons)):
        raise ValueError("White House saved-word audit drops a source document blocker")
    if ((audit.get("promoted_count") or retained_count) and (receipt.get("source_original_verified") is not True or
            _date(report.get("filed_at")) is None or source_document_reasons or audit_document_reasons)):
        raise ValueError("White House saved-word promotion lacks verified source or filing date")
    if (receipt.get("schema_version") != "whitehouse-278t-saved-ocr-receipt/v1" or
            receipt.get("mode") != "saved_ocr_words" or receipt.get("source_sha256") != sha or
            receipt.get("coverage_complete") is not False or
            receipt.get("unique_source_row_count", "missing") is not None):
        raise ValueError("White House saved-word receipt has inconsistent count semantics")
    words = _count(receipt.get("observed_word_page_count"))
    slots = _count(receipt.get("observed_physical_slot_count"))
    if words is None or slots is None:
        raise ValueError("White House saved-word observed counts are missing")
    partial = (report.get("content_observations") or {}).get("partial_geometry_pages", [])
    partial_counts = {}
    if receipt.get("local_row_geometry_mode") is not None:
        regions = [{"page_number": page["page_number"], "regions": page["unresolved_regions"]}
                   for page in partial]
        partial_by_page = {page["page_number"]: page for page in partial}
        geometry_by_page = {page["page_number"]: page for page in
                            _rows(report.get("geometry_observations") or {}, "pages")}
        unbound_pages = []
        for raw_page in _rows(report, "raw_page_observations"):
            number = raw_page["page_number"]
            if number == 1:
                continue
            if number in partial_by_page:
                count = len(partial_by_page[number]["unbound_page_ocr_words"])
                basis = "partial_grid_unbound_saved_words"
            elif geometry_by_page.get(number, {}).get("coverage") == "physical_slots_located":
                count = _count(geometry_by_page[number].get("words_outside_slots"))
                basis = "fully_located_grid_words_outside_slots"
            else:
                count = len(_rows(raw_page, "words"))
                basis = "geometry_unknown_all_saved_words"
            if count is None:
                raise ValueError("White House observed words lack a source count")
            unbound_pages.append({"page_number": number, "word_count": count, "basis": basis})
        expected_local_mode = geometry_mode or "strict_joint_boundaries/v1"
        if (receipt["local_row_geometry_mode"] != expected_local_mode or
                audit.get("partial_geometry_pages", []) != partial or
                receipt.get("geometry_partial_pages") != [page["page_number"] for page in partial] or
                receipt.get("unresolved_regions") != regions or
                receipt.get("unbound_observed_word_unit") != "selected_table_page_words_outside_known_slots" or
                receipt.get("unbound_observed_word_pages") != unbound_pages or
                _count(receipt.get("unbound_observed_word_count")) != sum(page["word_count"] for page in unbound_pages) or
                receipt.get("whole_selected_table_slot_count", "missing") !=
                (None if partial or receipt.get("geometry_unknown_pages") else slots)):
            raise ValueError("White House local geometry status differs from source coverage")
        if any(_count(page.get("known_physical_slot_count")) is None or
               page.get("whole_page_physical_slot_count", "missing") is not None for page in partial):
            raise ValueError("White House local slot counts cannot claim a complete page census")
        partial_counts = {"local_row_geometry_mode": receipt["local_row_geometry_mode"],
            "geometry_partial_pages": receipt["geometry_partial_pages"],
            "unresolved_regions": regions,
            "whole_selected_table_slot_count": receipt["whole_selected_table_slot_count"],
            "unbound_observed_word_count": receipt["unbound_observed_word_count"],
            "unbound_observed_word_unit": receipt["unbound_observed_word_unit"],
            "unbound_observed_word_pages": unbound_pages}
    elif partial or audit.get("partial_geometry_pages"):
        raise ValueError("White House partial source coverage requires its explicit local mode")
    return {"quarantined_row_count": None, "unique_source_quarantined_row_count": None,
        "qualified_retained_transaction_ids": retained_ids,
        "quarantine_count_unit": "saved_ocr_entries_not_whole_source_unique_rows",
        "observed_quarantined_entry_count": _count(audit.get("quarantined_count")),
        "observed_word_page_count": words, "observed_physical_slot_count": slots,
        "source_observation_coverage": {**partial_counts, **{key: receipt.get(key) for key in
            ("coverage_complete", "unobserved_pages", "geometry_unknown_pages",
             "page_count_basis", "source_original_verified", "selected_page_numbers")}}}


def _native_partition_counts(record, audit, review, evidence):
    """Reopen the selected native projection before reporting its status."""
    from .native_partition_rows import partition_binding
    from .oge_native_partition import VERSIONS, replay_partitioned_oge
    from .whitehouse_278t import (NATIVE_VISIBLE_PARSER_VERSIONS,
        saved_ocr_source_roots, validate_native_visible_whitehouse_report)
    binding = audit.get('native_source_partition_binding')
    version, sha = audit.get('parser_version'), audit.get('source_sha256')
    if review is None or evidence is None or not isinstance(binding, dict) or sha not in record['source_versions']:
        raise ValueError('Native partition report status requires its selected review and original')
    whitehouse = version in NATIVE_VISIBLE_PARSER_VERSIONS
    if not whitehouse and version not in VERSIONS:
        raise ValueError('Native partition report status parser is unsupported')
    prefix = ('whitehouse/extractions/' + record['report_id'].removeprefix('wh-url:')
              if whitehouse else 'oge/extractions/' + record['report_id'])
    report = _read(review / prefix / sha / (version.replace('/', '-') + '.json'))
    if report.get('source_url') != record['source_url'] or report.get('document_id') != record['report_id']:
        raise ValueError('Native partition status crossed report identity')
    if whitehouse:
        with saved_ocr_source_roots(evidence, review):
            validate_native_visible_whitehouse_report(report)
    else:
        replay_partitioned_oge(report, evidence)
    if binding != partition_binding(report['native_text_partition'], version):
        raise ValueError('Native partition report status differs from original projection')
    audit_reasons = audit.get('document_reasons', [])
    if not isinstance(audit_reasons, list) or not set(report.get('document_reasons', [])) <= set(audit_reasons):
        raise ValueError('Native partition status erased original document blockers')
    retained = audit.get('retained_ids', []) if whitehouse else []
    current_good = {row.get('extraction_id') for row in report['transactions']}
    if report.get('document_reasons'):
        current_good = set()
    current_qualified_count = None
    if whitehouse:
        decisions = _rows(audit, 'rows')
        source = report['transactions'] + report['quarantined']
        if len(source) != len(decisions) or any(
                decision.get('extraction_id') != row.get('extraction_id') or
                decision.get('row_number') != row.get('row_number') or
                not set(row.get('reasons', [])) <= set(decision.get('reasons', []) if isinstance(decision.get('reasons'), list) else []) or
                decision.get('status') not in {'promoted', 'retained_existing', 'quarantined'} or
                decision.get('status') != 'quarantined' and row.get('extraction_id') not in current_good
                for row, decision in zip(source, decisions)):
            raise ValueError('Native partition status decisions differ from visible source rows')
        expected = [row['extraction_id'] for row in decisions if row.get('status') == 'retained_existing']
        if not isinstance(retained, list) or retained != expected or len(set(retained)) != len(retained) or not set(retained) <= current_good:
            raise ValueError('Native partition retained status differs from visible source rows')
        if (audit.get('promoted_count') != sum(row['status'] == 'promoted' for row in decisions) or
                audit.get('retained_count') != len(retained) or
                audit.get('quarantined_count') != sum(row['status'] == 'quarantined' for row in decisions)):
            raise ValueError('Native partition qualification counts differ from source decisions')
    else:
        published = audit.get('native_partition_published_ids')
        preserved = audit.get('native_partition_retained_formal_ids')
        rejected = _rows(audit, 'quarantined')
        all_source_ids = {row.get('extraction_id') for row in report['transactions'] + report['quarantined']}
        def rejection_key(row):
            return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        required_rejections = Counter(rejection_key(row) for row in report['quarantined'])
        observed_rejections = Counter(rejection_key(row) for row in rejected)
        if (not isinstance(published, list) or not isinstance(preserved, list) or
                any(not isinstance(value, str) for value in published + preserved) or
                len(set(published)) != len(published) or len(set(preserved)) != len(preserved) or
                not set(preserved) <= set(published) or
                not set(published) - set(preserved) <= current_good or
                audit.get('promoted_count') != len(published) or
                audit.get('quarantined_count') != len(rejected) or
                bool(required_rejections - observed_rejections) or
                any(row.get('extraction_id') not in all_source_ids for row in rejected)):
            raise ValueError('Native partition qualification counts differ from source decisions')
        # Publication may preserve historical complete facts that the current
        # visible projection cannot qualify again. Keep those separate.
        current_qualified_count = len(set(published) & current_good)
    return {'source_observation_coverage': {'coverage_complete': False,
                'unknown_regions': report['native_text_partition'].get('unknown_regions', []),
                'page_count_basis': 'same_sha_original_native_partition'},
            'qualified_retained_transaction_ids': retained,
            **({'native_partition_current_qualified_count': current_qualified_count}
               if current_qualified_count is not None else {})}


def _legacy_amount_current_count(audit: dict) -> int | None:
    """Conserved predecessors are publication facts, not current OCR approvals."""
    from .oge_278t_field_shadow import LEGACY_AMOUNT_INTERPRETATION_MODE
    receipt = audit.get('observation_receipt') or {}
    mode = audit.get('field_interpretation_mode')
    if mode is None and receipt.get('field_interpretation_mode') is None:
        return None
    if (mode != LEGACY_AMOUNT_INTERPRETATION_MODE or receipt.get('field_interpretation_mode') != mode
            or receipt.get('coverage_complete') is not False):
        raise ValueError('OGE legacy amount interpretation mode differs from its receipt')
    sets = []
    for field in ('amount_recompute_current_qualified_ids', 'amount_recompute_retained_formal_ids',
                  'amount_recompute_published_ids'):
        values = audit.get(field)
        if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                or values != sorted(set(values))):
            raise ValueError('OGE legacy amount qualification identities are not unique')
        sets.append(set(values))
    current, retained, published = sets
    rejected_ids = {row.get('extraction_id') for row in audit.get('quarantined', [])
                    if isinstance(row.get('extraction_id'), str)}
    unresolved_ids = {row.get('extraction_id') for row in receipt.get('rows', [])
                      if row.get('disposition') in {'unresolved', 'quarantined'}
                      and isinstance(row.get('extraction_id'), str)}
    normalized_reference = receipt.get('legacy_inventory_basis') == \
        'normalized_disposition_reference_without_physical_inventory'
    materialized_ids = {row.get('extraction_id') for row in receipt.get('rows', [])
                        if row.get('disposition') == 'materialized'}
    normalized_only_ids = retained - materialized_ids if normalized_reference else set()
    if (not current <= published or not retained <= published or published != current | retained
            or current & (rejected_ids | unresolved_ids | normalized_only_ids)
            or _count(audit.get('promoted_count')) != len(published)
            or _count(audit.get('quarantined_count')) != len(audit.get('quarantined', []))
            or receipt.get('protected_formal_reference_ids') != sorted(retained)):
        raise ValueError('OGE legacy amount qualification counts do not close')
    conflicts = []
    for row in receipt.get('rows', []):
        blockers = row.get('interpretation_blockers')
        if not blockers:
            continue
        reference = {key:row.get(key) for key in
            ('source_sha256', 'page_number', 'slot_key', 'field_observation_ref')}
        conflicts.append({**reference, 'interpretation_blockers':blockers})
        if (row.get('disposition') != 'unresolved' or row.get('extraction_id') in current or
                not {item['reason'] for item in blockers} <= set(row.get('reasons', []))):
            raise ValueError('OGE unresolved legacy amount row was qualified')
        if not any(rejection.get('observation_reference') == reference and
                {item['reason'] for item in blockers} <= set(rejection.get('reasons', []))
                for rejection in audit.get('quarantined', [])):
            raise ValueError('OGE legacy amount conflict lacks its rejected source row')
    if receipt.get('interpretation_conflicts') != conflicts:
        raise ValueError('OGE legacy amount conflicts differ from their receipt rows')
    return len(current)


def _apply_audit(record: dict, audit: dict, *, review: Path | None = None,
                 evidence: Path | None = None, catalog_record: dict | None = None) -> None:
    application_view = None
    if audit.get("native_exhibit_application_context") is not None:
        if review is None or evidence is None:
            raise ValueError("native replacement application requires controlled archive and review roots")
        from .oge_native_replacements import verify_native_exhibit_context
        application_view = verify_native_exhibit_context(audit["native_exhibit_application_context"], evidence, review).get(audit["document_id"])
        if application_view is None or application_view["source_sha256"] not in record["source_versions"]:
            raise ValueError("native replacement application lacks its source-bound view")
        for field in ("observation_receipt", "native_exhibit_replacement_applications", "native_exhibit_superseded_transaction_ids"):
            if audit.get(field) != application_view.get(field):
                raise ValueError("native replacement qualification differs from current source replay")
        expected_count = sum(r.get("native_replacement_disposition") != "superseded" for r in application_view["transactions"])
        expected_count += len(application_view.get("native_exhibit_preserved_target_ids", []))
        if audit.get("promoted_count") != expected_count or audit.get("quarantined_count") != len(application_view["quarantined"]):
            raise ValueError("native replacement qualification counts differ from source replay")
        from .oge_candidate import _identity_key, _person_id, _transaction_projection
        key = _identity_key(audit["native_exhibit_application_context"]["records"][audit["document_id"]])
        record["_native_replacement_expected_facts"] = [_transaction_projection(application_view,row,_person_id(key))
            for row in application_view["transactions"] if row.get("native_replacement_disposition") != "superseded"]
        record["superseded_transaction_ids"] = sorted(application_view.get("native_exhibit_superseded_transaction_ids", []))
        record["native_replacement_mappings"] = [{field:item[field] for field in
            ("effective_prior_transaction_id", "transaction_id", "prior_transaction_id", "amendment_document_id",
             "amendment_source_sha256", "amendment_filed_at", "application_status")}
            for item in application_view.get("native_exhibit_replacement_applications", [])]
    receipt = audit.get("observation_receipt")
    native_exhibit = isinstance(receipt, dict) and receipt.get("mode") == "native_exhibit_substitution"
    if (isinstance(receipt, dict) and receipt.get("legacy_inventory_basis") ==
            "normalized_disposition_reference_without_physical_inventory" and "quarantine_count_unit" not in audit):
        raise ValueError("OGE legacy reference observation counts require their explicit unit")
    saved_whitehouse = (isinstance(audit.get("parser_version"), str) and
                       audit["parser_version"] in _SAVED_WHITEHOUSE_PARSERS or
                       "saved_ocr_input" in audit or
                       isinstance(receipt, dict) and (
                           receipt.get("mode") == "saved_ocr_words" or
                           receipt.get("schema_version") == "whitehouse-278t-saved-ocr-receipt/v1"))
    native_partition = 'native_source_partition_binding' in audit
    separate_counts = (_native_partition_counts(record, audit, review, evidence) if native_partition else
                       _saved_whitehouse_counts(record, audit, review, evidence) if saved_whitehouse else
                       _native_exhibit_counts(record, audit) if native_exhibit else
                       _observed_reference_counts(record, audit, review, evidence, catalog_record)
                       if "quarantine_count_unit" in audit else {})
    promoted = _count(audit.get("promoted_count"))
    quarantined = _count(audit.get("quarantined_count"))
    retained = (separate_counts.pop("qualified_retained_transaction_ids", []) if saved_whitehouse or native_partition else
                _whitehouse_retained_ids(record, audit, review))
    if retained:
        if promoted is None:
            raise ValueError("White House retained qualification lacks a promoted count")
        promoted += len(retained)
        record["qualified_retained_transaction_ids"] = retained
    if 'native_partition_current_qualified_count' in separate_counts:
        promoted = separate_counts.pop('native_partition_current_qualified_count')
    amount_current = _legacy_amount_current_count(audit)
    if amount_current is not None:
        promoted = amount_current
    record["qualified_transaction_count"] = promoted
    record["quarantined_row_count"] = quarantined
    record["reason_codes"].extend(_reasons(audit))
    incomplete_observations = native_partition or isinstance(receipt, dict) and receipt.get("coverage_complete") is not True
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
    if amount_current is not None:
        record['source_observation_coverage'] = {**record.get('source_observation_coverage', {}),
            'coverage_complete':False, 'field_interpretation_mode':audit['field_interpretation_mode'],
            'current_qualified_transaction_ids':audit['amount_recompute_current_qualified_ids'],
            'retained_formal_transaction_ids':audit['amount_recompute_retained_formal_ids'],
            'published_transaction_ids':audit['amount_recompute_published_ids']}
    if application_view is not None and not promoted and not quarantined and (
            record.get("superseded_transaction_ids") or any(
                row.get("native_replacement_disposition") == "superseded" for row in application_view["transactions"])):
        record["qualification_state"] = "superseded"


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


def _bind_publication(scopes: list[dict], transactions: list[dict], holdings: list[dict], *,
                      direct_annual_ids: frozenset[str] = frozenset()) -> None:
    direct, _, whitehouse = scopes[:3]
    annual = scopes[3] if len(scopes) == 4 else None
    indexes = {scope["scope_id"]: {row["report_id"]: row for row in scope["reports"]}
               for scope in [direct, whitehouse] + ([annual] if annual is not None else [])}
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
            if not is_whitehouse and report_id in direct_annual_ids:
                if annual is None:
                    raise ValueError("archived annual facts require their annual status scope")
                scope = annual
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
            expected = record.pop("_annual_qualified_formal_ids", None)
            if expected is not None and any(not set(record.get(key, [])) <= set(expected[key])
                    for key in ("publication_transaction_ids", "publication_holding_ids")):
                record["publication_binding"] = "unknown"
                record["reason_codes"].append("published_annual_id_not_in_bound_qualification")
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


def _build_direct_annual(archives: dict[str, list[dict]], review: Path) -> dict:
    """Keep direct 278e facts out of PTR status; reuse the annual producer audit."""
    observed = max((row["retrieved_at"] for values in archives.values() for row in values
                    if isinstance(row.get("retrieved_at"), str)), default="") or None
    scope = _scope("oge_direct_278e", {
        "document_types": ["278e_annual"],
        "coverage_description": "Controlled archived annual reports; full annual catalog coverage is unknown.",
        "qualification_count_unit": "observed_annual_row_decisions",
    }, observed, ["annual_catalog_coverage_unknown",
                  "annual_qualified_counts_are_observed_rows_not_whole_source_physical_totals"])
    container = _read(review / "whitehouse/annual/candidate-current.json").get("oge_annual", {})
    reports = _rows(container, "reports") if isinstance(container, dict) and container.get(
        "schema_version") == "oge-annual-candidate-audit/v1" else []
    by_id = {}
    for audit in reports:
        document = audit.get("document_id")
        if document in by_id:
            raise ValueError("Annual status audit has duplicate report identities")
        by_id[document] = audit
    for document, versions in sorted(archives.items()):
        urls = {row["document_url"] for row in versions}
        record = _record(document, sorted(urls)[0], "278e_annual", "historical")
        if len(urls) > 1:
            record["reason_codes"].append("multiple_annual_archive_urls")
        _add_archive_state(record, versions, review, "oge/annual/extractions", False)
        record["qualified_holding_count"] = None
        record["quarantine_count_unit"] = "whole_source_annual_rows_unknown"
        audit = by_id.get(document)
        if audit is None:
            record["reason_codes"].append("annual_qualification_audit_unavailable")
        elif (len(record["source_versions"]) != 1 or
                audit.get("source_sha256") != record["source_versions"][0]):
            record["qualification_state"] = "unknown"
            record["reason_codes"].append("annual_qualification_source_binding_unresolved")
        elif audit.get("disposition") == "excluded_exact_source_copy":
            canonical = audit.get("canonical_document_id")
            canonical_versions = {row["sha256"] for row in archives.get(canonical, [])}
            canonical_audit = by_id.get(canonical, {})
            if (canonical != document and canonical_versions == set(record["source_versions"]) and
                    canonical_audit.get("source_sha256") == audit["source_sha256"] and
                    canonical_audit.get("disposition") != "excluded_exact_source_copy"):
                record.update(qualification_state="superseded", qualified_transaction_count=0,
                              qualified_holding_count=0, exact_source_canonical_report_id=canonical,
                              exact_source_sha256=audit["source_sha256"],
                              _annual_qualified_formal_ids={"publication_transaction_ids": [],
                                                            "publication_holding_ids": []})
                record["reason_codes"].append("exact_source_copy_alias")
            else:
                record["qualification_state"] = "unknown"
                record["reason_codes"].append("annual_exact_source_alias_binding_unresolved")
        else:
            rows = audit.get("row_qualification")
            if not isinstance(rows, list) or not rows:
                record["reason_codes"].append("annual_row_qualification_unavailable")
            else:
                candidates, formal = set(), set()
                counts = {"transactions": 0, "holdings": 0, "quarantined": 0}
                missing_bindings = 0
                report_reasons = audit.get("report_reasons", [])
                if not isinstance(report_reasons, list) or any(not isinstance(r, str) or not r for r in report_reasons):
                    raise ValueError("Annual status report reasons are invalid")
                reasons = set(report_reasons)
                expected = {"publication_transaction_ids": [], "publication_holding_ids": []}
                if audit.get("unlocated_source_issues"):
                    reasons.add("annual_source_rows_outside_page_inventory")
                for row in rows:
                    candidate = row.get("candidate_id")
                    disposition = row.get("disposition")
                    if (not isinstance(candidate, str) or not candidate or candidate in candidates or
                            row.get("section") not in {"part2", "part5", "part6", "part7"} or
                            disposition not in {"qualified", "quarantined", "excluded", "unrecognized"}):
                        raise ValueError("Annual status row decisions are invalid or duplicated")
                    row_reasons = row.get("reasons", [])
                    if not isinstance(row_reasons, list) or any(not isinstance(r, str) or not r for r in row_reasons):
                        raise ValueError("Annual status row reasons are invalid")
                    candidates.add(candidate)
                    if disposition == "qualified":
                        identifier = row.get("formal_id")
                        # Part 7's existing producer uses its source-row ID as
                        # the formal transaction ID; holdings have a separate
                        # formal_id. Require the full source identity binding.
                        if identifier is None and row["section"] == "part7":
                            identity = row.get("source_row_identity_sha256")
                            if isinstance(identity, str) and _SHA.fullmatch(identity) and candidate == "oge-annual-row:" + identity[:24]:
                                identifier = candidate
                        if row_reasons:
                            raise ValueError("Annual status qualified row has blocking reasons")
                        if identifier is None:
                            missing_bindings += 1
                            continue
                        if not isinstance(identifier, str) or not identifier or identifier in formal:
                            raise ValueError("Annual status qualified formal identities are invalid or duplicated")
                        formal.add(identifier)
                        counts["transactions" if row["section"] == "part7" else "holdings"] += 1
                        expected["publication_transaction_ids" if row["section"] == "part7"
                                 else "publication_holding_ids"].append(identifier)
                    elif disposition in {"quarantined", "unrecognized"}:
                        counts["quarantined"] += 1
                        reasons.update(row_reasons)
                total = counts["transactions"] + counts["holdings"]
                record.update(extraction_state="extracted", observed_annual_row_decision_count=len(rows),
                              observed_quarantined_annual_row_count=counts["quarantined"])
                if missing_bindings:
                    record.update(qualification_state="unknown", observed_annual_row_identity_gap_count=missing_bindings)
                    reasons.add("annual_formal_identity_binding_unavailable")
                else:
                    record.update(
                        qualification_state=("partial" if total and (counts["quarantined"] or reasons)
                            else "qualified" if total else "quarantined" if counts["quarantined"] else "unknown"),
                        qualified_transaction_count=counts["transactions"], qualified_holding_count=counts["holdings"],
                        selected_source_version=record["source_versions"][0], _annual_qualified_formal_ids=expected)
                record["reason_codes"].extend(reasons)
                # Empty/fully excluded decisions do not attest a zero-transaction report.
                if not total and not counts["quarantined"]:
                    record["reason_codes"].append("annual_empty_qualified_rows_not_zero_transaction_attestation")
        scope["reports"].append(record)
    return scope


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
            _apply_audit(record, audit_by_id[report_id], review=review, evidence=evidence,
                         catalog_record=catalog_direct.get(report_id))
            _bind_direct_audit(record, audit_by_id[report_id], review)
            _apply_manual_direct_date(record, audit_by_id[report_id], review)
        else:
            record["reason_codes"].append("qualification_artifact_missing_for_report")
        direct["reports"].append(record)
    if not audits:
        direct["limitations"].append("direct_qualification_artifact_unavailable")

    public = _build_whitehouse(evidence, review)
    trade = _read(review / "whitehouse/qualifications/candidate-current.json")
    cross_application = trade.get("native_cross_source_applications")
    if cross_application is not None:
        from .oge_cross_source_replacements import verify_cross_source_native_applications
        cross_candidate, verified = verify_cross_source_native_applications(cross_application,evidence,review)
        direct_by_id = {row["report_id"]: row for row in direct["reports"]}
        public_by_id = {row["report_id"]: row for row in public["reports"]}
        published = {row["id"]: row for row in transactions}
        active = {item["transaction_id"] for item in verified["applications"] if item["application_status"]=="applied"}
        for item in verified["applications"]:
            prior=item["prior_transaction_id"];new=item["transaction_id"]
            if prior in published:
                raise ValueError("superseded White House predecessor is still published")
            if new in active:
                expected=next(row for row in cross_candidate["transactions"] if row["id"]==new)
                if new in published and any(expected.get(key)!=published[new].get(key) for key in set(expected)|set(published[new])):
                    raise ValueError("cross-source published replacement differs from family-qualified facts")
        for doc in {item["target"]["target_document_id"] for item in verified["applications"]}:
            record=public_by_id.get(doc)
            if record is None:raise ValueError("cross-source predecessor lacks its White House report status")
            retired=sorted({item["prior_transaction_id"] for item in verified["applications"] if item["target"]["target_document_id"]==doc})
            count=record.get("qualified_transaction_count",0)
            if count<len(retired):raise ValueError("cross-source retirement exceeds qualified White House inventory")
            record["qualified_transaction_count"]=count-len(retired)
            record["superseded_transaction_ids"]=retired
            record["reason_codes"]=sorted(set(record["reason_codes"]+["source_verified_amount_only_replacement"]))
            if record["qualified_transaction_count"]==0 and record.get("quarantined_row_count")==0:
                record["qualification_state"]="superseded"
        for doc,view in verified["source_views"].items():
            record=direct_by_id.get(doc)
            if record is None:raise ValueError("cross-source replacement lacks its OGE report status")
            applied=[item for item in verified["applications"] if item["amendment_document_id"]==doc]
            if not applied:continue
            record["qualified_transaction_count"]+=sum(item["transaction_id"] in active for item in applied)
            record["observed_applied_replacement_block_count"]=sum(item["transaction_id"] in active for item in applied)
            if "observed_quarantined_replacement_block_count" in record:
                record["observed_quarantined_replacement_block_count"]-=len(applied)
            record["qualification_state"]="partial" if view["observation_receipt"]["coverage_complete"] is False else "qualified"
            if not view['quarantined']:
                record['reason_codes']=[reason for reason in record['reason_codes'] if reason not in {
                    'amendment_relationship_unresolved','replacement_target_document_unresolved',
                    'replacement_original_source_or_unique_row_unverified','replacement_context_candidate_not_unique_or_unverified'}]
            record["native_cross_source_replacement_mappings"]=[{key:item[key] for key in
                ("prior_transaction_id","effective_prior_transaction_id","transaction_id","application_status")} for item in applied]
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
    annual_archives = _archive_records(evidence, "oge/annual/reports", False)
    if set(annual_archives) & (set(archives) | set(catalog_direct)):
        raise ValueError("OGE annual archive is also classified as a direct 278-T report")
    if annual_archives:
        result.append(_build_direct_annual(annual_archives, review))
    _bind_publication(result, transactions, holdings, direct_annual_ids=frozenset(annual_archives))
    actual = {row["id"]: row for row in transactions}
    for scope in result:
        for record in scope["reports"]:
            for expected in record.pop("_native_replacement_expected_facts", []):
                fact = actual.get(expected["id"])
                if fact is not None and any(fact.get(field) != expected.get(field) for field in set(fact) | set(expected)):
                    raise ValueError("published native replacement differs from source-qualified formal facts")
            if set(record.get("superseded_transaction_ids", [])) & set(actual):
                raise ValueError("superseded native replacement predecessor is still published")
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
        _apply_audit(record, audit, review=review, evidence=evidence)
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
