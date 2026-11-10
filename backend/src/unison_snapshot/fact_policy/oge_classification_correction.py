"""Prove the native direct OGE Endnote classifier migration from fixed Git.

The review audit selects one raw reader; it cannot authorize a correction.
Source inventory, both raw caches, full predecessors and the normal receiver
are independently replayed using the existing direct OGE source boundary.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .source_objects import InventoryError, _differences, git_object
from .oge_owner_correction import DIRECT, _replay, _empty
from .whitehouse_owner_correction import _fixed_board
from .house_revision_supersession import _git_read
from ..whitehouse_native_correction import _Sources, NativeCorrectionError, MAX_PAGES

# Full direct-OGE replay spans more documents and complete catalog history than
# the separate White House migration. Per-object, page and row bounds remain.
MAX_DOCUMENTS = 128
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024


def _selected_reports(sources, review):
    audit = sources.obj(review, 'oge/qualifications/current.json')
    rows = audit.get('reports')
    if not isinstance(rows, list):
        raise InventoryError('Direct OGE classification lacks its selected receiver inventory')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('document_id'), str) or row['document_id'] in result:
            raise InventoryError('Direct OGE classification receiver identities are duplicated or invalid')
        result[row['document_id']] = row
    return result


def _selected_input(audit, legacy, generic):
    from ..oge_native_owner_scope import source_interpretation_binding
    from ..oge_reports import PARSER_VERSION, GENERIC_PARSER_VERSION
    if (audit is None or audit.get('observation_receipt') is not None or
            any(key in audit for key in ('native_source_partition_binding',
                'native_exhibit_application_context', 'saved_ocr_input', 'quarantine_count_unit'))):
        raise InventoryError('Direct OGE classification requires the selected ordinary native receiver')
    binding = audit.get('source_interpretation_binding')
    if not isinstance(binding, dict):
        raise InventoryError('Direct OGE classification requires an explicit raw interpretation binding')
    version = binding.get('parser_version')
    if version not in {PARSER_VERSION, GENERIC_PARSER_VERSION}:
        raise InventoryError('Direct OGE classification selected parser is unsupported')
    selected = legacy if version == PARSER_VERSION else generic
    if source_interpretation_binding(selected) != binding:
        raise InventoryError('Direct OGE classification selected raw input differs from complete source replay')
    return selected


def _replay_ordinary_current(sources, evidence, review, catalog, history, document, url, audit):
    """Authenticate the selected v8 positive cells without inferring an owner scope."""
    from .oge_catalog_source import load_fixed_oge_direct_source, _combined_direct_records
    from ..oge_candidate import _identity_key
    from ..oge_reports import PARSER_VERSION, parse_archived_pdf, selected_parser_version, parser_target
    from .oge_owner_correction import MAX_SOURCE_ROWS
    import pdfplumber
    binding = audit.get('source_interpretation_binding') if isinstance(audit, dict) else None
    if not isinstance(binding, dict) or binding.get('parser_version') != PARSER_VERSION:
        raise InventoryError('Unspecified classification requires selected current ordinary native input')
    record, metadata, raw = load_fixed_oge_direct_source(sources, evidence,
        catalog=catalog, catalog_history=history, document_id=document, source_url=url)
    identity = _identity_key(record)
    direct = _combined_direct_records(catalog, history)
    if len({_identity_key(item) for item in direct if _identity_key(item)[0] == identity[0]}) != 1:
        raise InventoryError('Direct OGE classification catalog identity is ambiguous')
    sha = metadata['sha256']
    saved = sources.obj(review, f'oge/extractions/{document}/{sha}/{PARSER_VERSION.replace("/", "-")}.json')
    with TemporaryDirectory() as temporary:
        root = Path(temporary); path = root / metadata['archive_path']
        path.parent.mkdir(parents=True); path.write_bytes(raw)
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
        if selected_parser_version(metadata, path, sha) != PARSER_VERSION:
            raise InventoryError('Unspecified classification cannot force a different source parser lane')
        with pdfplumber.open(path) as pdf:
            if not 1 <= len(pdf.pages) <= MAX_PAGES:
                raise InventoryError('Unspecified classification original exceeds page budget')
        actual = parse_archived_pdf(root, path.with_suffix('.json'))
    if (not parser_target(metadata, PARSER_VERSION).accepts(actual)
            or len(actual.get('source_rows', [])) > MAX_SOURCE_ROWS):
        raise InventoryError('Unspecified classification input is not a complete current bounded extraction')
    if actual != saved:
        raise InventoryError('Unspecified classification saved extraction differs from complete original replay')
    _selected_input(audit, actual, None)
    select = lambda selected: [item for item in selected['transactions']
        if item.get('access_method') == 'direct_pdf' and item.get('source_document_id') == document]
    scoped = {**catalog, 'transactions': select(catalog)}
    scoped_history = [{**item, 'transactions': select(item)} for item in history if select(item)]
    return actual, None, scoped, record, scoped_history, metadata, raw


def _prove(selected, legacy, generic, catalog, record, history, metadata, raw,
           prior, replacement, person):
    from ..oge_native_classification import prepare_native_classification
    from ..oge_candidate import build_oge_candidate, _identity_key, _person_id
    from ..oge_reports import GENERIC_PARSER_VERSION

    pid = _person_id(_identity_key(record))
    identity = {'id':pid, 'display_name':record['filer_name'],
        'role':record['position_title'], 'office_type':record['agency'], 'disclosure_authority':'oge'}
    if prior.get('person_id') != pid or any(person.get(field) != value for field,value in identity.items()):
        raise InventoryError('Direct OGE classification person differs from source catalog identity')
    filed = selected.get('filed_at')
    if not isinstance(filed, str):
        raise InventoryError('Direct OGE classification has no source qualified filing date')
    cutoff = filed + 'T23:59:59Z'
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        path = root / metadata['archive_path']
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
        if replacement.get('instrument_type') == 'Unspecified':
            from ..whitehouse_native_correction import _historical_projection
            from ..oge_projection import _transaction_projection
            from ..oge_native_owner_scope import _represented
            rows = [row for row in selected['transactions'] if row.get('extraction_id') == prior['id']]
            if (len(rows) != 1 or not re.search(r'\bEndnote\b', rows[0].get('asset_name', ''), re.I)
                    or _differences(prior, replacement) != ['instrument_type']
                    or _represented(_historical_projection(selected, rows[0], pid), prior) != prior
                    or _represented(_transaction_projection(selected, rows[0], pid), replacement) != replacement):
                raise InventoryError('Unspecified classification does not reproduce complete source old/new projections')
        else:
            context = prepare_native_classification(selected, [prior], record,
                evidence_root=root, source_extraction=selected, legacy_extraction=legacy)
            verified = context.reconcile(prior)
            if context.status != 'observed' or verified is None or verified['fact'] != replacement:
                raise InventoryError('Direct OGE classification does not reproduce complete source old/new projections')
        base = _empty(cutoff)
        base.update(people=[deepcopy(person)], transactions=[deepcopy(prior)])
        result,audit = build_oge_candidate(catalog, [selected], base,
            data_cutoff_at=cutoff, catalog_history=history, evidence_root=root,
            source_extractions_by_document={selected['document_id']:selected},
            **({'legacy_extractions_by_document':{selected['document_id']:legacy}}
               if selected['parser_version'] == GENERIC_PARSER_VERSION else {}))
        actual = [row for row in result['transactions'] if row['id'] == prior['id']]
        report = audit['reports'][0]
        if (actual != [replacement] or report.get('document_reasons') or
                any(row.get('extraction_id') == prior['id'] for row in report.get('quarantined', []))):
            raise InventoryError('Direct OGE classification is not the selected qualified normal interpretation')


def verified_oge_classification_corrections(*, old, current, repo, evidence_commit,
        review_commit, main_commit=None, previous_people=None, read_object=git_object):
    possible = []
    for key in old.keys() & current.keys():
        prior, replacement = old[key], current[key]
        changed = _differences(prior,replacement)
        if (prior.get('source_id') == replacement.get('source_id') == 'oge' and
                DIRECT.fullmatch(str(prior.get('filing_id'))) is not None and
                prior.get('instrument_type') == 'Bond' and replacement.get('instrument_type') in {'Stock', 'Unspecified'} and
                (changed == ['instrument_type'] or
                 replacement.get('instrument_type') == 'Stock' and
                 changed == ['instrument_type','owner'] and prior.get('owner') == 'Self'
                    and replacement.get('owner') == 'Undisclosed')):
            possible.append(key)
    if not possible:
        return []
    if repo is None or not all(re.fullmatch('[0-9a-f]{40}', str(ref))
                              for ref in (main_commit,evidence_commit,review_commit)):
        raise InventoryError('Direct OGE classification needs fixed main, review and evidence commits')
    from .oge_catalog_source import load_fixed_oge_catalogs
    from ..oge import OgeCatalogError
    sources = _Sources(repo, _git_read if read_object is git_object else read_object,
                       preflight_git=read_object is git_object, max_total=MAX_SOURCE_BYTES)
    try:
        pinned,people = _fixed_board(sources,repo,main_commit)
        catalog,history = load_fixed_oge_catalogs(sources,evidence_commit,review_commit)
        selected_reports = _selected_reports(sources,review_commit)
        cache,result = {},[]
        for key in sorted(possible):
            prior,replacement = old[key],current[key]
            if pinned.get(key) != prior:
                raise InventoryError('Direct OGE classification predecessor differs from fixed main board')
            person = people.get(prior.get('person_id'))
            if person is None or previous_people is not None and [item for item in previous_people
                    if item.get('id') == prior.get('person_id')] != [person]:
                raise InventoryError('Direct OGE classification previous person differs from fixed main board')
            document,url = prior['filing_id'],prior['source_url']
            mode = 'unspecified' if replacement['instrument_type'] == 'Unspecified' else 'stock'
            cache_key = (document, mode)
            if cache_key not in cache:
                if document not in {key[0] for key in cache} and len({key[0] for key in cache}) >= MAX_DOCUMENTS:
                    raise InventoryError('Direct OGE classification document budget exceeded')
                cache[cache_key] = (_replay_ordinary_current(sources,evidence_commit,review_commit,
                    catalog,history,document,url,selected_reports.get(document)) if mode == 'unspecified' else
                    _replay(sources,evidence_commit,review_commit,catalog,history,document,url))
            legacy,generic,scoped,record,scoped_history,metadata,raw = cache[cache_key]
            selected = _selected_input(selected_reports.get(document),legacy,generic)
            _prove(selected,legacy,generic,scoped,record,scoped_history,metadata,raw,
                   prior,replacement,person)
            result.append({'entity':'transactions','kind':'corrected','old_id':key,'new_id':key,
                'reason':'source_classification_correction','evidence_url':url,
                'source_sha256':selected['source_sha256'],'changed_fields':_differences(prior,replacement)})
        return result
    except (NativeCorrectionError,OgeCatalogError,OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        raise InventoryError(f'Direct OGE classification source verification failed: {exc}') from exc
