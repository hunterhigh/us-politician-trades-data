"""Bounded source proof for direct OGE's historical Endnote classifier.

Physical bindings conserve existing identities; only authenticated complete
source projections authorize a classification change. Unchanged fresh facts
retain their separate ordinary financial qualification path.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

from .oge import OgeCatalogError
from .oge_reports import PARSER_VERSION, GENERIC_PARSER_VERSION
from .oge_native_owner_scope import (prepare_native_owner_scope, source_interpretation_binding,
    _represented, _instrument, _transaction_projection, _person_id, _identity_key)
from .whitehouse_native_correction import _historical_projection, _historical_instrument

POLICY_VERSION = 'oge-native-endnote-classification/v1'


def _endnote(value):
    return isinstance(value, str) and bool(re.search(r'\bEndnote\b', value, re.I))


def _mechanism(row):
    asset = row.get('asset_name')
    return (_endnote(asset) and _historical_instrument(asset, row.get('ticker')) == 'Bond'
            and _instrument(asset, row.get('ticker')) == 'Stock')


def _key(row):
    from .oge_transaction_adapter import _row_key
    return _row_key(row)


def _rows(report):
    return [] if report is None else report.get('transactions', []) + report.get('quarantined', [])


class NativeClassification:
    def __init__(self, extraction, formal, record, *, evidence_root=None,
                 source_extraction=None, legacy_extraction=None):
        self._effective = deepcopy(extraction)
        self._source = deepcopy(extraction if source_extraction is None else source_extraction)
        self._legacy = deepcopy(self._source if self._source.get('parser_version') == PARSER_VERSION
                                else legacy_extraction)
        self._native = deepcopy(self._source if self._source.get('parser_version') == GENERIC_PARSER_VERSION else None)
        self._scope = None
        self._mechanism = set()
        self._entries = {}
        self._blocked = set()
        self._person = _person_id(_identity_key(record))
        self.status, self.reason = 'not_applicable', 'no_native_endnote_classification_target'
        self.binding = {'policy_version': POLICY_VERSION,
                        'source': source_interpretation_binding(self._source), 'owner_scope': None}
        facts = list(formal)
        if len({fact.get('id') for fact in facts}) != len(facts):
            raise OgeCatalogError('OGE classification formal IDs are duplicated')
        hinted_ids = {row.get('extraction_id') for report in (self._source, self._legacy)
                      for row in _rows(report) if _mechanism(row)}
        if any(fact.get('source_id') == 'oge'
               and fact.get('verification_status') == 'official_matched'
               and fact.get('filing_id') == self._source.get('document_id')
               and fact.get('source_url') != self._source.get('source_url')
               and (fact.get('id') in hinted_ids or _endnote(fact.get('asset_name')))
               for fact in facts):
            raise OgeCatalogError('OGE classification formal source URL conflicts with selected source')
        self._formal = {fact['id']: deepcopy(fact) for fact in facts if
            fact.get('source_id') == 'oge' and fact.get('verification_status') == 'official_matched'
            and fact.get('filing_id') == self._source.get('document_id')
            and fact.get('source_url') == self._source.get('source_url')}
        if self._source.get('parser_version') not in {PARSER_VERSION, GENERIC_PARSER_VERSION}:
            return
        hinted = bool(self._formal.keys() & hinted_ids)
        hinted |= any(_endnote(fact.get('asset_name')) for fact in self._formal.values())
        if not self._formal or not hinted:
            return
        identity = ('document_id', 'source_id', 'source_sha256', 'source_url',
                    'catalog_filer_name', 'agency', 'position_title')
        if (extraction.get('parser_version') != self._source['parser_version'] or
                any(extraction.get(field) != self._source.get(field) for field in identity)):
            raise OgeCatalogError('OGE classification effective input differs from raw source identity')
        if self._legacy is not None and (self._legacy.get('parser_version') != PARSER_VERSION or
                any(self._legacy.get(field) != self._source.get(field) for field in identity)):
            raise OgeCatalogError('OGE classification legacy input differs from selected source')
        self.status, self.reason = 'unknown', 'original_source_unavailable'
        if evidence_root is not None:
            self._authenticate(Path(evidence_root), record)
        elif self._native is not None:
            self._scope = prepare_native_owner_scope(self._native, self._legacy, record)
            self.binding['owner_scope'] = deepcopy(self._scope.binding)
        self._bind_physical()

    def _authenticate(self, root, record):
        from .oge_reports import parse_archived_pdf
        document, sha = self._source.get('document_id'), self._source.get('source_sha256')
        if (not isinstance(document, str) or not re.fullmatch(r'[0-9a-f]{32}', document)
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            raise OgeCatalogError('OGE classification canonical source identity is invalid')
        path = root / f'oge/reports/{document}/{sha}.json'
        try:
            if self._source['parser_version'] == PARSER_VERSION:
                self._native = parse_archived_pdf(root, path, generic=True)
            elif self._legacy is None:
                self._legacy = parse_archived_pdf(root, path)
            if (self._legacy.get('parser_version') != PARSER_VERSION or
                    self._native.get('parser_version') != GENERIC_PARSER_VERSION):
                raise OgeCatalogError('OGE classification requires ordinary native v2/v7 source selection')
            self._scope = prepare_native_owner_scope(self._native, self._legacy, record,
                evidence_root=root, source_extraction=self._native)
        except OgeCatalogError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise OgeCatalogError(f'OGE classification source authentication failed: {error}') from error
        self.binding['owner_scope'] = deepcopy(self._scope.binding)
        self.status, self.reason = self._scope.status, self._scope.reason

    def _bind_physical(self):
        grouped = {}
        for label, report in (('legacy', self._legacy), ('native', self._native)):
            for row in _rows(report):
                grouped.setdefault(_key(row), {'legacy': [], 'native': []})[label].append(row)
        possible = {}
        for key, group in grouped.items():
            rows = group['legacy'] + group['native']
            if not any(_mechanism(row) for row in rows):
                continue
            for fact in self._formal.values():
                if any(row.get('extraction_id') == fact['id'] for row in rows):
                    self._mechanism.add(fact['id'])
                    if all(len(group[label]) <= 1 for label in group):
                        possible[fact['id']] = (key, group)
        known_ids = {row.get('extraction_id') for report in (self._legacy, self._native) for row in _rows(report)}
        # A formal hint requests replay; an explained non-Stock source row
        # does not become this migration merely because it says Endnote.
        self._mechanism.update(fact['id'] for fact in self._formal.values()
                               if fact['id'] not in known_ids and _endnote(fact.get('asset_name')))
        multiplicity = Counter(key for key, _ in possible.values())
        selected_label = 'legacy' if self._source['parser_version'] == PARSER_VERSION else 'native'
        for identifier, (key, group) in possible.items():
            if multiplicity[key] == 1:
                self._entries[identifier] = group
            else:
                self._blocked.update(row['extraction_id'] for row in group[selected_label])
        if self._mechanism - self._entries.keys():
            self._blocked.update(row['extraction_id'] for row in _rows(self._source) if _mechanism(row))

    @property
    def mechanism_ids(self):
        return set(self._mechanism)

    @property
    def target_ids(self):
        return self.mechanism_ids

    @property
    def blocked_selected_ids(self):
        return set(self._blocked)

    @property
    def unbound_selected_ids(self):
        return self.blocked_selected_ids

    def legacy_binding(self, prior):
        """A real v2/v7 row carrying the old ID, for conservation only."""
        group = self._entries.get(prior.get('id'))
        if group is None:
            return None
        rows = [row for label in ('legacy', 'native') for row in group[label]
                if row['extraction_id'] == prior['id']]
        if not rows:
            return None
        return deepcopy(rows[0])

    def selected_binding(self, prior):
        """Locate the unique selected physical row without changing its ID."""
        group = self._entries.get(prior.get('id'))
        label = 'legacy' if self._source['parser_version'] == PARSER_VERSION else 'native'
        return deepcopy(group[label][0]) if group is not None and len(group[label]) == 1 else None

    def decision(self, prior):
        identifier = prior.get('id')
        empty = {'status': 'deferred' if identifier in self._mechanism else 'not_applicable',
                 'changed_fields': [], 'corrections': []}
        if (identifier not in self._entries or prior != self._formal.get(identifier)
                or self._scope is None or self.status != 'observed'
                or not self._source.get('filed_at') or not self._effective.get('filed_at')
                or not self._legacy.get('filed_at') or not self._native.get('filed_at')
                or prior.get('filed_at') != self._source['filed_at']+'T00:00:00Z'):
            return empty
        group = self._entries[identifier]
        if len(group['legacy']) != 1 or len(group['native']) != 1:
            return empty
        legacy, native = group['legacy'][0], group['native'][0]
        for report, row in ((self._legacy, legacy), (self._native, native)):
            if (row not in report['transactions'] or row.get('reasons') or row.get('account_title')
                    or 'amount_low_exclusive' in row
                    or sum(_key(item) == _key(row) for item in report.get('source_rows', [])) != 1):
                return empty
        fields = ('asset_name', 'ticker', 'transaction_type', 'transaction_date', 'amount_low', 'amount_high')
        if any(legacy.get(field) != native.get(field) for field in fields) or not _mechanism(legacy):
            return empty
        owner = native.get('owner')
        owner_proof = None
        if owner in {'Self', 'Spouse', 'Dependent Child'}:
            if prior.get('owner') != owner:
                return empty
        elif owner == 'Undisclosed':
            owner_proof = self._scope.decision(prior)
            if prior.get('owner') == 'Self':
                if owner_proof['status'] != 'approved':
                    return empty
                owner = ('Undisclosed' if self._source['parser_version'] == GENERIC_PARSER_VERSION else 'Self')
            elif prior.get('owner') != 'Undisclosed':
                return empty
        else:
            return empty
        matched = []
        for report, row in ((self._legacy, legacy), (self._native, native)):
            if row['extraction_id'] != identifier:
                continue
            historical = _historical_projection(report, row, self._person)
            owners = [historical['owner']]
            if (report is self._legacy and native['owner'] == 'Undisclosed'
                    and self._scope.decision(prior)['status'] == 'alias'):
                owners.append('Undisclosed')
            for old_owner in owners:
                for instrument in ('Bond', 'Stock'):
                    represented = _represented({**historical, 'owner': old_owner,
                                                'instrument_type': instrument}, prior)
                    if represented is not None and represented not in matched:
                        matched.append(represented)
        if len(matched) != 1:
            return empty
        final = {**matched[0], 'instrument_type': 'Stock', 'owner': owner}
        changed = sorted(key for key in final if final[key] != prior[key])
        if not changed:
            return {'status': 'current', 'fact': final, 'changed_fields': [], 'corrections': []}
        if (prior.get('instrument_type') == 'Stock' and changed == ['owner']
                and owner_proof is not None and owner_proof['status'] == 'approved'
                and self._source['parser_version'] == GENERIC_PARSER_VERSION):
            # Classification is already current. Preserve the independently
            # established owner policy; reconcile() does not authorize this
            # as a classification migration or a new publication exception.
            correction = {'field': 'owner', 'old_value': 'Self', 'new_value': 'Undisclosed',
                'rule': 'approved_v2_default_self_to_undisclosed/v1',
                'formal_id': identifier, 'source_sha256': self._source['source_sha256'],
                'source_reference': deepcopy(owner_proof['source_reference'])}
            return {'status': 'current', 'fact': final, 'changed_fields': changed,
                    'corrections': [correction]}
        if prior.get('instrument_type') != 'Bond' or changed not in (
                ['instrument_type'], ['instrument_type', 'owner']):
            return empty
        corrections = [{'field': field, 'old_value': prior[field], 'new_value': final[field],
                        'rule': POLICY_VERSION, 'formal_id': identifier,
                        'source_sha256': self._source['source_sha256'],
                        'page_number': legacy['page_number'], 'cells': deepcopy(legacy['cells'])}
                       for field in changed]
        return {'status': 'approved', 'fact': final, 'changed_fields': changed, 'corrections': corrections}

    def reconcile(self, prior):
        decision = self.decision(prior)
        return {key: decision[key] for key in ('fact', 'corrections')} if decision['status'] == 'approved' else None

    def source_current_ids(self, actualfacts):
        result = set()
        for fact in actualfacts:
            prior = self._formal.get(fact.get('id'))
            if prior is not None:
                decision = self.decision(prior)
                if decision.get('fact') == fact:
                    result.add(fact['id'])
        return result

    def unchanged_current_ids(self, actualfacts):
        """Exact fresh-current compatibility, never a source migration proof."""
        view = self._ordinary_current_view()
        if (not view.get('filed_at') or view.get('document_reasons')
                or view.get('legacy_source_binding')):
            return set()
        result = set()
        for fact in actualfacts:
            prior = self._formal.get(fact.get('id'))
            if (prior is None or fact != prior or prior.get('instrument_type') != 'Stock'
                    or fact.get('id') not in self._mechanism):
                continue
            rows = [row for row in view.get('transactions', []) if row['extraction_id'] == fact['id']]
            if (len(rows) != 1 or rows[0].get('reasons') or rows[0].get('legacy_inheritance')
                    or sum(_key(item) == _key(rows[0]) for item in view.get('source_rows', [])) != 1):
                continue
            if _represented(_transaction_projection(view, rows[0], self._person), prior) is not None:
                result.add(fact['id'])
        return result

    def _ordinary_current_view(self):
        """Recognize only the existing receiver's exact manual-date layer.

        The caller applies/validates the sidecar; status replays it separately.
        This does not authenticate raw dates or authorize any fact change.
        """
        from .oge_manual_dates import DATE_REASON
        evidence = self._effective.get('manual_filing_date_evidence')
        if (self._source.get('parser_version') != PARSER_VERSION
                or self._source.get('filed_at') is not None
                or DATE_REASON not in self._source.get('document_reasons', [])
                or not isinstance(evidence, dict)
                or set(evidence) != {'review_path', 'page_number', 'source_sha256',
                                     'raw_reading', 'observed_date'}):
            return self._source
        observed = evidence['observed_date']
        from datetime import date
        try:
            if not isinstance(observed, str) or date.fromisoformat(observed).isoformat() != observed:
                return self._source
        except ValueError:
            return self._source
        if (observed != self._effective.get('filed_at') or evidence['page_number'] != 1
                or evidence['source_sha256'] != self._source['source_sha256']
                or evidence['review_path'] != f"oge/manual_cover_dates/{self._source['document_id']}/{self._source['source_sha256']}.json"
                or not isinstance(evidence['raw_reading'], str) or not evidence['raw_reading'].strip()):
            return self._source
        expected = deepcopy(self._source)
        expected['filed_at'] = observed
        expected['document_reasons'] = [reason for reason in expected['document_reasons'] if reason != DATE_REASON]
        expected['evidence_complete'] = not expected['document_reasons']
        expected['manual_filing_date_evidence'] = deepcopy(evidence)
        return self._effective if expected == self._effective else self._source


def prepare_native_classification(extraction, formal, record, *, evidence_root=None,
                                  source_extraction=None, legacy_extraction=None):
    return NativeClassification(extraction, formal, record, evidence_root=evidence_root,
        source_extraction=source_extraction, legacy_extraction=legacy_extraction)
