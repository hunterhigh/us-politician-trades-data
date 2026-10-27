"""Public boilerplate and synthetic upper content, never official trade gold."""
from pathlib import Path
import hashlib
import tempfile
import unittest

import pdfplumber
import pymupdf

from unison_snapshot.oge_278t_native_objects import inspect_strict_native_objects
from unison_snapshot.oge_278t_summary_region import (
    REGION_BBOX, observe_native_summary_region, replay_native_summary_region,
)


SUMMARY_LINES = (
    "The 278-T discloses purchases, sales, or exchanges of securities in excess of $1,000 made on behalf of the filer, the filer's spouse, or dependent child. Transactions are",
    "required to be disclosed within 30 days of receiving notification of a transaction but not later than 45 days after the transaction. Filers need not disclose (1) mutual funds and",
    "other excepted investment funds; (2) certificates of deposit, savings or checking accounts, and money market accounts; (3) U.S. Treasury bills, notes, and bonds; (4) Thrift",
    "Savings Plan accounts; (5) real property; and (6) transactions that are solely by and between the filer, the filer's spouse, and the filer's dependent children.",
)


def _text(text, x, y, size=10):
    escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    return f'BT /helv {size} Tf {x:.8f} {y:.8f} Td ({escaped})Tj ET'


def summary_pdf(path, *, page_number=1, upper_program=None, mutation=None, footer_name='EXAMPLE, ADA'):
    """One 842x595 native page; insert after a cover with page_number=2.

    Only the public fixed Summary wording/layout is reproduced. Everything
    above it is synthetic and may be replaced by an explicit upper_program.
    No official source page, financial payload or font binary is embedded.
    """
    footer = footer_name + f' - Page {page_number}'
    x = 806-pymupdf.get_text_length(footer, fontname='helv', fontsize=10)
    statements = ['q', _text(footer, x, 36), _text('Summary of Contents', 36, 185, 14),
                  _text(' ', 36, 170)]
    for index, line in enumerate(SUMMARY_LINES):
        if mutation == 'body_changed' and index == 1:
            line = line.replace('30 days', '31 days')
        statements.append(_text(line, 36, 159-index*11))
    statements += [_text(' ', 36, 111), 'Q']
    if upper_program is None:
        edges = [36, 65, 360, 430, 500, 610, 806]
        statements += ['q', '2 J', '0 G', '1 w']
        for y in (519, 489, 459):
            statements += [f'36 {y} m 806 {y} l S']
        for x in edges:
            statements += [f'{x} 519 m {x} 459 l S']
        statements += ['Q']
        for values, y in ((['#', 'DESCRIPTION', 'TYPE', 'DATE', 'NOTIFICATION', 'AMOUNT'], 500),
                          (['1', 'Example Company', 'Purchase', '06/01/2026', 'No', '$1,001 - $15,000'], 470)):
            for text, x in zip(values, edges):
                statements.append(_text(text, x+2, y))
        statements += [_text('Endnotes', 36, 373, 14),
                       _text('Synthetic note remains outside the Summary role.', 36, 335)]
    else:
        statements.append(upper_program)
    if mutation == 'extra_text': statements.append(_text('Additional financial material', 36, 145))
    elif mutation == 'crossing_text': statements.append(_text('CROSSING', 36, 190))
    elif mutation == 'line': statements.append('q 2 J 1 w 36 170 m 806 170 l S Q')
    elif mutation == 'white_cover': statements.append('q 1 g 35 110 770 80 re f Q')
    elif mutation == 'wrong_cap': statements = [s.replace('2 J', '1 J') for s in statements]
    elif mutation == 'unscoped_state': statements.append('q')
    stream = ('\n'.join(statements)+'\n').encode('ascii')
    document = pymupdf.open(); page = document.new_page(width=842, height=595)
    page.insert_font(fontname='helv')
    xref = document.get_new_xref(); document.update_object(xref, '<<>>')
    document.update_stream(xref, stream); page.set_contents(xref)
    document.save(path); document.close()
    raw = Path(path).read_bytes()
    return {'source_path': Path(path), 'raw': raw, 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'region_bbox_pdf_points': list(REGION_BBOX), 'upper_row_band_pdf_points': [106, 136]}


class NativeSummaryRegionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'source.pdf'

    def observe(self, data, number=1):
        with pdfplumber.open(self.path) as document:
            return observe_native_summary_region(document.pages[0], number,
                                                 source_sha256=data['source_sha256'])

    def test_complete_region_and_original_runs_replay_without_exempting_upper_content(self):
        data = summary_pdf(self.path); before = self.path.read_bytes(); proof = self.observe(data)
        self.assertEqual(proof['status'], 'observed', proof)
        self.assertEqual(proof['page_coverage'], 'partial')
        self.assertFalse(proof['financial_fact_qualification'])
        self.assertEqual(proof['region_character_count'], 701+len('EXAMPLE, ADA - Page 1'))
        self.assertEqual(len(proof['body_character_indices']), 701)
        self.assertEqual(proof['footer_text'], 'EXAMPLE, ADA - Page 1')
        self.assertTrue(replay_native_summary_region(self.path, proof,
            source_sha256=data['source_sha256'], page_number=1))
        self.assertEqual(self.path.read_bytes(), before)
        with pdfplumber.open(self.path) as document:
            old = inspect_strict_native_objects(document.pages[0], allowed_sizes=(10, 14))
            allowed = inspect_strict_native_objects(document.pages[0], allowed_sizes=(10, 14),
                                                    allow_square_linecaps=True)
        self.assertEqual(old['status'], 'unknown')
        self.assertEqual(old['reason'], 'unsupported_native_content_operator')
        self.assertEqual(allowed['status'], 'observed')
        self.assertTrue(allowed['line_cap_operations'])

    def test_independent_upper_text_and_scoped_grid_do_not_change_body_role(self):
        upper = ('q 2 J 2 w 36 500 m 806 500 l S Q\n' +
                 _text('Unresolved dynamic Endnote 99 remains here.', 36, 350))
        data = summary_pdf(self.path, upper_program=upper, page_number=2)
        proof = self.observe(data, 2)
        self.assertEqual(proof['status'], 'observed', proof)
        self.assertEqual(proof['footer_text'], 'EXAMPLE, ADA - Page 2')
        self.assertEqual(proof['region_bbox_pdf_points'], list(REGION_BBOX))

    def test_every_extra_crossing_or_occluding_source_member_stays_unknown(self):
        for mutation in ('body_changed', 'extra_text', 'crossing_text', 'line',
                         'white_cover', 'wrong_cap', 'unscoped_state'):
            with self.subTest(mutation=mutation):
                data = summary_pdf(self.path, mutation=mutation)
                proof = self.observe(data)
                self.assertEqual(proof['status'], 'unknown', proof)
                self.assertNotIn('region_bbox_pdf_points', proof)

    def test_source_and_receipt_changes_cannot_reuse_a_positive_region(self):
        data = summary_pdf(self.path); proof = self.observe(data)
        altered = {**proof, 'region_bbox_pdf_points': [0, 0, 842, 595]}
        self.assertFalse(replay_native_summary_region(self.path, altered,
            source_sha256=data['source_sha256'], page_number=1))
        with self.assertRaises(ValueError):
            replay_native_summary_region(self.path, proof, source_sha256='0'*64, page_number=1)
        data = summary_pdf(self.path, mutation='extra_text')
        self.assertFalse(replay_native_summary_region(self.path, proof,
            source_sha256=data['source_sha256'], page_number=1))


if __name__ == '__main__':
    unittest.main()
