"""Prepare selected existing Senate replay bundles for the saved-input CLI."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from unison_snapshot.senate_paper_runtime import prepare_senate_paper_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-root', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--document-id', action='append', required=True)
    parser.add_argument('--grid-output', type=Path, required=True)
    parser.add_argument('--word-output', type=Path, required=True)
    parser.add_argument('--receipt-output', type=Path)
    args = parser.parse_args()
    outputs = [args.grid_output, args.word_output, *([args.receipt_output] if args.receipt_output else [])]
    resolved = [path.resolve() for path in outputs]
    roots = [args.review_root.resolve(), args.evidence_root.resolve()]
    if any(path.is_relative_to(root) for path in resolved for root in roots):
        parser.error('Outputs must stay outside controlled review and evidence directories')
    for index, path in enumerate(resolved):
        for other in resolved[index+1:]:
            if path == other or path.exists() and other.exists() and path.samefile(other):
                parser.error('Preparation outputs must be distinct files')
        if path.is_dir():
            parser.error('Preparation outputs must be files')
    value = prepare_senate_paper_inputs(args.review_root, args.evidence_root,
                                       document_ids=args.document_id)
    # Also protect hard links to any dependency outside its normal root path.
    dependencies = [(args.review_root if item['root'] == 'review' else args.evidence_root) / item['path']
                    for item in value['receipt']['files']]
    if any(path.exists() and source.exists() and path.samefile(source)
           for path in resolved for source in dependencies):
        parser.error('Preparation outputs must not overwrite source dependencies')
    payloads = [value['grid_observations'], value['cached_word_pages'],
                *([value['receipt']] if args.receipt_output else [])]
    staged = []
    try:
        # Prepare every payload before replacing either paired output.
        for path, payload in zip(resolved, payloads, strict=True):
            raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name, suffix='.tmp', delete=False) as handle:
                staged.append((Path(handle.name), path))
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    print(json.dumps(value['receipt'], sort_keys=True))


if __name__ == '__main__':
    main()
