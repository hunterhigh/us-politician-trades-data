"""Explicit OGE EXHIBIT -> qualified White House row applications.

Each family keeps its own extraction schema and qualifier. Only proven formal
projections enter the existing replacement graph. No acquisition or publisher.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

from .oge import OgeCatalogError

SCHEMA = 'oge-whitehouse-native-replacement-context/v1'


def _canonical(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _same(left,right):
    return all(left.get(key)==right.get(key) for key in set(left)|set(right))


def _whitehouse_candidate(inputs, *, evidence_root=None, review_root=None):
    from .whitehouse_278t_audit import audit_whitehouse_278t
    from .whitehouse_278t_candidate import (build_whitehouse_278t_review_candidate,
        audit_whitehouse_278t_exact_source_candidate,build_whitehouse_278t_exact_source_candidate)
    args=(inputs['base'],inputs['extractions'])
    kw={key:inputs.get(key) for key in ('annual_extractions','directory_index','annual_identity_people')}
    policy = inputs.get('native_classification_policy_version')
    sources = None
    if policy is not None:
        from .whitehouse_native_correction import POLICY_VERSION, prepare_native_classification_sources
        if policy != POLICY_VERSION:
            raise OgeCatalogError('cross-source White House classification policy is unsupported')
        selection = inputs.get('native_classification_selection', {})
        if (not isinstance(selection, dict) or
                set(selection) - {'coverage_input', 'extraction_status_input'}):
            raise OgeCatalogError('cross-source White House classification selection is invalid')
        selected = {}
        for name, relative in selection.items():
            if (not isinstance(relative, str) or len(relative) > 512 or '\\' in relative or
                    Path(relative).is_absolute() or Path(relative).drive or
                    any(part in {'', '.', '..'} for part in relative.split('/')) or review_root is None):
                raise OgeCatalogError('cross-source White House classification selection escaped controlled review root')
            root = Path(review_root).resolve(strict=True)
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise OgeCatalogError('cross-source White House classification selection escaped controlled review root')
            selected[name] = path
        sources = prepare_native_classification_sources(inputs['extractions'],
            evidence_root=evidence_root, review_root=review_root,
            previous_transactions=inputs['base']['transactions'], **selected)
    coverage=inputs['oge_whitehouse_coverage']
    if inputs.get('exact_source_copies'):
        kw['source_bindings']=inputs.get('source_bindings')
        if policy is not None:
            kw['native_classification_sources'] = sources
        eligibility=audit_whitehouse_278t_exact_source_candidate(*args,coverage,**kw)
        candidate,audit=build_whitehouse_278t_exact_source_candidate(*args,eligibility,coverage,
            data_cutoff_at=inputs['base']['meta']['data_cutoff_at'],**kw)
    else:
        eligibility=audit_whitehouse_278t(inputs['extractions'],coverage,inputs['base'],
            inputs.get('annual_extractions'),inputs.get('directory_index'),inputs.get('annual_identity_people'))
        candidate,audit=build_whitehouse_278t_review_candidate(*args,eligibility,coverage,
            data_cutoff_at=inputs['base']['meta']['data_cutoff_at'],
            **({'native_classification_sources': sources} if policy is not None else {}), **kw)
    return candidate,audit,eligibility


def _bound_whitehouse_candidate(inputs, evidence_root, review_root):
    # Old stored contexts deliberately retain their original projection policy.
    # New contexts contain a version, never a serialized Sources/approval object.
    if 'native_classification_policy_version' in inputs:
        return _whitehouse_candidate(inputs, evidence_root=evidence_root, review_root=review_root)
    return _whitehouse_candidate(inputs)


def _cache_matches(root,folder,value):
    paths=list((Path(root)/folder).glob('*.json'))
    matches=[p for p in paths if _read(p)==value]
    if len(matches)!=1:raise OgeCatalogError('cross-source extraction is not a unique controlled cache')


def _whitehouse_source_row(report,row,evidence):
    """Never treat an OCR cache as native text or infer its printed ordinal."""
    from .whitehouse_278t import (GENERIC_PARSER_VERSIONS, SAVED_OCR_PARSER_VERSIONS,
        NATIVE_VISIBLE_PARSER_VERSIONS, validate_generic_whitehouse_report)
    from .oge_reports import _compact,_extract_borderless_transaction_tables,parse_table_rows,_ROW_NUMBER
    doc=report['document_id'];sha=report['source_sha256']
    tail=doc.removeprefix('wh-url:')
    metadata_path=Path(evidence)/'whitehouse/disclosures/reports'/tail/(sha+'.json')
    metadata=_read(metadata_path);pdf=Path(evidence)/metadata['archive_path']
    if (metadata.get('schema_version')!='whitehouse-public-disclosures-pdf/v1' or
            metadata.get('document_id')!=doc or metadata.get('document_url')!=report['source_url'] or
            metadata.get('sha256')!=sha or not pdf.resolve().is_relative_to(Path(evidence).resolve()) or
            hashlib.sha256(pdf.read_bytes()).hexdigest()!=sha):
        raise OgeCatalogError('White House replacement archive differs from its source')
    generic=report.get('parser_version') in GENERIC_PARSER_VERSIONS
    if generic:validate_generic_whitehouse_report(report)
    cells=row.get('cells',row.get('ocr_raw_cells'))
    if not isinstance(cells,list) or len(cells)!=6 or type(row.get('row_number')) is not int:
        return None
    if not _ROW_NUMBER.fullmatch(_compact(cells[0])) or int(_compact(cells[0]))!=row['row_number']:
        return None
    if report.get('parser_version') in SAVED_OCR_PARSER_VERSIONS:
        if report.get('observation_receipt',{}).get('source_original_verified') is not True:
            return None
        basis='whitehouse_saved_original_pdf_replayed_geometry_and_fields'
    elif report.get('parser_version') in NATIVE_VISIBLE_PARSER_VERSIONS:
        # The validator above reopened the complete same-SHA projection. The
        # unfiltered table may contain duplicate nonpainting glyphs and cannot
        # substitute for that independently reconstructed visible inventory.
        matching = [item for item in report['transactions'] if item == row and not item.get('reasons')]
        if len(matching) != 1 or report.get('document_reasons'):
            return None
        basis='same_source_visible_glyph_cell_projection_fully_reobserved_from_archive'
    else:
        # An old OCR extraction must independently match actual native cells;
        # a readable native number alone does not certify its OCR fields/frame.
        import pdfplumber
        with pdfplumber.open(pdf) as original:
            page=row.get('page_number')
            if type(page) is not int or not 1<=page<=len(original.pages):return None
            tables=_extract_borderless_transaction_tables(original.pages[page-1]) or original.pages[page-1].extract_tables() or []
            native=[c for table in tables for c in table if isinstance(c,list)]
            if sum([_compact(c) for c in values]==cells for values in native)!=1:return None
            if not generic:
                good,bad=parse_table_rows([(page,c) for c in native],source_sha=sha,default_owner='Self',
                    allow_open_amounts=True,allow_exchange=True)
                matches=[r for r in good+bad if r.get('cells')==cells]
                if len(matches)!=1 or any(matches[0].get(k)!=row.get(k) for k in
                        ('extraction_id','row_number','owner','account_title','asset_name','ticker','transaction_type',
                         'transaction_date','amount_low','amount_high','amount_low_exclusive')):return None
        basis='unique_same_page_native_cells_with_original_family_parser_scope_replay'
    return {'source_family':'whitehouse_278t','target_document_id':doc,'target_source_sha256':sha,
        'target_source_url':report['source_url'],'target_extraction_id':row['extraction_id'],
        'target_page_number':row['page_number'],'target_printed_number':row['row_number'],
        'target_parser_version':report['parser_version'],'target_raw_cells_sha256':_canonical(cells),
        'archive_metadata_sha256':hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        'target_raw_row_basis':basis}


def _sources(saved,evidence,review):
    from .oge_reports import _read_archived_source
    result={};unresolved=[]
    for doc,inputs in saved.items():
        if inputs.get('replacement_application_mode')!='verified_amount_only/v1':
            raise OgeCatalogError('post-White-House EXHIBIT input requires the explicit application mode')
        observations=inputs.get('observations') or []
        if not observations:raise OgeCatalogError('post-White-House EXHIBIT observations are missing')
        sha=observations[0]['source_sha256']
        folder=Path(review)/'oge/extractions'/doc/sha
        caches=[_read(p) for p in folder.glob('*.json')]
        reference=inputs.get('source_extraction_canonical_sha256')
        if reference is not None:caches=[value for value in caches if _canonical(value)==reference]
        if len(caches)!=1:
            unresolved.append({'document_id':doc,'source_sha256':sha,
                'reason':'source_extraction_reference_missing_or_not_unique'})
            continue
        metadata,pdf,actual=_read_archived_source(Path(evidence),Path(evidence)/'oge/reports'/doc/(sha+'.json'))
        x=caches[0]
        if x.get('document_id')!=doc or x.get('source_sha256')!=actual:
            raise OgeCatalogError('post-White-House OGE cache differs from archive')
        record={key:metadata.get(key) for key in ('document_url','filer_name','agency','position_title',
            'catalog_added_date','amended_label','pending_final_oge_disposition')}
        record.update(source_document_id=doc,document_type='278_transaction',access_method='direct_pdf',source_id='oge')
        result[doc]=(x,record,pdf)
    return result,unresolved


def apply_cross_source_native_replacements(candidate,saved,wh_inputs,evidence_root,review_root):
    """Apply only after the normal WH family candidate has independently passed."""
    if not saved:return candidate,None
    from .whitehouse_278t import saved_ocr_source_roots
    with saved_ocr_source_roots(evidence_root,review_root):
        return _apply_cross_source_native_replacements(candidate,saved,wh_inputs,evidence_root,review_root)


def _apply_cross_source_native_replacements(candidate,saved,wh_inputs,evidence_root,review_root):
    evidence=Path(evidence_root);review=Path(review_root)
    expected,wh_audit,eligibility=_bound_whitehouse_candidate(wh_inputs,evidence_root,review_root)
    if candidate!=expected:raise OgeCatalogError('cross-source input is not the current normal White House candidate')
    if _read(review/'oge/whitehouse/coverage-current.json')!=wh_inputs['oge_whitehouse_coverage']:
        raise OgeCatalogError('cross-source White House identity coverage differs from controlled source')
    if wh_inputs.get('directory_index') is not None and _read(review/'whitehouse/disclosures/index-current.json')!=wh_inputs['directory_index']:
        raise OgeCatalogError('cross-source White House directory differs from controlled source')
    for report in wh_inputs['extractions']:
        folder=Path('whitehouse/extractions')/report['document_id'].removeprefix('wh-url:')/report['source_sha256']
        _cache_matches(review,folder,report)
    sources,unresolved=_sources(saved,evidence,review)
    context={'schema_version':SCHEMA,'saved_inputs':deepcopy(saved),'wh_inputs':deepcopy(wh_inputs)}
    for doc,inputs in context['saved_inputs'].items():
        # Controlled roots are supplied by the caller, never public metadata.
        inputs.pop('archived_pdf_path',None);inputs.pop('replacement_targets',None)
        if set(inputs)-{'observations','amendment_context_page_number','replacement_application_mode','formal_reference_ids',
                       'source_extraction_canonical_sha256','application_stage'}:
            raise OgeCatalogError('unsupported cross-source saved EXHIBIT input')
        if inputs.get('application_stage') not in {None,'post_whitehouse/v1'}:
            raise OgeCatalogError('unsupported cross-source application stage')
    from .oge_reports import materialize_saved_oge_278t_exhibits,_native_exhibit_period_date,_native_original_metadata_context
    from .oge_candidate import _identity_key,_transaction_projection,_apply_native_exhibit_replacements
    from .whitehouse_278t_candidate import _transaction
    from .oge_native_replacements import _ambiguous_native_application_nodes
    facts={r['id']:r for r in candidate['transactions']};groups={};views={};applications=[]
    qualified={r['document_id']:r for r in eligibility.get('canonical_eligibility',eligibility)['reports']}
    consumer={r['document_id']:r for r in wh_audit['reports']}
    for doc,(source,record,pdf) in sources.items():
        inputs=context['saved_inputs'][doc]
        view=materialize_saved_oge_278t_exhibits(source,record,observations=inputs['observations'],
            archived_pdf_path=pdf,amendment_context_page_number=inputs.get('amendment_context_page_number'))
        for original_row in list(view['transactions']):
            view['transactions'].remove(original_row)
            view['quarantined'].append({**original_row,'reasons':sorted(set(
                original_row.get('reasons',[])+['amendment_relationship_unresolved']))})
        views[doc]=view
        for row in list(view['quarantined']):
            ref=row.get('native_exhibit_reference')
            if not ref:continue
            cover=view['observation_receipt'].get('amendment_context',{})
            label=ref.get('replacement_relation',{})
            number=label.get('target_printed_number')
            candidates=[]
            context_reports=[]
            for report in wh_inputs['extractions']:
                qa=qualified.get(report['document_id'],{});ca=consumer.get(report['document_id'],{})
                identity=qa.get('matched_catalog_identity')
                if not identity:continue
                urls=label.get('source_urls_observed',[])
                if urls and report['source_url'] not in urls:continue
                if not urls:
                    meta_path=evidence/'whitehouse/disclosures/reports'/report['document_id'].removeprefix('wh-url:')/(report['source_sha256']+'.json')
                    if not _native_original_metadata_context(_read(meta_path),cover):continue
                if _identity_key(identity)!=_identity_key(record):continue
                if cover.get('original_report_date')!=report.get('filed_at'):continue
                context_reports.append(report)
            # A bad row in another otherwise matching source is not evidence
            # that the sole qualified row belongs to this letter.
            if len(context_reports)!=1:continue
            for report in context_reports:
                qa=qualified.get(report['document_id'],{});ca=consumer.get(report['document_id'],{})
                if qa.get('document_reasons') or ca.get('document_reasons'):continue
                for old in report['transactions']:
                    if old.get('row_number')!=number:continue
                    outcome=[r for r in ca.get('rows',[]) if r.get('extraction_id')==old['extraction_id']]
                    if len(outcome)!=1 or outcome[0]['status'] not in {'promoted','retained_existing'}:continue
                    prior=_transaction(report,old,ca['matched_person_id'])
                    if prior['id'] not in facts or not _same(facts[prior['id']],prior):continue
                    target=_whitehouse_source_row(report,old,evidence)
                    if target:candidates.append((report,old,prior,target))
            if len(candidates)!=1:continue
            original,old,prior,target=candidates[0]
            # This closes row binding; a context-only document candidate never
            # satisfies the unique source row + qualified formal projection.
            reasons=set(row.get('reasons',[]))-{'replacement_target_document_unresolved',
                'replacement_original_source_or_unique_row_unverified','replacement_context_candidate_not_unique_or_unverified'}
            if (cover.get('status')!='source_context_available' or cover.get('amount_only_statement') is not True or
                    not source.get('filed_at') or source.get('document_reasons') and
                    set(source['document_reasons'])-{'transaction_table_not_found','no_transaction_rows_found'} or
                    record.get('pending_final_oge_disposition') is True):continue
            if source['filed_at']<original['filed_at'] or source['filed_at']>candidate['meta']['data_cutoff_at'][:10]:continue
            date_evidence=_native_exhibit_period_date(row['cells'][3],cover,{'status':'candidate_context_match',
                'candidate':{'source_context_sha256':cover['source_observation_canonical_sha256']}})
            if date_evidence:
                row['transaction_date']=date_evidence['normalized_value']
                reasons-={'transaction_date_invalid','transaction_date_century_unbound'}
                ref['transaction_date_interpretation']=date_evidence
            if reasons or any(row.get(key)!=old.get(key) for key in
                    ('asset_name','ticker','transaction_type','transaction_date')):continue
            if row['transaction_date']>source['filed_at']:continue
            start,end=cover['original_reporting_period']
            if not start<=row['transaction_date']<=end:continue
            row['owner']=old['owner']
            if old.get('account_title'):row['account_title']=old['account_title']
            new=_transaction_projection(view,row,prior['person_id'])
            if new['id']==prior['id']:continue
            if new['id'] in facts and not _same(new,facts[new['id']]):
                raise OgeCatalogError('existing cross-source replacement requires source reconciliation')
            proof={'schema_version':'oge-native-amount-only-application/v1','status':'source_verified_amount_only',
                'source_family':'oge_to_whitehouse_278t','prior_transaction_id':prior['id'],
                'transaction_id':new['id'],'prior_fact':prior,'prior_row':deepcopy(old),'target':target,
                'amendment_document_id':doc,'amendment_source_sha256':source['source_sha256'],
                'amendment_source_url':source['source_url'],'amendment_filed_at':source['filed_at'],
                'original_filed_at':original['filed_at'],'resolved_owner':row['owner'],
                'source_context_sha256':cover['source_observation_canonical_sha256'],
                'notification_metadata':{'original_raw':old.get('late_notification_raw'),
                    'replacement_raw':row.get('late_notification_raw'),'role':'optional_over30days_metadata'}}
            groups.setdefault(prior['id'],[]).append((doc,row,proof,new))
    ambiguous=_ambiguous_native_application_nodes({key:[entry[:3] for entry in entries] for key,entries in groups.items()})
    for prior,entries in groups.items():
        if prior in ambiguous:continue
        previous=prior
        ordered=sorted(entries,key=lambda entry:entry[2]['amendment_filed_at'])
        for index,(doc,row,proof,new) in enumerate(ordered):
            proof['effective_prior_transaction_id']=previous
            proof['application_status']='applied' if index==len(ordered)-1 else 'superseded_by_later_verified_amendment'
            view=views[doc];view['quarantined'].remove(row);row.pop('reasons',None)
            row['native_exhibit_reference']['replacement_application']=deepcopy(proof)
            row['native_exhibit_reference']['replacement_relation']={'status':'bound_replacement_target',
                'target':proof['target'],'reasons':[]}
            row['observation_reference']=deepcopy(row['native_exhibit_reference'])
            view['transactions'].append(row)
            receipt=next(r for r in view['observation_receipt']['rows'] if r.get('extraction_id')==row['extraction_id'])
            receipt.update(deepcopy(row['native_exhibit_reference']),disposition='qualified',reasons=[])
            if index<len(ordered)-1:
                row['native_replacement_disposition']='superseded';receipt['disposition']='superseded'
            applications.append(proof);facts[new['id']]=new;previous=new['id']
    result=deepcopy(candidate);result['transactions']=list(facts.values())
    applied_ids={item['transaction_id'] for item in applications}
    existing=[row for row in wh_inputs['base']['transactions'] if row.get('filing_id') in sources]
    if any(row['id'] not in applied_ids for row in existing):
        raise OgeCatalogError('existing cross-source formal fact cannot be retired or reinterpreted without current proof')
    _apply_native_exhibit_replacements(result['transactions'],[{'native_exhibit_replacement_applications':applications}],candidate)
    result['transactions'].sort(key=lambda r:r['id'])
    if applications:
        qualified_count=wh_audit['promoted_transaction_count']+len(wh_audit.get('retained_existing_transaction_ids',[]))
        old_summary=(f"White House public 278-T: {len(wh_audit['reports'])} PDFs audited, "
            f"{qualified_count} transactions qualified, {wh_audit['quarantined_row_count']} rows quarantined; "
            f"{len(candidate['transactions'])} total OGE candidate transactions.")
        new_summary=old_summary.removesuffix(f"{len(candidate['transactions'])} total OGE candidate transactions.")+f"{len(result['transactions'])} total OGE candidate transactions."
        detail=f" Verified cross-source EXHIBIT applications: {sum(item['application_status']=='applied' for item in applications)} active; {len(applications)} source-proven predecessor replacements."
        for health in result['source_health']:
            if health.get('source_id')=='oge':
                if health['detail'].endswith('; '+old_summary):
                    health['detail']=health['detail'][:-len(old_summary)]+new_summary
                if not health['detail'].endswith(detail):health['detail']+=detail
        # Do not inherit the direct CLI's intermediate identity. Follow the
        # cached-candidate content identity pattern without running publication
        # validation before the later annual and market stages have completed.
        from .codec import digest, encode
        identity=dict(result,meta={key:value for key,value in result['meta'].items() if key!='snapshot_id'})
        result['meta']['snapshot_id']=digest(encode(identity))
    for view in views.values():
        view['replacement_application_mode']='verified_amount_only/v1'
        view['cross_source_native_application_context']=deepcopy(context)
    audit={'schema_version':'oge-whitehouse-native-replacement-application/v1','context':context,
        'applications':applications,'source_views':views,'candidate_before_sha256':_canonical(candidate),
        'candidate_after_sha256':_canonical(result),'source_selection_unresolved':unresolved}
    return result,audit


def verify_cross_source_native_applications(audit,evidence_root,review_root):
    if not isinstance(audit,dict) or audit.get('schema_version')!='oge-whitehouse-native-replacement-application/v1':
        raise OgeCatalogError('cross-source replacement application audit is missing')
    context=audit.get('context',{})
    if context.get('schema_version')!=SCHEMA:raise OgeCatalogError('cross-source replacement context is missing')
    wh_inputs=context['wh_inputs']
    from .whitehouse_278t import saved_ocr_source_roots
    with saved_ocr_source_roots(evidence_root,review_root):
        candidate,_,_=_bound_whitehouse_candidate(wh_inputs,evidence_root,review_root)
        actual,proof=apply_cross_source_native_replacements(candidate,context['saved_inputs'],wh_inputs,evidence_root,review_root)
    if proof!=audit:raise OgeCatalogError('cross-source replacement audit differs from current family source replay')
    return actual,proof


def replay_cross_source_context(context,evidence_root,review_root):
    if context.get('schema_version')!=SCHEMA:raise OgeCatalogError('cross-source replacement context is invalid')
    from .whitehouse_278t import saved_ocr_source_roots
    with saved_ocr_source_roots(evidence_root,review_root):
        candidate,_,_=_bound_whitehouse_candidate(context['wh_inputs'],evidence_root,review_root)
        return apply_cross_source_native_replacements(candidate,context['saved_inputs'],context['wh_inputs'],evidence_root,review_root)


def cross_source_dependency_sha256(saved,wh_reports,evidence_root,review_root):
    """Bound local dependencies, not the whole review tree or unselected pages."""
    evidence=Path(evidence_root);review=Path(review_root);files={}
    for doc,inputs in saved.items():
        sha=inputs['observations'][0]['source_sha256']
        for path in (review/'oge/extractions'/doc/sha).glob('*.json'):
            files['review/'+path.relative_to(review).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        for extension in ('json','pdf'):
            path=evidence/'oge/reports'/doc/(sha+'.'+extension)
            files['evidence/'+path.relative_to(evidence).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    for report in wh_reports:
        for extension in ('json','pdf'):
            path=evidence/'whitehouse/disclosures/reports'/report['document_id'].removeprefix('wh-url:')/(report['source_sha256']+'.'+extension)
            files['evidence/'+path.relative_to(evidence).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return _canonical(files)
