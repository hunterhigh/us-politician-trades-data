"""Verify only Self -> Undisclosed; classification corrections stay separate."""
from __future__ import annotations

import hashlib
import re

from .source_objects import InventoryError, _differences, git_object
from ..whitehouse_native_correction import (
    MAX_DOCUMENTS, MAX_PAGES, MAX_JSON_BYTES, MAX_PDF_BYTES, MAX_TOTAL_BYTES,
    MAX_BOARD_BYTES, _safe, _historical_instrument, _historical_projection,
    NativeCorrectionError, _Sources as _SharedSources, _replay as _source_replay,
    prove_native_row)


class _Sources(_SharedSources):
    def __init__(self, repo, read_object):
        super().__init__(repo, read_object, preflight_git=read_object is git_object,
                         max_total=MAX_TOTAL_BYTES, max_pages=MAX_PAGES)


def _replay(*args):
    return _source_replay(*args)


def _fixed_board(sources, repo, main_commit):
    from .source_replacements import _board
    from .source_objects import _index
    manifest = sources.obj(main_commit, 'manifest.json')
    if manifest.get('is_demo') is not False:
        raise InventoryError('White House correction predecessor is not an official snapshot')
    board = _board(repo, main_commit, lambda repo, ref, path: sources.read(ref, path,
        MAX_BOARD_BYTES if path.startswith('board/') else MAX_JSON_BYTES))
    return (_index(board.get('transactions'), 'fixed main transactions'),
            _index(board.get('people'), 'fixed main people'))


def verified_owner_corrections(*, old, current, repo, evidence_commit, review_commit,
                               main_commit=None, previous_people=None, read_object=git_object):
    possible = [key for key in old.keys() & current.keys()
        if old[key].get('source_id') == current[key].get('source_id') == 'oge' and
        old[key].get('owner') == 'Self' and current[key].get('owner') == 'Undisclosed' and
        _differences(old[key], current[key]) == ['owner']]
    if not possible:
        return []
    if repo is None or not all(re.fullmatch(r'[0-9a-f]{40}', str(ref))
                              for ref in (main_commit, evidence_commit, review_commit)):
        raise InventoryError('White House owner correction needs fixed main, review and evidence commits')
    from ..oge import OgeCatalogError
    from ..whitehouse_disclosures import WhiteHouseDisclosureError
    sources, cache, result = _Sources(repo, read_object), {}, []
    try:
        pinned_old, pinned_people = _fixed_board(sources, repo, main_commit)
        for key in sorted(possible):
            prior, replacement = old[key], current[key]
            if pinned_old.get(key) != prior:
                raise InventoryError('White House correction predecessor differs from fixed main board')
            person = pinned_people.get(prior.get('person_id'))
            supplied = [item for item in previous_people or [] if item.get('id') == prior.get('person_id')]
            if person is None or previous_people is not None and supplied != [person]:
                raise InventoryError('White House correction previous identity differs from fixed main board')
            source_people = list(pinned_people.values())
            url = replacement.get('source_url')
            _safe(url, r'https://www\.whitehouse\.gov/wp-content/uploads/20\d{2}/\d{2}/[^?#]+\.pdf')
            document = 'wh-url:' + hashlib.sha256(url.encode()).hexdigest()[:24]
            if replacement.get('filing_id') != document:
                raise InventoryError('White House owner correction document differs from source URL')
            if document not in cache:
                if len(cache) >= MAX_DOCUMENTS:
                    raise InventoryError('White House owner correction document count exceeds budget')
                cache[document] = _replay(sources, evidence_commit, review_commit, document, url)
            report, raw_rows, coverage, directory = cache[document]
            reconstructed, normalized, _ = prove_native_row(
                report, raw_rows, coverage, directory, key, source_people, prior)
            expected = {**reconstructed, 'owner': 'Undisclosed'}
            if prior != reconstructed or replacement != expected:
                raise InventoryError('White House owner correction does not reproduce complete old and new formal objects')
            result.append({'entity': 'transactions', 'kind': 'corrected', 'old_id': key, 'new_id': key,
                'reason': 'source_identity_correction', 'evidence_url': url,
                'source_sha256': report['source_sha256'], 'changed_fields': ['owner']})
    except (NativeCorrectionError, OgeCatalogError, WhiteHouseDisclosureError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InventoryError(f'White House owner correction source verification failed: {exc}') from exc
    return result
