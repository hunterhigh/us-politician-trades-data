"""Reproduce an Endnote-only classifier change on an existing native v2 fact.

This grants no financial row, identity, owner change or OCR interpretation.
Both complete objects must be projections of the same replayed official PDF.
"""
from copy import deepcopy
import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .source_objects import InventoryError, _differences, git_object
from .whitehouse_owner_correction import _Sources, _fixed_board
from ..whitehouse_native_correction import _historical_projection, _historical_instrument


def _report(sources, evidence, review, document, url, people):
    from ..whitehouse_278t import PARSER_VERSION, parse_whitehouse_278t_pdf
    from ..whitehouse_278t_candidate import audit_whitehouse_278t
    from ..whitehouse_disclosures import parse_disclosure_index

    coverage=sources.obj(review,'whitehouse/coverage-current.json')
    rows=[row for row in coverage.get('reports',[]) if row.get('document_id')==document]
    if (coverage.get('schema_version')!='whitehouse-public-coverage/v1' or len(rows)!=1 or
            rows[0].get('document_url')!=url or rows[0].get('extraction_parser_version')!=PARSER_VERSION or
            rows[0].get('review_state') not in {'extracted_review_only','extracted_with_issues'}):
        raise InventoryError('Legacy classification requires the selected native v2 report')
    versions=rows[0].get('archive_sha256_versions')
    if not isinstance(versions,list) or len(versions)!=1 or not re.fullmatch('[0-9a-f]{64}',str(versions[0])):
        raise InventoryError('Legacy classification original source is ambiguous')
    sha=versions[0]
    path=f'whitehouse/extractions/{document[7:]}/{sha}/whitehouse-278t-hybrid-geometry-v2.json'
    if rows[0].get('extraction_path')!=path:
        raise InventoryError('Legacy classification selected cache path differs')
    saved=sources.obj(review,path)
    pdf_path=f'whitehouse/disclosures/reports/{document[7:]}/{sha}.pdf'
    metadata=sources.obj(evidence,pdf_path[:-4]+'.json')
    raw=sources.read(evidence,pdf_path,16*1024*1024)
    if (metadata.get('schema_version')!='whitehouse-public-disclosures-pdf/v1' or
            metadata.get('source_id')!='whitehouse_public_disclosures' or
            metadata.get('document_id')!=document or metadata.get('document_url')!=url or
            metadata.get('archive_path')!=pdf_path or metadata.get('sha256')!=sha or
            metadata.get('byte_length')!=len(raw) or hashlib.sha256(raw).hexdigest()!=sha):
        raise InventoryError('Legacy classification original archive differs')
    page_sha=metadata.get('page_sha256')
    if not isinstance(page_sha,str) or not re.fullmatch('[0-9a-f]{64}',page_sha):
        raise InventoryError('Legacy classification official directory is missing')
    index=parse_disclosure_index(sources.read(evidence,f'whitehouse/disclosures/pages/{page_sha}.html',4*1024*1024))
    entries=[row for row in index['reports'] if row.get('source_document_id')==document]
    fields=('document_url','filer_name_from_label','document_type_from_label','link_label')
    if (index['page_sha256']!=page_sha or len(entries)!=1 or
            any(entries[0].get(field)!=metadata.get(field) for field in fields) or
            metadata.get('document_type_from_label')!='278t'):
        raise InventoryError('Legacy classification official directory identity differs')
    with TemporaryDirectory() as temporary:
        pdf=Path(temporary)/'source.pdf';pdf.write_bytes(raw)
        import pdfplumber
        with pdfplumber.open(pdf) as source:
            if not 1<=len(source.pages)<=32:
                raise InventoryError('Legacy classification original exceeds page budget')
        report=parse_whitehouse_278t_pdf(pdf,source_url=url,source_sha256=sha,
            document_id=document,filer_name=metadata['filer_name_from_label'],amended_label=metadata['link_label'])
    # Old v2 caches predate raw cells and these null evidence placeholders.
    # Replayed dates, identity, raw financial fields and every other key must agree.
    reference=deepcopy(report)
    for field in ('filing_date_evidence','filer_identity_evidence'):
        if field not in saved and reference.get(field) is None:reference.pop(field,None)
    if all('cells' not in row for row in saved.get('transactions',[])):
        for row in reference['transactions']:row.pop('cells',None)
    if (saved!=reference or report.get('extraction_method')!='native_pdf_text_table' or
            report.get('parser_version')!=PARSER_VERSION or report.get('document_reasons')):
        raise InventoryError('Legacy classification cache differs from complete native original replay')
    catalog=sources.obj(review,'oge/whitehouse/coverage-current.json')
    base={'meta':{'is_demo':False},'people':people,'transactions':[]}
    audit=audit_whitehouse_278t([report],catalog,base,directory_index=index)['reports'][0]
    return report,audit


