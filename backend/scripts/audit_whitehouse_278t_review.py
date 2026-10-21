"""Read-only White House 278-T audit of one pinned GitHub review commit.

Usage (from repository root):
  python backend/scripts/audit_whitehouse_278t_review.py --ref review
  python backend/scripts/audit_whitehouse_278t_review.py --commit <40-hex-sha>
  python backend/scripts/audit_whitehouse_278t_review.py --commit <40-hex-sha> --local-git

The API mode uses GITHUB_TOKEN/GH_TOKEN or the local `gh auth token` credential.
The local mode requires the pinned commit and blobs in this Git checkout. No Git
branch, remote object, evidence, review artifact, or production array is changed.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t  # noqa: E402
from unison_snapshot.whitehouse_278t_candidate import audit_whitehouse_278t_exact_source_candidate  # noqa: E402
from unison_snapshot.whitehouse_278t import SUPPORTED_PARSER_VERSIONS  # noqa: E402
from unison_snapshot.oge_278e_public import SUPPORTED_PARSER_VERSIONS as ANNUAL_PARSERS  # noqa: E402
from unison_snapshot.oge_candidate import _identity_key, _person_id  # noqa: E402
from unison_snapshot.whitehouse_directory_identity_shadow import bind_directory_entry  # noqa: E402
from unison_snapshot.whitehouse_annual_candidate import annual_identity_people  # noqa: E402


REPO = "hunterhigh/us-politician-trades-data"
API = f"https://api.github.com/repos/{REPO}"
_SHA1 = re.compile(r"[0-9a-f]{40}")
_KEY_FIGURES = {
    "Trump": ("donald", "trump"),
    "Vance": ("jd", "vance"),
    "Wiles": ("susie", "wiles"),
    "Miller": ("stephen", "miller"),
    "Scavino": ("dan", "scavino"),
    "Hassett": ("kevin", "hassett"),
    "Leavitt": ("karoline", "leavitt"),
    "Bresso": ("gineen", "bresso"),
}


def _token() -> str:
    value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if value:
        return value
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("A GitHub read credential is required") from None


def _get_json(route: str, token: str) -> dict:
    request = urllib.request.Request(API + route, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "User-Agent": "whitehouse-278t-readonly-audit/1",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            if attempt == 2:
                raise RuntimeError(f"GitHub read failed: {route}: {exc}") from None
            time.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


def _blob(sha: str, token: str) -> dict:
    if not _SHA1.fullmatch(sha):
        raise RuntimeError("GitHub tree contains an invalid blob SHA")
    item = _get_json("/git/blobs/" + sha, token)
    if item.get("encoding") != "base64" or item.get("sha") != sha:
        raise RuntimeError("GitHub blob response is invalid")
    content = base64.b64decode(item["content"], validate=False)
    header = f"blob {len(content)}\0".encode()
    if hashlib.sha1(header + content).hexdigest() != sha:
        raise RuntimeError("GitHub blob failed its content hash check")
    return json.loads(content)


def _local_git(*args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT.parent), *args], stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Local Git read failed: {' '.join(args)}: {exc}") from None


def _local_blob(sha: str, _token: str) -> dict:
    if not _SHA1.fullmatch(sha):
        raise RuntimeError("Git tree contains an invalid blob SHA")
    content = _local_git("cat-file", "blob", sha)
    header = f"blob {len(content)}\0".encode()
    if hashlib.sha1(header + content).hexdigest() != sha:
        raise RuntimeError("Local Git blob failed its content hash check")
    return json.loads(content)


def _local_tree(commit: str) -> dict:
    resolved = _local_git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    if resolved != commit:
        raise RuntimeError("Pinned local Git commit did not resolve exactly")
    rows = []
    for entry in _local_git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        _, kind, sha = header.decode("ascii").split()
        if kind == "blob":
            rows.append({"type": kind, "path": path.decode("utf-8"), "sha": sha})
    return {"truncated": False, "tree": rows}


def _names(records: list[dict], field: str) -> set[str]:
    return {record[field] for record in records
            if isinstance(record.get(field), str) and record[field].strip()}


def _current_extraction_path(row: dict, blobs: dict[str, str],
                             parsers: tuple[str, ...]) -> str | None:
    """Resolve only the coverage-selected extraction, never a stale sibling."""
    if row.get("review_state") not in {"extracted_review_only", "extracted_with_issues"}:
        return None
    explicit = row.get("extraction_path")
    if isinstance(explicit, str):
        if explicit not in blobs:
            raise RuntimeError("Coverage-selected White House extraction is missing")
        return explicit
    document_id = row.get("document_id")
    versions = row.get("archive_sha256_versions")
    if (not isinstance(document_id, str) or ":" not in document_id or
            not isinstance(versions, list) or len(versions) != 1 or
            not isinstance(versions[0], str)):
        raise RuntimeError("White House extraction coverage identity is invalid")
    folder = f"whitehouse/extractions/{document_id.split(':', 1)[1]}/{versions[0]}"
    declared = row.get("extraction_parser_version")
    if declared is not None:
        if declared not in parsers:
            raise RuntimeError("White House coverage selects an unsupported parser")
        path = f"{folder}/{declared.replace('/', '-')}.json"
        if path not in blobs:
            raise RuntimeError("Coverage-selected White House extraction is missing")
        return path
    matches = [f"{folder}/{parser.replace('/', '-')}.json" for parser in parsers
               if f"{folder}/{parser.replace('/', '-')}.json" in blobs]
    if not matches:
        raise RuntimeError("White House current extraction is missing")
    return matches[0]


def _key_figure_counts(coverage_rows: list[dict], index_quarantine: list[dict],
                       audit_reports: list[dict]) -> dict:
    summary = {}
    for label, tokens in _KEY_FIGURES.items():
        indexed = [row for row in coverage_rows
                   if all(token in row.get("filer_name_from_label", "").casefold()
                          for token in tokens)]
        excluded = [row for row in index_quarantine
                    if all(token in row.get("label", "").casefold() for token in tokens) and
                    "periodic transaction" in row.get("label", "").casefold()]
        ids = {row["document_id"] for row in indexed}
        audited = [row for row in audit_reports if row["document_id"] in ids]
        summary[label] = {
            "accepted_index_report_count": len(indexed),
            "source_index_quarantined_report_count": len(excluded),
            "archived_report_count": sum(bool(row.get("archive_sha256_versions")) for row in indexed),
            "audited_report_count": len(audited),
            "eligible_report_count": sum(row["status"] == "eligible" for row in audited),
            "eligible_row_count": sum(line["status"] == "eligible"
                                      for row in audited for line in row["rows"]),
            "quarantined_row_count": sum(line["status"] == "quarantined"
                                         for row in audited for line in row["rows"]),
        }
    return summary


def _identity_mapping_diagnostic(audit_reports: list[dict], coverage_rows: list[dict],
                                 directory_rows: list[dict], extractions: list[dict],
                                 candidate: dict) -> dict:
    """Measure ID fragmentation risk without assigning or qualifying people."""
    missing = {row["document_id"] for row in audit_reports
               if "catalog_filer_identity_missing" in row["document_reasons"]}
    coverage = {row["document_id"]: row for row in coverage_rows}
    directory = {row["source_document_id"]: row for row in directory_rows}
    if len(directory) != len(directory_rows):
        raise RuntimeError("White House directory document IDs are duplicated")
    audit_by_id = {row["document_id"]: row for row in audit_reports}
    selected = [row for row in extractions if row["document_id"] in missing]
    if len(selected) != len(missing):
        raise RuntimeError("Identity diagnostic is missing a selected extraction")

    def name_key(value: str) -> tuple[str, ...]:
        return tuple(sorted(re.findall(r"[a-z]+", value.casefold())))

    people = candidate["people"]
    existing_ids = {row["id"] for row in people}
    candidate_names: dict[tuple[str, ...], set[str]] = {}
    for row in people:
        candidate_names.setdefault(name_key(row["display_name"]), set()).add(row["id"])
    proposed_ids = set()
    same_name_ids = set()
    same_name_reports = 0
    roles_by_pdf_name: dict[str, set[str]] = {}
    person_groups: dict[str, dict] = {}
    for row in selected:
        name = row["pdf_filer_name"]
        role = row["pdf_position_title"]
        roles_by_pdf_name.setdefault(name, set()).add(role)
        proposed_ids.add(_person_id(_identity_key({
            "filer_name": name, "agency": row["pdf_agency_label"],
            "position_title": role})))
        matches = candidate_names.get(name_key(name), set())
        if matches:
            same_name_reports += 1
            same_name_ids.update(matches)
        directory_row = directory.get(row["document_id"])
        if directory_row is None:
            raise RuntimeError("Identity diagnostic is missing the official directory entry")
        binding = bind_directory_entry(row, directory_row)
        report = audit_by_id[row["document_id"]]
        group = person_groups.setdefault(name, {
            "pdf_filer_name": name, "directory_labels": set(), "pdf_positions": set(),
            "document_ids": [], "report_count": 0, "source_row_count": 0,
            "identity_only_row_count": 0, "directory_pdf_aligned_report_count": 0,
            "existing_candidate_person_ids": set(), "binding_problems": [],
        })
        group["directory_labels"].add(directory_row["filer_name_from_label"])
        group["pdf_positions"].add(role)
        group["document_ids"].append(row["document_id"])
        group["report_count"] += 1
        group["source_row_count"] += report["source_row_count"]
        group["identity_only_row_count"] += sum(
            source_row["reasons"] == ["catalog_filer_identity_missing"]
            for source_row in report["rows"])
        group["existing_candidate_person_ids"].update(matches)
        if binding["status"] == "directory_pdf_name_aligned":
            group["directory_pdf_aligned_report_count"] += 1
        else:
            group["binding_problems"].append({
                "document_id": row["document_id"], "reason": binding["reason"]})
    groups = []
    for group in person_groups.values():
        for key in ("directory_labels", "pdf_positions", "existing_candidate_person_ids"):
            group[key] = sorted(group[key])
        group["document_ids"].sort()
        group["binding_problems"].sort(key=lambda item: item["document_id"])
        existing = group["existing_candidate_person_ids"]
        group["identity_mapping_state"] = (
            "directory_pdf_unresolved" if group["binding_problems"] else
            "existing_person_ambiguous" if len(existing) > 1 else
            "existing_id_reuse_candidate" if existing else
            "new_person_id_unresolved")
        groups.append(group)
    return {
        "identity_missing_report_count": len(missing),
        "directory_label_count": len({coverage[row_id]["filer_name_from_label"]
                                      for row_id in missing}),
        "pdf_full_name_count": len(roles_by_pdf_name),
        "pdf_names_with_multiple_positions": sorted(
            name for name, roles in roles_by_pdf_name.items() if len(roles) > 1),
        "naive_pdf_role_agency_id_count": len(proposed_ids),
        "naive_ids_matching_existing_candidate_count": len(proposed_ids & existing_ids),
        "same_name_candidate_report_count": same_name_reports,
        "same_name_candidate_person_count": len(same_name_ids),
        "identity_only_row_count": sum(group["identity_only_row_count"] for group in groups),
        "person_groups": sorted(groups, key=lambda group: group["pdf_filer_name"]),
        "qualification_changed": False,
    }


def run(*, ref: str | None, commit: str | None, audit_output: Path | None = None,
        local_git: bool = False, directory_identity_shadow: bool = False,
        exact_source_copies: bool = False, exact_source_bindings: Path | None = None) -> dict:
    bindings = None
    if exact_source_bindings is not None:
        if not exact_source_copies:
            raise RuntimeError("Exact source bindings require --exact-source-copies")
        bindings = json.loads(exact_source_bindings.read_bytes())
    if local_git and commit is None:
        raise RuntimeError("--local-git requires a pinned --commit")
    token = "" if local_git else _token()
    if commit is None:
        if not ref or not re.fullmatch(r"[A-Za-z0-9._/-]+", ref):
            raise RuntimeError("Review ref is invalid")
        pointer = _get_json("/git/ref/heads/" + ref, token)
        commit = pointer["object"]["sha"]
    if not _SHA1.fullmatch(commit):
        raise RuntimeError("Review commit SHA is invalid")
    tree = (_local_tree(commit) if local_git else
            _get_json("/git/trees/" + commit + "?recursive=1", token))
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise RuntimeError("GitHub review tree is incomplete")
    blobs = {item["path"]: item["sha"] for item in tree["tree"]
             if item.get("type") == "blob"}
    required = {
        "whitehouse/coverage-current.json",
        "whitehouse/disclosures/index-current.json",
        "whitehouse/extraction-status.json",
        "oge/whitehouse/coverage-current.json",
        "candidates/sources/oge-current.json",
    }
    if not required.issubset(blobs):
        raise RuntimeError("Pinned review tree lacks required audit inputs")
    if directory_identity_shadow and "whitehouse/annual/filer-reported-current.json" in blobs:
        required.add("whitehouse/annual/filer-reported-current.json")
    blob_reader = _local_blob if local_git else _blob
    contents = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(blob_reader, blobs[path], token): path for path in sorted(required)}
        for future in as_completed(futures):
            contents[futures[future]] = future.result()
    coverage = contents["whitehouse/coverage-current.json"]
    directory = contents["whitehouse/disclosures/index-current.json"]
    status = contents["whitehouse/extraction-status.json"]
    oge_catalog = contents["oge/whitehouse/coverage-current.json"]
    oge_candidate = contents["candidates/sources/oge-current.json"]
    if coverage.get("schema_version") != "whitehouse-public-coverage/v1":
        raise RuntimeError("White House coverage schema is unexpected")
    if directory.get("schema_version") != "whitehouse-public-disclosures-index/v1":
        raise RuntimeError("White House directory index schema is unexpected")
    if directory.get("page_sha256") != coverage.get("index_page_sha256"):
        raise RuntimeError("White House directory and coverage use different source pages")
    coverage_rows = [row for row in coverage["reports"]
                     if row.get("document_type_from_label") == "278t"]
    extraction_paths = sorted(path for row in coverage_rows
                              if (path := _current_extraction_path(
                                  row, blobs, SUPPORTED_PARSER_VERSIONS)) is not None)
    annual_rows = [row for row in coverage["reports"] if isinstance(row, dict) and
                   str(row.get("document_type_from_label", "")).startswith("278e_")]
    annual_paths = sorted(path for row in annual_rows
                          if (path := _current_extraction_path(
                              row, blobs, ANNUAL_PARSERS)) is not None)
    selected = sorted(required | set(extraction_paths) | set(annual_paths))
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(blob_reader, blobs[path], token): path
                   for path in [*extraction_paths, *annual_paths]}
        for future in as_completed(futures):
            contents[futures[future]] = future.result()
    by_id = {row["document_id"]: row for row in coverage_rows}
    if len(by_id) != len(coverage_rows):
        raise RuntimeError("White House 278-T coverage IDs are duplicated")
    extractions = [contents[path] for path in extraction_paths]
    if len({(item["document_id"], item["source_sha256"]) for item in extractions}) != len(extractions):
        raise RuntimeError("White House 278-T extractions are duplicated")
    for item in extractions:
        record = by_id.get(item["document_id"])
        if (record is None or item["source_sha256"] not in record["archive_sha256_versions"] or
                item["source_url"] != record["document_url"]):
            raise RuntimeError("Extraction is not bound to current White House coverage")
    annual_extractions = [contents[path] for path in annual_paths]
    audit = audit_whitehouse_278t(
        extractions, oge_catalog, oge_candidate, annual_extractions)
    shadow = (audit_whitehouse_278t(
        extractions, oge_catalog, oge_candidate, annual_extractions,
        contents["whitehouse/disclosures/index-current.json"],
        annual_identity_people(contents["whitehouse/annual/filer-reported-current.json"])
        if "whitehouse/annual/filer-reported-current.json" in contents else [])
        if directory_identity_shadow else None)
    exact_audit = None
    if exact_source_copies:
        exact_audit = audit_whitehouse_278t_exact_source_candidate(
            oge_candidate, extractions, oge_catalog, annual_extractions=annual_extractions,
            source_bindings=bindings)
        audit = exact_audit["canonical_eligibility"]
        if directory_identity_shadow:
            shadow = audit_whitehouse_278t_exact_source_candidate(
                oge_candidate, extractions, oge_catalog, annual_extractions=annual_extractions, source_bindings=bindings,
                directory_index=directory, annual_identity_people=annual_identity_people(
                    contents["whitehouse/annual/filer-reported-current.json"])
                    if "whitehouse/annual/filer-reported-current.json" in contents else []
            )["canonical_eligibility"]
    if audit_output is not None:
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        audit_output.write_text(json.dumps(exact_audit or audit, ensure_ascii=False, indent=2), encoding="utf-8")
    archived = [row for row in coverage_rows if row.get("archive_sha256_versions")]
    extracted_ids = {item["document_id"] for item in extractions}
    archived_not_extracted = [row for row in archived if row["document_id"] not in extracted_ids]
    eligible = [row for row in audit["reports"] if row["status"] == "eligible"]
    index_quarantine = [row for row in coverage.get("index_quarantine", [])
                        if "periodic transaction" in row.get("label", "").casefold()]
    reason_counts: dict[str, int] = {}
    for report in audit["reports"]:
        for reason in report["document_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    digest = hashlib.sha256("\n".join(f"{path} {blobs[path]}" for path in selected).encode()).hexdigest()
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "review_commit": commit,
        "input_set_sha256": digest,
        "whitehouse_index_page_sha256": coverage.get("index_page_sha256"),
        "oge_catalog_sha256": oge_catalog.get("catalog_sha256"),
        "oge_candidate_blob_sha": blobs["candidates/sources/oge-current.json"],
        "whitehouse_coverage_blob_sha": blobs["whitehouse/coverage-current.json"],
        "extraction_status": status,
        "index_278t_report_count": len(coverage_rows),
        "source_index_quarantined_278t_report_count": len(index_quarantine),
        "official_page_278t_link_count": len(coverage_rows) + len(index_quarantine),
        "archived_278t_report_count": len(archived),
        "archived_278t_label_name_count": len(_names(archived, "filer_name_from_label")),
        "extracted_278t_report_count": len(extractions),
        "archived_not_extracted_278t_report_count": len(archived_not_extracted),
        "archived_not_extracted_278t_names": sorted(_names(
            archived_not_extracted, "filer_name_from_label")),
        "pdf_filer_name_count": len(_names(extractions, "pdf_filer_name")),
        "eligible_report_count": audit["eligible_report_count"],
        "quarantined_report_count": audit["report_count"] - audit["eligible_report_count"],
        "eligible_catalog_person_count": len({row["matched_catalog_identity"]["person_id"]
                                              for row in eligible if row["matched_catalog_identity"]}),
        "eligible_catalog_people": sorted({row["matched_catalog_identity"]["filer_name"]
                                           for row in eligible if row["matched_catalog_identity"]}),
        "eligible_row_count": audit["eligible_row_count"],
        "quarantined_row_count": audit["quarantined_row_count"],
        "source_row_count": audit["source_row_count"],
        "row_conservation_complete": audit["row_conservation_complete"],
        "annual_part7_comparable_row_count": audit["annual_part7_comparable_row_count"],
        "annual_part7_incomparable_row_count": audit["annual_part7_incomparable_row_count"],
        "annual_part7_overlap_count": audit["annual_part7_overlap_count"],
        "document_reason_counts": dict(sorted(reason_counts.items())),
        "identity_mapping_diagnostic": _identity_mapping_diagnostic(
            audit["reports"], coverage_rows, directory["reports"],
            extractions, oge_candidate),
        "key_figures": _key_figure_counts(coverage_rows, index_quarantine, audit["reports"]),
    }
    if shadow is not None:
        old_by_id = {row["document_id"]: row for row in audit["reports"]}
        changed = [row for row in shadow["reports"]
                   if row["document_reasons"] != old_by_id[row["document_id"]]["document_reasons"]]
        identity_only_residual = {}
        identity_only_before = 0
        for report in shadow["reports"]:
            original = old_by_id[report["document_id"]]
            if "catalog_filer_identity_missing" not in original["document_reasons"]:
                continue
            if "catalog_filer_identity_missing" in report["document_reasons"]:
                continue
            for before, after in zip(original["rows"], report["rows"], strict=True):
                if before["reasons"] != ["catalog_filer_identity_missing"]:
                    continue
                identity_only_before += 1
                for reason in after["reasons"]:
                    identity_only_residual[reason] = identity_only_residual.get(reason, 0) + 1
        result["directory_identity_shadow"] = {
            "eligible_row_count": shadow["eligible_row_count"],
            "eligible_row_delta": shadow["eligible_row_count"] - audit["eligible_row_count"],
            "identity_only_row_count_before": identity_only_before,
            "residual_reason_counts": dict(sorted(identity_only_residual.items())),
            "identity_reason_removed_report_count": len(changed),
            "identity_reason_removed_document_ids": sorted(row["document_id"] for row in changed),
            "qualification_written": False,
        }
    if exact_audit is not None:
        result.update(audit_schema=exact_audit["schema_version"],
                      exact_source_aliases=exact_audit["exact_source_aliases"],
                      retained_rows=exact_audit["retained_rows"],
                      retained_existing_transaction_count=exact_audit["retained_existing_transaction_count"])
        if "cross_source_relations" in exact_audit:
            result["cross_source_relations"] = exact_audit["cross_source_relations"]
    return result


def run_local(*, review_root: Path, oge_candidate: Path, audit_output: Path | None = None,
              exact_source_copies: bool = False, exact_source_bindings: Path | None = None) -> dict:
    """Use the existing candidate script's local evidence gates without Git/network reads."""
    from build_whitehouse_278t_candidate import _verify_review, _read_object
    (extractions, annual, coverage, directory, people, provenance, failures) = _verify_review(
        review_root, expected_report_count=None)
    base, base_sha = _read_object(oge_candidate)
    bindings = None
    if exact_source_bindings is not None:
        if not exact_source_copies:
            raise RuntimeError("Exact source bindings require --exact-source-copies")
        bindings, binding_sha = _read_object(exact_source_bindings)
        provenance["exact_source_binding_file_sha256"] = binding_sha
    audit = (audit_whitehouse_278t_exact_source_candidate(
        base, extractions, coverage, annual_extractions=annual, directory_index=directory,
        annual_identity_people=people, source_bindings=bindings) if exact_source_copies else
        audit_whitehouse_278t(extractions, coverage, base, annual, directory, people))
    result = {**audit, "input_provenance": provenance, "base_candidate_sha256": base_sha,
              "extraction_failure_count": failures, "qualification_written": False}
    if audit_output is not None:
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        audit_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ref", default="review")
    source.add_argument("--commit")
    source.add_argument("--review-root", type=Path, help="Audit a local review using the existing candidate input validator")
    parser.add_argument("--oge-candidate", type=Path, help="Required with --review-root")
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--local-git", action="store_true",
                        help="Read the pinned commit and verified blobs from local Git")
    parser.add_argument("--directory-identity-shadow", action="store_true",
                        help="Also calculate the read-only unique existing-person fallback")
    parser.add_argument("--exact-source-copies", action="store_true",
                        help="Opt in to canonical same-PDF SHA aliases; retain raw source URLs and row IDs in audit output")
    parser.add_argument("--exact-source-bindings", type=Path,
                        help="Optional archived cross-source SHA and original row bindings")
    args = parser.parse_args()
    if args.review_root is not None:
        if args.oge_candidate is None:
            parser.error("--review-root requires --oge-candidate")
        result = run_local(review_root=args.review_root, oge_candidate=args.oge_candidate,
                           audit_output=args.audit_output, exact_source_copies=args.exact_source_copies,
                           exact_source_bindings=args.exact_source_bindings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    result = run(ref=args.ref, commit=args.commit, audit_output=args.audit_output,
                 local_git=args.local_git,
                 directory_identity_shadow=args.directory_identity_shadow,
                 exact_source_copies=args.exact_source_copies,
                 exact_source_bindings=args.exact_source_bindings)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
