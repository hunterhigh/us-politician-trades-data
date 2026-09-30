"""Reproduce a read-only wh-url document audit from frozen local files/refs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.whitehouse_candidate_audit import audit_wh_url_documents


def _paths(commit: str) -> list[str]:
    return subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit], text=True).splitlines()


def _verify_blob(commit: str, path: str, content: bytes) -> None:
    listing = subprocess.check_output(["git", "ls-tree", commit, "--", path], text=True)
    expected = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    if not any(line.split()[2] == expected for line in listing.splitlines()):
        raise ValueError(f"local {path} bytes do not match frozen commit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidate_bytes = args.candidate.read_bytes()
    index_bytes = args.index.read_bytes()
    _verify_blob(args.candidate_commit, "candidates/disclosure-current.json", candidate_bytes)
    _verify_blob(args.candidate_commit, "whitehouse/disclosures/index-current.json", index_bytes)
    result = audit_wh_url_documents(
        candidate_bytes, index_bytes,
        _paths(args.evidence_commit), _paths(args.candidate_commit),
        candidate_commit=args.candidate_commit,
        evidence_commit=args.evidence_commit)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