def verified_legacy_classification_corrections(*, old, current, repo, evidence_commit,
        review_commit, main_commit=None, previous_people=None, read_object=git_object):
    possible=[key for key in old.keys()&current.keys() if
        old[key].get('source_id')==current[key].get('source_id')=='oge' and
        old[key].get('owner')==current[key].get('owner')=='Self' and
        old[key].get('instrument_type')=='Bond' and current[key].get('instrument_type') in {'Stock','Unspecified'} and
        _differences(old[key],current[key])==['instrument_type'] and
        re.search(r'\bEndnote\b',str(old[key].get('asset_name')),re.I)]
    if not possible:return []
    if repo is None or not all(re.fullmatch('[0-9a-f]{40}',str(ref)) for ref in
            (main_commit,evidence_commit,review_commit)):
        raise InventoryError('Legacy classification requires fixed main, review and evidence commits')
    sources=_Sources(repo,read_object);cache={};result=[]
    try:
        pinned,people=_fixed_board(sources,repo,main_commit)
        for key in sorted(possible):
            prior,replacement=old[key],current[key];person=people.get(prior.get('person_id'))
            if (pinned.get(key)!=prior or person is None or previous_people is not None and
                    [row for row in previous_people if row.get('id')==person['id']]!=[person]):
                raise InventoryError('Legacy classification predecessor or person differs from fixed main')
            url=prior.get('source_url')
            if not isinstance(url,str) or not re.fullmatch(r'https://www\.whitehouse\.gov/wp-content/uploads/20\d{2}/\d{2}/[^?#]+\.pdf',url):
                raise InventoryError('Legacy classification original URL is unsupported')
            document='wh-url:'+hashlib.sha256(url.encode()).hexdigest()[:24]
            if prior.get('filing_id')!=document:
                raise InventoryError('Legacy classification document differs from original URL')
            if document not in cache:
                if len(cache)>=12:raise InventoryError('Legacy classification document budget exceeded')
                cache[document]=_report(sources,evidence_commit,review_commit,document,url,list(people.values()))
            report,audit=cache[document]
            rows=[row for row in report['transactions'] if row.get('extraction_id')==key]
            decisions=[row for row in audit['rows'] if row.get('extraction_id')==key]
            if (len(rows)!=1 or len(decisions)!=1 or decisions[0].get('status')!='eligible' or
                    audit.get('document_reasons') or (audit.get('matched_catalog_identity') or {}).get('person_id')!=person['id']):
                raise InventoryError('Legacy classification target is not uniquely source-qualified')
            row=rows[0]
            from ..whitehouse_278t_candidate import _transaction
            stripped=re.sub(r'\bEndnote\b','',row['asset_name'],flags=re.I)
            if (prior!=_historical_projection(report,row,person['id']) or
                    replacement!=_transaction(report,row,person['id']) or
                    _historical_instrument(stripped,row.get('ticker'))!=replacement['instrument_type']):
                raise InventoryError('Legacy classification does not reproduce complete Endnote-only projections')
            result.append({'entity':'transactions','kind':'corrected','old_id':key,'new_id':key,
                'reason':'source_classification_correction','evidence_url':url,
                'source_sha256':report['source_sha256'],'changed_fields':['instrument_type']})
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
        raise InventoryError('Legacy classification original verification failed: '+str(error)) from error
    return result
