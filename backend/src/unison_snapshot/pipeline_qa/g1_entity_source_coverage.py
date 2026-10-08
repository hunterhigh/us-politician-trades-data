"""Bind formal holdings and people to pinned legacy source decisions."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from .holdings_rebinding import git_tree_paths
from .migration_inventory import (InventoryError, _differences, _load,
                                  _market_enrichment_only, git_object, SHA)


def _same_formal(candidate: dict[str, Any], formal: dict[str, Any], record_id: str) -> None:
    fields = _differences(formal, candidate)
    if fields and not _market_enrichment_only(formal, candidate, fields):
        raise InventoryError(f"qualified holding field mismatch: {record_id}: {fields}")


def build_g1_entity_source_coverage(
    *, repo: Path, inventory: dict[str, Any],
    read_object: Callable = git_object, list_paths: Callable = git_tree_paths,
) -> dict[str, Any]:
    fixed = inventory.get("fixed_inputs", {})
    commit = fixed.get("review_commit")
    if (inventory.get("schema_version") != "pipeline-migration-inventory/v1" or
            not isinstance(commit, str) or not SHA.fullmatch(commit)):
        raise InventoryError("expected a pinned inventory")
    holdings = inventory["records"]["reported_holdings"]
    by_id = {row["id"]: row for row in holdings}
    if len(by_id) != len(holdings) or any(row.get("issues") for row in holdings):
        raise InventoryError("formal holding inventory has duplicate or unresolved IDs")
    source_records = {}

    paths = list_paths(repo, commit, "house_clerk/holding_qualifications")
    for relative in paths:
        if not re.fullmatch(r"20\d\d/\d+/[0-9a-f]{64}\.json", relative):
            raise InventoryError(f"unexpected House holding qualification path: {relative}")
        path = f"house_clerk/holding_qualifications/{relative}"
        raw = read_object(repo, commit, path)
        decision = _load(raw, path)
        doc_id = relative.split("/")[1]
        source_hash = relative.rsplit("/", 1)[-1][:-5]
        rows = decision.get("holdings")
        if (decision.get("source_sha256") != source_hash or
                not isinstance(rows, list) or
                (rows and decision.get("production_eligible") is not True)):
            raise InventoryError(f"invalid House holding qualification: {path}")
        for row in rows:
            record_id = row.get("id") if isinstance(row, dict) else None
            formal = by_id.get(record_id)
            if (record_id in source_records or not formal or formal["source_id"] != "house_clerk" or
                    formal.get("filing_id") != doc_id):
                raise InventoryError(f"House holding ID/source mismatch: {record_id}")
            _same_formal(row, formal["canonical_record"], record_id)
            source_records[record_id] = {"id": record_id, "source_id": "house_clerk",
                                         "source_sha256": source_hash,
                                         "qualification_sha256": hashlib.sha256(raw).hexdigest(),
                                         "verification_level": "legacy_qualified_row_archived_hash_not_reverified"}

    senate_path = "senate_efd/annual/current.json"
    senate_raw = read_object(repo, commit, senate_path)
    senate = _load(senate_raw, senate_path)
    senate_rows = senate.get("reported_holdings")
    if not isinstance(senate_rows, list) or senate.get("holding_count") != len(senate_rows):
        raise InventoryError("invalid Senate annual candidate holdings")
    senate_reports = {report["document_id"]: report for report in senate.get("reports", [])
                      if report.get("publication_state") == "qualified"}
    for row in senate_rows:
        record_id = row.get("id") if isinstance(row, dict) else None
        formal = by_id.get(record_id)
        report = senate_reports.get(row.get("filing_id"))
        if (record_id in source_records or not formal or formal["source_id"] != "senate_efd" or
                not report or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("source_sha256")))):
            raise InventoryError(f"Senate annual holding ID/source mismatch: {record_id}")
        _same_formal(row, formal["canonical_record"], record_id)
        source_records[record_id] = {"id": record_id, "source_id": "senate_efd",
                                     "source_sha256": report["source_sha256"],
                                     "qualification_sha256": hashlib.sha256(senate_raw).hexdigest(),
                                     "verification_level": "legacy_annual_candidate_archived_hash_not_reverified"}

    wh_path = "whitehouse/annual/filer-reported-current.json"
    wh_raw = read_object(repo, commit, wh_path)
    wh = _load(wh_raw, wh_path)
    eligible = [row for row in wh.get("holdings", []) if row.get("source_holdings_eligible") is True]
    if wh.get("source_eligible_holding_count") != len(eligible):
        raise InventoryError("White House eligible holding count mismatch")
    for row in eligible:
        record_id = row.get("row_id")
        formal = by_id.get(record_id)
        if (record_id in source_records or not formal or formal["source_id"] != "oge" or
                formal.get("filing_id") != row.get("document_id") or
                formal.get("source_url") != row.get("source_url") or
                row.get("row_blocking_reasons") or
                any(formal["canonical_record"].get(key) != row.get(key)
                    for key in ("asset_name", "value_low", "value_high", "report_period_end")) or
                not re.fullmatch(r"[0-9a-f]{64}", str(row.get("source_sha256")))):
            raise InventoryError(f"White House eligible holding mismatch: {record_id}")
        source_records[record_id] = {"id": record_id, "source_id": "oge",
                                     "source_sha256": row["source_sha256"],
                                     "qualification_sha256": hashlib.sha256(wh_raw).hexdigest(),
                                     "verification_level": "legacy_eligible_row_archived_hash_not_reverified"}
    if set(source_records) != set(by_id):
        raise InventoryError("not all formal holdings have a fixed source qualification")

    people = inventory["records"]["people"]
    person_ids = {row["id"] for row in people}
    refs = {row.get("person_id") for name in ("transactions", "reported_holdings")
            for row in inventory["records"][name]}
    if len(person_ids) != len(people) or person_ids != refs or any(row.get("issues") for row in people):
        raise InventoryError("person identity candidate/ref coverage is incomplete")
    person_records = [{"id": row["id"], "source_id": row["source_id"],
                       "candidate_sha256": row["source_candidate_sha256"],
                       "verification_level": "legacy_source_identity_candidate"}
                      for row in sorted(people, key=lambda item: item["id"])]
    counts = Counter(row["source_id"] for row in source_records.values())
    return {"schema_version": "pipeline-g1-entity-source-coverage/v1",
            "fixed_inputs": fixed,
            "holding_counts": dict(sorted(counts.items())),
            "holding_total": len(source_records), "person_total": len(person_records),
            "house_qualification_file_count": len(paths),
            "holding_records": sorted(source_records.values(), key=lambda row: row["id"]),
            "person_records": person_records,
            "holdings_people_legacy_source_binding_complete": True,
            "original_source_bytes_reverified": False,
            "projection_ready": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    report = build_g1_entity_source_coverage(repo=args.repo, inventory=inventory)
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({"holding_counts": report["holding_counts"],
                      "holding_total": report["holding_total"],
                      "person_total": report["person_total"],
                      "holdings_people_legacy_source_binding_complete": True,
                      "projection_ready": False}, sort_keys=True))


if __name__ == "__main__":
    main()
