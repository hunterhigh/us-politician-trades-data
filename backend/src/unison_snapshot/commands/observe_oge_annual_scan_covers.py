"""Observe, but do not qualify, covers rejected by the strict annual parser."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from unison_snapshot.oge_annual_scan_cover import SCHEMA, observe_scan_cover, observe_native_cover_text
from unison_snapshot.oge_whitehouse import ANNUAL_ARCHIVE_SCHEMA
from unison_snapshot.oge_annual_runtime import controlled_scan_output_path, guard_scan_observation_output


_ID = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")


def observe_failed_covers(evidence_root: Path, review_root: Path,
                          *, tesseract: str | None = None) -> dict:
    evidence = evidence_root.resolve()
    review = review_root.resolve()
    extraction_status = json.loads((review / "oge/annual/extraction-status.json").read_text(
        encoding="utf-8"))
    failures = extraction_status.get("failures", [])
    if not isinstance(failures, list) or len(failures) > 20:
        raise ValueError("OGE annual cover observation batch is outside bounds")
    observed = []
    errors = []
    for item in failures:
        document_id, sha = item["document_id"], item["source_sha256"]
        if (not isinstance(document_id, str) or not _ID.fullmatch(document_id) or
                not isinstance(sha, str) or not _SHA.fullmatch(sha)):
            raise ValueError("OGE annual cover source identity is invalid")
        directory = Path("oge/annual/reports") / document_id
        metadata = json.loads((evidence / directory / f"{sha}.json").read_text(
            encoding="utf-8"))
        if (metadata.get("schema_version") != ANNUAL_ARCHIVE_SCHEMA or
                metadata.get("document_id") != document_id or metadata.get("sha256") != sha or
                metadata.get("archive_path") != (directory / f"{sha}.pdf").as_posix()):
            raise ValueError("OGE annual cover source metadata is inconsistent")
        pdf = evidence / metadata["archive_path"]
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != sha:
            raise ValueError("OGE annual cover source bytes changed")
        target = controlled_scan_output_path(review,'cover_observation',document_id,sha)
        guard_scan_observation_output(target,pdf)
        try:
            if target.exists():
                prior = json.loads(target.read_text(encoding="utf-8"))
                if (prior.get("schema_version") == SCHEMA and
                        prior.get("source_sha256") == sha):
                    observed.append({"document_id": document_id, "source_sha256": sha,
                                     "path": target.relative_to(review).as_posix(),
                                     "disposition": "reused"})
                    continue
            value = observe_scan_cover(pdf,
                                       source_sha256=sha, document_id=document_id,
                                       catalog_filer=metadata["filer_name"],
                                       catalog_year=metadata["report_year"],
                                       tesseract=tesseract)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")), encoding="utf-8")
            observed.append({"document_id": document_id, "source_sha256": sha,
                             "path": target.relative_to(review).as_posix(),
                             "disposition": "observed"})
        except (OSError, ValueError) as exc:
            errors.append({"document_id": document_id, "source_sha256": sha,
                           "reason": str(exc)})
    summary = {
        "schema_version": "oge-278e-scan-cover-status/v1",
        "strict_parser_failure_count": len(failures),
        "cover_observation_count": len(observed),
        "cover_observation_failure_count": len(errors),
        "observed": observed,
        "errors": errors,
        "production_qualification_authorized": False,
    }
    target = review / "oge/annual/cover-observation-status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--native-cover-pdf", type=Path)
    parser.add_argument("--source-sha256")
    parser.add_argument("--document-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tesseract", default="tesseract")
    args = parser.parse_args()
    if args.native_cover_pdf is not None:
        if (args.evidence_root or bool(args.review_root) == bool(args.output) or
                not all((args.source_sha256, args.document_id))):
            parser.error("native cover mode requires PDF, source SHA, document ID and exactly one output/review-root")
        output = (controlled_scan_output_path(args.review_root,'native_cover_observation',
            args.document_id,args.source_sha256) if args.review_root is not None else args.output)
        guard_scan_observation_output(output,args.native_cover_pdf)
        result = observe_native_cover_text(args.native_cover_pdf, source_sha256=args.source_sha256, document_id=args.document_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print(json.dumps({"source_sha256": result["source_sha256"], "page_number": 1, "method": result["method"]}))
        return
    if not args.evidence_root or not args.review_root or any((args.source_sha256, args.document_id, args.output)):
        parser.error("batch mode requires evidence-root and review-root; native arguments must not be ignored")
    summary = observe_failed_covers(args.evidence_root, args.review_root,
                                    tesseract=args.tesseract)
    print(json.dumps({"observed": summary["cover_observation_count"],
                      "failures": summary["cover_observation_failure_count"]}))


if __name__ == "__main__":
    main()
