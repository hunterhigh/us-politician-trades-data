"""Verify a paper default-owner correction against three immutable Git inputs.

The saved profile selects historical replay inputs, never authorizes a change.
Only the existing source-bound paper consumer may reproduce the old and final
qualifications and the unique physical row mapping. No OCR is invoked here.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .house_revision_supersession import _git_paths, _git_read
from .migration_inventory import InventoryError, _differences, _index, git_object
from .whitehouse_owner_correction import _Sources, _fixed_board
from ..house_paper_owner_correction import PROFILE_KEY
from ..house_paper_owner_inputs import (
    MAX_JSON_BYTES, load_house_paper_owner_inputs,
    replay_house_paper_owner_correction, represented_paper_owner_fact,
)
from ..whitehouse_native_correction import NativeCorrectionError


MAX_DOCUMENTS = 32
MAX_VERSIONS = 32
MAX_TOTAL_BYTES = 256 * 1024 * 1024
PTR = re.compile(r'https://disclosures-clerk\.house\.gov/public_disc/ptr-pdfs/([0-9]{4})/([0-9]{1,12})\.pdf')
SHA_JSON = re.compile(r'([0-9a-f]{64})\.json')
PERSON_FIELDS = ('id', 'display_name', 'role', 'office_type', 'chamber', 'party',
                 'state', 'disclosure_authority')


def _object(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise InventoryError('House paper owner JSON has duplicate keys')
            value[key] = item
        return value

    def constant(_):
        raise InventoryError('House paper owner JSON has nonfinite numbers')

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise InventoryError('House paper owner qualification must be an object')
    return value


def _paths(sources, ref, directory, list_paths, *, optional=False):
    try:
        names = list_paths(sources.repo, ref, directory)
    except InventoryError as error:
        # The only optional namespace is the existing field reread directory.
        # Every mandatory object and the complete final qualification must
        # still reproduce, so absence supplies no field or owner authority.
        if optional and list_paths is _git_paths and str(error) == 'House revision fixed Git directory unavailable':
            return []
        raise
    if (not isinstance(names, list) or len(names) > MAX_VERSIONS or
            any(not isinstance(name, str) or SHA_JSON.fullmatch(name) is None for name in names) or
            len(set(names)) != len(names)):
        raise InventoryError('House paper owner canonical version inventory is invalid or exceeds budget')
    return names


def _selected_qualification(sources, ref, year, document, keys, list_paths):
    directory = f'house_clerk/qualifications/{year}/{document}'
    matches = {key: [] for key in keys}
    for name in _paths(sources, ref, directory, list_paths):
        raw = sources.read(ref, f'{directory}/{name}', MAX_JSON_BYTES)
        qualification = _object(raw)
        rows = qualification.get('transactions')
        if not isinstance(rows, list):
            raise InventoryError('House paper owner qualification transaction inventory is invalid')
        row_ids = {row.get('id') for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)}
        if PROFILE_KEY in qualification:
            for key in keys & row_ids:
                matches[key].append((name[:-5], raw, qualification))
    if any(len(values) != 1 for values in matches.values()):
        raise InventoryError('House paper owner has no unique selected correction qualification')
    return {key: values[0] for key, values in matches.items()}


def _stage_document(sources, evidence_ref, review_ref, evidence, review,
                    year, document, sha, qualification_raw, list_paths):
    from ..house import MAX_PDF_BYTES

    relative = f'{year}/{document}/{sha}'
    for suffix, limit in (('.json', MAX_JSON_BYTES), ('.pdf', MAX_PDF_BYTES)):
        path = f'house_clerk/documents/{relative}{suffix}'
        target = evidence / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sources.read(evidence_ref, path, limit))
    for folder in ('extractions', 'qualifications', 'identities', 'paper_observations',
                   'paper_amount_headers', 'paper_key_field_rereads'):
        path = f'house_clerk/{folder}/{relative}.json'
        if folder == 'paper_key_field_rereads' and f'{sha}.json' not in _paths(
                sources, review_ref, f'house_clerk/{folder}/{year}/{document}', list_paths, optional=True):
            continue
        raw = qualification_raw if folder == 'qualifications' else sources.read(review_ref, path, MAX_JSON_BYTES)
        target = review / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def verified_house_paper_owner_corrections(*, old, current, repo, evidence_commit,
        review_commit, main_commit=None, previous_people=None, read_object=git_object,
        list_paths=None):
    possible = {key for key in old.keys() & current.keys()
        if old[key].get('source_id') == current[key].get('source_id') == 'house_clerk'
        and old[key].get('owner') == 'Self' and current[key].get('owner') == 'Undisclosed'
        and _differences(old[key], current[key]) == ['owner']}
    if not possible:
        return []
    if repo is None or not all(re.fullmatch(r'[0-9a-f]{40}', str(ref))
            for ref in (main_commit, evidence_commit, review_commit)):
        raise InventoryError('House paper owner correction needs fixed main, review and evidence commits')
    try:
        sources = _Sources(repo, read_object)
        sources.max_total = MAX_TOTAL_BYTES
        if read_object is git_object:
            sources.read_object = _git_read
        list_paths = list_paths or _git_paths
        pinned, pinned_people = _fixed_board(sources, repo, main_commit)
        groups = {}
        for key in sorted(possible):
            prior, replacement = old[key], current[key]
            if pinned.get(key) != prior:
                raise InventoryError('House paper owner predecessor differs from complete fixed main fact')
            if replacement != {**prior, 'owner': 'Undisclosed'}:
                raise InventoryError('House paper owner correction changes another field or key representation')
            match = PTR.fullmatch(prior.get('source_url', ''))
            if match is None or prior.get('filing_id') != match[2] or prior.get('verification_status') != 'official_matched':
                raise InventoryError('House paper owner correction needs the same canonical official PTR source')
            person = pinned_people.get(prior.get('person_id'))
            if (person is None or previous_people is not None and
                    [row for row in previous_people if row.get('id') == prior.get('person_id')] != [person]):
                raise InventoryError('House paper owner previous identity differs from complete fixed main person')
            groups.setdefault((match[1], match[2]), set()).add(key)
        if len(groups) > MAX_DOCUMENTS:
            raise InventoryError('House paper owner document count exceeds budget')
        result = []
        with TemporaryDirectory(prefix='house-paper-owner-proof-') as directory:
            evidence, review = Path(directory) / 'e', Path(directory) / 'r'
            evidence.mkdir(); review.mkdir()
            for (year, document), keys in sorted(groups.items()):
                selections = _selected_qualification(sources, review_commit, year, document, keys, list_paths)
                replayed = {}
                for key in sorted(keys):
                    sha, raw, qualification = selections[key]
                    if sha not in replayed:
                        _stage_document(sources, evidence_commit, review_commit, evidence, review,
                            year, document, sha, raw, list_paths)
                        inputs = load_house_paper_owner_inputs(evidence, review,
                            year=year, document_id=document, source_sha256=sha)
                        proof = replay_house_paper_owner_correction(evidence, review,
                            qualification=qualification, extraction=inputs['extraction'])
                        if proof is None:
                            raise InventoryError('House paper owner saved profile has no original replay')
                        replayed[sha] = (inputs, proof)
                    inputs, proof = replayed[sha]
                    before = _index(proof['legacy_qualification']['transactions'], 'House replayed legacy facts')
                    after = _index(proof['final_qualification']['transactions'], 'House replayed corrected facts')
                    mappings = {row['legacy_id']: row for row in proof['id_mappings']}
                    if len(mappings) != len(proof['id_mappings']) or key not in mappings:
                        raise InventoryError('House paper owner source physical mapping is not unique')
                    prior, replacement = old[key], current[key]
                    if (key not in before or key not in after or
                            represented_paper_owner_fact(before[key], prior) != prior or
                            represented_paper_owner_fact(after[key], replacement) != replacement):
                        raise InventoryError('House paper owner complete old and new facts differ from source projections')
                    from ..house_candidate import _person
                    source_person = _person(inputs['identity'])
                    person = pinned_people[prior['person_id']]
                    if any(person.get(field) != source_person.get(field) for field in PERSON_FIELDS):
                        raise InventoryError('House paper owner source identity differs from fixed main person')
                    result.append({'entity': 'transactions', 'kind': 'corrected', 'old_id': key,
                        'new_id': key, 'reason': 'source_identity_correction',
                        'evidence_url': prior['source_url'], 'source_sha256': sha,
                        'changed_fields': ['owner']})
        return result
    except InventoryError:
        raise
    except (NativeCorrectionError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise InventoryError('House paper owner original source verification failed: ' + str(error)) from error
