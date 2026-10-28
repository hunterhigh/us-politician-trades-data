"""Replay the narrow Senate account-spacing correction from pinned source inputs.

Neither a candidate audit nor a transition receipt can grant permission. The
complete grid extraction is recomputed from saved words and archived pixels.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile

from .source_objects import InventoryError, _differences, git_object


def _json(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise InventoryError("Senate correction input must be an object")
    return value


def _safe(value, pattern, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise InventoryError(f"invalid Senate correction {label}")
    return value


def _replay(repo, evidence_commit, review_commit, filing, source_url, read_object):
    from ..senate_paper import PAPER_EXTRACTION_SCHEMA, replay_legacy_grid_extraction
    from ..senate_reports import parse_paper_viewer_manifest

    def read(commit, path):
        return read_object(repo, commit, path)

    status_raw = read(review_commit, "status/senate_efd.json")
    status = _json(status_raw)
    version = _safe(status.get("report_parser_version"), r"[A-Za-z0-9_-]+", "parser version")
    catalog = _safe(status.get("catalog_sha256"), r"[0-9a-f]{64}", "catalog hash")
    manifest_path = f"senate_efd/paper_pages/{filing}/manifest.json"
    manifest_raw = read(evidence_commit, manifest_path)
    manifest = _json(manifest_raw)
    digest = _safe(manifest.get("entrypoint_source_sha256"), r"[0-9a-f]{64}", "source hash")
    manifest_body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    encoded = (json.dumps(manifest_body, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode()
    if hashlib.sha256(encoded).hexdigest() != manifest.get("manifest_sha256"):
        raise InventoryError("Senate correction manifest hash differs")
    prefix = f"senate_efd/reports/{filing}/{digest}"
    metadata = _json(read(evidence_commit, prefix + ".metadata.json"))
    viewer = parse_paper_viewer_manifest(metadata, read(evidence_commit, prefix + ".html"))
    if (metadata.get("document_url") != source_url or viewer.get("document_id") != filing or
            any(manifest.get(key) != viewer[key] for key in viewer if key != "pages") or
            [{key: page[key] for key in ("page_number", "source_url")} for page in manifest["pages"]]
            != viewer["pages"]):
        raise InventoryError("Senate correction manifest differs from archived viewer")
    discovery_path = f"senate_efd/discoveries/{catalog}.json"
    discovery_raw = read(review_commit, discovery_path)
    reports = [row for row in _json(discovery_raw)["reports"] if row.get("document_id") == filing]
    if (len(reports) != 1 or any(reports[0].get(key) != metadata.get(key) for key in
            ("source_id", "access_method", "document_id", "document_url", "filer_name",
             "portal_listed_date", "report_label_date", "report_amendment_number"))):
        raise InventoryError("Senate correction catalog differs from archived report identity or dates")
    extraction_path = f"senate_efd/extractions/{filing}/{digest}/{version}.json"
    extraction = _json(read(review_commit, extraction_path))
    if (extraction.get("schema_version") != PAPER_EXTRACTION_SCHEMA or
            extraction.get("source_id") != "senate_efd" or extraction.get("source_sha256") != digest or
            extraction.get("document_id") != filing or extraction.get("source_url") != source_url or
            extraction.get("parser_version") != version or extraction.get("evidence_complete") is not True):
        raise InventoryError("Senate correction extraction version or source differs")
    roster = _safe(status.get("identity_roster_sha256"), r"[0-9a-f]{64}", "roster hash")
    congress = status.get("identity_congress_roster_sha256")
    if congress is not None:
        _safe(congress, r"[0-9a-f]{64}", "Congress roster hash")
    binding = hashlib.sha256(f"{roster}:{congress or ''}".encode("ascii")).hexdigest()
    identities = _json(read(review_commit, f"senate_efd/identities/{catalog}/{binding}.json"))
    if (identities.get("schema_version") != "senate-efd-identities/v1" or
            identities.get("source_id") != "senate_efd" or identities.get("catalog_sha256") != catalog or
            identities.get("roster_sha256") != roster or
            identities.get("congress_roster_sha256") != congress):
        raise InventoryError("Senate correction identity batch differs")
    matches = [item for item in identities["identities"] if item.get("document_id") == filing]
    if (len(matches) != 1 or matches[0].get("status") != "matched_automatically" or
            matches[0].get("match_class") not in {"exact", "alias"} or
            matches[0].get("filer_name") != metadata.get("filer_name") or
            matches[0].get("roster_sha256") != roster or
            matches[0].get("congress_roster_sha256") != congress):
        raise InventoryError("Senate correction identity is not qualified")
    replay_path = extraction.get("grid_replay_input", {}).get("path")
    _safe(replay_path, r"senate_efd/paper_grid_inputs/[0-9a-f]{64}\.json", "replay path")
    with tempfile.TemporaryDirectory(prefix="senate-correction-") as temporary:
        root = Path(temporary)
        def save(directory, path, raw):
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        review, evidence = root / "review", root / "evidence"
        for path, raw in (("status/senate_efd.json", status_raw),
                          (discovery_path, discovery_raw),
                          (replay_path, read(review_commit, replay_path))):
            save(review, path, raw)
        save(evidence, manifest_path, manifest_raw)
        for page in manifest["pages"]:
            path = _safe(page.get("archive_path"), r"senate_efd/paper_pages/[A-Za-z0-9_./-]+", "page path")
            if ".." in path.split("/"):
                raise InventoryError("Senate correction page path escaped archive")
            raw = read(evidence_commit, path)
            if hashlib.sha256(raw).hexdigest() != page.get("sha256"):
                raise InventoryError("Senate correction archived page hash differs")
            save(evidence, path, raw)
        replay_legacy_grid_extraction(extraction, evidence, review)
    return extraction, matches[0]


def verified_account_corrections(*, old, current, repo, evidence_commit, review_commit,
                                 read_object=git_object):
    """Authorize only the exact old fact recovered by undoing proven token spaces."""
    possible = [key for key in old.keys() & current.keys()
                if old[key].get("source_id") == current[key].get("source_id") == "senate_efd"
                and _differences(old[key], current[key]) == ["asset_name"]]
    if not possible:
        return []
    if repo is None or not all(re.fullmatch(r"[0-9a-f]{40}", str(commit))
                               for commit in (evidence_commit, review_commit)):
        raise InventoryError("Senate correction needs fixed review and evidence commits")
    from ..senate import SenateEfdError
    from ..senate_candidate import _source_account_whitespace_correction, _transaction
    cache, result = {}, []
    try:
        for key in sorted(possible):
            prior, replacement = old[key], current[key]
            filing = _safe(replacement.get("filing_id"), r"[0-9a-f-]{36}", "filing ID")
            url = f"https://efdsearch.senate.gov/search/view/paper/{filing}/"
            if replacement.get("source_url") != url:
                raise InventoryError("Senate correction is limited to paper source facts")
            if filing not in cache:
                cache[filing] = _replay(repo, evidence_commit, review_commit, filing, url, read_object)
            extraction, identity = cache[filing]
            rows = [row for row in extraction["transactions"] if row.get("extraction_id") == key]
            if len(rows) != 1 or extraction.get("portal_listed_date") != replacement.get("filed_at", "")[:10]:
                raise InventoryError("Senate correction has no unique qualified source row")
            fact, reasons = _transaction(rows[0], extraction, identity)
            proof = _source_account_whitespace_correction(rows[0], fact, prior) if fact else None
            if reasons or fact != replacement or proof is None:
                raise InventoryError("Senate correction does not reproduce the exact old and new facts")
            result.append({"entity": "transactions", "kind": "corrected", "old_id": key,
                           "new_id": key, "reason": "source_identity_correction",
                           "evidence_url": url, "source_sha256": extraction["source_sha256"],
                           "changed_fields": ["asset_name"]})
    except (SenateEfdError, OSError, ValueError, KeyError, TypeError) as exc:
        raise InventoryError(f"Senate account correction source verification failed: {exc}") from exc
    return result
