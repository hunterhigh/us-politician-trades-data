"""Bounded public White House disclosures ingestion; no Form 201 requests.

Examples (evidence and review are separate checked-out branches):

  python scripts/whitehouse_disclosures.py sync --evidence-root E --index-out R/index.json \
      --iri-audit-out R/iri-audit.json --batch-out R/batch.json --limit 25
  python scripts/whitehouse_disclosures.py crosswalk --evidence-root E \
      --oge-manifest E/oge/catalog/MANIFEST.json --index R/index.json \
      --out R/crosswalk.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from unison_snapshot.whitehouse_disclosure_audit import build_oge_public_crosswalk
from unison_snapshot.whitehouse_disclosures import (
    WhiteHouseDisclosureError, archive_public_batch, archive_public_index,
)
from unison_snapshot.whitehouse_iri_links import recover_official_iri_links


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="archive page and a bounded PDF batch")
    sync.add_argument("--evidence-root", type=Path, required=True)
    sync.add_argument("--index-out", type=Path, required=True)
    sync.add_argument("--iri-audit-out", type=Path, required=True,
                      help="Review audit mapping exact Unicode hrefs to encoded official URIs")
    sync.add_argument("--batch-out", type=Path, required=True)
    sync.add_argument("--limit", type=int, default=25)
    sync.add_argument("--refresh-existing", action="store_true")
    sync.add_argument("--start-after-id")
    audit = commands.add_parser("crosswalk", help="conservative OGE catalog candidate audit")
    audit.add_argument("--evidence-root", type=Path, required=True)
    audit.add_argument("--oge-manifest", type=Path, required=True)
    audit.add_argument("--index", type=Path, required=True)
    audit.add_argument("--out", type=Path, required=True)
    audit.add_argument("--since-catalog-date", default="2025-01-20")
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            raw_index = archive_public_index(args.evidence_root)
            index, iri_audit = recover_official_iri_links(raw_index)
            _write_json(args.index_out, index)
            _write_json(args.iri_audit_out, iri_audit)
            batch = archive_public_batch(
                args.evidence_root, index, limit=args.limit,
                refresh_existing=args.refresh_existing,
                start_after_id=args.start_after_id,
            )
            _write_json(args.batch_out, batch)
            print(json.dumps({"report_links": index["report_link_count"],
                              "index_quarantine": index["quarantine_count"],
                              "official_unicode_hrefs_encoded": iri_audit["recovered_url_count"],
                              "download_attempts": batch["attempted_count"],
                              "pending_urls": batch["pending_url_count"],
                              "download_failures": len(batch["failures"]),
                              "last_attempted_id": batch["last_attempted_id"]}))
        else:
            index = json.loads(args.index.read_text(encoding="utf-8"))
            metadata = json.loads(args.oge_manifest.read_text(encoding="utf-8"))
            result = build_oge_public_crosswalk(
                args.evidence_root, metadata, index,
                since_catalog_date=args.since_catalog_date)
            _write_json(args.out, result)
            print(json.dumps({"request_catalog_occurrences":
                              result["request_catalog_occurrence_count"],
                              "counts_by_status": result["counts_by_status"]}))
    except (WhiteHouseDisclosureError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"White House disclosures failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
