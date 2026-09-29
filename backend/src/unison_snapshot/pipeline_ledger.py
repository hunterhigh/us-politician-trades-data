"""Versioned evidence observations and run manifests for shadow pipeline work.

This module is deliberately independent of source adapters and production
publication.  It validates immutable observations before adapters persist them
in an isolated review path.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any


OBSERVATION_SCHEMA = "pipeline-field-observation/v1"
CANDIDATE_ROW_SCHEMA = "pipeline-candidate-row/v1"
RUN_MANIFEST_SCHEMA = "pipeline-run-manifest/v1"
ROW_DISPOSITIONS = {"qualified", "quarantined", "excluded", "unrecognized"}
DOCUMENT_DISPOSITIONS = {"parsed", "failed", "no_rows", "excluded"}
OBSERVATION_METHODS = {"embedded_text", "ocr", "geometry", "derived"}
COORDINATE_UNITS = {"pdf_points", "pixels", "normalized"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class LedgerValidationError(ValueError):
    """A ledger record is incomplete, internally inconsistent, or invalid."""


def idempotency_key(*, source_id: str, source_sha256: str, parser_id: str,
                    parser_version: str, rules_version: str,
                    layout_fingerprint: str) -> str:
    """Return the stable key for one source document/parser/rules tuple."""

    payload = {
        "schema_version": CANDIDATE_ROW_SCHEMA,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "parser_id": parser_id,
        "parser_version": parser_version,
        "rules_version": rules_version,
        "layout_fingerprint": layout_fingerprint,
    }
    _nonempty(source_id, "source_id")
    _digest(source_sha256, "source_sha256")
    _nonempty(parser_id, "parser_id")
    _nonempty(parser_version, "parser_version")
    _nonempty(rules_version, "rules_version")
    _nonempty(layout_fingerprint, "layout_fingerprint")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "pipeline:v1:" + hashlib.sha256(encoded).hexdigest()


def validate_observation(value: object) -> dict[str, Any]:
    """Validate one field observation and return it unchanged."""

    item = _object(value, "observation")
    _schema(item, OBSERVATION_SCHEMA, "observation")
    _nonempty(item.get("observation_id"), "observation_id")
    _nonempty(item.get("field"), "field")
    _json_scalar(item.get("raw_value"), "raw_value")
    _json_scalar(item.get("normalized_value"), "normalized_value")
    if item.get("method") not in OBSERVATION_METHODS:
        raise LedgerValidationError("observation method is unsupported")
    confidence = item.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float) or
                                   not 0 <= confidence <= 1):
        raise LedgerValidationError("observation confidence must be between 0 and 1")
    if type(item.get("selected")) is not bool:
        raise LedgerValidationError("observation selected must be a boolean")
    if type(item.get("required_for_projection")) is not bool:
        raise LedgerValidationError("observation required_for_projection must be a boolean")
    locations = item.get("locations")
    if not isinstance(locations, list) or not locations:
        raise LedgerValidationError("observation must identify at least one evidence location")
    for location in locations:
        _validate_location(location)
    conditions = item.get("conditions", {})
    if not isinstance(conditions, dict) or not all(
            isinstance(key, str) and key for key in conditions):
        raise LedgerValidationError("observation conditions must be a string-keyed object")
    _json_value(conditions, "observation conditions")
    return item


def validate_candidate_row(value: object) -> dict[str, Any]:
    """Validate an extracted row without imposing source-specific field rules."""

    item = _object(value, "candidate row")
    _schema(item, CANDIDATE_ROW_SCHEMA, "candidate row")
    _nonempty(item.get("candidate_id"), "candidate_id")
    _nonempty(item.get("run_id"), "run_id")
    source = _object(item.get("source"), "source")
    _nonempty(source.get("source_id"), "source.source_id")
    _nonempty(source.get("document_id"), "source.document_id")
    _nonempty(source.get("source_url"), "source.source_url")
    _digest(source.get("source_sha256"), "source.source_sha256")
    parser = _object(item.get("parser"), "parser")
    for key in ("parser_id", "parser_version", "rules_version", "layout_fingerprint"):
        _nonempty(parser.get(key), f"parser.{key}")
    expected_key = idempotency_key(
        source_id=source["source_id"], source_sha256=source["source_sha256"],
        parser_id=parser["parser_id"], parser_version=parser["parser_version"],
        rules_version=parser["rules_version"],
        layout_fingerprint=parser["layout_fingerprint"],
    )
    if item.get("idempotency_key") != expected_key:
        raise LedgerValidationError("candidate row idempotency_key does not match its inputs")
    if item.get("disposition") not in ROW_DISPOSITIONS:
        raise LedgerValidationError("candidate row disposition is unsupported")
    reasons = item.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) and reason for reason in reasons):
        raise LedgerValidationError("candidate row reasons must be a list of nonempty strings")
    disposition = item["disposition"]
    if disposition == "qualified" and reasons:
        raise LedgerValidationError("qualified row cannot have blocking disposition reasons")
    if disposition in {"quarantined", "excluded", "unrecognized"} and not reasons:
        raise LedgerValidationError("non-qualified row must include a machine-readable reason")
    if disposition == "qualified" and item.get("unresolved_conflicts"):
        raise LedgerValidationError("qualified row cannot retain unresolved field conflicts")
    conflicts = item.get("unresolved_conflicts", [])
    if not isinstance(conflicts, list) or not all(isinstance(field, str) and field for field in conflicts):
        raise LedgerValidationError("unresolved_conflicts must be a list of field names")
    observations = item.get("observations")
    if not isinstance(observations, list) or not observations:
        raise LedgerValidationError("candidate row must contain field observations")
    checked = [validate_observation(observation) for observation in observations]
    observation_ids: set[str] = set()
    selected_fields: set[str] = set()
    for observation in checked:
        if observation["observation_id"] in observation_ids:
            raise LedgerValidationError("candidate row contains a duplicate observation_id")
        observation_ids.add(observation["observation_id"])
        if observation["selected"]:
            if observation["field"] in selected_fields:
                raise LedgerValidationError("a candidate row can select only one value per field")
            selected_fields.add(observation["field"])
    evidence = item.get("evidence_locations")
    if not isinstance(evidence, list) or not evidence:
        raise LedgerValidationError("candidate row must identify its source location")
    for location in evidence:
        _validate_location(location)
    required_fields = item.get("required_projection_fields")
    if not isinstance(required_fields, list) or not all(
            isinstance(field, str) and field for field in required_fields):
        raise LedgerValidationError("required_projection_fields must be a list of field names")
    if disposition == "qualified":
        for field in required_fields:
            selected = [observation for observation in checked
                        if observation["field"] == field and observation["selected"]]
            if (len(selected) != 1 or selected[0]["normalized_value"] in (None, "") or
                    not selected[0]["required_for_projection"]):
                raise LedgerValidationError(
                    f"qualified row must have one normalized observation for {field}")
    return item


def validate_run_manifest(value: object) -> dict[str, Any]:
    """Validate run provenance, per-document keys, and row accounting."""

    item = _object(value, "run manifest")
    _schema(item, RUN_MANIFEST_SCHEMA, "run manifest")
    _nonempty(item.get("run_id"), "run_id")
    _nonempty(item.get("workflow"), "workflow")
    if not _HEX40.fullmatch(str(item.get("code_commit", ""))):
        raise LedgerValidationError("code_commit must be a full 40-character Git SHA")
    for name in ("evidence_commit", "review_commit", "state_commit", "market_commit"):
        if name in item and not _HEX40.fullmatch(str(item[name])):
            raise LedgerValidationError(f"{name} must be a full 40-character Git SHA")
    trigger = item.get("trigger")
    if trigger not in {"schedule", "workflow_run", "workflow_dispatch", "replay", "local"}:
        raise LedgerValidationError("run trigger is unsupported")
    _timestamp(item.get("started_at"), "started_at")
    completed_at = item.get("completed_at")
    if completed_at is not None:
        _timestamp(completed_at, "completed_at")
        if datetime.fromisoformat(completed_at.replace("Z", "+00:00")) < datetime.fromisoformat(
                item["started_at"].replace("Z", "+00:00")):
            raise LedgerValidationError("completed_at cannot precede started_at")
    scope = item.get("source_scope")
    if not isinstance(scope, list) or not scope or not all(isinstance(source, str) and source for source in scope):
        raise LedgerValidationError("source_scope must list at least one source id")
    documents = item.get("documents")
    if not isinstance(documents, list):
        raise LedgerValidationError("documents must be a list")
    for document in documents:
        _validate_document_record(document)
        if document["source_id"] not in scope:
            raise LedgerValidationError("document source_id must be included in source_scope")
    counts = _object(item.get("counts"), "counts")
    count_names = ("discovered_documents", "archived_documents", "parsed_documents",
                   "failed_documents", "no_row_documents", "excluded_documents",
                   "qualified_rows", "quarantined_rows", "excluded_rows", "unrecognized_rows")
    for name in count_names:
        if type(counts.get(name)) is not int or counts[name] < 0:
            raise LedgerValidationError(f"counts.{name} must be a nonnegative integer")
    document_counts = {
        "parsed_documents": DOCUMENT_DISPOSITIONS.intersection({"parsed"}),
        "failed_documents": DOCUMENT_DISPOSITIONS.intersection({"failed"}),
        "no_row_documents": DOCUMENT_DISPOSITIONS.intersection({"no_rows"}),
        "excluded_documents": DOCUMENT_DISPOSITIONS.intersection({"excluded"}),
    }
    unique_documents: set[tuple[str, str, str]] = set()
    idempotency_keys: set[str] = set()
    for document in documents:
        unique_documents.add((document["source_id"], document["document_id"],
                              document["source_sha256"]))
        if document["idempotency_key"] in idempotency_keys:
            raise LedgerValidationError("run manifest contains a duplicate document idempotency key")
        idempotency_keys.add(document["idempotency_key"])
    if counts["discovered_documents"] < len(unique_documents):
        raise LedgerValidationError("discovered_documents cannot be less than unique listed documents")
    for count_name, disposition in document_counts.items():
        actual = sum(document["disposition"] in disposition for document in documents)
        if actual != counts[count_name]:
            raise LedgerValidationError(f"{count_name} does not match document dispositions")
    if counts["archived_documents"] > counts["discovered_documents"]:
        raise LedgerValidationError("archived_documents cannot exceed discovered_documents")
    outputs = item.get("outputs")
    if not isinstance(outputs, list):
        raise LedgerValidationError("outputs must be a list")
    output_paths: set[str] = set()
    for output in outputs:
        output_item = _object(output, "output")
        _nonempty(output_item.get("artifact_type"), "output.artifact_type")
        normalized_path = _relative_artifact_path(output_item.get("path"), "output.path")
        if normalized_path in output_paths:
            raise LedgerValidationError("run manifest contains a duplicate output path")
        output_paths.add(normalized_path)
        _digest(output_item.get("sha256"), "output.sha256")
    inputs = item.get("input_artifacts", [])
    if not isinstance(inputs, list):
        raise LedgerValidationError("input_artifacts must be a list")
    for source in inputs:
        source_item = _object(source, "input artifact")
        _nonempty(source_item.get("artifact_type"), "input artifact.artifact_type")
        _digest(source_item.get("sha256"), "input artifact.sha256")
    source_runs = item.get("source_runs", [])
    if not isinstance(source_runs, list):
        raise LedgerValidationError("source_runs must be a list")
    for source in source_runs:
        source_item = _object(source, "source run")
        _nonempty(source_item.get("run_id"), "source run.run_id")
        _nonempty(source_item.get("workflow"), "source run.workflow")
        path = _relative_artifact_path(source_item.get("manifest_path"),
                                       "source run.manifest_path")
        _digest(source_item.get("manifest_sha256"), "source run.manifest_sha256")
        if path not in output_paths:
            raise LedgerValidationError("source run manifest path must be a listed output")
        for name in ("evidence_commit", "review_commit"):
            if name in source_item and not _HEX40.fullmatch(str(source_item[name])):
                raise LedgerValidationError(f"source run {name} must be a full Git SHA")
    total_rows = sum(counts[name] for name in ("qualified_rows", "quarantined_rows",
                                               "excluded_rows", "unrecognized_rows"))
    if type(item.get("accounted_rows")) is not int or item["accounted_rows"] != total_rows:
        raise LedgerValidationError("accounted_rows must equal all row dispositions")
    return item


def _relative_artifact_path(value: object, name: str) -> str:
    _nonempty(value, name)
    normalized = value.replace("\\", "/")
    if (normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or
            any(segment in {"", ".", ".."} for segment in normalized.split("/"))):
        raise LedgerValidationError(f"{name} must stay inside the run artifact root")
    return normalized


def _validate_document_record(value: object) -> None:
    document = _object(value, "document")
    for key in ("source_id", "document_id", "source_url", "parser_id", "parser_version",
                "rules_version", "layout_fingerprint"):
        _nonempty(document.get(key), f"document.{key}")
    _digest(document.get("source_sha256"), "document.source_sha256")
    expected = idempotency_key(
        source_id=document["source_id"], source_sha256=document["source_sha256"],
        parser_id=document["parser_id"], parser_version=document["parser_version"],
        rules_version=document["rules_version"], layout_fingerprint=document["layout_fingerprint"],
    )
    if document.get("idempotency_key") != expected:
        raise LedgerValidationError("document idempotency_key does not match its inputs")
    if document.get("disposition") not in DOCUMENT_DISPOSITIONS:
        raise LedgerValidationError("document disposition is unsupported")
    if document["disposition"] in {"failed", "excluded"}:
        _nonempty(document.get("reason"), "document.reason")


def _validate_location(value: object) -> None:
    location = _object(value, "evidence location")
    if type(location.get("page")) is not int or location["page"] < 1:
        raise LedgerValidationError("evidence location page must be a positive integer")
    row_locator = location.get("row_locator")
    if row_locator is not None and (not isinstance(row_locator, str) or not row_locator.strip()):
        raise LedgerValidationError("row_locator must be nonempty text when provided")
    box = location.get("bbox")
    if box is not None:
        if not isinstance(box, dict) or box.get("unit") not in COORDINATE_UNITS:
            raise LedgerValidationError("bbox must declare a supported coordinate unit")
        coords = [box.get(key) for key in ("x0", "y0", "x1", "y1")]
        if any(type(coord) not in (int, float) for coord in coords):
            raise LedgerValidationError("bbox coordinates must be numeric")
        x0, y0, x1, y1 = coords
        if not (x0 >= 0 and y0 >= 0 and x1 > x0 and y1 > y0):
            raise LedgerValidationError("bbox coordinates must have positive area")
        if box["unit"] == "normalized":
            if x1 > 1 or y1 > 1:
                raise LedgerValidationError("normalized bbox coordinates must be at most 1")
        else:
            for key in ("page_width", "page_height"):
                if type(box.get(key)) not in (int, float) or box[key] <= 0:
                    raise LedgerValidationError(f"bbox {key} is required and must be positive")
            if x1 > box["page_width"] or y1 > box["page_height"]:
                raise LedgerValidationError("bbox must fit within its declared page dimensions")
    if not isinstance(location.get("kind"), str) or not location["kind"]:
        raise LedgerValidationError("evidence location kind is required")


def _schema(item: dict[str, Any], expected: str, label: str) -> None:
    if item.get("schema_version") != expected:
        raise LedgerValidationError(f"{label} schema_version must be {expected}")


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LedgerValidationError(f"{label} must be an object")
    return value


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise LedgerValidationError(f"{name} must be nonempty text")


def _digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise LedgerValidationError(f"{name} must be a lowercase SHA-256 hex digest")


def _json_scalar(value: object, name: str) -> None:
    if value is not None and type(value) not in (str, int, float, bool):
        raise LedgerValidationError(f"{name} must be a JSON scalar or null")
    if isinstance(value, float) and not (float("-inf") < value < float("inf")):
        raise LedgerValidationError(f"{name} cannot be a non-finite number")


def _json_value(value: object, name: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LedgerValidationError(f"{name} must contain only finite JSON values") from exc


def _timestamp(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise LedgerValidationError(f"{name} must be an ISO timestamp")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerValidationError(f"{name} must be an ISO timestamp") from exc
    if timestamp.tzinfo is None:
        raise LedgerValidationError(f"{name} must include a timezone")
