"""Verify a source account-title suffix, optionally with its default-owner fix."""
from __future__ import annotations

import hashlib
import re

from .source_objects import InventoryError, _differences, git_object
from .whitehouse_owner_correction import _Sources, _fixed_board
from ..whitehouse_native_correction import (
    MAX_DOCUMENTS, NativeCorrectionError, _safe, prove_native_account_display,
    replay_native_account_display)
from ..oge import OgeCatalogError
from ..whitehouse_disclosures import WhiteHouseDisclosureError


def verified_account_display_corrections(*, old, current, repo, evidence_commit,
                                        review_commit, main_commit=None,
                                        previous_people=None, read_object=git_object):
    possible = [key for key in old.keys() & current.keys()
        if old[key].get('source_id') == current[key].get('source_id') == 'oge' and
        current[key].get('owner') == 'Undisclosed' and
        ((old[key].get('owner') == 'Undisclosed' and
          _differences(old[key], current[key]) == ['asset_name']) or
         (old[key].get('owner') == 'Self' and
          _differences(old[key], current[key]) == ['asset_name', 'owner']))]
    if not possible:
        return []
    if repo is None or not all(re.fullmatch(r'[0-9a-f]{40}', str(ref))
                              for ref in (main_commit, evidence_commit, review_commit)):
        raise InventoryError('White House account display needs fixed main, review and evidence commits')
    sources, cache, result = _Sources(repo, read_object), {}, []
    try:
        pinned, people = _fixed_board(sources, repo, main_commit)
        for key in sorted(possible):
            prior, replacement = old[key], current[key]
            if pinned.get(key) != prior:
                raise InventoryError('White House account display predecessor differs from fixed main board')
            person = people.get(prior.get('person_id'))
            supplied = [item for item in previous_people or [] if item.get('id') == prior.get('person_id')]
            if person is None or previous_people is not None and supplied != [person]:
                raise InventoryError('White House account display previous identity differs from fixed main board')
            url = replacement.get('source_url')
            _safe(url, r'https://www\.whitehouse\.gov/wp-content/uploads/20\d{2}/\d{2}/[^?#]+\.pdf')
            document = 'wh-url:' + hashlib.sha256(url.encode()).hexdigest()[:24]
            if replacement.get('filing_id') != document:
                raise InventoryError('White House account display document differs from source URL')
            if document not in cache:
                if len(cache) >= MAX_DOCUMENTS:
                    raise InventoryError('White House account display document count exceeds budget')
                cache[document] = replay_native_account_display(
                    sources, evidence_commit, review_commit, document, url)
            report, raw, coverage, directory = cache[document]
            historical, normalized, final = prove_native_account_display(
                report, raw, coverage, directory, key, list(people.values()), prior)
            if prior not in (historical, normalized) or replacement != final:
                raise InventoryError('White House account display does not reproduce complete old and new formal objects')
            result.append({'entity': 'transactions', 'kind': 'corrected', 'old_id': key, 'new_id': key,
                'reason': 'source_identity_correction', 'evidence_url': url,
                'source_sha256': report['source_sha256'], 'changed_fields': _differences(prior, replacement)})
    except (NativeCorrectionError, OgeCatalogError, WhiteHouseDisclosureError, OSError,
            ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InventoryError(f'White House account display source verification failed: {exc}') from exc
    return result
