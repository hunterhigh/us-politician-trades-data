"""Emit a fixed-input OGE 278-T shadow run from existing source artifacts."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from unison_snapshot.oge_reports import collapse_direct_catalog_records
from unison_snapshot.oge_transaction_adapter import (
    PARSER_ID, RULES_VERSION, adapt_oge_278t_extraction,
)
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.pipeline_ledger import (
    RUN_MANIFEST_SCHEMA, idempotency_key, validate_run_manifest,
)


class OgeShadowInputError(ValueError):
    """An OGE source artifact cannot be bound to this shadow run."""


_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_DOCUMENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_LAYOUT = hashlib.sha256(b"oge-278t-six-column-table/v1").hexdigest()


def build_oge_shadow_run(*, catalog_path: str | Path,
                         archive_batch_path: str | Path,
                         extraction_batch_path: str | Path,
                         extractions_dir: str | Path,
                         evidence_root: str | Path | None = None,
                         output_dir: str | Path,
                         run_id: str, code_commit: str, evidence_commit: str,
                         started_at: str, completed_at: str,
                         trigger: str = "local",
                         review_commit: str | None = None,
                         workflow_run_id: str | None = None) -> dict[str, Any]:
    """Build a row ledger and manifest; never fetch sources or write Git refs."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise OgeShadowInputError("run_id is required")
    commit_values = [("code_commit", code_commit), ("evidence_commit", evidence_commit)]
    if review_commit is not None:
        commit_values.append(("review_commit", review_commit))
    for name, value in commit_values:
        if not isinstance(value, str) or not _SHA40.fullmatch(value):
            raise OgeShadowInputError(f"{name} must be a lowercase full Git SHA")
    evidence_path_root = Path(evidence_root).resolve() if evidence_root is not None else None
    if evidence_path_root is not None:
        try:
            evidence_head = subprocess.run(
                ["git", "-C", str(evidence_path_root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True).stdout.strip()
            evidence_dirty = subprocess.run(
                ["git", "-C", str(evidence_path_root), "status", "--porcelain"],
                check=True, capture_output=True, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise OgeShadowInputError("pinned OGE evidence checkout is unavailable") from exc
        if evidence_head != evidence_commit or evidence_dirty:
            raise OgeShadowInputError("OGE evidence checkout is not the clean pinned commit")
    input_paths = {
        "catalog": Path(catalog_path),
        "archive_batch": Path(archive_batch_path),
        "extraction_batch": Path(extraction_batch_path),
    }
    values = {name: _read_json(path) for name, path in input_paths.items()}
    catalog, archive_batch, extraction_batch = (
        values["catalog"], values["archive_batch"], values["extraction_batch"])
    if not all(isinstance(item, dict) for item in values.values()):
        raise OgeShadowInputError("OGE source artifacts must be JSON objects")
    if (archive_batch.get("schema_version") != "oge-278t-archive-batch/v1" or
            extraction_batch.get("schema_version") != "oge-278t-extraction-batch/v1"):
        raise OgeShadowInputError("OGE source batch schema is unsupported")
    catalog_rows = catalog.get("transactions")
    if not isinstance(catalog_rows, list):
        raise OgeShadowInputError("OGE catalog transactions are missing")
    direct, _ = collapse_direct_catalog_records(
        [row for row in catalog_rows if isinstance(row, dict) and
         row.get("access_method") == "direct_pdf"])
    by_id = {record["source_document_id"]: record for record in direct}
    reports = archive_batch.get("reports")
    failures = extraction_batch.get("failures")
    if not isinstance(reports, list) or not isinstance(failures, list):
        raise OgeShadowInputError("OGE report inventory or extraction failures are missing")
    count_fields = (archive_batch.get("catalog_direct_count"),
                    archive_batch.get("pending_count"),
                    archive_batch.get("attempted_count"),
                    archive_batch.get("archived_count"),
                    archive_batch.get("failure_count"),
                    extraction_batch.get("report_count"),
                    extraction_batch.get("extraction_count"),
                    extraction_batch.get("failure_count"))
    if any(type(value) is not int or value < 0 for value in count_fields):
        raise OgeShadowInputError("OGE source batch counts are missing or invalid")
    if (archive_batch.get("catalog_direct_count") != len(direct) or
            archive_batch.get("pending_count") != len(direct) - len(reports) or
            archive_batch.get("attempted_count") !=
            archive_batch.get("archived_count") + archive_batch.get("failure_count") or
            extraction_batch.get("report_count") != len(reports) or
            extraction_batch.get("extraction_count") + extraction_batch.get("failure_count") != len(reports) or
            extraction_batch.get("failure_count") != len(failures)):
        raise OgeShadowInputError("OGE source batch counts do not conserve documents")
    archive_failures = archive_batch.get("failures")
    if (not isinstance(archive_failures, list) or
            len(archive_failures) != archive_batch["failure_count"] or
            any(not isinstance(item, dict) or
                item.get("document_id") not in by_id or
                not isinstance(item.get("error"), str)
                for item in archive_failures)):
        raise OgeShadowInputError("OGE archive failure inventory is invalid")
    failed_by_id: dict[str, str] = {}
    for failure in failures:
        if not isinstance(failure, dict) or not isinstance(failure.get("error"), str):
            raise OgeShadowInputError("OGE extraction failure entry is malformed")
        document_id = failure.get("document_id")
        if document_id in failed_by_id:
            raise OgeShadowInputError("OGE extraction failure document is duplicated")
        failed_by_id[document_id] = failure["error"]

    extraction_root = Path(extractions_dir).resolve()
    available = {path.name for path in extraction_root.glob("*.json")}
    expected_files: set[str] = set()
    documents: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    input_hashes = [{"artifact_type": name, "sha256": _sha256(path)}
                    for name, path in input_paths.items()]
    seen_ids: set[str] = set()
    for metadata in sorted(reports, key=lambda item: item.get("document_id", "")
                           if isinstance(item, dict) else ""):
        if not isinstance(metadata, dict):
            raise OgeShadowInputError("OGE archive report metadata is malformed")
        document_id = metadata.get("document_id")
        source_sha = metadata.get("sha256")
        if (not isinstance(document_id, str) or not _DOCUMENT_ID.fullmatch(document_id) or
                document_id in seen_ids or document_id not in by_id or
                not isinstance(source_sha, str) or not _SHA64.fullmatch(source_sha)):
            raise OgeShadowInputError("OGE archived document ID or SHA is invalid, repeated, or absent from catalog")
        seen_ids.add(document_id)
        record = by_id[document_id]
        evidence_path = f"oge/reports/{document_id}/{source_sha}.pdf"
        if (metadata.get("archive_path") != evidence_path or
                type(metadata.get("byte_length")) is not int or
                metadata["byte_length"] <= 0):
            raise OgeShadowInputError(f"{document_id}: archive path or byte length is invalid")
        for metadata_name, catalog_name in (
                ("document_url", "document_url"), ("filer_name", "filer_name"),
                ("agency", "agency"), ("position_title", "position_title"),
                ("catalog_added_date", "catalog_added_date"),
                ("amended_label", "amended_label"),
                ("pending_final_oge_disposition", "pending_final_oge_disposition")):
            expected_value = (record.get(catalog_name) is True
                              if catalog_name == "pending_final_oge_disposition"
                              else record.get(catalog_name))
            if metadata.get(metadata_name) != expected_value:
                raise OgeShadowInputError(
                    f"{document_id}: archive {metadata_name} differs from catalog")
        extraction_path = extraction_root / f"{document_id}.json"
        if document_id in failed_by_id:
            if extraction_path.exists():
                raise OgeShadowInputError(f"{document_id}: failed extraction also has a result file")
            # A failed extraction has no trustworthy parser-version output.
            parser_version = "unavailable:extraction_failed"
            disposition = "failed"
            reason = "extraction_failed"
        else:
            source_rows_recovered = False
            expected_files.add(extraction_path.name)
            extraction = _read_json(extraction_path)
            if (not isinstance(extraction, dict) or
                    extraction.get("document_id") != document_id or
                    extraction.get("source_sha256") != source_sha):
                raise OgeShadowInputError(f"{document_id}: extraction differs from archived document")
            if not isinstance(extraction.get("source_rows"), list):
                if evidence_path_root is None:
                    raise OgeShadowInputError(
                        f"{document_id}: source_rows missing and pinned evidence is unavailable")
                metadata_path = (evidence_path_root / Path(evidence_path).with_suffix(".json")).resolve()
                archive_root = evidence_path_root
                if not metadata_path.is_file() or not metadata_path.is_relative_to(archive_root):
                    raise OgeShadowInputError(
                        f"{document_id}: archived PDF metadata is absent from pinned evidence")
                try:
                    reparsed = parse_archived_pdf(archive_root, metadata_path)
                    _assert_legacy_reparse_equivalent(extraction, reparsed)
                except Exception as exc:
                    raise OgeShadowInputError(
                        f"{document_id}: fixed-evidence reparse was not equivalent: {exc}") from exc
                extraction = reparsed
                source_rows_recovered = True
                input_hashes.append({"artifact_type": "archived_pdf_reparse",
                                     "document_id": document_id,
                                     "source_sha256": source_sha,
                                     "artifact_type_detail": "archive_metadata",
                                     "sha256": _sha256(metadata_path)})
            adapted = adapt_oge_278t_extraction(extraction, record, run_id=run_id)
            parser_version = adapted["parser_version"]
            disposition = "parsed" if adapted["row_count"] else "no_rows"
            reason = None
            rows.extend(adapted["rows"])
            input_hashes.append({"artifact_type": "extraction", "document_id": document_id,
                                 "sha256": _sha256(extraction_path)})
        key = idempotency_key(source_id="oge", source_sha256=source_sha,
                              parser_id=PARSER_ID, parser_version=parser_version,
                              rules_version=RULES_VERSION, layout_fingerprint=_LAYOUT)
        documents.append({"source_id": "oge", "document_id": document_id,
                          "source_url": record["document_url"], "source_sha256": source_sha,
                          "evidence_path": evidence_path,
                          "source_byte_length": metadata["byte_length"],
                          "parser_id": PARSER_ID, "parser_version": parser_version,
                          "rules_version": RULES_VERSION, "layout_fingerprint": _LAYOUT,
                          "idempotency_key": key, "disposition": disposition,
                          **({"source_rows_status": "recovered_from_pinned_evidence_after_equivalence_check"}
                             if not failed_by_id.get(document_id) and source_rows_recovered else {}),
                          **({"reason": reason} if reason else {})})
    if set(failed_by_id) - seen_ids or available != expected_files:
        raise OgeShadowInputError("OGE failure or extraction files do not match archived report inventory")

    row_bytes = (json.dumps(rows, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n").encode("utf-8")
    row_counts = Counter(row["disposition"] for row in rows)
    doc_counts = Counter(doc["disposition"] for doc in documents)
    manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id,
        "workflow": "oge-direct-278t-shadow", "trigger": trigger,
        "code_commit": code_commit, "evidence_commit": evidence_commit,
        **({"review_commit": review_commit} if review_commit else {}),
        "started_at": started_at, "completed_at": completed_at,
        "source_scope": ["oge"], "documents": documents,
        "counts": {
            "discovered_documents": len(direct), "archived_documents": len(reports),
            "parsed_documents": doc_counts["parsed"],
            "failed_documents": doc_counts["failed"],
            "no_row_documents": doc_counts["no_rows"],
            "excluded_documents": 0,
            "qualified_rows": row_counts["qualified"],
            "quarantined_rows": row_counts["quarantined"],
            "excluded_rows": row_counts["excluded"],
            "unrecognized_rows": row_counts["unrecognized"],
        },
        "accounted_rows": len(rows),
        "outputs": [{"artifact_type": "candidate_rows", "path": "candidate_rows.json",
                     "sha256": hashlib.sha256(row_bytes).hexdigest()}],
        "input_artifacts": input_hashes,
        **({"workflow_run_id": workflow_run_id} if workflow_run_id else {}),
    }
    validate_run_manifest(manifest)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise OgeShadowInputError("shadow output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    (output / "candidate_rows.json").write_bytes(row_bytes)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    return manifest


def _assert_legacy_reparse_equivalent(cached: dict, reparsed: dict) -> None:
    """Allow only parser-added physical inventory and cells absent in old caches."""
    if not isinstance(cached, dict) or not isinstance(reparsed, dict):
        raise OgeShadowInputError("cached and reparsed extraction must be objects")
    if "source_rows" in cached or not isinstance(reparsed.get("source_rows"), list):
        raise OgeShadowInputError("legacy cache/reparse source_rows contract is invalid")

    def compare(old: Any, new: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(old, dict) and isinstance(new, dict):
            if set(old) - set(new):
                raise OgeShadowInputError(f"reparse removed fields at {'.'.join(path)}")
            for key, value in old.items():
                compare(value, new[key], path + (key,))
            extras = set(new) - set(old)
            row_list = path == ("transactions",)
            row_item = (len(path) == 2 and path[0] == "transactions"
                        and path[1].isdecimal())
            allowed = ((row_list or row_item) and extras <= {"cells"}) or (
                       (path == () and extras <= {"source_rows"}))
            if extras and not allowed:
                raise OgeShadowInputError(f"reparse added unexpected fields at {'.'.join(path)}")
            return
        if isinstance(old, list) and isinstance(new, list):
            if len(old) != len(new):
                raise OgeShadowInputError(f"reparse changed row count at {'.'.join(path)}")
            for index, (before, after) in enumerate(zip(old, new, strict=True)):
                compare(before, after, path + (str(index),))
            return
        if old != new or type(old) is not type(new):
            raise OgeShadowInputError(f"reparse changed cached value at {'.'.join(path)}")

    compare(cached, reparsed)
    if not reparsed["source_rows"]:
        raise OgeShadowInputError("fixed-evidence PDF produced no physical source rows")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OgeShadowInputError(f"invalid or unavailable OGE artifact {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
