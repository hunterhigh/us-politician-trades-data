"""Normal House review queues and artifact accounting, without source collection."""
import argparse
import os
from pathlib import Path
import re
from unison_snapshot.house_ptr import LEGACY_PARSER_VERSION, PARSER_RETRY_VERSION
from unison_snapshot.house_holdings import PARSER_VERSION as HOLDING_PARSER_VERSION

def select(args):
    import json, os
    from pathlib import Path
    from unison_snapshot.house_paper_pipeline import preserved_paper_materialization
    from unison_snapshot.house_paper_commit import recover_pending_paper_materializations
    evidence = Path(args.evidence_root)
    review = Path(args.review_root)
    recover_pending_paper_materializations(evidence, review)
    pending = []
    retries = []
    for metadata_path in sorted(evidence.glob('house_clerk/documents/*/*/*.json')):
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        year = str(metadata['filing_year'])
        if args.year and year != args.year:
            continue
        document_id = metadata['document_id']
        sha = metadata['sha256']
        extraction = review / 'house_clerk' / 'extractions' / year / document_id / f'{sha}.json'
        failure = review / 'house_clerk' / 'failures' / year / document_id / f'{sha}.json'
        if extraction.is_file():
            # A retained legacy failure is evidence, not a request to
            # overwrite a successfully bound saved interpretation.
            try:
                value = json.loads(extraction.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                value = {}
            if value.get('extraction_method') == 'source_bound_paper_cells':
                relative = Path(year) / document_id / f'{sha}.json'
                qualification = review / 'house_clerk/qualifications' / relative
                observation = review / 'house_clerk/paper_observations' / relative
                if preserved_paper_materialization(metadata, value,
                        json.loads(qualification.read_text(encoding='utf-8')),
                        json.loads(observation.read_text(encoding='utf-8'))):
                    continue
        from unison_snapshot.house_native_contrast import plan_house_native_contrast, PARSER_VERSIONS as CONTRAST_VERSIONS
        from unison_snapshot.house import HouseIndexError
        try:
            contrast_plan = plan_house_native_contrast(evidence, metadata_path)
        except HouseIndexError:
            # Unavailable source is a parse/retry disposition, never an
            # absence proof allowing a retained success to skip policy.
            if not extraction.is_file() and failure.is_file():
                try:
                    if json.loads(failure.read_text(encoding='utf-8')).get('parser_version') == PARSER_RETRY_VERSION:
                        continue
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    pass
            retries.append(metadata_path)
            continue
        if contrast_plan['parser_version'] in CONTRAST_VERSIONS and extraction.is_file():
            from unison_snapshot.house_native_financial import requires_source_financial_upgrade
            try:
                selected = json.loads(extraction.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                selected = {}
            if selected.get('parser_version') != contrast_plan['parser_version']:
                retries.append(metadata_path)
                continue
            if requires_source_financial_upgrade(evidence, metadata_path, selected):
                retries.append(metadata_path)
                continue
        if extraction.is_file() and failure.is_file():
            retries.append(metadata_path)
            continue
        if extraction.is_file():
            try:
                value = json.loads(extraction.read_text(encoding='utf-8'))
                stale_legacy = (value.get('extraction_method') == 'tesseract_legacy_checkbox'
                                and value.get('parser_version') != LEGACY_PARSER_VERSION)
                if not stale_legacy:
                    continue
                retries.append(metadata_path)
                continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                retries.append(metadata_path)
                continue
        if failure.is_file():
            try:
                if json.loads(failure.read_text(encoding='utf-8')).get('parser_version') == PARSER_RETRY_VERSION:
                    continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            retries.append(metadata_path)
        else:
            pending.append(metadata_path)
    selected = (pending + retries)[:int(args.batch_size)]
    Path('.local').mkdir(exist_ok=True)
    Path('.local/parse-batch.txt').write_text(
        ''.join(f'{path}\n' for path in selected), encoding='utf-8')


def ocr(args):
    import json, os
    from pathlib import Path
    import pdfplumber
    from unison_snapshot.house_ptr import observe_native_word_pages, planned_ocr_pages
    evidence = Path(args.evidence_root).resolve()
    needs_ocr = False
    selected = Path('.local/parse-batch.txt').read_text(encoding='utf-8').splitlines()
    paper_id = args.paper_document_id
    if paper_id:
        if not paper_id.isdecimal() or not 1 <= len(paper_id) <= 12:
            raise SystemExit('paper_document_id must be a numeric House document ID')
        matches = list(evidence.glob(f'house_clerk/documents/*/{paper_id}/*.json'))
        if len(matches) != 1:
            raise SystemExit('paper_document_id has no unique archived source')
        selected.append(str(matches[0]))
    for line in selected:
        metadata = json.loads(Path(line).read_text(encoding='utf-8'))
        pdf = (evidence / metadata['archive_path']).resolve()
        if evidence not in pdf.parents:
            raise SystemExit('Evidence path escapes its root')
        with pdfplumber.open(pdf) as document:
            if planned_ocr_pages(observe_native_word_pages(document, metadata['sha256'])):
                needs_ocr = True
                break
    with open(args.github_output, 'a', encoding='utf-8') as output:
        output.write(f'needed={str(needs_ocr).lower()}\n')


def status(args):
    import json, os
    from datetime import datetime, timezone
    from pathlib import Path
    evidence = Path(args.evidence_root)
    review = Path(args.review_root)
    extractions = list(review.glob('house_clerk/extractions/*/*/*.json'))
    failures = list(review.glob('house_clerk/failures/*/*/*.json'))
    identities = list(review.glob('house_clerk/identities/*/*/*.json'))
    qualifications = list(review.glob('house_clerk/qualifications/*/*/*.json'))
    paper_observations = list(review.glob('house_clerk/paper_observations/*/*/*.json'))
    paper_failures = list(review.glob('house_clerk/paper_failures/*/*/*.json'))
    paper_rereads = list(review.glob('house_clerk/paper_key_field_rereads/*/*/*.json'))
    paper_reread_failures = list(review.glob('house_clerk/paper_reread_failures/*/*/*.json'))
    paper_temporal_failures = list(review.glob('house_clerk/paper_temporal_failures/*/*/*.json'))
    def keys(paths, bucket):
        root = review / 'house_clerk' / bucket
        return {path.relative_to(root).as_posix() for path in paths}
    extraction_keys = keys(extractions, 'extractions')
    failure_keys = keys(failures, 'failures')
    status_conflicts = extraction_keys & failure_keys
    identity_counts = {}
    for path in identities:
        value = json.loads(path.read_text(encoding='utf-8'))
        status = value.get('status', 'invalid')
        identity_counts[status] = identity_counts.get(status, 0) + 1
    qualified_documents = 0
    qualified_transactions = 0
    zero_transaction_documents = 0
    quarantined_documents = 0
    quarantined_rows = 0
    for path in qualifications:
        value = json.loads(path.read_text(encoding='utf-8'))
        qualified = len(value.get('transactions', []))
        quarantined = len(value.get('quarantined', []))
        zero_transaction = int(
            value.get('qualification', {}).get('zero_transaction_document_count', 0))
        qualified_transactions += qualified
        zero_transaction_documents += zero_transaction
        quarantined_rows += quarantined
        qualified_documents += int(qualified > 0 or zero_transaction == 1)
        quarantined_documents += int(quarantined > 0)
    evidence_count = sum(1 for _ in evidence.glob('house_clerk/documents/*/*/*.json'))
    value = {
        'schema_version': 'house-review-run/v1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'parser_commit': args.parser_commit,
        'evidence_count': evidence_count,
        'extracted_count': len(extractions),
        'failure_count': len(failures),
        'status_conflict_count': len(status_conflicts),
        'pending_parse_count': max(evidence_count - len(extraction_keys | failure_keys), 0),
        'identity_counts': dict(sorted(identity_counts.items())),
        'qualification_count': len(qualifications),
        'paper_observation_count': len(paper_observations),
        'paper_failure_count': len(paper_failures),
        'paper_key_field_reread_count': len(paper_rereads),
        'paper_reread_failure_count': len(paper_reread_failures),
        'paper_temporal_failure_count': len(paper_temporal_failures),
        'qualified_document_count': qualified_documents,
        'qualified_transaction_count': qualified_transactions,
        'zero_transaction_document_count': zero_transaction_documents,
        'quarantined_document_count': quarantined_documents,
        'quarantined_row_count': quarantined_rows,
        'status': 'attention' if failures or quarantined_rows else 'ok',
    }
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    status_root = review / 'status'
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / 'house_clerk.json').write_text(content, encoding='utf-8')
    (status_root / 'summary.json').write_text(content, encoding='utf-8')


def select_holdings(args):
    import json, os
    from pathlib import Path
    evidence, review = Path(args.evidence_root), Path(args.review_root)
    parser_version=HOLDING_PARSER_VERSION
    pending=[]
    for path in evidence.glob('house_clerk/financial-reports/*/*/*.json'):
        value=json.loads(path.read_text(encoding='utf-8'))
        relative=Path(str(value['filing_year']))/value['document_id']/f"{value['sha256']}.json"
        extraction=review/'house_clerk/holding_extractions'/relative
        qualification=review/'house_clerk/holding_qualifications'/relative
        failure=review/'house_clerk/holding_failures'/relative
        if qualification.is_file() and extraction.is_file():
            try:
                if json.loads(extraction.read_text(encoding='utf-8')).get('parser_version') == parser_version:
                    continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        elif failure.is_file():
            try:
                if json.loads(failure.read_text(encoding='utf-8')).get('parser_version') == parser_version:
                    continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        pending.append((value.get('filed_date') or '', value['document_id'], path))
    pending.sort(reverse=True)
    Path(args.run_dir, 'holding-metadata.txt').write_text(
        ''.join(f'{item[2]}\n' for item in pending[:int(args.batch_size)]),
        encoding='utf-8')


def holding_status(args):
    import json, os
    from datetime import datetime, timezone
    from pathlib import Path
    evidence, review = Path(args.evidence_root), Path(args.review_root)
    metadata=list(evidence.glob('house_clerk/financial-reports/*/*/*.json'))
    extractions=list(review.glob('house_clerk/holding_extractions/*/*/*.json'))
    failures=list(review.glob('house_clerk/holding_failures/*/*/*.json'))
    identities=list(review.glob('house_clerk/holding_identities/*/*/*.json'))
    qualifications=list(review.glob('house_clerk/holding_qualifications/*/*/*.json'))
    section_observations=list(review.glob('house_clerk/holding_section_observations/*/*/*.json'))
    section_failures=list(review.glob('house_clerk/holding_section_failures/*/*/*.json'))
    def keys(paths, root):
        return {path.relative_to(root).as_posix() for path in paths}
    metadata_keys=keys(metadata, evidence/'house_clerk/financial-reports')
    extraction_keys=keys(extractions, review/'house_clerk/holding_extractions')
    failure_keys=keys(failures, review/'house_clerk/holding_failures')
    identity_keys=keys(identities, review/'house_clerk/holding_identities')
    qualification_keys=keys(qualifications, review/'house_clerk/holding_qualifications')
    if (extraction_keys != identity_keys or extraction_keys != qualification_keys or
            failure_keys & extraction_keys or
            (extraction_keys | failure_keys) - metadata_keys):
        raise SystemExit('House holding artifact states are inconsistent')
    pending_count=len(metadata_keys - (qualification_keys | failure_keys))
    batch=json.loads(Path(args.run_dir, 'house-holding-batch.json').read_text(
        encoding='utf-8'))
    eligible=holdings=quarantined=excluded=0
    for path in qualifications:
        value=json.loads(path.read_text(encoding='utf-8'))
        eligible += int(value.get('production_eligible') is True)
        holdings += len(value.get('holdings', []))
        quarantined += len(value.get('quarantined', []))
        excluded += len(value.get('excluded', []))
    value={'schema_version':'house-holding-run/v1',
           'generated_at':datetime.now(timezone.utc).isoformat(),
           'parser_commit':args.parser_commit, 'evidence_count':len(metadata),
           'extraction_count':len(extractions), 'failure_count':len(failures),
           'qualification_count':len(qualifications), 'eligible_report_count':eligible,
           'section_observation_count':len(section_observations),
           'section_failure_count':len(section_failures),
           'qualified_holding_count':holdings, 'excluded_row_count':excluded,
           'quarantined_row_count':quarantined, 'pending_parse_count':pending_count,
           'current_member_annual_count':batch['current_member_annual_count'],
           'remaining_archive_count':max(batch['unarchived_before_count']-
                                         batch['selected_count'], 0),
           'archived_this_run':batch['selected_count'],
           'status':'attention' if failures or quarantined or pending_count or
                                     batch['unarchived_before_count'] > batch['selected_count'] else 'ok'}
    target=review/'status/house_holdings.json'; target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,sort_keys=True,separators=(',',':')),encoding='utf-8')

