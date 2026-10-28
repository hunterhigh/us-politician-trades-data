"""Read immutable source objects and compare canonical published facts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable


SHA = re.compile(r"^[0-9a-f]{40}$")


class InventoryError(ValueError):
    pass


def git_object(repo: Path, commit: str, path: str) -> bytes:
    if not SHA.fullmatch(commit) or not re.fullmatch(r"[a-zA-Z0-9_./-]+", path) or ".." in path:
        raise InventoryError("inventory inputs must use a full commit and safe Git path")
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise InventoryError(f"cannot read {commit}:{path}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _load(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8", errors="surrogateescape"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"invalid JSON: {label}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"expected object: {label}")
    return value


def _index(rows: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise InventoryError(f"expected array: {label}")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in result:
            raise InventoryError(f"invalid or duplicate ID: {label}")
        result[row["id"]] = row
    return result


def _differences(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    return sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))


def _market_enrichment_only(production: dict[str, Any], candidate: dict[str, Any], fields: list[str]) -> bool:
    if not fields or not set(fields) <= {"ticker", "ticker_mapping_basis"}:
        return False
    return (candidate.get("ticker") is None and candidate.get("ticker_mapping_basis") is None
            and bool(production.get("ticker")) and bool(production.get("ticker_mapping_basis")))






