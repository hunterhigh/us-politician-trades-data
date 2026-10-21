"""Source-bound native amount-only replacement views; no acquisition or publisher."""
from copy import deepcopy
from pathlib import Path
import json
from .oge import OgeCatalogError


def _ambiguous_native_application_nodes(groups):
    edges={};bad=set();undirected={}
    for prior,entries in groups.items():
        dates=[item[2]['amendment_filed_at'] for item in entries]
        if len(dates)!=len(set(dates)):bad.add(prior)
        previous=prior
        for _,row,_ in sorted(entries,key=lambda item:item[2]['amendment_filed_at']):
            node=row['extraction_id'];edges.setdefault(previous,set()).add(node)
            undirected.setdefault(prior,set()).add(node);undirected.setdefault(node,set()).add(prior)
            previous=node
    reverse={};degree={node:len(values) for node,values in edges.items()}
    for node,values in edges.items():
        if len(values)>1:bad.add(node)
        for other in values:
            degree.setdefault(other,0);reverse.setdefault(other,set()).add(node)
    bad.update(node for node,parents in reverse.items() if len(parents)>1)
    leaves=[node for node,count in degree.items() if count==0];removed=set()
    while leaves:
        node=leaves.pop();removed.add(node)
        for parent in reverse.get(node,set()):
            degree[parent]-=1
            if degree[parent]==0:leaves.append(parent)
    bad.update(set(edges)-removed)
    pending=list(bad)
    while pending:
        for node in undirected.get(pending.pop(),set()):
            if node not in bad:bad.add(node);pending.append(node)
    return bad


