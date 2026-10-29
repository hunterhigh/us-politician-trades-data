import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from unison_snapshot import __main__ as cli


class SavedSenateCliTests(unittest.TestCase):
    def test_saved_cli_passes_only_explicit_selected_documents_to_existing_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grids, words = root/'grids.json', root/'words.json'
            grids.write_text(json.dumps({'b': {'document_id': 'b'}, 'a': {'document_id': 'a'}}), encoding='utf-8')
            words.write_text(json.dumps({'a': [], 'b': []}), encoding='utf-8')
            args = ['snapshot', 'extract-senate-paper-pages', '--evidence-root', str(root),
                    '--review-root', str(root), '--output', str(root/'out.json'),
                    '--saved-grid-observations', str(grids), '--saved-word-pages', str(words)]
            result = {'report_count': 2, 'extraction_count': 2, 'failure_count': 0,
                      'transaction_count': 1, 'eligible_transaction_count': 1, 'quarantined_transaction_count': 0, 'scope': 'selected_fixed_reports_not_full_catalog'}
            with patch.object(sys, 'argv', args), patch.object(cli, 'extract_archived_paper_reports', return_value=result) as runner:
                cli.main()
            self.assertEqual(runner.call_args.kwargs['selected_document_ids'], ['a', 'b'])
            self.assertEqual(runner.call_args.kwargs['cached_word_pages'], {'a': [], 'b': []})
            self.assertEqual(json.loads((root/'out.json').read_text()), result)

    def test_one_saved_flag_cannot_fall_back_to_ocr(self):
        args = ['snapshot', 'extract-senate-paper-pages', '--evidence-root', '.',
                '--review-root', '.', '--output', 'unused.json', '--saved-word-pages', 'unused.json']
        with patch.object(sys, 'argv', args), patch.object(cli, 'extract_archived_paper_reports') as runner:
            with self.assertRaises(SystemExit) as error:
                cli.main()
        self.assertEqual(error.exception.code, 2)
        runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
