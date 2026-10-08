"""Build a pinned five-array migration candidate from qualified legacy facts.

This is an offline candidate, not a publisher. Market pages are read and
content-verified from the market commit named by the formal manifest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .migration_inventory import InventoryError, _load, _differences, git_object

FACTS = ("people", "transactions", "reported_holdings")


def build_g2_candidate(*, repo: Path, inventory: dict[str, Any], gate: dict[str, Any],
                       read_object: Callable = git_object) -> tuple[dict[str, Any], dict[str, Any]]:
    fixed = inventory.get("fixed_inputs", {})
    if (inventory.get("schema_version") != "pipeline-migration-inventory/v1" or
            inventory.get("market", {}).get("verification_status") != "content_verified" or
            gate.get("schema_version") != "pipeline-g1-legacy-gate/v1" or
            gate.get("g1_legacy_candidate_binding_complete") is not True or
            gate.get("market_page_bytes_verified") is not True or
            {key: value for key, value in gate.get("fixed_inputs", {}).items()
             if key != "old_main_commit"} != fixed):
        raise InventoryError("G2 requires one pinned G1 gate with verified market pages")
    main, review, market = (fixed[key] for key in ("main_commit", "review_commit", "market_commit"))
    manifest_raw = read_object(repo, main, "manifest.json")
    if hashlib.sha256(manifest_raw).hexdigest() != fixed["manifest_sha256"]:
        raise InventoryError("formal manifest changed")
    manifest = _load(manifest_raw, "formal manifest")
    if manifest.get("market_commit") != market or manifest.get("board") != fixed["board_sha256"]:
        raise InventoryError("formal manifest references changed")
    candidate_raw = read_object(repo, review, "candidates/disclosure-current.json")
    if hashlib.sha256(candidate_raw).hexdigest() != fixed["unified_candidate_sha256"]:
        raise InventoryError("unified candidate changed")
    source = _load(candidate_raw, "unified candidate")
    if source.get("meta", {}).get("is_demo") is not False:
        raise InventoryError("demo candidate cannot enter G2")
    board_raw = read_object(repo, main, f"board/{fixed['board_sha256']}.json")
    if hashlib.sha256(board_raw).hexdigest() != fixed["board_sha256"]:
        raise InventoryError("formal board changed")
    board = _load(board_raw, "formal board")

    facts: dict[str, list[dict[str, Any]]] = {}
    enrichment = {}
    for name in FACTS:
        formal = {row["id"]: row for row in board[name]}
        proposed = {row["id"]: row for row in source[name]}
        if (len(formal) != len(board[name]) or len(proposed) != len(source[name]) or
                formal.keys() != proposed.keys() or
                {row["id"] for row in inventory["records"][name]} != formal.keys()):
            raise InventoryError(f"G2 {name} ID conservation failed")
        rows = []
        changed = 0
        for record_id, row in sorted(proposed.items()):
            result = dict(row)
            differences = _differences(formal[record_id], row)
            if differences:
                if set(differences) - {"ticker", "ticker_mapping_basis"} or name == "people":
                    raise InventoryError(f"G2 unexplained field change: {name}/{record_id}")
                for field in differences:
                    if row.get(field) is not None or formal[record_id].get(field) is None:
                        raise InventoryError(f"G2 invalid market enrichment: {name}/{record_id}")
                    result[field] = formal[record_id][field]
                changed += 1
            if result != formal[record_id]:
                raise InventoryError(f"G2 formal field mismatch: {name}/{record_id}")
            rows.append(result)
        facts[name] = rows
        enrichment[name] = changed

    page_hashes = manifest.get("market_pages")
    if not isinstance(page_hashes, list) or len(page_hashes) != inventory["market"]["page_count"]:
        raise InventoryError("G2 market page list changed")
    market_rows: dict[str, dict[str, Any]] = {}
    page_bytes = 0
    for page_hash in page_hashes:
        raw = read_object(repo, market, f"market-pages/{page_hash}.json")
        if hashlib.sha256(raw).hexdigest() != page_hash:
            raise InventoryError(f"G2 market page changed: {page_hash}")
        page_bytes += len(raw)
        page = _load(raw, f"market page {page_hash}")
        for row in page.get("security_market_data", []):
            ticker = row.get("ticker") if isinstance(row, dict) else None
            if not isinstance(ticker, str) or ticker in market_rows:
                raise InventoryError("G2 duplicate or invalid market ticker")
            market_rows[ticker] = row
    if (len(market_rows) != inventory["market"]["ticker_count"] or
            set(market_rows) != {row["ticker"] for row in inventory["market"]["records"]}):
        raise InventoryError("G2 market ticker inventory changed")
    health = manifest.get("source_health")
    if health != inventory["source_health"]["records"]:
        raise InventoryError("G2 source health changed")
    result = {"meta": dict(source["meta"]), **facts,
              "security_market_data": [market_rows[key] for key in sorted(market_rows)],
              "source_health": health}
    result["meta"].update(market_commit=market, market_pages=page_hashes,
                          migration_binding="legacy_qualified_binding")
    report = {"schema_version": "pipeline-g2-candidate-projection/v1",
              "fixed_inputs": fixed,
              "counts": {**{name: len(facts[name]) for name in FACTS},
                         "security_market_data": len(market_rows), "source_health": len(health)},
              "legacy_market_enrichment": enrichment,
              "market_page_count": len(page_hashes), "market_page_bytes": page_bytes,
              "formal_fact_ids_and_fields_preserved": True,
              "market_pages_content_verified": True,
              "published": False}
    return result, report


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "inventory", "gate", "output", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    gate = json.loads(args.gate.read_text(encoding="utf-8"))
    candidate, report = build_g2_candidate(repo=args.repo, inventory=inventory, gate=gate)
    raw = json.dumps(candidate, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    args.output.write_bytes(raw)
    report["candidate_sha256"] = hashlib.sha256(raw).hexdigest()
    args.report.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
