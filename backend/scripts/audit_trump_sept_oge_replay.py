"""Require the fixed September filing replay to preserve every v2 row verbatim."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from unison_snapshot.oge_reports import (
    PARSER_VERSION, TRUMP_SEPT_2026_DOCUMENT_ID,
    TRUMP_SEPT_2026_FILING_DATE_EVIDENCE, TRUMP_SEPT_2026_PARSER_VERSION,
    TRUMP_SEPT_2026_SOURCE_SHA256, TRUMP_SEPT_2026_SOURCE_URL,
)


def audit(review_root: Path) -> dict:
    directory = (review_root / "oge" / "extractions" /
                 TRUMP_SEPT_2026_DOCUMENT_ID / TRUMP_SEPT_2026_SOURCE_SHA256)
    old_path = directory / (PARSER_VERSION.replace("/", "-") + ".json")
    new_path = directory / (TRUMP_SEPT_2026_PARSER_VERSION.replace("/", "-") + ".json")
    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))
    for value in (old, new):
        if (value.get("document_id") != TRUMP_SEPT_2026_DOCUMENT_ID or
                value.get("source_url") != TRUMP_SEPT_2026_SOURCE_URL or
                value.get("source_sha256") != TRUMP_SEPT_2026_SOURCE_SHA256):
            raise ValueError("September OGE replay is not bound to the fixed source")
    if (old.get("parser_version") != PARSER_VERSION or
            old.get("filed_at") is not None or
            old.get("document_reasons") != ["filer_signature_date_not_unique"] or
            old.get("evidence_complete") is not False or
            len(old.get("transactions", [])) != 228 or
            len(old.get("quarantined", [])) != 925):
        raise ValueError("September OGE v2 baseline changed")
    expected = deepcopy(old)
    expected.update(
        parser_version=TRUMP_SEPT_2026_PARSER_VERSION,
        filed_at="2026-09-08",
        filing_date_evidence=TRUMP_SEPT_2026_FILING_DATE_EVIDENCE,
        document_reasons=[],
        evidence_complete=True,
    )
    if new != expected:
        raise ValueError("September OGE replay changed a table row or another source field")
    return {
        "document_id": TRUMP_SEPT_2026_DOCUMENT_ID,
        "source_sha256": TRUMP_SEPT_2026_SOURCE_SHA256,
        "transaction_count": 228,
        "quarantined_count": 925,
        "filing_date": "2026-09-08",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.review_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
