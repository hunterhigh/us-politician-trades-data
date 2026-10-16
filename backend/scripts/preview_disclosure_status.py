"""Exercise status generation against frozen repository artifacts without publishing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_disclosure_status import fixed_tree
from unison_snapshot.builder import Bundle
from unison_snapshot.codec import digest, encode
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.disclosure_status_house import build_house_status
from unison_snapshot.disclosure_status_senate import build_senate_status
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.public_repo import PublicSnapshotRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--main-root", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    provenance = {"code_commit": args.code_commit, "evidence_commit": fixed_tree(args.evidence_root),
                  "review_commit": fixed_tree(args.review_root), "previous_main_commit": fixed_tree(args.main_root)}
    manifest = json.loads((args.main_root / "manifest.json").read_text(encoding="utf-8"))
    board_bytes = (args.main_root / "board" / f"{manifest['board']}.json").read_bytes()
    if digest(board_bytes) != manifest["board"]:
        raise ValueError("Preview board does not match the fixed publication manifest")
    board = json.loads(board_bytes)
    scopes = []
    for adapter in (build_house_status, build_senate_status, build_oge_status):
        scopes.extend(adapter(args.evidence_root, args.review_root,
                              board["transactions"], board["reported_holdings"]))
    bundle = attach_disclosure_status(
        Bundle({"manifest.json": encode(manifest), f"board/{manifest['board']}.json": board_bytes}, manifest), scopes,
        transactions=board["transactions"], provenance=provenance)

    class PreparedTransport:
        def get(self, url: str, limit: int, *, api: bool = False) -> bytes:
            marker = f"/{provenance['previous_main_commit']}/"
            if api or marker not in url:
                raise ValueError("Preview reader attempted an unpinned read")
            content = bundle.files[url.split(marker, 1)[1]]
            if len(content) > limit:
                raise ValueError("Preview reader exceeded the status size budget")
            return content

    reader = PublicSnapshotRepository("preview", "preview", transport=PreparedTransport())
    status = reader.disclosure_status(provenance["previous_main_commit"], include_records=True)
    for key, root in (("evidence_commit", args.evidence_root), ("review_commit", args.review_root),
                      ("previous_main_commit", args.main_root)):
        if fixed_tree(root) != provenance[key]:
            raise ValueError("Preview source tree changed during the run")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path, content in bundle.files.items():
        if path.startswith("board/"):
            continue
        target = args.output_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    report = {"published": False, "provenance": provenance,
              "snapshot_id": manifest["snapshot_id"], "board_sha256": manifest["board"],
              "transaction_count": len(board["transactions"]),
              "status_reference": bundle.manifest["disclosure_status"],
              "source_counts": {s["scope_id"]: s["counts"] for s in status["sources"]},
              "full_status_readback_verified": True}
    (args.output_dir / "preview-report.json").write_bytes(encode(report))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
