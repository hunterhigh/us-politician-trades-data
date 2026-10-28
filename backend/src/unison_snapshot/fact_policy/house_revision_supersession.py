"""Reproduce House amendment supersessions from three immutable Git inputs.

A supersession may change disclosed financial fields. It is therefore a
different transition from a semantic-preserving rekey or a same-ID correction.
No saved relationship mapping or explicit transition can substitute for source
scope and the normal deterministic amendment chain.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from tempfile import TemporaryDirectory

from .source_replacements import _board
from .source_objects import InventoryError, _differences, _index, git_object
from .whitehouse_owner_correction import _Sources
from ..house_source_revisions import resolve_source_bound_house_revisions
from ..house_revision_source_context import load_house_revision_source_context


MAX_OBJECTS = 100_000
MAX_BYTES = 256 * 1024 * 1024
MAX_OBJECT_BYTES = 64 * 1024 * 1024
PTR = re.compile(r'https://disclosures-clerk\.house\.gov/public_disc/ptr-pdfs/[0-9]{4}/[0-9]{1,20}\.pdf')


def _git_paths(repo, ref, directory):
    """Bound the allocation itself and forbid partial-clone lazy fetching."""
    process = subprocess.Popen(['git', '-C', str(repo), 'ls-tree', '-r', '--name-only',
        f'{ref}:{directory}'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
    try:
        chunks, size = [], 0
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 8 * 1024 * 1024:
                raise InventoryError('House revision Git path bytes exceed budget')
            chunks.append(chunk)
        if process.wait() != 0:
            raise InventoryError('House revision fixed Git directory unavailable')
        return b''.join(chunks).decode('utf-8').splitlines()
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdout.close()


def _git_read(repo, ref, path):
    # _Sources preflights the blob length before this bounded blob is read.
    process = subprocess.run(['git', '-C', str(repo), 'cat-file', 'blob', f'{ref}:{path}'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
    if process.returncode:
        raise InventoryError('House revision fixed Git object unavailable')
    return process.stdout


def _ptr(row):
    return (row.get('source_id') == 'house_clerk' and isinstance(row.get('source_url'), str)
            and PTR.fullmatch(row['source_url']) is not None)


def _snapshot(sources, ref, target, prefixes, list_paths, *, selected_keys):
    count, size = 0, 0
    names = list_paths(sources.repo, ref, 'house_clerk')
    if len(names) > MAX_OBJECTS:
        raise InventoryError('House revision Git path inventory exceeds budget')
    seen = set()
    for name in names:
        relative = 'house_clerk/' + name
        if not any(relative.startswith(prefix) for prefix in prefixes):
            continue
        path = PurePosixPath(relative)
        if (path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts)
                or '\\' in relative or not re.fullmatch(r'[a-zA-Z0-9_./-]+', relative)):
            raise InventoryError('House revision Git source path is invalid')
        if relative.casefold() in seen:
            raise InventoryError('House revision Git paths alias on the source filesystem')
        seen.add(relative.casefold())
        count += 1
        if count > MAX_OBJECTS:
            raise InventoryError('House revision Git object inventory exceeds budget')
        original_index = relative.startswith(('house_clerk/index/', 'house_clerk/members/'))
        parts = path.parts
        key = '/'.join(parts[2:4]) if len(parts) >= 5 else None
        # Inventory-only files are explicitly empty, never source evidence.
        # Keep every path so unindexed/orphan sources remain unknown. Only
        # index/XML-proven person memberships decide which original bytes are
        # subsequently read; another person's large PDF/cache is irrelevant.
        raw = (sources.read(ref, relative, MAX_OBJECT_BYTES)
               if original_index or key in selected_keys else b'')
        size += len(raw)
        if size > MAX_BYTES:
            raise InventoryError('House revision Git source bytes exceed budget')
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)


def verified_house_revision_supersessions(*, old, current, repo, evidence_commit,
        review_commit, main_commit, previous_people=None, read_object=git_object,
        list_paths=None):
    missing = [old[key] for key in old.keys() - current.keys() if _ptr(old[key])]
    added = [current[key] for key in current.keys() - old.keys() if _ptr(current[key])]
    people = {r.get('person_id') for r in missing} & {r.get('person_id') for r in added}
    if not people:
        return []
    if repo is None or not all(re.fullmatch(r'[a-f0-9]{40}', str(ref))
            for ref in (main_commit, evidence_commit, review_commit)):
        raise InventoryError('House supersession needs fixed main, review and evidence commits')
    try:
        sources = _Sources(repo, read_object)
        sources.max_total = MAX_BYTES
        if read_object is git_object:
            sources.read_object = _git_read
        list_paths = list_paths or _git_paths
        if sources.obj(main_commit, 'manifest.json').get('is_demo') is not False:
            raise InventoryError('House supersession predecessor is not an official snapshot')
        board = _board(repo, main_commit, lambda _, ref, path: sources.read(ref, path, MAX_OBJECT_BYTES))
        pinned = _index(board['transactions'], 'House fixed main transactions')
        pinned_people = _index(board['people'], 'House fixed main people')
        prior = [r for r in old.values() if _ptr(r) and r.get('person_id') in people]
        for row in prior:
            if pinned.get(row['id']) != row:
                raise InventoryError('House supersession old complete fact differs from fixed main')
        if {r['id']: r for r in board['transactions'] if _ptr(r) and r.get('person_id') in people} != {r['id']: r for r in prior}:
            raise InventoryError('House supersession predecessor omits fixed main person facts')
        for person in people:
            if person not in pinned_people or previous_people is not None and [r for r in previous_people if r.get('id') == person] != [pinned_people[person]]:
                raise InventoryError('House supersession previous person differs from fixed main')
        with TemporaryDirectory(prefix='house-revision-proof-') as directory:
            evidence, review = Path(directory) / 'e', Path(directory) / 'r'
            evidence.mkdir(); review.mkdir()
            _snapshot(sources, evidence_commit, evidence,
                ('house_clerk/index/', 'house_clerk/members/', 'house_clerk/documents/'), list_paths,
                selected_keys=set())
            _snapshot(sources, review_commit, review,
                ('house_clerk/extractions/', 'house_clerk/qualifications/'), list_paths,
                selected_keys=set())
            # This preliminary context is necessarily unknown because source
            # bytes are absent. Its independent index/XML membership inventory
            # selects reads only; no status/qualification/absence is accepted.
            inventory = load_house_revision_source_context(evidence, review,
                requested_person_ids=people, prior_transactions=prior,
                current_transactions=list(current.values()))
            selected_keys = {item['key'] for item in inventory.provenance()['source_person_memberships']
                             if item['person_id'] in people}
            _snapshot(sources, evidence_commit, evidence,
                ('house_clerk/index/', 'house_clerk/members/', 'house_clerk/documents/'), list_paths,
                selected_keys=selected_keys)
            _snapshot(sources, review_commit, review,
                ('house_clerk/extractions/', 'house_clerk/qualifications/'), list_paths,
                selected_keys=selected_keys)
            inputs = []
            for path in sorted((review / 'house_clerk/qualifications').glob('*/*/*.json')):
                relative = path.relative_to(review / 'house_clerk/qualifications')
                if '/'.join(relative.parts[:2]) not in selected_keys:
                    continue
                qualification = json.loads(path.read_bytes())
                extraction = json.loads((review / 'house_clerk/extractions' / relative).read_bytes())
                inputs.append((qualification, extraction))
            baseline = [row for q, _ in inputs for row in q.get('transactions', [])]
            resolution = resolve_source_bound_house_revisions(inputs, baseline, prior_transactions=prior,
                evidence_root=evidence, review_root=review)
            expected = {r['id']: r for r in resolution['transactions'] if _ptr(r) and r.get('person_id') in people}
            supplied = {key: r for key, r in current.items() if _ptr(r) and r.get('person_id') in people}
            if expected != supplied:
                raise InventoryError('House supersession current complete projection differs from source replay')
            redirects = {r['prior_transaction_id']: r for r in resolution['replacements']}
            if len(redirects) != len(resolution['replacements']):
                raise InventoryError('House supersession has a repeated predecessor edge')
            result, targets = [], set()
            for row in sorted(missing, key=lambda r:r['id']):
                if row.get('person_id') not in people:
                    continue
                key, chain, seen = row['id'], [], set()
                while key in redirects:
                    if key in seen:
                        raise InventoryError('House supersession has a cyclic chain')
                    seen.add(key)
                    edge = redirects[key]
                    if edge['person_id'] != row['person_id']:
                        raise InventoryError('House supersession crosses person scope')
                    chain.append(edge)
                    key = edge['transaction_id']
                if not chain:
                    continue
                if key not in supplied or key in old or key in targets:
                    raise InventoryError('House supersession terminal target is not uniquely new')
                replacement = supplied[key]
                targets.add(key)
                result.append({'entity': 'transactions', 'kind': 'superseded',
                    'old_id': row['id'], 'new_id': key, 'reason': 'official_amendment',
                    'evidence_url': replacement['source_url'],
                    'source_sha256': chain[-1]['source_sha256'],
                    'changed_fields': _differences(row, replacement)})
            return result
    except InventoryError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise InventoryError('House supersession original source verification failed: ' + str(error)) from error
