"""Prepare bounded saved annual inputs offline from controlled review/evidence."""
import argparse
import json
from pathlib import Path

from unison_snapshot.oge_annual_runtime import prepare_controlled_annual_input
from overlay_whitehouse_annual_candidate import _write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-root', required=True, type=Path)
    parser.add_argument('--evidence-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--code-commit', required=True)
    parser.add_argument('--started-at', required=True)
    parser.add_argument('--document-id', action='append')
    parser.add_argument('--include-saved-scans', action='store_true',
                        help='Also prepare complete source-bound saved scan observations; never run OCR/models')
    args = parser.parse_args()
    output = args.output.resolve()
    if any(output.is_relative_to(root.resolve()) for root in (args.review_root, args.evidence_root)):
        parser.error('--output must be outside controlled review and evidence trees')
    value = prepare_controlled_annual_input(args.review_root, args.evidence_root,
        code_commit=args.code_commit, started_at=args.started_at, document_ids=args.document_id,
        include_saved_scans=args.include_saved_scans)
    _write(args.output, value)
    print(json.dumps(value['controlled_annual_runtime'], sort_keys=True))


if __name__ == '__main__':
    main()
