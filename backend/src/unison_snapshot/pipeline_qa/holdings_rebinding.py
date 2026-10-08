"""Audit changed holding IDs against two archived White House PDF versions."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from .migration_inventory import InventoryError, _index, _load, git_object, SHA


def _board(repo: Path, commit: str, read_object: Callable) -> dict[str, Any]:
    manifest = _load(read_object(repo, commit, "manifest.json"), "manifest")
    digest = manifest.get("board")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise InventoryError("invalid board hash")
    raw = read_object(repo, commit, f"board/{digest}.json")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise InventoryError("board content hash mismatch")
    return _load(raw, "board")


def _fact_key(row: dict[str, Any]) -> str:
    fact = {key: value for key, value in row.items()
            if key not in {"id", "filing_id", "source_url"}}
    return json.dumps(fact, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _archive(repo: Path, evidence_commit: str, url: str,
             read_object: Callable, list_paths: Callable) -> dict[str, Any]:
    document_id = hashlib.sha256(url.encode()).hexdigest()[:24]
    directory = f"whitehouse/disclosures/reports/{document_id}"
    paths = [path for path in list_paths(repo, evidence_commit, directory) if path.endswith(".json")]
    if len(paths) != 1:
        raise InventoryError(f"expected one archived PDF metadata object: {document_id}")
    metadata = _load(read_object(repo, evidence_commit, f"{directory}/{paths[0]}"), "PDF metadata")
    digest = metadata.get("sha256")
    if (metadata.get("document_url") != url or metadata.get("document_id") != f"wh-url:{document_id}" or
            not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
        raise InventoryError(f"PDF metadata does not match URL: {url}")
    pdf = read_object(repo, evidence_commit, metadata["archive_path"])
    if hashlib.sha256(pdf).hexdigest() != digest or len(pdf) != metadata.get("byte_length"):
        raise InventoryError(f"archived PDF bytes do not match metadata: {url}")
    return {"url": url, "sha256": digest, "byte_length": len(pdf),
            "archive_path": metadata["archive_path"],
            "filer_name_from_label": metadata.get("filer_name_from_label"),
            "report_year_from_label": metadata.get("report_year_from_label"),
            "retrieved_at": metadata.get("retrieved_at"),
            "last_modified": metadata.get("headers", {}).get("last-modified")}


def git_tree_paths(repo: Path, commit: str, directory: str) -> list[str]:
    if not SHA.fullmatch(commit) or not re.fullmatch(r"[a-zA-Z0-9_./-]+", directory):
        raise InventoryError("invalid evidence commit or path")
    result = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only",
                             f"{commit}:{directory}"], capture_output=True, check=False)
    if result.returncode:
        raise InventoryError(f"cannot list evidence directory: {directory}")
    return result.stdout.decode().splitlines()


def audit_holdings_rebinding(
    *, repo: Path, old_main_commit: str, new_main_commit: str, evidence_commit: str,
    read_object: Callable = git_object, list_paths: Callable = git_tree_paths,
) -> dict[str, Any]:
    for commit in (old_main_commit, new_main_commit, evidence_commit):
        if not SHA.fullmatch(commit):
            raise InventoryError("all refs must be full 40-character commits")
    old = _index(_board(repo, old_main_commit, read_object).get("reported_holdings"), "old holdings")
    new = _index(_board(repo, new_main_commit, read_object).get("reported_holdings"), "new holdings")
    removed = {key: old[key] for key in sorted(old.keys() - new.keys())}
    added = {key: new[key] for key in sorted(new.keys() - old.keys())}
    buckets = defaultdict(list)
    for row in added.values():
        buckets[_fact_key(row)].append(row)
    pairs = []
    used = set()
    for old_row in removed.values():
        matches = buckets[_fact_key(old_row)]
        if len(matches) != 1 or matches[0]["id"] in used:
            raise InventoryError(f"holding change has no unique fact-field match: {old_row['id']}")
        new_row = matches[0]
        used.add(new_row["id"])
        if not all(isinstance(row.get("source_url"), str) and row.get("source_url", "").startswith("https://www.whitehouse.gov/")
                   for row in (old_row, new_row)):
            raise InventoryError("changed holding lacks a White House source URL")
        old_pdf = _archive(repo, evidence_commit, old_row["source_url"], read_object, list_paths)
        new_pdf = _archive(repo, evidence_commit, new_row["source_url"], read_object, list_paths)
        same_label = (old_pdf["filer_name_from_label"] == new_pdf["filer_name_from_label"] and
                      old_pdf["report_year_from_label"] == new_pdf["report_year_from_label"])
        pairs.append({"old_id": old_row["id"], "new_id": new_row["id"],
                      "old_filing_id": old_row.get("filing_id"),
                      "new_filing_id": new_row.get("filing_id"),
                      "fact_fields_equal": True, "old_evidence": old_pdf,
                      "new_evidence": new_pdf, "same_filer_and_report_year_label": same_label,
                      "same_pdf_bytes": old_pdf["sha256"] == new_pdf["sha256"],
                      "revision_relation_verified": False,
                      "disposition": "source_rebinding_requires_revision_evidence"})
    return {"schema_version": "pipeline-holdings-rebinding/v1",
            "old_main_commit": old_main_commit, "new_main_commit": new_main_commit,
            "evidence_commit": evidence_commit,
            "removed_count": len(removed), "added_count": len(added),
            "paired_count": len(pairs), "other_new_ids": sorted(added.keys() - used),
            "pairs": pairs, "rebinding_complete": False,
            "projection_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--old-main-commit", required=True)
    parser.add_argument("--new-main-commit", required=True)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_holdings_rebinding(repo=args.repo, old_main_commit=args.old_main_commit,
                                      new_main_commit=args.new_main_commit,
                                      evidence_commit=args.evidence_commit)
    args.output.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"pairs"}},
                     sort_keys=True))


if __name__ == "__main__":
    main()
