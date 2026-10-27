"""Generated original PDF contracts; no official accuracy or OCR claim."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
import pdfplumber

from test_oge_candidate import base as empty_base
from test_oge_native_cover_roles import native_cover
from unison_snapshot.oge import parse_catalog_page, build_catalog, archive_catalog
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.oge_candidate import build_oge_candidate, _person_id, _identity_key
from unison_snapshot.whitehouse_native_correction import _historical_projection
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError
from unison_snapshot.pipeline_qa.oge_owner_correction import verified_oge_owner_corrections

DOCUMENT = '42300720a4227e9e85258e77002dd1b3'
URL = f'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{DOCUMENT}/$FILE/Example-278T.pdf'
CUTOFF = '2026-06-20T00:00:00Z'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode()


def write_files(root, files):
    for path, raw in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def fixture(*, asset='Example Common Stock (EXM)', heading=None, historical=True,
            pdf_mutator=None, extra_rows=None):
    """Return (old, proposed owner correction, review bytes, evidence bytes, base).

    Scope negatives deliberately return a proposed change that the normal
    candidate did not authorize. The original PDF and caches are never forged.
    """
    with tempfile.TemporaryDirectory() as directory:
        cover_path = Path(directory) / 'cover.pdf'
        native_cover(cover_path, filer_name='Example, Ada', position_title='Director', agency='Example Agency')
        pdf = pymupdf.open(stream=cover_path.read_bytes(), filetype='pdf')
    page = pdf.new_page(width=792, height=792)
    page.insert_text((30, 40), 'Transactions')
    edges = [30, 50, 350, 410, 480, 550, 720]
    rows = [['#', 'DESCRIPTION', 'TYPE', 'DATE', 'NOTIFICATION', 'AMOUNT']]
    if heading:
        rows.append(['', heading, '', '', '', ''])
    rows.append(['1', asset, 'Purchase', '06/01/2026', 'No', '$1,001 - $15,000'])
    rows.extend(extra_rows or [])
    step = min(25, 650 / len(rows))
    for x in edges:
        page.draw_line((x, 100), (x, 100 + step * len(rows)))
    for index in range(len(rows) + 1):
        page.draw_line((30, 100 + step * index), (720, 100 + step * index))
    for index, values in enumerate(rows):
        for x, value in zip(edges, values):
            if value:
                page.insert_text((x + 2, 100 + step * (index + .6)), value, fontsize=8)
    raw = pdf.tobytes(); pdf.close()
    if pdf_mutator is not None:
        raw = pdf_mutator(raw)
        if not isinstance(raw, bytes):
            raise AssertionError('Fixture mutator must return source bytes before hashing')
    sha = hashlib.sha256(raw).hexdigest()
    payload = {'draw': 1, 'recordsTotal': 1, 'recordsFiltered': 1, 'data': [{
        'type': f"<a href='{URL}'>278 Transaction</a>", 'name': 'Example, Ada',
        'agency': 'Example Agency', 'title': 'Director', 'level': 'I',
        'docDate': '2026-06-20T00:00:00', 'amended': ''}]}
    catalog = build_catalog([parse_catalog_page(payload, start=0, length=1)])
    record = catalog['transactions'][0]
    pdfpath = f'oge/reports/{DOCUMENT}/{sha}.pdf'
    metadata = {'schema_version': 'oge-278t-archive/v1', 'source_id': 'oge',
        'document_id': DOCUMENT, 'document_url': URL, 'sha256': sha,
        'byte_length': len(raw), 'archive_path': pdfpath, 'headers': {}, 'retrieved_at': CUTOFF,
        **{key: record[key] for key in ('filer_name', 'agency', 'position_title',
            'catalog_added_date', 'amended_label', 'pending_final_oge_disposition')}}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_files(root, {pdfpath: raw, pdfpath[:-4] + '.json': encoded(metadata)})
        catalog_meta = archive_catalog(root, [(0, 1, encoded(payload), {'content-type': 'application/json'})],
                                       catalog, retrieved_at=CUTOFF)
        legacy = parse_archived_pdf(root, root / (pdfpath[:-4] + '.json'))
        native = parse_archived_pdf(root, root / (pdfpath[:-4] + '.json'), generic=True)
        if (legacy['parser_version'], native['parser_version']) != ('oge-278t-pdf/v2', 'oge-278t-pdf/v7'):
            raise AssertionError('Fixture must use actual ordinary native source selection')
        prior, _ = build_oge_candidate(catalog, [legacy], empty_base(),
            data_cutoff_at=CUTOFF, evidence_root=root)
        if historical:
            prior['transactions'] = [_historical_projection(legacy, legacy['transactions'][0],
                _person_id(_identity_key(record)))]
        old = prior['transactions'][0]
        expected = {**old, 'owner': 'Undisclosed'}
        current, audit = build_oge_candidate(catalog, [native], prior, data_cutoff_at=CUTOFF,
            evidence_root=root, legacy_extractions_by_document={DOCUMENT: legacy})
        if heading is None and [row for row in current['transactions'] if row['id'] == old['id']] != [expected]:
            raise AssertionError('Normal native candidate must independently approve this correction')
        evidence = {path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob('*') if path.is_file()}
        folder = f'oge/extractions/{DOCUMENT}/{sha}'
        review = {f'{folder}/oge-278t-pdf-v2.json': encoded(legacy),
            f'{folder}/oge-278t-pdf-v7.json': encoded(native),
            'status/oge.json': encoded({'catalog_sha256': catalog_meta['sha256']}),
            'oge/qualifications/current.json': encoded(audit)}
    return old, expected, review, evidence, prior


class OgeOwnerCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()

    def verify(self, data=None, *, edit=None, previous_people=None):
        old, current, review, evidence, board = deepcopy(data or self.data)
        if edit:
            edit(old, current, review, evidence, board)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            write_files(repo, {**evidence, **review})
            raw = encoded(board); sha = hashlib.sha256(raw).hexdigest()
            write_files(repo, {'manifest.json': encoded({'is_demo': False, 'board': sha}),
                              f'board/{sha}.json': raw})
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args],
                    env=dict(os.environ, GIT_NO_LAZY_FETCH='1'), stderr=subprocess.STDOUT).decode().strip()
            for args in (('init', '-q'), ('config', 'user.email', 'contract@example.invalid'),
                         ('config', 'user.name', 'Contract'), ('add', '.'), ('commit', '-qm', 'source contract')):
                git(*args)
            commit = git('rev-parse', 'HEAD')
            return verified_oge_owner_corrections(old={old['id']: old}, current={current['id']: current},
                repo=repo, evidence_commit=commit, review_commit=commit, main_commit=commit,
                previous_people=board['people'] if previous_people is None else previous_people)

    def test_real_native_fixed_git_owner_only_and_repeat(self):
        result = self.verify()
        self.assertEqual(result[0]['changed_fields'], ['owner'])
        self.assertEqual(result[0]['old_id'], result[0]['new_id'])
        self.assertEqual(self.verify(), result)

    def test_both_source_classifier_epochs_reconstruct_full_endnote_projection(self):
        for historical, instrument in ((True, 'Bond'), (False, 'Stock')):
            data = fixture(asset='Example Common Stock See Endnote (EXM)', historical=historical)
            self.assertEqual(data[0]['instrument_type'], instrument)
            self.assertEqual(self.verify(data)[0]['changed_fields'], ['owner'])

    def test_full_source_reconstruction_rejects_even_self_consistent_main_forgeries(self):
        for field, value in (('amount_low', 1002), ('instrument_type', 'Bond'),
                             ('id', 'oge-278t:' + '0' * 24), ('option_type', 'call'),
                             ('unexpected_null', None)):
            def edit(old, current, review, evidence, board):
                old[field] = current[field] = board['transactions'][0][field] = value
            with self.subTest(field=field), self.assertRaises(InventoryError):
                self.verify(edit=edit)

    def test_null_key_representation_and_fixed_person_annotations_are_preserved(self):
        def edit(old, current, review, evidence, board):
            for value in (old, current, board['transactions'][0]):
                for field in ('option_type', 'strike_price', 'expiration_date', 'position_effect_basis'):
                    value.pop(field, None)
            board['people'][0].update(portrait_url='https://example.invalid/portrait', priority=True,
                                      priority_reason='existing official snapshot annotation')
        self.assertEqual(self.verify(edit=edit)[0]['changed_fields'], ['owner'])

    def test_explicit_and_unknown_scope_cannot_be_corrected(self):
        for heading in ('Filer assets', 'Unclear owner assets'):
            with self.subTest(heading=heading), self.assertRaises(InventoryError):
                self.verify(fixture(heading=heading))

    def test_full_original_scope_rejects_image_native_and_neutral_external_declarations(self):
        for kind in ('image', 'native', 'neutral', 'extra_page'):
            def mutate(raw):
                document = pymupdf.open(stream=raw, filetype='pdf')
                if kind == 'image':
                    picture = pymupdf.open(); p = picture.new_page(width=160, height=30)
                    p.insert_text((3, 20), 'Filer assets', fontsize=12)
                    pixels = p.get_pixmap().tobytes('png'); picture.close()
                    document[1].insert_image(pymupdf.Rect(30, 60, 190, 90), stream=pixels)
                elif kind == 'extra_page':
                    document.new_page(width=792, height=792).insert_text((30, 50), 'Filer assets')
                else:
                    document[1].insert_text((30, 80), 'Filer assets' if kind == 'native' else 'Additional information')
                result = document.tobytes(); document.close(); return result
            data = fixture(pdf_mutator=mutate)
            metadata = json.loads(next(raw for path, raw in data[3].items()
                if '/reports/' in path and path.endswith('.json')))
            source = next(raw for path, raw in data[3].items() if path.endswith('.pdf'))
            from unison_snapshot.oge_278t_page_roles import observe_native_page_role
            import io
            with pdfplumber.open(io.BytesIO(source)) as pdf:
                role = observe_native_page_role(pdf.pages[0], 1, source_sha256=metadata['sha256'],
                    cover_identity_context={'schema_version':'oge-278t-cover-identity-context/v1',
                        'source_sha256':metadata['sha256'], **{key:metadata[key] for key in
                            ('filer_name','agency','position_title')}})
                self.assertEqual(role['status'], 'non_table')
                self.assertEqual(role['role'], 'fixed_native_administrative_cover_2019')
            # The unchanged ordinary candidate still approves this same-ID
            # proposal. Only the destructive migration needs closed scope.
            reason = ('visibility/object scope is unknown' if kind == 'image' else
                      'page has no complete' if kind == 'extra_page' else
                      'unexplained words outside transaction tables')
            with self.subTest(kind=kind), self.assertRaisesRegex(InventoryError, reason):
                self.verify(data)

    def test_original_scope_cannot_be_self_signed_by_a_review_receipt(self):
        def edit(old, current, review, evidence, board):
            path = 'oge/qualifications/current.json'
            value = json.loads(review[path]); value['owner_scope_complete'] = True
            value['native_objects_fully_accounted'] = True
            review[path] = encoded(value)
        # An audit marker is not an input to source proof, in either direction.
        self.assertEqual(self.verify(edit=edit)[0]['changed_fields'], ['owner'])

    def test_table_cell_ownership_does_not_explain_unknown_nonfinancial_rows(self):
        for label, x, size in (('Filer assets', 32, 3), ('Additional information', 32, 1.5),
                               ('Additional information', 482, 4)):
            def mutate(raw):
                document = pymupdf.open(stream=raw, filetype='pdf')
                document[1].insert_text((x, 140), label, fontsize=size)
                result = document.tobytes(); document.close(); return result
            data = fixture(heading=' ', pdf_mutator=mutate)
            # All glyphs really fit inside a previously blank source cell.
            native = json.loads(next(raw for path, raw in data[2].items() if path.endswith('v7.json')))
            self.assertIn(label, native['source_rows'][1]['cells'])
            with self.subTest(label=label, x=x), self.assertRaisesRegex(InventoryError,
                    'unexplained nonfinancial table content'):
                self.verify(data)

    def test_native_source_proof_does_not_invoke_ocr(self):
        with patch.object(pymupdf.Page, 'get_textpage_ocr', side_effect=AssertionError('no OCR')) as ocr:
            self.assertEqual(self.verify()[0]['changed_fields'], ['owner'])
        ocr.assert_not_called()

    def test_unrelated_bad_financial_amount_remains_quarantined_without_blocking_owner(self):
        data = fixture(extra_rows=[['2', 'Independent Company', 'Purchase', '06/02/2026',
                                     'No', 'unread amount']])
        legacy = json.loads(next(raw for path, raw in data[2].items() if path.endswith('v2.json')))
        native = json.loads(next(raw for path, raw in data[2].items() if path.endswith('v7.json')))
        self.assertEqual(len(legacy['quarantined']), 1)
        self.assertEqual(len(native['quarantined']), 1)
        self.assertEqual(native['quarantined'][0]['cells'][-1], 'unread amount')
        self.assertEqual(self.verify(data)[0]['changed_fields'], ['owner'])

    def test_geometry_budget_supports_forty_regular_rows_and_rejects_before_membership(self):
        data = fixture(extra_rows=[[str(n), f'Asset{n}', 'Purchase', '06/02/2026', 'No',
                                     '$1,001-$15,000'] for n in range(2, 41)])
        self.assertEqual(self.verify(data)[0]['changed_fields'], ['owner'])
        with patch('unison_snapshot.pipeline_qa.oge_owner_correction.MAX_SCOPE_COMPARISONS', 1), \
                self.assertRaisesRegex(InventoryError, 'comparison budget exceeded'):
            self.verify()
        with patch('unison_snapshot.pipeline_qa.oge_owner_correction.MAX_SOURCE_ROWS', 1), \
                self.assertRaisesRegex(InventoryError, 'row count exceeds budget'):
            self.verify()

    def test_wrong_source_and_full_saved_cache_are_not_receipt_authority(self):
        for kind in ('pdf', 'metadata', 'cache', 'url', 'person'):
            def edit(old, current, review, evidence, board):
                if kind == 'pdf':
                    path = next(path for path in evidence if path.endswith('.pdf'))
                    evidence[path] += b'\nchanged original\n'
                elif kind == 'metadata':
                    path = next(path for path in evidence if '/reports/' in path and path.endswith('.json'))
                    value = json.loads(evidence[path]); value['agency'] = 'Changed Agency'; evidence[path] = encoded(value)
                elif kind == 'cache':
                    path = next(path for path in review if path.endswith('v7.json'))
                    value = json.loads(review[path]); value['transactions'][0]['asset_name'] = 'FORGED ASSET'
                    review[path] = encoded(value)
                elif kind == 'url':
                    for value in (old, current, board['transactions'][0]): value['source_url'] = 'https://example.invalid/bad.pdf'
                else:
                    board['people'][0]['display_name'] = 'Someone Else'
            with self.subTest(kind=kind), self.assertRaises(InventoryError):
                self.verify(edit=edit)

    def test_missing_refs_and_other_sources_do_not_get_oge_proof(self):
        old, current, _, _, _ = self.data
        with self.assertRaises(InventoryError):
            verified_oge_owner_corrections(old={old['id']: old}, current={current['id']: current},
                repo=None, evidence_commit=None, review_commit=None)
        other = {**old, 'filing_id': 'wh-url:' + 'a' * 24}
        self.assertEqual(verified_oge_owner_corrections(old={other['id']: other},
            current={other['id']: {**other, 'owner': 'Undisclosed'}}, repo=None,
            evidence_commit=None, review_commit=None), [])

    def test_historical_source_keeps_original_catalog_membership_and_full_identity_context(self):
        def historical(data, current_rows=None):
            old, current, review, evidence, board = deepcopy(data)
            normalized = next(path for path in evidence if '/catalog/normalized/' in path)
            old_sha = Path(normalized).stem
            catalog = json.loads(evidence[normalized])
            metadata = json.loads(evidence[f'oge/catalog/{old_sha}.json'])
            review[f'oge/discoveries/{old_sha}.json'] = encoded({**catalog, 'metadata': metadata})
            payload = {'draw':1, 'recordsTotal':len(current_rows or []),
                'recordsFiltered':len(current_rows or []), 'data':current_rows or []}
            current_catalog = build_catalog([parse_catalog_page(payload, start=0, length=1)])
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current_meta = archive_catalog(root, [(0,1,encoded(payload),{'content-type':'application/json'})],
                    current_catalog, retrieved_at=CUTOFF)
                evidence.update({path.relative_to(root).as_posix():path.read_bytes()
                                 for path in root.rglob('*') if path.is_file()})
            review['status/oge.json'] = encoded({'catalog_sha256':current_meta['sha256']})
            return old, current, review, evidence, board
        data = historical(self.data)
        self.assertEqual(self.verify(data)[0]['changed_fields'], ['owner'])
        forged = deepcopy(data)
        path = next(path for path in forged[2] if path.startswith('oge/discoveries/'))
        wrapper = json.loads(forged[2][path]); wrapper['transactions'][0]['agency'] = 'Forged Agency'
        forged[2][path] = encoded(wrapper)
        with self.assertRaisesRegex(InventoryError, 'historical discovery'):
            self.verify(forged)
        original_page = json.loads(next(raw for path,raw in self.data[3].items() if '/catalog/pages/' in path))
        variant = original_page['data'][0]
        variant['type'] = variant['type'].replace(DOCUMENT, 'f'*32)
        variant['agency'] = 'Another Agency'
        with self.assertRaisesRegex(InventoryError, 'identity is ambiguous'):
            self.verify(historical(self.data, [variant]))


if __name__ == '__main__':
    unittest.main()
