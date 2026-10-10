"""Read-only identity evidence from the official White House directory.

This is a source binding observation, not a canonical person assignment. A
matching directory entry cannot by itself settle filing date, amendments,
duplicate trades, or row extraction quality.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def _name_tokens(name: str) -> tuple[str, str] | None:
    parts = re.findall(r"[a-z]+", name.casefold())
    if len(parts) < 2:
        return None
    if "," in name:
        return parts[0], parts[1]
    # The White House directory sometimes drops the comma in surname-first
    # labels. The two edge tokens in either order must still match the PDF.
    if len(parts) == 2:
        return tuple(sorted(parts))
    return tuple(sorted((parts[0], parts[-1])))


def _same_person_label(directory_name: str, pdf_name: str) -> bool:
    left = _name_tokens(directory_name)
    right = _name_tokens(pdf_name)
    if left is None or right is None:
        return False
    # Comma-based PDF names can contain a middle initial; take its first
    # given token, not the final middle token.
    if "," in pdf_name:
        right_parts = re.findall(r"[a-z]+", pdf_name.casefold())
        right = tuple(sorted((right_parts[0], right_parts[1])))
    if "," in directory_name:
        left_parts = re.findall(r"[a-z]+", directory_name.casefold())
        left = tuple(sorted((left_parts[0], left_parts[1])))
    return left == right


def bind_directory_entry(extraction: dict, directory: dict) -> dict:
    """Bind one archived extraction to one exact official directory entry."""
    source_url = extraction.get("source_url")
    source_sha = extraction.get("source_sha256")
    document_id = extraction.get("document_id")
    if not isinstance(source_url, str) or urlsplit(source_url).hostname != "www.whitehouse.gov":
        raise ValueError("White House source URL is required")
    if (not isinstance(source_sha, str) or len(source_sha) != 64 or
            re.fullmatch(r"[0-9a-fA-F]{64}", source_sha) is None):
        raise ValueError("archived PDF SHA-256 is required")
    if directory.get("document_url") != source_url or directory.get("source_document_id") != document_id:
        raise ValueError("directory URL and document ID must exactly match")
    if directory.get("document_type_from_label") != "278t" or directory.get("source_section") != "Transaction Reports":
        raise ValueError("directory entry is not a 278-T transaction report")
    directory_name = directory.get("filer_name_from_label")
    pdf_name = extraction.get("pdf_filer_name")
    if not isinstance(directory_name, str) or not isinstance(pdf_name, str):
        return {"status": "identity_unknown", "reason": "name_missing"}
    if not _same_person_label(directory_name, pdf_name):
        return {"status": "identity_unknown", "reason": "name_mismatch",
                "directory_name": directory_name, "pdf_name": pdf_name}
    return {"status": "directory_pdf_name_aligned",
            "document_id": document_id, "source_url": source_url,
            "source_sha256": source_sha.lower(),
            "directory_name": directory_name, "pdf_name": pdf_name,
            "pdf_position_title": extraction.get("pdf_position_title"),
            "canonical_person_id": None,
            "qualification": None}
