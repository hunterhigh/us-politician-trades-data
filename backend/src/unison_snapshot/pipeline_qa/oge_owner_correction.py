"""Prove direct native OGE's existing default-owner correction from fixed Git.

The candidate's saved receipt is never authority. This supports ordinary native
v2/v7 only, and does not migrate instruments, accounts, amounts or identities.
"""
from __future__ import annotations

from copy import deepcopy
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
    from ..oge_native_owner_scope import _represented as represent
    return represent(projection, prior)


def _empty(cutoff):
    return {'meta': {'is_demo': False, 'data_cutoff_at': cutoff}, 'people': [],
            'transactions': [], 'reported_holdings': [], 'security_market_data': [],
            'source_health': []}




def _source_scope(pdf, metadata, record):
    """Use the same finite source inventory as the normal receiver."""
    from ..oge_native_owner_scope import _source_scope as observe, InventoryError as SourceError
    try:
        return observe(pdf, metadata, record, max_source_rows=MAX_SOURCE_ROWS,
                       max_scope_comparisons=MAX_SCOPE_COMPARISONS)
    except SourceError as error:
        raise InventoryError(str(error)) from error


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
    return actual[0], actual[1], scoped_current, record, scoped_history, metadata, raw


def _prove(legacy, generic, scoped_catalog, record, scoped_history, metadata, raw, prior, replacement, person):
    # The receiver must independently consume its original while the temporary
    # canonical archive still exists. No saved success assertion crosses layers.
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        path = root / metadata['archive_path']
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        import json
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
        return _prove_source(legacy, generic, scoped_catalog, record, scoped_history,
                             prior, replacement, person, evidence_root=root)


def _prove_source(legacy, generic, scoped_catalog, record, scoped_history, prior, replacement, person, *, evidence_root):
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
        data_cutoff_at=cutoff, catalog_history=scoped_history, evidence_root=evidence_root)
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
        legacy_extractions_by_document={legacy['document_id']: legacy}, evidence_root=evidence_root)
    target = [row for row in current['transactions'] if row['id'] == prior['id']]
    report = audit['reports'][0]
    expected_correction = {'extraction_id': prior['id'], 'field': 'owner', 'old_value': 'Self',
        'new_value': 'Undisclosed', 'rule': 'approved_v2_default_self_to_undisclosed/v1',
        'source_reference': proof}
    # The owner-only policy predates the independent Endnote correction.
    # A current producer may compose that known source-proved classification
    # without invalidating an earlier owner-only transition. This does not
    # authorize a classification change in the returned owner event.
    normal_targets = [replacement]
    if (prior.get('instrument_type') == 'Bond'
            and re.search(r'\bEndnote\b', raw_row['asset_name'], re.I)
            and _instrument(raw_row['asset_name'], raw_row['ticker']) == 'Stock'):
        normal_targets.append({**replacement, 'instrument_type': 'Stock'})
    if (len(target) != 1 or target[0] not in normal_targets or report['document_reasons'] or
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
