"""Bounded original-source proof shared by native consumers and publication.

The historical classifier is a frozen predecessor projection, not a classifier
for new facts. Only the explicit Endnote migration uses the current classifier.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

class NativeCorrectionError(ValueError):
    """An attempted native migration lacks its complete source proof."""


MAX_DOCUMENTS = 12
MAX_PAGES = 32
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_BOARD_BYTES = 64 * 1024 * 1024


def _safe(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise NativeCorrectionError('White House owner correction source identity/path is invalid')
    return value


def _historical_instrument(asset, ticker):
    """Frozen pre-f09b37958 oge_candidate._instrument; Endnote matched note.

    This reproduces an old value from source text. A separate current source
    projection and Endnote scope proof authorize the narrow migration.
    """
    value = asset.casefold()
    if ' etf' in value or value.endswith('etf'):
        return 'ETF'
    if 'mutual fund' in value or 'fund' in value:
        return 'Mutual Fund'
    if 'bond' in value or 'treasury' in value or 'note' in value:
        return 'Bond'
    if ticker or 'stock' in value or 'inc.' in value or 'corp.' in value:
        return 'Stock'
    return 'Unspecified'


def _historical_projection(report, raw_row, person):
    """Frozen conventional v2 formal projection, entirely from replayed source.

    Account display, market mapping and open amount migrations are outside
    this version. No prior/current formal field is an input.
    """
    ticker = raw_row['ticker']
    return {
        'id': raw_row['extraction_id'], 'filing_id': report['document_id'], 'person_id': person,
        'owner': raw_row['owner'], 'asset_name': raw_row['asset_name'], 'ticker': ticker,
        'ticker_mapping_basis': 'filing_explicit' if ticker else None,
        'instrument_type': _historical_instrument(raw_row['asset_name'], ticker),
        'option_type': None, 'strike_price': None, 'expiration_date': None,
        'transaction_type': raw_row['transaction_type'], 'transaction_date': raw_row['transaction_date'],
        'filed_at': report['filed_at'] + 'T00:00:00Z', 'amount_low': raw_row['amount_low'],
        'amount_high': raw_row['amount_high'], 'position_effect': 'unknown', 'position_effect_basis': None,
        'source_id': 'oge', 'source': 'U.S. Office of Government Ethics',
        'source_url': report['source_url'], 'verification_status': 'official_matched'}


def _current_projection(report, row, person):
    """Complete current native projection, using source fields, never prior fields."""
    from .oge_candidate import _instrument
    fact = _historical_projection(report, row, person)
    fact['instrument_type'] = _instrument(row['asset_name'], row.get('ticker'))
    if row.get('account_title'):
        fact['asset_name'] = f"{row['asset_name']} — {row['account_title']}"
    if 'amount_low_exclusive' in row:
        fact['amount_low_exclusive'] = row['amount_low_exclusive']
    return fact


class _Sources:
    def __init__(self, repo, read_object, *, preflight_git=False, max_total=MAX_TOTAL_BYTES, max_pages=MAX_PAGES):
        self.repo, self.read_object, self.total, self.cache = repo, read_object, 0, {}
        self.preflight_git, self.max_total, self.max_pages = preflight_git, max_total, max_pages

    def read(self, commit, path, limit=MAX_JSON_BYTES):
        _safe(path, r'[A-Za-z0-9_./-]+')
        if '..' in path.split('/') or path.startswith('/'):
            raise NativeCorrectionError('White House correction source path escaped archive')
        key = (commit, path)
        if key not in self.cache:
            # The existing helper reads a whole Git blob. Bound its allocation
            # first in real Git, then check injected readers as well.
            if self.preflight_git:
                size = subprocess.run(['git', '-C', str(self.repo), 'cat-file', '-s', f'{commit}:{path}'],
                                      capture_output=True, check=False,
                                      env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
                if size.returncode or not size.stdout.strip().isdigit() or int(size.stdout) > limit:
                    raise NativeCorrectionError('White House correction source is missing or exceeds byte budget')
            raw = self.read_object(self.repo, commit, path)
            self.total += len(raw)
            if len(raw) > limit or self.total > self.max_total:
                raise NativeCorrectionError('White House correction source exceeds byte budget')
            self.cache[key] = raw
        raw = self.cache[key]
        if len(raw) > limit:
            raise NativeCorrectionError('White House correction source exceeds byte budget')
        return raw

    def obj(self, commit, path):
        value = json.loads(self.read(commit, path))
        if not isinstance(value, dict):
            raise NativeCorrectionError('White House correction source must be an object')
        return value


def _replay(sources, evidence, review, document, url, *, require_qualified_document=True,
            allow_missing_legacy=False, require_account_visibility=False):
    from .whitehouse_278t import (GENERIC_PARSER_VERSION, PARSER_VERSION,
        parse_generic_whitehouse_278t_pdf, validate_generic_whitehouse_report)
    from .whitehouse_disclosures import parse_disclosure_index
    from .oge_reports import parse_table_rows

    coverage = sources.obj(review, 'whitehouse/coverage-current.json')
    status = sources.obj(review, 'whitehouse/extraction-status.json')
    matches = [row for row in coverage.get('reports', []) if row.get('document_id') == document]
    if (coverage.get('schema_version') != 'whitehouse-public-coverage/v1' or
            coverage.get('source_id') != 'whitehouse_public_disclosures' or len(matches) != 1 or
            status.get('schema_version') != 'whitehouse-public-extraction-status/v1' or
            status.get('source_id') != 'whitehouse_public_disclosures'):
        raise NativeCorrectionError('White House correction selected review inventory differs')
    selected = matches[0]
    versions = selected.get('archive_sha256_versions')
    if not isinstance(versions, list) or len(versions) != 1:
        raise NativeCorrectionError('White House correction source version is ambiguous')
    sha = _safe(versions[0], r'[0-9a-f]{64}')
    folder = f'whitehouse/extractions/{document[7:]}/{sha}'
    current_path = f'{folder}/{GENERIC_PARSER_VERSION.replace("/", "-")}.json'
    if (selected.get('document_url') != url or selected.get('document_type_from_label') != '278t' or
            selected.get('extraction_parser_version') != GENERIC_PARSER_VERSION or
            selected.get('extraction_path') != current_path or
            selected.get('review_state') not in {'extracted_review_only', 'extracted_with_issues'}):
        raise NativeCorrectionError('White House correction requires selected native v5 input')
    saved = sources.obj(review, current_path)
    legacy_path = f'{folder}/{PARSER_VERSION.replace("/", "-")}.json'
    legacy = (None if allow_missing_legacy and not sources.exists(review, legacy_path)
              else sources.obj(review, legacy_path))
    pdf_path = f'whitehouse/disclosures/reports/{document[7:]}/{sha}.pdf'
    meta = sources.obj(evidence, pdf_path[:-4] + '.json')
    raw = sources.read(evidence, pdf_path, MAX_PDF_BYTES)
    if (meta.get('schema_version') != 'whitehouse-public-disclosures-pdf/v1' or
            meta.get('source_id') != 'whitehouse_public_disclosures' or
            meta.get('document_id') != document or meta.get('document_url') != url or
            meta.get('archive_path') != pdf_path or meta.get('sha256') != sha or
            meta.get('byte_length') != len(raw) or hashlib.sha256(raw).hexdigest() != sha or
            legacy is not None and legacy.get('parser_version') != PARSER_VERSION):
        raise NativeCorrectionError('White House correction archive or legacy version differs')
    page_sha = _safe(meta.get('page_sha256'), r'[0-9a-f]{64}')
    page_path = f'whitehouse/disclosures/pages/{page_sha}'
    html = sources.read(evidence, page_path + '.html', 4 * 1024 * 1024)
    index = parse_disclosure_index(html)
    entries = [row for row in index['reports'] if row['source_document_id'] == document]
    if (index['page_sha256'] != page_sha or
            any(item.get('url') == url for item in index['quarantine']) or
            len(entries) != 1 or any(entries[0].get(field) != meta.get(field) for field in
                ('document_url', 'filer_name_from_label', 'document_type_from_label', 'link_label')) or
            entries[0]['document_type_from_label'] != '278t' or
            saved.get('filer_name') != entries[0]['filer_name_from_label'] or
            saved.get('amended_label') != entries[0]['link_label']):
        raise NativeCorrectionError('White House correction official directory identity differs')
    catalog_coverage = sources.obj(review, 'oge/whitehouse/coverage-current.json')
    with tempfile.TemporaryDirectory(prefix='whitehouse-owner-') as temporary:
        root = Path(temporary)
        def save(path, content):
            target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
        save(pdf_path, raw)
        import pdfplumber
        with pdfplumber.open(root/pdf_path) as pdf:
            if not 1 <= len(pdf.pages) <= sources.max_pages:
                raise NativeCorrectionError('White House correction source page count exceeds budget')
            if require_account_visibility:
                from .native_source_visibility import inspect_standard_visible_document
                visible = inspect_standard_visible_document(pdf, max_pages=sources.max_pages)
                if visible.get('status') != 'observed':
                    raise NativeCorrectionError('White House account display source visibility is unknown: ' +
                        str(visible.get('reason')))
        replay = parse_generic_whitehouse_278t_pdf(root/pdf_path, source_url=url, source_sha256=sha,
            document_id=document, filer_name=entries[0]['filer_name_from_label'],
            amended_label=entries[0]['link_label'], legacy_extraction=legacy)
    if replay != saved or require_qualified_document and replay.get('document_reasons'):
        raise NativeCorrectionError('White House correction native extraction differs from complete source replay')
    validate_generic_whitehouse_report(replay)
    inventory = [(row['page_number'], row['cells']) for row in replay['source_rows']]
    old_rows, bad = parse_table_rows(inventory, source_sha=sha, default_owner='Self')
    if len(old_rows) + len(bad) > 256:
        raise NativeCorrectionError('White House correction legacy row count exceeds budget')
    # Unrelated quarantined rows remain quarantined. The target must have a
    # unique raw replay and the normal interpreter's whole-input scope proof;
    # a report-wide perfect-recognition requirement would discard valid rows.
    return replay, old_rows, catalog_coverage, index


def replay_native_account_display(sources, evidence, review, document, url):
    """Reprove account migration against every page of the same fixed original.

    This strict source precondition belongs only to account-display migration.
    Ordinary owner/classification replay and saved native observations retain
    their established contracts; no saved visibility marker grants this proof.
    """
    return _replay(sources, evidence, review, document, url, require_account_visibility=True)



def _qualified_native_person(report, coverage, directory, key, source_people, prior, *, correction='owner'):
    """Ordinary qualification plus identity independently read from the directory."""
    from .whitehouse_278t_audit import audit_whitehouse_278t, _source_bound_person_from_directory
    checked = audit_whitehouse_278t([report], coverage,
        {'meta': {'is_demo': False}, 'transactions': [prior],
         'people': source_people}, directory_index=directory)
    decision = checked['reports'][0]
    identity = decision['matched_catalog_identity']
    source_identity = _source_bound_person_from_directory(report, directory,
        {'people': source_people}, [], [report])
    disposition = [item for item in decision['rows'] if item['extraction_id'] == key]
    if (identity is None or source_identity is None or
            source_identity['person_id'] != identity['person_id'] or decision['document_reasons'] or
            len(disposition) != 1 or disposition[0]['status'] != 'eligible'):
        raise NativeCorrectionError(f'White House {correction} correction source is not qualified')
    return identity['person_id']


def prove_native_row(report, raw_rows, coverage, directory, key, source_people, prior):
    """Reconstruct a whole predecessor and owner-normalized state from sources.

    The supplied prior only participates in ordinary identity/audit matching;
    none of its transaction fields supplies a reconstructed projection field.
    """
    rows = [row for row in report['transactions'] if row.get('extraction_id') == key]
    raw = [row for row in raw_rows if row.get('extraction_id') == key]
    if len(rows) != 1 or len(raw) != 1 or raw[0].get('owner') != 'Self':
        raise NativeCorrectionError('White House owner correction source row is not unique')
    row = rows[0]
    inheritance = row.get('legacy_inheritance', {})
    legacy_row = inheritance.get('legacy_row', {})
    fields = ('extraction_id', 'page_number', 'row_number', 'owner', 'asset_name', 'ticker',
              'transaction_type', 'transaction_date', 'amount_low', 'amount_high', 'cells')
    if any(legacy_row.get(field) != raw[0].get(field) for field in fields):
        raise NativeCorrectionError('White House correction legacy target differs from original raw cells')
    if (row.get('owner') != 'Undisclosed' or row.get('account_title') or
            inheritance.get('account_display_correction') or inheritance.get('unresolved_field_differences') or
            inheritance.get('owner_correction') != {'field': 'owner', 'old_value': 'Self',
                'new_value': 'Undisclosed', 'rule': 'approved_default_self_to_undisclosed/v1',
                'source_rows_index': row['source_rows_index']}):
        raise NativeCorrectionError('White House owner correction has no isolated default-owner source proof')
    # A fixed review catalog cache may constrain ordinary eligibility,
    # but cannot grant identity. Independently bind the original PDF
    # and archived official directory to the previous stable person.
    person_id = _qualified_native_person(report, coverage, directory, key, source_people, prior)
    # Identity reuse follows the existing source-directory/candidate
    # rules. Prior people supply a stable identity, never transaction
    # fields; the full name/office is checked against the original.
    reconstructed = _historical_projection(report, raw[0], person_id)
    return reconstructed, {**reconstructed, 'owner': 'Undisclosed'}, row


def prove_native_account_display(report, raw_rows, coverage, directory, key, source_people, prior):
    """Prove a same-ID account suffix, with the independent default-owner fix.

    Callers first obtain the complete report through original-source _replay.
    Neither a saved correction marker nor prior/current fact fields supplies
    account text, owner scope, classification or any reconstructed fact field.
    """
    from .whitehouse_278t import GENERIC_PARSER_VERSION, validate_generic_whitehouse_report
    from .oge_reports import parse_generic_table_rows, parse_table_rows
    if report.get('parser_version') != GENERIC_PARSER_VERSION:
        raise NativeCorrectionError('White House account display needs native v5 source')
    validate_generic_whitehouse_report(report)
    inventory = [(item['page_number'], item['cells']) for item in report['source_rows']]
    conventional, _ = parse_table_rows(inventory, source_sha=report['source_sha256'], default_owner='Self')
    native_good, _, _ = parse_generic_table_rows(inventory, source_sha256=report['source_sha256'])
    rows = [row for row in report['transactions'] if row.get('extraction_id') == key]
    supplied_raw = [row for row in raw_rows if row.get('extraction_id') == key]
    raw = [row for row in conventional if row.get('extraction_id') == key]
    if (len(rows) != 1 or len(raw) != 1 or supplied_raw != raw or raw[0].get('owner') != 'Self' or
            raw[0].get('account_title') or len(raw[0].get('cells', [])) != 6 or
            'amount_low_exclusive' in raw[0]):
        raise NativeCorrectionError('White House account display legacy source row is not unique')
    row = rows[0]
    inherited = row.get('legacy_inheritance', {})
    legacy = inherited.get('legacy_row', {})
    expected = [item for item in native_good if
        item.get('source_rows_index') == row.get('source_rows_index') and
        item.get('page_number') == raw[0]['page_number'] and item.get('cells') == raw[0]['cells']]
    if len(expected) != 1:
        raise NativeCorrectionError('White House account display has no unique whole-input scope')
    source = expected[0]
    title = source.get('account_title')
    fields = ('extraction_id', 'page_number', 'row_number', 'owner', 'asset_name', 'ticker',
              'transaction_type', 'transaction_date', 'amount_low', 'amount_high', 'amount_low_exclusive', 'cells')
    correction = {'field': 'asset_name', 'rule': 'approved_source_account_title_display/v1',
        'old_value': raw[0]['asset_name'], 'new_value': f"{raw[0]['asset_name']} — {title}"}
    owner_correction = {'field': 'owner', 'old_value': 'Self', 'new_value': 'Undisclosed',
        'rule': 'approved_default_self_to_undisclosed/v1', 'source_rows_index': source['source_rows_index']}
    if (not isinstance(title, str) or not title.strip() or source.get('owner') != 'Undisclosed' or
            source.get('account_context') != {'status': 'observed', 'title': title} or source.get('reasons') or
            'amount_low_exclusive' in source or
            any(legacy.get(field) != raw[0].get(field) for field in fields) or legacy.get('account_title') or
            row.get('owner') != source['owner'] or row.get('account_title') != title or
            row.get('account_context') != source['account_context'] or row.get('reasons') or
            inherited.get('unresolved_field_differences') or inherited.get('owner_correction') != owner_correction or
            inherited.get('account_display_correction') != correction):
        raise NativeCorrectionError('White House account display differs from independent source scope')
    person_id = _qualified_native_person(report, coverage, directory, key, source_people, prior,
        correction='account display')
    historical = _historical_projection(report, raw[0], person_id)
    normalized = {**historical, 'owner': 'Undisclosed'}
    final = {**normalized, 'asset_name': correction['new_value']}
    if prior != historical and prior != normalized:
        raise NativeCorrectionError('White House account display does not reproduce complete predecessor')
    return historical, normalized, final


POLICY_VERSION = 'whitehouse-native-endnote-classification/v1'


def _mechanism_raw_rows(report):
    from .whitehouse_278t import GENERIC_PARSER_VERSION
    from .oge_candidate import _instrument
    from .oge_reports import parse_table_rows
    if report.get('parser_version') != GENERIC_PARSER_VERSION:
        return []
    raw, _ = parse_table_rows([(item['page_number'], item['cells'])
        for item in report.get('source_rows', [])], source_sha=report['source_sha256'], default_owner='Self')
    return [item for item in raw if
        bool(re.search(r'\bEndnote\b', item['asset_name'], re.I)) and
        _historical_instrument(item['asset_name'], item.get('ticker')) == 'Bond' and
        _instrument(item['asset_name'], item.get('ticker')) == 'Stock']


def native_classification_target(report, row):
    """Source-row scope, independent of a retained fact's old classification."""
    return any(item['extraction_id'] == row.get('extraction_id') for item in _mechanism_raw_rows(report))


