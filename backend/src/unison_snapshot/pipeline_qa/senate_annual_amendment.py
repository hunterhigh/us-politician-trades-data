"""Verify one Senate annual amendment before rebinding published holding IDs.

The source archive is immutable. Matching a filer and year alone is not enough:
the two complete extracted row sequences and the qualified row positions must
also agree. This module records a transition; it does not retain old IDs in the
new candidate.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from .holdings_rebinding import git_tree_paths
from .migration_inventory import InventoryError, git_object


_SHA = re.compile(r"[0-9a-f]{64}")
_EXTRACTION = "senate-efd-annual-html-2026-09-v2.json"
_FACT_EXCLUDE = {"id", "filing_id", "source_url", "filed_at",
                 "ticker", "ticker_mapping_basis"}


def _json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"invalid {label}")
    return value


def _report(repo: Path, evidence_commit: str, review_commit: str,
            filing_id: str, read_object: Callable, list_paths: Callable) -> tuple[dict, dict]:
    if not re.fullmatch(r"[0-9a-f-]{36}", filing_id):
        raise InventoryError("invalid Senate annual filing ID")
    directory = f"senate_efd/annual/reports/{filing_id}"
    names = list_paths(repo, evidence_commit, directory)
    metadata_names = [name for name in names if name.endswith(".metadata.json")]
    if len(metadata_names) != 1:
        raise InventoryError("Senate annual report must have one archived source version")
    digest = metadata_names[0].removesuffix(".metadata.json")
    if not _SHA.fullmatch(digest):
        raise InventoryError("invalid Senate annual archive digest")
    metadata = _json(read_object(repo, evidence_commit,
                                 f"{directory}/{digest}.metadata.json"), "Senate archive metadata")
    html = read_object(repo, evidence_commit, f"{directory}/{digest}.html")
    if (hashlib.sha256(html).hexdigest() != digest or
            metadata.get("source_sha256") != digest or
            metadata.get("byte_length") != len(html) or
            metadata.get("document_id") != filing_id or
            metadata.get("document_url") !=
            f"https://efdsearch.senate.gov/search/view/annual/{filing_id}/"):
        raise InventoryError("Senate annual archive bytes or metadata mismatch")
    extraction = _json(read_object(
        repo, review_commit,
        f"senate_efd/annual/extractions/{filing_id}/{digest}/{_EXTRACTION}"),
        "Senate annual extraction")
    if (extraction.get("source_sha256") != digest or
            extraction.get("document_id") != filing_id or
            extraction.get("source_url") != metadata["document_url"] or
            extraction.get("amendment_number") != metadata.get("amendment_number") or
            extraction.get("filer_name") != metadata.get("filer_name") or
            extraction.get("report_year") != metadata.get("report_year") or
            extraction.get("portal_listed_date") != metadata.get("portal_listed_date") or
            not isinstance(extraction.get("rows"), list) or
            extraction.get("row_count") != len(extraction["rows"])):
        raise InventoryError("Senate annual extraction is not bound to archived source")
    return metadata, extraction


def _fact(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in _FACT_EXCLUDE}


def _groups(rows: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for row in rows:
        if (row.get("source_id") == "senate_efd" and
                str(row.get("id", "")).startswith("senate-annual:")):
            groups[row["filing_id"]].append(row)
    return dict(groups)


def verified_amendment_rekeys(*, old: dict[str, dict], current: dict[str, dict],
                              repo: Path | None, evidence_commit: str | None,
                              review_commit: str | None,
                              read_object: Callable = git_object,
                              list_paths: Callable = git_tree_paths) -> list[dict]:
    """Return only complete, position-bound report amendment transitions."""
    missing = [old[key] for key in old.keys() - current.keys()]
    added = [current[key] for key in current.keys() - old.keys()]
    old_groups, new_groups = _groups(missing), _groups(added)
    if not old_groups or not new_groups:
        return []
    if (repo is None or not re.fullmatch(r"[0-9a-f]{40}", str(evidence_commit)) or
            not re.fullmatch(r"[0-9a-f]{40}", str(review_commit))):
        return []
    result, used_new = [], set()
    cache = {}
    def report(filing_id):
        if filing_id not in cache:
            cache[filing_id] = _report(repo, evidence_commit, review_commit,
                                       filing_id, read_object, list_paths)
        return cache[filing_id]

    for old_filing, old_rows in sorted(old_groups.items()):
        possible = [filing for filing, rows in new_groups.items()
                    if filing not in used_new and
                    {row.get("person_id") for row in rows} ==
                    {row.get("person_id") for row in old_rows} and
                    {row.get("report_period_end") for row in rows} ==
                    {row.get("report_period_end") for row in old_rows}]
        if len(possible) != 1:
            continue
        new_filing = possible[0]
        old_meta, old_extract = report(old_filing)
        new_meta, new_extract = report(new_filing)
        if (old_meta.get("schema_version") != "senate-efd-annual-archive/v1" or
                new_meta.get("schema_version") != old_meta["schema_version"] or
                old_meta.get("filer_name") != new_meta.get("filer_name") or
                old_meta.get("office") != new_meta.get("office") or
                old_meta.get("report_year") != new_meta.get("report_year") or
                not isinstance(old_meta.get("amendment_number"), int) or
                new_meta.get("amendment_number") != old_meta["amendment_number"] + 1 or
                not old_meta["portal_listed_date"] < new_meta["portal_listed_date"]):
            raise InventoryError("Senate annual replacement lacks an official amendment relationship")
        before, after = old_extract["rows"], new_extract["rows"]
        if (len(before) != len(after) or
                any({key: value for key, value in a.items() if key != "extraction_id"} !=
                    {key: value for key, value in b.items() if key != "extraction_id"}
                    for a, b in zip(before, after))):
            raise InventoryError("Senate annual amendment changed source rows; automatic rekey denied")
        old_by_id = {row["id"]: row for row in old_rows}
        new_by_id = {row["id"]: row for row in new_groups[new_filing]}
        old_positions = {index for index, row in enumerate(before)
                         if row["extraction_id"] in old_by_id}
        new_positions = {index for index, row in enumerate(after)
                         if row["extraction_id"] in new_by_id}
        if (len(old_positions) != len(old_rows) or
                len(new_positions) != len(new_groups[new_filing]) or
                old_positions != new_positions):
            raise InventoryError("Senate annual qualified row positions changed")
        for index in sorted(old_positions):
            old_id, new_id = before[index]["extraction_id"], after[index]["extraction_id"]
            prior, replacement = old_by_id[old_id], new_by_id[new_id]
            if (_fact(prior) != _fact(replacement) or
                    prior.get("source_url") != old_meta["document_url"] or
                    replacement.get("source_url") != new_meta["document_url"] or
                    prior.get("filed_at", "")[:10] != old_meta["portal_listed_date"] or
                    replacement.get("filed_at", "")[:10] != new_meta["portal_listed_date"]):
                raise InventoryError("Senate annual candidate fact changed across amendment")
            result.append({"entity": "reported_holdings", "kind": "rekey",
                           "old_id": old_id, "new_id": new_id,
                           "reason": "official_amendment",
                           "evidence_url": new_meta["document_url"],
                           "source_sha256": new_meta["source_sha256"],
                           "old_source_sha256": old_meta["source_sha256"]})
        used_new.add(new_filing)
    return result
