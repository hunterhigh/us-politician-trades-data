"""Read-only comparison for candidate rows and pipeline run manifests."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from unison_snapshot.pipeline_ledger import (
    LedgerValidationError,
    ROW_DISPOSITIONS,
    RUN_MANIFEST_SCHEMA,
    validate_candidate_row,
    validate_run_manifest,
)


class DiffInputError(ValueError):
    """Input rows or manifests cannot be compared safely."""


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSON array/object-with-rows, JSONL, or CSV of row records."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raw = source.read_text(encoding="utf-8-sig")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        records = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DiffInputError(f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
        parsed = records
    if isinstance(parsed, dict):
        parsed = parsed.get("rows", parsed.get("candidates"))
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise DiffInputError(f"{source}: expected an array of row objects (or an object with rows)")
    return parsed


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return validate_run_manifest(value)
    except (OSError, json.JSONDecodeError, LedgerValidationError) as exc:
        raise DiffInputError(f"invalid run manifest {path}: {exc}") from exc


def stable_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    """Identity matches a logical row across immutable source revisions."""
    source = row.get("source") if isinstance(row.get("source"), dict) else row
    source_id = _text(source, "source_id")
    document_id = _text(source, "document_id")
    row_id = row.get("candidate_id", row.get("row_id", row.get("stable_row_id")))
    if not isinstance(row_id, str) or not row_id.strip():
        raise DiffInputError("each row needs candidate_id, row_id, or stable_row_id")
    return source_id, document_id, row_id


def check_conservation(rows: list[dict[str, Any]], manifest: dict[str, Any], label: str) -> dict[str, Any]:
    actual: Counter[str | None] = Counter()
    unexpected: Counter[str] = Counter()
    for row in rows:
        disposition = row.get("disposition")
        if disposition is None or isinstance(disposition, str):
            actual[disposition] += 1
        else:
            unexpected[type(disposition).__name__] += 1
    expected_names = {
        "qualified": "qualified_rows", "quarantined": "quarantined_rows",
        "excluded": "excluded_rows", "unrecognized": "unrecognized_rows",
    }
    actual_counts = {name: actual.get(name, 0) for name in ROW_DISPOSITIONS}
    unexpected.update({str(k): v for k, v in actual.items() if k not in ROW_DISPOSITIONS})
    mismatches = {
        disposition: {"manifest": manifest["counts"][count_name], "actual": actual_counts[disposition]}
        for disposition, count_name in expected_names.items()
        if manifest["counts"][count_name] != actual_counts[disposition]
    }
    accounted_actual = sum(actual_counts.values())
    if accounted_actual != len(rows):
        mismatches["unaccounted_rows"] = {"manifest": manifest["accounted_rows"], "actual": accounted_actual,
                                          "loaded_rows": len(rows)}
    return {"label": label, "loaded_rows": len(rows), "actual_dispositions": actual_counts,
            "actual_accounted_rows": accounted_actual, "manifest_accounted_rows": manifest["accounted_rows"],
            "mismatches": mismatches, "invalid_dispositions": unexpected}


def compare_runs(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]],
                 old_manifest: dict[str, Any], new_manifest: dict[str, Any], *,
                 old_artifact_root: str | Path | None = None,
                 new_artifact_root: str | Path | None = None) -> dict[str, Any]:
    """Compare rows by stable identity and report every duplicate/add/remove/change."""
    for manifest, label in ((old_manifest, "old"), (new_manifest, "new")):
        if manifest.get("schema_version") != RUN_MANIFEST_SCHEMA:
            raise DiffInputError(f"{label} manifest has unsupported schema")
    conservation = [check_conservation(old_rows, old_manifest, "old"),
                    check_conservation(new_rows, new_manifest, "new")]
    artifact_checks = [
        verify_manifest_outputs(old_manifest, old_artifact_root, "old"),
        verify_manifest_outputs(new_manifest, new_artifact_root, "new"),
    ]
    old_index, old_dups = _index(old_rows)
    new_index, new_dups = _index(new_rows)
    duplicate_records = [{"run": label, "identity": list(key), "count": count}
                         for label, values in (("old", old_dups), ("new", new_dups))
                         for key, count in sorted(values.items())]
    added, removed, changed = [], [], []
    for key in sorted(old_index.keys() | new_index.keys()):
        before, after = old_index.get(key), new_index.get(key)
        if before is None:
            added.append({"identity": list(key), "row": after})
        elif after is None:
            removed.append({"identity": list(key), "row": before})
        else:
            field_changes = _field_diff(before, after)
            if field_changes:
                changed.append({"identity": list(key), "changes": field_changes})
    return {
        "schema_version": "pipeline-run-diff/v1",
        "runs": {"old": old_manifest["run_id"], "new": new_manifest["run_id"]},
        "conservation": conservation,
        "artifact_checks": artifact_checks,
        "duplicates": duplicate_records,
        "added_count": len(added), "removed_count": len(removed), "changed_count": len(changed),
        "added": added, "removed": removed, "changed": changed,
        "ok": (not duplicate_records and
               not any(c["mismatches"] or c["invalid_dispositions"] for c in conservation) and
               not any(check["status"] == "failed" for check in artifact_checks)),
    }


def verify_manifest_outputs(manifest: dict[str, Any], root: str | Path | None,
                            label: str) -> dict[str, Any]:
    """Verify manifest output hashes against files beneath an artifact root."""
    outputs = manifest["outputs"]
    if root is None:
        return {"label": label, "status": "not_checked", "checked": 0,
                "failures": [], "reason": "artifact root not provided"}
    base = Path(root).resolve()
    failures = []
    for output in outputs:
        relative = output["path"].replace("\\", "/")
        path = (base / relative).resolve()
        if base not in path.parents:
            failures.append({"path": relative, "reason": "path escapes artifact root"})
            continue
        if not path.is_file():
            failures.append({"path": relative, "reason": "file missing"})
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != output["sha256"]:
            failures.append({"path": relative, "reason": "sha256 mismatch",
                             "expected": output["sha256"], "actual": actual})
    return {"label": label, "status": "failed" if failures else "passed",
            "checked": len(outputs), "failures": failures}


def validate_ledger_rows(rows: list[dict[str, Any]], label: str) -> None:
    for index, row in enumerate(rows, 1):
        if row.get("schema_version") == "pipeline-candidate-row/v1":
            try:
                validate_candidate_row(row)
            except LedgerValidationError as exc:
                raise DiffInputError(f"{label} row {index} fails ledger validation: {exc}") from exc


def _index(rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, str], dict[str, Any]], Counter]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[stable_identity(row)].append(row)
    duplicates = Counter({key: len(group) for key, group in groups.items() if len(group) > 1})
    # Keep one representative only for a readable diff; every collision is listed and makes `ok` false.
    return {key: group[0] for key, group in groups.items()}, duplicates


def _field_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for field in sorted(before.keys() | after.keys()):
        if before.get(field) != after.get(field) or (field in before) != (field in after):
            changes.append({"field": field, "old": before.get(field), "new": after.get(field),
                            "old_present": field in before, "new_present": field in after})
    return changes


def _text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DiffInputError(f"each row needs nonempty {name}")
    return value
