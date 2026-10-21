"""Read a prepared publication from remote immutable commits before moving production refs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
from threading import Lock
from urllib.parse import urlsplit

from unison_snapshot.codec import encode, digest
from unison_snapshot.legacy import load
from unison_snapshot.oge_278e_public import TRUMP_2025_SOURCE_URL
from unison_snapshot.oge_reports import (
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_SOURCE_URL,
)
from unison_snapshot.public_repo import HTTPTransport, PublicSnapshotRepository


SHA1 = re.compile(r"[0-9a-f]{40}\Z")


def _official_whitehouse_pdf(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.hostname == "www.whitehouse.gov" and
            parsed.username is None and parsed.password is None and
            port is None and not parsed.query and not parsed.fragment and
            parsed.path.startswith("/wp-content/uploads/") and
            parsed.path.lower().endswith(".pdf"))


def _official_trump_transaction_source(row: dict) -> bool:
    filing_id = row.get("filing_id")
    if isinstance(filing_id, str) and filing_id.startswith("wh-url:"):
        return _official_whitehouse_pdf(row.get("source_url"))
    return (filing_id == TRUMP_SEPT_2026_DOCUMENT_ID and
            row.get("source_url") == TRUMP_SEPT_2026_SOURCE_URL and
            isinstance(row.get("id"), str) and
            row["id"].startswith("oge-278t:"))


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


class PublishedMarketTransport:
    """Reuse immutable bytes only at an already published market commit.

    A different commit always goes through HTTP, even when page hashes match:
    publication must still demonstrate that the new commit exposes those files.
    Main objects and market indexes are always read remotely.
    """

    def __init__(self, *, owner: str, repo: str, main_root: Path, market_root: Path,
                 market_commit: str, delegate=None):
        manifest = json.loads((main_root / "manifest.json").read_bytes())
        if not SHA1.fullmatch(market_commit) or manifest.get("market_commit") != market_commit \
                or manifest.get("is_demo") is not False:
            raise ValueError("Readback cache requires a published main/market pair")
        self.prefix = f"https://raw.githubusercontent.com/{owner}/{repo}/{market_commit}/"
        self.root = market_root.resolve()
        self.pages = set(manifest.get("market_pages", []))
        self.delegate = delegate or HTTPTransport(retries=4, backoff=1.0)
        self.lock = Lock()
        self.metrics = dict(reused_objects=0, reused_bytes=0, cache_misses=0,
                            remote_requests=0, remote_decoded_bytes=0)

    def get(self, url: str, limit: int, *, api: bool = False) -> bytes:
        relative = url.removeprefix(self.prefix) if url.startswith(self.prefix) else ""
        match = re.fullmatch(r"(market-pages|market/[0-9a-f]{2})/([0-9a-f]{64})\.json", relative)
        if not api and match and (match[1] != "market-pages" or match[2] in self.pages):
            path = (self.root / relative).resolve()
            if path.is_relative_to(self.root) and path.is_file() and path.stat().st_size <= limit:
                raw = path.read_bytes()
                if len(raw) <= limit and digest(raw) == match[2]:
                    with self.lock:
                        self.metrics["reused_objects"] += 1
                        self.metrics["reused_bytes"] += len(raw)
                    return raw
            with self.lock:
                self.metrics["cache_misses"] += 1
        raw = self.delegate.get(url, limit, api=api)
        with self.lock:
            self.metrics["remote_requests"] += 1
            self.metrics["remote_decoded_bytes"] += len(raw)
        return raw


def verify_prepared_publication(
        *, owner: str, repo: str, main_commit: str, market_commit: str,
        person_id: str, ticker: str, twelve_ticker: str, output_dir: Path,
        expected_people: int, expected_transactions: int,
        expected_holdings: int, expected_market_rows: int,
        expected_source_health: int, expected_person_holdings: int,
        expected_person_transactions: int,
        require_disclosure_status: bool = False,
        expected_review_commit: str | None = None,
        expected_evidence_commit: str | None = None,
        transport=None) -> dict:
    if not SHA1.fullmatch(market_commit):
        raise ValueError("Prepared market commit is invalid")
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    repository = PublicSnapshotRepository(
        owner, repo, ref="main",
        transport=transport or FixedCommitTransport(main_commit),
        cache_bytes=256 * 1024 * 1024)
    status = repository.disclosure_status(main_commit, include_records=True)
    if require_disclosure_status and status is None:
        raise ValueError("Prepared publication is missing disclosure processing status")
    if status is not None:
        for key, expected_commit in (("review_commit", expected_review_commit),
                                     ("evidence_commit", expected_evidence_commit)):
            if expected_commit is not None and status["provenance"][key] != expected_commit:
                raise ValueError(f"Prepared disclosure status has an unexpected {key}")
        if sum(s["counts"]["publication_transaction_count"] +
               s["counts"]["unmatched_publication_transaction_count"]
               for s in status["sources"]) != expected_transactions:
            raise ValueError("Prepared disclosure status does not account for all transactions")
        (output_dir / "disclosure-status.json").write_bytes(encode(status))
    # The person shard is small enough to reject bad provenance before fetching
    # the much larger dashboard and search snapshots.
    person_selection = repository.fetch("person", person_id)
    if person_selection.commit != main_commit:
        raise ValueError("Prepared person selection resolved an unexpected main commit")
    person = person_selection.snapshot
    if person["meta"].get("market_commit") != market_commit:
        raise ValueError("Prepared person selection references an unexpected market commit")
    if (person["meta"]["selection_scope"].get("key") != person_id or
            len(person["reported_holdings"]) != expected_person_holdings or
            len(person["transactions"]) != expected_person_transactions):
        raise ValueError("Prepared Trump person readback is incomplete")
    if (person["meta"].get("is_demo") is True or any(
            row.get("source_url") != TRUMP_2025_SOURCE_URL or
            row.get("report_period_end") != "2025-12-31" or
            row.get("verification_status") != "official_matched"
            for row in person["reported_holdings"])):
        raise ValueError("Prepared Trump holding provenance is invalid")
    if (expected_person_transactions < 1 or
            not any(isinstance(row.get("filing_id"), str) and
                    row["filing_id"].startswith("wh-url:") and
                    _official_whitehouse_pdf(row.get("source_url"))
                    for row in person["transactions"]) or
            any(row.get("person_id") != person_id or
                row.get("source_id") != "oge" or
                row.get("verification_status") != "official_matched" or
                not _official_trump_transaction_source(row)
                for row in person["transactions"])):
        raise ValueError("Prepared Trump transaction provenance is invalid")
    selections = {
        "person": person_selection,
        "dashboard": repository.fetch("dashboard"),
        "search": repository.fetch("search"),
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
    dashboard = selections["dashboard"].snapshot
    expected = {
        "people": expected_people,
        "transactions": expected_transactions,
        "reported_holdings": expected_holdings,
        "security_market_data": expected_market_rows,
        "source_health": expected_source_health,
    }
    observed = {field: len(dashboard[field]) for field in expected}
    if observed != expected:
        raise ValueError(f"Prepared dashboard counts differ from the candidate: {observed}")
    person_transaction_ids = {row.get("id") for row in person["transactions"]}
    if (None in person_transaction_ids or
            person_id not in {row.get("id") for row in selections["search"].snapshot["people"]} or
            any(not person_transaction_ids <= {
                    row.get("id") for row in selections[mode].snapshot["transactions"]}
                for mode in ("dashboard", "search"))):
        raise ValueError("Prepared dashboard/search omit Trump transactions")
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

    read_seconds = time.perf_counter() - started
    # Only dashboard.json is consumed by the renderer. The other selections
    # have already been checked above; serializing their full price histories
    # produced large files with no downstream reader.
    (output_dir / "dashboard.json").write_bytes(encode(dashboard))
    written_at = time.perf_counter()
    renderer = load("render_dashboard", version="v2")
    html = renderer.render_html(renderer.load_dashboard_data(output_dir / "dashboard.json"))
    (output_dir / "dashboard.html").write_text(html, encoding="utf-8")
    return {"main_commit": main_commit, "market_commit": market_commit,
            "person_id": person_id, "ticker": ticker.upper(),
            "twelve_ticker": twelve_ticker.upper(),
            "people_count": len(selections["dashboard"].snapshot["people"]),
            "transaction_count": len(selections["dashboard"].snapshot["transactions"]),
            "reported_holding_count": len(
                selections["dashboard"].snapshot["reported_holdings"]),
            "person_transaction_count": len(person["transactions"]),
            "timings": {"read_and_check_seconds": round(read_seconds, 3),
                        "serialize_seconds": round(written_at - started - read_seconds, 3),
                        "render_seconds": round(time.perf_counter() - written_at, 3)},
            "trump_transactions_visible_in_dashboard_search": True,
            "disclosure_status_verified": status is not None}


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
    parser.add_argument("--expected-people", type=int, required=True)
    parser.add_argument("--expected-transactions", type=int, required=True)
    parser.add_argument("--expected-holdings", type=int, required=True)
    parser.add_argument("--expected-market-rows", type=int, required=True)
    parser.add_argument("--expected-source-health", type=int, required=True)
    parser.add_argument("--expected-person-holdings", type=int, required=True)
    parser.add_argument("--expected-person-transactions", type=int, required=True)
    parser.add_argument("--require-disclosure-status", action="store_true")
    parser.add_argument("--expected-review-commit")
    parser.add_argument("--expected-evidence-commit")
    parser.add_argument("--published-main-root", type=Path)
    parser.add_argument("--published-market-root", type=Path)
    parser.add_argument("--published-market-commit")
    args = parser.parse_args()
    cached = None
    options = (args.published_main_root, args.published_market_root, args.published_market_commit)
    if any(options):
        if not all(options):
            parser.error("Published main, market root and market commit must be supplied together")
        cached = PublishedMarketTransport(owner=args.owner, repo=args.repo,
            main_root=args.published_main_root, market_root=args.published_market_root,
            market_commit=args.published_market_commit)
    result = verify_prepared_publication(
        owner=args.owner, repo=args.repo, main_commit=args.main_commit,
        market_commit=args.market_commit, person_id=args.person_id,
        ticker=args.ticker, twelve_ticker=args.twelve_ticker,
        output_dir=args.output_dir, expected_people=args.expected_people,
        expected_transactions=args.expected_transactions,
        expected_holdings=args.expected_holdings,
        expected_market_rows=args.expected_market_rows,
        expected_source_health=args.expected_source_health,
        expected_person_holdings=args.expected_person_holdings,
        expected_person_transactions=args.expected_person_transactions,
        require_disclosure_status=args.require_disclosure_status,
        expected_review_commit=args.expected_review_commit,
        expected_evidence_commit=args.expected_evidence_commit,
        transport=FixedCommitTransport(args.main_commit, delegate=cached))
    if cached:
        result["transport"] = cached.metrics
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