def prepare_native_exhibit_applications(extractions, records, saved, formal, data_cutoff_at=None):
    from .oge_reports import materialize_saved_oge_278t_exhibits
    selected={doc:deepcopy(value) for doc,value in saved.items()
              if value.get('replacement_application_mode')=='verified_amount_only/v1'}
    if not selected:return {}
    originals={x['document_id']:deepcopy(x) for x in extractions}
    views={};relevant=set(selected)
    for doc,inputs in selected.items():
        if 'prior_formal_transactions' in inputs:raise OgeCatalogError('formal facts must come from current candidate base')
        views[doc]=materialize_saved_oge_278t_exhibits(originals[doc],records[doc],
            prior_formal_transactions=formal,**inputs)
        for row in views[doc]['observation_receipt']['rows']:
            target=row.get('replacement_relation',{}).get('target',{})
            if target.get('target_document_id') in originals:relevant.add(target['target_document_id'])
    # Context candidates are not application inputs. Keep their existing local
    # pending interpretation; do not invent a cross-source application contract.
    selected={doc:inputs for doc,inputs in selected.items() if any(
        row.get('replacement_relation',{}).get('status')=='bound_replacement_target'
        for row in views[doc]['observation_receipt']['rows'])}
    if not selected:return {}
    relevant=set(selected)|{row.get('replacement_relation',{}).get('target',{}).get('target_document_id')
        for doc in selected for row in views[doc]['observation_receipt']['rows']}
    relevant&=set(originals)
    views={doc:view for doc,view in views.items() if doc in selected}
    for doc in relevant:
        views.setdefault(doc,deepcopy(originals[doc]))
    for doc in selected:
        for row in views[doc]['quarantined']:
            pending_reasons=set(row.get('reasons',[]))
            if not pending_reasons&{'replacement_existing_formal_fact_not_source_bound',
                                   'replacement_prior_formal_row_identity_unresolved'}:continue
            target=row.get('native_exhibit_reference',{}).get('replacement_relation',{}).get('target',{})
            target_doc=target.get('target_document_id');identifier=target.get('target_extraction_id')
            if 'replacement_prior_formal_row_identity_unresolved' in pending_reasons and target_doc in views:
                # Unmapped old IDs cannot be guessed from financial similarity.
                # Preserve the real base, and block new interpretations of this
                # source until its old identities have a source-bound mapping.
                prior_ids=[r['id'] for r in formal if str(r.get('filing_id'))==str(target_doc)]
                for prior in list(views[target_doc]['transactions']):
                    views[target_doc]['transactions'].remove(prior)
                    prior['reasons']=['existing_formal_target_identity_requires_source_reconciliation']
                    views[target_doc]['quarantined'].append(prior)
                views[target_doc]['native_exhibit_preserved_target_ids']=prior_ids
                continue
            if any(r.get('id')==row.get('extraction_id') for r in formal) and not any(r.get('id')==identifier for r in formal):
                raise OgeCatalogError('existing replacement facts require reconciliation; predecessor will not be revived')
            if target_doc not in views or not any(r.get('id')==identifier for r in formal):continue
            matches=[r for r in views[target_doc]['transactions'] if r.get('extraction_id')==identifier]
            for prior in matches:
                views[target_doc]['transactions'].remove(prior)
                prior['reasons']=['existing_formal_target_diff_requires_source_reconciliation']
                views[target_doc]['quarantined'].append(prior)
                views[target_doc].setdefault('native_exhibit_preserved_target_ids',[]).append(identifier)
    from .oge_candidate import _identity_key
    identities={}
    for record in records.values():
        key=_identity_key(record)
        if key is not None:identities.setdefault(key[0],set()).add(key)
    for doc in selected:
        key=_identity_key(records[doc]);reason=None
        if key is None or len(identities.get(key[0],set()))!=1:reason='identity_ambiguous'
        elif records[doc].get('pending_final_oge_disposition') is True:reason='pending_final_oge_disposition'
        elif data_cutoff_at is not None and str(views[doc].get('filed_at',''))>data_cutoff_at[:10]:reason='filing_exceeds_cutoff'
        for row in list(views[doc]['transactions']):
            application=row.get('native_exhibit_reference',{}).get('replacement_application')
            if not application:continue
            target_doc=application['target']['target_document_id']
            target_record=records.get(target_doc);target_source=originals.get(target_doc)
            row_reason=reason
            if row_reason is None and (not target_record or not target_source or
                    _identity_key(target_record)!=key or any(target_source.get(field)!=target_record.get(meta)
                        for field,meta in (('source_url','document_url'),('catalog_filer_name','filer_name'),
                                           ('agency','agency'),('position_title','position_title')))):
                row_reason='replacement_target_catalog_identity_unqualified'
            if row_reason is None and target_record.get('pending_final_oge_disposition') is True:
                row_reason='replacement_target_pending_final_oge_disposition'
            if row_reason:
                views[doc]['transactions'].remove(row);views[doc]['quarantined'].append(row)
                row['reasons']=[row_reason]
                next(r for r in views[doc]['observation_receipt']['rows'] if r.get('extraction_id')==row['extraction_id']).update(
                    disposition='quarantined',reasons=[row_reason])
    groups={}
    for doc in selected:
        for row in views[doc]['transactions']:
            application=row.get('native_exhibit_reference',{}).get('replacement_application')
            if application:groups.setdefault(application['prior_transaction_id'],[]).append((doc,row,application))
    ambiguous_nodes=_ambiguous_native_application_nodes(groups)
    for prior,entries in groups.items():
        dates=[entry[2]['amendment_filed_at'] for entry in entries]
        if prior in ambiguous_nodes:
            for doc,row,application in entries:
                views[doc]['transactions'].remove(row)
                row['reasons']=['replacement_application_component_not_unique_chain']
                views[doc]['quarantined'].append(row)
                receipt=next(r for r in views[doc]['observation_receipt']['rows'] if r.get('extraction_id')==row['extraction_id'])
                receipt.update(disposition='quarantined',reasons=row['reasons'])
            continue
        previous=prior
        for index,(doc,row,application) in enumerate(sorted(entries,key=lambda item:item[2]['amendment_filed_at'])):
            application['effective_prior_transaction_id']=previous
            application['application_status']='applied' if index==len(entries)-1 else 'superseded_by_later_verified_amendment'
            views[doc].setdefault('native_exhibit_replacement_applications',[]).append(deepcopy(application))
            if index<len(entries)-1:
                row['native_replacement_disposition']='superseded'
                next(r for r in views[doc]['observation_receipt']['rows'] if r.get('extraction_id')==row['extraction_id'])['disposition']='superseded'
            previous=row['extraction_id']
        first=entries[0][2]
        target_doc=first['target']['target_document_id']
        if target_doc in views:
            matches=[r for r in views[target_doc]['transactions'] if r.get('extraction_id')==prior]
            if len(matches)!=1:
                raise OgeCatalogError('verified native replacement target source inventory differs')
            matches[0]['native_replacement_disposition']='superseded'
            views[target_doc].setdefault('native_exhibit_superseded_transaction_ids',[]).append(prior)
    for view in views.values():
        for row in view['transactions']:
            if row.get('native_replacement_disposition')!='superseded':continue
            for receipt in view.get('observation_receipt',{}).get('rows',[]):
                if receipt.get('extraction_id')==row['extraction_id']:receipt['disposition']='superseded'
            for application in view.get('native_exhibit_replacement_applications',[]):
                if application['transaction_id']==row['extraction_id']:
                    application['application_status']='superseded_by_later_verified_amendment'
                    row.get('native_exhibit_reference',{}).get('replacement_application',{})['application_status']=application['application_status']
    ids={r.get('extraction_id') for view in views.values() for r in view.get('transactions',[])+view.get('quarantined',[])}
    prior=[deepcopy(r) for r in formal if str(r.get('filing_id')) in relevant or r.get('id') in ids]
    context={'schema_version':'oge-native-exhibit-application-context/v1',
        'extractions':[originals[d] for d in sorted(relevant)],'records':deepcopy(records),
        'saved_inputs':selected,'prior_formal_transactions':prior,'data_cutoff_at':data_cutoff_at}
    # Replay uses exactly the same reduced prior view as the first preparation.
    for doc in selected:
        views[doc]['native_exhibit_application_replay']['inputs']['prior_formal_transactions']=deepcopy(prior)
    views={doc:view for doc,view in views.items() if doc in selected or view.get('native_exhibit_superseded_transaction_ids')
           or view.get('native_exhibit_preserved_target_ids')}
    for view in views.values():view['native_exhibit_application_context']=deepcopy(context)
    return views