HANDLERS = {'select': select, 'ocr': ocr, 'status': status,
            'select-holdings': select_holdings, 'holding-status': holding_status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=HANDLERS)
    parser.add_argument('--evidence-root', type=Path, default=os.environ.get('EVIDENCE_ROOT'))
    parser.add_argument('--review-root', type=Path, default=os.environ.get('REVIEW_ROOT'))
    parser.add_argument('--parser-commit', default=os.environ.get('PARSER_COMMIT'))
    parser.add_argument('--batch-size', type=int, default=os.environ.get('BATCH_SIZE', '25'))
    parser.add_argument('--year', default=(os.environ.get('SELECT_YEAR') or
                                          os.environ.get('UNISON_HOUSE_FILING_YEAR', '')))
    parser.add_argument('--paper-document-id', default=os.environ.get('PAPER_DOCUMENT_ID', ''))
    parser.add_argument('--github-output', type=Path, default=os.environ.get('GITHUB_OUTPUT'))
    parser.add_argument('--run-dir', type=Path, default=os.environ.get('RUNNER_TEMP', '.local'))
    args = parser.parse_args()
    if args.evidence_root is None or not args.evidence_root.is_dir():
        parser.error('evidence-root must be an existing directory')
    if args.mode != 'ocr' and (args.review_root is None or not args.review_root.is_dir()):
        parser.error('review-root must be an existing directory')
    if not 1 <= args.batch_size <= 25:
        parser.error('batch-size must be between 1 and 25')
    if args.year and not re.fullmatch(r'[0-9]{4}', args.year):
        parser.error('year must be a four-digit filing year')
    if args.mode in {'status', 'holding-status'} and not re.fullmatch(r'[0-9a-f]{40}', args.parser_commit or ''):
        parser.error('parser-commit must be a fixed Git commit')
    if args.mode == 'ocr' and args.github_output is None:
        parser.error('github-output is required for OCR planning')
    args.run_dir.mkdir(parents=True, exist_ok=True)
    HANDLERS[args.mode](args)
    return 0
