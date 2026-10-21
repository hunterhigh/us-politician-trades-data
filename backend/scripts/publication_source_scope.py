"""Exclude collection/parser-only files from disposable publication worktrees."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


# Keep all other paths, including future qualified outputs and status inputs.
# The fixed-input preview compares full and scoped candidate/status bytes.
EXCLUDED = {
    "review": ("whitehouse/ocr-checkpoints/",),
    "evidence": ("oge/catalog/pages/",),
}


def git(root: Path, *args: str, input: str | None = None) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   input=input, text=True, encoding="utf-8")


def apply_scope(root: Path, kind: str, *, keep_parser_inputs: bool = False) -> dict:
    root = root.resolve(strict=True)
    commit = git(root, "rev-parse", "HEAD").strip()
    # Status may replay saved source observations rather than consume only facts.
    excluded = () if keep_parser_inputs else EXCLUDED[kind]
    patterns = "/*\n" + "".join("!/" + prefix + "\n" for prefix in excluded)
    git(root, "sparse-checkout", "set", "--no-cone", "--stdin", input=patterns)
    # --no-checkout worktrees have no index yet; populate it after setting scope.
    git(root, "checkout", "--detach", commit)
    if git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Publication source scope requires a clean worktree")
    entries = []
    for entry in git(root, "ls-tree", "-r", "-z", commit).split("\0"):
        if entry:
            metadata, path = entry.split("\t", 1)
            entries.append((*metadata.split(), path))
    omitted = [(sha, path) for mode, typ, sha, path in entries
               if typ == "blob" and path.startswith(excluded)]
    if any((root / path).exists() for _, path in omitted):
        raise ValueError("Excluded publication files were materialized")
    # Enumerate only stored objects. Asking Git for the size of a missing blob
    # would lazily download it and defeat the scope being measured.
    present = set(git(root, "cat-file", "--batch-all-objects",
                      "--batch-check=%(objectname)").splitlines())
    selected = [root / path for _, typ, _, path in entries
                if typ == "blob" and not path.startswith(excluded)]
    result = dict(kind=kind, commit=commit,
                  profile="parser-safe" if keep_parser_inputs else "publication",
                  omitted_files=len(omitted),
                  omitted_blobs_already_present=len({sha for sha, _ in omitted} & present),
                  selected_files=len(selected), selected_bytes=sum(p.stat().st_size for p in selected))
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=EXCLUDED, required=True)
    parser.add_argument("--keep-parser-inputs", action="store_true",
                        help="Keep source pages and checkpoints required by saved-observation status replay")
    args = parser.parse_args()
    apply_scope(args.root, args.kind, keep_parser_inputs=args.keep_parser_inputs)