def verify_native_exhibit_context(context,evidence,review):
    """Rebase private input paths onto controlled roots and re-read original sources."""
    from .oge_transaction_adapter import _observation_input_extraction
    from .oge_reports import _read_archived_source
    if not isinstance(context,dict) or context.get('schema_version')!='oge-native-exhibit-application-context/v1':
        raise ValueError('native replacement source context is missing')
    context=deepcopy(context);evidence=Path(evidence).resolve();review=Path(review).resolve()
    def validate_cache(value):
        doc=value.get('document_id');sha=value.get('source_sha256')
        paths=list((review/'oge/extractions'/str(doc)/str(sha)).glob('*.json'))
        hits=[]
        for path in paths:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved==value or _observation_input_extraction(saved)==value:hits.append(path)
        if len(hits)!=1:raise ValueError('native replacement original cache is not uniquely source bound')
    for value in context['extractions']:
        validate_cache(value)
        doc=value['document_id'];sha=value['source_sha256']
        metadata,_,actual_sha=_read_archived_source(evidence,evidence/'oge/reports'/doc/(sha+'.json'))
        record=context['records'].get(doc)
        if actual_sha!=sha or not isinstance(record,dict) or any(
                metadata.get(meta)!=record.get(field) for meta,field in (
                    ('document_id','source_document_id'),('document_url','document_url'),
                    ('filer_name','filer_name'),('agency','agency'),('position_title','position_title'),
                    ('amended_label','amended_label'))):
            raise ValueError('native replacement catalog identity differs from the controlled archive')
    for doc,inputs in context['saved_inputs'].items():
        source=next(x for x in context['extractions'] if x['document_id']==doc)
        inputs['archived_pdf_path']=str(evidence/'oge/reports'/doc/(source['source_sha256']+'.pdf'))
        for target in inputs.get('replacement_targets',[]):
            value=target['extraction'];validate_cache(value)
            target['evidence_root']=str(evidence)
            target['archive_metadata_path']=str(evidence/'oge/reports'/value['document_id']/(value['source_sha256']+'.json'))
    return prepare_native_exhibit_applications(context['extractions'],context['records'],context['saved_inputs'],
                                               context['prior_formal_transactions'],context.get('data_cutoff_at'))


