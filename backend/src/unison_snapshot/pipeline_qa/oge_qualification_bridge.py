"""Bind pinned OGE candidates to legacy document qualifications and WH audits."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .migration_inventory import InventoryError, _load, git_object, SHA


def build_oge_qualification_bridge(
    *, repo: Path, inventory: dict[str, Any], overlay: dict[str, Any],
    read_object: Callable = git_object,
) -> dict[str, Any]:
    fixed = inventory.get("fixed_inputs", {})
    commit = fixed.get("review_commit")
    if (inventory.get("schema_version") != "pipeline-migration-inventory/v1" or
            overlay.get("schema_version") != "pipeline-legacy-channel-overlay/v1" or
            overlay.get("fixed_inputs") != fixed or not isinstance(commit, str) or
            not SHA.fullmatch(commit)):
        raise InventoryError("OGE bridge requires one pinned inventory and overlay")
    formal = {row["id"]: row for row in inventory["records"]["transactions"]
              if row.get("source_id") == "oge"}
    direct = [row for row in formal.values()
              if not str(row.get("filing_id", "")).startswith("wh-url:")]
    direct_counts = Counter(row["filing_id"] for row in direct)
    path = "oge/qualifications/current.json"
    raw = read_object(repo, commit, path)
    decision = _load(raw, path)
    if decision.get("source_id") != "oge" or not isinstance(decision.get("reports"), list):
        raise InventoryError("invalid fixed OGE qualification report")
    report_counts = {}
    for report in decision["reports"]:
        document_id = report.get("document_id")
        count = report.get("promoted_count")
        if (not isinstance(document_id, str) or document_id in report_counts or
                not isinstance(count, int) or count < 0 or
                (count and report.get("document_reasons"))):
            raise InventoryError("invalid OGE document qualification")
        report_counts[document_id] = count
    if {key: count for key, count in report_counts.items() if count} != dict(direct_counts):
        raise InventoryError("OGE current formal rows differ from document promotion counts")

    wh_ids = set(overlay.get("groups", {}).get("whitehouse_url_fixed_document_continuity", []))
    other_oge_ids = set(overlay.get("groups", {}).get("oge_124_fixed_id_continuity", []))
    if not other_oge_ids <= {row["id"] for row in direct}:
        raise InventoryError("OGE 124 audit IDs must belong to direct candidate documents")
    if wh_ids != set(formal) - {row["id"] for row in direct}:
        raise InventoryError("White House fixed audit does not cover OGE URL candidates")
    wh_doc_counts = overlay.get("whitehouse_document_counts", {})
    if Counter(formal[record_id]["filing_id"] for record_id in wh_ids) != wh_doc_counts:
        raise InventoryError("White House document counts changed after fixed audit")
    if any(row.get("issues") for row in formal.values()):
        raise InventoryError("unresolved formal OGE candidate difference")

    records = []
    decision_hash = hashlib.sha256(raw).hexdigest()
    for record_id, row in sorted(formal.items()):
        whitehouse = record_id in wh_ids
        records.append({"id": record_id, "document_id": row["filing_id"],
                        "source_url": row["source_url"],
                        "binding_kind": "whitehouse_fixed_document_audit" if whitehouse else
                                        "oge_legacy_document_qualification",
                        "qualification_or_audit_sha256":
                            overlay["historical_audit_fixtures"]["whitehouse_sha256"] if whitehouse else decision_hash,
                        "verification_level": "legacy_document_audit_not_replayed" if whitehouse else
                                              "legacy_document_promotion_count_not_row_replayed"})
    return {"schema_version": "pipeline-oge-qualification-bridge/v1",
            "fixed_inputs": fixed,
            "qualification_path": path,
            "qualification_sha256": decision_hash,
            "counts": {"direct_oge": len(direct), "whitehouse_url": len(wh_ids),
                       "total": len(records), "qualified_direct_documents": len(direct_counts)},
            "records": records,
            "oge_candidate_continuity_complete": True,
            "historical_row_replay_performed": False,
            "projection_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    overlay = json.loads(args.overlay.read_text(encoding="utf-8"))
    report = build_oge_qualification_bridge(repo=args.repo, inventory=inventory, overlay=overlay)
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({"counts": report["counts"],
                      "oge_candidate_continuity_complete": True,
                      "historical_row_replay_performed": False,
                      "projection_ready": False}, sort_keys=True))


if __name__ == "__main__":
    main()
