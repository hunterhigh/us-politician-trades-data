"""Bind source replacements to archived originals and official indexes."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from .source_objects import InventoryError, _index, _load, git_object, SHA


def _board(repo: Path, commit: str, read_object: Callable) -> dict[str, Any]:
    manifest = _load(read_object(repo, commit, "manifest.json"), "manifest")
    digest = manifest.get("board")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise InventoryError("invalid board hash")
    raw = read_object(repo, commit, f"board/{digest}.json")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise InventoryError("board content hash mismatch")
    return _load(raw, "board")




def _archive(repo: Path, evidence_commit: str, url: str,
             read_object: Callable, list_paths: Callable) -> dict[str, Any]:
    document_id = hashlib.sha256(url.encode()).hexdigest()[:24]
    directory = f"whitehouse/disclosures/reports/{document_id}"
    paths = [path for path in list_paths(repo, evidence_commit, directory) if path.endswith(".json")]
    if len(paths) != 1:
        raise InventoryError(f"expected one archived PDF metadata object: {document_id}")
    metadata = _load(read_object(repo, evidence_commit, f"{directory}/{paths[0]}"), "PDF metadata")
    digest = metadata.get("sha256")
    if (metadata.get("document_url") != url or metadata.get("document_id") != f"wh-url:{document_id}" or
            not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
        raise InventoryError(f"PDF metadata does not match URL: {url}")
    pdf = read_object(repo, evidence_commit, metadata["archive_path"])
    if hashlib.sha256(pdf).hexdigest() != digest or len(pdf) != metadata.get("byte_length"):
        raise InventoryError(f"archived PDF bytes do not match metadata: {url}")
    return {"url": url, "sha256": digest, "byte_length": len(pdf),
            "archive_path": metadata["archive_path"],
            "page_sha256": metadata.get("page_sha256"),
            "filer_name_from_label": metadata.get("filer_name_from_label"),
            "report_year_from_label": metadata.get("report_year_from_label"),
            "retrieved_at": metadata.get("retrieved_at"),
            "last_modified": metadata.get("headers", {}).get("last-modified")}


def _official_index_replacement(repo: Path, evidence_commit: str, old_pdf: dict[str, Any],
                                new_pdf: dict[str, Any], read_object: Callable) -> bool:
    pages = []
    for pdf in (old_pdf, new_pdf):
        digest = pdf.get("page_sha256")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise InventoryError("archived PDF lacks an official index page hash")
        path = f"whitehouse/disclosures/pages/{digest}"
        meta = _load(read_object(repo, evidence_commit, f"{path}.json"), "index page metadata")
        html = read_object(repo, evidence_commit, f"{path}.html")
        if (hashlib.sha256(html).hexdigest() != digest or
                meta.get("sha256") != digest or
                meta.get("page_url") != "https://www.whitehouse.gov/disclosures/"):
            raise InventoryError("official index page archive mismatch")
        pages.append((meta, html.decode("utf-8", errors="replace")))
    old_name = urlsplit(old_pdf["url"]).path.rsplit("/", 1)[-1]
    new_name = urlsplit(new_pdf["url"]).path.rsplit("/", 1)[-1]
    if old_name == new_name or old_pdf["page_sha256"] == new_pdf["page_sha256"]:
        return False
    old_meta, old_html = pages[0]
    new_meta, new_html = pages[1]
    return (old_html.count(old_name) == 1 and old_html.count(new_name) == 0 and
            new_html.count(new_name) == 1 and new_html.count(old_name) == 0 and
            datetime.fromisoformat(old_meta["retrieved_at"]) <
            datetime.fromisoformat(new_meta["retrieved_at"]))


def git_tree_paths(repo: Path, commit: str, directory: str) -> list[str]:
    if not SHA.fullmatch(commit) or not re.fullmatch(r"[a-zA-Z0-9_./-]+", directory):
        raise InventoryError("invalid evidence commit or path")
    result = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only",
                             f"{commit}:{directory}"], capture_output=True, check=False)
    if result.returncode:
        raise InventoryError(f"cannot list evidence directory: {directory}")
    return result.stdout.decode().splitlines()






