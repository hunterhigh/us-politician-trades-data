"""Publish record changes and stop source losses greater than five percent."""
from collections import Counter
import json
from pathlib import Path

from ..codec import encode

ENTITIES = ('people', 'transactions', 'reported_holdings')


def build_publication_delta(previous_board, prepared, *, main_commit=None):
    changes = {}
    for entity in ENTITIES:
        old = {row['id']: row for row in previous_board[entity]}
        current = {row['id']: row for row in prepared[entity]}
        modified = []
        for identifier in sorted(old.keys() & current.keys()):
            before, after = old[identifier], current[identifier]
            fields = {field: {'before': before.get(field), 'after': after.get(field),
                             'before_present': field in before, 'after_present': field in after}
                      for field in sorted(before.keys() | after.keys())
                      if field not in before or field not in after or before[field] != after[field]}
            if fields:
                modified.append({'id': identifier, 'fields': fields})
        changes[entity] = {'added': sorted(current.keys() - old.keys()),
                           'modified': modified, 'deleted': sorted(old.keys() - current.keys())}
    counts = []
    old_counts = Counter(row['source_id'] for kind in ENTITIES[1:] for row in previous_board[kind])
    new_counts = Counter(row['source_id'] for kind in ENTITIES[1:] for row in prepared[kind])
    for source in sorted(old_counts.keys() | new_counts.keys()):
        before, after = old_counts[source], new_counts[source]
        counts.append({'source_id': source, 'before': before, 'after': after})
        if (before - after) * 100 > before * 5:
            raise ValueError(f'{source} published records decreased by more than 5%: {before} -> {after}')
    return {'schema_version': 'publication-changes/v1', 'previous_main_commit': main_commit,
            'source_counts': counts, 'changes': changes}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous-board', 'prepared', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--main-commit')
    args = parser.parse_args()
    report = build_publication_delta(json.loads(args.previous_board.read_bytes()),
        json.loads(args.prepared.read_bytes()), main_commit=args.main_commit)
    args.output.write_bytes(encode(report))
    print(json.dumps({'source_counts': report['source_counts'],
        'changes': {kind: {action: len(rows) for action, rows in changes.items()}
                    for kind, changes in report['changes'].items()}}))


if __name__ == '__main__':
    main()
