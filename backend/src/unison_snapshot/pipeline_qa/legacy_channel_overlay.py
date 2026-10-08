"""Join fixed legacy audit fixtures to a current pinned transaction inventory.

This only checks current ID/document continuity. Historical OCR, qualification,
and evidence decisions are not replayed by this join.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .migration_inventory import InventoryError


def _fixture(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise InventoryError(f"invalid legacy audit fixture: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def build_legacy_channel_overlay(
    inventory: dict[str, Any], *, whitehouse_audit: Path, oge_124_audit: Path,
) -> dict[str, Any]:
    if inventory.get("schema_version") != "pipeline-migration-inventory/v1" or not inventory.get("fixed_inputs"):
        raise InventoryError("expected a pinned migration inventory")
    rows = inventory.get("records", {}).get("transactions")
    if not isinstance(rows, list):
        raise InventoryError("transaction inventory is missing")
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise InventoryError("duplicate current transaction ID")
    wh, wh_sha = _fixture(whitehouse_audit)
    oge, oge_sha = _fixture(oge_124_audit)
    wh_documents = wh.get("documents")
    oge_documents = oge.get("documents")
    if not isinstance(wh_documents, list) or not isinstance(oge_documents, list):
        raise InventoryError("legacy audit documents missing")

    wh_ids = set()
    wh_counts = {}
    for document in wh_documents:
        document_id = document.get("document_id")
        source_url = document.get("source_url")
        expected = document.get("candidate_row_count")
        if not isinstance(document_id, str) or document_id in wh_counts or not isinstance(expected, int):
            raise InventoryError("invalid White House document audit")
        matched = [row for row in rows if row.get("filing_id") == document_id]
        if (len(matched) != expected or
                any(row.get("source_id") != "oge" or row.get("source_url") != source_url
                    or row.get("issues") for row in matched)):
            raise InventoryError(f"White House current candidate differs from fixed document audit: {document_id}")
        wh_counts[document_id] = len(matched)
        wh_ids.update(row["id"] for row in matched)
    if len(wh_ids) != wh.get("wh_url_row_count"):
        raise InventoryError("White House fixed audit total differs from current candidate")
    current_wh = {row["id"] for row in rows if str(row.get("filing_id", "")).startswith("wh-url:")}
    if wh_ids != current_wh:
        raise InventoryError("current White House URL documents are outside fixed audit")

    oge_ids = set()
    for document in oge_documents:
        ids = document.get("candidate_transaction_ids")
        if not isinstance(ids, list) or len(ids) != document.get("candidate_row_count"):
            raise InventoryError("invalid OGE 124 document audit")
        for record_id in ids:
            if record_id in oge_ids or record_id not in by_id:
                raise InventoryError(f"OGE 124 candidate is duplicate or missing: {record_id}")
            row = by_id[record_id]
            if row.get("source_id") != "oge" or row.get("issues") or record_id in wh_ids:
                raise InventoryError(f"OGE 124 candidate is not a current qualified OGE row: {record_id}")
            oge_ids.add(record_id)
    if len(oge_ids) != oge.get("candidate_row_count"):
        raise InventoryError("OGE 124 fixed audit total differs from current candidate")

    paper_ids = {row["id"] for row in rows
                 if row.get("source_id") == "senate_efd" and
                 "/search/view/paper/" in str(row.get("source_url", ""))}
    if len(paper_ids) != 1:
        raise InventoryError("expected exactly one current Senate paper candidate")
    groups = {"whitehouse_url_fixed_document_continuity": sorted(wh_ids),
              "oge_124_fixed_id_continuity": sorted(oge_ids),
              "senate_paper_source_binding_required": sorted(paper_ids),
              "other_current_candidate_ids": sorted(by_id.keys() - wh_ids - oge_ids - paper_ids)}
    if sum(len(group) for group in groups.values()) != len(rows):
        raise InventoryError("legacy overlay does not conserve transaction IDs")
    return {
        "schema_version": "pipeline-legacy-channel-overlay/v1",
        "fixed_inputs": inventory["fixed_inputs"],
        "historical_audit_fixtures": {"whitehouse_sha256": wh_sha, "oge_124_sha256": oge_sha},
        "historical_source_replay_performed": False,
        "whitehouse_document_counts": dict(sorted(wh_counts.items())),
        "counts": {key: len(value) for key, value in groups.items()},
        "groups": groups,
        "g1_source_binding_complete": False,
        "projection_ready": False,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--whitehouse-audit", type=Path, required=True)
    parser.add_argument("--oge-124-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    report = build_legacy_channel_overlay(inventory,
        whitehouse_audit=args.whitehouse_audit, oge_124_audit=args.oge_124_audit)
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "g1_source_binding_complete": False,
                      "projection_ready": False}, sort_keys=True))


if __name__ == "__main__":
    main()
