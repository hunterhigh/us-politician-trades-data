"""Read-only White House 278-T audit of one pinned GitHub review commit.

Usage (from repository root):
  python backend/scripts/audit_whitehouse_278t_review.py --ref review
  python backend/scripts/audit_whitehouse_278t_review.py --commit <40-hex-sha>

Uses GITHUB_TOKEN/GH_TOKEN or the local `gh auth token` credential.  No Git
branch, remote object, evidence, review artifact, or production array is changed.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
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


REPO = "hunterhigh/us-politician-trades-data"
API = f"https://api.github.com/repos/{REPO}"
_SHA1 = re.compile(r"[0-9a-f]{40}")
_EXTRACTION_PATH = re.compile(
    r"whitehouse/extractions/[^/]+/[0-9a-f]{64}/whitehouse-278t-pdf-v1\.json")
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
        except (urllib.error.URLError, TimeoutError) as exc:
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


def _names(records: list[dict], field: str) -> set[str]:
    return {record[field] for record in records
            if isinstance(record.get(field), str) and record[field].strip()}


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


def run(*, ref: str | None, commit: str | None, audit_output: Path | None = None) -> dict:
    token = _token()
    if commit is None:
        if not ref or not re.fullmatch(r"[A-Za-z0-9._/-]+", ref):
            raise RuntimeError("Review ref is invalid")
        pointer = _get_json("/git/ref/heads/" + ref, token)
        commit = pointer["object"]["sha"]
    if not _SHA1.fullmatch(commit):
        raise RuntimeError("Review commit SHA is invalid")
    tree = _get_json("/git/trees/" + commit + "?recursive=1", token)
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise RuntimeError("GitHub review tree is incomplete")
    blobs = {item["path"]: item["sha"] for item in tree["tree"]
             if item.get("type") == "blob"}
    required = {
        "whitehouse/coverage-current.json",
        "whitehouse/extraction-status.json",
        "oge/whitehouse/coverage-current.json",
        "candidates/sources/oge-current.json",
    }
    if not required.issubset(blobs):
        raise RuntimeError("Pinned review tree lacks required audit inputs")
    extraction_paths = sorted(path for path in blobs if _EXTRACTION_PATH.fullmatch(path))
    selected = sorted(required | set(extraction_paths))
    contents = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_blob, blobs[path], token): path for path in selected}
        for future in as_completed(futures):
            contents[futures[future]] = future.result()
    coverage = contents["whitehouse/coverage-current.json"]
    status = contents["whitehouse/extraction-status.json"]
    oge_catalog = contents["oge/whitehouse/coverage-current.json"]
    oge_candidate = contents["candidates/sources/oge-current.json"]
    if coverage.get("schema_version") != "whitehouse-public-coverage/v1":
        raise RuntimeError("White House coverage schema is unexpected")
    coverage_rows = [row for row in coverage["reports"]
                     if row.get("document_type_from_label") == "278t"]
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
    audit = audit_whitehouse_278t(extractions, oge_catalog, oge_candidate)
    if audit_output is not None:
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        audit_output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    archived = [row for row in coverage_rows if row.get("archive_sha256_versions")]
    eligible = [row for row in audit["reports"] if row["status"] == "eligible"]
    index_quarantine = [row for row in coverage.get("index_quarantine", [])
                        if "periodic transaction" in row.get("label", "").casefold()]
    reason_counts: dict[str, int] = {}
    for report in audit["reports"]:
        for reason in report["document_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    digest = hashlib.sha256("\n".join(f"{path} {blobs[path]}" for path in selected).encode()).hexdigest()
    return {
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
        "pdf_filer_name_count": len(_names(extractions, "pdf_filer_name")),
        "eligible_report_count": audit["eligible_report_count"],
        "quarantined_report_count": audit["report_count"] - audit["eligible_report_count"],
        "eligible_catalog_person_count": len({row["matched_catalog_identity"]["person_id"]
                                              for row in eligible if row["matched_catalog_identity"]}),
        "eligible_catalog_people": sorted({row["matched_catalog_identity"]["filer_name"]
                                           for row in eligible if row["matched_catalog_identity"]}),
        "eligible_row_count": audit["eligible_row_count"],
        "quarantined_row_count": audit["quarantined_row_count"],
        "document_reason_counts": dict(sorted(reason_counts.items())),
        "key_figures": _key_figure_counts(coverage_rows, index_quarantine, audit["reports"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ref", default="review")
    source.add_argument("--commit")
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    result = run(ref=args.ref, commit=args.commit, audit_output=args.audit_output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