class _RootSources(_Sources):
    def __init__(self, evidence_root, review_root, *, coverage_input=None, extraction_status_input=None):
        self.roots = {'evidence': Path(evidence_root).resolve(strict=True),
                      'review': Path(review_root).resolve(strict=True)}
        self.aliases = {}
        for canonical, supplied in [('whitehouse/coverage-current.json', coverage_input),
                                    ('whitehouse/extraction-status.json', extraction_status_input)]:
            if supplied is not None:
                path = Path(supplied).resolve(strict=True)
                if not path.is_relative_to(self.roots['review']):
                    raise NativeCorrectionError('White House selected review inventory escaped controlled root')
                self.aliases[canonical] = path.relative_to(self.roots['review']).as_posix()
        super().__init__(None, self._read_path)

    def read(self, role, path, limit=MAX_JSON_BYTES):
        if role == 'review':
            path = self.aliases.get(path, path)
        return super().read(role, path, limit)

    def _read_path(self, repo, role, relative):
        root = self.roots[role]
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise NativeCorrectionError('White House correction source path escaped controlled root')
        if path.stat().st_size > MAX_PDF_BYTES:
            raise NativeCorrectionError('White House correction source exceeds byte budget')
        return path.read_bytes()

    def exists(self, role, relative):
        _safe(relative, r'[A-Za-z0-9_./-]+')
        root = self.roots[role]
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise NativeCorrectionError('White House correction source path escaped controlled root')
        return path.exists()


