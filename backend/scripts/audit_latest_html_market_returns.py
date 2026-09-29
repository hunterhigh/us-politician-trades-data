"""Read-only audit of the user's latest HTML against its pinned market pages.

The published dashboard trims displayed price histories. Its event returns were
computed from the full, content-addressed market pages in the market commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from bisect import bisect_left
from pathlib import Path

LATEST_HTML_SHA256 = "d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4"
MAIN_COMMIT = "958b173ca37964dfa60e4762c6ef2931e3ed06ea"


def git_blob(revision: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{revision}:{path}"])


def html_data(path: Path) -> tuple[str, dict]:
    content = path.read_bytes()
    prefix = b"const DATA = "
    lines = [line for line in content.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError("HTML must contain exactly one inline DATA declaration")
    return hashlib.sha256(content).hexdigest(), json.loads(lines[0][len(prefix):].removesuffix(b";"))


def event_return(market: dict, day: str) -> float | None:
    history = market["price_history"]
    dates = [point["date"] for point in history]
    index = bisect_left(dates, day)
    if index == len(history) or day < dates[0]:
        return None
    baseline = history[index]["close"]
    return round((history[-1]["close"] / baseline - 1) * 100, 2) if baseline > 0 else None


def audit(html_path: Path, main_commit: str) -> dict:
    html_sha, data = html_data(html_path)
    if html_sha != LATEST_HTML_SHA256:
        raise ValueError("HTML changed; update the user-approved baseline before auditing")
    manifest = json.loads(git_blob(main_commit, "manifest.json"))
    market_commit = manifest["market_commit"]
    if data["meta"]["snapshot_id"] != manifest["snapshot_id"]:
        raise ValueError("HTML snapshot does not match pinned main manifest")
    if data["meta"]["publication"]["market_commit"] != market_commit:
        raise ValueError("HTML market commit does not match pinned main manifest")

    market: dict[str, dict] = {}
    page_bytes = 0
    for sha in manifest["market_pages"]:
        content = git_blob(market_commit, f"market-pages/{sha}.json")
        if hashlib.sha256(content).hexdigest() != sha:
            raise ValueError(f"Market page hash mismatch: {sha}")
        page_bytes += len(content)
        for row in json.loads(content)["security_market_data"]:
            ticker = row["ticker"]
            if ticker in market:
                raise ValueError(f"Duplicate market ticker: {ticker}")
            market[ticker] = row

    accepted_tickers = {row["ticker"] for row in data["security_market_data"]}
    if not accepted_tickers <= market.keys():
        raise ValueError("HTML market rows are absent from pinned market pages")
    window_mismatches = []
    for row in data["security_market_data"]:
        full = market[row["ticker"]]["price_history"]
        shown = row["price_history"]
        if not shown or shown != full[-len(shown):]:
            window_mismatches.append(row["ticker"])
    mismatches = []
    counts = {"matched": 0, "unavailable": 0, "html_only": 0}
    for row in data["transactions"]:
        ticker = row.get("ticker")
        series = market.get(ticker) if ticker in accepted_tickers else None
        for field, day in (
            ("underlying_return_since_trade", row["transaction_date"][:10]),
            ("underlying_return_since_filing", row["filed_at"][:10]),
        ):
            expected = event_return(series, day) if series else None
            actual = row.get(field)
            if expected == actual:
                counts["matched" if expected is not None else "unavailable"] += 1
            elif expected is None and actual is not None:
                counts["html_only"] += 1
                if len(mismatches) < 20:
                    mismatches.append({"id": row["id"], "ticker": ticker,
                                       "field": field, "date": day,
                                       "html": actual, "recomputed": expected,
                                       "first_market_date": (series["price_history"][0]["date"]
                                                             if series else None),
                                       "first_market_close_fallback": (
                                           round((series["price_history"][-1]["close"] /
                                                  series["price_history"][0]["close"] - 1)
                                                 * 100, 2) if series else None)})
            else:
                if len(mismatches) < 20:
                    mismatches.append({"id": row["id"], "ticker": ticker,
                                       "field": field, "date": day,
                                       "html": actual, "recomputed": expected})
    return {"html_sha256": html_sha, "snapshot_id": manifest["snapshot_id"],
            "market_commit": market_commit, "market_pages": len(manifest["market_pages"]),
            "market_tickers": len(market), "market_page_bytes": page_bytes,
            "transactions": len(data["transactions"]),
            "html_market_window_mismatch_count": len(window_mismatches),
            "html_market_window_mismatch_sample": window_mismatches[:20],
            "return_fields": counts,
            "mismatch_sample": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--main-commit", default=MAIN_COMMIT)
    args = parser.parse_args()
    result = audit(args.html, args.main_commit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if (result["mismatch_sample"] or result["return_fields"]["html_only"]
                 or result["html_market_window_mismatch_count"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
