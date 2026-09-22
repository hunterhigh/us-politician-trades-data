"""Replay a pinned review commit's public White House annual 278e holdings audit.

Read-only: downloads GitHub review artifacts and matching official PDFs into a
temporary cache, never writes to the repository or publishes canonical facts.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_278e_audit import audit_public_278e  # noqa: E402
from unison_snapshot.oge_278e_public import extract_public_278e_pdf  # noqa: E402


REPO = "hunterhigh/us-politician-trades-data"
ANNUAL_TYPES = {"Annual", "Annual Term"}


def _get(url: str, *, token: str | None = None) -> bytes:
    headers = {"User-Agent": "whitehouse-278e-readonly-audit", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=50) as response:
                return response.read()
        except (IncompleteRead, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))
    raise AssertionError("retry loop exhausted")


def _api(path: str, token: str) -> dict:
    return json.loads(_get(f"https://api.github.com/repos/{REPO}/{path}", token=token))


def _tree_child(parent: dict, name: str) -> str:
    return next(row["sha"] for row in parent["tree"] if row["path"] == name and row["type"] == "tree")


def _blob(row: dict, token: str) -> tuple[str, dict]:
    blob = _api(f"git/blobs/{row['sha']}", token)
    if blob.get("encoding") != "base64":
        raise ValueError(f"Unexpected GitHub blob encoding: {row['path']}")
    raw = base64.b64decode(blob["content"])
    if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["sha"]:
        raise ValueError(f"GitHub blob SHA mismatch: {row['path']}")
    return row["path"], json.loads(raw)


def _reparse(record: dict, original: dict) -> dict:
    try:
        pdf = _get(original["source_url"])
        if hashlib.sha256(pdf).hexdigest() != original["source_sha256"]:
            record["reparse_error"] = "official_pdf_sha256_differs_from_review_archive"
            return record
        with tempfile.TemporaryDirectory(prefix="wh-278e-audit-") as temporary:
            pdf_path = Path(temporary) / "report.pdf"
            pdf_path.write_bytes(pdf)
            current = extract_public_278e_pdf(pdf_path, source_url=original["source_url"],
                                               source_sha256=original["source_sha256"],
                                               expected_filer=original["filer_name"])
            record["reparse_matches_review_extraction"] = current == original
            audit = audit_public_278e(current)
            record["current_audit"] = {"source_holdings_eligible": audit["source_holdings_eligible"],
                                       "source_report_eligible": audit["source_report_eligible"],
                                       "holding_blocking_reasons": audit["holding_blocking_reasons"],
                                       "report_blocking_reasons": audit["report_blocking_reasons"],
                                       "source_candidate_holding_count": audit["source_candidate_holding_count"],
                                       "disposition_counts": audit["disposition_counts"]}
    except Exception as exc:
        record["reparse_error"] = f"{type(exc).__name__}: {exc}"
    return record


def replay(ref: str, *, workers: int = 12, reparse: bool = False,
           artifact_version: int = 2) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Pin --ref to a 40-character review commit SHA")
    token = subprocess.run(["gh", "auth", "token"], check=True, capture_output=True,
                           text=True).stdout.strip()
    commit = _api(f"git/commits/{ref}", token)
    root = _api(f"git/trees/{commit['tree']['sha']}", token)
    whitehouse = _api(f"git/trees/{_tree_child(root, 'whitehouse')}", token)
    extraction_tree_sha = _tree_child(whitehouse, "extractions")
    extraction_tree = _api(f"git/trees/{extraction_tree_sha}?recursive=1", token)
    if extraction_tree.get("truncated"):
        raise ValueError("GitHub extraction tree truncated")
    filename = f"/whitehouse-278e-positioned-text-v{artifact_version}.json"
    rows = [row for row in extraction_tree["tree"] if row["type"] == "blob" and
            row["path"].endswith(filename)]
    coverage_blob = next(row for row in whitehouse["tree"] if row["path"] == "coverage-current.json")
    coverage = _api(f"git/blobs/{coverage_blob['sha']}", token)
    coverage_raw = base64.b64decode(coverage["content"])
    coverage_data = json.loads(coverage_raw)
    report_index = {row["document_id"].removeprefix("wh-url:"): row
                    for row in coverage_data["reports"]}
    parsed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_blob, row, token): row for row in rows}
        for future in as_completed(futures):
            path, extraction = future.result()
            document_id, source_sha, _ = path.split("/")
            if extraction.get("source_sha256") != source_sha:
                raise ValueError(f"Source SHA mismatch in {path}")
            if document_id not in report_index or source_sha not in report_index[document_id]["archive_sha256_versions"]:
                raise ValueError(f"Extraction missing from review coverage: {path}")
            if extraction.get("source_url") != report_index[document_id]["document_url"]:
                raise ValueError(f"Source URL mismatch in {path}")
            parsed.append((path, extraction, report_index[document_id]))
    annual = [(path, extraction, index) for path, extraction, index in parsed
              if extraction.get("report_type") in ANNUAL_TYPES]
    results = []
    reparses = []
    for path, original, index in sorted(annual, key=lambda x: x[1].get("filer_name", "")):
        historical_audit = audit_public_278e(original)
        record = {"document_id": index["document_id"], "filer_name": original.get("filer_name"),
                  "report_type": original.get("report_type"), "cover_report_year": original.get("cover_report_year"),
                  "source_url": original.get("source_url"), "source_sha256": original.get("source_sha256"),
                  "review_extraction_path": path, "review_parser_version": original.get("parser_version"),
                  "review_state": index["review_state"],
                  "review_audit": {"source_holdings_eligible": historical_audit["source_holdings_eligible"],
                                   "source_report_eligible": historical_audit["source_report_eligible"],
                                   "holding_blocking_reasons": historical_audit["holding_blocking_reasons"],
                                   "report_blocking_reasons": historical_audit["report_blocking_reasons"],
                                   "source_candidate_holding_count": historical_audit["source_candidate_holding_count"],
                                   "holding_row_reason_counts": dict(Counter(reason for row in historical_audit["holding_row_audit"]
                                                                        for reason in row["reasons"])),
                                   "disposition_counts": historical_audit["disposition_counts"]}}
        results.append(record)
        if reparse:
            reparses.append((record, original))
    if reparse:
        with ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
            list(pool.map(lambda pair: _reparse(*pair), reparses))
    return {"schema_version": "whitehouse-278e-review-replay/v1", "review_commit": ref,
            "review_tree": commit["tree"]["sha"], "coverage_blob_sha": coverage_blob["sha"],
            "index_page_sha256": coverage_data["index_page_sha256"],
            "review_total_links": coverage_data["report_link_count"],
            "review_archived_link_count": sum(bool(row["archive_sha256_versions"]) for row in coverage_data["reports"]),
            "review_state_counts": coverage_data["counts_by_review_state"],
            "artifact_version": artifact_version,
            "review_278e_extraction_count": len(parsed), "review_annual_extraction_count": len(annual),
            "review_annual_version_blocked_count": sum("untrusted_extraction_version" in r["review_audit"]["holding_blocking_reasons"] for r in results),
            "review_holdings_eligible_count": sum(r["review_audit"]["source_holdings_eligible"] for r in results),
            "review_report_eligible_count": sum(r["review_audit"]["source_report_eligible"] for r in results),
            "review_quarantined_count": sum(bool(r["review_audit"]["disposition_counts"]["quarantined"]) for r in results),
            "review_blocker_counts": dict(Counter(reason for r in results for reason in r["review_audit"]["holding_blocking_reasons"])),
            "current_reparse_success_count": sum("current_audit" in r for r in results),
            "current_reparse_matches_review_count": sum(r.get("reparse_matches_review_extraction", False) for r in results),
            "current_holdings_eligible_count": sum(r.get("current_audit", {}).get("source_holdings_eligible", False) for r in results),
            "current_report_eligible_count": sum(r.get("current_audit", {}).get("source_report_eligible", False) for r in results),
            "current_quarantined_count": sum(bool(r.get("current_audit", {}).get("disposition_counts", {}).get("quarantined")) for r in results),
            "current_blocker_counts": dict(Counter(reason for r in results for reason in
                                                    r.get("current_audit", {}).get("holding_blocking_reasons", []))),
            "reports": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="Pinned remote review commit SHA")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--artifact-version", type=int, choices=(1, 2), default=2)
    parser.add_argument("--reparse", action="store_true", help="Reparse SHA-matching official PDFs with current parser")
    parser.add_argument("--output", type=Path, help="Write JSON outside checkout; stdout by default")
    args = parser.parse_args()
    result = replay(args.ref, workers=args.workers, reparse=args.reparse,
                    artifact_version=args.artifact_version)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
