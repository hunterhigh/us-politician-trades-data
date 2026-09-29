"""CLI for producing an OGE 278-T source shadow artifact."""
from __future__ import annotations

import argparse
import json
import sys

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.pipeline_ledger import LedgerValidationError

from .oge_run import OgeShadowInputError, build_oge_shadow_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an offline OGE 278-T shadow ledger artifact.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--archive-batch", required=True)
    parser.add_argument("--extraction-batch", required=True)
    parser.add_argument("--extractions-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--review-commit")
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--completed-at", required=True)
    parser.add_argument("--trigger", choices=("schedule", "workflow_run", "workflow_dispatch",
                                              "replay", "local"), default="local")
    parser.add_argument("--workflow-run-id")
    args = parser.parse_args(argv)
    try:
        manifest = build_oge_shadow_run(
            catalog_path=args.catalog, archive_batch_path=args.archive_batch,
            extraction_batch_path=args.extraction_batch,
            extractions_dir=args.extractions_dir, output_dir=args.output_dir,
            run_id=args.run_id, code_commit=args.code_commit,
            evidence_commit=args.evidence_commit,
            review_commit=args.review_commit,
            started_at=args.started_at, completed_at=args.completed_at,
            trigger=args.trigger, workflow_run_id=args.workflow_run_id)
    except (OgeShadowInputError, OgeCatalogError, LedgerValidationError, OSError) as exc:
        print(f"OGE shadow run failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": manifest["run_id"],
                      "discovered_documents": manifest["counts"]["discovered_documents"],
                      "archived_documents": manifest["counts"]["archived_documents"],
                      "accounted_rows": manifest["accounted_rows"],
                      "output_dir": args.output_dir}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
