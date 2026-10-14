"""Observe, but do not qualify, covers rejected by the strict annual parser."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from unison_snapshot.oge_annual_scan_cover import SCHEMA, observe_scan_cover
from unison_snapshot.oge_whitehouse import ANNUAL_ARCHIVE_SCHEMA


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
        target = (review / "oge/annual/cover_observations" / document_id / sha /
                  "cover-ocr-v1.json")
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
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--tesseract", default="tesseract")
    args = parser.parse_args()
    summary = observe_failed_covers(args.evidence_root, args.review_root,
                                    tesseract=args.tesseract)
    print(json.dumps({"observed": summary["cover_observation_count"],
                      "failures": summary["cover_observation_failure_count"]}))


if __name__ == "__main__":
    main()
