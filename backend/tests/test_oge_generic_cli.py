import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from unison_snapshot import __main__ as cli
from unison_snapshot.oge import OgeSourceConfig
from unison_snapshot.oge_reports import archive_direct_pdf, parse_archived_pdf, load_reusable_extraction
from test_oge_reports import record, Client, DOCUMENT_ID
from test_oge_generic_candidate import generic, CELLS
from test_oge_candidate import catalog, extraction, base


class GenericOgeCliTests(unittest.TestCase):
    def test_candidate_cli_explicit_generic_selects_bound_v7_and_default_stays_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extracts = root/'extractions'; extracts.mkdir()
            for name, value in [('catalog', catalog()), ('base', base())]:
                (root/f'{name}.json').write_text(json.dumps(value), encoding='utf-8')
            old = extraction()
            old['source_rows'] = [{'page_number': 1, 'cells': CELLS}]
            (extracts/'old.json').write_text(json.dumps(old), encoding='utf-8')
            (extracts/'new.json').write_text(json.dumps(generic()), encoding='utf-8')
            args = ['snapshot', 'build-oge-candidate', '--catalog', str(root/'catalog.json'),
                    '--base', str(root/'base.json'), '--extractions-dir', str(extracts),
                    '--data-cutoff-at', '2025-06-04T00:00:00Z', '--output', str(root/'out.json'),
                    '--audit-output', str(root/'audit.json')]
            for extra, owner in [([], 'Self'), (['--generic'], 'Undisclosed')]:
                with self.subTest(owner=owner), patch.object(sys, 'argv', args+extra):
                    cli.main()
                output = json.loads((root/'out.json').read_text(encoding='utf-8'))
                self.assertEqual(len(output['transactions']), 1)
                self.assertEqual(output['transactions'][0]['owner'], owner)

    def test_generic_checkpoint_never_reuses_v2_or_invalid_cover_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = archive_direct_pdf(root, record(), OgeSourceConfig(True, True),
                                          client=Client(), retrieved_at='2026-09-20T00:00:00Z')
            path = root/'oge/reports'/DOCUMENT_ID/f"{metadata['sha256']}.json"
            row = ['1', 'Example Inc.', 'Purchase', '09/19/2026', 'No', '$1,001 - $15,000']
            cover = {'page_number': 1, 'text': "Filer's Certification\nDate: 09/20/2026",
                     'document_page_count': 2}
            with patch('unison_snapshot.oge_reports._extract_pdf', return_value=(cover['text'], [(2,row)], [(2,row)], cover)):
                parsed = parse_archived_pdf(root, path, generic=True)
            folder = root/'reuse'/DOCUMENT_ID/metadata['sha256']; folder.mkdir(parents=True)
            target = folder/'oge-278t-pdf-v7.json'
            target.write_text(json.dumps(parsed), encoding='utf-8')
            self.assertEqual(load_reusable_extraction(root, path, root/'reuse', generic=True), parsed)
            self.assertIsNone(load_reusable_extraction(root, path, root/'reuse'))
            parsed['cover_date_evidence']['source_sha256'] = 'b'*64
            target.write_text(json.dumps(parsed), encoding='utf-8')
            self.assertIsNone(load_reusable_extraction(root, path, root/'reuse', generic=True))

    def test_extract_cli_passes_explicit_generic_mode_to_existing_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'batch.json').write_text(json.dumps({'reports':[{'document_id':DOCUMENT_ID,'sha256':'a'*64}]}))
            args = ['snapshot', 'extract-oge-direct-pdfs', '--batch', str(root/'batch.json'),
                    '--archive', str(root), '--output-dir', str(root/'extracted'),
                    '--output', str(root/'summary.json'), '--generic']
            with patch.object(sys, 'argv', args), patch.object(cli, 'parse_oge_archived_pdf', return_value=generic()) as parser:
                cli.main()
            self.assertEqual(parser.call_args.kwargs, {'generic': True})


if __name__ == '__main__':
    unittest.main()
