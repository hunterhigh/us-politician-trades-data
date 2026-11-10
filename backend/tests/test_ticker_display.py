import unittest
from unison_snapshot.legacy import load


class TickerDisplayTests(unittest.TestCase):
    def test_published_renderer_marks_unmatched_rows_without_null_navigation(self):
        from unison_snapshot.ticker_display import render
        from unison_snapshot.legacy import SCRIPTS
        baseline = (SCRIPTS / 'render_dashboard.py').read_text(encoding='utf-8')
        html = render(baseline)
        self.assertEqual(html.count('data-ticker-status="unmatched"'), 2)
        self.assertIn("t.ticker || '未匹配'", html)
        self.assertIn('${t.ticker ? `<button class="person-security', html)
        # Loading the production renderer verifies its frozen baseline pin as well.
        self.assertTrue(callable(load('render_dashboard', version='v2').render_html))
