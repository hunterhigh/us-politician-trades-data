"""Persist source-bound human cover-date readings without changing qualification.

The input is a reviewed set of observations, not an OCR guess or a substitute
for transaction-row validation. This script only writes review sidecars.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge_reports import _read_archived_source
from unison_snapshot.oge_manual_dates import load_observations


SCHEMA = "oge-manual-cover-date-observations/v1"
OUTPUT_SCHEMA = "oge-manual-cover-date/v1"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ROLES = {"filer_signature_date", "amendment_signature_date"}


def build(*, observations_path: Path, evidence_root: Path,
          review_root: Path) -> dict:
    manifest = load_observations(observations_path)
    if (manifest.get("schema_version") != SCHEMA or
            manifest.get("status") != "observation_only" or
            not isinstance(manifest.get("observations"), list)):
        raise ValueError("manual cover-date manifest is invalid")
    seen: set[str] = set()
    prepared: list[tuple[Path, bytes]] = []
    roles: dict[str, int] = {role: 0 for role in _ROLES}
    for row in manifest["observations"]:
        if not isinstance(row, dict):
            raise ValueError("manual cover-date row is invalid")
        document_id, source_sha = row.get("document_id"), row.get("source_sha256")
        if (not isinstance(document_id, str) or not _ID.fullmatch(document_id) or
                document_id in seen or not isinstance(source_sha, str) or
                not _SHA.fullmatch(source_sha)):
            raise ValueError("manual cover-date identity is invalid or duplicated")
        seen.add(document_id)
        if (row.get("page_number") != 1 or row.get("field_role") not in _ROLES or
                not isinstance(row.get("raw_reading"), str) or
                not row["raw_reading"].strip()):
            raise ValueError("manual cover-date locator is invalid")
        observed_date = row.get("observed_date")
        if not isinstance(observed_date, str) or date.fromisoformat(observed_date).isoformat() != observed_date:
            raise ValueError("manual cover date is invalid")
        metadata_path = (evidence_root / "oge" / "reports" / document_id /
                         f"{source_sha}.json")
        metadata, _, actual_sha = _read_archived_source(evidence_root, metadata_path)
        if metadata["document_id"] != document_id or actual_sha != source_sha:
            raise ValueError("manual cover date does not match archived PDF")
        extraction_path = (review_root / "oge" / "extractions" / document_id /
                           source_sha / "oge-278t-pdf-v2.json")
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
        if (extraction.get("document_id") != document_id or
                extraction.get("source_sha256") != source_sha or
                extraction.get("source_url") != metadata.get("document_url") or
                extraction.get("filed_at") is not None or
                "filer_signature_date_not_unique" not in extraction.get("document_reasons", [])):
            raise ValueError("manual cover date does not match unresolved v2 extraction")
        output = {
            "schema_version": OUTPUT_SCHEMA,
            "status": "observation_only",
            "qualification_effect": "none",
            "source_id": "oge",
            "document_id": document_id,
            "source_url": metadata["document_url"],
            "source_sha256": source_sha,
            "page_number": 1,
            "field_role": row["field_role"],
            "recognition_method": "human_visual_reading_of_pdf_cover",
            "raw_reading": row["raw_reading"],
            "observed_date": observed_date,
            "filed_at": (observed_date if row["field_role"] == "filer_signature_date"
                         else None),
            "extraction_parser_version": "oge-278t-pdf/v2",
        }
        path = (review_root / "oge" / "manual_cover_dates" / document_id /
                f"{source_sha}.json")
        payload = (json.dumps(output, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n").encode("utf-8")
        if path.exists() and path.read_bytes() != payload:
            raise ValueError("immutable manual cover-date collision")
        prepared.append((path, payload))
        roles[row["field_role"]] += 1
    for path, payload in prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return {"observations": len(prepared), "roles": roles,
            "qualification_effect": "none"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(observations_path=args.observations,
                           evidence_root=args.evidence_root,
                           review_root=args.review_root), sort_keys=True))


if __name__ == "__main__":
    main()
