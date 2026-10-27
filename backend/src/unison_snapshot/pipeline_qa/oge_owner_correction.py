"""Prove direct native OGE's existing default-owner correction from fixed Git.

The candidate's saved receipt is never authority. This supports ordinary native
v2/v7 only, and does not migrate instruments, accounts, amounts or identities.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .migration_inventory import InventoryError, _differences, git_object
from .whitehouse_owner_correction import _fixed_board
from .house_revision_supersession import _git_read
from ..whitehouse_native_correction import (
    _Sources, NativeCorrectionError, _historical_projection,
    MAX_DOCUMENTS, MAX_PAGES,
)

DIRECT = re.compile(r'[0-9a-f]{32}')
NULLABLE = {'ticker', 'ticker_mapping_basis', 'option_type', 'strike_price',
            'expiration_date', 'position_effect_basis'}
MAX_SOURCE_ROWS = 4096
MAX_SCOPE_COMPARISONS = 200_000  # Per page; document page count is bounded too.


def _represented(projection, prior):
    """Allow only omission of known, source-proved nulls in public shards."""
    if set(prior) - set(projection):
        return None
    if any(key not in prior and (key not in NULLABLE or value is not None)
           or key in prior and prior[key] != value for key, value in projection.items()):
        return None
    return {key: value for key, value in projection.items() if key in prior}


def _empty(cutoff):
    return {'meta': {'is_demo': False, 'data_cutoff_at': cutoff}, 'people': [],
            'transactions': [], 'reported_holdings': [], 'security_market_data': [],
            'source_health': []}


def _explained_row(cells, number, sha):
    """Positive source roles for every nonempty cell, not an owner blacklist."""
    from ..oge_reports import classify_table_structure, parse_table_rows, _ROW_NUMBER
    structure = classify_table_structure(cells)
    kind = structure.get('kind') if structure else None
    if kind == 'blank':
        return True
    if kind == 'header':
        partial = not any(cells[1:4])
        return not cells[0] if partial else cells[0].casefold() in {'', '#', 'no', 'no.'}
    if kind in {'owner_heading', 'owner_account_heading', 'account_heading', 'blank_notice'}:
        return not any(cells[index] for index in (0, 2, 3, 4, 5))
    if kind is not None:
        return False  # Broad footer/marker text is not a complete content role.
    if not _ROW_NUMBER.fullmatch(cells[0]) or not cells[1]:
        return False
    good, bad = parse_table_rows([(number, cells)], source_sha=sha,
        default_owner='Undisclosed', allow_exchange=True)
    return len(good + bad) == 1 and any((
        (good + bad)[0].get('transaction_type'), (good + bad)[0].get('transaction_date'),
        (good + bad)[0].get('amount_low') is not None))


def _source_scope(pdf, metadata, record):
    """Close every page before inferring the absence of explicit owner scope.

    This finite route explains known administrative pages and visible ruled
    six-column tables. Unknown external text is not an owner keyword problem:
    it means this migration has no absence proof, while ordinary rows remain
    on their existing candidate path.
    """
    from ..native_source_visibility import (inspect_standard_visible_page, CATALOG_KEYS)
    from ..oge_278t_page_roles import observe_native_page_role
    from ..oge_reports import _compact, _looks_like_transaction_table

    if set(pdf.doc.catalog) - CATALOG_KEYS:
        raise InventoryError('Direct OGE owner source has unexplained document objects')
    context = {'schema_version': 'oge-278t-cover-identity-context/v1',
        'source_sha256': metadata['sha256'],
        **{field: record[field] for field in ('filer_name', 'agency', 'position_title')}}
    inventory = []
    for number, page in enumerate(pdf.pages, 1):
        role = observe_native_page_role(page, number, source_sha256=metadata['sha256'],
                                        cover_identity_context=context)
        if role['status'] == 'non_table' and role.get('native_objects_fully_accounted') is True:
            continue
        visible = inspect_standard_visible_page(page)
        if visible['status'] != 'observed':
            raise InventoryError('Direct OGE owner source visibility/object scope is unknown: ' + str(visible['reason']))
        tables = []
        page_cells = []
        for table in page.find_tables():
            physical_rows = table.rows
            if len(inventory) + len(physical_rows) > MAX_SOURCE_ROWS:
                raise InventoryError('Direct OGE owner source row count exceeds budget')
            rows = [[_compact(cell) for cell in row] for row in table.extract()
                    if isinstance(row, list)]
            signature = ' '.join(cell.lower() for row in rows for cell in row)
            has_headers = all(value in signature for value in
                              ('description', 'type', 'date', 'amount', 'notification'))
            if not rows or any(len(row) != 6 for row in rows) or not (
                    has_headers or _looks_like_transaction_table(rows)):
                continue
            if len(physical_rows) != len(rows) or any(
                    len(row.cells) != 6 or any(cell is None for cell in row.cells)
                    for row in physical_rows):
                raise InventoryError('Direct OGE owner source table has unsupported merged or missing cells')
            if any(not _explained_row(row, number, metadata['sha256']) for row in rows):
                raise InventoryError('Direct OGE owner source has unexplained nonfinancial table content')
            tables.append(table)
            inventory.extend({'page_number': number, 'cells': row} for row in rows)
            page_cells.extend(cell for row in physical_rows for cell in row.cells)
        if not tables:
            raise InventoryError('Direct OGE owner source page has no complete administrative/table role')
        words = page.extract_words(return_chars=True)
        if len(words) * (len(tables) + len(page_cells)) > MAX_SCOPE_COMPARISONS:
            raise InventoryError('Direct OGE owner source geometry comparison budget exceeded')
        def char_key(char):
            return (char['text'], char['x0'], char['x1'], char['top'], char['bottom'])
        if Counter(char_key(char) for word in words for char in word['chars']
                   if not char['text'].isspace()) != Counter(
                       char_key(char) for char in page.chars if not char['text'].isspace()):
            raise InventoryError('Direct OGE owner source words omit or repeat native characters')
        outside = [word for word in words if not any(
            table.bbox[0] <= word['x0'] < word['x1'] <= table.bbox[2]
            and table.bbox[1] <= word['top'] < word['bottom'] <= table.bbox[3]
            for table in tables)]
        outside_ids = {id(word) for word in outside}
        for word in words:
            if id(word) in outside_ids:
                continue
            cells = [cell for cell in page_cells
                     if cell[0] <= word['x0'] < word['x1'] <= cell[2]
                     and cell[1] <= word['top'] < word['bottom'] <= cell[3]]
            if len(cells) != 1:
                raise InventoryError('Direct OGE owner source word crosses or ambiguously binds table cells')
        # This is the sole supported transaction-page title, not a general
        # header/footer classifier. All other external words stay unknown.
        if outside and not (len(outside) == 1 and outside[0]['text'] == 'Transactions'
                            and outside[0]['bottom'] < min(table.bbox[1] for table in tables)):
            raise InventoryError('Direct OGE owner source has unexplained words outside transaction tables')
    return inventory


def _replay(sources, evidence, review, catalog, catalog_history, document, url):
    from .oge_catalog_source import load_fixed_oge_direct_source
    from ..oge_reports import (MAX_PDF_BYTES, PARSER_VERSION, GENERIC_PARSER_VERSION,
        parse_archived_pdf, selected_parser_version, collapse_direct_catalog_records)
    from ..oge_candidate import _identity_key
    import pdfplumber

    record, metadata, raw = load_fixed_oge_direct_source(sources, evidence,
        catalog=catalog, catalog_history=catalog_history, document_id=document, source_url=url)
    direct, _ = collapse_direct_catalog_records([
        item for selected in [catalog, *catalog_history] for item in selected['transactions']
        if item.get('access_method') == 'direct_pdf'])
    key = _identity_key(record)
    if len({_identity_key(item) for item in direct if _identity_key(item)[0] == key[0]}) != 1:
        raise InventoryError('Direct OGE correction catalog identity is ambiguous')
    sha = metadata['sha256']
    folder = f'oge/extractions/{document}/{sha}'
    saved = [sources.obj(review, f'{folder}/{version.replace("/", "-")}.json')
             for version in (PARSER_VERSION, GENERIC_PARSER_VERSION)]
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        path = root / metadata['archive_path']
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        import json
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
        if len(raw) > MAX_PDF_BYTES:
            raise InventoryError('Direct OGE correction original exceeds byte budget')
        with pdfplumber.open(path) as pdf:
            if not 1 <= len(pdf.pages) <= MAX_PAGES:
                raise InventoryError('Direct OGE correction original exceeds page budget')
            scoped_inventory = _source_scope(pdf, metadata, record)
        # source_plan and these native parser families read PDF text/tables;
        # they have no OCR fallback. Never force a legacy lane for a new plan.
        if (selected_parser_version(metadata, path, sha) != PARSER_VERSION or
                selected_parser_version(metadata, path, sha, generic=True) != GENERIC_PARSER_VERSION):
            raise InventoryError('Direct OGE owner correction requires ordinary native v2/v7 source selection')
        actual = [parse_archived_pdf(root, path.with_suffix('.json'), generic=mode)
                  for mode in (False, True)]
    if actual != saved:
        raise InventoryError('Direct OGE correction saved extraction differs from complete original replay')
    if scoped_inventory != actual[0]['source_rows'] or scoped_inventory != actual[1]['source_rows']:
        raise InventoryError('Direct OGE owner source table scope differs from normal complete inventory')
    if any(len(report['source_rows']) > MAX_SOURCE_ROWS for report in actual):
        raise InventoryError('Direct OGE correction source row count exceeds budget')
    # Full catalog ambiguity was checked before limiting the normal receiver
    # to the one source whose old fact is being corrected.
    # Keep current/history membership distinct in the one-document receiver.
    # Full archives/counts/identity context were already proved above; these
    # scoped views are not serialized as replacement catalog observations.
    selected_rows = lambda selected: [item for item in selected['transactions']
        if item.get('access_method') == 'direct_pdf' and item.get('source_document_id') == document]
    scoped_current = {**catalog, 'transactions': selected_rows(catalog)}
    scoped_history = [{**selected, 'transactions': selected_rows(selected)}
                      for selected in catalog_history if selected_rows(selected)]
    return actual[0], actual[1], scoped_current, record, scoped_history


def _prove(legacy, generic, scoped_catalog, record, scoped_history, prior, replacement, person):
    from ..oge_candidate import (build_oge_candidate, _identity_key, _person_id,
        _legacy_default_owner_evidence, _instrument)
    from ..oge_reports import PARSER_VERSION, GENERIC_PARSER_VERSION
    from ..oge_transaction_adapter import adapt_oge_278t_extraction

    if legacy['parser_version'] != PARSER_VERSION or generic['parser_version'] != GENERIC_PARSER_VERSION:
        raise InventoryError('Direct OGE owner correction parser family is unsupported')
    # Preserve the existing source row ledger/qualification contract. An
    # unrelated quarantined row need not be perfectly recognized.
    adapt_oge_278t_extraction(legacy, record)
    adapt_oge_278t_extraction(generic, record)
    cutoff = generic['filed_at'] + 'T23:59:59Z' if generic.get('filed_at') else None
    if cutoff is None:
        raise InventoryError('Direct OGE owner correction has no qualified filed date')
    normal, _ = build_oge_candidate(scoped_catalog, [legacy], _empty(cutoff),
        data_cutoff_at=cutoff, catalog_history=scoped_history)
    pid = _person_id(_identity_key(record))
    source_person = [row for row in normal['people'] if row['id'] == pid]
    identity_fields = ('id', 'display_name', 'role', 'office_type', 'disclosure_authority')
    if len(source_person) != 1 or any(person.get(field) != source_person[0].get(field)
                                     for field in identity_fields):
        raise InventoryError('Direct OGE correction person differs from source catalog identity')
    rows = [row for row in legacy['transactions'] if row.get('extraction_id') == prior['id']]
    if len(rows) != 1 or rows[0].get('account_title') or rows[0].get('amount_low_exclusive'):
        raise InventoryError('Direct OGE correction has no unique conventional predecessor row')
    raw_row = rows[0]
    historical = _historical_projection(legacy, raw_row, pid)
    modern = {**historical, 'instrument_type': _instrument(raw_row['asset_name'], raw_row['ticker'])}
    # v2 did not label classifier epochs. Reconstruct both known complete
    # projections independently; prior never supplies a classifier result.
    candidates = []
    for projection in (historical, modern):
        represented = _represented(projection, prior)
        if represented is not None and represented not in candidates:
            candidates.append(represented)
    if len(candidates) != 1 or replacement != {**candidates[0], 'owner': 'Undisclosed'}:
        raise InventoryError('Direct OGE correction does not reproduce complete old and owner-only new objects')
    proof = _legacy_default_owner_evidence(legacy, raw_row)
    if proof is None:
        raise InventoryError('Direct OGE correction owner is explicit, ambiguous or not a v2 default')
    base = _empty(cutoff)
    base.update(people=[deepcopy(person)], transactions=[deepcopy(prior)])
    current, audit = build_oge_candidate(scoped_catalog, [generic], base,
        data_cutoff_at=cutoff, catalog_history=scoped_history,
        legacy_extractions_by_document={legacy['document_id']: legacy})
    target = [row for row in current['transactions'] if row['id'] == prior['id']]
    report = audit['reports'][0]
    expected_correction = {'extraction_id': prior['id'], 'field': 'owner', 'old_value': 'Self',
        'new_value': 'Undisclosed', 'rule': 'approved_v2_default_self_to_undisclosed/v1',
        'source_reference': proof}
    if (target != [replacement] or report['document_reasons'] or
            report['legacy_inheritance']['field_corrections'] != [expected_correction]):
        raise InventoryError('Direct OGE correction is not the qualified normal source interpretation')


def verified_oge_owner_corrections(*, old, current, repo, evidence_commit, review_commit,
                                   main_commit=None, previous_people=None, read_object=git_object):
    possible = [key for key in old.keys() & current.keys()
        if old[key].get('source_id') == current[key].get('source_id') == 'oge'
        and DIRECT.fullmatch(str(old[key].get('filing_id'))) is not None
        and old[key].get('owner') == 'Self' and current[key].get('owner') == 'Undisclosed'
        and _differences(old[key], current[key]) == ['owner']]
    if not possible:
        return []
    if repo is None or not all(re.fullmatch('[0-9a-f]{40}', str(ref))
                              for ref in (main_commit, evidence_commit, review_commit)):
        raise InventoryError('Direct OGE correction needs fixed main, review and evidence commits')
    from .oge_catalog_source import load_fixed_oge_catalogs
    from ..oge import OgeCatalogError
    sources = _Sources(repo, _git_read if read_object is git_object else read_object,
                       preflight_git=read_object is git_object)
    try:
        pinned, people = _fixed_board(sources, repo, main_commit)
        catalog, catalog_history = load_fixed_oge_catalogs(sources, evidence_commit, review_commit)
        cache, result = {}, []
        for key in sorted(possible):
            prior, replacement = old[key], current[key]
            if pinned.get(key) != prior:
                raise InventoryError('Direct OGE correction predecessor differs from fixed main board')
            person = people.get(prior.get('person_id'))
            if person is None or previous_people is not None and [item for item in previous_people
                    if item.get('id') == prior.get('person_id')] != [person]:
                raise InventoryError('Direct OGE correction previous person differs from fixed main board')
            document, url = prior['filing_id'], prior['source_url']
            if document not in cache:
                if len(cache) >= MAX_DOCUMENTS:
                    raise InventoryError('Direct OGE correction document count exceeds budget')
                cache[document] = _replay(sources, evidence_commit, review_commit,
                                         catalog, catalog_history, document, url)
            _prove(*cache[document], prior, replacement, person)
            result.append({'entity': 'transactions', 'kind': 'corrected', 'old_id': key, 'new_id': key,
                'reason': 'source_identity_correction', 'evidence_url': url,
                'source_sha256': cache[document][0]['source_sha256'], 'changed_fields': ['owner']})
        return result
    except (NativeCorrectionError, OgeCatalogError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InventoryError(f'Direct OGE owner correction source verification failed: {exc}') from exc