class NativeClassificationSources:
    """An offline accessor to replayed originals, never a correction receipt."""
    def __init__(self, sources=None, *, evidence_ref='evidence', review_ref='review'):
        self._sources = sources
        self._reports = {}
        self._legacy_documents = set()
        self._mechanism_ids = {}
        self._evidence_ref, self._review_ref = evidence_ref, review_ref

    def _load(self, document, url, *, allow_missing_legacy=False, require_qualified_document=True):
        if self._sources is None:
            raise NativeCorrectionError('White House classification has no original source accessor')
        if document not in self._reports:
            if len(self._reports) >= MAX_DOCUMENTS:
                raise NativeCorrectionError('White House classification document count exceeds budget')
            context = _replay(self._sources, self._evidence_ref, self._review_ref, document, url,
                allow_missing_legacy=allow_missing_legacy,
                require_qualified_document=require_qualified_document)
            self._reports[document] = context
            self._mechanism_ids[document] = {item['extraction_id'] for item in _mechanism_raw_rows(context[0])}
            if context[0].get('legacy_source_binding'):
                self._legacy_documents.add(document)
        return self._reports[document]

    def includes_row(self, report, row):
        context = self._reports.get(report.get('document_id'))
        return bool(context and report.get('document_id') in self._legacy_documents and
                    report == context[0] and row.get('extraction_id') in self._mechanism_ids[report['document_id']] and
                    sum(item == row for item in context[0]['transactions']) == 1)

    def provenance(self):
        dependencies = [] if self._sources is None else sorted(
            (role, path, hashlib.sha256(raw).hexdigest())
            for (role, path), raw in self._sources.cache.items())
        return {'native_endnote_classification_policy_version': POLICY_VERSION,
                'native_endnote_classification_dependency_sha256': hashlib.sha256(
                    json.dumps(dependencies, separators=(',', ':')).encode()).hexdigest()}

    def canonical_legacy_digest(self, report):
        """Normalize two locator fields only after full original-source replay."""
        context = self._reports.get(report.get('document_id'))
        if not context or report.get('document_id') not in self._legacy_documents or report != context[0]:
            return None
        from .whitehouse_278t import PARSER_VERSION
        path = (f"whitehouse/extractions/{report['document_id'][7:]}/{report['source_sha256']}/"
                f"{PARSER_VERSION.replace('/', '-')}.json")
        legacy = dict(self._sources.obj(self._review_ref, path))
        legacy.pop('document_id', None)
        legacy.pop('source_url', None)
        return hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(',', ':'),
                                         ensure_ascii=False).encode()).hexdigest()


