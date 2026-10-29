"""Original-source proof for one existing WH/direct native duplicate mechanism.

This conserves an already published direct object and its person. It cannot
qualify a new direct fact/person, change the canonical fields, or authorize
general rekeys. Existing owner, Endnote and account-display source proofs may
explain the WH predecessor before its redundant object is removed.
Only ordinary direct v7's fresh Undisclosed ID and native WH v5's inherited
Self ID with the existing owner normalization are supported. Unsupported
complete projections remain pending; saved receipts never grant this proof.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .whitehouse_native_correction import (
    MAX_DOCUMENTS, NativeCorrectionError, _RootSources,
    _replay, prove_native_row, prove_native_account_display, _historical_projection,
)

LEGACY_POLICY_VERSION = 'whitehouse-exact-duplicate/v1'
POLICY_VERSION = 'whitehouse-exact-duplicate/v2'
MAX_SOURCE_ROWS = 4096
PERSON_FIELDS = ('id', 'display_name', 'role', 'office_type', 'chamber',
                 'party', 'state', 'disclosure_authority')
_IDENTITY_FIELDS = {'id', 'filing_id', 'source_url'}


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _need(condition, reason):
    if not condition:
        raise NativeCorrectionError('White House exact duplicate ' + reason)


def _direct_url(document, url):
    if not isinstance(document, str) or not re.fullmatch(r'[0-9a-f]{32}', document):
        return False
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc == 'extapps2.oge.gov' and
            not parsed.query and not parsed.fragment and
            bool(re.fullmatch(r'/201/Presiden\.nsf/PAS\+Index/' + document +
                              r'/\$FILE/[^/]+\.pdf', parsed.path, re.I)))


def _inventory(report):
    rows = report.get('source_rows')
    _need(isinstance(rows, list) and len(rows) <= MAX_SOURCE_ROWS,
          'source inventory exceeds row budget')
    _need(all(isinstance(row, dict) and type(row.get('page_number')) is int and
              row['page_number'] > 0 and isinstance(row.get('cells'), list) for row in rows),
          'source inventory is invalid')
    return [(row['page_number'], row['cells']) for row in rows]


def _direct_replay(sources, evidence, review, binding, wh_report):
    """Read precisely the bound canonical namespace, never choose a version."""
    from .oge_reports import (GENERIC_PARSER_VERSION, PARSER_VERSION, MAX_PDF_BYTES,
                              REPORT_ARCHIVE_SCHEMA, _validate_pdf,
                              parse_archived_pdf, selected_parser_version)
    archive = binding['oge_archive']
    document, sha, url = (archive.get(key) for key in
                           ('document_id', 'sha256', 'document_url'))
    _need(_direct_url(document, url) and sha == wh_report['source_sha256'],
          'direct archive locator differs from the shared original')
    relative = f'oge/reports/{document}/{sha}.pdf'
    metadata = sources.obj(evidence, relative[:-4] + '.json')
    raw = sources.read(evidence, relative, MAX_PDF_BYTES)
    _validate_pdf(raw)
    _need(metadata == archive and metadata.get('schema_version') == REPORT_ARCHIVE_SCHEMA and
          metadata.get('source_id') == 'oge' and metadata.get('document_id') == document and
          metadata.get('document_url') == url and metadata.get('archive_path') == relative and
          metadata.get('sha256') == sha and metadata.get('byte_length') == len(raw) and
          hashlib.sha256(raw).hexdigest() == sha,
          'direct canonical metadata or PDF binding differs')
    wh_relative = f"whitehouse/disclosures/reports/{wh_report['document_id'][7:]}/{sha}.pdf"
    _need(sources.read(evidence, wh_relative, MAX_PDF_BYTES) == raw,
          'archives do not contain identical original bytes')
    saved = [sources.obj(review, f'oge/extractions/{document}/{sha}/'
                         f'{version.replace("/", "-")}.json')
             for version in (PARSER_VERSION, GENERIC_PARSER_VERSION)]
    _need(saved[1] == binding['oge_extraction'], 'bound direct raw cache differs')
    with TemporaryDirectory(prefix='wh-exact-direct-') as temporary:
        root = Path(temporary)
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        path.with_suffix('.json').write_bytes(_encoded(metadata))
        # Select from actual source before any parser call. The only accepted
        # families are native; no OCR or special-source fallback is invoked.
        _need(selected_parser_version(metadata, path, sha) == PARSER_VERSION and
              selected_parser_version(metadata, path, sha, generic=True) == GENERIC_PARSER_VERSION,
              'requires ordinary native direct v2/v7 source selection')
        actual = [parse_archived_pdf(root, path.with_suffix('.json'), generic=mode)
                  for mode in (False, True)]
    _need(_encoded(actual) == _encoded(saved),
          'direct raw caches differ from complete original replay')
    _need(all(_inventory(item) == _inventory(wh_report) for item in actual),
          'the two readers have different physical source inventories')
    return actual[0], actual[1], metadata


class ExactDuplicateSources:
    """Runtime source accessor; no constructor accepts a successful receipt."""
    def __init__(self):
        self._sources = None
        self._contexts = {}
        self._pending = set()
        self._base = None
        self._policy_version = POLICY_VERSION

    @property
    def pending_ids(self):
        return set(self._pending)

    @property
    def resolved_ids(self):
        return {entry['duplicate_fact']['id'] for context in self._contexts.values()
                for entry in context['rows']}

    @property
    def has_resolutions(self):
        return bool(self.resolved_ids)

    def provenance(self):
        dependencies = [] if self._sources is None else sorted(
            (role, path, hashlib.sha256(raw).hexdigest())
            for (role, path), raw in self._sources.cache.items())
        return {'exact_duplicate_policy_version': self._policy_version,
                'exact_duplicate_dependency_sha256': _digest(dependencies)}

    def resolve(self, report, row, *, canonical_fact, duplicate_fact):
        """Conserve two actual prepared objects only after complete source proof."""
        context = self._contexts.get(report.get('document_id'))
        if context is None or report != context['report']:
            return None
        proofs = [entry for entry in context['rows']
                  if entry['row'] == row and entry['canonical_fact'] == canonical_fact and
                  entry['duplicate_fact'] == duplicate_fact]
        if len(proofs) != 1:
            return None
        proof = proofs[0]
        return {'canonical_fact': deepcopy(canonical_fact), 'duplicate_fact': deepcopy(duplicate_fact),
                'record': {'old_transaction_id': duplicate_fact['id'],
                    'canonical_transaction_id': canonical_fact['id'],
                    'source_sha256': report['source_sha256'],
                    'page_number': row['page_number'], 'row_number': row['row_number'],
                    'old_fact_sha256': _digest(duplicate_fact),
                    'canonical_fact_sha256': _digest(canonical_fact),
                    'source_proof_rule': self._policy_version,
                    **({'source_correction_fields': list(proof['source_correction_fields'])}
                       if proof['source_correction_fields'] else {})}}


def _source_states(sources, evidence, review, report, conventional, coverage, directory,
                   row, person, people, classification_sources, policy_version):
    """Derive H/Howner/F only from already replayed original source fields.

    Existing helpers retain their strict predecessor contract: their supplied
    H is reconstructed from source, never edited out of the proposed old fact.
    The actual old WH object is compared with the resulting states afterwards.
    """
    from .whitehouse_native_correction import (
        NativeClassificationSources, native_classification_target,
        reconcile_native_classification,
    )
    raw = [item for item in conventional if item.get('extraction_id') == row['extraction_id']]
    if len(raw) != 1:
        return None
    source_historical = _historical_projection(report, raw[0], person)
    account = row.get('account_title')
    endnote = native_classification_target(report, row)
    if policy_version == LEGACY_POLICY_VERSION and (
            account or endnote or re.search(r'\bEndnote\b', str(row.get('asset_name')), re.I)):
        return None
    if account:
        # Those are two independently supported mechanisms, not an approved
        # arbitrary combination of classifier and account display changes.
        if endnote or re.search(r'\bEndnote\b', str(row.get('asset_name')), re.I):
            return None
        return prove_native_account_display(report, conventional, coverage, directory,
            row['extraction_id'], people, source_historical)
    historical, normalized, checked = prove_native_row(report, conventional, coverage,
        directory, row['extraction_id'], people, source_historical)
    if checked != row:
        return None
    if endnote:
        if classification_sources[0] is None:
            classification_sources[0] = NativeClassificationSources(
                sources, evidence_ref=evidence, review_ref=review)
            classification_sources[0]._load(report['document_id'], report['source_url'])
        projected = reconcile_native_classification(report, row, historical,
            person_id=person, source_people=people, sources=classification_sources[0])
        if projected is None:
            return None
        return historical, normalized, projected['fact']
    if historical['instrument_type'] != 'Stock' or normalized['instrument_type'] != 'Stock':
        return None
    return historical, normalized, normalized


def _prepare_context(context, sources, evidence, review, report, binding, base):
    from .whitehouse_278t import GENERIC_PARSER_VERSION, PARSER_VERSION
    from .oge_reports import parse_table_rows
    from .oge_candidate import _identity_key, _person_id, _transaction_projection
    from .whitehouse_278t_candidate import _person
    from .oge_native_owner_scope import _represented

    document, sha = report.get('document_id'), report.get('source_sha256')
    _need(isinstance(document, str) and re.fullmatch(r'wh-url:[0-9a-f]{24}', document) and
          isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{64}', sha), 'WH source identity is invalid')
    archive = binding['whitehouse_archive']
    _need(archive.get('document_id') == document and archive.get('sha256') == sha and
          archive.get('document_url') == report.get('source_url'), 'WH binding selects a different source')
    # Unsupported families remain observable, but cannot use a saved receipt
    # to assert this native duplicate policy.
    if report.get('parser_version') != GENERIC_PARSER_VERSION:
        return
    replay, conventional, coverage, directory = _replay(sources, evidence, review,
        document, report['source_url'], require_account_visibility=True)
    _need(_encoded(replay) == _encoded(report), 'selected WH cache differs from source replay')
    meta_path = f'whitehouse/disclosures/reports/{document[7:]}/{sha}.json'
    _need(sources.obj(evidence, meta_path) == archive, 'bound WH metadata differs')
    legacy_path = (f'whitehouse/extractions/{document[7:]}/{sha}/'
                   f'{PARSER_VERSION.replace("/", "-")}.json')
    wh_legacy = sources.obj(review, legacy_path)
    old_good, old_bad = parse_table_rows(_inventory(replay), source_sha=sha, default_owner='Self')
    _need(wh_legacy.get('transactions') == old_good and wh_legacy.get('quarantined') == old_bad,
          'WH legacy financial inventory differs from original raw cells')
    legacy, direct, metadata = _direct_replay(sources, evidence, review, binding, replay)
    # This policy does not interpret amendments or open amounts, and cannot
    # grant a new direct source identity.
    if metadata.get('amended_label') or metadata.get('pending_final_oge_disposition'):
        return
    key = _identity_key(metadata)
    people = [person for person in base['people'] if person.get('id') == _person_id(key)]
    if len(people) != 1:
        return
    person = people[0]
    expected_person = _person(metadata)
    if any(person.get(field) != expected_person.get(field) for field in PERSON_FIELDS):
        return
    matching_identities = [item for item in coverage.get('reports', [])
                           if all(item.get(field) == metadata.get(field)
                                  for field in ('filer_name', 'agency', 'position_title'))]
    if not matching_identities:
        return
    rows = []
    classification_sources = [None]  # One bounded accessor per actual report.
    for wh_row in replay['transactions']:
        locator = wh_row.get('source_rows_index')
        if type(locator) is not int or not 0 <= locator < len(replay['source_rows']):
            continue
        physical = replay['source_rows'][locator]
        same = lambda item: (item.get('page_number') == physical['page_number'] and
                             item.get('cells') == physical['cells'])
        # Count all dispositions; a competing/quarantined identical source row
        # cannot be resolved by choosing the successful interpretation.
        wh_matches = [item for item in [*replay['transactions'], *replay['quarantined']] if same(item)]
        direct_matches = [item for item in [*direct['transactions'], *direct['quarantined']] if same(item)]
        legacy_matches = [item for item in [*legacy['transactions'], *legacy['quarantined']] if same(item)]
        physical_matches = [item for item in replay['source_rows'] if
                            item['page_number'] == physical['page_number'] and item['cells'] == physical['cells']]
        if not (len(wh_matches) == len(direct_matches) == len(legacy_matches) == len(physical_matches) == 1):
            continue
        direct_row, raw_row = direct_matches[0], legacy_matches[0]
        if (direct_row not in direct['transactions'] or raw_row not in legacy['transactions'] or
                wh_row.get('reasons') or direct_row.get('reasons') or raw_row.get('reasons') or
                wh_row.get('owner') != 'Undisclosed' or direct_row.get('owner') != 'Undisclosed' or
                raw_row.get('owner') != 'Self' or
                any(item.get('amount_low_exclusive') for item in (wh_row, direct_row, raw_row)) or
                # Conventional v2 proves original cells and the historical
                # Self ID only. Its old whole-text signature reader need not
                # resolve the cover date: the complete formal dates below
                # come independently from WH v5 and direct v7 cover roles.
                any(item.get('document_reasons') or not item.get('evidence_complete') or
                    not item.get('filed_at') for item in (replay, direct))):
            continue
        canonical = [fact for fact in base['transactions'] if
                     fact.get('id') == direct_row.get('extraction_id') and
                     fact.get('filing_id') == direct['document_id'] and
                     fact.get('source_url') == direct['source_url']]
        duplicates = [fact for fact in base['transactions'] if
                      fact.get('id') == wh_row.get('extraction_id') and
                      fact.get('filing_id') == document and fact.get('source_url') == replay['source_url']]
        if len(canonical) != 1 or len(duplicates) != 1:
            continue
        try:
            states = _source_states(sources, evidence, review, replay, conventional,
                coverage, directory, wh_row, person['id'], base['people'], classification_sources,
                context._policy_version)
        except NativeCorrectionError:
            # Valid sources may be outside the supported identity/row contract.
            # No partial old/current field comparison authorizes deletion.
            continue
        if states is None:
            continue
        historical, normalized, final = states
        expected_direct = _transaction_projection(direct, direct_row, person['id'])
        if (final['id'] == expected_direct['id'] or
                {key: value for key, value in final.items() if key not in _IDENTITY_FIELDS} !=
                {key: value for key, value in expected_direct.items() if key not in _IDENTITY_FIELDS} or
                _represented(expected_direct, canonical[0]) != canonical[0]):
            continue
        allowed = (normalized,) if context._policy_version == LEGACY_POLICY_VERSION else states
        if any(_represented(projection, duplicates[0]) == duplicates[0] for projection in allowed):
            changed = sorted(key for key in final.keys() | duplicates[0].keys()
                if key not in _IDENTITY_FIELDS and final.get(key) != duplicates[0].get(key))
            rows.append({'row': deepcopy(wh_row), 'canonical_fact': deepcopy(canonical[0]),
                         'duplicate_fact': deepcopy(duplicates[0]), 'source_correction_fields': changed})
            context._pending.discard(duplicates[0]['id'])
    context._contexts[document] = {'report': deepcopy(replay), 'rows': rows}


def prepare_whitehouse_exact_duplicate_sources(base, extractions, source_bindings, *,
        evidence_root=None, review_root=None, coverage_input=None, extraction_status_input=None,
        _sources=None, evidence_ref='evidence', review_ref='review', policy_version=POLICY_VERSION):
    """Prepare bounded originals before normal candidate cache fast returns.

    The private source-reader injection is also used by fixed-Git publication;
    it accepts the existing bounded read/obj/cache interface, not a proof dict.
    Publication must first independently pin both full old objects and people.
    """
    context = ExactDuplicateSources()
    _need(policy_version in {LEGACY_POLICY_VERSION, POLICY_VERSION}, 'policy version is unsupported')
    context._policy_version = policy_version
    _need(isinstance(base, dict) and base.get('meta', {}).get('is_demo') is False and
          isinstance(base.get('transactions'), list) and isinstance(base.get('people'), list),
          'needs actual non-demo formal objects and people')
    ids = [fact.get('id') for fact in base['transactions'] if isinstance(fact, dict)]
    _need(len(ids) == len(base['transactions']) and len(ids) == len(set(ids)),
          'formal IDs are invalid or duplicated')
    if source_bindings is None:
        return context
    _need(isinstance(source_bindings, dict) and
          source_bindings.get('schema_version') == 'whitehouse-278t-exact-source-bindings/v1' and
          isinstance(source_bindings.get('reports'), list), 'binding schema differs')
    _need(isinstance(extractions, list) and
          all(isinstance(report, dict) for report in extractions), 'selected reports exceed document budget')
    reports = {report.get('document_id'): report for report in extractions}
    _need(len(reports) == len(extractions), 'selected report identity is duplicated')
    selected, seen_documents = [], set()
    for binding in source_bindings['reports']:
        _need(isinstance(binding, dict) and all(isinstance(binding.get(field), dict) for field in
              ('whitehouse_archive', 'oge_archive', 'oge_extraction')), 'binding objects are missing')
        document = binding['whitehouse_archive'].get('document_id')
        _need(document in reports and document not in seen_documents,
              'binding selected report is missing or repeated')
        seen_documents.add(document)
        direct_archive, direct_raw = binding['oge_archive'], binding['oge_extraction']
        _need(all(isinstance(direct_raw.get(key), list) for key in ('transactions', 'quarantined')),
              'binding direct row inventory is invalid')
        _need(len(direct_raw['transactions']) + len(direct_raw['quarantined']) <= MAX_SOURCE_ROWS,
              'binding direct row inventory exceeds budget')
        bound_ids = {row.get('extraction_id') for row in
                     [*direct_raw['transactions'], *direct_raw['quarantined']]
                     if isinstance(row, dict) and isinstance(row.get('extraction_id'), str)}
        # A new report, or only its existing WH fact, is not a destructive
        # duplicate transition. Do not add a source-profile gate to those paths.
        # Keep reading on canonical-only repeats so provenance remains stable.
        if not any(fact.get('source_id') == 'oge' and
                   fact.get('filing_id') == direct_archive.get('document_id') and
                   fact.get('source_url') == direct_archive.get('document_url') and
                   fact.get('id') in bound_ids for fact in base['transactions']):
            continue
        selected.append((document, binding))
        _need(len(selected) <= MAX_DOCUMENTS, 'selected binding document count exceeds budget')
        context._pending.update(fact['id'] for fact in base['transactions'] if
            fact.get('filing_id') == document and fact.get('source_id') == 'oge')
    if not selected:
        return context
    if _sources is None:
        if evidence_root is None or review_root is None:
            return context
        _sources = _RootSources(evidence_root, review_root, coverage_input=coverage_input,
                               extraction_status_input=extraction_status_input)
    context._sources = _sources
    # Immutable prepared views prevent later caller edits from changing a
    # source proof. Every returned object remains a copy of an actual old fact.
    context._base = deepcopy(base)
    for document, binding in selected:
        _prepare_context(context, _sources, evidence_ref, review_ref,
                         deepcopy(reports[document]), deepcopy(binding), context._base)
    return context
