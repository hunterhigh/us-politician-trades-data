"""Verify one legacy native six-column owner correction from fixed Git sources.

Only the historical default Self -> Undisclosed is supported. Receipts cannot
authorize facts, and historical instrument classification is reconstructed,
never corrected here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .migration_inventory import InventoryError, _differences, git_object

MAX_DOCUMENTS = 12
MAX_PAGES = 32
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_BOARD_BYTES = 64 * 1024 * 1024


def _safe(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise InventoryError('White House owner correction source identity/path is invalid')
    return value


def _historical_instrument(asset, ticker):
    """Frozen pre-f09b37958 oge_candidate._instrument; Endnote matched note.

    This reproduces an old value from source text. It must not be used to
    classify new facts or to authorize Bond -> Stock migration.
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


class _Sources:
    def __init__(self, repo, read_object):
        self.repo, self.read_object, self.total, self.cache = repo, read_object, 0, {}

    def read(self, commit, path, limit=MAX_JSON_BYTES):
        _safe(path, r'[A-Za-z0-9_./-]+')
        if '..' in path.split('/') or path.startswith('/'):
            raise InventoryError('White House correction source path escaped archive')
        key = (commit, path)
        if key not in self.cache:
            # The existing helper reads a whole Git blob. Bound its allocation
            # first in real Git, then check injected readers as well.
            if self.read_object is git_object:
                size = subprocess.run(['git', '-C', str(self.repo), 'cat-file', '-s', f'{commit}:{path}'],
                                      capture_output=True, check=False,
                                      env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
                if size.returncode or not size.stdout.strip().isdigit() or int(size.stdout) > limit:
                    raise InventoryError('White House correction source is missing or exceeds byte budget')
            raw = self.read_object(self.repo, commit, path)
            self.total += len(raw)
            if len(raw) > limit or self.total > MAX_TOTAL_BYTES:
                raise InventoryError('White House correction source exceeds byte budget')
            self.cache[key] = raw
        raw = self.cache[key]
        if len(raw) > limit:
            raise InventoryError('White House correction source exceeds byte budget')
        return raw

    def obj(self, commit, path):
        value = json.loads(self.read(commit, path))
        if not isinstance(value, dict):
            raise InventoryError('White House correction source must be an object')
        return value


def _replay(sources, evidence, review, document, url):
    from ..whitehouse_278t import (GENERIC_PARSER_VERSION, PARSER_VERSION,
        parse_generic_whitehouse_278t_pdf, validate_generic_whitehouse_report)
    from ..whitehouse_disclosures import parse_disclosure_index
    from ..oge_reports import parse_table_rows

    coverage = sources.obj(review, 'whitehouse/coverage-current.json')
    status = sources.obj(review, 'whitehouse/extraction-status.json')
    matches = [row for row in coverage.get('reports', []) if row.get('document_id') == document]
    if (coverage.get('schema_version') != 'whitehouse-public-coverage/v1' or
            coverage.get('source_id') != 'whitehouse_public_disclosures' or len(matches) != 1 or
            status.get('schema_version') != 'whitehouse-public-extraction-status/v1' or
            status.get('source_id') != 'whitehouse_public_disclosures'):
        raise InventoryError('White House correction selected review inventory differs')
    selected = matches[0]
    versions = selected.get('archive_sha256_versions')
    if not isinstance(versions, list) or len(versions) != 1:
        raise InventoryError('White House correction source version is ambiguous')
    sha = _safe(versions[0], r'[0-9a-f]{64}')
    folder = f'whitehouse/extractions/{document[7:]}/{sha}'
    current_path = f'{folder}/{GENERIC_PARSER_VERSION.replace("/", "-")}.json'
    if (selected.get('document_url') != url or selected.get('document_type_from_label') != '278t' or
            selected.get('extraction_parser_version') != GENERIC_PARSER_VERSION or
            selected.get('extraction_path') != current_path or
            selected.get('review_state') not in {'extracted_review_only', 'extracted_with_issues'}):
        raise InventoryError('White House correction requires selected native v5 input')
    saved = sources.obj(review, current_path)
    legacy = sources.obj(review, f'{folder}/{PARSER_VERSION.replace("/", "-")}.json')
    pdf_path = f'whitehouse/disclosures/reports/{document[7:]}/{sha}.pdf'
    meta = sources.obj(evidence, pdf_path[:-4] + '.json')
    raw = sources.read(evidence, pdf_path, MAX_PDF_BYTES)
    if (meta.get('schema_version') != 'whitehouse-public-disclosures-pdf/v1' or
            meta.get('source_id') != 'whitehouse_public_disclosures' or
            meta.get('document_id') != document or meta.get('document_url') != url or
            meta.get('archive_path') != pdf_path or meta.get('sha256') != sha or
            meta.get('byte_length') != len(raw) or hashlib.sha256(raw).hexdigest() != sha or
            legacy.get('parser_version') != PARSER_VERSION):
        raise InventoryError('White House correction archive or legacy version differs')
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
        raise InventoryError('White House correction official directory identity differs')
    catalog_coverage = sources.obj(review, 'oge/whitehouse/coverage-current.json')
    with tempfile.TemporaryDirectory(prefix='whitehouse-owner-') as temporary:
        root = Path(temporary)
        def save(path, content):
            target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
        save(pdf_path, raw)
        import pdfplumber
        with pdfplumber.open(root/pdf_path) as pdf:
            if not 1 <= len(pdf.pages) <= MAX_PAGES:
                raise InventoryError('White House correction source page count exceeds budget')
        replay = parse_generic_whitehouse_278t_pdf(root/pdf_path, source_url=url, source_sha256=sha,
            document_id=document, filer_name=entries[0]['filer_name_from_label'],
            amended_label=entries[0]['link_label'], legacy_extraction=legacy)
    if replay != saved or replay.get('document_reasons'):
        raise InventoryError('White House correction native extraction differs from complete source replay')
    validate_generic_whitehouse_report(replay)
    inventory = [(row['page_number'], row['cells']) for row in replay['source_rows']]
    old_rows, bad = parse_table_rows(inventory, source_sha=sha, default_owner='Self')
    if len(old_rows) + len(bad) > 256:
        raise InventoryError('White House correction legacy row count exceeds budget')
    # Unrelated quarantined rows remain quarantined. The target must have a
    # unique raw replay and the normal interpreter's whole-input scope proof;
    # a report-wide perfect-recognition requirement would discard valid rows.
    return replay, old_rows, catalog_coverage, index


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
    from ..whitehouse_278t_audit import audit_whitehouse_278t, _source_bound_person_from_directory
    from ..whitehouse_disclosures import WhiteHouseDisclosureError
    from .holdings_rebinding import _board
    from .migration_inventory import _index
    sources, cache, result = _Sources(repo, read_object), {}, []
    try:
        manifest = sources.obj(main_commit, 'manifest.json')
        if manifest.get('is_demo') is not False:
            raise InventoryError('White House correction predecessor is not an official snapshot')
        board = _board(repo, main_commit, lambda repo, ref, path: sources.read(ref, path,
            MAX_BOARD_BYTES if path.startswith('board/') else MAX_JSON_BYTES))
        pinned_old = _index(board.get('transactions'), 'fixed main transactions')
        pinned_people = _index(board.get('people'), 'fixed main people')
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
            rows = [row for row in report['transactions'] if row.get('extraction_id') == key]
            raw = [row for row in raw_rows if row.get('extraction_id') == key]
            if len(rows) != 1 or len(raw) != 1 or raw[0].get('owner') != 'Self':
                raise InventoryError('White House owner correction source row is not unique')
            row = rows[0]
            inheritance = row.get('legacy_inheritance', {})
            legacy_row = inheritance.get('legacy_row', {})
            fields = ('extraction_id', 'page_number', 'row_number', 'owner', 'asset_name', 'ticker',
                      'transaction_type', 'transaction_date', 'amount_low', 'amount_high', 'cells')
            if any(legacy_row.get(field) != raw[0].get(field) for field in fields):
                raise InventoryError('White House correction legacy target differs from original raw cells')
            if (row.get('owner') != 'Undisclosed' or row.get('account_title') or
                    inheritance.get('account_display_correction') or inheritance.get('unresolved_field_differences') or
                    inheritance.get('owner_correction') != {'field': 'owner', 'old_value': 'Self',
                        'new_value': 'Undisclosed', 'rule': 'approved_default_self_to_undisclosed/v1',
                        'source_rows_index': row['source_rows_index']}):
                raise InventoryError('White House owner correction has no isolated default-owner source proof')
            checked = audit_whitehouse_278t([report], coverage,
                {'meta': {'is_demo': False}, 'transactions': [prior],
                 'people': source_people}, directory_index=directory)
            decision = checked['reports'][0]
            identity = decision['matched_catalog_identity']
            # A fixed review catalog cache may constrain ordinary eligibility,
            # but cannot grant identity. Independently bind the original PDF
            # and archived official directory to the previous stable person.
            source_identity = _source_bound_person_from_directory(report, directory,
                {'people': source_people}, [], [report])
            disposition = [item for item in decision['rows'] if item['extraction_id'] == key]
            if (identity is None or source_identity is None or
                    source_identity['person_id'] != identity['person_id'] or decision['document_reasons'] or
                    len(disposition) != 1 or disposition[0]['status'] != 'eligible'):
                raise InventoryError('White House owner correction source is not qualified')
            # Identity reuse follows the existing source-directory/candidate
            # rules. Prior people supply a stable identity, never transaction
            # fields; the full name/office is checked against the original.
            reconstructed = _historical_projection(report, raw[0], identity['person_id'])
            expected = {**reconstructed, 'owner': 'Undisclosed'}
            if prior != reconstructed or replacement != expected:
                raise InventoryError('White House owner correction does not reproduce complete old and new formal objects')
            result.append({'entity': 'transactions', 'kind': 'corrected', 'old_id': key, 'new_id': key,
                'reason': 'source_identity_correction', 'evidence_url': url,
                'source_sha256': report['source_sha256'], 'changed_fields': ['owner']})
    except (OgeCatalogError, WhiteHouseDisclosureError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InventoryError(f'White House owner correction source verification failed: {exc}') from exc
    return result
