"""Per-report cache selection through the normal CLI.

Generated native v2/v7 controls prove selection, not official accuracy. Other
cached generic families retain their existing source-plan/receiver contracts;
only an actual ordinary v7/v2 pair requires a canonical same-SHA v2 input.
"""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_oge_owner_correction as sample
from unison_snapshot import __main__ as cli
from unison_snapshot.codec import encode
from unison_snapshot.oge import build_catalog, parse_catalog_page, archive_catalog


@lru_cache(maxsize=1)
def _generated_sources():
    sources = []
    for document, asset in [('42300720a4227e9e85258e77002dd1b3', 'First Common Stock (FST)'),
                            ('52300720a4227e9e85258e77002dd1b3', 'Second Common Stock (SND)')]:
        url = f'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{document}/$FILE/Example-278T.pdf'
        with patch.object(sample, 'DOCUMENT', document), patch.object(sample, 'URL', url):
            sources.append(sample.fixture(asset=asset))
    return sources


def make_mixed_inputs(root):
    """Write two distinct source PDFs/canonical cache pairs and an old board.

    Return paths (catalog_path, base_path, evidence_root, review_root,
    extractions_root), a/b IDs, old_a/old_b objects, and sha_a/sha_b.
    """
    root = Path(root)
    evidence_root, review_root = root/'evidence', root/'review'
    sources = deepcopy(_generated_sources())
    rows, originals = [], {}
    base = deepcopy(sources[0][4])
    base['transactions'] = [data[0] for data in sources]
    for old, expected, review, evidence, previous in sources:
        if base['people'] != previous['people']:
            raise AssertionError('Generated controls must share one real source-derived person')
        payload = json.loads(next(raw for path, raw in evidence.items() if '/catalog/pages/' in path))
        rows.extend(payload['data'])
        originals.update({path: raw for path, raw in evidence.items() if '/reports/' in path})
        sample.write_files(review_root, {path: raw for path, raw in review.items() if '/extractions/' in path})
    sample.write_files(evidence_root, originals)
    payload = {'draw': 1, 'recordsTotal': 2, 'recordsFiltered': 2, 'data': rows}
    catalog = build_catalog([parse_catalog_page(payload, start=0, length=2)])
    metadata = archive_catalog(evidence_root, [(0, 2, sample.encoded(payload),
        {'content-type': 'application/json'})], catalog, retrieved_at=sample.CUTOFF)
    sample.write_files(root, {'catalog.json': sample.encoded(catalog), 'base.json': sample.encoded(base)})
    sample.write_files(review_root, {'status/oge.json': sample.encoded({'catalog_sha256': metadata['sha256']})})
    a, b = [source[0]['filing_id'] for source in sources]
    sha_a, sha_b = [json.loads(next(raw for path, raw in source[2].items()
                                   if path.endswith('pdf-v7.json')))['source_sha256'] for source in sources]
    if a == b or sha_a == sha_b:
        raise AssertionError('Two independent source identities and bytes are required')
    return {'catalog_path': root/'catalog.json', 'base_path': root/'base.json',
        'evidence_root': evidence_root, 'review_root': review_root,
        'extractions_root': review_root/'oge/extractions', 'a': a, 'b': b,
        'old_a': sources[0][0], 'old_b': sources[1][0], 'sha_a': sha_a, 'sha_b': sha_b,
        'cutoff': sample.CUTOFF}


class ScopedGenericCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-scoped-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = make_mixed_inputs(self.root)
        self.output, self.audit = self.root/'candidate.json', self.root/'audit.json'

    def cache(self, doc, sha, version):
        return self.data['extractions_root']/doc/sha/(version.replace('/', '-')+'.json')

    def run_cli(self, arguments=(), *, roots=True):
        args = ['snapshot', 'build-oge-candidate', '--catalog', str(self.data['catalog_path']),
            '--base', str(self.data['base_path']), '--extractions-dir', str(self.data['extractions_root']),
            '--data-cutoff-at', self.data['cutoff'], '--output', str(self.output), '--audit-output', str(self.audit)]
        if roots:
            args += ['--evidence-root', str(self.data['evidence_root'])]
        with patch.object(sys, 'argv', args + list(arguments)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cli.main()

    def assert_rejected_without_writes(self, arguments, *, roots=True):
        self.output.write_bytes(b'candidate sentinel'); self.audit.write_bytes(b'audit sentinel')
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(arguments, roots=roots)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.output.read_bytes(), b'candidate sentinel')
        self.assertEqual(self.audit.read_bytes(), b'audit sentinel')

    def read(self):
        return json.loads(self.output.read_bytes()), json.loads(self.audit.read_bytes())

    def test_old_default_global_and_new_scoped_modes_bind_the_actual_selected_raw_cache(self):
        a, b = self.data['a'], self.data['b']
        for flags, versions, owners in [([], ['v2', 'v2'], ['Self', 'Self']),
                (['--generic', '--legacy-extractions-dir', str(self.data['extractions_root'])],
                 ['v7', 'v7'], ['Undisclosed', 'Undisclosed']),
                (['--generic-document-id', a], ['v7', 'v2'], ['Undisclosed', 'Self'])]:
            with self.subTest(flags=flags):
                self.run_cli(flags)
                candidate, audit = self.read()
                facts = {row['filing_id']: row for row in candidate['transactions']}
                self.assertEqual([facts[doc]['owner'] for doc in (a, b)], owners)
                if flags and flags[0] == '--generic-document-id':
                    self.assertEqual(facts[b], self.data['old_b'])
                for doc, sha, version in zip((a, b), (self.data['sha_a'], self.data['sha_b']), versions):
                    raw = json.loads(self.cache(doc, sha, 'oge-278t-pdf/'+version).read_bytes())
                    binding = next(report for report in audit['reports'] if report['document_id'] == doc)['source_interpretation_binding']
                    self.assertEqual(binding['parser_version'], raw['parser_version'])
                    self.assertEqual(binding['extraction_canonical_sha256'], hashlib.sha256(encode(raw)).hexdigest())
        before = self.output.read_bytes(), self.audit.read_bytes()
        self.run_cli(['--generic-document-id', a])
        self.assertEqual((self.output.read_bytes(), self.audit.read_bytes()), before)
        prior_output, _ = self.read()
        self.data['base_path'].write_bytes(sample.encoded(prior_output))
        self.run_cli(['--generic-document-id', a])
        next_output, next_audit = self.read()
        self.assertEqual(next_output['transactions'], prior_output['transactions'])
        self.assertEqual(next(row for row in next_output['transactions'] if row['filing_id'] == b), self.data['old_b'])
        a_report = next(report for report in next_audit['reports'] if report['document_id'] == a)
        self.assertEqual(a_report['owner_scope_view']['current_ids'], [self.data['old_a']['id']])

    def test_only_selected_document_needs_a_generic_cache_and_repeated_ids_select_two_reports(self):
        a, b = self.data['a'], self.data['b']
        path = self.cache(b, self.data['sha_b'], 'oge-278t-pdf/v7')
        original = path.read_bytes(); path.unlink()
        self.run_cli(['--generic-document-id', a])
        self.assertEqual(next(row for row in self.read()[0]['transactions'] if row['filing_id'] == b), self.data['old_b'])
        path.write_bytes(original)
        self.run_cli(['--generic-document-id', a, '--generic-document-id', b])
        self.assertEqual({row['owner'] for row in self.read()[0]['transactions']}, {'Undisclosed'})

    def test_invalid_duplicate_unknown_missing_and_mutually_exclusive_selection_preserve_outputs(self):
        a = self.data['a']
        for arguments in [ ['--generic-document-id', '../outside'],
                ['--generic-document-id', a.upper()],
                ['--generic-document-id', a, '--generic-document-id', a],
                ['--generic-document-id', '9'*32],
                ['--generic', '--generic-document-id', a],
                sum((['--generic-document-id', format(n, '032x')] for n in range(33)), [])]:
            with self.subTest(arguments=arguments):
                self.assert_rejected_without_writes(arguments)
        self.assert_rejected_without_writes(['--generic-document-id', a], roots=False)
        self.cache(a, self.data['sha_a'], 'oge-278t-pdf/v7').unlink()
        self.assert_rejected_without_writes(['--generic-document-id', a])

    def test_canonical_same_sha_legacy_pair_is_required_and_cannot_self_sign_other_source_or_reading(self):
        a, sha = self.data['a'], self.data['sha_a']
        source = self.cache(a, sha, 'oge-278t-pdf/v2')
        original = source.read_bytes()
        source.unlink()
        self.assert_rejected_without_writes(['--generic-document-id', a])
        source.write_bytes(original)
        external = self.root/'explicit-legacy'
        target = external/a/sha/'oge-278t-pdf-v2.json'
        target.parent.mkdir(parents=True)
        for field, value in [('source_sha256', self.data['sha_b']), ('document_id', self.data['b']),
                             ('owner_scope_complete', True)]:
            forged = json.loads(original); forged[field] = value
            target.write_bytes(sample.encoded(forged))
            with self.subTest(field=field):
                self.assert_rejected_without_writes(['--generic-document-id', a,
                    '--legacy-extractions-dir', str(external)])


if __name__ == '__main__':
    unittest.main()
