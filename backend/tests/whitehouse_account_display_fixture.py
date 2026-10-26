"""Generated native PDF contracts; these are not official accuracy gold."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from test_whitehouse_owner_correction import encoded, fixture as owner_fixture


def write_files(root, files):
    for relative, raw in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def fixture(*, extra=None, asset='Example Common Stock (EXM)',
            account_title='Investment account #7', inherit_legacy=True, pdf_mutator=None):
    """Return (historical fact, expected fact, review bytes, evidence bytes, base)."""
    import pymupdf
    from scripts.whitehouse_coverage import build_coverage
    from unison_snapshot.whitehouse_278t import parse_generic_whitehouse_278t_pdf, PARSER_VERSION
    from unison_snapshot.whitehouse_disclosures import parse_disclosure_index
    from unison_snapshot.oge_reports import parse_table_rows
    from unison_snapshot.whitehouse_native_correction import _historical_projection, _current_projection

    _, _, original_review, evidence, base = owner_fixture(asset=asset)
    evidence = deepcopy(evidence)
    old_pdf = next(path for path in evidence if path.endswith('.pdf'))
    metadata = json.loads(evidence.pop(old_pdf[:-4] + '.json'))
    evidence.pop(old_pdf)
    url, document_id = metadata['document_url'], metadata['document_id']
    document = pymupdf.open()
    page = document.new_page(width=700, height=792)
    page.insert_text((30, 40), "Periodic Transaction Report (OGE Form 278-T)\nFiler's Information\n"
        "Example, Ada\nDirector - White House\nFiler's Certification\nDate: 06/13/2026\n"
        "Agency Ethics Official's Opinion\nDate: 06/15/2026")
    page = document.new_page(width=700, height=792)
    page.insert_text((30, 40), 'Transactions')
    edges = [30, 50, 350, 410, 480, 550, 680]
    table = [['#', 'DESCRIPTION', 'TYPE', 'DATE', 'NOTIFICATION', 'AMOUNT'],
             ['', account_title, '', '', '', '']]
    if extra == 'scope':
        table.append(['', 'Spouse retirement account unclear', '', '', '', ''])
    table.append(['1', asset, 'Purchase', '06/01/2026', 'No', '$1,001 - $15,000'])
    if extra == 'unrelated':
        table.append(['2', 'Unrelated Company (OTH)', 'Purchase', '06/02/2026', 'No', 'unread amount'])
    if extra == 'two_accounts':
        table.extend([['', 'Investment account #8', '', '', '', ''],
                      ['2', asset, 'Purchase', '06/01/2026', 'No', '$1,001 - $15,000']])
    for x in edges:
        page.draw_line((x, 100), (x, 100 + len(table) * 25))
    for y in range(100, 101 + len(table) * 25, 25):
        page.draw_line((30, y), (680, y))
    for index, cells in enumerate(table):
        for x, text in zip(edges, cells):
            page.insert_text((x + 2, 115 + index * 25), text, fontsize=8)
    raw = document.tobytes()
    document.close()
    if pdf_mutator is not None:
        raw = pdf_mutator(raw)
        if not isinstance(raw, bytes):
            raise TypeError('PDF fixture mutation must return source bytes')
    sha = hashlib.sha256(raw).hexdigest()
    pdf_path = f'whitehouse/disclosures/reports/{document_id[7:]}/{sha}.pdf'
    folder = f'whitehouse/extractions/{document_id[7:]}/{sha}'
    metadata.update(sha256=sha, byte_length=len(raw), archive_path=pdf_path)
    evidence.update({pdf_path: raw, pdf_path[:-4] + '.json': encoded(metadata)})
    index = parse_disclosure_index(evidence[f"whitehouse/disclosures/pages/{metadata['page_sha256']}.html"])
    options = dict(document_id=document_id, source_url=url, source_sha256=sha,
        filer_name='Example, Ada', amended_label=metadata['link_label'])
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_files(root, evidence)
        fresh = parse_generic_whitehouse_278t_pdf(root / pdf_path, **options)
        rows, bad = parse_table_rows([(row['page_number'], row['cells'])
            for row in fresh['source_rows']], source_sha=sha)
        assert len(rows) == (2 if extra == 'two_accounts' else 1), (rows, bad)
        legacy = dict(schema_version='whitehouse-278t-extraction/v1', parser_version=PARSER_VERSION,
            document_id=document_id, source_url=url, source_sha256=sha, filer_name='Example, Ada',
            transactions=rows, quarantined=bad)
        native = (parse_generic_whitehouse_278t_pdf(root / pdf_path, legacy_extraction=legacy, **options)
                  if inherit_legacy else fresh)
        review = {f'{folder}/whitehouse-278t-native-roles-v5.json': encoded(native),
            'oge/whitehouse/coverage-current.json': original_review['oge/whitehouse/coverage-current.json'],
            'whitehouse/disclosures/index-current.json': encoded(index),
            'whitehouse/extraction-status.json': original_review['whitehouse/extraction-status.json']}
        if inherit_legacy:
            review[f'{folder}/whitehouse-278t-hybrid-geometry-v2.json'] = encoded(legacy)
        write_files(root, review)
        batch = dict(schema_version='whitehouse-public-disclosures-batch/v1', indexed_count=1,
            reports=[metadata], failures=[])
        review['whitehouse/coverage-current.json'] = encoded(build_coverage(index, batch, root, generic_trade=True))
    old = _historical_projection(native, rows[0], base['people'][0]['id'])
    target = next((row for row in native['transactions']
                   if row.get('row_number') == rows[0]['row_number']), None)
    # The scope-barrier negative has no eligible native target. This describes
    # the forbidden proposed correction, without inserting it into observations.
    expected = (_current_projection(native, target, old['person_id']) if target else
                {**old, 'owner': 'Undisclosed', 'asset_name': old['asset_name'] + ' — ' + account_title})
    base['transactions'] = [old] if inherit_legacy else []
    return old, expected, review, evidence, base
