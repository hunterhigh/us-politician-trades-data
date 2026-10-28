"""Observe OCR words inside confirmed slots of selected archived 278-T pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_raster_slots import (observe_raster_slots,
    census_with_2024_fallback, DUAL_MODE, MAX_PAGES_PER_RUN)
from unison_snapshot.oge_ocr_line_order import MODE as OCR_LINE_MODE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    parser.add_argument("--tesseract")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--geometry-mode", choices=[DUAL_MODE],
                        help="Explicit dual-axis source geometry; legacy mode remains the default")
    parser.add_argument("--geometry-only", action="store_true",
                        help="Export witnessed grid candidates without invoking OCR or interpreting rows")
    parser.add_argument("--description-line-mode", choices=[OCR_LINE_MODE],
                        help="Retain source OCR line groups for checked asset description ordering")
    args = parser.parse_args()
    if (not 1 <= len(args.pages) <= MAX_PAGES_PER_RUN or len(set(args.pages)) != len(args.pages) or
            any(n < 1 for n in args.pages)):
        parser.error("requires 1-8 distinct positive page numbers")
    if args.geometry_only and args.description_line_mode:
        parser.error("description line mode requires word observations")
    if args.geometry_only:
        result, _ = census_with_2024_fallback(args.pdf, expected_sha256=args.expected_sha256,
            page_numbers=args.pages, geometry_mode=args.geometry_mode)
        result.update(geometry_only=True, production_mutation_authorized=False)
    else:
        result = observe_raster_slots(
            args.pdf, expected_sha256=args.expected_sha256,
            page_numbers=args.pages, executable=args.tesseract,
            resolution=args.dpi, geometry_mode=args.geometry_mode,
            **({"description_line_mode":args.description_line_mode} if args.description_line_mode else {}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "source_sha256", "selected_page_numbers",
        "confirmed_physical_slot_count", "slots_with_ocr_words",
        "pages_with_column_geometry", "slots_with_column_observation") if key in result},
        sort_keys=True))


if __name__ == "__main__":
    main()
