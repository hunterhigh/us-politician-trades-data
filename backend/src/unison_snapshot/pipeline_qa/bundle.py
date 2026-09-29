"""Build one immutable, offline bundle from fixed source run artifacts."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from unison_snapshot.pipeline_ledger import (
    CANDIDATE_ROW_SCHEMA, RUN_MANIFEST_SCHEMA, validate_run_manifest,
)

from .diff import DiffInputError, load_manifest, load_rows, validate_ledger_rows


class BundleInputError(ValueError):
    """Fixed source run artifacts cannot be safely combined."""


def build_bundle(run_dirs: Iterable[str | Path], output_dir: str | Path, *,
                 run_id: str, code_commit: str, trigger: str = "local") -> dict[str, Any]:
    """Combine validated source manifests/rows without contacting or writing refs.

    Each input directory must contain ``manifest.json`` and the one
    ``candidate_rows`` output named by that manifest. The manifest output hash
    is verified before rows are loaded. All sources must use the same code SHA.
    """
    if len(code_commit) != 40 or any(c not in "0123456789abcdef" for c in code_commit):
        raise BundleInputError("code_commit must be a lowercase full Git SHA")
    if trigger not in {"schedule", "workflow_run", "workflow_dispatch", "replay", "local"}:
        raise BundleInputError("trigger is unsupported")
    roots = [Path(path).resolve() for path in run_dirs]
    if not roots:
        raise BundleInputError("at least one source run directory is required")
    if len({str(root) for root in roots}) != len(roots):
        raise BundleInputError("source run directories must be unique")

    manifests: list[dict[str, Any]] = []
    source_manifest_bytes: list[bytes] = []
    all_rows: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    seen_documents: set[tuple[str, str, str]] = set()
    for root in roots:
        try:
            manifest_path = root / "manifest.json"
            manifest = load_manifest(manifest_path)
            raw_manifest = manifest_path.read_bytes()
            if json.loads(raw_manifest.decode("utf-8-sig")) != manifest:
                raise BundleInputError(f"{root}: source manifest changed during verification")
            if manifest["code_commit"] != code_commit:
                raise BundleInputError(f"{root}: code_commit does not match requested fixed SHA")
            sources = set(manifest["source_scope"])
            source_ids.update(sources)
            document_keys = {(doc["source_id"], doc["document_id"], doc["source_sha256"])
                             for doc in manifest["documents"]}
            duplicate_docs = seen_documents & document_keys
            if duplicate_docs:
                raise BundleInputError(f"{root}: duplicate source document identities: {sorted(duplicate_docs)}")
            seen_documents.update(document_keys)
            row_outputs = [item for item in manifest["outputs"]
                           if item["artifact_type"] == "candidate_rows"]
            if len(row_outputs) != 1:
                raise BundleInputError(f"{root}: manifest must declare exactly one candidate_rows output")
            rows_path = (root / row_outputs[0]["path"]).resolve()
            if root not in rows_path.parents or not rows_path.is_file():
                raise BundleInputError(f"{root}: candidate_rows output is missing or escapes its run directory")
            actual_hash = _sha256(rows_path)
            if actual_hash != row_outputs[0]["sha256"]:
                raise BundleInputError(f"{root}: candidate_rows output SHA-256 mismatch")
            rows = load_rows(rows_path)
            validate_ledger_rows(rows, str(root))
            document_index = {
                (doc["source_id"], doc["document_id"], doc["source_sha256"]): doc
                for doc in manifest["documents"]
            }
            seen_candidates: set[tuple[str, str, str]] = set()
            for row in rows:
                if row.get("schema_version") != CANDIDATE_ROW_SCHEMA:
                    raise BundleInputError(f"{root}: candidate_rows must use the v1 ledger schema")
                source = row["source"]
                identity = (source["source_id"], source["document_id"],
                            source["source_sha256"])
                document = document_index.get(identity)
                if (row["run_id"] != manifest["run_id"] or document is None or
                        document["disposition"] != "parsed" or
                        source["source_url"] != document["source_url"] or
                        row["idempotency_key"] != document["idempotency_key"]):
                    raise BundleInputError(
                        f"{root}: candidate row is not bound to a parsed document in its run")
                candidate_identity = (source["source_id"], source["document_id"],
                                      row["candidate_id"])
                if candidate_identity in seen_candidates:
                    raise BundleInputError(f"{root}: duplicate candidate row identity")
                seen_candidates.add(candidate_identity)
            counts = Counter(row.get("disposition") for row in rows)
            expected = {"qualified": manifest["counts"]["qualified_rows"],
                        "quarantined": manifest["counts"]["quarantined_rows"],
                        "excluded": manifest["counts"]["excluded_rows"],
                        "unrecognized": manifest["counts"]["unrecognized_rows"]}
            if counts != Counter(expected) or len(rows) != manifest["accounted_rows"]:
                raise BundleInputError(f"{root}: candidate rows do not conserve manifest dispositions")
            manifests.append(manifest)
            source_manifest_bytes.append(raw_manifest)
            all_rows.extend(rows)
        except (OSError, DiffInputError) as exc:
            raise BundleInputError(f"{root}: {exc}") from exc

    rows_bytes = (json.dumps(all_rows, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")) + "\n").encode("utf-8")
    source_outputs = [
        {"artifact_type": "source_manifest", "path": f"sources/run-{index:04d}-manifest.json",
         "sha256": hashlib.sha256(raw).hexdigest()}
        for index, raw in enumerate(source_manifest_bytes, 1)
    ]
    counts = Counter(row["disposition"] for row in all_rows)
    doc_counts = Counter(doc["disposition"] for manifest in manifests
                         for doc in manifest["documents"])
    discovered = sum(m["counts"]["discovered_documents"] for m in manifests)
    archived = sum(m["counts"]["archived_documents"] for m in manifests)
    started = min(manifests, key=lambda m: _time(m["started_at"]))["started_at"]
    completed_values = [m["completed_at"] for m in manifests]
    completed = (max(completed_values, key=_time) if all(completed_values) else None)
    combined = {
        "schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id,
        "workflow": "pipeline-shadow-bundle", "trigger": trigger,
        "code_commit": code_commit, "started_at": started, "completed_at": completed,
        "source_scope": sorted(source_ids),
        "documents": [doc for manifest in manifests for doc in manifest["documents"]],
        "counts": {
            "discovered_documents": discovered, "archived_documents": archived,
            "parsed_documents": doc_counts["parsed"], "failed_documents": doc_counts["failed"],
            "no_row_documents": doc_counts["no_rows"], "excluded_documents": doc_counts["excluded"],
            "qualified_rows": counts["qualified"], "quarantined_rows": counts["quarantined"],
            "excluded_rows": counts["excluded"], "unrecognized_rows": counts["unrecognized"],
        },
        "accounted_rows": len(all_rows),
        "outputs": [{"artifact_type": "candidate_rows", "path": "candidate_rows.json",
                     "sha256": hashlib.sha256(rows_bytes).hexdigest()}, *source_outputs],
        "downstream_run_ids": sorted({str(value) for m in manifests
                                       for value in m.get("downstream_run_ids", [])}),
        "source_runs": [{"run_id": m["run_id"], "workflow": m["workflow"],
                         "manifest_path": source_outputs[index]["path"],
                         "manifest_sha256": source_outputs[index]["sha256"],
                         **({"evidence_commit": m["evidence_commit"]}
                            if m.get("evidence_commit") else {}),
                         **({"review_commit": m["review_commit"]}
                            if m.get("review_commit") else {})}
                        for index, m in enumerate(manifests)],
    }
    validate_run_manifest(combined)
    out = Path(output_dir).resolve()
    if out.exists() and any(out.iterdir()):
        raise BundleInputError("output directory must be new or empty; shadow bundles are immutable")
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidate_rows.json").write_bytes(rows_bytes)
    (out / "sources").mkdir()
    for output, raw in zip(source_outputs, source_manifest_bytes):
        (out / output["path"]).write_bytes(raw)
    manifest_bytes = (json.dumps(combined, ensure_ascii=False, indent=2,
                                 sort_keys=True) + "\n").encode("utf-8")
    (out / "manifest.json").write_bytes(manifest_bytes)
    return combined


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
