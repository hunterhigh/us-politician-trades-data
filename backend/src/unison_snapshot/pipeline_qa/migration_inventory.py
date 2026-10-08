"""Read-only inventory of a pinned production snapshot and review candidates.

This is a G0/G1 input audit, not a new parser, qualification decision, or
publisher. Every object is read from an immutable Git commit.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable


COLLECTIONS = ("people", "transactions", "reported_holdings")
SOURCE_IDS = ("house_clerk", "oge", "senate_efd")
SHA = re.compile(r"^[0-9a-f]{40}$")
HTML_SHA256 = "d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4"


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


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _differences(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    return sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))


def _market_enrichment_only(production: dict[str, Any], candidate: dict[str, Any], fields: list[str]) -> bool:
    if not fields or not set(fields) <= {"ticker", "ticker_mapping_basis"}:
        return False
    return (candidate.get("ticker") is None and candidate.get("ticker_mapping_basis") is None
            and bool(production.get("ticker")) and bool(production.get("ticker_mapping_basis")))


def build_inventory(
    *, repo: Path, main_commit: str, review_commit: str, market_commit: str,
    html_path: Path, read_object: Callable[[Path, str, str], bytes] = git_object,
    verify_market_pages: bool = False,
) -> dict[str, Any]:
    """Reconcile current formal facts by ID; never silently authorize cutover."""
    for commit in (main_commit, review_commit, market_commit):
        if not SHA.fullmatch(commit):
            raise InventoryError("all refs must be full 40-character commits")
    html_hash = hashlib.sha256(html_path.read_bytes()).hexdigest()
    if html_hash != HTML_SHA256:
        raise InventoryError("latest HTML baseline hash changed")

    manifest_raw = read_object(repo, main_commit, "manifest.json")
    manifest = _load(manifest_raw, "production manifest")
    if manifest.get("is_demo") is not False or manifest.get("market_commit") != market_commit:
        raise InventoryError("production manifest is demo or points to another market commit")
    board_hash = manifest.get("board")
    if not isinstance(board_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", board_hash):
        raise InventoryError("invalid board hash")
    board_raw = read_object(repo, main_commit, f"board/{board_hash}.json")
    if hashlib.sha256(board_raw).hexdigest() != board_hash:
        raise InventoryError("production board content hash mismatch")
    board = _load(board_raw, "production board")

    unified_raw = read_object(repo, review_commit, "candidates/disclosure-current.json")
    unified = _load(unified_raw, "unified candidate")
    if unified.get("meta", {}).get("is_demo") is not False:
        raise InventoryError("unified candidate must be official")
    sources = {}
    source_indexes = {}
    for source in SOURCE_IDS:
        path = f"candidates/sources/{source}-current.json"
        raw = read_object(repo, review_commit, path)
        candidate = _load(raw, path)
        if candidate.get("meta", {}).get("is_demo") is not False:
            raise InventoryError(f"source candidate must be official: {source}")
        sources[source] = {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
        source_indexes[source] = {name: _index(candidate.get(name), f"{source}.{name}")
                                  for name in COLLECTIONS}

    records = {name: [] for name in COLLECTIONS}
    summary = {}
    for name in COLLECTIONS:
        formal = _index(board.get(name), f"production.{name}")
        proposed = _index(unified.get(name), f"unified.{name}")
        counts = Counter()
        for record_id, row in sorted(formal.items()):
            candidate = proposed.get(record_id)
            source_id = row.get("source_id") if name != "people" else row.get("disclosure_authority")
            source = source_indexes.get(source_id, {}).get(name, {}).get(record_id)
            issues = []
            differences = []
            if candidate is None:
                issues.append("missing_unified_candidate")
            else:
                differences = _differences(row, candidate)
                if differences and not _market_enrichment_only(row, candidate, differences):
                    issues.append("unexplained_unified_difference")
                elif differences:
                    counts["market_enrichment_only"] += 1
            if source is None:
                issues.append("missing_source_candidate")
            elif candidate is not None:
                source_differences = _differences(candidate, source)
                if source_differences and not _market_enrichment_only(candidate, source, source_differences):
                    issues.append("unexplained_source_difference")
            if name != "people" and (row.get("verification_status") != "official_matched" or
                                     (candidate is not None and candidate.get("verification_status") != "official_matched") or
                                     (source is not None and source.get("verification_status") != "official_matched")):
                issues.append("qualification_assertion_missing")
            if source_id not in SOURCE_IDS:
                issues.append("unknown_source")
            disposition = "legacy_qualified_binding" if not issues else "blocked_for_investigation"
            counts[disposition] += 1
            for issue in issues:
                counts[issue] += 1
            records[name].append({
                "id": record_id, "source_id": source_id,
                "filing_id": row.get("filing_id"), "person_id": row.get("person_id"),
                "source_url": row.get("source_url"),
                "canonical_record": row,
                "production_sha256": _digest(row),
                "candidate_sha256": _digest(candidate) if candidate is not None else None,
                "source_candidate_sha256": _digest(source) if source is not None else None,
                "unified_difference_fields": differences,
                "issues": issues, "disposition": disposition,
                "qualification_verification": "legacy_candidate_assertion_not_revalidated",
                "evidence_verification": "source_url_only_original_bytes_not_reverified",
            })
        summary[name] = {"production": len(formal), "unified": len(proposed),
                         "unified_extra_ids": sorted(proposed.keys() - formal.keys()),
                         **dict(sorted(counts.items()))}

    health = manifest.get("source_health")
    market_pages = manifest.get("market_pages")
    if not isinstance(health, list) or not isinstance(market_pages, list):
        raise InventoryError("missing market or source health references")
    market_records = []
    tickers = set()
    for page_hash in market_pages if verify_market_pages else ():
        if not isinstance(page_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", page_hash):
            raise InventoryError("invalid market page hash")
        raw = read_object(repo, market_commit, f"market-pages/{page_hash}.json")
        if hashlib.sha256(raw).hexdigest() != page_hash:
            raise InventoryError(f"market page content hash mismatch: {page_hash}")
        page = _load(raw, "market page")
        rows = page.get("security_market_data")
        if not isinstance(rows, list):
            raise InventoryError("market page lacks security_market_data")
        for row in rows:
            ticker = row.get("ticker") if isinstance(row, dict) else None
            history = row.get("price_history") if isinstance(row, dict) else None
            if not isinstance(ticker, str) or not ticker or ticker in tickers or not isinstance(history, list):
                raise InventoryError("invalid or duplicate market ticker")
            tickers.add(ticker)
            market_records.append({"ticker": ticker, "source_id": row.get("source_id"),
                                   "page_sha256": page_hash, "canonical_sha256": _digest(row),
                                   "price_point_count": len(history),
                                   "first_price_date": history[0].get("date") if history else None,
                                   "last_price_date": history[-1].get("date") if history else None})
    expected_tickers = manifest.get("coverage", {}).get("market_ticker_count")
    if verify_market_pages and expected_tickers != len(market_records):
        raise InventoryError("market ticker count differs from production manifest")
    health_ids = [item.get("source_id") if isinstance(item, dict) else None for item in health]
    if any(not isinstance(source_id, str) for source_id in health_ids) or len(set(health_ids)) != len(health_ids):
        raise InventoryError("invalid or duplicate source health ID")
    return {
        "schema_version": "pipeline-migration-inventory/v1",
        "fixed_inputs": {
            "main_commit": main_commit, "review_commit": review_commit,
            "market_commit": market_commit, "html_sha256": html_hash,
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "board_sha256": board_hash,
            "unified_candidate_sha256": hashlib.sha256(unified_raw).hexdigest(),
            "source_candidates": sources,
        },
        "summary": summary,
        "market": {"commit": market_commit, "page_count": len(market_pages),
                   "pages_sha256": _digest(market_pages),
                   "verification_status": "content_verified" if verify_market_pages else "pinned_ref_only",
                   "manifest_ticker_count": expected_tickers,
                   "ticker_count": len(market_records) if verify_market_pages else None,
                   "records": sorted(market_records, key=lambda row: row["ticker"])},
        "source_health": {"count": len(health), "sha256": _digest(health),
                          "records": sorted(health, key=lambda item: item["source_id"])},
        "records": records,
        "production_candidate_id_coverage_complete": all(not any(row["issues"] for row in group)
                                                      for group in records.values()),
        "projection_ready": False,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--main-commit", required=True)
    parser.add_argument("--review-commit", required=True)
    parser.add_argument("--market-commit", required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-market-pages", action="store_true")
    args = parser.parse_args()
    report = build_inventory(repo=args.repo, main_commit=args.main_commit,
                             review_commit=args.review_commit, market_commit=args.market_commit,
                             html_path=args.html, verify_market_pages=args.verify_market_pages)
    args.output.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "production_candidate_id_coverage_complete":
                      report["production_candidate_id_coverage_complete"], "projection_ready": False},
                     ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