def prepare_native_classification_sources(extractions, *, evidence_root=None, review_root=None,
                                          coverage_input=None, extraction_status_input=None,
                                          previous_transactions=()):
    """Replay selected native sources before any cached candidate fast return.

    Dependencies come from source rows, so a Stock repeat uses the same set as
    its Bond predecessor. No discovery, OCR, model or network operation occurs.
    """
    from .whitehouse_278t import GENERIC_PARSER_VERSION
    def endnote(value):
        return isinstance(value, str) and bool(re.search(r'\bEndnote\b', value, re.I))

    def row_hint(row):
        legacy = row.get('legacy_inheritance', {}).get('legacy_row', {})
        return any(endnote(value) for value in [row.get('asset_name'), legacy.get('asset_name'),
                   *row.get('cells', []), *legacy.get('cells', [])])

    def hinted(report):
        # Hints only select an original to replay. No one mutable cache field
        # can declare this policy inapplicable and thereby authorize reuse.
        rows = [*report.get('source_rows', []), *report.get('transactions', []),
                *report.get('quarantined', [])]
        for row in rows:
            if row_hint(row):
                return True
        return any(fact.get('source_id') == 'oge' and
            fact.get('source_url') == report.get('source_url') and
            fact.get('filing_id') == report.get('document_id') and endnote(fact.get('asset_name'))
            for fact in previous_transactions)

    targets = [report for report in extractions
        if report.get('parser_version') == GENERIC_PARSER_VERSION and hinted(report)]
    if not targets:
        return NativeClassificationSources()
    source_ids = {report['document_id']: {row['extraction_id'] for row in _mechanism_raw_rows(report)}
                  for report in targets}
    if evidence_root is None or review_root is None:
        from .whitehouse_278t import validate_generic_whitehouse_report
        # An unbound fresh source may retain the established pure route, but
        # only a structurally valid fresh inventory can establish that route.
        # Erasing the raw inventory must not turn inherited IDs into fresh IDs.
        for report in targets:
            validate_generic_whitehouse_report(report)
            source_ids[report['document_id']].update(
                row.get('legacy_inheritance', {}).get('legacy_row', {}).get('extraction_id')
                for row in report.get('transactions', []))
            binding = report.get('legacy_source_binding', {})
            source_ids[report['document_id']].update(binding.get('inherited_ids', []))
            source_ids[report['document_id']].update(binding.get('unmatched_ids', []))
            for fact in previous_transactions:
                if not (fact.get('source_id') == 'oge' and
                        fact.get('source_url') == report.get('source_url') and
                        fact.get('filing_id') == report.get('document_id') and
                        (endnote(fact.get('asset_name')) or fact.get('id') in source_ids[report['document_id']] or
                         any(row.get('extraction_id') == fact.get('id') and row_hint(row)
                             for row in report['transactions']))):
                    continue
                rows = [row for row in report['transactions'] if row.get('extraction_id') == fact.get('id')]
                # This is a positive compatibility exception for an unchanged
                # fresh native fact. Absence of an old ID or an entire erased
                # inventory cannot establish this exception.
                if (binding or fact.get('id') in source_ids[report['document_id']] or
                        len(rows) != 1 or rows[0].get('legacy_inheritance') or
                        rows[0].get('reasons') or report.get('document_reasons') or
                        fact != _current_projection(report, rows[0], fact.get('person_id'))):
                    raise NativeCorrectionError('White House native classification needs controlled evidence and review roots')
        return NativeClassificationSources()
    if len(targets) > MAX_DOCUMENTS:
        raise NativeCorrectionError('White House classification document count exceeds budget')
    sources = _RootSources(evidence_root, review_root, coverage_input=coverage_input,
        extraction_status_input=extraction_status_input)
    result = NativeClassificationSources(sources)
    seen = set()
    for supplied in targets:
        document, url = supplied['document_id'], supplied['source_url']
        _safe(url, r'https://www\.whitehouse\.gov/wp-content/uploads/20\d{2}/\d{2}/[^?#]+\.pdf')
        if document != 'wh-url:' + hashlib.sha256(url.encode()).hexdigest()[:24] or document in seen:
            raise NativeCorrectionError('White House classification source document is invalid or repeated')
        seen.add(document)
        replay = result._load(document, url, require_qualified_document=False, allow_missing_legacy=True)
        if supplied != replay[0]:
            raise NativeCorrectionError('White House classification selected input differs from source replay')
        if document not in result._legacy_documents and any(
                fact.get('source_id') == 'oge' and fact.get('source_url') == url and
                fact.get('filing_id') == document and
                fact.get('id') in result._mechanism_ids[document]
                for fact in previous_transactions):
            raise NativeCorrectionError('White House classification predecessor requires original legacy input')
    return result


def reconcile_native_classification(report, row, prior, *, person_id, source_people, sources):
    """Only Endnote-caused Bond -> Stock, optionally with the proved owner fix."""
    if not sources.includes_row(report, row):
        return None
    original, raw, coverage, directory = sources._reports[report['document_id']]
    historical, normalized, fresh_row = prove_native_row(
        original, raw, coverage, directory, row['extraction_id'], source_people, prior)
    if 'amount_low_exclusive' in fresh_row:
        raise NativeCorrectionError('White House classification does not migrate open-amount fields')
    if historical['person_id'] != person_id:
        raise NativeCorrectionError('White House classification source person differs')
    final = _current_projection(original, fresh_row, person_id)
    if prior == final:
        return None
    if prior != historical and prior != normalized:
        raise NativeCorrectionError('White House classification does not reproduce complete predecessor')
    corrections = [{'field': field, 'old_value': prior[field], 'new_value': final[field],
        'rule': POLICY_VERSION, 'formal_id': prior['id'], 'source_sha256': report['source_sha256'],
        'page_number': row['page_number'], 'cells': row['cells']}
        for field in sorted(final) if prior[field] != final[field]]
    return {'fact': final, 'corrections': corrections}
