"""Read saved slot observations into a conservative, read-only field ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unison_snapshot.oge_278t_field_shadow import (
    LEGACY_AMOUNT_INTERPRETATION_MODE, observe_content_rows, observe_field_shapes,
)


def _preparation_outputs(args, saved):
    """Check both destinations before writing either prepared artifact."""
    from unison_snapshot.oge_saved_observations import guard_saved_oge_outputs
    document_id, request = next(iter(saved.items()))
    sha = request["observations"][0]["source_sha256"]
    review, evidence = args.review_root.resolve(), args.evidence_root.resolve()
    package = (review / "oge/saved_observations" / document_id / sha /
               "saved-observations.json").resolve()
    if not package.is_relative_to(review):
        raise ValueError("saved observations output escapes review root")
    # prepare() has already verified these canonical doc/SHA paths. Include the
    # files, as well as their root, so an outside-root hardlink cannot truncate
    # the original through either output.
    original = evidence / "oge/reports" / document_id / f"{sha}.pdf"
    protected = [*args.observations, *(args.secondary_observations or []), args.metadata,
                 original, original.with_suffix(".json")]
    guard_saved_oge_outputs((args.output, package), inputs=protected, protected_roots=(evidence,))
    return package


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
    parser.add_argument("--review-root", type=Path,
                        help="Also prepare a portable saved-observation input under this review root")
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--metadata", type=Path,
                        help="Canonical archived OGE report metadata; requires both roots")
    parser.add_argument("--field-interpretation-mode", choices=[LEGACY_AMOUNT_INTERPRETATION_MODE],
                        help="Explicitly isolate unresolved legacy amount derivations; requires saved-input preparation")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    preparation = any(value is not None for value in (args.review_root, args.evidence_root, args.metadata))
    if preparation and not all(value is not None for value in (args.review_root, args.evidence_root, args.metadata)):
        parser.error("--review-root, --evidence-root and --metadata must be provided together")
    if args.field_interpretation_mode is not None and not preparation:
        parser.error("--field-interpretation-mode requires saved-input preparation with bound original")
    interpretation = ({"field_interpretation_mode": args.field_interpretation_mode}
                      if args.field_interpretation_mode is not None else {})
    if preparation and any(value is not None for value in (
            args.read_description_pdf, args.description_output, args.description_executable,
            args.description_pages)):
        parser.error("saved observation preparation only reads saved inputs; description OCR options are not allowed")
    if preparation:
        from unison_snapshot.oge_saved_observations import MAX_JSON_BYTES, _json as read_saved_json
        paths = [*args.observations, *(args.secondary_observations or [])]
        if sum(path.stat().st_size for path in paths) > MAX_JSON_BYTES:
            raise ValueError("saved observation inputs exceed JSON byte budget")
    read_input = read_saved_json if preparation else lambda path: json.loads(path.read_text(encoding="utf-8"))
    inputs = [read_input(path)
              for path in args.observations]
    secondary = ([read_input(path)
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
    saved, package = None, None
    source_kwargs = {}
    if preparation:
        from unison_snapshot.oge_saved_observations import prepare_saved_oge_observations
        saved = prepare_saved_oge_observations(
            evidence_root=args.evidence_root, metadata_path=args.metadata,
            observations=inputs, secondary_observations=secondary,
            document_page_count=args.document_page_count, **interpretation)
        package = _preparation_outputs(args, saved)
        args.document_page_count = next(iter(saved.values()))["document_page_count"]
        if any(item.get("read_mode") == "source_quad_cells/v1" for item in secondary or []):
            from unison_snapshot.oge_saved_observations import _source
            _, source, _ = _source(args.evidence_root, args.metadata)
            source_kwargs = {"source_pdf_path": source}
    if args.document_page_count is None:
        result = observe_field_shapes(inputs, secondary_observations=secondary, **interpretation, **source_kwargs)
    else:
        content = observe_content_rows(inputs, document_page_count=args.document_page_count,
                                       secondary_observations=secondary, **interpretation, **source_kwargs)
        result = content.pop("field_observations")
        result.update(content)
    ledger_content = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=not preparation)
    if preparation:
        package_content = json.dumps(saved, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False)
        if max(len(value.encode("utf-8")) for value in (ledger_content, package_content)) > MAX_JSON_BYTES:
            raise ValueError("prepared observation output exceeds JSON byte budget")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(ledger_content, encoding="utf-8")
    if preparation:
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text(package_content, encoding="utf-8")
    summary = {"source_sha256": result["source_sha256"], "page_count": result["page_count"],
               "summary": result["summary"]}
    if secondary is not None:
        summary["secondary_field_summary"] = result["secondary_field_summary"]
    if preparation:
        summary["saved_observations_path"] = package.relative_to(args.review_root.resolve()).as_posix()
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
