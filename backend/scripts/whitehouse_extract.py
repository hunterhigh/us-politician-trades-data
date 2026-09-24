"""Extract archived White House public OGE forms into review-only evidence.

This command deliberately does not build or publish canonical disclosure facts.
Each extraction is bound to the immutable PDF hash and parser version.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_278e_public import (
    OcrCheckpointPending,
    PARSER_VERSION as ANNUAL_PARSER_VERSION,
    TRUMP_2025_PARSER_VERSION,
    extract_public_278e_pdf,
    extract_public_278e_pdf_checkpointed,
    parser_version_for_source,
)
from unison_snapshot.whitehouse_278t import (
    PARSER_VERSION as TRADE_PARSER_VERSION,
    parser_version_for_source as trade_parser_version_for_source,
    parse_whitehouse_278t_pdf,
)
from unison_snapshot.whitehouse_disclosures import (
    PDF_ARCHIVE_SCHEMA, PDF_CHUNK_ARCHIVE_SCHEMA, read_archived_pdf,
)


SHA = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"wh-url:([0-9a-f]{24})\Z")
STATUS_SCHEMA = "whitehouse-public-extraction-status/v1"
FAILURE_SCHEMA = "whitehouse-public-extraction-failure/v1"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")), encoding="utf-8")


def _archive_rows(evidence_root: Path) -> list[tuple[dict, Path | None]]:
    folder = evidence_root / "whitehouse/disclosures/reports"
    rows = []
    for metadata_path in sorted(folder.glob("*/*.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        document_id = metadata.get("document_id")
        sha = metadata.get("sha256")
        match = ID.fullmatch(document_id) if isinstance(document_id, str) else None
        if (metadata.get("schema_version") not in {PDF_ARCHIVE_SCHEMA, PDF_CHUNK_ARCHIVE_SCHEMA} or
                metadata.get("source_id") != "whitehouse_public_disclosures" or
                match is None or metadata_path.parent.name != match[1] or
                not isinstance(sha, str) or not SHA.fullmatch(sha) or
                metadata_path.name != f"{sha}.json"):
            raise ValueError(f"Invalid White House archive metadata: {metadata_path}")
        read_archived_pdf(evidence_root, metadata)
        pdf = metadata_path.with_suffix(".pdf") if metadata["schema_version"] == PDF_ARCHIVE_SCHEMA else None
        rows.append((metadata, pdf))
    return sorted(rows, key=lambda item: (
        item[0].get("document_type_from_label") != "278t",
        item[0]["document_id"], item[0]["sha256"],
    ))


def _select_fixed_source(
        rows: list[tuple[dict, Path | None]], *, document_id: str | None,
        source_sha256: str | None) -> list[tuple[dict, Path | None]]:
    """Select one immutable archived source or preserve the normal full queue."""

    if document_id is None and source_sha256 is None:
        return rows
    if (not isinstance(document_id, str) or ID.fullmatch(document_id) is None or
            not isinstance(source_sha256, str) or SHA.fullmatch(source_sha256) is None):
        raise ValueError(
            "Fixed White House replay requires a valid document id and source SHA-256")
    selected = [item for item in rows
                if item[0]["document_id"] == document_id and
                item[0]["sha256"] == source_sha256]
    if len(selected) != 1:
        raise ValueError("Fixed White House replay source is missing or ambiguous")
    return selected


def extract_batch(evidence_root: Path, review_root: Path, *, limit: int,
                  start_after_id: str | None = None,
                  ocr_page_limit: int = 50,
                  document_id: str | None = None,
                  source_sha256: str | None = None) -> dict:
    if type(limit) is not int or not 0 <= limit <= 100:
        raise ValueError("White House extraction limit must be between 0 and 100")
    if type(ocr_page_limit) is not int or not 25 <= ocr_page_limit <= 100:
        raise ValueError("White House OCR page limit must be between 25 and 100")
    all_rows = _archive_rows(evidence_root)
    rows = _select_fixed_source(all_rows, document_id=document_id,
                                source_sha256=source_sha256)
    if document_id is not None and start_after_id is not None:
        raise ValueError("Fixed White House replay does not accept a queue cursor")
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
    known_failures = 0
    recorded_failures = 0
    checkpoint_pending = []
    last_attempted_id = start_after_id
    for metadata, pdf in rows:
        kind = metadata.get("document_type_from_label")
        version = (trade_parser_version_for_source(
                       metadata.get("document_url"), metadata.get("sha256"))
                   if kind == "278t" else
                   parser_version_for_source(metadata.get("document_url"),
                                             metadata.get("sha256")))
        suffix = version.replace("/", "-")
        target = (review_root / "whitehouse/extractions" /
                  metadata["document_id"].split(":", 1)[1] /
                  metadata["sha256"] / f"{suffix}.json")
        failure_target = target.with_suffix(".failure.json")
        if target.is_file():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if (existing.get("source_sha256") != metadata["sha256"] or
                    existing.get("source_url") != metadata.get("document_url") or
                    existing.get("parser_version") != version):
                raise ValueError(f"White House extraction evidence conflict: {target}")
            skipped += 1
            continue
        if failure_target.is_file():
            prior = json.loads(failure_target.read_text(encoding="utf-8"))
            if (prior.get("schema_version") != FAILURE_SCHEMA or
                    prior.get("document_id") != metadata["document_id"] or
                    prior.get("source_sha256") != metadata["sha256"] or
                    prior.get("source_url") != metadata.get("document_url") or
                    prior.get("parser_version") != version):
                raise ValueError(f"White House extraction failure evidence conflict: {failure_target}")
            resumable = (kind != "278t" and
                         prior.get("reason") == "White House 278e requires checkpointed OCR")
            if not resumable:
                known_failures += 1
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
            def parse(path: Path) -> dict:
                if kind == "278t":
                    return parse_whitehouse_278t_pdf(
                        path, **common, document_id=metadata["document_id"],
                        filer_name=name,
                        amended_label=metadata.get("link_label"))
                try:
                    return extract_public_278e_pdf(path, **common, expected_filer=name)
                except OgeCatalogError as exc:
                    if str(exc) != "White House 278e requires checkpointed OCR":
                        raise
                checkpoint = (review_root / "whitehouse/ocr-checkpoints" /
                              metadata["document_id"].split(":", 1)[1] /
                              metadata["sha256"] / suffix)
                legacy_checkpoint = None
                if version == TRUMP_2025_PARSER_VERSION:
                    legacy_checkpoint = (review_root / "whitehouse/ocr-checkpoints" /
                                         metadata["document_id"].split(":", 1)[1] /
                                         metadata["sha256"] /
                                         ANNUAL_PARSER_VERSION.replace("/", "-"))
                return extract_public_278e_pdf_checkpointed(
                    path, **common, expected_filer=name,
                    checkpoint_root=checkpoint, page_limit=ocr_page_limit,
                    legacy_checkpoint_root=legacy_checkpoint)
            if pdf is None:
                with tempfile.TemporaryDirectory(prefix="whitehouse-pdf-") as temporary:
                    assembled = Path(temporary) / "original.pdf"
                    assembled.write_bytes(read_archived_pdf(evidence_root, metadata))
                    result = parse(assembled)
            else:
                result = parse(pdf)
            if (result.get("source_sha256") != metadata["sha256"] or
                    result.get("source_url") != metadata["document_url"] or
                    result.get("parser_version") != version):
                raise ValueError("White House parser returned unbound evidence")
            _write(target, result)
            failure_target.unlink(missing_ok=True)
            created += 1
        except OcrCheckpointPending as exc:
            failure_target.unlink(missing_ok=True)
            checkpoint_pending.append({"document_id": metadata["document_id"],
                                       **exc.status})
        except (OgeCatalogError, ValueError, OSError) as exc:
            failures.append({"document_id": metadata["document_id"],
                             "sha256": metadata["sha256"],
                             "reason": str(exc)})
            if not isinstance(exc, OSError):
                _write(failure_target, {
                    "schema_version": FAILURE_SCHEMA,
                    "document_id": metadata["document_id"],
                    "source_sha256": metadata["sha256"],
                    "source_url": metadata["document_url"],
                    "parser_version": version,
                    "reason": str(exc),
                    "status": "quarantined_until_parser_revision",
                })
                recorded_failures += 1
    return {"schema_version": STATUS_SCHEMA, "source_id": "whitehouse_public_disclosures",
            "archived_version_count": len(all_rows), "selected_version_count": len(rows),
            "fixed_document_id": document_id, "fixed_source_sha256": source_sha256,
            "attempted_count": attempted,
            "last_attempted_id": last_attempted_id,
            "extraction_created_count": created, "existing_extraction_count": skipped,
            "existing_failure_count": known_failures,
            "recorded_failure_count": recorded_failures,
            "checkpoint_pending_count": len(checkpoint_pending),
            "checkpoint_pending": checkpoint_pending,
            "pending_count": len(rows) - skipped - created - known_failures - recorded_failures,
            "failure_count": len(failures), "failures": failures,
            "production_qualification": "not_attempted"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--start-after-id")
    parser.add_argument("--ocr-page-limit", type=int, default=50)
    parser.add_argument("--document-id")
    parser.add_argument("--source-sha256")
    parser.add_argument("--status-output", type=Path)
    args = parser.parse_args(argv)
    try:
        status = extract_batch(args.evidence_root, args.review_root, limit=args.limit,
                               start_after_id=args.start_after_id,
                               ocr_page_limit=args.ocr_page_limit,
                               document_id=args.document_id,
                               source_sha256=args.source_sha256)
        status_output = (args.status_output or
                         args.review_root / "whitehouse/extraction-status.json")
        if args.document_id is not None and args.status_output is None:
            raise ValueError("Fixed White House replay requires a separate status output")
        _write(status_output, status)
        print(json.dumps({key: status[key] for key in (
            "archived_version_count", "attempted_count", "extraction_created_count",
            "pending_count", "failure_count")}))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"White House extraction failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
