"""Publish current disclosures using existing or asynchronously prepared prices."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from unison_snapshot.cached_publication import join_disclosures, published_prices, prepared_prices
from unison_snapshot.codec import digest, encode
from unison_snapshot.legacy import load, processor_version_for, PROCESSOR_V2_SHA256, PROCESSOR_V3_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--main-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--market-commit", required=True)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir
    if args.prepared:
        prices = prepared_prices(args.prepared, args.market_commit)
    else:
        prices = published_prices(args.main_root, args.market_root, args.market_commit)
    disclosures = json.loads(args.input.read_text(encoding="utf-8"))
    candidate, audit = join_disclosures(disclosures, prices,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    identity = dict(candidate, meta={k: v for k, v in candidate["meta"].items() if k != "snapshot_id"})
    candidate["meta"]["snapshot_id"] = digest(encode(identity))
    root.mkdir(parents=True, exist_ok=True)
    target = root / "complete-candidate.json"
    candidate_bytes = encode(candidate)
    target.write_bytes(candidate_bytes)
    candidate_sha = digest(candidate_bytes)
    del candidate_bytes
    renderer = load("render_dashboard", version=processor_version_for(candidate))
    (root / "complete-dashboard.html").write_text(
        renderer.render_html(renderer.load_dashboard_data(target)), encoding="utf-8")
    # Only a successful rendering produces this receipt. The final producer
    # checks the exact input bytes and pinned processor before reusing it.
    (root / "consumer-validation.json").write_bytes(encode({
        "input_sha256": candidate_sha, "processor_sha256": PROCESSOR_V3_SHA256 if processor_version_for(candidate) == "v3" else PROCESSOR_V2_SHA256}))
    audit.update(mode="refreshed" if args.prepared else "cached", base_market_commit=args.market_commit,
                 vendor_requests_in_publish_job=0)
    (root / "publication-market-audit.json").write_bytes(encode(audit))
    if not args.prepared:
        manifest = json.loads((args.main_root / "manifest.json").read_text(encoding="utf-8"))
        (root / "market-publication.json").write_bytes(encode({"market_pages": manifest["market_pages"]}))
    print(json.dumps(audit))


if __name__ == "__main__":
    main()
