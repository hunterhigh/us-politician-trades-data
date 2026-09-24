"""Read a prepared publication from remote immutable commits before moving production refs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from unison_snapshot.codec import encode
from unison_snapshot.legacy import load
from unison_snapshot.public_repo import HTTPTransport, PublicSnapshotRepository


SHA1 = re.compile(r"[0-9a-f]{40}\Z")


class FixedCommitTransport:
    """Resolve ``main`` to a candidate commit while retaining real raw HTTP reads."""

    def __init__(self, commit: str, *, delegate=None):
        if not SHA1.fullmatch(commit):
            raise ValueError("Prepared main commit is invalid")
        self.commit = commit
        self.delegate = delegate or HTTPTransport(retries=4, backoff=1.0)

    def get(self, url: str, limit: int, *, api: bool = False) -> bytes:
        if api:
            return encode({"object": {"type": "commit", "sha": self.commit}})
        return self.delegate.get(url, limit, api=False)


def verify_prepared_publication(
        *, owner: str, repo: str, main_commit: str, market_commit: str,
        person_id: str, ticker: str, twelve_ticker: str, output_dir: Path,
        transport=None) -> dict:
    if not SHA1.fullmatch(market_commit):
        raise ValueError("Prepared market commit is invalid")
    output_dir.mkdir(parents=True, exist_ok=True)
    repository = PublicSnapshotRepository(
        owner, repo, ref="main",
        transport=transport or FixedCommitTransport(main_commit))
    selections = {
        "dashboard": repository.fetch("dashboard"),
        "search": repository.fetch("search"),
        "person": repository.fetch("person", person_id),
        "ticker": repository.fetch("ticker", ticker),
        "twelve": repository.fetch("ticker", twelve_ticker),
    }
    for mode, selection in selections.items():
        if selection.commit != main_commit:
            raise ValueError(f"Prepared {mode} selection resolved an unexpected main commit")
        if selection.snapshot["meta"].get("market_commit") != market_commit:
            raise ValueError(f"Prepared {mode} selection references an unexpected market commit")
    for mode in ("dashboard", "search"):
        snapshot = selections[mode].snapshot
        for field in ("people", "transactions", "reported_holdings",
                      "security_market_data", "source_health"):
            if not snapshot.get(field):
                raise ValueError(f"Prepared {mode} selection has an empty {field} array")
    person = selections["person"].snapshot
    if (person["meta"]["selection_scope"].get("key") != person_id or
            not person["reported_holdings"]):
        raise ValueError("Prepared holding-only person readback is incomplete")
    ticker_value = selections["ticker"].snapshot
    if (ticker_value["meta"]["selection_scope"].get("key") != ticker.upper() or
            not ticker_value["transactions"] or
            not ticker_value["security_market_data"]):
        raise ValueError("Prepared ticker readback is incomplete")
    twelve_value = selections["twelve"].snapshot
    if (twelve_value["meta"]["selection_scope"].get("key") != twelve_ticker.upper() or
            not any(row.get("ticker") == twelve_ticker.upper() and
                    row.get("source_id") == "twelve_data_split_adjusted_eod"
                    for row in twelve_value["security_market_data"])):
        raise ValueError("Prepared Twelve Data ticker readback is incomplete")

    for mode, selection in selections.items():
        (output_dir / f"{mode}.json").write_bytes(encode(selection.snapshot))
    renderer = load("render_dashboard", version="v2")
    html = renderer.render_html(renderer.load_dashboard_data(output_dir / "dashboard.json"))
    (output_dir / "dashboard.html").write_text(html, encoding="utf-8")
    return {"main_commit": main_commit, "market_commit": market_commit,
            "person_id": person_id, "ticker": ticker.upper(),
            "twelve_ticker": twelve_ticker.upper(),
            "people_count": len(selections["dashboard"].snapshot["people"]),
            "transaction_count": len(selections["dashboard"].snapshot["transactions"]),
            "reported_holding_count": len(
                selections["dashboard"].snapshot["reported_holdings"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--main-commit", required=True)
    parser.add_argument("--market-commit", required=True)
    parser.add_argument("--person-id", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--twelve-ticker", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = verify_prepared_publication(
        owner=args.owner, repo=args.repo, main_commit=args.main_commit,
        market_commit=args.market_commit, person_id=args.person_id,
        ticker=args.ticker, twelve_ticker=args.twelve_ticker,
        output_dir=args.output_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
