"""Prepare one archived ordinary native PTR pair without collection or OCR."""
import argparse
import json
from pathlib import Path

from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_ptr_runtime import prepare_oge_ptr_generic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--evidence-root', required=True, type=Path)
    parser.add_argument('--review-root', required=True, type=Path)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--catalog-history-dir', type=Path)
    args = parser.parse_args()
    try:
        receipt = prepare_oge_ptr_generic(**vars(args))
    except (OgeCatalogError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
