"""Summarize existing qualification observations without OCR or fact changes."""
import argparse
import json
from pathlib import Path
from unison_snapshot.source_quality import observe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['house_clerk', 'senate_efd', 'oge'], required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--review-root', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    result = observe(args.source, args.evidence_root, args.review_root, json.loads(args.input.read_bytes()))
    print(json.dumps({'source': args.source, 'scope_count': len(result['scopes'])}))


if __name__ == '__main__': main()
