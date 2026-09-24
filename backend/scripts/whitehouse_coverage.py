"""Summarize public White House report links without claiming canonical facts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from unison_snapshot.whitehouse_disclosures import INDEX_SCHEMA
from unison_snapshot.oge_278e_public import (
    PARSER_VERSION as ANNUAL_PARSER_VERSION,
    SUPPORTED_PARSER_VERSIONS as ANNUAL_PARSER_VERSIONS,
    TRUMP_2025_PARSER_VERSION,
    parser_version_for_source,
)
from unison_snapshot.whitehouse_278t import (
    PARSER_VERSION as TRADE_PARSER_VERSION,
    SUPPORTED_PARSER_VERSIONS as TRADE_PARSER_VERSIONS,
    TRUMP_081225_PARSER_VERSION,
    parser_version_for_source as trade_parser_version_for_source,
)


SCHEMA = "whitehouse-public-coverage/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _annual_versions(source_url: str, source_sha256: str) -> tuple[str, ...]:
    """Select v6 only for its exact source while retaining older fallbacks."""

    current = parser_version_for_source(source_url, source_sha256)
    return (current, *(version for version in ANNUAL_PARSER_VERSIONS
                       if version not in {current, TRUMP_2025_PARSER_VERSION}))


def _trade_versions(source_url: str, source_sha256: str) -> tuple[str, ...]:
    """Select v3 only for its exact Trump source while retaining older fallbacks."""

    current = trade_parser_version_for_source(source_url, source_sha256)
    return (current, *(version for version in TRADE_PARSER_VERSIONS
                       if version not in {current, TRUMP_081225_PARSER_VERSION}))


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid White House review object: {path}")
    return value


def build_coverage(index: dict, batch: dict, review_root: Path) -> dict:
    reports = index.get("reports")
    archive_rows = batch.get("reports")
    if (index.get("schema_version") != INDEX_SCHEMA or
            not isinstance(reports, list) or
            index.get("report_link_count") != len(reports) or
            not isinstance(index.get("quarantine"), list) or
            batch.get("schema_version") != "whitehouse-public-disclosures-batch/v1" or
            batch.get("indexed_count") != len(reports) or
            not isinstance(archive_rows, list) or
            not isinstance(batch.get("failures"), list)):
        raise ValueError("White House coverage inputs do not match")
    by_id = defaultdict(list)
    for metadata in archive_rows:
        if not isinstance(metadata, dict):
            raise ValueError("White House archive report metadata is invalid")
        by_id[metadata.get("document_id")].append(metadata)
    failed_download_ids = {row.get("document_id") for row in batch["failures"]}
    rows = []
    seen = set()
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError("White House index report is invalid")
        document_id = report.get("source_document_id")
        if (not isinstance(document_id, str) or not document_id.startswith("wh-url:") or
                document_id in seen):
            raise ValueError("White House index document IDs are invalid or duplicated")
        seen.add(document_id)
        versions = by_id.pop(document_id, [])
        states = []
        hashes = []
        extraction_versions = []
        extraction_paths = []
        failure_paths = []
        quarantined_rows = 0
        for metadata in versions:
            sha = metadata.get("sha256")
            if (not isinstance(sha, str) or not _SHA.fullmatch(sha) or sha in hashes or
                    metadata.get("document_url") != report.get("document_url")):
                raise ValueError("White House archive version is not index-bound")
            hashes.append(sha)
            folder = (review_root / "whitehouse/extractions" /
                      document_id.split(":", 1)[1] / sha)
            is_trade = report.get("document_type_from_label") == "278t"
            versions_to_check = (_trade_versions(metadata["document_url"], sha) if is_trade else
                                 _annual_versions(metadata["document_url"], sha))
            forbidden_v3 = folder / f"{TRUMP_081225_PARSER_VERSION.replace('/', '-')}.json"
            if (is_trade and versions_to_check[0] != TRUMP_081225_PARSER_VERSION and
                    forbidden_v3.is_file()):
                raise ValueError("Source-bound White House 278-T v3 extraction has the wrong source")
            forbidden_v6 = folder / f"{TRUMP_2025_PARSER_VERSION.replace('/', '-')}.json"
            if (report.get("document_type_from_label") != "278t" and
                    versions_to_check[0] != TRUMP_2025_PARSER_VERSION and
                    forbidden_v6.is_file()):
                raise ValueError("Source-bound White House v6 extraction has the wrong source")
            valid_options = []
            failed_options = []
            for parser_version in versions_to_check:
                stem = parser_version.replace("/", "-")
                valid_path = folder / f"{stem}.json"
                failed_path = folder / f"{stem}.failure.json"
                if valid_path.is_file() and failed_path.is_file():
                    raise ValueError("White House archive version has conflicting review states")
                if valid_path.is_file():
                    valid_options.append((parser_version, valid_path))
                if failed_path.is_file():
                    failed_options.append((parser_version, failed_path))
            selected_valid = valid_options[0] if valid_options else None
            current_failure = next((item for item in failed_options
                                    if item[0] == versions_to_check[0]), None)
            if selected_valid is not None and not (
                    current_failure is not None and selected_valid[0] != versions_to_check[0]):
                parser_version, valid = selected_valid
                extraction = _load(valid)
                if (extraction.get("source_sha256") != sha or
                        extraction.get("source_url") != metadata["document_url"] or
                        extraction.get("parser_version") != parser_version):
                    raise ValueError("White House extraction is not archive-bound")
                quarantined = extraction.get("quarantined", [])
                if not isinstance(quarantined, list):
                    raise ValueError("White House extraction quarantine is invalid")
                quarantined_rows += len(quarantined)
                states.append("extracted_with_issues" if (
                    extraction.get("document_reasons") or
                    extraction.get("evidence_complete") is False or quarantined
                ) else "extracted_review_only")
                extraction_versions.append(parser_version)
                extraction_paths.append(valid.relative_to(review_root).as_posix())
                failure_paths.append(None)
            elif current_failure is not None:
                parser_version, failed = current_failure
                failure = _load(failed)
                if (failure.get("source_sha256") != sha or
                        failure.get("source_url") != metadata["document_url"] or
                        failure.get("parser_version") != parser_version):
                    raise ValueError("White House extraction failure is not archive-bound")
                states.append("extraction_quarantined")
                extraction_versions.append(parser_version)
                extraction_paths.append(None)
                failure_paths.append(failed.relative_to(review_root).as_posix())
            else:
                states.append("pending_extraction")
                extraction_versions.append(None)
                extraction_paths.append(None)
                failure_paths.append(None)
        if not versions:
            state = "download_failed" if document_id in failed_download_ids else "not_archived"
        elif len(versions) > 1:
            state = "multiple_archive_versions_unresolved"
        else:
            state = states[0]
        rows.append({
            "document_id": document_id,
            "link_label": report.get("link_label"),
            "filer_name_from_label": report.get("filer_name_from_label"),
            "document_type_from_label": report.get("document_type_from_label"),
            "report_year_from_label": report.get("report_year_from_label"),
            "document_url": report.get("document_url"),
            "archive_sha256_versions": sorted(hashes),
            "review_state": state,
            "extraction_parser_version": extraction_versions[0] if len(versions) == 1 else None,
            "extraction_path": extraction_paths[0] if len(versions) == 1 else None,
            "failure_path": failure_paths[0] if len(versions) == 1 else None,
            "quarantined_row_count": quarantined_rows,
            "production_qualification": "not_evaluated",
        })
    if by_id:
        raise ValueError("White House batch contains a document outside its current index")
    counts = Counter(row["review_state"] for row in rows)
    return {
        "schema_version": SCHEMA,
        "source_id": "whitehouse_public_disclosures",
        "index_page_sha256": index["page_sha256"],
        "report_link_count": len(rows),
        "index_quarantine_count": len(index["quarantine"]),
        "counts_by_review_state": dict(sorted(counts.items())),
        "production_qualified_fact_count": 0,
        "reports": rows,
        "index_quarantine": index["quarantine"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.review_root / "whitehouse/disclosures"
    result = build_coverage(_load(root / "index-current.json"),
                            _load(root / "batch-current.json"), args.review_root)
    target = args.review_root / "whitehouse/coverage-current.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"report_links": result["report_link_count"],
                      "review_states": result["counts_by_review_state"],
                      "index_quarantine": result["index_quarantine_count"]}))


if __name__ == "__main__":
    main()
