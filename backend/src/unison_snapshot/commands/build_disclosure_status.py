"""Build processing observations from frozen existing trees, without source collection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

from unison_snapshot.codec import digest, encode
from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.disclosure_status_senate import build_senate_status
from unison_snapshot.disclosure_status_oge import build_oge_status


def fixed_tree(root: Path) -> str:
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Status source tree must have a fixed commit")
    if subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"], text=True).strip():
        raise ValueError("Status source tree has modified or untracked input artifacts")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--previous-main-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for value in (args.code_commit, args.previous_main_commit):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("Status provenance requires fixed commit hashes")
    evidence_commit, review_commit = fixed_tree(args.evidence_root), fixed_tree(args.review_root)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if payload.get("meta", {}).get("is_demo") is not False:
        raise ValueError("Processing status requires a production candidate")
    transactions, holdings = payload["transactions"], payload["reported_holdings"]
    scopes = []
    for adapter in (build_house_status, build_senate_status, build_oge_status):
        scopes.extend(adapter(args.evidence_root, args.review_root, transactions, holdings))
    # Recheck after reading so a local process cannot silently modify a frozen tree.
    if fixed_tree(args.evidence_root) != evidence_commit or fixed_tree(args.review_root) != review_commit:
        raise ValueError("Status source trees changed during generation")
    result = {"schema_version": "disclosure-status-input/v1",
              "transactions_sha256": digest(encode(transactions)),
              "provenance": {"code_commit": args.code_commit, "evidence_commit": evidence_commit,
                             "review_commit": review_commit,
                             "previous_main_commit": args.previous_main_commit},
              "scopes": scopes}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps({"scopes": len(scopes), "records": sum(len(s["reports"]) for s in scopes),
                      "evidence_commit": evidence_commit, "review_commit": review_commit,
                      "status_input_sha256": digest(encode(result))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
