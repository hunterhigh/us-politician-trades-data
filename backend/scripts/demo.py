"""One command: publish synthetic data, reconstruct pinned version, render baseline."""
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND / "src"))
from unison_snapshot.builder import build
from unison_snapshot.codec import encode
from unison_snapshot.legacy import load
from unison_snapshot.store import GitStore, assemble


def main():
    output = ROOT / ".local/demo"
    output.mkdir(parents=True, exist_ok=True)
    data = json.loads((BACKEND / "examples/synthetic.json").read_text(encoding="utf-8"))
    bundle = build(data, generated_at="2026-09-18T00:01:00Z")
    store = GitStore(output / "snapshots.git")
    store.initialize()
    commit = store.publish(bundle, expected_head=store.head())
    snapshot = assemble(store, commit)
    processed = load("process_snapshot").build_snapshot(snapshot)
    (output / "assembled.json").write_bytes(encode(snapshot))
    (output / "processed.json").write_bytes(encode(processed))
    renderer = load("render_dashboard")
    html = renderer.render_html(renderer.load_dashboard_data(output / "assembled.json"))
    (output / "dashboard.html").write_text(html, encoding="utf-8")
    report = {"is_demo": True, "commit": commit, "snapshot_id": bundle.manifest["snapshot_id"],
              "published_files": len(bundle.files), "people": len(snapshot["people"]),
              "transactions": len(snapshot["transactions"]), "holdings": len(snapshot["reported_holdings"]),
              "new_consumer_verified": False, "live_sources_enabled": False,
              "html": str(output / "dashboard.html")}
    (output / "result.json").write_bytes(encode(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
