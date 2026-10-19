"""Read saved slot observations into a conservative, read-only field ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, nargs="+", required=True)
    parser.add_argument("--secondary-observations", type=Path, nargs="+")
    parser.add_argument("--document-page-count", type=int,
                        help="Whole-document count from the source-bound PDF metadata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = [json.loads(path.read_text(encoding="utf-8"))
              for path in args.observations]
    secondary = ([json.loads(path.read_text(encoding="utf-8"))
                  for path in args.secondary_observations]
                 if args.secondary_observations is not None else None)
    if args.document_page_count is None:
        result = observe_field_shapes(inputs, secondary_observations=secondary)
    else:
        content = observe_content_rows(inputs, document_page_count=args.document_page_count,
                                       secondary_observations=secondary)
        result = content.pop("field_observations")
        result.update(content)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    summary = {"source_sha256": result["source_sha256"], "page_count": result["page_count"],
               "summary": result["summary"]}
    if secondary is not None:
        summary["secondary_field_summary"] = result["secondary_field_summary"]
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
