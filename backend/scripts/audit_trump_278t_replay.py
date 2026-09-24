"""Audit the fixed Trump 08/12/25 278-T replay without rejecting row-level quarantine."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unison_snapshot.codec import encode
from unison_snapshot.whitehouse_278t import (
    EXTRACTION_SCHEMA,
    TRUMP_081225_DOCUMENT_ID,
    TRUMP_081225_PARSER_VERSION,
    TRUMP_081225_SOURCE_SHA256,
    TRUMP_081225_SOURCE_URL,
)


SCHEMA = "whitehouse-trump-278t-replay-audit/v1"


def audit_replay(extraction: dict, status: dict) -> dict:
    transactions = extraction.get("transactions")
    quarantined = extraction.get("quarantined")
    if (extraction.get("schema_version") != EXTRACTION_SCHEMA or
            extraction.get("parser_version") != TRUMP_081225_PARSER_VERSION or
            extraction.get("document_id") != TRUMP_081225_DOCUMENT_ID or
            extraction.get("source_url") != TRUMP_081225_SOURCE_URL or
            extraction.get("source_sha256") != TRUMP_081225_SOURCE_SHA256 or
            extraction.get("filed_at") != "2025-08-12" or
            extraction.get("evidence_complete") is not True or
            extraction.get("document_reasons") != [] or
            not isinstance(transactions, list) or not transactions or
            not isinstance(quarantined, list)):
        raise ValueError("Fixed Trump 278-T replay is not source-complete")
    rows = [*transactions, *quarantined]
    if extraction.get("source_row_count") != 507 or len(rows) != 507:
        raise ValueError("Fixed Trump 278-T replay did not conserve 507 physical rows")
    numbers = [row.get("row_number") for row in rows if isinstance(row, dict)]
    identities = [row.get("extraction_id") for row in rows if isinstance(row, dict)]
    if (len(numbers) != 507 or sorted(numbers) != list(range(1, 508)) or
            len(identities) != 507 or any(not isinstance(value, str) or not value
                                          for value in identities) or
            len(set(identities)) != 507):
        raise ValueError("Fixed Trump 278-T row identities are incomplete or duplicated")
    if any(not isinstance(row.get("reasons"), list) or not row["reasons"]
           for row in quarantined):
        raise ValueError("Fixed Trump 278-T quarantined rows need explicit reasons")
    if (status.get("schema_version") != "whitehouse-public-extraction-status/v1" or
            status.get("fixed_document_id") != TRUMP_081225_DOCUMENT_ID or
            status.get("fixed_source_sha256") != TRUMP_081225_SOURCE_SHA256 or
            status.get("selected_version_count") != 1 or
            status.get("failure_count") != 0 or
            status.get("recorded_failure_count") != 0 or
            status.get("checkpoint_pending_count") != 0 or
            status.get("pending_count") != 0 or
            status.get("extraction_created_count", 0) +
            status.get("existing_extraction_count", 0) != 1):
        raise ValueError("Fixed Trump 278-T replay status is incomplete")
    return {
        "schema_version": SCHEMA,
        "document_id": TRUMP_081225_DOCUMENT_ID,
        "source_url": TRUMP_081225_SOURCE_URL,
        "source_sha256": TRUMP_081225_SOURCE_SHA256,
        "parser_version": TRUMP_081225_PARSER_VERSION,
        "filed_at": "2025-08-12",
        "source_row_count": 507,
        "transaction_count": len(transactions),
        "quarantined_row_count": len(quarantined),
        "row_conservation_complete": True,
        "duplicate_row_identity_count": 0,
        "extraction_sha256": hashlib.sha256(encode(extraction)).hexdigest(),
    }


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit_replay(_object(args.input), _object(args.status))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
