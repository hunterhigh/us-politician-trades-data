"""Content addressed processing status, bound to one prepared disclosure board.

Source adapters observe existing artifacts. This module never promotes source
rows or changes the disclosure snapshot identity.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
import json
import re

from .builder import Bundle
from .codec import digest, encode

SCHEMA = "disclosure-status/v1"
PAGE_SCHEMA = "disclosure-status-records/v1"
MAX_STATUS_BYTES = 256 * 1024
MAX_STATUS_PAGES = 256
SHA1 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
STATES = {
    "catalog_state": {"current", "historical", "unknown"},
    "acquisition_state": {"archived", "request_required", "not_archived", "unknown"},
    "extraction_state": {"extracted", "failed", "not_attempted", "unknown"},
    "qualification_state": {"qualified", "partial", "quarantined", "zero_transactions",
                            "superseded", "not_evaluated", "unknown"},
    "publication_binding": {"verified", "unknown"},
}


def _time(value: object, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise ValueError("Status timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Status timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("Status timestamp must include timezone")


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(s, str) or not s.strip() for s in value):
        raise ValueError(f"Status {label} must contain nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"Status {label} contains duplicates")
    return value


def _count(value: object, label: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) is not int or value < 0:
        raise ValueError(f"Status {label} must be a nonnegative integer")


def validate_record(row: object) -> dict:
    if not isinstance(row, dict):
        raise ValueError("Status record must be an object")
    for key in ("record_key", "document_type"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"Status record has invalid {key}")
    for key in ("report_id", "source_url"):
        if key not in row or (row[key] is not None and
                              (not isinstance(row[key], str) or not row[key].strip())):
            raise ValueError(f"Status record has invalid {key}")
    for key in ("filed_date", "catalog_added_date"):
        if row.get(key) is not None:
            try:
                if not isinstance(row[key], str) or date.fromisoformat(row[key]).isoformat() != row[key]:
                    raise ValueError
            except ValueError:
                raise ValueError(f"Status record has invalid {key}") from None
    if row.get("filed_date") is not None and not row.get("filed_date_basis"):
        raise ValueError("Status filing date requires its source basis")
    for key, allowed in STATES.items():
        if row.get(key) not in allowed:
            raise ValueError(f"Status record has invalid {key}")
    for key in ("qualified_transaction_count", "quarantined_row_count"):
        if key not in row:
            raise ValueError(f"Status record is missing {key}")
        _count(row[key], key, nullable=True)
    _count(row.get("publication_transaction_count"), "publication_transaction_count")
    _strings(row.get("reason_codes"), "reason_codes")
    versions = _strings(row.get("source_versions"), "source_versions")
    if any(not SHA256.fullmatch(s) for s in versions):
        raise ValueError("Status source version must be a SHA-256")
    return row


def validate_reference(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"schema_version", "sha256", "byte_length"}
            or value["schema_version"] != SCHEMA
            or not isinstance(value["sha256"], str) or not SHA256.fullmatch(value["sha256"])
            or type(value["byte_length"]) is not int
            or not 0 < value["byte_length"] <= MAX_STATUS_BYTES):
        raise ValueError("Disclosure status reference is invalid")
    return value


def validate_summary(value: object, manifest: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        raise ValueError("Disclosure status summary schema is invalid")
    _time(value.get("generated_at"))
    publication = value.get("publication")
    if publication != {"snapshot_id": manifest["snapshot_id"],
                       "board_sha256": manifest["board"],
                       "data_cutoff_at": manifest["data_cutoff_at"]}:
        raise ValueError("Disclosure status belongs to another publication")
    if value["generated_at"] != manifest["generated_at"]:
        raise ValueError("Disclosure status generation time differs from its publication")
    provenance = value.get("provenance")
    required = {"code_commit", "evidence_commit", "review_commit", "previous_main_commit"}
    if (not isinstance(provenance, dict) or set(provenance) != required or
            any(not isinstance(provenance[k], str) or not SHA1.fullmatch(provenance[k])
                for k in required)):
        raise ValueError("Disclosure status provenance must use fixed commits")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Disclosure status has no source scopes")
    seen = set()
    all_pages = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Disclosure status scope must be an object")
        scope_id = source.get("scope_id")
        if not isinstance(scope_id, str) or not NAME.fullmatch(scope_id) or scope_id in seen:
            raise ValueError("Disclosure status scope is missing or duplicated")
        seen.add(scope_id)
        if source.get("source_id") not in {"house_clerk", "senate_efd", "oge"}:
            raise ValueError("Disclosure status source is invalid")
        if not isinstance(source.get("scope"), dict):
            raise ValueError("Disclosure status scope definition is missing")
        _time(source.get("observed_at"), nullable=True)
        _strings(source.get("limitations"), "limitations")
        counts = source.get("counts")
        if not isinstance(counts, dict):
            raise ValueError("Disclosure status counts are missing")
        for key in ("record_count", "identified_report_count", "unidentified_record_count",
                    "publication_transaction_count", "unmatched_publication_transaction_count"):
            _count(counts.get(key), key)
        if counts["identified_report_count"] + counts["unidentified_record_count"] != counts["record_count"]:
            raise ValueError("Disclosure status identity counts do not conserve records")
        for key in STATES:
            groups = counts.get(f"records_by_{key}")
            if not isinstance(groups, dict) or set(groups) - STATES[key]:
                raise ValueError("Disclosure status state counts are invalid")
            for count in groups.values():
                _count(count, key)
            if sum(groups.values()) != counts["record_count"]:
                raise ValueError("Disclosure status state counts do not conserve records")
        pages = source.get("pages")
        if not isinstance(pages, list):
            raise ValueError("Disclosure status pages are missing")
        for page in pages:
            if (not isinstance(page, dict) or set(page) != {"sha256", "byte_length", "record_count"}
                    or not isinstance(page["sha256"], str) or not SHA256.fullmatch(page["sha256"])
                    or page["sha256"] in all_pages or type(page["byte_length"]) is not int
                    or not 0 < page["byte_length"] <= MAX_STATUS_BYTES):
                raise ValueError("Disclosure status page reference is invalid")
            _count(page["record_count"], "page record_count")
            if not page["record_count"]:
                raise ValueError("Disclosure status page must not be empty")
            all_pages.add(page["sha256"])
        if sum(p["record_count"] for p in pages) != counts["record_count"]:
            raise ValueError("Disclosure status page counts do not conserve records")
    if len(all_pages) > MAX_STATUS_PAGES:
        raise ValueError("Disclosure status has too many pages")
    return value


def validate_page(value: object, source: dict, reference: dict) -> dict:
    if (not isinstance(value, dict) or value.get("schema_version") != PAGE_SCHEMA
            or value.get("scope_id") != source["scope_id"]
            or value.get("source_id") != source["source_id"]
            or not isinstance(value.get("records"), list)
            or len(value["records"]) != reference["record_count"]):
        raise ValueError("Disclosure status page identity or count is invalid")
    for row in value["records"]:
        validate_record(row)
    if len({r["record_key"] for r in value["records"]}) != len(value["records"]):
        raise ValueError("Disclosure status page contains duplicate records")
    return value


def validate_scope_records(source: dict, records: list[dict]) -> None:
    """Reconcile all pages with their summary after a fixed-commit readback."""
    counts = source["counts"]
    if (len(records) != counts["record_count"] or
            len({r["record_key"] for r in records}) != len(records)):
        raise ValueError("Disclosure status records are missing or duplicated across pages")
    identities = [r["report_id"] for r in records if r["report_id"] is not None]
    if len(set(identities)) != len(identities) or len(identities) != counts["identified_report_count"]:
        raise ValueError("Disclosure status report identities differ from the summary")
    if sum(r["report_id"] is None for r in records) != counts["unidentified_record_count"]:
        raise ValueError("Disclosure status unknown identities differ from the summary")
    if sum(r["publication_transaction_count"] for r in records) != counts["publication_transaction_count"]:
        raise ValueError("Disclosure status transaction counts differ from the summary")
    for key in STATES:
        if dict(Counter(r[key] for r in records)) != counts[f"records_by_{key}"]:
            raise ValueError("Disclosure status state counts differ from the summary")


def attach_disclosure_status(bundle: Bundle, scopes: list[dict], *, transactions: list[dict],
                             provenance: dict, max_page_bytes: int = MAX_STATUS_BYTES) -> Bundle:
    """Add deterministic status files; require every prepared transaction to be accounted for."""
    if type(max_page_bytes) is not int or not 1024 <= max_page_bytes <= MAX_STATUS_BYTES:
        raise ValueError("Disclosure status page size budget is invalid")
    if bundle.manifest.get("is_demo") is not False:
        raise ValueError("Disclosure status is only supported for production publications")
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("Disclosure status source scopes are missing")
    board_content = bundle.files.get(f"board/{bundle.manifest['board']}.json")
    if board_content is None or digest(board_content) != bundle.manifest["board"]:
        raise ValueError("Disclosure status requires the hash-verified prepared board")
    board = json.loads(board_content)
    identity_fields = ("id", "source_id", "filing_id", "source_url")
    if (not isinstance(board.get("transactions"), list) or
            sorted(tuple(row.get(k) for k in identity_fields) for row in board["transactions"]) !=
            sorted(tuple(row.get(k) for k in identity_fields) for row in transactions)):
        raise ValueError("Disclosure status transaction bindings differ from the prepared board")
    tx_by_id = {r["id"]: r for r in transactions}
    if len(tx_by_id) != len(transactions):
        raise ValueError("Disclosure status publication input contains duplicate IDs")
    seen_transactions: set[str] = set()
    files = dict(bundle.files)
    sources = []
    for source in sorted(scopes, key=lambda s: s["scope_id"]):
        source = deepcopy(source)
        unmatched = _strings(source.pop("unmatched_publication_transaction_ids"), "unmatched transaction IDs")
        reports = source.pop("reports")
        if not isinstance(reports, list):
            raise ValueError("Disclosure status source reports must be an array")
        records = []
        report_ids = set()
        record_keys = set()
        published_count = 0
        for row in sorted(reports, key=lambda r: r["record_key"]):
            ids = _strings(row.pop("publication_transaction_ids"), "publication transaction IDs")
            holding_ids = row.pop("publication_holding_ids", None)
            if holding_ids is not None:
                row["publication_holding_count"] = len(_strings(holding_ids, "publication holding IDs"))
            for txid in ids:
                if (txid not in tx_by_id or tx_by_id[txid]["source_id"] != source["source_id"]
                        or txid in seen_transactions):
                    raise ValueError("Disclosure status publication binding is invalid or duplicated")
                seen_transactions.add(txid)
            if ids and row.get("publication_binding") != "verified":
                raise ValueError("Disclosure status cannot attach transactions to an unknown report binding")
            row["publication_transaction_count"] = len(ids)
            validate_record(row)
            if row["record_key"] in record_keys:
                raise ValueError("Disclosure status source contains duplicate record keys")
            record_keys.add(row["record_key"])
            if row["report_id"] is not None:
                if row["report_id"] in report_ids:
                    raise ValueError("Disclosure status source counts a report more than once")
                report_ids.add(row["report_id"])
            for key in ("reason_codes", "source_versions"):
                row[key] = sorted(row[key])
            records.append(row)
            published_count += len(ids)
        for txid in unmatched:
            if (txid not in tx_by_id or tx_by_id[txid]["source_id"] != source["source_id"]
                    or txid in seen_transactions):
                raise ValueError("Disclosure status unmatched transaction binding is invalid or duplicated")
            seen_transactions.add(txid)
        source["counts"] = {
            "record_count": len(records), "identified_report_count": len(report_ids),
            "unidentified_record_count": sum(r["report_id"] is None for r in records),
            "publication_transaction_count": published_count,
            "unmatched_publication_transaction_count": len(unmatched),
            **{f"records_by_{key}": dict(sorted(Counter(r[key] for r in records).items()))
               for key in STATES},
        }
        pages = []

        def save(rows: list[dict]) -> None:
            content = encode({"schema_version": PAGE_SCHEMA, "scope_id": source["scope_id"],
                              "source_id": source["source_id"], "records": rows})
            sha = digest(content)
            files[f"status/{sha}.json"] = content
            pages.append({"sha256": sha, "byte_length": len(content), "record_count": len(rows)})

        chunk: list[dict] = []
        empty_bytes = len(encode({"schema_version": PAGE_SCHEMA, "scope_id": source["scope_id"],
                                  "source_id": source["source_id"], "records": []}))
        chunk_bytes = empty_bytes
        for row in records:
            row_bytes = len(encode(row))
            if chunk_bytes + row_bytes + bool(chunk) > max_page_bytes:
                if not chunk:
                    raise ValueError("One disclosure status record exceeds the page size budget")
                save(chunk)
                chunk = []
                chunk_bytes = empty_bytes
                if chunk_bytes + row_bytes > max_page_bytes:
                    raise ValueError("One disclosure status record exceeds the page size budget")
            chunk_bytes += row_bytes + bool(chunk)
            chunk.append(row)
        if chunk:
            save(chunk)
        source["pages"] = pages
        source["limitations"] = sorted(_strings(source["limitations"], "limitations"))
        sources.append(source)
    if seen_transactions != set(tx_by_id):
        raise ValueError("Disclosure status source scopes do not account for every publication transaction")
    manifest = deepcopy(bundle.manifest)
    summary = {"schema_version": SCHEMA, "generated_at": manifest["generated_at"],
               "provenance": provenance,
               "publication": {"snapshot_id": manifest["snapshot_id"], "board_sha256": manifest["board"],
                               "data_cutoff_at": manifest["data_cutoff_at"]}, "sources": sources}
    validate_summary(summary, manifest)
    content = encode(summary)
    if len(content) > MAX_STATUS_BYTES:
        raise ValueError("Disclosure status summary exceeds its size budget")
    sha = digest(content)
    files[f"status/{sha}.json"] = content
    manifest["disclosure_status"] = {"schema_version": SCHEMA, "sha256": sha, "byte_length": len(content)}
    from .known_gaps import attach, from_scopes
    attach(manifest, files, from_scopes(scopes, board))
    files["manifest.json"] = encode(manifest)
    if len(files["manifest.json"]) > 16 * 1024:
        raise ValueError("Manifest with disclosure status exceeds its size budget")
    return Bundle(files, manifest)
