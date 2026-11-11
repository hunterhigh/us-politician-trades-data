"""Import maintainer-saved Senate PTR originals and manage the weekly inbox Issue."""
import argparse
from collections import defaultdict, Counter
from datetime import date, datetime, timezone
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import quote

from unison_snapshot.codec import encode, digest
from unison_snapshot.builder import build
from unison_snapshot.production_health import GitHub
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_candidate import build_senate_candidate, _canonical_sha256, _filed_date
from unison_snapshot.senate_identity import build_catalog_identities
from unison_snapshot.senate_reports import (
    PARSER_VERSION, PAPER_PAGE_MANIFEST_SCHEMA, archive_report, parse_electronic_ptr,
    _report_parser, _write_once,
)
from unison_snapshot.senate_paper import parse_paper_word_pages, tesseract_words

TITLE = 'Senate 本周待下载'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
STEPS = ('打开 efdsearch.senate.gov → 勾选同意条款 → Report Type 选 Periodic Transactions → '
         '起始日期填上次截止日 → 逐份打开并另存为网页（仅 HTML） → '
         '上传到 senate-inbox 分支的 inbox/ 目录。')
PAPER = ('纸质 PTR 上传完整 PDF，或一次上传全部页面图片。文件名：'
         '`报告编号__YYYY-MM-DD__申报人姓名.pdf`；图片为 '
         '`报告编号__YYYY-MM-DD__申报人姓名__页码.png`（也支持 jpg、gif、tif）。'
         '编号从报告网址复制，日期和姓名照搜索结果填写。')


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


def selected_artifacts(review, status):
    versions = status.get('report_source_sha256', {})
    extracted, failed = {}, {}
    for folder, target in [('extractions', extracted), ('report_failures', failed),
                           ('paper_report_failures', failed)]:
        for p in sorted((review / 'senate_efd' / folder).glob(f'*/*/{PARSER_VERSION}.json')):
            if versions.get(p.parent.parent.name, p.parent.name) == p.parent.name:
                target[p.parent.parent.name] = read(p)
    return extracted, failed


def electronic_report(path, content, previous):
    parser = _report_parser(content)
    title = next(text for tag, text in parser.headings if tag == 'h1')
    match = re.fullmatch(r'Periodic Transaction Report for (\d{2}/\d{2}/\d{4})'
                        r'(?: \(Amendment(?: ([1-9][0-9]*))?\))?', title)
    if match is None:
        raise SenateEfdError('不是可识别的电子 PTR 页面')
    label = datetime.strptime(match[1], '%m/%d/%Y').date().isoformat()
    amendment = (int(match[2]) if match[2] else 'unspecified' if 'Amendment' in title else None)
    heading = next(text for tag, text in parser.headings if tag == 'h2')
    name = re.sub(r'^The Honorable\s+', '', heading).split(' (')[0].strip()
    filed = next(text for text in parser.text_segments if re.fullmatch(r'Filed \d{2}/\d{2}/\d{4} @ .+', text))
    listed = _filed_date(filed)
    return {**previous, 'filer_name': name, 'portal_listed_date': listed,
            'report_label_date': label, 'report_amendment_number': amendment,
            'access_method': 'electronic_ptr'}


def paper_original(evidence, report, files, now):
    import fitz
    from PIL import Image
    originals = []
    for p in files:
        raw = p.read_bytes(); sha = digest(raw)
        relative = f'senate_efd/manual_uploads/{sha}{p.suffix.lower()}'
        originals.append(relative)
    if len(files) == 1 and files[0].suffix.lower() == '.pdf':
        pdf = files[0].read_bytes()
    else:
        document = fitz.open()
        for p in files:
            image = Image.open(p)
            page = document.new_page(width=image.width, height=image.height)
            image_data = BytesIO(); image.convert('RGB').save(image_data, format='PNG')
            page.insert_image(page.rect, stream=image_data.getvalue())
        pdf = document.tobytes(); document.close()
    metadata = archive_report(evidence, report, pdf, {'content-type': 'application/pdf'},
                              now, acquisition='manual_browser')
    document = fitz.open(stream=pdf, filetype='pdf')
    pages, words = [], []
    for n, page in enumerate(document, 1):
        pix = page.get_pixmap(matrix=fitz.Matrix(3400/page.rect.width, 4400/page.rect.height))
        image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples).resize((3400, 4400))
        output = BytesIO(); image.save(output, format='GIF'); raw = output.getvalue(); sha = digest(raw)
        relative = f"senate_efd/paper_pages/{report['document_id']}/{n}/{sha}.gif"
        _write_once(evidence / relative, raw)
        pages.append({'page_number': n, 'sha256': sha, 'archive_path': relative})
        _write_once(evidence / relative.replace('.gif', '.metadata.json'), encode({
            'schema_version': 'senate-efd-paper-page-archive/v1',
            'document_id': report['document_id'], 'page_number': n, 'page_count': len(document),
            'sha256': sha, 'width': 3400, 'height': 4400, 'archive_path': relative,
            'acquisition': 'manual_browser', 'derived_from': originals}))
        words.append({'page_number': n, 'width': 3400, 'height': 4400,
                      'words': tesseract_words(evidence / relative)})
    document.close()
    manifest = {'schema_version': PAPER_PAGE_MANIFEST_SCHEMA, 'source_id': 'senate_efd',
        'document_id': report['document_id'], 'parser_version': PARSER_VERSION,
        'entrypoint_source_sha256': metadata['sha256'], 'evidence_complete': True,
        'page_count': len(pages), 'pages': pages, 'acquisition': 'manual_browser',
        'originals': originals}
    manifest['manifest_sha256'] = digest(encode(manifest))
    write(evidence / f"senate_efd/paper_pages/{report['document_id']}/manifest.json", manifest)
    return metadata, parse_paper_word_pages(report, manifest, words)


