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
    materialize_oge_278t_observations,
    _observation_input_extraction,
    legacy_disposition_reference,
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
                         workflow_run_id: str | None = None,
                         saved_observations_path: str | Path | None = None,
                         saved_exhibit_observations_path: str | Path | None = None,
                         legacy_extractions_path: str | Path | None = None,
                         candidate_base_path: str | Path | None = None,
                         data_cutoff_at: str | None = None) -> dict[str, Any]:
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
    integration = saved_observations_path is not None or saved_exhibit_observations_path is not None
    if integration != (candidate_base_path is not None) or integration != (data_cutoff_at is not None):
        raise OgeShadowInputError("observed candidate run requires observations, base and cutoff together")
    saved = _read_json(Path(saved_observations_path)) if saved_observations_path is not None else {}
    exhibits = _read_json(Path(saved_exhibit_observations_path)) if saved_exhibit_observations_path is not None else {}
    if not isinstance(saved, dict):
        raise OgeShadowInputError("saved observation input must map document IDs to bound observations")
    if not isinstance(exhibits,dict) or set(exhibits)&set(saved):
        raise OgeShadowInputError("native exhibit observations must be a disjoint document mapping")
    if legacy_extractions_path is not None and not integration:
        raise OgeShadowInputError("legacy extraction input requires a candidate base")
    legacy = _read_json(Path(legacy_extractions_path)) if legacy_extractions_path is not None else {}
    if not isinstance(legacy, dict):
        raise OgeShadowInputError("legacy extraction input must map document IDs to extractions")
    candidate_base = _read_json(Path(candidate_base_path)) if integration else None
    original_extractions = []
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
    if set(legacy) - set(by_id):
        raise OgeShadowInputError("legacy extraction documents are outside the catalog")
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
    if saved_observations_path is not None:
        input_hashes.append({"artifact_type":"saved_observations","sha256":_sha256(Path(saved_observations_path))})
    if saved_exhibit_observations_path is not None:
        input_hashes.append({"artifact_type":"saved_native_exhibit_observations","sha256":_sha256(Path(saved_exhibit_observations_path))})
    if integration:
        input_hashes.append({"artifact_type":"candidate_base","sha256":_sha256(Path(candidate_base_path))})
    if legacy_extractions_path is not None:
        input_hashes.append({"artifact_type": "legacy_extractions", "sha256": _sha256(Path(legacy_extractions_path))})
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
            source_rows_reference = None
            expected_files.add(extraction_path.name)
            extraction = _read_json(extraction_path)
            if (not isinstance(extraction, dict) or
                    extraction.get("document_id") != document_id or
                    extraction.get("source_sha256") != source_sha):
                raise OgeShadowInputError(f"{document_id}: extraction differs from archived document")
            if document_id in saved or document_id in exhibits:
                extraction = _observation_input_extraction(extraction)
                if "source_rows" not in extraction:
                    source_rows_reference = legacy_disposition_reference(extraction, record)
            if not isinstance(extraction.get("source_rows"), list) and source_rows_reference is None and document_id not in exhibits:
                if integration:
                    raise OgeShadowInputError(f"{document_id}: observed integration requires saved source_rows; no automatic reparse")
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
            original_extractions.append(extraction)
            try:
                from unison_snapshot.oge_reports import GENERIC_PARSER_VERSION, GENERIC_ROW_INTERPRETATION_MODE
                scoped_formal = None
                if integration and extraction.get("parser_version") == GENERIC_PARSER_VERSION:
                    from unison_snapshot.oge_candidate import _inherit_generic_legacy
                    formal = [row for row in candidate_base.get("transactions", [])
                              if row.get("source_id") == "oge" and row.get("filing_id") == document_id
                              and row.get("verification_status") == "official_matched"]
                    if extraction.get("row_interpretation_mode") == GENERIC_ROW_INTERPRETATION_MODE:
                        scoped_formal = formal
                    else:
                        extraction, _ = _inherit_generic_legacy(extraction, legacy.get(document_id), formal, record)
                if document_id in saved:
                    formal_ids = [row["id"] for row in candidate_base.get("transactions", [])
                                  if row.get("source_id") == "oge" and row.get("filing_id") == document_id
                                  and row.get("source_url") == record["document_url"]
                                  and row.get("verification_status") == "official_matched"]
                    extraction = materialize_oge_278t_observations(extraction, record,
                        formal_reference_ids=formal_ids, **saved[document_id])
                if document_id in exhibits:
                    from unison_snapshot.oge_reports import materialize_saved_oge_278t_exhibits
                    extraction = materialize_saved_oge_278t_exhibits(extraction,record,**exhibits[document_id])
                scope_binding = ({"legacy_extraction": legacy.get(document_id),
                                  "formal_reference_facts": scoped_formal}
                                 if scoped_formal is not None and extraction.get("parser_version") == GENERIC_PARSER_VERSION
                                 else {})
                adapted = adapt_oge_278t_extraction(extraction, record, run_id=run_id, **scope_binding)
            except Exception as exc:
                raise OgeShadowInputError(
                    f"{document_id}: source-row ledger adaptation failed: {exc}") from exc
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
                          **({"source_rows_status": "saved_observation_rows_with_legacy_disposition_reference",
                              "legacy_reference_sha256": source_rows_reference["source_extraction_sha256"],
                              "legacy_normalized_reference_count": source_rows_reference["normalized_transaction_count"],
                              "legacy_quarantined_reference_count": source_rows_reference["quarantined_reference_count"],
                              "legacy_physical_inventory_status": "not_saved"}
                             if not failed_by_id.get(document_id) and source_rows_reference else {}),
                          **({"reason": reason} if reason else {})})
    if set(failed_by_id) - seen_ids or available != expected_files:
        raise OgeShadowInputError("OGE failure or extraction files do not match archived report inventory")
    from unison_snapshot.oge_native_replacements import prepare_native_exhibit_applications
    native_views = prepare_native_exhibit_applications(original_extractions, by_id, exhibits,
        candidate_base.get("transactions", []) if integration else [], data_cutoff_at)
    if native_views:
        rows = [row for row in rows if row["source"]["document_id"] not in native_views]
        for document_id, view in sorted(native_views.items()):
            adapted = adapt_oge_278t_extraction(view, by_id[document_id], run_id=run_id)
            rows.extend(adapted["rows"])
            document = next(row for row in documents if row["document_id"] == document_id)
            document.update(rules_version=adapted["rules_version"], idempotency_key=adapted["idempotency_key"])

    row_bytes = (json.dumps(rows, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n").encode("utf-8")
    artifacts = {"candidate_rows.json": row_bytes}
    if integration:
        from unison_snapshot.oge_candidate import build_oge_candidate
        from unison_snapshot.builder import build
        candidate, audit = build_oge_candidate(
            catalog, original_extractions, candidate_base,
            data_cutoff_at=data_cutoff_at, saved_observations_by_document=saved,
            legacy_extractions_by_document=legacy,saved_exhibit_observations_by_document=exhibits)
        for name, value in (("candidate.json", candidate), ("candidate_audit.json", audit)):
            artifacts[name] = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        # Exercise the existing final builder locally; no publication or refs.
        try:
            bundle = build(candidate, generated_at=completed_at, allow_production=True,
                           allow_empty_production=True)
        except ValueError as exc:
            raise OgeShadowInputError(f"observed final snapshot construction failed: {exc}") from exc
        artifacts.update({"snapshot/" + name: value for name, value in bundle.files.items()})
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
        "outputs": [{"artifact_type": name.removesuffix(".json"), "path": name,
                     "sha256": hashlib.sha256(value).hexdigest()} for name, value in artifacts.items()],
        "input_artifacts": input_hashes,
        **({"workflow_run_id": workflow_run_id} if workflow_run_id else {}),
    }
    validate_run_manifest(manifest)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise OgeShadowInputError("shadow output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
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
