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
                                     source_interpretation_binding)


class DefaultOwnerConservation:
    def __init__(self, extraction, formal, record, *, evidence_root=None,
                 source_extraction=None):
        self._effective = deepcopy(extraction)
        self._source = deepcopy(extraction if source_extraction is None else source_extraction)
        self._scope = None
        self._protected = set()
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
        if not self._protected:
            self.reason = 'no_protected_default_owner_fact'
            return
        self.status, self.reason = 'unknown', 'original_source_unavailable'
        if evidence_root is None:
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

    @property
    def protected_ids(self) -> set[str]:
        """Physical intersection to conserve, independently of full proof."""
        return set(self._protected)

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
            if identifier in self._protected and self._scope.alias(fact) is not None:
                result.add(identifier)
        return result


def prepare_default_owner_conservation(extraction, formal, record, *,
                                       evidence_root=None, source_extraction=None) -> DefaultOwnerConservation:
    return DefaultOwnerConservation(extraction, formal, record,
        evidence_root=evidence_root, source_extraction=source_extraction)
