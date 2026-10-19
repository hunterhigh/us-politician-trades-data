"""Read saved slot observations into a conservative, read-only field ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, nargs="+", required=True)
    parser.add_argument("--secondary-observations", type=Path, nargs="+")
    parser.add_argument("--read-description-pdf", type=Path)
    parser.add_argument("--description-output", type=Path)
    parser.add_argument("--description-executable")
    parser.add_argument("--description-pages", type=int, nargs="+")
    parser.add_argument("--document-page-count", type=int,
                        help="Whole-document count from the source-bound PDF metadata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = [json.loads(path.read_text(encoding="utf-8"))
              for path in args.observations]
    secondary = ([json.loads(path.read_text(encoding="utf-8"))
                  for path in args.secondary_observations]
                 if args.secondary_observations is not None else None)
    if bool(args.read_description_pdf) != bool(args.description_output):
        parser.error("--read-description-pdf and --description-output must be provided together")
    if args.description_pages is not None and args.read_description_pdf is None:
        parser.error("--description-pages requires --read-description-pdf")
    if args.read_description_pdf is not None:
        if len(inputs) != 1:
            parser.error("one bounded primary input is required when reading the description column")
        observe_field_shapes(inputs, secondary_observations=secondary)
        from unison_snapshot.oge_278t_type_column_shadow import observe_description_column
        if args.description_output.exists():
            description = json.loads(args.description_output.read_text(encoding="utf-8"))
            if (hashlib.sha256(args.read_description_pdf.read_bytes()).hexdigest() != inputs[0]["source_sha256"] or
                    description.get("schema_version") != "oge-278t-description-column-observation/v1" or
                    {p["page_number"] for p in description.get("pages", [])} !=
                    set(args.description_pages or [p["page_number"] for p in inputs[0]["pages"]])):
                raise ValueError("cached description read differs from source PDF or schema")
        else:
            description = observe_description_column(
                args.read_description_pdf, slots_observation=inputs[0], executable=args.description_executable,
                page_numbers=args.description_pages)
            args.description_output.parent.mkdir(parents=True, exist_ok=True)
            args.description_output.write_text(json.dumps(description, ensure_ascii=False, sort_keys=True,
                                                          separators=(",", ":")), encoding="utf-8")
        secondary = [*(secondary or []), description]
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
