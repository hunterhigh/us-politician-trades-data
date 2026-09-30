"""Replay one quarantined Senate paper PTR row from fixed official evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.senate_paper_sample import replay_fixed_row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True,
                        help="Git repository containing the pinned evidence commit")
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--fixture", type=Path,
                        help="Compare exact observations with a saved JSON fixture")
    args = parser.parse_args()
    result = replay_fixed_row(args.repo, executable=args.tesseract)
    if args.fixture is not None:
        expected = json.loads(args.fixture.read_text(encoding="utf-8"))
        if result != expected:
            raise SystemExit("Senate paper fixed-row observation differs from fixture")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
