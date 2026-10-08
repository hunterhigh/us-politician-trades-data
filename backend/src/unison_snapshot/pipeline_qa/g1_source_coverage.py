"""Conserve every current formal transaction across pinned legacy source bindings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .migration_inventory import InventoryError


def build_g1_source_coverage(inventory: dict[str, Any],
                             house_senate: dict[str, Any],
                             oge: dict[str, Any]) -> dict[str, Any]:
    fixed = inventory.get("fixed_inputs")
    if (inventory.get("schema_version") != "pipeline-migration-inventory/v1" or
            house_senate.get("schema_version") != "pipeline-source-qualification-bridge/v1" or
            oge.get("schema_version") != "pipeline-oge-qualification-bridge/v1" or
            house_senate.get("fixed_inputs") != fixed or oge.get("fixed_inputs") != fixed or
            house_senate.get("house_senate_binding_complete") is not True or
            oge.get("oge_candidate_continuity_complete") is not True):
        raise InventoryError("G1 transaction bindings do not share a verified fixed input")
    formal = {row["id"] for row in inventory["records"]["transactions"]}
    if len(formal) != len(inventory["records"]["transactions"]):
        raise InventoryError("duplicate formal transaction ID")
    sources = {"house_clerk": house_senate["records"]["house_clerk"],
               "senate_efd": house_senate["records"]["senate_efd"],
               "oge": oge["records"]}
    seen = set()
    counts = {}
    for source_id, rows in sources.items():
        ids = {row["id"] for row in rows}
        if len(ids) != len(rows) or ids & seen:
            raise InventoryError(f"duplicate source-qualified transaction: {source_id}")
        expected = {row["id"] for row in inventory["records"]["transactions"]
                    if row["source_id"] == source_id}
        if ids != expected:
            raise InventoryError(f"source-qualified ID set differs from formal candidate: {source_id}")
        seen.update(ids)
        counts[source_id] = len(ids)
    if seen != formal:
        raise InventoryError("not all formal transactions have a legacy source binding")
    return {"schema_version": "pipeline-g1-source-coverage/v1",
            "fixed_inputs": fixed, "counts": counts,
            "total": len(formal),
            "transaction_legacy_source_binding_complete": True,
            "house_senate_level": "qualified_rows_with_archived_source_hash",
            "oge_direct_level": "document_promotion_count_plus_candidate_ID",
            "whitehouse_level": "fixed_document_audit_plus_candidate_ID",
            "historical_source_replay_performed": False,
            "holdings_people_source_binding_complete": False,
            "projection_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--house-senate", type=Path, required=True)
    parser.add_argument("--oge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    def read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
    report = build_g1_source_coverage(read(args.inventory), read(args.house_senate), read(args.oge))
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
