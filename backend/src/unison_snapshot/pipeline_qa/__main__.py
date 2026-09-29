"""CLI entry point: python -m unison_snapshot.pipeline_qa."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .diff import DiffInputError, compare_runs, load_manifest, load_rows, validate_ledger_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline, read-only comparison of pipeline run outputs.")
    parser.add_argument("--old-rows", required=True, help="Old run candidate rows: JSON, JSONL, or CSV")
    parser.add_argument("--new-rows", required=True, help="New run candidate rows: JSON, JSONL, or CSV")
    parser.add_argument("--old-manifest", required=True, help="Old pipeline-run-manifest/v1 JSON")
    parser.add_argument("--new-manifest", required=True, help="New pipeline-run-manifest/v1 JSON")
    parser.add_argument("--old-artifact-root", help="Root directory for verifying old manifest output hashes")
    parser.add_argument("--new-artifact-root", help="Root directory for verifying new manifest output hashes")
    parser.add_argument("--json-out", help="Write the complete machine-readable report to this path")
    args = parser.parse_args(argv)
    try:
        old_rows, new_rows = load_rows(args.old_rows), load_rows(args.new_rows)
        old_manifest, new_manifest = load_manifest(args.old_manifest), load_manifest(args.new_manifest)
        validate_ledger_rows(old_rows, "old")
        validate_ledger_rows(new_rows, "new")
        report = compare_runs(old_rows, new_rows, old_manifest, new_manifest,
                              old_artifact_root=args.old_artifact_root,
                              new_artifact_root=args.new_artifact_root)
    except (DiffInputError, OSError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(serialized + "\n", encoding="utf-8")
    print(_summary(report))
    if args.json_out:
        print(f"JSON report: {args.json_out}")
    return 0 if report["ok"] else 1


def _summary(report: dict) -> str:
    lines = [f"Pipeline diff: {report['runs']['old']} to {report['runs']['new']}"]
    for check in report["conservation"]:
        state = "PASS" if not check["mismatches"] and not check["invalid_dispositions"] else "FAIL"
        lines.append(f"{state} {check['label']} run row conservation: {check['actual_accounted_rows']}/{check['loaded_rows']} rows; "
                     f"manifest accounted={check['manifest_accounted_rows']}")
        if check["mismatches"]:
            lines.append(f"  count mismatches: {json.dumps(check['mismatches'], ensure_ascii=False, sort_keys=True)}")
        if check["invalid_dispositions"]:
            lines.append(f"  invalid dispositions: {json.dumps(check['invalid_dispositions'], ensure_ascii=False, sort_keys=True)}")
    for check in report["artifact_checks"]:
        state = check["status"].upper()
        lines.append(f"{state} {check['label']} manifest output hashes: {check['checked']} checked")
        for failure in check["failures"]:
            lines.append(f"  {failure['path']}: {failure['reason']}")
    lines.append(f"Row changes: +{report['added_count']} added, -{report['removed_count']} removed, "
                 f"~{report['changed_count']} changed, {len(report['duplicates'])} duplicate identities")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
