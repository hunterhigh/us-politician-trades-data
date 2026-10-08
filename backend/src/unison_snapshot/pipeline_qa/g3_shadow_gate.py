"""Repeat a fixed G2 candidate build and compare its bytes with published main.

Only reads pinned Git objects and writes the local audit report. No ref moves.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from unison_snapshot.builder import build
from .migration_inventory import InventoryError


def git_blob_oid(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def compare_bundle_tree(files: dict[str, bytes], tree: dict[str, str]) -> dict[str, Any]:
    mismatches = [path for path, content in sorted(files.items())
                  if tree.get(path) != git_blob_oid(content)]
    return {"file_count": len(files), "matched_file_count": len(files) - len(mismatches),
            "mismatched_paths": mismatches[:20], "all_candidate_files_match_published_main": not mismatches}


def _main_tree(repo: Path, commit: str) -> dict[str, str]:
    raw = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-rz", commit])
    tree = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, kind, oid = header.split(b" ")
        if kind != b"blob":
            raise InventoryError("main tree contains a non-blob release path")
        tree[path.decode()] = oid.decode()
    return tree


def audit_g3_shadow(*, repo: Path, candidate_path: Path,
                    g2_report: dict[str, Any]) -> dict[str, Any]:
    if (g2_report.get("schema_version") != "pipeline-g2-candidate-projection/v1" or
            not g2_report.get("formal_fact_ids_and_fields_preserved") or
            not g2_report.get("market_pages_content_verified") or
            g2_report.get("published") is not False):
        raise InventoryError("G3 requires a verified unpublished G2 candidate")
    raw = candidate_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != g2_report.get("candidate_sha256"):
        raise InventoryError("G2 candidate bytes changed")
    fixed = g2_report["fixed_inputs"]
    payload = json.loads(raw)
    main = fixed["main_commit"]
    manifest_raw = subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{main}:manifest.json"])
    if hashlib.sha256(manifest_raw).hexdigest() != fixed["manifest_sha256"]:
        raise InventoryError("published manifest bytes changed")
    manifest = json.loads(manifest_raw)
    if (manifest.get("market_commit") != fixed["market_commit"] or
            manifest.get("board") != fixed["board_sha256"]):
        raise InventoryError("published manifest differs from fixed G2 input")
    kwargs = {"generated_at": manifest["generated_at"], "allow_production": True,
              "allow_market": True, "market_commit": fixed["market_commit"],
              "market_pages": manifest["market_pages"]}
    first = build(payload, **kwargs)
    first_oids = {path: git_blob_oid(content) for path, content in first.files.items()}
    first_snapshot = first.manifest["snapshot_id"]
    comparison = compare_bundle_tree(first.files, _main_tree(repo, main))
    del first
    second = build(payload, **kwargs)
    second_oids = {path: git_blob_oid(content) for path, content in second.files.items()}
    if first_oids != second_oids or first_snapshot != second.manifest["snapshot_id"]:
        raise InventoryError("G3 repeated build is not byte-deterministic")
    return {"schema_version": "pipeline-g3-shadow-gate/v1", "fixed_inputs": fixed,
            "candidate_sha256": g2_report["candidate_sha256"],
            "snapshot_id": first_snapshot, "expected_snapshot_id": manifest["snapshot_id"],
            "repeated_build_deterministic": True,
            **comparison,
            "published": False,
            "fixed_snapshot_exact_replay": (comparison["all_candidate_files_match_published_main"] and
                                            first_snapshot == manifest["snapshot_id"]),
            "production_cutover_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "candidate", "g2_report", "output"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    args = parser.parse_args()
    report = audit_g3_shadow(repo=args.repo, candidate_path=args.candidate,
                             g2_report=json.loads(args.g2_report.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
