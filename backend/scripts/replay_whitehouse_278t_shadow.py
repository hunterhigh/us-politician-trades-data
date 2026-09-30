"""Reconcile 18 frozen White House 278-T PDFs with review rows and candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.whitehouse_278t_shadow_replay import replay_278t_shadow


def _verify_review_path(commit: str, path: str, blob: str) -> None:
    listing = subprocess.check_output(["git", "ls-tree", commit, "--", path], text=True)
    if not any(line.split()[2] == blob for line in listing.splitlines()):
        raise ValueError(f"review extraction is not in fixed commit: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--document-audit", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--summary-out", type=Path, required=True)
    parser.add_argument("--rows-out", type=Path, required=True)
    args = parser.parse_args()
    candidate_bytes = args.candidate.read_bytes()
    document_audit = json.loads(args.document_audit.read_text(encoding="utf-8"))
    sources = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    if (sources["candidate_commit"] != document_audit["candidate_commit"] or
            sources["candidate_sha256"] != hashlib.sha256(candidate_bytes).hexdigest() or
            sources["evidence_commit"] != document_audit["evidence_commit"]):
        raise ValueError("source manifest is not bound to the candidate audit")
    bundles = {}
    for doc in sources["documents"]:
        _verify_review_path(sources["candidate_commit"],
                            doc["review_extraction_path"],
                            doc["review_extraction_git_blob"])
        stem = doc["document_id"].removeprefix("wh-url:")
        bundles[doc["document_id"]] = (
            (args.pdf_dir / f"{stem}.pdf").read_bytes(),
            (args.review_dir / f"{stem}.json").read_bytes(),
            doc["review_extraction_git_blob"])
    result = replay_278t_shadow(candidate_bytes, document_audit, bundles)
    rows_text = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")) + "\n" for row in result["rows"])
    summary = {key: value for key, value in result.items() if key != "rows"}
    summary["rows_jsonl_sha256"] = hashlib.sha256(rows_text.encode("utf-8")).hexdigest()
    args.summary_out.write_text(json.dumps(summary, indent=2, sort_keys=True,
                                           ensure_ascii=False) + "\n", encoding="utf-8")
    args.rows_out.write_bytes(rows_text.encode("utf-8"))
    print(f"{result['document_count']} documents, {result['source_row_count']} rows, "
          f"{result['dispositions']}")


if __name__ == "__main__":
    main()
