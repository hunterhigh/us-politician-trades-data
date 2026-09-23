"""Overlay the complete White House annual review subset on the OGE candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import os

from unison_snapshot.whitehouse_annual_candidate import overlay_whitehouse_annual_candidate


def _write(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--audit-out", required=True, type=Path)
    args = parser.parse_args()
    candidate = json.loads(args.candidate.read_bytes())
    result, audit = overlay_whitehouse_annual_candidate(candidate, args.review_root)
    _write(args.candidate, result)
    _write(args.audit_out, audit)
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
