"""Pin both sides of the narrow native Endnote classification migration."""
from __future__ import annotations

import hashlib
import re

from .migration_inventory import InventoryError, _differences, git_object
from .whitehouse_owner_correction import _Sources, _fixed_board
from ..whitehouse_native_correction import (NativeCorrectionError,
    NativeClassificationSources, _safe, reconcile_native_classification)
from ..oge import OgeCatalogError


def verified_classification_corrections(*, old, current, repo, evidence_commit, review_commit,
                                        main_commit=None, previous_people=None, read_object=git_object):
    possible = [key for key in old.keys() & current.keys()
        if old[key].get('source_id') == current[key].get('source_id') == 'oge' and
        old[key].get('instrument_type') == 'Bond' and current[key].get('instrument_type') == 'Stock' and
        current[key].get('owner') == 'Undisclosed' and
        ((old[key].get('owner') == 'Undisclosed' and _differences(old[key], current[key]) == ['instrument_type']) or
         (old[key].get('owner') == 'Self' and _differences(old[key], current[key]) == ['instrument_type', 'owner']))]
    if not possible:
        return []
    if repo is None or not all(re.fullmatch(r'[0-9a-f]{40}', str(ref))
                              for ref in (main_commit, evidence_commit, review_commit)):
        raise InventoryError('White House classification needs fixed main, review and evidence commits')
    sources, result = _Sources(repo, read_object), []
    context = NativeClassificationSources(sources, evidence_ref=evidence_commit, review_ref=review_commit)
    try:
        pinned, people = _fixed_board(sources, repo, main_commit)
        for key in sorted(possible):
            prior, replacement = old[key], current[key]
            if pinned.get(key) != prior:
                raise InventoryError('White House classification predecessor differs from fixed main board')
            person = people.get(prior.get('person_id'))
            supplied = [item for item in previous_people or [] if item.get('id') == prior.get('person_id')]
            if person is None or previous_people is not None and supplied != [person]:
                raise InventoryError('White House classification previous identity differs from fixed main board')
            url = replacement.get('source_url')
            _safe(url, r'https://www\.whitehouse\.gov/wp-content/uploads/20\d{2}/\d{2}/[^?#]+\.pdf')
            document = 'wh-url:' + hashlib.sha256(url.encode()).hexdigest()[:24]
            if replacement.get('filing_id') != document:
                raise InventoryError('White House classification document differs from source URL')
            report = context._load(document, url)[0]
            rows = [row for row in report['transactions'] if row.get('extraction_id') == key]
            if len(rows) != 1:
                raise InventoryError('White House classification source row is not unique')
            verified = reconcile_native_classification(report, rows[0], prior,
                person_id=person['id'], source_people=list(people.values()), sources=context)
            if verified is None or verified['fact'] != replacement:
                raise InventoryError('White House classification does not reproduce complete old and new formal objects')
            result.append({'entity': 'transactions', 'kind': 'corrected', 'old_id': key, 'new_id': key,
                'reason': 'source_classification_correction', 'evidence_url': url,
                'source_sha256': report['source_sha256'], 'changed_fields': _differences(prior, replacement)})
    except (NativeCorrectionError, OgeCatalogError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InventoryError(f'White House classification source verification failed: {exc}') from exc
    return result
