#!/usr/bin/env python3
"""Fetch one request-scoped, refreshed disclosure snapshot from the data API."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from process_snapshot import ProcessingError, SCHEMA_VERSION, build_snapshot


REQUIRED_SNAPSHOT_KEYS = {
    "meta",
    "people",
    "transactions",
    "summary",
    "activity_by_day",
    "priority_people",
    "reported_holdings",
    "top_people",
    "top_tickers",
    "recent_transactions",
    "security_market_data",
    "source_health",
}


class SnapshotError(RuntimeError):
    """Safe, user-presentable data-service error."""


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise SnapshotError("Data API returned a non-object snapshot")
    missing = sorted(REQUIRED_SNAPSHOT_KEYS - set(snapshot))
    if missing:
        raise SnapshotError(f"Data API snapshot is missing fields: {', '.join(missing)}")
    if not isinstance(snapshot.get("meta"), dict):
        raise SnapshotError("Snapshot meta must be an object")
    if not snapshot["meta"].get("snapshot_id"):
        raise SnapshotError("Snapshot has no snapshot_id")
    if snapshot["meta"].get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError(f"Snapshot schema_version must be {SCHEMA_VERSION}")
    for key in (
        "people", "transactions", "activity_by_day", "priority_people", "reported_holdings",
        "top_people", "top_tickers", "recent_transactions", "security_market_data", "source_health",
    ):
        if not isinstance(snapshot.get(key), list):
            raise SnapshotError(f"Snapshot field {key} must be an array")
    return snapshot


def build_request(args: argparse.Namespace) -> urllib.request.Request:
    api_url = (args.api_url or os.environ.get("POLITICIAN_DATA_API_URL") or "").rstrip("/")
    if not api_url:
        raise SnapshotError("POLITICIAN_DATA_API_URL is not configured")
    parsed = urllib.parse.urlparse(api_url)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SnapshotError("POLITICIAN_DATA_API_URL must use HTTPS")

    value = args.value.strip() if args.value else None
    if args.mode != "dashboard" and not value:
        raise SnapshotError(f"Mode {args.mode} requires --value")

    body = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "mode": args.mode,
            "scope": {"type": args.mode, "value": value},
            "force_refresh": bool(args.force_refresh),
            "locale": args.locale,
            "include": [
                "people", "transactions", "reported_holdings",
                "security_market_data", "source_health",
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "us-politician-trades-watch/0.1",
    }
    token = os.environ.get("POLITICIAN_DATA_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(f"{api_url}/v1/snapshots", data=body, headers=headers, method="POST")


def fetch(args: argparse.Namespace) -> dict[str, Any]:
    request = build_request(args)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise SnapshotError(f"Data API returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        label = type(reason).__name__ if reason is not None else "network error"
        raise SnapshotError(f"Data API is unavailable ({label})") from None
    except TimeoutError:
        raise SnapshotError("Data API request timed out") from None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SnapshotError("Data API returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise SnapshotError("Data API response must be an object")

    refresh = payload.get("refresh")
    snapshot = payload.get("snapshot", payload)
    try:
        snapshot = build_snapshot(snapshot)
    except ProcessingError as exc:
        raise SnapshotError(f"Data API snapshot failed the source contract: {exc}") from None
    snapshot = validate_snapshot(snapshot)
    if isinstance(refresh, dict):
        snapshot.setdefault("meta", {})["refresh"] = refresh
    return snapshot


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dashboard", "person", "ticker", "search"))
    parser.add_argument("--value", help="Person, ticker, or search expression for scoped refresh")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--locale", default="zh-CN")
    parser.add_argument("--api-url", help="Override POLITICIAN_DATA_API_URL")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        snapshot = fetch(args)
        write_atomic(args.output, snapshot)
    except SnapshotError as exc:
        parser.exit(2, f"snapshot error: {exc}\n")
    print(json.dumps({"snapshot_id": snapshot["meta"]["snapshot_id"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
