#!/usr/bin/env python3
"""Build an offline, fixed-commit handoff fixture for browser acceptance."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
HANDOFF_ROOT = ROOT / "review-input" / "us-politician-trades-watch"
sys.path.insert(0, str(BACKEND_SRC))

from unison_snapshot.builder import build  # noqa: E402
from unison_snapshot.codec import encode  # noqa: E402
from unison_snapshot.legacy import load  # noqa: E402
from unison_snapshot.public_repo import (  # noqa: E402
    PublicSnapshotError,
    PublicSnapshotRepository,
)
from unison_snapshot.store import GitStore  # noqa: E402


class LocalPublishedTransport:
    """Serve the public-reader URL contract from a published local Git commit."""

    def __init__(self, store: GitStore, *, corrupt_board: bool = False):
        self.store = store
        self.commit = store.head()
        if self.commit is None:
            raise RuntimeError("Synthetic fixture has no published commit")
        self.corrupt_board = corrupt_board
        self.calls: list[tuple[str, bool]] = []

    def get(self, url: str, limit: int, *, api: bool = False) -> bytes:
        self.calls.append((url, api))
        if api:
            value = encode({"object": {"type": "commit", "sha": self.commit}})
        else:
            marker = f"/{self.commit}/"
            if marker not in url:
                raise AssertionError("Reader did not pin raw reads to the resolved commit")
            path = url.split(marker, 1)[1]
            try:
                value = self.store.read(self.commit, path)
            except RuntimeError:
                raise PublicSnapshotError("Repository returned HTTP 404") from None
            if self.corrupt_board and path.startswith("board/"):
                value += b"\n"
        if len(value) > limit:
            raise PublicSnapshotError("Repository object exceeds the contract size limit")
        return value


def load_handoff_script(name: str):
    path = HANDOFF_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"handoff_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load handoff script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(selection: dict, destination: Path) -> dict:
    processor = load("process_snapshot")
    renderer = load("render_dashboard")
    processed = processor.build_snapshot(selection)
    for person in processed["people"]:
        person["portrait_data_uri"] = renderer.initials_data_uri(person)
        person["portrait_is_placeholder"] = True
    destination.write_text(renderer.render_html(processed), encoding="utf-8")
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    payload = json.loads((ROOT / "backend" / "examples" / "synthetic.json").read_text(encoding="utf-8"))
    bundle = build(payload, generated_at="2026-09-18T00:01:00Z")
    store = GitStore(output / "snapshot.git")
    store.initialize()
    commit = store.publish(bundle, expected_head=None)

    transport = LocalPublishedTransport(store)
    repo = PublicSnapshotRepository("offline", "fixture", ref="demo", transport=transport)
    selections = {
        "dashboard": repo.fetch("dashboard"),
        "search": repo.fetch("search"),
        "person": repo.fetch("person", "Demo Person One"),
        "ticker": repo.fetch("ticker", "zzdemo"),
    }
    if any(item.commit != commit for item in selections.values()):
        raise AssertionError("A handoff selection escaped the published fixture commit")
    raw_calls = [url for url, api in transport.calls if not api]
    if not raw_calls or any(f"/{commit}/" not in url for url in raw_calls):
        raise AssertionError("Raw snapshot reads were not frozen to one commit")

    processed = {}
    for mode, selection in selections.items():
        destination = output / f"{mode}.html"
        processed[mode] = render(selection.snapshot, destination)

    query = load_handoff_script("query_snapshot")
    query_cases = {
        "person": SimpleNamespace(person="Demo Person One", ticker=None, latest=None, limit=20),
        "ticker": SimpleNamespace(person=None, ticker="ZZDEMO", latest=None, limit=20),
        "no_matches": SimpleNamespace(person="Nobody Here", ticker=None, latest=None, limit=20),
    }
    query_results = {name: query.project(processed["search"], case) for name, case in query_cases.items()}
    if query_results["person"]["status"] != "ok" or not query_results["person"]["transactions"]:
        raise AssertionError("Person evidence query did not retain the matched disclosure")
    if query_results["ticker"]["match_summary"]["transaction_record_count"] != 2:
        raise AssertionError("Ticker evidence query lost disclosure rows")
    if query_results["no_matches"]["status"] != "no_matches":
        raise AssertionError("Unknown evidence query did not preserve no_matches semantics")
    (output / "queries.json").write_text(
        json.dumps(query_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    errors = {}
    for label, mode, key in (
        ("unknown_person", "person", "Nobody Here"),
        ("unknown_ticker", "ticker", "NOPE"),
    ):
        try:
            repo.fetch(mode, key)
        except PublicSnapshotError as exc:
            errors[label] = str(exc)
        else:
            raise AssertionError(f"{label} unexpectedly loaded")
    try:
        PublicSnapshotRepository(
            "offline", "fixture", ref="demo",
            transport=LocalPublishedTransport(store, corrupt_board=True),
        ).fetch("dashboard")
    except PublicSnapshotError as exc:
        errors["corrupt_content"] = str(exc)
    else:
        raise AssertionError("A corrupt content shard unexpectedly loaded")

    metadata = {
        "commit": commit,
        "is_demo": True,
        "schema_version": bundle.manifest["schema_version"],
        "modes": {mode: str((output / f"{mode}.html").resolve()) for mode in selections},
        "query_results": str((output / "queries.json").resolve()),
        "expected_errors": errors,
    }
    (output / "fixture.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
