"""Collect and replay current senators' latest public eFD annual reports."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from unison_snapshot.senate import (SenateEfdClient, SenateEfdError,
                                   source_config_from_environment, require_collection_enabled)
from unison_snapshot.senate_annual import (
    archive_selected_annuals, build_annual_review, discover_annuals,
    overlay_annual_candidate, select_current_annual_versions, _json, _write_once,
)
from unison_snapshot.builder import build as build_snapshot


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SenateEfdError(f"Senate annual input is invalid: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--evidence-root", type=Path, required=True)
    collect.add_argument("--roster", type=Path, required=True)
    collect.add_argument("--submitted-start-date", default="2025-01-01")
    collect.add_argument("--limit", type=int, default=200)
    build = commands.add_parser("build")
    build.add_argument("--evidence-root", type=Path, required=True)
    build.add_argument("--review-root", type=Path, required=True)
    build.add_argument("--roster", type=Path, required=True)
    overlay = commands.add_parser("overlay")
    overlay.add_argument("--review-root", type=Path, required=True)
    overlay.add_argument("--candidate", type=Path, required=True)
    overlay.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            roster = _read(args.roster)
            config = source_config_from_environment()
            require_collection_enabled(config)
            client = SenateEfdClient()
            discovery, pages = discover_annuals(
                client, config, submitted_start_date=args.submitted_start_date)
            folder = args.evidence_root / "senate_efd/annual/catalog"
            for raw, start in pages:
                _write_once(folder / "pages" / f"{hashlib.sha256(raw).hexdigest()}.json", raw)
            catalog_raw = _json(discovery)
            _write_once(folder / f"{hashlib.sha256(catalog_raw).hexdigest()}.json", catalog_raw)
            (folder / "current.json").write_bytes(catalog_raw)
            selected, selection = select_current_annual_versions(discovery, roster)
            archive = archive_selected_annuals(
                client, args.evidence_root, selected, limit=args.limit)
            print(json.dumps({**selection, **archive,
                              "catalog_record_count": discovery["catalog_record_count"],
                              "annual_report_count": discovery["annual_report_count"]},
                             sort_keys=True))
        elif args.command == "build":
            roster = _read(args.roster)
            result = build_annual_review(args.evidence_root, args.review_root, roster)
            print(json.dumps({"selected_report_count": result["selected_report_count"],
                              "qualified_report_count": result["qualified_report_count"],
                              "holding_count": result["holding_count"]}, sort_keys=True))
        else:
            status = _read(args.review_root / "status/senate_efd.json")
            before = _read(args.candidate)
            candidate, audit = overlay_annual_candidate(
                before, args.review_root,
                expected_roster_sha256=status["identity_roster_sha256"])
            bundle = build_snapshot(candidate,
                                    generated_at=candidate["meta"]["data_cutoff_at"],
                                    allow_production=True)
            candidate["meta"]["snapshot_id"] = bundle.manifest["snapshot_id"]
            args.candidate.write_bytes(_json(candidate))
            args.audit_output.parent.mkdir(parents=True, exist_ok=True)
            args.audit_output.write_bytes(_json(audit))
            print(json.dumps(audit, sort_keys=True))
    except (SenateEfdError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Senate annual task failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