def receive(args):
    review, evidence, state = args.review_root, args.evidence_root, args.state_root
    status = read(review / 'status/senate_efd.json')
    discovery = read(review / f"senate_efd/discoveries/{status['catalog_sha256']}.json")
    reports = {r['document_id']: r for r in discovery['reports']}
    ledger_path = review / 'senate_efd/manual_imports.json'
    ledger = read(ledger_path) if ledger_path.is_file() else {}
    extracted, failed = selected_artifacts(review, status)
    result = {'imported': [], 'duplicates': [], 'unrecognized': [], 'files': []}
    groups = defaultdict(list)
    now = datetime.now(timezone.utc).isoformat()
    for p in sorted(args.inbox.iterdir()):
        if p.name == '.gitkeep':
            continue
        raw = p.read_bytes()
        result['files'].append({'name': p.name,
            'sha': hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()})
        original = f'senate_efd/manual_uploads/{digest(raw)}{p.suffix.lower()}'
        _write_once(evidence / original, raw)
        _write_once(evidence / f'senate_efd/manual_uploads/{digest(raw)}.json', encode({
            'acquisition': 'manual_browser', 'sha256': digest(raw), 'archive_path': original}))
        identifiers = set(re.findall(UUID, p.name.lower()))
        if p.suffix.lower() in {'.html', '.htm'}:
            try:
                header = re.split(r'<body\b', raw.decode('utf-8-sig'), flags=re.I)[0]
            except UnicodeDecodeError:
                result['unrecognized'].append({'files': [p.name], 'reason': 'HTML 不是 UTF-8 编码'})
                continue
            identifiers.update(re.findall(r'https://efdsearch\.senate\.gov/search/view/(?:ptr|paper)/('+UUID+r')/',
                                         header, re.I))
        if len(identifiers) == 1:
            groups[identifiers.pop()].append(p)
        else:
            result['unrecognized'].append({'files': [p.name], 'reason': '无法取得唯一 eFD 报告编号；请在文件名保留报告网址中的编号'})
    batches = []
    for document_id, files in groups.items():
        batches.extend((document_id, [p]) for p in files if p.suffix.lower() in {'.html', '.htm', '.pdf'})
        images = [p for p in files if p.suffix.lower() not in {'.html', '.htm', '.pdf'}]
        if images:batches.append((document_id, images))
    for document_id, files in batches:
        names = [p.name for p in files]
        if document_id in ledger:
            result['duplicates'].append({'document_id': document_id, 'files': names})
            continue
        try:
            report = dict(reports.get(document_id, {'source_id': 'senate_efd',
                'report_type': 'periodic_transaction_report', 'document_id': document_id,
                'catalog_index': len(reports), 'office': 'Not supplied by manual upload'}))
            if len(files) == 1 and files[0].suffix.lower() in {'.html', '.htm'}:
                content = files[0].read_bytes()
                report = electronic_report(files[0], content, report)
                report['document_url'] = f'https://efdsearch.senate.gov/search/view/ptr/{document_id}/'
                metadata = archive_report(evidence, report, content, {'content-type': 'text/html'},
                                          now, acquisition='manual_browser')
                extraction = parse_electronic_ptr(metadata, content)
            else:
                if not all(p.suffix.lower() in {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.tif', '.tiff'} for p in files):
                    raise SenateEfdError('支持 HTML、PDF 或纸表图片')
                if document_id not in reports:
                    fields = files[0].stem.split('__')
                    report.update(portal_listed_date=date.fromisoformat(fields[1]).isoformat(),
                                  filer_name=fields[2], report_label_date=None, report_amendment_number=None)
                report.update(access_method='paper_ptr',
                    document_url=f'https://efdsearch.senate.gov/search/view/paper/{document_id}/')
                files.sort(key=lambda p: int(p.stem.split('__')[-1]) if p.stem.split('__')[-1].isdigit() else 0)
                metadata, extraction = paper_original(evidence, report, files, now)
            sha = metadata['sha256']
            write(review / f'senate_efd/extractions/{document_id}/{sha}/{PARSER_VERSION}.json', extraction)
            extracted[document_id] = extraction; failed.pop(document_id, None)
            reports[document_id] = report
            receipt = {'document_id': document_id, 'source_sha256': sha, 'acquisition': 'manual_browser',
                       'files': names, 'filed_date': report['portal_listed_date'], 'imported_at': now}
            ledger[document_id] = receipt
            result['imported'].append(receipt)
        except (RuntimeError, ValueError, OSError, IndexError, StopIteration) as error:
            result['unrecognized'].append({'files': names, 'reason': str(error) or type(error).__name__})
    if result['imported']:
        discovery = {'schema_version': 'senate-efd-discovery/v1', 'source_id': 'senate_efd',
                     'records_total': len(reports), 'reports': list(reports.values())}
        sha = _canonical_sha256(discovery)
        discovery['metadata'] = {'sha256': sha, 'retrieved_at': now, 'page_count': 0,
                                 'acquisition': 'manual_browser', 'artifact_role': 'manual_report_index'}
        write(evidence / f'senate_efd/catalog/{sha}.json', discovery['metadata'])
        write(review / f'senate_efd/discoveries/{sha}.json', discovery)
        roster_sha = status['identity_roster_sha256']
        roster = read(evidence / f'senate_efd/members/{roster_sha}.roster.json')
        congress_sha = status.get('identity_congress_roster_sha256')
        congress = read(evidence / f'senate_efd/congress_members/{congress_sha}/roster.json') if congress_sha else None
        identities = build_catalog_identities(discovery, roster, congress)
        binding = hashlib.sha256(f"{roster_sha}:{congress_sha or ''}".encode()).hexdigest()
        write(review / f'senate_efd/identities/{sha}/{binding}.json', identities)
        status.update(catalog_sha256=sha, catalog_record_count=len(reports), generated_at=now,
            acquisition='manual_browser', identity_binding_sha256=binding,
            identity_counts=identities['identity_counts'], report_entrypoint_count=len(reports),
            report_entrypoint_pending_count=0, report_entrypoint_failure_count=0,
            report_evidence_count=len(extracted), report_extraction_failure_count=len(failed),
            extracted_transaction_count=sum(len(x['transactions']) for x in extracted.values()),
            report_source_sha256={k:v['source_sha256'] for k,v in {**failed, **extracted}.items()},
            detail='Maintainer-saved PTR originals imported; existing annual and PTR inventory retained.')
        write(review / 'status/senate_efd.json', status)
        prior = read(state / 'status/senate_efd.json')
        prior.update(acquisition='manual_browser', run_at=now, last_successful_collection_at=now,
            status='catalog_ready_for_review', catalog={'status': 'ok', 'sha256': sha,
                'record_count': len(reports), 'retrieved_at': now, 'page_count': 0},
            reports={'entrypoint_count': len(reports), 'pending_count': 0},
            detail='Senate PTR files acquired manually in a browser and imported automatically.')
        write(state / 'status/senate_efd.json', prior)
        write(ledger_path, ledger)
    write(args.result, result)
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a') as stream:
            stream.write(f"imported={len(result['imported'])}\nfiles={len(result['files'])}\n")
    print(json.dumps({k:len(v) for k,v in result.items()}))


def qualify(args):
    candidate_path = args.review_root / 'candidates/sources/senate_efd-current.json'
    candidate, audit = build_senate_candidate(args.review_root,
        read(args.state_root / 'status/senate_efd.json'), read(candidate_path), args.evidence_root)
    cutoff = candidate['meta']['data_cutoff_at']
    bundle = build(candidate, generated_at=cutoff, allow_production=True)
    candidate['meta'].update(snapshot_id=bundle.manifest['snapshot_id'], generated_at=cutoff)
    audit.update(candidate_snapshot_id=bundle.manifest['snapshot_id'], candidate_sha256=digest(encode(candidate)))
    write(candidate_path, candidate)
    write(args.review_root / 'senate_efd/qualifications/current.json', audit)
    write(args.review_root / ('senate_efd/qualifications/'+audit['catalog_sha256']+'/'+
        audit['identity_binding_sha256']+'/'+PARSER_VERSION+'/'+audit['builder_version']+'/'+
        audit['candidate_snapshot_id']+'.json'), audit)
    result = read(args.result)
    counts = Counter(row['document_id'] for row in audit['qualified_rows'])
    for report in result['imported']:
        report['transaction_count'] = counts[report['document_id']]
    write(args.result, result)
    print(json.dumps({'transactions':len(candidate['transactions']), 'holdings':len(candidate['reported_holdings'])}))


def reminder(args, api):
    status = read(args.review_root / 'status/senate_efd.json')
    extracted, _ = selected_artifacts(args.review_root, status)
    latest = max(_filed_date(x['filed_at_raw']) for x in extracted.values())
    candidate = read(args.review_root / 'candidates/sources/senate_efd-current.json')
    cutoff = datetime.fromisoformat(candidate['meta']['data_cutoff_at'].replace('Z','+00:00'))
    remaining = max(0, math.ceil(float(os.environ.get('SENATE_MAX_STALE_DAYS','30')) -
                               (datetime.now(timezone.utc)-cutoff).total_seconds()/86400))
    body = (f'已导入的最新申报日期：**{latest}**（下载时从这个日期之后开始搜；起始日期填此日以覆盖当天）。\n\n'
            f'Senate 宽限期剩余：**{remaining} 天**。数据截止时间：{cutoff.isoformat()}。\n\n'
            f'{STEPS}\n\n{PAPER}\n\n电子 HTML 请保留浏览器保存的来源网址；若浏览器没有保留，在文件名中加入报告网址中的编号。')
    issues = []; page = 1
    while True:
        batch = api.request(f'/issues?state=all&per_page=100&page={page}')
        issues.extend(i for i in batch if i['title'] == TITLE and 'pull_request' not in i)
        if len(batch) < 100:break
        page += 1
    if issues:
        issue = next((i for i in issues if i['state']=='open'), issues[0])
        api.request('/issues/'+str(issue['number']), 'PATCH', {'body':body, 'state':'open'})
        for other in issues:
            if other['number'] != issue['number'] and other['state']=='open':
                api.request('/issues/'+str(other['number']), 'PATCH', {'state':'closed'})
    else:
        issue = api.request('/issues', 'POST', {'title':TITLE, 'body':body})
    return issue['number']


def finish(args, api):
    result = read(args.result)
    if not result['files']:return
    issue = reminder(args, api)
    imported, duplicates, errors = result['imported'], result['duplicates'], result['unrecognized']
    completed = imported if args.outcome == 'success' else []
    text = (f"导入报告：{len(completed)} 份；合格交易：{sum(r.get('transaction_count',0) for r in completed)} 笔"
            '（按本次报告计数，不代表净新增交易）。\n\n'
            + '\n'.join(f"- {r['document_id']}：{r.get('transaction_count',0)} 笔" for r in completed)
            + f'\n\n跳过重复报告：{len(duplicates)} 份。\n'
            + '\n'.join(f"- {r['document_id']}：已导入，跳过" for r in duplicates)
            + f'\n\n无法识别文件：{sum(len(e["files"]) for e in errors)} 个。\n'
            + '\n'.join(f"- {', '.join(e['files'])}：{e['reason']}" for e in errors))
    text += ('\n\n导入和组装已完成，正式发布由现有流水线继续。' if args.outcome=='success' and imported
             else '\n\n无新增报告。' if args.outcome=='success' else
             '\n\n工作流失败，inbox 原件保留；详见运行日志。')
    text += '\n\n运行：'+os.environ.get('GITHUB_SERVER_URL','https://github.com')+'/'+args.repository+'/actions/runs/'+os.environ['GITHUB_RUN_ID']
    api.request(f'/issues/{issue}/comments','POST',{'body':text})
    if args.outcome == 'success':
        for f in result['files']:
            api.request('/contents/inbox/'+quote(f['name'],safe=''), 'DELETE', {
                'message':'Senate: archive inbox originals in evidence', 'branch':'senate-inbox', 'sha':f['sha']})
        if not errors:api.request(f'/issues/{issue}','PATCH',{'state':'closed'})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['receive','qualify','remind','finish'])
    p.add_argument('--review-root',type=Path,required=True)
    p.add_argument('--evidence-root',type=Path)
    p.add_argument('--state-root',type=Path)
    p.add_argument('--inbox',type=Path)
    p.add_argument('--result',type=Path)
    p.add_argument('--repository',default=os.environ.get('GITHUB_REPOSITORY'))
    p.add_argument('--outcome',default='success')
    args = p.parse_args()
    if args.operation in {'receive','qualify'}:
        {'receive':receive,'qualify':qualify}[args.operation](args)
    else:
        api = GitHub(args.repository, os.environ['GH_TOKEN'])
        if args.operation=='remind':print(reminder(args,api))
        else:finish(args,api)


if __name__ == '__main__':main()
