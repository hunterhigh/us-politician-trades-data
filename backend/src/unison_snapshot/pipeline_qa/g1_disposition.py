"""Conserve old formal IDs against the pinned current candidate inventory."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .holdings_rebinding import _board
from .migration_inventory import InventoryError, _index, git_object, SHA


def build_g1_disposition(
    *, repo: Path, old_main_commit: str, inventory: dict[str, Any],
    overlay: dict[str, Any], rebinding: dict[str, Any],
    read_object: Callable = git_object,
) -> dict[str, Any]:
    if not SHA.fullmatch(old_main_commit):
        raise InventoryError("old main must be a full commit")
    fixed = inventory.get("fixed_inputs", {})
    if (inventory.get("schema_version") != "pipeline-migration-inventory/v1" or
            overlay.get("schema_version") != "pipeline-legacy-channel-overlay/v1" or
            rebinding.get("schema_version") != "pipeline-holdings-rebinding/v1" or
            rebinding.get("old_main_commit") != old_main_commit or
            rebinding.get("new_main_commit") != fixed.get("main_commit") or
            overlay.get("fixed_inputs") != fixed):
        raise InventoryError("G1 inputs do not share immutable commits")
    old_board = _board(repo, old_main_commit, read_object)
    current = inventory.get("records", {})
    old = {name: _index(old_board.get(name), f"old.{name}")
           for name in ("people", "transactions", "reported_holdings")}
    new = {name: _index([row["canonical_record"] for row in current.get(name, [])],
                        f"current.{name}")
           for name in old}
    issues = []
    transaction_removed = sorted(old["transactions"].keys() - new["transactions"].keys())
    transaction_changed = sorted(key for key in old["transactions"].keys() & new["transactions"].keys()
                                 if old["transactions"][key] != new["transactions"][key])
    if transaction_removed or transaction_changed:
        issues.append("old_transaction_loss_or_change")
    group_ids = {key: set(value) for key, value in overlay.get("groups", {}).items()}
    if set().union(*group_ids.values()) != new["transactions"].keys() or sum(
            len(value) for value in group_ids.values()) != len(new["transactions"]):
        raise InventoryError("legacy channel overlay does not partition current transactions")
    transaction_records = []
    for record_id in sorted(new["transactions"]):
        channel = next(key for key, ids in group_ids.items() if record_id in ids)
        transaction_records.append({"id": record_id, "channel": channel,
                                    "old_formal": record_id in old["transactions"],
                                    "disposition": "retained_same_id" if record_id in old["transactions"]
                                                   else "new_current_candidate"})

    pairs = rebinding.get("pairs", [])
    paired_old = {pair["old_id"] for pair in pairs}
    paired_new = {pair["new_id"] for pair in pairs}
    holding_removed = old["reported_holdings"].keys() - new["reported_holdings"].keys()
    holding_added = new["reported_holdings"].keys() - old["reported_holdings"].keys()
    if (paired_old != holding_removed or not paired_new <= holding_added or
            len(paired_old) != len(pairs) or len(paired_new) != len(pairs) or
            not rebinding.get("rebinding_complete")):
        issues.append("holding_rebinding_not_closed")
    holding_changed = sorted(key for key in old["reported_holdings"].keys() & new["reported_holdings"].keys()
                             if old["reported_holdings"][key] != new["reported_holdings"][key])
    if holding_changed:
        issues.append("retained_holding_fields_changed")
    holding_records = []
    for record_id in sorted(new["reported_holdings"]):
        if record_id in paired_new:
            disposition = "official_index_replacement_bound"
        elif record_id in old["reported_holdings"]:
            disposition = "retained_same_id"
        else:
            disposition = "new_current_candidate"
        holding_records.append({"id": record_id, "disposition": disposition})

    person_removed = sorted(old["people"].keys() - new["people"].keys())
    if person_removed:
        issues.append("old_person_removed")
    person_changes = []
    for record_id in sorted(old["people"].keys() & new["people"].keys()):
        prior, now = old["people"][record_id], new["people"][record_id]
        differences = sorted(key for key in prior.keys() | now.keys()
                             if prior.get(key) != now.get(key))
        if differences:
            if differences != ["display_name"] or str(prior.get("display_name")).casefold() != str(now.get("display_name")).casefold():
                issues.append(f"person_field_change:{record_id}")
            person_changes.append({"id": record_id, "fields": differences})

    return {
        "schema_version": "pipeline-g1-disposition/v1",
        "fixed_inputs": {**fixed, "old_main_commit": old_main_commit},
        "counts": {
            "old_transactions": len(old["transactions"]),
            "retained_old_transactions": len(old["transactions"]) - len(transaction_removed),
            "new_transactions": len(new["transactions"].keys() - old["transactions"].keys()),
            "old_holdings": len(old["reported_holdings"]),
            "retained_old_holdings": len(old["reported_holdings"].keys() & new["reported_holdings"].keys()),
            "rebound_holdings": len(pairs),
            "other_new_holdings": len(holding_added - paired_new),
            "old_people": len(old["people"]),
            "new_people": len(new["people"].keys() - old["people"].keys()),
        },
        "transaction_records": transaction_records,
        "holding_records": holding_records,
        "person_changes": person_changes,
        "issues": sorted(set(issues)),
        "old_formal_id_conservation_complete": not issues,
        "g1_source_binding_complete": False,
        "projection_ready": False,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--old-main-commit", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--rebinding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    def read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
    report = build_g1_disposition(repo=args.repo, old_main_commit=args.old_main_commit,
        inventory=read(args.inventory), overlay=read(args.overlay), rebinding=read(args.rebinding))
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "issues": report["issues"],
                      "old_formal_id_conservation_complete": report["old_formal_id_conservation_complete"],
                      "projection_ready": False}, sort_keys=True))


if __name__ == "__main__":
    main()
