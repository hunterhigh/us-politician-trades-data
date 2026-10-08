"""Bind pinned formal House/Senate rows to existing qualification decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from .holdings_rebinding import git_tree_paths
from .migration_inventory import (InventoryError, _differences, _load,
                                  _market_enrichment_only, git_object, SHA)


def build_source_qualification_bridge(
    *, repo: Path, inventory: dict[str, Any],
    read_object: Callable = git_object, list_paths: Callable = git_tree_paths,
) -> dict[str, Any]:
    fixed = inventory.get("fixed_inputs", {})
    review_commit = fixed.get("review_commit")
    if inventory.get("schema_version") != "pipeline-migration-inventory/v1" or not isinstance(review_commit, str) or not SHA.fullmatch(review_commit):
        raise InventoryError("expected inventory with a pinned review commit")
    formal_rows = inventory.get("records", {}).get("transactions", [])
    formal = {row["id"]: row for row in formal_rows}
    if len(formal) != len(formal_rows):
        raise InventoryError("duplicate formal transaction ID")

    house = {}
    paths = list_paths(repo, review_commit, "house_clerk/qualifications")
    for relative in paths:
        if not re.fullmatch(r"20\d\d/\d+/[0-9a-f]{64}\.json", relative):
            raise InventoryError(f"unexpected House qualification path: {relative}")
        path = f"house_clerk/qualifications/{relative}"
        raw = read_object(repo, review_commit, path)
        decision = _load(raw, path)
        doc_id = relative.split("/")[1]
        source_hash = relative.rsplit("/", 1)[-1][:-5]
        q = decision.get("qualification", {})
        if (decision.get("document_id") != doc_id or decision.get("source_sha256") != source_hash or
                q.get("method") != "deterministic_automatic_rules" or
                not isinstance(decision.get("transactions"), list) or
                q.get("qualified_count") != len(decision["transactions"])):
            raise InventoryError(f"House qualification is not an eligible fixed decision: {path}")
        if decision["transactions"] and q.get("production_eligible") is not True:
            raise InventoryError(f"House ineligible rows cannot bind formal candidates: {path}")
        for row in decision["transactions"]:
            record_id = row.get("id") if isinstance(row, dict) else None
            current = formal.get(record_id)
            if record_id in house or not current or current.get("source_id") != "house_clerk" or current.get("issues"):
                raise InventoryError(f"House qualified ID is duplicate or absent: {record_id}")
            fields = _differences(current["canonical_record"], row)
            if fields and not _market_enrichment_only(current["canonical_record"], row, fields):
                raise InventoryError(f"House qualified field mismatch: {record_id}: {fields}")
            house[record_id] = {"id": record_id, "document_id": doc_id,
                                "source_sha256": source_hash,
                                "qualification_path": path,
                                "qualification_sha256": hashlib.sha256(raw).hexdigest(),
                                "verification_level": "legacy_qualification_and_archived_hash_not_reverified"}

    senate_path = "senate_efd/qualifications/current.json"
    senate_raw = read_object(repo, review_commit, senate_path)
    senate_decision = _load(senate_raw, senate_path)
    senate = {}
    qualified_rows = senate_decision.get("qualified_rows")
    if not isinstance(qualified_rows, list) or senate_decision.get("candidate_transaction_count") != len(qualified_rows):
        raise InventoryError("Senate fixed qualification count is invalid")
    for row in qualified_rows:
        record_id = row.get("transaction_id") if isinstance(row, dict) else None
        current = formal.get(record_id)
        if record_id in senate or not current or current.get("source_id") != "senate_efd" or current.get("issues"):
            raise InventoryError(f"Senate qualified ID is duplicate or absent: {record_id}")
        if (row.get("document_id") != current.get("filing_id") or
                row.get("source_url") != current.get("source_url") or
                not isinstance(row.get("source_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"])):
            raise InventoryError(f"Senate qualified source mismatch: {record_id}")
        senate[record_id] = {"id": record_id, "document_id": row["document_id"],
                             "source_sha256": row["source_sha256"],
                             "qualification_path": senate_path,
                             "qualification_sha256": hashlib.sha256(senate_raw).hexdigest(),
                             "verification_level": "legacy_qualification_and_archived_hash_not_reverified"}

    expected_house = {row["id"] for row in formal_rows if row.get("source_id") == "house_clerk"}
    expected_senate = {row["id"] for row in formal_rows if row.get("source_id") == "senate_efd"}
    if set(house) != expected_house or set(senate) != expected_senate:
        raise InventoryError("House or Senate fixed qualification does not cover all formal IDs")
    return {"schema_version": "pipeline-source-qualification-bridge/v1",
            "fixed_inputs": fixed,
            "counts": {"house_clerk": len(house), "senate_efd": len(senate)},
            "house_qualification_file_count": len(paths),
            "senate_qualification_sha256": hashlib.sha256(senate_raw).hexdigest(),
            "senate_paper_ids": sorted(record_id for record_id in senate
                                        if "/search/view/paper/" in formal[record_id].get("source_url", "")),
            "records": {"house_clerk": sorted(house.values(), key=lambda row: row["id"]),
                        "senate_efd": sorted(senate.values(), key=lambda row: row["id"])},
            "house_senate_binding_complete": True,
            "g1_source_binding_complete": False,
            "projection_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    report = build_source_qualification_bridge(repo=args.repo, inventory=inventory)
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "senate_paper_ids": report["senate_paper_ids"],
                      "house_senate_binding_complete": True, "projection_ready": False}, sort_keys=True))


if __name__ == "__main__":
    main()
