"""Check a selected offline Senate replay without changing controlled artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--review-root', type=Path, required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    selected = args.documents.split(',')
    if (not 1 <= len(selected) <= 12 or len(set(selected)) != len(selected) or
            any(not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', doc) for doc in selected)):
        parser.error('documents must contain 1 to 12 unique canonical Senate document IDs')
    batch = json.loads(args.batch.read_bytes())
    if (batch.get('scope') != 'selected_fixed_reports_not_full_catalog' or
            batch['failure_count'] != 0 or batch['report_count'] != len(selected) or
            {row['document_id'] for row in batch['extractions']} != set(selected)):
        raise ValueError('Saved replay must conserve every selected report without failures')
    roots = {'review': args.review_root.resolve(), 'evidence': args.evidence_root.resolve()}
    for row in batch['extractions']:
        path = (roots['review'] / 'senate_efd/extractions' / row['document_id'] /
                row['source_sha256'] / (row['parser_version'] + '.json')).resolve()
        if not path.is_relative_to(roots['review']) or json.loads(path.read_bytes()) != row:
            raise ValueError('Saved replay differs from its existing complete extraction')
    for item in json.loads(args.receipt.read_bytes())['files']:
        root = roots.get(item['root'])
        if root is None: raise ValueError('Saved replay receipt has an invalid controlled root')
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Saved replay changed an archived input or global source status')
    return 0
