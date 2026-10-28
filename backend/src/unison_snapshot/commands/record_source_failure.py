"""Publish a bounded official collection failure without promoting partial candidate work."""
import argparse
import json
from pathlib import Path
from unison_snapshot.source_failures import persist


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(persist(args.workspace,args.directory)))


if __name__=='__main__':main()
