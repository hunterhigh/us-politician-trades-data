"""Preserve a published native owner alias when the selected reader is v2.

The selected default interpretation remains unchanged. A generic replay is
auxiliary source evidence only; it never supplies default rows or fresh owners.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from .oge import OgeCatalogError
from .oge_reports import PARSER_VERSION, GENERIC_PARSER_VERSION
from .oge_native_owner_scope import (prepare_native_owner_scope,
    source_interpretation_binding, _transaction_projection, _represented)


class DefaultOwnerConservation:
    def __init__(self, extraction, formal, record, *, evidence_root=None,
                 source_extraction=None):
        self._effective = deepcopy(extraction)
        self._source = deepcopy(extraction if source_extraction is None else source_extraction)
        self._scope = None
        self._protected = set()
        self._explicit = {}
        self._selected = {}
        self._blocked = set()
        self._blocked_formal = set()
        self.status, self.reason = 'not_applicable', 'ordinary_default_owner_conservation_not_applicable'
        self.binding = {
            'policy_version': 'oge-default-owner-conservation/v1',
            'source': source_interpretation_binding(self._source),
            'auxiliary_owner_scope': None,
        }
        facts = list(formal)
        identifiers = [fact.get('id') for fact in facts]
        if len(identifiers) != len(set(identifiers)):
            raise OgeCatalogError('OGE default owner formal IDs are duplicated')
        if (self._source.get('parser_version') != PARSER_VERSION
                or extraction.get('parser_version') != PARSER_VERSION):
            return
        if any(extraction.get(field) != self._source.get(field) for field in (
                'document_id', 'source_id', 'source_sha256', 'source_url',
                'parser_version', 'catalog_filer_name', 'agency', 'position_title')):
            raise OgeCatalogError('OGE default owner effective input differs from raw source identity')

        related = [fact for fact in facts if fact.get('source_id') == 'oge'
            and fact.get('verification_status') == 'official_matched'
            and fact.get('filing_id') == self._source.get('document_id')
            and fact.get('owner') in {'Undisclosed', 'Spouse', 'Dependent Child'}]
        if any(fact.get('source_url') != self._source.get('source_url') for fact in related):
            raise OgeCatalogError('OGE default owner formal source URL differs from selected document')
        self._explicit = {fact['id']: deepcopy(fact) for fact in related
                          if fact.get('owner') in {'Spouse', 'Dependent Child'}}

        self_rows = {row.get('extraction_id') for row in
                     self._source.get('transactions', []) + self._source.get('quarantined', [])
                     if row.get('owner') == 'Self'}
        self._protected = {
            fact['id'] for fact in facts
            if fact.get('id') in self_rows and fact.get('owner') == 'Undisclosed'
            and fact.get('source_id') == 'oge'
            and fact.get('verification_status') == 'official_matched'
            and fact.get('filing_id') == self._source.get('document_id')
            and fact.get('source_url') == self._source.get('source_url')
        }
        self._protected.update(self._explicit)
        if not self._protected:
            self.reason = 'no_protected_default_owner_fact'
            return
        self.status, self.reason = 'unknown', 'original_source_unavailable'
        if evidence_root is None:
            if self._explicit:
                raise OgeCatalogError('OGE default explicit owner conservation requires the original source root')
            self._bind_selected()
            return

        # The shared verifier authenticates raw v2 and this actual generic
        # auxiliary in full. No saved generic cache selects the current reader.
        document = self._source.get('document_id')
        sha = self._source.get('source_sha256')
        if (not isinstance(document, str) or not re.fullmatch(r'[0-9a-f]{32}', document)
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            raise OgeCatalogError('OGE default owner canonical source identity is invalid')
        root = Path(evidence_root)
        metadata_path = root / f'oge/reports/{document}/{sha}.json'
        from .oge_reports import parse_archived_pdf
        try:
            auxiliary = parse_archived_pdf(root, metadata_path, generic=True)
        except OgeCatalogError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise OgeCatalogError(f'OGE default owner source authentication failed: {error}') from error
        if auxiliary.get('parser_version') != GENERIC_PARSER_VERSION:
            raise OgeCatalogError('OGE default owner source does not select ordinary native interpretations')
        self._scope = prepare_native_owner_scope(auxiliary, self._source, record,
            evidence_root=root, source_extraction=auxiliary)
        self.binding['auxiliary_owner_scope'] = deepcopy(self._scope.binding)
        self.status, self.reason = self._scope.status, self._scope.reason
        self._bind_selected()

    def _bind_selected(self):
        """Bind old identities to actual raw default rows without changing raw."""
        from .oge_transaction_adapter import _row_key
        rows = self._source.get('transactions', []) + self._source.get('quarantined', [])
        native_rows = ([] if self._scope is None else
                       self._scope.source.get('transactions', []) + self._scope.source.get('quarantined', []))
        possible = {}
        for identifier in self._protected:
            candidates = [row for row in rows if row.get('extraction_id') == identifier]
            if identifier in self._explicit:
                native = [row for row in native_rows if row.get('extraction_id') == identifier]
                if native:
                    keys = {_row_key(row) for row in native}
                    candidates = [row for row in rows if _row_key(row) in keys]
                elif not candidates:
                    raise OgeCatalogError('OGE default explicit owner has no native physical source identity')
            if len(candidates) != 1:
                self._blocked_formal.add(identifier)
                self._blocked.update(row['extraction_id'] for row in candidates)
                continue
            row = candidates[0]
            key = _row_key(row)
            if (sum(_row_key(item) == key for item in rows) != 1
                    or sum(_row_key(item) == key for item in self._source.get('source_rows', [])) != 1):
                self._blocked_formal.add(identifier)
                self._blocked.add(row['extraction_id'])
                continue
            possible[identifier] = row
        by_physical = {}
        for identifier, row in possible.items():
            by_physical.setdefault(_row_key(row), []).append(identifier)
        for identifiers in by_physical.values():
            if len(identifiers) != 1:
                self._blocked_formal.update(identifiers)
                self._blocked.update(possible[identifier]['extraction_id'] for identifier in identifiers)
            else:
                identifier = identifiers[0]
                self._selected[identifier] = deepcopy(possible[identifier])

    @property
    def protected_ids(self) -> set[str]:
        """Physical intersection to conserve, independently of full proof."""
        return set(self._protected)

    @property
    def blocked_selected_ids(self) -> set[str]:
        return set(self._blocked)

    def selected_binding(self, prior: dict) -> dict | None:
        """Return one real selected row, solely for preserving an existing ID."""
        row = self._selected.get(prior.get('id'))
        return deepcopy(row) if row is not None else None

    def _explicit_current(self, fact):
        from .oge_transaction_adapter import _row_key
        from .whitehouse_native_correction import _historical_projection
        from .oge_native_classification import _mechanism
        prior = self._explicit[fact['id']]
        native = self._scope.source
        if (not native.get('filed_at') or native['filed_at'] != self._source['filed_at']
                or set(fact) != set(prior)):
            return False
        rows = [row for row in native.get('transactions', []) if row.get('extraction_id') == fact['id']]
        selected = self._selected.get(fact['id'])
        if len(rows) != 1 or selected is None:
            return False
        row = rows[0]
        key = _row_key(row)
        if (_row_key(selected) != key or row.get('reasons')
                or row.get('owner') != prior.get('owner')
                or sum(_row_key(item) == key for item in native.get('source_rows', [])) != 1
                or selected not in self._source.get('transactions', [])):
            return False
        fields = ('asset_name', 'ticker', 'transaction_type', 'transaction_date', 'amount_low', 'amount_high')
        if any(selected.get(field) != row.get(field) for field in fields):
            return False
        projection = _transaction_projection(native, row, self._scope._person)
        if _represented(projection, fact) is None:
            return False
        if _represented(projection, prior) is not None:
            return fact == prior
        # The previous classification unit independently authorizes this only
        # Endnote transition. This observer merely rechecks its actual final
        # source object; it never applies a field change or publishes a proof.
        if (_mechanism(row) and not row.get('account_title') and 'amount_low_exclusive' not in row
                and fact == {**prior, 'instrument_type': 'Stock'}):
            historical = _historical_projection(native, row, self._scope._person)
            return _represented(historical, prior) is not None
        return False

    def source_current_ids(self, actualfacts) -> set[str]:
        """Prove retained full aliases; ordinary financial checks remain separate."""
        if (self._scope is None or self.status != 'observed'
                or not self._source.get('filed_at') or not self._effective.get('filed_at')):
            return set()
        result = set()
        seen = set()
        for fact in actualfacts:
            identifier = fact.get('id')
            if identifier in seen:
                raise OgeCatalogError('OGE default owner published IDs are duplicated')
            seen.add(identifier)
            if identifier in self._blocked_formal:
                continue
            if identifier in self._explicit:
                if self._explicit_current(fact):
                    result.add(identifier)
            elif identifier in self._protected and self._scope.alias(fact) is not None:
                result.add(identifier)
        return result


def prepare_default_owner_conservation(extraction, formal, record, *,
                                       evidence_root=None, source_extraction=None) -> DefaultOwnerConservation:
    return DefaultOwnerConservation(extraction, formal, record,
        evidence_root=evidence_root, source_extraction=source_extraction)
