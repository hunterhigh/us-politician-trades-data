"""Adjacent-page cell continuations must not consume independent holdings."""
import unittest

from test_house_holdings import word, SHA
from unison_snapshot.house_holdings import extract_schedule_a_pages


def sheet(words):
    return {'width': 612, 'height': 792, 'words': [
        word('Asset', 25, 90), word('Owner', 282, 90),
        word('Value', 321, 90), word('Income', 408, 90), *words]}


class WrappedCellsTests(unittest.TestCase):
    def rows(self, pages):
        return extract_schedule_a_pages(pages, source_sha256=SHA)[0]

    def test_upper_bound_without_asset_text_or_with_location_note(self):
        for note in ['', 'L: Example, US', 'D: Rental description']:
            with self.subTest(note=note):
                pages = [sheet([word('Rental [RP]', 25, 703), word('SP', 282, 703),
                    word('$100,001 -', 321, 703)]), sheet([
                    *([word(note, 25, 105)] if note else []), word('$250,000', 321, 105)])]
                rows = self.rows(pages)
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['asset_raw'], rows[0]['owner'],
                    rows[0]['value_low'], rows[0]['value_high']), ('Rental [RP]', 'Spouse', 100001, 250000))
                self.assertFalse(rows[0]['review_reasons'])

    def test_asset_name_and_code_resume_without_account_arrow(self):
        for tail in ['Long Name [MF]', '[MF]']:
            pages = [sheet([word('Fund', 25, 700), word('$1,001 - $15,000', 321, 700)]),
                     sheet([word(tail, 25, 105)])]
            rows = self.rows(pages)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['asset_raw'], 'Fund ' + tail)
            self.assertFalse(rows[0]['review_reasons'])

    def test_open_value_continuation(self):
        rows = self.rows([sheet([word('Fund [MF]', 25, 700), word('Spouse/DC Over', 321, 700)]),
                          sheet([word('$1,000,000', 321, 105)])])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['value_low'], 1000000)
        self.assertIsNone(rows[0]['value_high'])
        self.assertTrue(rows[0]['value_low_exclusive'])

    def test_existing_account_code_continuation_keeps_qualified_spelling(self):
        for name in ['Account ⇒', 'Account ⇒ Existing name']:
            pages = [sheet([word(name, 25, 700), word('$1,001 - $15,000', 321, 700)]),
                sheet([word('Additional printed context', 25, 105),
                       word('(FUND)', 25, 116), word('[MF]', 80, 116)])]
            row = self.rows(pages)[0]
            self.assertEqual(row['asset_raw'], name + ' (FUND) [MF]')
            self.assertEqual(row['evidence']['continuation']['top_points'], 116)
            self.assertFalse(row['review_reasons'])

    def test_description_and_repeated_wrapped_code_do_not_create_holdings(self):
        p = sheet([word('Fund [MF]', 25, 130), word('$1,001 - $15,000', 321, 130),
            word('[MF]', 25, 141), word('D: Description [ST]', 25, 155),
            word('$999', 321, 155)])
        rows = self.rows([p])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['asset_raw'], 'Fund [MF]')
        self.assertEqual(rows[0]['value_high'], 15000)

    def test_new_owner_or_new_financial_row_is_not_joined(self):
        for extra in [[word('SP', 282, 105)], [word('$15,001 - $50,000', 321, 105)]]:
            rows = self.rows([sheet([word('Unfinished', 25, 700), word('$1,001 - $15,000', 321, 700)]),
                              sheet([word('Independent', 25, 105), word('[MF]', 90, 105), *extra])])
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0]['review_reasons'])

    def test_nonadjacent_or_nonedge_fragments_stay_unresolved(self):
        first = sheet([word('Unfinished', 25, 300), word('$1,001 - $15,000', 321, 300)])
        last = sheet([word('Fund', 25, 105), word('[MF]', 90, 105)])
        self.assertEqual(len(self.rows([first, last])), 2)
        first['words'] = [dict(w, top=700) if w['top'] == 300 else w for w in first['words']]
        self.assertEqual(len(self.rows([first, sheet([]), last])), 2)
