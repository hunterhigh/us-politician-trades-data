"""Recoverable, fail-closed planning for House PTR archive work."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from .codec import encode
from .house import HouseIndexError

SCHEMA = "house-ptr-checkpoint/v1"
RESULT_STATUSES = {"archived", "failed"}
SIGNATURE_FIELDS = ("source_id", "document_id", "filing_type", "filer_name", "state_district",
                    "filing_year", "filed_date", "document_url", "verification_status")


def _time(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise HouseIndexError(f"House PTR checkpoint requires {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HouseIndexError(f"House PTR checkpoint has invalid {field}") from None
    if parsed.tzinfo is None:
        raise HouseIndexError(f"House PTR checkpoint requires a timezone for {field}")
    return parsed.isoformat()


def _parsed_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _queued(state: dict, now: datetime) -> bool:
    if state["status"] == "pending":
        return True
    if state["status"] != "failed":
        return False
    retry_at = state.get("next_retry_at")
    return retry_at is None or _parsed_time(retry_at) <= now


def _queue(documents: dict[str, dict], now: datetime) -> list[str]:
    eligible = ((document_id, state) for document_id, state in documents.items()
                if _queued(state, now))
    return [document_id for document_id, _ in sorted(
        eligible, key=lambda item: (item[1].get("filed_date") or "", item[0]), reverse=True)]


def _signature(row: dict) -> str:
    payload = {field: row.get(field) for field in SIGNATURE_FIELDS}
    return hashlib.sha256(encode(payload)).hexdigest()


def _validate_discovery(discovery: dict) -> tuple[int, str, str, list[dict]]:
    metadata = discovery.get("metadata")
    filings = discovery.get("filings")
    if not isinstance(metadata, dict) or not isinstance(filings, list):
        raise HouseIndexError("House discovery document is invalid")
    year, index_sha = metadata.get("filing_year"), metadata.get("sha256")
    if type(year) is not int or not 2008 <= year <= 2100 or not isinstance(index_sha, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", index_sha):
        raise HouseIndexError("House discovery metadata is invalid")
    retrieved_at = _time(metadata.get("retrieved_at"), "index retrieved_at")
    rows: list[dict] = []
    seen: set[str] = set()
    for row in filings:
        if not isinstance(row, dict) or row.get("filing_type") != "P":
            continue
        document_id = row.get("document_id")
        if (row.get("source_id") != "house_clerk" or row.get("filing_year") != year or
                row.get("verification_status") != "official_raw_unparsed" or
                not isinstance(document_id, str) or not re.fullmatch(r"[0-9]{1,20}", document_id) or
                document_id in seen):
            raise HouseIndexError("House discovery contains an invalid PTR row")
        if any(field not in row for field in SIGNATURE_FIELDS):
            raise HouseIndexError("House discovery PTR row is incomplete")
        rows.append(row)
        seen.add(document_id)
    rows.sort(key=lambda row: (row.get("filed_date") or "", row["document_id"]))
    return year, index_sha, retrieved_at, rows


def _previous_documents(previous: dict | None, year: int) -> dict[str, dict]:
    if previous is None:
        return {}
    if previous.get("schema_version") != SCHEMA or previous.get("source_id") != "house_clerk" \
            or previous.get("filing_year") != year:
        raise HouseIndexError("House PTR checkpoint does not match this source year")
    documents = previous.get("documents")
    if not isinstance(documents, dict):
        raise HouseIndexError("House PTR checkpoint documents are invalid")
    for document_id, state in documents.items():
        if not re.fullmatch(r"[0-9]{1,20}", str(document_id)) or not isinstance(state, dict):
            raise HouseIndexError("House PTR checkpoint contains an invalid document state")
        if state.get("status") not in {"pending", "archived", "failed"}:
            raise HouseIndexError("House PTR checkpoint contains an invalid status")
        if not isinstance(state.get("signature"), str) or not re.fullmatch(r"[0-9a-f]{64}", state["signature"]):
            raise HouseIndexError("House PTR checkpoint contains an invalid signature")
        if type(state.get("attempts")) is not int or state["attempts"] < 0:
            raise HouseIndexError("House PTR checkpoint contains invalid attempts")
        if state.get("next_retry_at") is not None:
            _time(state["next_retry_at"], "next_retry_at")
    return documents


def _archived_versions(archive_root: Path, row: dict) -> tuple[list[dict], list[str]]:
    year, document_id = row["filing_year"], row["document_id"]
    folder = archive_root.resolve() / "house_clerk" / "documents" / str(year) / document_id
    if not folder.is_dir():
        return [], []
    versions: list[dict] = []
    mismatched: list[str] = []
    for metadata_path in sorted(folder.glob("*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        sha = metadata.get("sha256")
        pdf_path = folder / f"{sha}.pdf" if isinstance(sha, str) else None
        if (metadata.get("source_id") == "house_clerk" and metadata.get("document_id") == document_id and
                metadata.get("filing_year") == year and isinstance(sha, str) and
                re.fullmatch(r"[0-9a-f]{64}", sha) and metadata_path.stem == sha and
                pdf_path is not None and pdf_path.is_file()):
            stable_matches = metadata.get("source_url") == row.get("document_url") and all(
                metadata.get(field) == row.get(field) for field in
                ("filing_type", "filer_name", "state_district", "filed_date"))
            if stable_matches:
                versions.append(metadata)
            else:
                mismatched.append(sha)
    return versions, mismatched


def plan_checkpoint(discovery: dict, archive_root: Path, previous: dict | None = None, *,
                    planned_at: str | None = None) -> dict:
    year, index_sha, index_retrieved_at, rows = _validate_discovery(discovery)
    prior = _previous_documents(previous, year)
    planned_at = _time(planned_at or datetime.now(timezone.utc).isoformat(), "planned_at")
    planned_time = _parsed_time(planned_at)
    documents: dict[str, dict] = {}
    anomalies: list[dict] = []
    current_ids: set[str] = set()
    for row in rows:
        document_id = row["document_id"]
        current_ids.add(document_id)
        signature = _signature(row)
        old = prior.get(document_id)
        attempts = old.get("attempts", 0) if old else 0
        status = old.get("status", "pending") if old and old.get("signature") == signature else "pending"
        last_error = old.get("last_error") if status == "failed" else None
        next_retry_at = old.get("next_retry_at") if status == "failed" else None
        archive_sha256 = old.get("archive_sha256") if status == "archived" else None
        if old and old.get("signature") != signature:
            anomalies.append({"document_id": document_id, "kind": "official_index_row_changed",
                              "previous_signature": old.get("signature"), "current_signature": signature})
            attempts, last_error, next_retry_at, archive_sha256 = 0, None, None, None
        archived, mismatched = _archived_versions(archive_root, row)
        if len(archived) == 1:
            status, archive_sha256, last_error, next_retry_at = "archived", archived[0]["sha256"], None, None
        elif len(archived) > 1:
            status, archive_sha256 = "pending", None
            anomalies.append({"document_id": document_id, "kind": "multiple_archived_versions",
                              "archive_sha256": [item["sha256"] for item in archived]})
        elif status == "archived":
            status, archive_sha256 = "pending", None
            anomalies.append({"document_id": document_id, "kind": "archived_evidence_missing"})
        if mismatched:
            anomalies.append({"document_id": document_id, "kind": "archive_metadata_mismatch",
                              "archive_sha256": mismatched})
        documents[document_id] = {
            "signature": signature, "status": status, "attempts": attempts,
            "last_error": last_error, "next_retry_at": next_retry_at,
            "archive_sha256": archive_sha256,
            "filed_date": row.get("filed_date"), "filer_name": row.get("filer_name"),
            "document_url": row.get("document_url"),
        }
    for document_id in sorted(set(prior) - current_ids):
        anomalies.append({"document_id": document_id, "kind": "missing_from_latest_index",
                          "previous_signature": prior[document_id].get("signature")})
    counts = {status: sum(state["status"] == status for state in documents.values())
              for status in ("pending", "failed", "archived")}
    return {
        "schema_version": SCHEMA, "source_id": "house_clerk", "filing_year": year,
        "index_sha256": index_sha, "index_retrieved_at": index_retrieved_at,
        "planned_at": planned_at, "documents": documents, "anomalies": anomalies,
        "queue": _queue(documents, planned_time),
        "counts": counts,
    }


def record_result(checkpoint: dict, document_id: str, status: str, *, result_at: str | None = None,
                  archive_metadata: dict | None = None, error: str | None = None) -> dict:
    year = checkpoint.get("filing_year")
    if type(year) is not int or status not in RESULT_STATUSES:
        raise HouseIndexError("House PTR checkpoint result is invalid")
    documents = _previous_documents(checkpoint, year)
    if document_id not in documents:
        raise HouseIndexError("House PTR checkpoint does not contain this document")
    result_at = _time(result_at or datetime.now(timezone.utc).isoformat(), "result_at")
    result_time = _parsed_time(result_at)
    state = documents[document_id]
    if status == "archived":
        sha = archive_metadata.get("sha256") if isinstance(archive_metadata, dict) else None
        if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or
                archive_metadata.get("document_id") != document_id or
                archive_metadata.get("filing_year") != checkpoint.get("filing_year") or
                archive_metadata.get("source_id") != "house_clerk" or
                archive_metadata.get("source_url") != state.get("document_url") or
                archive_metadata.get("filer_name") != state.get("filer_name") or
                archive_metadata.get("filed_date") != state.get("filed_date")):
            raise HouseIndexError("House PTR archived result metadata is invalid")
        state.update(status="archived", archive_sha256=sha, last_error=None, next_retry_at=None,
                     last_result_at=result_at, attempts=state["attempts"] + 1)
    else:
        if not isinstance(error, str) or not error.strip() or len(error) > 500:
            raise HouseIndexError("House PTR failed result requires a bounded error")
        attempts = state["attempts"] + 1
        delay_minutes = min(15 * (2 ** min(attempts - 1, 7)), 24 * 60)
        retry_at = (result_time + timedelta(minutes=delay_minutes)).isoformat()
        state.update(status="failed", archive_sha256=None, last_error=error.strip(),
                     next_retry_at=retry_at, last_result_at=result_at, attempts=attempts)
    checkpoint["queue"] = _queue(documents, result_time)
    checkpoint["counts"] = {value: sum(item["status"] == value for item in documents.values())
                            for value in ("pending", "failed", "archived")}
    return checkpoint
