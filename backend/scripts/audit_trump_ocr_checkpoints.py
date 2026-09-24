"""Verify the immutable 38-shard OCR geometry used for the Trump replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from unison_snapshot.codec import encode
from unison_snapshot.oge_278e_public import (
    OCR_SHARD_SCHEMA,
    PARSER_VERSION,
    TRUMP_2025_LEGACY_OCR_ENGINE,
    TRUMP_2025_PAGE_COUNT,
    TRUMP_2025_SOURCE_SHA256,
    TRUMP_2025_SOURCE_URL,
)


SCHEMA = "whitehouse-ocr-checkpoint-audit/v1"
ORIGIN_REVIEW_COMMIT = "2dab10fd5c94ccc80c4ccdd054800e4c41fcd1b4"
EXPECTED_AUDIT_SHA256 = "291eec853889ebcb102187a7f876fcd624270f33f0bcc5108dd42c90611a995b"
NAME = re.compile(r"pages-(\d{4})-(\d{4})\.json\Z")


def audit_checkpoints(root: Path) -> dict:
    files = sorted(root.glob("pages-*.json"))
    ranges = [(start, min(start + 24, TRUMP_2025_PAGE_COUNT))
              for start in range(1, TRUMP_2025_PAGE_COUNT + 1, 25)]
    if len(files) != len(ranges):
        raise ValueError(f"Expected 38 Trump OCR shards, found {len(files)}")
    pages = []
    entries = []
    total_words = 0
    empty_pages = 0
    for path, expected in zip(files, ranges, strict=True):
        match = NAME.fullmatch(path.name)
        observed = (int(match[1]), int(match[2])) if match else None
        if observed != expected:
            raise ValueError(f"Trump OCR shard range changed: {path.name}")
        raw = path.read_bytes()
        value = json.loads(raw)
        start, end = expected
        required = {
            "schema_version": OCR_SHARD_SCHEMA,
            "parser_version": PARSER_VERSION,
            "source_url": TRUMP_2025_SOURCE_URL,
            "source_sha256": TRUMP_2025_SOURCE_SHA256,
            "ocr_engine": TRUMP_2025_LEGACY_OCR_ENGINE,
            "page_start": start,
            "page_end": end,
        }
        if any(value.get(key) != expected_value
               for key, expected_value in required.items()):
            raise ValueError(f"Trump OCR shard binding changed: {path.name}")
        page_rows = value.get("pages")
        if (not isinstance(page_rows, list) or len(page_rows) != end - start + 1 or
                [row.get("page_number") for row in page_rows
                 if isinstance(row, dict)] != list(range(start, end + 1)) or
                any(not isinstance(row, dict) or
                    not isinstance(row.get("width"), (int, float)) or
                    not isinstance(row.get("height"), (int, float)) or
                    not isinstance(row.get("words"), list) or
                    any(not isinstance(word, dict) for word in row["words"])
                    for row in page_rows)):
            raise ValueError(f"Trump OCR shard geometry changed: {path.name}")
        word_count = sum(len(row["words"]) for row in page_rows)
        empty_pages += sum(not row["words"] for row in page_rows)
        total_words += word_count
        pages.extend(row["page_number"] for row in page_rows)
        entries.append({
            "path": path.name,
            "page_start": start,
            "page_end": end,
            "page_count": len(page_rows),
            "word_count": word_count,
            "byte_length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    if pages != list(range(1, TRUMP_2025_PAGE_COUNT + 1)):
        raise ValueError("Trump OCR checkpoint coverage is not exactly pages 1..927")
    payload = {
        "schema_version": SCHEMA,
        "review_commit": ORIGIN_REVIEW_COMMIT,
        "source_sha256": TRUMP_2025_SOURCE_SHA256,
        "source_url": TRUMP_2025_SOURCE_URL,
        "parser_version": PARSER_VERSION,
        "ocr_engine": TRUMP_2025_LEGACY_OCR_ENGINE,
        "shard_size": 25,
        "shard_count": len(entries),
        "page_count": len(pages),
        "page_start": pages[0],
        "page_end": pages[-1],
        "gap_count": 0,
        "duplicate_page_count": 0,
        "empty_word_page_count": empty_pages,
        "word_count": total_words,
        "byte_length": sum(row["byte_length"] for row in entries),
        "shards": entries,
    }
    digest = hashlib.sha256(encode(payload)).hexdigest()
    if digest != EXPECTED_AUDIT_SHA256:
        raise ValueError("Trump OCR checkpoint content hash changed")
    return {**payload, "audit_sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_checkpoints(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encode(result))
    print(json.dumps({key: result[key] for key in (
        "audit_sha256", "shard_count", "page_count", "word_count", "byte_length")},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
