"""Replay archived reports through the existing parsers and qualification paths."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND / 'src'))
from unison_snapshot.codec import encode as encoded


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def cases():
    return read(HERE / 'cases.json')['cases']


def parsed_rows(extraction):
    fields = ('schema_version', 'parser_version', 'source', 'source_id', 'document_id',
              'source_sha256', 'source_url', 'filer_name', 'filed_at', 'filed_at_raw',
              'filing_date', 'report_period_end', 'report_period_basis', 'evidence_complete',
              'document_reasons', 'transactions', 'quarantined', 'holdings', 'rows', 'excluded')
    return {key: extraction[key] for key in fields if key in extraction}


def house(case, evidence, review, metadata):
    if case['format'] == 'house_annual':
        from unison_snapshot.house_holdings import parse_archived_financial_report, qualify_financial_report
        extraction = parse_archived_financial_report(evidence, metadata)
        qualified = qualify_financial_report(extraction, read(review / case['context']['identity_path']))
    else:
        from unison_snapshot.house_ptr import parse_archived_pdf, qualify_automatic
        extraction = parse_archived_pdf(evidence, evidence / case['metadata_path'])
        qualified = qualify_automatic(extraction, read(review / case['context']['identity_path']),
                                      evidence_root=evidence)
    return extraction, qualified


def oge(case, evidence, review, metadata):
    context = case['context']
    if case['format'] == 'oge_annual':
        from unison_snapshot.oge_annual import extract_annual_pdf
        from unison_snapshot.oge_annual_ledger import build_oge_annual_shadow_ledger
        extraction = extract_annual_pdf(evidence / metadata['archive_path'],
            source_url=metadata['document_url'], source_sha256=metadata['sha256'],
            expected_filer=metadata['filer_name'])
        extraction.update(source_document_id=metadata['document_id'],
                          evidence_archive_path=metadata['archive_path'])
        ledger = build_oge_annual_shadow_ledger(extraction, run_id='golden',
            code_commit=read(HERE / 'cases.json')['baseline_code'],
            started_at='2026-11-10T00:00:00Z', completed_at='2026-11-10T00:00:00Z')
        qualified = {'rows': [{key: row[key] for key in ('candidate_id', 'disposition', 'reasons')}
                              for row in ledger['rows']],
                     'part_counts': ledger['part_counts'],
                     'publication_status': ledger['publication_status']}
    else:
        from unison_snapshot.oge_reports import parse_archived_pdf
        from unison_snapshot.oge_candidate import build_oge_candidate
        extraction = parse_archived_pdf(evidence, evidence / case['metadata_path'])
        candidate, audit = build_oge_candidate(context['catalog'], [extraction], context['base'],
            data_cutoff_at='2026-11-10T00:00:00Z', evidence_root=evidence)
        qualified = {'transactions': candidate['transactions'], 'people': candidate['people'],
                     'reports': audit['reports']}
    return extraction, qualified


def whitehouse(case, evidence, review, metadata):
    from unison_snapshot.commands.whitehouse_extract import extract_batch
    from unison_snapshot.oge_278e_public import parser_version_for_source as annual_version
    from unison_snapshot.whitehouse_278t import plan_whitehouse_278t_archive
    context = case['context']
    extract_batch(evidence, review, limit=1, document_id=case['id'],
                  source_sha256=case['source_sha256'])
    version = (plan_whitehouse_278t_archive(metadata, evidence)['parser_version']
               if case['format'] == 'whitehouse_278t'
               else annual_version(metadata['document_url'], metadata['sha256']))
    relative = (Path('whitehouse/extractions') / case['id'].removeprefix('wh-url:') /
                case['source_sha256'] / (version.replace('/', '-') + '.json'))
    extraction = read(review / relative)
    if case['format'] == 'whitehouse_278t':
        from unison_snapshot.whitehouse_278t_qualification import audit_whitehouse_278t
        qualified = audit_whitehouse_278t([extraction], context['coverage'],
            existing_oge_candidate=context['base'], directory_index=context['directory'],
            annual_identity_people=context['annual_people'])
    else:
        from unison_snapshot.whitehouse_annual_review import build_annual_review
        coverage = deepcopy(context['coverage'])
        coverage['reports'][0].update(extraction_path=relative.as_posix(),
                                      extraction_parser_version=version)
        qualified = build_annual_review(coverage, review,
            coverage_sha256=hashlib.sha256(encoded(coverage)).hexdigest())
    return extraction, qualified


def senate(case, evidence, review, metadata):
    context = case['context']
    if case['format'] == 'senate_annual':
        from unison_snapshot.senate_annual import build_annual_review
        write(evidence / 'senate_efd/annual/catalog/current.json', context['catalog'])
        qualified = build_annual_review(evidence, review,
                                       read(evidence / context['roster_path']))
        record = next(row for row in qualified['reports'] if row['document_id'] == case['id'])
        extraction = read(review / record['extraction_path'])
    else:
        from unison_snapshot.senate_reports import parse_electronic_ptr
        from unison_snapshot.senate_candidate import build_senate_candidate
        extraction = parse_electronic_ptr(metadata, (evidence / metadata['archive_path']).read_bytes(),
                                          include_source_ledger=True)
        relative = (Path('senate_efd/extractions') / case['id'] / case['source_sha256'] /
                    (extraction['parser_version'] + '.json'))
        write(review / relative, extraction)
        status = deepcopy(context['status'])
        status['extracted_transaction_count'] = len(extraction['transactions'])
        write(review / 'status/senate_efd.json', status)
        identities = context['identities']
        binding = hashlib.sha256((identities['roster_sha256'] + ':').encode('ascii')).hexdigest()
        write(review / 'senate_efd/identities' / identities['catalog_sha256'] /
              (binding + '.json'), identities)
        candidate, audit = build_senate_candidate(review, context['state'],
            read(BACKEND / 'data/empty-disclosure.json'), evidence_root=evidence)
        qualified = {'transactions': candidate['transactions'], 'people': candidate['people'],
                     'qualified_rows': audit['qualified_rows'],
                     'quarantined_rows': audit['quarantined_rows'],
                     'quarantined_reports': audit['quarantined_reports']}
    return extraction, qualified


def replay(case):
    with tempfile.TemporaryDirectory(prefix='golden-') as directory:
        root = Path(directory)
        for item in case['inputs']:
            destination = root / item['tree'] / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HERE / item['file'], destination)
        evidence, review = root / 'evidence', root / 'review'
        metadata = read(evidence / case['metadata_path'])
        handler = {'house': house, 'oge': oge, 'whitehouse': whitehouse,
                   'senate': senate}[case['format'].split('_')[0]]
        extraction, qualified = handler(case, evidence, review, metadata)
        return encoded({'parsed': parsed_rows(extraction), 'qualified': qualified})


def main():
    parser = argparse.ArgumentParser(description='Regenerate all archived-report golden answers')
    parser.add_argument('--regenerate', action='store_true', required=True)
    parser.parse_args()
    for case in cases():
        print(case['key'], case['id'], flush=True)
        target = HERE / case['key'] / 'expected.json'
        result = replay(case)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result)


if __name__ == '__main__':
    main()
