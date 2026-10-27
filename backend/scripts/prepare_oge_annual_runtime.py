"""Prepare bounded saved annual inputs offline from controlled review/evidence."""
import argparse
import json
from pathlib import Path

from unison_snapshot.oge_annual_runtime import prepare_controlled_annual_input
from unison_snapshot.oge_annual import PARSER_VERSION, SHADOW_PARSER_VERSION, QUALIFIED_PARSER_VERSION
from overlay_whitehouse_annual_candidate import _write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-root', required=True, type=Path)
    parser.add_argument('--evidence-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--code-commit', required=True)
    parser.add_argument('--started-at', required=True)
    parser.add_argument('--document-id', action='append')
    parser.add_argument('--parser-version', choices=sorted({PARSER_VERSION, SHADOW_PARSER_VERSION,
                                                          QUALIFIED_PARSER_VERSION}),
                        help='Select an existing saved native extraction version; never upgrade or reparse')
    parser.add_argument('--include-saved-scans', action='store_true',
                        help='Prepare saved scan observations; OCR requires the separate --replay-cover-ocr option')
    parser.add_argument('--blue-date-document-id', action='append',
                        help='Explicit saved scan document using the existing blue-date consumer; requires a model directory')
    parser.add_argument('--blue-date-model-directory',
                        help='Existing canonical model directory relative to controlled evidence; never download weights')
    parser.add_argument('--verify-transaction-blank-slots', action='store_true',
                        help='Request existing source-pixel blank-slot verification for saved scans')
    parser.add_argument('--replay-cover-ocr', action='store_true',
                        help='Replay the existing cover form/type/report-period reader from source; does not verify signature dates')
    args = parser.parse_args()
    output = args.output.resolve()
    if any(output.is_relative_to(root.resolve()) for root in (args.review_root, args.evidence_root)):
        parser.error('--output must be outside controlled review and evidence trees')
    value = prepare_controlled_annual_input(args.review_root, args.evidence_root,
        code_commit=args.code_commit, started_at=args.started_at, document_ids=args.document_id,
        include_saved_scans=args.include_saved_scans, parser_version=args.parser_version,
        blue_date_document_ids=args.blue_date_document_id,
        blue_date_model_directory=args.blue_date_model_directory,
        verify_transaction_blank_slots=args.verify_transaction_blank_slots,
        replay_cover_ocr=args.replay_cover_ocr)
    _write(args.output, value)
    print(json.dumps(value['controlled_annual_runtime'], sort_keys=True))


if __name__ == '__main__':
    main()
