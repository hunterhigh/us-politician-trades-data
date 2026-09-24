"""Persist unique Alpaca-name ticker mappings into the White House review candidate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from unison_snapshot.alpaca_market import AlpacaMarketClient
from unison_snapshot.whitehouse_annual_tickers import enrich_whitehouse_annual_tickers


def _write(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    parser.add_argument("--checked-at")
    parser.add_argument("--assets-url", default="https://paper-api.alpaca.markets/v2/assets")
    parser.add_argument("--distribution-authorized", action="store_true")
    args = parser.parse_args()
    if not args.distribution_authorized:
        raise ValueError("Alpaca distribution authorization is required")
    client = AlpacaMarketClient(
        os.environ.get("ALPACA_API_KEY_ID", ""),
        os.environ.get("ALPACA_API_SECRET_KEY", ""),
        assets_url=args.assets_url,
    )
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    previous = (json.loads(args.mapping_output.read_text(encoding="utf-8"))
                if args.mapping_output.is_file() else None)
    checked_at = args.checked_at or datetime.now(timezone.utc).isoformat()
    result, audit = enrich_whitehouse_annual_tickers(
        candidate, client.assets(), checked_at=checked_at, previous=previous)
    _write(args.candidate, result)
    _write(args.mapping_output, audit)
    print(json.dumps({key: audit[key] for key in (
        "annual_transaction_count", "mapping_count", "retained_mapping_count",
        "new_mapping_count", "ambiguous_record_count", "unmatched_record_count")},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
