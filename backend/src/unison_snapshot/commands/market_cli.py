"""Existing market validation, supplement and preparation commands."""
import sys
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from ..alpaca_market import AlpacaMarketClient, build_market_validation
from ..twelve_data_market import TwelveDataClient, TwelveDataError, supplement as supplement_twelve_data
from ..codec import digest, encode
from ..market_store import build_market_bundle, load_published_market_cache, load_published_twelve_data_cache, materialize_market, load_price_cache_with_recovery
from ..legacy import load
from ._io import _write_atomic
from ._runtime import invoke


def _configure_price_recovery(parser):
    parser.add_argument('--recovery-main-root', type=Path)
    parser.add_argument('--recovery-market-root', type=Path)
    parser.add_argument('--recovery-market-commit')


def configure_prepare_market_publication(parser):
    prepare_market = parser
    prepare_market.add_argument("--input", type=Path, required=True)
    prepare_market.add_argument("--root", type=Path, required=True)
    prepare_market.add_argument("--metadata-output", type=Path, required=True)
    prepare_market.add_argument("--audit", type=Path)
    prepare_market.add_argument("--twelve-audit", type=Path)


def execute_prepare_market_publication(args):
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if payload.get("meta", {}).get("is_demo") is not False:
        raise ValueError("Public market publication requires is_demo=false")
    bundle = build_market_bundle(
        payload.get("security_market_data"),
        data_cutoff_at=payload.get("meta", {}).get("data_cutoff_at"),
        audit=(json.loads(args.audit.read_text(encoding="utf-8"))
               if args.audit else None),
        twelve_audit=(json.loads(args.twelve_audit.read_text(encoding="utf-8"))
                      if args.twelve_audit else None))
    result = materialize_market(args.root, bundle)
    _write_atomic(args.metadata_output, {
        "schema_version": "market-publication-plan/v1",
        "data_cutoff_at": bundle.data_cutoff_at,
        "ticker_count": len(bundle.tickers),
        "market_pages": list(bundle.page_shas),
    })
    print(json.dumps({"changed": result.changed, "tickers": len(bundle.tickers),
                      "pages": len(bundle.page_shas),
                      "written": result.written, "removed": result.removed}))


def configure_build_alpaca_market_validation(parser):
    market_validation = parser
    market_validation.add_argument("--input", type=Path, required=True)
    market_validation.add_argument("--output", type=Path, required=True)
    market_validation.add_argument("--audit-output", type=Path, required=True)
    market_validation.add_argument("--processed-output", type=Path)
    market_validation.add_argument("--html-output", type=Path)
    market_validation.add_argument("--checked-at")
    market_validation.add_argument("--as-of-date")
    market_validation.add_argument("--batch-size", type=int, default=50)
    market_validation.add_argument("--timeout", type=float, default=30.0)
    market_validation.add_argument("--key-id-env", default="ALPACA_API_KEY_ID")
    market_validation.add_argument("--secret-key-env", default="ALPACA_API_SECRET_KEY")
    market_validation.add_argument(
        "--assets-url", default="https://paper-api.alpaca.markets/v2/assets",
        help="Allowlisted Alpaca paper or live Assets API endpoint")
    market_validation.add_argument("--distribution-authorized", action="store_true")
    market_validation.add_argument("--previous-main-root", type=Path)
    market_validation.add_argument("--previous-market-root", type=Path)
    market_validation.add_argument("--previous-market-commit")
    _configure_price_recovery(parser)


def execute_build_alpaca_market_validation(args):
    checked_at = args.checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    client = AlpacaMarketClient(
        os.environ.get(args.key_id_env, ""),
        os.environ.get(args.secret_key_env, ""),
        timeout=args.timeout,
        assets_url=args.assets_url,
    )
    cache = load_price_cache_with_recovery(load_published_market_cache,
        main_root=args.previous_main_root, market_root=args.previous_market_root,
        market_commit=args.previous_market_commit, recovery_main_root=args.recovery_main_root,
        recovery_market_root=args.recovery_market_root,
        recovery_market_commit=args.recovery_market_commit)
    validation = build_market_validation(
        payload, client=client, checked_at=checked_at,
        as_of_date=args.as_of_date, batch_size=args.batch_size,
        distribution_authorized=args.distribution_authorized,
        previous_market=cache)
    result = validation.snapshot
    result["meta"]["generated_at"] = checked_at
    result["meta"]["snapshot_id"] = "prepublication-validation"
    processor = load("process_snapshot", version="v2")
    identity = dict(result, meta={key: value for key, value in result["meta"].items()
                                  if key != "snapshot_id"})
    result["meta"]["snapshot_id"] = digest(encode(identity))
    processed = processor.build_snapshot(result)
    audit = dict(validation.audit, candidate_snapshot_id=result["meta"]["snapshot_id"],
                 candidate_sha256=digest(encode(result)))
    _write_atomic(args.output, result)
    _write_atomic(args.audit_output, audit)
    if args.processed_output:
        _write_atomic(args.processed_output, processed)
    if args.html_output:
        renderer = load("render_dashboard", version="v2")
        html = renderer.render_html(renderer.load_dashboard_data(args.output))
        args.html_output.parent.mkdir(parents=True, exist_ok=True)
        args.html_output.write_text(html, encoding="utf-8")
    print(json.dumps({"symbols": audit["symbol_count"],
                      "market_rows": audit["market_row_count"],
                      "cache_status": audit["cache_status"],
                      "incremental_tickers": audit["incremental_ticker_count"],
                      "full_refresh_tickers": audit["full_refresh_ticker_count"],
                      "split_refresh_tickers": audit["split_refresh_ticker_count"],
                      "response_pages": audit.get("response_page_count"),
                      "request_attempts": audit.get("request_attempt_count"),
                      "historical_recovered": audit["historical_recovered_count"],
                      "historical_rejected": len(audit["historical_rejected_symbols"]),
                      "missing_tickers": audit["missing_ticker_count"],
                      "unsupported_tickers": audit["unsupported_ticker_count"],
                      "unresolved_tickers": audit["unresolved_ticker_count"],
                      "recovered_tickers": audit["recovered_ticker_count"],
                      "output": str(args.output.resolve()),
                      "audit": str(args.audit_output.resolve()),
                      "processed": (str(args.processed_output.resolve())
                                    if args.processed_output else None),
                      "html": str(args.html_output.resolve()) if args.html_output else None}))


