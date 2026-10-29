"""Reconcile explicit content failure receipts into publication-blocking Issues."""
import argparse
import json
import os
from pathlib import Path
from unison_snapshot.content_integrity import report
from unison_snapshot.production_health import GitHub


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--successful', choices=['true', 'false'], default='false')
    args = parser.parse_args()
    print(json.dumps(report(GitHub(args.repository, os.environ.get('GH_TOKEN')), args.directory, successful=args.successful == 'true')))


if __name__ == '__main__': main()