def reconcile_annual_ptr_references(candidate,review_root,evidence_root=None):
    """Redirect only verified replacement references in already retained annual metadata."""
    review=Path(review_root)
    audit_path=review/'oge/qualifications/current.json'
    if not audit_path.is_file():return candidate,{'replacements':[]}
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    reports=audit.get('reports',[]);contexts=[r.get('native_exhibit_application_context') for r in reports
                                            if r.get('native_exhibit_application_context')]
    if not contexts:return candidate,{'replacements':[]}
    if evidence_root is None:raise ValueError('native annual reference reconciliation requires controlled evidence root')
    redirects={};proof=[];seen_contexts=set()
    for context in contexts:
        fingerprint=json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if fingerprint in seen_contexts:continue
        seen_contexts.add(fingerprint)
        if context.get('data_cutoff_at')!=audit.get('data_cutoff_at'):
            raise ValueError('native replacement audit cutoff differs from its source replay')
        for view in verify_native_exhibit_context(context,evidence_root,review).values():
            matches=[r for r in reports if r.get('document_id')==view['document_id']]
            if len(matches)!=1:
                raise ValueError('native replacement source audit is not unique')
            report=matches[0]
            for field in ('observation_receipt','native_exhibit_replacement_applications',
                          'native_exhibit_superseded_transaction_ids'):
                if report.get(field)!=view.get(field):
                    raise ValueError('native replacement audit differs from its source replay')
            expected=sum(r.get('native_replacement_disposition')!='superseded' for r in view['transactions'])
            expected+=len(view.get('native_exhibit_preserved_target_ids',[]))
            if report.get('promoted_count')!=expected or report.get('quarantined_count')!=len(view['quarantined']):
                raise ValueError('native replacement audit counts differ from its source replay')
            for item in view.get('native_exhibit_replacement_applications',[]):
                old=item['effective_prior_transaction_id'];new=item['transaction_id']
                if old in redirects and redirects[old]!=new:
                    raise ValueError('native replacement reference has conflicting successors')
                redirects[old]=new
                proof.append({key:item[key] for key in ('effective_prior_transaction_id','transaction_id',
                    'amendment_document_id','amendment_source_sha256','application_status')})
    result=deepcopy(candidate);facts={r['id']:r for r in result['transactions']}
    for relation in result['meta'].get('transaction_disclosure_relations',[]):
        values=[];annual=facts.get(relation['transaction_id'])
        for identifier in relation.get('possible_ptr_transaction_ids',[]):
            seen=set()
            while identifier in redirects:
                if identifier in seen:raise ValueError('native replacement reference cycle')
                seen.add(identifier);identifier=redirects[identifier]
            target=facts.get(identifier)
            if target is not None and annual is not None and target.get('person_id')==annual.get('person_id'):
                values.append(identifier)
            elif identifier not in redirects and not seen:values.append(identifier)
            else:raise ValueError('verified native replacement reference has no current same-person fact')
        relation['possible_ptr_transaction_ids']=sorted(set(values))
        relation['status']='possible_ptr_overlap' if values else 'cross_report_relationship_unconfirmed'
    return result,{'replacements':proof}
