"""List candidate differences for later targeted review without blocking construction.

This report is diagnostic. The existing release readiness and publication delta
gates decide whether a candidate may move production pointers.
"""
from __future__ import annotations

import json
from pathlib import Path

from .migration_inventory import InventoryError, _differences, _market_enrichment_only

FACTS = ("people", "transactions", "reported_holdings")


def _rows(value: dict, entity: str, label: str) -> dict[str, dict]:
    rows = value.get(entity)
    if not isinstance(rows, list):
        raise InventoryError(f"{label} {entity} must be an array")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in indexed:
            raise InventoryError(f"{label} {entity} has an invalid or repeated ID")
        indexed[row["id"]] = row
    return indexed


def collect_projection_issues(*, governed: dict, reference: dict,
                              previous_board: dict) -> dict:
    """Collect every unverified difference; never decide source truth here."""
    if (governed.get("meta", {}).get("is_demo") is not False or
            reference.get("meta", {}).get("is_demo") is not False):
        raise InventoryError("projection comparison requires official candidates")
    issues = []
    counts = {}
    for entity in FACTS:
        current = _rows(governed, entity, "governed")
        prior = _rows(previous_board, entity, "previous")
        frozen = _rows(reference, entity, "review")
        for record_id in sorted(frozen.keys() - current.keys()):
            issues.append({"entity": entity, "id": record_id,
                           "reason": "missing_from_governed_candidate"})
        for record_id in sorted(current.keys() - frozen.keys()):
            issues.append({"entity": entity, "id": record_id,
                           "reason": "new_since_review_candidate"})
        for record_id in sorted(current.keys() & frozen.keys()):
            fields = _differences(current[record_id], frozen[record_id])
            if fields:
                issues.append({"entity": entity, "id": record_id,
                               "reason": "changed_since_review_candidate", "fields": fields})
        for record_id in sorted(prior.keys() - current.keys()):
            issues.append({"entity": entity, "id": record_id,
                           "reason": "missing_from_previous_publication"})
        for record_id in sorted(prior.keys() & current.keys()):
            fields = _differences(prior[record_id], current[record_id])
            if fields and not _market_enrichment_only(prior[record_id], current[record_id], fields):
                issues.append({"entity": entity, "id": record_id,
                               "reason": "changed_from_previous_publication", "fields": fields})
        counts[entity] = {"governed": len(current), "review": len(frozen),
                          "previous": len(prior), "new_from_previous": len(current.keys() - prior.keys())}
    for field in ("data_cutoff_at",):
        if governed["meta"].get(field) != reference["meta"].get(field):
            issues.append({"entity": "meta", "reason": "changed_since_review_candidate",
                           "fields": [field]})
    if governed.get("source_health") != reference.get("source_health"):
        issues.append({"entity": "source_health", "reason": "changed_since_review_candidate"})
    return {"schema_version": "pipeline-projection-issues/v1", "counts": counts,
            "issue_count": len(issues), "issues": issues,
            "targeted_review_deferred_until_after_construction": True,
            "publication_authorized": False}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("governed", "reference", "previous-board", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()

    def read(path: Path) -> dict:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise InventoryError(f"expected JSON object: {path}")
        return value

    report = collect_projection_issues(
        governed=read(args.governed), reference=read(args.reference),
        previous_board=read(args.previous_board))
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "issue_count": report["issue_count"]},
                     sort_keys=True))


if __name__ == "__main__":
    main()
