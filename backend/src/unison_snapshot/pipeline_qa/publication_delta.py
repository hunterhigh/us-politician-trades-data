"""Check canonical publication facts and report only snapshot changes.

Old IDs are compared at this boundary; they are not copied into new facts.
An explicit review decision can account for a rekey, correction or withdrawal.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .migration_inventory import InventoryError, _differences, _market_enrichment_only
from .holdings_rebinding import _archive, _official_index_replacement, git_tree_paths
from .migration_inventory import git_object
from .senate_annual_amendment import verified_amendment_rekeys

ENTITIES = ("people", "transactions", "reported_holdings")
SOURCES = ("house_clerk", "oge", "senate_efd")
REASONS = {"official_index_replacement", "official_amendment", "source_identity_correction",
           "duplicate_resolution", "official_withdrawal"}


def _index(rows, label):
    if not isinstance(rows, list):
        raise InventoryError(f"{label} is not an array")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in result:
            raise InventoryError(f"{label} has an invalid or repeated ID")
        result[row["id"]] = row
    return result


def _source_id(row, entity):
    return row.get("disclosure_authority" if entity == "people" else "source_id")


def _decision(item, entity, old, current, *, repo, evidence_commit, verified_replacements):
    kind, old_id, new_id = (item.get(key) for key in ("kind", "old_id", "new_id"))
    if (item.get("entity") != entity or kind not in {"rekey", "corrected", "withdrawn"} or
            not isinstance(old_id, str) or old_id not in old or item.get("reason") not in REASONS or
            not re.fullmatch(r"[0-9a-f]{64}", str(item.get("source_sha256")))):
        raise InventoryError("invalid fact transition")
    url = item.get("evidence_url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if (not parsed or parsed.scheme != "https" or parsed.username or parsed.password or
            not parsed.hostname or not (parsed.hostname in {
                "disclosures-clerk.house.gov", "clerk.house.gov", "efdsearch.senate.gov",
                "www.whitehouse.gov", "oge.gov"} or parsed.hostname.endswith(".oge.gov"))):
        raise InventoryError("transition needs an official evidence URL")
    if kind == "rekey":
        if (not isinstance(new_id, str) or new_id == old_id or old_id in current or
                new_id in old or new_id not in current or
                set(_differences(old[old_id], current[new_id])) !=
                {"id", "filing_id", "source_url"}):
            raise InventoryError("rekey must preserve one fact's semantic fields")
        if (entity != "reported_holdings" or item["reason"] != "official_index_replacement" or
                repo is None or not re.fullmatch(r"[0-9a-f]{40}", str(evidence_commit)) or
                old[old_id].get("source_url") == current[new_id].get("source_url") or
                url != current[new_id].get("source_url")):
            raise InventoryError("rekey needs a fixed official replacement archive")
        evidence_key = (old[old_id]["source_url"], url, item["source_sha256"])
        if evidence_key not in verified_replacements:
            prior = _archive(repo, evidence_commit, old[old_id]["source_url"], git_object, git_tree_paths)
            replacement = _archive(repo, evidence_commit, url, git_object, git_tree_paths)
            if (replacement["sha256"] != item["source_sha256"] or
                    prior["filer_name_from_label"] != replacement["filer_name_from_label"] or
                    prior["report_year_from_label"] != replacement["report_year_from_label"] or
                    not _official_index_replacement(repo, evidence_commit, prior, replacement, git_object)):
                raise InventoryError("official replacement evidence does not verify")
            verified_replacements.add(evidence_key)
    elif kind == "corrected":
        raise InventoryError("correction needs a source-specific evidence verifier")
    else:
        raise InventoryError("withdrawal needs a source-specific evidence verifier")
    return {"entity": entity, "kind": kind, "old_id": old_id, "new_id": new_id,
            "reason": item["reason"], "evidence_url": url,
            "source_sha256": item["source_sha256"],
            **({"changed_fields": item["changed_fields"]} if kind == "corrected" else {})}


def build_publication_delta(*, previous_board, unified, prepared, sources,
                            transitions=None, main_commit=None, review_commit=None,
                            repo=None, evidence_commit=None):
    if (unified.get("meta", {}).get("is_demo") is not False or
            prepared.get("meta", {}).get("is_demo") is not False or
            set(sources) != set(SOURCES) or
            any(value.get("meta", {}).get("is_demo") is not False for value in sources.values()) or
            unified["meta"].get("data_cutoff_at") != prepared["meta"].get("data_cutoff_at")):
        raise InventoryError("publication input is not one official cutoff")
    transitions = transitions or {"schema_version": "pipeline-fact-transitions/v1", "records": []}
    decisions = transitions.get("records")
    if transitions.get("schema_version") != "pipeline-fact-transitions/v1" or not isinstance(decisions, list):
        raise InventoryError("invalid transition file")
    if decisions and (not re.fullmatch(r"[0-9a-f]{40}", str(main_commit)) or
                      not re.fullmatch(r"[0-9a-f]{40}", str(review_commit)) or
                      transitions.get("from_main_commit") != main_commit or
                      transitions.get("to_review_commit") != review_commit or
                      transitions.get("evidence_commit") != evidence_commit):
        raise InventoryError("transition file does not bind fixed commits")
    changes, counts, used, verified_replacements = [], {}, set(), set()
    verified_amendments = 0
    for entity in ENTITIES:
        old = _index(previous_board.get(entity), f"previous {entity}")
        proposed = _index(unified.get(entity), f"review {entity}")
        current = _index(prepared.get(entity), f"prepared {entity}")
        by_source = {name: _index(value.get(entity), f"{name} {entity}")
                     for name, value in sources.items()}
        if proposed.keys() != current.keys():
            raise InventoryError(f"prepared {entity} IDs differ from review")
        for record_id, row in current.items():
            source_id = _source_id(row, entity)
            source_row = by_source.get(source_id, {}).get(record_id)
            review_row = proposed[record_id]
            if source_row is None:
                raise InventoryError(f"{entity}/{record_id} has no source candidate")
            differences = _differences(row, review_row)
            source_differences = _differences(review_row, source_row)
            if ((differences and not _market_enrichment_only(row, review_row, differences)) or
                (source_differences and not _market_enrichment_only(review_row, source_row, source_differences))):
                raise InventoryError(f"{entity}/{record_id} has a source field conflict")
            if entity != "people" and any(value.get("verification_status") != "official_matched"
                                          for value in (row, review_row, source_row)):
                raise InventoryError(f"{entity}/{record_id} lacks official qualification")
        selected = [item for item in decisions if isinstance(item, dict) and item.get("entity") == entity]
        old_seen, new_seen = set(), set()
        for item in selected:
            checked = _decision(item, entity, old, current, repo=repo,
                                evidence_commit=evidence_commit,
                                verified_replacements=verified_replacements)
            old_id, new_id = checked["old_id"], checked["new_id"]
            if old_id in old_seen or (checked["kind"] == "rekey" and new_id in new_seen):
                raise InventoryError("transition repeats an old or new ID")
            old_seen.add(old_id)
            if checked["kind"] == "rekey":
                new_seen.add(new_id)
            changes.append(checked)
            used.add(id(item))
        if entity == "reported_holdings":
            automatic = verified_amendment_rekeys(
                old=old, current=current, repo=repo,
                evidence_commit=evidence_commit, review_commit=review_commit)
            for item in automatic:
                if item["old_id"] in old_seen or item["new_id"] in new_seen:
                    raise InventoryError("amendment transition conflicts with an explicit decision")
                old_seen.add(item["old_id"])
                new_seen.add(item["new_id"])
                changes.append(item)
                verified_amendments += 1
            selected = selected + automatic
        missing = old.keys() - current.keys()
        changed = {key for key in old.keys() & current.keys() if old[key] != current[key]}
        if (missing != {item["old_id"] for item in selected if item["kind"] in {"rekey", "withdrawn"}} or
                changed != {item["old_id"] for item in selected if item["kind"] == "corrected"}):
            raise InventoryError(f"{entity} has an unexplained removal or field change")
        added = current.keys() - old.keys() - new_seen
        changes.extend({"entity": entity, "kind": "new", "new_id": key,
                        "source_id": _source_id(current[key], entity)} for key in sorted(added))
        counts[entity] = {"previous": len(old), "prepared": len(current),
                          "unchanged": len(old.keys() & current.keys()) - len(changed),
                          "new": len(added), **dict(Counter(item["kind"] for item in selected))}
    if len(used) != len(decisions):
        raise InventoryError("transition has an unknown entity or invalid shape")
    if not isinstance(prepared.get("security_market_data"), list) or not isinstance(prepared.get("source_health"), list):
        raise InventoryError("prepared candidate lacks market or source health")
    return {"schema_version": "pipeline-publication-delta/v1", "counts": counts,
            "market_count": len(prepared["security_market_data"]),
            "source_health_count": len(prepared["source_health"]),
            "changes": changes, "canonical_source_alignment_complete": True,
            "snapshot_changes_disposed": True,
            "transition_verification_level": (
                "archived_official_index_and_senate_amendment_bytes"
                if verified_replacements and verified_amendments else
                "archived_official_index_bytes_for_rekeys" if verified_replacements else
                "archived_senate_amendment_bytes_and_rows" if verified_amendments else
                "not_applicable_no_rekeys"),
            "publication_pointer_moved": False}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("previous-board", "unified", "prepared", "source-root", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--transitions", type=Path)
    parser.add_argument("--main-commit")
    parser.add_argument("--review-commit")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--evidence-commit")
    args = parser.parse_args()
    def read(path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise InventoryError(f"expected object: {path}")
        return value
    sources = {name: read(args.source_root / f"{name}-current.json") for name in SOURCES}
    report = build_publication_delta(previous_board=read(args.previous_board),
        unified=read(args.unified), prepared=read(args.prepared), sources=sources,
        transitions=read(args.transitions) if args.transitions else None,
        main_commit=args.main_commit, review_commit=args.review_commit,
        repo=args.repo, evidence_commit=args.evidence_commit)
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    args.output.write_bytes(raw)
    print(json.dumps({"counts": report["counts"], "change_count": len(report["changes"]),
                      "report_sha256": hashlib.sha256(raw).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
