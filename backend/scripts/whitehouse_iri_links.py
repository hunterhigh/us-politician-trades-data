"""Recover exact Unicode PDF hrefs from the official disclosures HTML index."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from unison_snapshot.whitehouse_iri_links import (
    recover_official_iri_links, verify_recovered_pdfs,
)
from unison_snapshot.whitehouse_disclosures import archive_public_batch


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True,
                        help="Index from whitehouse_disclosures.py sync")
    parser.add_argument("--index-out", type=Path, required=True,
                        help="Recovered index for a subsequent bounded PDF batch")
    parser.add_argument("--audit-out", type=Path, required=True)
    parser.add_argument("--verify", action="store_true",
                        help="Read each recovered PDF and record its SHA-256")
    parser.add_argument("--evidence-root", type=Path,
                        help="Archive recovered PDFs into the evidence checkout")
    parser.add_argument("--batch-out", type=Path,
                        help="Write bounded archive status when evidence root is given")
    parser.add_argument("--limit", type=int, default=11,
                        help="Maximum recovered PDF downloads this run (1-25)")
    parser.add_argument("--start-after-id",
                        help="Rotate bounded retries past the previous attempt")
    args = parser.parse_args(argv)
    if bool(args.evidence_root) != bool(args.batch_out) or not 1 <= args.limit <= 25:
        parser.error("evidence-root and batch-out are required together; limit must be 1-25")
    try:
        index = json.loads(args.index.read_text(encoding="utf-8"))
        recovered, audit = recover_official_iri_links(index)
        if args.verify:
            audit = verify_recovered_pdfs(audit)
        archived_count = None
        if args.evidence_root:
            new_ids = {row["source_document_id"] for row in audit["rows"]
                       if "retrieval_url" in row}
            selected = dict(recovered)
            selected["reports"] = [row for row in recovered["reports"]
                                   if row["source_document_id"] in new_ids]
            selected["report_link_count"] = len(selected["reports"])
            batch = archive_public_batch(args.evidence_root, selected, limit=args.limit,
                                         start_after_id=args.start_after_id)
            _write(args.batch_out, batch)
            by_id = {row["document_id"]: row for row in batch["reports"]}
            for row in audit["rows"]:
                original = by_id.get(row.get("source_document_id"))
                if original:
                    row.update({"status": "official_pdf_archived",
                                "pdf_sha256": original["sha256"],
                                "pdf_byte_length": original["byte_length"],
                                "archive_path": original["archive_path"]})
            archived_count = len(by_id)
            audit["official_pdf_archived_count"] = archived_count
        _write(args.index_out, recovered)
        _write(args.audit_out, audit)
        print(json.dumps({"recovered_urls": audit["recovered_url_count"],
                          "remaining_quarantine": audit["remaining_quarantine_count"],
                          "verified_pdfs": audit.get("official_pdf_verified_count"),
                          "archived_pdfs": archived_count}))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Official Unicode link recovery failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
