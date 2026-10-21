"""Compare actual full/scoped candidate and processing status bytes without publishing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from publication_source_scope import apply_scope


ROOT = Path(__file__).resolve().parents[2]


def outputs(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("evidence-root", "review-root", "main-root", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--keep-parser-inputs", action="store_true",
                        help="Compare the input profile used by saved-observation status replay")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-scope-", dir=args.output_dir.parent) as folder:
        tmp = Path(folder)
        def run(label: str) -> dict:
            target = tmp / label
            target.mkdir()
            status_dir = args.output_dir if label == "full" else target / "status"
            command = [sys.executable, "-m", "unison_snapshot", "build-disclosure-candidate",
                       "--base", str(ROOT / "ingest/current.json"), "--harmonize-cutoffs"]
            for source in ("house_clerk", "oge", "senate_efd"):
                command += ["--source", f"{source}={args.review_root}/candidates/sources/{source}-current.json"]
            command += ["--output", str(target / "candidate.json"),
                        "--audit-output", str(target / "candidate-audit.json")]
            subprocess.run(command, cwd=ROOT, check=True)
            subprocess.run([sys.executable, str(ROOT / "backend/scripts/preview_disclosure_status.py"),
                "--evidence-root", str(args.evidence_root), "--review-root", str(args.review_root),
                "--main-root", str(args.main_root), "--code-commit", args.code_commit,
                "--output-dir", str(status_dir)], cwd=ROOT, check=True)
            return {**outputs(target), **{"status/" + k: v for k, v in outputs(status_dir).items()}}

        full = run("full")
        scopes = [apply_scope(args.review_root, "review", keep_parser_inputs=args.keep_parser_inputs),
                  apply_scope(args.evidence_root, "evidence", keep_parser_inputs=args.keep_parser_inputs)]
        scoped = run("scoped")
        if full != scoped:
            differences = sorted(k for k in full.keys() | scoped.keys() if full.get(k) != scoped.get(k))
            raise ValueError(f"Publication source scope changed outputs: {differences}")
        report = dict(candidate_and_status_identical=True, output_hashes=full, scopes=scopes,
                      published=False, code_commit=args.code_commit)
        (args.output_dir / "source-scope-comparison.json").write_text(
            json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
