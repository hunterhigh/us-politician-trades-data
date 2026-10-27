"""Real normal CLI/source/status plumbing; generated readings are not OCR gold."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf

from test_oge_cover_consumption import fixture, write, DOCUMENT_ID, CUTOFF
from test_oge_content_relations import saved
from test_public_repo import FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.public_repo import PublicSnapshotRepository
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.oge_saved_observations import (
    load_saved_oge_observations, prepare_saved_oge_observations,
)

ROOT = Path(__file__).resolve().parents[2]


def normal_fixture(root):
    data = fixture(root)
    observation = saved([data['old']['transactions'][0]['cells'],
        ['', 'Filer assets', '', '', '', ''],
        ['2', 'ALPHA', 'purchase', '05/02/2025', 'No', '$1,001 - $15,000']])
    observation['source_sha256'] = data['sha']
    data['request'].pop('archived_pdf_path')
    data['request']['observations'] = [observation]
    data['package'] = root/'saved.json'
    write(data['package'], {DOCUMENT_ID: data['request']})
    write(root/'extractions/original.json', data['old'])
    write(root/'catalog.json', data['catalog'])
    write(root/'base.json', data['formal'])
    return data


def run_cli(root, *extra):
    return subprocess.run([sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
        '--catalog', str(root/'catalog.json'), '--base', str(root/'base.json'),
        '--extractions-dir', str(root/'extractions'), '--data-cutoff-at', CUTOFF,
        '--output', str(root/'candidate.json'), '--audit-output', str(root/'audit.json'),
        *map(str, extra)], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT/'backend/src'),
        PYTHONIOENCODING='utf-8'), text=True, capture_output=True)


class SavedOgeNormalCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = normal_fixture(self.root)

    def normal(self, *extra):
        return run_cli(self.root, '--evidence-root', self.data['evidence'],
                       '--saved-observations', self.data['package'], *extra)

    def assert_refused(self, *extra):
        write(self.root/'candidate.json', {'keep': 'candidate'})
        write(self.root/'audit.json', {'keep': 'audit'})
        before = [(self.root/name).read_bytes() for name in ('candidate.json', 'audit.json')]
        process = self.normal(*extra)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(before, [(self.root/name).read_bytes() for name in ('candidate.json', 'audit.json')])
        return process

    def test_actual_normal_candidate_status_and_public_reader_preserve_facts_and_partial(self):
        data, root = self.data, self.root
        original_bytes = data['source'].read_bytes()
        plain = run_cli(root, '--evidence-root', data['evidence'])
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(json.loads((root/'candidate.json').read_bytes())['transactions'], data['formal']['transactions'])
        process = self.normal()
        self.assertEqual(process.returncode, 0, process.stderr)
        candidate = json.loads((root/'candidate.json').read_bytes())
        audit = json.loads((root/'audit.json').read_bytes())
        self.assertEqual(len(candidate['transactions']), 2)
        self.assertIn(data['formal']['transactions'][0], candidate['transactions'])
        self.assertIn('ALPHA', {row['asset_name'] for row in candidate['transactions']})
        self.assertFalse(audit['reports'][0]['observation_receipt']['coverage_complete'])
        pair = [(root/name).read_bytes() for name in ('candidate.json', 'audit.json')]
        self.assertEqual(self.normal().returncode, 0)
        self.assertEqual(pair, [(root/name).read_bytes() for name in ('candidate.json', 'audit.json')])
        write(data['review']/'oge/qualifications/current.json', audit)
        for tree in (data['evidence'], data['review']):
            for args in (('init', '-q'), ('config', 'user.name', 'Contract'),
                    ('config', 'user.email', 'contract@example.invalid'), ('add', '.'),
                    ('commit', '-qm', 'fixed normal source contract')):
                subprocess.run(['git', '-C', str(tree), *args], check=True, capture_output=True)
        status_process = subprocess.run([sys.executable, str(ROOT/'backend/scripts/build_disclosure_status.py'),
            '--evidence-root', str(data['evidence']), '--review-root', str(data['review']),
            '--input', str(root/'candidate.json'), '--code-commit', 'b'*40,
            '--previous-main-commit', 'a'*40, '--output', str(root/'status.json')],
            cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT/'backend/src')),
            capture_output=True, text=True)
        self.assertEqual(status_process.returncode, 0, status_process.stderr)
        status = json.loads((root/'status.json').read_bytes())
        record = next(r for scope in status['scopes'] for r in scope['reports'] if r['report_id'] == DOCUMENT_ID)
        self.assertEqual(record['qualified_transaction_count'], 2)
        self.assertEqual(record['qualification_state'], 'partial')
        self.assertEqual(set(record['publication_transaction_ids']), {r['id'] for r in candidate['transactions']})
        bundle = build(candidate, generated_at=CUTOFF, allow_production=True)
        bundle = attach_disclosure_status(bundle, status['scopes'], transactions=candidate['transactions'],
                                          provenance=status['provenance'])
        public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'], candidate['transactions'])
        self.assertTrue(public.disclosure_status(public.resolve(), include_records=True))
        self.assertEqual(data['source'].read_bytes(), original_bytes)

    def test_portable_preparation_and_explicit_generic_use_existing_materializer(self):
        data = self.data
        portable = prepare_saved_oge_observations(evidence_root=data['evidence'],
            metadata_path=data['source'].with_suffix('.json'), **data['request'])
        self.assertEqual(portable, {DOCUMENT_ID: data['request']})
        bound = load_saved_oge_observations(data['package'], evidence_root=data['evidence'], extractions=[data['old']])
        self.assertEqual(bound[DOCUMENT_ID]['archived_pdf_path'], str(data['source'].resolve()))
        generic = parse_archived_pdf(data['evidence'], data['source'].with_suffix('.json'), generic=True)
        write(self.root/'extractions/generic.json', generic)
        # Explicit generic input never becomes the scheduled/default parser.
        default = self.normal()
        self.assertEqual(default.returncode, 0, default.stderr)
        default_audit = json.loads((self.root/'audit.json').read_bytes())
        explicit = self.normal('--generic')
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        generic_audit = json.loads((self.root/'audit.json').read_bytes())
        self.assertEqual(default_audit['reports'][0]['legacy_normalized_reference_count'], 1)
        self.assertNotIn('cover_date_evidence', default_audit['reports'][0])
        self.assertEqual(generic_audit['reports'][0]['cover_date_evidence'], generic['cover_date_evidence'])
        self.assertIn(data['formal']['transactions'][0], json.loads((self.root/'candidate.json').read_bytes())['transactions'])

    def test_invalid_packages_refuse_before_outputs(self):
        original = {DOCUMENT_ID: self.data['request']}
        for kind in ('path', 'ids', 'unknown', 'sha', 'secondary', 'page', 'boolean_page',
                     'unselected', 'empty', 'schema', 'unused_document'):
            with self.subTest(kind=kind):
                changed = deepcopy(original)
                request = changed[DOCUMENT_ID]
                if kind == 'path': request['archived_pdf_path'] = str(self.data['source'])
                elif kind == 'ids': request['formal_reference_ids'] = ['pretend']
                elif kind == 'unknown': request['source_glyph_mode'] = 'pretend'
                elif kind == 'sha': request['observations'][0]['source_sha256'] = 'e'*64
                elif kind == 'secondary': request['secondary_observations'] = [{'source_sha256': 'e'*64}]
                elif kind == 'page': request['document_page_count'] = 3
                elif kind == 'boolean_page': request['document_page_count'] = True
                elif kind == 'unselected': changed = {'e'*32: request}
                elif kind == 'empty': changed = {}
                elif kind == 'schema': request['observations'][0]['schema_version'] = 'oge-278t-raster-slot-observation/v1'
                elif kind == 'unused_document': changed['e'*32] = deepcopy(request)
                write(self.data['package'], changed)
                self.assert_refused()

    def test_missing_root_duplicate_extraction_and_metadata_source_tamper_refuse(self):
        no_root = run_cli(self.root, '--saved-observations', self.data['package'])
        self.assertEqual(no_root.returncode, 2)
        self.assertFalse((self.root/'candidate.json').exists())
        write(self.root/'extractions/duplicate.json', self.data['old'])
        self.assert_refused()
        (self.root/'extractions/duplicate.json').unlink()
        metadata_path = self.data['source'].with_suffix('.json')
        for field, value in (('byte_length', 1), ('document_id', 'e'*32),
                             ('filer_name', 'Another person'), ('archive_path', '../../elsewhere.pdf')):
            with self.subTest(field=field):
                metadata = {**self.data['metadata'], field: value}
                write(metadata_path, metadata)
                self.assert_refused()
        write(metadata_path, self.data['metadata'])
        self.data['source'].write_bytes(self.data['source'].read_bytes() + b'\n% changed source bytes\n')
        self.assert_refused()

    def test_output_aliases_budget_and_duplicate_json_keys_refuse(self):
        data, root = self.data, self.root
        for output in (data['source'], data['source'].with_suffix('.json'), data['package'],
                       root/'base.json', root/'audit.json', root/'extractions/new.json'):
            with self.subTest(output=output):
                before = {p: p.read_bytes() for p in (data['source'], data['package'], root/'base.json')}
                self.assert_refused('--output', output)
                self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))
        raw = data['package'].read_bytes()
        data['package'].write_bytes(b' '*(16*1024*1024 + 1))
        self.assert_refused()
        data['package'].write_bytes(b'{"'+DOCUMENT_ID.encode()+b'":{},"'+DOCUMENT_ID.encode()+b'":{}}')
        self.assert_refused()
        data['package'].write_bytes(raw)

    def test_hardlink_output_and_missing_source_dependency_refuse_without_mutation(self):
        data, root = self.data, self.root
        alias = root/'source-hardlink.json'
        os.link(data['source'], alias)
        before = data['source'].read_bytes()
        self.assert_refused('--output', alias)
        self.assertEqual(alias.read_bytes(), before)
        self.assertEqual(data['source'].read_bytes(), before)
        guard = root/'import-guard'; guard.mkdir()
        marker = guard/'attempted.txt'
        (guard/'sitecustomize.py').write_text(
            'import sys,os\nfrom pathlib import Path\n'
            'class Block:\n'
            ' def find_spec(self, fullname, path=None, target=None):\n'
            '  if fullname == "pdfplumber" or fullname.startswith("pdfplumber."):\n'
            '   Path(os.environ["BLOCKED_IMPORT_LOG"]).write_text(fullname)\n'
            '   raise ModuleNotFoundError("test blocked source dependency")\n'
            'sys.meta_path.insert(0,Block())\n', encoding='utf-8')
        before_pair = [(root/name).read_bytes() for name in ('candidate.json', 'audit.json')]
        process = subprocess.run([sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
            '--catalog', str(root/'catalog.json'), '--base', str(root/'base.json'),
            '--extractions-dir', str(root/'extractions'), '--data-cutoff-at', CUTOFF,
            '--output', str(root/'candidate.json'), '--audit-output', str(root/'audit.json'),
            '--evidence-root', str(data['evidence']), '--saved-observations', str(data['package'])],
            cwd=ROOT, env=dict(os.environ, PYTHONPATH=os.pathsep.join((str(guard), str(ROOT/'backend/src'))),
                BLOCKED_IMPORT_LOG=str(marker)), capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertTrue(marker.exists(), process.stderr)
        self.assertEqual(before_pair, [(root/name).read_bytes() for name in ('candidate.json', 'audit.json')])

    def test_actual_page_count_can_exceed_selected_render_page_budget(self):
        data = self.data
        document = pymupdf.open(data['source'])
        for _ in range(10):
            document.new_page(width=792, height=792)
        raw = document.tobytes(); document.close()
        import hashlib
        sha = hashlib.sha256(raw).hexdigest()
        source = data['source'].with_name(sha+'.pdf'); source.write_bytes(raw)
        metadata = {**data['metadata'], 'sha256': sha, 'byte_length': len(raw),
                    'archive_path': source.relative_to(data['evidence']).as_posix()}
        write(source.with_suffix('.json'), metadata)
        observation = deepcopy(data['request']['observations'][0]); observation['source_sha256'] = sha
        portable = prepare_saved_oge_observations(evidence_root=data['evidence'],
            metadata_path=source.with_suffix('.json'), observations=[observation])
        self.assertEqual(portable[DOCUMENT_ID]['document_page_count'], 12)
        with self.assertRaisesRegex(ValueError, 'page count'):
            prepare_saved_oge_observations(evidence_root=data['evidence'],
                metadata_path=source.with_suffix('.json'), observations=[observation], document_page_count=2)


if __name__ == '__main__':
    unittest.main()
