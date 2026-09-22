"""Publish review-only extraction audits for archived OGE annual reports."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual import PARSER_VERSION, SCHEMA, extract_annual_pdf
from unison_snapshot.oge_whitehouse import ANNUAL_ARCHIVE_SCHEMA


_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")
_OUTPUT_NAME = "oge-278e-tables-v1.json"


def extract_archived_reports(evidence_root: Path, review_root: Path) -> dict:
    """Write audits only to review, never to the frontend candidate or main."""
    evidence = evidence_root.resolve()
    review = review_root.resolve()
    source = evidence / "oge" / "annual" / "reports"
    results = []
    for metadata_path in sorted(source.glob("*/*.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        document_id = metadata.get("document_id")
        sha = metadata.get("sha256")
        if (metadata.get("schema_version") != ANNUAL_ARCHIVE_SCHEMA or
                not isinstance(document_id, str) or not _ID.fullmatch(document_id) or
                not isinstance(sha, str) or not _SHA.fullmatch(sha) or
                metadata_path != source / document_id / f"{sha}.json" or
                metadata.get("archive_path") != f"oge/annual/reports/{document_id}/{sha}.pdf"):
            raise OgeCatalogError("OGE annual archive metadata is inconsistent")
        pdf = evidence / metadata["archive_path"]
        result = extract_annual_pdf(pdf, source_url=metadata["document_url"],
                                    source_sha256=sha, expected_filer=metadata["filer_name"])
        if result.get("schema_version") != SCHEMA or result.get("parser_version") != PARSER_VERSION:
            raise OgeCatalogError("OGE annual extraction contract is invalid")
        result["source_document_id"] = document_id
        result["evidence_archive_path"] = metadata["archive_path"]
        target = review / "oge" / "annual" / "extractions" / document_id / sha / _OUTPUT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(result, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        target.write_bytes(content)
        results.append({
            "document_id": document_id, "source_sha256": sha,
            "extraction_path": target.relative_to(review).as_posix(),
            "extraction_sha256": hashlib.sha256(content).hexdigest(),
            "page_count": result["page_count"],
            "part7_printed_rows": result["part7_numbering"]["printed_row_count"],
            "part7_numbering_complete": result["part7_numbering"]["numbering_complete"],
            "part7_row_reconciliation_complete": result["part7_numbering"]["row_reconciliation_complete"],
            "strict_extracted_transactions": len(result["transactions"]),
            "strict_extracted_holdings": len(result["holdings"]),
            "quarantined_items": len(result["quarantined"]),
            "production_qualification": result["production_qualification"],
            "promoted_to_candidate": 0,
        })
    if not results:
        raise OgeCatalogError("No archived annual OGE PDF is available for review")
    summary = {
        "schema_version": "oge-278e-annual-review-status/v1",
        "parser_version": PARSER_VERSION,
        "archived_reports_reviewed": len(results),
        "promoted_to_candidate": 0,
        "reports": results,
    }
    target = review / "oge" / "annual" / "extraction-status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args()
    summary = extract_archived_reports(args.evidence_root, args.review_root)
    print(json.dumps({"reviewed": summary["archived_reports_reviewed"],
                      "promoted": summary["promoted_to_candidate"]}))


if __name__ == "__main__":
    main()
