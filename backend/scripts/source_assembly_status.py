"""Expose one source-workflow assembly attempt without implying publication."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from unison_snapshot.builder import timestamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.status.read_text(encoding='utf-8'))
    if (value.get('schema_version') != 'disclosure-assembly-status/v1' or
            value.get('status') not in {'ready', 'deferred'}):
        raise ValueError('Invalid source assembly status')
    timestamp(value['attempted_at'])
    status = value['status']
    if status == 'deferred':
        if (value.get('reason') != 'source_cutoffs_too_stale' or
                not isinstance(value.get('source_cutoffs'), dict) or
                not value['source_cutoffs'] or
                set(value['source_cutoffs']) - {'house_clerk', 'senate_efd', 'oge'}):
            raise ValueError('Invalid deferred source cutoffs')
        for cutoff in value['source_cutoffs'].values():
            timestamp(cutoff)
        message = ('Unified assembly deferred: source cutoffs exceed the two-day limit. '
                   'The source candidate is saved; the prior unified candidate and '
                   'formal snapshot are retained. This is not a successful data update.')
        details = '\n'.join(f'- {source}: `{cutoff}`'
                            for source, cutoff in sorted(value['source_cutoffs'].items()))
        print('::warning::' + message, file=sys.stderr)
    else:
        timestamp(value['data_cutoff_at'])
        if not re.fullmatch('[0-9a-f]{64}', value.get('candidate_sha256', '')):
            raise ValueError('Ready assembly has no valid candidate hash')
        message = 'Unified candidate assembled and validated; formal publication is a separate gated workflow.'
        details = f"- Candidate cutoff: `{value['data_cutoff_at']}`"
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(summary).open('a', encoding='utf-8') as handle:
            handle.write(f'\n{message}\n\n{details}\n')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with Path(output).open('a', encoding='utf-8') as handle:
            handle.write(f'assembly_status={status}\n')
    print(status)


if __name__ == '__main__':
    main()
