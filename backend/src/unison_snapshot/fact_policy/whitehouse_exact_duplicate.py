"""Verify deletion of an exact-source alias while keeping its published peer."""
from __future__ import annotations

import re

from .source_objects import InventoryError, git_object
from .whitehouse_owner_correction import _fixed_board
from .house_revision_supersession import _git_read
from ..whitehouse_native_correction import NativeCorrectionError, _Sources


def _comparison_fields(fact):
    """Find possible peers; source replay must still verify every full field."""
    from ..oge_native_owner_scope import NULLABLE
    return {key:value for key,value in fact.items() if key not in {
            'id','filing_id','source_url','owner','asset_name','instrument_type'}
            and not (key in NULLABLE and value is None)}


def verified_exact_source_duplicates(*, old, current, repo, evidence_commit, review_commit,
                                     main_commit=None, previous_people=None, read_object=git_object):
    possible = []
    for key in sorted(old.keys()-current.keys()):
        duplicate = old[key]
        if (duplicate.get('source_id') != 'oge' or
                not re.fullmatch(r'wh-url:[0-9a-f]{24}',str(duplicate.get('filing_id')))):
            continue
        peers = [fact for identifier,fact in current.items() if identifier in old and
            old[identifier] == fact and fact.get('source_id') == 'oge' and
            re.fullmatch(r'[0-9a-f]{32}',str(fact.get('filing_id'))) and
            _comparison_fields(fact) == _comparison_fields(duplicate)]
        # Source row/ID replay below selects the peer. Equal amounts, dates
        # and assets are not a unique identity and may legitimately repeat.
        possible.extend((duplicate,peer) for peer in peers)
    if not possible:
        return []
    if repo is None or not all(re.fullmatch(r'[0-9a-f]{40}',str(ref))
                              for ref in (main_commit,evidence_commit,review_commit)):
        raise InventoryError('Exact-source duplicate removal needs fixed main, review and evidence commits')
    from ..whitehouse_exact_duplicate import prepare_whitehouse_exact_duplicate_sources
    sources = _Sources(repo,_git_read if read_object is git_object else read_object,
                       preflight_git=read_object is git_object)
    try:
        pinned,people = _fixed_board(sources,repo,main_commit)
        for duplicate,canonical in possible:
            for fact in (duplicate,canonical):
                if pinned.get(fact['id']) != fact:
                    raise InventoryError('Exact duplicate predecessor differs from fixed main board')
                person=people.get(fact.get('person_id'))
                supplied=[row for row in previous_people or [] if row.get('id')==fact.get('person_id')]
                if person is None or previous_people is not None and supplied != [person]:
                    raise InventoryError('Exact duplicate predecessor identity differs from fixed main board')
        coverage = sources.obj(review_commit,'whitehouse/coverage-current.json')
        reports,bindings,source_pairs = {},{},[]
        for duplicate,canonical in possible:
            document=duplicate['filing_id']
            entries=[row for row in coverage.get('reports',[]) if row.get('document_id')==document]
            if len(entries)!=1 or len(entries[0].get('archive_sha256_versions',[]))!=1:
                raise InventoryError('Exact duplicate source version is not unique')
            selected=entries[0];sha=selected['archive_sha256_versions'][0]
            if not re.fullmatch(r'[0-9a-f]{64}',str(sha)):
                raise InventoryError('Exact duplicate source SHA is invalid')
            report=sources.obj(review_commit,selected['extraction_path'])
            wh=sources.obj(evidence_commit,f'whitehouse/disclosures/reports/{document[7:]}/{sha}.json')
            direct_document=canonical['filing_id']
            try:
                direct=sources.obj(evidence_commit,f'oge/reports/{direct_document}/{sha}.json')
                parsed=sources.obj(review_commit,f'oge/extractions/{direct_document}/{sha}/oge-278t-pdf-v7.json')
            except (InventoryError,NativeCorrectionError,OSError,ValueError,KeyError,TypeError):
                # A financially identical fact in another report is not a
                # source alias. Its unavailable source must not select a peer.
                continue
            if not any(row.get('extraction_id')==canonical['id'] for row in
                       parsed.get('transactions',[]) if isinstance(row,dict)):
                continue
            if document in bindings and bindings[document]['oge_archive']!=direct:
                raise InventoryError('Exact duplicate has competing direct canonical documents')
            reports[document]=report
            bindings[document]={'whitehouse_archive':wh,'oge_archive':direct,'oge_extraction':parsed}
            source_pairs.append((duplicate,canonical))
        context=prepare_whitehouse_exact_duplicate_sources(
            {'meta':{'is_demo':False},'transactions':list(old.values()),'people':list(people.values())},
            list(reports.values()),{'schema_version':'whitehouse-278t-exact-source-bindings/v1',
            'reports':list(bindings.values())},_sources=sources,
            evidence_ref=evidence_commit,review_ref=review_commit)
        results=[]
        for duplicate,canonical in source_pairs:
            report=reports[duplicate['filing_id']]
            rows=[row for row in report.get('transactions',[]) if row.get('extraction_id')==duplicate['id']]
            if len(rows)!=1:
                continue
            proof=context.resolve(report,rows[0],canonical_fact=canonical,duplicate_fact=duplicate)
            if proof is None:
                continue
            results.append({'entity':'transactions','kind':'duplicate','old_id':duplicate['id'],
                'new_id':canonical['id'],'reason':'duplicate_resolution',
                'evidence_url':canonical['source_url'],'source_sha256':proof['record']['source_sha256'],
                **({'source_correction_fields':proof['record']['source_correction_fields']}
                   if proof['record'].get('source_correction_fields') else {})})
        return results
    except (NativeCorrectionError,OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        raise InventoryError(f'Exact-source duplicate verification failed: {error}') from error
