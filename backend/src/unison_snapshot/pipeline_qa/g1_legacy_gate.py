"""Combine immutable G1 legacy binding and conservation reports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .migration_inventory import InventoryError


def build_g1_legacy_gate(inventory: dict[str, Any], disposition: dict[str, Any],
                         transactions: dict[str, Any], entities: dict[str, Any],
                         rebinding: dict[str, Any]) -> dict[str, Any]:
    fixed = inventory.get("fixed_inputs")
    if (inventory.get("schema_version") != "pipeline-migration-inventory/v1" or
            disposition.get("schema_version") != "pipeline-g1-disposition/v1" or
            transactions.get("schema_version") != "pipeline-g1-source-coverage/v1" or
            entities.get("schema_version") != "pipeline-g1-entity-source-coverage/v1" or
            rebinding.get("schema_version") != "pipeline-holdings-rebinding/v1" or
            transactions.get("fixed_inputs") != fixed or entities.get("fixed_inputs") != fixed or
            {key: value for key, value in disposition.get("fixed_inputs", {}).items()
             if key != "old_main_commit"} != fixed or
            rebinding.get("new_main_commit") != fixed.get("main_commit") or
            rebinding.get("old_main_commit") != disposition["fixed_inputs"].get("old_main_commit")):
        raise InventoryError("G1 gate inputs are not the same immutable snapshot")
    conditions = {
        "formal_candidate_id_coverage": inventory.get("production_candidate_id_coverage_complete") is True,
        "old_formal_id_conservation": disposition.get("old_formal_id_conservation_complete") is True,
        "transaction_legacy_source_binding": transactions.get("transaction_legacy_source_binding_complete") is True,
        "holdings_people_legacy_source_binding": entities.get("holdings_people_legacy_source_binding_complete") is True,
        "holding_index_replacement": rebinding.get("rebinding_complete") is True,
        "transaction_count_matches": transactions.get("total") == len(inventory["records"]["transactions"]),
        "holding_count_matches": entities.get("holding_total") == len(inventory["records"]["reported_holdings"]),
        "person_count_matches": entities.get("person_total") == len(inventory["records"]["people"]),
    }
    return {"schema_version": "pipeline-g1-legacy-gate/v1",
            "fixed_inputs": {**fixed, "old_main_commit": disposition["fixed_inputs"]["old_main_commit"]},
            "conditions": conditions,
            "g1_legacy_candidate_binding_complete": all(conditions.values()),
            "verification_levels": {
                "house_senate_transactions": transactions["house_senate_level"],
                "oge_direct_transactions": transactions["oge_direct_level"],
                "whitehouse_transactions": transactions["whitehouse_level"],
                "holdings": "source_specific_legacy_qualification_or_eligible_row",
                "people": "source_identity_candidate_and_formal_reference",
            },
            "historical_shadow_9742_map_replayed": False,
            "original_source_bytes_fully_reverified": False,
            "market_page_bytes_verified": inventory.get("market", {}).get("verification_status") == "content_verified",
            "projection_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inventory", "disposition", "transactions", "entities", "rebinding", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    def read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
    report = build_g1_legacy_gate(read(args.inventory), read(args.disposition),
                                  read(args.transactions), read(args.entities),
                                  read(args.rebinding))
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