def configure_build_twelve_data_supplement(parser):
    twelve_market = parser
    twelve_market.add_argument("--input", type=Path, required=True)
    twelve_market.add_argument("--output", type=Path, required=True)
    twelve_market.add_argument("--audit-output", type=Path, required=True)
    twelve_market.add_argument("--processed-output", type=Path)
    twelve_market.add_argument("--html-output", type=Path)
    twelve_market.add_argument("--checked-at")
    twelve_market.add_argument("--limit", type=int)
    twelve_market.add_argument("--key-env", default="TWELVE_DATA_API_KEY")
    twelve_market.add_argument("--distribution-authorized", action="store_true")
    twelve_market.add_argument("--previous-main-root", type=Path)
    twelve_market.add_argument("--previous-market-root", type=Path)
    twelve_market.add_argument("--previous-market-commit")
    _configure_price_recovery(parser)


def execute_build_twelve_data_supplement(args):
    if not args.distribution_authorized:
        raise TwelveDataError("Twelve Data distribution authorization is required")
    checked_at = args.checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    # A zero limit is a cache-only publication: it carries forward
    # already licensed rows and deliberately cannot issue a request.
    key = "cache-only" if args.limit == 0 else os.environ.get(args.key_env, "")
    client = TwelveDataClient(key)
    previous_market = load_price_cache_with_recovery(load_published_twelve_data_cache,
        main_root=args.previous_main_root, market_root=args.previous_market_root,
        market_commit=args.previous_market_commit, recovery_main_root=args.recovery_main_root,
        recovery_market_root=args.recovery_market_root,
        recovery_market_commit=args.recovery_market_commit)
    result, audit = supplement_twelve_data(payload, client=client,
                                           checked_at=checked_at, limit=args.limit,
                                           previous_market=previous_market)
    result["meta"].update(generated_at=checked_at,
                          snapshot_id="prepublication-validation")
    version = ("v2")
    processor = load("process_snapshot", version=version)
    identity = dict(result, meta={key: value for key, value in result["meta"].items()
                                  if key != "snapshot_id"})
    result["meta"]["snapshot_id"] = digest(encode(identity))
    processed = processor.build_snapshot(result)
    audit = dict(audit, candidate_snapshot_id=result["meta"]["snapshot_id"],
                 candidate_sha256=digest(encode(result)))
    _write_atomic(args.output, result)
    _write_atomic(args.audit_output, audit)
    if args.processed_output:
        _write_atomic(args.processed_output, processed)
    if args.html_output:
        renderer = load("render_dashboard", version=version)
        html = renderer.render_html(renderer.load_dashboard_data(args.output))
        args.html_output.parent.mkdir(parents=True, exist_ok=True)
        args.html_output.write_text(html, encoding="utf-8")
    print(json.dumps({"attempted": audit["attempted_count"],
                      "accepted": audit["accepted_count"],
                      "remaining": audit["remaining_count"],
                      "requests": audit["request_count"],
                      "output": str(args.output.resolve())}))


COMMANDS = {
    'prepare-market-publication': (configure_prepare_market_publication, execute_prepare_market_publication),
    'build-alpaca-market-validation': (configure_build_alpaca_market_validation, execute_build_alpaca_market_validation),
    'build-twelve-data-supplement': (configure_build_twelve_data_supplement, execute_build_twelve_data_supplement),
}


def main():
    command = sys.argv[0].rsplit(" ", 1)[-1]
    invoke(command, *COMMANDS[command])
