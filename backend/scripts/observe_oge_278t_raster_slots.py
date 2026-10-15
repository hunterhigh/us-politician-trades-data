"""Observe OCR words inside confirmed slots of selected archived 278-T pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unison_snapshot.oge_278t_raster_slots import observe_raster_slots


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    parser.add_argument("--tesseract")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = observe_raster_slots(
        args.pdf, expected_sha256=args.expected_sha256,
        page_numbers=args.pages, executable=args.tesseract,
        resolution=args.dpi)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "source_sha256", "selected_page_numbers",
        "confirmed_physical_slot_count", "slots_with_ocr_words")},
        sort_keys=True))


if __name__ == "__main__":
    main()
