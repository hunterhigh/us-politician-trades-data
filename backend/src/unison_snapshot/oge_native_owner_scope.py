"""Bounded original-source proof for ordinary native OGE owner migration.

This module never calls the candidate receiver. Selection bindings identify raw
inputs; only authenticated replay and a closed page inventory authorize changes.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .codec import encode
from .oge import OgeCatalogError
from .oge_reports import PARSER_VERSION, GENERIC_PARSER_VERSION
from .whitehouse_native_correction import _historical_projection

NULLABLE = {'ticker', 'ticker_mapping_basis', 'option_type', 'strike_price',
            'expiration_date', 'position_effect_basis'}
MAX_SOURCE_ROWS = 4096
MAX_SCOPE_COMPARISONS = 200_000
MAX_PAGES = 32


class InventoryError(ValueError):
    """The finite absence-of-owner proof cannot explain the complete source."""


def _legacy_replay_matches(actual: dict, saved: dict) -> bool:
    """Old v2 caches may omit additive cells; present evidence stays exact.

    This does not add the omitted evidence to the saved cache or authorize an
    owner alias. The separate source inventory proof and row binding still
    require their actual cells.
    """
    if saved.get('parser_version') != PARSER_VERSION:
        return encode(actual) == encode(saved)
    replay = deepcopy(actual)
    if 'source_rows' not in saved:
        replay.pop('source_rows', None)
    current, previous = replay.get('transactions'), saved.get('transactions')
    if isinstance(current, list) and isinstance(previous, list) and len(current) == len(previous):
        for row, old in zip(current, previous, strict=True):
            if isinstance(row, dict) and isinstance(old, dict) and 'cells' not in old:
                row.pop('cells', None)
    return encode(replay) == encode(saved)


def _represented(projection, prior):
    """Allow only omission of known, source-proved nulls in public shards."""
    if set(prior) - set(projection):
        return None
    if any((key not in prior and (key not in NULLABLE or value is not None) or (key in prior and prior[key] != value) for key, value in projection.items())):
        return None
    return {key: value for key, value in projection.items() if key in prior}


def _explained_row(cells, number, sha):
    """Positive source roles for every nonempty cell, not an owner blacklist."""
    from .oge_reports import classify_table_structure, parse_table_rows, _ROW_NUMBER
    structure = classify_table_structure(cells)
    kind = structure.get('kind') if structure else None
    if kind == 'blank':
        return True
    if kind == 'header':
        partial = not any(cells[1:4])
        return not cells[0] if partial else cells[0].casefold() in {'', '#', 'no', 'no.'}
    if kind in {'owner_heading', 'owner_account_heading', 'account_heading', 'blank_notice'}:
        return not any((cells[index] for index in (0, 2, 3, 4, 5)))
    if kind is not None:
        return False
    if not _ROW_NUMBER.fullmatch(cells[0]) or not cells[1]:
        return False
    good, bad = parse_table_rows([(number, cells)], source_sha=sha, default_owner='Undisclosed', allow_exchange=True)
    return len(good + bad) == 1 and any(((good + bad)[0].get('transaction_type'), (good + bad)[0].get('transaction_date'), (good + bad)[0].get('amount_low') is not None))


def _source_scope(pdf, metadata, record, *, max_source_rows=MAX_SOURCE_ROWS,
                  max_scope_comparisons=MAX_SCOPE_COMPARISONS):
    """Close every page before inferring the absence of explicit owner scope.

    This finite route explains known administrative pages and visible ruled
    six-column tables. Unknown external text is not an owner keyword problem:
    it means this migration has no absence proof, while ordinary rows remain
    on their existing candidate path.
    """
    from .native_source_visibility import inspect_standard_visible_page, CATALOG_KEYS
    from .oge_278t_page_roles import observe_native_page_role
    from .oge_reports import _compact, _looks_like_transaction_table
    if set(pdf.doc.catalog) - CATALOG_KEYS:
        raise InventoryError('Direct OGE owner source has unexplained document objects')
    context = {'schema_version': 'oge-278t-cover-identity-context/v1', 'source_sha256': metadata['sha256'], **{field: record[field] for field in ('filer_name', 'agency', 'position_title')}}
    inventory = []
    for number, page in enumerate(pdf.pages, 1):
        role = observe_native_page_role(page, number, source_sha256=metadata['sha256'], cover_identity_context=context)
        if role['status'] == 'non_table' and role.get('native_objects_fully_accounted') is True:
            continue
        visible = inspect_standard_visible_page(page)
        if visible['status'] != 'observed':
            raise InventoryError('Direct OGE owner source visibility/object scope is unknown: ' + str(visible['reason']))
        tables = []
        page_cells = []
        for table in page.find_tables():
            physical_rows = table.rows
            if len(inventory) + len(physical_rows) > max_source_rows:
                raise InventoryError('Direct OGE owner source row count exceeds budget')
            rows = [[_compact(cell) for cell in row] for row in table.extract() if isinstance(row, list)]
            signature = ' '.join((cell.lower() for row in rows for cell in row))
            has_headers = all((value in signature for value in ('description', 'type', 'date', 'amount', 'notification')))
            if not rows or any((len(row) != 6 for row in rows)) or (not (has_headers or _looks_like_transaction_table(rows))):
                continue
            if len(physical_rows) != len(rows) or any((len(row.cells) != 6 or any((cell is None for cell in row.cells)) for row in physical_rows)):
                raise InventoryError('Direct OGE owner source table has unsupported merged or missing cells')
            if any((not _explained_row(row, number, metadata['sha256']) for row in rows)):
                raise InventoryError('Direct OGE owner source has unexplained nonfinancial table content')
            tables.append(table)
            inventory.extend(({'page_number': number, 'cells': row} for row in rows))
            page_cells.extend((cell for row in physical_rows for cell in row.cells))
        if not tables:
            raise InventoryError('Direct OGE owner source page has no complete administrative/table role')
        words = page.extract_words(return_chars=True)
        if len(words) * (len(tables) + len(page_cells)) > max_scope_comparisons:
            raise InventoryError('Direct OGE owner source geometry comparison budget exceeded')

        def char_key(char):
            return (char['text'], char['x0'], char['x1'], char['top'], char['bottom'])
        if Counter((char_key(char) for word in words for char in word['chars'] if not char['text'].isspace())) != Counter((char_key(char) for char in page.chars if not char['text'].isspace())):
            raise InventoryError('Direct OGE owner source words omit or repeat native characters')
        outside = [word for word in words if not any((table.bbox[0] <= word['x0'] < word['x1'] <= table.bbox[2] and table.bbox[1] <= word['top'] < word['bottom'] <= table.bbox[3] for table in tables))]
        outside_ids = {id(word) for word in outside}
        for word in words:
            if id(word) in outside_ids:
                continue
            cells = [cell for cell in page_cells if cell[0] <= word['x0'] < word['x1'] <= cell[2] and cell[1] <= word['top'] < word['bottom'] <= cell[3]]
            if len(cells) != 1:
                raise InventoryError('Direct OGE owner source word crosses or ambiguously binds table cells')
        if outside and (not (len(outside) == 1 and outside[0]['text'] == 'Transactions' and (outside[0]['bottom'] < min((table.bbox[1] for table in tables))))):
            raise InventoryError('Direct OGE owner source has unexplained words outside transaction tables')
    return inventory


def _identity_key(record: dict) -> tuple[str, str, str]:
    values = []
    for field in ('filer_name', 'agency', 'position_title'):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OgeCatalogError(f'OGE identity has invalid {field}')
        values.append(' '.join(value.casefold().replace(',', ' ').split()))
    return tuple(values)


def _person_id(key: tuple[str, str, str]) -> str:
    return 'oge:' + hashlib.sha256('|'.join(key).encode()).hexdigest()[:16]


def _instrument(asset_name: str, ticker: str | None) -> str:
    value = asset_name.casefold()
    if ' etf' in value or value.endswith('etf'):
        return 'ETF'
    if 'mutual fund' in value or 'fund' in value:
        return 'Mutual Fund'
    if 'bond' in value or 'treasury' in value or re.search('\\bnotes?\\b', value):
        return 'Bond'
    if ticker or 'stock' in value or 'inc.' in value or ('corp.' in value):
        return 'Stock'
    return 'Unspecified'


def _transaction_projection(extraction: dict, row: dict, person_id: str) -> dict:
    """One formal projection for first publication and strict source-bound repeat."""
    from .oge_transaction_adapter import open_amount_qualified
    asset_name, ticker = (row['asset_name'], row['ticker'])
    is_open = open_amount_qualified(row, extraction['parser_version'])
    return {'id': row['extraction_id'], 'filing_id': extraction['document_id'], 'person_id': person_id, 'owner': row['owner'], 'asset_name': f"{asset_name} — {row['account_title']}" if row.get('account_title') else asset_name, 'ticker': ticker, 'ticker_mapping_basis': 'filing_explicit' if ticker else None, 'instrument_type': _instrument(asset_name, ticker), 'option_type': None, 'strike_price': None, 'expiration_date': None, 'transaction_type': row['transaction_type'], 'transaction_date': row['transaction_date'], 'filed_at': f"{extraction['filed_at']}T00:00:00Z", 'amount_low': row['amount_low'], 'amount_high': row['amount_high'], **({'amount_low_exclusive': True} if is_open else {}), 'position_effect': 'unknown', 'position_effect_basis': None, 'source_id': 'oge', 'source': 'U.S. Office of Government Ethics', 'source_url': extraction['source_url'], 'verification_status': 'official_matched'}


def _legacy_default_owner_evidence(legacy: dict, old: dict) -> dict | None:
    """Distinguish the v2 default from an explicit owner using its whole input."""
    from .oge_reports import parse_table_rows, _ACCOUNT_HEADING
    from .oge_transaction_adapter import _row_key, _cells
    if legacy.get('parser_version') != PARSER_VERSION or old.get('owner') != 'Self' or old.get('owner_observation_reference') or old.get('owner_disclosure_basis'):
        return None
    inventory = legacy.get('source_rows')
    if not isinstance(inventory, list):
        return None
    physical = [(row['page_number'], _cells(row['cells'])) for row in inventory]
    locators = [index for index, (page, cells) in enumerate(physical) if _row_key({'page_number': page, 'cells': cells}) == _row_key(old)]
    if len(locators) != 1:
        return None
    for _, cells in physical[:locators[0]]:
        if len(cells) == 6 and cells[1] and (not any((cells[2], cells[3], cells[5]))) and (not _ACCOUNT_HEADING.fullmatch(cells[1])) and (not re.fullmatch("(?:filer|self|spouse|dependent child(?:ren)?)(?:'s)? (?:assets|accounts?)", cells[1], re.I)) and ('intentionally' not in cells[1].casefold()):
            return None
    replays = []
    for default in ('Self', 'Undisclosed'):
        good, bad = parse_table_rows(physical, source_sha=legacy['source_sha256'], default_owner=default)
        matches = [row for row in good + bad if _row_key(row) == _row_key(old)]
        if len(matches) != 1:
            return None
        replays.append(matches[0])
    fields = ('asset_name', 'ticker', 'transaction_type', 'transaction_date', 'amount_low', 'amount_high')
    if replays[0]['owner'] != 'Self' or replays[1]['owner'] != 'Undisclosed' or any((replays[0].get(field) != old.get(field) for field in fields)):
        return None
    return {'source_sha256': legacy['source_sha256'], 'document_id': legacy['document_id'], 'legacy_parser_version': PARSER_VERSION, 'source_rows_index': locators[0], 'page_number': old['page_number'], 'cells': deepcopy(old['cells']), 'source_rows_sha256': hashlib.sha256(json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'basis': 'whole_saved_source_input_replayed_with_two_defaults_no_explicit_owner'}




def source_interpretation_binding(raw_extraction: dict) -> dict:
    """Select a complete raw interpretation without asserting its truth."""
    return {
        'schema_version': 'oge-source-interpretation-binding/v1',
        'document_id': raw_extraction['document_id'],
        'source_sha256': raw_extraction['source_sha256'],
        'parser_version': raw_extraction['parser_version'],
        'extraction_canonical_sha256': hashlib.sha256(encode(raw_extraction)).hexdigest(),
    }


def _catalog_identity(record):
    # Catalog row position is not source identity: historical collapse may move it.
    return {key: value for key, value in record.items() if key != 'catalog_index'}


class NativeOwnerScope:
    """Replay-owned context; callers cannot supply an observed scope assertion.

    decision() recognizes mechanism membership separately from complete matching.
    alias() proves an existing owner-only successor before inheritance attempts
    to bind formal IDs, including when the absence proof itself remains unknown.
    """

    def __init__(self, extraction, legacy, record, *, evidence_root=None,
                 source_extraction=None):
        self.native = deepcopy(extraction)
        self.source = deepcopy(extraction if source_extraction is None else source_extraction)
        self.legacy = deepcopy(legacy)
        self.record = deepcopy(record)
        self.status, self.reason = 'unknown', 'original_source_unavailable'
        self._source_authenticated = False
        self.binding = {
            'policy_version': 'oge-native-owner-scope/v1',
            'native': source_interpretation_binding(self.source),
            'legacy': source_interpretation_binding(self.legacy) if self.legacy is not None else None,
            'catalog_identity_sha256': hashlib.sha256(encode(_catalog_identity(record))).hexdigest(),
        }
        self._person = _person_id(_identity_key(record))
        self._default_rows = {}
        if self.source.get('parser_version') != GENERIC_PARSER_VERSION:
            self.reason = 'ordinary_native_owner_scope_not_applicable'
        elif evidence_root is not None:
            self._authenticate(Path(evidence_root))
        # Authenticate whole caches before attempting their predecessor hints.
        # With no original root, malformed hints confer no migration authority.
        if self.legacy is not None:
            try:
                for row in self.legacy.get('transactions', []):
                    proof = _legacy_default_owner_evidence(self.legacy, row)
                    if proof is not None:
                        self._default_rows.setdefault(row['extraction_id'], []).append((row, proof))
            except (ValueError, KeyError, TypeError, AttributeError):
                self._default_rows = {}

    def _authenticate(self, root):
        from .oge_reports import (_read_archived_source, parse_archived_pdf,
                                  selected_parser_version)
        import pdfplumber

        document, sha = self.source.get('document_id'), self.source.get('source_sha256')
        if (not isinstance(document, str) or not re.fullmatch(r'[0-9a-f]{32}', document)
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            raise OgeCatalogError('OGE owner source canonical identity is invalid')
        relative = f'oge/reports/{document}/{sha}.pdf'
        metadata_path = root / (relative[:-4] + '.json')
        try:
            from .oge import _official_url
            from .oge_reports import _validate_direct_record
            _validate_direct_record(self.record)
            _official_url(self.record['document_url'])
            if any(self.native.get(key) != self.source.get(key) for key in (
                    'document_id', 'source_id', 'source_sha256', 'source_url', 'parser_version',
                    'catalog_filer_name', 'agency', 'position_title')):
                raise OgeCatalogError('OGE owner effective input differs from raw source identity')
            metadata, path, actual = _read_archived_source(root, metadata_path)
            if (actual != sha or metadata['archive_path'] != relative
                    or path.resolve() != (root / relative).resolve()
                    or metadata['document_id'] != document
                    or self.record['source_document_id'] != document
                    or metadata['document_url'] != self.source['source_url']
                    or self.record['document_url'] != self.source['source_url']
                    or any(metadata.get(key) != self.record.get(key) for key in
                           ('filer_name', 'agency', 'position_title', 'catalog_added_date',
                            'amended_label', 'pending_final_oge_disposition'))):
                raise OgeCatalogError('OGE owner source authentication differs')
            if (selected_parser_version(metadata, path, sha) != PARSER_VERSION
                    or selected_parser_version(metadata, path, sha, generic=True) != GENERIC_PARSER_VERSION):
                raise OgeCatalogError('OGE owner source selected parser differs')
            actual_legacy = parse_archived_pdf(root, metadata_path)
            actual_native = parse_archived_pdf(root, metadata_path, generic=True)
            if (encode(actual_native) != encode(self.source) or self.legacy is not None
                    and not _legacy_replay_matches(actual_legacy, self.legacy)):
                raise OgeCatalogError('OGE owner source whole-cache replay differs')
            self._source_authenticated = True
        except OgeCatalogError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise OgeCatalogError(f'OGE owner source authentication failed: {error}') from error
        try:
            with pdfplumber.open(path) as pdf:
                if not 1 <= len(pdf.pages) <= MAX_PAGES:
                    raise InventoryError('Direct OGE owner source page count exceeds budget')
                inventory = _source_scope(pdf, metadata, self.record,
                    max_source_rows=MAX_SOURCE_ROWS, max_scope_comparisons=MAX_SCOPE_COMPARISONS)
            if (inventory != actual_legacy['source_rows']
                    or inventory != actual_native['source_rows']):
                raise InventoryError('Direct OGE owner source inventory is not closed')
        except (InventoryError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.reason = str(error)
        else:
            self.status, self.reason = 'observed', None

    def decision(self, fact: dict) -> dict:
        """Return owner-only disposition, never infer a full fact from its ID.

        `matched` requires a unique complete historical/current classifier
        projection, allowing omission only of known nullable fields. Unsupported
        old objects remain mechanism members but can only be deferred.
        """
        empty = {'status': 'not_applicable', 'matched': False}
        if (self.legacy is None or fact.get('source_id') != 'oge'
                or fact.get('filing_id') != self.legacy.get('document_id')
                or fact.get('source_url') != self.legacy.get('source_url')):
            return empty
        rows = self._default_rows.get(fact.get('id'), [])
        if not rows:
            return empty
        if len(rows) != 1 or not self.legacy.get('filed_at'):
            # An existing public date is not evidence for a missing raw date.
            return {'status': 'deferred', 'matched': False}
        matches = []
        for row, proof in rows:
            # Frozen H predates account display/open-amount migrations.
            if row.get('account_title') or row.get('amount_low_exclusive'):
                continue
            historical = _historical_projection(self.legacy, row, self._person)
            modern = {**historical, 'instrument_type': _instrument(row['asset_name'], row['ticker'])}
            for projection in (historical, modern):
                for owner in ('Self', 'Undisclosed'):
                    represented = _represented({**projection, 'owner': owner}, fact)
                    if represented is not None:
                        match = (represented, row, proof)
                        if match not in matches:
                            matches.append(match)
        if len(matches) != 1:
            return {'status': 'deferred', 'matched': False}
        represented, row, proof = matches[0]
        state = ('alias' if represented['owner'] == 'Undisclosed' else
                 'approved' if self.status == 'observed' else 'deferred')
        return {'status': state, 'matched': True, 'row': deepcopy(row),
                'source_reference': deepcopy(proof),
                'self_fact': {**represented, 'owner': 'Self'},
                'owner_fact': {**represented, 'owner': 'Undisclosed'}}

    def alias(self, fact: dict) -> dict | None:
        """Return a complete Self predecessor for a proved Howner alias."""
        decision = self.decision(fact)
        return decision['self_fact'] if decision['status'] == 'alias' else None

    def owner_decisions(self, actualfacts) -> dict:
        """Owner mechanism intersection; other qualifications stay independent."""
        mechanism, current = set(), set()
        for fact in actualfacts:
            decision = self.decision(fact)
            if decision['status'] == 'not_applicable':
                continue
            mechanism.add(fact['id'])
            if (decision['matched'] and self.status == 'observed'
                    and fact.get('owner') == 'Undisclosed'):
                current.add(fact['id'])
        return {'mechanism_ids': sorted(mechanism), 'current_ids': sorted(current),
                'published_ids': sorted(mechanism), 'deferred_ids': sorted(mechanism - current),
                'status': self.status, 'reason': self.reason}

    def ordinary_legacy_current(self, fact: dict) -> bool:
        """Prove one retained explicit-owner fact from two complete raw replays.

        Both known classifier epochs are reconstructed from the actual legacy
        row. A unique qualified generic row must corroborate that exact physical
        row and every financial field. This is identity preservation, not a
        migration of the classifier or a saved-observation qualification path.
        """
        from .oge_transaction_adapter import _row_key
        if (not self._source_authenticated or self.legacy is None
                or not self.legacy.get('filed_at') or not self.source.get('filed_at')
                or not self.native.get('filed_at')
                or self.source.get('parser_version') != GENERIC_PARSER_VERSION
                or self.native.get('parser_version') != GENERIC_PARSER_VERSION
                or fact.get('id') in self._default_rows):
            return False
        old_rows = [row for row in self.legacy.get('transactions', [])
                    if row.get('extraction_id') == fact.get('id')]
        if len(old_rows) != 1:
            return False
        old = old_rows[0]
        if old.get('account_title') or old.get('amount_low_exclusive'):
            return False
        key = _row_key(old)
        if (sum(_row_key(row) == key for row in self.legacy.get('source_rows', [])) != 1
                or sum(_row_key(row) == key for row in self.source.get('source_rows', [])) != 1
                or sum(_row_key(row) == key for row in self.legacy.get('transactions', [])
                       + self.legacy.get('quarantined', [])) != 1):
            return False
        native_rows = [row for row in self.source.get('transactions', [])
                       + self.source.get('quarantined', []) if _row_key(row) == key]
        if len(native_rows) != 1 or native_rows[0] not in self.source.get('transactions', []):
            return False
        native = native_rows[0]
        fields = ('owner', 'asset_name', 'ticker', 'transaction_type', 'transaction_date',
                  'amount_low', 'amount_high', 'amount_low_exclusive', 'account_title')
        if (native.get('owner') not in {'Self', 'Spouse', 'Dependent Child'}
                or native.get('reasons')
                or any(old.get(field) != native.get(field) for field in fields)):
            return False
        historical = _historical_projection(self.legacy, old, self._person)
        modern = {**historical, 'instrument_type': _instrument(old['asset_name'], old['ticker'])}
        matches = []
        for projection in (historical, modern):
            represented = _represented(projection, fact)
            if represented is not None and represented not in matches:
                matches.append(represented)
        return len(matches) == 1

    def ordinary_native_view(self, actualfacts) -> dict:
        """Recompute full current qualification only for the ordinary final lane."""
        facts = list(actualfacts)
        view = self.owner_decisions(facts)
        mechanism, current = set(view['mechanism_ids']), set(view['current_ids'])
        for fact in facts:
            if fact.get('id') in mechanism:
                continue
            if self.ordinary_legacy_current(fact):
                current.add(fact['id'])
                continue
            if not self.source.get('filed_at') or not self.native.get('filed_at'):
                continue
            for row in self.native.get('transactions', []):
                projection = _transaction_projection(self.native, row, self._person)
                if (_represented(projection, fact) is not None
                        and (row['owner'] != 'Undisclosed' or self.status == 'observed')):
                    current.add(fact['id'])
                    break
        published = {fact['id'] for fact in facts}
        return {**view, 'current_ids': sorted(current), 'published_ids': sorted(published),
                'deferred_ids': sorted(published - current)}


def prepare_native_owner_scope(extraction, legacy, record, *, evidence_root=None,
                               source_extraction=None) -> NativeOwnerScope:
    return NativeOwnerScope(extraction, legacy, record, evidence_root=evidence_root,
                            source_extraction=source_extraction)
