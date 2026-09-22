"""Extract archived White House public OGE forms into review-only evidence.

This command deliberately does not build or publish canonical disclosure facts.
Each extraction is bound to the immutable PDF hash and parser version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_278e_public import (
    PARSER_VERSION as ANNUAL_PARSER_VERSION,
    extract_public_278e_pdf,
)
from unison_snapshot.whitehouse_278t import (
    PARSER_VERSION as TRADE_PARSER_VERSION,
    parse_whitehouse_278t_pdf,
)


SHA = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"wh-url:([0-9a-f]{24})\Z")
METADATA_SCHEMA = "whitehouse-public-disclosures-pdf/v1"
STATUS_SCHEMA = "whitehouse-public-extraction-status/v1"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")), encoding="utf-8")


def _archive_rows(evidence_root: Path) -> list[tuple[dict, Path]]:
    folder = evidence_root / "whitehouse/disclosures/reports"
    rows = []
    for metadata_path in sorted(folder.glob("*/*.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        document_id = metadata.get("document_id")
        sha = metadata.get("sha256")
        match = ID.fullmatch(document_id) if isinstance(document_id, str) else None
        if (metadata.get("schema_version") != METADATA_SCHEMA or
                metadata.get("source_id") != "whitehouse_public_disclosures" or
                match is None or metadata_path.parent.name != match[1] or
                not isinstance(sha, str) or not SHA.fullmatch(sha) or
                metadata_path.name != f"{sha}.json"):
            raise ValueError(f"Invalid White House archive metadata: {metadata_path}")
        pdf = metadata_path.with_suffix(".pdf")
        if (not pdf.is_file() or pdf.stat().st_size != metadata.get("byte_length") or
                metadata.get("archive_path") != pdf.relative_to(evidence_root).as_posix() or
                hashlib.sha256(pdf.read_bytes()).hexdigest() != sha):
            raise ValueError(f"White House PDF archive hash mismatch: {pdf}")
        rows.append((metadata, pdf))
    return sorted(rows, key=lambda item: (
        item[0].get("document_type_from_label") != "278t",
        item[0]["document_id"], item[0]["sha256"],
    ))


def extract_batch(evidence_root: Path, review_root: Path, *, limit: int,
                  start_after_id: str | None = None) -> dict:
    if type(limit) is not int or not 0 <= limit <= 100:
        raise ValueError("White House extraction limit must be between 0 and 100")
    rows = _archive_rows(evidence_root)
    if start_after_id:
        if ID.fullmatch(start_after_id) is None:
            raise ValueError("White House extraction cursor is invalid")
        boundary = next((index for index, (metadata, _) in enumerate(rows)
                         if metadata["document_id"] == start_after_id), None)
        if boundary is not None:
            rows = rows[boundary + 1:] + rows[:boundary + 1]
    attempted = 0
    created = 0
    failures = []
    skipped = 0
    last_attempted_id = start_after_id
    for metadata, pdf in rows:
        kind = metadata.get("document_type_from_label")
        version = TRADE_PARSER_VERSION if kind == "278t" else ANNUAL_PARSER_VERSION
        suffix = version.replace("/", "-")
        target = (review_root / "whitehouse/extractions" /
                  metadata["document_id"].split(":", 1)[1] /
                  metadata["sha256"] / f"{suffix}.json")
        if target.is_file():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if (existing.get("source_sha256") != metadata["sha256"] or
                    existing.get("source_url") != metadata.get("document_url") or
                    existing.get("parser_version") != version):
                raise ValueError(f"White House extraction evidence conflict: {target}")
            skipped += 1
            continue
        if attempted >= limit:
            continue
        attempted += 1
        last_attempted_id = metadata["document_id"]
        try:
            name = metadata.get("filer_name_from_label")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("White House link has no filer name for PDF identity binding")
            common = {"source_url": metadata["document_url"],
                      "source_sha256": metadata["sha256"]}
            if kind == "278t":
                result = parse_whitehouse_278t_pdf(
                    pdf, **common, document_id=metadata["document_id"],
                    filer_name=name,
                    amended_label=metadata.get("link_label"))
            else:
                result = extract_public_278e_pdf(pdf, **common, expected_filer=name)
            if (result.get("source_sha256") != metadata["sha256"] or
                    result.get("source_url") != metadata["document_url"] or
                    result.get("parser_version") != version):
                raise ValueError("White House parser returned unbound evidence")
            _write(target, result)
            created += 1
        except (OgeCatalogError, ValueError, OSError) as exc:
            failures.append({"document_id": metadata["document_id"],
                             "sha256": metadata["sha256"],
                             "reason": str(exc)})
    return {"schema_version": STATUS_SCHEMA, "source_id": "whitehouse_public_disclosures",
            "archived_version_count": len(rows), "attempted_count": attempted,
            "last_attempted_id": last_attempted_id,
            "extraction_created_count": created, "existing_extraction_count": skipped,
            "pending_count": len(rows) - skipped - created,
            "failure_count": len(failures), "failures": failures,
            "production_qualification": "not_attempted"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--start-after-id")
    args = parser.parse_args(argv)
    try:
        status = extract_batch(args.evidence_root, args.review_root, limit=args.limit,
                               start_after_id=args.start_after_id)
        _write(args.review_root / "whitehouse/extraction-status.json", status)
        print(json.dumps({key: status[key] for key in (
            "archived_version_count", "attempted_count", "extraction_created_count",
            "pending_count", "failure_count")}))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"White House extraction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
